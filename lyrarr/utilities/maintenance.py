
"""Periodic database housekeeping."""

import logging
from datetime import datetime, timedelta

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)


def run_maintenance():
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
    finally:
        database.remove()
