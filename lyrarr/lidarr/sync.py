# coding=utf-8

import logging
import os
import re
from datetime import datetime

from lyrarr.app.database import (
    database, TableArtists, TableAlbums, TableTracks,
    select, update
)
from lyrarr.lidarr.api_client import lidarr_api
from lyrarr.app.config import settings
from lyrarr.app.event_handler import event_stream

logger = logging.getLogger(__name__)


def _lidarr_image_url(image_path):
    """Build full Lidarr image URL from a relative path."""
    if not image_path:
        return None
    if image_path.startswith('http'):
        return image_path
    protocol = 'https' if settings.lidarr.ssl else 'http'
    base = f"{protocol}://{settings.lidarr.ip}:{settings.lidarr.port}"
    return base + image_path


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
        artist_id = artist.get('id')
        if not artist_id:
            continue

        if settings.lidarr.only_monitored and not artist.get('monitored', False):
            continue

        # Extract images
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
                update(TableArtists)
                .where(TableArtists.lidarrArtistId == artist_id)
                .values(**values)
            )
        else:
            values['created_at_timestamp'] = datetime.now()
            values['metadata_status'] = 'unknown'
            from sqlalchemy.dialects.sqlite import insert
            database.execute(insert(TableArtists).values(**values))

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
        album_id = album.get('id')
        if not album_id:
            continue

        if settings.lidarr.only_monitored and not album.get('monitored', False):
            continue

        # Only sync albums that have files on disk
        stats = album.get('statistics', {})
        track_file_count = stats.get('trackFileCount', 0)
        if track_file_count == 0:
            skipped_no_files += 1
            continue

        # Extract cover image
        cover = None
        for image in album.get('images', []):
            if image.get('coverType') == 'cover':
                cover = image.get('remoteUrl') or _lidarr_image_url(image.get('url', ''))
                break

        artist_id = album.get('artistId')

        # Extract year from releaseDate
        release_date = album.get('releaseDate', '')
        year = None
        if release_date and len(release_date) >= 4:
            try:
                year = int(release_date[:4])
            except (ValueError, TypeError):
                pass

        # Get album path: fetch first track file to derive the album directory
        album_path = ''
        try:
            track_files = lidarr_api.get_tracks(album_id=album_id)
            if track_files and isinstance(track_files, list) and len(track_files) > 0:
                first_track_path = track_files[0].get('path', '')
                if first_track_path:
                    album_path = os.path.dirname(first_track_path)
        except Exception as e:
            logger.debug(f"Could not get track files for album {album_id}: {e}")

        # Fallback: use artist path if available
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
                update(TableAlbums)
                .where(TableAlbums.lidarrAlbumId == album_id)
                .values(**values)
            )
        else:
            values['created_at_timestamp'] = datetime.now()
            # Check if cover.jpg already exists in the album dir
            cover_exists = False
            if album_path:
                cover_filename = settings.metadata.covers.folder_art_filename
                for ext in ['.jpg', '.png', '.webp']:
                    if os.path.isfile(os.path.join(album_path, f"{cover_filename}{ext}")):
                        cover_exists = True
                        break
            values['cover_status'] = 'available' if cover_exists else 'missing'
            values['lyrics_status'] = 'unknown'

            # Auto-assign default profile if configured
            default_profile_id = settings.general.default_profile_id
            if default_profile_id:
                values['profileId'] = int(default_profile_id)

            from sqlalchemy.dialects.sqlite import insert
            database.execute(insert(TableAlbums).values(**values))

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
        try:
            # Get track files (has path, id)
            track_files = lidarr_api.get_tracks(album_id=album.lidarrAlbumId)
            if not track_files or not isinstance(track_files, list):
                continue

            # Get track records (has title, trackNumber, discNumber, duration)
            track_records = lidarr_api.get_track_records(album_id=album.lidarrAlbumId)

            # Build a mapping: trackFileId -> track record metadata
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
            
            logger.info(f"Album {album.lidarrAlbumId} '{album.title}': {len(track_files)} trackfiles, "
                        f"{len(track_records) if track_records else 0} track records, "
                        f"{len(tf_to_metadata)} matched by trackFileId")

            for tf in track_files:
                track_id = tf.get('id')
                if not track_id:
                    continue

                track_path = tf.get('path', '')

                # Get real metadata from track record (matched by trackFileId)
                meta = tf_to_metadata.get(track_id, {})
                
                # If no match by trackFileId, try matching by track file path
                if not meta.get('title') and track_records:
                    for tr in track_records:
                        tr_file_id = tr.get('trackFileId')
                        if tr_file_id and tr_file_id == track_id:
                            meta = {
                                'title': tr.get('title', ''),
                                'trackNumber': tr.get('absoluteTrackNumber') or tr.get('trackNumber'),
                                'mediumNumber': tr.get('mediumNumber', 1),
                                'duration': int((tr.get('duration', 0) or 0) / 1000) if tr.get('duration') else None,
                            }
                            break
                
                # Derive title: prefer track record, fall back to filename
                title = meta.get('title', '').strip()
                if not title and track_path:
                    # Parse from filename, trying to strip track numbers and prefixes
                    fname = os.path.splitext(os.path.basename(track_path))[0]
                    # Remove common patterns: "01 - ", "01. ", "1-01 ", artist - album - 01 - 
                    # Strip leading disc-track patterns like "1-01 " or "01 "
                    cleaned = re.sub(r'^(\d+-)?(\d+)\s*[-\.]\s*', '', fname)
                    # Strip "Artist - Album - " prefix patterns
                    parts = cleaned.split(' - ')
                    if len(parts) >= 3:
                        cleaned = parts[-1].strip()
                    elif len(parts) == 2:
                        cleaned = parts[-1].strip()
                    title = cleaned if cleaned else fname
                    logger.info(f"Track {track_id}: no metadata from Lidarr, parsed title from filename: '{title}'")
                if not title:
                    title = 'Unknown'

                existing = database.execute(
                    select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
                ).scalars().first()

                # Check if lyrics file exists alongside the track
                lyrics_exist = False
                detected_lang = None
                is_synced_flag = False
                if track_path:
                    track_base = os.path.splitext(track_path)[0]
                    for ext in ['.lrc', '.txt']:
                        lyrics_path = track_base + ext
                        if os.path.isfile(lyrics_path):
                            lyrics_exist = True
                            # Detect sync status from content, not extension
                            try:
                                from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
                                with open(lyrics_path, 'r', encoding='utf-8', errors='ignore') as lf:
                                    lyrics_content = lf.read()
                                is_synced_flag = is_synced_lyrics(lyrics_content)
                                detected_lang = detect_language(lyrics_content)
                            except Exception:
                                pass
                            break

                # Determine lyrics_status: preserve 'blacklisted' if already set
                if existing and existing.lyrics_status == 'blacklisted':
                    new_lyrics_status = 'blacklisted'
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
                        update(TableTracks)
                        .where(TableTracks.lidarrTrackId == track_id)
                        .values(**values)
                    )
                else:
                    values['created_at_timestamp'] = datetime.now()
                    from sqlalchemy.dialects.sqlite import insert
                    database.execute(insert(TableTracks).values(**values))

                total_synced += 1

        except Exception as e:
            logger.error(f"Error syncing tracks for album {album.lidarrAlbumId}: {e}")

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
