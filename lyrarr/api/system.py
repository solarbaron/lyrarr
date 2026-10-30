
import logging
import os
import platform
import re

from flask import request
from flask_restx import Namespace, Resource

from lyrarr import __version__
from lyrarr.app.config import SettingsError, base_url_slash_cleaner, get_settings, save_settings, settings

logger = logging.getLogger(__name__)

api_ns_system = Namespace('system', description='System operations')


def apply_settings_side_effects(result):
    """Act on save_settings' change flags (shared with backup restore)."""
    # Apply schedule-affecting changes immediately instead of waiting for a
    # restart. save_settings flags when a sync/backup interval or the Lidarr
    # toggle changed; the jobs use replace_existing so this re-arms them.
    if result.get('update_schedule'):
        try:
            from lyrarr.app.scheduler import scheduler
            scheduler.update_configurable_tasks()
        except Exception as e:
            logger.warning(f"Could not refresh scheduled tasks: {e}")

    # Reconnect SignalR when Lidarr connection details changed, so live
    # sync follows the new instance without a restart.
    if result.get('lidarr_changed'):
        import threading

        def _reconnect_signalr():
            try:
                from lyrarr.lidarr.signalr_client import lidarr_signalr_client
                lidarr_signalr_client.stop()
                lidarr_signalr_client.start()
            except Exception as e:
                logger.warning(f"SignalR reconnect failed: {e}")

        threading.Thread(target=_reconnect_signalr, daemon=True).start()


@api_ns_system.route('/system/status')
class SystemStatus(Resource):
    def get(self):
        """Get system status."""
        return {
            'version': os.environ.get('LYRARR_VERSION', __version__),
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
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return {'message': 'No data provided'}, 400

        settings_items = [(k, v) for k, v in data.items()]
        try:
            result = save_settings(settings_items)
        except SettingsError as e:
            return {'message': str(e)}, 400

        apply_settings_side_effects(result)
        return {'message': 'Settings saved', **result}


@api_ns_system.route('/system/tasks')
class SystemTasks(Resource):
    def get(self):
        """Get scheduled tasks."""
        from lyrarr.app.scheduler import scheduler
        return scheduler.get_task_list()

    def post(self):
        """Run a task immediately."""
        data = request.get_json(silent=True)
        task_id = data.get('taskId') if isinstance(data, dict) else None
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
            with open(log_file, encoding='utf-8', errors='ignore') as f:
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
        from lyrarr.lidarr.sync import request_sync, sync_in_progress
        # debounce=False so a manual sync starts immediately, but still respects
        # the single-flight lock so it won't run on top of an in-progress sync.
        already_running = sync_in_progress()
        request_sync(force=True, debounce=False)
        if already_running:
            # The in-flight run will drain this request as a follow-up pass.
            return {'message': 'Sync already running — another pass queued'}
        return {'message': 'Sync started'}


# Hostname, IPv4 or (bracketed) IPv6 literal — no scheme, path, userinfo or spaces.
_HOST_RE = re.compile(r'^(\[[0-9A-Fa-f:.]+\]|[0-9A-Fa-f:]*:[0-9A-Fa-f:.]+|[A-Za-z0-9_]([A-Za-z0-9_.-]*[A-Za-z0-9_])?)$')


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


@api_ns_system.route('/system/test/lidarr')
class TestLidarr(Resource):
    def post(self):
        """Test Lidarr connection using provided details (does not require saving)."""
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return {'message': 'Invalid request body'}, 400

        ip = str(data.get('ip', settings.lidarr.ip) or '').strip()
        if not _HOST_RE.match(ip):
            return {'message': 'Invalid Lidarr host — enter a hostname or IP without http:// or a path'}, 400
        if ':' in ip and not ip.startswith('['):
            ip = f'[{ip}]'  # bare IPv6 literal
        try:
            port = int(data.get('port', settings.lidarr.port))
        except (TypeError, ValueError):
            return {'message': 'Invalid Lidarr port'}, 400
        if not 1 <= port <= 65535:
            return {'message': 'Invalid Lidarr port'}, 400
        base_url = data.get('base_url', settings.lidarr.base_url) or '/'
        if not isinstance(base_url, str) or any(c in base_url for c in '?#@\\ '):
            return {'message': 'Invalid Lidarr base URL'}, 400
        base_url = base_url_slash_cleaner('/' + base_url.strip()).rstrip('/')
        ssl = _as_bool(data.get('ssl', settings.lidarr.ssl))
        verify_ssl = _as_bool(data.get('verify_ssl', getattr(settings.lidarr, 'verify_ssl', False)))

        # Only send the STORED API key to the stored Lidarr address. Otherwise a
        # caller could point the test at their own host and harvest the key.
        apikey = data.get('apikey')
        if not apikey:
            stored_ip = str(settings.lidarr.ip or '').strip()
            if ':' in stored_ip and not stored_ip.startswith('['):
                stored_ip = f'[{stored_ip}]'
            stored_base = base_url_slash_cleaner('/' + str(settings.lidarr.base_url or '')).rstrip('/')
            same_target = (ip.lower() == stored_ip.lower() and port == int(settings.lidarr.port)
                           and base_url == stored_base and ssl == bool(settings.lidarr.ssl))
            if not same_target:
                return {'message': 'Enter the Lidarr API key to test a different Lidarr address'}, 400
            apikey = settings.lidarr.apikey
        apikey = str(apikey)

        import requests as req
        protocol = 'https' if ssl else 'http'
        url = f"{protocol}://{ip}:{port}{base_url}/api/v1/system/status"
        try:
            # No redirects: requests would forward the X-Api-Key header to
            # whatever host a redirect points at.
            response = req.get(url, headers={'X-Api-Key': apikey}, timeout=10, verify=verify_ssl,
                               allow_redirects=False)
            if response.status_code == 200:
                version = response.json().get('version', 'unknown')
                return {'message': f'Connected to Lidarr v{version}'}
            return {'message': f'Lidarr returned HTTP {response.status_code}'}, 500
        except req.exceptions.ConnectionError:
            return {'message': f'Connection refused at {ip}:{port}'}, 500
        except req.exceptions.Timeout:
            return {'message': 'Connection timed out'}, 500
        except Exception as e:
            return {'message': str(e).replace(apikey, '***') if apikey else str(e)}, 500


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

