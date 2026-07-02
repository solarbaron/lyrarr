
import logging

import requests

from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import clean_for_search, duration_ms_to_seconds, normalize_title
from lyrarr.metadata.provider_utils import (
    ProviderTransientError,
    is_transient_status,
    note_transient_error,
    rate_limiter,
)

logger = logging.getLogger(__name__)

LRCLIB_BASE_URL = 'https://lrclib.net/api'
MUSICBRAINZ_BASE_URL = 'https://musicbrainz.org/ws/2'

# Shared session for connection pooling — avoids a fresh TLS handshake per call.
_session = requests.Session()

# (connect, read) — fail fast on unreachable host, allow slow responses.
_TIMEOUT = (5, 15)


class LRCLIBProvider(LyricsProvider):
    """Fetch synced and plain lyrics from LRCLIB (free, no API key needed).

    Uses a cascading search strategy:
    0. MusicBrainz ISRC/metadata lookup (if recording ID available)
    1. Exact match with normalized title + artist + album + duration
    2. Exact match without album
    3. Exact match with title variants
    4. Search with artist + title combined query
    5. Search with title only (broadest)

    Results are scored using real fuzzy matching against the query metadata.
    """

    name = 'lrclib'

    def search(self, track_name=None, artist_name=None, album_name=None,
               duration=None, mb_recording_id=None, **kwargs):
        """
        Search for lyrics using cascading strategy.

        Returns a list of lyrics results sorted by computed match score:
        [{'synced_lyrics': str|None, 'plain_lyrics': str|None, 'provider': str,
          'score': float, 'match_details': dict}]
        """
        if not track_name:
            return []

        meta = clean_for_search(track_name, artist_name or '')
        duration_s = duration_ms_to_seconds(duration)
        results = []
        seen_ids = set()

        # Strategy 0: MusicBrainz Recording ID → canonical metadata → exact match
        # This bypasses fuzzy matching entirely by using verified metadata from MB
        if mb_recording_id:
            mb_results = self._search_via_musicbrainz(
                mb_recording_id, track_name, artist_name, duration, seen_ids
            )
            results.extend(mb_results)

        # If MusicBrainz gave us a high-confidence result, skip further searching
        if results and results[0].get('score', 0) >= 0.8:
            return results

        # Strategy 1: Exact match with album
        exact = self._get_exact(
            meta['title_clean'], meta['artist_clean'],
            normalize_title(album_name) if album_name else None,
            duration_s
        )
        if exact:
            if exact.get('source_id'):
                seen_ids.add(exact['source_id'])
            # Score it
            match = self.score_result(
                track_name, artist_name, duration,
                exact.get('track_name', ''), exact.get('artist_name', ''),
                exact.get('duration')
            )
            exact['score'] = match['score']
            exact['match_details'] = match
            results.append(exact)

        # Strategy 2: Exact match without album (broader)
        if not results:
            exact_no_album = self._get_exact(
                meta['title_clean'], meta['artist_clean'],
                None, duration_s
            )
            if exact_no_album:
                sid = exact_no_album.get('source_id')
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    match = self.score_result(
                        track_name, artist_name, duration,
                        exact_no_album.get('track_name', ''),
                        exact_no_album.get('artist_name', ''),
                        exact_no_album.get('duration')
                    )
                    exact_no_album['score'] = match['score']
                    exact_no_album['match_details'] = match
                    results.append(exact_no_album)

        # Strategy 3: Exact match with title variants
        if not results:
            for variant in meta.get('title_variants', [])[1:]:  # Skip first, already tried
                exact_var = self._get_exact(variant, meta['artist_primary'], None, duration_s)
                if exact_var:
                    sid = exact_var.get('source_id')
                    if sid not in seen_ids:
                        seen_ids.add(sid)
                        match = self.score_result(
                            track_name, artist_name, duration,
                            exact_var.get('track_name', ''),
                            exact_var.get('artist_name', ''),
                            exact_var.get('duration')
                        )
                        exact_var['score'] = match['score']
                        exact_var['match_details'] = match
                        results.append(exact_var)
                        break  # One hit is enough for exact match

        # Strategy 4: Search with artist + title (broader)
        if not results or all(r.get('score', 0) < 0.6 for r in results):
            search_query = f"{meta['artist_clean']} {meta['title_clean']}".strip()
            if search_query:
                search_results = self._search_query(
                    search_query, track_name, artist_name, duration, seen_ids
                )
                results.extend(search_results)

        # Strategy 5: Search with title only (broadest fallback)
        if not results:
            for variant in meta.get('title_variants', []):
                if variant:
                    search_results = self._search_query(
                        variant, track_name, artist_name, duration, seen_ids
                    )
                    results.extend(search_results)
                    if results:
                        break

        # Sort by score descending
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        return results

    # -------------------------------------------------------------------------
    # MusicBrainz ISRC Lookup
    # -------------------------------------------------------------------------

    def _search_via_musicbrainz(self, mb_recording_id, orig_track, orig_artist,
                                orig_duration, seen_ids):
        """Look up canonical metadata from MusicBrainz and use it for exact LRCLIB match.

        MusicBrainz provides verified title, artist, and duration data that is
        often more accurate than local library metadata (which may include
        parentheticals, inconsistent artist names, etc.).

        Flow:
        1. GET /ws/2/recording/{id}?inc=isrcs+artist-credits&fmt=json
        2. Extract canonical title, artist name, duration from response
        3. Use canonical metadata for LRCLIB exact match
        4. If ISRCs available, also try LRCLIB search with ISRC as query
        """
        mb_data = self._fetch_musicbrainz_recording(mb_recording_id)
        if not mb_data:
            return []

        results = []
        mb_title = mb_data.get('title', '')
        mb_duration_ms = mb_data.get('length')  # MB returns length in ms
        mb_duration_s = mb_duration_ms // 1000 if mb_duration_ms else None
        mb_artist = self._extract_mb_artist(mb_data)
        mb_isrcs = mb_data.get('isrcs', [])

        logger.debug(
            f"MusicBrainz lookup for {mb_recording_id}: "
            f"title='{mb_title}', artist='{mb_artist}', "
            f"duration={mb_duration_s}s, ISRCs={mb_isrcs}"
        )

        # Attempt 1: Exact match using canonical MB metadata
        if mb_title and mb_artist:
            exact = self._get_exact(
                mb_title.lower(), mb_artist.lower(),
                None, mb_duration_s
            )
            if exact:
                sid = exact.get('source_id')
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    match = self.score_result(
                        orig_track, orig_artist, orig_duration,
                        exact.get('track_name', ''),
                        exact.get('artist_name', ''),
                        exact.get('duration')
                    )
                    exact['score'] = match['score']
                    exact['match_details'] = match
                    exact['match_details']['mb_matched'] = True
                    results.append(exact)

        # Attempt 2: Search LRCLIB with each ISRC as a query keyword
        # LRCLIB doesn't have an ISRC endpoint, but ISRCs are sometimes
        # embedded in the metadata that LRCLIB indexes
        for isrc in mb_isrcs[:3]:  # Try up to 3 ISRCs
            if isinstance(isrc, dict):
                isrc = isrc.get('isrc', '')
            if not isrc:
                continue

            search_results = self._search_query(
                isrc, orig_track, orig_artist, orig_duration, seen_ids
            )
            results.extend(search_results)
            if results:
                break

        # Attempt 3: Search with canonical MB title + artist (clean metadata)
        if not results and mb_title and mb_artist:
            search_query = f"{mb_artist} {mb_title}"
            search_results = self._search_query(
                search_query, orig_track, orig_artist, orig_duration, seen_ids
            )
            results.extend(search_results)

        return results

    def _fetch_musicbrainz_recording(self, recording_id):
        """Fetch recording details from MusicBrainz API.

        Returns the JSON recording object with ISRCs and artist credits,
        or None on failure.

        Rate limit: MusicBrainz allows 1 req/sec. The provider_utils rate
        limiter handles this at a higher level.
        """
        if not recording_id:
            return None

        try:
            # MusicBrainz allows ~1 req/sec; throttle every call, not just the
            # first one the worker spaced out, so a burst of lookups can't 503.
            rate_limiter.wait('musicbrainz')
            response = _session.get(
                f"{MUSICBRAINZ_BASE_URL}/recording/{recording_id}",
                params={
                    'inc': 'isrcs+artist-credits',
                    'fmt': 'json',
                },
                timeout=10,
                headers={
                    'User-Agent': 'Lyrarr/1.0 (https://github.com/lyrarr)',
                    'Accept': 'application/json',
                }
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.debug(f"MusicBrainz recording not found: {recording_id}")
            elif is_transient_status(response.status_code):
                note_transient_error()
                logger.warning(
                    f"MusicBrainz transient error {response.status_code}, skipping ISRC lookup"
                )
            else:
                logger.debug(
                    f"MusicBrainz API returned {response.status_code} "
                    f"for recording {recording_id}"
                )

        except requests.exceptions.RequestException as e:
            note_transient_error()
            logger.debug(f"MusicBrainz API error: {e}")

        return None

    @staticmethod
    def _extract_mb_artist(mb_data):
        """Extract a combined artist name from MusicBrainz artist-credit.

        Handles joinphrases like ' feat. ' to reconstruct the full artist string.
        Example: [{"name": "Queen", "joinphrase": " feat. "},
                  {"name": "David Bowie", "joinphrase": ""}]
        → "Queen feat. David Bowie"
        """
        credits = mb_data.get('artist-credit', [])
        if not credits:
            return ''

        parts = []
        for credit in credits:
            name = credit.get('name', '')
            joinphrase = credit.get('joinphrase', '')
            parts.append(name + joinphrase)

        return ''.join(parts).strip()

    # -------------------------------------------------------------------------
    # LRCLIB Exact Match & Search
    # -------------------------------------------------------------------------

    def _get_exact(self, track_name, artist_name, album_name=None, duration_s=None):
        """Try to get exact match from LRCLIB's /api/get endpoint."""
        if not track_name or not artist_name:
            return None

        params = {
            'track_name': track_name,
            'artist_name': artist_name,
        }
        if album_name:
            params['album_name'] = album_name
        if duration_s is not None:
            params['duration'] = duration_s

        try:
            rate_limiter.wait('lrclib')
            response = _session.get(
                f"{LRCLIB_BASE_URL}/get",
                params=params,
                timeout=_TIMEOUT,
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
                        'score': 0.0,  # Will be replaced by computed score
                        'source_id': data.get('id'),
                        'track_name': data.get('trackName', ''),
                        'artist_name': data.get('artistName', ''),
                        'album_name': data.get('albumName', ''),
                        'duration': data.get('duration'),
                    }
            elif is_transient_status(response.status_code):
                # Abort this track's whole cascade: hammering a rate-limited or
                # erroring host with the remaining strategies only makes it
                # worse. Raising also lets the worker's health tracker put
                # LRCLIB in cooldown if this keeps happening.
                note_transient_error()
                raise ProviderTransientError(
                    f"LRCLIB returned {response.status_code} on exact match"
                )
        except requests.exceptions.RequestException as e:
            note_transient_error()
            raise ProviderTransientError(f"LRCLIB exact match error: {e}") from e

        return None

    def _search_query(self, query, orig_track, orig_artist, orig_duration, seen_ids):
        """Search LRCLIB with a query string, score and de-duplicate results."""
        results = []

        try:
            rate_limiter.wait('lrclib')
            response = _session.get(
                f"{LRCLIB_BASE_URL}/search",
                params={'q': query},
                timeout=_TIMEOUT,
                headers={'User-Agent': 'Lyrarr/1.0'}
            )
            if response.status_code != 200 and is_transient_status(response.status_code):
                note_transient_error()
                raise ProviderTransientError(
                    f"LRCLIB returned {response.status_code} on search"
                )
            if response.status_code == 200:
                data = response.json()
                for item in data[:10]:  # Check top 10 for best scored match
                    sid = item.get('id')
                    if sid in seen_ids:
                        continue

                    synced = item.get('syncedLyrics')
                    plain = item.get('plainLyrics')
                    if not synced and not plain:
                        continue

                    seen_ids.add(sid)

                    # Compute real match score
                    match = self.score_result(
                        orig_track, orig_artist, orig_duration,
                        item.get('trackName', ''),
                        item.get('artistName', ''),
                        item.get('duration')
                    )

                    results.append({
                        'synced_lyrics': synced,
                        'plain_lyrics': plain,
                        'provider': self.name,
                        'score': match['score'],
                        'match_details': match,
                        'source_id': sid,
                        'track_name': item.get('trackName', ''),
                        'artist_name': item.get('artistName', ''),
                        'album_name': item.get('albumName', ''),
                        'duration': item.get('duration'),
                    })

        except requests.exceptions.RequestException as e:
            note_transient_error()
            raise ProviderTransientError(f"LRCLIB search error: {e}") from e

        # Sort by score
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        return results[:5]  # Return top 5
