"""
Shared, safe cover-image downloading and cover-result checks.

Every cover provider's download(url) goes through safe_download(): only http(s)
URLs on that provider's own image hosts are fetched (re-checked on every
redirect hop), the body is streamed under a byte cap, and the bytes must really
be an image (JPEG/PNG/WebP/GIF by magic number). Anything else returns None.

Also: sniff_image() for the real type of downloaded bytes (so a PNG isn't saved
as cover.jpg / embedded as image/jpeg), and cover_result_matches() so a name
search (Deezer/iTunes/TheAudioDB) can't hand back some other album's cover.
"""

import logging
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse

import requests

from lyrarr.metadata.normalize import get_primary_artist, normalize_artist, normalize_title

logger = logging.getLogger(__name__)

# Hosts each provider's image URLs (and their redirects) may live on. A host
# matches an entry when it equals it or is a subdomain of it.
COVER_HOSTS = {
    # coverartarchive.org/release/<id>/front redirects to archive.org storage
    # (archive.org/download/... then ia80xxxx.us.archive.org).
    'musicbrainz': ('coverartarchive.org', 'archive.org', 'musicbrainz.org'),
    'fanart': ('fanart.tv',),                     # assets.fanart.tv
    # cover_xl etc. are e-cdns-images.dzcdn.net / cdn-images.dzcdn.net; the
    # api.deezer.com/album/<id>/image form redirects there.
    'deezer': ('dzcdn.net', 'deezer.com'),
    'itunes': ('mzstatic.com',),                  # is1-ssl.mzstatic.com ...
    'theaudiodb': ('theaudiodb.com',),            # www.theaudiodb.com, r2.theaudiodb.com
}

MAX_COVER_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 5
_TIMEOUT = (5, 30)
_REDIRECT_CODES = (301, 302, 303, 307, 308)

# Album-name / artist similarity required to accept a name-search cover result.
COVER_TITLE_MIN = 0.75
COVER_ARTIST_MIN = 0.6


def host_allowed(url, provider_name):
    """Whether url is http(s) on a default port and on one of the provider's hosts."""
    allowed = COVER_HOSTS.get(provider_name)
    if not allowed:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https') or port not in (None, 80, 443):
        return False
    host = (parsed.hostname or '').lower().rstrip('.')
    if not host:
        return False
    return any(host == d or host.endswith('.' + d) for d in allowed)


def sniff_image(data):
    """Identify image bytes by magic number.

    Returns (extension, mime) — e.g. ('jpg', 'image/jpeg') — or None when the
    bytes aren't a JPEG, PNG, WebP or GIF.
    """
    if not data:
        return None
    head = bytes(data[:16])
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg', 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png', 'image/png'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'webp', 'image/webp'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'gif', 'image/gif'
    return None


def safe_download(url, provider_name, max_bytes=MAX_COVER_BYTES):
    """Fetch a cover image for a provider. Returns the bytes, or None (logged).

    Redirects are followed by hand so every hop's host is checked against the
    provider's allow-list; the body is streamed and abandoned past max_bytes.
    """
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if not host_allowed(current, provider_name):
            logger.warning(f"Refusing {provider_name} cover download from disallowed URL: {current}")
            return None
        try:
            response = requests.get(current, timeout=_TIMEOUT, stream=True, allow_redirects=False)
        except requests.exceptions.RequestException as e:
            logger.error(f"{provider_name} cover download error: {e}")
            return None
        try:
            if response.status_code in _REDIRECT_CODES:
                location = response.headers.get('Location')
                if not location:
                    logger.warning(f"{provider_name} cover redirect without Location: {current}")
                    return None
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                logger.warning(f"{provider_name} cover download returned HTTP {response.status_code}: {current}")
                return None
            try:
                declared = int(response.headers.get('Content-Length') or 0)
            except ValueError:
                declared = 0
            if declared > max_bytes:
                logger.warning(f"{provider_name} cover too large ({declared} bytes): {current}")
                return None
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    logger.warning(f"{provider_name} cover exceeded {max_bytes} bytes: {current}")
                    return None
                chunks.append(chunk)
            data = b''.join(chunks)
        except requests.exceptions.RequestException as e:
            logger.error(f"{provider_name} cover download error: {e}")
            return None
        finally:
            response.close()

        if not sniff_image(data):
            logger.warning(f"{provider_name} cover download is not a JPEG/PNG/WebP/GIF image: {current}")
            return None
        return data

    logger.warning(f"{provider_name} cover download: too many redirects from {url}")
    return None


def cover_result_matches(result, artist_name, album_name):
    """Whether a name-search cover result is really the requested album.

    Results without a 'title' (MBID lookups: Cover Art Archive, fanart.tv)
    are already tied to the release group and pass. Otherwise the album title
    must be close (normalized, so "(Remastered)"/"(Deluxe Edition)" don't
    matter) and, when both sides have one, so must the artist.
    """
    r_title = result.get('title')
    if r_title is None:
        return True
    q_title = normalize_title(album_name or '')
    r_title = normalize_title(r_title or '')
    if not q_title or not r_title:
        return False
    if SequenceMatcher(None, q_title, r_title).ratio() < COVER_TITLE_MIN:
        return False

    r_artist = result.get('artist')
    if artist_name and r_artist is not None:
        if not r_artist:
            return False
        q_full, r_full = normalize_artist(artist_name), normalize_artist(r_artist)
        ratio = max(
            SequenceMatcher(None, q_full, r_full).ratio(),
            SequenceMatcher(None, get_primary_artist(artist_name), get_primary_artist(r_artist)).ratio(),
        )
        if ratio < COVER_ARTIST_MIN:
            return False
    return True
