"""Publish lyrics back to LRCLIB (flow ported from LRCGET).

LRCLIB accepts community contributions guarded by a proof-of-work challenge:
POST /api/request-challenge returns {prefix, target}; the client finds a nonce
such that sha256(prefix + nonce) <= target (compared as big-endian numbers),
then POSTs the lyrics with header X-Publish-Token set to "{prefix}:{nonce}".
"""

import hashlib
import logging

import requests

# NOTE: lyrarr.metadata.lyrics.lrclib is imported lazily inside the network
# functions — it pulls in app config (argument parsing on import), which would
# make the pure solve_challenge() un-importable in unit tests.

logger = logging.getLogger(__name__)

_TIMEOUT = (5, 15)

# Safety cap for the nonce search, only there to bound a malformed target.
# LRCLIB's production target needs ~22M hashes on average (~12s measured), so
# a legitimate challenge essentially never hits this.
_MAX_ATTEMPTS = 400_000_000


class LrclibPublishError(Exception):
    """Publishing to LRCLIB failed; the message is user-presentable."""


def solve_challenge(prefix, target_hex, max_attempts=_MAX_ATTEMPTS):
    """Find a nonce so that sha256(prefix + nonce) <= target. Returns the nonce.

    CPU-bound by design (proof of work) — production difficulty takes a few
    seconds, so only call this from a user-triggered request.
    """
    target = int.from_bytes(bytes.fromhex(target_hex), 'big')
    prefix_bytes = prefix.encode()
    for nonce in range(max_attempts):
        digest = hashlib.sha256(prefix_bytes + str(nonce).encode()).digest()
        if int.from_bytes(digest, 'big') <= target:
            return str(nonce)
    raise LrclibPublishError('Could not solve the LRCLIB publish challenge')


def _request_challenge():
    from lyrarr.metadata.lyrics.lrclib import USER_AGENT, lrclib_api_url
    try:
        response = requests.post(
            f'{lrclib_api_url()}/request-challenge',
            timeout=_TIMEOUT,
            headers={'User-Agent': USER_AGENT},
        )
    except requests.exceptions.RequestException as e:
        raise LrclibPublishError(f'LRCLIB is unreachable: {e}') from e
    if response.status_code != 200:
        raise LrclibPublishError(
            f'LRCLIB challenge request failed (HTTP {response.status_code})'
        )
    data = response.json()
    return data['prefix'], data['target']


def publish_lyrics(track_name, artist_name, album_name, duration_s,
                   plain_lyrics=None, synced_lyrics=None):
    """Publish a track's lyrics to the configured LRCLIB instance.

    Raises LrclibPublishError with a user-presentable message on any failure.
    """
    from lyrarr.metadata.lyrics.lrclib import USER_AGENT, lrclib_api_url

    if not plain_lyrics and not synced_lyrics:
        raise LrclibPublishError('No lyrics content to publish')
    if not track_name or not artist_name:
        raise LrclibPublishError('Track and artist name are required to publish')
    if not duration_s:
        raise LrclibPublishError('Track duration is required to publish')

    prefix, target = _request_challenge()
    logger.info(f"Solving LRCLIB publish challenge for '{track_name}'...")
    nonce = solve_challenge(prefix, target)

    payload = {
        'trackName': track_name,
        'artistName': artist_name,
        'albumName': album_name or '',
        'duration': round(duration_s),
    }
    if plain_lyrics:
        payload['plainLyrics'] = plain_lyrics
    if synced_lyrics:
        payload['syncedLyrics'] = synced_lyrics

    try:
        response = requests.post(
            f'{lrclib_api_url()}/publish',
            json=payload,
            timeout=_TIMEOUT,
            headers={
                'User-Agent': USER_AGENT,
                'X-Publish-Token': f'{prefix}:{nonce}',
            },
        )
    except requests.exceptions.RequestException as e:
        raise LrclibPublishError(f'LRCLIB is unreachable: {e}') from e

    if response.status_code == 201:
        logger.info(f"Published lyrics for '{track_name}' to LRCLIB")
        return True

    try:
        message = response.json().get('message', '')
    except Exception:
        message = ''
    raise LrclibPublishError(
        f'LRCLIB rejected the publish (HTTP {response.status_code})'
        + (f': {message}' if message else '')
    )
