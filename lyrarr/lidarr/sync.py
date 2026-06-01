
import logging
import os
import re
import threading
from datetime import datetime

from lyrarr.app.config import settings
from lyrarr.app.database import TableAlbums, TableArtists, TableTracks, database, select, update
from lyrarr.app.event_handler import event_stream
from lyrarr.lidarr.api_client import lidarr_api

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


def _upsert_album(album):
    """Insert or update a single album row from a Lidarr album payload."""
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
    try:
        track_files = lidarr_api.get_tracks(album_id=album_id)
        if track_files and isinstance(track_files, list) and len(track_files) > 0:
            first_track_path = track_files[0].get('path', '')
            if first_track_path:
                album_path = os.path.dirname(first_track_path)
    except Exception as e:
        logger.debug(f"Could not get track files for album {album_id}: {e}")

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
        values['lyrics_status'] = 'unknown'

        default_profile_id = settings.general.default_profile_id
        if default_profile_id:
            values['profileId'] = int(default_profile_id)

        from sqlalchemy.dialects.sqlite import insert
        database.execute(insert(TableAlbums).values(**values))


def _sync_album_tracks(album):
    """Sync all track files for one album row. Returns number of tracks synced.

    Joins /trackfile (paths) with /track (title, number, duration), and reconciles
    each track's lyrics state against any .lrc on disk.
    """
    synced = 0
    try:
        track_files = lidarr_api.get_tracks(album_id=album.lidarrAlbumId)
        if not track_files or not isinstance(track_files, list):
            return 0

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
                    }

        for tf in track_files:
            track_id = tf.get('id')
            if not track_id:
                continue
            track_path = tf.get('path', '')

            meta = tf_to_metadata.get(track_id, {})
            if not meta.get('title') and track_records:
                for tr in track_records:
                    if tr.get('trackFileId') == track_id:
                        meta = {
                            'title': tr.get('title', ''),
                            'trackNumber': tr.get('absoluteTrackNumber') or tr.get('trackNumber'),
                            'mediumNumber': tr.get('mediumNumber', 1),
                            'duration': int((tr.get('duration', 0) or 0) / 1000) if tr.get('duration') else None,
                        }
                        break

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

            existing = database.execute(
                select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
            ).scalars().first()

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
            if existing and existing.lyrics_status in ('blacklisted', 'instrumental'):
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

            if existing:
                database.execute(
                    update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values)
                )
            else:
                values['created_at_timestamp'] = datetime.now()
                from sqlalchemy.dialects.sqlite import insert
                database.execute(insert(TableTracks).values(**values))
            synced += 1

    except Exception as e:
        logger.error(f"Error syncing tracks for album {album.lidarrAlbumId}: {e}")
    return synced


# ---------------------------------------------------------------------------
# Incremental (single-artist) sync
# ---------------------------------------------------------------------------

def _delete_artist(artist_id):
    """Remove a locally-stored artist (cascades to albums/tracks)."""
    from lyrarr.app.database import delete
    existing = database.execute(
        select(TableArtists).where(TableArtists.lidarrArtistId == artist_id)
    ).scalars().first()
    if existing:
        database.execute(delete(TableArtists).where(TableArtists.lidarrArtistId == artist_id))
        logger.info(f"Removed artist {artist_id} (deleted in Lidarr)")


def sync_artist(artist_id):
    """Incrementally sync a single artist and its albums + tracks.

    Lets a SignalR/webhook event update only the affected artist instead of
    re-syncing the entire library. If the artist no longer exists in Lidarr it is
    removed locally.
    """
    if not settings.general.use_lidarr:
        return

    artist = lidarr_api.get_artist(artist_id)
    if not artist:
        _delete_artist(artist_id)
        return

    if settings.lidarr.only_monitored and not artist.get('monitored', False):
        return

    logger.info(f"Incremental sync: artist {artist_id} '{artist.get('artistName', '?')}'")
    _upsert_artist(artist)

    albums = lidarr_api.get_albums(artist_id=artist_id) or []
    for album in albums:
        if not album.get('id'):
            continue
        if settings.lidarr.only_monitored and not album.get('monitored', False):
            continue
        stats = album.get('statistics', {})
        if stats.get('trackFileCount', 0) == 0:
            continue
        _upsert_album(album)

    album_rows = database.execute(
        select(TableAlbums).where(TableAlbums.artistId == artist_id)
    ).scalars().all()
    total_tracks = 0
    for album_row in album_rows:
        total_tracks += _sync_album_tracks(album_row)

    logger.info(
        f"Incremental sync done: artist {artist_id} — "
        f"{len(album_rows)} album(s), {total_tracks} track(s)"
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
    artists = lidarr_api.get_artists()

    if artists is None:
        logger.error("Failed to fetch artists from Lidarr")
        return

    if not artists:
        logger.warning("No artists received from Lidarr")
        return

    logger.info(f"Fetched {len(artists)} artists from Lidarr")

    synced = 0
    for artist in artists:
        if not artist.get('id'):
            continue
        if settings.lidarr.only_monitored and not artist.get('monitored', False):
            continue
        _upsert_artist(artist)
        synced += 1

    logger.info(f"Synced {synced} artists from Lidarr")

    # Now sync albums (only those with files on disk)
    update_albums(force=force)


def update_albums(force=False):
    """Sync albums from Lidarr to the local database. Only includes albums with downloaded tracks."""
    if not force and not settings.general.use_lidarr:
        return

    logger.info("Starting album sync from Lidarr...")
    albums = lidarr_api.get_albums()

    if albums is None:
        logger.error("Failed to fetch albums from Lidarr")
        return

    if not albums:
        logger.warning("No albums received from Lidarr")
        return

    logger.info(f"Fetched {len(albums)} albums from Lidarr")

    synced = 0
    skipped_no_files = 0

    for album in albums:
        if not album.get('id'):
            continue
        if settings.lidarr.only_monitored and not album.get('monitored', False):
            continue
        # Only sync albums that have files on disk
        stats = album.get('statistics', {})
        if stats.get('trackFileCount', 0) == 0:
            skipped_no_files += 1
            continue
        _upsert_album(album)
        synced += 1

    logger.info(f"Synced {synced} albums from Lidarr (skipped {skipped_no_files} without files)")

    # Now sync track files for all synced albums
    update_tracks(force=force)


def update_tracks(force=False):
    """Sync track files from Lidarr to the local database.

    Joins the /trackfile endpoint (file paths) with /track endpoint
    (real metadata: title, trackNumber, discNumber, duration).
    """
    if not force and not settings.general.use_lidarr:
        return

    logger.info("Starting track sync from Lidarr...")

    # Get all albums we have in our DB
    albums = database.execute(select(TableAlbums)).scalars().all()

    if not albums:
        logger.info("No albums to sync tracks for")
        return

    total_synced = 0

    for album in albums:
        total_synced += _sync_album_tracks(album)

    logger.info(f"Synced {total_synced} tracks from Lidarr")

    # Emit sync_complete event with stats
    from lyrarr.app.database import func
    artist_count = database.execute(
        select(func.count()).select_from(TableArtists)
    ).scalar() or 0
    album_count = database.execute(
        select(func.count()).select_from(TableAlbums)
    ).scalar() or 0
    event_stream(type='sync_complete', payload={
        'message': f'Sync complete: {artist_count} artists, {album_count} albums, {total_synced} tracks',
        'artists_synced': artist_count,
        'albums_synced': album_count,
    })
