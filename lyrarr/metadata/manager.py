# coding=utf-8

import logging
import os
from datetime import datetime

from lyrarr.app.config import settings
from lyrarr.app.database import (
    database, TableAlbums, TableTracks, TableHistory, TableProfiles,
    select, update
)
from lyrarr.metadata.registry import cover_providers, lyrics_providers
from lyrarr.metadata.embed import embed_cover_in_files

logger = logging.getLogger(__name__)


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
            # Build kwargs based on what this provider accepts
            kwargs = {}
            if provider_name == 'musicbrainz':
                kwargs['mb_release_group_id'] = album.get('mbId')
            elif provider_name == 'fanart':
                kwargs['mb_artist_id'] = album.get('artistMbId')
            else:
                # Deezer, iTunes, TheAudioDB use artist + album name search
                kwargs['artist_name'] = album.get('artistName')
                kwargs['album_name'] = album.get('title')

            provider_results = provider.search(**kwargs)
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
    """Save lyrics to disk as .lrc for a track.

    - Archives the previous version in the database (TableLyricsVersions)
    - Always saves as .lrc; sync status determined from content
    """
    from lyrarr.app.database import TableLyricsVersions

    track = database.execute(
        select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
    ).scalars().first()

    if not track or not track.path:
        logger.error(f"Track {track_id} not found or has no path")
        return False

    try:
        # Determine content (prefer synced over plain)
        synced = lyrics_data.get('synced_lyrics')
        plain = lyrics_data.get('plain_lyrics')

        if synced:
            content = synced
        elif plain:
            content = plain
        else:
            return False

        # Always save as .lrc — sync status is determined from content
        from lyrarr.metadata.language_detect import is_synced_lyrics
        lyrics_type = 'synced' if is_synced_lyrics(content) else 'plain'

        track_base = os.path.splitext(track.path)[0]
        filepath = track_base + '.lrc'

        # Archive previous version if a lyrics file already exists
        old_path = track_base + '.lrc'
        if os.path.isfile(old_path):
            try:
                with open(old_path, 'r', encoding='utf-8', errors='ignore') as f:
                    old_content = f.read()

                if old_content.strip():
                    old_type = 'synced' if is_synced_lyrics(old_content) else 'plain'
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                    database.execute(
                        sqlite_insert(TableLyricsVersions).values(
                            lidarrTrackId=track_id,
                            content=old_content,
                            lyrics_type=old_type,
                            provider=provider_name,
                            timestamp=datetime.now(),
                        )
                    )
                    logger.debug(f"Archived previous lyrics for track {track_id}")
            except Exception as e:
                logger.warning(f"Failed to archive old lyrics: {e}")

        # Write the new lyrics file
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        # Language detection & synced status
        from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
        detected_lang = detect_language(content)
        synced_flag = is_synced_lyrics(content)

        # Update database
        database.execute(
            update(TableTracks)
            .where(TableTracks.lidarrTrackId == track_id)
            .values(
                lyrics_status='available',
                hasLyrics=True,
                detected_language=detected_lang,
                is_synced=synced_flag,
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

        logger.info(f"Saved lyrics for track '{track.title}' to {filepath} (lang={detected_lang}, synced={synced_flag})")
        return True

    except Exception as e:
        logger.error(f"Error saving lyrics for track {track_id}: {e}")
        return False


def translate_lyrics_content(content, target_lang, mode='replace'):
    """Translate lyrics text to a target language.

    Args:
        content: Lyrics text (may include LRC timestamps)
        target_lang: Target ISO 639-1 language code
        mode: 'replace' (replace original) or 'dual' (interleave original + translation)

    Returns:
        Translated content string, or None on failure.
    """
    import re

    if not content or not content.strip():
        return None

    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source='auto', target=target_lang)

        lrc_ts = re.compile(r'(\[\d{1,2}:\d{2}[.:]\d{2,3}\])\s*(.*)')
        lines = content.split('\n')
        translated_lines = []
        original_lines = []

        for line in lines:
            stripped = line.strip()
            m = lrc_ts.match(stripped)

            if m:
                tag, text = m.group(1), m.group(2)
                if text:
                    try:
                        trans = translator.translate(text)
                    except Exception:
                        trans = text
                    translated_lines.append(f"{tag} {trans}")
                    original_lines.append(stripped)
                else:
                    translated_lines.append(stripped)
                    original_lines.append(stripped)
            elif stripped:
                try:
                    trans = translator.translate(stripped)
                except Exception:
                    trans = stripped
                translated_lines.append(trans)
                original_lines.append(stripped)
            else:
                translated_lines.append('')
                original_lines.append('')

        if mode == 'dual':
            dual = []
            for orig, trans in zip(original_lines, translated_lines):
                if orig.strip():
                    dual.append(orig)
                    if trans != orig:
                        dual.append(trans)
                else:
                    dual.append('')
            return '\n'.join(dual)

        return '\n'.join(translated_lines)

    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return None

