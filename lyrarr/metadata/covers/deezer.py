
"""
Deezer Cover Art Provider.
Uses the public Deezer API to search for album cover art.
No API key required.
"""

import logging

import requests

from lyrarr.metadata.base import CoverProvider
from lyrarr.metadata.cover_fetch import safe_download
from lyrarr.metadata.provider_utils import is_transient_status, note_transient_error

logger = logging.getLogger(__name__)

DEEZER_API_BASE = 'https://api.deezer.com'


class DeezerCoverProvider(CoverProvider):
    """Fetch cover art from Deezer (free, no API key)."""

    name = 'deezer'

    def search(self, artist_name=None, album_name=None, **kwargs):
        """
        Search for cover art by artist + album name.

        Returns a list of cover art results:
        [{'url': str, 'url_small': str, 'url_large': str, 'type': str, 'provider': str}]
        """
        if not album_name:
            return []

        results = []
        query = f'artist:"{artist_name}" album:"{album_name}"' if artist_name else album_name

        try:
            response = requests.get(
                f"{DEEZER_API_BASE}/search/album",
                params={'q': query, 'limit': 5},
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                for item in data.get('data', []):
                    cover = item.get('cover_xl') or item.get('cover_big') or item.get('cover_medium')
                    if cover:
                        results.append({
                            'url': item.get('cover_xl', cover),
                            'url_small': item.get('cover_small', ''),
                            'url_large': item.get('cover_big', cover),
                            'type': 'front',
                            'provider': self.name,
                            'title': item.get('title', ''),
                            'artist': item.get('artist', {}).get('name', ''),
                        })
            elif is_transient_status(response.status_code):
                note_transient_error()
                logger.warning(f"Deezer API returned {response.status_code}, skipping")
            else:
                logger.warning(f"Deezer API returned {response.status_code}")
        except requests.exceptions.RequestException as e:
            note_transient_error()
            logger.error(f"Deezer cover search error: {e}")

        return results

    def download(self, url):
        """Download cover art image from URL (allow-listed hosts only). Returns bytes or None."""
        return safe_download(url, self.name)
