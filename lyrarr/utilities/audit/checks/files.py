"""File checks F-0..F-15 (PLAN §4.4 + lead addendum F-15).

All of them (except F-0) need the library: they only look at entities under
reachable roots, and every repair re-checks the row and the audio before it
writes. Manual/translated/generated files are only reported (the single
exception is lossless BOM/UTF-16 normalisation, archived first).
"""

from __future__ import annotations

import html
import os
import re
import time

from lyrarr.metadata.language_detect import is_synced_lyrics
from lyrarr.metadata.lyrics_text import classify_lyrics_text, is_substantive_lyrics, strip_credit_blocks

from .. import repairs as rp
from ..registry import Check, CheckSpec, Issue, TrackCheck, failed, fixed, register, skipped

AUDIO_EXTENSIONS = ('.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.mp4', '.aac', '.wma', '.wav', '.ape',
                    '.wv', '.alac', '.aiff', '.aif', '.dsf')
TEMP_SUFFIX = '.lyrarr-tmp'
TEMP_MIN_AGE_S = 600


def _T():
    from lyrarr.app.database import TableTracks
    return TableTracks


def _select():
    from lyrarr.app.database import select
    return select(_T())


def _available():
    from lyrarr.app.database import and_
    T = _T()
    return and_(T.lyrics_status.in_(('available', 'manual')), T.path.is_not(None))


def _auto_source():
    from lyrarr.app.database import or_
    T = _T()
    return or_(T.lyrics_source.is_(None), T.lyrics_source == 'auto')


def _text(ctx, row):
    path, kind, sr = ctx.sidecar(row)
    if sr is None or not sr.lossless:
        return path, kind, None
    return path, kind, sr.text


def walk_library(ctx):
    """[(dirpath, filenames)] for every reachable artist folder (depth <= 3), cached per run."""
    if 'walk' in ctx.cache:
        return ctx.cache['walk']
    from lyrarr.app.database import TableArtists, select
    out = []
    seen = set()
    for artist in ctx.artists(_WalkDummy, select(TableArtists)):
        base = os.path.normpath(artist.path)
        if base in seen or not os.path.isdir(base):
            continue
        seen.add(base)
        depth0 = base.count(os.sep)
        for dirpath, dirnames, filenames in os.walk(base):
            if dirpath.count(os.sep) - depth0 >= 3:
                dirnames[:] = []
            out.append((dirpath, filenames))
    ctx.cache['walk'] = out
    return out


class _WalkSpec:
    needs_library = True


class _WalkDummy:
    spec = _WalkSpec()


# ---------------------------------------------------------------------------

@register
class LibraryUnreachable(Check):
    spec = CheckSpec(
        id='files.library_unreachable', code='F-0',
        title='Music folder not reachable',
        description='A library root folder is missing or most of its audio files are gone (unmounted). '
                    'Tracks under it are skipped by every file check; nothing is written there.',
        category='files', severity='critical', cost='fs_stat', fix_mode='report', bootstrap=True, order=-1,
    )

    def detect(self, ctx):
        for st in ctx.reach.unreachable:
            sev = 'critical' if st.checked else 'low'
            yield Issue(
                entity_type='file', entity_id=None, entity_key=f'root:{st.root}', path=st.root,
                title=f"Music folder not reachable: {st.root}", severity=sev,
                details={'exists': st.exists, 'checked': st.checked, 'missing': st.missing,
                         'ratio': st.ratio, 'sample_path': st.sample_path},
            )


@register
class StatusDrift(TrackCheck):
    spec = CheckSpec(
        id='files.status_drift', code='F-1',
        title='Lyrics status does not match the disk',
        description='Tracks marked missing that have a lyrics file, and tracks marked available whose '
                    'lyrics file is gone.',
        category='files', severity='medium', cost='fs_read', fix_mode='auto',
        needs_library=True, bootstrap=True, order=30,
    )

    def query(self, ctx):
        from lyrarr.app.database import or_
        T = _T()
        return _select().where(T.path.is_not(None), T.lyrics_replace_requested.is_(None),
                               or_(T.lyrics_status.is_(None), T.lyrics_status.in_(('missing', 'available', 'manual'))))

    def examine(self, ctx, row):
        from lyrarr.metadata.lyrics_store import audio_present
        path, kind, _ = ctx.sidecar(row)
        if row.lyrics_status in (None, 'missing'):
            if kind == 'lyrics':
                return self.track_issue(row, f"'{row.title}' has a lyrics file but is marked missing", 'adopt',
                                        path=path)
            return None
        if kind is None and audio_present(row):
            return self.track_issue(row, f"'{row.title}' is marked available but its lyrics file is gone",
                                    'mark_missing', path=path)
        return None

    def fix_adopt(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import adopt_sidecar
        row = rp.fresh_track(issue.track_id)
        if row is None or not adopt_sidecar(row, keep_source=True):
            return failed('the lyrics file could not be adopted')
        return fixed('marked available')

    def fix_mark_missing(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        return fixed('marked missing') if row and rp.mark_missing(row, retry_after=ctx.stagger()) else skipped('the track changed')


@register
class MarkerOrEmptySidecar(TrackCheck):
    spec = CheckSpec(
        id='files.marker_or_empty_sidecar', code='F-2',
        title='Lyrics files without lyrics',
        description='Available tracks whose lyrics file is empty, an "[Instrumental]" marker, a '
                    'placeholder or only credits. They are marked missing; the file is kept and can be '
                    'replaced by the next download.',
        category='files', severity='medium', cost='fs_read', fix_mode='auto',
        needs_library=True, bootstrap=True, order=31,
    )

    def query(self, ctx):
        return _select().where(_available(), _auto_source())

    def examine(self, ctx, row):
        path, kind, _ = ctx.sidecar(row)
        if kind not in ('empty', 'marker', 'placeholder', 'credits_only'):
            return None
        return self.track_issue(row, f"'{row.title}': lyrics file holds no lyrics ({kind})", 'mark_missing_keep',
                                path=path, details={'kind': kind})

    def fix_mark_missing_keep(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        return fixed('marked missing (file kept)') if row and rp.mark_missing(row, retry_after=ctx.stagger()) else skipped('the track changed')


@register
class Encoding(TrackCheck):
    spec = CheckSpec(
        id='files.encoding', code='F-3', requires_download_lock=True,
        title='Lyrics file encoding problems',
        description='Byte-order marks and UTF-16 files are re-encoded as UTF-8 (lossless, archived first). '
                    'Files that are not valid UTF-8 or contain NUL/replacement characters are reported.',
        category='files', severity='low', cost='fs_read', fix_mode='auto', needs_library=True, order=32,
    )

    def query(self, ctx):
        return _select().where(_T().path.is_not(None))

    def examine(self, ctx, row):
        path, kind, sr = ctx.sidecar(row)
        if kind is None:
            return None
        if sr is None:
            return self.track_issue(row, f"'{row.title}': lyrics file could not be read", None, fix_mode='report',
                                    path=path)
        if not sr.lossless:
            return self.track_issue(row, f"'{row.title}': lyrics file is not valid UTF-8", None,
                                    fix_mode='report', path=path, details={'encoding': sr.encoding})
        if '\x00' in sr.text or '�' in sr.text:
            return self.track_issue(row, f"'{row.title}': lyrics file contains NUL or replacement characters",
                                    None, fix_mode='report', path=path)
        if sr.had_bom or sr.encoding not in ('utf-8',):
            return self.track_issue(row, f"'{row.title}': lyrics file is {sr.encoding}"
                                         f"{' with a byte-order mark' if sr.had_bom else ''}",
                                    'normalize_encoding', path=path, details={'encoding': sr.encoding,
                                                                              'bom': sr.had_bom})
        return None

    def fix_normalize_encoding(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        return rp.rewrite_file(ctx, row, text, self.spec.code, recompute_language=False)


def _validate_lrc(text):
    from lyrarr.metadata.lrc_repair import validate_lrc
    return validate_lrc(text)


@register
class LrcTimingInvalid(TrackCheck):
    spec = CheckSpec(
        id='files.lrc_timing_invalid', code='F-4', requires_download_lock=True,
        title='Synced lyrics with broken timestamps',
        description='Synced lyrics with out-of-order, duplicate or invalid timestamps. Automatic and '
                    'generated files are repaired (archived first); your own files are reported.',
        category='files', severity='medium', cost='fs_read', fix_mode='auto', needs_library=True, order=33,
    )

    def query(self, ctx):
        return _select().where(_available())

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or not is_synced_lyrics(text) or rp.is_fake_synced(text):
            return None
        if any(_OLD_REPAIR_RE.match(line) for line in text.splitlines()):
            return None  # F-5 fixes those lines first
        v = _validate_lrc(text)
        if not v.get('needs_repair'):
            return None
        types = sorted({i.get('type') for i in v.get('issues', []) if isinstance(i, dict)})
        details = {'issues': types}
        src = row.lyrics_source
        if src in rp.AUTO_SOURCES or (src == 'generated' and set(types) <= {'invalid_seconds'}):
            return self.track_issue(row, f"'{row.title}': broken LRC timestamps", 'repair_lrc', path=path,
                                    details=details)
        return self.track_issue(row, f"'{row.title}': broken LRC timestamps ({src} file, not changed)", None,
                                fix_mode='report', path=path, details=details)

    def fix_repair_lrc(self, ctx, issue):
        from lyrarr.metadata.lrc_repair import repair_lrc
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        new = repair_lrc(text)
        if not new or new.strip() == text.strip():
            return skipped('nothing to repair')
        return rp.rewrite_file(ctx, row, new, self.spec.code)


_OLD_REPAIR_RE = re.compile(
    r'^\[(\d{2}:\d{2}\.\d{2})\] \[(\d{1,3}:\d{2}(?:[.:]\d{1,3})?|[a-z#][a-z#_-]{0,15}:[^\]]*)\](.*)$')


def fix_old_repair_text(text):
    header, body = [], []
    for line in text.splitlines():
        m = _OLD_REPAIR_RE.match(line)
        if not m:
            body.append(line)
            continue
        inner, rest = m.group(2), m.group(3)
        if re.match(r'^\d{1,3}:\d{2}', inner):
            mm, ss = inner.split(':', 1)
            if '.' not in ss and ':' not in ss:
                ss += '.00'
            body.append(f'[{int(mm):02d}:{ss.replace(":", ".")}]{rest}')
        else:
            header.append(f'[{inner}]')
            if rest.strip():
                body.append(f'[{m.group(1)}]{rest}')
    return '\n'.join(header + body)


@register
class OldRepairJunk(TrackCheck):
    spec = CheckSpec(
        id='files.old_repair_junk', code='F-5', requires_download_lock=True,
        title='Lyrics damaged by an older timestamp repair',
        description='Lines like "[00:12.00] [01:20]words" left by an older repair. The extra timestamp is '
                    'removed (archived first).',
        category='files', severity='low', cost='fs_read', fix_mode='auto', needs_library=True, order=34,
    )

    def query(self, ctx):
        return _select().where(_available())

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text:
            return None
        n = sum(1 for line in text.splitlines() if _OLD_REPAIR_RE.match(line))
        if not n:
            return None
        if row.lyrics_source not in rp.AUTO_SOURCES:
            return self.track_issue(row, f"'{row.title}': {n} damaged line(s) in your file", None,
                                    fix_mode='report', path=path, details={'lines': n})
        return self.track_issue(row, f"'{row.title}': {n} line(s) damaged by an older repair", 'fix_old_repair',
                                path=path, details={'lines': n})

    def fix_fix_old_repair(self, ctx, issue):
        from lyrarr.metadata.lrc_repair import repair_lrc
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        new = fix_old_repair_text(text)
        if _validate_lrc(new).get('needs_repair'):
            new = repair_lrc(new)
        return rp.rewrite_file(ctx, row, new, self.spec.code)


_ELLIPSIS_RE = re.compile(r'(?:\.\.\.|…)\s*$')


@register
class ExcerptLyrics(TrackCheck):
    spec = CheckSpec(
        id='files.excerpt_lyrics', code='F-6', requires_download_lock=True,
        title='Lyrics excerpts saved as full lyrics',
        description='Short plain lyrics that end in "..." (a provider excerpt, typically Musixmatch). '
                    'Confirmed excerpts are archived, blacklisted and removed so the track is searched again.',
        category='files', severity='medium', cost='fs_read', fix_mode='auto', needs_library=True, order=35,
    )

    def query(self, ctx):
        return _select().where(_available(), _auto_source())

    def examine(self, ctx, row):
        from datetime import datetime, timedelta
        path, kind, text = _text(ctx, row)
        if not text or is_synced_lyrics(text):
            return None
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines or len(lines) >= 25 or not _ELLIPSIS_RE.search(lines[-1]):
            return None
        hist = rp.newest_lyrics_history(row.lidarrTrackId, actions=(1, 2))
        provider = (hist.provider if hist else None) or row.lyrics_provider
        if provider and provider != 'musixmatch' and '+' not in provider and provider != 'unknown':
            return None
        confirmed = False
        if hist is not None and provider == 'musixmatch' and (hist.description or '').startswith('Downloaded') \
                and hist.metadata_path == path:
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                confirmed = mtime <= hist.timestamp + timedelta(seconds=60)
            except OSError:
                confirmed = False
        return self.track_issue(row, f"'{row.title}': lyrics look like an excerpt ({len(lines)} lines)",
                                'archive_remove_wrong', fix_mode=None if confirmed else 'review', path=path,
                                details={'provider': provider, 'confirmed': confirmed, 'lines': len(lines)})

    def fix_archive_remove_wrong(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        return rp.remove_wrong(ctx, row, self.spec.code) if row else skipped('track no longer exists')


_CONTRIB_RE = re.compile(r'^\s*\d[\d,.]*K?\s*Contributors', re.IGNORECASE)
_ENTITY_RE = re.compile(r'&#\d+;|&#x[0-9a-fA-F]+;|&(?:amp|quot|lt|gt|apos|nbsp);')


def clean_genius_header(text):
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0]
    idx = first.find('[')
    if idx > 0:
        lines[0] = first[idx:]
    elif idx < 0:
        lines = lines[1:]
    return html.unescape('\n'.join(lines))


@register
class GeniusScrapeJunk(TrackCheck):
    spec = CheckSpec(
        id='files.genius_scrape_junk', code='F-7', requires_download_lock=True,
        title='Web page junk in lyrics',
        description='Plain lyrics starting with a "42 Contributors" page header (review: re-search or clean), '
                    'or containing HTML entities like "&amp;" (decoded automatically).',
        category='files', severity='medium', cost='fs_read', fix_mode='review', needs_library=True, order=36,
    )

    def query(self, ctx):
        return _select().where(_available(), _T().lyrics_replace_requested.is_(None))

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or is_synced_lyrics(text):
            return None
        lines = text.splitlines()
        if lines and _CONTRIB_RE.match(lines[0]):
            if row.lyrics_source not in rp.AUTO_SOURCES:
                return self.track_issue(row, f"'{row.title}': web page header in your lyrics file", None,
                                        fix_mode='report', path=path)
            return self.track_issue(row, f"'{row.title}': lyrics start with a web page header", 'request_replace',
                                    path=path, details={'kind': 'contributors', 'alt_actions': ['clean_in_place']})
        if _ENTITY_RE.search(text):
            if row.lyrics_source not in rp.AUTO_SOURCES:
                return self.track_issue(row, f"'{row.title}': HTML entities in your lyrics file", None,
                                        fix_mode='report', path=path, details={'kind': 'entities'})
            return self.track_issue(row, f"'{row.title}': HTML entities in the lyrics", 'unescape', fix_mode='auto',
                                    path=path, details={'kind': 'entities'})
        return None

    def fix_request_replace(self, ctx, issue):
        return rp.request_replace(ctx, rp.fresh_track(issue.track_id), self.spec.code)

    def fix_clean_in_place(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        return rp.rewrite_file(ctx, row, clean_genius_header(text), self.spec.code)

    def fix_unescape(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        # Decode every layer ("&amp;amp;" → "&") so one apply is enough.
        new = text
        for _ in range(5):
            nxt = html.unescape(new)
            if nxt == new:
                break
            new = nxt
        if new == text:
            return skipped('nothing to decode')
        return rp.rewrite_file(ctx, row, new, self.spec.code)


def _strip_credits(text):
    new = strip_credit_blocks(text)
    same = [x.strip() for x in new.splitlines() if x.strip()] == [x.strip() for x in text.splitlines() if x.strip()]
    return None if same else new


@register
class NeteaseCredits(TrackCheck):
    spec = CheckSpec(
        id='files.netease_credits', code='F-8', requires_download_lock=True,
        title='Credit lines in lyrics',
        description='Automatic lyrics that start or end with credit lines (作词/作曲, Composer:, ...). The '
                    'credits are stripped (archived first); a file with nothing else becomes missing.',
        category='files', severity='low', cost='fs_read', fix_mode='auto', needs_library=True, order=37,
    )

    def query(self, ctx):
        return _select().where(_available(), _auto_source())

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or kind not in ('lyrics', 'credits_only'):
            return None
        if _strip_credits(text) is None:
            return None
        return self.track_issue(row, f"'{row.title}': credit lines in the lyrics", 'strip_credits', path=path)

    def fix_strip_credits(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import archive_and_remove
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        new = _strip_credits(text)
        if new is None:
            return skipped('nothing to strip')
        if classify_lyrics_text(new) != 'lyrics':
            if not archive_and_remove(row, f'audit:{self.spec.code}'):
                return failed('the file could not be archived, so it was kept')
            ctx.forget(issue.path)
            rp.mark_missing(row, retry_after=ctx.stagger())
            return fixed('only credits: archived, removed, marked missing')
        return rp.rewrite_file(ctx, row, new, self.spec.code)


def _whisper_versions(track_id):
    from lyrarr.app.database import TableLyricsVersions, and_, database, or_, select
    V = TableLyricsVersions
    return database.execute(
        select(V).where(V.lidarrTrackId == track_id, V.lyrics_type == 'plain',
                        or_(and_(V.provider == 'batch-sync-generate', V.replaced_by.is_(None)),
                            V.replaced_by == 'batch-sync-generate'))
        .order_by(V.timestamp.desc(), V.id.desc()).limit(1)
    ).scalars().first()


@register
class WhisperScrambled(TrackCheck):
    spec = CheckSpec(
        id='files.whisper_scrambled', code='F-9', requires_download_lock=True,
        title='Generated sync scrambled the line order',
        description='Lyrics synced by the Whisper batch job whose lines are in a different order than the '
                    'plain lyrics they were generated from. The plain version is kept until reviewed.',
        category='files', severity='medium', cost='fs_read', fix_mode='review',
        needs_library=True, pins_evidence=True, order=38,
    )

    def query(self, ctx):
        from lyrarr.app.database import or_
        T = _T()
        return _select().where(_available(), or_(T.lyrics_source.is_(None), T.lyrics_source == 'generated'))

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or not is_synced_lyrics(text):
            return None
        version = _whisper_versions(row.lidarrTrackId)
        if version is None:
            return None
        cur, old = rp.line_multiset(text), rp.line_multiset(version.content)
        if not cur or cur != old:
            return None
        cur_order = [ln for ln in rp._norm_for_compare(text).splitlines() if ln.strip()]
        old_order = [ln for ln in rp._norm_for_compare(version.content).splitlines() if ln.strip()]
        if cur_order == old_order:
            return None
        hist = rp.newest_lyrics_history(row.lidarrTrackId)
        details = {'version_id': version.id, 'pin_version_ids': [version.id]}
        if hist is not None and hist.provider == 'editor':
            return self.track_issue(row, f"'{row.title}': line order differs from the plain lyrics (edited later)",
                                    None, fix_mode='report', path=path, details=details)
        return self.track_issue(row, f"'{row.title}': generated sync scrambled the line order", 'restore_version',
                                path=path, details=details)

    def fix_restore_version(self, ctx, issue):
        return rp.restore_version(ctx, issue.track_id, issue.details.get('version_id'))


@register
class LegacyTxtSidecars(TrackCheck):
    spec = CheckSpec(
        id='files.legacy_txt_sidecars', code='F-10', requires_download_lock=True,
        title='Old .txt lyrics files',
        description='"<track>.txt" files written by old versions. With a .lrc present the .txt is archived '
                    'and deleted; without one its lyrics are imported.',
        category='files', severity='low', cost='fs_stat', fix_mode='review', needs_library=True, order=39,
    )

    def query(self, ctx):
        return _select().where(_T().path.is_not(None))

    def examine(self, ctx, row):
        txt = os.path.splitext(row.path)[0] + '.txt'
        if not os.path.isfile(txt):
            return None
        path, kind, _ = ctx.sidecar(row)
        if kind is not None:
            return self.track_issue(row, f"'{row.title}': old .txt lyrics file next to the .lrc", 'archive_txt',
                                    path=txt)
        sr = _read_txt(txt)
        if sr is None or not sr.lossless or classify_lyrics_text(sr.text) != 'lyrics':
            return self.track_issue(row, f"'{row.title}': old .txt file (no usable lyrics)", None, fix_mode='report',
                                    path=txt)
        blocker = _txt_import_blocker(row, sr.text)
        if blocker is not None:
            return self.track_issue(row, f"'{row.title}': lyrics in an old .txt file (not imported: {blocker})",
                                    None, fix_mode='report', path=txt, details={'reason': blocker})
        return self.track_issue(row, f"'{row.title}': lyrics in an old .txt file", 'import_txt', path=txt)

    def fix_archive_txt(self, ctx, issue):
        from datetime import datetime

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from lyrarr.app.database import TableLyricsVersions, database, select
        sr = _read_txt(issue.path)
        if sr is None or not sr.lossless:
            return failed('the .txt file could not be read losslessly')
        V = TableLyricsVersions
        newest = database.execute(select(V.content).where(V.lidarrTrackId == issue.track_id)
                                  .order_by(V.timestamp.desc(), V.id.desc()).limit(1)).scalar()
        if sr.text.strip() and (newest or '').rstrip('\r\n') != sr.text.rstrip('\r\n'):
            database.execute(sqlite_insert(TableLyricsVersions).values(
                lidarrTrackId=issue.track_id, content=sr.text.rstrip('\r\n'),
                lyrics_type='synced' if is_synced_lyrics(sr.text) else 'plain',
                provider='legacy-txt', replaced_by=f'audit:{self.spec.code}', timestamp=datetime.now()))
        try:
            os.remove(issue.path)
        except OSError as e:
            return failed(f'could not delete {issue.path}: {e}')
        return fixed('archived and deleted the .txt file')

    def fix_import_txt(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import persist_lyrics_result, sidecar_path
        row = rp.fresh_track(issue.track_id)
        if row is None or not row.path:
            return skipped('track no longer exists')
        if os.path.exists(sidecar_path(row)):
            return skipped('the track has a lyrics file now')
        sr = _read_txt(issue.path)
        if sr is None or not sr.lossless:
            return failed('the .txt file could not be read losslessly')
        if classify_lyrics_text(sr.text) != 'lyrics':
            return skipped('the .txt file has no usable lyrics')
        blocker = _txt_import_blocker(row, sr.text)
        if blocker is not None:
            return skipped(f'not imported: {blocker}')
        r = persist_lyrics_result(row, sr.text, 'legacy-txt', source=None, expect_status=_TXT_IMPORT_STATUSES)
        if not r:
            return failed(r.message or 'import failed')
        try:
            os.remove(issue.path)
        except OSError:
            pass
        ctx.forget(os.path.splitext(row.path)[0] + '.lrc')
        return fixed('imported the .txt lyrics')


# F-10 imports only onto tracks without lyrics and without a user decision:
# never onto blacklisted (user-rejected) or instrumental rows.
_TXT_IMPORT_STATUSES = ('missing', None)


def _txt_import_blocker(row, text):
    """Why the .txt lyrics must not be imported for `row` (None when they may be)."""
    from lyrarr.metadata.lyrics_store import text_is_blacklisted
    from lyrarr.metadata.validation import validate_lyrics
    if row.lyrics_status not in _TXT_IMPORT_STATUSES:
        return f'track status is {row.lyrics_status}'
    if getattr(row, 'instrumental_override', None) == 'always':
        return 'the track is marked instrumental'
    if text_is_blacklisted(row.lidarrTrackId, text):
        return 'these lyrics are blacklisted for the track'
    v = validate_lyrics(text, track_title=row.title, duration_s=row.duration)
    if v.get('is_instrumental'):
        return 'the text reads as an instrumental marker'
    errors = [i for i in v.get('issues', []) if i.get('severity') == 'error']
    if errors:
        return f"validation failed ({errors[0].get('type')})"
    return None


def _read_txt(path):
    from lyrarr.metadata.lyrics_store import read_sidecar
    return read_sidecar(path)


def _orphans(ctx):
    out = []
    for dirpath, filenames in walk_library(ctx):
        stems = {os.path.splitext(f)[0] for f in filenames if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS}
        for f in filenames:
            if f.lower().endswith('.lrc') and not f.startswith('.') and os.path.splitext(f)[0] not in stems:
                out.append(os.path.join(dirpath, f))
    return sorted(out)


@register
class OrphanSidecars(Check):
    spec = CheckSpec(
        id='files.orphan_sidecars', code='F-11', requires_download_lock=True,
        title='Lyrics files without an audio file',
        description='.lrc files whose audio file is gone (left behind by renames). Moved back to the right '
                    'track when lyrarr\'s history proves where they belong.',
        category='files', severity='low', cost='fs_stat', fix_mode='review', needs_library=True, order=40,
    )

    def detect(self, ctx):
        paths = _orphans(ctx)
        for i, path in enumerate(paths, 1):
            if ctx.cancelled:
                return
            ctx.progress(i, len(paths))
            yield self._examine(path)

    def _examine(self, path):
        target = _rename_target(path)
        issue = Issue(entity_type='file', entity_id=None, entity_key=f'file:{path}', path=path,
                      title=f"Lyrics file without audio: {os.path.basename(path)}")
        if target is not None:
            row, new_path = target
            issue.fix_action = 'rename_sidecar'
            issue.track_id, issue.album_id, issue.artist_id = row.lidarrTrackId, row.albumId, row.artistId
            issue.details = {'target': new_path, 'track_title': row.title}
        else:
            issue.fix_mode = 'report'
        return issue

    def recheck(self, ctx, issue_row):
        path = issue_row.path
        if not path or not os.path.isfile(path) or not ctx.path_ok(path):
            return None
        stem = os.path.splitext(path)[0]
        if any(os.path.isfile(stem + ext) for ext in AUDIO_EXTENSIONS):
            return None
        return self._examine(path)

    def fix_rename_sidecar(self, ctx, issue):
        from lyrarr.metadata.lyrics_store import reconcile_track_lyrics
        target = issue.details.get('target')
        if not target or os.path.exists(target):
            return skipped('the target already has a lyrics file')
        try:
            os.replace(issue.path, target)
        except OSError as e:
            return failed(f'could not move the file: {e}')
        row = rp.fresh_track(issue.track_id)
        if row is not None:
            reconcile_track_lyrics(row)
        return fixed(f'moved to {os.path.basename(target)}')


def _rename_target(path):
    """(track row, new sidecar path) when history proves the orphan belongs to a track
    whose audio exists and whose own sidecar slot is free; else None."""
    from lyrarr.app.database import TableHistory, database, select
    from lyrarr.metadata.lyrics_store import audio_present, sidecar_path
    ids = database.execute(
        select(TableHistory.lidarrTrackId).where(TableHistory.metadata_path == path,
                                                 TableHistory.lidarrTrackId.is_not(None)).distinct()
    ).scalars().all()
    if len(ids) != 1:
        return None
    row = rp.fresh_track(ids[0])
    if row is None or not audio_present(row):
        return None
    new_path = sidecar_path(row)
    if not new_path or new_path == path or os.path.exists(new_path):
        return None
    if os.path.dirname(new_path) != os.path.dirname(path):
        return None
    return row, new_path


@register
class TempLeftovers(Check):
    spec = CheckSpec(
        id='files.temp_leftovers', code='F-12',
        title='Leftover temporary files',
        description='".<name>.lyrarr-tmp" files left by an interrupted write (older than 10 minutes).',
        category='files', severity='info', cost='fs_stat', fix_mode='auto', needs_library=True, bootstrap=True,
        order=41,
    )

    def detect(self, ctx):
        now = time.time()
        for dirpath, filenames in walk_library(ctx):
            for f in filenames:
                if f.startswith('.') and f.endswith(TEMP_SUFFIX):
                    path = os.path.join(dirpath, f)
                    issue = self._examine(path, now)
                    if issue is not None:
                        yield issue

    def _examine(self, path, now=None):
        try:
            age = (now or time.time()) - os.path.getmtime(path)
        except OSError:
            return None
        if age < TEMP_MIN_AGE_S:
            return None
        return Issue(entity_type='file', entity_id=None, entity_key=f'file:{path}', path=path,
                     title=f'Leftover temp file: {os.path.basename(path)}', fix_action='delete_temp')

    def recheck(self, ctx, issue_row):
        if not issue_row.path or not ctx.path_ok(issue_row.path) or not os.path.isfile(issue_row.path):
            return None
        return self._examine(issue_row.path)

    def fix_delete_temp(self, ctx, issue):
        try:
            os.remove(issue.path)
        except FileNotFoundError:
            return skipped('already gone')
        except OSError as e:
            return failed(f'could not delete: {e}')
        return fixed('deleted')


@register
class FakeSynced(TrackCheck):
    spec = CheckSpec(
        id='files.fake_synced', code='F-13', requires_download_lock=True,
        title='Fake synced lyrics',
        description='"Synced" lyrics whose lines all share one timestamp. Rewritten as plain lyrics '
                    '(archived first).',
        category='files', severity='low', cost='fs_read', fix_mode='auto', needs_library=True, order=42,
    )

    def query(self, ctx):
        return _select().where(_available(), _auto_source())

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or not is_synced_lyrics(text) or not rp.is_fake_synced(text):
            return None
        return self.track_issue(row, f"'{row.title}': every line has the same timestamp", 'to_plain', path=path)

    def fix_to_plain(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        return rp.rewrite_file(ctx, row, rp.to_plain(text), self.spec.code)


@register
class EmbeddedVsSidecar(TrackCheck):
    spec = CheckSpec(
        id='files.embedded_vs_sidecar', code='F-14',
        title='Lyrics file differs from the audio tags',
        description='The lyrics file and the lyrics in the audio file\'s own tags are different songs '
                    '(report only).',
        category='files', severity='info', cost='audio', fix_mode='report', needs_library=True,
        scheduled=False, default_enabled=False, order=43,
    )

    def query(self, ctx):
        return _select().where(_available())

    def detect(self, ctx):
        available, _ = rp.read_embedded(None)
        if not available:
            ctx.note('reading embedded lyrics is not available in this build')
            return
        yield from super().detect(ctx)

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or kind != 'lyrics':
            return None
        _, emb = rp.read_embedded(row.path)
        etext = getattr(emb, 'text', None)
        if not etext or not is_substantive_lyrics(etext):
            return None
        ratio = rp.equivalence(text, etext)
        if ratio >= 0.5:
            return None
        return self.track_issue(row, f"'{row.title}': lyrics file and audio tags differ", None, path=path,
                                details={'equivalence': round(ratio, 3)})


def m1_applies(row, text):
    """M-1's territory (wrong-song suspect): synced NULL/auto, 1 <= duration <= 3600,
    last timestamp > duration + 15 s."""
    dur = row.duration
    if row.lyrics_source not in rp.AUTO_SOURCES or not dur or not (1 <= dur <= 3600):
        return False
    ts = rp.lyric_timestamps(text)
    return bool(ts) and max(ts) > dur + 15


# validation.synced_fits_duration reasons -> issue wording
_OVERFLOW_PROBLEMS = {
    'overrun': 'synced lyrics run past the end of the track',
    'starts_after_end': 'synced lyrics start after the end of the track',
    'fake_synced': 'synced timing covers only a small part of the track',
}


@register
class SyncOverflow(TrackCheck):
    spec = CheckSpec(
        id='files.sync_overflow', code='F-15', requires_download_lock=True,
        title='Synced lyrics timed for a different edit',
        description='Synced lyrics that run past the end of the track (timed for another edit of the song) '
                    'or whose timing covers only a small part of it. '
                    'Review: re-search (the file is kept unless something better is found); when nothing '
                    'better exists, convert to plain lyrics.',
        category='files', severity='medium', cost='fs_read', fix_mode='review', needs_library=True, order=44,
    )

    def query(self, ctx):
        T = _T()
        return _select().where(_available(), T.duration.is_not(None), T.duration >= 1, T.duration <= 10800,
                               T.lyrics_replace_requested.is_(None))

    def examine(self, ctx, row):
        path, kind, text = _text(ctx, row)
        if not text or not is_synced_lyrics(text) or rp.is_fake_synced(text):
            return None
        ok, reason = rp.synced_fits_duration(text, row.duration)
        if ok or m1_applies(row, text):
            return None
        ts = rp.lyric_timestamps(text)
        details = {'reason': reason, 'duration': row.duration, 'last_ts': round(max(ts), 2) if ts else None}
        problem = _OVERFLOW_PROBLEMS.get(reason, _OVERFLOW_PROBLEMS['overrun'])
        if row.lyrics_source not in rp.AUTO_SOURCES:
            return self.track_issue(row, f"'{row.title}': {problem} ({row.lyrics_source} file)", None,
                                    fix_mode='report', path=path, details=details)
        kept = rp.replace_kept_at(row)
        if kept is not None:
            details['replace_kept_at'] = kept
            return self.track_issue(row, f"'{row.title}': re-searched on {kept:%Y-%m-%d}, nothing better found",
                                    'convert_plain', path=path, details=details)
        details['alt_actions'] = ['convert_plain']
        return self.track_issue(row, f"'{row.title}': {problem}", 'request_replace', path=path, details=details)

    def fix_request_replace(self, ctx, issue):
        return rp.request_replace(ctx, rp.fresh_track(issue.track_id), self.spec.code)

    def fix_convert_plain(self, ctx, issue):
        row = rp.fresh_track(issue.track_id)
        if row is None or row.lyrics_source not in rp.AUTO_SOURCES:
            return skipped('only automatic lyrics are converted')
        _, _, text = _text(ctx, row)
        if text is None:
            return failed('the file could not be read losslessly')
        return rp.rewrite_file(ctx, row, rp.to_plain(text), self.spec.code)
