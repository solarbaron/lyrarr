
"""Periodic database housekeeping."""

import logging
from datetime import datetime, timedelta

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)


def run_maintenance():
    """Daily housekeeping: prune old history and reconcile lyrics with disk."""
    from lyrarr.app.database import database

    try:
        _prune_history()
        _reconcile_lyrics_files()
    finally:
        database.remove()


def _prune_history():
    """Prune old history rows so the database doesn't grow unbounded.

    Retention is controlled by general.history_retention_days (0 = keep
    forever). Lyrics version archives are capped per-track at write time in
    lyrics_store, so they need no scheduled pruning.
    """
    from lyrarr.app.database import TableHistory, database, delete, func, select

    retention_days = int(getattr(settings.general, 'history_retention_days', 90) or 0)
    if retention_days <= 0:
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    try:
        old_count = database.execute(
            select(func.count()).select_from(TableHistory)
            .where(TableHistory.timestamp < cutoff)
        ).scalar() or 0
        if old_count:
            database.execute(
                delete(TableHistory).where(TableHistory.timestamp < cutoff)
            )
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
    """
    from lyrarr.app.database import TableTracks, database, select
    from lyrarr.metadata.lyrics_store import reconcile_track_lyrics

    try:
        tracks = database.execute(
            select(TableTracks).where(
                TableTracks.lyrics_status.in_(['available', 'missing']),
                TableTracks.path.is_not(None),
            )
        ).scalars().all()

        changed = sum(1 for t in tracks if reconcile_track_lyrics(t))
        if changed:
            logger.info(
                f"Maintenance: reconciled {changed} track(s) whose lyrics files "
                f"changed on disk"
            )
    except Exception as e:
        logger.error(f"Maintenance: lyrics reconciliation failed: {e}")
