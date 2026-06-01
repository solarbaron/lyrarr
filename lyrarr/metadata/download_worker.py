# coding=utf-8

"""
Scheduled metadata download worker.
Processes albums/tracks that are missing cover art or lyrics,
respecting each album's profile settings.
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta

from sqlalchemy import or_

from lyrarr.app.database import (
    database, TableAlbums, TableTracks, TableArtists, TableProfiles, TableHistory,
    select, update
)
from lyrarr.metadata.registry import cover_providers as _cover_providers, lyrics_providers as _lyrics_providers
from lyrarr.metadata.embed import embed_cover_in_files
from lyrarr.app.event_handler import event_stream
from lyrarr.metadata.provider_utils import (
    begin_search, health_tracker, rate_limiter, search_had_transient_error,
)
from lyrarr.metadata.validation import is_instrumental_title, validate_lyrics
from lyrarr.metadata.lrc_repair import validate_lrc, repair_lrc
from lyrarr.metadata.merge import merge_provider_results
from lyrarr.metadata.lyrics_store import (
    get_blacklisted_hashes, persist_lyrics, result_is_blacklisted,
)

logger = logging.getLogger(__name__)

# Retry backoff schedule (days) for tracks where no lyrics could be found.
_BACKOFF_DAYS = [1, 3, 7, 14, 30]

# Transient failures (rate limit / timeout / provider cooldown) retry on the next
# run instead of consuming the multi-day backoff budget above. This is what stops
# the "run it over and over for days" behaviour: a track that only failed because
# of a 429 or timeout comes back quickly rather than being benched.
_TRANSIENT_RETRY = timedelta(minutes=15)

# Minimum composite match score (0..1) to accept lyrics when the user hasn't set
# a stricter score_threshold on the profile. Rejects clearly-wrong matches (a
# completely different song) that would otherwise be saved as nonsense.
# MusicBrainz-verified matches bypass this floor.
_MIN_ACCEPT_SCORE = 0.5

# Serializes all download runs so the scheduled job and a manual batch trigger
# can't process the same "missing lyrics/covers" tracks at the same time.
_downloads_lock = threading.Lock()


def downloads_in_progress():
    """True if a guarded download run currently holds the lock."""
    return _downloads_lock.locked()


def _plan_retry(retry_count, transient):
    """Decide when to retry a track that produced no usable lyrics.

    Returns (new_retry_count, retry_after).

    - Transient failures keep the same retry_count and come back in minutes, so
      rate limits / timeouts don't push a findable track onto a multi-day schedule.
    - Genuine "not found" advances the exponential day-based backoff.
    """
    if transient:
        return retry_count, datetime.now() + _TRANSIENT_RETRY
    days = _BACKOFF_DAYS[min(retry_count, len(_BACKOFF_DAYS) - 1)]
    return retry_count + 1, datetime.now() + timedelta(days=days)


def run_downloads(album_ids=None, do_covers=True, do_lyrics=True, source='scheduled'):
    """Guarded entry point for metadata downloads.

    Acquires a process-wide lock so concurrent triggers (the scheduled job and a
    manual batch download) don't run at the same time and double-process tracks.
    If a run is already active, this one is skipped rather than queued.
    """
    if not _downloads_lock.acquire(blocking=False):
        logger.warning(f"Skipping {source} download run — another run is already in progress")
        event_stream(type='download_skipped', payload={
            'source': source,
            'message': 'Another download run is already in progress',
        })
        return {'skipped': True, 'covers': 0, 'lyrics': 0}

    try:
        covers = (download_missing_covers(album_ids=album_ids) or 0) if do_covers else 0
        lyrics = (download_missing_lyrics(album_ids=album_ids) or 0) if do_lyrics else 0
        return {'skipped': False, 'covers': covers, 'lyrics': lyrics}
    finally:
        _downloads_lock.release()


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
    query = select(TableTracks).where(
        TableTracks.lyrics_status == 'missing',
        or_(
            TableTracks.lyrics_retry_after.is_(None),
            TableTracks.lyrics_retry_after <= datetime.now()
        )
    )
    if album_ids:
        query = query.where(TableTracks.albumId.in_(album_ids))
    tracks = database.execute(query).scalars().all()

    if not tracks:
        logger.info("No tracks with missing lyrics")
        return 0

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

        # Instrumental tracks (detected by title) can't have lyrics — classify and
        # skip so a wrong vocal-version match isn't fetched and saved as nonsense.
        if is_instrumental_title(track.title):
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                .values(
                    lyrics_status='instrumental',
                    hasLyrics=False,
                    lyrics_retry_count=0,
                    lyrics_retry_after=None,
                    updated_at_timestamp=datetime.now(),
                )
            )
            logger.info(f"⊘ Instrumental (title): '{track.title}' — skipping lyrics search")
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
        begin_search()  # reset the per-track transient-error flag
        providers_attempted = 0
        providers_in_cooldown = 0

        for provider_name in providers:
            provider = _lyrics_providers.get(provider_name)
            if not provider:
                continue

            # Skip unhealthy providers
            if not health_tracker.is_available(provider_name):
                providers_in_cooldown += 1
                logger.debug(f"Skipping '{provider_name}' — currently in cooldown")
                continue

            rate_limiter.wait(provider_name)
            providers_attempted += 1

            try:
                results = provider.search(
                    track_name=track.title,
                    artist_name=artist_name,
                    album_name=album.title if album else None,
                    duration=track.duration,
                    mb_recording_id=track.mbId if track.mbId else None,
                )

                if results:
                    for r in results:
                        r['_provider'] = provider_name
                    all_results.extend(results)
                # A call that returned without raising means the provider is up,
                # even if it had no match — reset its consecutive-failure streak.
                health_tracker.record_success(provider_name)

            except Exception as e:
                logger.error(f"Lyrics search error ({provider_name}) for '{track.title}': {e}")
                health_tracker.record_failure(provider_name, str(e))

        if not all_results:
            # Distinguish a genuine "no lyrics exist" from a transient failure
            # (rate limit / timeout, or every provider being in cooldown). Only
            # the former advances the multi-day backoff; transient failures retry
            # shortly so a single bad moment doesn't bench a findable track.
            transient = search_had_transient_error() or (
                providers_attempted == 0 and providers_in_cooldown > 0
            )
            retry_count = getattr(track, 'lyrics_retry_count', 0) or 0
            new_count, retry_after = _plan_retry(retry_count, transient)
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                .values(
                    lyrics_retry_count=new_count,
                    lyrics_retry_after=retry_after,
                    updated_at_timestamp=datetime.now()
                )
            )
            if transient:
                logger.info(
                    f"Transient failure for '{track.title}' (rate limit/timeout/cooldown) — "
                    f"will retry shortly without advancing backoff"
                )
            else:
                logger.debug(f"No lyrics found for '{track.title}' — retry #{new_count}")
            continue

        # --- Merge and de-duplicate cross-provider results ---
        all_results = merge_provider_results(all_results, selection_mode)

        # Drop any results the user has blacklisted for this track, so a rejected
        # wrong match is never re-selected on re-runs (regardless of provider).
        blacklisted = get_blacklisted_hashes(track.lidarrTrackId)
        if blacklisted:
            kept = [r for r in all_results if not result_is_blacklisted(r, blacklisted)]
            if len(kept) != len(all_results):
                logger.debug(
                    f"Filtered {len(all_results) - len(kept)} blacklisted result(s) for '{track.title}'"
                )
            all_results = kept

        if not all_results:
            # Every candidate was blacklisted — treat as not-found (not transient).
            retry_count = getattr(track, 'lyrics_retry_count', 0) or 0
            new_count, retry_after = _plan_retry(retry_count, False)
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                .values(
                    lyrics_retry_count=new_count,
                    lyrics_retry_after=retry_after,
                    updated_at_timestamp=datetime.now()
                )
            )
            logger.debug(f"All candidate lyrics blacklisted for '{track.title}' — retry #{new_count}")
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

        # Reject matches below the acceptance bar. The profile's score_threshold
        # (0-100) takes precedence; otherwise a built-in floor rejects clearly
        # wrong matches so odd/instrumental tracks don't get a different song's
        # lyrics. MusicBrainz-verified matches bypass the floor (authoritative).
        best_score = lyrics_data.get('score', 0) or 0
        mb_verified = bool((lyrics_data.get('match_details') or {}).get('mb_matched'))
        min_score = (eff.get('score_threshold', 0) or 0) / 100.0
        if not mb_verified:
            min_score = max(min_score, _MIN_ACCEPT_SCORE)
        if best_score < min_score:
            retry_count = getattr(track, 'lyrics_retry_count', 0) or 0
            new_count, retry_after = _plan_retry(retry_count, False)
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                .values(
                    lyrics_retry_count=new_count,
                    lyrics_retry_after=retry_after,
                    updated_at_timestamp=datetime.now()
                )
            )
            logger.debug(
                f"Skipping '{track.title}' — best match {best_score:.0%} below minimum {min_score:.0%}"
            )
            continue

        if lyrics_data and track.path:
            try:
                synced = lyrics_data.get('synced_lyrics')
                plain = lyrics_data.get('plain_lyrics')

                # Determine content based on what's available and selection mode
                if synced and (selection_mode != 'prefer_plain'):
                    content = synced
                elif plain:
                    content = plain
                elif synced:  # prefer_plain but only synced available
                    content = synced
                else:
                    continue

                # --- Quality validation ---
                validation = validate_lyrics(
                    content,
                    track_title=track.title,
                    artist_name=artist_name,
                    duration_ms=track.duration,
                    artist_language=getattr(artist, 'language_override', None) if artist else None,
                )

                if validation.get('is_instrumental'):
                    database.execute(
                        update(TableTracks)
                        .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                        .values(
                            lyrics_status='instrumental',
                            lyrics_retry_count=0,
                            lyrics_retry_after=None,
                            updated_at_timestamp=datetime.now()
                        )
                    )
                    logger.info(f"⊘ Instrumental: '{track.title}' — skipping lyrics save")
                    continue

                has_errors = any(i['severity'] == 'error' for i in validation.get('issues', []))
                if has_errors:
                    # Check if a non-truncated alternative exists
                    if validation.get('is_truncated') and len(all_results) > 1:
                        for alt in all_results[1:]:
                            alt_content = alt.get('synced_lyrics') or alt.get('plain_lyrics')
                            if alt_content:
                                alt_val = validate_lyrics(alt_content, duration_ms=track.duration)
                                if not any(i['severity'] == 'error' for i in alt_val.get('issues', [])):
                                    content = alt_content
                                    lyrics_data = alt
                                    used_provider = alt.get('_provider', 'unknown')
                                    has_errors = False
                                    logger.debug(f"Swapped truncated result for '{track.title}' to alt from {used_provider}")
                                    break

                if has_errors:
                    for issue in validation.get('issues', []):
                        if issue['severity'] == 'error':
                            logger.warning(f"Validation error for '{track.title}': {issue['message']}")
                    continue

                # Log warnings (non-blocking)
                for issue in validation.get('issues', []):
                    if issue['severity'] == 'warning':
                        logger.debug(f"Validation warning for '{track.title}': {issue['message']}")

                # --- LRC timestamp repair (for synced content) ---
                from lyrarr.metadata.language_detect import is_synced_lyrics
                is_synced_file = is_synced_lyrics(content)

                if is_synced_file:
                    lrc_validation = validate_lrc(content)
                    if not lrc_validation.get('valid'):
                        issue_types = [i['type'] for i in lrc_validation.get('issues', [])]
                        logger.debug(f"LRC repair for '{track.title}': {issue_types}")
                        content = repair_lrc(content)

                track_base = os.path.splitext(track.path)[0]

                # If a lyrics file already exists and overwrite is off, just
                # reconcile the DB status instead of refetching/replacing.
                if os.path.isfile(track_base + '.lrc') and not eff['overwrite_existing']:
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

                # Shared write path: archives any existing file, writes the .lrc,
                # sets status + history, and (optionally) detects language.
                result = persist_lyrics(
                    track, content, used_provider,
                    detect_lang=eff.get('auto_detect_language', True),
                )
                if not result:
                    logger.error(f"Failed to persist lyrics for '{track.title}'")
                    continue

                filepath = result['filepath']
                detected_lang = result['detected_language']
                is_synced_file = result['is_synced']

                downloaded += 1
                md = lyrics_data.get('match_details', {})
                score_breakdown = ''
                if md:
                    score_breakdown = f" [title={md.get('title_score', '?')} artist={md.get('artist_score', '?')} dur={md.get('duration_score', '?')}]"
                score_val = lyrics_data.get('score')
                score_str = f"{score_val:.0%}" if isinstance(score_val, (int, float)) else '?'
                logger.info(f"✓ Lyrics: '{track.title}' ({used_provider}, score={score_str}{score_breakdown}, lang={detected_lang}, synced={is_synced_file})")
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

    # --- Album coherence check ---
    if downloaded > 0:
        try:
            from lyrarr.metadata.coherence import check_album_coherence
            album_ids_processed = set(t.albumId for t in tracks if t.albumId)
            for aid in album_ids_processed:
                report = check_album_coherence(aid)
                if report.get('issues'):
                    logger.info(f"Coherence check for '{report.get('album_title', '?')}': "
                                f"{len(report['issues'])} issue(s)")
                    for issue in report['issues']:
                        logger.warning(f"  ⚠ [{issue['issue']}] '{issue['track_title']}': {issue['detail']}")
        except Exception as e:
            logger.debug(f"Coherence check skipped: {e}")

    return downloaded


def run_metadata_downloads():
    """Main entry point for the scheduled metadata download task."""
    logger.info("Starting scheduled metadata download task...")

    # Count pending items for progress tracking
    from lyrarr.app.database import func
    total_cover_count = database.execute(
        select(func.count()).select_from(TableAlbums).where(TableAlbums.cover_status == 'missing')
    ).scalar() or 0
    total_lyrics_count = database.execute(
        select(func.count()).select_from(TableTracks).where(TableTracks.lyrics_status == 'missing')
    ).scalar() or 0

    event_stream(type='download_start', payload={
        'message': 'Metadata download task started',
        'total_covers': total_cover_count,
        'total_lyrics': total_lyrics_count,
    })

    # run_downloads holds the shared lock so this can't overlap a manual batch run.
    result = run_downloads(source='scheduled')
    if result.get('skipped'):
        logger.info("Scheduled metadata download skipped — another run is in progress")
        return

    covers_downloaded = result['covers']
    lyrics_downloaded = result['lyrics']

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
