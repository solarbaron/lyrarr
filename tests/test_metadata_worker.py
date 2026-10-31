"""End-to-end tests for the scheduled download worker against a throwaway SQLite DB.

Providers are fakes; nothing touches the network or the repo's data/ dir.
Covers: score floor + gates, prefer_synced after the floor, provider
attribution through merge, blacklist (incl. a translated file's source),
retry/backoff on every give-up path (the §3.6 counter table, explicit runs,
cooldown), fresh-row compare-and-set, per-root reachability, missing audio,
Musixmatch excerpt, translation through the persist path, the synced-upgrade
pass and its guards, verify mode, progress events, covers (verification,
real image type, backoff, no directory creation) and the manager save
helpers.

All lyrics text here is synthetic.

The `env` fixture and the result builders are shared with
tests/test_instrumental_policy.py and tests/test_worker_replace_mode.py.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-metadata-worker-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from lyrarr.app.get_args import args as _args  # noqa: E402

_REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
if os.path.abspath(_args.config_dir) == os.path.abspath(_REPO_DATA):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from sqlalchemy import insert  # noqa: E402

from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableBlacklist,
    TableHistory,
    TableLibraryRoots,
    TableLyricsVersions,
    TableProfiles,
    TableTracks,
    database,
    delete,
    init_db,
    select,
    update,
)
from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.metadata import lyrics_store  # noqa: E402
from lyrarr.metadata.provider_utils import ProviderTransientError, health_tracker  # noqa: E402

init_db()

ENGLISH = "\n".join([
    "first line of the verse walks along",
    "second line of the verse sings along",
    "third line of the verse is placeholder text",
    "fourth line of the verse keeps it going",
    "fifth line comes from a made up song",
    "sixth line closes the synthetic verse",
])
SYNCED = "\n".join(f"[00:{10 + 5 * i:02d}.00]{line}" for i, line in enumerate(ENGLISH.splitlines()))
OTHER = ENGLISH.replace("verse", "chorus").replace("along", "around")
DIFFERENT_SYNCED = "\n".join(f"[00:{10 + 5 * i:02d}.00]{line}" for i, line in enumerate([
    "nothing here resembles the other text",
    "a totally different set of words appears",
    "some random line about la la placeholder",
    "and a refrain nobody would confuse with it",
]))
MARKER = '[Instrumental]'


def mk(provider, plain=None, synced=None, *, title='Song', artist='Artist', album='Album',
       duration=200, track_duration=200, score=0.95, title_score=100, gate=None,
       strategy='search', md=None, **extra):
    """A provider result with explicit match_details (independent of score_result)."""
    dd = abs(duration - track_duration) if duration is not None and track_duration else None
    details = {'title_score': title_score, 'artist_score': 100, 'duration_diff': dd,
               'gate': gate, 'mb_matched': False}
    details.update(md or {})
    r = {'plain_lyrics': plain, 'synced_lyrics': synced, 'provider': provider, 'score': score,
         'match_details': details, 'track_name': title, 'artist_name': artist, 'album_name': album,
         'duration': duration, 'strategy': strategy, 'source_id': f'{provider}-{title}'}
    r.update(extra)
    return r


def _result(provider, plain=None, synced=None, artist='Artist', duration=200, score=None, **extra):
    gate = None if artist == 'Artist' else 'artist_mismatch'
    if score is None:
        score = 0.95 if gate is None else 0.3
    return mk(provider, plain=plain, synced=synced, artist=artist, duration=duration, score=score,
              gate=gate, **extra)


def marker(provider='lrclib', *, strategy='exact_album', duration=200, title='Song', **kw):
    """An LRCLIB-style strong "instrumental" flag (no lyrics)."""
    return mk(provider, title=title, duration=duration, strategy=strategy, instrumental=True, **kw)


def hint(provider='netease', *, kind='pure_music', duration_diff=1.0, title='Song', album='Album'):
    """A NetEase/Musixmatch-style instrumental hint (no lyrics)."""
    r = mk(provider, title=title, album=album, duration=None)
    r['instrumental_hint'] = {'provider': provider, 'kind': kind, 'matched_title': title,
                              'matched_album': album, 'score': 0.9, 'title_score': 100,
                              'duration_diff': duration_diff}
    return r


class FakeProvider:
    def __init__(self, name, results=None, exc=None, on_search=None):
        self.name = name
        self.results = results or []
        self.exc = exc
        self.calls = 0
        self.kwargs = []
        self.on_search = on_search

    def search(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        if self.on_search:
            self.on_search()
        if self.exc:
            raise self.exc
        return [dict(r) for r in self.results]


@pytest.fixture
def env(tmp_path, monkeypatch):
    for table in (TableBlacklist, TableLyricsVersions, TableHistory, TableTracks, TableAlbums,
                  TableArtists, TableProfiles, TableLibraryRoots):
        database.execute(delete(table))
    database.execute(insert(TableProfiles).values(
        id=1, name='Default', is_default=True, download_covers=True, download_lyrics=True,
        lyrics_selection_mode='best_score', lyrics_providers='["p1","p2","p3"]',
        cover_providers='["c1","c2"]', auto_translate='off', score_threshold=0,
        overwrite_existing=False, embed_cover_art=False, cover_format='jpg', prefer_synced_lyrics=True))
    music = tmp_path / 'music'
    artist_dir = music / 'Artist'
    album_dir = artist_dir / 'Album [FLAC]'
    album_dir.mkdir(parents=True)
    database.execute(insert(TableLibraryRoots).values(path=str(music), source='lidarr',
                                                      updated_at=datetime.now()))
    database.execute(insert(TableArtists).values(lidarrArtistId=1, name='Artist', path=str(artist_dir)))
    database.execute(insert(TableAlbums).values(
        lidarrAlbumId=10, artistId=1, title='Album', path=str(album_dir), cover_status='missing',
        profileId=1))
    audio = album_dir / '01 Song.flac'
    audio.write_bytes(b'audio')
    database.execute(insert(TableTracks).values(
        lidarrTrackId=100, albumId=10, artistId=1, title='Song', duration=200, path=str(audio),
        lyrics_status='missing', lyrics_retry_count=0, track_artist='Artist'))

    providers = {}
    monkeypatch.setattr(w, '_lyrics_providers', providers)
    covers = {}
    monkeypatch.setattr(w, '_cover_providers', covers)
    monkeypatch.setattr(w.rate_limiter, 'wait', lambda name: None)
    monkeypatch.setattr(lyrics_store, 'detect_language', lambda text: 'en')
    events = []
    monkeypatch.setattr(w, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    health_events = []
    monkeypatch.setattr(event_handler, 'event_stream',
                        lambda type, payload=None: health_events.append((type, payload)))
    health_tracker.reset()
    from lyrarr.utilities import health
    health._last_state.clear()
    health._library_cache.update(at=0.0, value=None)
    yield {'providers': providers, 'covers': covers, 'audio': audio, 'album_dir': album_dir,
           'artist_dir': artist_dir, 'music': music, 'events': events, 'health_events': health_events,
           'lrc': str(audio)[:-5] + '.lrc'}
    database.remove()


def _track(track_id=100):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


def _set_track(track_id=100, **values):
    database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values))


def _set_profile(**values):
    database.execute(update(TableProfiles).where(TableProfiles.id == 1).values(**values))


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _history(action=None):
    q = select(TableHistory)
    if action is not None:
        q = q.where(TableHistory.action == action)
    return database.execute(q).scalars().all()


def _cooldown(name):
    for _ in range(health_tracker.MAX_CONSECUTIVE_FAILURES):
        health_tracker.record_failure(name, 'down')


def _backoff(t):
    return (t.lyrics_retry_count, t.lyrics_retry_after, t.lyrics_transient_count,
            t.lyrics_cooldown_count, t.lyrics_retry_set_at)


def _close(dt, delta, slack=timedelta(minutes=2)):
    return dt is not None and abs(dt - (datetime.now() + delta)) < slack


class TestLyricsSelection:
    def test_wrong_artist_rejected_and_backed_off(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH, artist='Somebody Else')])
        assert w.download_missing_lyrics() == 0
        t = _track()
        assert t.lyrics_status == 'missing' and not os.path.exists(env['lrc'])
        assert t.lyrics_retry_count == 1 and t.lyrics_retry_after > datetime.now() + timedelta(hours=12)

    def test_floor_miss_after_transient_failure_retries_soon(self, env):
        env['providers']['p1'] = FakeProvider('p1', exc=ProviderTransientError('429'))
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=ENGLISH, artist='Nobody')])
        w.download_missing_lyrics()
        t = _track()
        assert t.lyrics_retry_count == 0  # transient: budget not consumed
        assert t.lyrics_retry_after < datetime.now() + timedelta(hours=1)
        assert t.lyrics_transient_count == 1

    def test_prefer_synced_floor_first(self, env):
        _set_profile(lyrics_selection_mode='prefer_synced')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=DIFFERENT_SYNCED, score=0.35)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER, score=0.8)])
        assert w.download_missing_lyrics() == 1
        assert 'chorus' in _read(env['lrc'])
        assert _track().lyrics_source == 'auto'

    def test_provider_attribution_and_provenance(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER, score=0.7)])
        assert w.download_missing_lyrics() == 1
        hist = _history()
        assert [h.provider for h in hist] == ['p1'] and hist[0].action == 1
        t = _track()
        assert t.lyrics_provider == 'p1' and t.lyrics_source_id == 'p1-Song'
        assert t.lyrics_match_score == pytest.approx(0.95)
        assert t.to_dict()['lyrics_match_json']['title_score'] == 100

    def test_blacklisted_result_skipped(self, env):
        lyrics_store.blacklist_content(100, SYNCED)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER, score=0.7)])
        assert w.download_missing_lyrics() == 1
        assert 'chorus' in _read(env['lrc'])

    def test_genius_intro_header_is_saved_not_instrumental(self, env):
        genius = "[Intro]\nla la placeholder\n\n[Verse 1]\n" + ENGLISH + "\n[Outro]\nhold the placeholder"
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=genius, duration=None)])
        assert w.download_missing_lyrics() == 1
        assert _track().lyrics_status == 'available'

    def test_one_provider_saying_instrumental_is_only_a_hint(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=MARKER)])
        w.download_missing_lyrics()
        t = _track()
        assert t.lyrics_status == 'missing' and t.lyrics_retry_count == 1
        assert t.lyrics_retry_after > datetime.now() + timedelta(days=13)
        assert [v['source'] for v in t.to_dict()['instrumental_evidence']] == ['p1']

    def test_two_providers_saying_instrumental_is_consensus(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=MARKER)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain='Instrumental')])
        w.download_missing_lyrics()
        t = _track()
        assert t.lyrics_status == 'instrumental' and t.instrumental_reason == 'consensus'
        assert {v['source'] for v in t.to_dict()['instrumental_evidence']} == {'p1', 'p2'}
        assert _history(action=6)

    def test_validation_error_backs_off(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain='Lyrics not available')])
        w.download_missing_lyrics()
        assert _track().lyrics_retry_count == 1

    def test_musixmatch_excerpt_never_saved(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH[:60], plain_truncated=True)])
        w.download_missing_lyrics()
        assert not os.path.exists(env['lrc'])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER, score=0.7)])
        database.execute(update(TableTracks).values(lyrics_retry_after=None))
        assert w.download_missing_lyrics() == 1
        assert 'chorus' in _read(env['lrc'])

    def test_below_new_floor_not_saved(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=OTHER, score=0.6)])
        assert w.download_missing_lyrics() == 0

    def test_same_artist_different_title_not_saved(self, env):
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=OTHER, title='Another Song',
                                                       title_score=40, gate='title_mismatch', score=0.3)])
        assert w.download_missing_lyrics() == 0
        assert not os.path.exists(env['lrc'])

    def test_persist_failure_backs_off(self, env, monkeypatch):
        monkeypatch.setattr(w, 'persist_lyrics_result',
                            lambda *a, **k: lyrics_store.PersistResult(False, 'write_failed', 'nope', None))
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        w.download_missing_lyrics()
        assert _track().lyrics_retry_count == 1

    def test_overrunning_synced_saves_plain(self, env):
        long_synced = SYNCED + "\n[03:40.00]a closing line sung far past the end"
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=long_synced, plain=ENGLISH)])
        assert w.download_missing_lyrics() == 1
        saved = _read(env['lrc'])
        assert '[00:' not in saved and 'first line of the verse' in saved
        assert _track().is_synced is False

    def test_queries_with_track_artist_and_album_artist_alt(self, env):
        _set_track(track_artist='Guest Singer')
        env['providers']['p1'] = p1 = FakeProvider('p1', [])
        w.download_missing_lyrics()
        kw = p1.kwargs[0]
        assert kw['artist_name'] == 'Guest Singer' and kw['alt_artist_names'] == ('Artist',)
        assert kw['strict'] is True

    def test_track_artist_not_filled_without_tag(self, env):
        _set_track(track_artist=None)
        env['providers']['p1'] = p1 = FakeProvider('p1', [])
        w.download_missing_lyrics()
        # No ARTIST tag in the fake file: stays NULL (= album artist), queried as the album artist.
        assert _track().track_artist is None
        assert p1.kwargs[0]['artist_name'] == 'Artist' and p1.kwargs[0]['alt_artist_names'] == ()

    def test_track_artist_filled_from_tag(self, env, monkeypatch):
        _set_track(track_artist=None)
        monkeypatch.setattr(w, 'read_embedded_artist', lambda path: 'Guest Singer')
        env['providers']['p1'] = p1 = FakeProvider('p1', [])
        w.download_missing_lyrics()
        assert _track().track_artist == 'Guest Singer'
        assert p1.kwargs[0]['artist_name'] == 'Guest Singer'

    def test_track_artist_filled_from_mb_credit_without_tag(self, env):
        _set_track(track_artist=None)
        r = _result('p1', plain=ENGLISH)
        r.setdefault('match_details', {})['mb_artist'] = 'Featured Artist'
        env['providers']['p1'] = FakeProvider('p1', [r])
        assert w.download_missing_lyrics() == 1
        assert _track().track_artist == 'Featured Artist'


class TestBackoff:
    """PLAN §3.6, one test per table row (+ mixed sequences)."""

    def _defer(self, kind, **kw):
        return w._defer_track(_track(), kind, **kw)

    def test_transient_keeps_count_and_retries_soon(self, env):
        self._defer('transient')
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_transient_count, t.lyrics_cooldown_count) == (0, 1, 0)
        assert _close(t.lyrics_retry_after, timedelta(minutes=15)) and t.lyrics_retry_set_at

    def test_sixth_transient_advances_backoff(self, env):
        for _ in range(5):
            self._defer('transient')
        assert _track().lyrics_transient_count == 5
        self._defer('transient')
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_transient_count) == (1, 0)
        assert _close(t.lyrics_retry_after, timedelta(days=1))

    def test_cooldown_keeps_count_six_hours(self, env):
        _set_track(lyrics_transient_count=2)
        self._defer('cooldown')
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_transient_count, t.lyrics_cooldown_count) == (0, 0, 1)
        assert _close(t.lyrics_retry_after, timedelta(hours=6))

    def test_third_cooldown_advances(self, env):
        for _ in range(3):
            self._defer('cooldown')
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_cooldown_count) == (1, 0)

    def test_normal_miss_resets_counters(self, env):
        _set_track(lyrics_transient_count=3, lyrics_cooldown_count=2, lyrics_retry_count=1)
        self._defer('normal')
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_transient_count, t.lyrics_cooldown_count) == (2, 0, 0)
        assert _close(t.lyrics_retry_after, timedelta(days=3))

    def test_hint_backoff_at_least_14_days(self, env):
        self._defer('hint')
        t = _track()
        assert t.lyrics_retry_count == 1 and _close(t.lyrics_retry_after, timedelta(days=14))

    def test_no_audio_counts_nothing(self, env):
        _set_track(lyrics_retry_count=2, lyrics_transient_count=1, lyrics_cooldown_count=1)
        self._defer('no_audio')
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_transient_count, t.lyrics_cooldown_count) == (2, 1, 1)
        assert _close(t.lyrics_retry_after, timedelta(hours=6)) and t.lyrics_retry_set_at

    def test_mixed_sequences_dont_advance_each_other(self, env):
        # transient → cooldown → transient: the cooldown resets the transient
        # streak, a transient leaves the cooldown streak alone, nothing advances.
        for kind in ('transient', 'transient', 'cooldown', 'transient', 'cooldown', 'transient'):
            self._defer(kind)
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_transient_count, t.lyrics_cooldown_count) == (0, 1, 2)

    def test_every_write_stamps_set_at(self, env):
        for kind in ('transient', 'cooldown', 'normal', 'hint', 'no_audio'):
            _set_track(lyrics_retry_set_at=None, lyrics_retry_after=None)
            self._defer(kind)
            assert _track().lyrics_retry_set_at is not None, kind

    def test_legacy_bool_signature(self, env):
        w._defer_track(_track(), True)
        assert _track().lyrics_transient_count == 1
        w._defer_track(_track(), False)
        assert _track().lyrics_retry_count == 1

    def test_N_explicit_run_inside_window_changes_nothing(self, env):
        _set_track(lyrics_retry_count=2, lyrics_retry_after=datetime.now() + timedelta(days=3),
                   lyrics_retry_set_at=datetime(2026, 1, 1))
        before = _backoff(_track())
        env['providers']['p1'] = FakeProvider('p1', [])
        w.download_missing_lyrics(ignore_backoff=True)
        assert env['providers']['p1'].calls == 1
        assert _backoff(_track()) == before
        w.download_missing_lyrics(track_ids=[100])
        assert _backoff(_track()) == before

    def test_G_cooldown_deferral_via_run(self, env):
        env['providers']['p1'] = FakeProvider('p1', [])
        env['providers']['p2'] = FakeProvider('p2', [])
        _cooldown('p2')
        w.download_missing_lyrics()
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_cooldown_count) == (0, 1)
        assert _close(t.lyrics_retry_after, timedelta(hours=6))
        for _ in range(2):
            _set_track(lyrics_retry_after=None)
            w.download_missing_lyrics()
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_cooldown_count) == (1, 0)

    def test_all_providers_cooling_down_is_transient(self, env):
        env['providers']['p1'] = FakeProvider('p1', [])
        _cooldown('p1')
        _set_profile(lyrics_providers='["p1"]')
        w.download_missing_lyrics()
        t = _track()
        assert env['providers']['p1'].calls == 0
        assert (t.lyrics_retry_count, t.lyrics_transient_count) == (0, 1)


class TestFreshRow:
    def test_M_skip_mid_run_is_preserved(self, env):
        def skip():
            _set_track(lyrics_status='blacklisted')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)], on_search=skip)
        assert w.download_missing_lyrics() == 0
        t = _track()
        assert t.lyrics_status == 'blacklisted' and not os.path.exists(env['lrc'])

    def test_manual_save_mid_run_is_preserved(self, env):
        from lyrarr.metadata.manager import save_lyrics

        def manual():
            assert save_lyrics(100, {'plain_lyrics': OTHER}, 'editor', source='manual')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)], on_search=manual)
        assert w.download_missing_lyrics() == 0
        t = _track()
        assert t.lyrics_source == 'manual' and _read(env['lrc']).strip() == OTHER

    def test_snapshot_changed_before_processing_is_skipped(self, env):
        snapshot = _track()
        _set_track(lyrics_status='available')
        res = w.run_lyrics_pipeline(snapshot, eff=w._effective_settings(
            database.execute(select(TableAlbums)).scalars().first(), w._get_profile(1)),
            artist_name='Artist', album_title='Album', write=True)
        assert res['outcome'] == 'skipped'

    def test_H_overwrite_with_manual_source_keeps_file(self, env):
        _set_profile(overwrite_existing=True)
        _write(env['lrc'], OTHER)
        _set_track(lyrics_source='manual')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        w.download_missing_lyrics()
        assert _read(env['lrc']) == OTHER
        t = _track()
        assert t.lyrics_status == 'available' and t.lyrics_source == 'manual'
        assert env['providers']['p1'].calls == 0

    def test_marker_sidecar_is_replaced_with_overwrite_off(self, env):
        _write(env['lrc'], '[au: instrumental]\n')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        assert w.download_missing_lyrics() == 1
        assert 'first line' in _read(env['lrc'])


class TestAudioAndRoots:
    def test_missing_audio_defers_without_counting(self, env):
        # Other tracks of the root are there, so only this track's audio is missing.
        for i in range(1, 6):
            other = env['album_dir'] / f'0{i + 1} Other.flac'
            other.write_bytes(b'audio')
            database.execute(insert(TableTracks).values(
                lidarrTrackId=100 + i, albumId=10, artistId=1, title=f'Other {i}', duration=200,
                path=str(other), lyrics_status='available'))
        os.remove(env['audio'])
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        _set_track(lyrics_retry_count=1)
        w.download_missing_lyrics()
        t = _track()
        assert env['providers']['p1'].calls == 0 and not os.path.exists(env['lrc'])
        assert t.lyrics_retry_count == 1 and _close(t.lyrics_retry_after, timedelta(hours=6))

    def test_two_roots_one_unmounted(self, env, tmp_path):
        m2 = tmp_path / 'music2'
        a2 = m2 / 'Other' / 'Other Album'
        a2.mkdir(parents=True)
        audio2 = a2 / '01 Other.flac'
        audio2.write_bytes(b'audio')
        database.execute(insert(TableLibraryRoots).values(path=str(m2), source='lidarr', updated_at=datetime.now()))
        database.execute(insert(TableArtists).values(lidarrArtistId=2, name='Artist', path=str(m2 / 'Other')))
        database.execute(insert(TableAlbums).values(
            lidarrAlbumId=20, artistId=2, title='Album', path=str(a2), cover_status='missing', profileId=1))
        database.execute(insert(TableTracks).values(
            lidarrTrackId=200, albumId=20, artistId=2, title='Song', duration=200, path=str(audio2),
            lyrics_status='missing', lyrics_retry_count=2, lyrics_transient_count=1,
            lyrics_retry_after=datetime.now() - timedelta(minutes=1), track_artist='Artist'))
        os.rename(m2, tmp_path / 'music2-away')
        before = _backoff(_track(200))
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])

        assert w.download_missing_lyrics() == 1
        assert _track(100).lyrics_status == 'available' and os.path.exists(env['lrc'])
        assert _backoff(_track(200)) == before
        assert _track(200).lyrics_status == 'missing'
        assert not m2.exists()  # nothing created under the unmounted root
        alerts = [p for t, p in env['health_events'] if t == 'health' and not p.get('healthy')]
        assert len(alerts) == 1 and str(m2) in alerts[0]['service']
        # A second run doesn't alert again.
        w.download_missing_lyrics()
        assert len([p for t, p in env['health_events'] if t == 'health' and not p.get('healthy')]) == 1


class TestEvents:
    def test_progress_events_with_flag_on(self, env, monkeypatch):
        monkeypatch.setattr(event_handler, 'FRONTEND_EVENTS_V2', True)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        w.download_missing_lyrics()
        types = [t for t, _ in env['events']]
        assert types == ['download_scope', 'download_progress', 'download_item']
        assert env['events'][0][1] == {'metadata_type': 'lyrics', 'total': 1}
        assert env['events'][2][1]['outcome'] == 'ok'

    def test_no_new_events_with_flag_off(self, env, monkeypatch):
        monkeypatch.setattr(event_handler, 'FRONTEND_EVENTS_V2', False)
        env['providers']['p1'] = FakeProvider('p1', [])
        w.download_missing_lyrics()
        assert [t for t, _ in env['events']] == []  # nothing saved: no download_progress either


class TestVerifyMode:
    def _eff(self):
        return w._effective_settings(database.execute(select(TableAlbums)).scalars().first(), w._get_profile(1))

    def _snapshot(self, env):
        return (_backoff(_track()), _track().lyrics_status, len(_history()),
                _read(env['lrc']) if os.path.exists(env['lrc']) else None)

    @pytest.mark.parametrize('status', ['available', 'instrumental'])
    def test_returns_raw_candidates_and_writes_nothing(self, env, status):
        if status == 'available':
            _write(env['lrc'], ENGLISH)
        _set_track(lyrics_status=status, lyrics_source='auto' if status == 'available' else None)
        env['providers']['p1'] = FakeProvider('p1', [
            _result('p1', plain=ENGLISH),
            _result('p1', plain=OTHER, artist='Somebody Else'),
            marker('p1'),
        ])
        before = self._snapshot(env)
        res = w.run_lyrics_pipeline(_track(), eff=self._eff(), artist_name='Artist', album_title='Album',
                                    mode='verify')
        assert res['outcome'] == 'ok' and res['chosen']['acceptable']
        reasons = sorted(str(c['reject_reason']) for c in res['candidates'])
        assert reasons == ['None', 'gate:artist_mismatch', 'marker']
        gated = next(c for c in res['candidates'] if c['gate'])
        assert gated['plain'] == OTHER
        if status == 'available':
            good = next(c for c in res['candidates'] if c['acceptable'])
            assert good['equivalence_to_file'] == pytest.approx(1.0)
        assert self._snapshot(env) == before

    def test_none_and_transient(self, env):
        env['providers']['p1'] = FakeProvider('p1', [])
        res = w.run_lyrics_pipeline(_track(), eff=self._eff(), artist_name='Artist', album_title='Album',
                                    mode='verify')
        assert res['outcome'] == 'none'
        env['providers']['p1'] = FakeProvider('p1', exc=ProviderTransientError('503'))
        res = w.run_lyrics_pipeline(_track(), eff=self._eff(), artist_name='Artist', album_title='Album',
                                    mode='verify')
        assert res['outcome'] == 'transient'

    def test_verify_refuses_write(self, env):
        with pytest.raises(ValueError):
            w.run_lyrics_pipeline(_track(), eff=self._eff(), artist_name='A', album_title='B',
                                  mode='verify', write=True)

    def test_download_dry_run_has_no_side_effects(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        before = self._snapshot(env)
        res = w.run_lyrics_pipeline(_track(), eff=self._eff(), artist_name='Artist', album_title='Album',
                                    mode='download', write=False)
        assert res['outcome'] == 'ok' and self._snapshot(env) == before


class TestTranslation:
    def test_translation_goes_through_persist(self, env, monkeypatch):
        _set_profile(auto_translate='replace', translate_target_lang='de', translate_only_foreign=True)
        from lyrarr.metadata import manager
        monkeypatch.setattr(manager, 'translate_lyrics_content', lambda c, t, m: 'ÜBERSETZT\n' + c.upper())
        # Five older versions already archived: the translation must respect the cap.
        for i in range(5):
            database.execute(insert(TableLyricsVersions).values(
                lidarrTrackId=100, content=f'old {i}', lyrics_type='plain',
                timestamp=datetime.now() - timedelta(days=10 - i)))
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        assert w.download_missing_lyrics() == 1

        t = _track()
        assert t.lyrics_source == 'translated' and t.detected_language == 'de'
        assert _read(env['lrc']).startswith('ÜBERSETZT')
        versions = database.execute(
            select(TableLyricsVersions).where(TableLyricsVersions.lidarrTrackId == 100)).scalars().all()
        assert len(versions) == lyrics_store._MAX_VERSIONS_PER_TRACK
        source = [v for v in versions if v.translated_from]
        assert len(source) == 1 and source[0].content == ENGLISH and source[0].translated_from == 'en'

        # Rejecting the translated file must also reject the provider source.
        hashes = lyrics_store.hashes_for_blacklist(100, _read(env['lrc']))
        assert lyrics_store.content_hash(ENGLISH) in hashes
        assert lyrics_store.content_hash(_read(env['lrc'])) in hashes
        # ...but a different (search-result) text is only itself.
        assert lyrics_store.hashes_for_blacklist(100, OTHER) == [lyrics_store.content_hash(OTHER)]

    def test_noop_translation_not_saved(self, env, monkeypatch):
        _set_profile(auto_translate='replace', translate_target_lang='de')
        from lyrarr.metadata import manager
        monkeypatch.setattr(manager, 'translate_lyrics_content', lambda c, t, m: c)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        assert w.download_missing_lyrics() == 1
        assert _track().lyrics_source == 'auto'

    def test_failing_translator_returns_none(self, monkeypatch):
        from lyrarr.metadata import manager

        class Broken:
            def translate(self, text):
                raise ConnectionError('offline')
        monkeypatch.setattr(manager, 'get_translator', lambda lang: Broken())
        assert manager.translate_lyrics_content(ENGLISH, 'de') is None

    def test_partial_translation_returns_none(self, monkeypatch):
        from lyrarr.metadata import manager
        calls = []

        class Flaky:
            def translate(self, text):
                calls.append(text)
                if len(calls) == 2:
                    raise ConnectionError('blip')
                return text.upper()
        monkeypatch.setattr(manager, 'get_translator', lambda lang: Flaky())
        assert manager.translate_lyrics_content(ENGLISH, 'de') is None

    def test_identical_translation_returns_none(self, monkeypatch):
        from lyrarr.metadata import manager

        class Echo:
            def translate(self, text):
                return text
        monkeypatch.setattr(manager, 'get_translator', lambda lang: Echo())
        assert manager.translate_lyrics_content(ENGLISH, 'de') is None


class TestUpgrade:
    def _available_plain(self, env, source, text=ENGLISH, with_hash=True):
        _write(env['lrc'], text)
        database.execute(update(TableTracks).values(
            lyrics_status='available', is_synced=False, lyrics_source=source,
            lyrics_content_hash=lyrics_store.content_hash(text) if with_hash and source == 'auto' else None))

    @pytest.mark.parametrize('source', ['manual', 'translated', 'generated'])
    def test_user_lyrics_never_upgraded(self, env, source):
        self._available_plain(env, source)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        assert env['providers']['p1'].calls == 0
        assert _read(env['lrc']) == ENGLISH

    def test_auto_lyrics_upgraded_with_history_action_2(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 1
        t = _track()
        assert t.is_synced is True and t.lyrics_source == 'auto'
        assert [h.action for h in _history()] == [2]
        assert (t.lyrics_retry_count, t.lyrics_transient_count, t.lyrics_cooldown_count) == (0, 0, 0)

    def test_unknown_origin_needs_overwrite(self, env):
        self._available_plain(env, None)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        _set_profile(overwrite_existing=True)
        assert w.upgrade_unsynced_lyrics() == 1

    def test_upgrade_failure_uses_its_own_backoff(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced='[00:01.00]Lyrics not available')])
        assert w.upgrade_unsynced_lyrics() == 0
        t = _track()
        assert t.upgrade_retry_count == 1 and t.upgrade_retry_set_at is not None
        assert _backoff(t)[:4] == (0, None, 0, 0)

    def test_upgrade_cooldown_and_transient(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [])
        env['providers']['p2'] = FakeProvider('p2', [])
        _cooldown('p2')
        w.upgrade_unsynced_lyrics()
        t = _track()
        assert t.upgrade_retry_count == 0 and _close(t.upgrade_retry_after, timedelta(hours=6))
        _set_track(upgrade_retry_after=None)
        _set_profile(lyrics_providers='["p2"]')
        w.upgrade_unsynced_lyrics()  # attempted == 0: transient
        t = _track()
        assert t.upgrade_retry_count == 0 and _close(t.upgrade_retry_after, timedelta(minutes=15))

    def test_upgrade_cooldown_advances_on_third(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [])
        env['providers']['p2'] = FakeProvider('p2', [])
        _cooldown('p2')

        def due():  # move the deferral into the past, keeping its length
            t = _track()
            length = t.upgrade_retry_after - t.upgrade_retry_set_at
            after = datetime.now() - timedelta(minutes=1)
            _set_track(upgrade_retry_after=after, upgrade_retry_set_at=after - length)

        for _ in range(2):
            w.upgrade_unsynced_lyrics()
            t = _track()
            assert t.upgrade_retry_count == 0 and _close(t.upgrade_retry_after, timedelta(hours=6))
            due()
        w.upgrade_unsynced_lyrics()
        t = _track()
        assert t.upgrade_retry_count == 1 and _close(t.upgrade_retry_after, timedelta(days=1))
        # The streak starts over after the advance.
        due()
        w.upgrade_unsynced_lyrics()
        t = _track()
        assert t.upgrade_retry_count == 1 and _close(t.upgrade_retry_after, timedelta(hours=6))

    def test_upgrade_transient_advances_on_sixth(self, env):
        self._available_plain(env, 'auto')
        for i in range(6):
            w._defer_upgrade(_track(), 'transient')
            t = _track()
            if i < 5:
                assert t.upgrade_retry_count == 0 and _close(t.upgrade_retry_after, timedelta(minutes=15))
        assert t.upgrade_retry_count == 1 and _close(t.upgrade_retry_after, timedelta(days=1))
        assert _backoff(t)[:4] == (0, None, 0, 0)

    def test_upgrade_skips_overrunning_synced_for_a_fitting_one(self, env):
        self._available_plain(env, 'auto')
        long_synced = SYNCED + "\n[03:40.00]a closing line sung far past the end"
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=long_synced, score=0.99)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', synced=SYNCED, score=0.8)])
        assert w.upgrade_unsynced_lyrics() == 1
        assert _track().is_synced is True and '[03:40' not in _read(env['lrc'])

    def test_hash_mismatch_skips_and_downgrades_source(self, env):
        self._available_plain(env, 'auto')
        _write(env['lrc'], OTHER)  # someone edited the file
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        assert _track().lyrics_source is None and _read(env['lrc']) == OTHER
        assert env['providers']['p1'].calls == 0

    def test_null_hash_backfilled_then_upgraded(self, env):
        self._available_plain(env, 'auto', with_hash=False)
        database.execute(insert(TableHistory).values(
            action=1, description='Downloaded lyrics for Song', metadata_type='lyrics', provider='p1',
            lidarrTrackId=100, lidarrAlbumId=10, lidarrArtistId=1, timestamp=datetime.now(),
            metadata_path=env['lrc']))
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 1
        assert _track().is_synced is True

    def test_null_hash_without_history_is_downgraded_and_skipped(self, env):
        self._available_plain(env, 'auto', with_hash=False)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        assert _track().lyrics_source is None and _read(env['lrc']) == ENGLISH

    @pytest.mark.parametrize('bypass, expected', [(None, 1), (False, 0), (True, 1)])
    def test_bypass_source_guard_with_track_ids(self, env, bypass, expected):
        self._available_plain(env, None)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics(track_ids=[100], bypass_source_guard=bypass) == expected

    def test_prefer_synced_off_skips(self, env):
        self._available_plain(env, 'auto')
        _set_profile(prefer_synced_lyrics=False)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0 and env['providers']['p1'].calls == 0

    def test_different_song_never_replaces_plain(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=DIFFERENT_SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        assert _read(env['lrc']) == ENGLISH

    def test_overrunning_synced_is_not_an_upgrade(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED + "\n[04:00.00]too late")])
        assert w.upgrade_unsynced_lyrics() == 0

    def test_already_synced_on_disk_fixes_flag(self, env):
        self._available_plain(env, 'auto', text=SYNCED)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        assert _track().is_synced is True and env['providers']['p1'].calls == 0

    def test_strong_title_skipped_unless_never(self, env):
        self._available_plain(env, 'auto')
        _set_track(title='Song (Instrumental)')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0 and env['providers']['p1'].calls == 0
        _set_track(instrumental_override='never')
        assert w.upgrade_unsynced_lyrics() == 1
        assert env['providers']['p1'].kwargs[0]['track_name'] == 'Song'


class TestManagerSaves:
    def test_save_lyrics_returns_no_audio(self, env):
        from lyrarr.metadata.manager import save_lyrics
        os.remove(env['audio'])
        r = save_lyrics(100, {'plain_lyrics': ENGLISH}, 'editor')
        assert not r and r.reason == 'no_audio' and str(env['audio']) in r.message

    def test_save_lyrics_manual_history_action_4(self, env):
        from lyrarr.metadata.manager import save_lyrics
        r = save_lyrics(100, {'plain_lyrics': ENGLISH}, 'editor')
        assert r and r.ok
        assert [h.action for h in _history()] == [4]

    def test_save_cover_art_missing_dir(self, env, tmp_path):
        from lyrarr.metadata.manager import save_cover_art
        gone = tmp_path / 'gone-album'
        database.execute(update(TableAlbums).values(path=str(gone)))
        assert save_cover_art(10, PNG, 'musicbrainz') == (False, 'album_dir_missing')
        assert not gone.exists()

    def test_save_cover_art_artist_root(self, env):
        from lyrarr.metadata.manager import save_cover_art
        database.execute(update(TableAlbums).values(path=str(env['artist_dir'])))
        assert save_cover_art(10, PNG, 'musicbrainz') == (False, 'artist_root')

    def test_save_cover_art_ok(self, env):
        from lyrarr.metadata.manager import save_cover_art
        assert save_cover_art(10, PNG, 'musicbrainz') == (True, None)
        assert any(p.name.endswith('.png') for p in env['album_dir'].iterdir())


class FakeCoverProvider:
    def __init__(self, name, results, data):
        self.name = name
        self.results = results
        self.data = data
        self.downloads = []

    def search(self, **kwargs):
        return [dict(r) for r in self.results]

    def download(self, url):
        self.downloads.append(url)
        return self.data


PNG = b'\x89PNG\r\n\x1a\n' + b'x' * 32
JPEG = b'\xff\xd8\xff\xe0' + b'x' * 32


class TestCovers:
    def test_wrong_album_skipped_and_real_type_saved(self, env):
        env['covers']['c1'] = FakeCoverProvider('c1', [
            {'url': 'u-wrong', 'type': 'front', 'title': 'Greatest Hits', 'artist': 'Artist'},
            {'url': 'u-cd', 'type': 'cdart', 'title': 'Album', 'artist': 'Artist'},
            {'url': 'u-right', 'type': 'front', 'title': 'Album (Deluxe Edition)', 'artist': 'Artist'},
        ], PNG)
        assert w.download_missing_covers() == 1
        assert env['covers']['c1'].downloads == ['u-right']
        assert (env['album_dir'] / 'cover.png').read_bytes() == PNG
        assert not (env['album_dir'] / 'cover.jpg').exists()

    def test_no_cover_backs_off(self, env):
        provider = FakeCoverProvider('c1', [], JPEG)
        provider.search = lambda **kw: (provider.downloads.append('searched'), [])[1]
        env['covers']['c1'] = provider
        w.download_missing_covers()
        w.download_missing_covers()
        assert provider.downloads == ['searched']  # second run skipped by backoff
        w.download_missing_covers(ignore_backoff=True)
        assert provider.downloads == ['searched', 'searched']

    def test_missing_album_dir_never_created(self, env, tmp_path):
        gone = env['artist_dir'] / 'Not Mounted'
        database.execute(update(TableAlbums).values(path=str(gone)))
        env['covers']['c1'] = FakeCoverProvider('c1', [{'url': 'u', 'type': 'front', 'title': 'Album',
                                                        'artist': 'Artist'}], PNG)
        assert w.download_missing_covers() == 0
        assert not gone.exists() and env['covers']['c1'].downloads == []
        assert database.execute(select(TableAlbums)).scalars().first().cover_retry_count in (0, None)

    def test_album_pointing_at_artist_folder_skipped(self, env):
        database.execute(update(TableAlbums).values(path=str(env['artist_dir'])))
        env['covers']['c1'] = FakeCoverProvider('c1', [{'url': 'u', 'type': 'front', 'title': 'Album',
                                                        'artist': 'Artist'}], PNG)
        assert w.download_missing_covers() == 0
        assert not list(env['artist_dir'].glob('cover.*'))

    def test_existing_folder_jpg_marks_available(self, env):
        (env['album_dir'] / 'folder.jpg').write_bytes(JPEG)
        env['covers']['c1'] = FakeCoverProvider('c1', [], JPEG)
        w.download_missing_covers()
        assert database.execute(select(TableAlbums)).scalars().first().cover_status == 'available'


def test_skipped_run_still_emits_complete(env):
    assert w._downloads_lock.acquire(blocking=False)
    try:
        w.run_metadata_downloads()
    finally:
        w._downloads_lock.release()
    types = [t for t, _ in env['events']]
    assert types[0] == 'download_start' and types[-1] == 'download_complete'
    assert env['events'][-1][1].get('skipped') is True


def test_exclusive_downloads():
    with w.exclusive_downloads() as got:
        assert got and w.downloads_in_progress()
        with w.exclusive_downloads(timeout_s=0.05) as again:
            assert not again
    assert not w.downloads_in_progress()
