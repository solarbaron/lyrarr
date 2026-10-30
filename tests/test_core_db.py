"""Regression tests for database startup migrations, logging and health checks.

Covers: legacy TEXT-boolean rebuild recovering from a leftover `__boolfix`
column, model indexes created on existing databases, the in-process Alembic
stamp leaving the app's logging configuration intact, configure_logging not
duplicating handlers, and the Lidarr health URL / auth-disabled warning.
"""

import logging
import os
import sqlite3
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

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import lyrarr.app.database as dbmod  # noqa: E402
from lyrarr.app.config import settings  # noqa: E402

dbmod.init_db()


@pytest.fixture
def legacy_engine(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE table_artists (lidarrArtistId INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
                                    monitored TEXT, monitored__boolfix BOOLEAN);
        CREATE TABLE table_tracks (lidarrTrackId INTEGER PRIMARY KEY, title TEXT, hasLyrics BOOLEAN,
                                   hasLyrics__boolfix BOOLEAN);
        CREATE TABLE table_albums (lidarrAlbumId INTEGER PRIMARY KEY, title TEXT, monitored__boolfix BOOLEAN,
                                   override_prefer_synced TEXT);
        INSERT INTO table_artists VALUES (1, 'A', '/a', '0', NULL), (2, 'B', '/b', 'True', NULL);
        INSERT INTO table_albums VALUES (1, 'X', 1, NULL);
    """)
    conn.commit()
    conn.close()
    engine = create_engine(f'sqlite:///{path}', poolclass=NullPool, isolation_level='AUTOCOMMIT')
    monkeypatch.setattr(dbmod, 'engine', engine)
    yield engine
    engine.dispose()


def _cols(engine, table):
    return {c['name']: str(c['type']).upper() for c in inspect(engine).get_columns(table)}


def test_rebuild_recovers_from_leftover_boolfix_columns(legacy_engine):
    dbmod._rebuild_text_bool_columns()
    artists = _cols(legacy_engine, 'table_artists')
    assert artists['monitored'] == 'BOOLEAN' and 'monitored__boolfix' not in artists
    tracks = _cols(legacy_engine, 'table_tracks')
    assert tracks['hasLyrics'] == 'BOOLEAN' and 'hasLyrics__boolfix' not in tracks
    # Interrupted after DROP but before RENAME: the temp column is renamed back.
    albums = _cols(legacy_engine, 'table_albums')
    assert albums['monitored'] == 'BOOLEAN' and 'monitored__boolfix' not in albums
    with legacy_engine.connect() as conn:
        assert conn.execute(text('SELECT monitored FROM table_artists ORDER BY 1')).scalars().all() == [0, 1]
        assert conn.execute(text('SELECT monitored FROM table_albums')).scalar() == 1
    # Idempotent: a second startup is a no-op.
    dbmod._rebuild_text_bool_columns()
    assert _cols(legacy_engine, 'table_artists')['monitored'] == 'BOOLEAN'


def test_failed_rebuild_rolls_back(legacy_engine, monkeypatch):
    with legacy_engine.connect() as conn:
        conn.exec_driver_sql('ALTER TABLE table_artists DROP COLUMN monitored__boolfix')
        # A view referencing the column makes DROP COLUMN fail mid-rebuild.
        conn.exec_driver_sql('CREATE VIEW v AS SELECT monitored FROM table_artists')
    with pytest.raises(OperationalError):
        dbmod._rebuild_bool_column('table_artists', 'monitored', 'TEXT', False)
    cols = _cols(legacy_engine, 'table_artists')
    assert cols['monitored'] == 'TEXT' and 'monitored__boolfix' not in cols


def test_missing_indexes_are_created_on_existing_db():
    with dbmod.engine.connect() as conn:
        conn.exec_driver_sql('DROP INDEX IF EXISTS ix_table_tracks_albumId')
    dbmod.init_db()
    names = {i['name'] for i in inspect(dbmod.engine).get_indexes('table_tracks')}
    assert {'ix_table_tracks_albumId', 'ix_table_tracks_artistId', 'ix_table_tracks_lyrics_status'} <= names
    assert 'ix_table_history_timestamp' in {i['name'] for i in inspect(dbmod.engine).get_indexes('table_history')}


def test_alembic_stamp_keeps_app_logging():
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    probe = logging.getLogger('lyrarr.test_core_probe')
    with dbmod.engine.connect() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS alembic_version')
    dbmod.init_db()
    assert inspect(dbmod.engine).has_table('alembic_version')  # stamped without alembic.ini
    assert root.handlers == handlers_before
    assert root.level == level_before
    assert not probe.disabled


def test_configure_logging_replaces_implicit_handlers(monkeypatch):
    from lyrarr.app import logger as logger_mod
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        root.addHandler(logging.StreamHandler())  # what an early logging.error() leaves behind
        logger_mod.configure_logging()
        logger_mod.configure_logging()
        kinds = sorted(type(h).__name__ for h in root.handlers)
        assert kinds == ['RotatingFileHandler', 'StreamHandler']
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


@pytest.mark.parametrize('lidarr_base,expected', [
    ('/', 'http://lidarr:8686/api/v1/system/status'),
    ('/lidarr', 'http://lidarr:8686/lidarr/api/v1/system/status'),
    ('/lidarr/', 'http://lidarr:8686/lidarr/api/v1/system/status'),
])
def test_health_url_honours_lidarr_base(monkeypatch, lidarr_base, expected):
    from lyrarr.utilities import health
    seen = []

    class Resp:
        status_code = 200

    monkeypatch.setattr(health.requests, 'get', lambda url, **k: seen.append(url) or Resp())
    monkeypatch.setattr(settings.general, 'use_lidarr', True)
    monkeypatch.setattr(settings.lidarr, 'ip', 'lidarr')
    monkeypatch.setattr(settings.lidarr, 'port', 8686)
    monkeypatch.setattr(settings.lidarr, 'ssl', False)
    monkeypatch.setattr(settings.lidarr, 'base_url', lidarr_base)
    assert health._check_lidarr_health()['healthy']
    assert seen == [expected]


def test_health_warns_when_auth_disabled(monkeypatch):
    from lyrarr.utilities import health
    monkeypatch.setattr(settings.auth, 'type', None)
    assert health._check_auth_health()['healthy'] is False
    monkeypatch.setattr(settings.auth, 'type', 'form')
    assert health._check_auth_health()['healthy'] is True
