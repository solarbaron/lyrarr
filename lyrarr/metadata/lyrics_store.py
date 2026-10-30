"""Shared lyrics persistence and per-result blacklisting.

Single source of truth for writing a chosen lyrics file to disk and recording it
in the database, used by both the scheduled download worker and the manual
save/upload/editor API so the two behave identically.

Also implements per-result blacklisting: a rejected (wrong) match is hashed and
stored so the automatic downloader never re-selects that same content on later
runs, no matter which provider returns it.
"""

import logging
import os
from datetime import datetime

# NOTE: lyrarr.app.database is imported lazily inside the functions that need it.
# Importing it at module load pulls in app config + argument parsing (side
# effects on import), which would make the pure helpers here (content_hash,
# result_is_blacklisted) un-importable in unit tests. language_detect and merge
# are side-effect-free, so they're safe to import at top.
from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
from lyrarr.metadata.merge import _content_hash

logger = logging.getLogger(__name__)

# Most recent archived lyrics versions kept per track.
_MAX_VERSIONS_PER_TRACK = 5


def content_hash(text):
    """Stable hash of lyrics content (timestamps/metadata stripped, lowercased).

    Returns None for empty/whitespace input. Shared with merge so a synced and a
    plain copy of the same lyrics hash identically.
    """
    return _content_hash(text or '')


# ---------------------------------------------------------------------------
# Persistence (shared write path)
# ---------------------------------------------------------------------------

def _archive_existing(track_id, filepath, provider, translated_from=None):
    """Archive the current on-disk lyrics into TableLyricsVersions before replacing.

    Always archiving before overwrite (rather than only on an explicit overwrite
    flag) is what removes the old drift between the manual and scheduled paths.

    translated_from: set when the file being archived is the source of a
    translation that is about to replace it (its language code) — that's how
    a blacklisted translation finds the provider content it came from.

    Returns True if there was nothing to archive or it was archived, False if
    archiving failed — callers must not delete/overwrite the file then.
    """
    if not os.path.isfile(filepath):
        return True
    try:
        with open(filepath, encoding='utf-8', errors='ignore') as f:
            old = f.read()
    except Exception as e:
        logger.error(f"Failed to read existing lyrics for archive (track {track_id}): {e}")
        return False
    if not old.strip():
        return True
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableLyricsVersions, database, delete, select
    try:
        database.execute(
            sqlite_insert(TableLyricsVersions).values(
                lidarrTrackId=track_id,
                content=old,
                lyrics_type='synced' if is_synced_lyrics(old) else 'plain',
                provider=provider,
                translated_from=translated_from,
                timestamp=datetime.now(),
            )
        )
    except Exception as e:
        logger.error(f"Failed to archive old lyrics (track {track_id}): {e}")
        return False
    try:
        # Cap versions per track so repeated upgrades/overwrites don't grow
        # the table unbounded — full lyrics text per row adds up.
        keep_ids = database.execute(
            select(TableLyricsVersions.id)
            .where(TableLyricsVersions.lidarrTrackId == track_id)
            .order_by(TableLyricsVersions.timestamp.desc(), TableLyricsVersions.id.desc())
            .limit(_MAX_VERSIONS_PER_TRACK)
        ).scalars().all()
        database.execute(
            delete(TableLyricsVersions)
            .where(TableLyricsVersions.lidarrTrackId == track_id)
            .where(TableLyricsVersions.id.not_in(keep_ids))
        )
    except Exception as e:
        # The archive row exists; only the trim failed.
        logger.warning(f"Failed to trim archived lyrics versions (track {track_id}): {e}")
    logger.debug(f"Archived previous lyrics for track {track_id}")
    return True


def persist_lyrics(track, content, provider, *, source='auto', detect_lang=True, archive=True,
                   archive_translated_from=None, detected_language=None, record_history=True):
    """Write chosen lyrics to disk and record status + history.

    The single shared write path for both the manual API and the scheduled
    worker. Archives any existing file first, writes the new `.lrc`, sets the
    track to 'available' (resetting retry state), and logs a history entry.

    Args:
        track: a TableTracks row (must have a path).
        content: the lyrics text to write.
        provider: provider name for history/version records.
        source: origin of the content, stored in TableTracks.lyrics_source —
            'auto', 'manual', 'translated' or 'generated'.
        detect_lang: run language detection on the content.
        archive: archive the existing file into TableLyricsVersions first.
            If archiving fails the file is left untouched and None returned.
        archive_translated_from: when writing a translation over its source,
            the source's language — recorded on the archived source version.
        detected_language: known language of the content (e.g. a translation's
            target); used instead of running detection.
        record_history: add a TableHistory entry (off for follow-up writes
            such as an auto-translation of lyrics that were just logged).

    Returns:
        dict with keys filepath, is_synced, detected_language — or None on failure.
    """
    if not track or not track.path:
        logger.error("persist_lyrics called with no track path")
        return None
    if not content or not content.strip():
        return None

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableHistory, TableTracks, database, update

    track_base = os.path.splitext(track.path)[0]
    filepath = track_base + '.lrc'

    if archive and not _archive_existing(track.lidarrTrackId, filepath, provider,
                                         translated_from=archive_translated_from):
        logger.error(
            f"Not overwriting lyrics for track {track.lidarrTrackId}: "
            f"the existing file could not be archived"
        )
        return None

    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Error writing lyrics for track {track.lidarrTrackId}: {e}")
        return None

    synced_flag = is_synced_lyrics(content)
    if detected_language:
        detected = detected_language
    else:
        detected = detect_language(content) if detect_lang else None

    database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
        .values(
            lyrics_status='available',
            hasLyrics=True,
            is_synced=synced_flag,
            detected_language=detected,
            lyrics_source=source,
            lyrics_retry_count=0,
            lyrics_retry_after=None,
            updated_at_timestamp=datetime.now(),
        )
    )
    if not record_history:
        return {'filepath': filepath, 'is_synced': synced_flag, 'detected_language': detected}
    database.execute(
        sqlite_insert(TableHistory).values(
            action=1,
            description=f"Downloaded lyrics for {track.title}",
            metadata_type='lyrics',
            provider=provider,
            lidarrTrackId=track.lidarrTrackId,
            lidarrArtistId=track.artistId,
            lidarrAlbumId=track.albumId,
            timestamp=datetime.now(),
            metadata_path=filepath,
        )
    )
    return {'filepath': filepath, 'is_synced': synced_flag, 'detected_language': detected}


# ---------------------------------------------------------------------------
# Disk ↔ DB reconciliation
# ---------------------------------------------------------------------------

def reconcile_track_lyrics(track):
    """Sync one track row's lyrics state with what is actually on disk.

    Catches .lrc files deleted (or dropped in) outside lyrarr between Lidarr
    syncs. Cheap when nothing changed (one stat call), so it's safe to run
    on-demand from read paths. Terminal states (blacklisted, instrumental)
    are left alone. Returns True if the row was updated.
    """
    if not track or not track.path:
        return False

    from lyrarr.app.database import TableTracks, database, update

    filepath = os.path.splitext(track.path)[0] + '.lrc'
    exists = os.path.isfile(filepath)

    # Only trust a missing .lrc when its audio file is there: if the music
    # mount is down (or the track moved) every file is "missing", and flipping
    # the whole library to 'missing' would trigger a mass re-download.
    if not exists and track.lyrics_status == 'available' and os.path.isfile(track.path):
        database.execute(
            update(TableTracks)
            .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
            .values(
                lyrics_status='missing',
                hasLyrics=False,
                is_synced=False,
                detected_language=None,
                lyrics_retry_count=0,
                lyrics_retry_after=None,
                updated_at_timestamp=datetime.now(),
            )
        )
        logger.info(f"Lyrics file for '{track.title}' was deleted from disk — marked missing")
        return True

    if exists and track.lyrics_status == 'missing':
        try:
            with open(filepath, encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            return False
        if not content.strip():
            return False
        database.execute(
            update(TableTracks)
            .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
            .values(
                lyrics_status='available',
                hasLyrics=True,
                is_synced=is_synced_lyrics(content),
                detected_language=detect_language(content),
                # Placed by someone else — origin unknown, so the scheduled
                # synced-upgrade treats it like pre-existing lyrics.
                lyrics_source=None,
                lyrics_retry_count=0,
                lyrics_retry_after=None,
                updated_at_timestamp=datetime.now(),
            )
        )
        logger.info(f"Lyrics file for '{track.title}' appeared on disk — marked available")
        return True

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


def result_is_blacklisted(result, blacklisted_hashes):
    """Whether a provider result's content matches any blacklisted hash.

    Checks both synced and plain content so a result is rejected regardless of
    which form was stored when it was blacklisted.
    """
    if not blacklisted_hashes:
        return False
    for key in ('synced_lyrics', 'plain_lyrics'):
        h = content_hash(result.get(key))
        if h and h in blacklisted_hashes:
            return True
    return False


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
    try:
        with open(os.path.splitext(track.path)[0] + '.lrc', encoding='utf-8', errors='ignore') as f:
            on_disk = f.read()
    except OSError:
        return hashes
    if content_hash(on_disk) != h:
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


def blacklist_content(track_id, content, provider=None):
    """Record a lyrics content blob as blacklisted for a track (idempotent).

    Blacklists exactly this content. When the user rejects the track's current
    file, also blacklist hashes_for_blacklist(track_id, content) so an
    auto-translation's provider source is caught too (the API does this).

    Returns True if the content was hashable and is now blacklisted.
    """
    h = content_hash(content)
    if not h:
        return False

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
            timestamp=datetime.now(),
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
