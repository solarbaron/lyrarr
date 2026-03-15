# coding=utf-8

"""
Provider utilities: per-provider rate limiting, retry with exponential backoff,
and provider health tracking.
"""

import logging
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)


class ProviderRateLimiter:
    """Per-provider token-bucket rate limiter.

    Each provider has its own rate limit. Calls to `wait(provider_name)` will
    block until enough time has elapsed since the last call to that provider.
    """

    # Default minimum seconds between calls per provider
    DEFAULT_RATES = {
        'musicbrainz': 1.5,   # CAA is generous but MusicBrainz API is strict
        'fanart': 1.0,
        'deezer': 0.5,        # Deezer is generous
        'itunes': 0.5,        # iTunes is generous
        'theaudiodb': 1.0,
        'lrclib': 0.5,        # LRCLIB is generous
        'genius': 1.0,
        'musixmatch': 1.5,    # Free tier has daily limits
        'netease': 0.5,
    }

    def __init__(self):
        self._last_call = {}
        self._lock = threading.Lock()

    def wait(self, provider_name):
        """Block until the rate limit for this provider allows a new call."""
        rate = self.DEFAULT_RATES.get(provider_name, 1.0)

        with self._lock:
            last = self._last_call.get(provider_name, 0)
            elapsed = time.monotonic() - last
            if elapsed < rate:
                wait_time = rate - elapsed
                time.sleep(wait_time)
            self._last_call[provider_name] = time.monotonic()


class ProviderHealthTracker:
    """Track provider success/failure rates and auto-skip unhealthy providers.

    If a provider fails too many times in a row, it's temporarily disabled
    to avoid wasting time on broken providers.
    """

    MAX_CONSECUTIVE_FAILURES = 5
    COOLDOWN_MINUTES = 15

    def __init__(self):
        self._stats = defaultdict(lambda: {
            'successes': 0,
            'failures': 0,
            'consecutive_failures': 0,
            'last_failure': None,
            'disabled_until': None,
        })
        self._lock = threading.Lock()

    def is_available(self, provider_name):
        """Check if a provider is currently available (not in cooldown)."""
        with self._lock:
            stats = self._stats[provider_name]
            if stats['disabled_until']:
                if datetime.now() >= stats['disabled_until']:
                    # Cooldown expired, re-enable
                    stats['disabled_until'] = None
                    stats['consecutive_failures'] = 0
                    logger.info(f"Provider '{provider_name}' cooldown expired, re-enabling")
                    return True
                return False
            return True

    def record_success(self, provider_name):
        """Record a successful provider call."""
        with self._lock:
            stats = self._stats[provider_name]
            stats['successes'] += 1
            stats['consecutive_failures'] = 0

    def record_failure(self, provider_name, error=None):
        """Record a failed provider call. May trigger cooldown."""
        with self._lock:
            stats = self._stats[provider_name]
            stats['failures'] += 1
            stats['consecutive_failures'] += 1
            stats['last_failure'] = datetime.now()

            if stats['consecutive_failures'] >= self.MAX_CONSECUTIVE_FAILURES:
                stats['disabled_until'] = datetime.now() + timedelta(minutes=self.COOLDOWN_MINUTES)
                logger.warning(
                    f"Provider '{provider_name}' disabled for {self.COOLDOWN_MINUTES}m "
                    f"after {stats['consecutive_failures']} consecutive failures"
                    f"{f': {error}' if error else ''}"
                )

    def get_stats(self):
        """Return a snapshot of all provider stats."""
        with self._lock:
            return {
                name: {
                    'successes': s['successes'],
                    'failures': s['failures'],
                    'consecutive_failures': s['consecutive_failures'],
                    'available': self.is_available(name),
                }
                for name, s in self._stats.items()
            }


def retry_with_backoff(fn, max_retries=2, base_delay=1.0, provider_name=''):
    """Execute a function with retry and exponential backoff.

    Returns the function result, or None if all retries fail.
    On transient errors (timeouts, 429, 5xx), retries with increasing delay.
    On permanent errors (4xx), returns None immediately.
    """
    import requests

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.debug(f"[{provider_name}] Timeout, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status == 429 and attempt < max_retries:
                delay = base_delay * (2 ** attempt) * 2  # Longer backoff for rate limits
                logger.debug(f"[{provider_name}] Rate limited (429), retrying in {delay:.1f}s")
                time.sleep(delay)
                last_error = e
            elif 500 <= status < 600 and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.debug(f"[{provider_name}] Server error ({status}), retrying in {delay:.1f}s")
                time.sleep(delay)
                last_error = e
            else:
                # 4xx (except 429) = permanent error, don't retry
                logger.debug(f"[{provider_name}] HTTP {status}, not retrying")
                return None
        except requests.exceptions.ConnectionError as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.debug(f"[{provider_name}] Connection error, retrying in {delay:.1f}s")
                time.sleep(delay)
        except Exception as e:
            # Unknown error, don't retry
            logger.error(f"[{provider_name}] Unexpected error: {e}")
            return None

    if last_error:
        logger.warning(f"[{provider_name}] All {max_retries + 1} attempts failed: {last_error}")
    return None


# Singleton instances
rate_limiter = ProviderRateLimiter()
health_tracker = ProviderHealthTracker()
