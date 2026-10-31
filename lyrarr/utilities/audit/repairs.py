"""Shared repair primitives for the audit checks.

Every file change goes through lyrarr.metadata.lyrics_store (archive first,
atomic writes, never makedirs, CAS on the row). Nothing here deletes a
directory or touches a manual/translated/generated file except through an
explicit, archived restore the user chose.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from .registry import failed, fixed, skipped

logger = logging.getLogger(__name__)

USER_SOURCES = ('manual', 'translated', 'generated')
AUTO_SOURCES = (None, 'auto')
CURATED_PROVIDERS = ('editor', 'upload', 'restored', 'manual')


# ---------------------------------------------------------------------------
# Locks and rows
# ---------------------------------------------------------------------------

@contextmanager
def exclusive_downloads(timeout_s=600):
    """download_worker.exclusive_downloads(): yields whether the lock was acquired."""
    from lyrarr.metadata import download_worker as dw
    with dw.exclusive_downloads(timeout_s) as ok:
        yield ok


def downloads_running():
    try:
        from lyrarr.metadata import download_worker as dw
        return dw.downloads_in_progress()
    except Exception:
        return False


def fresh_track(track_id):
    from lyrarr.app.database import TableTracks, database, select
    if track_id is None:
        return None
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


def parse_json(value):
    if value is None or value == '':
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def newest_lyrics_history(track_id, *, actions=(1, 2, 4), exclude_publish=True):
    from lyrarr.app.database import TableHistory, database, or_, select
    q = select(TableHistory).where(
        TableHistory.lidarrTrackId == track_id,
        TableHistory.metadata_type == 'lyrics',
        TableHistory.action.in_(actions),
    )
    if exclude_publish:
        q = q.where(or_(TableHistory.description.is_(None), ~TableHistory.description.like('Published lyrics%')))
    return database.execute(q.order_by(TableHistory.timestamp.desc(), TableHistory.id.desc()).limit(1)).scalars().first()


def is_curated_provider(provider):
    return bool(provider) and (provider in CURATED_PROVIDERS or provider.startswith('batch-translate-'))


def replace_kept_at(row):
    """When the worker's replace mode last kept this row's file ('nothing better
    found') after the file was written, else None."""
    from lyrarr.app.database import TableHistory, database, select
    hist = database.execute(
        select(TableHistory)
        .where(TableHistory.lidarrTrackId == row.lidarrTrackId, TableHistory.action == 7,
               TableHistory.details.like('%"replace_result": "kept"%'))
        .order_by(TableHistory.timestamp.desc(), TableHistory.id.desc()).limit(1)
    ).scalars().first()
    if hist is None:
        return None
    from lyrarr.metadata.lyrics_store import sidecar_path
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(sidecar_path(row)))
    except (OSError, TypeError):
        mtime = None
    if mtime is not None and hist.timestamp < mtime - timedelta(seconds=60):
        return None
    return hist.timestamp


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_TS_RE = re.compile(r'\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]')
_OFFSET_RE = re.compile(r'^\s*\[offset:\s*([+-]?\d+)\s*\]\s*$', re.IGNORECASE | re.MULTILINE)
_META_LINE_RE = re.compile(r'^\s*\[[a-zA-Z#][a-zA-Z#_-]{0,15}:[^\]]*\]\s*$')


def lyric_timestamps(text):
    """Seconds of every timestamp that starts a lyric line, honouring [offset:] (ms)."""
    if not text:
        return []
    m = _OFFSET_RE.search(text)
    offset = int(m.group(1)) / 1000.0 if m else 0.0
    out = []
    for line in text.splitlines():
        pos = 0
        stamps = []
        while True:
            mm = _TS_RE.match(line, pos)
            if not mm:
                break
            frac = mm.group(3) or '0'
            stamps.append(int(mm.group(1)) * 60 + int(mm.group(2)) + int(frac) / (10 ** len(frac)))
            pos = mm.end()
            while pos < len(line) and line[pos] == ' ':
                pos += 1
        if stamps and line[pos:].strip():
            out.extend(s - offset for s in stamps)
    return out


def synced_fits_duration(content, duration_s):
    """(ok, reason) of validation.synced_fits_duration: reason 'overrun',
    'starts_after_end' or 'fake_synced'."""
    from lyrarr.metadata import validation
    return validation.synced_fits_duration(content, duration_s)


def is_fake_synced(content):
    """Timing that carries no information (duration-independent variant)."""
    from lyrarr.metadata import lrc_repair
    return bool(lrc_repair.is_fake_synced(content))


def to_plain(content):
    """Lyric text without LRC timestamps, enhanced tags or meta tag lines."""
    lines = []
    for line in (content or '').splitlines():
        if _META_LINE_RE.match(line):
            continue
        stripped = _TS_RE.sub('', line)
        stripped = re.sub(r'<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>', '', stripped)
        lines.append(stripped.strip())
    text = '\n'.join(lines).strip('\n')
    return re.sub(r'\n{3,}', '\n\n', text)


def _norm_for_compare(text):
    from lyrarr.metadata.merge import _normalize_for_comparison
    return _normalize_for_comparison(text or '')


def equivalence(a, b):
    """0..1 similarity of two lyrics texts (timestamps/meta ignored)."""
    na, nb = _norm_for_compare(a), _norm_for_compare(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb, autojunk=False).ratio()


def line_multiset(text):
    from collections import Counter
    return Counter(line for line in _norm_for_compare(text).splitlines() if line.strip())


def read_embedded(path):
    """(available, EmbeddedLyrics|None) via embed.read_embedded_lyrics; available is
    False only when the tag reader can't be imported (mutagen missing)."""
    try:
        from lyrarr.metadata import embed
    except Exception:
        return False, None
    if path is None:
        return True, None
    try:
        return True, embed.read_embedded_lyrics(path)
    except Exception as e:
        logger.debug(f"embedded read failed for {path}: {e}")
        return True, None


# ---------------------------------------------------------------------------
# Row updates
# ---------------------------------------------------------------------------

def _missing_values(row, now, retry_after=None):
    from lyrarr.metadata.lyrics_store import cleared_provenance
    values = dict(
        lyrics_status='missing', hasLyrics=False, is_synced=False, lyrics_source=None,
        lyrics_replace_requested=None, lyrics_retry_count=0, lyrics_retry_after=retry_after,
        lyrics_transient_count=0, lyrics_cooldown_count=0, lyrics_retry_set_at=now,
        updated_at_timestamp=now,
    )
    values.update(cleared_provenance())
    if (row.language_source or 'detected') not in ('manual',):
        values['detected_language'] = None
    return values


def mark_missing(row, *, expect_status=('available', 'manual'), retry_after=None):
    """available → missing (source NULL, retries/counters reset, provenance dropped).

    retry_after: pass ctx.stagger() for bulk repairs so a mass flip (a lost
    sidecar folder) doesn't requeue every track for the very next pass."""
    from lyrarr.app.database import TableTracks, database, update
    now = datetime.now()
    n = database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.lyrics_status.in_(expect_status))
        .values(**_missing_values(row, now, retry_after))
    ).rowcount
    return bool(n)


def update_track(track_id, guard=(), **values):
    from lyrarr.app.database import TableTracks, database, update
    values.setdefault('updated_at_timestamp', datetime.now())
    return bool(database.execute(
        update(TableTracks).where(TableTracks.lidarrTrackId == track_id, *guard).values(**values)
    ).rowcount)


def rewrite_file(ctx, row, new_text, code, *, recompute_language=True):
    """Archive the current sidecar, atomically write `new_text`, refresh is_synced,
    language and content hash. Never for an unreachable track."""
    from lyrarr.metadata import lyrics_store as ls
    from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
    from lyrarr.metadata.lyrics_text import normalize_text_for_write
    row = fresh_track(row.lidarrTrackId) or row
    if not ls.audio_present(row):
        return failed('the audio file is not reachable')
    path = ls.sidecar_path(row)
    if not os.path.isfile(path):
        return skipped('the lyrics file is gone')
    sr = ls.read_sidecar(path)
    if sr is None:
        return failed('the lyrics file could not be read')
    content = normalize_text_for_write(new_text)
    if not content.strip():
        return failed('nothing left to write')
    # Carry the content hash forward only when the file being rewritten is
    # provably lyrarr's own text. Otherwise leave the hash alone (a stale hash,
    # or NULL, is what tells the upgrade pass / S-15 that someone else edited
    # the file); re-stamping it would launder a user edit into "lyrarr wrote it".
    proven = ls.file_provenance_proven(row, path, sr)
    if not ls._archive_existing(row.lidarrTrackId, path, origin_provider=ls._origin_provider(row),
                                replaced_by=f'audit:{code}'):
        return failed(ls.PERSIST_REASONS['archive_failed'])
    try:
        ls.atomic_write_text(path, content)
    except OSError as e:
        return failed(ls.PERSIST_REASONS['write_failed'].format(path=path, error=e))
    ctx.forget(path)
    values = dict(is_synced=is_synced_lyrics(content))
    if proven:
        values['lyrics_content_hash'] = ls.content_hash(content)
    if recompute_language and (row.language_source or 'detected') not in ('manual', 'translated'):
        values['detected_language'] = detect_language(content)
    update_track(row.lidarrTrackId, **values)
    return fixed('rewritten (previous version archived)', path=path)


def remove_wrong(ctx, row, code, *, mark_status_missing=True):
    """'This content is wrong': archive, blacklist and remove the file (NULL/auto only)."""
    from lyrarr.metadata import lyrics_store as ls
    row = fresh_track(row.lidarrTrackId) or row
    if row.lyrics_source in USER_SOURCES:
        return skipped('the lyrics were saved by the user')
    if not ls.audio_present(row):
        return failed('the audio file is not reachable')
    if not ls.archive_and_remove(row, f'audit:{code}', blacklist_reason=code):
        return failed(ls.PERSIST_REASONS['archive_failed'])
    ctx.forget(ls.sidecar_path(row))
    if mark_status_missing and row.lyrics_status in ('available', 'manual'):
        mark_missing(row, retry_after=ctx.stagger())
    return fixed('archived, blacklisted and removed')


def restore_version(ctx, track_id, version_id):
    """Restore an archived version as a manual save (archives the current file)."""
    from lyrarr.app.database import TableLyricsVersions, database, select
    from lyrarr.metadata import lyrics_store as ls
    row = fresh_track(track_id)
    if row is None:
        return skipped('track no longer exists')
    version = database.execute(
        select(TableLyricsVersions).where(TableLyricsVersions.id == version_id)
    ).scalars().first()
    if version is None or version.lidarrTrackId != track_id:
        return failed('the archived version no longer exists')
    was_instrumental = row.lyrics_status == 'instrumental'
    r = ls.persist_lyrics_result(row, version.content, 'restored', source='manual', expect_status=None,
                                 history_details={'audit_run': ctx.run_id, 'version_id': version_id})
    if not r:
        return failed(r.message or r.reason or 'restore failed')
    ctx.forget(ls.sidecar_path(row))
    if was_instrumental:
        ctx.counts['cleared'] += 1
    return fixed('restored the archived version')


def request_replace(ctx, row, code):
    from lyrarr.metadata import lyrics_store as ls
    row = fresh_track(row.lidarrTrackId) or row
    if row.lyrics_status != 'available' or row.lyrics_source not in AUTO_SOURCES:
        return skipped('only automatic lyrics can be re-searched')
    if not ls.request_replace(row.lidarrTrackId, code, actor=ctx.actor, retry_after=ctx.replace_retry_after()):
        return failed('the re-search could not be requested')
    from .registry import pending
    return pending('Re-search pending: the file is kept unless something better is found')
