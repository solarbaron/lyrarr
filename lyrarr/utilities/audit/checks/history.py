"""History-based checks H-1..H-3 (review/report only).

H-1 pins the archived version and the two history rows it relies on, so the
evidence survives the per-track version trim and the 90-day history prune
until the issue is reviewed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import repairs as rp
from ..registry import Check, CheckSpec, Issue, TrackCheck, register
from .files import _T, _select

# becff4c (LRCLIB publish duration fix) commit time: publishes before it
# almost certainly sent duration 0/1.
_PUBLISH_FIX_AT = datetime(2026, 10, 30, 17, 13)


def _curated_history_filter():
    from lyrarr.app.database import TableHistory, and_, or_
    H = TableHistory
    return or_(H.action == 4,
               and_(H.action == 1, or_(H.provider.in_(rp.CURATED_PROVIDERS), H.provider.like('batch-translate-%'))))


@register
class CuratedSuperseded(TrackCheck):
    spec = CheckSpec(
        id='history.curated_superseded', code='H-1', requires_download_lock=True,
        title='Your lyrics were replaced by a download',
        description='Lyrics you saved (editor, upload, restore, translation) that an automatic download or '
                    'synced upgrade later replaced. The archived copy is kept until you review it. Interactive '
                    'picks and profile auto-translations from before 11ad79a cannot be detected.',
        category='history', severity='high', cost='db', fix_mode='review', pins_evidence=True, order=70,
    )

    def query(self, ctx):
        from lyrarr.app.database import TableHistory, or_, select
        H, T = TableHistory, _T()
        curated = select(H.lidarrTrackId).where(H.metadata_type == 'lyrics', H.lidarrTrackId.is_not(None),
                                                _curated_history_filter())
        return _select().where(T.lyrics_status.in_(('available', 'manual')),
                               or_(T.lyrics_source.is_(None), T.lyrics_source == 'auto'),
                               T.lidarrTrackId.in_(curated))

    def examine(self, ctx, row):
        from lyrarr.app.database import TableHistory, TableLyricsVersions, and_, database, or_, select
        H, V = TableHistory, TableLyricsVersions
        curated = database.execute(
            select(H).where(H.lidarrTrackId == row.lidarrTrackId, H.metadata_type == 'lyrics', _curated_history_filter())
            .order_by(H.timestamp.desc(), H.id.desc()).limit(1)).scalars().first()
        if curated is None:
            return None
        replacing = database.execute(
            select(H).where(
                H.lidarrTrackId == row.lidarrTrackId, H.metadata_type == 'lyrics', H.action.in_((1, 2)),
                H.timestamp > curated.timestamp,
                or_(H.provider.is_(None), and_(H.provider.notin_(rp.CURATED_PROVIDERS),
                                               ~H.provider.like('batch-translate-%'))),
                or_(H.description.is_(None), ~H.description.like('Published lyrics%')),
            ).order_by(H.timestamp.asc(), H.id.asc()).limit(1)).scalars().first()
        if replacing is None:
            return None
        version = database.execute(
            select(V).where(V.lidarrTrackId == row.lidarrTrackId,
                            V.timestamp >= replacing.timestamp - timedelta(seconds=10),
                            V.timestamp <= replacing.timestamp + timedelta(seconds=2))
            .order_by(V.timestamp.desc(), V.id.desc()).limit(1)).scalars().first()
        if version is None:
            return None
        kind = 'upgrade' if (version.lyrics_type == 'plain' and row.is_synced) or replacing.action == 2 else 'overwrite'
        return self.track_issue(
            row, f"'{row.title}': your {curated.provider or 'saved'} lyrics were replaced by "
                 f"{replacing.provider or 'a download'} on {replacing.timestamp:%Y-%m-%d}",
            'restore_version',
            details={'version_id': version.id, 'kind': kind, 'curated_provider': curated.provider,
                     'replaced_by': replacing.provider, 'replaced_at': replacing.timestamp,
                     'pin_version_ids': [version.id], 'pin_history_ids': [curated.id, replacing.id]})

    def fix_restore_version(self, ctx, issue):
        return rp.restore_version(ctx, issue.track_id, issue.details.get('version_id'))


@register
class ReachabilityBurst(Check):
    spec = CheckSpec(
        id='history.reachability_burst', code='H-2',
        title='Days with mass lyrics downloads',
        description='Days with more than 500 lyrics downloads: the signature of a music folder that was '
                    'unmounted and re-downloaded (report).',
        category='history', severity='info', cost='db', fix_mode='report', order=71,
    )

    def detect(self, ctx):
        from lyrarr.app.database import TableHistory, database, func, select
        H = TableHistory
        day = func.date(H.timestamp)
        rows = database.execute(
            select(day, func.count()).where(H.metadata_type == 'lyrics', H.action.in_((1, 2)))
            .group_by(day).having(func.count() > 500)).all()
        for d, n in rows:
            yield Issue(entity_type='db', entity_id=None, entity_key=f'day:{d}',
                        title=f'{n} lyrics downloads on {d}', details={'day': str(d), 'writes': n})


@register
class LegacyPublishZeroDuration(Check):
    spec = CheckSpec(
        id='history.legacy_publish_zero_duration', code='H-3',
        title='Lyrics published to LRCLIB by an older version',
        description='Lyrics published to LRCLIB before this version, possibly with duration 0/1 (an older '
                    'bug). Re-publish them from the track if needed (report).',
        category='history', severity='info', cost='db', fix_mode='report', order=72,
    )

    def detect(self, ctx):
        from lyrarr.app.database import TableHistory, data_migration_applied, database, select
        H = TableHistory
        cutoff = data_migration_applied('2026-10-app-version-2.0.0') or ctx.now
        rows = database.execute(
            select(H).where(H.description.like('Published lyrics for % to LRCLIB'), H.action == 1,
                            H.timestamp < cutoff).order_by(H.timestamp)).scalars().all()
        for h in rows:
            likely = h.timestamp < _PUBLISH_FIX_AT
            yield Issue(entity_type='history', entity_id=h.id, entity_key=f'history:{h.id}',
                        title=f"{h.description} ({'likely' if likely else 'possibly'} published with duration 0/1)",
                        track_id=h.lidarrTrackId, album_id=h.lidarrAlbumId, artist_id=h.lidarrArtistId,
                        details={'likely': likely, 'at': h.timestamp})
