"""Regression tests for database startup migrations, logging and health checks.

Covers: legacy TEXT-boolean rebuild recovering from a leftover `__boolfix`
column, model indexes created on existing databases, the in-process Alembic
stamp leaving the app's logging configuration intact, configure_logging not
duplicating handlers, and the Lidarr health URL / auth-disabled warning.
Also the 1.7 additions: legacy_non_download_filter, JSON columns in to_dict,
data-migration bookkeeping, the FRONTEND_EVENTS_V2 gate and emit_throttled,
audit settings, single default profile after a restore, the dashboard lyrics
series and per-root library health.
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


# ---------------------------------------------------------------------------
# 1.7 schema helpers, events, settings, backup restore, dashboard series
# ---------------------------------------------------------------------------

def _clean_history():
    dbmod.database.execute(dbmod.delete(dbmod.TableHistory))


def test_legacy_non_download_filter():
    from sqlalchemy import insert
    _clean_history()
    rows = [
        (1, 'Downloaded lyrics for A', 'lrclib'),
        (1, 'Published lyrics for B', 'lrclib'),         # pre-1.7 publish
        (1, 'Downloaded lyrics for C', 'editor'),        # pre-1.7 manual save
        (1, 'Downloaded lyrics for D', 'batch-translate-de'),
        (2, 'Upgraded lyrics for E', 'netease'),
        (1, 'Downloaded lyrics for F', None),
        (1, 'Downloaded lyrics for G', 'upload'),
    ]
    for action, desc, provider in rows:
        dbmod.database.execute(insert(dbmod.TableHistory).values(
            action=action, description=desc, provider=provider, metadata_type='lyrics'))
    kept = dbmod.database.execute(
        dbmod.select(dbmod.TableHistory.description)
        .where(dbmod.TableHistory.action.in_((1, 2)), dbmod.legacy_non_download_filter())
        .order_by(dbmod.TableHistory.id)
    ).scalars().all()
    assert kept == ['Downloaded lyrics for A', 'Upgraded lyrics for E', 'Downloaded lyrics for F']
    _clean_history()


def test_history_constants():
    assert (dbmod.HISTORY_ACTION_DOWNLOAD, dbmod.HISTORY_ACTION_UPGRADE, dbmod.HISTORY_ACTION_DELETE,
            dbmod.HISTORY_ACTION_MANUAL, dbmod.HISTORY_ACTION_PUBLISH, dbmod.HISTORY_ACTION_CLASSIFY,
            dbmod.HISTORY_ACTION_AUDIT_REPAIR) == (1, 2, 3, 4, 5, 6, 7)
    assert 'instrumental' in dbmod.LYRICS_STATUSES and 'generated' in dbmod.LYRICS_SOURCES


def test_to_dict_parses_json_and_tolerates_garbage():
    t = dbmod.TableTracks(lidarrTrackId=1, title='x', instrumental_evidence='[{"source": "title"}]',
                          instrumental_prev='{not json', lyrics_match_json=None)
    d = t.to_dict()
    assert d['instrumental_evidence'] == [{'source': 'title'}]
    assert d['instrumental_prev'] is None and d['lyrics_match_json'] is None
    run = dbmod.TableAuditRuns(id=1, totals='{"issues": 3}', checks='oops')
    assert run.to_dict()['totals'] == {'issues': 3} and run.to_dict()['checks'] is None


def test_record_data_migration_idempotent():
    dbmod.record_data_migration('test-only-marker', {'n': 1})
    dbmod.record_data_migration('test-only-marker', {'n': 2})
    assert dbmod.data_migration_applied('test-only-marker') is not None
    row = dbmod.database.execute(dbmod.select(dbmod.TableDataMigrations)
                                 .where(dbmod.TableDataMigrations.id == 'test-only-marker')).scalars().one()
    assert row.to_dict()['result'] == {'n': 1}
    dbmod.database.execute(dbmod.delete(dbmod.TableDataMigrations)
                           .where(dbmod.TableDataMigrations.id == 'test-only-marker'))


def test_events_gate_and_throttle(monkeypatch):
    from lyrarr.app import event_handler as eh
    sent = []
    q = eh.subscribe(last_event_id=eh._next_id)  # no replay of earlier tests' events
    try:
        monkeypatch.setattr(eh, 'FRONTEND_EVENTS_V2', False)
        for t in ('lyrics_audit_progress', 'instrumental_classified', 'download_scope', 'download_item'):
            assert t in eh._NO_REPLAY_TYPES
        eh.event_stream('download_item', {'title': 'x'})
        assert eh.emit_throttled('lyrics_audit_progress', {'current': 1}, key='k', force=True) is False
        assert q.empty()
        monkeypatch.setattr(eh, 'FRONTEND_EVENTS_V2', True)
        monkeypatch.setattr(eh, '_throttle_last', {})
        assert eh.emit_throttled('lyrics_audit_progress', {'current': 1}, key='k', force=True)
        assert not eh.emit_throttled('lyrics_audit_progress', {'current': 2}, key='k')
        assert eh.emit_throttled('lyrics_audit_progress', {'current': 3}, key='k', force=True)
        assert eh.emit_throttled('lyrics_audit_progress', {'current': 1}, key='other')
        while not q.empty():
            sent.append(q.get_nowait())
        assert len(sent) == 3
    finally:
        eh.unsubscribe(q)


def test_audit_settings_defaults_and_schedule_paths():
    from lyrarr.app import config
    assert settings.audit.schedule == 'off'
    assert settings.audit.schedule_day == 6 and settings.audit.schedule_hour == 3
    assert settings.audit.scheduled_mode == 'dry_run'
    assert settings.audit.auto_fix_on_upgrade is True
    assert settings.audit.scheduled_include_file_scan is True
    assert settings.audit.network_max_tracks == 200 and settings.audit.report_retention == 10
    assert {'audit.schedule', 'audit.schedule_day', 'audit.schedule_hour'} <= config._schedule_paths
    with pytest.raises(config.SettingsError):
        config._validate_value('audit.network_max_tracks', 9999)
    with pytest.raises(config.SettingsError):
        config._validate_value('audit.schedule', 'hourly')


def _api_client(*namespaces):
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    api = Api(app)
    for ns in namespaces:
        api.add_namespace(ns, '/')
    return app.test_client()


def test_restore_leaves_exactly_one_default():
    from sqlalchemy import insert

    from lyrarr.api import backup
    P = dbmod.TableProfiles
    dbmod.database.execute(dbmod.update(dbmod.TableArtists).values(profileId=None))
    dbmod.database.execute(dbmod.update(dbmod.TableAlbums).values(profileId=None))
    dbmod.database.execute(dbmod.delete(P))
    dbmod.database.execute(insert(P).values(name='Default', is_default=True))
    dbmod.database.execute(insert(P).values(name='Other', is_default=False))
    client = _api_client(backup.api_ns_backup)
    r = client.post('/system/restore', json={'version': 1, 'profiles': [
        {'name': 'Other', 'is_default': True}, {'name': 'Imported', 'is_default': True}]})
    assert r.status_code == 200, r.get_json()
    dbmod.database.remove()
    defaults = dbmod.database.execute(dbmod.select(P.name).where(P.is_default == True)).scalars().all()  # noqa: E712
    assert defaults == ['Imported']
    # A restore without any default keeps the existing one.
    r = client.post('/system/restore', json={'version': 1, 'profiles': [{'name': 'Third'}]})
    dbmod.database.remove()
    defaults = dbmod.database.execute(dbmod.select(P.name).where(P.is_default == True)).scalars().all()  # noqa: E712
    assert defaults == ['Imported']


def test_dashboard_lyrics_series_counts_acquired_only():
    from datetime import datetime

    from sqlalchemy import insert

    from lyrarr.api import dashboard
    _clean_history()
    now = datetime.now()
    for action, desc in ((1, 'Downloaded lyrics for A'), (2, 'Upgraded lyrics for B'), (4, 'Saved lyrics for C'),
                         (1, 'Published lyrics for D'), (5, 'Published lyrics for E'),
                         (6, 'Classified instrumental (title): F'), (7, 'Audit: x'), (3, 'Deleted lyrics')):
        dbmod.database.execute(insert(dbmod.TableHistory).values(
            action=action, description=desc, metadata_type='lyrics', timestamp=now))
    dbmod.database.execute(insert(dbmod.TableHistory).values(
        action=4, description='Uploaded custom cover art', metadata_type='cover', timestamp=now))
    client = _api_client(dashboard.api_ns_dashboard)
    today = client.get('/dashboard/stats').get_json()['downloadHistory'][-1]
    assert today['lyrics'] == 3 and today['covers'] == 1
    _clean_history()


def test_health_reports_each_unreachable_root(monkeypatch, tmp_path):
    from lyrarr.metadata import lyrics_store
    from lyrarr.utilities import health
    events = []
    import lyrarr.app.event_handler as eh
    monkeypatch.setattr(eh, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    monkeypatch.setattr(health, '_last_state', {})
    up = lyrics_store.RootStatus(str(tmp_path / 'm1'), True, True, 3, 0, 0.0, None)
    down = lyrics_store.RootStatus(str(tmp_path / 'm2'), False, False, 3, 3, 1.0, None)
    reach = lyrics_store.Reachability({up.root: up, down.root: down})
    out = health._check_library_roots(reach)
    assert list(out) == [f'library:{down.root}'] and out[f'library:{down.root}']['healthy'] is False
    assert len(events) == 1 and down.root in events[0][1]['message']
    health._check_library_roots(reach)  # unchanged: no second alert
    assert len(events) == 1
    up2 = down._replace(ok=True, exists=True)
    assert health._check_library_roots(lyrics_store.Reachability({down.root: up2})) == {}
    assert len(events) == 2 and events[-1][1]['healthy'] is True
    # A root that isn't mounted but has no tracks under it raises nothing.
    unused = lyrics_store.RootStatus(str(tmp_path / 'm3'), False, False, 0, 0, 0.0, None)
    assert health._check_library_roots(lyrics_store.Reachability({unused.root: unused})) == {}
    assert len(events) == 2
