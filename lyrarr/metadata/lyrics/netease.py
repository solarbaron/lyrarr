# coding=utf-8

"""
NetEase Cloud Music Lyrics Provider.
Uses the public NetEase Cloud Music API to fetch synced and plain lyrics.
No API key required. Excellent coverage for both Western and Asian music.
"""

import logging
import re
import requests
from difflib import SequenceMatcher

from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import clean_for_search, normalize_title, normalize_artist

logger = logging.getLogger(__name__)

NETEASE_API_BASE = 'https://music.163.com'


class NetEaseProvider(LyricsProvider):
    """Fetch synced and plain lyrics from NetEase Cloud Music (free, no API key).

    Uses normalized queries and proper fuzzy matching to find the best song
    match, with a title-only fallback search.
    """

    name = 'netease'

    _headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; Lyrarr/1.0)',
        'Referer': 'https://music.163.com/',
        'Content-Type': 'application/x-www-form-urlencoded',
    }

    def search(self, track_name=None, artist_name=None, duration=None, **kwargs):
        """
        Search for lyrics on NetEase Cloud Music with normalized queries.

        Returns a list of lyrics results with computed match scores:
        [{'synced_lyrics': str|None, 'plain_lyrics': str|None, 'provider': str,
          'score': float, 'match_details': dict}]
        """
        if not track_name:
            return []

        meta = clean_for_search(track_name, artist_name or '')
        results = []

        # Strategy 1: Search with artist + title
        song_info = self._find_best_song(
            meta['artist_clean'], meta['title_clean'],
            track_name, artist_name, duration
        )

        # Strategy 2: Fallback — search with title only
        if not song_info:
            song_info = self._find_best_song(
                None, meta['title_clean'],
                track_name, artist_name, duration
            )

        # Strategy 3: Try title variants
        if not song_info:
            for variant in meta.get('title_variants', [])[1:]:
                song_info = self._find_best_song(
                    meta['artist_primary'], variant,
                    track_name, artist_name, duration
                )
                if song_info:
                    break

        if not song_info:
            return results

        # Get lyrics by song ID
        lyrics_data = self._get_lyrics(song_info['id'])
        if lyrics_data:
            # Compute real match score
            match = self.score_result(
                track_name, artist_name, duration,
                song_info.get('name', ''),
                song_info.get('artist_name', ''),
                song_info.get('duration')
            )

            lyrics_data['score'] = match['score']
            lyrics_data['match_details'] = match
            lyrics_data['track_name'] = song_info.get('name', '')
            lyrics_data['artist_name'] = song_info.get('artist_name', '')
            results.append(lyrics_data)

        return results

    def _find_best_song(self, artist_clean, title_clean,
                        orig_title=None, orig_artist=None, orig_duration=None):
        """Search for a song and return the best-scoring match.

        Uses fuzzy matching instead of substring checks to find the
        best match from search results.
        """
        query = f"{artist_clean} {title_clean}".strip() if artist_clean else title_clean
        if not query:
            return None

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
            if response.status_code == 200:
                data = response.json()
                songs = data.get('result', {}).get('songs', [])

                if not songs:
                    return None

                # Score all results and pick the best
                best = None
                best_score = -1

                for song in songs:
                    song_name = song.get('name', '')
                    artists = [a.get('name', '') for a in song.get('artists', [])]
                    combined_artist = ', '.join(artists) if artists else ''
                    # NetEase duration is in milliseconds
                    song_duration_ms = song.get('duration')
                    song_duration_s = song_duration_ms / 1000 if song_duration_ms else None

                    match = self.score_result(
                        orig_title or title_clean, orig_artist or artist_clean,
                        orig_duration,
                        song_name, combined_artist,
                        song_duration_s
                    )

                    if match['score'] > best_score:
                        best_score = match['score']
                        best = {
                            'id': song.get('id'),
                            'name': song_name,
                            'artist_name': combined_artist,
                            'duration': song_duration_s,
                            'match_score': match['score'],
                        }

                # Only return if score is reasonable (> 0.3)
                if best and best_score > 0.3:
                    return best

        except requests.exceptions.RequestException as e:
            logger.error(f"NetEase search error: {e}")

        return None

    def _get_lyrics(self, song_id):
        """Get lyrics for a song by its NetEase ID."""
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
            if response.status_code == 200:
                data = response.json()

                # lrc = synced lyrics in LRC format
                lrc_data = data.get('lrc', {}).get('lyric', '')

                # tlyric = translated lyrics (often English translations)
                # klyric = karaoke lyrics (word-level sync)
                # We use lrc as our primary source

                synced_lyrics = None
                plain_lyrics = None

                if lrc_data and self._is_valid_lrc(lrc_data):
                    synced_lyrics = lrc_data
                    # Also extract plain lyrics from LRC
                    plain_lyrics = self._lrc_to_plain(lrc_data)
                elif lrc_data:
                    # No timestamps, treat as plain
                    plain_lyrics = lrc_data

                if synced_lyrics or plain_lyrics:
                    return {
                        'synced_lyrics': synced_lyrics,
                        'plain_lyrics': plain_lyrics,
                        'provider': self.name,
                        'score': 0.0,  # Will be replaced by computed score
                    }

        except requests.exceptions.RequestException as e:
            logger.error(f"NetEase lyrics fetch error: {e}")

        return None

    def _is_valid_lrc(self, lrc_text):
        """Check if text contains LRC timestamps like [00:12.34]."""
        return bool(re.search(r'\[\d{2}:\d{2}\.\d{2,3}\]', lrc_text))

    def _lrc_to_plain(self, lrc_text):
        """Strip LRC timestamps to get plain lyrics."""
        lines = []
        for line in lrc_text.strip().split('\n'):
            # Remove all [xx:xx.xx] timestamps
            cleaned = re.sub(r'\[\d{2}:\d{2}\.\d{2,3}\]', '', line).strip()
            # Skip metadata lines like [ti:Title] [ar:Artist]
            if cleaned and not re.match(r'^\[.+:.+\]$', line.strip()):
                lines.append(cleaned)
        return '\n'.join(lines) if lines else None
