"""AuditContext: per-run state shared by the checks (PLAN §4.1).

Holds the run id/mode/trigger, the cancel flag, the per-root reachability
result, the scope filter, a sidecar read cache (each file is read once per
run), the network budget, the instrumental change counters for the one
`instrumental_bulk` event per check, and the throttled progress reporter.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class AuditContext:
    def __init__(self, run_id, mode, trigger, *, cancel_event, include=None, scope=None,
                 network_limit=None, resume_cursor=None, settings_snapshot=None):
        from lyrarr.metadata.lyrics_store import Reachability
        self.run_id = run_id
        self.mode = mode
        self.trigger = trigger
        self.cancel_event = cancel_event
        self.include = dict(include or {})
        self.scope = dict(scope or {})
        self.network_limit = network_limit
        self.network_used = 0
        self.resume_cursor = resume_cursor
        self.settings = dict(settings_snapshot or {})
        self.now = datetime.now()
        self.reach = Reachability({})
        self.phase = 'detect'
        self.check = None
        self.check_index = 0
        self.checks_total = 0
        self.current = 0
        self.total = 0
        self.cursor = None
        self.counts = {'flagged': 0, 'cleared': 0, 'hinted': 0}
        self.unreachable_skipped = 0
        self.degraded = 0
        self.stagger_n = 0
        self.replace_n = 0
        self.notes = []
        self.cache = {}
        self._sidecars = {}

    # -- identity -----------------------------------------------------------
    @property
    def actor(self):
        return 'bootstrap' if self.trigger == 'bootstrap' else 'audit'

    @property
    def cancelled(self):
        return self.cancel_event is not None and self.cancel_event.is_set()

    # -- check lifecycle (called by the runner) ------------------------------
    def begin_check(self, check, index, total, phase='detect'):
        self.check = check
        self.check_index = index
        self.checks_total = total
        self.phase = phase
        self.current = 0
        self.total = 0
        self.counts = {'flagged': 0, 'cleared': 0, 'hinted': 0}
        self.unreachable_skipped = 0
        self.degraded = 0
        self.notes = []

    def progress(self, current, total, cursor=None, message=None):
        self.current, self.total = current, total
        if cursor is not None:
            self.cursor = cursor
        check = self.check
        if check is None:
            return
        try:
            from lyrarr.app.event_handler import emit_throttled
            emit_throttled('lyrics_audit_progress', {
                'run_id': self.run_id, 'phase': self.phase, 'check_id': check.spec.id,
                'check_title': check.spec.title, 'check_index': self.check_index,
                'checks': self.checks_total, 'current': current, 'total': total,
                'message': message or f'{check.spec.title}: {current}/{total}',
            }, key=f'audit-{self.run_id}', min_interval=1.0, force=current in (0, 1) or current >= total)
        except Exception as e:
            logger.debug(f"audit progress event failed: {e}")

    # -- stagger --------------------------------------------------------------
    def stagger(self, n=None):
        """retry_after for a bulk requeue of n items: None (next pass) for n <= 20,
        else now + uniform(0, min(72 h, ceil(n/100) × 6 h))."""
        n = self.stagger_n if n is None else n
        if n <= 20:
            return None
        from lyrarr.metadata.lyrics_store import stagger_until
        return stagger_until(n)

    def replace_retry_after(self):
        return self.stagger(self.replace_n)

    # -- network budget -------------------------------------------------------
    def network_take(self):
        if self.network_limit is not None and self.network_used >= self.network_limit:
            return False
        self.network_used += 1
        return True

    # -- scope / reachability -------------------------------------------------
    def scope_tracks(self, query):
        from lyrarr.app.database import TableTracks
        artist_ids = self.scope.get('artistIds') or []
        album_ids = self.scope.get('albumIds') or []
        if artist_ids:
            query = query.where(TableTracks.artistId.in_(artist_ids))
        if album_ids:
            query = query.where(TableTracks.albumId.in_(album_ids))
        return query

    def scope_albums(self, query):
        from lyrarr.app.database import TableAlbums
        artist_ids = self.scope.get('artistIds') or []
        album_ids = self.scope.get('albumIds') or []
        if artist_ids:
            query = query.where(TableAlbums.artistId.in_(artist_ids))
        if album_ids:
            query = query.where(TableAlbums.lidarrAlbumId.in_(album_ids))
        return query

    def scope_artists(self, query):
        from lyrarr.app.database import TableArtists
        artist_ids = self.scope.get('artistIds') or []
        if artist_ids:
            query = query.where(TableArtists.lidarrArtistId.in_(artist_ids))
        return query

    def path_ok(self, path):
        return self.reach.ok_for(path)

    def is_degraded(self, row):
        return not self.reach.ok_for(getattr(row, 'path', None))

    def track_allowed(self, check, row):
        spec = check.spec
        if not (spec.needs_library or spec.degrades_without_library):
            return True
        if self.reach.ok_for(row.path):
            return True
        return spec.degrades_without_library

    def tracks(self, check, query):
        """Rows of a scoped TableTracks query, minus rows under unreachable roots
        for needs_library checks (counted for the check's note). Checks that
        degrade keep those rows; is_degraded(row) tells them apart."""
        from lyrarr.app.database import database
        rows = database.execute(self.scope_tracks(query)).scalars().all()
        spec = check.spec
        if not (spec.needs_library or spec.degrades_without_library):
            return rows
        out = []
        for row in rows:
            if self.reach.ok_for(row.path):
                out.append(row)
            elif spec.degrades_without_library:
                self.degraded += 1
                out.append(row)
            else:
                self.unreachable_skipped += 1
        return out

    def albums(self, check, query):
        from lyrarr.app.database import database
        rows = database.execute(self.scope_albums(query)).scalars().all()
        if not check.spec.needs_library:
            return rows
        out = []
        for row in rows:
            if self.reach.ok_for(row.path):
                out.append(row)
            else:
                self.unreachable_skipped += 1
        return out

    def artists(self, check, query):
        from lyrarr.app.database import database
        rows = database.execute(self.scope_artists(query)).scalars().all()
        out = []
        for row in rows:
            if row.path and self.reach.ok_for(row.path):
                out.append(row)
            else:
                self.unreachable_skipped += 1
        return out

    # -- file cache -------------------------------------------------------------
    def sidecar(self, row):
        """(path, kind, SidecarRead|None) for the track's `<stem>.lrc`, read once per run.

        kind: None (no file) | classify_lyrics_text() value | 'unknown' (not
        valid UTF-8 / unreadable). Files are only read under reachable roots.
        """
        from lyrarr.metadata.lyrics_store import read_sidecar_kind, sidecar_path
        path = sidecar_path(row)
        if not path:
            return None, None, None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        key = (path, mtime)
        cached = self._sidecars.get(path)
        if cached is not None and cached[0] == key:
            return path, cached[1], cached[2]
        if mtime is None:
            kind, sr = None, None
        else:
            kind, sr = read_sidecar_kind(path)
        if len(self._sidecars) > 20000:
            self._sidecars.clear()
        self._sidecars[path] = (key, kind, sr)
        return path, kind, sr

    def forget(self, path):
        self._sidecars.pop(path, None)

    def note(self, text):
        if text and text not in self.notes:
            self.notes.append(text)
