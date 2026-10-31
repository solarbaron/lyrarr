"""State checks S-1..S-15: database rows left inconsistent by older versions.

S-4, S-5, S-14 and S-15 are bootstrap_only one-shot legacy repairs: they
never run on the schedule, and S-4/S-5 only select backoff written by an
older version (*_set_at IS NULL), stamping it when they reset it, so no
audit run can ever undo the worker's own backoff.
"""

from __future__ import annotations

import os

from lyrarr.metadata.language_detect import is_synced_lyrics

from .. import repairs as rp
from ..registry import Check, CheckSpec, Issue, TrackCheck, failed, fixed, register, skipped
from .files import _T, _select, _text


@register
class StaleLyricsSource(TrackCheck):
    spec = CheckSpec(
        id='state.stale_lyrics_source', code='S-1',
        title='Lyrics source on tracks without lyrics',
        description='Tracks without lyrics that still record who wrote their (deleted) lyrics file.',
        category='state', severity='low', cost='db', fix_mode='auto', bootstrap=True, order=10,
    )

    def query(self, ctx):
        from lyrarr.app.database import or_
        T = _T()
        return _select().where(T.lyrics_source.is_not(None),
                               or_(T.lyrics_status.is_(None), T.lyrics_status.notin_(('available', 'manual'))))

    def examine(self, ctx, row):
        return self.track_issue(row, f"'{row.title}': stale source '{row.lyrics_source}'", 'set_null_source')

    def fix_set_null_source(self, ctx, issue):
        from lyrarr.app.database import or_
        T = _T()
        ok = rp.update_track(issue.track_id, (or_(T.lyrics_status.is_(None),
                                                  T.lyrics_status.notin_(('available', 'manual'))),),
                             lyrics_source=None)
        return fixed('source cleared') if ok else skipped('the track changed')


def _source_from_history(row):
    """(source, why) for an available NULL-source row from its lyrics history, else (None, None)."""
    from lyrarr.app.database import TableLyricsVersions, database, select
    hist = rp.newest_lyrics_history(row.lidarrTrackId)
    provider = hist.provider if hist is not None else None
    if provider in ('editor', 'upload', 'restored', 'manual'):
        return 'manual', f'history provider {provider}'
    if provider and provider.startswith('batch-translate-'):
        return 'translated', f'history provider {provider}'
    if provider == 'batch-sync-generate':
        return 'generated', 'history provider batch-sync-generate'
    V = TableLyricsVersions
    v = database.execute(
        select(V).where(V.lidarrTrackId == row.lidarrTrackId, V.translated_from.is_not(None))
        .order_by(V.timestamp.desc(), V.id.desc()).limit(1)).scalars().first()
    if v is not None and (hist is None or v.timestamp >= hist.timestamp):
        return 'translated', 'newest archived version is a translation source'
    return None, None


@register
class SourceBackfill(TrackCheck):
    spec = CheckSpec(
        id='state.source_backfill', code='S-2',
        title='Unknown origin of saved lyrics',
        description='Lyrics without a recorded source whose history shows you saved, translated or generated '
                    'them; recording it protects them from automatic replacement.',
        category='state', severity='medium', cost='db', fix_mode='auto', bootstrap=True, order=1,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status.in_(('available', 'manual')), T.lyrics_source.is_(None))

    def examine(self, ctx, row):
        source, why = _source_from_history(row)
        if source is None:
            return None
        return self.track_issue(row, f"'{row.title}': lyrics are {source} ({why})", 'set_source',
                                details={'source': source, 'why': why})

    def fix_set_source(self, ctx, issue):
        T = _T()
        source = issue.details.get('source')
        values = {'lyrics_source': source}
        if source == 'translated':
            values['language_source'] = 'translated'
        ok = rp.update_track(issue.track_id, (T.lyrics_source.is_(None),), **values)
        return fixed(f'source set to {source}') if ok else skipped('the track changed')


_STATUSES = ('missing', 'available', 'manual', 'blacklisted', 'instrumental')


@register
class RowInvariants(TrackCheck):
    spec = CheckSpec(
        id='state.row_invariants', code='S-3',
        title='Inconsistent track rows',
        description='hasLyrics that disagrees with the status, a synced flag or language on tracks without '
                    'lyrics, and missing or unknown statuses.',
        category='state', severity='low', cost='fs_stat', fix_mode='auto',
        degrades_without_library=True, bootstrap=True, order=11,
    )

    def query(self, ctx):
        return _select()

    def examine(self, ctx, row):
        problems = {}
        status = row.lyrics_status
        degraded = ctx.is_degraded(row)
        if status not in _STATUSES:
            if degraded:
                return self.track_issue(row, f"'{row.title}': unknown status {status!r} (folder unreachable)",
                                        None, fix_mode='report', details={'status': status, 'degraded': True})
            _, kind, _ = ctx.sidecar(row)
            problems['status'] = 'available' if kind == 'lyrics' else 'missing'
        else:
            want_has = status in ('available', 'manual')
            if bool(row.hasLyrics) != want_has or row.hasLyrics is None:
                problems['hasLyrics'] = want_has
            if status not in ('available', 'manual') and (row.is_synced or row.detected_language):
                has_file = False
                if status == 'missing':
                    if degraded:
                        has_file = True  # can't tell: leave it
                    else:
                        _, kind, _ = ctx.sidecar(row)
                        has_file = kind is not None
                if not has_file:
                    if row.is_synced:
                        problems['is_synced'] = False
                    if row.detected_language and (status in ('blacklisted', 'instrumental')
                                                  or row.language_source not in ('manual', 'translated')):
                        problems['detected_language'] = None
        if not problems:
            return None
        return self.track_issue(row, f"'{row.title}': {', '.join(sorted(problems))} inconsistent",
                                'fix_invariants', details={'set': problems, 'degraded': degraded})

    def fix_fix_invariants(self, ctx, issue):
        values = dict(issue.details.get('set') or {})
        if 'status' in values:
            status = values.pop('status')
            row = rp.fresh_track(issue.track_id)
            if status == 'available' and row is not None:
                from lyrarr.metadata.lyrics_store import adopt_sidecar
                if adopt_sidecar(row, keep_source=True):
                    return fixed('marked available from disk')
            values.update(lyrics_status='missing', hasLyrics=False, is_synced=False, lyrics_source=None)
        return fixed('row fixed') if rp.update_track(issue.track_id, **values) else skipped('track gone')


@register
class BackoffStale(TrackCheck):
    spec = CheckSpec(
        id='state.backoff_stale', code='S-4',
        title='Retry schedule from an older version',
        description='Missing tracks whose search back-off was written by an older version (before the '
                    'matching fixes). It is reset with a staggered retry. One-shot: never scheduled.',
        category='state', severity='medium', cost='db', fix_mode='auto', bootstrap=True, bootstrap_only=True,
        scheduled=False, default_enabled=False, order=28,
    )

    def query(self, ctx):
        from lyrarr.app.database import func, or_
        T = _T()
        return _select().where(
            T.lyrics_status == 'missing', T.lyrics_retry_set_at.is_(None), T.instrumental_evidence.is_(None),
            or_(T.instrumental_override.is_(None), T.instrumental_override != 'always'),
            or_(func.coalesce(T.lyrics_retry_count, 0) > 0, T.lyrics_retry_after.is_not(None),
                func.coalesce(T.lyrics_transient_count, 0) > 0, func.coalesce(T.lyrics_cooldown_count, 0) > 0))

    def examine(self, ctx, row):
        return self.track_issue(row, f"'{row.title}': old retry schedule ({row.lyrics_retry_count or 0} misses)",
                                'reset_backoff', details={'retry_count': row.lyrics_retry_count,
                                                          'retry_after': row.lyrics_retry_after})

    def fix_reset_backoff(self, ctx, issue):
        from datetime import datetime
        T = _T()
        now = datetime.now()
        ok = rp.update_track(issue.track_id, (T.lyrics_status == 'missing', T.lyrics_retry_set_at.is_(None)),
                             lyrics_retry_count=0, lyrics_retry_after=ctx.stagger(), lyrics_transient_count=0,
                             lyrics_cooldown_count=0, lyrics_retry_set_at=now)
        return fixed('retry schedule reset') if ok else skipped('the track changed')


def _prefer_synced(album_id, cache):
    if album_id in cache:
        return cache[album_id]
    from lyrarr.app.database import TableAlbums, TableArtists, TableProfiles, database, select
    album = database.execute(select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)).scalars().first()
    value = True
    if album is not None and album.override_prefer_synced is not None:
        value = bool(album.override_prefer_synced)
    else:
        pid = album.profileId if album is not None else None
        if pid is None and album is not None:
            pid = database.execute(select(TableArtists.profileId)
                                   .where(TableArtists.lidarrArtistId == album.artistId)).scalar()
        prof = None
        if pid is not None:
            prof = database.execute(select(TableProfiles).where(TableProfiles.id == pid)).scalars().first()
        if prof is None:
            prof = database.execute(select(TableProfiles).where(TableProfiles.is_default == True)  # noqa: E712
                                    ).scalars().first()
        if prof is not None and prof.prefer_synced_lyrics is not None:
            value = bool(prof.prefer_synced_lyrics)
    cache[album_id] = value
    return value


@register
class UpgradeCandidates(TrackCheck):
    spec = CheckSpec(
        id='state.upgrade_candidates', code='S-5',
        title='Upgrade retry schedule from an older version',
        description='Plain lyrics whose synced-upgrade back-off was written by an older version. It is reset '
                    'with a staggered retry; the daily upgrade pass re-checks them under its normal guards.',
        category='state', severity='low', cost='db', fix_mode='auto', bootstrap=True, bootstrap_only=True,
        scheduled=False, default_enabled=False, order=29,
    )

    def query(self, ctx):
        from lyrarr.app.database import func, or_
        T = _T()
        return _select().where(
            T.lyrics_status == 'available', or_(T.is_synced.is_(None), T.is_synced == False),  # noqa: E712
            or_(T.lyrics_source.is_(None), T.lyrics_source == 'auto'), T.upgrade_retry_set_at.is_(None),
            or_(func.coalesce(T.upgrade_retry_count, 0) > 0, T.upgrade_retry_after.is_not(None)))

    def examine(self, ctx, row):
        if not _prefer_synced(row.albumId, ctx.cache.setdefault('prefer_synced', {})):
            return None
        return self.track_issue(row, f"'{row.title}': old upgrade retry schedule", 'reset_upgrade_backoff',
                                details={'upgrade_retry_count': row.upgrade_retry_count})

    def fix_reset_upgrade_backoff(self, ctx, issue):
        from datetime import datetime
        T = _T()
        ok = rp.update_track(issue.track_id, (T.upgrade_retry_set_at.is_(None),), upgrade_retry_count=0,
                             upgrade_retry_after=ctx.stagger(), upgrade_retry_set_at=datetime.now())
        return fixed('upgrade retry schedule reset') if ok else skipped('the track changed')


@register
class TranslatedNoop(TrackCheck):
    spec = CheckSpec(
        id='state.translated_noop', code='S-6',
        title='"Translations" identical to the original',
        description='Lyrics marked translated whose text is the untranslated original (a failed translation '
                    'saved as-is). They are marked as downloaded lyrics again.',
        category='state', severity='medium', cost='fs_read', fix_mode='auto', needs_library=True, bootstrap=True,
        order=12,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status.in_(('available', 'manual')), T.lyrics_source == 'translated')

    def examine(self, ctx, row):
        from lyrarr.app.database import TableLyricsVersions, and_, database, or_, select
        from lyrarr.metadata.lyrics_store import content_hash
        path, kind, text = _text(ctx, row)
        if not text:
            return None
        V = TableLyricsVersions
        v = database.execute(
            select(V).where(V.lidarrTrackId == row.lidarrTrackId,
                            or_(V.translated_from.is_not(None),
                                and_(V.provider.like('batch-translate-%'), V.replaced_by.is_(None)),
                                V.replaced_by.like('batch-translate-%')))
            .order_by(V.timestamp.desc(), V.id.desc()).limit(1)).scalars().first()
        if v is None or content_hash(v.content) != content_hash(text):
            return None
        return self.track_issue(row, f"'{row.title}': the translation equals the original", 'mark_auto', path=path,
                                details={'version_id': v.id})

    @staticmethod
    def _origin_source(track_id, version_id):
        """Source of the untranslated original: 'manual' / 'generated' when the
        user (or Whisper) made it, 'auto' only for a provider download, None
        when the origin is unknown."""
        from lyrarr.app.database import TableHistory, TableLyricsVersions, database, select
        V = TableLyricsVersions
        v = database.execute(select(V).where(V.id == version_id)).scalars().first() if version_id else None
        origin = v.provider if v is not None else None
        if not origin or origin.startswith('batch-translate-') or origin == 'unknown':
            H = TableHistory
            q = select(H.provider).where(
                H.lidarrTrackId == track_id, H.metadata_type == 'lyrics', H.action.in_((1, 2, 4)),
                H.provider.is_not(None), ~H.provider.like('batch-translate-%'))
            if v is not None and v.timestamp is not None:
                q = q.where(H.timestamp <= v.timestamp)
            origin = database.execute(q.order_by(H.timestamp.desc(), H.id.desc()).limit(1)).scalar()
        if not origin or origin == 'unknown':
            return None
        if origin in ('editor', 'upload', 'restored', 'manual'):
            return 'manual'
        if origin == 'batch-sync-generate':
            return 'generated'
        if origin.startswith(('batch-', 'audit:', 'user-', 'lrc-')):
            return None
        return 'auto'

    def fix_mark_auto(self, ctx, issue):
        from lyrarr.metadata.language_detect import detect_language
        from lyrarr.metadata.lyrics_store import content_hash
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read')
        details = issue.details if isinstance(getattr(issue, 'details', None), dict) else {}
        source = self._origin_source(issue.track_id, details.get('version_id'))
        T = _T()
        values = dict(lyrics_source=source, language_source='detected', detected_language=detect_language(text))
        # The file equals the archived original, so for a provider download the
        # provenance is proven; for anything else the hash stays unset.
        if source == 'auto':
            values['lyrics_content_hash'] = content_hash(text)
        ok = rp.update_track(issue.track_id, (T.lyrics_source == 'translated',), **values)
        label = {'auto': 'marked as downloaded lyrics', 'manual': 'marked as your own lyrics',
                 'generated': 'marked as generated lyrics'}.get(source, 'marked as lyrics of unknown origin')
        return fixed(label) if ok else skipped('the track changed')


def _profiles():
    from lyrarr.app.database import TableProfiles, database, select, text
    rows = database.execute(select(TableProfiles).order_by(TableProfiles.id)).scalars().all()
    raw = dict(database.execute(text('SELECT id, is_default FROM table_profiles')).all())
    def truthy(v):
        return str(v).strip().lower() in ('1', 'true', 'yes', 'on')
    return rows, [r.id for r in rows if truthy(raw.get(r.id))]


@register
class ProfilesDefault(Check):
    spec = CheckSpec(
        id='state.profiles_default', code='S-7',
        title='Default profile',
        description='Exactly one profile must be the default. Extra defaults are cleared automatically; '
                    'with none, choose which one to promote.',
        category='state', severity='high', cost='db', fix_mode='auto', bootstrap=True, order=3,
    )

    def _examine(self):
        from lyrarr.app.config import settings
        rows, defaults = _profiles()
        if len(defaults) == 1 or not rows:
            return None
        preferred = getattr(settings.general, 'default_profile_id', None)
        if len(defaults) > 1:
            keep = preferred if preferred in defaults else min(defaults)
            return Issue(entity_type='profile', entity_id=keep, entity_key='profiles:default',
                         title=f'{len(defaults)} profiles are marked default', fix_action='fix_defaults',
                         details={'defaults': defaults, 'keep': keep})
        ids = [r.id for r in rows]
        named = next((r.id for r in rows if r.name == 'Default'), None)
        keep = preferred if preferred in ids else (named or min(ids))
        return Issue(entity_type='profile', entity_id=keep, entity_key='profiles:default',
                     title='No profile is marked default', fix_action='promote_default', fix_mode='review',
                     details={'keep': keep})

    def detect(self, ctx):
        issue = self._examine()
        if issue is not None:
            yield issue

    def recheck(self, ctx, issue_row):
        return self._examine()

    def _set_default(self, keep):
        from lyrarr.app.database import TableProfiles, database, update
        database.execute(update(TableProfiles).values(is_default=False))
        database.execute(update(TableProfiles).where(TableProfiles.id == keep).values(is_default=True))
        return fixed(f'profile {keep} is the only default')

    def fix_fix_defaults(self, ctx, issue):
        return self._set_default(issue.details['keep'])

    def fix_promote_default(self, ctx, issue):
        return self._set_default(issue.details['keep'])


@register
class BoolColumns(Check):
    spec = CheckSpec(
        id='state.bool_columns', code='S-8',
        title='Text values in boolean columns',
        description='Boolean columns holding text like "True" (written by old versions), which reads as '
                    'enabled even when off.',
        category='state', severity='high', cost='db', fix_mode='auto', bootstrap=True, order=2,
    )

    def _bad(self):
        from sqlalchemy import inspect as sa_inspect

        from lyrarr.app import database as db
        inspector = sa_inspect(db.engine)
        out = []
        for table, cols in db._BOOL_MIGRATION_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            existing = {c['name'] for c in inspector.get_columns(table)}
            for col in cols:
                if col not in existing:
                    continue
                n = db.database.execute(db.text(
                    f"SELECT COUNT(*) FROM {table} WHERE typeof({col}) NOT IN ('integer', 'null')")).scalar()
                if n:
                    out.append((table, col, n))
        return out

    def detect(self, ctx):
        for table, col, n in self._bad():
            yield Issue(entity_type='db', entity_id=None, entity_key=f'column:{table}.{col}',
                        title=f'{table}.{col}: {n} non-boolean value(s)', fix_action='rebuild_bool_columns',
                        details={'rows': n})

    def recheck(self, ctx, issue_row):
        for table, col, n in self._bad():
            if issue_row.entity_key == f'column:{table}.{col}':
                return Issue(entity_type='db', entity_id=None, entity_key=issue_row.entity_key,
                             title=issue_row.title, fix_action='rebuild_bool_columns', details={'rows': n})
        return None

    def fix_rebuild_bool_columns(self, ctx, issue):
        from lyrarr.app import database as db
        if not ctx.cache.get('bool_rebuilt'):
            db._rebuild_text_bool_columns()
            db._migrate_bool_columns()
            ctx.cache['bool_rebuilt'] = True
        table, col = issue.entity_key.split(':', 1)[1].split('.', 1)
        n = db.database.execute(db.text(
            f"SELECT COUNT(*) FROM {table} WHERE typeof({col}) NOT IN ('integer', 'null')")).scalar()
        if n:
            # Values the migration can't map ('yes', 'on', ...): coerce them.
            db.database.execute(db.text(
                f"UPDATE {table} SET {col} = CASE WHEN lower(CAST({col} AS TEXT)) IN "
                f"('0','false','','no','off','none') THEN 0 ELSE 1 END "
                f"WHERE typeof({col}) NOT IN ('integer','null')"))
        return fixed('column values are booleans')


@register
class DuplicateRows(Check):
    spec = CheckSpec(
        id='state.duplicate_rows', code='S-9',
        title='Tracks sharing one audio file',
        description='Several track rows point at the same audio file (left by Lidarr re-imports). A full '
                    'Lidarr sync removes stale rows; leftovers are reported.',
        category='state', severity='low', cost='db', fix_mode='auto', scheduled=False, order=13,
    )

    def _dups(self, ctx):
        from lyrarr.app.database import database, func, select
        T = _T()
        q = select(T.path, func.count()).where(T.path.is_not(None)).group_by(T.path).having(func.count() > 1)
        return database.execute(q).all()

    @staticmethod
    def _lidarr_enabled():
        from lyrarr.app.config import settings
        return bool(settings.general.use_lidarr)

    def _issue(self, path, n, lidarr=True):
        if not lidarr:  # the only repair is a Lidarr sync: report-only without Lidarr
            return Issue(entity_type='file', entity_id=None, entity_key=f'file:{path}', path=path,
                         title=f'{n} track rows share {os.path.basename(path)}', fix_mode='report',
                         details={'rows': n, 'reason': 'Lidarr is not enabled'})
        return Issue(entity_type='file', entity_id=None, entity_key=f'file:{path}', path=path,
                     title=f'{n} track rows share {os.path.basename(path)}', fix_action='full_sync',
                     details={'rows': n})

    def detect(self, ctx):
        lidarr = self._lidarr_enabled()
        dups = self._dups(ctx)
        if dups and not lidarr:
            ctx.note('report only: Lidarr is not enabled (a full Lidarr sync is the repair)')
        for path, n in dups:
            yield self._issue(path, n, lidarr)

    def recheck(self, ctx, issue_row):
        for path, n in self._dups(ctx):
            if path == issue_row.path:
                return self._issue(path, n, self._lidarr_enabled())
        return None

    def fix_full_sync(self, ctx, issue):
        if not self._lidarr_enabled():
            return skipped('Lidarr is not enabled')
        if 'full_sync_ok' not in ctx.cache:
            from lyrarr.lidarr import sync
            ctx.cache['full_sync_ok'] = bool(sync.run_full_sync())
        if not ctx.cache['full_sync_ok']:
            return failed('the full Lidarr sync did not complete (is Lidarr reachable?)')
        if self.recheck(ctx, _PathOnly(issue.path)) is not None:
            return skipped('still shared after a full sync (reported)')
        return fixed('resolved by a full Lidarr sync')


class _PathOnly:
    def __init__(self, path):
        self.path = path


@register
class DurationSuspect(Check):
    spec = CheckSpec(
        id='state.duration_suspect', code='S-10',
        title='Missing durations and database integrity',
        description='Tracks without a duration or longer than an hour (a re-sync fixes them), foreign-key '
                    'violations, and path mappings (not supported).',
        category='state', severity='info', cost='db', fix_mode='report', order=14,
    )

    def detect(self, ctx):
        from lyrarr.app.config import settings
        from lyrarr.app.database import database, func, select, text
        T = _T()
        null_n = database.execute(ctx.scope_tracks(select(func.count()).select_from(T)
                                                   .where(T.duration.is_(None), T.path.is_not(None)))).scalar()
        if null_n:
            yield Issue(entity_type='db', entity_id=None, entity_key='tracks:duration_null',
                        title=f'{null_n} track(s) without a duration', details={'rows': null_n})
        for row in database.execute(ctx.scope_tracks(_select().where(T.duration > 3600))).scalars().all():
            yield self.track_issue(row, f"'{row.title}': duration {row.duration} s", None,
                                   details={'duration': row.duration})
        try:
            fk = database.execute(text('PRAGMA foreign_key_check')).all()
        except Exception:
            fk = []
        for r in fk[:500]:
            yield Issue(entity_type='db', entity_id=None, entity_key=f'fk:{r[0]}:{r[1]}',
                        title=f'Foreign key violation in {r[0]} (row {r[1]} → {r[2]})')
        if getattr(settings.general, 'path_mappings', None):
            yield Issue(entity_type='db', entity_id=None, entity_key='settings:path_mappings',
                        title='Path mappings are configured but not implemented')


@register
class VersionsOverCap(Check):
    spec = CheckSpec(
        id='state.versions_over_cap', code='S-11',
        title='Archived lyrics versions',
        description='Version rows labelled with the wrong type are relabelled; tracks with more than 5 '
                    'archived versions can be trimmed (pinned evidence and the newest translation source '
                    'are always kept).',
        category='state', severity='low', cost='db', fix_mode='auto', order=99,
    )

    def detect(self, ctx):
        from lyrarr.app.database import TableLyricsVersions, database, func, select
        V = TableLyricsVersions
        for v in database.execute(select(V.id, V.lidarrTrackId, V.lyrics_type, V.content)).all():
            want = 'synced' if is_synced_lyrics(v.content or '') else 'plain'
            if v.lyrics_type != want:
                yield Issue(entity_type='version', entity_id=v.id, entity_key=f'version:{v.id}',
                            title=f'Version {v.id} is {want}, labelled {v.lyrics_type}', fix_action='relabel_version',
                            track_id=v.lidarrTrackId, details={'type': want})
        from lyrarr.metadata.lyrics_store import pinned_version_ids
        counts = database.execute(select(V.lidarrTrackId, func.count()).group_by(V.lidarrTrackId)
                                  .having(func.count() > 5)).all()
        for track_id, n in counts:
            drop = _trim_ids(track_id, pinned_version_ids(track_id))
            if not drop:
                continue
            row = rp.fresh_track(track_id)
            if row is None:
                continue
            yield self.track_issue(row, f"'{row.title}': {n} archived versions", 'trim_versions', fix_mode='review',
                                   details={'versions': n, 'drop': len(drop)})

    def recheck(self, ctx, issue_row):
        from lyrarr.app.database import TableLyricsVersions, database, select
        if issue_row.entity_type == 'version':
            v = database.execute(select(TableLyricsVersions).where(TableLyricsVersions.id == issue_row.entity_id)
                                 ).scalars().first()
            if v is None:
                return None
            want = 'synced' if is_synced_lyrics(v.content or '') else 'plain'
            if v.lyrics_type == want:
                return None
            return Issue(entity_type='version', entity_id=v.id, entity_key=issue_row.entity_key, title=issue_row.title,
                         fix_action='relabel_version', track_id=v.lidarrTrackId, details={'type': want})
        from lyrarr.metadata.lyrics_store import pinned_version_ids
        if not _trim_ids(issue_row.entity_id, pinned_version_ids(issue_row.entity_id)):
            return None
        from ..runner import issue_from_row
        return issue_from_row(issue_row)

    def fix_relabel_version(self, ctx, issue):
        from lyrarr.app.database import TableLyricsVersions, database, update
        database.execute(update(TableLyricsVersions).where(TableLyricsVersions.id == issue.entity_id)
                         .values(lyrics_type=issue.details['type']))
        return fixed('relabelled')

    def fix_trim_versions(self, ctx, issue):
        from lyrarr.app.database import TableLyricsVersions, database, delete
        from lyrarr.metadata.lyrics_store import pinned_version_ids
        drop = _trim_ids(issue.track_id, pinned_version_ids(issue.track_id))
        if not drop:
            return skipped('nothing to trim')
        database.execute(delete(TableLyricsVersions).where(TableLyricsVersions.id.in_(drop)))
        return fixed(f'removed {len(drop)} old version(s)')


def _trim_ids(track_id, pinned):
    from lyrarr.app.database import TableLyricsVersions, database, select
    V = TableLyricsVersions
    rows = database.execute(select(V.id, V.translated_from).where(V.lidarrTrackId == track_id)
                            .order_by(V.timestamp.desc(), V.id.desc())).all()
    keep = {r.id for r in rows[:5]} | set(pinned)
    newest_tr = next((r.id for r in rows if r.translated_from), None)
    if newest_tr is not None:
        keep.add(newest_tr)
    return [r.id for r in rows if r.id not in keep]


@register
class RecreatedArtists(Check):
    spec = CheckSpec(
        id='state.recreated_artists', code='S-12',
        title='Artists re-created by a Lidarr sync',
        description='Artists whose local row is much newer than when Lidarr added them: their earlier '
                    'lyrics state may have been lost (report; needs Lidarr).',
        category='state', severity='low', cost='network', fix_mode='report', scheduled=False,
        default_enabled=False, order=15,
    )

    def detect(self, ctx):
        from datetime import datetime, timedelta

        from lyrarr.app.config import settings
        from lyrarr.app.database import TableArtists, database, select
        if not settings.general.use_lidarr:
            ctx.note('skipped: Lidarr is not enabled')
            return
        try:
            from lyrarr.lidarr.api_client import lidarr_api
            remote = {a.get('id'): a for a in (lidarr_api.get_artists() or [])}
        except Exception as e:
            ctx.note(f'skipped: Lidarr not reachable ({e})')
            return
        for artist in database.execute(ctx.scope_artists(select(TableArtists))).scalars().all():
            added = (remote.get(artist.lidarrArtistId) or {}).get('added')
            if not added or not artist.created_at_timestamp:
                continue
            try:
                added_dt = datetime.fromisoformat(str(added).replace('Z', '+00:00')).replace(tzinfo=None)
            except ValueError:
                continue
            if artist.created_at_timestamp - added_dt > timedelta(days=7):
                yield Issue(entity_type='artist', entity_id=artist.lidarrArtistId,
                            entity_key=f'artist:{artist.lidarrArtistId}', artist_id=artist.lidarrArtistId,
                            title=f"{artist.name}: re-created {artist.created_at_timestamp:%Y-%m-%d}, "
                                  f"added to Lidarr {added_dt:%Y-%m-%d}",
                            details={'added': str(added), 'created': artist.created_at_timestamp})


@register
class BlacklistedWithFile(TrackCheck):
    spec = CheckSpec(
        id='state.blacklisted_with_file', code='S-13', requires_download_lock=True,
        title='Skipped tracks with a lyrics file',
        description='Tracks you skipped (blacklisted) that still have a lyrics file. Review: archive, '
                    'blacklist and remove it, honouring the skip.',
        category='state', severity='low', cost='fs_read', fix_mode='review', needs_library=True, order=16,
    )

    def query(self, ctx):
        return _select().where(_T().lyrics_status == 'blacklisted')

    def examine(self, ctx, row):
        path, kind, _ = ctx.sidecar(row)
        if kind != 'lyrics':
            return None
        return self.track_issue(row, f"'{row.title}' is skipped but has a lyrics file", 'archive_remove_wrong',
                                path=path)

    def fix_archive_remove_wrong(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import archive_and_remove
        row = rp.fresh_track(issue.track_id)
        if row is None:
            return skipped('track no longer exists')
        if not archive_and_remove(row, f'audit:{self.spec.code}', blacklist_reason=self.spec.code):
            return failed('the lyrics file could not be archived, so it was kept')
        ctx.forget(issue.path)
        return fixed('archived, blacklisted and removed')


@register
class DurationMs(TrackCheck):
    spec = CheckSpec(
        id='state.duration_ms', code='S-14',
        title='Durations stored in milliseconds',
        description='Track durations over 3 hours are milliseconds written by an older version; they are '
                    'converted to seconds before any track is searched again.',
        category='state', severity='high', cost='db', fix_mode='auto', bootstrap=True, bootstrap_only=True,
        scheduled=False, default_enabled=False, order=0,
    )

    def query(self, ctx):
        from sqlalchemy import false
        if ctx.cache.get('lidarr_synced'):
            # Phase 0 just rewrote every duration (in seconds) from Lidarr: a
            # value over 3 h now is a genuinely long track (a 4 h mix), not ms.
            return _select().where(false())
        return _select().where(_T().duration > 10800)

    def examine(self, ctx, row):
        secs = int(round(row.duration / 1000))
        new = secs if 1 <= secs <= 10800 else None
        return self.track_issue(row, f"'{row.title}': duration {row.duration}", 'fix_duration',
                                details={'old': row.duration, 'new': new})

    def fix_fix_duration(self, ctx, issue):
        T = _T()
        ok = rp.update_track(issue.track_id, (T.duration > 10800,), duration=issue.details.get('new'))
        return fixed(f"duration set to {issue.details.get('new')}") if ok else skipped('already fixed')


@register
class ContentHashBackfill(TrackCheck):
    spec = CheckSpec(
        id='state.content_hash_backfill', code='S-15',
        title='Automatic lyrics without a content hash',
        description='Downloaded lyrics written before content hashes were recorded. The hash is recorded '
                    'when lyrarr\'s history proves the file is unchanged; otherwise the origin becomes '
                    'unknown so automatic passes leave the file alone.',
        category='state', severity='low', cost='fs_read', fix_mode='auto', needs_library=True, bootstrap=True,
        bootstrap_only=True, scheduled=False, default_enabled=False, order=17,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status == 'available', T.lyrics_source == 'auto',
                               T.lyrics_content_hash.is_(None))

    def examine(self, ctx, row):
        path, kind, _ = ctx.sidecar(row)
        if kind is None:
            return None
        return self.track_issue(row, f"'{row.title}': no content hash", 'backfill_hash', path=path)

    def fix_backfill_hash(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import backfill_content_hash
        result = backfill_content_hash(issue.track_id)
        if result == 'unchanged':
            return skipped('nothing to record')
        ctx.cache['s15_' + result] = ctx.cache.get('s15_' + result, 0) + 1
        return fixed('hash recorded' if result == 'set' else 'file changed since lyrarr wrote it: origin now unknown',
                     result=result)
