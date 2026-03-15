# coding=utf-8

"""
TheAudioDB Cover Art Provider.
Uses the TheAudioDB API to find album artwork by MusicBrainz ID.
Free test API key ("2") included — works for most lookups.
"""

import logging
import requests

from lyrarr.app.config import settings
from lyrarr.metadata.base import CoverProvider

logger = logging.getLogger(__name__)

TADB_BASE_URL = 'https://theaudiodb.com/api/v1/json'


class TheAudioDBCoverProvider(CoverProvider):
    """Fetch cover art from TheAudioDB (free test key available)."""

    name = 'theaudiodb'

    @property
    def _api_key(self):
        return settings.theaudiodb.apikey or '2'

    def search(self, mb_release_group_id=None, artist_name=None, album_name=None, **kwargs):
        """
        Search for cover art by MusicBrainz Release Group ID or artist+album name.

        Returns a list of cover art results:
        [{'url': str, 'url_small': str, 'type': str, 'provider': str}]
        """
        results = []

        # Try MBID lookup first (most reliable)
        if mb_release_group_id:
            results.extend(self._fetch_by_mbid(mb_release_group_id))

        # Fall back to name search
        if not results and artist_name and album_name:
            results.extend(self._search_by_name(artist_name, album_name))

        return results

    def _fetch_by_mbid(self, mbid):
        """Fetch album art by MusicBrainz Release Group ID."""
        results = []
        url = f"{TADB_BASE_URL}/{self._api_key}/album-mb.php"

        try:
            response = requests.get(url, params={'i': mbid}, timeout=15)
            if response.status_code == 200:
                data = response.json()
                album = data.get('album')
                if album and isinstance(album, list) and len(album) > 0:
                    album = album[0]
                    results.extend(self._extract_art(album))
        except requests.exceptions.RequestException as e:
            logger.error(f"TheAudioDB MBID lookup error: {e}")

        return results

    def _search_by_name(self, artist_name, album_name):
        """Search for album art by artist and album name."""
        results = []
        url = f"{TADB_BASE_URL}/{self._api_key}/searchalbum.php"

        try:
            response = requests.get(
                url,
                params={'s': artist_name, 'a': album_name},
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                for album in (data.get('album') or [])[:3]:
                    results.extend(self._extract_art(album))
        except requests.exceptions.RequestException as e:
            logger.error(f"TheAudioDB name search error: {e}")

        return results

    def _extract_art(self, album_data):
        """Extract artwork URLs from album data."""
        results = []

        # Album thumbnail (primary cover art)
        thumb = album_data.get('strAlbumThumb')
        if thumb:
            results.append({
                'url': thumb,
                'url_small': thumb + '/preview' if thumb else '',
                'url_large': thumb,
                'type': 'front',
                'provider': self.name,
                'title': album_data.get('strAlbum', ''),
                'artist': album_data.get('strArtist', ''),
            })

        # CD art (disc image)
        cdart = album_data.get('strAlbumCDart')
        if cdart:
            results.append({
                'url': cdart,
                'url_small': cdart + '/preview' if cdart else '',
                'url_large': cdart,
                'type': 'cdart',
                'provider': self.name,
            })

        return results

    def download(self, url):
        """Download cover art image from URL. Returns bytes or None."""
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.content
        except requests.exceptions.RequestException as e:
            logger.error(f"TheAudioDB cover download error: {e}")
        return None
