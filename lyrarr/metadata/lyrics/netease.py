# coding=utf-8

"""
NetEase Cloud Music Lyrics Provider.
Uses the public NetEase Cloud Music API to fetch synced and plain lyrics.
No API key required. Excellent coverage for both Western and Asian music.
"""

import logging
import re
import requests

from lyrarr.metadata.base import LyricsProvider

logger = logging.getLogger(__name__)

NETEASE_API_BASE = 'https://music.163.com'


class NetEaseProvider(LyricsProvider):
    """Fetch synced and plain lyrics from NetEase Cloud Music (free, no API key)."""

    name = 'netease'

    _headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; Lyrarr/1.0)',
        'Referer': 'https://music.163.com/',
        'Content-Type': 'application/x-www-form-urlencoded',
    }

    def search(self, track_name=None, artist_name=None, **kwargs):
        """
        Search for lyrics on NetEase Cloud Music.

        Returns a list of lyrics results:
        [{'synced_lyrics': str|None, 'plain_lyrics': str|None, 'provider': str, 'score': float}]
        """
        if not track_name:
            return []

        results = []

        # Step 1: Search for the song
        song_id = self._find_song(track_name, artist_name)
        if not song_id:
            return results

        # Step 2: Get lyrics by song ID
        lyrics_data = self._get_lyrics(song_id)
        if lyrics_data:
            results.append(lyrics_data)

        return results

    def _find_song(self, track_name, artist_name=None):
        """Search for a song and return its NetEase song ID."""
        query = f"{artist_name} {track_name}" if artist_name else track_name

        try:
            response = requests.post(
                f"{NETEASE_API_BASE}/api/search/get",
                data={
                    's': query,
                    'type': 1,  # 1 = songs
                    'limit': 5,
                    'offset': 0,
                },
                headers=self._headers,
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                songs = data.get('result', {}).get('songs', [])

                # Try to find a good match
                for song in songs:
                    song_name = song.get('name', '').lower()
                    artists = [a.get('name', '').lower() for a in song.get('artists', [])]

                    # Check if track name matches
                    if track_name.lower() in song_name or song_name in track_name.lower():
                        # If artist specified, verify it matches
                        if artist_name:
                            if any(artist_name.lower() in a or a in artist_name.lower() for a in artists):
                                return song.get('id')
                        else:
                            return song.get('id')

                # If no good match, return first result
                if songs:
                    return songs[0].get('id')

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
                        'score': 0.85 if synced_lyrics else 0.6,
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
