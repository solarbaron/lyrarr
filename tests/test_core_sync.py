"""Regression tests for the Lidarr sync (lyrarr/lidarr/*).

Covers: a failed Lidarr request is never treated as "deleted", per-album error
isolation (FOREIGN KEY / stale default profile), propagation of deletions,
carry-over of lyrarr state when Lidarr re-imports a file under a new trackfile
id, recording-id population, and SignalR message parsing / supervision.

Runs against a throwaway SQLite database in a temp config dir.
"""

import os
import sys
import tempfile

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
    TableLyricsVersions,
    TableProfiles,
    TableTracks,
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
    for table in (TableLyricsVersions, TableBlacklist, TableHistory, TableTracks, TableAlbums, TableArtists):
        database.execute(delete(table))
    monkeypatch.setattr(settings.general, 'use_lidarr', True)
    monkeypatch.setattr(settings.lidarr, 'only_monitored', False)
    monkeypatch.setattr(settings.general, 'default_profile_id', None)
    monkeypatch.setattr(sync, 'event_stream', lambda *a, **k: None)
    yield
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


def _patch_lidarr(monkeypatch, artists, albums, files, records=()):
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

        def flaky(album, track_files=None):
            if album['id'] == 10:
                raise RuntimeError('boom')
            return real(album, track_files=track_files)
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
