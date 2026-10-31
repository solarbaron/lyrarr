"""Audit runner behaviour (PLAN §4.2; WS-D): one run at a time, cancel and
resume, the pins lifecycle, retention, scheduled-run check selection and the
replace stagger. Providers are fakes; all lyrics are synthetic."""

import os
import time
from datetime import datetime, timedelta

import pytest

from tests.fixtures import legacy_library as L

if not L.configure('lyrarr-test-audit-runner-'):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

import lyrarr.utilities.audit as audit  # noqa: E402
from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.config import settings  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAuditIssues,
    TableAuditPins,
    TableAuditRuns,
    TableTracks,
    database,
    init_db,
    select,
)
from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.utilities.audit import runner  # noqa: E402
from lyrarr.utilities.audit.registry import REGISTRY, get_check  # noqa: E402

init_db()


@pytest.fixture
def small(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    L.reset_db()
    root = os.path.join(str(tmp_path), 'music')
    L.add_root(root)
    artist = os.path.join(root, 'Artist')
    album = os.path.join(artist, 'Album')
    L.add_artist(1, 'Artist', artist)
    L.add_album(10, 1, 'Album', album)
    yield {'root': root, 'album': album, 'events': events}
    database.remove()


def add(small, track_id, title='Song', **kw):
    return L.add_track(track_id, small['album'], 10, 1, title, **kw)


def get_run(run_id):
    database.remove()
    return database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)).scalars().first()


def run_issues(run_id, check_id=None):
    q = select(TableAuditIssues).where(TableAuditIssues.run_id == run_id)
    if check_id:
        q = q.where(TableAuditIssues.check_id == check_id)
    return database.execute(q.order_by(TableAuditIssues.id)).scalars().all()


def wait_idle(timeout=20):
    deadline = time.time() + timeout
    while runner.is_running() and time.time() < deadline:
        time.sleep(0.02)
    assert not runner.is_running()


def add_curated(small, track_id):
    """An H-1 case: an editor save replaced by an lrclib upgrade, archived version kept."""
    now = datetime.now()
    text = L.verse(f'curated{track_id}', 6)
    add(small, track_id, f'Curated {track_id}', status='available', is_synced=True, lrc=L.synced(text))
    L.add_history(track_id, 10, 1, provider='editor', when=now - timedelta(days=5))
    up = now - timedelta(days=4)
    L.add_history(track_id, 10, 1, action=2, provider='lrclib', when=up)
    return L.add_version(track_id, text, provider='lrclib', when=up)


def test_one_run_at_a_time(small):
    assert runner._run_lock.acquire(blocking=False)
    try:
        assert audit.start_run('dry_run') is None
        assert audit.is_running()
    finally:
        runner._run_lock.release()
    with pytest.raises(ValueError):
        audit.start_run('dry_run', ['no.such.check'])
    with pytest.raises(ValueError):
        audit.start_run('sideways')
    run_id = audit.start_run('dry_run', ['S-1'], wait=True)
    assert get_run(run_id).status == 'done'
    assert small['events'][0][0] == 'lyrics_audit_start' and 'total' not in small['events'][0][1]
    assert small['events'][-1][0] == 'lyrics_audit_complete'


def test_stale_running_rows_are_aborted(small):
    from sqlalchemy.dialects.sqlite import insert
    stale = database.execute(insert(TableAuditRuns).values(mode='dry_run', trigger='user', status='running',
                                                           started_at=datetime.now())).inserted_primary_key[0]
    audit.start_run('dry_run', ['S-1'], wait=True)
    assert get_run(stale).status == 'aborted'


def test_cancel_then_resume_network_check(small, monkeypatch):
    for tid in range(1, 31):
        add(small, tid, f'Flagged {tid}', status='instrumental', instrumental_reason='consensus')
    seen = []

    def slow_verify(track, **kw):
        seen.append(track.lidarrTrackId)
        time.sleep(0.03)
        return {'outcome': 'ok', 'chosen': {'provider': 'lrclib', 'acceptable': True}, 'candidates': []}

    monkeypatch.setattr(w, 'run_lyrics_pipeline', slow_verify)
    run_id = audit.start_run('dry_run', ['I-9'])
    time.sleep(0.25)
    assert audit.cancel_run(run_id)
    wait_idle()
    first = get_run(run_id)
    assert first.status == 'cancelled'
    cursor = first.to_dict()['progress']['cursor']
    partial = run_issues(run_id)
    assert 0 < len(partial) < 30 and cursor
    seen.clear()
    resumed = audit.start_run('dry_run', resume_run_id=run_id, wait=True)
    assert get_run(resumed).status == 'done'
    assert seen and min(seen) > cursor and max(seen) == 30
    got = {i.entity_id for i in run_issues(run_id)} | {i.entity_id for i in run_issues(resumed)}
    assert got == set(range(1, 31))


def test_network_limit_and_download_guard(small, monkeypatch):
    for tid in range(1, 6):
        add(small, tid, f'Flagged {tid}', status='instrumental', instrumental_reason='consensus')
    calls = []
    monkeypatch.setattr(w, 'run_lyrics_pipeline', lambda track, **kw: calls.append(1) or {'outcome': 'none'})
    rid = audit.start_run('dry_run', ['I-9'], network_limit=2, wait=True)
    assert len(calls) == 2
    entry = get_run(rid).to_dict()['checks'][0]
    assert 'network limit' in entry['note'] and entry['status'] == 'aborted'
    calls.clear()
    with w.exclusive_downloads() as ok:
        assert ok
        rid = audit.start_run('dry_run', ['I-9'], wait=True)
    assert calls == [] and 'download run' in get_run(rid).to_dict()['checks'][0]['note']


def test_pins_lifecycle(small):
    v1 = add_curated(small, 1)
    v2 = add_curated(small, 2)
    dry = audit.start_run('dry_run', ['H-1'], wait=True)
    found = {i.entity_id: i for i in run_issues(dry)}
    assert set(found) == {1, 2}
    pins = database.execute(select(TableAuditPins)).scalars().all()
    assert {(p.kind, p.ref_id) for p in pins if p.issue_id == found[1].id} >= {('version', v1)}
    from lyrarr.metadata.lyrics_store import pinned_version_ids
    assert pinned_version_ids(1) == {v1}
    # A second dry run supersedes the first run's open evidence issue (pins move).
    dry2 = audit.start_run('dry_run', ['H-1'], wait=True)
    old = {i.entity_id: i for i in run_issues(dry)}
    assert old[1].status == 'skipped' and 'superseded' in old[1].message
    assert not database.execute(select(TableAuditPins).where(TableAuditPins.issue_id == old[1].id)).first()
    assert pinned_version_ids(1) == {v1}
    new = {i.entity_id: i for i in run_issues(dry2)}
    # Ignoring drops the pins; applying (fixed) drops them too.
    from flask import Flask
    from flask_restx import Api

    from lyrarr.api.audit import api_ns_audit
    app = Flask(__name__)
    Api(app).add_namespace(api_ns_audit, '/')
    c = app.test_client()
    assert c.post('/audit/ignore', json={'issueIds': [new[2].id]}).status_code == 200
    assert pinned_version_ids(2) == set()
    audit.start_run('apply', parent_run_id=dry2, issue_ids=[new[1].id], wait=True)
    assert {i.id: i.status for i in run_issues(dry2)}[new[1].id] == 'fixed'
    assert pinned_version_ids(1) == set()
    assert v2


def test_failed_evidence_repair_keeps_the_pin(small, monkeypatch):
    v1 = add_curated(small, 1)
    dry = audit.start_run('dry_run', ['H-1'], wait=True)
    issue = run_issues(dry)[0]
    from lyrarr.utilities.audit import repairs
    monkeypatch.setattr(repairs, 'restore_version', lambda ctx, t, v: repairs.failed('disk full'))
    rid = audit.start_run('apply', parent_run_id=dry, issue_ids=[issue.id], wait=True)
    assert get_run(rid).to_dict()['totals']['failed'] == 1
    database.remove()
    again = database.execute(select(TableAuditIssues).where(TableAuditIssues.id == issue.id)).scalars().first()
    assert again.status == 'open' and again.message == 'disk full'
    from lyrarr.metadata.lyrics_store import pinned_version_ids
    assert pinned_version_ids(1) == {v1}


def test_retention_keeps_pinned_and_bootstrap_runs(small, monkeypatch):
    monkeypatch.setitem(settings.audit, 'report_retention', 2)
    add_curated(small, 1)
    evidence = audit.start_run('dry_run', ['H-1'], wait=True)
    from sqlalchemy.dialects.sqlite import insert
    boot = database.execute(insert(TableAuditRuns).values(mode='apply', trigger='bootstrap', status='done',
                                                          started_at=datetime.now())).inserted_primary_key[0]
    plain = [audit.start_run('dry_run', ['S-1'], wait=True) for _ in range(4)]
    remaining = set(database.execute(select(TableAuditRuns.id)).scalars().all())
    assert evidence in remaining and boot in remaining
    assert set(plain[-2:]) <= remaining and not set(plain[:2]) & remaining
    assert database.execute(select(TableAuditPins)).first()


def test_scheduled_runs_only_run_scheduled_checks(small, monkeypatch):
    ids = runner.default_check_ids('scheduled', {'fs_read': True, 'audio': True, 'network': True})
    assert ids and all(REGISTRY[i].spec.scheduled for i in ids)
    for code in ('S-4', 'S-5', 'S-9', 'S-14', 'S-15', 'I-9', 'M-4'):
        assert get_check(code).spec.id not in ids
    no_scan = runner.default_check_ids('scheduled', {'fs_read': False})
    assert all(REGISTRY[i].spec.cost != 'fs_read' for i in no_scan)
    user = runner.default_check_ids('user')
    assert 'state.backoff_stale' not in user and 'state.duplicate_rows' in user
    # Explicitly listed bootstrap_only checks run in a user run, never in a scheduled one.
    assert runner.resolve_checks(['S-4'], 'user') == ['state.backoff_stale']
    assert runner.resolve_checks(['S-4'], 'scheduled') == []
    monkeypatch.setitem(settings.audit, 'scheduled_mode', 'apply_safe')
    monkeypatch.setitem(settings.audit, 'scheduled_include_file_scan', False)
    last = audit.run_scheduled()
    r = get_run(last)
    assert r.trigger == 'scheduled' and r.mode == 'apply' and r.parent_run_id
    ran = {e['check_id'] for e in get_run(r.parent_run_id).to_dict()['checks']}
    assert all(REGISTRY[c].spec.scheduled for c in ran)


def test_replace_stagger_spread(small):
    for tid in range(1, 26):
        add(small, tid, f'Over {tid}', status='available', duration=30, lrc=L.synced(L.verse(f'o{tid}', 6), 10, 10))
    dry = audit.start_run('dry_run', ['M-1'], wait=True)
    ids = [i.id for i in run_issues(dry)]
    assert len(ids) == 25
    start = datetime.now()
    audit.start_run('apply', parent_run_id=dry, issue_ids=ids, wait=True)
    rows = database.execute(select(TableTracks).where(TableTracks.lidarrTrackId.in_(range(1, 26)))).scalars().all()
    afters = [r.lyrics_retry_after for r in rows]
    assert all(r.lyrics_replace_requested for r in rows)
    assert all(start < a <= start + timedelta(hours=6, minutes=1) for a in afters)
    assert len(set(afters)) > 20
    assert {i.status for i in run_issues(dry)} == {'pending'}
    # A single item is searched on the next pass (no stagger).
    add(small, 99, 'Single', status='available', duration=30, lrc=L.synced(L.verse('single', 6), 10, 10))
    dry = audit.start_run('dry_run', ['M-1'], wait=True)
    one = [i.id for i in run_issues(dry) if i.entity_id == 99]
    audit.start_run('apply', parent_run_id=dry, issue_ids=one, wait=True)
    database.remove()
    single = database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == 99)).scalars().first()
    assert single.lyrics_replace_requested and single.lyrics_retry_after is None


def test_review_issue_is_not_applied_by_check_id(small):
    add(small, 1, 'Over', status='available', duration=30, lrc=L.synced(L.verse('o', 6), 10, 10))
    dry = audit.start_run('dry_run', ['M-1'], wait=True)
    audit.start_run('apply', ['M-1'], parent_run_id=dry, wait=True)
    assert [i.status for i in run_issues(dry)] == ['open']
    database.remove()
    assert database.execute(select(TableTracks.lyrics_replace_requested)
                            .where(TableTracks.lidarrTrackId == 1)).scalar() is None


def test_file_repairs_wait_for_the_download_lock(small, monkeypatch):
    add(small, 1, 'Bom', status='available', lrc_bytes=b'\xef\xbb\xbf' + L.verse('b').encode())
    monkeypatch.setattr(runner, 'DOWNLOAD_LOCK_TIMEOUT_S', 0.1)
    assert get_check('F-3').spec.requires_download_lock
    with w.exclusive_downloads() as ok:
        assert ok
        rid = audit.start_run('apply', ['F-3'], wait=True)
    r = get_run(rid)
    assert r.status == 'failed' and 'download run is in progress' in r.error
    assert [i.status for i in run_issues(rid)] == ['open']
    rid = audit.start_run('apply', ['F-3'], wait=True)
    assert [i.status for i in run_issues(rid)] == ['fixed']


def test_bootstrap_ignores_unused_unmounted_root(small, monkeypatch):
    from lyrarr.app.database import data_migration_applied
    from lyrarr.lidarr import sync
    monkeypatch.setattr(sync, 'run_full_sync', lambda timeout_s=1800: False)
    L.add_root(os.path.join(small['root'] + '_unused_and_unmounted'))
    add(small, 1, 'Interlude', status='instrumental')
    rid = audit.run_bootstrap()
    assert rid is not None and data_migration_applied('audit-bootstrap-v1')
    f0 = run_issues(rid, 'files.library_unreachable')
    assert len(f0) == 1 and f0[0].severity == 'low'
    assert audit.run_bootstrap() is None


def test_refresh_pending_skips_issues_of_deleted_tracks(small):
    from sqlalchemy.dialects.sqlite import insert
    add(small, 1, 'Kept')
    run_id = database.execute(insert(TableAuditRuns).values(mode='apply', trigger='user', status='done',
                                                            started_at=datetime.now())).inserted_primary_key[0]
    ids_ = {}
    for tid in (1, 99):
        ids_[tid] = database.execute(insert(TableAuditIssues).values(
            run_id=run_id, check_id='match.duplicate_content_across_titles', entity_type='track', entity_id=tid,
            entity_key=f'track:{tid}', severity='medium', title='x', fix_action='request_replace',
            fix_mode='review', status='pending', lidarrTrackId=tid, created_at=datetime.now()
        )).inserted_primary_key[0]
    runner._refresh_pending()
    database.remove()
    got = {tid: database.execute(select(TableAuditIssues).where(TableAuditIssues.id == i)).scalars().first()
           for tid, i in ids_.items()}
    assert got[99].status == 'skipped' and got[99].message == 'track no longer exists'
    assert got[1].status == 'fixed' and got[1].message == 'Re-search finished'
