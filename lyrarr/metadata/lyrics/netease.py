
"""
NetEase Cloud Music Lyrics Provider.
Uses the public NetEase Cloud Music API to fetch synced and plain lyrics.
No API key required. Excellent coverage for both Western and Asian music.
"""

import logging
import re

import requests

from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.lyrics_text import (
    classify_lyrics_text,
    instrumental_title_tier,
    is_credit_line,
    strip_credit_blocks,
    version_tags,
)
from lyrarr.metadata.normalize import clean_for_search
from lyrarr.metadata.provider_utils import ProviderTransientError, is_transient_status, note_transient_error

logger = logging.getLogger(__name__)

NETEASE_API_BASE = 'https://music.163.com'

# NetEase answers HTTP 200 with a JSON "code" for throttling / anti-abuse
# (-460 "cheating", -462 verification required, 405 too frequent).
_THROTTLE_CODES = {-460, -462, 405, 429, 503}


def _json(response):
    """Response JSON as a dict ({} for non-JSON / non-object bodies)."""
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _raise_transient(what):
    """Flag the current search as transiently failed and abort the cascade."""
    note_transient_error()
    raise ProviderTransientError(f"NetEase {what}")


# Credit lines NetEase puts before/after the lyrics: "[00:00.000] 作词 : X",
# "指挥家 : X", "Mixing Engineer : X". lyrics_text.is_credit_line knows the
# common roles; NetEase uses many more, so a role here is any short CJK word
# or 1-3 Latin words containing a known role word, followed by a colon. Only
# the LEADING and TRAILING contiguous blocks are removed.
_NE_TS = r'(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)*'
_NE_ROLE_WORDS = (
    r'lyrics?|lyricist|words|composer|composed|composition|music|arranger|arranged|arrangement|'
    r'producer|produced|production|mix(?:ing|ed|er)?|master(?:ing|ed)?|engineer|recording|recorded|'
    r'programming|programmer|vocals?|guitars?|bass|drums|keyboards?|piano|strings|harmony|'
    r'backing|chorus|publisher|published|label|op|sp|isrc|written|writer|by'
)
_NE_CREDIT_RE = re.compile(
    r'^\s*' + _NE_TS + r'(?:[㐀-鿿豈-﫿]{1,10}'
    r'|(?:[A-Za-z][\w.&-]*\s+){0,2}(?:' + _NE_ROLE_WORDS + r')(?:\s+[A-Za-z][\w.&-]*){0,2})'
    r'\s*[:：]\s*\S',
    re.IGNORECASE,
)
_NE_NEUTRAL_RE = re.compile(r'^\s*' + _NE_TS + r'(?:\[[^\]]*\])?\s*$')
_NE_LRC_TS_RE = re.compile(r'\[\d{2}:\d{2}(?:\.\d{2,3})?\]')

# NetEase response flags meaning "this song has no lyrics". 'uncollected'
# ("nobody uploaded lyrics yet") is deliberately NOT one of them: it says
# nothing about the audio, and a vocal song must never be hinted instrumental.
_NO_LYRICS_FLAGS = ('pureMusic', 'nolyric')

# The matched song's own title marks it as an instrumental/karaoke version:
# its "no lyrics" answer is about that version, not about the track.
_INSTRUMENTAL_TITLE_RE = re.compile(r'伴奏|纯音乐|純音樂|karaoke|instrumental|off[\s-]?vocal|inst\.', re.IGNORECASE)

# A candidate the worker could accept: no gate and at least this score.
_AUTO_ACCEPT_SCORE = 0.5
# Manual-search floor (raw score) for showing a candidate at all.
_MANUAL_FLOOR = 0.3


def _is_ne_credit(line):
    return is_credit_line(line) or bool(_NE_CREDIT_RE.match(line))


def strip_netease_credits(text):
    """Remove NetEase's leading/trailing credit blocks (timestamps of the
    remaining lines untouched)."""
    if not text:
        return text or ''
    text = strip_credit_blocks(text)
    lines = text.split('\n')

    def block_end(seq):
        end = 0
        for i, line in enumerate(seq):
            if _is_ne_credit(line):
                end = i + 1
            elif not _NE_NEUTRAL_RE.match(line):
                break
        return end

    head = block_end(lines)
    tail = block_end(list(reversed(lines[head:])))
    n = len(lines)
    keep = [line for i, line in enumerate(lines)
            if not ((i < head or i >= n - tail) and _is_ne_credit(line))]
    out = '\n'.join(keep).strip('\n')
    return out if out.strip() else ''


class NetEaseProvider(LyricsProvider):
    """Fetch synced and plain lyrics from NetEase Cloud Music (free, no API key).

    Strategies (artist + title, title only, title variants) run until one
    finds an auto-acceptable song (no gate, score ≥ 0.5); otherwise the best
    candidate overall is used. Credit blocks are stripped from the lyrics.
    A song NetEase flags as pure music / no lyrics yields no lyrics plus an
    ``instrumental_hint`` (never the ``instrumental`` key) — only when the
    matched song's own title is not an instrumental/karaoke version and the
    match is good (score ≥ 0.5, no gate).
    """

    name = 'netease'

    _headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; Lyrarr/1.0)',
        'Referer': 'https://music.163.com/',
        'Content-Type': 'application/x-www-form-urlencoded',
    }

    def search(self, track_name=None, artist_name=None, album_name=None, duration=None,
               mb_recording_id=None, *, alt_artist_names=(), strict=True, **kwargs):
        """
        Search for lyrics on NetEase Cloud Music with normalized queries.

        Returns at most one result:
        [{'synced_lyrics', 'plain_lyrics', 'provider', 'score', 'match_details',
          'strategy': 'search', 'source_id', 'track_name', 'artist_name',
          'album_name', 'duration'[, 'instrumental_hint']}]
        """
        if not track_name:
            return []

        meta = clean_for_search(track_name, artist_name or '')
        ctx = (track_name, artist_name, duration, tuple(a for a in alt_artist_names or () if a))
        title_q = meta['title_query'] or meta['title_clean']

        attempts = []
        if meta['artist_query']:
            attempts.append((meta['artist_query'], title_q))
        attempts.append((None, title_q))
        for variant in meta.get('title_variants', [])[1:]:
            attempts.append((meta['artist_primary'] or None, variant))

        best = None
        tried = set()
        for artist_q, title_v in attempts:
            query = f"{artist_q} {title_v}".strip() if artist_q else (title_v or '')
            if not query or query.casefold() in tried:
                continue
            tried.add(query.casefold())
            cand = self._find_best_song(query, ctx)
            if cand and (best is None or _rank(cand) > _rank(best)):
                best = cand
            if best and _auto_acceptable(best['match']):
                break

        if not best:
            return []
        match = best['match']
        if strict:
            if match.get('gate') or match['score'] <= _MANUAL_FLOOR:
                return []
        elif match.get('raw_score', match['score']) <= _MANUAL_FLOOR:
            return []

        lyrics_data, hint_kind = self._get_lyrics(best['id'])
        result = {
            'synced_lyrics': None,
            'plain_lyrics': None,
            'provider': self.name,
            'score': match['score'],
            'match_details': match,
            'strategy': 'search',
            'source_id': best['id'],
            'track_name': best['name'],
            'artist_name': best['artist_name'],
            'album_name': best['album_name'],
            'duration': best['duration'],
        }
        if lyrics_data:
            result.update(lyrics_data)
            return [result]
        if hint_kind and self._hint_allowed(best):
            result['instrumental_hint'] = {
                'provider': self.name,
                'kind': 'pure_music',
                'matched_title': best['name'],
                'matched_album': best['album_name'],
                'score': match['score'],
                'title_score': match.get('title_score'),
                'duration_diff': match.get('duration_diff'),
            }
            return [result]
        return []

    @staticmethod
    def _hint_allowed(song):
        match = song['match']
        if match.get('gate') or match['score'] < _AUTO_ACCEPT_SCORE:
            return False
        title = song.get('name') or ''
        if _INSTRUMENTAL_TITLE_RE.search(title):
            return False
        if instrumental_title_tier(title) is not None:
            return False
        return not (version_tags(title) & {'instrumental', 'karaoke'})

    def _find_best_song(self, query, ctx):
        """Search NetEase and return the best-scoring song (any score), or None.

        Timeouts, 429/5xx and NetEase's throttle codes raise
        ProviderTransientError (flagged as transient) so they don't read as
        "no lyrics exist".
        """
        orig_title, orig_artist, orig_duration, alt_artists = ctx
        try:
            response = requests.post(
                f"{NETEASE_API_BASE}/api/search/get",
                data={
                    's': query,
                    'type': 1,  # 1 = songs
                    'limit': 10,
                    'offset': 0,
                },
                headers=self._headers,
                timeout=15
            )
        except requests.exceptions.RequestException as e:
            _raise_transient(f"search error: {e}")

        if is_transient_status(response.status_code):
            _raise_transient(f"search returned HTTP {response.status_code}")
        if response.status_code != 200:
            logger.debug(f"NetEase search returned HTTP {response.status_code}")
            return None

        data = _json(response)
        if data.get('code') in _THROTTLE_CODES:
            _raise_transient(f"search throttled (code {data.get('code')})")
        songs = (data.get('result') or {}).get('songs') or []

        best = None
        for song in songs:
            if not isinstance(song, dict):
                continue
            song_name = song.get('name') or ''
            artists = [a.get('name', '') for a in (song.get('artists') or []) if isinstance(a, dict)]
            combined_artist = ', '.join(a for a in artists if a)
            # NetEase duration is in milliseconds
            song_duration_ms = song.get('duration')
            try:
                song_duration_s = song_duration_ms / 1000 if song_duration_ms else None
            except TypeError:
                song_duration_s = None
            album = song.get('album') if isinstance(song.get('album'), dict) else {}

            match = self.score_result(
                orig_title, orig_artist, orig_duration,
                song_name, combined_artist, song_duration_s,
                alt_artists=alt_artists,
            )
            cand = {
                'id': song.get('id'),
                'name': song_name,
                'artist_name': combined_artist,
                'album_name': album.get('name') or None,
                'duration': song_duration_s,
                'match': match,
            }
            if best is None or _rank(cand) > _rank(best):
                best = cand
        return best

    def _get_lyrics(self, song_id):
        """Get lyrics for a song by its NetEase ID.

        Returns (lyrics_dict | None, hint_kind | None). Credit blocks are
        stripped first; the plain text is rebuilt from what remains. hint_kind
        is 'pure_music' when NetEase flags the song as pure music / no lyrics,
        or when only 纯音乐-style markers remain after the strip.
        """
        try:
            response = requests.get(
                f"{NETEASE_API_BASE}/api/song/lyric",
                params={
                    'id': song_id,
                    'lv': -1,   # plain lyrics version
                    'kv': -1,   # karaoke version
                    'tv': -1,   # translated version
                },
                headers=self._headers,
                timeout=15
            )
        except requests.exceptions.RequestException as e:
            _raise_transient(f"lyrics fetch error: {e}")

        if is_transient_status(response.status_code):
            _raise_transient(f"lyrics fetch returned HTTP {response.status_code}")
        if response.status_code != 200:
            return None, None

        data = _json(response)
        if data.get('code') in _THROTTLE_CODES:
            _raise_transient(f"lyrics fetch throttled (code {data.get('code')})")

        # lrc = synced lyrics in LRC format; tlyric = translation; klyric = word-level
        lrc_data = (data.get('lrc') or {}).get('lyric') if isinstance(data.get('lrc'), dict) else None
        lrc_data = strip_netease_credits(lrc_data or '')
        content_class = classify_lyrics_text(lrc_data) if lrc_data else 'empty'

        flagged = any(data.get(flag) for flag in _NO_LYRICS_FLAGS)
        if content_class != 'lyrics':
            hint = 'pure_music' if (flagged or content_class == 'marker') else None
            return None, hint
        if flagged:
            # Real lyric lines despite a "pure music" flag: conflicting
            # signals. Never a hint; the text goes through normal validation.
            logger.debug(f"NetEase song {song_id} flagged pure music but has lyric lines")

        if self._is_valid_lrc(lrc_data):
            synced_lyrics = lrc_data
            plain_lyrics = self._lrc_to_plain(lrc_data)
        else:
            synced_lyrics = None
            plain_lyrics = lrc_data
        if not (synced_lyrics or plain_lyrics):
            return None, None
        return {
            'synced_lyrics': synced_lyrics,
            'plain_lyrics': plain_lyrics,
            'provider': self.name,
        }, None

    def _is_valid_lrc(self, lrc_text):
        """Check if text contains LRC timestamps like [00:12.34]."""
        return bool(re.search(r'\[\d{2}:\d{2}\.\d{2,3}\]', lrc_text))

    def _lrc_to_plain(self, lrc_text):
        """Strip LRC timestamps to get plain lyrics."""
        lines = []
        for line in lrc_text.strip().split('\n'):
            # Remove all [xx:xx.xx] timestamps
            cleaned = _NE_LRC_TS_RE.sub('', line).strip()
            # Skip metadata lines like [ti:Title] [ar:Artist]
            if cleaned and not re.match(r'^\[.+:.+\]$', line.strip()):
                lines.append(cleaned)
        return '\n'.join(lines) if lines else None


def _auto_acceptable(match):
    return not match.get('gate') and match['score'] >= _AUTO_ACCEPT_SCORE


def _rank(cand):
    """Ungated before gated, then score, then raw score."""
    m = cand['match']
    return (0 if m.get('gate') else 1, m['score'], m.get('raw_score', m['score']))
