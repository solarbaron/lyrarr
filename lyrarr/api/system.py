# coding=utf-8

import os
import platform

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.config import settings, get_settings, save_settings

api_ns_system = Namespace('system', description='System operations')


@api_ns_system.route('/system/status')
class SystemStatus(Resource):
    def get(self):
        """Get system status."""
        return {
            'version': os.environ.get('LYRARR_VERSION', 'dev'),
            'python_version': platform.python_version(),
            'os': platform.system(),
            'lidarr_configured': bool(settings.lidarr.apikey),
            'lidarr_enabled': settings.general.use_lidarr,
        }


@api_ns_system.route('/system/settings')
class SystemSettings(Resource):
    def get(self):
        """Get all settings."""
        return get_settings()

    def post(self):
        """Save settings."""
        data = request.get_json()
        if not data:
            return {'message': 'No data provided'}, 400

        settings_items = [(k, v) for k, v in data.items()]
        result = save_settings(settings_items)
        return {'message': 'Settings saved', **result}


@api_ns_system.route('/system/tasks')
class SystemTasks(Resource):
    def get(self):
        """Get scheduled tasks."""
        from lyrarr.app.scheduler import scheduler
        return scheduler.get_task_list()

    def post(self):
        """Run a task immediately."""
        data = request.get_json()
        task_id = data.get('taskId')
        if not task_id:
            return {'message': 'taskId is required'}, 400

        from lyrarr.app.scheduler import scheduler
        try:
            scheduler.execute_job_now(task_id)
            return {'message': f'Task {task_id} triggered'}
        except Exception as e:
            return {'message': str(e)}, 500


@api_ns_system.route('/system/logs')
class SystemLogs(Resource):
    def get(self):
        """Get recent log entries."""
        from lyrarr.app.get_args import args
        log_file = os.path.join(args.config_dir, 'log', 'lyrarr.log')

        if not os.path.exists(log_file):
            return []

        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                # Return last 500 lines
                return [line.rstrip() for line in lines[-500:]]
        except Exception:
            return []


@api_ns_system.route('/system/health')
class SystemHealth(Resource):
    def get(self):
        """Check system health."""
        from lyrarr.utilities.health import check_health
        return check_health()


@api_ns_system.route('/system/sync')
class SystemSync(Resource):
    def post(self):
        """Trigger a Lidarr sync now."""
        from threading import Thread
        from lyrarr.lidarr.sync import update_artists
        sync_thread = Thread(target=update_artists, kwargs={'force': True}, daemon=True)
        sync_thread.start()
        return {'message': 'Sync started'}


@api_ns_system.route('/system/test/lidarr')
class TestLidarr(Resource):
    def post(self):
        """Test Lidarr connection using provided details (does not require saving)."""
        data = request.get_json() or {}
        ip = data.get('ip', settings.lidarr.ip)
        port = data.get('port', settings.lidarr.port)
        base_url = data.get('base_url', settings.lidarr.base_url)
        apikey = data.get('apikey', settings.lidarr.apikey)
        ssl = data.get('ssl', settings.lidarr.ssl)

        import requests as req
        protocol = 'https' if ssl else 'http'
        url = f"{protocol}://{ip}:{port}{base_url.rstrip('/')}/api/v1/system/status"
        try:
            response = req.get(url, headers={'X-Api-Key': apikey}, timeout=10, verify=False)
            if response.status_code == 200:
                version = response.json().get('version', 'unknown')
                return {'message': f'Connected to Lidarr v{version}'}
            return {'message': f'Lidarr returned HTTP {response.status_code}'}, 500
        except req.exceptions.ConnectionError:
            return {'message': f'Connection refused at {ip}:{port}'}, 500
        except req.exceptions.Timeout:
            return {'message': 'Connection timed out'}, 500
        except Exception as e:
            return {'message': str(e)}, 500


@api_ns_system.route('/system/test/notification')
class TestNotification(Resource):
    def post(self):
        """Send a test notification to all configured channels."""
        from lyrarr.app.notifier import test_notification
        try:
            test_notification()
            return {'message': 'Test notification sent'}
        except Exception as e:
            return {'message': str(e)}, 500


@api_ns_system.route('/system/provider-stats')
class ProviderStats(Resource):
    def get(self):
        """Get provider health statistics."""
        from lyrarr.metadata.provider_utils import health_tracker
        return health_tracker.get_stats()


@api_ns_system.route('/system/providers')
class ProviderList(Resource):
    def get(self):
        """List all registered providers (auto-discovered)."""
        from lyrarr.metadata.registry import registry
        return registry.get_all_names()

