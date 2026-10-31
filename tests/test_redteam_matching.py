"""Red-team matching audit: previous release (HEAD) vs this release.

Built from LRCLIB responses recorded for a sample of the library snapshot
(record ids, titles, artists, albums and durations are real metadata; every
lyric text here is SYNTHETIC). No network: HTTP is mocked on
``lrclib._session.get``.

Two kinds of tests:
* locks on behaviour that improved over the previous release (must keep passing);
* ``xfail(strict=True)`` regressions: correct lyrics the previous release
  accepted and this one rejects. When the bug is fixed the test XPASSes and
  fails the run: drop the marker then.
"""

import sys
import tempfile

if 'lyrarr.app.get_args' not in sys.modules:
    # Provider modules import app config: point it at a throwaway dir, never data/.
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-redteam-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

import pytest  # noqa: E402

from lyrarr.metadata import download_worker as dw  # noqa: E402
from lyrarr.metadata import provider_utils  # noqa: E402
from lyrarr.metadata.base import LyricsProvider  # noqa: E402
from lyrarr.metadata.lyrics import lrclib  # noqa: E402
from lyrarr.metadata.normalize import clean_for_search  # noqa: E402
from lyrarr.metadata.provider_utils import ProviderTransientError, begin_search  # noqa: E402

score = LyricsProvider.score_result
FLOOR = dw._MIN_ACCEPT_SCORE

# LRCLIB's live answer to an /api/get miss AND to an overloaded /api/search
# (recorded 2026-10-31: a nonexistent title and 32 of 42 structured searches).
BUSY = {'message': 'The server is busy, please retry in a moment', 'name': 'ServerOverloaded',
        'statusCode': 503}


def synced_for(duration_s, n=None):
    """Synthetic LRC whose lines span the track (validation-friendly)."""
    n = n or max(8, int(duration_s // 12))
    step = (duration_s - 20) / n
    return "\n".join(
        f"[{int((10 + i * step) // 60):02d}:{(10 + i * step) % 60:05.2f}]placeholder verse line {i} la la"
        for i in range(n))


def plain_for(duration_s, n=None):
    n = n or max(8, int(duration_s // 12))
    return "\n".join(f"placeholder verse line {i} la la" for i in range(n))


def record(rid, title, artist, album, duration, *, synced=True, plain=True, instrumental=False):
    d = duration or 200
    return {'id': rid, 'trackName': title, 'artistName': artist, 'albumName': album,
            'duration': duration,
            'syncedLyrics': synced_for(d) if synced else None,
            'plainLyrics': plain_for(d) if plain else None,
            'instrumental': instrumental}


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data
        self.headers = {}

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data


@pytest.fixture
def lr(monkeypatch):
    """Replay harness: /api/get misses answer 503 (LRCLIB's live behaviour),
    /api/search answers from a recorded pool (or 503 when overloaded)."""
    sleeps = []
    calls = []
    monkeypatch.setattr(provider_utils.rate_limiter, 'wait', lambda name: None)
    monkeypatch.setattr(lrclib.time, 'sleep', lambda s: sleeps.append(s))
    state = {'pool': [], 'search_status': 200}

    def fake_get(url, params=None, **kw):
        params = dict(params or {})
        endpoint = 'mb' if 'musicbrainz.org' in url else url.rsplit('/', 1)[-1]
        calls.append((endpoint, params))
        if endpoint == 'mb':
            return Resp(404, None)
        if endpoint == 'get':
            for it in state['pool']:
                if (it['trackName'].casefold() == str(params['track_name']).casefold()
                        and it['artistName'].casefold() == str(params['artist_name']).casefold()
                        and (not params.get('album_name')
                             or (it['albumName'] or '').casefold() == str(params['album_name']).casefold())
                        and (params.get('duration') is None
                             or (it['duration'] and abs(it['duration'] - float(params['duration'])) <= 2))):
                    return Resp(200, it)
            return Resp(503, BUSY)
        if state['search_status'] != 200:
            return Resp(state['search_status'], BUSY)
        return Resp(200, list(state['pool']))

    monkeypatch.setattr(lrclib._session, 'get', fake_get)
    begin_search()
    return {'state': state, 'calls': calls, 'sleeps': sleeps, 'provider': lrclib.LRCLIBProvider()}


def worker_choice(results, title, album, duration, artist):
    """The worker's selection for LRCLIB-only results (best_score, prefer synced)."""
    ctx = dw.TrackContext.for_track(title, title, album, duration)
    _markers, _hints, rest = dw._split_results(results)
    ranked = dw.rank_candidates(rest, 'best_score', FLOOR, ctx, prefer_synced=True)
    chosen, _content, outcome = dw.choose_lyrics(
        ranked, 'best_score', track_title=title, artist_name=artist, duration_s=ctx.duration)
    return chosen, outcome


# ---------------------------------------------------------------------------
# Improvements over the previous release (keep passing)
# ---------------------------------------------------------------------------

class TestLrclib503OnMiss:
    """The previous release raised ProviderTransientError on the first /api/get
    503, so every track not stored under its exact title went 'transient'
    (7 of 11 recorded tracks). Now the structured search finds it."""

    def test_get_miss_503_falls_through_to_structured_search(self, lr):
        # Recorded metadata: Björk – Dark Matter, structured search records.
        lr['state']['pool'] = [
            record(36976883, 'Dark Matter', 'Björk', 'Biophilia Live at Manchester I', 201.0),
            record(36951923, 'Dark Matter', 'Bjork', 'Biophilia Live - 2CD+DVD', 185.0),
            record(36228008, 'Dark Matter', 'Björk', "Baldur's Gate 3 (Original Game Soundtrack)", 233.0),
            record(19962640, 'Dark Matter', 'Björk', 'Biophilia (Japanese Edition)', 202.44898),
        ]
        title, artist, album, dur = 'Dark Matter (with choir and organ)', 'Björk', 'Biophilia', 205
        results = lr['provider'].search(track_name=title, artist_name=artist, album_name=album, duration=dur)
        chosen, outcome = worker_choice(results, title, album, dur, artist)
        assert outcome == 'ok'
        assert chosen['source_id'] in (36976883, 19962640)  # within 4 s, not the 185/233 s edits
        lrclib_calls = [c for c in lr['calls'] if c[0] != 'mb']
        assert len(lrclib_calls) <= lrclib.LRCLIB_MAX_CALLS_PER_TRACK
        assert len(lr['sleeps']) <= 1 and sum(lr['sleeps']) <= lrclib.LRCLIB_MAX_RETRY_SLEEP_S

    def test_everything_503_is_transient_not_a_miss(self, lr):
        lr['state']['pool'] = []
        lr['state']['search_status'] = 503
        with pytest.raises(ProviderTransientError):
            lr['provider'].search(track_name='River', artist_name='Joni Mitchell', album_name='Blue',
                                  duration=245)
        assert len([c for c in lr['calls'] if c[0] != 'mb']) <= lrclib.LRCLIB_MAX_CALLS_PER_TRACK

    def test_typographic_apostrophe_title_found_and_accepted(self, lr):
        # Library (MusicBrainz) spelling uses U+2019; LRCLIB records use ASCII.
        lr['state']['pool'] = [
            record(37519560, "Mother Nature's Son", 'The Beatles',
                   'The Beatles (The White Album)-(Remastered)-2CD', 169.0),
            record(34913060, "Mother Nature's Son", 'The Beatles', 'The Beatles (White Album)', 2.0),
        ]
        title, artist, album, dur = 'Mother Nature’s Son', 'The Beatles', 'The Beatles', 169
        results = lr['provider'].search(track_name=title, artist_name=artist, album_name=album, duration=dur)
        chosen, outcome = worker_choice(results, title, album, dur, artist)
        assert outcome == 'ok' and chosen['source_id'] == 37519560
        # The 2-second junk-duration record is duration-gated, never chosen.
        junk = [r for r in results if r['source_id'] == 34913060]
        assert not junk or junk[0]['match_details']['gate'] == 'duration_mismatch'


class TestScoringImprovements:
    @pytest.mark.parametrize('q,r', [
        ('Wu‐Tang Clan', 'Wu-Tang Clan'),       # U+2010 (MusicBrainz style) vs ASCII hyphen
        ('Mach‐Hommy', 'Mach-Hommy'),
        ('Tyler, The Creator', 'Tyler The Creator'),
        ('death’s dynamic shroud', "death's dynamic shroud"),
        ('Kendrick Lamar', 'Kendrick Lamar, SZA'),
        ('Lil Ugly Mane', 'Lil Ugly Mane, Nickelus F'),
    ])
    def test_artist_spellings_accepted(self, q, r):
        m = score('Song', q, 200, 'Song', r, 200)
        assert m['gate'] is None and m['artist_score'] >= 90

    @pytest.mark.parametrize('q,r', [
        ('Faith (live)', 'Faith - Live'),
        ('Mother Nature’s Son', "Mother Nature's Son - Remastered 2009"),
        ('You and I, Part II', 'You And I, Part 2'),
        ('Scarface vs. Sosa, Pt. 2', 'Scarface vs Sosa Part 2'),
        ('Gazzillion Ear (Dr Who Dat? remix)', 'GAZZILLION EAR - DR WHO DAT? REMIX'),
        ('암전고백 (Lights Off Repentance)', '암전고백'),
        ('DNA.', 'DNA'),
    ])
    def test_title_variants_accepted(self, q, r):
        m = score(q, 'Artist', 200, r, 'Artist', 200)
        assert m['gate'] is None and m['score'] >= FLOOR

    @pytest.mark.parametrize('q,r', [
        ('Scarface vs. Sosa, Pt. 2', 'Scarface vs. Sosa, Pt. 1'),
        ('Clueless 2 (feat. Ilwad Awale)', 'Clueless'),
        ('Come On (Let the Good Times Roll)', 'Come On (Part 1)'),
        ('Intro', 'Intro (Skit)'),
    ])
    def test_wrong_song_same_artist_gated(self, q, r):
        assert score(q, 'Artist', 200, r, 'Artist', 200)['gate'] == 'title_mismatch'

    def test_dakuten_kept_in_query_form(self):
        # The previous release queried 'あなたのかさり' (voicing mark stripped).
        assert clean_for_search('あなたのかざり', '青葉市子')['title_query'] == 'あなたのかざり'


# ---------------------------------------------------------------------------
# Regressions vs the previous release (fixed; keep as regression tests)
# ---------------------------------------------------------------------------

# A library artist whose OWN name contains a credit separator (',' '&' '/'),
# against a provider credit that appends a guest or uses another separator.
# The previous release scored these 100; this one caps them at 50
# (artist_mismatch): the result's parts split the library name apart, and
# the library name is a token subset of the longer credit.
# In the snapshot library: Tyler, The Creator (163 tracks), Blu & Exile (49),
# Joey Valence & Brae (40), ... LRCLIB and NetEase (', '.join(artists))
# produce exactly these credit strings.
COMPOUND_NAME_GUEST = [
    ('Tyler, The Creator', 'Tyler, The Creator, Kali Uchis'),
    ('Tyler, The Creator', 'Tyler, The Creator & Kali Uchis'),
    ('Tyler, The Creator', 'Tyler, The Creator/Kali Uchis'),
    ('Joey Valence & Brae', 'Joey Valence & Brae, JPEGMAFIA'),   # recorded LRCLIB 35223387
    ('Joey Valence & Brae', 'Joey Valence, Brae'),               # recorded LRCLIB 29691363
    ('Blu & Exile', 'Blu, Exile'),
    ('Simon & Garfunkel', 'Simon & Garfunkel, Andy Williams'),
    ('Earth, Wind & Fire', 'Earth, Wind & Fire, The Emotions'),
    ('Mumford & Sons', 'Mumford & Sons, Baaba Maal'),
]


@pytest.mark.parametrize('q,r', COMPOUND_NAME_GUEST)
def test_compound_artist_with_guest_credit_accepted(q, r):
    m = score('Song', q, 200, 'Song', r, 200)
    assert m['gate'] is None and m['score'] >= FLOOR


def test_compound_artist_only_record_has_guest_credit(lr):
    # The exact /api/get on 'Joey Valence & Brae' misses (only guest-credited
    # records exist); the structured search returns them and all are gated.
    lr['state']['pool'] = [
        record(35223387, 'WASSUP', 'Joey Valence & Brae, JPEGMAFIA', 'WASSUP', 181.0),
        record(29691363, 'WASSUP', 'Joey Valence, Brae', 'HYPERYOUTH (afterparty)', 181.0),
    ]
    title, artist, album, dur = 'WASSUP', 'Joey Valence & Brae', 'HYPERYOUTH', 181
    results = lr['provider'].search(track_name=title, artist_name=artist, album_name=album, duration=dur)
    chosen, outcome = worker_choice(results, title, album, dur, artist)
    assert outcome == 'ok' and chosen is not None


# Guards for whoever fixes the regression above: these must stay rejected.
@pytest.mark.parametrize('q,r', [
    ('Bush', 'Kate Bush'),
    ('Queen', 'Queen Latifah'),
    ('Tyler, The Creator', 'Tyler'),
    ('Simon & Garfunkel', 'Paul Simon'),
    ('Blu & Exile', 'Exile'),
    ('Joey Valence & Brae', 'JPEGMAFIA'),
])
def test_compound_artist_fix_guards(q, r):
    assert score('Song', q, 200, 'Song', r, 200)['gate'] == 'artist_mismatch'
