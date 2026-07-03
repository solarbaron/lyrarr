
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
    }

    for service, status in health.items():
        if not status['healthy']:
            logger.warning(f"Health check failed for {service}: {status.get('error', 'Unknown error')}")
        _announce_transition(service, status)

    return health


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
        url = f"{protocol}://{settings.lidarr.ip}:{settings.lidarr.port}{settings.lidarr.base_url}api/v1/system/status"
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
