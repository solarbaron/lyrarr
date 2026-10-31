"""Upgrading a legacy database (PLAN §7.4 part A, §1.1.4 data migrations).

Builds `<tmp>/db/lyrarr.db` from tests/fixtures/legacy_schema_v1_0.sql (v1.0
schema with TEXT booleans, plus columns added by later versions and synthetic
rows), then runs init_db() twice in a subprocess so the module-level engine
points at that database. Asserts the schema, typed booleans, every data
migration recorded exactly once, and each migration's effect.
"""

import json
import os
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, 'tests', 'fixtures', 'legacy_schema_v1_0.sql')

MIGRATIONS = {
    '2026-10-app-version-2.0.0',
    '2026-10-stale-lyrics-source',
    '2026-10-hasLyrics-invariant',
    '2026-10-language-source',
    '2026-10-instrumental-title-reason',
    '2026-10-upgrade-backoff-split',
}

NEW_TRACK_COLUMNS = {
    'instrumental_reason', 'instrumental_evidence', 'instrumental_checked_at', 'instrumental_override',
    'instrumental_prev', 'language_source', 'track_artist', 'lyrics_provider', 'lyrics_source_id',
    'lyrics_match_score', 'lyrics_match_json', 'lyrics_content_hash', 'lyrics_replace_requested',
    'lyrics_transient_count', 'lyrics_cooldown_count', 'upgrade_retry_count', 'upgrade_retry_after',
    'lyrics_retry_set_at', 'upgrade_retry_set_at',
}
NEW_TABLES = {'table_audit_runs', 'table_audit_issues', 'table_audit_pins', 'table_audit_ignores',
              'table_data_migrations', 'table_library_roots', 'table_track_tombstones', 'table_lyrics_versions'}


def _init_db_twice(cfg):
    code = ("import sys; sys.argv=['lyrarr','--config',sys.argv[1],'--no-tasks','--no-signalr']; "
            "import lyrarr.app.get_args; from lyrarr.app.database import init_db; init_db(); init_db()")
    env = {**os.environ, 'PYTHONPATH': REPO}
    proc = subprocess.run([sys.executable, '-c', code, str(cfg)], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-4000:]


@pytest.fixture(scope='module')
def upgraded(tmp_path_factory):
    cfg = tmp_path_factory.mktemp('legacy-cfg')
    (cfg / 'db').mkdir()
    db = cfg / 'db' / 'lyrarr.db'
    conn = sqlite3.connect(db)
    with open(FIXTURE, encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    _init_db_twice(cfg)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    yield {'conn': conn, 'cfg': cfg, 'db': db}
    conn.close()


def _track(conn, track_id):
    return conn.execute('SELECT * FROM table_tracks WHERE lidarrTrackId = ?', (track_id,)).fetchone()


def _columns(conn, table):
    return {r['name']: r['type'].upper() for r in conn.execute(f'PRAGMA table_info({table})')}


def test_columns_and_tables_exist(upgraded):
    conn = upgraded['conn']
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert NEW_TABLES <= tables
    assert NEW_TRACK_COLUMNS <= set(_columns(conn, 'table_tracks'))
    assert 'details' in _columns(conn, 'table_history')
    assert 'replaced_by' in _columns(conn, 'table_lyrics_versions')
    assert 'content_hash' in _columns(conn, 'table_blacklist')
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {'ix_audit_issue_run_check', 'ix_audit_pin_kind_ref'} <= indexes


def test_booleans_are_typed(upgraded):
    conn = upgraded['conn']
    assert _columns(conn, 'table_tracks')['hasLyrics'] == 'BOOLEAN'
    assert _columns(conn, 'table_profiles')['is_default'] == 'BOOLEAN'
    assert {r[0] for r in conn.execute('SELECT typeof(is_default) FROM table_profiles')} == {'integer'}
    assert [r[0] for r in conn.execute('SELECT is_default FROM table_profiles ORDER BY id')] == [1, 1]


def test_migrations_recorded_once(upgraded):
    conn = upgraded['conn']
    rows = conn.execute('SELECT id, applied_at, result FROM table_data_migrations').fetchall()
    assert {r['id'] for r in rows} == MIGRATIONS and len(rows) == len(MIGRATIONS)
    before = {r['id']: r['applied_at'] for r in rows}
    _init_db_twice(upgraded['cfg'])  # a later restart changes nothing
    after = {r['id']: r['applied_at'] for r in conn.execute('SELECT id, applied_at FROM table_data_migrations')}
    assert after == before
    results = {r['id']: json.loads(r['result']) for r in rows}
    assert results['2026-10-instrumental-title-reason'] == {'rows': 1, 'legacy_unknown': 1}


def test_stale_source_cleared(upgraded):
    t = _track(upgraded['conn'], 3)
    assert t['lyrics_status'] == 'missing' and t['lyrics_source'] is None
    assert _track(upgraded['conn'], 4)['lyrics_source'] == 'auto'


def test_instrumental_title_reason(upgraded):
    strong = _track(upgraded['conn'], 1)
    assert strong['instrumental_reason'] == 'title'
    assert json.loads(strong['instrumental_evidence']) == [{'source': 'title', 'kind': 'title'}]
    legacy = _track(upgraded['conn'], 2)
    assert legacy['lyrics_status'] == 'instrumental' and legacy['instrumental_reason'] is None


def test_has_lyrics_invariant(upgraded):
    conn = upgraded['conn']
    assert _track(conn, 5)['hasLyrics'] == 1
    assert _track(conn, 1)['hasLyrics'] == 0
    for track_id in (1, 6):  # instrumental / blacklisted leftovers
        t = _track(conn, track_id)
        assert t['is_synced'] == 0 and t['detected_language'] is None and t['hasLyrics'] == 0


def test_language_source_translated(upgraded):
    assert _track(upgraded['conn'], 7)['language_source'] == 'translated'
    assert _track(upgraded['conn'], 5)['language_source'] is None


def test_upgrade_backoff_moved_and_set_at_null(upgraded):
    conn = upgraded['conn']
    t = _track(conn, 4)
    assert t['upgrade_retry_count'] == 2 and t['upgrade_retry_after'].startswith('2026-11-05')
    assert t['lyrics_retry_count'] == 0 and t['lyrics_retry_after'] is None
    # The missing row keeps its lyrics backoff (S-4 decides about it later).
    m = _track(conn, 3)
    assert m['lyrics_retry_count'] == 4 and m['upgrade_retry_count'] == 0
    # *_set_at are never backfilled: NULL marks backoff written by older versions.
    assert conn.execute('SELECT COUNT(*) FROM table_tracks WHERE lyrics_retry_set_at IS NOT NULL '
                        'OR upgrade_retry_set_at IS NOT NULL').fetchone()[0] == 0
    assert {r[0] for r in conn.execute('SELECT lyrics_transient_count FROM table_tracks')} == {0}
