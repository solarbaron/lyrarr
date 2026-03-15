# coding=utf-8

import logging
import os
from datetime import datetime

from lyrarr.app.config import settings
from lyrarr.app.database import (
    database, TableAlbums, TableTracks, TableHistory, TableProfiles,
    select, update
)
from lyrarr.metadata.covers.musicbrainz import MusicBrainzCoverProvider
from lyrarr.metadata.covers.fanart import FanartCoverProvider
from lyrarr.metadata.lyrics.lrclib import LRCLIBProvider
from lyrarr.metadata.lyrics.genius import GeniusProvider
from lyrarr.metadata.embed import embed_cover_in_files

logger = logging.getLogger(__name__)

# Provider instances
cover_providers = {
    'musicbrainz': MusicBrainzCoverProvider(),
    'fanart': FanartCoverProvider(),
}

lyrics_providers = {
    'lrclib': LRCLIBProvider(),
    'genius': GeniusProvider(),
}


def search_cover_art(album):
    """Search for cover art for an album using configured providers."""
    if not settings.metadata.covers.enabled:
        return []

    results = []
    enabled_providers = settings.metadata.covers.providers

    for provider_name in enabled_providers:
        provider = cover_providers.get(provider_name)
        if not provider:
            continue

        try:
            if provider_name == 'musicbrainz':
                provider_results = provider.search(mb_release_group_id=album.get('mbId'))
            elif provider_name == 'fanart':
                provider_results = provider.search(mb_artist_id=album.get('artistMbId'))
            else:
                provider_results = []

            results.extend(provider_results)
        except Exception as e:
            logger.error(f"Cover art search error ({provider_name}): {e}")

    return results


def search_lyrics(track):
    """Search for lyrics for a track using configured providers."""
    if not settings.metadata.lyrics.enabled:
        return []

    results = []
    enabled_providers = settings.metadata.lyrics.providers

    for provider_name in enabled_providers:
        provider = lyrics_providers.get(provider_name)
        if not provider:
            continue

        try:
            provider_results = provider.search(
                track_name=track.get('title'),
                artist_name=track.get('artistName'),
                album_name=track.get('albumTitle'),
                duration=track.get('duration'),
            )
            results.extend(provider_results)
        except Exception as e:
            logger.error(f"Lyrics search error ({provider_name}): {e}")

    # Sort by score (highest first)
    results.sort(key=lambda x: x.get('score', 0), reverse=True)

    # If prefer_synced is enabled, prioritize results with synced lyrics
    if settings.metadata.lyrics.prefer_synced:
        synced = [r for r in results if r.get('synced_lyrics')]
        plain = [r for r in results if not r.get('synced_lyrics')]
        results = synced + plain

    return results


def save_cover_art(album_id, image_data, provider_name):
    """Save cover art to disk for an album."""
    album = database.execute(
        select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
    ).scalars().first()

    if not album or not album.path:
        logger.error(f"Album {album_id} not found or has no path")
        return False

    try:
        filename = settings.metadata.covers.folder_art_filename
        filepath = os.path.join(album.path, f"{filename}.jpg")

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'wb') as f:
            f.write(image_data)

        # Update database
        database.execute(
            update(TableAlbums)
            .where(TableAlbums.lidarrAlbumId == album_id)
            .values(cover_status='available', updated_at_timestamp=datetime.now())
        )

        # Add to history
        from sqlalchemy.dialects.sqlite import insert
        database.execute(
            insert(TableHistory).values(
                action=1,
                description=f"Downloaded cover art for {album.title}",
                metadata_type='cover',
                provider=provider_name,
                lidarrAlbumId=album_id,
                lidarrArtistId=album.artistId,
                timestamp=datetime.now(),
                metadata_path=filepath,
            )
        )

        logger.info(f"Saved cover art for album '{album.title}' to {filepath}")

        # Embed in audio files if profile says so
        try:
            profile = None
            if album.profileId:
                profile = database.execute(
                    select(TableProfiles).where(TableProfiles.id == album.profileId)
                ).scalars().first()
            if not profile:
                profile = database.execute(
                    select(TableProfiles).where(TableProfiles.is_default == True)
                ).scalars().first()
            if profile and profile.embed_cover_art:
                embed_cover_in_files(album.path, image_data, profile.cover_format or 'jpg')
        except Exception as e:
            logger.error(f"Error embedding cover art for album {album_id}: {e}")

        return True

    except Exception as e:
        logger.error(f"Error saving cover art for album {album_id}: {e}")
        return False


def save_lyrics(track_id, lyrics_data, provider_name):
    """Save lyrics to disk for a track."""
    track = database.execute(
        select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
    ).scalars().first()

    if not track or not track.path:
        logger.error(f"Track {track_id} not found or has no path")
        return False

    try:
        # Determine file extension and content
        synced = lyrics_data.get('synced_lyrics')
        plain = lyrics_data.get('plain_lyrics')

        if synced and settings.metadata.lyrics.prefer_synced:
            content = synced
            ext = '.lrc'
        elif plain:
            content = plain
            ext = '.txt' if settings.metadata.lyrics.file_format == 'txt' else '.lrc'
        else:
            return False

        # Save alongside track file
        track_base = os.path.splitext(track.path)[0]
        filepath = track_base + ext

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        # Update database
        database.execute(
            update(TableTracks)
            .where(TableTracks.lidarrTrackId == track_id)
            .values(
                lyrics_status='available',
                hasLyrics=True,
                updated_at_timestamp=datetime.now()
            )
        )

        # Add to history
        from sqlalchemy.dialects.sqlite import insert
        database.execute(
            insert(TableHistory).values(
                action=1,
                description=f"Downloaded lyrics for {track.title}",
                metadata_type='lyrics',
                provider=provider_name,
                lidarrTrackId=track_id,
                lidarrArtistId=track.artistId,
                lidarrAlbumId=track.albumId,
                timestamp=datetime.now(),
                metadata_path=filepath,
            )
        )

        logger.info(f"Saved lyrics for track '{track.title}' to {filepath}")
        return True

    except Exception as e:
        logger.error(f"Error saving lyrics for track {track_id}: {e}")
        return False
