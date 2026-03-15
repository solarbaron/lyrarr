# coding=utf-8

"""
Musixmatch Lyrics Provider.
Uses the Musixmatch API to fetch synced and plain lyrics.
Requires a free API key (1000 requests/day on free tier).
"""

import logging
import requests

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)

MUSIXMATCH_BASE = 'https://api.musixmatch.com/ws/1.1'


class MusixmatchProvider:
    """Fetch synced and plain lyrics from Musixmatch (requires API key)."""

    name = 'musixmatch'

    @property
    def _api_key(self):
        return settings.musixmatch.apikey

    def search(self, track_name=None, artist_name=None, album_name=None, duration=None, **kwargs):
        """
        Search for lyrics on Musixmatch.

        Returns a list of lyrics results:
        [{'synced_lyrics': str|None, 'plain_lyrics': str|None, 'provider': str, 'score': float}]
        """
        if not self._api_key:
            logger.debug("Musixmatch API key not configured, skipping")
            return []

        if not track_name:
            return []

        results = []

        # Step 1: Find the track
        track_id = self._find_track(track_name, artist_name, album_name, duration)
        if not track_id:
            return results

        # Step 2: Get plain lyrics
        plain_lyrics = self._get_lyrics(track_id)

        # Step 3: Try to get synced lyrics (subtitle)
        synced_lyrics = self._get_subtitle(track_id)

        if plain_lyrics or synced_lyrics:
            results.append({
                'synced_lyrics': synced_lyrics,
                'plain_lyrics': plain_lyrics,
                'provider': self.name,
                'score': 0.95 if synced_lyrics else 0.75,
            })

        return results

    def _find_track(self, track_name, artist_name=None, album_name=None, duration=None):
        """Search for a track and return its Musixmatch track_id."""
        params = {
            'apikey': self._api_key,
            'q_track': track_name,
            's_track_rating': 'desc',
            'page_size': 5,
        }
        if artist_name:
            params['q_artist'] = artist_name
        if album_name:
            params['q_album'] = album_name
        if duration:
            # Musixmatch expects duration in seconds
            params['f_track_length'] = duration // 1000 if duration > 1000 else duration

        try:
            response = requests.get(
                f"{MUSIXMATCH_BASE}/track.search",
                params=params,
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                body = data.get('message', {}).get('body', {})
                track_list = body.get('track_list', [])
                if track_list:
                    return track_list[0].get('track', {}).get('track_id')
        except requests.exceptions.RequestException as e:
            logger.error(f"Musixmatch track search error: {e}")

        return None

    def _get_lyrics(self, track_id):
        """Get plain lyrics for a track by its Musixmatch ID."""
        try:
            response = requests.get(
                f"{MUSIXMATCH_BASE}/track.lyrics.get",
                params={'apikey': self._api_key, 'track_id': track_id},
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                body = data.get('message', {}).get('body', {})
                lyrics = body.get('lyrics', {})
                lyrics_body = lyrics.get('lyrics_body', '')
                if lyrics_body:
                    # Musixmatch free tier includes a disclaimer at the end — remove it
                    disclaimer_marker = '******* This Lyrics is NOT'
                    if disclaimer_marker in lyrics_body:
                        lyrics_body = lyrics_body[:lyrics_body.index(disclaimer_marker)].strip()
                    return lyrics_body if lyrics_body else None
        except requests.exceptions.RequestException as e:
            logger.error(f"Musixmatch lyrics fetch error: {e}")

        return None

    def _get_subtitle(self, track_id):
        """Get synced lyrics (subtitle) for a track by its Musixmatch ID."""
        try:
            response = requests.get(
                f"{MUSIXMATCH_BASE}/track.subtitle.get",
                params={
                    'apikey': self._api_key,
                    'track_id': track_id,
                    'subtitle_format': 'lrc',
                },
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                body = data.get('message', {}).get('body', {})
                subtitle = body.get('subtitle', {})
                subtitle_body = subtitle.get('subtitle_body', '')
                if subtitle_body:
                    return subtitle_body
        except requests.exceptions.RequestException as e:
            logger.error(f"Musixmatch subtitle fetch error: {e}")

        return None
