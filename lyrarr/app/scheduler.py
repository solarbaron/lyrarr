# coding=utf-8

import os
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from datetime import datetime, timedelta
from random import randrange
from tzlocal import get_localzone

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo

from dateutil import tz
from dateutil.relativedelta import relativedelta

from lyrarr.lidarr.sync import update_artists
from lyrarr.metadata.download_worker import run_metadata_downloads
from lyrarr.utilities.health import check_health
from lyrarr.api.backup import run_scheduled_backup
from .config import settings
from .get_args import args
from .event_handler import event_stream

ONE_YEAR_IN_SECONDS = 60 * 60 * 24 * 365


def in_a_century():
    century = datetime.now() + relativedelta(years=100)
    return century.year


class Scheduler:

    def __init__(self):
        self.__running_tasks = []

        if os.environ.get("TZ") == "":
            del os.environ["TZ"]

        try:
            self.timezone = get_localzone()
        except zoneinfo.ZoneInfoNotFoundError:
            logging.error("LYRARR cannot use the specified timezone and will use UTC instead.")
            self.timezone = tz.gettz("UTC")
        else:
            logging.info(f"Scheduler will use this timezone: {self.timezone}")

        self.aps_scheduler = BackgroundScheduler({'apscheduler.timezone': self.timezone})

        def task_listener_add(event):
            if event.job_id not in self.__running_tasks:
                self.__running_tasks.append(event.job_id)
                event_stream(type='task')

        def task_listener_remove(event):
            if event.job_id in self.__running_tasks:
                self.__running_tasks.remove(event.job_id)
                event_stream(type='task')

        self.aps_scheduler.add_listener(task_listener_add, EVENT_JOB_SUBMITTED)
        self.aps_scheduler.add_listener(task_listener_remove, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

        self.__check_health_task()
        self.update_configurable_tasks()

        self.aps_scheduler.start()

    def update_configurable_tasks(self):
        self.__lidarr_sync_task()
        self.__lidarr_full_update_task()
        self.__metadata_download_task()
        self.__backup_task()
        self.__randomize_interval_task()
        if args.no_tasks:
            self.__no_task()

    def execute_job_now(self, taskid):
        self.aps_scheduler.modify_job(taskid, next_run_time=datetime.now())

    def get_running_tasks(self):
        return self.__running_tasks

    def get_task_list(self):
        def get_time_from_interval(td_object):
            seconds = int(td_object.total_seconds())
            periods = [
                ('year', 60 * 60 * 24 * 365),
                ('month', 60 * 60 * 24 * 30),
                ('day', 60 * 60 * 24),
                ('hour', 60 * 60),
                ('minute', 60),
                ('second', 1)
            ]
            if seconds > ONE_YEAR_IN_SECONDS:
                return "None"
            strings = []
            for period_name, period_seconds in periods:
                if seconds > period_seconds:
                    period_value, seconds = divmod(seconds, period_seconds)
                    has_s = 's' if period_value > 1 else ''
                    strings.append("%s %s%s" % (period_value, period_name, has_s))
            return ", ".join(strings)

        task_list = []
        for job in self.aps_scheduler.get_jobs():
            next_run = "Never"
            if job.next_run_time:
                next_run = str(job.next_run_time)

            running = job.id in self.__running_tasks

            if isinstance(job.trigger, IntervalTrigger):
                interval = get_time_from_interval(job.trigger.__getstate__()['interval'])
                if interval != "None":
                    interval = f"every {interval}"
                task_list.append({
                    'name': job.name, 'interval': interval,
                    'next_run_time': next_run, 'job_id': job.id, 'job_running': running
                })
            elif isinstance(job.trigger, CronTrigger):
                task_list.append({
                    'name': job.name, 'interval': 'cron',
                    'next_run_time': next_run, 'job_id': job.id, 'job_running': running
                })

        return task_list

    def __lidarr_sync_task(self):
        if settings.general.use_lidarr:
            self.aps_scheduler.add_job(
                update_artists, 'interval', minutes=int(settings.lidarr.sync_interval),
                max_instances=1, coalesce=True, misfire_grace_time=15,
                id='sync_with_lidarr', name='Sync with Lidarr', replace_existing=True)

    def __lidarr_full_update_task(self):
        if settings.general.use_lidarr:
            full_update = settings.lidarr.full_update
            if full_update == "Daily":
                self.aps_scheduler.add_job(
                    update_artists, 'cron', hour=settings.lidarr.full_update_hour,
                    max_instances=1, coalesce=True, misfire_grace_time=15,
                    id='full_lidarr_scan', name='Full Lidarr Scan', replace_existing=True)
            elif full_update == "Weekly":
                self.aps_scheduler.add_job(
                    update_artists, 'cron', day_of_week=settings.lidarr.full_update_day,
                    hour=settings.lidarr.full_update_hour,
                    max_instances=1, coalesce=True, misfire_grace_time=15,
                    id='full_lidarr_scan', name='Full Lidarr Scan', replace_existing=True)
            elif full_update == "Manually":
                self.aps_scheduler.add_job(
                    update_artists, 'cron', year=in_a_century(),
                    max_instances=1, coalesce=True, misfire_grace_time=15,
                    id='full_lidarr_scan', name='Full Lidarr Scan', replace_existing=True)

    def __check_health_task(self):
        self.aps_scheduler.add_job(check_health, 'interval', hours=6, max_instances=1,
                                   coalesce=True, misfire_grace_time=15,
                                   id='check_health', name='Check Health')

    def __metadata_download_task(self):
        self.aps_scheduler.add_job(
            run_metadata_downloads, 'interval', hours=2,
            max_instances=1, coalesce=True, misfire_grace_time=15,
            id='download_metadata', name='Download Missing Metadata',
            replace_existing=True)

    def __backup_task(self):
        frequency = settings.backup.frequency
        hour = settings.backup.hour
        day = settings.backup.day

        if frequency == 'Daily':
            self.aps_scheduler.add_job(
                run_scheduled_backup, 'cron', hour=hour,
                max_instances=1, coalesce=True, misfire_grace_time=15,
                id='scheduled_backup', name='Scheduled Backup', replace_existing=True)
        elif frequency == 'Weekly':
            self.aps_scheduler.add_job(
                run_scheduled_backup, 'cron', day_of_week=day, hour=hour,
                max_instances=1, coalesce=True, misfire_grace_time=15,
                id='scheduled_backup', name='Scheduled Backup', replace_existing=True)
        else:  # Manually
            self.aps_scheduler.add_job(
                run_scheduled_backup, 'cron', year=in_a_century(),
                max_instances=1, coalesce=True, misfire_grace_time=15,
                id='scheduled_backup', name='Scheduled Backup', replace_existing=True)

    def __randomize_interval_task(self):
        for job in self.aps_scheduler.get_jobs():
            if isinstance(job.trigger, IntervalTrigger):
                if job.trigger.interval.total_seconds() > ONE_YEAR_IN_SECONDS:
                    continue
                self.aps_scheduler.modify_job(
                    job.id,
                    next_run_time=datetime.now(tz=self.timezone) +
                    timedelta(seconds=randrange(
                        int(job.trigger.interval.total_seconds() * 0.75),
                        int(job.trigger.interval.total_seconds())))
                )

    def __no_task(self):
        for job in self.aps_scheduler.get_jobs():
            self.aps_scheduler.modify_job(job.id, next_run_time=None)


scheduler = Scheduler()
