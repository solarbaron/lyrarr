"""Regression tests for lyrarr/api/metadata.py.

Covers: LRCLIB publish duration units, cover-download input validation,
Whisper model caching / serialization / timestamp formatting, batch-job
selection semantics + single-run guard + always-emitted completion events,
lyrics_source on every save, blacklist of derived (translated) files, and
400s instead of 500s on malformed input.

Runs the metadata namespace on a bare Flask app against a throwaway SQLite
database in a temp config dir (never the repo's data/).
"""

import os
import sys
import tempfile
import types

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _cfg_dir = tempfile.mkdtemp(prefix='lyrarr-test-api-metadata-')
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', _cfg_dir]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from lyrarr.app.get_args import args as _args  # noqa: E402

_REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
if os.path.abspath(_args.config_dir) == os.path.abspath(_REPO_DATA):
    # These tests wipe tables — never against the repo's data/ snapshot.
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from flask import Flask  # noqa: E402
from flask_restx import Api  # noqa: E402
from sqlalchemy import insert  # noqa: E402

import lyrarr.api.metadata as m  # noqa: E402
from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableBlacklist,
    TableTracks,
    database,
    delete,
    init_db,
    select,
)
from lyrarr.metadata import download_worker, lyrics_store  # noqa: E402

init_db()


@pytest.fixture
def client():
    app = Flask(__name__)
    api = Api(app)
    api.add_namespace(m.api_ns_metadata, '/')
    return app.test_client()


@pytest.fixture
def library(tmp_path):
    """Artist 1 with album 10 (tracks 100, 101); artist 2 has no albums."""
    for table in (TableBlacklist, TableTracks, TableAlbums, TableArtists):
        database.execute(delete(table))
    database.execute(insert(TableArtists).values(lidarrArtistId=1, name='A', path=str(tmp_path)))
    database.execute(insert(TableArtists).values(lidarrArtistId=2, name='Empty', path=str(tmp_path)))
    database.execute(insert(TableAlbums).values(
        lidarrAlbumId=10, artistId=1, title='Album', path=str(tmp_path), cover_status='missing'))
    tracks = {}
    for tid in (100, 101):
        audio = tmp_path / f'{tid}.flac'
        audio.write_bytes(b'fake audio')
        database.execute(insert(TableTracks).values(
            lidarrTrackId=tid, albumId=10, artistId=1, title=f'Song {tid}',
            duration=215, path=str(audio), lyrics_status='missing'))
        tracks[tid] = audio
    yield tracks
    database.remove()


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(event_handler, 'event_stream',
                        lambda type, payload=None: captured.append((type, payload)))
    return captured


@pytest.fixture
def inline_jobs(monkeypatch):
    """Run background jobs synchronously so their effects can be asserted."""
    monkeypatch.setattr(m, '_spawn_thread', lambda target: target())


def _lrc(audio_path):
    return os.path.splitext(str(audio_path))[0] + '.lrc'


# ---------------------------------------------------------------------------
# 1. LRCLIB publish
# ---------------------------------------------------------------------------

class TestPublish:
    def _patch_publish(self, monkeypatch):
        from lyrarr.metadata.lyrics import lrclib_publish
        calls = []
        monkeypatch.setattr(lrclib_publish, 'publish_lyrics', lambda **kw: calls.append(kw))
        return calls

    def test_duration_sent_in_seconds(self, client, library, monkeypatch):
        calls = self._patch_publish(monkeypatch)
        with open(_lrc(library[100]), 'w') as f:
            f.write('[00:01.00]hello\n[00:02.00]world')
        r = client.post('/metadata/lyrics/publish/100', json={})
        assert r.status_code == 200, r.json
        assert calls[0]['duration_s'] == 215  # was 0.215 (seconds divided by 1000)

    def test_refuses_without_duration(self, client, library, monkeypatch):
        calls = self._patch_publish(monkeypatch)
        database.execute(TableTracks.__table__.update()
                         .where(TableTracks.lidarrTrackId == 100).values(duration=None))
        with open(_lrc(library[100]), 'w') as f:
            f.write('hello')
        r = client.post('/metadata/lyrics/publish/100', json={})
        assert r.status_code == 400
        assert 'duration' in r.json['message']
        assert calls == []


# ---------------------------------------------------------------------------
# 2. Cover download validation
# ---------------------------------------------------------------------------

class TestCoverDownload:
    def test_rejects_non_http_url(self, client, library):
        r = client.post('/metadata/covers/download/10', json={'url': 'file:///etc/passwd', 'provider': 'musicbrainz'})
        assert r.status_code == 400

    def test_rejects_non_string_url(self, client, library):
        r = client.post('/metadata/covers/download/10', json={'url': ['x'], 'provider': 'musicbrainz'})
        assert r.status_code == 400

    def test_unknown_provider(self, client, library):
        r = client.post('/metadata/covers/download/10', json={'url': 'https://x.test/a.jpg', 'provider': 'nope'})
        assert r.status_code == 400

    def test_rejected_download_is_502(self, client, library, monkeypatch):
        name = next(iter(m.cover_providers))
        monkeypatch.setattr(m.cover_providers[name], 'download', lambda url: None)
        r = client.post('/metadata/covers/download/10', json={'url': 'https://x.test/a.jpg', 'provider': name})
        assert r.status_code == 502

    def test_missing_album_404(self, client, library):
        name = next(iter(m.cover_providers))
        r = client.post('/metadata/covers/download/999', json={'url': 'https://x.test/a.jpg', 'provider': name})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 3. Whisper: alignment / formatting / caching / serialization
# ---------------------------------------------------------------------------

def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text}


class TestAlignLyrics:
    def test_rounding_never_produces_60_seconds(self):
        lines, _ = m._align_lyrics(['hello there'], [_seg(59.996, 62.0, 'hello there')])
        assert lines == ['[01:00.00] hello there']

    def test_keeps_lyric_order_and_monotonic(self):
        lyrics = ['alpha line', 'unmatched words zzz', 'gamma line', 'delta line']
        segs = [_seg(5.0, 8.0, 'alpha line'), _seg(20.0, 23.0, 'gamma line'), _seg(30.0, 33.0, 'delta line')]
        lines, matched = m._align_lyrics(lyrics, segs)
        assert [line.split('] ', 1)[1] for line in lines] == lyrics
        stamps = [line[:10] for line in lines]
        assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
        assert matched == 3
        # Unmatched line is interpolated between its neighbours.
        assert '[00:05.00]' < stamps[1] < '[00:20.00]'

    def test_repeated_chorus_maps_forward(self):
        lyrics = ['chorus line', 'verse line', 'chorus line']
        segs = [_seg(1, 2, 'chorus line'), _seg(10, 11, 'verse line'), _seg(20, 21, 'chorus line')]
        lines, _ = m._align_lyrics(lyrics, segs)
        assert lines == ['[00:01.00] chorus line', '[00:10.00] verse line', '[00:20.00] chorus line']

    def test_out_of_order_match_is_forced_monotonic(self):
        # Segment starts going backwards must not produce decreasing stamps.
        lyrics = ['one', 'two']
        segs = [_seg(10, 11, 'one'), _seg(9, 12, 'two')]
        lines, _ = m._align_lyrics(lyrics, segs)
        assert lines[0] < lines[1]


@pytest.fixture
def fake_whisper(monkeypatch):
    created = []

    class FakeModel:
        def __init__(self, size, device=None, compute_type=None, download_root=None):
            created.append({'size': size, 'download_root': download_root})

        def transcribe(self, path, **kw):
            segs = [types.SimpleNamespace(start=1.0, end=2.0, text=' first line '),
                    types.SimpleNamespace(start=3.0, end=4.0, text='second line')]
            return iter(segs), types.SimpleNamespace(language='en')

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=FakeModel))
    m._whisper_models.clear()
    yield created
    m._whisper_models.clear()


class TestWhisper:
    def test_model_cached_and_persistent_download_root(self, fake_whisper):
        from lyrarr.app.get_args import args
        a = m._get_whisper_model('base', 'cpu', 'int8')
        b = m._get_whisper_model('base', 'cpu', 'int8')
        assert a is b and len(fake_whisper) == 1
        assert fake_whisper[0]['download_root'] == os.path.join(args.config_dir, 'cache', 'whisper')

    def test_switching_model_evicts_previous(self, fake_whisper):
        m._get_whisper_model('base', 'cpu', 'int8')
        m._get_whisper_model('tiny', 'cpu', 'int8')
        assert list(m._whisper_models) == [('tiny', 'cpu', 'int8')]

    def test_sync_generate_endpoint(self, client, library, fake_whisper):
        r = client.post('/metadata/lyrics/sync-generate/100',
                        json={'content': 'first line\nsecond line', 'model': 'tiny'})
        assert r.status_code == 200, r.json
        assert r.json['synced'] == '[00:01.00] first line\n[00:03.00] second line'

    def test_sync_generate_busy_is_409(self, client, library, fake_whisper):
        with m._whisper_transcribe_lock:
            r = client.post('/metadata/lyrics/sync-generate/100', json={'content': 'x'})
        assert r.status_code == 409

    def test_sync_generate_rejects_unknown_model(self, client, library, fake_whisper):
        r = client.post('/metadata/lyrics/sync-generate/100', json={'content': 'x', 'model': '/etc/evil'})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 4. Batch jobs
# ---------------------------------------------------------------------------

class TestBatchDownload:
    def test_artist_without_albums_is_noop(self, client, library, monkeypatch, inline_jobs):
        called = []
        monkeypatch.setattr(download_worker, 'run_downloads', lambda **kw: called.append(kw))
        r = client.post('/metadata/batch-download', json={'artistIds': [2]})
        assert r.status_code == 200 and r.json['albumCount'] == 0
        assert called == []  # must not fall through to a whole-library run

    def test_no_selection_is_400(self, client, library):
        assert client.post('/metadata/batch-download', json={}).status_code == 400

    def test_totals_in_tracks_and_complete_emitted(self, client, library, monkeypatch, events, inline_jobs):
        called = []

        def fake_run(**kw):
            called.append(kw)
            return {'skipped': False, 'covers': 1, 'lyrics': 2}
        monkeypatch.setattr(download_worker, 'run_downloads', fake_run)
        r = client.post('/metadata/batch-download', json={'artistIds': [1]})
        assert r.status_code == 200
        assert called[0]['album_ids'] == [10]
        start = dict(events)['download_start']
        assert start['total_lyrics'] == 2 and start['total_covers'] == 1
        assert dict(events)['download_complete']['lyrics'] == 2

    def test_failure_still_emits_complete_with_error(self, client, library, monkeypatch, events, inline_jobs):
        def boom(**kw):
            raise RuntimeError('kaboom')
        monkeypatch.setattr(download_worker, 'run_downloads', boom)
        client.post('/metadata/batch-download', json={'albumIds': [10]})
        complete = dict(events)['download_complete']
        assert complete['error'] == 'kaboom'

    def test_second_request_while_running_is_409(self, client, library):
        with m._job_locks['download']:
            r = client.post('/metadata/batch-download', json={'albumIds': [10]})
        assert r.status_code == 409

    def test_bad_ids_are_400(self, client, library):
        assert client.post('/metadata/batch-download', json={'albumIds': ['10']}).status_code == 400
        assert client.post('/metadata/batch-download', json=[10]).status_code == 400


class TestUpgrade:
    def test_empty_selection_is_noop(self, client, library, monkeypatch, inline_jobs):
        called = []
        monkeypatch.setattr(download_worker, 'run_lyrics_upgrade', lambda **kw: called.append(kw))
        r = client.post('/metadata/lyrics/upgrade', json={'artistIds': [2]})
        assert r.status_code == 200 and called == []
        r = client.post('/metadata/lyrics/upgrade', json={'albumIds': []})
        assert r.status_code == 200 and called == []

    def test_no_scope_is_library_wide(self, client, library, monkeypatch, events, inline_jobs):
        called = []

        def fake(**kw):
            called.append(kw)
            return {'skipped': False, 'upgraded': 0}
        monkeypatch.setattr(download_worker, 'run_lyrics_upgrade', fake)
        assert client.post('/metadata/lyrics/upgrade', json={}).status_code == 200
        assert called[0]['album_ids'] is None and called[0]['track_ids'] is None
        assert 'download_complete' in dict(events)


class TestRedetect:
    def test_explicit_empty_track_list_is_noop(self, client, library, monkeypatch):
        spawned = []
        monkeypatch.setattr(m, '_spawn_thread', lambda target: spawned.append(target))
        r = client.post('/metadata/lyrics/batch-redetect', json={'trackIds': []})
        assert r.status_code == 200 and spawned == []


class TestBatchTranslateAndSync:
    def _make_available(self, library, content):
        database.execute(TableTracks.__table__.update()
                         .where(TableTracks.lidarrTrackId == 100)
                         .values(lyrics_status='available', is_synced=False, detected_language=None))
        with open(_lrc(library[100]), 'w') as f:
            f.write(content)

    def test_translate_saves_as_translated(self, client, library, monkeypatch, events, inline_jobs):
        from lyrarr.metadata import language_detect, manager
        self._make_available(library, 'hola mundo')
        saves = []
        monkeypatch.setattr(m, 'save_lyrics', lambda *a, **kw: saves.append((a, kw)) or True)
        monkeypatch.setattr(language_detect, 'detect_language', lambda c: None)
        monkeypatch.setattr(manager, 'get_translator',
                            lambda lang: types.SimpleNamespace(translate=lambda t: t.upper()))
        r = client.post('/metadata/lyrics/batch-translate', json={'albumIds': [10]})
        assert r.status_code == 200
        assert saves[0][1]['source'] == 'translated'
        assert dict(events)['batch_translate_complete']['translated'] == 1

    def test_sync_generate_saves_as_generated(self, client, library, monkeypatch, events,
                                              inline_jobs, fake_whisper):
        self._make_available(library, 'first line\nsecond line')
        saves = []
        monkeypatch.setattr(m, 'save_lyrics', lambda *a, **kw: saves.append((a, kw)) or True)
        r = client.post('/metadata/lyrics/batch-sync-generate', json={'trackIds': [100]})
        assert r.status_code == 200
        args, kw = saves[0]
        assert kw['source'] == 'generated'
        assert args[1]['synced_lyrics'] == '[00:01.00] first line\n[00:03.00] second line'
        assert dict(events)['batch_sync_complete']['synced'] == 1


# ---------------------------------------------------------------------------
# 5/6. Manual saves and blacklist
# ---------------------------------------------------------------------------

def test_interactive_save_is_manual(client, library, monkeypatch):
    saves = []
    monkeypatch.setattr(m, 'save_lyrics', lambda *a, **kw: saves.append(kw) or True)
    r = client.post('/metadata/lyrics/download/100', json={'plain_lyrics': 'x', 'provider': 'editor'})
    assert r.status_code == 200 and saves[0]['source'] == 'manual'


def test_download_rejects_non_string_lyrics(client, library):
    r = client.post('/metadata/lyrics/download/100', json={'plain_lyrics': 5})
    assert r.status_code == 400


class TestBlacklist:
    def _hashes(self):
        return {r.content_hash for r in database.execute(select(TableBlacklist)).scalars()}

    def test_translated_file_also_blacklists_source(self, client, library, monkeypatch):
        with open(_lrc(library[100]), 'w') as f:
            f.write('HELLO WORLD TRANSLATED')
        src_hash = lyrics_store.content_hash('original source words')
        monkeypatch.setattr(lyrics_store, 'hashes_for_blacklist',
                            lambda track_id, content: [lyrics_store.content_hash(content), src_hash],
                            raising=False)
        r = client.post('/metadata/lyrics/blacklist/100', json={'rescan': False})
        assert r.status_code == 200
        assert self._hashes() == {lyrics_store.content_hash('HELLO WORLD TRANSLATED'), src_hash}

    def test_works_without_helper(self, client, library, monkeypatch):
        monkeypatch.delattr(lyrics_store, 'hashes_for_blacklist', raising=False)
        with open(_lrc(library[100]), 'w') as f:
            f.write('some lyrics')
        r = client.post('/metadata/lyrics/blacklist/100', json={})
        assert r.status_code == 200 and r.json['rescan'] is True
        assert not os.path.exists(_lrc(library[100]))
        assert self._hashes() == {lyrics_store.content_hash('some lyrics')}


def test_malformed_json_is_400(client, library):
    r = client.post('/metadata/lyrics/reset-retry', data='{not json', content_type='application/json')
    assert r.status_code == 400
    r = client.post('/metadata/lyrics/versions/100', json={'versionId': 'abc'})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 7. 2.0: PersistResult error contract, publish rules, stats, rescans, repairs
# ---------------------------------------------------------------------------

LYRICS = "first placeholder line of the verse\nsecond placeholder line of the verse\n" \
         "third placeholder line here\nfourth placeholder line here"


@pytest.fixture
def clean_history():
    from lyrarr.app.database import TableHistory, TableLyricsVersions
    database.execute(delete(TableHistory))
    database.execute(delete(TableLyricsVersions))
    yield
    database.execute(delete(TableHistory))
    database.execute(delete(TableLyricsVersions))


def _row(tid=100):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == tid)).scalars().first()


def _update(tid=100, **values):
    database.execute(TableTracks.__table__.update().where(TableTracks.lidarrTrackId == tid).values(**values))


def _versions():
    from lyrarr.app.database import TableLyricsVersions
    return database.execute(select(TableLyricsVersions)).scalars().all()


def _history_rows():
    from lyrarr.app.database import TableHistory
    return database.execute(select(TableHistory)).scalars().all()


class TestSaveErrors:
    def test_save_with_audio_gone_is_409_no_audio(self, client, library, clean_history):
        os.remove(library[100])
        r = client.post('/metadata/lyrics/download/100', json={'plain_lyrics': LYRICS, 'provider': 'editor'})
        assert r.status_code == 409
        assert r.json['code'] == 'no_audio' and str(library[100]) in r.json['message']
        assert not os.path.exists(_lrc(library[100]))

    def test_save_ok_is_manual_action_4(self, client, library, clean_history):
        r = client.post('/metadata/lyrics/download/100', json={'plain_lyrics': LYRICS, 'provider': 'editor'})
        assert r.status_code == 200
        assert [(h.action, h.provider) for h in _history_rows()] == [(4, 'editor')]
        assert _row().lyrics_source == 'manual'

    def test_upload_undecodable_is_400(self, client, library):
        import io
        r = client.post('/metadata/lyrics/upload/100', content_type='multipart/form-data',
                        data={'file': (io.BytesIO(b'caf\xe9 \xff\xfe\xfa broken'), 'x.lrc')})
        assert r.status_code == 400 and r.json['code'] == 'not_utf8'

    def test_upload_utf16_accepted(self, client, library, clean_history):
        import io
        r = client.post('/metadata/lyrics/upload/100', content_type='multipart/form-data',
                        data={'file': (io.BytesIO(b'\xff\xfe' + LYRICS.encode('utf-16-le')), 'x.lrc')})
        assert r.status_code == 200, r.json
        assert open(_lrc(library[100]), encoding='utf-8').read().strip() == LYRICS

    def test_cover_save_to_missing_dir_is_409_and_creates_nothing(self, client, library, monkeypatch, tmp_path):
        gone = tmp_path / 'not-mounted' / 'Album'
        database.execute(TableAlbums.__table__.update().values(path=str(gone)))
        name = next(iter(m.cover_providers))
        monkeypatch.setattr(m.cover_providers[name], 'download', lambda url: b'\x89PNG\r\n\x1a\n' + b'x' * 32)
        r = client.post('/metadata/covers/download/10', json={'url': 'https://x.test/a.png', 'provider': name})
        assert r.status_code == 409 and r.json['code'] == 'album_dir_missing'
        assert str(gone) in r.json['message']
        assert not (tmp_path / 'not-mounted').exists()

    def test_cover_save_into_artist_folder_is_409(self, client, library, monkeypatch):
        name = next(iter(m.cover_providers))
        monkeypatch.setattr(m.cover_providers[name], 'download', lambda url: b'\x89PNG\r\n\x1a\n' + b'x' * 32)
        r = client.post('/metadata/covers/download/10', json={'url': 'https://x.test/a.png', 'provider': name})
        assert r.status_code == 409 and r.json['code'] == 'artist_root'


class TestPublish17:
    def _patch(self, monkeypatch):
        from lyrarr.metadata.lyrics import lrclib_publish
        calls = []
        monkeypatch.setattr(lrclib_publish, 'publish_lyrics', lambda **kw: calls.append(kw))
        return calls

    def test_translated_is_400(self, client, library, monkeypatch, clean_history):
        calls = self._patch(monkeypatch)
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        _update(lyrics_source='translated', lyrics_status='available')
        r = client.post('/metadata/lyrics/publish/100', json={})
        assert r.status_code == 400 and r.json['code'] == 'translated' and calls == []

    def test_generated_needs_confirm(self, client, library, monkeypatch, clean_history):
        calls = self._patch(monkeypatch)
        with open(_lrc(library[100]), 'w') as f:
            f.write('[00:01.00]first placeholder\n[00:05.00]second placeholder')
        _update(lyrics_source='generated', lyrics_status='available')
        r = client.post('/metadata/lyrics/publish/100', json={})
        assert r.status_code == 409 and r.json['code'] == 'confirm_required' and calls == []
        r = client.post('/metadata/lyrics/publish/100', json={'confirm': True})
        assert r.status_code == 200 and len(calls) == 1

    def test_history_action_5_and_repaired_lrc(self, client, library, monkeypatch, clean_history):
        calls = self._patch(monkeypatch)
        with open(_lrc(library[100]), 'w') as f:
            f.write('[00:05.00]second placeholder\n[00:01.00]first placeholder')
        r = client.post('/metadata/lyrics/publish/100', json={})
        assert r.status_code == 200
        assert calls[0]['synced_lyrics'] == '[00:01.00] first placeholder\n[00:05.00] second placeholder'
        assert calls[0]['plain_lyrics'] == 'first placeholder\nsecond placeholder'
        assert [(h.action, h.provider) for h in _history_rows()] == [(5, 'lrclib-publish')]

    def test_plain_with_leftover_timestamps_refused(self, client, library, monkeypatch, clean_history):
        calls = self._patch(monkeypatch)
        with open(_lrc(library[100]), 'w') as f:
            f.write("plain placeholder one\nplain placeholder two\nplain placeholder three\n"
                    "[00:12.00]a stray timed line")
        r = client.post('/metadata/lyrics/publish/100', json={})
        assert r.status_code == 400 and r.json['code'] == 'timestamps_in_plain' and calls == []


def test_provider_stats_exclude_legacy_non_downloads(client, library, clean_history):
    from datetime import datetime

    from lyrarr.app.database import TableHistory
    rows = [
        (1, 'Downloaded lyrics for x', 'lrclib'),
        (2, 'Upgraded lyrics for x', 'netease'),
        (1, 'Published lyrics for x to LRCLIB', 'lrclib'),   # legacy publish recorded as a download
        (1, 'Saved lyrics for x', 'editor'),                 # legacy manual save
        (1, 'Saved lyrics for x', 'batch-translate-de'),
        (4, 'Saved lyrics for x', 'editor'),
        (5, 'Published lyrics for x to LRCLIB', 'lrclib-publish'),
        (6, 'Classified instrumental (title): x', None),
    ]
    for action, desc, provider in rows:
        database.execute(insert(TableHistory).values(action=action, description=desc, metadata_type='lyrics',
                                                     provider=provider, timestamp=datetime.now()))
    r = client.get('/metadata/lyrics/language-stats')
    assert r.status_code == 200
    assert r.json['providers'] == {'lrclib': 1, 'netease': 1}


class TestBlacklistRescan:
    def test_file_content_is_archived_then_removed(self, client, library, clean_history):
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        _update(lyrics_status='available', lyrics_source='auto', lyrics_provider='lrclib', is_synced=False)
        r = client.post('/metadata/lyrics/blacklist/100', json={})
        assert r.status_code == 200 and r.json['rescan'] is True
        assert not os.path.exists(_lrc(library[100]))
        assert [(v.content, v.provider, v.replaced_by) for v in _versions()] == [(LYRICS, 'lrclib', 'blacklist')]
        t = _row()
        assert t.lyrics_status == 'missing' and t.lyrics_source is None and t.lyrics_provider is None
        assert t.lyrics_retry_set_at is not None

    def test_other_content_never_removes_the_file(self, client, library, clean_history):
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        _update(lyrics_status='available', lyrics_source='manual')
        r = client.post('/metadata/lyrics/blacklist/100', json={'content': 'some other search result text'})
        assert r.status_code == 200 and r.json['rescan'] is False
        assert open(_lrc(library[100])).read() == LYRICS
        assert _row().lyrics_status == 'available' and _versions() == []

    @pytest.mark.parametrize('status', ['blacklisted', 'instrumental'])
    def test_terminal_status_kept(self, client, library, clean_history, status):
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        _update(lyrics_status=status)
        r = client.post('/metadata/lyrics/blacklist/100', json={})
        assert r.status_code == 200 and r.json['rescan'] is True
        assert _row().lyrics_status == status and not os.path.exists(_lrc(library[100]))

    def test_unarchivable_file_is_kept(self, client, library, clean_history, monkeypatch):
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        monkeypatch.setattr(lyrics_store, '_archive_existing', lambda *a, **k: False)
        r = client.post('/metadata/lyrics/blacklist/100', json={})
        assert r.json['rescan'] is False and os.path.exists(_lrc(library[100]))


class TestValidateLrcRepair:
    def test_large_gap_only_file_not_rewritten(self, client, library, clean_history):
        content = '[00:10.00]first placeholder\n[00:50.00]second placeholder\n'
        with open(_lrc(library[100]), 'w') as f:
            f.write(content)
        mtime = os.path.getmtime(_lrc(library[100]))
        r = client.post('/metadata/lyrics/validate-lrc/100', json={})
        assert r.status_code == 200 and 'Nothing to repair' in r.json['message']
        assert open(_lrc(library[100])).read() == content
        assert os.path.getmtime(_lrc(library[100])) == mtime and _versions() == []

    def test_repair_archives_and_keeps_status_and_hash(self, client, library, clean_history):
        content = '[00:05.00]second placeholder\n[00:01.00]first placeholder\n'
        with open(_lrc(library[100]), 'w') as f:
            f.write(content)
        _update(lyrics_status='available', lyrics_source='auto',
                lyrics_content_hash=lyrics_store.content_hash(content))
        r = client.post('/metadata/lyrics/validate-lrc/100', json={})
        assert r.status_code == 200
        assert open(_lrc(library[100])).read() == '[00:01.00] first placeholder\n[00:05.00] second placeholder\n'
        assert [v.content for v in _versions()] == [content.strip()]
        t = _row()
        assert t.lyrics_status == 'available' and t.lyrics_source == 'auto' and t.is_synced
        assert t.lyrics_content_hash == lyrics_store.content_hash(open(_lrc(library[100])).read())

    def test_repair_refused_without_audio(self, client, library, clean_history):
        with open(_lrc(library[100]), 'w') as f:
            f.write('[00:05.00]b\n[00:01.00]a\n')
        os.remove(library[100])
        r = client.post('/metadata/lyrics/validate-lrc/100', json={})
        assert r.status_code == 409 and r.json['code'] == 'no_audio'


def test_reset_retry_only_touches_missing_rows(client, library):
    from datetime import datetime, timedelta
    later = datetime.now() + timedelta(days=5)
    _update(100, lyrics_status='missing', lyrics_retry_count=3, lyrics_retry_after=later, lyrics_cooldown_count=2)
    _update(101, lyrics_status='instrumental', lyrics_retry_count=3, lyrics_retry_after=later)
    r = client.post('/metadata/lyrics/reset-retry', json={'trackIds': [100, 101]})
    assert r.status_code == 200 and r.json['count'] == 1
    a, b = _row(100), _row(101)
    assert (a.lyrics_retry_count, a.lyrics_retry_after, a.lyrics_cooldown_count) == (0, None, 0)
    assert a.lyrics_retry_set_at is not None
    assert b.lyrics_status == 'instrumental' and b.lyrics_retry_count == 3


def test_sidecar_import_adopts_only_real_lyrics(client, library, inline_jobs):
    with open(_lrc(library[100]), 'w') as f:
        f.write(LYRICS)
    with open(_lrc(library[101]), 'w') as f:
        f.write('[au: instrumental]\n')
    r = client.post('/metadata/lyrics/import-sidecar', json={})
    assert r.status_code == 200
    assert _row(100).lyrics_status == 'available' and _row(101).lyrics_status == 'missing'


def test_redetect_keeps_manual_language(client, library, inline_jobs, monkeypatch):
    from lyrarr.metadata import language_detect
    monkeypatch.setattr(language_detect, 'detect_language', lambda c: 'en')
    for tid in (100, 101):
        with open(_lrc(library[tid]), 'w') as f:
            f.write(LYRICS)
    _update(100, lyrics_status='available', detected_language='ja', language_source='manual')
    _update(101, lyrics_status='available', detected_language=None)
    client.post('/metadata/lyrics/batch-redetect', json={})
    assert _row(100).detected_language == 'ja' and _row(101).detected_language == 'en'


class TestTranslateFailures:
    def test_preview_all_calls_failed_is_502(self, client, library, monkeypatch):
        import deep_translator

        class Broken:
            def __init__(self, **kw):
                pass

            def translate(self, text):
                raise ConnectionError('offline')
        monkeypatch.setattr(deep_translator, 'GoogleTranslator', Broken)
        r = client.post('/metadata/lyrics/translate/100', json={'content': LYRICS, 'targetLang': 'de'})
        assert r.status_code == 502 and r.json['code'] == 'translation_failed'

    def test_batch_failure_saves_nothing_and_reports(self, client, library, monkeypatch, events, inline_jobs):
        from lyrarr.metadata import language_detect, manager

        class Broken:
            def translate(self, text):
                raise ConnectionError('offline')
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        _update(100, lyrics_status='available', detected_language=None)
        monkeypatch.setattr(language_detect, 'detect_language', lambda c: None)
        monkeypatch.setattr(manager, 'get_translator', lambda lang: Broken())
        r = client.post('/metadata/lyrics/batch-translate', json={'albumIds': [10]})
        assert r.status_code == 200
        done = dict(events)['batch_translate_complete']
        assert done['translated'] == 0 and done['failed'] == 1 and 'translation failed' in done['message']
        assert open(_lrc(library[100])).read() == LYRICS and _row().lyrics_source is None

    def test_batch_reports_per_track_reasons(self, client, library, monkeypatch, events, inline_jobs):
        from lyrarr.metadata import language_detect, manager
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        _update(100, lyrics_status='available', detected_language=None)
        monkeypatch.setattr(language_detect, 'detect_language', lambda c: None)
        monkeypatch.setattr(manager, 'get_translator',
                            lambda lang: types.SimpleNamespace(translate=lambda t: t.upper()))
        os.remove(library[100])
        client.post('/metadata/lyrics/batch-translate', json={'albumIds': [10]})
        assert '1 skipped: audio not reachable' in dict(events)['batch_translate_complete']['message']

    def test_batch_translation_archives_source(self, client, library, monkeypatch, events, inline_jobs,
                                               clean_history):
        from lyrarr.metadata import language_detect, manager
        with open(_lrc(library[100]), 'w') as f:
            f.write(LYRICS)
        _update(100, lyrics_status='available', detected_language=None)
        monkeypatch.setattr(language_detect, 'detect_language', lambda c: None)
        monkeypatch.setattr(manager, 'get_translator',
                            lambda lang: types.SimpleNamespace(translate=lambda t: t.upper()))
        client.post('/metadata/lyrics/batch-translate', json={'albumIds': [10], 'targetLang': 'de'})
        t = _row()
        assert t.lyrics_source == 'translated' and t.detected_language == 'de'
        assert [v.translated_from for v in _versions()] == ['und']


class TestLegacyAuditEndpoint:
    def _fake_runner(self, monkeypatch, result):
        calls = []
        runner = types.ModuleType('lyrarr.utilities.audit.runner')
        runner.LEGACY_ENDPOINT_CHECKS = ('files.status_drift', 'files.marker_or_empty_sidecar',
                                         'state.row_invariants')
        runner.start_run = lambda mode, check_ids=None, **kw: calls.append((mode, check_ids, kw)) or result
        pkg = types.ModuleType('lyrarr.utilities.audit')
        pkg.runner = runner
        monkeypatch.setitem(sys.modules, 'lyrarr.utilities.audit', pkg)
        monkeypatch.setitem(sys.modules, 'lyrarr.utilities.audit.runner', runner)
        return calls

    def test_delegates_to_audit(self, client, library, monkeypatch):
        calls = self._fake_runner(monkeypatch, 7)
        r = client.post('/metadata/lyrics/audit', json={})
        assert r.status_code == 200 and r.json['run_id'] == 7
        assert calls == [('apply', ['files.status_drift', 'files.marker_or_empty_sidecar',
                                    'state.row_invariants'], {'trigger': 'legacy_endpoint'})]

    def test_busy_is_409(self, client, library, monkeypatch):
        self._fake_runner(monkeypatch, None)
        assert client.post('/metadata/lyrics/audit', json={}).status_code == 409
