"""End-to-end audit against a synthetic legacy library (PLAN §7.4, WS-D).

tests/fixtures/legacy_library.py builds a DB + two temp music roots with the
bad state older versions left behind. The flow: dry run (no changes), apply
safe fixes, idempotency, review repairs incl. replace mode, evidence
protection, an unmounted second root, cancel, the upgrade bootstrap
(durations before requeues, the cleared view), the API, scheduled runs that
never touch backoff, and the replace stagger. Providers are fakes; nothing
touches the network. All lyrics are synthetic.
"""

import hashlib
import os
import threading
import time
from datetime import datetime, timedelta

import pytest

from tests.fixtures import legacy_library as L

if not L.configure('lyrarr-test-audit-e2e-'):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from flask import Flask  # noqa: E402
from flask_restx import Api  # noqa: E402
from sqlalchemy import text  # noqa: E402

import lyrarr.utilities.audit as audit  # noqa: E402
from lyrarr.api import audit as audit_api  # noqa: E402
from lyrarr.api import tracks as tracks_api  # noqa: E402
from lyrarr.api import wanted as wanted_api  # noqa: E402
from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.config import settings  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAuditIssues,
    TableAuditPins,
    TableAuditRuns,
    TableBlacklist,
    TableHistory,
    TableLyricsVersions,
    TableTracks,
    data_migration_applied,
    database,
    delete,
    init_db,
    select,
    update,
)
from lyrarr.lidarr import sync  # noqa: E402
from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.metadata import lyrics_store  # noqa: E402
from lyrarr.utilities.audit import runner  # noqa: E402
from lyrarr.utilities.audit.registry import REGISTRY, get_check  # noqa: E402

init_db()

AUDIT_TABLES = ('table_audit_runs', 'table_audit_issues', 'table_audit_pins', 'table_audit_ignores')
DATA_TABLES = ('table_tracks', 'table_albums', 'table_artists', 'table_history', 'table_lyrics_versions',
               'table_blacklist', 'table_profiles', 'table_library_roots', 'table_data_migrations')


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

class Events(list):
    def of(self, type_):
        return [p for t, p in self if t == type_]


@pytest.fixture
def lib(tmp_path, monkeypatch):
    events = Events()
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    monkeypatch.setattr(w, 'event_stream', lambda type, payload=None: events.append((type, payload)), raising=False)
    sync_calls = []

    def fake_full_sync(timeout_s=1800):
        # "Lidarr" no longer knows the stale duplicate row.
        sync_calls.append('sync')
        database.execute(delete(TableTracks).where(TableTracks.lidarrTrackId == 146))
        return True

    monkeypatch.setattr(sync, 'run_full_sync', fake_full_sync)
    monkeypatch.setattr(settings.general, 'use_lidarr', True)  # S-9's repair is a (faked) Lidarr sync
    monkeypatch.setattr(lyrics_store, 'detect_language', lambda text: 'en')
    library = L.build(tmp_path)
    library.events = events
    library.sync_calls = sync_calls
    yield library
    database.remove()


def run(mode='dry_run', checks=None, **kw):
    run_id = audit.start_run(mode, checks, wait=True, **kw)
    assert run_id is not None
    return database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)).scalars().first()


def issues(run_id, code=None, status=None, mode=None):
    q = select(TableAuditIssues).where(TableAuditIssues.run_id == run_id)
    if code:
        q = q.where(TableAuditIssues.check_id == REGISTRY_BY_CODE[code])
    if status:
        q = q.where(TableAuditIssues.status == status)
    if mode:
        q = q.where(TableAuditIssues.fix_mode == mode)
    return database.execute(q.order_by(TableAuditIssues.id)).scalars().all()


REGISTRY_BY_CODE = {c.spec.code: c.spec.id for c in REGISTRY.values()}
CODE_OF = {v: k for k, v in REGISTRY_BY_CODE.items()}


def by_code(run_id, **kw):
    out = {}
    for i in issues(run_id, **kw):
        out.setdefault(CODE_OF[i.check_id], set()).add(i.entity_id if i.entity_type in ('track', 'album')
                                                       else i.entity_key)
    return out


def track(track_id):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


def db_hash():
    h = hashlib.sha256()
    for table in DATA_TABLES:
        rows = database.execute(text(f'SELECT * FROM {table} ORDER BY 1')).all()
        h.update(repr(rows).encode())
    return h.hexdigest()


def tree_hash(root):
    h = hashlib.sha256()
    for dirpath, _dirnames, filenames in sorted(os.walk(root)):
        h.update(dirpath.encode())
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            with open(p, 'rb') as fh:
                h.update(f.encode() + hashlib.sha256(fh.read()).digest())
            h.update(str(os.stat(p).st_mtime_ns).encode())
    return h.hexdigest()


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def versions(track_id):
    return database.execute(select(TableLyricsVersions).where(TableLyricsVersions.lidarrTrackId == track_id)
                            .order_by(TableLyricsVersions.id)).scalars().all()


def open_auto(run_id):
    return [(CODE_OF[i.check_id], i.entity_id or i.entity_key) for i in issues(run_id, status='open', mode='auto')]


# ---------------------------------------------------------------------------
# 1-3: dry run, apply, idempotency
# ---------------------------------------------------------------------------

def test_dry_run_detects_expected_and_changes_nothing(lib):
    before_db, before_tree = db_hash(), tree_hash(lib.root)
    r = run('dry_run')
    assert r.status == 'done', r.error
    found = by_code(r.id)
    for code, expected in L.EXPECTED_DRY_RUN.items():
        assert found.get(code, set()) == expected, code
    for code, rel in L.EXPECTED_FILE_KEYS.items():
        keys = {k.split(':', 1)[1] for k in found.get(code, set())}
        assert keys == {os.path.join(lib.root, x) for x in rel}, code
    assert not found.get('F-0'), 'both roots are mounted'
    assert db_hash() == before_db
    assert tree_hash(lib.root) == before_tree
    # Bootstrap-only checks never run in a default user run.
    ran = {e['check_id'] for e in r.to_dict()['checks']}
    assert not ran & {'state.backoff_stale', 'state.upgrade_candidates', 'state.duration_ms',
                      'state.content_hash_backfill'}


def test_apply_safe_fixes_then_idempotent(lib):
    manual_lrc = read_bytes(lib.lrc(132))
    instrumental_before = {t.lidarrTrackId for t in database.execute(
        select(TableTracks).where(TableTracks.lyrics_status == 'instrumental')).scalars().all()}

    r = run('apply')
    assert r.status == 'done', r.error
    t = r.to_dict()['totals']
    assert t['fixed'] > 20 and t['failed'] == 0, t

    # Manual files are untouched, except the lossless BOM normalisation (archived).
    assert read_bytes(lib.lrc(132)) == manual_lrc
    assert not read_bytes(lib.lrc(131)).startswith(b'\xef\xbb\xbf')
    assert [v.replaced_by for v in versions(131)] == ['audit:F-3']
    # Every rewritten file has an archive row.
    for tid, code in ((113, 'F-3'), (114, 'F-3'), (116, 'F-5'), (119, 'F-4'), (120, 'F-13'), (121, 'F-8')):
        assert any(v.replaced_by == f'audit:{code}' for v in versions(tid)), tid
    # F-6 confirmed excerpt: archived, blacklisted, removed, missing.
    assert not os.path.exists(lib.lrc(117))
    assert track(117).lyrics_status == 'missing'
    assert database.execute(select(TableBlacklist).where(TableBlacklist.lidarrTrackId == 117)).scalars().first()
    # State fixes.
    assert track(105).lyrics_status == 'available'                 # I-1 adopt
    for tid in (101, 102, 103, 108, 201, 106, 107):
        row = track(tid)
        assert row.lyrics_status == 'missing' and row.instrumental_prev, tid
    assert track(104).instrumental_reason == 'title'
    assert track(152).lyrics_status == 'missing' and track(153).lyrics_status == 'available'
    assert track(111).lyrics_status == 'missing' and os.path.exists(lib.lrc(111))   # F-2 keeps the file
    assert track(142).lyrics_source is None
    assert track(155).lyrics_source == 'manual'
    assert track(143).lyrics_source == 'auto'
    assert track(156).hasLyrics is False and track(156).is_synced is False
    assert not os.path.exists(lib.extra['tmpfile'])
    assert track(146) is None                                      # S-9 full sync
    # No row became instrumental; review issues untouched.
    instrumental_after = {t.lidarrTrackId for t in database.execute(
        select(TableTracks).where(TableTracks.lyrics_status == 'instrumental')).scalars().all()}
    assert instrumental_after <= instrumental_before
    assert track(154).lyrics_status == 'available' and os.path.exists(lib.lrc(154))
    # History action 7 rows exist.
    assert database.execute(select(TableHistory).where(TableHistory.action == 7)).scalars().first()
    # Exactly one instrumental_bulk per instrumental check that changed rows; no per-track events.
    bulk = lib.events.of('instrumental_bulk')
    assert sorted(b['check_id'] for b in bulk) == ['instrumental.legacy_unknown', 'instrumental.weak_title_only']
    assert next(b for b in bulk if b['check_id'] == 'instrumental.legacy_unknown')['cleared'] == 6
    assert lib.events.of('instrumental_classified') == []
    assert lib.events.of('lyrics_audit_start') and lib.events.of('lyrics_audit_complete')

    # 3: a second dry run shows 0 open auto issues; review issues remain.
    r2 = run('dry_run')
    assert open_auto(r2.id) == []
    remaining = by_code(r2.id, status='open')
    for code in ('I-6', 'H-1', 'F-9', 'M-1', 'M-2', 'F-7', 'F-15', 'M-5', 'I-7'):
        assert remaining.get(code), code


# ---------------------------------------------------------------------------
# 4: review repairs, replace mode
# ---------------------------------------------------------------------------

def _apply_ids(parent_id, ids, actions=None):
    return run('apply', parent_run_id=parent_id, issue_ids=ids, actions=actions)


def test_review_repairs_and_replace_mode(lib, monkeypatch):
    dry = run('dry_run')
    i6 = issues(dry.id, 'I-6')[0]
    h1 = issues(dry.id, 'H-1')[0]
    m1 = issues(dry.id, 'M-1')[0]
    old_154 = lyrics_store.read_sidecar(lib.lrc(154)).text
    r = _apply_ids(dry.id, [i6.id, h1.id])
    assert r.status == 'done', r.error
    row = track(154)
    assert row.lyrics_status == 'instrumental' and row.instrumental_reason == 'title'
    assert not os.path.exists(lib.lrc(154))
    hashes = {b.content_hash for b in database.execute(
        select(TableBlacklist).where(TableBlacklist.lidarrTrackId == 154)).scalars().all()}
    assert lyrics_store.content_hash(old_154) in hashes
    v133 = database.execute(select(TableLyricsVersions).where(TableLyricsVersions.id == lib.extra['v133'])
                            ).scalars().first()
    assert lyrics_store.read_sidecar(lib.lrc(133)).text.strip() == v133.content.strip()
    assert track(133).lyrics_source == 'manual'
    statuses = {i.id: i.status for i in issues(dry.id)}
    assert statuses[i6.id] == 'fixed' and statuses[h1.id] == 'fixed'
    # Pins of resolved evidence issues are gone.
    assert not database.execute(select(TableAuditPins).where(TableAuditPins.issue_id == h1.id)).first()

    # 4b: replace mode for the overrunning file.
    before = read_bytes(lib.lrc(122))
    _apply_ids(dry.id, [m1.id])
    row = track(122)
    assert row.lyrics_status == 'available' and row.lyrics_replace_requested is not None
    assert {i.id: i.status for i in issues(dry.id)}[m1.id] == 'pending'
    assert read_bytes(lib.lrc(122)) == before
    third = run('dry_run')
    assert 122 not in by_code(third.id).get('F-1', set())
    assert 122 not in by_code(third.id).get('M-1', set())

    # The worker finds nothing better: file kept, flag cleared, history 'kept'.
    providers = {}
    monkeypatch.setattr(w, '_lyrics_providers', providers)
    monkeypatch.setattr(w.rate_limiter, 'wait', lambda name: None)
    providers['lrclib'] = _FakeProvider([])
    w.download_missing_lyrics(track_ids=[122])
    row = track(122)
    assert row.lyrics_replace_requested is None and read_bytes(lib.lrc(122)) == before
    assert database.execute(select(TableHistory).where(
        TableHistory.lidarrTrackId == 122, TableHistory.details.like('%"replace_result": "kept"%'))).first()
    fourth = run('dry_run')
    m1_again = issues(fourth.id, 'M-1')
    assert [i.entity_id for i in m1_again] == [122]
    assert m1_again[0].fix_mode == 'report' and 'nothing better found' in m1_again[0].title
    # The earlier pending issue is resolved.
    assert {i.id: i.status for i in issues(dry.id)}[m1.id] == 'fixed'

    # Again with an acceptable candidate: replaced, old file archived.
    assert lyrics_store.request_replace(122, 'M-1')
    good = L.synced(L.verse('overrun', 4), start=5, step=10)
    providers['lrclib'] = _FakeProvider([_candidate('lrclib', synced=good, title='Overrun Track',
                                                    artist='Artist One', album='Second Album', duration=60)])
    w.download_missing_lyrics(track_ids=[122])
    assert lyrics_store.read_sidecar(lib.lrc(122)).text.strip() == good.strip()
    assert any(v.content.strip() == before.decode().strip() for v in versions(122))


def test_upgrade_swapped_song_and_sync_overflow(lib):
    dry = run('dry_run')
    m5 = issues(dry.id, 'M-5')[0]
    f15 = issues(dry.id, 'F-15')[0]
    assert m5.fix_action == 'restore_version' and f15.fix_action == 'request_replace'
    swapped = read_bytes(lib.lrc(135))
    assert database.execute(select(TableAuditPins).where(TableAuditPins.issue_id == m5.id)).first()
    r = _apply_ids(dry.id, [m5.id, f15.id])
    assert r.status == 'done', r.error
    # M-5: the archived plain version is back (as a manual save) and the swapped file is archived.
    assert lyrics_store.read_sidecar(lib.lrc(135)).text.strip() == L.verse('rightsong', 8)
    assert track(135).lyrics_source == 'manual' and track(135).is_synced is False
    assert any(v.content.strip() == swapped.decode().strip() for v in versions(135))
    # F-15: re-search requested, file kept.
    assert {i.id: i.status for i in issues(dry.id)}[f15.id] == 'pending'
    assert track(136).lyrics_replace_requested is not None
    # Nothing better found → the next run offers "convert to plain".
    lyrics_store.clear_replace_request(136, result='kept')
    again = run('dry_run', ['F-15'])
    f15b = issues(again.id, 'F-15')[0]
    assert f15b.fix_action == 'convert_plain' and 'nothing better found' in f15b.title
    before = read_bytes(lib.lrc(136))
    _apply_ids(again.id, [f15b.id])
    text_now = lyrics_store.read_sidecar(lib.lrc(136)).text
    assert '[' not in text_now and 'edit line number 1 of the verse' in text_now
    assert track(136).is_synced is False
    assert any(v.content.strip() == before.decode().strip() for v in versions(136))
    assert issues(run('dry_run', ['F-15']).id) == []


class _FakeProvider:
    def __init__(self, results):
        self.results = results
        self.calls = 0

    def search(self, **kwargs):
        self.calls += 1
        return [dict(r) for r in self.results]


def _candidate(provider, plain=None, synced=None, *, title, artist, album, duration, score=0.95):
    return {'plain_lyrics': plain or (lyrics_store_plain(synced) if synced else None), 'synced_lyrics': synced,
            'provider': provider, 'score': score,
            'match_details': {'title_score': 100, 'artist_score': 100, 'duration_diff': 0, 'gate': None,
                              'mb_matched': False},
            'track_name': title, 'artist_name': artist, 'album_name': album, 'duration': duration,
            'strategy': 'exact_album', 'source_id': f'{provider}-{title}'}


def lyrics_store_plain(synced):
    from lyrarr.utilities.audit.repairs import to_plain
    return to_plain(synced)


# ---------------------------------------------------------------------------
# 5: evidence protection
# ---------------------------------------------------------------------------

def test_evidence_survives_trim_and_prune(lib, monkeypatch):
    from lyrarr.utilities import maintenance
    monkeypatch.setattr(sync, 'run_full_sync', lambda timeout_s=1800: False)
    assert audit.run_bootstrap() is not None
    boot = database.execute(select(TableAuditRuns).where(TableAuditRuns.trigger == 'bootstrap')).scalars().first()
    h1 = issues(boot.id, 'H-1', status='open')
    f9 = issues(boot.id, 'F-9', status='open')
    assert h1 and f9
    for tid in (133, 134):
        for i in range(6):
            assert lyrics_store.persist_lyrics(tid, L.verse(f'newer{tid}x{i}', 5), 'lrclib', source='auto',
                                               expect_status=None)
    monkeypatch.setattr(settings.general, 'history_retention_days', 30)
    maintenance._prune_history()
    ids = {v.id for v in versions(133)} | {v.id for v in versions(134)}
    assert lib.extra['v133'] in ids and lib.extra['v134'] in ids
    hist = set(database.execute(select(TableHistory.id)).scalars().all())
    assert {lib.extra['h133_editor'], lib.extra['h133_lrclib']} <= hist
    # Still restorable.
    r = _apply_ids(boot.id, [h1[0].id])
    assert r.status == 'done'
    assert {i.id: i.status for i in issues(boot.id)}[h1[0].id] == 'fixed'
    v = database.execute(select(TableLyricsVersions).where(TableLyricsVersions.id == lib.extra['v133'])
                         ).scalars().first()
    assert lyrics_store.read_sidecar(lib.lrc(133)).text.strip() == v.content.strip()
    assert not database.execute(select(TableAuditPins).where(TableAuditPins.issue_id == h1[0].id)).first()
    # Unpinned: further writes trim normally.
    for i in range(6):
        lyrics_store.persist_lyrics(133, L.verse(f'later{i}', 5), 'lrclib', source='auto', expect_status=None)
    assert len(versions(133)) <= 5 + len(lyrics_store.pinned_version_ids(133))
    assert lib.extra['v133'] not in {v.id for v in versions(133)}


# ---------------------------------------------------------------------------
# 6: unreachable second root
# ---------------------------------------------------------------------------

def test_unreachable_root_then_remount(lib):
    away = lib.music2 + '_away'
    os.rename(lib.music2, away)
    try:
        r = run('apply')
        assert r.status == 'done', r.error
        f0 = issues(r.id, 'F-0')
        assert [i.entity_key for i in f0] == [f'root:{lib.music2}']
        found = by_code(r.id)
        for code, ids in found.items():
            if code.startswith(('F-', 'C-')) and code != 'F-0':
                assert not ids & L.MUSIC2_TRACKS, code
        # music1 fixed as usual.
        assert track(152).lyrics_status == 'missing' and track(153).lyrics_status == 'available'
        # I-1 degraded for the music2 instrumental row: reported only, the flag
        # waits until the root is back (the worker skips the row meanwhile).
        i1 = [i for i in issues(r.id, 'I-1') if i.entity_id == 201][0]
        assert '"degraded": true' in i1.details and i1.status == 'open' and i1.fix_mode == 'report'
        assert track(201).lyrics_status == 'instrumental'
        assert track(203).lyrics_status == 'available'      # never flipped while unreachable
        assert track(202).lyrics_status == 'available'
        assert not os.path.exists(lib.music2)               # nothing created (no makedirs)
        notes = ' '.join(e.get('note') or '' for e in r.to_dict()['checks'])
        assert 'not reachable' in notes
    finally:
        os.rename(away, lib.music2)
    r2 = run('apply')
    assert not issues(r2.id, 'F-0')
    assert track(201).lyrics_status == 'missing'            # cleared once the root is back
    assert track(203).lyrics_status == 'missing'
    assert track(204).lyrics_status == 'available'
    assert track(202).lyrics_status == 'missing'
    r3 = run('dry_run')
    assert open_auto(r3.id) == []


# ---------------------------------------------------------------------------
# 7: cancel
# ---------------------------------------------------------------------------

def test_cancel_keeps_partial_issues(lib, monkeypatch):
    check = get_check('S-1')
    from lyrarr.utilities.audit.registry import Issue

    def slow(ctx):
        for i in range(200):
            if ctx.cancelled:
                return
            time.sleep(0.01)
            yield Issue(entity_type='db', entity_id=None, entity_key=f'slow:{i}', title=f'slow {i}')

    monkeypatch.setattr(check, 'detect', slow)
    run_id = audit.start_run('dry_run', ['S-1'])
    time.sleep(0.3)
    assert audit.cancel_run(run_id)
    _wait_idle()
    r = database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)).scalars().first()
    database.refresh(r)
    assert r.status == 'cancelled'
    assert 0 < len(issues(run_id)) < 200


def _wait_idle(timeout=20):
    deadline = time.time() + timeout
    while runner.is_running() and time.time() < deadline:
        time.sleep(0.02)
    assert not runner.is_running()
    database.remove()


# ---------------------------------------------------------------------------
# 8: bootstrap
# ---------------------------------------------------------------------------

def test_bootstrap_once_with_evidence_and_record(lib, monkeypatch):
    monkeypatch.setattr(sync, 'run_full_sync', lambda timeout_s=1800: False)
    run_id = audit.run_bootstrap()
    assert run_id is not None
    boot = database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)).scalars().first()
    assert boot.status == 'done', boot.error
    entries = boot.to_dict()['checks']
    status = {e['check_id']: e['status'] for e in entries}
    for cid in runner.EVIDENCE_CHECKS:
        assert status[cid] == 'detect_only'
    for code in ('H-1', 'I-7', 'F-9'):
        open_ = issues(run_id, code, status='open')
        assert open_, code
        assert database.execute(select(TableAuditPins).where(TableAuditPins.issue_id == open_[0].id)).first()
    assert data_migration_applied('audit-bootstrap-v1')
    # Phase 2 applied (I-1 cleared, S-4 reset with a stamp, S-15 backfilled).
    assert track(102).lyrics_status == 'missing'
    assert track(141).lyrics_retry_set_at is not None and track(141).lyrics_retry_count == 0
    assert track(149).lyrics_content_hash is not None and track(150).lyrics_source is None
    assert track(151).upgrade_retry_set_at is not None
    assert audit.run_bootstrap() is None                     # second call: no-op


def test_bootstrap_not_recorded_with_unreachable_root(lib, monkeypatch):
    monkeypatch.setattr(sync, 'run_full_sync', lambda timeout_s=1800: False)
    away = lib.music2 + '_away'
    os.rename(lib.music2, away)
    try:
        assert audit.run_bootstrap() is not None
        assert not data_migration_applied('audit-bootstrap-v1')
        assert track(204).lyrics_status == 'missing'         # left for the next tick
    finally:
        os.rename(away, lib.music2)
    assert audit.run_bootstrap() is not None
    assert data_migration_applied('audit-bootstrap-v1')
    assert track(204).lyrics_status == 'available'


def test_bootstrap_fixes_durations_before_requeue(lib, monkeypatch):
    monkeypatch.setattr(sync, 'run_full_sync', lambda timeout_s=1800: False)
    run_id = audit.run_bootstrap()
    assert track(101).duration == 130 and track(141).duration == 245
    order = [e['check_id'] for e in database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)
                                                     ).scalars().first().to_dict()['checks']]
    assert order.index('state.duration_ms') < order.index('instrumental.legacy_unknown')
    assert order.index('state.duration_ms') < order.index('state.backoff_stale')


def test_bootstrap_full_sync_runs_before_any_requeue(lib, monkeypatch):
    log = []

    def fake_sync(timeout_s=1800):
        log.append('sync')
        database.execute(update(TableTracks).where(TableTracks.duration > 10800)
                         .values(duration=TableTracks.duration / 1000))
        return True

    real_clear = lyrics_store.clear_instrumental

    def logged_clear(*a, **k):
        log.append('clear')
        return real_clear(*a, **k)

    monkeypatch.setattr(sync, 'run_full_sync', fake_sync)
    monkeypatch.setattr(lyrics_store, 'clear_instrumental', logged_clear)
    run_id = audit.run_bootstrap()
    assert log and log[0] == 'sync' and 'clear' in log
    entries = database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)
                               ).scalars().first().to_dict()['checks']
    assert next(e for e in entries if e['check_id'] == 'phase0.full_sync')['synced'] is True
    assert next(e for e in entries if e['check_id'] == 'state.duration_ms')['found'] == 0


def _client():
    app = Flask(__name__)
    api = Api(app)
    for ns in (audit_api.api_ns_audit, tracks_api.api_ns_tracks, wanted_api.api_ns_wanted):
        api.add_namespace(ns, '/')
    return app.test_client()


def test_bootstrap_cleared_view_and_actions(lib, monkeypatch):
    monkeypatch.setattr(sync, 'run_full_sync', lambda timeout_s=1800: False)
    audit.run_bootstrap()
    c = _client()
    body = c.get('/wanted/instrumental?view=cleared&pageSize=100').get_json()
    rows = {r['lidarrTrackId']: r for r in body['data']}
    assert {101, 102, 103, 108, 201, 106, 107} <= set(rows)
    assert rows[102]['instrumental_prev']['why'] == 'legacy-unverified'
    assert rows[106]['instrumental_prev']['why'] == 'weak-title'
    assert rows[102]['albumTitle'] == 'First Album'
    assert c.get('/wanted/stats').get_json()['instrumental_cleared'] == body['total']
    assert c.get('/audit/summary').get_json()['bootstrap']['instrumental_cleared'] >= 6

    r = c.post('/tracks/instrumental', json={'trackIds': [102], 'action': 'confirm'})
    assert r.status_code == 200 and r.get_json()['updated'] == 1
    row = track(102)
    assert (row.lyrics_status, row.instrumental_reason, row.instrumental_override, row.instrumental_prev) == \
        ('instrumental', 'user', 'always', None)
    # A cleared row that now has lyrics the user saved.
    with open(lib.lrc(103), 'w', encoding='utf-8') as f:
        f.write(L.verse('mine'))
    database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == 103)
                     .values(lyrics_status='available', lyrics_source='manual', hasLyrics=True))
    r = c.post('/tracks/instrumental', json={'trackIds': [103], 'action': 'confirm'}).get_json()
    assert r['skipped'] == [{'trackId': 103, 'reason': 'user_lyrics'}] and r['updated'] == 0
    assert track(103).lyrics_status == 'available' and os.path.exists(lib.lrc(103))
    r = c.post('/tracks/instrumental', json={'trackIds': [103], 'action': 'confirm', 'force': True}).get_json()
    assert r['updated'] == 1 and not os.path.exists(lib.lrc(103))
    assert any(v.replaced_by == 'user-instrumental' for v in versions(103))
    r = c.post('/tracks/instrumental', json={'trackIds': [101], 'action': 'dismiss_cleared'}).get_json()
    assert r['updated'] == 1 and track(101).lyrics_status == 'missing'
    left = {x['lidarrTrackId'] for x in c.get('/wanted/instrumental?view=cleared&pageSize=100').get_json()['data']}
    assert not left & {101, 102, 103}


# ---------------------------------------------------------------------------
# 9: API
# ---------------------------------------------------------------------------

def test_api_runs_apply_ignore(lib):
    c = _client()
    r = c.post('/audit/runs', json={'mode': 'dry_run'})
    assert r.status_code == 202
    dry_id = r.get_json()['run_id']
    _wait_idle()
    assert c.post('/audit/runs', json={'mode': 'dry_run', 'checks': ['nope']}).status_code == 400
    assert runner._run_lock.acquire(blocking=False)
    try:
        assert c.post('/audit/runs', json={'mode': 'dry_run'}).status_code == 409
    finally:
        runner._run_lock.release()

    page = c.get(f'/audit/runs/{dry_id}/issues?pageSize=5&page=2').get_json()
    assert page['pageSize'] == 5 and page['page'] == 2 and len(page['data']) == 5 and page['total'] > 30
    assert {'links', 'fix', 'ignored', 'check'} <= set(page['data'][0])
    assert c.get('/audit/checks').get_json()['checks']
    assert c.get(f'/audit/runs/{dry_id}').get_json()['status'] == 'done'
    assert c.get('/audit/runs/latest?mode=dry_run').get_json()['id'] == dry_id

    s1 = issues(dry_id, 'S-1')[0]
    assert c.post('/audit/ignore', json={'issueIds': [s1.id]}).status_code == 200
    r = c.post('/audit/runs', json={'mode': 'dry_run', 'checks': ['S-1']})
    _wait_idle()
    assert [i.status for i in issues(r.get_json()['run_id'], 'S-1')] == ['ignored']
    assert c.post('/audit/unignore', json={'checkId': 'S-1', 'entityKey': s1.entity_key}).get_json()['unignored'] == 1

    # checks → only auto issues of those checks; issueIds → review issues too.
    r = c.post(f'/audit/runs/{dry_id}/apply', json={'checks': ['state.stale_lyrics_source', 'I-6']})
    assert r.status_code == 202
    _wait_idle()
    assert track(142).lyrics_source is None
    assert track(154).lyrics_status == 'available'
    i6 = issues(dry_id, 'I-6')[0]
    r = c.post(f'/audit/runs/{dry_id}/apply', json={'issueIds': [i6.id]})
    _wait_idle()
    assert track(154).lyrics_status == 'instrumental'
    assert c.post(f'/audit/runs/{dry_id}/cancel', json={}).get_json() == {'cancelled': False}
    assert c.get('/audit/summary').get_json()['latest'] is not None


# ---------------------------------------------------------------------------
# 10: scheduled runs never touch backoff
# ---------------------------------------------------------------------------

BACKOFF_COLS = ('lyrics_retry_count', 'lyrics_retry_after', 'lyrics_retry_set_at', 'lyrics_transient_count',
                'lyrics_cooldown_count', 'upgrade_retry_count', 'upgrade_retry_after', 'upgrade_retry_set_at')


def _backoff_snapshot():
    cols = ', '.join(BACKOFF_COLS)
    return database.execute(text(f'SELECT lidarrTrackId, {cols} FROM table_tracks ORDER BY 1')).all()


def test_scheduled_runs_never_touch_backoff(lib, monkeypatch):
    monkeypatch.setattr(sync, 'run_full_sync', lambda timeout_s=1800: False)
    audit.run_bootstrap()
    run('apply')  # the user applies the remaining safe fixes (status changes reset backoff, §3.6)
    # Worker activity after the upgrade writes new backoff.
    for tid in (101, 102, 109):
        w._defer_track(track(tid), 'normal')
    lyrics_store.record_instrumental_hint(103, [{'source': 'lrclib', 'kind': 'flag', 'anchor': True}],
                                          emit_event=False)
    w._defer_track(track(103), 'hint')
    w._defer_track(track(108), 'cooldown')
    database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == 144).values(
        upgrade_retry_count=1, upgrade_retry_after=datetime.now() + timedelta(days=3),
        upgrade_retry_set_at=datetime.now()))
    snap = _backoff_snapshot()
    monkeypatch.setattr(settings.audit, 'scheduled_mode', 'apply_safe')
    ids = []
    for _ in range(2):
        last = audit.run_scheduled()
        ids.append(last)
        assert _backoff_snapshot() == snap
    for rid in ids:
        r = database.execute(select(TableAuditRuns).where(TableAuditRuns.id == rid)).scalars().first()
        ran = {e['check_id'] for e in r.to_dict()['checks']}
        assert 'state.backoff_stale' not in ran and 'state.upgrade_candidates' not in ran
        parent = r.parent_run_id
        if parent:
            pr = database.execute(select(TableAuditRuns).where(TableAuditRuns.id == parent)).scalars().first()
            assert pr.trigger == 'scheduled'
            assert not {e['check_id'] for e in pr.to_dict()['checks']} & {'state.backoff_stale',
                                                                          'state.upgrade_candidates'}
    explicit = run('dry_run', ['S-4', 'S-5'])
    assert issues(explicit.id) == []


# ---------------------------------------------------------------------------
# 11: replace stagger and the per-pass cap
# ---------------------------------------------------------------------------

def test_replace_stagger_and_per_pass_cap(lib, monkeypatch):
    d = os.path.join(lib.music1, 'Artist One', 'Bulk Album')
    L.add_album(30, 1, 'Bulk Album', d)
    ids = list(range(3001, 3151))
    for tid in ids:
        L.add_track(tid, d, 30, 1, f'Bulk {tid}', status='available', is_synced=True, duration=30,
                    lrc=L.synced(L.verse(f'bulk{tid}', 6), start=10, step=10))
    dry = run('dry_run', ['M-1'])
    m1 = [i for i in issues(dry.id, 'M-1') if i.entity_id in set(ids)]
    assert len(m1) == 150
    start = datetime.now()
    r = _apply_ids(dry.id, [i.id for i in m1])
    assert r.status == 'done', r.error
    rows = database.execute(select(TableTracks).where(TableTracks.lidarrTrackId.in_(ids))).scalars().all()
    assert all(x.lyrics_replace_requested is not None for x in rows)
    afters = [x.lyrics_retry_after for x in rows]
    assert all(a is not None and start < a <= start + timedelta(hours=12, minutes=1) for a in afters)
    assert len(set(afters)) > 100
    # Everything due: the worker takes at most REPLACE_PER_PASS replace-mode rows per pass.
    database.execute(update(TableTracks).where(TableTracks.lidarrTrackId.in_(ids))
                     .values(lyrics_retry_after=datetime.now() - timedelta(minutes=1)))
    fake = _FakeProvider([])
    monkeypatch.setattr(w, '_lyrics_providers', {'lrclib': fake})
    monkeypatch.setattr(w.rate_limiter, 'wait', lambda name: None)
    database.execute(update(TableTracks).where(TableTracks.lyrics_status == 'missing')
                     .values(lyrics_retry_after=datetime.now() + timedelta(days=30)))
    w.download_missing_lyrics()
    assert fake.calls == w.REPLACE_PER_PASS == 50


def test_threads_cleaned_up():
    assert not any(t.name.startswith('audit-run-') and t.is_alive() for t in threading.enumerate())
