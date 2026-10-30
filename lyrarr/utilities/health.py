
import logging
import threading

import requests

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)

# Last known health per service, so transitions (up→down, down→up) can be
# announced exactly once instead of re-toasting on every periodic check.
_last_state = {}
_state_lock = threading.Lock()


def check_health():
    """Check the health of connected services."""
    health = {
        'lidarr': _check_lidarr_health(),
        'auth': _check_auth_health(),
    }

    for service, status in health.items():
        if not status['healthy']:
            logger.warning(f"Health check failed for {service}: {status.get('error', 'Unknown error')}")
        # Connection flips are announced; configuration warnings are not.
        if service in _CONNECTION_SERVICES:
            _announce_transition(service, status)

    return health


_CONNECTION_SERVICES = frozenset({'lidarr'})


def _check_auth_health():
    """Warn while authentication is disabled — anyone who can reach the UI can
    change settings and lyrics files."""
    if getattr(settings.auth, 'type', None):
        return {'healthy': True, 'status': 'enabled'}
    return {
        'healthy': False,
        'status': 'disabled',
        'level': 'warning',
        'error': 'Authentication is disabled — enable it in Settings '
                 'if Lyrarr is reachable beyond a trusted network',
    }


def _announce_transition(service, status):
    """Emit an SSE event when a service flips between healthy and unhealthy."""
    healthy = bool(status.get('healthy'))
    with _state_lock:
        previous = _last_state.get(service)
        _last_state[service] = healthy
    # First observation sets the baseline silently; only real flips announce.
    if previous is None or previous == healthy:
        return

    from lyrarr.app.event_handler import event_stream
    if healthy:
        event_stream(type='health', payload={
            'service': service, 'healthy': True,
            'message': f'{service.capitalize()} connection restored',
        })
    else:
        event_stream(type='health', payload={
            'service': service, 'healthy': False,
            'message': f'{service.capitalize()} is unreachable: {status.get("error", "unknown error")}',
        })


def _check_lidarr_health():
    """Check Lidarr connectivity."""
    if not settings.general.use_lidarr:
        return {'healthy': True, 'status': 'disabled'}

    try:
        protocol = 'https' if settings.lidarr.ssl else 'http'
        # Same URL construction as the API client: works with and without a
        # Lidarr URL base, with or without a trailing slash.
        url = (f"{protocol}://{settings.lidarr.ip}:{settings.lidarr.port}"
               f"{(settings.lidarr.base_url or '').rstrip('/')}/api/v1/system/status")
        response = requests.get(
            url,
            headers={'X-Api-Key': settings.lidarr.apikey},
            timeout=10,
            verify=getattr(settings.lidarr, 'verify_ssl', False),
        )
        if response.status_code == 200:
            return {'healthy': True, 'status': 'connected'}
        else:
            return {'healthy': False, 'error': f"HTTP {response.status_code}"}
    except requests.exceptions.ConnectionError:
        return {'healthy': False, 'error': 'Connection refused'}
    except requests.exceptions.Timeout:
        return {'healthy': False, 'error': 'Connection timeout'}
    except Exception as e:
        return {'healthy': False, 'error': str(e)}
