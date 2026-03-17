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
from lyrarr.metadata.registry import cover_providers as _cover_providers, lyrics_providers as _lyrics_providers
from lyrarr.metadata.embed import embed_cover_in_files
from lyrarr.app.event_handler import event_stream
from lyrarr.metadata.provider_utils import rate_limiter, health_tracker

logger = logging.getLogger(__name__)


def _get_profile(profile_id):
    """Get a profile by ID, or fall back to the default profile.
    Returns None if no profile is assigned and no default exists.
    """
    if profile_id:
        profile = database.execute(
            select(TableProfiles).where(TableProfiles.id == profile_id)
        ).scalars().first()
        if profile:
            return profile

    # Fall back to default
    return database.execute(
        select(TableProfiles).where(TableProfiles.is_default == True)
    ).scalars().first()


def _effective_settings(album, profile):
    """Merge album overrides with profile defaults. Album overrides take priority.
    If no profile exists, everything is disabled — album is skipped.
    """
    if not profile:
        return {
            'download_covers': False,
            'download_lyrics': False,
            'cover_format': 'jpg',
            'prefer_synced_lyrics': True,
            'lyrics_selection_mode': 'best_score',
            'auto_detect_language': True,
            'auto_translate': 'off',
            'translate_target_lang': 'en',
            'translate_only_foreign': True,
            'cover_providers': '[]',
            'lyrics_providers': '[]',
            'overwrite_existing': False,
            'embed_cover_art': False,
        }

    def _override(override_val, profile_val):
        """Use album override if explicitly set (not None), otherwise profile default."""
        return override_val if override_val is not None else profile_val

    return {
        'download_covers': _override(album.override_download_covers, profile.download_covers),
        'download_lyrics': _override(album.override_download_lyrics, profile.download_lyrics),
        'cover_format': _override(album.override_cover_format, profile.cover_format),
        'prefer_synced_lyrics': _override(album.override_prefer_synced, profile.prefer_synced_lyrics),
        'lyrics_selection_mode': getattr(profile, 'lyrics_selection_mode', 'best_score') or 'best_score',
        'auto_detect_language': getattr(profile, 'auto_detect_language', True),
        'auto_translate': getattr(profile, 'auto_translate', 'off') or 'off',
        'translate_target_lang': getattr(profile, 'translate_target_lang', 'en') or 'en',
        'translate_only_foreign': getattr(profile, 'translate_only_foreign', True),
        'score_threshold': getattr(profile, 'score_threshold', 0) or 0,
        'cover_providers': profile.cover_providers or '["musicbrainz","deezer","itunes","fanart","theaudiodb"]',
        'lyrics_providers': profile.lyrics_providers or '["lrclib","musixmatch","netease","genius"]',
        'overwrite_existing': profile.overwrite_existing or False,
        'embed_cover_art': profile.embed_cover_art or False,
    }


def _parse_providers_list(providers_str):
    """Parse a JSON string list of providers, e.g. '["musicbrainz","fanart"]'."""
    try:
        return json.loads(providers_str)
    except (json.JSONDecodeError, TypeError):
        return []


def download_missing_covers(album_ids=None):
    """Download cover art for albums that are missing it, based on their profile.

    Args:
        album_ids: Optional list of album IDs to scope the download. If None, all missing.
    """
    query = select(TableAlbums).where(TableAlbums.cover_status == 'missing')
    if album_ids:
        query = query.where(TableAlbums.lidarrAlbumId.in_(album_ids))
    albums = database.execute(query).scalars().all()

    if not albums:
        logger.info("No albums with missing covers")
        return

    logger.info(f"Processing {len(albums)} albums with missing covers...")
    downloaded = 0

    for album in albums:
        profile = _get_profile(album.profileId)
        eff = _effective_settings(album, profile)
        if not eff['download_covers']:
            if not profile:
                logger.debug(f"Skipping '{album.title}' — no profile assigned")
            else:
                logger.debug(f"Skipping '{album.title}' — covers disabled in profile '{profile.name}'")
            continue

        providers = _parse_providers_list(eff['cover_providers'])
        if not providers:
            continue

        # Check if cover file already exists on disk
        if album.path:
            cover_format = eff['cover_format']
            existing_path = os.path.join(album.path, f"cover.{cover_format}")
            # Also check other common extensions
            cover_exists_on_disk = os.path.isfile(existing_path)
            if not cover_exists_on_disk:
                for ext in ['jpg', 'png', 'webp']:
                    if os.path.isfile(os.path.join(album.path, f"cover.{ext}")):
                        cover_exists_on_disk = True
                        break

            if cover_exists_on_disk and not eff['overwrite_existing']:
                # File exists but DB says missing — fix the DB status
                database.execute(
                    update(TableAlbums)
                    .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
                    .values(cover_status='available', updated_at_timestamp=datetime.now())
                )
                logger.debug(f"Cover already exists on disk for '{album.title}', updated DB status")
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

            # Skip unhealthy providers
            if not health_tracker.is_available(provider_name):
                logger.debug(f"Skipping '{provider_name}' — currently in cooldown")
                continue

            rate_limiter.wait(provider_name)

            try:
                results = provider.search(
                    mb_release_group_id=album.mbId if album.mbId else None,
                    mb_release_id=None,
                    mb_artist_id=artist.mbId if artist and artist.mbId else None,
                    mb_album_id=album.mbId if album.mbId else None,
                    artist_name=artist.name if artist else None,
                    album_name=album.title,
                )

                if results:
                    # Try to download the first result
                    for result in results:
                        url = result.get('url') or result.get('url_large') or result.get('url_small')
                        if url:
                            img_data = provider.download(url)
                            if img_data:
                                cover_data = img_data
                                used_provider = provider_name
                                health_tracker.record_success(provider_name)
                                break

                if cover_data:
                    break

                # No results is not a failure — just no match

            except Exception as e:
                logger.error(f"Cover search error ({provider_name}) for '{album.title}': {e}")
                health_tracker.record_failure(provider_name, str(e))

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
                if eff['embed_cover_art']:
                    try:
                        embed_cover_in_files(album.path, cover_data, cover_format)
                    except Exception as e:
                        logger.error(f"Error embedding cover for '{album.title}': {e}")

            except Exception as e:
                logger.error(f"Error saving cover for '{album.title}': {e}")
        elif not album.path:
            logger.debug(f"Skipping '{album.title}' — no album path set")


    logger.info(f"Cover art download complete: {downloaded}/{len(albums)} downloaded")
    return downloaded


def download_missing_lyrics(album_ids=None):
    """Download lyrics for tracks that are missing them, based on their album's profile.

    Args:
        album_ids: Optional list of album IDs to scope the download. If None, all missing.
    """
    query = select(TableTracks).where(TableTracks.lyrics_status == 'missing')
    if album_ids:
        query = query.where(TableTracks.albumId.in_(album_ids))
    tracks = database.execute(query).scalars().all()

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
        if not eff['download_lyrics']:
            if not profile:
                logger.debug(f"Skipping track '{track.title}' — album has no profile assigned")
            else:
                logger.debug(f"Skipping track '{track.title}' — lyrics disabled in profile '{profile.name}'")
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
        selection_mode = eff.get('lyrics_selection_mode', 'best_score')

        # Collect results from ALL providers (for best_score mode)
        all_results = []

        for provider_name in providers:
            provider = _lyrics_providers.get(provider_name)
            if not provider:
                continue

            # Skip unhealthy providers
            if not health_tracker.is_available(provider_name):
                logger.debug(f"Skipping '{provider_name}' — currently in cooldown")
                continue

            rate_limiter.wait(provider_name)

            try:
                results = provider.search(
                    track_name=track.title,
                    artist_name=artist_name,
                    album_name=album.title if album else None,
                    duration=track.duration,
                )

                if results:
                    for r in results:
                        r['_provider'] = provider_name
                    all_results.extend(results)
                    health_tracker.record_success(provider_name)

            except Exception as e:
                logger.error(f"Lyrics search error ({provider_name}) for '{track.title}': {e}")
                health_tracker.record_failure(provider_name, str(e))

        if not all_results:
            continue

        # Sort based on selection mode
        if selection_mode == 'prefer_synced':
            # Synced always wins, then sort by score
            all_results.sort(
                key=lambda x: (1 if x.get('synced_lyrics') else 0, x.get('score', 0)),
                reverse=True
            )
        elif selection_mode == 'prefer_plain':
            # Plain always wins, then sort by score
            all_results.sort(
                key=lambda x: (1 if x.get('plain_lyrics') and not x.get('synced_lyrics') else 0, x.get('score', 0)),
                reverse=True
            )
        else:
            # best_score (default): highest score wins, synced is tiebreaker
            all_results.sort(
                key=lambda x: (x.get('score', 0), 1 if x.get('synced_lyrics') else 0),
                reverse=True
            )

        lyrics_data = all_results[0]
        used_provider = lyrics_data.get('_provider', 'unknown')

        # Score threshold: reject if best result is below minimum
        score_threshold = eff.get('score_threshold', 0)
        if score_threshold > 0 and lyrics_data.get('score', 0) < score_threshold:
            logger.debug(f"Skipping '{track.title}' — best score {lyrics_data.get('score', 0)} below threshold {score_threshold}")
            continue

        if lyrics_data and track.path:
            try:
                synced = lyrics_data.get('synced_lyrics')
                plain = lyrics_data.get('plain_lyrics')

                # Determine content based on what's available and selection mode
                if synced and (selection_mode != 'prefer_plain'):
                    content = synced
                    ext = '.lrc'
                    is_synced_file = True
                elif plain:
                    content = plain
                    ext = '.txt'
                    is_synced_file = False
                elif synced:  # prefer_plain but only synced available
                    content = synced
                    ext = '.lrc'
                    is_synced_file = True
                else:
                    continue

                track_base = os.path.splitext(track.path)[0]
                filepath = track_base + ext

                # Check if lyrics file already exists on disk
                lrc_exists = os.path.isfile(track_base + '.lrc')
                txt_exists = os.path.isfile(track_base + '.txt')
                if (lrc_exists or txt_exists) and not eff['overwrite_existing']:
                    # File exists but DB says missing — fix the DB status
                    database.execute(
                        update(TableTracks)
                        .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                        .values(
                            lyrics_status='available',
                            hasLyrics=True,
                            updated_at_timestamp=datetime.now()
                        )
                    )
                    logger.debug(f"Lyrics already exist on disk for '{track.title}', updated DB status")
                    continue

                # Remove old lyrics file with different extension
                for old_ext in ['.lrc', '.txt']:
                    old_path = track_base + old_ext
                    if os.path.isfile(old_path) and old_path != filepath:
                        try:
                            os.remove(old_path)
                        except Exception:
                            pass

                os.makedirs(os.path.dirname(filepath), exist_ok=True)

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)

                # Language detection
                detected_lang = None
                if eff.get('auto_detect_language', True):
                    from lyrarr.metadata.language_detect import detect_language
                    detected_lang = detect_language(content)

                # Update database
                database.execute(
                    update(TableTracks)
                    .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                    .values(
                        lyrics_status='available',
                        hasLyrics=True,
                        is_synced=is_synced_file,
                        detected_language=detected_lang,
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
                logger.info(f"✓ Lyrics: '{track.title}' ({used_provider}, score={lyrics_data.get('score', '?')}, lang={detected_lang}, synced={is_synced_file})")
                event_stream(type='download_progress', payload={
                    'metadata_type': 'lyrics', 'title': track.title, 'provider': used_provider,
                    'language': detected_lang, 'is_synced': is_synced_file,
                })

                # Auto-translate if configured
                auto_translate = eff.get('auto_translate', 'off')
                if auto_translate != 'off' and detected_lang:
                    target_lang = eff.get('translate_target_lang', 'en')
                    only_foreign = eff.get('translate_only_foreign', True)

                    # Per-artist override takes priority
                    if artist and getattr(artist, 'translate_target_override', None):
                        target_lang = artist.translate_target_override

                    should_translate = not only_foreign or (detected_lang != target_lang)
                    if should_translate:
                        try:
                            from lyrarr.metadata.manager import translate_lyrics_content
                            translated = translate_lyrics_content(
                                content, target_lang, auto_translate
                            )
                            if translated:
                                # Cache original as a version before overwriting
                                from lyrarr.app.database import TableLyricsVersions
                                from sqlalchemy.dialects.sqlite import insert as ver_insert
                                database.execute(
                                    ver_insert(TableLyricsVersions).values(
                                        lidarrTrackId=track.lidarrTrackId,
                                        content=content,
                                        lyrics_type='synced' if is_synced_file else 'plain',
                                        provider=used_provider,
                                        translated_from=detected_lang,
                                        timestamp=datetime.now(),
                                    )
                                )
                                with open(filepath, 'w', encoding='utf-8') as f:
                                    f.write(translated)
                                logger.info(f"  → Auto-translated '{track.title}' ({detected_lang} → {target_lang}, mode={auto_translate})")
                        except Exception as e:
                            logger.warning(f"Auto-translation failed for '{track.title}': {e}")

            except Exception as e:
                logger.error(f"Error saving lyrics for '{track.title}': {e}")



    logger.info(f"Lyrics download complete: {downloaded}/{len(tracks)} downloaded")
    return downloaded


def run_metadata_downloads():
    """Main entry point for the scheduled metadata download task."""
    logger.info("Starting scheduled metadata download task...")

    # Count pending items for progress tracking
    total_covers = database.execute(
        select(TableAlbums).where(TableAlbums.cover_status == 'missing')
    ).scalars().all()
    total_lyrics = database.execute(
        select(TableTracks).where(TableTracks.lyrics_status == 'missing')
    ).scalars().all()

    event_stream(type='download_start', payload={
        'message': 'Metadata download task started',
        'total_covers': len(total_covers),
        'total_lyrics': len(total_lyrics),
    })

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
