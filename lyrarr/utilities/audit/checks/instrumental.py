"""Instrumental checks I-1..I-9 (PLAN §4.4).

Priority 1 of the release: a vocal track must never stay flagged
instrumental on weak evidence. These checks clear legacy/unverified flags
(recording instrumental_prev so the user can confirm real instrumentals in
Wanted ▸ "Cleared after upgrade"), adopt real lyrics found on disk, and only
ever SET instrumental through an explicit review repair (I-6).
"""

from __future__ import annotations

from datetime import timedelta

from lyrarr.metadata.lyrics_text import (
    instrumental_qualifier_kind,
    instrumental_title_tier,
    is_substantive_lyrics,
    matches_legacy_title_rule,
    needs_album_context,
)

from .. import repairs as rp
from ..registry import CheckSpec, TrackCheck, failed, fixed, register, skipped


def _T():
    from lyrarr.app.database import TableTracks
    return TableTracks


def _override_not_always():
    from lyrarr.app.database import or_
    T = _T()
    return or_(T.instrumental_override.is_(None), T.instrumental_override != 'always')


def _select():
    from lyrarr.app.database import select
    return select(_T())


@register
class LegacyUnknown(TrackCheck):
    spec = CheckSpec(
        id='instrumental.legacy_unknown', code='I-1',
        title='Instrumental flags from older versions',
        description='Tracks flagged instrumental by an older version without any recorded reason. '
                    'Real lyrics on disk are adopted; every other flag is cleared and the track is '
                    'searched again (listed under Wanted ▸ Cleared after upgrade for review).',
        category='instrumental', severity='high', cost='fs_stat', fix_mode='auto',
        needs_library=False, degrades_without_library=True, bootstrap=True, order=20,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status == 'instrumental', T.instrumental_reason.is_(None),
                               _override_not_always())

    def examine(self, ctx, row):
        if instrumental_title_tier(row.title) == 'strong':
            return None
        details = {'duration': row.duration, 'needs_album_context': needs_album_context(row.title),
                   'legacy_title_rule': matches_legacy_title_rule(row.title)}
        if ctx.is_degraded(row):
            # The library root is unreachable: the file can't be checked, and the
            # worker skips these rows anyway. Report only; the flag is cleared (or
            # the lyrics adopted) by the first run after the root is back.
            details['degraded'] = True
            return self.track_issue(row, f"'{row.title}' was flagged instrumental without a reason "
                                         f"(library folder unreachable — checked when it is back)",
                                    None, fix_mode='report', details=details)
        path, kind, _ = ctx.sidecar(row)
        if kind == 'lyrics':
            return self.track_issue(row, f"'{row.title}' is flagged instrumental but has lyrics on disk",
                                    'adopt', details=details, path=path)
        return self.track_issue(row, f"'{row.title}' was flagged instrumental without a reason",
                                'requeue_legacy', details=details)

    def fix_adopt(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import adopt_sidecar
        row = rp.fresh_track(issue.track_id)
        if row is None or not adopt_sidecar(row, keep_source=True):
            return failed('the lyrics file could not be adopted')
        ctx.counts['cleared'] += 1
        return fixed('lyrics file adopted; no longer instrumental')

    def fix_requeue_legacy(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import clear_instrumental
        if not clear_instrumental(issue.track_id, why='legacy-unverified', actor=ctx.actor,
                                  retry_after=ctx.stagger(), expect_status=('instrumental',),
                                  emit_event=False):
            return skipped('the track changed')
        ctx.counts['cleared'] += 1
        return fixed('flag cleared; the track will be searched again')


@register
class TitleBackfill(TrackCheck):
    spec = CheckSpec(
        id='instrumental.title_backfill', code='I-2',
        title='Instrumental title without a recorded reason',
        description='Legacy instrumental flags on tracks whose title clearly says instrumental: '
                    'the reason is recorded as "title".',
        category='instrumental', severity='info', cost='db', fix_mode='auto', bootstrap=True, order=21,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status == 'instrumental', T.instrumental_reason.is_(None))

    def examine(self, ctx, row):
        if instrumental_title_tier(row.title) != 'strong':
            return None
        return self.track_issue(row, f"'{row.title}': record reason 'title'", 'set_title_reason')

    def fix_set_title_reason(self, ctx, issue):
        import json
        T = _T()
        ok = rp.update_track(issue.track_id, (T.lyrics_status == 'instrumental', T.instrumental_reason.is_(None)),
                             instrumental_reason='title',
                             instrumental_evidence=json.dumps([{'source': 'title', 'kind': 'title'}]))
        return fixed('reason recorded') if ok else skipped('the track changed')


@register
class RealLyricsOnDisk(TrackCheck):
    spec = CheckSpec(
        id='instrumental.real_lyrics_on_disk', code='I-3', requires_download_lock=True,
        title='Instrumental tracks with lyrics on disk',
        description='Tracks marked instrumental whose lyrics file holds real lyrics. Plain titles are '
                    'adopted automatically; instrumental-titled ones are left for review.',
        category='instrumental', severity='high', cost='fs_read', fix_mode='auto',
        needs_library=True, bootstrap=True, order=22,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status == 'instrumental', _override_not_always())

    def examine(self, ctx, row):
        path, kind, _ = ctx.sidecar(row)
        if kind != 'lyrics':
            return None
        if instrumental_title_tier(row.title) == 'strong':
            return self.track_issue(
                row, f"'{row.title}' is instrumental by title but its lyrics file holds lyrics",
                'archive_remove_stale', fix_mode='review', path=path,
                details={'alt_actions': ['not_instrumental'], 'reason': row.instrumental_reason})
        return self.track_issue(row, f"'{row.title}' is flagged instrumental but has lyrics on disk",
                                'adopt', path=path, details={'reason': row.instrumental_reason})

    def fix_adopt(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import adopt_sidecar
        row = rp.fresh_track(issue.track_id)
        if row is None or not adopt_sidecar(row, keep_source=True):
            return failed('the lyrics file could not be adopted')
        ctx.counts['cleared'] += 1
        return fixed('lyrics file adopted; no longer instrumental')

    def fix_archive_remove_stale(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import archive_and_remove
        row = rp.fresh_track(issue.track_id)
        if row is None:
            return skipped('track no longer exists')
        if not archive_and_remove(row, f'audit:{self.spec.code}'):
            return failed('the lyrics file could not be archived, so it was kept')
        return fixed('stale lyrics archived and removed; still instrumental')

    def fix_not_instrumental(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import adopt_sidecar
        row = rp.fresh_track(issue.track_id)
        if row is None:
            return skipped('track no longer exists')
        # The override must be 'never' while adopting (a strong instrumental
        # title would otherwise keep rule-T evidence); restored on failure so a
        # failed repair leaves the user's override as it was.
        old = {'instrumental_override': row.instrumental_override, 'instrumental_prev': row.instrumental_prev}
        rp.update_track(issue.track_id, instrumental_override='never', instrumental_prev=None)
        row = rp.fresh_track(issue.track_id)
        if row is None or not adopt_sidecar(row, keep_source=True):
            if row is not None:
                rp.update_track(issue.track_id, **old)
            return failed('the lyrics file could not be adopted')
        ctx.counts['cleared'] += 1
        return fixed('marked not instrumental; lyrics adopted')


@register
class EmbeddedLyrics(TrackCheck):
    spec = CheckSpec(
        id='instrumental.embedded_lyrics', code='I-4',
        title='Instrumental tracks with lyrics in the audio tags',
        description="Instrumental tracks whose audio file has real lyrics in its own tags. The flag is "
                    "cleared and the next download pass saves those lyrics.",
        category='instrumental', severity='medium', cost='audio', fix_mode='auto',
        needs_library=True, scheduled=False, default_enabled=False, order=23,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status == 'instrumental', _override_not_always())

    def detect(self, ctx):
        available, _ = rp.read_embedded(None)
        if not available:
            ctx.note('reading embedded lyrics is not available in this build')
            return
        yield from super().detect(ctx)

    def examine(self, ctx, row):
        _, emb = rp.read_embedded(row.path)
        if emb is None or not is_substantive_lyrics(getattr(emb, 'text', None)):
            return None
        return self.track_issue(row, f"'{row.title}' has lyrics in its audio tags", 'clear_instrumental',
                                details={'source_tag': getattr(emb, 'source_tag', None)})

    def fix_clear_instrumental(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import clear_instrumental
        if not clear_instrumental(issue.track_id, why='embedded-lyrics', actor=ctx.actor,
                                  expect_status=('instrumental',), emit_event=False):
            return skipped('the track changed')
        ctx.counts['cleared'] += 1
        return fixed('flag cleared; the embedded lyrics will be saved')


@register
class WeakTitleOnly(TrackCheck):
    spec = CheckSpec(
        id='instrumental.weak_title_only', code='I-5',
        title='Instrumental by a title rule that no longer applies',
        description='Tracks flagged instrumental by the title alone, under an older, broader title rule '
                    '(for example "The Instrumental" or "Song (Instrumental Intro)").',
        category='instrumental', severity='high', cost='db', fix_mode='auto', bootstrap=True, order=24,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(T.lyrics_status == 'instrumental', T.instrumental_reason == 'title',
                               _override_not_always())

    def examine(self, ctx, row):
        tier = instrumental_title_tier(row.title)
        if tier == 'strong':
            return None
        return self.track_issue(row, f"'{row.title}' is not an instrumental title", 'clear_instrumental',
                                details={'tier': tier})

    def fix_clear_instrumental(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import clear_instrumental
        if not clear_instrumental(issue.track_id, why='weak-title', actor=ctx.actor, retry_after=ctx.stagger(),
                                  expect_status=('instrumental',), emit_event=False):
            return skipped('the track changed')
        ctx.counts['cleared'] += 1
        return fixed('flag cleared; the track will be searched again')


@register
class TitleWithVocalLyrics(TrackCheck):
    spec = CheckSpec(
        id='instrumental.title_with_vocal_lyrics', code='I-6', requires_download_lock=True,
        title='Instrumental titles with vocal lyrics',
        description='Tracks titled as instrumental (for example "Song (Instrumental)") that have a '
                    'lyrics file with real lyrics. Review: archive the lyrics and mark instrumental, '
                    'or keep the lyrics.',
        category='instrumental', severity='medium', cost='fs_read', fix_mode='review',
        needs_library=True, order=25,
    )

    def query(self, ctx):
        from lyrarr.app.database import or_
        T = _T()
        return _select().where(
            T.lyrics_status == 'available',
            or_(T.lyrics_source.is_(None), T.lyrics_source == 'auto'),
            or_(T.instrumental_override.is_(None), T.instrumental_override != 'never'),
        )

    def examine(self, ctx, row):
        if instrumental_title_tier(row.title) != 'strong':
            return None
        path, kind, _ = ctx.sidecar(row)
        if kind != 'lyrics':
            return None
        hist = rp.newest_lyrics_history(row.lidarrTrackId)
        provider = (hist.provider if hist else None) or row.lyrics_provider
        if provider == 'embedded':
            origin = "from the file's own tags"
        elif provider and provider not in ('upload', 'manual', 'restored', 'editor'):
            origin = 'likely lyrarr-written'
        else:
            origin = 'unknown origin'
        details = {'provider': provider, 'origin': origin, 'alt_actions': ['keep_lyrics']}
        kind_q = instrumental_qualifier_kind(row.title)
        title_l = (row.title or '').casefold()
        if kind_q == 'karaoke' or 'backing' in title_l:
            details['karaoke'] = True
            return self.track_issue(row, f"'{row.title}' (karaoke/backing) has lyrics", None,
                                    fix_mode='report', path=path, details=details)
        return self.track_issue(row, f"'{row.title}' has vocal lyrics ({origin})", 'archive_flag_instrumental',
                                path=path, details=details)

    def fix_archive_flag_instrumental(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import archive_and_remove, set_instrumental
        row = rp.fresh_track(issue.track_id)
        if row is None:
            return skipped('track no longer exists')
        if not archive_and_remove(row, f'audit:{self.spec.code}', blacklist_reason=self.spec.code):
            return failed('the lyrics file could not be archived, so it was kept')
        ctx.forget(issue.path)
        if not set_instrumental(row.lidarrTrackId, 'title', [{'source': 'title', 'kind': 'title'}],
                                actor=ctx.actor, expect_status=('available',), emit_event=False):
            return failed('the file was archived but the track changed before it could be flagged')
        ctx.counts['flagged'] += 1
        return fixed('lyrics archived; marked instrumental')

    def fix_keep_lyrics(self, ctx, issue):
        ok = rp.update_track(issue.track_id, instrumental_override='never', instrumental_evidence=None,
                             instrumental_reason=None)
        return fixed('kept the lyrics; never instrumental') if ok else skipped('track no longer exists')


def _batch_delete_versions(track_ids=None):
    from lyrarr.app.database import TableLyricsVersions, and_, database, or_, select
    V = TableLyricsVersions
    q = select(V).where(or_(and_(V.provider == 'batch-delete', V.replaced_by.is_(None)),
                            V.replaced_by == 'batch-delete'))
    if track_ids is not None:
        q = q.where(V.lidarrTrackId.in_(track_ids))
    return database.execute(q.order_by(V.timestamp.desc(), V.id.desc())).scalars().all()


@register
class BatchDeletedRestorable(TrackCheck):
    spec = CheckSpec(
        id='instrumental.batch_deleted_restorable', code='I-7', requires_download_lock=True,
        title='Lyrics removed by a batch delete that can be restored',
        description='Real lyrics removed by an earlier batch delete ("instrumental" mode deleted real '
                    'lyrics in older versions). The archived copy is kept until you review it.',
        category='instrumental', severity='medium', cost='fs_stat', fix_mode='review',
        needs_library=True, pins_evidence=True, order=26,
    )

    def query(self, ctx):
        from lyrarr.app.database import TableLyricsVersions, and_, or_, select
        T, V = _T(), TableLyricsVersions
        ids = select(V.lidarrTrackId).where(or_(and_(V.provider == 'batch-delete', V.replaced_by.is_(None)),
                                                V.replaced_by == 'batch-delete'))
        return _select().where(T.lidarrTrackId.in_(ids), or_(T.lyrics_status.is_(None),
                                                              T.lyrics_status != 'blacklisted'))

    def examine(self, ctx, row):
        from lyrarr.metadata.lyrics_text import classify_lyrics_text
        version = next((v for v in _batch_delete_versions([row.lidarrTrackId])
                        if classify_lyrics_text(v.content) == 'lyrics'), None)
        if version is None:
            return None
        path, kind, _ = ctx.sidecar(row)
        if kind is not None:
            if row.lyrics_source not in rp.AUTO_SOURCES:
                return None
            hist = rp.newest_lyrics_history(row.lidarrTrackId)
            if hist is None or rp.is_curated_provider(hist.provider) or hist.timestamp < version.timestamp:
                return None
            if rp.equivalence(_sidecar_text(ctx, row), version.content) >= 0.9:
                return None
        return self.track_issue(
            row, f"'{row.title}': lyrics removed by a batch delete can be restored", 'restore_version',
            path=path, details={'version_id': version.id, 'version_at': version.timestamp,
                                'current': kind, 'pin_version_ids': [version.id]})

    def fix_restore_version(self, ctx, issue):
        return rp.restore_version(ctx, issue.track_id, issue.details.get('version_id'))


def _sidecar_text(ctx, row):
    _, _, sr = ctx.sidecar(row)
    return sr.text if sr is not None else ''


@register
class StaleConsensus(TrackCheck):
    spec = CheckSpec(
        id='instrumental.stale_consensus', code='I-8',
        title='Instrumental by consensus, not re-checked for 180 days',
        description='Tracks classified instrumental because several sources agreed, checked more than '
                    '180 days ago. The flag is cleared so the policy re-evaluates them.',
        category='instrumental', severity='low', cost='db', fix_mode='auto', order=27,
    )

    def query(self, ctx):
        from lyrarr.app.database import or_
        T = _T()
        cutoff = ctx.now - timedelta(days=180)
        return _select().where(T.lyrics_status == 'instrumental', T.instrumental_reason == 'consensus',
                               _override_not_always(),
                               or_(T.instrumental_checked_at.is_(None), T.instrumental_checked_at < cutoff))

    def examine(self, ctx, row):
        return self.track_issue(row, f"'{row.title}': instrumental consensus is old", 'clear_instrumental',
                                details={'checked_at': row.instrumental_checked_at})

    def fix_clear_instrumental(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import clear_instrumental
        if not clear_instrumental(issue.track_id, why='periodic-recheck', actor=ctx.actor,
                                  retry_after=ctx.stagger(), expect_status=('instrumental',), emit_event=False):
            return skipped('the track changed')
        ctx.counts['cleared'] += 1
        return fixed('flag cleared for a re-check')


@register
class RecheckNetwork(TrackCheck):
    spec = CheckSpec(
        id='instrumental.recheck_network', code='I-9',
        title='Re-verify instrumental tracks online',
        description='Searches the lyrics providers again for tracks kept instrumental (consensus or '
                    'unverified). When acceptable lyrics now exist, offers "Not instrumental: search again".',
        category='instrumental', severity='medium', cost='network', fix_mode='review',
        scheduled=False, default_enabled=False, order=60,
    )

    def query(self, ctx):
        from lyrarr.app.database import or_
        T = _T()
        q = _select().where(T.lyrics_status == 'instrumental', _override_not_always(),
                            or_(T.instrumental_reason.is_(None), T.instrumental_reason == 'consensus'))
        if ctx.resume_cursor:
            q = q.where(T.lidarrTrackId > ctx.resume_cursor)
        return q

    def detect(self, ctx):
        from ..network import verify_rows
        yield from verify_rows(self, ctx, self.query(ctx), self._judge)

    def _judge(self, ctx, row, result):
        chosen = result.get('chosen')
        if result.get('outcome') != 'ok' or not chosen or not chosen.get('acceptable', True):
            return None
        return self.track_issue(
            row, f"'{row.title}': lyrics found online ({chosen.get('provider')})", 'not_instrumental_queue',
            details={'provider': chosen.get('provider'), 'matched_title': chosen.get('matched_title'),
                     'score': chosen.get('score')})

    def recheck(self, ctx, issue_row):
        row = rp.fresh_track(issue_row.entity_id)
        if row is None or row.lyrics_status != 'instrumental' or row.instrumental_override == 'always':
            return None
        from ..runner import issue_from_row
        return issue_from_row(issue_row)

    def fix_not_instrumental_queue(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import clear_instrumental
        if not clear_instrumental(issue.track_id, why='network-recheck', actor=ctx.actor, override='never',
                                  expect_status=('instrumental',), emit_event=False):
            return skipped('the track changed')
        ctx.counts['cleared'] += 1
        return fixed('marked not instrumental; queued for the next download pass')
