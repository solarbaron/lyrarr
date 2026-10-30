"""End-to-end tests for the scheduled download worker against a throwaway SQLite DB.

Providers are fakes; nothing touches the network or the repo's data/ dir.
Covers: score floor + artist gate, prefer_synced after the floor, provider
attribution through merge, blacklist (incl. a translated file's source),
retry/backoff on every give-up path (transient vs not-found), single-provider
instrumental content, Musixmatch excerpt, translation through the persist
path (version cap, source, language), the synced-upgrade pass leaving user
lyrics alone, reconcile with the music mount down, cover verification /
real image type / backoff, and download_complete on a skipped run.
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

from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableBlacklist,
    TableHistory,
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
    "I walked along the empty street tonight",
    "The city lights were burning low and bright",
    "And every step I took felt sweet to me",
    "As memories began to flow so free",
    "I thought about the years gone by",
    "And all the things I left behind",
])
SYNCED = "\n".join(f"[00:{10 + 5 * i:02d}.00]{line}" for i, line in enumerate(ENGLISH.splitlines()))
OTHER = ENGLISH.replace("street", "road").replace("city", "harbour")
DIFFERENT_SYNCED = "\n".join(f"[00:{10 + 5 * i:02d}.00]{line}" for i, line in enumerate([
    "Nothing here resembles the other song",
    "A totally different set of words appears",
    "Some random verse about the ocean waves",
    "And a chorus nobody would confuse with it",
]))


def _result(provider, plain=None, synced=None, artist='Artist', duration=200, score=None, **extra):
    from lyrarr.metadata.base import LyricsProvider
    md = LyricsProvider.score_result('Song', 'Artist', 200, 'Song', artist, duration)
    r = {'plain_lyrics': plain, 'synced_lyrics': synced, 'provider': provider,
         'score': md['score'] if score is None else score, 'match_details': md}
    r.update(extra)
    return r


class FakeProvider:
    def __init__(self, name, results=None, exc=None):
        self.name = name
        self.results = results or []
        self.exc = exc
        self.calls = 0

    def search(self, **kwargs):
        self.calls += 1
        if self.exc:
            raise self.exc
        return [dict(r) for r in self.results]


@pytest.fixture
def env(tmp_path, monkeypatch):
    for table in (TableBlacklist, TableLyricsVersions, TableHistory, TableTracks, TableAlbums,
                  TableArtists, TableProfiles):
        database.execute(delete(table))
    database.execute(insert(TableProfiles).values(
        id=1, name='Default', is_default=True, download_covers=True, download_lyrics=True,
        lyrics_selection_mode='best_score', lyrics_providers='["p1","p2","p3"]',
        cover_providers='["c1","c2"]', auto_translate='off', score_threshold=0,
        overwrite_existing=False, embed_cover_art=False, cover_format='jpg'))
    database.execute(insert(TableArtists).values(lidarrArtistId=1, name='Artist', path=str(tmp_path)))
    album_dir = tmp_path / 'Album [FLAC]'
    album_dir.mkdir()
    database.execute(insert(TableAlbums).values(
        lidarrAlbumId=10, artistId=1, title='Album', path=str(album_dir), cover_status='missing',
        profileId=1))
    audio = album_dir / '01 Song.flac'
    audio.write_bytes(b'audio')
    database.execute(insert(TableTracks).values(
        lidarrTrackId=100, albumId=10, artistId=1, title='Song', duration=200, path=str(audio),
        lyrics_status='missing', lyrics_retry_count=0))

    providers = {}
    monkeypatch.setattr(w, '_lyrics_providers', providers)
    covers = {}
    monkeypatch.setattr(w, '_cover_providers', covers)
    monkeypatch.setattr(w.rate_limiter, 'wait', lambda name: None)
    events = []
    monkeypatch.setattr(w, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    health_tracker.reset()
    yield {'providers': providers, 'covers': covers, 'audio': audio, 'album_dir': album_dir,
           'events': events, 'lrc': str(audio)[:-5] + '.lrc'}
    database.remove()


def _track():
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == 100)).scalars().first()


def _set_profile(**values):
    database.execute(update(TableProfiles).where(TableProfiles.id == 1).values(**values))


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


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

    def test_prefer_synced_floor_first(self, env):
        _set_profile(lyrics_selection_mode='prefer_synced')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=DIFFERENT_SYNCED, score=0.35)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER)])
        assert w.download_missing_lyrics() == 1
        assert 'harbour' in _read(env['lrc'])
        assert _track().lyrics_source == 'auto'

    def test_provider_attribution_with_several_candidates(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER, score=0.6)])
        assert w.download_missing_lyrics() == 1
        hist = database.execute(select(TableHistory)).scalars().all()
        assert [h.provider for h in hist] == ['p1']

    def test_blacklisted_result_skipped(self, env):
        lyrics_store.blacklist_content(100, SYNCED)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER, score=0.7)])
        assert w.download_missing_lyrics() == 1
        assert 'harbour' in _read(env['lrc'])

    def test_genius_intro_header_is_saved_not_instrumental(self, env):
        genius = "[Intro]\nYeah, yeah\n\n[Verse 1]\n" + ENGLISH + "\n[Outro]\nHold on"
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=genius)])
        assert w.download_missing_lyrics() == 1
        assert _track().lyrics_status == 'available'

    def test_one_provider_saying_instrumental_is_not_terminal(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain='[Instrumental]')])
        w.download_missing_lyrics()
        t = _track()
        assert t.lyrics_status == 'missing' and t.lyrics_retry_count == 1

    def test_two_providers_saying_instrumental_is_terminal(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain='[Instrumental]')])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain='Instrumental')])
        w.download_missing_lyrics()
        assert _track().lyrics_status == 'instrumental'

    def test_validation_error_backs_off(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain='Lyrics not available')])
        w.download_missing_lyrics()
        assert _track().lyrics_retry_count == 1

    def test_musixmatch_excerpt_never_saved(self, env):
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH[:60], plain_truncated=True)])
        w.download_missing_lyrics()
        assert not os.path.exists(env['lrc'])
        env['providers']['p2'] = FakeProvider('p2', [_result('p2', plain=OTHER, score=0.6)])
        database.execute(update(TableTracks).values(lyrics_retry_after=None))
        assert w.download_missing_lyrics() == 1
        assert 'harbour' in _read(env['lrc'])

    def test_persist_failure_backs_off(self, env, monkeypatch):
        monkeypatch.setattr(w, 'persist_lyrics', lambda *a, **k: None)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', plain=ENGLISH)])
        w.download_missing_lyrics()
        assert _track().lyrics_retry_count == 1


class TestTranslation:
    def test_translation_goes_through_persist(self, env, monkeypatch):
        _set_profile(auto_translate='replace', translate_target_lang='de', translate_only_foreign=True)
        from lyrarr.metadata import manager
        monkeypatch.setattr(manager, 'translate_lyrics_content', lambda c, t, m: 'ÜBERSETZT\n' + c.upper())
        monkeypatch.setattr(lyrics_store, 'detect_language', lambda text: 'en')  # langdetect is optional
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


class TestUpgrade:
    def _available_plain(self, env, source):
        with open(env['lrc'], 'w', encoding='utf-8') as f:
            f.write(ENGLISH)
        database.execute(update(TableTracks).values(
            lyrics_status='available', is_synced=False, lyrics_source=source))

    @pytest.mark.parametrize('source', ['manual', 'translated', 'generated'])
    def test_user_lyrics_never_upgraded(self, env, source):
        self._available_plain(env, source)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        assert env['providers']['p1'].calls == 0
        assert _read(env['lrc']) == ENGLISH

    def test_auto_lyrics_upgraded(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 1
        assert _track().is_synced is True and _track().lyrics_source == 'auto'

    def test_unknown_origin_needs_overwrite(self, env):
        self._available_plain(env, None)
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        _set_profile(overwrite_existing=True)
        assert w.upgrade_unsynced_lyrics() == 1

    def test_upgrade_validation_failure_backs_off(self, env):
        self._available_plain(env, 'auto')
        env['providers']['p1'] = FakeProvider('p1', [_result('p1', synced='[00:01.00]Lyrics not available')])
        assert w.upgrade_unsynced_lyrics() == 0
        assert _track().lyrics_retry_count == 1


class TestReconcile:
    def test_mount_down_keeps_available(self, env):
        database.execute(update(TableTracks).values(lyrics_status='available'))
        os.remove(env['audio'])  # audio gone too: mount down / file moved
        assert lyrics_store.reconcile_track_lyrics(_track()) is False
        assert _track().lyrics_status == 'available'

    def test_lrc_deleted_with_audio_present(self, env):
        database.execute(update(TableTracks).values(lyrics_status='available'))
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        assert _track().lyrics_status == 'missing'


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


def test_skipped_run_still_emits_complete(env):
    assert w._downloads_lock.acquire(blocking=False)
    try:
        w.run_metadata_downloads()
    finally:
        w._downloads_lock.release()
    types = [t for t, _ in env['events']]
    assert types[0] == 'download_start' and types[-1] == 'download_complete'
    assert env['events'][-1][1].get('skipped') is True
