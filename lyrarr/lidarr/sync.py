
import logging
import os
import re
import threading
from datetime import datetime

from lyrarr.app.config import settings
from lyrarr.app.database import (
    TableAlbums,
    TableArtists,
    TableProfiles,
    TableTracks,
    database,
    delete,
    select,
    update,
)
from lyrarr.app.event_handler import event_stream
from lyrarr.lidarr.api_client import LidarrError, lidarr_api

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sync coalescing
#
# Lidarr fires a SignalR/webhook event for every artist/album/track change. A
# bulk import can emit dozens of events in seconds. update_artists() is a full
# library sync, so spawning one thread per event would run many heavy syncs
# concurrently against both SQLite and the Lidarr API. request_sync() collapses
# bursts into at most one run (plus a single follow-up if events arrived while a
# sync was in progress), after a short debounce window.
# ---------------------------------------------------------------------------

_sync_lock = threading.Lock()
_sync_running = False
_sync_pending_full = False
_pending_artist_ids = set()
_sync_timer = None
_SYNC_DEBOUNCE_SECONDS = 5


def request_sync(force=False, debounce=True):
    """Request a coalesced FULL library sync. Safe to call from any thread."""
    global _sync_pending_full
    with _sync_lock:
        _sync_pending_full = True
    _schedule(force, debounce)


def sync_in_progress():
    """True while a coalesced sync run is active."""
    return _sync_running


def run_scheduled_sync():
    """Entry point for the APScheduler sync jobs.

    Participates in the same single-flight state as manual and SignalR
    triggers, with run_downloads-style skip semantics: if a sync is already
    running, this tick is skipped instead of running a second full sync
    concurrently (which the old direct update_artists call could do).

    Runs the drain loop synchronously in the scheduler's thread so the task
    shows as running for its true duration, and any per-artist syncs queued
    by SignalR events mid-run are drained before the job finishes.
    """
    global _sync_running, _sync_pending_full
    with _sync_lock:
        if _sync_running:
            logger.info("Skipping scheduled Lidarr sync — a sync is already in progress")
            return
        _sync_pending_full = True
        _sync_running = True
    _run_sync()


def request_artist_sync(artist_id, debounce=True):
    """Request a coalesced INCREMENTAL sync of a single artist.

    Falls back to a full sync if no artist id is given. Multiple artist ids that
    arrive within the debounce window are batched into one run.
    """
    if not artist_id:
        return request_sync(debounce=debounce)
    with _sync_lock:
        _pending_artist_ids.add(int(artist_id))
    _schedule(False, debounce)


def _schedule(force, debounce):
    """(Re)arm the debounce timer, or start immediately if debounce is off."""
    global _sync_timer
    if not debounce:
        _start_sync(force)
        return
    with _sync_lock:
        if _sync_timer is not None:
            _sync_timer.cancel()
        _sync_timer = threading.Timer(_SYNC_DEBOUNCE_SECONDS, lambda: _start_sync(force))
        _sync_timer.daemon = True
        _sync_timer.start()


def _start_sync(force=False):
    global _sync_running
    with _sync_lock:
        if _sync_running:
            # A run is in flight; it will pick up whatever is pending when it loops.
            return
        _sync_running = True
    threading.Thread(target=_run_sync, kwargs={'force': force}, daemon=True).start()


def _run_sync(force=False):
    """Drain pending sync work until nothing is queued.

    A single full request supersedes any queued per-artist syncs for that pass.
    Anything that arrives mid-run is handled on the next loop iteration.
    """
    global _sync_running, _sync_pending_full, _pending_artist_ids
    try:
        while True:
            with _sync_lock:
                do_full = _sync_pending_full
                artist_ids = _pending_artist_ids
                _sync_pending_full = False
                _pending_artist_ids = set()
                if not do_full and not artist_ids:
                    _sync_running = False
                    return
            try:
                if do_full:
                    update_artists(force=force)
                else:
                    for aid in artist_ids:
                        sync_artist(aid)
            except Exception as e:
                logger.error(f"Coalesced sync failed: {e}")
    finally:
        # A fresh thread is spawned per sync burst; drop its thread-local scoped
        # session on exit so dead threads don't accumulate sessions over time.
        database.remove()


def _lidarr_image_url(image_path):
    """Build full Lidarr image URL from a relative path."""
    if not image_path:
        return None
    if image_path.startswith('http'):
        return image_path
    protocol = 'https' if settings.lidarr.ssl else 'http'
    base = f"{protocol}://{settings.lidarr.ip}:{settings.lidarr.port}"
    return base + image_path


# ---------------------------------------------------------------------------
# Per-entity upserts (shared by the full sync and the incremental sync)
# ---------------------------------------------------------------------------

def _upsert_artist(artist):
    """Insert or update a single artist row from a Lidarr artist payload."""
    artist_id = artist.get('id')

    poster = None
    fanart = None
    for image in artist.get('images', []):
        cover_type = image.get('coverType', '')
        img_url = image.get('remoteUrl') or image.get('url', '')
        if cover_type == 'poster':
            poster = _lidarr_image_url(img_url)
        elif cover_type == 'fanart':
            fanart = _lidarr_image_url(img_url)

    existing = database.execute(
        select(TableArtists).where(TableArtists.lidarrArtistId == artist_id)
    ).scalars().first()

    values = {
        'lidarrArtistId': artist_id,
        'mbId': artist.get('foreignArtistId', ''),
        'name': artist.get('artistName', 'Unknown'),
        'sortName': artist.get('sortName', ''),
        'path': artist.get('path', ''),
        'monitored': bool(artist.get('monitored', False)),
        'overview': (artist.get('overview') or '')[:500],
        'fanart': fanart,
        'poster': poster,
        'tags': str(artist.get('tags', [])),
        'updated_at_timestamp': datetime.now(),
    }

    if existing:
        database.execute(
            update(TableArtists).where(TableArtists.lidarrArtistId == artist_id).values(**values)
        )
    else:
        values['created_at_timestamp'] = datetime.now()
        values['metadata_status'] = 'unknown'
        from sqlalchemy.dialects.sqlite import insert
        database.execute(insert(TableArtists).values(**values))


def _existing_profile_id(profile_id):
    """Return `profile_id` as an int if that profile exists, else None.

    `general.default_profile_id` can outlive the profile it points at; inserting
    it blindly would fail the album's FOREIGN KEY and abort the whole sync.
    """
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    exists = database.execute(
        select(TableProfiles.id).where(TableProfiles.id == pid)
    ).first()
    if not exists:
        logger.warning(f"Default profile {pid} no longer exists — new albums get no profile")
        return None
    return pid


def _upsert_album(album, track_files=None):
    """Insert or update a single album row from a Lidarr album payload.

    `track_files` is the pre-fetched /trackfile list for this album; passing it
    avoids one Lidarr API round-trip per album during bulk syncs. The album's
    artist must already be stored (FOREIGN KEY).
    """
    album_id = album.get('id')

    cover = None
    for image in album.get('images', []):
        if image.get('coverType') == 'cover':
            cover = image.get('remoteUrl') or _lidarr_image_url(image.get('url', ''))
            break

    artist_id = album.get('artistId')

    release_date = album.get('releaseDate', '')
    year = None
    if release_date and len(release_date) >= 4:
        try:
            year = int(release_date[:4])
        except (ValueError, TypeError):
            pass

    # Derive the album directory from its first track file.
    album_path = ''
    if track_files is None:
        try:
            track_files = lidarr_api.get_tracks(album_id=album_id)
        except Exception as e:
            logger.debug(f"Could not get track files for album {album_id}: {e}")
            track_files = []
    if track_files and isinstance(track_files, list) and len(track_files) > 0:
        first_track_path = track_files[0].get('path', '')
        if first_track_path:
            album_path = os.path.dirname(first_track_path)

    if not album_path:
        artist_obj = album.get('artist', {})
        if artist_obj and artist_obj.get('path'):
            album_path = artist_obj['path']

    existing = database.execute(
        select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
    ).scalars().first()

    values = {
        'lidarrAlbumId': album_id,
        'mbId': album.get('foreignAlbumId', ''),
        'artistId': artist_id,
        'title': album.get('title', 'Unknown'),
        'year': year,
        'path': album_path,
        'monitored': bool(album.get('monitored', False)),
        'overview': (album.get('overview') or '')[:500],
        'cover': cover,
        'genres': str(album.get('genres', [])),
        'albumType': album.get('albumType', ''),
        'updated_at_timestamp': datetime.now(),
    }

    if existing:
        database.execute(
            update(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id).values(**values)
        )
    else:
        values['created_at_timestamp'] = datetime.now()
        # Reconcile cover status against any art already on disk.
        cover_exists = False
        if album_path:
            cover_filename = settings.metadata.covers.folder_art_filename
            for ext in ['.jpg', '.png', '.webp']:
                if os.path.isfile(os.path.join(album_path, f"{cover_filename}{ext}")):
                    cover_exists = True
                    break
        values['cover_status'] = 'available' if cover_exists else 'missing'
        # API responses compute album lyrics status from its tracks; this
        # column is legacy and not relied upon.
        values['lyrics_status'] = 'unknown'

        default_profile_id = _existing_profile_id(settings.general.default_profile_id)
        if default_profile_id:
            values['profileId'] = default_profile_id

        from sqlalchemy.dialects.sqlite import insert
        database.execute(insert(TableAlbums).values(**values))


def _fetch_artist_track_data(artist_id):
    """Fetch all track files and track records for one artist, grouped by album.

    Two Lidarr API calls per artist instead of two per album — the main cost
    saving of the bulk sync. Returns (files_by_album, records_by_album).
    Raises LidarrError if either call fails, so callers never mistake a failed
    fetch for "this artist has no files".
    """
    files_by_album = {}
    records_by_album = {}

    for tf in lidarr_api.get_tracks(artist_id=artist_id):
        files_by_album.setdefault(tf.get('albumId'), []).append(tf)

    for tr in lidarr_api.get_track_records(artist_id=artist_id):
        records_by_album.setdefault(tr.get('albumId'), []).append(tr)

    return files_by_album, records_by_album


def _sync_album_tracks(album, track_files=None, track_records=None):
    """Sync all track files for one album row. Returns number of tracks synced.

    Joins /trackfile (paths) with /track (title, number, duration, recording
    id), and reconciles each track's lyrics state against any .lrc on disk.
    Pre-fetched per-artist `track_files`/`track_records` avoid two API
    round-trips per album. Errors propagate so the caller knows the album's
    tracks are incomplete.
    """
    synced = 0
    if track_files is None:
        track_files = lidarr_api.get_tracks(album_id=album.lidarrAlbumId)
    if not track_files or not isinstance(track_files, list):
        return 0

    if track_records is None:
        track_records = lidarr_api.get_track_records(album_id=album.lidarrAlbumId)
    tf_to_metadata = {}
    if track_records and isinstance(track_records, list):
        for tr in track_records:
            tf_id = tr.get('trackFileId')
            if tf_id:
                tf_to_metadata[tf_id] = {
                    'title': tr.get('title', ''),
                    'trackNumber': tr.get('absoluteTrackNumber') or tr.get('trackNumber'),
                    'mediumNumber': tr.get('mediumNumber', 1),
                    'duration': int((tr.get('duration', 0) or 0) / 1000) if tr.get('duration') else None,
                    'mbId': tr.get('foreignRecordingId') or None,
                }

    # One query for the album's existing rows instead of one per track.
    # Keyed on lidarrTrackId (not albumId) so a track that moved albums in
    # Lidarr still matches its existing row instead of inserting a duplicate.
    track_ids = [tf.get('id') for tf in track_files if tf.get('id')]
    existing_tracks = {
        t.lidarrTrackId: t
        for t in database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId.in_(track_ids))
        ).scalars().all()
    }

    for tf in track_files:
        track_id = tf.get('id')
        if not track_id:
            continue
        track_path = tf.get('path', '')

        meta = tf_to_metadata.get(track_id, {})

        # Derive title: prefer the track record, fall back to the filename.
        title = (meta.get('title') or '').strip()
        if not title and track_path:
            fname = os.path.splitext(os.path.basename(track_path))[0]
            cleaned = re.sub(r'^(\d+-)?(\d+)\s*[-\.]\s*', '', fname)
            parts = cleaned.split(' - ')
            if len(parts) >= 2:
                cleaned = parts[-1].strip()
            title = cleaned if cleaned else fname
        if not title:
            title = 'Unknown'

        existing = existing_tracks.get(track_id)

        lyrics_exist = False
        detected_lang = None
        is_synced_flag = False
        if track_path:
            lyrics_path = os.path.splitext(track_path)[0] + '.lrc'
            if os.path.isfile(lyrics_path):
                lyrics_exist = True
                try:
                    from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
                    with open(lyrics_path, encoding='utf-8', errors='ignore') as lf:
                        lyrics_content = lf.read()
                    is_synced_flag = is_synced_lyrics(lyrics_content)
                    detected_lang = detect_language(lyrics_content)
                except Exception:
                    pass

        # Preserve terminal lyrics states lyrarr set itself (a re-sync must not
        # reset a blacklisted or instrumental track back to 'missing').
        if existing and existing.lyrics_status in _TERMINAL_LYRICS_STATES:
            new_lyrics_status = existing.lyrics_status
            new_has_lyrics = False
        else:
            new_lyrics_status = 'available' if lyrics_exist else 'missing'
            new_has_lyrics = lyrics_exist

        values = {
            'lidarrTrackId': track_id,
            'albumId': album.lidarrAlbumId,
            'artistId': album.artistId,
            'title': title,
            'trackNumber': meta.get('trackNumber'),
            'discNumber': meta.get('mediumNumber', 1),
            'duration': meta.get('duration'),
            'path': track_path,
            'lyrics_status': new_lyrics_status,
            'hasLyrics': new_has_lyrics,
            'detected_language': detected_lang,
            'is_synced': is_synced_flag,
            'updated_at_timestamp': datetime.now(),
        }
        # Only overwrite a known recording id, never blank it out.
        if meta.get('mbId'):
            values['mbId'] = meta['mbId']

        if existing:
            database.execute(
                update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values)
            )
        else:
            values['created_at_timestamp'] = datetime.now()
            from sqlalchemy.dialects.sqlite import insert
            database.execute(insert(TableTracks).values(**values))
        synced += 1

    return synced


# ---------------------------------------------------------------------------
# Removing what Lidarr no longer has
#
# Track rows are keyed on the Lidarr *trackfile* id. When Lidarr upgrades or
# re-imports a file it gets a new trackfile id, so the sync inserts a new row
# and the old one is left pointing at the same .lrc. Reconciliation runs only
# after an artist's data was fetched completely; it folds each stale row into
# its replacement (same album + MusicBrainz recording id, or the same path
# stem, i.e. the same .lrc) so blacklist entries, history, archived lyrics
# versions and lyrarr-set lyrics state survive the upgrade.
# ---------------------------------------------------------------------------

_TERMINAL_LYRICS_STATES = ('blacklisted', 'instrumental')


def _path_stem(path):
    return os.path.splitext(path)[0] if path else None


def _carry_over_track_state(old, new):
    """Move user/lyrarr state from a stale track row onto its replacement."""
    from lyrarr.app.database import TableBlacklist, TableHistory, TableLyricsVersions

    for table in (TableHistory, TableBlacklist, TableLyricsVersions):
        database.execute(
            update(table)
            .where(table.lidarrTrackId == old.lidarrTrackId)
            .values(lidarrTrackId=new.lidarrTrackId)
        )

    values = {}
    if old.lyrics_status in _TERMINAL_LYRICS_STATES and new.lyrics_status not in _TERMINAL_LYRICS_STATES:
        values.update(lyrics_status=old.lyrics_status, hasLyrics=False)
    elif old.lyrics_status == 'missing' and new.lyrics_status == 'missing':
        # Keep the retry backoff so an upgrade doesn't re-hammer providers.
        values.update(
            lyrics_retry_count=old.lyrics_retry_count,
            lyrics_retry_after=old.lyrics_retry_after,
        )
    if new.lyrics_source is None and old.lyrics_source and _path_stem(old.path) == _path_stem(new.path):
        values['lyrics_source'] = old.lyrics_source
    if not new.mbId and old.mbId:
        values['mbId'] = old.mbId
    if values:
        values['updated_at_timestamp'] = datetime.now()
        database.execute(
            update(TableTracks).where(TableTracks.lidarrTrackId == new.lidarrTrackId).values(**values)
        )
    logger.info(
        f"Track '{new.title}' was re-imported in Lidarr (file {old.lidarrTrackId} → "
        f"{new.lidarrTrackId}); carried over its lyrarr state"
    )


def _reconcile_artist(artist_id, lidarr_albums, files_by_album):
    """Drop one artist's local albums/tracks that Lidarr no longer has.

    Only call with COMPLETE Lidarr data for the artist: `lidarr_albums` is every
    album Lidarr lists for it (unfiltered) and `files_by_album` every track file.
    Albums stay if Lidarr still has files for them (even if filtered out by
    only_monitored); tracks stay if their trackfile id still exists.
    """
    current_file_ids = {
        tf.get('id') for tfs in files_by_album.values() for tf in tfs if tf.get('id')
    }
    local_tracks = database.execute(
        select(TableTracks).where(TableTracks.artistId == artist_id)
    ).scalars().all()
    current = [t for t in local_tracks if t.lidarrTrackId in current_file_ids]
    stale = [t for t in local_tracks if t.lidarrTrackId not in current_file_ids]

    if stale:
        by_recording = {}
        by_stem = {}
        for t in current:
            if t.mbId:
                by_recording.setdefault((t.albumId, t.mbId), t)
            if t.path:
                by_stem.setdefault(_path_stem(t.path), t)
        for old in stale:
            replacement = None
            if old.mbId:
                replacement = by_recording.get((old.albumId, old.mbId))
            if replacement is None and old.path:
                replacement = by_stem.get(_path_stem(old.path))
            if replacement is not None:
                _carry_over_track_state(old, replacement)
            database.execute(delete(TableTracks).where(TableTracks.lidarrTrackId == old.lidarrTrackId))
        logger.info(f"Artist {artist_id}: removed {len(stale)} track(s) no longer in Lidarr")

    keep_album_ids = [
        a.get('id') for a in lidarr_albums if a.get('id') and files_by_album.get(a.get('id'))
    ]
    result = database.execute(
        delete(TableAlbums)
        .where(TableAlbums.artistId == artist_id)
        .where(TableAlbums.lidarrAlbumId.not_in(keep_album_ids))
    )
    if result.rowcount:
        logger.info(f"Artist {artist_id}: removed {result.rowcount} album(s) no longer in Lidarr (or without files)")


def _sync_artist_albums(artist_id, lidarr_albums, files_by_album, records_by_album):
    """Upsert one artist's albums and tracks, then remove what Lidarr dropped.

    `lidarr_albums` is the artist's full album list from Lidarr. One bad album
    (FK failure, unexpected payload) is logged and skipped instead of aborting
    the sync; removal of stale rows is then skipped for this artist because its
    local state is not known to be complete.
    Returns (albums_synced, tracks_synced, albums_skipped_no_files).
    """
    synced_albums = 0
    total_tracks = 0
    skipped_no_files = 0
    complete = True

    for album in lidarr_albums:
        album_id = album.get('id')
        if not album_id:
            continue
        if settings.lidarr.only_monitored and not album.get('monitored', False):
            continue
        # Presence of actual track files is the source of truth for "has files"
        # — the statistics block can be missing or stale in bulk responses.
        track_files = files_by_album.get(album_id)
        if not track_files:
            skipped_no_files += 1
            continue
        try:
            _upsert_album(album, track_files=track_files)
            album_row = database.execute(
                select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
            ).scalars().first()
            if album_row:
                total_tracks += _sync_album_tracks(
                    album_row, track_files, records_by_album.get(album_id, [])
                )
                synced_albums += 1
        except Exception as e:
            complete = False
            database.rollback()
            logger.error(f"Error syncing album {album_id} ('{album.get('title', '?')}') "
                         f"of artist {artist_id}: {e}")

    if complete:
        try:
            _reconcile_artist(artist_id, lidarr_albums, files_by_album)
        except Exception as e:
            database.rollback()
            logger.error(f"Error removing stale albums/tracks for artist {artist_id}: {e}")
    else:
        logger.warning(f"Artist {artist_id}: sync incomplete, not removing stale albums/tracks this run")

    return synced_albums, total_tracks, skipped_no_files


# ---------------------------------------------------------------------------
# Incremental (single-artist) sync
# ---------------------------------------------------------------------------

def _delete_artist(artist_id):
    """Remove a locally-stored artist (cascades to albums/tracks)."""
    existing = database.execute(
        select(TableArtists).where(TableArtists.lidarrArtistId == artist_id)
    ).scalars().first()
    if existing:
        database.execute(delete(TableArtists).where(TableArtists.lidarrArtistId == artist_id))
        logger.info(f"Removed artist {artist_id} '{existing.name}' (deleted in Lidarr)")


def sync_artist(artist_id):
    """Incrementally sync a single artist and its albums + tracks.

    Lets a SignalR/webhook event update only the affected artist instead of
    re-syncing the entire library. The artist is removed locally only when
    Lidarr positively answers 404 for it; an unreachable Lidarr (timeout,
    5xx, ...) changes nothing.
    """
    if not settings.general.use_lidarr:
        return

    try:
        artist = lidarr_api.get_artist(artist_id)
    except LidarrError as e:
        logger.warning(f"Incremental sync of artist {artist_id} skipped — Lidarr unavailable: {e}")
        return

    if artist is None:
        # A wrong Lidarr URL base also produces 404s for everything; only trust
        # the 404 if Lidarr itself answers.
        if lidarr_api.test_connection():
            _delete_artist(artist_id)
        else:
            logger.warning(f"Lidarr returned 404 for artist {artist_id} but its status endpoint is "
                           f"unreachable — not removing anything (check the Lidarr URL base)")
        return
    if not isinstance(artist, dict):
        logger.warning(f"Unexpected Lidarr payload for artist {artist_id}; skipping")
        return

    if settings.lidarr.only_monitored and not artist.get('monitored', False):
        return

    logger.info(f"Incremental sync: artist {artist_id} '{artist.get('artistName', '?')}'")
    _upsert_artist(artist)

    try:
        albums = lidarr_api.get_albums(artist_id=artist_id)
        files_by_album, records_by_album = _fetch_artist_track_data(artist_id)
    except LidarrError as e:
        logger.warning(f"Incremental sync of artist {artist_id}: could not fetch albums/tracks: {e}")
        return

    synced_albums, total_tracks, _ = _sync_artist_albums(
        artist_id, albums, files_by_album, records_by_album
    )

    logger.info(
        f"Incremental sync done: artist {artist_id} — "
        f"{synced_albums} album(s), {total_tracks} track(s)"
    )
    event_stream(type='sync_complete', payload={
        'message': f"Synced artist '{artist.get('artistName', '?')}'",
    })


def update_artists(force=False):
    """Sync artists from Lidarr to the local database."""
    if not force and not settings.general.use_lidarr:
        logger.debug("Lidarr not enabled, skipping sync")
        return

    logger.info("Starting artist sync from Lidarr...")
    event_stream(type='sync_start', payload={'message': 'Syncing with Lidarr...'})
    try:
        artists = lidarr_api.get_artists()
    except LidarrError as e:
        logger.error(f"Failed to fetch artists from Lidarr: {e}")
        return

    if not artists:
        # Never treat an empty list as "everything was deleted".
        logger.warning("No artists received from Lidarr")
        return

    logger.info(f"Fetched {len(artists)} artists from Lidarr")

    lidarr_artist_ids = {a.get('id') for a in artists if a.get('id')}
    synced = 0
    for artist in artists:
        if not artist.get('id'):
            continue
        if settings.lidarr.only_monitored and not artist.get('monitored', False):
            continue
        try:
            _upsert_artist(artist)
            synced += 1
        except Exception as e:
            database.rollback()
            logger.error(f"Error syncing artist {artist.get('id')} ('{artist.get('artistName', '?')}'): {e}")

    logger.info(f"Synced {synced} artists from Lidarr")

    # The artist list is complete, so anything stored locally that Lidarr no
    # longer has was deleted there.
    local_ids = set(database.execute(select(TableArtists.lidarrArtistId)).scalars().all())
    for artist_id in local_ids - lidarr_artist_ids:
        _delete_artist(artist_id)

    # Now sync albums + tracks (only albums with files on disk)
    update_albums(force=force, lidarr_artist_ids=lidarr_artist_ids)


def update_albums(force=False, lidarr_artist_ids=None):
    """Sync albums and their track files from Lidarr to the local database.

    Fetches the full album list in one call, then track files + track records
    per artist (two calls each) instead of per album — on large libraries this
    cuts Lidarr API round-trips by roughly 6x. Only albums that actually have
    track files are stored; presence in the /trackfile response is the source
    of truth, not the albums' statistics block. `lidarr_artist_ids` (the
    complete artist list of this sync) lets artists whose albums all vanished
    be cleaned up too.
    """
    if not force and not settings.general.use_lidarr:
        return

    logger.info("Starting album sync from Lidarr...")
    try:
        albums = lidarr_api.get_albums()
    except LidarrError as e:
        logger.error(f"Failed to fetch albums from Lidarr: {e}")
        return

    if not albums:
        logger.warning("No albums received from Lidarr")
        return

    logger.info(f"Fetched {len(albums)} albums from Lidarr")

    # Grouped unfiltered: reconciliation needs every album Lidarr has, while
    # _sync_artist_albums applies the only_monitored filter for upserts.
    albums_by_artist = {}
    for album in albums:
        if not album.get('id') or not album.get('artistId'):
            continue
        albums_by_artist.setdefault(album['artistId'], []).append(album)

    stored_artist_ids = set(database.execute(select(TableArtists.lidarrArtistId)).scalars().all())
    artists_with_local_albums = set(
        database.execute(select(TableAlbums.artistId).distinct()).scalars().all()
    )
    artist_ids = set(albums_by_artist)
    if lidarr_artist_ids is not None:
        artist_ids |= artists_with_local_albums & set(lidarr_artist_ids)

    synced = 0
    skipped_no_files = 0
    skipped_unstored = 0
    total_tracks = 0

    for artist_id in artist_ids:
        artist_albums = albums_by_artist.get(artist_id, [])
        if artist_id not in stored_artist_ids:
            # e.g. a monitored album of an unmonitored artist with only_monitored
            # on: the album can't be stored without its artist (FOREIGN KEY).
            skipped_unstored += len(artist_albums)
            continue

        has_local = artist_id in artists_with_local_albums
        wanted = [a for a in artist_albums
                  if not settings.lidarr.only_monitored or a.get('monitored', False)]
        if not wanted and not has_local:
            continue
        # Skip the per-artist track fetch only when Lidarr's statistics
        # explicitly say no album has files and there is nothing local to
        # reconcile. Missing statistics → fetch anyway and let the actual
        # trackfile list decide.
        stats_say_empty = all(
            isinstance(a.get('statistics'), dict)
            and a['statistics'].get('trackFileCount', 0) == 0
            for a in artist_albums
        )
        if stats_say_empty and not has_local:
            skipped_no_files += len(wanted)
            continue

        try:
            files_by_album, records_by_album = _fetch_artist_track_data(artist_id)
        except Exception as e:
            logger.error(f"Error fetching track data for artist {artist_id}: {e}")
            continue

        a, t, s = _sync_artist_albums(artist_id, artist_albums, files_by_album, records_by_album)
        synced += a
        total_tracks += t
        skipped_no_files += s

    logger.info(
        f"Synced {synced} albums and {total_tracks} tracks from Lidarr "
        f"(skipped {skipped_no_files} albums without files"
        + (f", {skipped_unstored} albums of artists not synced" if skipped_unstored else '')
        + ")"
    )

    # Emit sync_complete event with stats
    from lyrarr.app.database import func
    artist_count = database.execute(
        select(func.count()).select_from(TableArtists)
    ).scalar() or 0
    album_count = database.execute(
        select(func.count()).select_from(TableAlbums)
    ).scalar() or 0
    event_stream(type='sync_complete', payload={
        'message': f'Sync complete: {artist_count} artists, {album_count} albums, {total_tracks} tracks',
        'artists_synced': artist_count,
        'albums_synced': album_count,
    })
