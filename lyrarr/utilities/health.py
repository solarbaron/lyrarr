
import logging
import threading
import time

import requests

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)

# Last known health per service, so transitions (up→down, down→up) can be
# announced exactly once instead of re-toasting on every periodic check.
_last_state = {}
_state_lock = threading.Lock()


def check_health():
    """Check the health of connected services and of every library root.

    An unreachable music folder adds one `library:<root>` entry (and one
    alert when it goes down) per root; reachable roots add nothing.
    """
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

    health.update(_check_library_roots())
    return health


# Library reachability is sampled from disk; the health endpoint is polled by
# the UI, so the result is reused for a minute.
_LIBRARY_TTL = 60.0
_library_cache = {'at': 0.0, 'value': None}


def _check_library_roots(reach=None):
    """{'library:<root>': status} for each unreachable library root."""
    if reach is None:
        now = time.monotonic()
        cached = _library_cache['value']
        if cached is not None and now - _library_cache['at'] < _LIBRARY_TTL:
            return dict(cached)
        try:
            from lyrarr.metadata.lyrics_store import library_reachability
            reach = library_reachability()
        except Exception as e:
            logger.debug(f"Library reachability check failed: {e}")
            return {}
    out = note_library_reachability(reach)
    _library_cache.update(at=time.monotonic(), value=dict(out))
    return out


def library_root_message(root):
    return f"Music folder not reachable: {root}. Tracks under it are skipped until it is back."


def note_library_reachability(reach):
    """Announce per-root reachability changes (one alert per unreachable root).

    Used by the health check, and by the worker/sync/audit with the
    Reachability they already computed. Returns the health entries for the
    unreachable roots. A root that isn't mounted but has no tracks under it
    (e.g. an unused second Lidarr root folder) is informational: no track
    depends on it, so it raises neither a health entry nor an alert.
    """
    out = {}
    for root, status in getattr(reach, 'roots', {}).items():
        service = f'library:{root}'
        unused = not status.ok and not status.exists and not status.checked
        healthy = bool(status.ok) or unused
        entry = {
            'healthy': healthy,
            'status': 'reachable' if status.ok else ('unused' if unused else 'unreachable'),
            'root': root,
        }
        if not healthy:
            entry['error'] = library_root_message(root)
            entry['checked'] = status.checked
            entry['missing'] = status.missing
            out[service] = entry
        _announce_root(service, entry)
    return out


def _announce_root(service, status):
    """Like _announce_transition, but an unreachable root is announced on its
    first observation too (a library that is down at startup must be seen)."""
    healthy = bool(status.get('healthy'))
    with _state_lock:
        previous = _last_state.get(service)
        _last_state[service] = healthy
    if previous == healthy or (previous is None and healthy):
        return
    from lyrarr.app.event_handler import event_stream
    event_stream(type='health', payload={
        'service': service, 'healthy': healthy,
        'message': f"Music folder reachable again: {status.get('root')}" if healthy
        else status.get('error') or library_root_message(status.get('root')),
    })


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
