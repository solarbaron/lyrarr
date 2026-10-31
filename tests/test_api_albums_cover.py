"""Cover upload (api/albums.py) and the cover_fetch disk helpers.

The upload never creates directories: an unreachable album folder or an album
that points at its artist folder answers 409 with a readable message; the
saved file's extension follows the real image type.
"""

import io
import os
import sys
import tempfile

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-cover-'), '--no-tasks', '--no-signalr']
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

from lyrarr.api import albums  # noqa: E402
from lyrarr.app.config import settings  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableHistory,
    TableTracks,
    database,
    delete,
    init_db,
    select,
)
from lyrarr.metadata.cover_fetch import (  # noqa: E402
    COVER_REASONS,
    album_dir_writable,
    find_cover_files,
)

init_db()

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings.metadata.covers, 'folder_art_filename', 'cover')
    app = Flask(__name__)
    api = Api(app)
    api.add_namespace(albums.api_ns_albums, '/')
    return app.test_client()


@pytest.fixture
def lib(tmp_path):
    for table in (TableHistory, TableTracks, TableAlbums, TableArtists):
        database.execute(delete(table))
    artist_dir = tmp_path / 'Artist'
    album_dir = artist_dir / 'Album'
    album_dir.mkdir(parents=True)
    database.execute(insert(TableArtists).values(lidarrArtistId=1, name='A', path=str(artist_dir)))
    database.execute(insert(TableAlbums).values(lidarrAlbumId=10, artistId=1, title='Album', path=str(album_dir),
                                                cover_status='missing'))
    database.execute(insert(TableAlbums).values(lidarrAlbumId=11, artistId=1, title='Gone',
                                                path=str(tmp_path / 'Unmounted' / 'Gone'), cover_status='missing'))
    database.execute(insert(TableAlbums).values(lidarrAlbumId=12, artistId=1, title='Loose', path=str(artist_dir),
                                                cover_status='missing'))
    database.execute(insert(TableAlbums).values(lidarrAlbumId=13, artistId=1, title='Nowhere', path='',
                                                cover_status='missing'))
    yield {'tmp': tmp_path, 'album_dir': album_dir, 'artist_dir': artist_dir}
    for table in (TableHistory, TableTracks, TableAlbums, TableArtists):
        database.execute(delete(table))
    database.remove()


def _upload(client, album_id, data, name='cover.jpg'):
    return client.post(f'/albums/{album_id}/upload-cover', data={'file': (io.BytesIO(data), name)},
                       content_type='multipart/form-data')


def _album(album_id):
    database.remove()
    return database.execute(select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)).scalars().one()


def test_png_named_jpg_saved_as_png(client, lib):
    r = _upload(client, 10, PNG, 'photo.jpg')
    assert r.status_code == 200, r.get_json()
    assert os.path.isfile(lib['album_dir'] / 'cover.png')
    assert not os.path.exists(lib['album_dir'] / 'cover.jpg')
    assert _album(10).cover_status == 'available'
    h = database.execute(select(TableHistory)).scalars().one()
    assert h.metadata_type == 'cover' and h.action == 4


def test_missing_album_dir_409_and_no_dir_created(client, lib):
    r = _upload(client, 11, JPEG)
    assert r.status_code == 409
    body = r.get_json()
    assert body['code'] == 'album_dir_missing'
    assert body['message'] == COVER_REASONS['album_dir_missing'].format(path=str(lib['tmp'] / 'Unmounted' / 'Gone'))
    assert not (lib['tmp'] / 'Unmounted').exists()
    assert _album(11).cover_status == 'missing'


def test_artist_root_album_409(client, lib):
    r = _upload(client, 12, JPEG)
    assert r.status_code == 409 and r.get_json()['code'] == 'artist_root'
    assert not os.path.exists(lib['artist_dir'] / 'cover.jpg')


def test_not_an_image_400(client, lib):
    r = _upload(client, 10, b'<html>not an image</html>', 'cover.png')
    assert r.status_code == 400 and r.get_json()['code'] == 'not_image'
    assert find_cover_files(str(lib['album_dir'])) == []


def test_album_dir_writable_reasons(lib):
    assert album_dir_writable(_album(10)) == (True, None)
    assert album_dir_writable(_album(11)) == (False, 'album_dir_missing')
    assert album_dir_writable(_album(12)) == (False, 'artist_root')
    assert album_dir_writable(_album(13)) == (False, 'no_path')
    assert album_dir_writable(_album(12), artist_path='/elsewhere') == (True, None)


def test_find_cover_files(tmp_path):
    for name in ('Folder.JPG', 'front.webp', 'art.png', 'cover.txt', 'albumart.gif'):
        (tmp_path / name).write_bytes(b'x')
    found = sorted(os.path.basename(p) for p in find_cover_files(str(tmp_path), 'art'))
    assert found == ['Folder.JPG', 'albumart.gif', 'art.png', 'front.webp']
    assert find_cover_files(str(tmp_path / 'nope')) == []
