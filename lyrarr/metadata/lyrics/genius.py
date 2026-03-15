# coding=utf-8

import logging
import re
import requests

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)

GENIUS_API_BASE = 'https://api.genius.com'


class GeniusProvider:
    """Fetch plain lyrics from Genius (requires API key)."""

    name = 'genius'

    @property
    def _api_key(self):
        return settings.genius.apikey

    def search(self, track_name=None, artist_name=None, **kwargs):
        """
        Search for lyrics on Genius.

        Returns a list of lyrics results:
        [{'plain_lyrics': str, 'synced_lyrics': None, 'provider': str, 'score': float}]
        """
        if not self._api_key:
            logger.debug("Genius API key not configured, skipping")
            return []

        if not track_name:
            return []

        results = []
        query = f"{artist_name} {track_name}" if artist_name else track_name

        try:
            response = requests.get(
                f"{GENIUS_API_BASE}/search",
                params={'q': query},
                headers={
                    'Authorization': f'Bearer {self._api_key}',
                },
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                hits = data.get('response', {}).get('hits', [])

                for hit in hits[:3]:  # Limit to top 3
                    result = hit.get('result', {})
                    song_url = result.get('url', '')

                    if song_url:
                        lyrics = self._scrape_lyrics(song_url)
                        if lyrics:
                            results.append({
                                'synced_lyrics': None,  # Genius doesn't provide synced lyrics
                                'plain_lyrics': lyrics,
                                'provider': self.name,
                                'score': 0.6,
                                'source_url': song_url,
                                'title': result.get('title', ''),
                                'artist': result.get('primary_artist', {}).get('name', ''),
                            })
            else:
                logger.warning(f"Genius API returned {response.status_code}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Genius search error: {e}")

        return results

    def _scrape_lyrics(self, song_url):
        """Scrape lyrics from a Genius song page. Returns plain text lyrics or None."""
        try:
            response = requests.get(song_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Lyrarr/1.0)'
            })
            if response.status_code == 200:
                # Simple regex-based extraction from Genius page
                # Look for lyrics in the data-lyrics-container divs
                pattern = r'<div[^>]*data-lyrics-container="true"[^>]*>(.*?)</div>'
                matches = re.findall(pattern, response.text, re.DOTALL)
                if matches:
                    lyrics_html = ''.join(matches)
                    # Clean HTML tags
                    lyrics = re.sub(r'<br\s*/?>', '\n', lyrics_html)
                    lyrics = re.sub(r'<[^>]+>', '', lyrics)
                    lyrics = lyrics.strip()
                    if lyrics:
                        return lyrics
        except requests.exceptions.RequestException as e:
            logger.error(f"Genius scrape error: {e}")
        return None
