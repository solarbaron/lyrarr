"""One-shot audit after an upgrade (PLAN §4.6).

Runs once per install (recorded as data migration 'audit-bootstrap-v1') and
is re-attempted every 6 h by the scheduler until it completes for every
library root:

* phase 0: a full Lidarr sync (durations in seconds, fresh root folders),
  then S-14 converts leftover millisecond durations, so nothing is requeued
  with a duration that fails every candidate's duration gate;
* phase 1: S-2 (source backfill, reads history before it is pruned), then
  H-1, I-7 and F-9 detect-only with pinned evidence;
* phase 2: the safe auto repairs, requeues strictly after phase 0.

The whole run holds exclusive_downloads(600), so no worker pass can archive
or trim versions between phase 1 and phase 2.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

BOOTSTRAP_MIGRATION_ID = 'audit-bootstrap-v1'

# Applied in CheckSpec.order (S-14 order 0 and S-2 order 1 run first).
BOOTSTRAP_APPLY_CHECKS = (
    'state.duration_ms', 'state.source_backfill',
    'files.library_unreachable',
    'state.bool_columns', 'state.profiles_default', 'state.stale_lyrics_source', 'state.row_invariants',
    'state.translated_noop', 'state.content_hash_backfill',
    'instrumental.legacy_unknown', 'instrumental.title_backfill', 'instrumental.real_lyrics_on_disk',
    'instrumental.weak_title_only', 'state.backoff_stale', 'state.upgrade_candidates',
    'files.status_drift', 'files.marker_or_empty_sidecar', 'files.temp_leftovers',
)


def bootstrap_check_ids():
    from .runner import EVIDENCE_CHECKS
    return list(BOOTSTRAP_APPLY_CHECKS) + [c for c in EVIDENCE_CHECKS if c not in BOOTSTRAP_APPLY_CHECKS]


def _enabled():
    try:
        from lyrarr.app.config import settings
        return bool(settings.audit.auto_fix_on_upgrade)
    except Exception:
        return True


def run_bootstrap():
    """Run the upgrade bootstrap once. Returns the run id, or None when skipped."""
    from lyrarr.app.database import data_migration_applied, record_data_migration

    from .runner import EVIDENCE_CHECKS, _get_run, _loads, is_running, start_run

    if not _enabled():
        logger.debug('Audit bootstrap disabled (audit.auto_fix_on_upgrade)')
        return None
    if data_migration_applied(BOOTSTRAP_MIGRATION_ID):
        return None
    if is_running():
        logger.info('Audit bootstrap postponed: another audit is running')
        return None
    run_id = start_run('apply', bootstrap_check_ids(), trigger='bootstrap',
                       detect_only_check_ids=EVIDENCE_CHECKS, wait=True)
    if run_id is None:
        return None
    run = _get_run(run_id)
    entries = _loads(run.checks) or []
    library = _loads(run.library) or {}
    complete = (
        run.status == 'done'
        # An unmounted root that no track lives under doesn't block completion.
        and all(r.get('ok') or not r.get('checked') for r in library.get('roots', []))
        and all(e.get('status') in ('done', 'detect_only') for e in entries if e.get('check_id') != 'phase0.full_sync')
    )
    if complete:
        record_data_migration(BOOTSTRAP_MIGRATION_ID, {'run_id': run_id, **(_loads(run.totals) or {})})
        logger.info(f'Audit bootstrap complete (run {run_id})')
    else:
        logger.info(f'Audit bootstrap run {run_id} incomplete ({run.status}); it will run again')
    return run_id
