# coding=utf-8

"""
Musixmatch Lyrics Provider.
Uses the Musixmatch API to fetch synced and plain lyrics.
Requires a free API key (1000 requests/day on free tier).
"""

import logging
import requests

from lyrarr.app.config import settings
from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import clean_for_search, duration_ms_to_seconds

logger = logging.getLogger(__name__)

MUSIXMATCH_BASE = 'https://api.musixmatch.com/ws/1.1'


class MusixmatchProvider(LyricsProvider):
    """Fetch synced and plain lyrics from Musixmatch (requires API key).

    Uses normalized queries and picks the best-scoring track result
    instead of blindly taking the first. Falls back to title-only search
    if the full query returns no results.
    """

    name = 'musixmatch'

    @property
    def _api_key(self):
        return settings.musixmatch.apikey

    def search(self, track_name=None, artist_name=None, album_name=None, duration=None, **kwargs):
        """
        Search for lyrics on Musixmatch with normalized queries and real scoring.

        Returns a list of lyrics results:
        [{'synced_lyrics': str|None, 'plain_lyrics': str|None, 'provider': str,
          'score': float, 'match_details': dict}]
        """
        if not self._api_key:
            logger.debug("Musixmatch API key not configured, skipping")
            return []

        if not track_name:
            return []

        meta = clean_for_search(track_name, artist_name or '')
        results = []

        # Step 1: Find the best matching track
        track_info = self._find_best_track(
            meta['title_clean'], meta['artist_clean'],
            album_name, duration, track_name, artist_name
        )

        # Step 2: Fallback — search with title only if full query failed
        if not track_info:
            track_info = self._find_best_track(
                meta['title_clean'], None,
                None, duration, track_name, artist_name
            )

        if not track_info:
            return results

        mxm_track_id = track_info['track_id']
        result_title = track_info.get('track_name', '')
        result_artist = track_info.get('artist_name', '')

        # Step 3: Get plain lyrics
        plain_lyrics = self._get_lyrics(mxm_track_id)

        # Step 4: Try to get synced lyrics (subtitle)
        synced_lyrics = self._get_subtitle(mxm_track_id)

        if plain_lyrics or synced_lyrics:
            # Compute real match score
            match = self.score_result(
                track_name, artist_name, duration,
                result_title, result_artist,
                track_info.get('duration')
            )

            results.append({
                'synced_lyrics': synced_lyrics,
                'plain_lyrics': plain_lyrics,
                'provider': self.name,
                'score': match['score'],
                'match_details': match,
                'track_name': result_title,
                'artist_name': result_artist,
            })

        return results

    def _find_best_track(self, title, artist=None, album=None,
                         duration_ms=None, orig_title=None, orig_artist=None):
        """Search for a track and return the best-scoring match info.

        Instead of blindly returning track_list[0], we score all results
        and pick the best match.
        """
        params = {
            'apikey': self._api_key,
            'q_track': title,
            's_track_rating': 'desc',
            'page_size': 5,
        }
        if artist:
            params['q_artist'] = artist
        if album:
            params['q_album'] = album

        duration_s = duration_ms_to_seconds(duration_ms)
        if duration_s:
            params['f_track_length'] = duration_s

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

                if not track_list:
                    return None

                # Score all results and pick the best
                best = None
                best_score = -1

                for item in track_list:
                    track = item.get('track', {})
                    result_title = track.get('track_name', '')
                    result_artist = track.get('artist_name', '')
                    result_duration = track.get('track_length')

                    match = self.score_result(
                        orig_title or title, orig_artist or artist, duration_ms,
                        result_title, result_artist, result_duration
                    )

                    if match['score'] > best_score:
                        best_score = match['score']
                        best = {
                            'track_id': track.get('track_id'),
                            'track_name': result_title,
                            'artist_name': result_artist,
                            'duration': result_duration,
                        }

                return best

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
