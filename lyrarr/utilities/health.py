# coding=utf-8

import logging
import requests

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)


def check_health():
    """Check the health of connected services."""
    health = {
        'lidarr': _check_lidarr_health(),
    }

    for service, status in health.items():
        if not status['healthy']:
            logger.warning(f"Health check failed for {service}: {status.get('error', 'Unknown error')}")

    return health


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
            verify=False
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
