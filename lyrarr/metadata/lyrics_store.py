"""Shared lyrics persistence, disk reconciliation, instrumental state and
per-result blacklisting.

Single source of truth for writing a chosen lyrics file to disk and recording it
in the database, used by the scheduled download worker, the manual
save/upload/editor API and the audit, so they all behave identically
(PLAN §1.3). Also:

* sidecar reading with strict decoding (BOM / UTF-16 aware, never cp1252),
  atomic writes, and per-library-root reachability;
* the instrumental helpers (set/clear/hint/dismiss) that record reason,
  evidence and a history row for every classification change;
* replace-mode requests, content-hash backfill and tombstones;
* per-result blacklisting: a rejected (wrong) match is hashed and stored so
  the automatic downloader never re-selects that same content on later runs.
"""

import codecs
import json
import logging
import os
import random
import shutil
import threading
import time
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta

# NOTE: lyrarr.app.database is imported lazily inside the functions that need it.
# Importing it at module load pulls in app config + argument parsing (side
# effects on import), which would make the pure helpers here (content_hash,
# result_is_blacklisted) un-importable in unit tests. language_detect, merge
# and lyrics_text are side-effect-free, so they're safe to import at top.
from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
from lyrarr.metadata.lyrics_text import (
    classify_lyrics_text,
    instrumental_title_tier,
    normalize_text_for_write,
)
from lyrarr.metadata.merge import _content_hash

logger = logging.getLogger(__name__)

# Most recent archived (non-pinned) lyrics versions kept per track.
_MAX_VERSIONS_PER_TRACK = 5

# Tombstones older than this are ignored and purged by maintenance.
TOMBSTONE_TTL_DAYS = 30

_USER_SOURCES = ('manual', 'translated', 'generated')
_USER_LANGUAGE_SOURCES = ('manual', 'translated')
_NON_USER_ACTORS = ('bootstrap', 'audit', 'worker', 'maintenance', 'sync')


def content_hash(text):
    """Stable hash of lyrics content (timestamps/metadata stripped, lowercased).

    Returns None for empty/whitespace input. Shared with merge so a synced and a
    plain copy of the same lyrics hash identically.
    """
    return _content_hash((text or '').lstrip('\ufeff'))


def _now():
    return datetime.now()


def _json(value):
    if value is None:
        return None
    return json.dumps(value, default=str, ensure_ascii=False)


def _parse_json(value):
    if value is None or value == '':
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _fresh_row(track_or_id):
    """Re-read a TableTracks row (by row object or id); None when gone."""
    from lyrarr.app.database import TableTracks, database, select
    track_id = getattr(track_or_id, 'lidarrTrackId', track_or_id)
    if track_id is None:
        return None
    return database.execute(
        select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
    ).scalars().first()


def _in_expected(column, expected):
    """SQL condition: column value is in `expected` (None in the tuple = NULL)."""
    from lyrarr.app.database import or_
    values = [v for v in expected if v is not None]
    conds = []
    if values:
        conds.append(column.in_(values))
    if any(v is None for v in expected):
        conds.append(column.is_(None))
    if not conds:
        from sqlalchemy import false
        return false()
    return or_(*conds)


def _matches_expected(value, expected):
    return expected is None or value in tuple(expected)


# ---------------------------------------------------------------------------
# Files: paths, strict reads, atomic writes
# ---------------------------------------------------------------------------

SidecarRead = namedtuple('SidecarRead', 'text encoding lossless had_bom size')


def sidecar_path(track):
    """`<stem>.lrc` next to the track's audio file, or None without a path."""
    path = getattr(track, 'path', None)
    if not path:
        return None
    return os.path.splitext(path)[0] + '.lrc'


def decode_lyrics_bytes(raw):
    """Decode lyrics bytes strictly. Returns a SidecarRead.

    BOM sniff (UTF-8 / UTF-16 LE/BE / UTF-32) → strict UTF-8 → otherwise
    UTF-8 with replacement characters and lossless=False. Never guesses a
    legacy codepage (a cp1252 guess silently corrupts CJK/Cyrillic text).
    """
    raw = bytes(raw or b'')
    size = len(raw)
    boms = (
        (codecs.BOM_UTF32_LE, 'utf-32-le'),
        (codecs.BOM_UTF32_BE, 'utf-32-be'),
        (codecs.BOM_UTF8, 'utf-8'),
        (codecs.BOM_UTF16_LE, 'utf-16-le'),
        (codecs.BOM_UTF16_BE, 'utf-16-be'),
    )
    for bom, encoding in boms:
        if raw.startswith(bom):
            body = raw[len(bom):]
            label = 'utf-8-sig' if encoding == 'utf-8' else encoding
            try:
                return SidecarRead(body.decode(encoding), label, True, True, size)
            except UnicodeDecodeError:
                return SidecarRead(body.decode(encoding, errors='replace'), label, False, True, size)
    try:
        text = raw.decode('utf-8')
        # A stray U+FEFF that was re-encoded inside the body (double BOM).
        return SidecarRead(text.lstrip('\ufeff'), 'utf-8', True, text.startswith('\ufeff'), size)
    except UnicodeDecodeError:
        return SidecarRead(raw.decode('utf-8', errors='replace'), 'unknown', False, False, size)


def read_sidecar(path):
    """Read a lyrics file strictly. None when it doesn't exist or can't be read."""
    if not path:
        return None
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning(f"Could not read lyrics file {path}: {e}")
        return None
    return decode_lyrics_bytes(raw)


SIDECAR_UNKNOWN = 'unknown'


def read_sidecar_kind(path):
    """(kind, SidecarRead|None) of the lyrics file at `path`, for presence decisions.

    kind is None when there is no file, classify_lyrics_text() of the text when
    it decoded losslessly, and SIDECAR_UNKNOWN when a non-empty file exists but
    its content can't be judged: not valid UTF-8 (a cp1251/Shift-JIS file
    decodes to U+FFFD runs that would classify as 'empty') or unreadable
    (permissions, I/O error). An unknown file is *present*: it counts as
    lyrics for "never becomes missing" and for vetoes, its row's status,
    source and flags are never reclassified from it, and audit F-3 reports it.
    """
    if not path or not os.path.isfile(path):
        return None, None
    sr = read_sidecar(path)
    if sr is None:
        try:
            if os.path.getsize(path) == 0:
                return 'empty', None
        except OSError:
            pass
        return SIDECAR_UNKNOWN, None
    if not sr.lossless and sr.size:
        return SIDECAR_UNKNOWN, sr
    return classify_lyrics_text(sr.text), sr


def sidecar_present(kind):
    """True when a read_sidecar_kind() kind means "lyrics are on disk"."""
    return kind in ('lyrics', SIDECAR_UNKNOWN)


# Columns describing where the current lyrics file came from. They only stay
# valid while the file on disk is the one they were recorded for.
PROVENANCE_COLUMNS = ('lyrics_provider', 'lyrics_source_id', 'lyrics_match_score', 'lyrics_match_json')


def cleared_provenance():
    """Values that drop the provenance of a file that is no longer on disk."""
    values = {c: None for c in PROVENANCE_COLUMNS}
    values['lyrics_content_hash'] = None
    return values


def stale_provenance_values(row, content):
    """Provenance values for a row that (re)gains a file holding `content`.

    The stored provenance is kept only when lyrics_content_hash proves the
    file is the one it was recorded for (e.g. a hash-verified 'auto'); any
    other file (a user drop-in after a delete, a legacy row) gets it cleared,
    so a stale provider, record id or match score isn't attached to it.
    """
    stored = getattr(row, 'lyrics_content_hash', None)
    if stored and content is not None and stored == content_hash(content):
        return {}
    return cleared_provenance()


def _temp_path(path):
    d, base = os.path.split(path)
    return os.path.join(d, f'.{base}.lyrarr-tmp')


def _copy_owner_and_mode(tmp, path):
    """Give the temp file the old file's mode/owner (or the dir's, minus x bits)."""
    try:
        if os.path.exists(path):
            st = os.stat(path)
            mode = st.st_mode & 0o7777
        else:
            st = os.stat(os.path.dirname(path) or '.')
            mode = st.st_mode & 0o666
        os.chmod(tmp, mode)
    except OSError:
        return
    chown = getattr(os, 'chown', None)
    if chown is not None:
        try:
            chown(tmp, st.st_uid, st.st_gid)
        except OSError:
            pass  # not root: keep our own ownership


def atomic_write_bytes(path, data):
    """Write bytes via '.<basename>.lyrarr-tmp' + fsync + os.replace.

    Never creates directories. On any error the temp file is removed, the
    original is left untouched, and OSError is raised.
    """
    tmp = _temp_path(path)
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _copy_owner_and_mode(tmp, path)
        os.replace(tmp, path)
    except BaseException as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        if isinstance(e, OSError):
            raise
        if isinstance(e, Exception):
            raise OSError(str(e)) from e
        raise


def atomic_write_text(path, text):
    """Atomically write UTF-8 text (no BOM, newlines as given). Raises OSError."""
    atomic_write_bytes(path, (text or '').encode('utf-8'))


def audio_present(track):
    """Whether the track's audio file exists (the mount is up and the file is there)."""
    path = getattr(track, 'path', None)
    return bool(path) and os.path.isfile(path)


# ---------------------------------------------------------------------------
# Library reachability, per root (PLAN §1.3)
# ---------------------------------------------------------------------------

RootStatus = namedtuple('RootStatus', 'root ok exists checked missing ratio sample_path')


def normalize_root(path):
    """os.path.normpath without a trailing separator ('' for empty)."""
    if not path:
        return ''
    p = os.path.normpath(str(path))
    if len(p) > 1:
        p = p.rstrip(os.sep) or os.sep
    return p


def _under(path, root):
    if root == os.sep:
        return path.startswith(os.sep)
    return path == root or path.startswith(root + os.sep)


class Reachability:
    """Result of one library_reachability() call; cheap to query per entity."""

    def __init__(self, roots=None):
        self.roots = dict(roots or {})
        self._ordered = sorted(self.roots, key=len, reverse=True)

    def root_of(self, path):
        """Longest known root containing `path`; None when outside every root."""
        if not path:
            return None
        p = normalize_root(path)
        for root in self._ordered:
            if _under(p, root):
                return root
        return None

    def ok_for(self, path):
        """False only when `path` is under a known root that is not reachable.

        Paths outside every root return True: callers still gate each track
        with audio_present().
        """
        root = self.root_of(path)
        return True if root is None else bool(self.roots[root].ok)

    @property
    def any_ok(self):
        return not self.roots or any(s.ok for s in self.roots.values())

    @property
    def all_ok(self):
        return all(s.ok for s in self.roots.values())

    @property
    def unreachable(self):
        return [s for s in self.roots.values() if not s.ok]

    def __repr__(self):
        return f'Reachability({list(self.roots.values())!r})'


def library_roots():
    """Known library roots: table_library_roots, else the distinct parent dirs
    of the artists' folders."""
    from lyrarr.app.database import TableArtists, TableLibraryRoots, database, select
    try:
        rows = database.execute(select(TableLibraryRoots.path)).scalars().all()
    except Exception as e:
        logger.debug(f"library roots table unavailable: {e}")
        rows = []
    roots = {normalize_root(p) for p in rows if p}
    if not roots:
        paths = database.execute(select(TableArtists.path)).scalars().all()
        for p in paths:
            if p:
                parent = os.path.dirname(normalize_root(p))
                if parent:
                    roots.add(normalize_root(parent))
    return sorted(r for r in roots if r)


def _like_escape(s):
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def library_reachability(sample_per_root=50, max_missing_ratio=0.2, *, scope_paths=None):
    """Per-root reachability of the music library.

    Per root: exists = os.path.isdir(root); up to `sample_per_root` random
    tracks under it are stat'ed; ok = exists and (no tracks sampled, or the
    missing ratio <= max_missing_ratio). There is no global verdict: callers
    skip entities whose root is not ok and gate the rest with audio_present().
    `scope_paths` limits the check to the roots containing those paths.
    """
    from lyrarr.app.database import TableTracks, database, select

    roots = library_roots()
    if scope_paths is not None:
        probe = Reachability({r: None for r in roots})
        wanted = {probe.root_of(p) for p in scope_paths if p}
        roots = [r for r in roots if r in wanted]

    nested = any(_under(a, b) for a in roots for b in roots if a != b)
    statuses = {}
    for root in roots:
        exists = os.path.isdir(root)
        checked = missing = 0
        sample_path = None
        prefix = root if root == os.sep else root + os.sep
        limit = sample_per_root * (3 if nested else 1)
        # Sampled even when the root is missing (every stat fails fast), so
        # checked tells "a root tracks depend on" from an unused, unmounted one.
        paths = database.execute(
            select(TableTracks.path)
            .where(TableTracks.path.like(_like_escape(prefix) + '%', escape='\\'))
            .order_by(_random_order())
            .limit(limit)
        ).scalars().all()
        if nested:
            tmp = Reachability({r: None for r in roots})
            paths = [p for p in paths if tmp.root_of(p) == root][:sample_per_root]
        for p in paths:
            checked += 1
            if not exists or not os.path.isfile(p):
                missing += 1
                if sample_path is None:
                    sample_path = p
        if sample_path is None and paths:
            sample_path = paths[0]
        ratio = (missing / checked) if checked else 0.0
        ok = exists and (checked == 0 or ratio <= max_missing_ratio)
        statuses[root] = RootStatus(root, ok, exists, checked, missing, round(ratio, 4), sample_path)
        _log_root_state(root, ok, exists, checked, missing)
    return Reachability(statuses)


# Unreachable-root warnings: the UI polls health every minute, so a root that
# stays down is logged at WARNING when it goes down and then at most hourly.
_ROOT_WARN_INTERVAL_S = 3600.0
_root_log_state = {}
_root_log_lock = threading.Lock()


def _log_root_state(root, ok, exists, checked, missing):
    if ok:
        with _root_log_lock:
            was = _root_log_state.pop(root, None)
        if was is not None:
            logger.info(f"Music folder reachable again: {root}")
        return
    if not exists and checked == 0:
        logger.debug(f"Music folder {root} is not mounted, but no tracks are under it")
        return
    now = time.monotonic()
    with _root_log_lock:
        last = _root_log_state.get(root)
        due = last is None or now - last >= _ROOT_WARN_INTERVAL_S
        if due:
            _root_log_state[root] = now
    msg = (f"Music folder not reachable: {root} "
           f"({'missing' if not exists else f'{missing}/{checked} sampled audio files missing'})")
    if due:
        logger.warning(msg)
    else:
        logger.debug(msg)


def _random_order():
    from lyrarr.app.database import func
    return func.random()


def library_reachable(sample=50, max_missing_ratio=0.2):
    """Back-compat aggregate over all roots (health summary only)."""
    reach = library_reachability(sample, max_missing_ratio)
    statuses = list(reach.roots.values())
    checked = sum(s.checked for s in statuses)
    missing = sum(s.missing for s in statuses)
    bad = reach.unreachable
    return RootStatus(
        root=None,
        ok=reach.all_ok,
        exists=all(s.exists for s in statuses),
        checked=checked,
        missing=missing,
        ratio=round(missing / checked, 4) if checked else 0.0,
        sample_path=(bad[0].sample_path or bad[0].root) if bad else None,
    )


# ---------------------------------------------------------------------------
# Archive (TableLyricsVersions)
# ---------------------------------------------------------------------------

def _origin_provider(row):
    """Provider that produced the current file: row.lyrics_provider, else the
    newest lyrics history provider (download/upgrade/manual), else 'unknown'."""
    if row is None:
        return 'unknown'
    if getattr(row, 'lyrics_provider', None):
        return row.lyrics_provider
    from lyrarr.app.database import TableHistory, database, or_, select
    try:
        provider = database.execute(
            select(TableHistory.provider)
            .where(
                TableHistory.lidarrTrackId == row.lidarrTrackId,
                TableHistory.metadata_type == 'lyrics',
                TableHistory.action.in_((1, 2, 4)),
                TableHistory.provider.is_not(None),
                or_(TableHistory.description.is_(None),
                    ~TableHistory.description.like('Published lyrics%')),
            )
            .order_by(TableHistory.timestamp.desc(), TableHistory.id.desc())
            .limit(1)
        ).scalar()
    except Exception:
        provider = None
    return provider or 'unknown'


def pinned_version_ids(track_id):
    """Archived version ids of a track that an open audit issue pins as evidence."""
    from lyrarr.app.database import (
        TableAuditIssues,
        TableAuditPins,
        TableLyricsVersions,
        database,
        select,
    )
    try:
        return set(database.execute(
            select(TableAuditPins.ref_id)
            .join(TableAuditIssues, TableAuditIssues.id == TableAuditPins.issue_id)
            .join(TableLyricsVersions, TableLyricsVersions.id == TableAuditPins.ref_id)
            .where(
                TableAuditPins.kind == 'version',
                TableAuditIssues.status.in_(('open', 'pending')),
                TableLyricsVersions.lidarrTrackId == track_id,
            )
        ).scalars().all())
    except Exception as e:
        logger.debug(f"pinned versions lookup failed: {e}")
        return set()


def pinned_history_ids_subquery():
    """SELECT of history ids pinned by an open audit issue (for _prune_history)."""
    from lyrarr.app.database import TableAuditIssues, TableAuditPins, select
    return (
        select(TableAuditPins.ref_id)
        .join(TableAuditIssues, TableAuditIssues.id == TableAuditPins.issue_id)
        .where(TableAuditPins.kind == 'history', TableAuditIssues.status.in_(('open', 'pending')))
    )


def _archive_existing(track_id, filepath, *, origin_provider, replaced_by, translated_from=None,
                      new_content=None, allow_lossy=False):
    """Archive the current on-disk lyrics into TableLyricsVersions before replacing.

    The row's `provider` is the ORIGIN of the archived text, `replaced_by` the
    incoming writer. Identical content is never stored twice: when the old
    text equals `new_content` (or the newest archived version) after
    `.strip()`, nothing new is inserted. Trims to the newest
    _MAX_VERSIONS_PER_TRACK non-pinned rows plus every pinned row.

    translated_from: set when the file being archived is the source of a
    translation that is about to replace it (its language code) — that's how
    a blacklisted translation finds the provider content it came from.

    Returns True if there was nothing to archive or it was archived, False if
    archiving failed (or the file is not valid UTF-8 and allow_lossy is off) —
    callers must not delete/overwrite the file then.
    """
    if not filepath or not os.path.isfile(filepath):
        return True
    sr = read_sidecar(filepath)
    if sr is None:
        logger.error(f"Failed to read existing lyrics for archive (track {track_id}): {filepath}")
        return False
    if not sr.lossless and not allow_lossy:
        logger.error(
            f"Not archiving lyrics for track {track_id}: {filepath} is not valid UTF-8 "
            f"(archiving would lose characters)"
        )
        return False
    # Trailing newlines are a write artefact (normalize_text_for_write adds
    # one); the archived text is the lyrics themselves.
    old = sr.text.rstrip('\r\n')
    if not old.strip():
        return True
    if new_content is not None and old.strip() == (new_content or '').lstrip('\ufeff').strip():
        return True

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableLyricsVersions, database, delete, select, update
    try:
        newest = database.execute(
            select(TableLyricsVersions)
            .where(TableLyricsVersions.lidarrTrackId == track_id)
            .order_by(TableLyricsVersions.timestamp.desc(), TableLyricsVersions.id.desc())
            .limit(1)
        ).scalars().first()
        if newest is not None and (newest.content or '').strip() == old.strip():
            # Same text archived last time: refresh it instead of a duplicate.
            database.execute(
                update(TableLyricsVersions)
                .where(TableLyricsVersions.id == newest.id)
                .values(timestamp=_now(), replaced_by=replaced_by)
            )
        else:
            database.execute(
                sqlite_insert(TableLyricsVersions).values(
                    lidarrTrackId=track_id,
                    content=old,
                    lyrics_type='synced' if is_synced_lyrics(old) else 'plain',
                    provider=origin_provider or 'unknown',
                    replaced_by=replaced_by,
                    translated_from=translated_from,
                    timestamp=_now(),
                )
            )
    except Exception as e:
        logger.error(f"Failed to archive old lyrics (track {track_id}): {e}")
        return False
    try:
        # Cap versions per track so repeated upgrades/overwrites don't grow the
        # table unbounded; versions pinned as audit evidence are never trimmed.
        pinned = pinned_version_ids(track_id)
        rows = database.execute(
            select(TableLyricsVersions.id)
            .where(TableLyricsVersions.lidarrTrackId == track_id)
            .order_by(TableLyricsVersions.timestamp.desc(), TableLyricsVersions.id.desc())
        ).scalars().all()
        unpinned = [i for i in rows if i not in pinned]
        drop = unpinned[_MAX_VERSIONS_PER_TRACK:]
        if drop:
            database.execute(delete(TableLyricsVersions).where(TableLyricsVersions.id.in_(drop)))
    except Exception as e:
        # The archive row exists; only the trim failed.
        logger.warning(f"Failed to trim archived lyrics versions (track {track_id}): {e}")
    logger.debug(f"Archived previous lyrics for track {track_id}")
    return True


# ---------------------------------------------------------------------------
# Persistence (shared write path)
# ---------------------------------------------------------------------------

@dataclass
class PersistResult:
    ok: bool
    reason: str | None = None
    message: str | None = None
    info: dict | None = None

    def __bool__(self):
        return bool(self.ok)


PERSIST_REASONS = {
    'not_found': 'Track not found.',
    'no_audio': "The audio file isn't reachable at {path}. Check that the music folder is mounted, then try again.",
    'row_changed': 'This track changed while saving (another job or user updated it). Reload and try again.',
    'archive_failed': 'The current lyrics file could not be archived, so it was not overwritten.',
    'lossy_existing': 'The current lyrics file is not valid UTF-8. It was not overwritten automatically, '
                      'to avoid losing characters.',
    'write_failed': 'Could not write {path}: {error}',
    'empty': 'No lyrics content to save.',
}


def _fail(reason, **fmt):
    template = PERSIST_REASONS[reason]
    try:
        message = template.format(**{'path': '', 'error': '', **fmt})
    except (KeyError, IndexError):
        message = template
    return PersistResult(False, reason, message, None)


def _rule_t_evidence(veto, provider=None):
    entry = {'source': 'title', 'kind': 'title', 'veto': veto, 'at': _now().isoformat()}
    if provider is not None:
        entry['provider'] = provider
    return [entry]


def _has_veto_entry(evidence_json, veto=None):
    ev = _parse_json(evidence_json)
    if not isinstance(ev, list):
        return False
    return any(isinstance(e, dict) and e.get('veto') and (veto is None or e.get('veto') == veto) for e in ev)


def history_action_for_source(source):
    """auto/None → 1 (download); manual/translated/generated → 4 (manual)."""
    return 4 if source in _USER_SOURCES else 1


_HISTORY_DESCRIPTIONS = {
    1: 'Downloaded lyrics for {title}',
    2: 'Upgraded lyrics for {title}',
    4: 'Saved lyrics for {title}',
    5: 'Published lyrics for {title}',
    7: 'Audit: saved lyrics for {title}',
}

_MATCH_COLUMN_KEYS = ('source_id', 'score')


def persist_lyrics_result(track, content, provider, *, source='auto', detect_lang=True, archive=True,
                          archive_translated_from=None, detected_language=None, record_history=True,
                          expect_status=None, expect_sources=None, match=None, history_action=None,
                          history_details=None):
    """Write chosen lyrics to disk and record status, provenance and history.

    The single shared write path (worker, manual API, audit). Steps: re-read
    the row; require the audio file (never creates directories); honour the
    expect_status / expect_sources compare-and-set; refuse to overwrite a
    lossily decoded file unless the user saves manually (then a raw
    `<stem>.lrc.orig` copy is kept first); archive the old file; write
    atomically; update the row (provenance, content hash, retry reset,
    instrumental rules M and T); add a history row.

    Args:
        source: 'auto' (or None for unknown origin, e.g. embedded tags),
            'manual', 'translated' or 'generated' — stored in lyrics_source.
        match: provenance dict from the worker: 'source_id' and 'score' go to
            their columns, every other key into lyrics_match_json.
        history_action: None derives it from `source` (1 or 4).

    Returns a PersistResult (truthy on success). `info` carries filepath,
    is_synced, detected_language and row_changed.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableHistory, TableTracks, database, update

    if track is None:
        return _fail('not_found')
    row = _fresh_row(track)
    if row is None:
        return _fail('not_found')
    if not audio_present(row):
        logger.warning(f"Not writing lyrics for track {row.lidarrTrackId}: audio not reachable at {row.path}")
        return _fail('no_audio', path=row.path or '(no path)')
    if not _matches_expected(row.lyrics_status, expect_status) or \
            not _matches_expected(row.lyrics_source, expect_sources):
        logger.info(
            f"Not writing lyrics for track {row.lidarrTrackId}: row changed "
            f"(status={row.lyrics_status}, source={row.lyrics_source})"
        )
        return _fail('row_changed')

    content = normalize_text_for_write(content)
    if not content:
        return _fail('empty')

    filepath = sidecar_path(row)

    existing = read_sidecar(filepath) if os.path.isfile(filepath) else None
    lossy = existing is not None and not existing.lossless
    if lossy:
        if source != 'manual':
            logger.warning(f"Not overwriting {filepath}: not valid UTF-8 (would lose characters)")
            return _fail('lossy_existing')
        orig = filepath + '.orig'
        if not os.path.exists(orig):
            try:
                shutil.copy2(filepath, orig)
            except OSError as e:
                logger.error(f"Could not keep a raw copy of {filepath}: {e}")
                return _fail('archive_failed')

    if archive and not _archive_existing(
            row.lidarrTrackId, filepath, origin_provider=_origin_provider(row), replaced_by=provider,
            translated_from=archive_translated_from, new_content=content, allow_lossy=lossy):
        logger.error(
            f"Not overwriting lyrics for track {row.lidarrTrackId}: "
            f"the existing file could not be archived"
        )
        return _fail('archive_failed')

    try:
        atomic_write_text(filepath, content)
    except OSError as e:
        logger.error(f"Error writing lyrics for track {row.lidarrTrackId}: {e}")
        return _fail('write_failed', path=filepath, error=str(e))

    now = _now()
    synced_flag = is_synced_lyrics(content)
    if detected_language:
        detected = detected_language
    else:
        detected = detect_language(content) if detect_lang else None

    details = dict(history_details or {})
    values = dict(
        lyrics_status='available',
        hasLyrics=True,
        is_synced=synced_flag,
        detected_language=detected,
        language_source='translated' if source == 'translated' else 'detected',
        lyrics_source=source,
        lyrics_provider=provider,
        lyrics_source_id=None,
        lyrics_match_score=None,
        lyrics_match_json=None,
        lyrics_content_hash=content_hash(content),
        lyrics_replace_requested=None,
        lyrics_retry_count=0,
        lyrics_retry_after=None,
        lyrics_transient_count=0,
        lyrics_cooldown_count=0,
        lyrics_retry_set_at=now,
        updated_at_timestamp=now,
    )
    if match:
        sid = match.get('source_id')
        values['lyrics_source_id'] = str(sid) if sid is not None else None
        score = match.get('score')
        try:
            values['lyrics_match_score'] = float(score) if score is not None else None
        except (TypeError, ValueError):
            values['lyrics_match_score'] = None
        extra = {k: v for k, v in match.items() if k not in _MATCH_COLUMN_KEYS}
        values['lyrics_match_json'] = _json(extra) if extra else None

    tier = instrumental_title_tier(row.title)
    override = row.instrumental_override
    # Rule M: a manual save is the user saying "this track has lyrics".
    if source == 'manual' and (
            row.lyrics_status == 'instrumental' or row.instrumental_evidence or row.instrumental_prev
            or override == 'always' or tier in ('strong', 'weak')):
        values['instrumental_override'] = 'never'
        values['instrumental_prev'] = None
        override = 'never'
        details.update(override_set='never', why='manual-save')
    # Rule T: a STRONG title that now has lyrics is a conflict to review.
    values['instrumental_reason'] = None
    if tier == 'strong' and override != 'never' and source != 'manual':
        values['instrumental_evidence'] = _json(_rule_t_evidence('saved_lyrics', provider))
    else:
        values['instrumental_evidence'] = None

    stmt = update(TableTracks).where(TableTracks.lidarrTrackId == row.lidarrTrackId)
    if expect_status is not None:
        stmt = stmt.where(_in_expected(TableTracks.lyrics_status, tuple(expect_status)))
    if expect_sources is not None:
        stmt = stmt.where(_in_expected(TableTracks.lyrics_source, tuple(expect_sources)))
    rowcount = database.execute(stmt.values(**values)).rowcount
    info = {'filepath': filepath, 'is_synced': synced_flag, 'detected_language': detected,
            'row_changed': False}
    if not rowcount:
        logger.warning(
            f"Track {row.lidarrTrackId} changed while its lyrics were written; "
            f"file saved, row not updated"
        )
        info['row_changed'] = True
        return PersistResult(True, None, None, info)

    if not record_history:
        return PersistResult(True, None, None, info)
    action = history_action if history_action is not None else history_action_for_source(source)
    if source == 'translated':
        description = f"Saved translated lyrics for {row.title}"
    elif source == 'generated':
        description = f"Saved generated lyrics for {row.title}"
    else:
        description = _HISTORY_DESCRIPTIONS.get(action, 'Saved lyrics for {title}').format(title=row.title)
    database.execute(
        sqlite_insert(TableHistory).values(
            action=action,
            description=description,
            metadata_type='lyrics',
            provider=provider,
            lidarrTrackId=row.lidarrTrackId,
            lidarrArtistId=row.artistId,
            lidarrAlbumId=row.albumId,
            timestamp=now,
            metadata_path=filepath,
            details=_json(details) if details else None,
        )
    )
    return PersistResult(True, None, None, info)


def persist_lyrics(track, content, provider, **kw):
    """persist_lyrics_result() returning its info dict, or None on failure."""
    r = persist_lyrics_result(track, content, provider, **kw)
    return r.info if r else None


# ---------------------------------------------------------------------------
# Adopting an existing sidecar
# ---------------------------------------------------------------------------

def rediscovered_source(row, content):
    """lyrics_source for a file found on disk for a row that had none.

    User sources (manual/translated/generated) are kept; 'auto' is kept only
    when the file is exactly what lyrarr last wrote; anything else is NULL
    (unknown origin — automatic passes only touch it under overwrite_existing).
    """
    src = getattr(row, 'lyrics_source', None)
    if src in _USER_SOURCES:
        return src
    if src == 'auto' and row.lyrics_content_hash and row.lyrics_content_hash == content_hash(content):
        return 'auto'
    return None


def _adopt(row, content, *, source, actor='adopt', judged=True):
    """Mark `row` available from `content` already on disk (CAS on its status).

    judged=False: the file is present but its content couldn't be judged
    (SIDECAR_UNKNOWN) — the language isn't detected from replacement
    characters. Provenance is kept only when the content hash proves the
    file is the one it describes (stale_provenance_values).
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableHistory, TableTracks, database, update

    # ORM UPDATEs synchronise the in-session row, so read old values first.
    old_status, old_reason = row.lyrics_status, row.instrumental_reason
    now = _now()
    values = dict(
        lyrics_status='available',
        hasLyrics=True,
        is_synced=is_synced_lyrics(content),
        lyrics_source=source,
        lyrics_replace_requested=None,
        lyrics_retry_count=0,
        lyrics_retry_after=None,
        lyrics_transient_count=0,
        lyrics_cooldown_count=0,
        lyrics_retry_set_at=now,
        instrumental_reason=None,
        updated_at_timestamp=now,
    )
    values.update(stale_provenance_values(row, content))
    if row.language_source not in _USER_LANGUAGE_SOURCES:
        values['detected_language'] = detect_language(content) if judged else None
        values['language_source'] = 'detected'
    tier = instrumental_title_tier(row.title)
    if tier == 'strong' and row.instrumental_override != 'never' and source != 'manual':
        values['instrumental_evidence'] = (
            row.instrumental_evidence if _has_veto_entry(row.instrumental_evidence)
            else _json(_rule_t_evidence('sidecar'))
        )
    else:
        values['instrumental_evidence'] = None

    rowcount = database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId,
               _in_expected(TableTracks.lyrics_status, (old_status,)))
        .values(**values)
    ).rowcount
    if not rowcount:
        return False
    if old_status == 'instrumental':
        database.execute(sqlite_insert(TableHistory).values(
            action=6,
            description=f"Instrumental flag cleared (sidecar-lyrics): {row.title}",
            metadata_type='lyrics',
            lidarrTrackId=row.lidarrTrackId,
            lidarrArtistId=row.artistId,
            lidarrAlbumId=row.albumId,
            timestamp=now,
            metadata_path=sidecar_path(row),
            details=_json({'why': 'sidecar-lyrics', 'reason': old_reason, 'actor': actor}),
        ))
    logger.info(f"Lyrics file for '{row.title}' found on disk — marked available")
    return True


def adopt_sidecar(track, content=None, *, keep_source=True):
    """Adopt an existing `<stem>.lrc` that holds real lyrics (PLAN §1.3).

    content None → read from disk. Only 'lyrics' content is adopted (markers,
    placeholders, credits and empty files are not). A file read from disk
    whose content can't be judged (SIDECAR_UNKNOWN) is adopted as present
    without language detection, except onto an instrumental row. Never
    touches blacklisted rows or instrumental rows the user marked 'always'.
    """
    row = _fresh_row(track)
    if row is None:
        return False
    if row.lyrics_status == 'blacklisted':
        return False
    if row.lyrics_status == 'instrumental' and row.instrumental_override == 'always':
        return False
    source = row.lyrics_source if keep_source else None
    if content is None:
        kind, sr = read_sidecar_kind(sidecar_path(row))
        if kind == SIDECAR_UNKNOWN:
            # Present but not judgeable (not UTF-8 / unreadable): adopted as
            # present, never used to lift an instrumental flag.
            if row.lyrics_status == 'instrumental':
                return False
            return _adopt(row, sr.text if sr is not None else '', source=source, judged=False)
        if sr is None:
            return False
        content = sr.text
    if classify_lyrics_text(content) != 'lyrics':
        return False
    return _adopt(row, content, source=source)


# ---------------------------------------------------------------------------
# Removing a file (archived first)
# ---------------------------------------------------------------------------

def archive_and_remove(track, provider_label, *, blacklist_reason=None):
    """Archive the track's lyrics file into TableLyricsVersions, then delete it.

    Returns False (file untouched) when archiving fails or the file is not
    valid UTF-8. With `blacklist_reason` ("this content is wrong" repairs) the
    old content is also blacklisted for the track (provider 'audit:<reason>'),
    so neither a provider nor embedded tags can bring it back. The row's
    status is not changed: callers do that.
    """
    row = _fresh_row(track) or track
    path = sidecar_path(row)
    if not path or not os.path.isfile(path):
        return True
    sr = read_sidecar(path)
    if sr is None or not sr.lossless:
        logger.error(f"Not removing {path}: it could not be read as UTF-8, so it can't be archived safely")
        return False
    if not _archive_existing(row.lidarrTrackId, path, origin_provider=_origin_provider(row),
                             replaced_by=provider_label):
        return False
    if blacklist_reason:
        blacklist_content(row.lidarrTrackId, sr.text, reason=blacklist_reason)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.error(f"Could not remove {path}: {e}")
        return False
    return True


# ---------------------------------------------------------------------------
# Instrumental state (PLAN §1.3, §2)
# ---------------------------------------------------------------------------

_UNSET = object()

_REASON_TEXT = {
    'title': 'the title says so',
    'consensus': 'several sources agree there are no lyrics',
    'user': 'marked by you',
}


def _classify_history(row, description, details, now):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableHistory, database
    database.execute(sqlite_insert(TableHistory).values(
        action=6,
        description=description,
        metadata_type='lyrics',
        lidarrTrackId=row.lidarrTrackId,
        lidarrArtistId=row.artistId,
        lidarrAlbumId=row.albumId,
        timestamp=now,
        details=_json(details),
    ))


def _emit_instrumental(row, action, reason, actor, message):
    from lyrarr.app.event_handler import event_stream
    try:
        event_stream(type='instrumental_classified', payload={
            'track_id': row.lidarrTrackId, 'album_id': row.albumId, 'title': row.title,
            'action': action, 'reason': reason, 'actor': actor, 'message': message,
        })
    except Exception as e:
        logger.debug(f"instrumental event failed: {e}")


def set_instrumental(track_id, reason, evidence, *, actor='worker', expect_status=('missing',), emit_event=True):
    """Classify a track instrumental (compare-and-set on expect_status).

    Sets status 'instrumental' with reason/evidence/checked_at, clears lyrics
    flags, provenance, retry state and instrumental_prev; reason 'user' also
    sets override 'always'. Writes a history row (action 6). Does NOT touch
    the sidecar: callers that need it gone call archive_and_remove first.
    """
    from lyrarr.app.database import TableTracks, database, update

    row = _fresh_row(track_id)
    if row is None or not _matches_expected(row.lyrics_status, expect_status):
        return False
    previous_status = row.lyrics_status
    now = _now()
    values = dict(
        lyrics_status='instrumental',
        hasLyrics=False,
        is_synced=False,
        detected_language=None,
        lyrics_source=None,
        lyrics_provider=None,
        lyrics_source_id=None,
        lyrics_match_score=None,
        lyrics_match_json=None,
        lyrics_content_hash=None,
        lyrics_retry_count=0,
        lyrics_retry_after=None,
        lyrics_transient_count=0,
        lyrics_cooldown_count=0,
        lyrics_retry_set_at=now,
        lyrics_replace_requested=None,
        instrumental_reason=reason,
        instrumental_evidence=_json(evidence) if evidence is not None else None,
        instrumental_checked_at=now,
        instrumental_prev=None,
        updated_at_timestamp=now,
    )
    if row.language_source == 'manual':
        values.pop('detected_language')
    if reason == 'user':
        values['instrumental_override'] = 'always'
    rowcount = database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId,
               _in_expected(TableTracks.lyrics_status, tuple(expect_status)))
        .values(**values)
    ).rowcount
    if not rowcount:
        return False
    _classify_history(row, f"Classified instrumental ({reason}): {row.title}",
                      {'reason': reason, 'evidence': evidence, 'actor': actor,
                       'previous_status': previous_status}, now)
    if emit_event:
        _emit_instrumental(row, 'flagged', reason, actor,
                           f"'{row.title}' marked instrumental ({_REASON_TEXT.get(reason, reason)})")
    return True


def clear_instrumental(track_id, *, why, actor, override=_UNSET, retry_after=None,
                       expect_status=('instrumental', 'missing'), emit_event=True):
    """Remove an instrumental flag (or a 'likely instrumental' hint).

    An instrumental row becomes 'missing' (searched again from `retry_after`,
    None = immediately); reason/evidence are cleared. A clear by a non-user
    actor of a row that WAS instrumental records instrumental_prev, so the
    row shows up in Wanted ▸ "Cleared after upgrade"; a user clear sets it
    NULL. `override` is only written when passed (None included).
    """
    from lyrarr.app.database import TableTracks, database, update

    row = _fresh_row(track_id)
    if row is None or not _matches_expected(row.lyrics_status, expect_status):
        return False
    previous_status, old_reason = row.lyrics_status, row.instrumental_reason
    was_instrumental = previous_status == 'instrumental'
    now = _now()
    old_evidence = _parse_json(row.instrumental_evidence)
    evidence_count = len(old_evidence) if isinstance(old_evidence, list) else 0
    values = dict(
        instrumental_reason=None,
        instrumental_evidence=None,
        instrumental_checked_at=now,
        lyrics_retry_count=0,
        lyrics_retry_after=retry_after,
        lyrics_transient_count=0,
        lyrics_cooldown_count=0,
        lyrics_retry_set_at=now,
        lyrics_replace_requested=None,
        updated_at_timestamp=now,
    )
    if was_instrumental:
        values.update(lyrics_status='missing', hasLyrics=False, is_synced=False, lyrics_source=None)
        values.update(cleared_provenance())
    if override is not _UNSET:
        values['instrumental_override'] = override
    if actor == 'user':
        values['instrumental_prev'] = None
    elif actor in _NON_USER_ACTORS and was_instrumental:
        values['instrumental_prev'] = _json({
            'why': why, 'reason': old_reason, 'actor': actor,
            'at': now.isoformat(), 'evidence_count': evidence_count,
        })
    rowcount = database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId,
               _in_expected(TableTracks.lyrics_status, (previous_status,)))
        .values(**values)
    ).rowcount
    if not rowcount:
        return False
    what = 'Instrumental flag cleared' if was_instrumental else 'Instrumental hint cleared'
    details = {'why': why, 'actor': actor, 'reason': old_reason,
               'evidence_count': evidence_count, 'previous_status': previous_status}
    if override is not _UNSET:
        details['override'] = override
    if retry_after is not None:
        details['retry_after'] = retry_after.isoformat() if hasattr(retry_after, 'isoformat') else retry_after
    _classify_history(row, f"{what} ({why}): {row.title}", details, now)
    if emit_event:
        _emit_instrumental(row, 'cleared', old_reason, actor,
                           f"'{row.title}' is no longer marked instrumental ({why})")
    return True


def dismiss_cleared(track_ids):
    """Drop rows from the "Cleared after upgrade" view (instrumental_prev → NULL)."""
    from lyrarr.app.database import TableTracks, database, select, update

    ids = [int(i) for i in (track_ids or [])]
    if not ids:
        return 0
    rows = database.execute(
        select(TableTracks).where(TableTracks.lidarrTrackId.in_(ids), TableTracks.instrumental_prev.is_not(None))
    ).scalars().all()
    now = _now()
    count = 0
    for row in rows:
        prev = _parse_json(row.instrumental_prev)
        n = database.execute(
            update(TableTracks)
            .where(TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.instrumental_prev.is_not(None))
            .values(instrumental_prev=None, updated_at_timestamp=now)
        ).rowcount
        if n:
            count += 1
            _classify_history(row, f"Cleared-instrumental notice dismissed: {row.title}",
                              {'why': 'dismissed', 'actor': 'user', 'prev': prev},
                              now)
    return count


def record_instrumental_hint(track_id, votes, *, emit_event=True):
    """Store "likely instrumental" evidence on a 'missing' row (no status change)."""
    from lyrarr.app.database import TableTracks, database, update

    row = _fresh_row(track_id)
    if row is None or row.lyrics_status != 'missing':
        return False
    now = _now()
    rowcount = database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.lyrics_status == 'missing')
        .values(instrumental_evidence=_json(list(votes or [])), instrumental_checked_at=now,
                updated_at_timestamp=now)
    ).rowcount
    if not rowcount:
        return False
    n = len(votes or [])
    _classify_history(row, f"Likely instrumental ({n} vote{'s' if n != 1 else ''}): {row.title}",
                      {'votes': list(votes or []), 'actor': 'worker'}, now)
    if emit_event:
        _emit_instrumental(row, 'hint', None, 'worker',
                           f"'{row.title}' may be instrumental ({n} source{'s' if n != 1 else ''})")
    return True


# ---------------------------------------------------------------------------
# Replace mode (PLAN §2.8)
# ---------------------------------------------------------------------------

def _audit_history(row, description, details, now=None):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableHistory, database
    database.execute(sqlite_insert(TableHistory).values(
        action=7,
        description=description,
        metadata_type='lyrics',
        lidarrTrackId=row.lidarrTrackId,
        lidarrArtistId=row.artistId,
        lidarrAlbumId=row.albumId,
        timestamp=now or _now(),
        details=_json(details),
    ))


def request_replace(track_id, reason, *, actor='audit', retry_after=None):
    """Ask the worker to re-search an 'available' NULL/auto row and replace its
    file only if something better is found. retry_after None = next pass."""
    from lyrarr.app.database import TableTracks, database, update

    row = _fresh_row(track_id)
    if row is None or row.lyrics_status != 'available' or row.lyrics_source not in (None, 'auto'):
        return False
    now = _now()
    rowcount = database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId,
               TableTracks.lyrics_status == 'available',
               _in_expected(TableTracks.lyrics_source, (None, 'auto')))
        .values(lyrics_replace_requested=now, lyrics_retry_count=0, lyrics_retry_after=retry_after,
                lyrics_transient_count=0, lyrics_cooldown_count=0, lyrics_retry_set_at=now,
                updated_at_timestamp=now)
    ).rowcount
    if not rowcount:
        return False
    _audit_history(row, f"Replace requested ({reason}): {row.title}",
                   {'reason': reason, 'actor': actor,
                    'retry_after': retry_after.isoformat() if hasattr(retry_after, 'isoformat') else retry_after},
                   now)
    return True


def clear_replace_request(track_id, *, result, details=None):
    """Clear lyrics_replace_requested; result 'kept' | 'replaced' | 'cancelled'."""
    from lyrarr.app.database import TableTracks, database, update

    row = _fresh_row(track_id)
    if row is None:
        return
    now = _now()
    database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId)
        .values(lyrics_replace_requested=None, updated_at_timestamp=now)
    )
    text = {'kept': 'nothing better found, kept the current lyrics',
            'replaced': 'lyrics replaced', 'cancelled': 'cancelled'}.get(result, result)
    _audit_history(row, f"Re-search: {text}: {row.title}", {'replace_result': result, **(details or {})}, now)


# ---------------------------------------------------------------------------
# Content-hash backfill (PLAN §3.4)
# ---------------------------------------------------------------------------

def _history_proves_file(row, path, sr):
    """Whether lyrarr's own newest download/upgrade history row for `path` is
    not older than the file (mtime - 60 s): the file is still what lyrarr wrote."""
    from lyrarr.app.database import TableHistory, database, select
    if sr is None or not sr.lossless or not path:
        return False
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return False
    return database.execute(
        select(TableHistory.id)
        .where(
            TableHistory.lidarrTrackId == row.lidarrTrackId,
            TableHistory.metadata_type == 'lyrics',
            TableHistory.action.in_((1, 2)),
            TableHistory.metadata_path == path,
            TableHistory.timestamp >= mtime - timedelta(seconds=60),
        )
        .limit(1)
    ).scalar() is not None


def file_provenance_proven(row, path, sr):
    """Whether the sidecar's CURRENT text (`sr`) is provably what lyrarr wrote.

    True when the stored lyrics_content_hash matches the text, or — for a
    hash-less legacy row — when lyrarr's download history is not older than
    the file. A rewrite (audit repair, LRC repair) may only carry the content
    hash forward when this holds; otherwise re-stamping the hash would turn a
    user's edit into "lyrarr wrote this" and let the upgrade pass replace it.
    """
    if row is None or sr is None or not sr.text:
        return False
    stored = getattr(row, 'lyrics_content_hash', None)
    if stored:
        return stored in candidate_hashes(sr.text)
    return _history_proves_file(row, path, sr)


def backfill_content_hash(track):
    """Record lyrics_content_hash for an 'auto' row written before 2.0.

    'set' when lyrarr's own newest download/upgrade history row for this file
    is not older than the file (mtime - 60 s); 'downgraded' (lyrics_source →
    NULL, unknown origin) when the file changed afterwards or the history is
    gone; 'unchanged' when the row doesn't qualify.
    """
    from lyrarr.app.database import TableTracks, database, update

    row = _fresh_row(track)
    if row is None or row.lyrics_source != 'auto' or row.lyrics_content_hash is not None:
        return 'unchanged'
    path = sidecar_path(row)
    if not path or not os.path.isfile(path):
        return 'unchanged'
    try:
        os.path.getmtime(path)
    except OSError:
        return 'unchanged'
    sr = read_sidecar(path)
    found = _history_proves_file(row, path, sr)
    guard = (TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.lyrics_source == 'auto',
             TableTracks.lyrics_content_hash.is_(None))
    if found:
        database.execute(update(TableTracks).where(*guard).values(lyrics_content_hash=content_hash(sr.text)))
        return 'set'
    database.execute(update(TableTracks).where(*guard).values(lyrics_source=None))
    logger.info(f"'{row.title}': lyrics file changed since lyrarr wrote it — origin now unknown")
    return 'downgraded'


# ---------------------------------------------------------------------------
# Tombstones (PLAN §3.4 artist-cascade mitigation)
# ---------------------------------------------------------------------------

def _path_stem(path):
    return os.path.splitext(path)[0] if path else None


def tombstone_tracks(rows):
    """Keep the user state of track rows that sync is about to delete.

    Only rows carrying state worth restoring get a tombstone (override,
    source, manual/translated language, instrumental_prev, blacklist hashes,
    blacklisted status). Restored by restore_from_tombstone() for 30 days.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableBlacklist, TableTrackTombstones, database, select

    now = _now()
    written = 0
    for row in rows or []:
        try:
            hashes = sorted({h for h in database.execute(
                select(TableBlacklist.content_hash).where(
                    TableBlacklist.lidarrTrackId == row.lidarrTrackId,
                    TableBlacklist.metadata_type == 'lyrics',
                    TableBlacklist.content_hash.is_not(None),
                )
            ).scalars().all() if h})
            interesting = (
                row.instrumental_override or row.lyrics_source or row.instrumental_prev or hashes
                or row.language_source in _USER_LANGUAGE_SOURCES or row.lyrics_status == 'blacklisted'
            )
            if not interesting:
                continue
            sidecar_hash = None
            sr = read_sidecar(sidecar_path(row)) if row.path else None
            if sr is not None and sr.lossless:
                sidecar_hash = content_hash(sr.text)
            state = {
                'instrumental_override': row.instrumental_override,
                'lyrics_source': row.lyrics_source,
                'lyrics_content_hash': row.lyrics_content_hash,
                'sidecar_hash': sidecar_hash,
                'language_source': row.language_source,
                'detected_language': row.detected_language,
                'instrumental_prev': _parse_json(row.instrumental_prev),
                'blacklist_hashes': hashes,
                'lyrics_status_was': row.lyrics_status,
                'title': row.title,
            }
            database.execute(sqlite_insert(TableTrackTombstones).values(
                lidarrAlbumId=row.albumId,
                mbId=row.mbId or None,
                path_stem=_path_stem(row.path),
                lidarrTrackId=row.lidarrTrackId,
                state=_json(state),
                removed_at=now,
            ))
            written += 1
        except Exception as e:
            logger.warning(f"Could not keep the state of removed track {row.lidarrTrackId}: {e}")
    if written:
        logger.info(f"Kept the lyrarr state of {written} removed track(s) for {TOMBSTONE_TTL_DAYS} days")
    return written


def restore_from_tombstone(new_row):
    """Re-apply a removed row's user state to a newly inserted row.

    Matches (album id, recording id) or the path stem, newest tombstone first,
    within TOMBSTONE_TTL_DAYS. Restores override, language (manual/translated
    only), instrumental_prev, blacklist hashes, a blacklisted status, and the
    lyrics source when the new row's sidecar is the same content. Status
    'instrumental' is never restored; an 'always' override is re-applied via
    set_instrumental(reason='user'). The tombstone is deleted afterwards.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import (
        TableBlacklist,
        TableTracks,
        TableTrackTombstones,
        and_,
        database,
        delete,
        or_,
        select,
        update,
    )

    row = _fresh_row(new_row)
    if row is None:
        return False
    conds = []
    if row.mbId:
        conds.append(and_(TableTrackTombstones.lidarrAlbumId == row.albumId, TableTrackTombstones.mbId == row.mbId))
    stem = _path_stem(row.path)
    if stem:
        conds.append(TableTrackTombstones.path_stem == stem)
    if not conds:
        return False
    cutoff = _now() - timedelta(days=TOMBSTONE_TTL_DAYS)
    tomb = database.execute(
        select(TableTrackTombstones)
        .where(or_(*conds), TableTrackTombstones.removed_at >= cutoff)
        .order_by(TableTrackTombstones.removed_at.desc(), TableTrackTombstones.id.desc())
        .limit(1)
    ).scalars().first()
    if tomb is None:
        return False
    state = _parse_json(tomb.state) or {}
    now = _now()
    values = {}
    override = state.get('instrumental_override')
    if override and not row.instrumental_override:
        values['instrumental_override'] = override
    if state.get('language_source') in _USER_LANGUAGE_SOURCES:
        values['language_source'] = state['language_source']
        values['detected_language'] = state.get('detected_language')
    if state.get('instrumental_prev') and not row.instrumental_prev:
        values['instrumental_prev'] = _json(state['instrumental_prev'])
    if state.get('lyrics_source') and row.lyrics_source is None and row.lyrics_status == 'available':
        sr = read_sidecar(sidecar_path(row))
        if sr is not None and state.get('sidecar_hash') and content_hash(sr.text) == state['sidecar_hash']:
            values['lyrics_source'] = state['lyrics_source']
            if state.get('lyrics_source') == 'auto' and state.get('lyrics_content_hash'):
                values['lyrics_content_hash'] = state['lyrics_content_hash']
    if state.get('lyrics_status_was') == 'blacklisted' and override != 'always':
        values.update(lyrics_status='blacklisted', hasLyrics=False)
    if values:
        values['updated_at_timestamp'] = now
        database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == row.lidarrTrackId).values(**values))

    existing = set(database.execute(
        select(TableBlacklist.content_hash).where(
            TableBlacklist.lidarrTrackId == row.lidarrTrackId, TableBlacklist.metadata_type == 'lyrics')
    ).scalars().all())
    for h in state.get('blacklist_hashes') or []:
        if h and h not in existing:
            database.execute(sqlite_insert(TableBlacklist).values(
                metadata_type='lyrics', provider='tombstone', content_hash=h,
                lidarrTrackId=row.lidarrTrackId, lidarrAlbumId=row.albumId, timestamp=now,
            ))

    if override == 'always':
        current = _fresh_row(row.lidarrTrackId)
        if current is not None and current.lyrics_status in ('missing', 'available'):
            set_instrumental(row.lidarrTrackId, 'user', [{'source': 'user', 'kind': 'override', 'restored': True}],
                             actor='sync', expect_status=(current.lyrics_status,), emit_event=False)

    database.execute(delete(TableTrackTombstones).where(TableTrackTombstones.id == tomb.id))
    logger.info(f"Restored the lyrarr state of re-imported track '{row.title}'")
    return True


def purge_tombstones(max_age_days=TOMBSTONE_TTL_DAYS):
    """Delete tombstones older than max_age_days. Returns the count."""
    from lyrarr.app.database import TableTrackTombstones, database, delete
    cutoff = _now() - timedelta(days=max_age_days)
    return database.execute(
        delete(TableTrackTombstones).where(TableTrackTombstones.removed_at < cutoff)
    ).rowcount or 0


def tombstones_exist():
    from lyrarr.app.database import TableTrackTombstones, database, select
    try:
        return database.execute(select(TableTrackTombstones.id).limit(1)).first() is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Disk ↔ DB reconciliation
# ---------------------------------------------------------------------------

def reconcile_track_lyrics(track):
    """Sync one track row's lyrics state with what is actually on disk.

    Catches .lrc files deleted (or dropped in) outside lyrarr between Lidarr
    syncs. Cheap when nothing changed (a stat + small read), so it's safe to
    run on-demand from read paths. Never acts when the audio file is missing
    (mount down / file moved). Rules:

    * available + no real lyrics on disk → missing (source, provenance and
      replace request cleared, retry reset);
    * missing + real lyrics on disk → available (adopted; provenance kept only
      when the content hash proves it describes this file);
    * instrumental + real lyrics + non-STRONG title + override not 'always' →
      available (adopted); STRONG-title rows stay for review;
    * a file whose content can't be judged (not UTF-8, unreadable:
      SIDECAR_UNKNOWN) counts as present: an available row keeps its status,
      source and flags, a missing row adopts it, an instrumental row stays;
    * available STRONG-title rows get the title-conflict evidence entry;
    * blacklisted rows are left alone.

    Every UPDATE is conditioned on the status read here. Returns True if the
    row was updated.
    """
    if not track or not getattr(track, 'path', None):
        return False
    status = track.lyrics_status
    if status not in ('available', 'missing', 'instrumental'):
        return False
    if not os.path.isfile(track.path):
        return False

    from lyrarr.app.database import TableTracks, database, update

    path = sidecar_path(track)
    kind, sr = read_sidecar_kind(path)
    unknown = kind == SIDECAR_UNKNOWN
    has_lyrics = sidecar_present(kind)
    tier = instrumental_title_tier(track.title)

    if status == 'available':
        if not has_lyrics:
            now = _now()
            values = dict(
                lyrics_status='missing',
                hasLyrics=False,
                is_synced=False,
                lyrics_source=None,
                lyrics_replace_requested=None,
                lyrics_retry_count=0,
                lyrics_retry_after=None,
                lyrics_transient_count=0,
                lyrics_cooldown_count=0,
                lyrics_retry_set_at=now,
                updated_at_timestamp=now,
            )
            values.update(cleared_provenance())
            if track.language_source not in _USER_LANGUAGE_SOURCES:
                values['detected_language'] = None
            if _has_veto_entry(track.instrumental_evidence, 'saved_lyrics') or \
                    _has_veto_entry(track.instrumental_evidence, 'sidecar'):
                values['instrumental_evidence'] = None
            n = database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track.lidarrTrackId, TableTracks.lyrics_status == 'available')
                .values(**values)
            ).rowcount
            if n:
                logger.info(
                    f"Lyrics file for '{track.title}' "
                    f"{'was deleted from disk' if kind is None else f'holds no lyrics ({kind})'} — marked missing"
                )
            return bool(n)
        if tier == 'strong' and track.instrumental_override != 'never' and track.lyrics_source != 'manual' \
                and not _has_veto_entry(track.instrumental_evidence):
            n = database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track.lidarrTrackId, TableTracks.lyrics_status == 'available')
                .values(instrumental_evidence=_json(_rule_t_evidence('sidecar')))
            ).rowcount
            return bool(n)
        return False

    if not has_lyrics:
        return False
    content = sr.text if sr is not None else ''
    if status == 'missing':
        return _adopt(track, content, source=rediscovered_source(track, content), actor='reconcile',
                      judged=not unknown)
    # instrumental: only real, readable lyrics lift the flag; a file whose
    # content can't be judged leaves it alone (audit F-3 reports the file).
    if not unknown and tier != 'strong' and track.instrumental_override != 'always':
        return _adopt(track, content, source=rediscovered_source(track, content), actor='reconcile')
    return False


# ---------------------------------------------------------------------------
# Per-result blacklisting
# ---------------------------------------------------------------------------

def get_blacklisted_hashes(track_id):
    """Return the set of blacklisted lyrics content hashes for a track."""
    from lyrarr.app.database import TableBlacklist, database, select
    rows = database.execute(
        select(TableBlacklist).where(
            TableBlacklist.lidarrTrackId == track_id,
            TableBlacklist.metadata_type == 'lyrics',
        )
    ).scalars().all()
    return {r.content_hash for r in rows if r.content_hash}


def pick_best_synced(results, min_score=0.0):
    """Return the best synced-lyrics result at/above min_score, or None.

    Used by the upgrade pass to find a synced replacement for plain lyrics.
    Pure (no DB), so it lives here with the other lyrics-selection helpers.
    """
    synced = [
        r for r in results
        if r.get('synced_lyrics') and (r.get('score', 0) or 0) >= min_score
    ]
    if not synced:
        return None
    return max(synced, key=lambda r: r.get('score', 0) or 0)


def candidate_hashes(text):
    """Every hash a blacklist entry for `text` may have been stored under:
    the current content_hash, the pre-2.0 merge hash, and the hash of the
    repaired LRC when the text needs repair."""
    out = set()
    if not text or not text.strip():
        return out
    h = content_hash(text)
    if h:
        out.add(h)
    try:
        from lyrarr.metadata import merge
        legacy = getattr(merge, 'legacy_content_hash', None)
        if legacy is not None:
            lh = legacy(text)
            if lh:
                out.add(lh)
    except Exception as e:
        logger.debug(f"legacy content hash failed: {e}")
    try:
        from lyrarr.metadata.lrc_repair import repair_lrc, validate_lrc
        if validate_lrc(text).get('needs_repair'):
            rh = content_hash(repair_lrc(text))
            if rh:
                out.add(rh)
    except Exception as e:
        logger.debug(f"repaired content hash failed: {e}")
    return out


def result_is_blacklisted(result, blacklisted_hashes):
    """Whether a provider result's content matches any blacklisted hash.

    Checks both synced and plain content (so a result is rejected regardless of
    which form was blacklisted), plus the legacy and repaired-LRC hashes.
    """
    if not blacklisted_hashes:
        return False
    for key in ('synced_lyrics', 'plain_lyrics'):
        if candidate_hashes(result.get(key)) & set(blacklisted_hashes):
            return True
    return False


def text_is_blacklisted(track_id, text):
    """Whether `text` (e.g. embedded tag lyrics) is blacklisted for the track."""
    hashes = get_blacklisted_hashes(track_id)
    return bool(hashes) and bool(candidate_hashes(text) & hashes)


def hashes_for_blacklist(track_id, current_content):
    """Content hashes to blacklist when the user rejects `current_content`.

    Always the content's own hash. When the content is the track's current
    auto-translation, also the hash of the provider lyrics it was translated
    from (the newest archived version marked translated_from) — the
    downloader compares candidates against *provider* content, which never
    matches the translated text, so without this the same wrong match would
    be fetched and re-translated on the next run.
    """
    hashes = []
    h = content_hash(current_content)
    if not h:
        return hashes
    hashes.append(h)

    from lyrarr.app.database import TableLyricsVersions, TableTracks, database, select

    track = database.execute(
        select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
    ).scalars().first()
    if not track or getattr(track, 'lyrics_source', None) != 'translated' or not track.path:
        return hashes

    # Only when the rejected content *is* the translation on disk, not some
    # other candidate the user picked out of a search list.
    sr = read_sidecar(os.path.splitext(track.path)[0] + '.lrc')
    if sr is None or content_hash(sr.text) != h:
        return hashes

    source = database.execute(
        select(TableLyricsVersions)
        .where(
            TableLyricsVersions.lidarrTrackId == track_id,
            TableLyricsVersions.translated_from.is_not(None),
        )
        .order_by(TableLyricsVersions.timestamp.desc(), TableLyricsVersions.id.desc())
    ).scalars().first()
    source_hash = content_hash(source.content) if source else None
    if source_hash and source_hash not in hashes:
        hashes.append(source_hash)
    return hashes


def blacklist_content(track_id, content, provider=None, *, reason=None):
    """Record a lyrics content blob as blacklisted for a track (idempotent).

    Blacklists exactly this content. When the user rejects the track's current
    file, also blacklist hashes_for_blacklist(track_id, content) so an
    auto-translation's provider source is caught too (the API does this).
    `reason` (audit repairs) stores provider 'audit:<reason>'.

    Returns True if the content was hashable and is now blacklisted.
    """
    h = content_hash(content)
    if not h:
        return False
    if reason:
        provider = f'audit:{reason}'

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableBlacklist, TableTracks, database, select

    existing = database.execute(
        select(TableBlacklist).where(
            TableBlacklist.lidarrTrackId == track_id,
            TableBlacklist.metadata_type == 'lyrics',
            TableBlacklist.content_hash == h,
        )
    ).scalars().first()
    if existing:
        return True

    track = database.execute(
        select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
    ).scalars().first()

    database.execute(
        sqlite_insert(TableBlacklist).values(
            metadata_type='lyrics',
            provider=provider,
            content_hash=h,
            lidarrTrackId=track_id,
            lidarrAlbumId=track.albumId if track else None,
            timestamp=_now(),
        )
    )
    logger.info(f"Blacklisted a lyrics result for track {track_id} (hash {h})")
    return True


def clear_blacklist(track_id):
    """Remove all blacklisted lyrics entries for a track. Returns count removed."""
    from lyrarr.app.database import TableBlacklist, database, delete, select
    rows = database.execute(
        select(TableBlacklist).where(
            TableBlacklist.lidarrTrackId == track_id,
            TableBlacklist.metadata_type == 'lyrics',
        )
    ).scalars().all()
    count = len(rows)
    if count:
        database.execute(
            delete(TableBlacklist).where(
                TableBlacklist.lidarrTrackId == track_id,
                TableBlacklist.metadata_type == 'lyrics',
            )
        )
    return count


def stagger_until(n, *, max_hours=72, per_hundred_hours=6, now=None):
    """Random time in (now, now + min(max_hours, ceil(n/100) × per_hundred_hours)].

    The requeue stagger shared by bulk replace requests and legacy requeues,
    so a large repair doesn't hit the providers in a single worker pass.
    """
    now = now or _now()
    hours = min(max_hours, -(-max(int(n), 1) // 100) * per_hundred_hours)
    return now + timedelta(seconds=random.uniform(1, hours * 3600))
