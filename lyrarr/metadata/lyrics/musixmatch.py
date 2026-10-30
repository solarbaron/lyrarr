
"""
Musixmatch Lyrics Provider.
Uses the Musixmatch API to fetch synced and plain lyrics.
Requires a free API key (1000 requests/day on free tier).
"""

import logging

import requests

from lyrarr.app.config import settings
from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import clean_for_search, duration_seconds
from lyrarr.metadata.provider_utils import ProviderTransientError, is_transient_status, note_transient_error

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
        plain_lyrics, plain_truncated = self._get_lyrics(mxm_track_id)

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
                # Free-tier excerpt: the plain text is only part of the song.
                'plain_truncated': plain_truncated,
                'provider': self.name,
                'score': match['score'],
                'match_details': match,
                'track_name': result_title,
                'artist_name': result_artist,
            })

        return results

    def _api_get(self, endpoint, params):
        """GET a Musixmatch endpoint and return its message.body dict, or None.

        Musixmatch reports most failures inside an HTTP 200 envelope
        (message.header.status_code) with body "" — never assume body is a
        dict. Rate limits / quota (402, 429) and server errors are transient:
        they're flagged and raised so the worker retries soon and the health
        tracker sees the failure, instead of reading as "no lyrics".
        """
        try:
            response = requests.get(
                f"{MUSIXMATCH_BASE}/{endpoint}",
                params={'apikey': self._api_key, **params},
                timeout=15
            )
        except requests.exceptions.RequestException as e:
            note_transient_error()
            raise ProviderTransientError(f"Musixmatch {endpoint} error: {e}") from e

        if is_transient_status(response.status_code):
            note_transient_error()
            raise ProviderTransientError(f"Musixmatch {endpoint} returned HTTP {response.status_code}")
        if response.status_code != 200:
            logger.debug(f"Musixmatch {endpoint} returned HTTP {response.status_code}")
            return None

        try:
            data = response.json()
        except ValueError:
            logger.debug(f"Musixmatch {endpoint} returned a non-JSON body")
            return None
        message = data.get('message') if isinstance(data, dict) else None
        if not isinstance(message, dict):
            return None
        header = message.get('header') if isinstance(message.get('header'), dict) else {}
        status = header.get('status_code', 200)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 200
        if status == 402 or is_transient_status(status):
            # 402 = daily quota exhausted; resets on its own.
            note_transient_error()
            raise ProviderTransientError(f"Musixmatch {endpoint} status {status}")
        if status != 200:
            logger.debug(f"Musixmatch {endpoint} status {status}")
            return None
        body = message.get('body')
        return body if isinstance(body, dict) else None

    def _find_best_track(self, title, artist=None, album=None,
                         duration_s=None, orig_title=None, orig_artist=None):
        """Search for a track and return the best-scoring match info.

        Instead of blindly returning track_list[0], we score all results
        and pick the best match.
        """
        params = {
            'q_track': title,
            's_track_rating': 'desc',
            'page_size': 5,
        }
        if artist:
            params['q_artist'] = artist
        if album:
            params['q_album'] = album

        duration_s = duration_seconds(duration_s)
        if duration_s:
            params['f_track_length'] = duration_s

        body = self._api_get('track.search', params)
        track_list = (body or {}).get('track_list') or []
        if not track_list:
            return None

        # Score all results and pick the best
        best = None
        best_score = -1

        for item in track_list:
            track = (item or {}).get('track') or {}
            result_title = track.get('track_name', '')
            result_artist = track.get('artist_name', '')
            result_duration = track.get('track_length')  # seconds

            match = self.score_result(
                orig_title or title, orig_artist or artist, duration_s,
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

    def _get_lyrics(self, track_id):
        """Get plain lyrics for a track by its Musixmatch ID.

        Returns (lyrics, truncated). The free tier only returns ~30% of the
        lyrics, ending in "..." followed by a "NOT for Commercial use" footer;
        the footer is always stripped, and truncated=True tells the worker the
        excerpt must never be saved as the song's lyrics.
        """
        body = self._api_get('track.lyrics.get', {'track_id': track_id})
        lyrics = (body or {}).get('lyrics')
        lyrics_body = (lyrics.get('lyrics_body') or '') if isinstance(lyrics, dict) else ''
        return split_musixmatch_disclaimer(lyrics_body)

    def _get_subtitle(self, track_id):
        """Get synced lyrics (subtitle) for a track by its Musixmatch ID."""
        body = self._api_get('track.subtitle.get', {
            'track_id': track_id,
            'subtitle_format': 'lrc',
        })
        subtitle = (body or {}).get('subtitle')
        subtitle_body = subtitle.get('subtitle_body') if isinstance(subtitle, dict) else None
        if subtitle_body:
            subtitle_body, _ = split_musixmatch_disclaimer(subtitle_body)
        return subtitle_body or None


_DISCLAIMER_MARKER = '******* This Lyrics is NOT'


def split_musixmatch_disclaimer(text):
    """Strip Musixmatch's commercial-use footer. Returns (text or None, truncated).

    truncated is True when the footer was present and the remaining text ends
    in an ellipsis — the free tier's partial-lyrics signature.
    """
    if not text:
        return None, False
    truncated = False
    if _DISCLAIMER_MARKER in text:
        text = text[:text.index(_DISCLAIMER_MARKER)].rstrip()
        if text.endswith(('...', '…')):
            truncated = True
            text = text.rstrip('.…').rstrip()
    text = text.strip()
    return (text or None), truncated
