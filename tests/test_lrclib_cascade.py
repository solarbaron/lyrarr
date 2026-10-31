"""LRCLIB cascade: soft misses, request budget, marker strength, synced
preference (PLAN §1.4 / §3.2). HTTP is mocked on lrclib._session.get — no
network. All lyric text is synthetic.
"""

import sys
import tempfile

if 'lyrarr.app.get_args' not in sys.modules:
    # Provider modules import app config — point it at a throwaway dir, never data/.
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-lrclib-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

import pytest  # noqa: E402
import requests  # noqa: E402

from lyrarr.metadata import provider_utils  # noqa: E402
from lyrarr.metadata.lyrics import lrclib  # noqa: E402
from lyrarr.metadata.provider_utils import (  # noqa: E402
    ProviderTransientError,
    begin_search,
    search_had_transient_error,
)

SYNCED = "\n".join(f"[00:{10 + 4 * i:02d}.00]placeholder verse line number {i}" for i in range(8))
PLAIN = "\n".join(f"placeholder verse line number {i}" for i in range(8))


class Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data


BUSY = {'message': 'The server is busy, please retry in a moment', 'name': 'ServerOverloaded',
        'statusCode': 503}


def busy():
    return Resp(503, BUSY)


def record(rid, title='Song', artist='Band', album='Album', duration=200.0, synced=SYNCED, plain=PLAIN,
           instrumental=False):
    return {'id': rid, 'trackName': title, 'artistName': artist, 'albumName': album,
            'duration': duration, 'syncedLyrics': synced, 'plainLyrics': plain,
            'instrumental': instrumental}


class Recorder:
    """Fake _session.get: routes on endpoint, records every call."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, url, params=None, **kwargs):
        params = dict(params or {})
        if 'musicbrainz.org' in url:
            endpoint = 'mb'
        else:
            endpoint = url.rsplit('/', 1)[-1]
        self.calls.append((endpoint, params))
        out = self.handler(endpoint, params)
        if isinstance(out, Exception):
            raise out
        return out

    def lrclib_calls(self):
        return [c for c in self.calls if c[0] != 'mb']

    def gets(self):
        return [p for e, p in self.calls if e == 'get']

    def searches(self):
        return [p for e, p in self.calls if e == 'search']


@pytest.fixture
def env(monkeypatch):
    sleeps = []
    monkeypatch.setattr(provider_utils.rate_limiter, 'wait', lambda name: None)
    monkeypatch.setattr(lrclib.time, 'sleep', lambda s: sleeps.append(s))

    def install(handler):
        rec = Recorder(handler)
        monkeypatch.setattr(lrclib._session, 'get', rec)
        return rec

    begin_search()
    return {'install': install, 'sleeps': sleeps, 'provider': lrclib.LRCLIBProvider()}


def _search(env, **kw):
    args = dict(track_name='Song', artist_name='Band', album_name='Album', duration=200)
    args.update(kw)
    return env['provider'].search(**args)


# ---------------------------------------------------------------------------
# Soft misses and transient errors
# ---------------------------------------------------------------------------

class TestSoftMiss:
    def test_first_get_503_then_no_album_get_200_found(self, env):
        state = {'n': 0}

        def handler(endpoint, params):
            if endpoint == 'get':
                state['n'] += 1
                if state['n'] == 1:
                    return busy()
                if 'album_name' not in params:
                    return Resp(200, record(1))
                return Resp(404, {'message': 'not found'})
            return Resp(200, [])

        rec = env['install'](handler)
        results = _search(env)
        assert results and results[0]['source_id'] == 1
        assert results[0]['synced_lyrics']
        assert results[0]['strategy'] == 'exact_noalbum'
        assert len(env['sleeps']) == 1
        assert search_had_transient_error() is False  # a soft miss is not transient
        assert len(rec.lrclib_calls()) == 2

    def test_mb_hit_kept_when_next_get_503(self, env):
        def handler(endpoint, params):
            if endpoint == 'mb':
                return Resp(200, {'title': 'Song', 'length': 200400,
                                  'artist-credit': [{'name': 'Band', 'joinphrase': ''}]})
            if endpoint == 'get' and params.get('artist_name') == 'Band' and 'album_name' not in params:
                # MB-identified record, plain only, mediocre score (artist spelled differently)
                return Resp(200, record(7, artist='The Band Collective', synced=None))
            return busy()

        rec = env['install'](handler)
        results = _search(env, mb_recording_id='mbid-1')
        assert [r['source_id'] for r in results] == [7]
        hit = results[0]
        assert hit['strategy'] == 'mb_exact'
        assert hit['match_details']['mb_matched'] is True
        assert hit['match_details']['mb_artist'] == 'Band'
        mb_params = [p for e, p in rec.calls if e == 'mb'][0]
        assert mb_params['inc'] == 'artist-credits'
        # The cascade went on (plain-only hit → structured search), which
        # answered 503: no exception, the MB hit is kept.
        assert len(rec.lrclib_calls()) >= 2
        assert rec.searches()

    def test_all_503_is_transient(self, env):
        env['install'](lambda e, p: busy())
        with pytest.raises(ProviderTransientError):
            _search(env)

    def test_connection_error_in_one_step_keeps_results(self, env):
        def handler(endpoint, params):
            if endpoint == 'get' and 'album_name' not in params and params['track_name'] == 'Song':
                return Resp(200, record(3, synced=None))
            if endpoint == 'get':
                return requests.exceptions.ConnectTimeout('slow')
            return Resp(200, [])

        env['install'](handler)
        results = _search(env)
        assert [r['source_id'] for r in results] == [3]

    def test_404_everywhere_is_a_clean_miss(self, env):
        def handler(endpoint, params):
            return Resp(404, {'message': 'nope'}) if endpoint == 'get' else Resp(200, [])

        env['install'](handler)
        assert _search(env) == []
        assert search_had_transient_error() is False

    def test_hard_transient_everywhere_raises(self, env):
        env['install'](lambda e, p: Resp(502, None))
        with pytest.raises(ProviderTransientError):
            _search(env)
        assert search_had_transient_error() is True


# ---------------------------------------------------------------------------
# Request budget
# ---------------------------------------------------------------------------

class TestBudget:
    def test_full_miss_at_most_8_calls(self, env):
        def handler(endpoint, params):
            return Resp(404, {'message': 'nope'}) if endpoint == 'get' else Resp(200, [])

        rec = env['install'](handler)
        _search(env, track_name='Some Song (Remastered 2011) (feat. Guest)', artist_name='Lead feat. Guest',
                album_name='Album (Deluxe)', duration=211)
        assert len(rec.lrclib_calls()) <= lrclib.LRCLIB_MAX_CALLS_PER_TRACK
        # The structured search and the q search both ran.
        assert {('track_name' in p, 'q' in p) for p in rec.searches()} == {(True, False), (False, True)}

    def test_all_503_budget_one_retry_sleep_capped(self, env):
        def handler(endpoint, params):
            return Resp(503, BUSY, headers={'Retry-After': '30'})

        rec = env['install'](handler)
        with pytest.raises(ProviderTransientError):
            _search(env, track_name='Some Song (feat. Guest)', duration=211)
        assert len(rec.lrclib_calls()) <= lrclib.LRCLIB_MAX_CALLS_PER_TRACK
        assert len(env['sleeps']) == 1
        assert sum(env['sleeps']) <= 2.0
        # The first request was retried once (same params twice in a row).
        first, second = rec.lrclib_calls()[:2]
        assert first == second

    def test_identical_forms_requested_once(self, env):
        def handler(endpoint, params):
            return Resp(404, {'message': 'nope'}) if endpoint == 'get' else Resp(200, [])

        rec = env['install'](handler)
        _search(env, track_name='hello', artist_name='band', album_name=None, duration=200)
        keys = [(e, tuple(sorted((k, str(v).casefold()) for k, v in p.items()))) for e, p in rec.lrclib_calls()]
        assert len(keys) == len(set(keys))
        # raw == query form == comparison form: one /get only.
        assert len(rec.gets()) == 1

    def test_case_only_difference_is_deduped(self, env):
        def handler(endpoint, params):
            return Resp(404, {'message': 'nope'}) if endpoint == 'get' else Resp(200, [])

        rec = env['install'](handler)
        _search(env, track_name='Hello', artist_name='Band', album_name=None, duration=200)
        assert len(rec.gets()) == 1

    def test_two_soft_misses_skip_to_one_structured_search(self, env):
        def handler(endpoint, params):
            if endpoint == 'get':
                return busy()
            return Resp(200, [record(9, duration=201.0)])

        rec = env['install'](handler)
        results = _search(env, track_name='Song (feat. Guest)')
        gets = rec.gets()
        # retry of the first + one more soft miss = 3 /get requests, then no more /get
        assert len(gets) == 3
        assert len(rec.searches()) == 1
        assert 'track_name' in rec.searches()[0] and 'q' not in rec.searches()[0]
        assert rec.lrclib_calls()[-1][0] == 'search'
        assert [r['source_id'] for r in results] == [9]
        assert results[0]['strategy'] == 'search_struct'


# ---------------------------------------------------------------------------
# Synced preference, markers, ISRC
# ---------------------------------------------------------------------------

# Shape of the live "The Boxer" case (ids/albums/durations from LRCLIB, text synthetic):
# the exact lookup returns a plain-only record; search has synced ones at 309-311 s.
BOXER_SEARCH = [
    (37021618, 'Simon and Garfunkel Best', 313.0),
    (37525346, 'The Simon and Garfunkel Collection: 17 of Their All-Time Greatest Recordings', 87.0),
    (37173937, '500 Greatest Songs - Rolling Stone Magazines 2010', 309.0),
    (37022730, '2003-12-20: Atlanta, GA, USA', 501.0),
    (37374758, 'Ein (un)möglicher Härtefall', 310.0),
    (37403009, 'Billboard Top 100 Of 1969', 311.0),
    (34819577, 'The Concert in Central Park', 285.0),
]


class TestSyncedPreference:
    def test_boxer_plain_exact_then_synced_search(self, env):
        boxer_plain = "\n".join(f"placeholder boxer line {i}" for i in range(10))
        boxer_synced = "\n".join(f"[00:{10 + 5 * i:02d}.00]placeholder boxer line {i}" for i in range(10))

        def handler(endpoint, params):
            if endpoint == 'get':
                if 'album_name' in params:
                    return Resp(404, {'message': 'nope'})
                return Resp(200, record(595740, title='The Boxer', artist='Simon & Garfunkel',
                                        album='Bridge Over Troubled Water', duration=310.0,
                                        synced=None, plain=boxer_plain))
            return Resp(200, [record(rid, title='The Boxer', artist='Simon & Garfunkel', album=alb,
                                     duration=dur, synced=boxer_synced, plain=boxer_plain)
                              for rid, alb, dur in BOXER_SEARCH])

        rec = env['install'](handler)
        results = env['provider'].search(track_name='The Boxer', artist_name='Simon & Garfunkel',
                                         album_name='Bridge Over Troubled Water', duration=310)
        assert results[0]['synced_lyrics']
        assert any(r['source_id'] == 595740 for r in results)
        exact = next(r for r in results if r['source_id'] == 595740)
        assert exact['synced_lyrics'] and exact['synced_source_id'] in (37374758, 37173937, 37403009)
        assert len(rec.searches()) == 1
        # Every search item was scanned; far-off durations are gated, not dropped silently.
        by_id = {r['source_id']: r for r in results}
        assert by_id[37022730]['match_details']['gate'] == 'duration_mismatch'
        for r in results:
            for key in ('strategy', 'source_id', 'track_name', 'artist_name', 'album_name', 'duration'):
                assert key in r

    def test_search_scans_more_than_ten_items(self, env):
        items = [record(100 + i, title=f'Other Thing {i}', duration=200.0) for i in range(14)]
        items.append(record(999, duration=200.0))

        def handler(endpoint, params):
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, items)

        env['install'](handler)
        results = _search(env)
        assert results[0]['source_id'] == 999

    def test_sort_score_then_synced_then_delta(self, env):
        def handler(endpoint, params):
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [record(1, duration=200.0, synced=None),
                              record(2, duration=200.0),
                              record(3, duration=201.0)])

        env['install'](handler)
        results = _search(env)
        assert [r['source_id'] for r in results] == [2, 1, 3]


class TestMarkers:
    def _marker(self, rid, **kw):
        return record(rid, synced=None, plain=None, instrumental=True, **kw)

    def test_exact_album_marker_kept_and_search_still_runs(self, env):
        def handler(endpoint, params):
            if endpoint == 'get':
                if 'album_name' in params:
                    return Resp(200, self._marker(50, duration=201.0))
                return Resp(404, {'message': 'nope'})
            return Resp(200, [record(51, duration=200.0)])

        rec = env['install'](handler)
        results = _search(env)
        marker = next(r for r in results if r.get('instrumental'))
        assert marker['strategy'] == 'exact_album' and marker['source_id'] == 50
        assert marker['synced_lyrics'] is None and marker['plain_lyrics'] is None
        assert len(rec.searches()) == 1
        lyric = next(r for r in results if r['source_id'] == 51)
        assert lyric['synced_lyrics']

    def test_noalbum_marker_without_duration_dropped_and_search_runs(self, env):
        def handler(endpoint, params):
            if endpoint == 'get':
                if 'album_name' not in params:
                    return Resp(200, self._marker(60, duration=None))
                return Resp(404, {'message': 'nope'})
            return Resp(200, [record(61, duration=200.0)])

        rec = env['install'](handler)
        results = _search(env)
        assert not any(r.get('instrumental') for r in results)
        assert [r['source_id'] for r in results] == [61]
        assert rec.searches()

    def test_exact_album_marker_without_query_duration_dropped(self, env):
        def handler(endpoint, params):
            if endpoint == 'get' and 'album_name' in params:
                return Resp(200, self._marker(62, duration=200.0))
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [])

        env['install'](handler)
        assert _search(env, duration=None) == []

    def test_exact_album_marker_far_duration_dropped(self, env):
        def handler(endpoint, params):
            if endpoint == 'get' and 'album_name' in params:
                return Resp(200, self._marker(63, duration=205.0))
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [])

        env['install'](handler)
        assert _search(env) == []

    def test_mb_marker_without_mb_length_dropped(self, env):
        def handler(endpoint, params):
            if endpoint == 'mb':
                return Resp(200, {'title': 'Song', 'length': None,
                                  'artist-credit': [{'name': 'Band', 'joinphrase': ''}]})
            if endpoint == 'get' and 'duration' not in params:
                return Resp(200, self._marker(70, duration=200.0))
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [])

        rec = env['install'](handler)
        results = _search(env, album_name=None, mb_recording_id='mbid')
        assert results == []
        assert rec.searches()  # the dropped marker did not stop the cascade

    def test_mb_marker_with_length_kept(self, env):
        def handler(endpoint, params):
            if endpoint == 'mb':
                return Resp(200, {'title': 'Song', 'length': 199600,
                                  'artist-credit': [{'name': 'Band', 'joinphrase': ''}]})
            if endpoint == 'get' and params.get('duration') == 200 and 'album_name' not in params:
                return Resp(200, self._marker(71, duration=200.0))
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [])

        rec = env['install'](handler)
        results = _search(env, album_name=None, mb_recording_id='mbid')
        assert [r['source_id'] for r in results] == [71]
        assert results[0]['instrumental'] is True and results[0]['strategy'] == 'mb_exact'
        # MB length rounds: 199.6 s → 200, not floored to 199.
        assert rec.gets()[0]['duration'] == 200

    def test_marker_on_instrumental_titled_record_for_vocal_title_dropped(self, env):
        def handler(endpoint, params):
            if endpoint == 'get' and 'album_name' in params:
                return Resp(200, self._marker(72, title='Song (Instrumental)', duration=200.0))
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [])

        env['install'](handler)
        assert _search(env) == []

    def test_search_markers_never_returned(self, env):
        def handler(endpoint, params):
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [self._marker(80, duration=200.0)])

        env['install'](handler)
        assert _search(env) == []


class TestNoIsrc:
    def test_no_isrc_query(self, env):
        def handler(endpoint, params):
            if endpoint == 'mb':
                return Resp(200, {'title': 'Song', 'length': 200000, 'isrcs': ['USABC1234567'],
                                  'artist-credit': [{'name': 'Band', 'joinphrase': ''}]})
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [])

        rec = env['install'](handler)
        _search(env, mb_recording_id='mbid')
        for params in rec.searches():
            assert 'USABC1234567' not in ' '.join(str(v) for v in params.values())


class TestResultShape:
    def test_keys_and_strategy(self, env):
        def handler(endpoint, params):
            if endpoint == 'get' and 'album_name' in params:
                return Resp(200, record(5))
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [])

        env['install'](handler)
        [r] = _search(env)
        assert r['strategy'] == 'exact_album'
        assert r['album_name'] == 'Album' and r['duration'] == 200.0
        md = r['match_details']
        for key in ('title_score', 'artist_score', 'duration_diff', 'gate', 'artist_via_alias',
                    'karaoke', 'mb_artist'):
            assert key in md

    def test_alt_artist_names(self, env):
        def handler(endpoint, params):
            if endpoint == 'get':
                return Resp(404, {'message': 'nope'})
            return Resp(200, [record(6, artist='Guest Singer')])

        env['install'](handler)
        [r] = _search(env, artist_name='Album Artist', alt_artist_names=('Guest Singer',))
        assert r['match_details']['gate'] is None
