"""Regression tests for the core API namespaces and the web server.

Covers: Wanted untimed/undetected (500 on a wrong column), album lyrics status
computed from tracks + its filters, cover filter matching 'manual', paging
clamps, artist sorting, profile delete/rename, batch delete / blacklist never
deleting un-archived files, the dashboard aggregate, and server.py's JSON 404,
path traversal guard and base_url index injection.
"""

import os
import sys
import tempfile
import threading

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

from flask import Flask  # noqa: E402
from flask_restx import Api  # noqa: E402
from sqlalchemy import insert  # noqa: E402

from lyrarr.api import albums, artists, bulk, dashboard, profiles, tracks, wanted  # noqa: E402
from lyrarr.app import config  # noqa: E402
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
)
from lyrarr.metadata import lyrics_store  # noqa: E402

init_db()


@pytest.fixture
def client():
    app = Flask(__name__)
    api = Api(app)
    for ns in (albums.api_ns_albums, artists.api_ns_artists, tracks.api_ns_tracks, wanted.api_ns_wanted,
               bulk.api_ns_bulk, profiles.api_ns_profiles, dashboard.api_ns_dashboard):
        api.add_namespace(ns, '/')
    return app.test_client()


@pytest.fixture
def library(tmp_path):
    """Artist 1: album 10 complete, 11 partial, 12 missing, 13 all-blacklisted,
    14 no tracks (unknown). Artist 2: album 20 with an instrumental track."""
    for table in (TableLyricsVersions, TableBlacklist, TableHistory, TableTracks, TableAlbums, TableArtists):
        database.execute(delete(table))
    database.execute(insert(TableArtists).values(lidarrArtistId=1, name='Zed', sortName='Zed', path=str(tmp_path)))
    database.execute(insert(TableArtists).values(lidarrArtistId=2, name='Abba', sortName='', path=str(tmp_path)))
    album_tracks = {
        10: ['available', 'manual'],
        11: ['available', 'missing', 'blacklisted'],
        12: ['missing', 'missing'],
        13: ['blacklisted'],
        14: [],
        20: ['instrumental'],
    }
    tid = 100
    for album_id, states in album_tracks.items():
        artist_id = 2 if album_id == 20 else 1
        database.execute(insert(TableAlbums).values(
            lidarrAlbumId=album_id, artistId=artist_id, title=f'Album {album_id}', path=str(tmp_path),
            cover_status='manual' if album_id == 10 else 'missing', lyrics_status='unknown'))
        for state in states:
            audio = tmp_path / f'{tid}.flac'
            audio.write_bytes(b'x')
            database.execute(insert(TableTracks).values(
                lidarrTrackId=tid, albumId=album_id, artistId=artist_id, title=f'T{tid}', path=str(audio),
                lyrics_status=state, is_synced=False))
            if state in ('available', 'manual'):
                (tmp_path / f'{tid}.lrc').write_text('some lyrics')
            tid += 1
    yield tmp_path
    database.remove()


def _statuses(resp):
    return {a['lidarrAlbumId']: a['lyrics_status'] for a in resp.get_json()['data']}


class TestWanted:
    @pytest.mark.parametrize('tab', ['untimed', 'undetected', 'lyrics', 'covers'])
    def test_tabs_ok(self, client, library, tab):
        r = client.get(f'/wanted/{tab}')
        assert r.status_code == 200, r.get_json()

    def test_untimed_resolves_album_title(self, client, library):
        data = client.get('/wanted/untimed').get_json()['data']
        assert data and all(d['albumTitle'].startswith('Album') for d in data)

    def test_covers_report_computed_lyrics_status(self, client, library):
        data = client.get('/wanted/covers?pageSize=50').get_json()['data']
        assert {d['lidarrAlbumId']: d['lyrics_status'] for d in data}[12] == 'missing'


class TestAlbumLyricsStatus:
    def test_list_status_is_computed(self, client, library):
        assert _statuses(client.get('/albums?pageSize=50')) == {
            10: 'complete', 11: 'partial', 12: 'missing', 13: 'complete', 14: 'unknown', 20: 'complete'}

    @pytest.mark.parametrize('value,expected', [
        ('complete', {10, 13, 20}),
        ('available', {10, 13, 20}),
        ('partial', {11}),
        ('missing', {12}),
        ('incomplete', {11, 12}),
    ])
    def test_filter(self, client, library, value, expected):
        r = client.get(f'/albums?pageSize=50&lyricsStatus={value}')
        assert set(_statuses(r)) == expected
        assert r.get_json()['total'] == len(expected)

    def test_unknown_filter_is_400(self, client, library):
        assert client.get('/albums?lyricsStatus=bogus').status_code == 400

    def test_detail_status(self, client, library):
        assert client.get('/albums/11').get_json()['lyrics_status'] == 'partial'

    def test_cover_available_matches_manual(self, client, library):
        assert set(_statuses(client.get('/albums?coverStatus=available'))) == {10}


class TestPaging:
    def test_page_size_clamped(self, client, library):
        body = client.get('/albums?pageSize=100000&page=-3').get_json()
        assert body['pageSize'] == 500 and body['page'] == 1
        assert client.get('/tracks?pageSize=-5').get_json()['pageSize'] == 1
        assert client.get('/wanted/lyrics?pageSize=9999').get_json()['pageSize'] == 500

    def test_artist_sort(self, client, library):
        names = [a['name'] for a in client.get('/artists?sortBy=name').get_json()['data']]
        assert names == ['Abba', 'Zed']  # blank sortName falls back to name
        names = [a['name'] for a in client.get('/artists?sortBy=name&sortDir=desc').get_json()['data']]
        assert names == ['Zed', 'Abba']


class TestProfiles:
    def test_delete_clears_default_profile_setting(self, client, library, monkeypatch):
        monkeypatch.setattr(config, 'write_config', lambda: None)
        r = client.post('/profiles', json={'name': 'Temp profile'})
        pid = r.get_json()['id']
        monkeypatch.setattr(config.settings.general, 'default_profile_id', pid)
        assert client.delete(f'/profiles/{pid}').status_code == 200
        assert config.settings.general.default_profile_id is None

    def test_rename_to_existing_name_is_409(self, client, library):
        a = client.post('/profiles', json={'name': 'P-one'}).get_json()['id']
        client.post('/profiles', json={'name': 'P-two'})
        try:
            r = client.put(f'/profiles/{a}', json={'name': 'P-two'})
            assert r.status_code == 409
            assert client.put(f'/profiles/{a}', json={'name': '  '}).status_code == 400
        finally:
            database.execute(delete(TableProfiles).where(TableProfiles.name.in_(['P-one', 'P-two'])))


class TestNoUnarchivedDeletes:
    def test_batch_delete_keeps_file_when_archive_fails(self, client, library, monkeypatch):
        monkeypatch.setattr(lyrics_store, '_archive_existing', lambda *a, **k: False)
        r = client.post('/metadata/lyrics/batch-delete', json={'albumIds': [10]})
        assert r.get_json()['deleted'] == 0
        assert (library / '100.lrc').exists()

    def test_batch_delete_archives_and_deletes(self, client, library):
        r = client.post('/metadata/lyrics/batch-delete', json={'albumIds': [10]})
        assert r.get_json()['deleted'] == 2
        assert not (library / '100.lrc').exists()
        assert database.execute(select(TableLyricsVersions.lidarrTrackId)).scalars().all()

    def test_concurrent_batch_delete_is_409(self, client, library):
        assert bulk._batch_delete_lock.acquire()
        try:
            r = client.post('/metadata/lyrics/batch-delete', json={'albumIds': [10]})
            assert r.status_code == 409
        finally:
            bulk._batch_delete_lock.release()

    def test_bad_ids_are_400(self, client, library):
        assert client.post('/metadata/lyrics/batch-delete', json={'albumIds': 5}).status_code == 400

    def test_blacklist_keeps_file_when_archive_fails(self, client, library, monkeypatch):
        monkeypatch.setattr(lyrics_store, '_archive_existing', lambda *a, **k: False)
        r = client.put('/tracks/100', json={'lyrics_status': 'blacklisted'})
        assert r.status_code == 500
        assert (library / '100.lrc').exists()
        row = database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == 100)).scalars().one()
        assert row.lyrics_status == 'available'

    def test_blacklist_archives_then_deletes(self, client, library):
        r = client.put('/tracks/100', json={'lyrics_status': 'blacklisted'})
        assert r.status_code == 200 and r.get_json()['lyrics_status'] == 'blacklisted'
        assert r.get_json()['track']['lyrics_status'] == 'blacklisted'
        assert not (library / '100.lrc').exists()
        assert database.execute(select(TableLyricsVersions.lidarrTrackId)).scalars().all() == [100]
        v = database.execute(select(TableLyricsVersions)).scalars().one()
        assert v.replaced_by == 'blacklist'

    def test_instrumental_mode_deletes_only_non_lyrics(self, client, library):
        (library / '108.lrc').write_text('real words on line one\nreal words on line two\nmore of them here')
        r = client.post('/metadata/lyrics/batch-delete', json={'albumIds': [20], 'mode': 'instrumental'})
        body = r.get_json()
        assert body['deleted'] == 0 and body['unflagged'] == 1
        assert (library / '108.lrc').exists()


def test_dashboard_orders_by_outstanding_work(client, library):
    rows = client.get('/dashboard/stats').get_json()['artistCompletion']
    assert [r['id'] for r in rows] == [1, 2]
    zed = rows[0]
    assert zed['totalAlbums'] == 5 and zed['coversDone'] == 1
    assert zed['totalTracks'] == 6 and zed['lyricsDone'] == 3


# ---------------------------------------------------------------------------
# server.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def server_module():
    from lyrarr.app import server
    return server


@pytest.fixture
def web(server_module, tmp_path, monkeypatch):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'index-abc.js').write_text('console.log(1)')
    (tmp_path / 'index.html').write_text(
        '<!DOCTYPE html><html><head><link rel="icon" href="/lyrarr.svg" />'
        '<script type="module" src="/assets/index-abc.js"></script></head><body></body></html>')
    monkeypatch.setattr(server_module, 'frontend_dir', str(tmp_path))
    server_module._index_cache.update(mtime=None, html=None)
    for limiter in server_module.app.extensions.get('lyrarr_limiters', {}).values():
        if hasattr(limiter, '_requests'):
            limiter._requests.clear()
    yield server_module.app.test_client()
    server_module._index_cache.update(mtime=None, html=None)


class TestServer:
    def test_unknown_api_path_is_json_404(self, web, server_module):
        r = web.get(server_module.api_prefix + '/does-not-exist')
        assert r.status_code == 404 and r.is_json

    def test_spa_route_serves_index_with_base_url_global(self, web, server_module):
        r = web.get(server_module.base_url + '/artists/5')
        assert r.status_code == 200
        assert b'window.__LYRARR_BASE_URL__=' in r.data

    @pytest.mark.parametrize('path', ['/..%2f..%2fetc%2fpasswd', '/%2Fetc%2Fpasswd', '/../server.py'])
    def test_traversal_never_reads_outside(self, web, server_module, path):
        r = web.get(server_module.base_url + path)
        assert b'root:' not in r.data
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert b'__LYRARR_BASE_URL__' in r.data  # the SPA shell, not a file

    def test_index_rewrite_for_base_url(self, server_module, tmp_path, monkeypatch):
        (tmp_path / 'index.html').write_text(
            '<html><head><script src="/assets/a.js"></script><a href="//cdn.x/y"></a></head></html>')
        monkeypatch.setattr(server_module, 'frontend_dir', str(tmp_path))
        monkeypatch.setattr(server_module, 'base_url', '/lyrarr')
        server_module._index_cache.update(mtime=None, html=None)
        try:
            with server_module.app.test_request_context('/'):
                html = server_module._render_index().get_data(as_text=True)
        finally:
            server_module._index_cache.update(mtime=None, html=None)
        assert 'window.__LYRARR_BASE_URL__="/lyrarr"' in html
        assert 'src="/lyrarr/assets/a.js"' in html
        assert 'href="//cdn.x/y"' in html

    def test_events_mounted_under_api_prefix(self, server_module):
        rules = {r.rule for r in server_module.app.url_map.iter_rules()}
        assert server_module.api_prefix + '/events' in rules

    def test_importing_server_does_not_bind(self, server_module):
        assert server_module.webserver.server is None


def test_dropped_sse_client_stream_ends():
    from lyrarr.app import event_handler
    q = event_handler.subscribe()
    for _ in range(q.maxsize + 1):
        event_handler.event_stream('test', {'x': 1})
    assert q.closed and q not in event_handler._subscribers
    q2 = event_handler.subscribe()
    t = threading.Thread(target=event_handler.close_all_streams)
    t.start()
    t.join(2)
    assert q2.closed
