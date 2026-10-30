
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
    TableAlbums,
    TableArtists,
    TableHistory,
    TableProfiles,
    TableTracks,
    database,
    select,
    update,
)
from lyrarr.app.event_handler import event_stream
from lyrarr.metadata.cover_fetch import cover_result_matches, sniff_image
from lyrarr.metadata.embed import embed_cover_in_files
from lyrarr.metadata.lrc_repair import repair_lrc, validate_lrc
from lyrarr.metadata.lyrics_store import (
    get_blacklisted_hashes,
    persist_lyrics,
    result_is_blacklisted,
)
from lyrarr.metadata.merge import merge_provider_results
from lyrarr.metadata.provider_utils import (
    ProviderTransientError,
    begin_search,
    health_tracker,
    note_transient_error,
    rate_limiter,
    search_had_transient_error,
)
from lyrarr.metadata.registry import cover_providers as _cover_providers
from lyrarr.metadata.registry import lyrics_providers as _lyrics_providers
from lyrarr.metadata.validation import is_instrumental_title, validate_lyrics

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


def run_downloads(album_ids=None, do_covers=True, do_lyrics=True, source='scheduled',
                  ignore_backoff=False):
    """Guarded entry point for metadata downloads.

    Acquires a process-wide lock so concurrent triggers (the scheduled job and a
    manual batch download) don't run at the same time and double-process tracks.
    If a run is already active, this one is skipped rather than queued.

    ignore_backoff: retry backed-off tracks immediately — used for manual,
    explicitly-scoped requests where silently skipping would look like a no-op.
    """
    if not _downloads_lock.acquire(blocking=False):
        logger.warning(f"Skipping {source} download run — another run is already in progress")
        event_stream(type='download_skipped', payload={
            'source': source,
            'message': 'Another download run is already in progress',
        })
        return {'skipped': True, 'covers': 0, 'lyrics': 0}

    try:
        covers = (download_missing_covers(album_ids=album_ids,
                                          ignore_backoff=ignore_backoff) or 0) if do_covers else 0
        lyrics = (download_missing_lyrics(album_ids=album_ids,
                                          ignore_backoff=ignore_backoff) or 0) if do_lyrics else 0
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


def _album_track_paths(album_id):
    """File paths of an album's tracks — multi-disc albums span several folders,
    so embedding goes by track file rather than by album.path."""
    rows = database.execute(
        select(TableTracks.path).where(TableTracks.albumId == album_id)
    ).scalars().all()
    return [p for p in rows if p]


# Per-album retry schedule for covers that no provider had, persisted on
# TableAlbums so an unfindable album isn't re-queried on every run or restart.
def _cover_backed_off(album):
    return bool(album.cover_retry_after and album.cover_retry_after > datetime.now())


def _defer_cover(album, transient):
    count, retry_after = _plan_retry(album.cover_retry_count or 0, transient)
    database.execute(
        update(TableAlbums)
        .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
        .values(cover_retry_count=count, cover_retry_after=retry_after)
    )
    return count, retry_after


def _clear_cover_backoff(album):
    if album.cover_retry_count or album.cover_retry_after:
        database.execute(
            update(TableAlbums)
            .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
            .values(cover_retry_count=0, cover_retry_after=None)
        )


def find_cover(album, artist, providers):
    """Try the album's cover providers in order. Returns (image_bytes, provider) or (None, None).

    Only front covers are considered, and name-search results (Deezer, iTunes,
    TheAudioDB) must actually be this album by this artist — the first search
    hit is often a different album. Transient provider failures are flagged
    via the shared per-search state (search_had_transient_error()).
    """
    artist_name = artist.name if artist else None
    attempted = 0
    in_cooldown = 0
    for provider_name in providers:
        provider = _cover_providers.get(provider_name)
        if not provider:
            continue

        # Skip unhealthy providers
        if not health_tracker.is_available(provider_name):
            logger.debug(f"Skipping '{provider_name}' — currently in cooldown")
            in_cooldown += 1
            continue

        rate_limiter.wait(provider_name)
        attempted += 1

        try:
            results = provider.search(
                mb_release_group_id=album.mbId if album.mbId else None,
                mb_release_id=None,
                mb_artist_id=artist.mbId if artist and artist.mbId else None,
                mb_album_id=album.mbId if album.mbId else None,
                artist_name=artist_name,
                album_name=album.title,
            )
            # A call that returned means the provider is up, match or not.
            health_tracker.record_success(provider_name)

            for result in results or []:
                if result.get('type', 'front') != 'front':
                    continue  # cdart, artist art, ...
                if not cover_result_matches(result, artist_name, album.title):
                    logger.debug(
                        f"Ignoring {provider_name} cover '{result.get('title')}' by "
                        f"'{result.get('artist')}' for '{album.title}' — different album"
                    )
                    continue
                url = result.get('url') or result.get('url_large') or result.get('url_small')
                if not url:
                    continue
                img_data = provider.download(url)
                if img_data:
                    return img_data, provider_name

            # No results is not a failure — just no match

        except Exception as e:
            logger.error(f"Cover search error ({provider_name}) for '{album.title}': {e}")
            health_tracker.record_failure(provider_name, str(e))

    if attempted == 0 and in_cooldown:
        # Nothing was actually asked — every provider is cooling down.
        note_transient_error()
    return None, None


def download_missing_covers(album_ids=None, ignore_backoff=False):
    """Download cover art for albums that are missing it, based on their profile.

    Args:
        album_ids: Optional list of album IDs to scope the download. If None, all missing.
        ignore_backoff: Retry albums still inside their no-cover-found backoff.
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

        if not album.path:
            logger.debug(f"Skipping '{album.title}' — no album path set")
            continue

        # Check if cover file already exists on disk
        cover_format = eff['cover_format']
        cover_exists_on_disk = any(
            os.path.isfile(os.path.join(album.path, f"cover.{ext}"))
            for ext in dict.fromkeys([cover_format, 'jpg', 'jpeg', 'png', 'webp', 'gif'])
        )
        if cover_exists_on_disk and not eff['overwrite_existing']:
            # File exists but DB says missing — fix the DB status
            database.execute(
                update(TableAlbums)
                .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
                .values(cover_status='available', updated_at_timestamp=datetime.now())
            )
            logger.debug(f"Cover already exists on disk for '{album.title}', updated DB status")
            continue

        if not ignore_backoff and _cover_backed_off(album):
            logger.debug(f"Skipping '{album.title}' — no cover found recently, backing off")
            continue

        # Get the artist for MusicBrainz IDs
        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
        ).scalars().first()

        begin_search()  # reset the per-album transient-error flag
        cover_data, used_provider = find_cover(album, artist, providers)

        if not cover_data:
            _, retry_after = _defer_cover(album, search_had_transient_error())
            logger.debug(f"No cover found for '{album.title}' — next try after {retry_after:%Y-%m-%d %H:%M}")
            continue
        _clear_cover_backoff(album)

        try:
            # Save with the image's real type: providers mostly serve JPEG but
            # CAA has PNGs, and a PNG named cover.jpg confuses players. No image
            # library is bundled to convert, so the profile format is a preference.
            sniffed = sniff_image(cover_data)
            ext = sniffed[0] if sniffed else cover_format
            if ext != cover_format and not (ext == 'jpg' and cover_format == 'jpeg'):
                logger.debug(f"Cover for '{album.title}' is {ext}, not {cover_format} — saving as cover.{ext}")
            filepath = os.path.join(album.path, f"cover.{ext}")
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

            # Embed if profile flag is set. Existing embedded front covers are
            # only replaced when the profile allows overwriting.
            if eff['embed_cover_art']:
                try:
                    embed_cover_in_files(
                        album.path, cover_data, ext,
                        track_paths=_album_track_paths(album.lidarrAlbumId),
                        overwrite=bool(eff['overwrite_existing']),
                    )
                except Exception as e:
                    logger.error(f"Error embedding cover for '{album.title}': {e}")

        except Exception as e:
            logger.error(f"Error saving cover for '{album.title}': {e}")

    logger.info(f"Cover art download complete: {downloaded}/{len(albums)} downloaded")
    return downloaded


def collect_lyrics_results(track, artist_name, album_title, providers):
    """Run all enabled, healthy providers for a track and gather scored results.

    Shared orchestration for the missing-lyrics download and the plain→synced
    upgrade pass: handles health-skip, rate limiting, the per-track
    transient-error flag, and success/failure recording. No selection decisions.

    Returns (all_results, stats) where stats has keys attempted, in_cooldown,
    transient.
    """
    all_results = []
    begin_search()  # reset the per-track transient-error flag
    attempted = 0
    in_cooldown = 0

    for provider_name in providers:
        provider = _lyrics_providers.get(provider_name)
        if not provider:
            continue

        # Skip unhealthy providers (in cooldown after repeated failures)
        if not health_tracker.is_available(provider_name):
            in_cooldown += 1
            logger.debug(f"Skipping '{provider_name}' — currently in cooldown")
            continue

        rate_limiter.wait(provider_name)
        attempted += 1

        try:
            results = provider.search(
                track_name=track.title,
                artist_name=artist_name,
                album_name=album_title,
                duration=track.duration,
                mb_recording_id=track.mbId if track.mbId else None,
            )
            if results:
                for r in results:
                    r['_provider'] = provider_name
                all_results.extend(results)
            # A call that returned without raising means the provider is up,
            # even with no match — reset its consecutive-failure streak.
            health_tracker.record_success(provider_name)
        except ProviderTransientError as e:
            # Timeout/429/5xx: expected under load — log quietly, but still
            # count it so repeated failures put the provider in cooldown
            # instead of costing a full timeout on every remaining track.
            logger.warning(f"Lyrics provider unavailable ({provider_name}) for '{track.title}': {e}")
            note_transient_error()  # by definition — even if the provider didn't flag it
            health_tracker.record_failure(provider_name, str(e))
        except Exception as e:
            logger.error(f"Lyrics search error ({provider_name}) for '{track.title}': {e}")
            health_tracker.record_failure(provider_name, str(e))

    return all_results, {
        'attempted': attempted,
        'in_cooldown': in_cooldown,
        'transient': search_had_transient_error(),
    }


# ---------------------------------------------------------------------------
# Lyrics candidate selection (pure — no DB, unit-tested)
# ---------------------------------------------------------------------------

# In prefer_synced / prefer_plain modes, a preferred-type candidate wins over a
# better-scoring other-type one only if it's within this margin of the best
# acceptable score — preference shouldn't pick a shaky match over a sure one.
_PREFER_MARGIN = 0.2


def _min_accept_score(eff):
    """The acceptance floor: the profile's score_threshold (0-100) or the built-in floor."""
    return max((eff.get('score_threshold', 0) or 0) / 100.0, _MIN_ACCEPT_SCORE)


def is_acceptable(result, min_score):
    """Whether a scored provider result may be saved automatically.

    Results that failed a hard match gate (wrong artist, no artist, duration
    off by more than a few seconds) are never acceptable. MusicBrainz-verified
    matches bypass the score floor — the recording is identified, so a low
    fuzzy score only reflects metadata spelling — but a duration gate still
    rejects them (a different edit would be out of sync).
    """
    md = result.get('match_details') or {}
    gate = md.get('gate')
    if md.get('mb_matched'):
        return gate != 'duration_mismatch'
    if gate:
        return False
    return (result.get('score', 0) or 0) >= min_score


def _is_synced_result(r):
    return bool(r.get('synced_lyrics'))


def _is_plain_result(r):
    return bool(r.get('plain_lyrics')) and not r.get('synced_lyrics')


def rank_candidates(results, selection_mode, min_score):
    """Acceptable results, best first, honouring the selection mode.

    The floor and gates are applied first; the mode's preference then only
    chooses among acceptable results (and within _PREFER_MARGIN of the best),
    so a weak synced match can no longer outrank — and then sink — a strong
    plain one.
    """
    acceptable = [r for r in results if is_acceptable(r, min_score)]
    if not acceptable:
        return []

    def score(r):
        return r.get('score', 0) or 0

    by_score = sorted(acceptable, key=lambda r: (score(r), 1 if _is_synced_result(r) else 0), reverse=True)
    if selection_mode not in ('prefer_synced', 'prefer_plain'):
        return by_score  # best_score: highest score wins, synced is tiebreaker

    prefers = _is_synced_result if selection_mode == 'prefer_synced' else _is_plain_result
    best = score(by_score[0])
    preferred = [r for r in by_score if prefers(r) and score(r) >= best - _PREFER_MARGIN]
    return preferred + [r for r in by_score if r not in preferred]


def _content_for(result, selection_mode):
    """The text to save from a result, per selection mode — or None.

    A plain text flagged plain_truncated (Musixmatch free-tier excerpt) is
    never used.
    """
    synced = result.get('synced_lyrics')
    plain = None if result.get('plain_truncated') else result.get('plain_lyrics')
    if synced and selection_mode != 'prefer_plain':
        return synced
    return plain or synced or None


def choose_lyrics(candidates, selection_mode, track_title=None, artist_name=None,
                  duration_s=None, artist_language=None):
    """Validate ranked candidates in order and pick the first usable one.

    Returns (result, content, outcome):
      - outcome 'ok': result/content to save. A clean candidate beats one that
        only looks truncated; a truncated-looking one is the last resort.
      - 'instrumental': at least two different providers' content says "no
        lyrics" and nothing usable was found. One provider's content heuristic
        alone is never terminal (LRCLIB's explicit instrumental flag is
        handled before this).
      - 'none': nothing usable.
    """
    fallback = None
    instrumental_providers = set()

    for cand in candidates:
        content = _content_for(cand, selection_mode)
        if not content:
            continue
        validation = validate_lyrics(
            content,
            track_title=track_title,
            artist_name=artist_name,
            duration_s=duration_s,
            artist_language=artist_language,
        )
        if validation.get('is_instrumental'):
            instrumental_providers.add(cand.get('_provider') or cand.get('provider') or '?')
            continue
        issues = validation.get('issues', [])
        for issue in issues:
            if issue['severity'] == 'error':
                logger.debug(f"Rejected {cand.get('_provider', '?')} result for '{track_title}': {issue['message']}")
        if any(i['severity'] == 'error' for i in issues):
            continue
        if validation.get('is_truncated'):
            if fallback is None:
                fallback = (cand, content)
            continue
        for issue in issues:
            if issue['severity'] == 'warning':
                logger.debug(f"Validation warning for '{track_title}': {issue['message']}")
        return cand, content, 'ok'

    if fallback:
        logger.debug(f"Only a possibly-truncated result for '{track_title}' — using it")
        return fallback[0], fallback[1], 'ok'
    if len(instrumental_providers) >= 2:
        return None, None, 'instrumental'
    return None, None, 'none'


def _provider_of(result, default='unknown'):
    return result.get('_provider') or result.get('provider') or default


def _defer_track(track, transient):
    """Schedule the next lyrics search for a track that got nothing usable.

    Every "gave up on this track for now" path goes through here, so no path
    re-searches the same track every run.
    """
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
    return new_count


def _mark_instrumental(track):
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


def download_missing_lyrics(album_ids=None, ignore_backoff=False):
    """Download lyrics for tracks that are missing them, based on their album's profile.

    Args:
        album_ids: Optional list of album IDs to scope the download. If None, all missing.
        ignore_backoff: Include tracks still inside their retry-backoff window.
            Used by manual scoped requests — when a user explicitly asks for an
            album, skipping its backed-off tracks silently reads as "nothing
            happened".
    """
    query = select(TableTracks).where(TableTracks.lyrics_status == 'missing')
    if not ignore_backoff:
        query = query.where(or_(
            TableTracks.lyrics_retry_after.is_(None),
            TableTracks.lyrics_retry_after <= datetime.now()
        ))
    if album_ids:
        query = query.where(TableTracks.albumId.in_(album_ids))
    tracks = database.execute(query).scalars().all()

    if not tracks:
        logger.info("No tracks with missing lyrics")
        return 0

    logger.info(f"Processing {len(tracks)} tracks with missing lyrics...")
    downloaded = 0

    # Cache album profiles and artist names (local to this run — the run holds
    # _downloads_lock, so nothing else touches them)
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
            _mark_instrumental(track)
            logger.info(f"⊘ Instrumental (title): '{track.title}' — skipping lyrics search")
            continue

        providers = _parse_providers_list(eff['lyrics_providers'])
        if not providers:
            continue

        if not track.path:
            logger.debug(f"Skipping track '{track.title}' — no file path")
            continue

        # If a lyrics file already exists and overwrite is off, just reconcile
        # the DB status instead of searching providers for a replacement.
        track_base = os.path.splitext(track.path)[0]
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

        # Get artist name
        if track.artistId not in artist_cache:
            artist = database.execute(
                select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
            ).scalars().first()
            artist_cache[track.artistId] = artist
        artist = artist_cache[track.artistId]
        artist_name = artist.name if artist else None
        selection_mode = eff.get('lyrics_selection_mode', 'best_score')
        min_score = _min_accept_score(eff)

        # Collect results from all enabled, healthy providers
        all_results, search_stats = collect_lyrics_results(
            track, artist_name, album.title if album else None, providers
        )
        transient = search_stats['transient'] or (
            search_stats['attempted'] == 0 and search_stats['in_cooldown'] > 0
        )

        # LRCLIB can answer an exact match with "this track is instrumental"
        # (community-verified). That's terminal: classify the track instead of
        # retrying forever or accepting a wrong vocal-version match. Only trust
        # the flag when the match itself is confident.
        instrumental_marker = next(
            (r for r in all_results if r.get('instrumental') and is_acceptable(r, _MIN_ACCEPT_SCORE)),
            None,
        )
        if instrumental_marker:
            _mark_instrumental(track)
            provider_name = _provider_of(instrumental_marker, 'provider')
            logger.info(f"⊘ Instrumental ({provider_name}): '{track.title}' — no lyrics exist")
            continue
        # Markers below the confidence bar carry no lyrics — drop them so
        # selection can't pick an empty result.
        all_results = [r for r in all_results if not r.get('instrumental')]

        if not all_results:
            # Distinguish a genuine "no lyrics exist" from a transient failure
            # (rate limit / timeout, or every provider being in cooldown). Only
            # the former advances the multi-day backoff; transient failures retry
            # shortly so a single bad moment doesn't bench a findable track.
            new_count = _defer_track(track, transient)
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

        # Floor + hard gates first, then the selection mode's preference.
        # A provider that failed transiently may still have the right answer,
        # so a miss here only retries soon in that case.
        candidates = rank_candidates(all_results, selection_mode, min_score)
        if not candidates:
            new_count = _defer_track(track, transient)
            best = max((r.get('score', 0) or 0 for r in all_results), default=0)
            logger.debug(
                f"Skipping '{track.title}' — no acceptable match (best {best:.0%}, "
                f"minimum {min_score:.0%}, {len(all_results)} candidate(s)) — retry #{new_count}"
            )
            continue

        try:
            lyrics_data, content, outcome = choose_lyrics(
                candidates, selection_mode,
                track_title=track.title,
                artist_name=artist_name,
                duration_s=track.duration,
                artist_language=getattr(artist, 'language_override', None) if artist else None,
            )
            if outcome == 'instrumental':
                _mark_instrumental(track)
                logger.info(f"⊘ Instrumental (content, several providers): '{track.title}' — skipping lyrics save")
                continue
            if not lyrics_data:
                new_count = _defer_track(track, transient)
                logger.debug(f"No usable lyrics for '{track.title}' after validation — retry #{new_count}")
                continue
            used_provider = _provider_of(lyrics_data)

            # --- LRC timestamp repair (for synced content) ---
            from lyrarr.metadata.language_detect import is_synced_lyrics
            if is_synced_lyrics(content):
                lrc_validation = validate_lrc(content)
                # Large gaps alone are reported, not rewritten.
                if lrc_validation.get('needs_repair'):
                    issue_types = [i['type'] for i in lrc_validation.get('issues', [])]
                    logger.debug(f"LRC repair for '{track.title}': {issue_types}")
                    content = repair_lrc(content)

            # Shared write path: archives any existing file, writes the .lrc,
            # sets status + history, and (optionally) detects language.
            result = persist_lyrics(
                track, content, used_provider,
                source='auto',
                detect_lang=eff.get('auto_detect_language', True),
            )
            if not result:
                logger.error(f"Failed to persist lyrics for '{track.title}'")
                _defer_track(track, False)
                continue

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

            _auto_translate(track, artist, eff, content, used_provider, detected_lang)

        except Exception as e:
            logger.error(f"Error saving lyrics for '{track.title}': {e}")
            try:
                _defer_track(track, False)
            except Exception as e2:
                logger.debug(f"Could not schedule retry for '{track.title}': {e2}")

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


def _auto_translate(track, artist, eff, content, used_provider, detected_lang):
    """Translate freshly downloaded lyrics if the profile asks for it.

    The translation goes through the shared persist path as source='translated':
    the original is archived as a version (counted against the per-track cap,
    marked translated_from=<its language> so blacklisting the translation can
    also blacklist this source), and detected_language becomes the target.
    """
    auto_translate = eff.get('auto_translate', 'off')
    if auto_translate == 'off' or not detected_lang:
        return
    target_lang = eff.get('translate_target_lang', 'en')
    only_foreign = eff.get('translate_only_foreign', True)

    # Per-artist override takes priority
    if artist and getattr(artist, 'translate_target_override', None):
        target_lang = artist.translate_target_override

    if only_foreign and detected_lang == target_lang:
        return
    try:
        from lyrarr.metadata.manager import translate_lyrics_content
        translated = translate_lyrics_content(content, target_lang, auto_translate)
        if not translated:
            return
        result = persist_lyrics(
            track, translated, used_provider,
            source='translated',
            detect_lang=False,
            detected_language=target_lang,
            archive_translated_from=detected_lang,
            record_history=False,
        )
        if result:
            logger.info(f"  → Auto-translated '{track.title}' ({detected_lang} → {target_lang}, mode={auto_translate})")
    except Exception as e:
        logger.warning(f"Auto-translation failed for '{track.title}': {e}")


def run_metadata_downloads():
    """Main entry point for the scheduled metadata download task."""
    logger.info("Starting scheduled metadata download task...")

    covers_downloaded = 0
    lyrics_downloaded = 0
    complete = {'covers': 0, 'lyrics': 0, 'message': 'Metadata download task finished'}
    try:
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
            complete.update(skipped=True, message='Skipped — another download run is in progress')
            return

        covers_downloaded = result['covers']
        lyrics_downloaded = result['lyrics']
        complete.update(
            covers=covers_downloaded, lyrics=lyrics_downloaded,
            message=f'Downloaded {covers_downloaded} covers, {lyrics_downloaded} lyrics',
        )
    except Exception as e:
        logger.error(f"Scheduled metadata download failed: {e}")
        complete.update(error=str(e), message=f'Metadata download task failed: {e}')
        return
    finally:
        # Always close the progress started by download_start, even when the
        # run is skipped or fails, so the UI never hangs.
        event_stream(type='download_complete', payload=complete)

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


def upgrade_unsynced_lyrics(album_ids=None, track_ids=None):
    """Re-search providers for tracks that have plain lyrics and upgrade to synced.

    Only replaces a plain file when a synced match is found that passes the score
    floor and gates and isn't blacklisted — never downgrades. Honors each album's
    profile (skips albums whose profile prefers plain lyrics or has lyrics
    disabled) and uses the same backoff as the missing flow so it doesn't
    re-search every run.

    Never touches lyrics a person chose or made (lyrics_source 'manual',
    'translated', 'generated'): only 'auto' downloads, and pre-existing files
    of unknown origin (NULL) when the profile allows overwriting existing
    lyrics or the user asked for these specific tracks.

    When track_ids is given (an explicit user request for specific tracks), the
    backoff window is ignored so the retry schedule can't suppress the upgrade.
    """
    conds = [
        TableTracks.lyrics_status == 'available',
        TableTracks.is_synced == False,
        or_(TableTracks.lyrics_source.is_(None), TableTracks.lyrics_source == 'auto'),
    ]
    if track_ids:
        conds.append(TableTracks.lidarrTrackId.in_(track_ids))
    else:
        # Only the scheduled/bulk pass honors the backoff; explicit track
        # requests below bypass it.
        conds.append(or_(
            TableTracks.lyrics_retry_after.is_(None),
            TableTracks.lyrics_retry_after <= datetime.now(),
        ))
    query = select(TableTracks).where(*conds)
    if album_ids:
        query = query.where(TableTracks.albumId.in_(album_ids))
    tracks = database.execute(query).scalars().all()

    if not tracks:
        logger.info("No unsynced lyrics to upgrade")
        return 0

    logger.info(f"Checking {len(tracks)} unsynced track(s) for a synced upgrade...")
    upgraded = 0
    album_cache = {}
    artist_cache = {}

    for track in tracks:
        if track.albumId not in album_cache:
            album_cache[track.albumId] = database.execute(
                select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
            ).scalars().first()
        album = album_cache[track.albumId]
        if not album:
            continue

        profile = _get_profile(album.profileId)
        eff = _effective_settings(album, profile)
        # Only upgrade when lyrics are enabled and the profile isn't plain-only.
        if not eff['download_lyrics'] or eff.get('lyrics_selection_mode') == 'prefer_plain':
            continue
        if is_instrumental_title(track.title):
            continue
        # Unknown-origin lyrics may be the user's own file — the scheduled pass
        # replaces them only when the profile says existing lyrics may be overwritten.
        if getattr(track, 'lyrics_source', None) is None and not track_ids and not eff['overwrite_existing']:
            continue

        providers = _parse_providers_list(eff['lyrics_providers'])
        if not providers:
            continue

        if track.artistId not in artist_cache:
            artist_cache[track.artistId] = database.execute(
                select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
            ).scalars().first()
        artist = artist_cache[track.artistId]
        artist_name = artist.name if artist else None

        all_results, search_stats = collect_lyrics_results(
            track, artist_name, album.title if album else None, providers
        )

        # Drop blacklisted candidates before picking the best synced one.
        blacklisted = get_blacklisted_hashes(track.lidarrTrackId)
        if blacklisted:
            all_results = [r for r in all_results if not result_is_blacklisted(r, blacklisted)]

        # Same acceptance floor and gates as the download flow; synced only.
        min_score = _min_accept_score(eff)
        synced = [r for r in all_results if r.get('synced_lyrics') and is_acceptable(r, min_score)]
        best, content, _ = choose_lyrics(
            rank_candidates(synced, 'prefer_synced', min_score), 'prefer_synced',
            track_title=track.title, artist_name=artist_name, duration_s=track.duration,
        )
        if best and not best.get('synced_lyrics'):
            best = None

        if not best:
            # No acceptable synced upgrade — back off so we don't re-search every run.
            _defer_track(track, search_stats['transient'])
            continue

        if validate_lrc(content).get('needs_repair'):
            content = repair_lrc(content)

        provider_name = _provider_of(best, 'upgrade')
        result = persist_lyrics(
            track, content, provider_name,
            source='auto',
            detect_lang=eff.get('auto_detect_language', True),
        )
        if not result:
            _defer_track(track, False)
            continue

        upgraded += 1
        logger.info(
            f"⤴ Upgraded to synced: '{track.title}' "
            f"({provider_name}, score={(best.get('score') or 0):.0%})"
        )
        event_stream(type='download_progress', payload={
            'metadata_type': 'lyrics', 'title': track.title,
            'provider': provider_name, 'is_synced': True, 'upgraded': True,
        })

    logger.info(f"Lyrics upgrade complete: {upgraded}/{len(tracks)} upgraded")
    return upgraded


def run_lyrics_upgrade(album_ids=None, track_ids=None, source='scheduled'):
    """Guarded entry point for the plain→synced upgrade pass.

    Shares the download lock so it can't run at the same time as a download run
    (or another upgrade) and double-hit providers. Skipped (not queued) if a run
    is already active.
    """
    if not _downloads_lock.acquire(blocking=False):
        logger.warning(f"Skipping {source} lyrics upgrade — a run is already in progress")
        return {'skipped': True, 'upgraded': 0}
    try:
        upgraded = upgrade_unsynced_lyrics(album_ids=album_ids, track_ids=track_ids) or 0
        return {'skipped': False, 'upgraded': upgraded}
    finally:
        _downloads_lock.release()
