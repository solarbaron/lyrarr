
"""
Musixmatch Lyrics Provider.
Uses the Musixmatch API to fetch synced and plain lyrics.
Requires a free API key (1000 requests/day on free tier).
"""

import logging
import re

import requests

from lyrarr.app.config import settings
from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.lyrics_text import instrumental_title_tier, version_tags
from lyrarr.metadata.normalize import clean_for_search, duration_seconds
from lyrarr.metadata.provider_utils import ProviderTransientError, is_transient_status, note_transient_error

logger = logging.getLogger(__name__)

MUSIXMATCH_BASE = 'https://api.musixmatch.com/ws/1.1'


# A candidate the worker could accept: no gate and at least this score.
_AUTO_ACCEPT_SCORE = 0.5

_INSTRUMENTAL_TITLE_RE = re.compile(r'伴奏|纯音乐|純音樂|karaoke|instrumental|off[\s-]?vocal|inst\.', re.IGNORECASE)


class MusixmatchProvider(LyricsProvider):
    """Fetch synced and plain lyrics from Musixmatch (requires API key).

    Searches tracks that have lyrics (f_has_lyrics=1) by title + artist and
    scores every hit. Falls back to a title-only search when the first search
    finds nothing, or only a gated / weak (< 0.5) candidate. With strict=True
    (the worker), the lyrics/subtitle calls are skipped for gated candidates.
    A track Musixmatch flags instrumental yields no lyrics plus an
    ``instrumental_hint`` (only for a good, ungated, non-instrumental-titled
    match).
    """

    name = 'musixmatch'

    @property
    def _api_key(self):
        return settings.musixmatch.apikey

    def search(self, track_name=None, artist_name=None, album_name=None, duration=None,
               mb_recording_id=None, *, alt_artist_names=(), strict=True, **kwargs):
        """
        Search for lyrics on Musixmatch with normalized queries and real scoring.

        Returns at most one result:
        [{'synced_lyrics', 'plain_lyrics', 'plain_truncated', 'provider', 'score',
          'match_details', 'strategy': 'search', 'source_id', 'track_name',
          'artist_name', 'album_name', 'duration'[, 'instrumental_hint']}]
        """
        if not self._api_key:
            logger.debug("Musixmatch API key not configured, skipping")
            return []

        if not track_name:
            return []

        meta = clean_for_search(track_name, artist_name or '')
        alt = tuple(a for a in alt_artist_names or () if a)
        title_q = meta['title_query'] or meta['title_clean']

        # Step 1: title + artist
        track_info = self._find_best_track(title_q, meta['artist_query'] or None,
                                           duration, track_name, artist_name, alt)

        # Step 2: title only — when nothing, or nothing acceptable, was found
        if not track_info or not _auto_acceptable(track_info['match']):
            fallback = self._find_best_track(title_q, None, duration, track_name, artist_name, alt)
            if fallback and (not track_info or _rank(fallback) > _rank(track_info)):
                track_info = fallback

        if not track_info:
            return []

        match = track_info['match']
        if strict and match.get('gate'):
            return []

        result = {
            'synced_lyrics': None,
            'plain_lyrics': None,
            'plain_truncated': False,
            'provider': self.name,
            'score': match['score'],
            'match_details': match,
            'strategy': 'search',
            'source_id': track_info['track_id'],
            'track_name': track_info['track_name'],
            'artist_name': track_info['artist_name'],
            'album_name': track_info['album_name'],
            'duration': track_info['duration'],
        }

        if track_info.get('instrumental'):
            if self._hint_allowed(track_info):
                result['instrumental_hint'] = {
                    'provider': self.name,
                    'kind': 'mxm_instrumental',
                    'matched_title': track_info['track_name'],
                    'matched_album': track_info['album_name'],
                    'score': match['score'],
                    'title_score': match.get('title_score'),
                    'duration_diff': match.get('duration_diff'),
                }
                return [result]
            return []

        mxm_track_id = track_info['track_id']

        # Step 3: Get plain lyrics
        plain_lyrics, plain_truncated = self._get_lyrics(mxm_track_id)

        # Step 4: Try to get synced lyrics (subtitle)
        synced_lyrics = self._get_subtitle(mxm_track_id)

        if not (plain_lyrics or synced_lyrics):
            return []
        result.update({
            'synced_lyrics': synced_lyrics,
            'plain_lyrics': plain_lyrics,
            # Free-tier excerpt: the plain text is only part of the song.
            'plain_truncated': plain_truncated,
        })
        return [result]

    @staticmethod
    def _hint_allowed(track_info):
        match = track_info['match']
        if match.get('gate') or match['score'] < _AUTO_ACCEPT_SCORE:
            return False
        title = track_info.get('track_name') or ''
        if _INSTRUMENTAL_TITLE_RE.search(title) or instrumental_title_tier(title) is not None:
            return False
        return not (version_tags(title) & {'instrumental', 'karaoke'})

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

    def _find_best_track(self, title, artist=None, duration_s=None, orig_title=None,
                         orig_artist=None, alt_artists=()):
        """Search tracks with lyrics and return the best-scoring one (any score).

        The duration and album are NOT sent as filters (f_track_length /
        q_album hid the right track whenever Lidarr's length or album title
        differed slightly); they are only used for scoring.
        """
        params = {
            'q_track': title,
            'f_has_lyrics': 1,
            's_track_rating': 'desc',
            'page_size': 5,
        }
        if artist:
            params['q_artist'] = artist

        body = self._api_get('track.search', params)
        track_list = (body or {}).get('track_list') or []

        best = None
        for item in track_list:
            track = (item or {}).get('track') if isinstance(item, dict) else None
            if not isinstance(track, dict):
                continue
            result_title = track.get('track_name', '')
            result_artist = track.get('artist_name', '')
            result_duration = track.get('track_length') or None  # seconds

            match = self.score_result(
                orig_title or title, orig_artist or artist, duration_seconds(duration_s),
                result_title, result_artist, result_duration,
                alt_artists=alt_artists,
            )
            cand = {
                'track_id': track.get('track_id'),
                'track_name': result_title,
                'artist_name': result_artist,
                'album_name': track.get('album_name') or None,
                'duration': result_duration,
                'instrumental': track.get('instrumental') in (1, '1', True),
                'match': match,
            }
            if best is None or _rank(cand) > _rank(best):
                best = cand

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


def _auto_acceptable(match):
    return not match.get('gate') and match['score'] >= _AUTO_ACCEPT_SCORE


def _rank(cand):
    """Ungated before gated, then score, then raw score."""
    m = cand['match']
    return (0 if m.get('gate') else 1, m['score'], m.get('raw_score', m['score']))


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
