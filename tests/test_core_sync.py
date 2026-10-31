"""Regression tests for the Lidarr sync (lyrarr/lidarr/*).

Covers: a failed Lidarr request is never treated as "deleted", per-album error
isolation (FOREIGN KEY / stale default profile), propagation of deletions,
carry-over of lyrarr state when Lidarr re-imports a file under a new trackfile
id, recording-id population, and SignalR message parsing / supervision.
Lyrics state from disk (PLAN §3.1/§3.4): instrumental adoption, STRONG-title
conflict entries, marker sidecars, mount-down guard, language preservation,
renamed files, cover re-derivation, duration rounding, tombstones and
run_full_sync.

Runs against a throwaway SQLite database in a temp config dir.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _cfg_dir = tempfile.mkdtemp(prefix='lyrarr-test-core-')
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', _cfg_dir, '--no-tasks', '--no-signalr']
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from lyrarr.app.get_args import args as _args  # noqa: E402

_REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
if os.path.abspath(_args.config_dir) == os.path.abspath(_REPO_DATA):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

import requests  # noqa: E402
from sqlalchemy import insert  # noqa: E402

import lyrarr.lidarr.sync as sync  # noqa: E402
from lyrarr.app.config import settings  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableBlacklist,
    TableHistory,
    TableLibraryRoots,
    TableLyricsVersions,
    TableProfiles,
    TableTracks,
    TableTrackTombstones,
    database,
    delete,
    func,
    init_db,
    select,
)
from lyrarr.lidarr import api_client, signalr_client  # noqa: E402
from lyrarr.lidarr.api_client import LidarrError, lidarr_api  # noqa: E402

init_db()


def _count(table):
    return database.execute(select(func.count()).select_from(table)).scalar()


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    for table in (TableLyricsVersions, TableBlacklist, TableHistory, TableTrackTombstones, TableTracks,
                  TableAlbums, TableArtists, TableLibraryRoots):
        database.execute(delete(table))
    monkeypatch.setattr(lidarr_api, 'get_root_folders', lambda: [])
    monkeypatch.setattr(settings.general, 'use_lidarr', True)
    monkeypatch.setattr(settings.lidarr, 'only_monitored', False)
    monkeypatch.setattr(settings.general, 'default_profile_id', None)
    monkeypatch.setattr(sync, 'event_stream', lambda *a, **k: None)
    monkeypatch.setattr(sync, '_detect', lambda content: 'en')  # langdetect is optional
    yield
    for table in (TableTrackTombstones, TableLibraryRoots):
        database.execute(delete(table))
    database.remove()


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f'{self.status_code}')

    def json(self):
        return self._payload


def _seed_artist_with_state():
    database.execute(insert(TableArtists).values(lidarrArtistId=1, name='A', path='/a'))
    database.execute(insert(TableAlbums).values(lidarrAlbumId=1, artistId=1, title='Alb'))
    database.execute(insert(TableTracks).values(
        lidarrTrackId=1, albumId=1, artistId=1, title='T', lyrics_status='blacklisted'))
    database.execute(insert(TableHistory).values(action=1, description='x', lidarrTrackId=1))
    database.execute(insert(TableBlacklist).values(metadata_type='lyrics', content_hash='h', lidarrTrackId=1))
    database.execute(insert(TableLyricsVersions).values(lidarrTrackId=1, content='old', lyrics_type='plain'))


class TestLidarrErrors:
    def test_get_raises_on_timeout_and_returns_none_on_404(self, monkeypatch):
        monkeypatch.setattr(lidarr_api._session, 'get',
                            lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout('t')))
        with pytest.raises(LidarrError):
            lidarr_api.get_artist(1)
        with pytest.raises(LidarrError):
            lidarr_api.get_artists()
        monkeypatch.setattr(lidarr_api._session, 'get', lambda *a, **k: FakeResponse(404))
        assert lidarr_api.get_artist(1) is None
        monkeypatch.setattr(lidarr_api._session, 'get', lambda *a, **k: FakeResponse(500))
        with pytest.raises(LidarrError):
            lidarr_api.get_artist(1)

    def test_timeout_during_artist_sync_keeps_everything(self, monkeypatch):
        _seed_artist_with_state()
        monkeypatch.setattr(lidarr_api._session, 'get',
                            lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout('t')))
        sync.sync_artist(1)
        for table in (TableArtists, TableTracks, TableHistory, TableBlacklist, TableLyricsVersions):
            assert _count(table) == 1, table.__tablename__

    def test_404_with_unreachable_status_keeps_everything(self, monkeypatch):
        # A wrong Lidarr URL base 404s every endpoint — that is not a deletion.
        _seed_artist_with_state()
        monkeypatch.setattr(lidarr_api._session, 'get', lambda *a, **k: FakeResponse(404))
        sync.sync_artist(1)
        assert _count(TableArtists) == 1

    def test_confirmed_404_deletes_artist(self, monkeypatch):
        _seed_artist_with_state()

        def fake_get(url, **kwargs):
            if url.endswith('system/status'):
                return FakeResponse(200, {'version': '2.0'})
            return FakeResponse(404)
        monkeypatch.setattr(lidarr_api._session, 'get', fake_get)
        sync.sync_artist(1)
        assert _count(TableArtists) == 0
        assert _count(TableTracks) == 0

    def test_failed_full_artist_list_deletes_nothing(self, monkeypatch):
        _seed_artist_with_state()
        monkeypatch.setattr(lidarr_api, 'get_artists', lambda: (_ for _ in ()).throw(LidarrError('down')))
        sync.update_artists()
        assert _count(TableArtists) == 1
        monkeypatch.setattr(lidarr_api, 'get_artists', lambda: [])
        sync.update_artists()
        assert _count(TableArtists) == 1


def _patch_lidarr(monkeypatch, artists, albums, files, records=(), roots=()):
    monkeypatch.setattr(lidarr_api, 'get_root_folders', lambda: [{'path': r} for r in roots])
    monkeypatch.setattr(lidarr_api, 'get_artists', lambda: artists)
    monkeypatch.setattr(lidarr_api, 'get_artist', lambda artist_id: next(
        (a for a in artists if a['id'] == artist_id), None))
    monkeypatch.setattr(lidarr_api, 'get_albums', lambda artist_id=None: [
        a for a in albums if artist_id is None or a['artistId'] == artist_id])
    monkeypatch.setattr(lidarr_api, 'get_tracks', lambda album_id=None, artist_id=None: [
        f for f in files if f['artistId'] == artist_id])
    monkeypatch.setattr(lidarr_api, 'get_track_records', lambda album_id=None, artist_id=None: [
        r for r in records if r['artistId'] == artist_id])


class TestAlbumSyncIsolation:
    def test_unsynced_artist_album_is_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setattr(settings.lidarr, 'only_monitored', True)
        artists = [{'id': 1, 'artistName': 'Mon', 'path': '/m/a', 'monitored': True},
                   {'id': 2, 'artistName': 'Unmon', 'path': '/m/b', 'monitored': False},
                   {'id': 3, 'artistName': 'Later', 'path': '/m/c', 'monitored': True}]
        albums = [{'id': 20, 'artistId': 2, 'title': 'of unmonitored artist', 'monitored': True},
                  {'id': 30, 'artistId': 3, 'title': 'C', 'monitored': True},
                  {'id': 10, 'artistId': 1, 'title': 'A', 'monitored': True}]
        files = [{'id': a * 100, 'albumId': a * 10, 'artistId': a, 'path': f'/m/{a}/01 - x.flac'}
                 for a in (1, 2, 3)]
        _patch_lidarr(monkeypatch, artists, albums, files)
        sync.update_artists()
        ids = set(database.execute(select(TableAlbums.lidarrAlbumId)).scalars())
        assert ids == {10, 30}

    def test_stale_default_profile_falls_back_to_null(self, monkeypatch):
        missing = (database.execute(select(func.max(TableProfiles.id))).scalar() or 0) + 50
        monkeypatch.setattr(settings.general, 'default_profile_id', missing)
        artists = [{'id': 1, 'artistName': 'A', 'path': '/m/a', 'monitored': True}]
        albums = [{'id': 10, 'artistId': 1, 'title': 'A', 'monitored': True}]
        files = [{'id': 100, 'albumId': 10, 'artistId': 1, 'path': '/m/1/01 - x.flac'}]
        _patch_lidarr(monkeypatch, artists, albums, files)
        sync.update_artists()
        album = database.execute(select(TableAlbums)).scalars().one()
        assert album.profileId is None

    def test_one_failing_album_does_not_abort_others(self, monkeypatch):
        artists = [{'id': 1, 'artistName': 'A', 'path': '/m/a', 'monitored': True}]
        albums = [{'id': 10, 'artistId': 1, 'title': 'Bad', 'monitored': True},
                  {'id': 11, 'artistId': 1, 'title': 'Good', 'monitored': True}]
        files = [{'id': 100, 'albumId': 10, 'artistId': 1, 'path': '/m/1/a.flac'},
                 {'id': 110, 'albumId': 11, 'artistId': 1, 'path': '/m/1/b.flac'}]
        _patch_lidarr(monkeypatch, artists, albums, files)
        real = sync._upsert_album

        def flaky(album, track_files=None, **kw):
            if album['id'] == 10:
                raise RuntimeError('boom')
            return real(album, track_files=track_files, **kw)
        monkeypatch.setattr(sync, '_upsert_album', flaky)
        sync.update_artists()
        assert set(database.execute(select(TableAlbums.lidarrAlbumId)).scalars()) == {11}


class TestDeletionPropagation:
    def _library(self, tmp_path):
        artists = [{'id': 1, 'artistName': 'A', 'path': str(tmp_path), 'monitored': True},
                   {'id': 2, 'artistName': 'B', 'path': str(tmp_path), 'monitored': True}]
        albums = [{'id': 10, 'artistId': 1, 'title': 'A1', 'monitored': True},
                  {'id': 11, 'artistId': 1, 'title': 'A2', 'monitored': True},
                  {'id': 20, 'artistId': 2, 'title': 'B1', 'monitored': True}]
        files = [{'id': 100, 'albumId': 10, 'artistId': 1, 'path': str(tmp_path / '01 - one.flac')},
                 {'id': 101, 'albumId': 10, 'artistId': 1, 'path': str(tmp_path / '02 - two.flac')},
                 {'id': 110, 'albumId': 11, 'artistId': 1, 'path': str(tmp_path / 'other.flac')},
                 {'id': 200, 'albumId': 20, 'artistId': 2, 'path': str(tmp_path / 'b.flac')}]
        records = [{'id': 1000, 'trackFileId': 100, 'albumId': 10, 'artistId': 1, 'title': 'One',
                    'foreignRecordingId': 'rec-one'},
                   {'id': 1001, 'trackFileId': 101, 'albumId': 10, 'artistId': 1, 'title': 'Two',
                    'foreignRecordingId': 'rec-two'}]
        return artists, albums, files, records

    def test_mbid_is_populated(self, monkeypatch, tmp_path):
        _patch_lidarr(monkeypatch, *self._library(tmp_path))
        sync.update_artists()
        track = database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == 100)).scalars().one()
        assert track.mbId == 'rec-one'

    def test_removed_artist_album_track_are_deleted(self, monkeypatch, tmp_path):
        artists, albums, files, records = self._library(tmp_path)
        _patch_lidarr(monkeypatch, artists, albums, files, records)
        sync.update_artists()
        assert _count(TableTracks) == 4

        # Artist 2 deleted; album 11 lost its files; track 101 deleted.
        artists = artists[:1]
        albums = [a for a in albums if a['artistId'] == 1]
        files = [f for f in files if f['id'] == 100]
        _patch_lidarr(monkeypatch, artists, albums, files, records)
        sync.update_artists()
        assert set(database.execute(select(TableArtists.lidarrArtistId)).scalars()) == {1}
        assert set(database.execute(select(TableAlbums.lidarrAlbumId)).scalars()) == {10}
        assert set(database.execute(select(TableTracks.lidarrTrackId)).scalars()) == {100}

    def test_failed_trackfile_fetch_deletes_nothing(self, monkeypatch, tmp_path):
        _patch_lidarr(monkeypatch, *self._library(tmp_path))
        sync.update_artists()
        monkeypatch.setattr(lidarr_api, 'get_tracks',
                            lambda **k: (_ for _ in ()).throw(LidarrError('timeout')))
        sync.update_artists()
        sync.sync_artist(1)
        assert _count(TableTracks) == 4

    def test_reimported_file_carries_state_over(self, monkeypatch, tmp_path):
        artists, albums, files, records = self._library(tmp_path)
        _patch_lidarr(monkeypatch, artists, albums, files, records)
        sync.update_artists()
        database.execute(sync.update(TableTracks).where(TableTracks.lidarrTrackId == 101)
                         .values(lyrics_status='blacklisted', lyrics_source='manual'))
        database.execute(insert(TableHistory).values(action=1, description='h', lidarrTrackId=101))
        database.execute(insert(TableBlacklist).values(metadata_type='lyrics', content_hash='x', lidarrTrackId=101))
        database.execute(insert(TableLyricsVersions).values(lidarrTrackId=101, content='c', lyrics_type='plain'))

        # Lidarr upgrades "02 - two" to FLAC... under a new trackfile id 102.
        files = [f for f in files if f['id'] != 101] + [
            {'id': 102, 'albumId': 10, 'artistId': 1, 'path': str(tmp_path / '02 - two.mp3')}]
        records = [r if r['id'] != 1001 else {**r, 'trackFileId': 102} for r in records]
        _patch_lidarr(monkeypatch, artists, albums, files, records)
        sync.update_artists()

        ids = set(database.execute(select(TableTracks.lidarrTrackId)).scalars())
        assert 101 not in ids and 102 in ids
        new = database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == 102)).scalars().one()
        assert new.lyrics_status == 'blacklisted'
        for table in (TableHistory, TableBlacklist, TableLyricsVersions):
            assert database.execute(select(table.lidarrTrackId)).scalars().all() == [102]

    def test_legacy_duplicate_row_merged_by_path(self, monkeypatch, tmp_path):
        # Rows left by earlier versions have no recording id: match on the
        # path stem (= the same .lrc file).
        artists, albums, files, records = self._library(tmp_path)
        _patch_lidarr(monkeypatch, artists, albums, files, records)
        sync.update_artists()
        database.execute(insert(TableTracks).values(
            lidarrTrackId=99, albumId=10, artistId=1, title='Old', lyrics_status='instrumental',
            path=str(tmp_path / '01 - one.mp3')))
        sync.sync_artist(1)
        assert _count(TableTracks) == 4
        row = database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == 100)).scalars().one()
        assert row.lyrics_status == 'instrumental'


VERSE = "\n".join([
    "first line of the verse",
    "second line of the verse",
    "la la placeholder chorus",
    "closing words of the song",
])


class Lib:
    """One artist / one album / N tracks under <tmp>/music with real files."""

    def __init__(self, tmp_path, titles=('Song',), root='music'):
        self.root = tmp_path / root
        self.artist_dir = self.root / 'Artist'
        self.album_dir = self.artist_dir / 'Album'
        self.album_dir.mkdir(parents=True)
        self.files = []
        self.records = []
        for i, title in enumerate(titles):
            audio = self.album_dir / f'{i + 1:02d} - track{i + 1}.flac'
            audio.write_bytes(b'audio')
            fid = 100 + i
            self.files.append({'id': fid, 'albumId': 10, 'artistId': 1, 'path': str(audio)})
            self.records.append({'id': 1000 + i, 'trackFileId': fid, 'albumId': 10, 'artistId': 1, 'title': title,
                                 'duration': 200_400, 'foreignRecordingId': f'rec-{i}', 'trackNumber': i + 1})
        self.artists = [{'id': 1, 'artistName': 'Artist', 'path': str(self.artist_dir), 'monitored': True}]
        self.albums = [{'id': 10, 'artistId': 1, 'title': 'Album', 'monitored': True}]

    def lrc(self, i=0):
        return self.files[i]['path'][:-5] + '.lrc'

    def audio(self, i=0):
        return self.files[i]['path']

    def sync(self, monkeypatch):
        _patch_lidarr(monkeypatch, self.artists, self.albums, self.files, self.records, roots=[str(self.root)])
        return sync.update_artists()


def _row(track_id=100):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


def _set(track_id=100, **values):
    database.execute(sync.update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values))


class TestLyricsStateFromDisk:
    def test_instrumental_with_real_lyrics_and_plain_title_becomes_available(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path, titles=('Interlude', 'Song (Instrumental)'))
        lib.sync(monkeypatch)
        for i in (0, 1):
            open(lib.lrc(i), 'w').write(VERSE)
        _set(100, lyrics_status='instrumental', instrumental_reason=None)
        _set(101, lyrics_status='instrumental', instrumental_reason='title')
        lib.sync(monkeypatch)
        assert _row(100).lyrics_status == 'available' and _row(100).instrumental_reason is None
        assert _row(101).lyrics_status == 'instrumental'

    def test_override_always_stays_instrumental(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        open(lib.lrc(), 'w').write(VERSE)
        _set(lyrics_status='instrumental', instrumental_reason='user', instrumental_override='always')
        lib.sync(monkeypatch)
        assert _row().lyrics_status == 'instrumental'

    def test_available_strong_title_gets_conflict_entry(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path, titles=('X (Instrumental)', 'Y (Instrumental)'))
        open(lib.lrc(0), 'w').write(VERSE)
        open(lib.lrc(1), 'w').write(VERSE)
        lib.sync(monkeypatch)
        row = _row(100)
        assert row.lyrics_status == 'available'
        assert row.to_dict()['instrumental_evidence'][0]['veto'] == 'sidecar'
        # A manual-source row gets no entry.
        _set(101, lyrics_source='manual', instrumental_evidence=None)
        lib.sync(monkeypatch)
        assert _row(101).instrumental_evidence is None
        assert _row(100).to_dict()['instrumental_evidence'][0]['veto'] == 'sidecar'

    def test_marker_sidecar_is_missing(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        open(lib.lrc(), 'w').write('[au: instrumental]\n')
        lib.sync(monkeypatch)
        assert _row().lyrics_status == 'missing' and not _row().hasLyrics

    def test_mount_down_keeps_state(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        _set(lyrics_source='manual', detected_language='ja', language_source='manual')
        os.remove(lib.lrc())
        os.remove(lib.audio())  # audio gone too: the file moved or the mount is down
        lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_status == 'available' and row.lyrics_source == 'manual'
        assert row.detected_language == 'ja' and row.hasLyrics

    def test_manual_language_survives_sync(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        assert _row().detected_language == 'en'
        _set(detected_language='ja', language_source='manual')
        lib.sync(monkeypatch)
        assert _row().detected_language == 'ja'
        # A detection failure never writes None over a value.
        _set(language_source=None, detected_language='de')
        monkeypatch.setattr(sync, '_detect', lambda content: None)
        lib.sync(monkeypatch)
        assert _row().detected_language == 'de'

    def test_status_change_resets_retry_unchanged_keeps_it(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        later = datetime.now() + timedelta(days=3)
        _set(lyrics_retry_count=3, lyrics_retry_after=later, lyrics_transient_count=2)
        lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_retry_count == 3 and row.lyrics_transient_count == 2  # still missing: kept
        _set(lyrics_status='available', lyrics_source='auto')
        lib.sync(monkeypatch)  # no file: available → missing
        row = _row()
        assert row.lyrics_status == 'missing' and row.lyrics_source is None
        assert (row.lyrics_retry_count, row.lyrics_retry_after, row.lyrics_transient_count) == (0, None, 0)
        assert row.lyrics_retry_set_at is not None

    def test_discovered_file_has_unknown_origin(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        _set(lyrics_source='auto')  # stale source on a missing row
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_status == 'available' and row.lyrics_source is None

    def test_lossy_manual_file_stays_available(self, monkeypatch, tmp_path):
        # Synthetic Cyrillic text in cp1251: not UTF-8, so its content can't be
        # judged — it must never be reclassified as 'empty' and flipped to missing.
        lossy = '[00:01.00]первая строка куплета\n[00:05.00]вторая строка куплета\n'.encode('cp1251')
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        with open(lib.lrc(), 'wb') as f:
            f.write(lossy)
        _set(lyrics_status='available', hasLyrics=True, lyrics_source='manual', detected_language='ru',
             language_source='detected', lyrics_retry_count=2)
        for _ in range(2):
            lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_status == 'available' and row.lyrics_source == 'manual' and row.hasLyrics
        assert row.detected_language == 'ru' and row.lyrics_retry_count == 2
        with open(lib.lrc(), 'rb') as f:
            assert f.read() == lossy

    def test_lossy_file_presence(self, monkeypatch, tmp_path):
        lossy = '[00:01.00]первая строка куплета\n'.encode('cp1251')
        lib = Lib(tmp_path, titles=('Song', 'Interlude', 'Third'))
        with open(lib.lrc(0), 'wb') as f:
            f.write(lossy)
        lib.sync(monkeypatch)  # new row: present
        row = _row(100)
        assert row.lyrics_status == 'available' and row.hasLyrics and row.detected_language is None
        for i in (1, 2):
            with open(lib.lrc(i), 'wb') as f:
                f.write(lossy)
        _set(101, lyrics_status='instrumental', instrumental_reason='consensus')
        lib.sync(monkeypatch)
        assert _row(101).lyrics_status == 'instrumental'  # unknown content never lifts the flag
        assert _row(102).lyrics_status == 'available'  # missing row: the file is present

    def test_provenance_cleared_on_delete_and_user_file(self, monkeypatch, tmp_path):
        from lyrarr.metadata import lyrics_store
        lib = Lib(tmp_path)
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        _set(lyrics_source='auto', lyrics_content_hash=lyrics_store.content_hash(VERSE),
             lyrics_provider='lrclib', lyrics_source_id='4242', lyrics_match_score=0.9,
             lyrics_match_json='{"strategy": "exact_album"}')
        os.remove(lib.lrc())
        lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_status == 'missing'
        assert (row.lyrics_provider, row.lyrics_source_id, row.lyrics_match_score, row.lyrics_match_json,
                row.lyrics_content_hash) == (None, None, None, None, None)
        open(lib.lrc(), 'w').write(VERSE.replace('verse', 'stanza'))  # the user's own file
        lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_status == 'available' and row.lyrics_source is None
        assert (row.lyrics_provider, row.lyrics_source_id, row.lyrics_match_score) == (None, None, None)

    def test_flip_to_available_drops_provenance_of_other_content(self, monkeypatch, tmp_path):
        from lyrarr.metadata import lyrics_store
        lib = Lib(tmp_path, titles=('Song', 'Other'))
        lib.sync(monkeypatch)
        prov = dict(lyrics_source='auto', lyrics_provider='lrclib', lyrics_source_id='4242',
                    lyrics_match_score=0.9)
        # 100: legacy missing row whose stale provenance belongs to other text.
        _set(100, lyrics_content_hash=lyrics_store.content_hash('older text line'), **prov)
        # 101: the file is exactly what lyrarr wrote (hash-verified): provenance kept.
        _set(101, lyrics_content_hash=lyrics_store.content_hash(VERSE), **prov)
        open(lib.lrc(0), 'w').write(VERSE)
        open(lib.lrc(1), 'w').write(VERSE)
        lib.sync(monkeypatch)
        a, b = _row(100), _row(101)
        assert a.lyrics_status == b.lyrics_status == 'available'
        assert (a.lyrics_source, a.lyrics_provider, a.lyrics_match_score) == (None, None, None)
        assert (b.lyrics_source, b.lyrics_provider, b.lyrics_source_id, b.lyrics_match_score) == \
            ('auto', 'lrclib', '4242', 0.9)

    def test_duration_rounded(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.records[0]['duration'] = 200_600
        lib.sync(monkeypatch)
        assert _row().duration == 201
        lib.records[0]['duration'] = 200_400
        lib.sync(monkeypatch)
        assert _row().duration == 200

    def test_cover_status_rederived(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        (lib.album_dir / 'Folder.JPG').write_bytes(b'\xff\xd8\xff')
        lib.sync(monkeypatch)
        album = database.execute(select(TableAlbums)).scalars().one()
        assert album.cover_status == 'available'
        os.remove(lib.album_dir / 'Folder.JPG')
        lib.sync(monkeypatch)
        database.remove()
        assert database.execute(select(TableAlbums)).scalars().one().cover_status == 'missing'

    def test_rename_keeps_manual_lyrics(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        _set(lyrics_source='manual')
        # Lidarr renames the file (same trackfile id).
        new_audio = lib.album_dir / '01 - Song.flac'
        os.rename(lib.audio(), new_audio)
        lib.files[0]['path'] = str(new_audio)
        lib.sync(monkeypatch)
        row = _row()
        assert os.path.isfile(str(new_audio)[:-5] + '.lrc')
        assert row.lyrics_status == 'available' and row.lyrics_source == 'manual'

    def test_reimport_under_new_id_and_name_moves_lrc(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        _set(lyrics_source='manual', instrumental_override='never')
        new_audio = lib.album_dir / '01 - Song.mp3'
        os.rename(lib.audio(), new_audio)
        lib.files[0] = {**lib.files[0], 'id': 150, 'path': str(new_audio)}
        lib.records[0] = {**lib.records[0], 'trackFileId': 150}
        lib.sync(monkeypatch)
        row = _row(150)
        assert _row(100) is None
        assert row.lyrics_status == 'available' and row.lyrics_source == 'manual'
        assert row.instrumental_override == 'never'


class TestCarryOver:
    def _reimport(self, monkeypatch, lib, new_id=150):
        lib.files[0] = {**lib.files[0], 'id': new_id}
        lib.records[0] = {**lib.records[0], 'trackFileId': new_id}
        database.execute(sync.delete(TableTracks).where(TableTracks.lidarrTrackId == new_id))
        lib.sync(monkeypatch)
        return _row(new_id)

    def test_instrumental_carried_only_onto_missing(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        _set(lyrics_status='instrumental', instrumental_reason='consensus',
             instrumental_evidence=json.dumps([{'source': 'lrclib'}]), instrumental_override='always')
        new = self._reimport(monkeypatch, lib)
        assert new.lyrics_status == 'instrumental' and new.instrumental_reason == 'consensus'
        assert new.instrumental_override == 'always'

    def test_instrumental_not_carried_onto_available(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        _set(lyrics_status='instrumental', instrumental_reason=None)
        open(lib.lrc(), 'w').write(VERSE)
        new = self._reimport(monkeypatch, lib)
        assert new.lyrics_status == 'available'

    def test_blacklisted_always_carried(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        _set(lyrics_status='blacklisted', instrumental_prev=json.dumps({'why': 'legacy-unverified'}),
             language_source='manual', detected_language='ja')
        new = self._reimport(monkeypatch, lib)
        assert new.lyrics_status == 'blacklisted' and new.instrumental_prev is not None
        assert new.language_source == 'manual' and new.detected_language == 'ja'


class TestTombstones:
    def test_round_trip_override_never_and_blacklist(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        _set(instrumental_override='never')
        for h in ('hash-a', 'hash-b'):
            database.execute(insert(TableBlacklist).values(metadata_type='lyrics', content_hash=h,
                                                           lidarrTrackId=100, lidarrAlbumId=10))
        # Lidarr temporarily loses the album (e.g. a failed import), then re-imports it.
        files = lib.files
        lib.files = []
        lib.albums[0]['statistics'] = {'trackFileCount': 0}
        _patch_lidarr(monkeypatch, lib.artists, lib.albums, [], lib.records, roots=[str(lib.root)])
        monkeypatch.setattr(lidarr_api, 'get_tracks', lambda album_id=None, artist_id=None: [])
        sync.update_artists()
        assert _row(100) is None and _count(TableTrackTombstones) == 1
        lib.albums[0].pop('statistics')
        lib.files = [{**files[0], 'id': 160}]
        lib.records[0] = {**lib.records[0], 'trackFileId': 160}
        open(lib.lrc(), 'w').write(VERSE)
        lib.sync(monkeypatch)
        row = _row(160)
        assert row.instrumental_override == 'never' and row.lyrics_status == 'available'
        hashes = set(database.execute(select(TableBlacklist.content_hash)
                                      .where(TableBlacklist.lidarrTrackId == 160)).scalars())
        assert hashes == {'hash-a', 'hash-b'}
        assert _count(TableTrackTombstones) == 0

    def test_override_always_restored_as_user_instrumental(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        _set(lyrics_status='instrumental', instrumental_reason='user', instrumental_override='always')
        row = _row()
        from lyrarr.metadata import lyrics_store
        lyrics_store.tombstone_tracks([row])
        database.execute(sync.delete(TableTracks).where(TableTracks.lidarrTrackId == 100))
        lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_status == 'instrumental' and row.instrumental_reason == 'user'

    def test_status_instrumental_never_restored(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        _set(lyrics_status='instrumental', instrumental_reason='consensus', lyrics_source=None,
             instrumental_override='never')
        from lyrarr.metadata import lyrics_store
        lyrics_store.tombstone_tracks([_row()])
        database.execute(sync.delete(TableTracks).where(TableTracks.lidarrTrackId == 100))
        lib.sync(monkeypatch)
        row = _row()
        assert row.lyrics_status == 'missing' and row.instrumental_override == 'never'

    def test_expired_tombstone_not_applied(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        lib.sync(monkeypatch)
        _set(instrumental_override='never')
        from lyrarr.metadata import lyrics_store
        lyrics_store.tombstone_tracks([_row()])
        database.execute(sync.update(TableTrackTombstones).values(removed_at=datetime.now() - timedelta(days=31)))
        database.execute(sync.delete(TableTracks).where(TableTracks.lidarrTrackId == 100))
        lib.sync(monkeypatch)
        assert _row().instrumental_override is None


class TestRunFullSync:
    def test_false_when_lidarr_raises(self, monkeypatch):
        monkeypatch.setattr(lidarr_api, 'get_artists', lambda: (_ for _ in ()).throw(LidarrError('down')))
        assert sync.run_full_sync(timeout_s=1) is False
        assert sync.sync_in_progress() is False

    def test_false_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings.general, 'use_lidarr', False)
        assert sync.run_full_sync(timeout_s=1) is False

    def test_true_and_roots_refreshed(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        _patch_lidarr(monkeypatch, lib.artists, lib.albums, lib.files, lib.records,
                      roots=[str(lib.root) + '/'])
        assert sync.run_full_sync(timeout_s=1) is True
        roots = database.execute(select(TableLibraryRoots)).scalars().all()
        assert [(r.path, r.source) for r in roots] == [(str(lib.root), 'lidarr')]
        assert _row().duration == 200

    def test_false_when_a_track_fetch_fails(self, monkeypatch, tmp_path):
        lib = Lib(tmp_path)
        _patch_lidarr(monkeypatch, lib.artists, lib.albums, lib.files, lib.records)
        monkeypatch.setattr(lidarr_api, 'get_tracks',
                            lambda **k: (_ for _ in ()).throw(LidarrError('timeout')))
        assert sync.run_full_sync(timeout_s=1) is False

    def test_waits_for_running_sync_then_gives_up(self, monkeypatch):
        monkeypatch.setattr(sync, '_sync_running', True)
        assert sync.run_full_sync(timeout_s=0) is False


class TestSignalR:
    def test_parse_servarr_shape(self):
        msg = [{'name': 'album', 'body': {'action': 'updated', 'resource': {'id': 5, 'artistId': 7}}}]
        assert signalr_client.parse_message(msg) == ('album', 'updated', {'id': 5, 'artistId': 7})

    def test_parse_legacy_shape(self):
        msg = {'name': 'artist', 'action': 'deleted', 'body': {'id': 3}}
        assert signalr_client.parse_message(msg) == ('artist', 'deleted', {'id': 3})

    def test_album_delete_resolves_artist_locally(self, monkeypatch):
        database.execute(insert(TableArtists).values(lidarrArtistId=7, name='A', path='/a'))
        database.execute(insert(TableAlbums).values(lidarrAlbumId=5, artistId=7, title='X'))
        requested = []
        monkeypatch.setattr(signalr_client, 'request_artist_sync', requested.append)
        monkeypatch.setattr(signalr_client, 'request_sync', lambda: requested.append('full'))
        monkeypatch.setattr(settings.lidarr, 'sync_on_live', True)
        monkeypatch.setattr(signalr_client, 'event_stream', lambda *a, **k: None)
        client = signalr_client.LidarrSignalRClient()
        client._on_message([{'name': 'album', 'body': {'action': 'deleted', 'resource': {'id': 5}}}])
        client._on_message([{'name': 'queue', 'body': {'action': 'sync'}}])
        assert requested == [7]

    def test_hub_url_includes_messages_path_and_base(self, monkeypatch):
        monkeypatch.setattr(settings.lidarr, 'base_url', '/lidarr/')
        assert signalr_client._hub_url().endswith(f':{settings.lidarr.port}/lidarr/signalr/messages')

    def test_supervisor_retries_until_connected(self, monkeypatch):
        monkeypatch.setattr(settings.lidarr, 'apikey', 'k')
        monkeypatch.setattr(signalr_client, '_BACKOFF_MIN', 0.01)
        monkeypatch.setattr(signalr_client, '_CHECK_INTERVAL', 0.01)
        client = signalr_client.LidarrSignalRClient()
        attempts = []

        def fake_open(stop_event):
            attempts.append(1)
            if len(attempts) >= 3:
                client._connected = True
                stop_event.set()
                return True
            return False
        monkeypatch.setattr(client, '_open', fake_open)
        monkeypatch.setattr(signalr_client.LidarrSignalRClient, 'is_connected',
                            property(lambda self: self._connected))
        import threading
        stop = threading.Event()
        client._supervise(stop)
        assert len(attempts) == 3

    def test_no_signalr_flag_disables_start(self, monkeypatch):
        monkeypatch.setattr(_args, 'no_signalr', True)
        client = signalr_client.LidarrSignalRClient()
        client.start()
        assert client._supervisor is None


def test_api_client_module_exports():
    assert api_client.LidarrError is LidarrError
