# coding=utf-8

import logging
import requests

from lyrarr.metadata.base import LyricsProvider

logger = logging.getLogger(__name__)

LRCLIB_BASE_URL = 'https://lrclib.net/api'


class LRCLIBProvider(LyricsProvider):
    """Fetch synced and plain lyrics from LRCLIB (free, no API key needed)."""

    name = 'lrclib'

    def search(self, track_name=None, artist_name=None, album_name=None, duration=None):
        """
        Search for lyrics.

        Returns a list of lyrics results:
        [{'synced_lyrics': str|None, 'plain_lyrics': str|None, 'provider': str, 'score': float}]
        """
        results = []

        # Try exact match first
        exact = self._get_exact(track_name, artist_name, album_name, duration)
        if exact:
            results.append(exact)

        # Fall back to search
        if not results:
            search_results = self._search(track_name, artist_name)
            results.extend(search_results)

        return results

    def _get_exact(self, track_name, artist_name, album_name, duration):
        """Try to get exact match from LRCLIB."""
        if not track_name or not artist_name:
            return None

        params = {
            'track_name': track_name,
            'artist_name': artist_name,
        }
        if album_name:
            params['album_name'] = album_name
        if duration:
            params['duration'] = duration // 1000 if duration > 1000 else duration  # convert ms to seconds

        try:
            response = requests.get(
                f"{LRCLIB_BASE_URL}/get",
                params=params,
                timeout=15,
                headers={'User-Agent': 'Lyrarr/1.0'}
            )
            if response.status_code == 200:
                data = response.json()
                synced = data.get('syncedLyrics')
                plain = data.get('plainLyrics')
                if synced or plain:
                    return {
                        'synced_lyrics': synced,
                        'plain_lyrics': plain,
                        'provider': self.name,
                        'score': 1.0 if synced else 0.8,
                        'source_id': data.get('id'),
                    }
        except requests.exceptions.RequestException as e:
            logger.error(f"LRCLIB exact match error: {e}")

        return None

    def _search(self, track_name, artist_name):
        """Search LRCLIB for lyrics."""
        results = []

        if not track_name:
            return results

        params = {'q': track_name}
        if artist_name:
            params['artist_name'] = artist_name

        try:
            response = requests.get(
                f"{LRCLIB_BASE_URL}/search",
                params=params,
                timeout=15,
                headers={'User-Agent': 'Lyrarr/1.0'}
            )
            if response.status_code == 200:
                data = response.json()
                for item in data[:5]:  # Limit to top 5 results
                    synced = item.get('syncedLyrics')
                    plain = item.get('plainLyrics')
                    if synced or plain:
                        results.append({
                            'synced_lyrics': synced,
                            'plain_lyrics': plain,
                            'provider': self.name,
                            'score': 0.7 if synced else 0.5,
                            'source_id': item.get('id'),
                            'track_name': item.get('trackName', ''),
                            'artist_name': item.get('artistName', ''),
                        })
        except requests.exceptions.RequestException as e:
            logger.error(f"LRCLIB search error: {e}")

        return results
