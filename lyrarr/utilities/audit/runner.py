"""Audit runner: one run at a time, dry run vs apply, pins, retention, events (PLAN §4.2).

start_run() creates a TableAuditRuns row and executes the run in a
background thread (or inline with wait=True, used by the scheduler wrappers
and tests). A run:

* computes per-root reachability (library_reachability(200, 0.05)) and
  records one F-0 issue per unreachable root; needs_library checks only look
  at entities under reachable roots, I-1/S-3 degrade to DB-only there;
* detects issues check by check (ordered by CheckSpec.order), persisting
  them in batches, marking ignored ones and pinning evidence;
* in apply mode, re-checks and repairs: auto issues by check id, review
  issues only by explicit issue ids, report issues never.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import ExitStack
from datetime import datetime

from . import repairs as rp
from .context import AuditContext
from .registry import REGISTRY, Issue, action_info, ordered_checks, resolve_check_id

logger = logging.getLogger(__name__)

LEGACY_ENDPOINT_CHECKS = ('files.status_drift', 'files.marker_or_empty_sidecar', 'state.row_invariants')
EVIDENCE_CHECKS = ('history.curated_superseded', 'instrumental.batch_deleted_restorable', 'files.whisper_scrambled')

_BATCH = 500
# How long an apply waits for a running download pass before failing.
DOWNLOAD_LOCK_TIMEOUT_S = 600
_run_lock = threading.Lock()
_state_lock = threading.Lock()
_state = {'run_id': None, 'cancel': None, 'ctx': None}


class AuditAbort(Exception):
    """Ends a run as 'failed' with a user-readable message."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_running():
    return _run_lock.locked()


def running_info():
    with _state_lock:
        ctx = _state['ctx']
        run_id = _state['run_id']
    if run_id is None:
        return None
    if ctx is None:
        return {'run_id': run_id, 'mode': None, 'phase': None, 'check_id': None, 'current': 0, 'total': 0}
    return {'run_id': run_id, 'mode': ctx.mode, 'phase': ctx.phase,
            'check_id': ctx.check.spec.id if ctx.check else None, 'current': ctx.current, 'total': ctx.total}


def cancel_run(run_id):
    with _state_lock:
        if _state['run_id'] == run_id and _state['cancel'] is not None:
            _state['cancel'].set()
            return True
    return False


def settings_snapshot():
    try:
        from lyrarr.app.config import settings
        a = settings.audit
        return {'scheduled_mode': a.scheduled_mode, 'scheduled_include_file_scan': a.scheduled_include_file_scan,
                'network_max_tracks': a.network_max_tracks, 'report_retention': a.report_retention,
                'auto_fix_on_upgrade': a.auto_fix_on_upgrade}
    except Exception:
        return {'network_max_tracks': 200, 'report_retention': 10, 'scheduled_include_file_scan': True}


def default_check_ids(trigger='user', include=None):
    """Checks of a default run: default_enabled ones plus the opt-in cost tiers in `include`."""
    include = _norm_include(include)
    out = []
    for check in ordered_checks():
        s = check.spec
        if s.bootstrap_only:
            continue
        if trigger == 'scheduled' and not s.scheduled:
            continue
        if s.cost == 'fs_read' and not include['fs_read']:
            continue
        if s.cost == 'audio':
            if not include['audio']:
                continue
        elif s.cost == 'network':
            if not include['network']:
                continue
        elif not s.default_enabled:
            continue
        out.append(s.id)
    return out


def resolve_checks(check_ids, trigger='user', include=None):
    """Explicit ids (dotted or codes) → ids; raises ValueError for unknown ids."""
    if check_ids is None:
        return default_check_ids(trigger, include)
    out = []
    unknown = []
    for value in check_ids:
        cid = resolve_check_id(value)
        if cid is None:
            unknown.append(value)
        elif cid not in out:
            out.append(cid)
    if unknown:
        raise ValueError(f"Unknown check id(s): {', '.join(map(str, unknown))}")
    if trigger == 'scheduled':
        out = [c for c in out if REGISTRY[c].spec.scheduled]
    return out


def start_run(mode, check_ids=None, *, trigger='user', include=None, scope=None, parent_run_id=None,
              issue_ids=None, network_limit=None, resume_run_id=None, detect_only_check_ids=(), wait=False,
              actions=None):
    """Start an audit run. Returns the run id, or None when another run is active.

    Raises ValueError for an unknown mode/check id or a missing parent run.
    """
    if mode not in ('dry_run', 'apply'):
        raise ValueError(f'Unknown mode {mode!r}')
    resume_cursor = None
    if resume_run_id is not None:
        prev = _get_run(resume_run_id)
        if prev is None:
            raise ValueError(f'Run {resume_run_id} not found')
        opts = _loads(prev.options) or {}
        check_ids = check_ids if check_ids is not None else opts.get('checks')
        include = include if include is not None else opts.get('include')
        scope = scope if scope is not None else opts.get('scope')
        resume_cursor = (_loads(prev.progress) or {}).get('cursor')
    if parent_run_id is not None:
        if mode != 'apply':
            raise ValueError('Only apply runs take a parent run')
        if _get_run(parent_run_id) is None:
            raise ValueError(f'Run {parent_run_id} not found')
        ids = resolve_checks(check_ids, trigger, include) if check_ids is not None else None
    else:
        ids = resolve_checks(check_ids, trigger, include)
    detect_only = tuple(c for c in (resolve_check_id(x) for x in detect_only_check_ids or ()) if c)

    if not _run_lock.acquire(blocking=False):
        return None
    try:
        _abort_stale_runs()
        include = _norm_include(include)
        options = {'checks': ids, 'include': include, 'scope': scope or {}, 'issue_ids': list(issue_ids or []),
                   'detect_only': list(detect_only), 'network_limit': network_limit,
                   'resume_run_id': resume_run_id, 'actions': {str(k): v for k, v in (actions or {}).items()}}
        run_id = _create_run(mode, trigger, parent_run_id, options)
        cancel = threading.Event()
        with _state_lock:
            _state.update(run_id=run_id, cancel=cancel, ctx=None)
    except Exception:
        _run_lock.release()
        raise
    plan = dict(mode=mode, trigger=trigger, ids=ids, include=include, scope=scope or {}, parent=parent_run_id,
                issue_ids=list(issue_ids or []), network_limit=network_limit, resume_cursor=resume_cursor,
                detect_only=detect_only, actions={int(k): v for k, v in (actions or {}).items()})
    if wait:
        _execute(run_id, cancel, plan)
    else:
        threading.Thread(target=_execute, args=(run_id, cancel, plan), name=f'audit-run-{run_id}',
                         daemon=True).start()
    return run_id


def run_bootstrap():
    from .bootstrap import run_bootstrap as _rb
    return _rb()


def run_scheduled():
    """Scheduled audit: a dry run of the scheduled-safe default checks, then (with
    scheduled_mode 'apply_safe') an apply run of that dry run's auto issues."""
    snap = settings_snapshot()
    include = {'fs_read': bool(snap.get('scheduled_include_file_scan', True)), 'audio': False, 'network': False}
    ids = default_check_ids('scheduled', include)
    dry = start_run('dry_run', ids, trigger='scheduled', include=include, wait=True)
    if dry is None:
        logger.info('Scheduled audit skipped: another audit is running')
        return None
    run = _get_run(dry)
    if snap.get('scheduled_mode') == 'apply_safe' and run is not None and run.status == 'done':
        return start_run('apply', ids, trigger='scheduled', parent_run_id=dry, include=include, wait=True)
    return dry


def issue_from_row(row):
    d = _loads(row.details) or {}
    return Issue(entity_type=row.entity_type, entity_id=row.entity_id, entity_key=row.entity_key, title=row.title,
                 severity=row.severity, path=row.path, details=d, fix_action=row.fix_action,
                 fix_mode=row.fix_mode, track_id=row.lidarrTrackId, album_id=row.lidarrAlbumId,
                 artist_id=row.lidarrArtistId)


def issue_to_dict(row, ignored_keys=None):
    out = row.to_dict()
    check = REGISTRY.get(row.check_id)
    out['links'] = {'trackId': row.lidarrTrackId, 'albumId': row.lidarrAlbumId, 'artistId': row.lidarrArtistId}
    out['fix'] = action_info(row.fix_action, row.fix_mode)
    if out['fix']['action'] is None or row.fix_mode == 'report':
        out['fix']['action'] = None
    alts = (out.get('details') or {}).get('alt_actions') or []
    out['fix']['alternatives'] = [action_info(a, row.fix_mode) for a in alts]
    out['ignored'] = row.status == 'ignored' or (ignored_keys is not None and
                                                   (row.check_id, row.entity_key) in ignored_keys)
    out['check'] = {'id': row.check_id, 'code': check.spec.code if check else None,
                    'title': check.spec.title if check else row.check_id,
                    'category': check.spec.category if check else None}
    return out


# ---------------------------------------------------------------------------
# Run rows
# ---------------------------------------------------------------------------

def _dumps(value):
    return json.dumps(value, default=str, ensure_ascii=False) if value is not None else None


def _loads(value):
    return rp.parse_json(value)


def _get_run(run_id):
    from lyrarr.app.database import TableAuditRuns, database, select
    return database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)).scalars().first()


def _create_run(mode, trigger, parent, options):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableAuditRuns, database
    res = database.execute(sqlite_insert(TableAuditRuns).values(
        mode=mode, trigger=trigger, status='running', parent_run_id=parent, options=_dumps(options),
        started_at=datetime.now(), checks=_dumps([]), totals=_dumps({})))
    return res.inserted_primary_key[0]


def _update_run(run_id, **values):
    from lyrarr.app.database import TableAuditRuns, database, update
    for k in ('options', 'library', 'totals', 'checks', 'progress'):
        if k in values and not isinstance(values[k], str) and values[k] is not None:
            values[k] = _dumps(values[k])
    database.execute(update(TableAuditRuns).where(TableAuditRuns.id == run_id).values(**values))


def _abort_stale_runs():
    """Runs left 'running' by a crash/restart (we hold the run lock, so none is live)."""
    from lyrarr.app.database import TableAuditRuns, database, update
    database.execute(update(TableAuditRuns).where(TableAuditRuns.status == 'running')
                     .values(status='aborted', finished_at=datetime.now(),
                             error='interrupted (lyrarr restarted while the check was running)'))


def _norm_include(include):
    include = dict(include or {})
    return {'fs_read': bool(include.get('fs_read', True)), 'audio': bool(include.get('audio', False)),
            'network': bool(include.get('network', False))}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _emit(type_, payload):
    try:
        from lyrarr.app.event_handler import event_stream
        event_stream(type=type_, payload=payload)
    except Exception as e:
        logger.debug(f'audit event {type_} failed: {e}')


def _sequence(ids, detect_only):
    checks = ordered_checks(ids)
    head = [c for c in checks if c.spec.order <= 1 and c.spec.id not in detect_only]
    evidence = [c for c in checks if c.spec.id in detect_only]
    rest = [c for c in checks if c not in head and c not in evidence]
    return head + evidence + rest


def _execute(run_id, cancel, plan):
    from lyrarr.app.database import database
    from lyrarr.metadata.lyrics_store import library_reachability

    snap = settings_snapshot()
    limit = plan['network_limit'] or snap.get('network_max_tracks') or 200
    ctx = AuditContext(run_id, plan['mode'], plan['trigger'], cancel_event=cancel, include=plan['include'],
                       scope=plan['scope'], network_limit=int(limit), resume_cursor=plan['resume_cursor'],
                       settings_snapshot=snap)
    with _state_lock:
        _state['ctx'] = ctx
    entries = []
    status, error = 'done', None
    n_checks = len(plan['ids'] or []) if plan['parent'] is None else len(plan['ids'] or REGISTRY)
    _emit('lyrics_audit_start', {
        'run_id': run_id, 'mode': plan['mode'], 'trigger': plan['trigger'], 'checks': n_checks,
        'message': 'Library check started' if plan['mode'] == 'dry_run' else 'Applying library fixes'})
    try:
        with ExitStack() as stack:
            if plan['trigger'] == 'bootstrap':
                if not stack.enter_context(rp.exclusive_downloads(DOWNLOAD_LOCK_TIMEOUT_S)):
                    raise AuditAbort('a download run is in progress')
                ctx.cache['holds_download_lock'] = True
                entries.append(_phase0_sync(ctx))
            ctx.reach = library_reachability(sample_per_root=200, max_missing_ratio=0.05)
            _update_run(run_id, library=_library_json(ctx.reach))
            _refresh_pending()
            if plan['parent'] is not None:
                entries.extend(_apply_from_parent(ctx, plan))
            else:
                seq = _sequence(plan['ids'], plan['detect_only'])
                for i, check in enumerate(seq, 1):
                    if ctx.cancelled:
                        break
                    entries.append(_run_check(ctx, check, i, len(seq), plan))
                    _update_run(run_id, checks=entries,
                                progress={'check_id': check.spec.id, 'current': ctx.current,
                                          'total': ctx.total, 'cursor': ctx.cursor})
        if ctx.cancelled:
            status = 'cancelled'
    except AuditAbort as e:
        status, error = 'failed', str(e)
    except Exception as e:
        logger.exception(f'Audit run {run_id} failed')
        status, error = 'failed', str(e)
        try:
            database.rollback()
        except Exception:
            pass
    finally:
        try:
            totals = _totals(run_id, entries, plan)
            # The resume cursor only means something for the (track-ordered) network checks.
            network = ctx.check is not None and ctx.check.spec.cost == 'network'
            _update_run(run_id, status=status, error=error, finished_at=datetime.now(), checks=entries,
                        totals=totals, progress={'check_id': ctx.check.spec.id if ctx.check else None,
                                                 'current': ctx.current, 'total': ctx.total,
                                                 'cursor': ctx.cursor if network else None})
            if error:
                _emit('lyrics_audit_error', {'run_id': run_id, 'message': error})
            _emit('lyrics_audit_complete', {
                'run_id': run_id, 'mode': plan['mode'], 'status': status, 'totals': totals,
                'message': _complete_message(plan['mode'], status, totals, error),
                **({'error': error} if error else {})})
            try:
                _retention()
            except Exception as e:
                logger.warning(f'audit retention failed: {e}')
        finally:
            with _state_lock:
                _state.update(run_id=None, cancel=None, ctx=None)
            _run_lock.release()
            if threading.current_thread().name.startswith('audit-run-'):
                database.remove()


def _complete_message(mode, status, totals, error):
    if status == 'failed':
        return f'Library check failed: {error}'
    if status == 'cancelled':
        return 'Library check cancelled'
    if mode == 'dry_run':
        auto, review = totals.get('fixable_auto', 0), totals.get('review', 0)
        parts = [f'{auto} safe fix(es)'] if auto else []
        if review:
            parts.append(f'{review} to review')
        extra = f" ({', '.join(parts)})" if parts else ''
        return f"Library check: {totals.get('open', 0)} issue(s) found{extra}"
    return f"Library fixes applied: {totals.get('fixed', 0)} fixed" + (
        f", {totals.get('pending', 0)} re-search pending" if totals.get('pending') else '')


def _library_json(reach):
    roots = [{'root': s.root, 'ok': s.ok, 'exists': s.exists, 'checked': s.checked, 'missing': s.missing,
              'ratio': s.ratio} for s in reach.roots.values()]
    checked = sum(r['checked'] for r in roots)
    missing = sum(r['missing'] for r in roots)
    return {'ok': reach.all_ok, 'checked': checked, 'missing': missing,
            'ratio': round(missing / checked, 4) if checked else 0.0, 'roots': roots,
            'unreachable': [s.root for s in reach.unreachable]}


def _earlier_bootstrap_synced(run_id):
    """Whether an earlier bootstrap run's phase 0 completed a full Lidarr sync."""
    from lyrarr.app.database import TableAuditRuns, database, select
    R = TableAuditRuns
    try:
        rows = database.execute(select(R.checks).where(R.trigger == 'bootstrap', R.id != run_id)
                                .order_by(R.id.desc()).limit(20)).scalars().all()
    except Exception:
        return False
    for raw in rows:
        entries = _loads(raw) or []
        if any(isinstance(e, dict) and e.get('check_id') == 'phase0.full_sync' and e.get('synced')
               for e in entries):
            return True
    return False


def _phase0_sync(ctx):
    """Bootstrap phase 0: a full Lidarr sync (durations in seconds, fresh roots)."""
    entry = {'check_id': 'phase0.full_sync', 'code': 'P-0', 'found': 0, 'fixed': 0, 'failed': 0,
             'skipped': 0, 'ignored': 0, 'pending': 0, 'status': 'done', 'note': None}
    if _earlier_bootstrap_synced(ctx.run_id):
        # A retry (a root was unmounted, a check failed): an earlier bootstrap
        # run already completed the full sync, and regular syncs have kept the
        # durations in seconds since. Don't hold the download lock for another.
        entry.update(synced=True, note='Lidarr already synced by an earlier upgrade check')
        ctx.cache['lidarr_synced'] = True
        return entry
    try:
        from lyrarr.lidarr import sync
        synced = bool(sync.run_full_sync(1800))
    except Exception as e:
        logger.warning(f'bootstrap full sync failed: {e}')
        synced = False
    entry['synced'] = synced
    ctx.cache['lidarr_synced'] = synced  # S-14 trusts the durations Lidarr just wrote
    if not synced:
        entry['note'] = 'Lidarr sync did not complete; durations fixed from the database only'
    return entry


def _ignored_keys(check_id):
    from lyrarr.app.database import TableAuditIgnores, database, select
    return set(database.execute(select(TableAuditIgnores.entity_key)
                                .where(TableAuditIgnores.check_id == check_id)).scalars().all())


def _issue_values(ctx, check, issue, ignored):
    spec = check.spec
    details = dict(issue.details or {})
    return dict(
        run_id=ctx.run_id, check_id=spec.id, entity_type=issue.entity_type, entity_id=issue.entity_id,
        entity_key=issue.entity_key, path=issue.path, severity=issue.severity or spec.severity,
        title=issue.title, details=_dumps(details), fix_action=issue.fix_action,
        fix_mode=(issue.fix_mode or spec.fix_mode) if (issue.fix_action or issue.fix_mode) else 'report',
        status='ignored' if issue.entity_key in ignored else 'open',
        lidarrTrackId=issue.track_id, lidarrAlbumId=issue.album_id, lidarrArtistId=issue.artist_id,
        created_at=datetime.now(),
    )


def _persist(ctx, check, batch, ignored):
    """Insert issues; pins_evidence checks insert one by one to pin their evidence."""
    from sqlalchemy import insert

    from lyrarr.app.database import TableAuditIssues, database
    if not batch:
        return
    values = [_issue_values(ctx, check, i, ignored) for i in batch]
    if not check.spec.pins_evidence:
        database.execute(insert(TableAuditIssues), values)
        return
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    for issue, v in zip(batch, values):
        issue_id = database.execute(sqlite_insert(TableAuditIssues).values(**v)).inserted_primary_key[0]
        if v['status'] == 'open':
            _pin(issue_id, issue.details)
            _supersede(check.spec.id, issue.entity_key, ctx.run_id)


def _pin(issue_id, details):
    from sqlalchemy import insert

    from lyrarr.app.database import TableAuditPins, database
    rows = [{'issue_id': issue_id, 'kind': 'version', 'ref_id': int(v)} for v in (details or {}).get('pin_version_ids') or []]
    rows += [{'issue_id': issue_id, 'kind': 'history', 'ref_id': int(h)} for h in (details or {}).get('pin_history_ids') or []]
    if rows:
        database.execute(insert(TableAuditPins), rows)


def _unpin(issue_ids):
    from lyrarr.app.database import TableAuditPins, database, delete
    ids = list(issue_ids)
    if ids:
        database.execute(delete(TableAuditPins).where(TableAuditPins.issue_id.in_(ids)))


def _supersede(check_id, entity_key, run_id):
    """Older open evidence issues for the same entity are superseded by this run's
    (whose pins now hold the evidence), so retention can drop the old runs."""
    from lyrarr.app.database import TableAuditIssues, database, select, update
    I = TableAuditIssues  # noqa: E741
    old = database.execute(select(I.id).where(I.check_id == check_id, I.entity_key == entity_key,
                                              I.run_id != run_id, I.status == 'open')).scalars().all()
    if old:
        database.execute(update(I).where(I.id.in_(old)).values(
            status='skipped', message=f'superseded by run {run_id}', resolved_at=datetime.now()))
        _unpin(old)


def _new_entry(check):
    return {'check_id': check.spec.id, 'code': check.spec.code, 'found': 0, 'fixed': 0, 'failed': 0,
            'skipped': 0, 'ignored': 0, 'pending': 0, 'status': 'done', 'note': None}


def _run_check(ctx, check, index, total, plan):
    from lyrarr.app.database import TableAuditIssues, database, select
    spec = check.spec
    entry = _new_entry(check)
    detect_only = spec.id in plan['detect_only']
    if spec.needs_library and not spec.degrades_without_library and not ctx.reach.any_ok:
        entry.update(status='skipped', note='no music folder is reachable')
        return entry
    ctx.begin_check(check, index, total, 'detect')
    ignored = _ignored_keys(spec.id)
    batch, found = [], 0
    try:
        for issue in check.detect(ctx):
            batch.append(issue)
            found += 1
            if len(batch) >= _BATCH:
                _persist(ctx, check, batch, ignored)
                batch = []
            if ctx.cancelled:
                break
    except AuditAbort:
        raise
    except Exception as e:
        logger.exception(f'audit check {spec.code} failed')
        entry.update(status='failed', note=f'check failed: {e}')
    _persist(ctx, check, batch, ignored)
    entry['found'] = found
    entry['ignored'] = database.execute(
        select(TableAuditIssues.id).where(TableAuditIssues.run_id == ctx.run_id, TableAuditIssues.check_id == spec.id,
                                          TableAuditIssues.status == 'ignored')).all().__len__()
    notes = list(ctx.notes)
    if ctx.unreachable_skipped:
        roots = ', '.join(s.root for s in ctx.reach.unreachable) or 'an unreachable folder'
        notes.append(f'{ctx.unreachable_skipped:,} item(s) under {roots} skipped: folder not reachable')
    if ctx.degraded:
        notes.append(f'{ctx.degraded:,} track(s) under unreachable folders checked from the database only')
    if notes:
        entry['note'] = '; '.join(notes)
    if ctx.cancelled:
        entry['status'] = 'cancelled'
        return entry
    if detect_only:
        if entry['status'] == 'done':
            entry['status'] = 'detect_only'
        return entry
    if ctx.cache.pop('network_incomplete', False) and entry['status'] == 'done':
        entry['status'] = 'aborted'
    if plan['mode'] == 'apply' and entry['status'] in ('done', 'aborted'):
        rows = database.execute(
            select(TableAuditIssues).where(TableAuditIssues.run_id == ctx.run_id,
                                           TableAuditIssues.check_id == spec.id,
                                           TableAuditIssues.status == 'open',
                                           TableAuditIssues.fix_mode == 'auto')
            .order_by(TableAuditIssues.id)).scalars().all()
        ctx.replace_n = sum(1 for r in rows if r.fix_action == 'request_replace')
        _apply_rows(ctx, check, rows, entry, plan)
    _emit_bulk(ctx, check)
    return entry


def _emit_bulk(ctx, check):
    c = ctx.counts
    if not any(c.values()):
        return
    parts = []
    if c['cleared']:
        parts.append(f"{c['cleared']} instrumental flag(s) cleared")
    if c['flagged']:
        parts.append(f"{c['flagged']} marked instrumental")
    if c['hinted']:
        parts.append(f"{c['hinted']} likely instrumental")
    _emit('instrumental_bulk', {'run_id': ctx.run_id, 'check_id': check.spec.id, 'flagged': c['flagged'],
                                'cleared': c['cleared'], 'hinted': c['hinted'],
                                'message': f"Library check: {', '.join(parts)}"})
    ctx.counts = {'flagged': 0, 'cleared': 0, 'hinted': 0}


def _apply_rows(ctx, check, rows, entry, plan):
    if not rows:
        return
    ctx.phase = 'apply'
    ctx.stagger_n = len(rows)
    with ExitStack() as stack:
        if check.spec.requires_download_lock and not ctx.cache.get('holds_download_lock'):
            if not stack.enter_context(rp.exclusive_downloads(DOWNLOAD_LOCK_TIMEOUT_S)):
                raise AuditAbort('a download run is in progress')
        explicit_ids = set(plan['issue_ids'] or ())
        for i, row in enumerate(rows, 1):
            if ctx.cancelled:
                break
            ctx.progress(i, len(rows), cursor=row.entity_id)
            _apply_one(ctx, check, row, entry, plan['actions'].get(row.id), explicit=row.id in explicit_ids)


def _apply_one(ctx, check, row, entry, chosen_action=None, *, explicit=False):
    from lyrarr.app.database import TableAuditIssues, database, update
    spec = check.spec
    message, status, result = None, None, None
    try:
        fresh = check.recheck(ctx, row)
    except Exception as e:
        logger.warning(f'audit {spec.code}: recheck of issue {row.id} failed: {e}')
        fresh, status, message = None, 'failed', f'recheck failed: {e}'
    if status is None and fresh is None:
        status, message = 'skipped', 'no longer applies'
    elif status is None:
        allowed = [fresh.fix_action] + list((fresh.details or {}).get('alt_actions') or [])
        if chosen_action and chosen_action in allowed:
            fresh.fix_action = chosen_action
        mode = fresh.fix_mode or spec.fix_mode
        if mode == 'report' or not fresh.fix_action:
            status, message = 'skipped', 'report only'
        elif mode == 'review' and not explicit:
            status, message = 'open', 'needs review: apply it individually'
        else:
            try:
                result = check.repair(ctx, fresh)
                status, message = result.status, result.message
            except Exception as e:
                logger.exception(f'audit {spec.code}: repair of issue {row.id} failed')
                status, message = 'failed', f'repair failed: {e}'
    if result is not None and result.changed and status == 'fixed':
        _history(ctx, check, fresh, row, result)
    db_status = status
    if status == 'open':
        database.execute(update(TableAuditIssues).where(TableAuditIssues.id == row.id).values(
            fix_mode='review', message=message))
        return
    if status == 'failed' and spec.pins_evidence:
        db_status = 'open'  # keep the evidence pinned until a repair succeeds
    database.execute(update(TableAuditIssues).where(TableAuditIssues.id == row.id).values(
        status=db_status, message=message,
        resolved_at=datetime.now() if db_status not in ('open', 'pending') else None))
    if db_status not in ('open', 'pending'):
        _unpin([row.id])
    entry[status] = entry.get(status, 0) + 1


def _history(ctx, check, issue, row, result):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableHistory, database
    spec = check.spec
    track_id = issue.track_id if issue.track_id is not None and rp.fresh_track(issue.track_id) else None
    try:
        database.execute(sqlite_insert(TableHistory).values(
            action=7, description=f'Audit: {spec.title}: {issue.title}'[:500],
            metadata_type='cover' if spec.category == 'covers' else 'lyrics', provider=f'audit:{spec.code}',
            lidarrTrackId=track_id, lidarrAlbumId=issue.album_id if track_id or spec.category == 'covers' else None,
            lidarrArtistId=issue.artist_id if track_id or issue.album_id else None,
            timestamp=datetime.now(), metadata_path=issue.path,
            details=_dumps({'check_id': spec.id, 'code': spec.code, 'issue_id': row.id, 'run_id': ctx.run_id,
                            'action': issue.fix_action, 'message': result.message, **(result.details or {})})))
    except Exception as e:
        logger.debug(f'audit history row failed: {e}')


def _apply_from_parent(ctx, plan):
    from lyrarr.app.database import TableAuditIssues, database, select
    I = TableAuditIssues  # noqa: E741
    q = select(I).where(I.run_id == plan['parent'])
    picked = []
    if plan['issue_ids']:
        picked += database.execute(q.where(I.id.in_(plan['issue_ids']), I.fix_mode.in_(('auto', 'review')),
                                           I.status.in_(('open', 'failed')))).scalars().all()
    if plan['ids'] is not None or not plan['issue_ids']:
        qq = q.where(I.fix_mode == 'auto', I.status == 'open')
        if plan['ids'] is not None:
            qq = qq.where(I.check_id.in_(plan['ids']))
        picked += database.execute(qq).scalars().all()
    seen, rows = set(), []
    for r in picked:
        if r.id not in seen:
            seen.add(r.id)
            rows.append(r)
    ctx.replace_n = sum(1 for r in rows if r.fix_action == 'request_replace')
    by_check = {}
    for r in rows:
        by_check.setdefault(r.check_id, []).append(r)
    checks = [c for c in ordered_checks(list(by_check)) if c.spec.id in by_check]
    entries = []
    for i, check in enumerate(checks, 1):
        if ctx.cancelled:
            break
        ctx.begin_check(check, i, len(checks), 'apply')
        entry = _new_entry(check)
        entry['found'] = len(by_check[check.spec.id])
        group = sorted(by_check[check.spec.id], key=lambda r: r.id)
        _apply_rows(ctx, check, group, entry, plan)
        if ctx.cancelled:
            entry['status'] = 'cancelled'
        _emit_bulk(ctx, check)
        entries.append(entry)
    return entries


def _totals(run_id, entries, plan):
    from lyrarr.app.database import TableAuditIssues, database, func, select
    I = TableAuditIssues  # noqa: E741
    t = {k: sum(int(e.get(k) or 0) for e in entries) for k in ('fixed', 'failed', 'skipped', 'pending')}
    rows = database.execute(select(I.status, I.fix_mode, I.severity, func.count())
                            .where(I.run_id == run_id).group_by(I.status, I.fix_mode, I.severity)).all()
    by_sev = {}
    open_n = fixable = auto = review = ignored = 0
    for status, mode, sev, n in rows:
        if status == 'ignored':
            ignored += n
        if status == 'open':
            open_n += n
            by_sev[sev] = by_sev.get(sev, 0) + n
            if mode in ('auto', 'review'):
                fixable += n
            if mode == 'auto':
                auto += n
            if mode == 'review':
                review += n
    issues = sum(n for *_, n in rows) if plan['parent'] is None else sum(int(e.get('found') or 0) for e in entries)
    t.update(issues=issues, open=open_n, fixable=fixable, fixable_auto=auto, review=review, ignored=ignored,
             by_severity=by_sev)
    return t


def _refresh_pending():
    """Resolve 'pending' replace issues whose request the worker has handled."""
    from lyrarr.app.database import TableAuditIssues, TableHistory, TableTracks, database, select, update
    I = TableAuditIssues  # noqa: E741
    rows = database.execute(select(I).where(I.status == 'pending')).scalars().all()
    for r in rows:
        t = database.execute(select(TableTracks.lyrics_replace_requested)
                             .where(TableTracks.lidarrTrackId == r.lidarrTrackId)).first()
        if t is not None and t[0] is not None:
            continue
        if t is None:  # the row was deleted (e.g. by a Lidarr sync) before the worker got to it
            database.execute(update(I).where(I.id == r.id).values(
                status='skipped', message='track no longer exists', resolved_at=datetime.now()))
            _unpin([r.id])
            continue
        hist = database.execute(
            select(TableHistory).where(TableHistory.lidarrTrackId == r.lidarrTrackId, TableHistory.action == 7,
                                       TableHistory.details.like('%replace_result%'))
            .order_by(TableHistory.timestamp.desc(), TableHistory.id.desc()).limit(1)).scalars().first()
        result = (_loads(hist.details) or {}).get('replace_result') if hist else None
        msg = {'kept': 'Re-searched: nothing better found, the file was kept',
               'replaced': 'Re-searched: lyrics replaced (previous file archived)'}.get(result, 'Re-search finished')
        database.execute(update(I).where(I.id == r.id).values(status='fixed', message=msg, resolved_at=datetime.now()))
        _unpin([r.id])


def _retention():
    """Keep the last N runs, the latest bootstrap run, and every run with an open
    issue of an evidence (pins) check; delete the rest (issues and pins cascade)."""
    from lyrarr.app.database import TableAuditIssues, TableAuditPins, TableAuditRuns, database, delete, select
    keep_n = int(settings_snapshot().get('report_retention') or 10)
    R, I = TableAuditRuns, TableAuditIssues  # noqa: E741
    ids = database.execute(select(R.id).order_by(R.id.desc())).scalars().all()
    keep = set(ids[:keep_n])
    boot = database.execute(select(R.id).where(R.trigger == 'bootstrap').order_by(R.id.desc()).limit(1)).scalar()
    if boot is not None:
        keep.add(boot)
    pin_checks = [c.spec.id for c in REGISTRY.values() if c.spec.pins_evidence]
    keep |= set(database.execute(select(I.run_id).where(I.check_id.in_(pin_checks),
                                                        I.status.in_(('open', 'pending')))).scalars().all())
    keep |= set(database.execute(select(R.id).where(R.status == 'running')).scalars().all())
    drop = [i for i in ids if i not in keep]
    if not drop:
        return
    issue_ids = select(I.id).where(I.run_id.in_(drop))
    database.execute(delete(TableAuditPins).where(TableAuditPins.issue_id.in_(issue_ids)))
    database.execute(delete(I).where(I.run_id.in_(drop)))
    database.execute(delete(R).where(R.id.in_(drop)))

