"""Matching checks M-1..M-5: wrong-song suspects (never auto-delete, never blank).

Suspects get a review "Re-search" (replace mode, §2.8: the worker replaces
the file only with something better and keeps it otherwise), or with the
opt-in network check M-4 a verified replacement.
"""

from __future__ import annotations

import re
from collections import defaultdict

from lyrarr.metadata.language_detect import is_synced_lyrics
from lyrarr.metadata.lyrics_text import classify_lyrics_text, needs_album_context

from .. import repairs as rp
from ..registry import CheckSpec, TrackCheck, failed, fixed, register, skipped
from .files import _T, _auto_source, _available, _select, _text


def _report_if_kept(check, row, path, details, title):
    kept = rp.replace_kept_at(row)
    if kept is None:
        return None
    details = dict(details, replace_kept_at=kept)
    return check.track_issue(row, f"'{row.title}': re-searched on {kept:%Y-%m-%d}: nothing better found",
                             None, fix_mode='report', path=path, details=details)


@register
class SyncedOverrunsDuration(TrackCheck):
    spec = CheckSpec(
        id='match.synced_overruns_duration', code='M-1',
        title='Synced lyrics much longer than the track',
        description='Automatic synced lyrics whose last line comes more than 15 s after the track ends: '
                    'probably another song. Review: re-search (the file is kept unless something better '
                    'is found).',
        category='matching', severity='medium', cost='fs_read', fix_mode='review', needs_library=True, order=50,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(_available(), _auto_source(), T.duration >= 1, T.duration <= 3600,
                               T.lyrics_replace_requested.is_(None))

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or not is_synced_lyrics(text):
            return None
        ts = rp.lyric_timestamps(text)
        if not ts or max(ts) <= row.duration + 15:
            return None
        details = {'duration': row.duration, 'last_ts': round(max(ts), 2), 'alt_actions': []}
        kept = _report_if_kept(self, row, path, details, row.title)
        if kept is not None:
            return kept
        return self.track_issue(row, f"'{row.title}': lyrics run {max(ts) - row.duration:.0f} s past the end",
                                'request_replace', path=path, details=details)

    def fix_request_replace(self, ctx, issue):
        return rp.request_replace(ctx, rp.fresh_track(issue.track_id), self.spec.code)


_FOLD_SUFFIX_RE = re.compile(
    r'\s*[\(\[][^\)\]]*(?:live|remaster|remastered|reprise|version|edit|mix|mono|stereo|demo|acoustic|'
    r'radio|explicit|clean|bonus|deluxe)[^\)\]]*[\)\]]', re.IGNORECASE)
_FOLD_DASH_RE = re.compile(
    r'\s+[-–—]\s+.*(?:live|remaster|remastered|reprise|version|edit|mix|mono|stereo|demo|acoustic|radio).*$',
    re.IGNORECASE)


def title_key(title):
    """Version-insensitive title key: the worker's comparison key (Latin-only
    folding, Pt/roman numerals, feat./dash-suffix tags) after dropping version
    brackets, so "Song Pt. 2" and "Song Part II" count as one title."""
    t = _FOLD_SUFFIX_RE.sub('', title or '')
    t = _FOLD_DASH_RE.sub('', t)
    from lyrarr.metadata.normalize import comparison_key
    t = comparison_key(t) or t
    return re.sub(r'[\W_]+', '', t.casefold())


def _row_hash(ctx, row):
    from lyrarr.metadata.lyrics_store import content_hash
    path, kind, text = _text(ctx, row)
    if kind != 'lyrics' or not text:
        return None
    return content_hash(text)


def _group_reason(members):
    """Why tracks of one artist sharing one content hash are suspect (None: they are not).

    Rows that point at the same audio file (duplicate rows Lidarr left behind,
    often one titled "<artist> - <album> - 01 - <title>") read the same sidecar,
    so they count as one file: only distinct files are compared.
    """
    by_path = {}
    for m in members:
        by_path.setdefault(m.path, []).append(m)
    if len(by_path) < 2:
        return None
    files = list(by_path.values())
    keys = [{title_key(m.title) for m in rows} for rows in files]
    if any(keys[i].isdisjoint(keys[j]) for i in range(len(keys)) for j in range(i + 1, len(keys))):
        return 'different_titles'
    if any(needs_album_context(m.title) for m in members) and len({m.albumId for m in members}) > 1:
        return 'same_generic_title_other_album'
    return None


def _group_info(row, members, reason, h):
    return {'reason': reason, 'hash': h,
            'shared_with': [o.lidarrTrackId for o in members if o.lidarrTrackId != row.lidarrTrackId],
            'titles': sorted({o.title for o in members})}


@register
class DuplicateContentAcrossTitles(TrackCheck):
    spec = CheckSpec(
        id='match.duplicate_content_across_titles', code='M-2',
        title='Same lyrics on different songs',
        description='Identical lyrics on tracks of one artist with different titles, or on two same-titled '
                    'intros/outros of different albums: at least one of them is the wrong song.',
        category='matching', severity='medium', cost='fs_read', fix_mode='review', needs_library=True, order=51,
    )

    def query(self, ctx):
        return _select().where(_available())

    def _groups(self, ctx):
        """{track_id: info} of the suspect groups, built once per (run, phase).

        The apply phase rebuilds it once (the detect-time map may be stale);
        each recheck then re-verifies only its own group (_still_suspect).
        """
        stamp = (ctx.run_id, ctx.phase)
        cached = ctx.cache.get('m2_groups')
        if cached is not None and cached[0] == stamp:
            return cached[1]
        rows = ctx.tracks(self, self.query(ctx).order_by(_T().lidarrTrackId))
        by_hash = defaultdict(list)
        report = ctx.phase == 'detect'
        for i, row in enumerate(rows, 1):
            if ctx.cancelled:
                break
            if report:
                ctx.progress(i, len(rows), cursor=row.lidarrTrackId)
            h = _row_hash(ctx, row)
            if h:
                by_hash[(row.artistId, h)].append(row)
        suspects = {}
        for (_, h), members in by_hash.items():
            reason = _group_reason(members)
            if reason:
                for m in members:
                    suspects[m.lidarrTrackId] = _group_info(m, members, reason, h)
        ctx.cache['m2_groups'] = (stamp, suspects)
        return suspects

    def _still_suspect(self, ctx, row, info):
        """Re-verify one group against the current rows and files (apply phase)."""
        from lyrarr.app.database import database
        ids = [row.lidarrTrackId] + list(info.get('shared_with') or [])
        rows = database.execute(self.query(ctx).where(_T().lidarrTrackId.in_(ids))).scalars().all()
        members = [r for r in rows if r.artistId == row.artistId and _row_hash(ctx, r) == info['hash']]
        if not any(m.lidarrTrackId == row.lidarrTrackId for m in members):
            return None
        reason = _group_reason(members)
        return _group_info(row, members, reason, info['hash']) if reason else None

    def detect(self, ctx):
        suspects = self._groups(ctx)
        if not suspects:
            return
        from lyrarr.app.database import database
        rows = database.execute(_select().where(_T().lidarrTrackId.in_(list(suspects)))
                                .order_by(_T().lidarrTrackId)).scalars().all()
        for row in rows:
            issue = self.examine(ctx, row)
            if issue is not None:
                yield issue

    def examine(self, ctx, row):
        info = self._groups(ctx).get(row.lidarrTrackId)
        if info is None:
            return None
        if ctx.phase != 'detect':
            info = self._still_suspect(ctx, row, info)
            if info is None:
                return None
        path, kind, text = _text(ctx, row)
        from lyrarr.metadata.lyrics_store import content_hash
        if not text or content_hash(text) != info['hash']:
            return None
        details = dict(info)
        if row.lyrics_replace_requested is not None:
            return None
        if row.lyrics_source not in rp.AUTO_SOURCES:
            return self.track_issue(row, f"'{row.title}': same lyrics as {len(info['shared_with'])} other track(s)",
                                    None, fix_mode='report', path=path, details=details)
        kept = _report_if_kept(self, row, path, details, row.title)
        if kept is not None:
            return kept
        return self.track_issue(row, f"'{row.title}': same lyrics as {', '.join(t for t in info['titles'] if t != row.title) or 'another track'}",
                                'request_replace', path=path, details=details)

    def fix_request_replace(self, ctx, issue):
        return rp.request_replace(ctx, rp.fresh_track(issue.track_id), self.spec.code)


@register
class LanguageOutlier(TrackCheck):
    spec = CheckSpec(
        id='match.language_outlier', code='M-3',
        title='Lyrics in an unexpected language',
        description='Automatic lyrics whose language differs from the rest of the album (report only).',
        category='matching', severity='low', cost='db', fix_mode='report', order=52,
    )

    def query(self, ctx):
        return _select().where(_available(), _auto_source())

    def detect(self, ctx):
        from lyrarr.app.database import TableAlbums, select
        from lyrarr.metadata.coherence import check_album_coherence
        albums = ctx.albums(self, select(TableAlbums).order_by(TableAlbums.lidarrAlbumId))
        for i, album in enumerate(albums, 1):
            if ctx.cancelled:
                return
            ctx.progress(i, len(albums))
            try:
                result = check_album_coherence(album.lidarrAlbumId)
            except Exception:
                continue
            for item in result.get('issues', []):
                if item.get('issue') != 'language_outlier' or item.get('track_id') is None:
                    continue
                row = rp.fresh_track(item['track_id'])
                if row is None or row.lyrics_source not in rp.AUTO_SOURCES:
                    continue
                yield self.track_issue(row, f"'{row.title}': {item.get('detail') or 'language outlier'}", None,
                                       details={'detail': item.get('detail')})

    def examine(self, ctx, row):
        return None


@register
class ReverifyNetwork(TrackCheck):
    spec = CheckSpec(
        id='match.reverify_network', code='M-4', requires_download_lock=True,
        title='Re-verify suspect lyrics online',
        description='Searches the providers again for wrong-song suspects (M-1/M-2) or a chosen scope and '
                    'compares each candidate with the file. Confirmed-wrong files can be replaced; '
                    'confirmed matches get their provenance recorded.',
        category='matching', severity='high', cost='network', fix_mode='review',
        scheduled=False, default_enabled=False, order=61,
    )

    def query(self, ctx):
        T = _T()
        q = _select().where(_available(), _auto_source(), T.lyrics_replace_requested.is_(None))
        if not (ctx.scope.get('artistIds') or ctx.scope.get('albumIds')):
            q = q.where(T.lidarrTrackId.in_(_suspect_ids()))
        if ctx.resume_cursor:
            q = q.where(T.lidarrTrackId > ctx.resume_cursor)
        return q

    def detect(self, ctx):
        from ..network import verify_rows
        yield from verify_rows(self, ctx, self.query(ctx), self._judge)

    def _judge(self, ctx, row, result):
        from lyrarr.metadata.lyrics_store import content_hash
        path, kind, text = _text(ctx, row)
        if not text:
            return None
        cands = result.get('candidates') or []
        chosen = result.get('chosen')

        def eq(c):
            v = c.get('equivalence_to_file')
            if v is None:
                v = rp.equivalence(text, c.get('synced') or c.get('plain') or '')
            return v or 0.0

        good = [c for c in cands if c.get('acceptable') and eq(c) >= 0.8]
        bad = [c for c in cands if not c.get('acceptable') and eq(c) >= 0.8]
        base = {'file_hash': content_hash(text)}
        if good:
            c = max(good, key=lambda c: c.get('score') or 0)
            return self.track_issue(
                row, f"'{row.title}': confirmed ({c.get('provider')})", 'stamp_provenance', fix_mode='auto',
                path=path, severity='info',
                details={**base, 'provider': c.get('provider'), 'source_id': c.get('source_id'),
                         'score': c.get('score'), 'match': _match_of(c)})
        if bad and chosen and chosen.get('acceptable') and (chosen.get('synced') or chosen.get('plain')):
            content = chosen.get('synced') or chosen.get('plain')
            wrong = bad[0]
            return self.track_issue(
                row, f"'{row.title}': the file matches '{wrong.get('matched_title')}' "
                     f"({wrong.get('reject_reason')}); a better match exists", 'replace_with_candidate',
                path=path,
                details={**base, 'wrong': {k: wrong.get(k) for k in ('provider', 'matched_title', 'matched_album',
                                                                     'reject_reason', 'score')},
                         'chosen_provider': chosen.get('provider'), 'chosen_content': content,
                         'match': _match_of(chosen)})
        return self.track_issue(row, f"'{row.title}': could not be confirmed online", 'request_replace',
                                path=path, severity='info', details=base)

    def recheck(self, ctx, issue_row):
        from lyrarr.metadata.lyrics_store import content_hash
        row = rp.fresh_track(issue_row.entity_id)
        if row is None or row.lyrics_status not in ('available', 'manual') or row.lyrics_source not in rp.AUTO_SOURCES:
            return None
        _, _, text = _text(ctx, row)
        from ..runner import issue_from_row
        issue = issue_from_row(issue_row)
        if not text or content_hash(text) != issue.details.get('file_hash'):
            return None
        return issue

    def fix_stamp_provenance(self, ctx, issue):
        import json
        d = issue.details
        match = d.get('match') or {}
        ok = rp.update_track(issue.track_id, lyrics_provider=d.get('provider'),
                             lyrics_source_id=str(d['source_id']) if d.get('source_id') is not None else None,
                             lyrics_match_score=d.get('score'),
                             lyrics_match_json=json.dumps(match, default=str) if match else None)
        return fixed('provenance recorded') if ok else skipped('track no longer exists')

    def fix_replace_with_candidate(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import blacklist_content, persist_lyrics_result
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        content = issue.details.get('chosen_content')
        if row is None or text is None or not content:
            return failed('nothing to replace with')
        match = dict(issue.details.get('match') or {})
        r = persist_lyrics_result(row, content, issue.details.get('chosen_provider') or 'unknown', source='auto',
                                  match=match, expect_sources=(None, 'auto'))
        if not r:
            return failed(r.message or 'replace failed')
        # Blacklist only once the replacement is saved: a failed persist leaves
        # the file in place, and its content must not be blacklisted then.
        blacklist_content(row.lidarrTrackId, text, reason=self.spec.code)
        ctx.forget(issue.path)
        return fixed('replaced (previous file archived and blacklisted)')

    def fix_request_replace(self, ctx, issue):
        return rp.request_replace(ctx, rp.fresh_track(issue.track_id), self.spec.code)


def _match_of(c):
    return {k: c.get(k) for k in ('source_id', 'score', 'strategy', 'matched_title', 'matched_artist',
                                  'matched_album', 'duration', 'gate') if c.get(k) is not None}


def _suspect_ids():
    from lyrarr.app.database import TableAuditIssues, select
    return select(TableAuditIssues.lidarrTrackId).where(
        TableAuditIssues.check_id.in_(('match.synced_overruns_duration', 'match.duplicate_content_across_titles')),
        TableAuditIssues.status == 'open', TableAuditIssues.lidarrTrackId.is_not(None))


@register
class UpgradeSwappedSong(TrackCheck):
    spec = CheckSpec(
        id='match.upgrade_swapped_song', code='M-5', requires_download_lock=True,
        title='Synced upgrade replaced the song',
        description='The plain-to-synced upgrade replaced plain lyrics with synced lyrics of a different song. '
                    'Review: restore the archived plain version (itself archived first, so undoable).',
        category='matching', severity='high', cost='fs_read', fix_mode='review', needs_library=True,
        pins_evidence=True, order=53,
    )

    def query(self, ctx):
        from lyrarr.app.database import TableHistory, select
        T = _T()
        upgraded = select(TableHistory.lidarrTrackId).where(TableHistory.action == 2,
                                                            TableHistory.metadata_type == 'lyrics')
        return _select().where(_available(), _auto_source(), T.lidarrTrackId.in_(upgraded))

    def examine(self, ctx, row):
        from datetime import timedelta

        from lyrarr.app.database import TableHistory, TableLyricsVersions, database, select
        path, kind, text = _text(ctx, row)
        if not text or not is_synced_lyrics(text):
            return None
        up = database.execute(
            select(TableHistory).where(TableHistory.lidarrTrackId == row.lidarrTrackId, TableHistory.action == 2,
                                       TableHistory.metadata_type == 'lyrics')
            .order_by(TableHistory.timestamp.desc(), TableHistory.id.desc()).limit(1)).scalars().first()
        if up is None:
            return None
        V = TableLyricsVersions
        version = database.execute(
            select(V).where(V.lidarrTrackId == row.lidarrTrackId, V.lyrics_type == 'plain',
                            V.timestamp <= up.timestamp + timedelta(seconds=10))
            .order_by(V.timestamp.desc(), V.id.desc()).limit(1)).scalars().first()
        if version is None or classify_lyrics_text(version.content) != 'lyrics':
            return None
        ratio = rp.equivalence(text, version.content)
        if ratio >= 0.5:
            return None
        return self.track_issue(
            row, f"'{row.title}': the synced upgrade looks like a different song", 'restore_version', path=path,
            details={'version_id': version.id, 'equivalence': round(ratio, 3), 'upgrade_at': up.timestamp,
                     'upgrade_provider': up.provider, 'pin_version_ids': [version.id], 'pin_history_ids': [up.id]})

    def fix_restore_version(self, ctx, issue):
        return rp.restore_version(ctx, issue.track_id, issue.details.get('version_id'))
