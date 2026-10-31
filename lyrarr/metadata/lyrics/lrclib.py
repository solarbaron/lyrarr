
"""LRCLIB lyrics provider.

A bounded cascade of exact lookups (``/api/get``) followed by LRCLIB's
structured search (``/api/search``), all inside a per-track request budget
(PLAN §1.4):

* every LRCLIB request goes through :meth:`LRCLIBProvider._call`, which
  de-duplicates identical ``(endpoint, casefolded params)`` pairs within one
  ``search()`` — a title whose raw, query and comparison forms are the same
  costs one request, not three;
* at most ``LRCLIB_MAX_CALLS_PER_TRACK`` HTTP requests per track (retries
  included; MusicBrainz lookups are separate and rate-limited on their own);
* at most ONE Retry-After retry (sleep ≤ 2 s) per ``search()``;
* LRCLIB answers many misses with ``503 ServerOverloaded``: that is a *soft
  miss*, never an exception, never a provider-health failure. After
  ``LRCLIB_SOFT_MISS_LIMIT`` soft misses the remaining exact lookups are
  skipped and only the structured search runs.

A transient error (timeout, connection error, 429, 500/502/504) in one step
never discards results collected by another; the search raises
ProviderTransientError only when no request got a definitive answer.

Instrumental markers (LRCLIB's ``instrumental: true`` records) are returned
as ``{'instrumental': True}`` results only when STRONG (§1.4): found by the
MusicBrainz-identified lookup with an MB length, or by the exact lookup WITH
the album; the query duration is known and within 2 s; no gate; title score
≥ 90; and both raw titles agree on being (or not being) an instrumental
version. Weaker markers are dropped here and never stop the cascade.
"""

import logging
import re
import threading
import time

import requests

from lyrarr import __version__
from lyrarr.app.config import settings
from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import (
    artist_query_form,
    clean_for_search,
    comparison_key,
    duration_seconds,
    fold_text,
)
from lyrarr.metadata.provider_utils import (
    ProviderTransientError,
    is_transient_status,
    note_transient_error,
    rate_limiter,
    retry_after_seconds,
)

logger = logging.getLogger(__name__)

MUSICBRAINZ_BASE_URL = 'https://musicbrainz.org/ws/2'

# Shared session for connection pooling — avoids a fresh TLS handshake per call.
_session = requests.Session()

# (connect, read) — fail fast on unreachable host, allow slow responses.
_TIMEOUT = (5, 15)

# LRCLIB's docs ask clients to identify themselves with name, version and URL.
USER_AGENT = f'Lyrarr v{__version__} (https://github.com/solarbaron/lyrarr)'

# --- Request budget (per search(), i.e. per track) ---
LRCLIB_MAX_CALLS_PER_TRACK = 8
LRCLIB_SOFT_MISS_LIMIT = 2
LRCLIB_MAX_RETRY_SLEEP_S = 2.0
LRCLIB_MAX_VARIANT_GETS = 2

# Items kept per search call after scoring EVERY item LRCLIB returned.
_SEARCH_RESULTS_KEPT = 10

# An ungated, non-marker synced hit at least this good ends the cascade.
_SATISFIED_SCORE = 0.6

# Strong-marker rule (§1.4).
STRONG_MARKER_MAX_DELTA_S = 2.0
STRONG_MARKER_MIN_TITLE_SCORE = 90

# Attaching a search record's synced lyrics to a plain-only exact hit.
_ATTACH_MAX_DELTA_S = 2.0
_ATTACH_MIN_TITLE_SCORE = 90
_ATTACH_MIN_TEXT_OVERLAP = 0.6

_STRATEGY_RANK = {
    'mb_exact': 6, 'exact_album': 5, 'exact_noalbum': 4, 'variant': 3,
    'search_struct': 2, 'search_q': 1, 'search_title': 0,
}
_EXACT_STRATEGIES = ('mb_exact', 'exact_album', 'exact_noalbum', 'variant')

# Sentinel: the request was answered with 503 / ServerOverloaded.
_SOFT_MISS = object()


class _BudgetExhausted(Exception):
    """LRCLIB_MAX_CALLS_PER_TRACK reached: stop and return what we have."""


class _SearchState:
    """Per-search() request bookkeeping (budget, retry, dedupe cache)."""

    def __init__(self):
        self.calls = 0            # HTTP requests sent to LRCLIB (retries included)
        self.retry_used = False
        self.soft_misses = 0
        self.definitive = 0       # answers that settle a request: 200, 404, other 4xx
        self.transient = 0        # timeouts, connection errors, 429, 500/502/504
        self.cache = {}


_tls = threading.local()


def lrclib_api_url():
    """Base API URL for the configured LRCLIB instance (self-hostable)."""
    instance = (settings.lrclib.instance or 'https://lrclib.net').rstrip('/')
    return f'{instance}/api'


def _dedupe_key(endpoint, params):
    return (endpoint, tuple(sorted(
        (k, str(v).strip().casefold()) for k, v in params.items() if v not in (None, '')
    )))


def _is_soft_miss(response):
    """503, or any 5xx whose body says ServerOverloaded (LRCLIB's busy/miss answer)."""
    status = response.status_code
    if status == 503:
        return True
    if 500 <= status < 600:
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get('name') == 'ServerOverloaded'
    return False


def _norm_lines(text):
    lines = set()
    for line in (text or '').splitlines():
        line = re.sub(r'\[[^\]]*\]', '', line)
        line = re.sub(r'\W+', ' ', line.casefold()).strip()
        if line:
            lines.add(line)
    return lines


def _text_overlap(a, b):
    la, lb = _norm_lines(a), _norm_lines(b)
    if not la or not lb:
        return 0.0
    return len(la & lb) / max(len(la), len(lb))


class _Ctx:
    """The library track being matched."""

    def __init__(self, track_name, artist_name, album_name, duration_s, alt_artists):
        self.track_name = track_name
        self.artist_name = artist_name
        self.album_name = album_name
        self.duration_s = duration_s
        self.alt_artists = tuple(a for a in (alt_artists or ()) if a)
        self.mb_artist = None
        self.mb_length_s = None


class LRCLIBProvider(LyricsProvider):
    """Fetch synced and plain lyrics from LRCLIB (free, no API key needed).

    Order, inside the request budget:
      0. MusicBrainz-identified exact lookup (``mb_exact``; replaces 1 when
         the recording lookup succeeded)
      1. exact lookup, raw title/artist/duration, no album (``exact_noalbum``)
      2. the same with the raw album (``exact_album``)
      3. query form (noise/feat. removed), no album
      4. comparison form (``variant``)
      5-6. up to two more title variants (``variant``)
      7. structured search ``track_name`` + ``artist_name`` (``search_struct``)
      8. ``q`` search, only when nothing usable was found (``search_q``)
    Identical parameter sets are requested once. Steps 1-6 stop early on a
    good synced hit; a plain-only hit or a strong marker jumps to step 7.
    """

    name = 'lrclib'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, track_name=None, artist_name=None, album_name=None,
               duration=None, mb_recording_id=None, *, alt_artist_names=(),
               strict=True, **kwargs):
        """Search for lyrics. Returns scored results, best first.

        Each result: synced_lyrics, plain_lyrics, provider, score,
        match_details, strategy, source_id, track_name, artist_name,
        album_name, duration; strong markers carry instrumental=True and no
        lyrics.
        """
        if not track_name:
            return []
        previous = getattr(_tls, 'state', None)
        _tls.state = _SearchState()
        try:
            return self._cascade(track_name, artist_name, album_name, duration,
                                 mb_recording_id, alt_artist_names, strict)
        finally:
            _tls.state = previous

    # ------------------------------------------------------------------
    # Cascade
    # ------------------------------------------------------------------

    def _cascade(self, track_name, artist_name, album_name, duration,
                 mb_recording_id, alt_artist_names, strict):
        state = _tls.state
        meta = clean_for_search(track_name, artist_name or '')
        ctx = _Ctx(track_name, artist_name, album_name, duration_seconds(duration), alt_artist_names)
        results = {}

        # --- Plan the exact lookups ---
        gets = []
        mb_ok = False
        if mb_recording_id:
            mb_data = self._fetch_musicbrainz_recording(mb_recording_id)
            if mb_data:
                ctx.mb_artist = self._extract_mb_artist(mb_data) or None
                mb_ms = mb_data.get('length')
                try:
                    ctx.mb_length_s = int(round(float(mb_ms) / 1000)) if mb_ms else None
                except (TypeError, ValueError):
                    ctx.mb_length_s = None
                mb_title = mb_data.get('title') or ''
                if mb_title and ctx.mb_artist:
                    gets.append(('mb_exact', mb_title, ctx.mb_artist, None, ctx.mb_length_s))
                    mb_ok = True
                logger.debug(
                    f"MusicBrainz lookup for {mb_recording_id}: title='{mb_title}', "
                    f"artist='{ctx.mb_artist}', duration={ctx.mb_length_s}s"
                )
        if artist_name:
            raw_title = re.sub(r'\s+', ' ', track_name).strip()
            raw_artist = artist_query_form(artist_name)
            if not mb_ok:
                gets.append(('exact_noalbum', raw_title, raw_artist, None, ctx.duration_s))
            if album_name:
                gets.append(('exact_album', raw_title, raw_artist, album_name, ctx.duration_s))
            gets.append(('exact_noalbum', meta['title_query'], meta['artist_query'], None, ctx.duration_s))
            gets.append(('variant', comparison_key(track_name), fold_text(raw_artist), None, ctx.duration_s))
            planned = {_dedupe_key('get', self._get_params(g[1], g[2], g[3], g[4])) for g in gets}
            extra = 0
            for variant in meta.get('title_variants', []):
                if extra >= LRCLIB_MAX_VARIANT_GETS:
                    break
                g = ('variant', variant, meta['artist_primary'] or meta['artist_clean'], None, ctx.duration_s)
                key = _dedupe_key('get', self._get_params(g[1], g[2], g[3], g[4]))
                if key in planned:
                    continue
                planned.add(key)
                gets.append(g)
                extra += 1

        try:
            # --- Exact lookups ---
            for strategy, title, artist, album, dur in gets:
                if state.soft_misses >= LRCLIB_SOFT_MISS_LIMIT:
                    break
                try:
                    data = self._get_exact(title, artist, album, dur)
                except ProviderTransientError:
                    continue
                if data is None or data is _SOFT_MISS:
                    continue
                self._add(results, self._make_result(data, strategy, ctx))
                if self._satisfied(results):
                    return self._finish(results, ctx)
                if self._needs_search_now(results):
                    break

            # --- Structured search ---
            if artist_name:
                params = {'track_name': meta['title_query'], 'artist_name': meta['artist_query']}
                struct_strategy = 'search_struct'
            else:
                params = {'track_name': meta['title_query']}
                struct_strategy = 'search_title'
            short_circuit = state.soft_misses >= LRCLIB_SOFT_MISS_LIMIT
            self._run_search(results, params, struct_strategy, ctx)
            if (self._satisfied(results) or short_circuit
                    or state.soft_misses >= LRCLIB_SOFT_MISS_LIMIT):
                return self._finish(results, ctx)

            # --- q search, only when nothing usable was found ---
            if artist_name and not self._has_usable(results):
                q = f"{meta['artist_query']} {meta['title_query']}".strip()
                self._run_search(results, {'q': q}, 'search_q', ctx)
            if not strict and not self._has_usable(results):
                self._run_search(results, {'q': meta['title_query']}, 'search_title', ctx)
        except _BudgetExhausted:
            logger.debug(f"LRCLIB request budget reached for '{track_name}'")

        return self._finish(results, ctx)

    def _finish(self, results, ctx):
        state = _tls.state
        out = list(results.values())
        self._attach_synced(out, ctx)
        if not out and state.calls and not state.definitive:
            raise ProviderTransientError(
                f"LRCLIB: no request answered definitively "
                f"({state.soft_misses} soft misses, {state.transient} transient errors)"
            )
        out.sort(key=_sort_key)
        return out

    @staticmethod
    def _add(results, res):
        if not res:
            return
        sid = res.get('source_id')
        key = sid if sid is not None else ('anon', len(results))
        old = results.get(key)
        if old is None or _STRATEGY_RANK[res['strategy']] > _STRATEGY_RANK[old['strategy']]:
            results[key] = res

    @staticmethod
    def _usable(r):
        md = r.get('match_details') or {}
        return (not r.get('instrumental') and not md.get('gate')
                and (r.get('score') or 0) >= _SATISFIED_SCORE)

    def _has_usable(self, results):
        return any(self._usable(r) for r in results.values())

    def _satisfied(self, results):
        """A usable synced hit: nothing better to look for."""
        return any(self._usable(r) and r.get('synced_lyrics') for r in results.values())

    def _needs_search_now(self, results):
        """A plain-only usable hit or a strong marker: skip the remaining
        exact lookups and look for synced records / real lyrics in search."""
        return any(self._usable(r) or r.get('instrumental') for r in results.values())

    def _run_search(self, results, params, strategy, ctx):
        try:
            items = self._search_api(params)
        except ProviderTransientError:
            return
        if not items or items is _SOFT_MISS:
            return
        scored = []
        for item in items:
            if not isinstance(item, dict):
                continue
            res = self._make_result(item, strategy, ctx)
            if res and not res.get('instrumental'):
                scored.append(res)
        scored.sort(key=_sort_key)
        for res in scored[:_SEARCH_RESULTS_KEPT]:
            self._add(results, res)

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _make_result(self, data, strategy, ctx):
        synced = data.get('syncedLyrics') or None
        plain = data.get('plainLyrics') or None
        marker = bool(data.get('instrumental')) and not synced and not plain
        if not synced and not plain and not marker:
            return None
        mb_matched = strategy == 'mb_exact'
        exact_get = strategy in ('mb_exact', 'exact_album') or (
            strategy == 'exact_noalbum' and (data.get('trackName') or '').casefold()
            == (ctx.track_name or '').strip().casefold())
        match = self.score_result(
            ctx.track_name, ctx.artist_name, ctx.duration_s,
            data.get('trackName', ''), data.get('artistName', ''), data.get('duration'),
            # The MusicBrainz credit of the track's own recording id is a trusted
            # name too ('Various Artists' compilations without an ARTIST tag).
            alt_artists=ctx.alt_artists + ((ctx.mb_artist,) if ctx.mb_artist else ()),
            exact_get=exact_get, mb_matched=mb_matched,
        )
        match['mb_artist'] = ctx.mb_artist
        match['strategy'] = strategy
        res = {
            'synced_lyrics': synced,
            'plain_lyrics': plain,
            'provider': self.name,
            'score': match['score'],
            'match_details': match,
            'strategy': strategy,
            'source_id': data.get('id'),
            'track_name': data.get('trackName', ''),
            'artist_name': data.get('artistName', ''),
            'album_name': data.get('albumName') or None,
            'duration': data.get('duration') or None,
        }
        if marker:
            if not self._is_strong_marker(res, ctx):
                logger.debug(
                    f"LRCLIB: dropping weak instrumental marker {res['source_id']} "
                    f"({strategy}) for '{ctx.track_name}'"
                )
                return None
            res['instrumental'] = True
        return res

    @staticmethod
    def _is_strong_marker(res, ctx):
        """§1.4: only an album- or MusicBrainz-verified, duration-verified,
        ungated, same-title, same-version marker counts."""
        from lyrarr.metadata.lyrics_text import instrumental_title_tier

        md = res['match_details']
        strategy = res['strategy']
        if strategy == 'mb_exact':
            if ctx.mb_length_s is None:
                return False
        elif strategy != 'exact_album':
            return False
        if ctx.duration_s is None or not res.get('duration'):
            return False
        try:
            delta = abs(float(ctx.duration_s) - float(res['duration']))
        except (TypeError, ValueError):
            return False
        if delta > STRONG_MARKER_MAX_DELTA_S:
            return False
        if md.get('gate') or md.get('artist_via_alias') or md.get('karaoke'):
            return False
        if (md.get('title_score') or 0) < STRONG_MARKER_MIN_TITLE_SCORE:
            return False
        q_strong = instrumental_title_tier(ctx.track_name) == 'strong'
        r_strong = instrumental_title_tier(res.get('track_name')) == 'strong'
        return q_strong == r_strong

    @staticmethod
    def _attach_synced(results, ctx):
        """Give a plain-only exact hit the synced lyrics of a matching search
        record (same song, same edit): LRCLIB often has both records."""
        synced_pool = [
            r for r in results
            if r.get('synced_lyrics') and not r.get('instrumental')
            and r['strategy'] not in _EXACT_STRATEGIES
            and not r['match_details'].get('gate')
            and (r['match_details'].get('title_score') or 0) >= _ATTACH_MIN_TITLE_SCORE
            and r.get('duration')
        ]
        if not synced_pool:
            return
        for r in results:
            if r['strategy'] not in _EXACT_STRATEGIES or r.get('synced_lyrics') or r.get('instrumental'):
                continue
            if r['match_details'].get('gate'):
                continue
            ref = ctx.duration_s or r.get('duration')
            if not ref:
                continue
            best = None
            for s in synced_pool:
                delta = abs(float(s['duration']) - float(ref))
                if delta > _ATTACH_MAX_DELTA_S:
                    continue
                if r.get('plain_lyrics') and s.get('plain_lyrics') and \
                        _text_overlap(r['plain_lyrics'], s['plain_lyrics']) < _ATTACH_MIN_TEXT_OVERLAP:
                    continue
                if best is None or (s['score'], -delta) > (best[0]['score'], -best[1]):
                    best = (s, delta)
            if best:
                r['synced_lyrics'] = best[0]['synced_lyrics']
                r['synced_source_id'] = best[0]['source_id']
                r['match_details']['synced_attached_from'] = best[0]['source_id']

    # ------------------------------------------------------------------
    # MusicBrainz
    # ------------------------------------------------------------------

    def _fetch_musicbrainz_recording(self, recording_id):
        """Fetch a recording (title, length, artist credits) from MusicBrainz.

        Returns the JSON recording object, or None on failure. MusicBrainz
        requests are outside the LRCLIB budget and throttled separately.
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
                    'inc': 'artist-credits',
                    'fmt': 'json',
                },
                timeout=10,
                headers={
                    'User-Agent': USER_AGENT,
                    'Accept': 'application/json',
                }
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, dict) else None
            elif response.status_code == 404:
                logger.debug(f"MusicBrainz recording not found: {recording_id}")
            elif is_transient_status(response.status_code):
                note_transient_error()
                logger.warning(
                    f"MusicBrainz transient error {response.status_code}, skipping recording lookup"
                )
            else:
                logger.debug(
                    f"MusicBrainz API returned {response.status_code} "
                    f"for recording {recording_id}"
                )

        except (requests.exceptions.RequestException, ValueError) as e:
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
            if not isinstance(credit, dict):
                continue
            name = credit.get('name', '')
            joinphrase = credit.get('joinphrase', '')
            parts.append(name + joinphrase)

        return ''.join(parts).strip()

    # ------------------------------------------------------------------
    # LRCLIB HTTP (every request goes through _call)
    # ------------------------------------------------------------------

    @staticmethod
    def _get_params(track_name, artist_name, album_name=None, duration_s=None):
        params = {'track_name': track_name, 'artist_name': artist_name}
        if album_name:
            params['album_name'] = album_name
        if duration_s is not None:
            params['duration'] = duration_s
        return params

    def _get_exact(self, track_name, artist_name, album_name=None, duration_s=None):
        """LRCLIB /api/get.

        Returns the record dict, None (404 / no record), or _SOFT_MISS
        (503 / ServerOverloaded, never raised). Raises ProviderTransientError
        for connection errors, timeouts, 429 and other 5xx, and
        _BudgetExhausted when the per-track budget is spent.
        """
        if not track_name or not artist_name:
            return None
        kind, payload = self._call('get', self._get_params(track_name, artist_name, album_name, duration_s))
        if kind == 'soft':
            return _SOFT_MISS
        if kind == 'ok' and isinstance(payload, dict):
            return payload
        return None

    def _search_api(self, params):
        """LRCLIB /api/search. Returns the item list, None, or _SOFT_MISS."""
        kind, payload = self._call('search', params)
        if kind == 'soft':
            return _SOFT_MISS
        if kind == 'ok' and isinstance(payload, list):
            return payload
        return None

    def _call(self, endpoint, params):
        """One de-duplicated, budgeted LRCLIB request.

        Returns ('ok', json) | ('miss', None) | ('soft', None). Raises
        ProviderTransientError (hard transient failure; cached, so a repeat
        costs nothing) or _BudgetExhausted.
        """
        state = getattr(_tls, 'state', None)
        if state is None:  # called outside search(): a one-off budget
            state = _SearchState()
        key = _dedupe_key(endpoint, params)
        if key in state.cache:
            kind, payload = state.cache[key]
        else:
            clean = {k: v for k, v in params.items() if v not in (None, '')}
            kind, payload = self._http(endpoint, clean, state)
            state.cache[key] = (kind, payload)
        if kind == 'transient':
            raise ProviderTransientError(payload)
        return kind, payload

    def _http(self, endpoint, params, state):
        url = f"{lrclib_api_url()}/{endpoint}"
        while True:
            if state.calls >= LRCLIB_MAX_CALLS_PER_TRACK:
                raise _BudgetExhausted()
            state.calls += 1
            rate_limiter.wait('lrclib')
            try:
                response = _session.get(url, params=params, timeout=_TIMEOUT,
                                        headers={'User-Agent': USER_AGENT})
            except requests.exceptions.RequestException as e:
                state.transient += 1
                note_transient_error()
                return 'transient', f"LRCLIB {endpoint} error: {e}"

            status = response.status_code
            if status == 200:
                state.definitive += 1
                try:
                    return 'ok', response.json()
                except ValueError:
                    return 'miss', None
            if _is_soft_miss(response):
                if not state.retry_used and state.calls < LRCLIB_MAX_CALLS_PER_TRACK:
                    state.retry_used = True
                    time.sleep(retry_after_seconds(response, default=1.0,
                                                   cap=LRCLIB_MAX_RETRY_SLEEP_S))
                    continue
                state.soft_misses += 1
                logger.debug(f"LRCLIB {endpoint} soft miss ({status})")
                return 'soft', None
            if is_transient_status(status):
                state.transient += 1
                note_transient_error()
                return 'transient', f"LRCLIB returned {status} on {endpoint}"
            # 404 (no record) and other 4xx: a definitive "no".
            state.definitive += 1
            return 'miss', None


def _sort_key(r):
    """Score desc, then synced first, then the smallest duration difference."""
    md = r.get('match_details') or {}
    diff = md.get('duration_diff')
    return (-(r.get('score') or 0), 0 if r.get('synced_lyrics') else 1,
            diff if diff is not None else float('inf'))
