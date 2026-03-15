# coding=utf-8

"""
Scheduled metadata download worker.
Processes albums/tracks that are missing cover art or lyrics,
respecting each album's profile settings.
"""

import json
import logging
import os
import time
from datetime import datetime

from lyrarr.app.database import (
    database, TableAlbums, TableTracks, TableArtists, TableProfiles, TableHistory,
    select, update
)
from lyrarr.metadata.covers.musicbrainz import MusicBrainzCoverProvider
from lyrarr.metadata.covers.fanart import FanartCoverProvider
from lyrarr.metadata.lyrics.lrclib import LRCLIBProvider
from lyrarr.metadata.lyrics.genius import GeniusProvider
from lyrarr.metadata.embed import embed_cover_in_files
from lyrarr.app.event_handler import event_stream

logger = logging.getLogger(__name__)

# Provider instances
_cover_providers = {
    'musicbrainz': MusicBrainzCoverProvider(),
    'fanart': FanartCoverProvider(),
}

_lyrics_providers = {
    'lrclib': LRCLIBProvider(),
    'genius': GeniusProvider(),
}

# Rate limit: seconds between API calls
RATE_LIMIT = 2.0


def _get_profile(profile_id):
    """Get a profile by ID, or fall back to the default profile."""
    if profile_id:
        profile = database.execute(
            select(TableProfiles).where(TableProfiles.id == profile_id)
        ).scalars().first()
        if profile:
            return profile

    # Fall back to default
    return database.execute(
        select(TableProfiles).where(TableProfiles.is_default == 'True')
    ).scalars().first()


def _effective_settings(album, profile):
    """Merge album overrides with profile defaults. Album overrides take priority."""
    return {
        'download_covers': album.override_download_covers or (profile.download_covers if profile else 'True'),
        'download_lyrics': album.override_download_lyrics or (profile.download_lyrics if profile else 'True'),
        'cover_format': album.override_cover_format or (profile.cover_format if profile else 'jpg'),
        'prefer_synced_lyrics': album.override_prefer_synced or (profile.prefer_synced_lyrics if profile else 'True'),
        'cover_providers': profile.cover_providers if profile else '["musicbrainz","fanart"]',
        'lyrics_providers': profile.lyrics_providers if profile else '["lrclib","genius"]',
        'overwrite_existing': profile.overwrite_existing if profile else 'False',
        'embed_cover_art': profile.embed_cover_art if profile else 'False',
    }


def _parse_providers_list(providers_str):
    """Parse a JSON string list of providers, e.g. '["musicbrainz","fanart"]'."""
    try:
        return json.loads(providers_str)
    except (json.JSONDecodeError, TypeError):
        return []


def download_missing_covers():
    """Download cover art for albums that are missing it, based on their profile."""
    albums = database.execute(
        select(TableAlbums).where(TableAlbums.cover_status == 'missing')
    ).scalars().all()

    if not albums:
        logger.info("No albums with missing covers")
        return

    logger.info(f"Processing {len(albums)} albums with missing covers...")
    downloaded = 0

    for album in albums:
        profile = _get_profile(album.profileId)
        eff = _effective_settings(album, profile)
        if eff['download_covers'] != 'True':
            continue

        providers = _parse_providers_list(eff['cover_providers'])
        if not providers:
            continue

        # Get the artist for MusicBrainz IDs
        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
        ).scalars().first()

        cover_data = None
        used_provider = None

        for provider_name in providers:
            provider = _cover_providers.get(provider_name)
            if not provider:
                continue

            try:
                results = []
                if provider_name == 'musicbrainz' and album.mbId:
                    results = provider.search(mb_release_group_id=album.mbId)
                elif provider_name == 'fanart' and artist and artist.mbId:
                    results = provider.search(mb_artist_id=artist.mbId)

                if results:
                    # Try to download the first result
                    for result in results:
                        url = result.get('url') or result.get('url_large') or result.get('url_small')
                        if url:
                            img_data = provider.download(url)
                            if img_data:
                                cover_data = img_data
                                used_provider = provider_name
                                break

                if cover_data:
                    break

            except Exception as e:
                logger.error(f"Cover search error ({provider_name}) for '{album.title}': {e}")

            time.sleep(RATE_LIMIT)

        if cover_data and album.path:
            try:
                cover_format = eff['cover_format']
                filepath = os.path.join(album.path, f"cover.{cover_format}")
                os.makedirs(os.path.dirname(filepath), exist_ok=True)

                with open(filepath, 'wb') as f:
                    f.write(cover_data)

                # Update database
                database.execute(
                    update(TableAlbums)
                    .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
                    .values(cover_status='available', updated_at_timestamp=datetime.now())
                )

                # Add to history
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                database.execute(
                    sqlite_insert(TableHistory).values(
                        action=1,
                        description=f"Downloaded cover art for {album.title}",
                        metadata_type='cover',
                        provider=used_provider,
                        lidarrAlbumId=album.lidarrAlbumId,
                        lidarrArtistId=album.artistId,
                        timestamp=datetime.now(),
                        metadata_path=filepath,
                    )
                )

                downloaded += 1
                logger.info(f"✓ Cover art: '{album.title}' ({used_provider})")
                event_stream(type='download_progress', payload={
                    'metadata_type': 'cover', 'title': album.title, 'provider': used_provider,
                })

                # Embed if profile flag is set
                if eff['embed_cover_art'] == 'True':
                    try:
                        embed_cover_in_files(album.path, cover_data, cover_format)
                    except Exception as e:
                        logger.error(f"Error embedding cover for '{album.title}': {e}")

            except Exception as e:
                logger.error(f"Error saving cover for '{album.title}': {e}")
        elif not album.path:
            logger.debug(f"Skipping '{album.title}' — no album path set")

        time.sleep(RATE_LIMIT)

    logger.info(f"Cover art download complete: {downloaded}/{len(albums)} downloaded")
    return downloaded


def download_missing_lyrics():
    """Download lyrics for tracks that are missing them, based on their album's profile."""
    tracks = database.execute(
        select(TableTracks).where(TableTracks.lyrics_status == 'missing')
    ).scalars().all()

    if not tracks:
        logger.info("No tracks with missing lyrics")
        return

    logger.info(f"Processing {len(tracks)} tracks with missing lyrics...")
    downloaded = 0

    # Cache album profiles and artist names
    album_cache = {}
    artist_cache = {}

    for track in tracks:
        # Get album and its profile
        if track.albumId not in album_cache:
            album = database.execute(
                select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
            ).scalars().first()
            album_cache[track.albumId] = album
        album = album_cache[track.albumId]

        if not album:
            continue

        profile = _get_profile(album.profileId)
        eff = _effective_settings(album, profile)
        if eff['download_lyrics'] != 'True':
            continue

        providers = _parse_providers_list(eff['lyrics_providers'])
        if not providers:
            continue

        # Get artist name
        if track.artistId not in artist_cache:
            artist = database.execute(
                select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
            ).scalars().first()
            artist_cache[track.artistId] = artist
        artist = artist_cache[track.artistId]
        artist_name = artist.name if artist else None

        lyrics_data = None
        used_provider = None
        prefer_synced = profile.prefer_synced_lyrics == 'True'

        for provider_name in providers:
            provider = _lyrics_providers.get(provider_name)
            if not provider:
                continue

            try:
                results = provider.search(
                    track_name=track.title,
                    artist_name=artist_name,
                    album_name=album.title if album else None,
                    duration=track.duration,
                )

                if results:
                    # Sort: prefer synced if configured
                    if prefer_synced:
                        results.sort(key=lambda x: (1 if x.get('synced_lyrics') else 0, x.get('score', 0)), reverse=True)
                    else:
                        results.sort(key=lambda x: x.get('score', 0), reverse=True)

                    lyrics_data = results[0]
                    used_provider = provider_name
                    break

            except Exception as e:
                logger.error(f"Lyrics search error ({provider_name}) for '{track.title}': {e}")

            time.sleep(RATE_LIMIT)

        if lyrics_data and track.path:
            try:
                synced = lyrics_data.get('synced_lyrics')
                plain = lyrics_data.get('plain_lyrics')

                if synced and prefer_synced:
                    content = synced
                    ext = '.lrc'
                elif plain:
                    content = plain
                    ext = '.lrc'
                else:
                    continue

                track_base = os.path.splitext(track.path)[0]
                filepath = track_base + ext

                os.makedirs(os.path.dirname(filepath), exist_ok=True)

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)

                # Update database
                database.execute(
                    update(TableTracks)
                    .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                    .values(
                        lyrics_status='available',
                        hasLyrics='True',
                        updated_at_timestamp=datetime.now()
                    )
                )

                # Add to history
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                database.execute(
                    sqlite_insert(TableHistory).values(
                        action=1,
                        description=f"Downloaded lyrics for {track.title}",
                        metadata_type='lyrics',
                        provider=used_provider,
                        lidarrTrackId=track.lidarrTrackId,
                        lidarrArtistId=track.artistId,
                        lidarrAlbumId=track.albumId,
                        timestamp=datetime.now(),
                        metadata_path=filepath,
                    )
                )

                downloaded += 1
                logger.info(f"✓ Lyrics: '{track.title}' ({used_provider})")

            except Exception as e:
                logger.error(f"Error saving lyrics for '{track.title}': {e}")
        elif not track.path:
            logger.debug(f"Skipping '{track.title}' — no track path set")

        time.sleep(RATE_LIMIT)

    logger.info(f"Lyrics download complete: {downloaded}/{len(tracks)} downloaded")
    return downloaded


def run_metadata_downloads():
    """Main entry point for the scheduled metadata download task."""
    logger.info("Starting scheduled metadata download task...")
    event_stream(type='download_start', payload={'message': 'Metadata download task started'})

    covers_downloaded = 0
    lyrics_downloaded = 0

    try:
        covers_downloaded = download_missing_covers() or 0
    except Exception as e:
        logger.error(f"Cover art download task failed: {e}")

    try:
        lyrics_downloaded = download_missing_lyrics() or 0
    except Exception as e:
        logger.error(f"Lyrics download task failed: {e}")

    event_stream(type='download_complete', payload={
        'covers': covers_downloaded, 'lyrics': lyrics_downloaded,
        'message': f'Downloaded {covers_downloaded} covers, {lyrics_downloaded} lyrics',
    })
    logger.info("Metadata download task complete")

    # Send notification
    try:
        from lyrarr.app.notifier import send_notification
        if covers_downloaded or lyrics_downloaded:
            send_notification(
                title='Metadata Download Complete',
                message=f'Downloaded {covers_downloaded} covers and {lyrics_downloaded} lyrics.',
                metadata_type='summary',
            )
    except Exception as e:
        logger.debug(f"Notification skipped: {e}")
