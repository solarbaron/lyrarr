"""Audit check registry: CheckSpec, Issue, RepairResult and the Check base classes.

Every check is a subclass of Check registered with @register. The registry is
ordered by registration; the runner orders execution by CheckSpec.order.
Checks are pure with respect to import: nothing touches the DB until
detect/recheck/repair runs (PLAN §0 "import without side effects").
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckSpec:
    id: str
    code: str
    title: str
    description: str
    category: str            # instrumental | files | matching | state | history | covers
    severity: str            # critical | high | medium | low | info
    cost: str                # db | fs_stat | fs_read | audio | network
    fix_mode: str            # auto | review | report
    needs_library: bool = False
    degrades_without_library: bool = False
    bootstrap: bool = False
    bootstrap_only: bool = False
    scheduled: bool = True
    pins_evidence: bool = False
    default_enabled: bool = True
    order: int = 50
    requires_download_lock: bool = False

    def to_dict(self):
        return {
            'id': self.id, 'code': self.code, 'title': self.title, 'description': self.description,
            'category': self.category, 'severity': self.severity, 'cost': self.cost,
            'fix_mode': self.fix_mode, 'needs_library': self.needs_library,
            'degrades_without_library': self.degrades_without_library, 'bootstrap': self.bootstrap,
            'bootstrap_only': self.bootstrap_only, 'scheduled': self.scheduled,
            'default_enabled': self.default_enabled, 'pins_evidence': self.pins_evidence,
            'order': self.order,
        }


@dataclass
class Issue:
    entity_type: str
    entity_id: int | None
    entity_key: str
    title: str
    severity: str | None = None
    path: str | None = None
    details: dict = field(default_factory=dict)
    fix_action: str | None = None
    fix_label: str | None = None
    fix_mode: str | None = None
    track_id: int | None = None
    album_id: int | None = None
    artist_id: int | None = None


@dataclass
class RepairResult:
    status: str               # fixed | pending | skipped | failed
    message: str = ''
    details: dict = field(default_factory=dict)
    changed: bool = True      # whether anything was written (history action 7 row)


def fixed(message='', **details):
    return RepairResult('fixed', message, details)


def failed(message, **details):
    return RepairResult('failed', message, details, changed=False)


def skipped(message, **details):
    return RepairResult('skipped', message, details, changed=False)


def pending(message, **details):
    return RepairResult('pending', message, details)


# Human labels for fix actions (Issue.fix in the API) and whether the action
# removes or rewrites a file (the UI asks for confirmation then).
ACTION_LABELS = {
    'adopt': ('Mark available (lyrics file found)', False),
    'requeue_legacy': ('Clear the flag and search again', False),
    'set_title_reason': ('Record the reason (title)', False),
    'archive_remove_stale': ('Archive & remove the stale lyrics file', True),
    'not_instrumental': ('Not instrumental (keep the lyrics)', False),
    'clear_instrumental': ('Clear the flag and search again', False),
    'archive_flag_instrumental': ('Archive the lyrics and mark instrumental', True),
    'keep_lyrics': ('Keep the lyrics, not instrumental', False),
    'restore_version': ('Restore the archived version', True),
    'not_instrumental_queue': ('Not instrumental: search again', False),
    'mark_missing': ('Mark missing', False),
    'mark_missing_keep': ('Mark missing (file kept, replaceable)', False),
    'normalize_encoding': ('Re-encode as UTF-8 (archived first)', True),
    'repair_lrc': ('Repair timestamps (archived first)', True),
    'fix_old_repair': ('Fix the old repair damage (archived first)', True),
    'archive_remove_wrong': ('Archive, remove and blacklist this content', True),
    'request_replace': ('Re-search (keep the file if nothing better)', False),
    'clean_in_place': ('Clean the file in place (archived first)', True),
    'unescape': ('Decode HTML entities (archived first)', True),
    'strip_credits': ('Strip credit lines (archived first)', True),
    'archive_txt': ('Archive and delete the .txt file', True),
    'import_txt': ('Import the .txt lyrics', False),
    'rename_sidecar': ('Rename to the track it belongs to', True),
    'delete_temp': ('Delete the leftover temp file', True),
    'to_plain': ('Rewrite as plain lyrics (archived first)', True),
    'convert_plain': ('Convert to plain lyrics (archived first)', True),
    'replace_with_candidate': ('Replace with the verified lyrics (archived first)', True),
    'stamp_provenance': ('Record where the lyrics came from', False),
    'set_null_source': ('Clear the stale source', False),
    'set_source': ('Record the lyrics source', False),
    'fix_invariants': ('Fix the row', False),
    'reset_backoff': ('Reset the retry schedule (staggered)', False),
    'reset_upgrade_backoff': ('Reset the upgrade retry schedule (staggered)', False),
    'mark_auto': ('Mark as downloaded (translation did nothing)', False),
    'fix_defaults': ('Keep one default profile', False),
    'promote_default': ('Make a profile the default', False),
    'rebuild_bool_columns': ('Rebuild the column as BOOLEAN', False),
    'full_sync': ('Run a full Lidarr sync', False),
    'relabel_version': ('Fix the version type', False),
    'trim_versions': ('Trim old versions', True),
    'fix_duration': ('Convert milliseconds to seconds', False),
    'backfill_hash': ('Record the content hash', False),
    'cover_missing': ('Mark the cover missing', False),
    'rename_cover': ('Fix the cover file extension', True),
    'delete_cover': ('Delete the broken cover and mark missing', True),
    'delete_files': ('Delete the lyrarr-written files', True),
}


def action_info(action, mode):
    label, destructive = ACTION_LABELS.get(action, (action, False)) if action else (None, False)
    return {'action': action, 'label': label, 'mode': mode, 'destructive': destructive}


class Check:
    """Base class. Subclasses set `spec` and implement detect/recheck/repair."""

    spec: CheckSpec

    def detect(self, ctx) -> Iterator[Issue]:  # pragma: no cover - interface
        raise NotImplementedError

    def recheck(self, ctx, issue_row) -> Issue | None:
        """Re-detect ONE entity right before a repair. Default: no recheck."""
        return None

    def repair(self, ctx, issue: Issue):
        handler = getattr(self, f'fix_{issue.fix_action}', None) if issue.fix_action else None
        if handler is None:
            return skipped(f'no repair for action {issue.fix_action!r}')
        return handler(ctx, issue)

    # helpers -------------------------------------------------------------
    def track_issue(self, row, title, fix_action=None, *, details=None, fix_mode=None, severity=None,
                    path=None, fix_label=None):
        return Issue(
            entity_type='track', entity_id=row.lidarrTrackId, entity_key=f'track:{row.lidarrTrackId}',
            title=title, severity=severity, path=path, details=dict(details or {}),
            fix_action=fix_action, fix_label=fix_label, fix_mode=fix_mode,
            track_id=row.lidarrTrackId, album_id=row.albumId, artist_id=row.artistId,
        )


class TrackCheck(Check):
    """A check over TableTracks rows: query() narrows in SQL, examine() decides.

    detect() iterates the (scope- and reachability-filtered) rows; recheck()
    re-reads the one row, re-applies query() and examine().
    """

    def query(self, ctx):  # pragma: no cover - interface
        raise NotImplementedError

    def examine(self, ctx, row) -> Issue | None:  # pragma: no cover - interface
        raise NotImplementedError

    def order_by(self):
        from lyrarr.app.database import TableTracks
        return TableTracks.lidarrTrackId

    def detect(self, ctx):
        rows = ctx.tracks(self, self.query(ctx).order_by(self.order_by()))
        total = len(rows)
        for i, row in enumerate(rows, 1):
            if ctx.cancelled:
                return
            ctx.progress(i, total, cursor=row.lidarrTrackId)
            try:
                issue = self.examine(ctx, row)
            except Exception as e:  # one bad row never aborts a check
                logger.warning(f"audit {self.spec.code}: track {row.lidarrTrackId} failed: {e}")
                continue
            if issue is not None:
                yield issue

    def recheck(self, ctx, issue_row):
        from lyrarr.app.database import TableTracks, database
        if issue_row.entity_id is None:
            return None
        row = database.execute(
            self.query(ctx).where(TableTracks.lidarrTrackId == issue_row.entity_id)
        ).scalars().first()
        if row is None:
            return None
        if not ctx.track_allowed(self, row):
            return None
        return self.examine(ctx, row)


REGISTRY: dict[str, Check] = {}
_CODES: dict[str, str] = {}


def register(cls):
    inst = cls()
    spec = inst.spec
    if spec.id in REGISTRY:
        raise ValueError(f'duplicate audit check {spec.id}')
    REGISTRY[spec.id] = inst
    _CODES[spec.code.upper()] = spec.id
    return cls


def resolve_check_id(value):
    """Accept a dotted id ('files.status_drift') or a code ('F-1')."""
    if value in REGISTRY:
        return value
    if isinstance(value, str):
        return _CODES.get(value.strip().upper())
    return None


def get_check(check_id):
    cid = resolve_check_id(check_id)
    return REGISTRY.get(cid) if cid else None


def ordered_checks(ids=None):
    checks = list(REGISTRY.values()) if ids is None else [REGISTRY[i] for i in ids if i in REGISTRY]
    position = {cid: i for i, cid in enumerate(REGISTRY)}
    return sorted(checks, key=lambda c: (c.spec.order, position[c.spec.id]))
