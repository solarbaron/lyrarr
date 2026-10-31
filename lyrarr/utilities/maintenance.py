
"""Periodic database housekeeping."""

import logging
from datetime import datetime, timedelta

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)


def run_maintenance():
    """Daily housekeeping: prune old history, reconcile lyrics with disk and
    purge expired track tombstones."""
    from lyrarr.app.database import database

    try:
        _prune_history()
        _reconcile_lyrics_files()
        _purge_tombstones()
    finally:
        database.remove()


def _prune_history():
    """Prune old history rows so the database doesn't grow unbounded.

    Retention is controlled by general.history_retention_days (0 = keep
    forever). History rows pinned as evidence by an open audit issue are kept.
    Lyrics version archives are capped per-track at write time in
    lyrics_store, so they need no scheduled pruning.
    """
    from lyrarr.app.database import TableHistory, database, delete, func, select
    from lyrarr.metadata.lyrics_store import pinned_history_ids_subquery

    retention_days = int(getattr(settings.general, 'history_retention_days', 90) or 0)
    if retention_days <= 0:
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    try:
        prunable = (
            TableHistory.timestamp < cutoff,
            TableHistory.id.not_in(pinned_history_ids_subquery()),
        )
        old_count = database.execute(
            select(func.count()).select_from(TableHistory).where(*prunable)
        ).scalar() or 0
        if old_count:
            database.execute(delete(TableHistory).where(*prunable))
            logger.info(
                f"Maintenance: pruned {old_count} history entries older than "
                f"{retention_days} days"
            )
    except Exception as e:
        logger.error(f"Maintenance: history pruning failed: {e}")


def _reconcile_lyrics_files():
    """Reconcile every track's lyrics status with what's actually on disk.

    Catches .lrc files deleted or added outside lyrarr, so stale 'available'
    rows flip back to 'missing' (and get re-fetched) at most a day after the
    file disappears — even if no Lidarr sync or album view touches them first.
    Instrumental rows with real lyrics next to a non-STRONG title are adopted.
    Tracks under an unreachable library root are skipped, and every other
    track is still gated on its own audio file (reconcile_track_lyrics).
    """
    from lyrarr.app.database import TableTracks, database, select
    from lyrarr.metadata.lyrics_store import library_reachability, reconcile_track_lyrics

    try:
        reach = library_reachability()
        tracks = database.execute(
            select(TableTracks).where(
                TableTracks.lyrics_status.in_(['available', 'missing', 'instrumental']),
                TableTracks.path.is_not(None),
            )
        ).scalars().all()

        skipped = 0
        changed = 0
        for t in tracks:
            if not reach.ok_for(t.path):
                skipped += 1
                continue
            if reconcile_track_lyrics(t):
                changed += 1
        if changed:
            logger.info(
                f"Maintenance: reconciled {changed} track(s) whose lyrics files "
                f"changed on disk"
            )
        if skipped:
            roots = ', '.join(s.root for s in reach.unreachable)
            logger.warning(f"Maintenance: skipped {skipped} track(s) under unreachable music folder(s): {roots}")
    except Exception as e:
        logger.error(f"Maintenance: lyrics reconciliation failed: {e}")


def _purge_tombstones():
    """Drop the kept state of removed tracks after 30 days."""
    from lyrarr.metadata.lyrics_store import purge_tombstones

    try:
        n = purge_tombstones()
        if n:
            logger.info(f"Maintenance: purged {n} expired track tombstone(s)")
    except Exception as e:
        logger.error(f"Maintenance: tombstone purge failed: {e}")
