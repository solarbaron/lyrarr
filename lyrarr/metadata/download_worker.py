
"""
Scheduled metadata download worker.

Processes albums/tracks that are missing cover art or lyrics, respecting each
album's profile settings. The lyrics side implements the instrumental policy
and decision procedure of PLAN §2 (run_lyrics_pipeline):

  fresh row → user override → audio present → existing sidecar → embedded
  tags → title → provider search (strict, track-context acceptance) →
  save / replace / adopt / classify / hint / defer.

Real (vocal) songs must never be flagged instrumental: only a STRONG title
qualifier, the user, or a clean multi-source consensus with a duration-
verified anchor can classify a track, and any sign of vocal lyrics (a lyrics
sidecar, substantive embedded lyrics, the user's "never") vetoes it.
"""

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import or_

from lyrarr.app import event_handler as _events
from lyrarr.app.database import (
    HISTORY_ACTION_UPGRADE,
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
from lyrarr.metadata.cover_fetch import (
    album_dir_writable,
    cover_result_matches,
    find_cover_files,
    sniff_image,
)
from lyrarr.metadata.embed import embed_cover_in_files, read_embedded_artist, read_embedded_lyrics
from lyrarr.metadata.language_detect import is_synced_lyrics
from lyrarr.metadata.lrc_repair import repair_lrc, strip_to_plain, validate_lrc
from lyrarr.metadata.lyrics_store import (
    SIDECAR_UNKNOWN,
    adopt_sidecar,
    atomic_write_bytes,
    audio_present,
    backfill_content_hash,
    candidate_hashes,
    clear_replace_request,
    content_hash,
    get_blacklisted_hashes,
    library_reachability,
    persist_lyrics,
    persist_lyrics_result,
    read_sidecar,
    read_sidecar_kind,
    record_instrumental_hint,
    result_is_blacklisted,
    set_instrumental,
    sidecar_path,
    text_is_blacklisted,
)
from lyrarr.metadata.lyrics_text import (
    classify_lyrics_text,
    instrumental_qualifier_kind,
    instrumental_title_tier,
    is_bare_instrumental_title,
    is_junk_line,
    is_substantive_lyrics,
    needs_album_context,
    strip_credit_blocks,
    strip_instrumental_qualifier,
    version_tags,
)
from lyrarr.metadata.merge import lyrics_similarity, merge_provider_results, result_members
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
from lyrarr.metadata.validation import is_instrumental_title, synced_fits_duration, validate_lyrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Retry backoff schedule (days) for tracks where no lyrics could be found.
_BACKOFF_DAYS = [1, 3, 7, 14, 30]

# Transient failures (rate limit / timeout / provider cooldown) retry soon
# instead of consuming the multi-day budget, up to _TRANSIENT_CAP times in a
# row; then the day backoff advances anyway (PLAN §3.6).
_TRANSIENT_RETRY = timedelta(minutes=15)
_TRANSIENT_CAP = 6
# A miss while some provider was cooling down (it might have had the lyrics).
_COOLDOWN_RETRY = timedelta(hours=6)
_COOLDOWN_CAP = 3
# Audio file not reachable for a track (its root is fine): check again later.
_NO_AUDIO_RETRY = timedelta(hours=6)
# "Likely instrumental" hints come back no sooner than this.
_HINT_MIN_DAYS = 14

# Minimum composite match score (0..1) to accept lyrics when the user hasn't
# set a stricter score_threshold. Together with the title gate in score_result
# this rejects same-artist wrong songs. MusicBrainz-verified matches bypass it.
_MIN_ACCEPT_SCORE = 0.65

# Replace-mode rows (audit "re-search") processed per pass, oldest request first.
REPLACE_PER_PASS = 50

# Album-context rule (§2.7) and instrumental votes (§2.1).
_ALBUM_SIMILARITY_MIN = 85
_ALBUM_CONTEXT_MAX_DIFF_S = 2.0
_MARKER_MAX_DIFF_S = 2.0
_VOTE_MAX_DIFF_S = 3.0
_VOTE_TITLE_MIN = 90
_MARKER_TITLE_MIN = 90
# After instrumental content was seen, a vocal candidate needs this title score.
_AFTER_INSTRUMENTAL_TITLE_MIN = 85
# Version tags whose presence on one side only means different content.
_CONTENT_TAGS = frozenset({'intro', 'outro', 'interlude', 'skit', 'reprise'})

# Replace mode: content this similar to the file is "the same lyrics".
_SAME_AS_FILE = 0.9
# Upgrade pass: the synced candidate must be these lyrics (plain-text equivalence).
_UPGRADE_MIN_EQUIVALENCE = 0.6

# Serializes all download runs so the scheduled job, a manual batch trigger
# and the audit can't process the same tracks at the same time.
_downloads_lock = threading.Lock()


def downloads_in_progress():
    """True if a guarded download run currently holds the lock."""
    return _downloads_lock.locked()


@contextmanager
def exclusive_downloads(timeout_s: float = 0):
    """Hold the download lock (waiting up to timeout_s); yields whether it was acquired.

    Used by the audit so no worker pass archives or rewrites files while a
    repair runs. The body runs either way; check the yielded flag.
    """
    if timeout_s and timeout_s > 0:
        acquired = _downloads_lock.acquire(timeout=timeout_s)
    else:
        acquired = _downloads_lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _downloads_lock.release()


def _emit_v2(type, payload):
    """download_scope / download_item: only for the 1.7 frontend (PLAN §1.6)."""
    if not getattr(_events, 'FRONTEND_EVENTS_V2', False):
        return
    try:
        event_stream(type=type, payload=payload)
    except Exception as e:
        logger.debug(f"event {type} failed: {e}")


def _plan_retry(retry_count, transient):
    """Next retry for a cover that no provider had: (new_retry_count, retry_after).

    Transient failures keep the count and come back in minutes; a genuine
    "not found" advances the day-based backoff.
    """
    if transient:
        return retry_count, datetime.now() + _TRANSIENT_RETRY
    days = _BACKOFF_DAYS[min(retry_count, len(_BACKOFF_DAYS) - 1)]
    return retry_count + 1, datetime.now() + timedelta(days=days)


def _advance(count, now, min_days=None):
    days = _BACKOFF_DAYS[min(count, len(_BACKOFF_DAYS) - 1)]
    if min_days:
        days = max(days, min_days)
    return count + 1, now + timedelta(days=days)


def run_downloads(album_ids=None, do_covers=True, do_lyrics=True, source='scheduled',
                  ignore_backoff=False, track_ids=None):
    """Guarded entry point for metadata downloads.

    Acquires a process-wide lock so concurrent triggers (the scheduled job, a
    manual batch download, an audit repair) don't run at the same time and
    double-process tracks. If a run is already active, this one is skipped
    rather than queued.

    ignore_backoff / track_ids: an explicit request — backed-off tracks are
    retried now (and a miss doesn't escalate their backoff, §3.6).
    """
    if not _downloads_lock.acquire(blocking=False):
        logger.warning(f"Skipping {source} download run — another run is already in progress")
        event_stream(type='download_skipped', payload={
            'source': source,
            'message': 'Another download run is already in progress',
        })
        return {'skipped': True, 'covers': 0, 'lyrics': 0}

    try:
        cover_albums = album_ids
        if track_ids and not album_ids and do_covers:
            cover_albums = sorted({
                a for a in database.execute(
                    select(TableTracks.albumId).where(TableTracks.lidarrTrackId.in_(track_ids))
                ).scalars().all() if a is not None
            }) or None
        covers = 0
        if do_covers and (cover_albums or not track_ids):
            covers = download_missing_covers(album_ids=cover_albums, ignore_backoff=ignore_backoff) or 0
        lyrics = (download_missing_lyrics(album_ids=album_ids, ignore_backoff=ignore_backoff,
                                          track_ids=track_ids) or 0) if do_lyrics else 0
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


def _note_reachability(reach):
    """One health alert per unreachable library root (transitions only)."""
    try:
        from lyrarr.utilities.health import note_library_reachability
        note_library_reachability(reach)
    except Exception as e:
        logger.debug(f"Could not record library reachability: {e}")


# ---------------------------------------------------------------------------
# Covers
# ---------------------------------------------------------------------------

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

    Albums under an unreachable library root are skipped without touching
    their backoff, and nothing is ever written outside an existing album
    folder (no directories are created; an album that points at its artist
    folder gets no cover).

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
        return 0

    reach = library_reachability(scope_paths=[a.path for a in albums if a.path])
    _note_reachability(reach)
    skipped_roots = [a for a in albums if a.path and not reach.ok_for(a.path)]
    if skipped_roots:
        logger.warning(f"Skipping {len(skipped_roots)} album(s) whose music folder is not reachable")
    albums = [a for a in albums if not (a.path and not reach.ok_for(a.path))]

    logger.info(f"Processing {len(albums)} albums with missing covers...")
    _emit_v2('download_scope', {'metadata_type': 'covers', 'total': len(albums)})
    downloaded = 0
    artist_cache = {}

    for album in albums:
        outcome = _download_album_cover(album, ignore_backoff, artist_cache)
        if outcome == 'saved':
            downloaded += 1
        _emit_v2('download_item', {'metadata_type': 'covers', 'title': album.title, 'outcome': outcome})

    logger.info(f"Cover art download complete: {downloaded}/{len(albums)} downloaded")
    return downloaded


def _download_album_cover(album, ignore_backoff, artist_cache):
    profile = _get_profile(album.profileId)
    eff = _effective_settings(album, profile)
    if not eff['download_covers']:
        if not profile:
            logger.debug(f"Skipping '{album.title}' — no profile assigned")
        else:
            logger.debug(f"Skipping '{album.title}' — covers disabled in profile '{profile.name}'")
        return 'skipped'

    providers = _parse_providers_list(eff['cover_providers'])
    if not providers:
        return 'skipped'

    if album.artistId not in artist_cache:
        artist_cache[album.artistId] = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
        ).scalars().first()
    artist = artist_cache[album.artistId]

    writable, reason = album_dir_writable(album, artist.path if artist else None)
    if not writable:
        logger.debug(f"Skipping cover for '{album.title}' — {reason} ({album.path})")
        return 'unreachable' if reason == 'album_dir_missing' else 'skipped'

    # A cover file already on disk (any common name) and overwrite off: fix the status.
    if find_cover_files(album.path) and not eff['overwrite_existing']:
        database.execute(
            update(TableAlbums)
            .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
            .values(cover_status='available', updated_at_timestamp=datetime.now())
        )
        logger.debug(f"Cover already exists on disk for '{album.title}', updated DB status")
        return 'adopted'

    if not ignore_backoff and _cover_backed_off(album):
        logger.debug(f"Skipping '{album.title}' — no cover found recently, backing off")
        return 'skipped'

    begin_search()  # reset the per-album transient-error flag
    cover_data, used_provider = find_cover(album, artist, providers)

    if not cover_data:
        transient = search_had_transient_error()
        _, retry_after = _defer_cover(album, transient)
        logger.debug(f"No cover found for '{album.title}' — next try after {retry_after:%Y-%m-%d %H:%M}")
        return 'transient' if transient else 'none'
    _clear_cover_backoff(album)

    cover_format = eff['cover_format']
    try:
        # Save with the image's real type: providers mostly serve JPEG but
        # CAA has PNGs, and a PNG named cover.jpg confuses players. No image
        # library is bundled to convert, so the profile format is a preference.
        sniffed = sniff_image(cover_data)
        ext = sniffed[0] if sniffed else cover_format
        if ext != cover_format and not (ext == 'jpg' and cover_format == 'jpeg'):
            logger.debug(f"Cover for '{album.title}' is {ext}, not {cover_format} — saving as cover.{ext}")
        filepath = os.path.join(album.path, f"cover.{ext}")
        # The folder exists (album_dir_writable); never create one.
        atomic_write_bytes(filepath, cover_data)

        database.execute(
            update(TableAlbums)
            .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
            .values(cover_status='available', updated_at_timestamp=datetime.now())
        )

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
        return 'saved'

    except Exception as e:
        logger.error(f"Error saving cover for '{album.title}': {e}")
        return 'none'


# ---------------------------------------------------------------------------
# Provider search
# ---------------------------------------------------------------------------

def collect_lyrics_results(track, artist_name, album_title, providers, *, query_title=None,
                           alt_artist_names=(), strict=True):
    """Run all enabled, healthy providers for a track and gather scored results.

    Shared orchestration for the missing-lyrics download, the plain→synced
    upgrade pass and the audit's verify mode: handles health-skip, rate
    limiting, the per-track transient-error flag, and success/failure
    recording. No selection decisions.

    Returns (all_results, stats) where stats has keys attempted, in_cooldown,
    failed (providers that raised a non-transient error), transient.
    """
    all_results = []
    begin_search()  # reset the per-track transient-error flag
    attempted = 0
    in_cooldown = 0
    failed = 0
    title = query_title or track.title

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
                track_name=title,
                artist_name=artist_name,
                album_name=album_title,
                duration=track.duration,
                mb_recording_id=track.mbId if track.mbId else None,
                alt_artist_names=tuple(a for a in (alt_artist_names or ()) if a),
                strict=strict,
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
            logger.warning(f"Lyrics provider unavailable ({provider_name}) for '{title}': {e}")
            note_transient_error()  # by definition — even if the provider didn't flag it
            health_tracker.record_failure(provider_name, str(e))
        except Exception as e:
            logger.error(f"Lyrics search error ({provider_name}) for '{title}': {e}")
            health_tracker.record_failure(provider_name, str(e))
            # Not transient for backoff purposes, but the pass is not clean:
            # the crashed provider might have had the lyrics (§2.1).
            failed += 1

    return all_results, {
        'attempted': attempted,
        'in_cooldown': in_cooldown,
        'failed': failed,
        'transient': search_had_transient_error(),
    }


# ---------------------------------------------------------------------------
# Lyrics candidate selection (pure — no DB, unit-tested)
# ---------------------------------------------------------------------------

# In prefer_synced / prefer_plain modes, a preferred-type candidate wins over a
# better-scoring other-type one only if it's within this margin of the best
# acceptable score — preference shouldn't pick a shaky match over a sure one.
_PREFER_MARGIN = 0.2
# best_score mode with the profile's "prefer synced lyrics" on: a synced
# candidate this close to the best wins.
_PREFER_SYNCED_MARGIN = 0.05


@dataclass(frozen=True)
class TrackContext:
    """What a candidate is judged against (track-context acceptance, §2.7)."""
    title: str | None = None            # query title (STRONG qualifier stripped when override 'never')
    raw_title: str | None = None
    album: str | None = None
    duration: float | None = None
    album_context: bool = False         # needs_album_context(title)
    tags: frozenset = field(default_factory=frozenset)   # version_tags(query title)

    @classmethod
    def for_track(cls, raw_title, query_title=None, album=None, duration=None):
        q = query_title or raw_title
        try:
            d = float(duration) if duration else None
        except (TypeError, ValueError):
            d = None
        return cls(
            title=q, raw_title=raw_title, album=album, duration=d,
            album_context=bool(needs_album_context(raw_title) or needs_album_context(q)),
            tags=frozenset(version_tags(q)),
        )


def _min_accept_score(eff):
    """The acceptance floor: the profile's score_threshold (0-100) or the built-in floor."""
    return max((eff.get('score_threshold', 0) or 0) / 100.0, _MIN_ACCEPT_SCORE)


def _md(result):
    return result.get('match_details') or {}


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _duration_diff(result, ctx):
    """|Δduration| in seconds, or None when either side is unknown."""
    dd = _float(_md(result).get('duration_diff'))
    if dd is not None:
        return abs(dd)
    d = _float(result.get('duration'))
    if d is not None and ctx is not None and ctx.duration:
        return abs(d - ctx.duration)
    return None


def _reports_duration(result):
    return result.get('duration') is not None or _md(result).get('duration_diff') is not None


def _result_title(result):
    return result.get('track_name') or result.get('title') or _md(result).get('matched_title')


def _album_key(text):
    from lyrarr.metadata.normalize import comparison_key
    return comparison_key(text or '')


def _album_similarity(a, b):
    """0-100 similarity of two album titles' comparison keys. Different
    numbers ("Vol. 1" / "Vol. 2", "1999" / "2001") or a different last word
    ("Sessions A" / "Sessions B") are different albums."""
    if not a or not b:
        return 0
    ka, kb = _album_key(a), _album_key(b)
    if not ka or not kb:
        return 0
    if ka == kb:
        return 100
    if set(re.findall(r'\d+', ka)) != set(re.findall(r'\d+', kb)):
        return 0
    wa, wb = ka.split(), kb.split()
    if wa and wb and len(wa) == len(wb) and wa[:-1] == wb[:-1] and wa[-1] != wb[-1] \
            and min(len(wa[-1]), len(wb[-1])) <= 2:
        return 0
    return int(SequenceMatcher(None, ka, kb).ratio() * 100)


def _album_verified(result, ctx):
    """Exact-album / MusicBrainz match, or the result's album ≈ the track's album."""
    if result.get('strategy') in ('exact_album', 'mb_exact') or _md(result).get('mb_matched'):
        return True
    return bool(ctx and ctx.album) and _album_similarity(result.get('album_name'), ctx.album) >= _ALBUM_SIMILARITY_MIN


def candidate_reject_reason(result, min_score, ctx=None):
    """Why a scored provider result may not be saved automatically, or None.

    Hard gates (wrong artist, title, duration, version) are never acceptable.
    MusicBrainz-verified matches bypass the score floor — the recording is
    identified — but not a duration or version gate. With a track context:
    a result whose raw title carries a content tag (intro/outro/interlude/
    skit/reprise, or an instrumental qualifier) the track lacks, or lacks
    one the track has, is a different version; and for generic titles
    (Intro, Skit #3, ...) the album-context rule applies: the result needs a
    duration, and either an exact-album/MB match, the same album, or a
    duration within 2 s (§2.7).
    """
    md = _md(result)
    gate = md.get('gate')
    if md.get('mb_matched'):
        if gate in ('duration_mismatch', 'version_mismatch'):
            return f'gate:{gate}'
    elif gate:
        return f'gate:{gate}'
    elif (result.get('score', 0) or 0) < min_score:
        return 'below_floor'

    if ctx is None:
        return None
    rtitle = _result_title(result)
    if rtitle:
        rtags = version_tags(rtitle)
        extra = rtags - ctx.tags
        missing = ctx.tags - rtags
        if (extra & (_CONTENT_TAGS | {'instrumental'})) or (missing & _CONTENT_TAGS):
            return 'version_tag'
        # A track titled just "Instrumental" (usually a real instrumental) only
        # takes a result that is also titled that way — not "The Instrumental".
        if 'instrumental' in missing and is_bare_instrumental_title(ctx.title):
            return 'version_tag'
        # An instrumental version of a vocal track (karaoke versions keep the
        # lyrics: they are candidates, they just never vote).
        if instrumental_title_tier(rtitle) == 'strong' and instrumental_title_tier(ctx.title) != 'strong' \
                and instrumental_qualifier_kind(rtitle) != 'karaoke':
            return 'version_tag'
    if ctx.album_context:
        if not _reports_duration(result):
            return 'album_context'
        dd = _duration_diff(result, ctx)
        if not _album_verified(result, ctx) and not (dd is not None and dd <= _ALBUM_CONTEXT_MAX_DIFF_S):
            return 'album_context'
    return None


def is_acceptable(result, min_score, ctx=None):
    """Whether a scored provider result may be saved automatically (see candidate_reject_reason)."""
    return candidate_reject_reason(result, min_score, ctx) is None


def _is_synced_result(r):
    return bool(r.get('synced_lyrics'))


def _is_plain_result(r):
    return bool(r.get('plain_lyrics')) and not r.get('plain_truncated')


def _score(r):
    return r.get('score', 0) or 0


def rank_candidates(results, selection_mode, min_score, ctx=None, prefer_synced=None):
    """Acceptable results, best first, honouring the selection mode.

    The floor, gates and track-context rules are applied first; the mode's
    preference then only chooses among acceptable results (and within a
    margin of the best), so a weak synced match can't outrank — and then
    sink — a strong plain one. Near-ties go to the smaller duration
    difference. In best_score mode with prefer_synced on, a synced candidate
    within 0.05 of the best wins.
    """
    acceptable = [r for r in results if is_acceptable(r, min_score, ctx)]
    if not acceptable:
        return []

    def key(r):
        dd = _duration_diff(r, ctx)
        return (round(_score(r), 3), 1 if _is_synced_result(r) else 0, -(dd if dd is not None else 1e6))

    by_score = sorted(acceptable, key=key, reverse=True)
    best = _score(by_score[0])
    if selection_mode in ('prefer_synced', 'prefer_plain'):
        prefers = _is_synced_result if selection_mode == 'prefer_synced' else _is_plain_result
        preferred = [r for r in by_score if prefers(r) and _score(r) >= best - _PREFER_MARGIN]
    elif prefer_synced:
        preferred = [r for r in by_score if _is_synced_result(r) and _score(r) >= best - _PREFER_SYNCED_MARGIN]
    else:
        return by_score
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


def _plain_of(result):
    plain = None if result.get('plain_truncated') else result.get('plain_lyrics')
    if plain and plain.strip():
        return plain
    synced = result.get('synced_lyrics')
    return strip_to_plain(synced) if synced else None


def _strict_ok(result):
    """A vocal candidate after instrumental content was seen: confident title, no gate."""
    md = _md(result)
    ts = md.get('title_score')
    return ts is not None and ts >= _AFTER_INSTRUMENTAL_TITLE_MIN and not md.get('gate')


def choose_lyrics(candidates, selection_mode, track_title=None, artist_name=None,
                  duration_s=None, *, info=None):
    """Validate ranked candidates in order and pick the first usable one.

    Returns (result, content, outcome):
      - 'ok': result/content to save.
      - 'truncated': only a possibly-truncated candidate was usable (the
        download saves it as a last resort; the upgrade pass never does).
      - 'none': nothing usable.

    Every candidate is pre-cleaned of NetEase-style credit blocks. A synced
    candidate that doesn't fit the track (last lyric > duration + 3 s, first
    lyric after the end, or fake timing) is replaced by its own plain text
    (right song, other edit). Instrumental content ("[Instrumental]") is
    never a verdict here: it only makes the rest stricter — after it, a vocal
    candidate needs title_score ≥ 85 and no gate, else the outcome is 'none'.

    `info` (a dict) receives 'instrumental_seen' and 'rejected'
    [(provider, reason)].
    """
    if info is None:
        info = {}
    info.setdefault('instrumental_seen', False)
    rejected = info.setdefault('rejected', [])
    fallback = None
    instrumental_seen = False

    for cand in candidates:
        provider = _provider_of(cand, '?')
        content = _content_for(cand, selection_mode)
        if not content:
            continue
        content = strip_credit_blocks(content)
        if not content.strip():
            rejected.append((provider, 'credits_only'))
            continue
        if duration_s and is_synced_lyrics(content):
            fits, why = synced_fits_duration(content, duration_s)
            if not fits:
                plain = _plain_of(cand)
                rejected.append((provider, f'synced:{why}'))
                content = strip_credit_blocks(plain) if plain else ''
                if not content.strip():
                    continue
        validation = validate_lyrics(
            content,
            track_title=track_title,
            artist_name=artist_name,
            duration_s=duration_s,
        )
        if validation.get('is_instrumental'):
            instrumental_seen = True
            info['instrumental_seen'] = True
            continue
        issues = validation.get('issues', [])
        errors = [i for i in issues if i['severity'] == 'error']
        if errors:
            for issue in errors:
                logger.debug(f"Rejected {provider} result for '{track_title}': {issue['message']}")
            rejected.append((provider, f"validation:{errors[0]['type']}"))
            continue
        if instrumental_seen and not _strict_ok(cand):
            rejected.append((provider, 'after_instrumental'))
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
        if not instrumental_seen or _strict_ok(fallback[0]):
            logger.debug(f"Only a possibly-truncated result for '{track_title}' — using it")
            return fallback[0], fallback[1], 'truncated'
        rejected.append((_provider_of(fallback[0], '?'), 'after_instrumental'))
    return None, None, 'none'


def _provider_of(result, default='unknown'):
    return result.get('_provider') or result.get('provider') or default


# ---------------------------------------------------------------------------
# Instrumental evidence (§2.1) — pure
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now().isoformat(timespec='seconds')


def _vote(source, provider, kind, *, anchor, album_verified=False, result=None, ctx=None, **extra):
    md = _md(result) if result is not None else {}
    v = {
        'source': source,
        'provider': provider,
        'kind': kind,
        'anchor': bool(anchor),
        'album_verified': bool(album_verified),
        'record_id': (result.get('source_id') if result is not None else None),
        'score': (result.get('score') if result is not None else None),
        'title_score': md.get('title_score'),
        'duration_diff': _duration_diff(result, ctx) if result is not None else None,
        'strategy': (result.get('strategy') if result is not None else None),
        'matched_title': (_result_title(result) if result is not None else None),
        'matched_album': (result.get('album_name') if result is not None else None),
        'at': _now_iso(),
    }
    v.update(extra)
    return v


def _split_results(results):
    """(markers, hints, rest): LRCLIB "instrumental" flags, provider hints, lyric results."""
    markers, hints, rest = [], [], []
    for r in results:
        has_lyrics = bool((r.get('synced_lyrics') or '').strip() or (r.get('plain_lyrics') or '').strip())
        if has_lyrics:
            rest.append(r)
        elif r.get('instrumental') is True:
            markers.append(r)
        elif isinstance(r.get('instrumental_hint'), dict):
            hints.append(r)
    return markers, hints, rest


def _marker_vote(r, ctx):
    """E2: a strong LRCLIB instrumental flag (re-checked here defensively)."""
    md = _md(r)
    strategy = r.get('strategy')
    if strategy not in ('mb_exact', 'exact_album'):
        return None
    if strategy == 'mb_exact' and r.get('duration') is None:
        return None
    dd = _duration_diff(r, ctx)
    if not ctx.duration or dd is None or dd > _MARKER_MAX_DIFF_S:
        return None
    if md.get('gate') or (md.get('title_score') or 0) < _MARKER_TITLE_MIN:
        return None
    rtitle = _result_title(r)
    if rtitle is not None and (instrumental_title_tier(rtitle) == 'strong') != (
            instrumental_title_tier(ctx.title) == 'strong'):
        return None
    return _vote(_provider_of(r, 'lrclib'), _provider_of(r, 'lrclib'), 'flag', anchor=True,
                 album_verified=True, result=r, ctx=ctx)


def _content_votes(results, ctx, min_score):
    """E3: marker-only provider content, per qualifying merge member."""
    votes = []
    for r in results:
        content = r.get('synced_lyrics') or r.get('plain_lyrics') or ''
        if classify_lyrics_text(strip_credit_blocks(content)) != 'marker':
            continue
        for m in result_members(r):
            md = _md(m)
            if candidate_reject_reason(m, min_score, ctx) is not None:
                continue
            if md.get('gate') or md.get('karaoke') or md.get('artist_via_alias'):
                continue
            if (md.get('title_score') or 0) < _VOTE_TITLE_MIN:
                continue
            rtitle = _result_title(m)
            if rtitle and (version_tags(rtitle) - ctx.tags):
                continue
            dd = _duration_diff(m, ctx)
            if _reports_duration(m):
                if dd is None or dd > _VOTE_MAX_DIFF_S:
                    continue  # reported a duration that doesn't fit: no vote at all
                verified = _album_verified(m, ctx)
                anchor = not ctx.album_context or verified
            else:
                verified, anchor = _album_verified(m, ctx), False
            provider = _provider_of(m)
            votes.append(_vote(provider, provider, 'marker_content', anchor=anchor,
                               album_verified=verified, result=m, ctx=ctx))
    return votes


def _hint_vote(r, ctx):
    """E4: NetEase pureMusic/nolyric, Musixmatch instrumental==1, within 3 s."""
    h = r.get('instrumental_hint') or {}
    provider = h.get('provider') or _provider_of(r)
    dd = _float(h.get('duration_diff'))
    if dd is None:
        dd = _duration_diff(r, ctx)
    if dd is None or not ctx.duration or abs(dd) > _VOTE_MAX_DIFF_S:
        return None
    matched = h.get('matched_title')
    if matched and (instrumental_title_tier(matched) or version_tags(matched) & {'instrumental', 'karaoke'}):
        return None
    if h.get('score') is not None and (_float(h.get('score')) or 0) < 0.5:
        return None
    # Same title-confidence floor as E2/E3: both hint providers return their
    # single best hit, so a near-title ("Lights" → "Lights Out") from two of
    # them is one correlated mistake, not two votes.
    md = _md(r)
    ts = h.get('title_score')
    if ts is None:
        ts = md.get('title_score')
    if (_float(ts) or 0) < _VOTE_TITLE_MIN:
        return None
    # §2.1: a match made only through a relaxed artist alias is never evidence.
    if md.get('gate') or md.get('artist_via_alias') or h.get('artist_via_alias'):
        return None
    verified = bool(ctx.album) and _album_similarity(h.get('matched_album'), ctx.album) >= _ALBUM_SIMILARITY_MIN
    anchor = not ctx.album_context or verified
    v = _vote(provider, provider, h.get('kind') or 'hint', anchor=anchor, album_verified=verified)
    v.update(record_id=r.get('source_id'), score=h.get('score'), title_score=h.get('title_score'),
             duration_diff=abs(dd), matched_title=matched, matched_album=h.get('matched_album'),
             strategy=r.get('strategy'))
    return v


def collect_votes(*, markers, hints, results, ctx, min_score, title_tier=None, override=None,
                  sidecar_marker=False):
    """Every instrumental vote for a track (E2-E6), most specific first."""
    votes = []
    for r in markers:
        v = _marker_vote(r, ctx)
        if v:
            votes.append(v)
    votes.extend(_content_votes(results, ctx, min_score))
    for r in hints:
        v = _hint_vote(r, ctx)
        if v:
            votes.append(v)
    if title_tier == 'weak' and override != 'never':
        votes.append(_vote('title_weak', 'title', 'title', anchor=False))
    if sidecar_marker:
        # LRCGET copies LRCLIB's flag into "[au: instrumental]": same source as E2.
        votes.append(_vote('lrclib', 'sidecar', 'sidecar_marker', anchor=False))
    return votes


def consensus_reached(votes, *, clean=True):
    """≥ 2 distinct sources and ≥ 1 anchor vote on a clean search pass (§2.1).

    Vetoes and the user's 'never' are checked by the caller.
    """
    counted = [v for v in votes if not v.get('veto') and v.get('kind') != 'title_strong']
    # A WEAK title only completes a consensus next to an LRCLIB flag or marker
    # content (E2/E3). A single NetEase/Musixmatch hint plus a title like
    # "Song (Instrumental Intro)" is one provider's word: at most a hint.
    if not any(v.get('anchor') and v.get('kind') in ('flag', 'marker_content') for v in counted):
        counted = [v for v in counted if v.get('source') != 'title_weak']
    sources = {v['source'] for v in counted}
    return clean and len(sources) >= 2 and any(v.get('anchor') for v in counted)


def _evidence_key(evidence):
    """Comparable form of an evidence list (stored JSON or list), timestamps ignored."""
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            return None
    if not isinstance(evidence, list):
        return None
    return sorted(
        json.dumps({k: v for k, v in e.items() if k != 'at'}, sort_keys=True, default=str)
        if isinstance(e, dict) else json.dumps(e, default=str)
        for e in evidence
    )


def _veto_entry(veto):
    return {'source': 'veto', 'kind': 'veto', 'veto': veto, 'anchor': False, 'at': _now_iso()}


# ---------------------------------------------------------------------------
# Backoff (§3.6)
# ---------------------------------------------------------------------------

def _defer_track(track, kind='normal', *, explicit=False):
    """Schedule the next lyrics search for a track that got nothing usable.

    kind: 'transient' (a provider failed, or all were cooling down),
    'cooldown' (nothing found while some provider was cooling down),
    'normal' (nothing found), 'hint' (likely instrumental: ≥ 14 days) or
    'no_audio' (audio file not reachable: +6 h, nothing counted). A bool is
    accepted for compatibility (True = transient). Explicit runs on a track
    still inside its window don't escalate a non-transient miss. Every write
    stamps lyrics_retry_set_at. Returns the (new) retry count.
    """
    if kind is True:
        kind = 'transient'
    elif kind is False or kind is None:
        kind = 'normal'
    from lyrarr.metadata.lyrics_store import _fresh_row
    row = _fresh_row(track) or track
    now = datetime.now()
    count = row.lyrics_retry_count or 0
    tc = row.lyrics_transient_count or 0
    cc = row.lyrics_cooldown_count or 0
    after = row.lyrics_retry_after

    if explicit and kind in ('cooldown', 'normal', 'hint') and after and after > now:
        return count

    if kind == 'no_audio':
        after = now + _NO_AUDIO_RETRY
    elif kind == 'transient':
        tc += 1
        if tc >= _TRANSIENT_CAP:
            tc = 0
            count, after = _advance(count, now)
        else:
            after = now + _TRANSIENT_RETRY
    elif kind == 'cooldown':
        tc = 0
        cc += 1
        if cc >= _COOLDOWN_CAP:
            cc = 0
            count, after = _advance(count, now)
        else:
            after = now + _COOLDOWN_RETRY
    else:
        tc = cc = 0
        count, after = _advance(count, now, _HINT_MIN_DAYS if kind == 'hint' else None)

    stmt = update(TableTracks).where(TableTracks.lidarrTrackId == row.lidarrTrackId)
    if getattr(row, 'lyrics_status', None):
        stmt = stmt.where(TableTracks.lyrics_status == row.lyrics_status)
    database.execute(stmt.values(
        lyrics_retry_count=count,
        lyrics_retry_after=after,
        lyrics_transient_count=tc,
        lyrics_cooldown_count=cc,
        lyrics_retry_set_at=now,
        updated_at_timestamp=now,
    ))
    return count


def _upgrade_streak(row, base, cap):
    """Consecutive upgrade deferrals of the kind whose retry delay is `base`.

    The upgrade pass has no counter columns (§3.6 keeps it off the
    lyrics_*_count ones), so the streak is carried in the deferral itself:
    deferral n of a streak sets after = set_at + base + (n-1) seconds.
    """
    after, set_at = row.upgrade_retry_after, getattr(row, 'upgrade_retry_set_at', None)
    if after is None or set_at is None:
        return 0
    extra = (after - set_at) - base
    if timedelta(0) <= extra < timedelta(seconds=cap) and extra.microseconds == 0:
        return extra.seconds + 1
    return 0


def _defer_upgrade(track, kind='normal', *, explicit=False):
    """Backoff for the plain→synced upgrade pass (upgrade_retry_* only).

    Same rule as the missing flow (§3.6): 'transient' retries in 15 min and
    advances the day backoff on the 6th consecutive one, 'cooldown' retries
    in 6 h and advances on the 3rd, anything else advances right away.
    """
    from lyrarr.metadata.lyrics_store import _fresh_row
    row = _fresh_row(track) or track
    now = datetime.now()
    count = row.upgrade_retry_count or 0
    after = row.upgrade_retry_after
    if explicit and kind != 'transient' and after and after > now:
        return count
    if kind in ('transient', 'cooldown'):
        base, cap = (_TRANSIENT_RETRY, _TRANSIENT_CAP) if kind == 'transient' else (_COOLDOWN_RETRY, _COOLDOWN_CAP)
        n = _upgrade_streak(row, base, cap) + 1
        if n >= cap:
            count, after = _advance(count, now)
        else:
            after = now + base + timedelta(seconds=n - 1)
    else:
        count, after = _advance(count, now)
    database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId)
        .values(upgrade_retry_count=count, upgrade_retry_after=after, upgrade_retry_set_at=now)
    )
    return count


def _miss_kind(stats, has_votes=False):
    if stats.get('transient') or (stats.get('attempted', 0) == 0 and stats.get('in_cooldown', 0) > 0):
        return 'transient'
    if stats.get('in_cooldown', 0) > 0 or stats.get('failed', 0) > 0:
        return 'cooldown'
    return 'hint' if has_votes else 'normal'


# ---------------------------------------------------------------------------
# The lyrics pipeline (§2.3)
# ---------------------------------------------------------------------------

def _candidate_view(r, *, min_score, ctx, blacklisted=frozenset(), include_content=False,
                    file_text=None, reject_reason=None, acceptable=None):
    md = _md(r)
    synced = r.get('synced_lyrics') or None
    plain = r.get('plain_lyrics') or None
    if synced:
        kind = 'synced'
    elif plain:
        kind = 'marker' if classify_lyrics_text(plain) == 'marker' else 'plain'
    elif r.get('instrumental') is True:
        kind = 'marker'
    elif isinstance(r.get('instrumental_hint'), dict):
        kind = 'hint'
    else:
        kind = 'empty'
    if synced and classify_lyrics_text(synced) == 'marker':
        kind = 'marker'
    if reject_reason is None and acceptable is None:
        if kind in ('marker', 'hint', 'empty'):
            reject_reason = kind
        else:
            reject_reason = candidate_reject_reason(r, min_score, ctx)
            if reject_reason is None and blacklisted and result_is_blacklisted(r, blacklisted):
                reject_reason = 'blacklisted'
            if reject_reason is None:
                content = strip_credit_blocks(_content_for(r, 'best_score') or '')
                v = validate_lyrics(content, track_title=ctx.title if ctx else None,
                                    duration_s=ctx.duration if ctx else None)
                errs = [i for i in v.get('issues', []) if i['severity'] == 'error']
                if v.get('is_instrumental'):
                    reject_reason = 'validation:instrumental'
                elif errs:
                    reject_reason = f"validation:{errs[0]['type']}"
    if acceptable is None:
        acceptable = reject_reason is None
    equivalence = None
    if include_content and file_text is not None and (synced or plain):
        equivalence = round(lyrics_similarity(synced or plain, file_text), 3)
    return {
        'provider': _provider_of(r),
        'strategy': r.get('strategy'),
        'source_id': None if r.get('source_id') is None else str(r.get('source_id')),
        'matched_title': _result_title(r),
        'matched_artist': r.get('artist_name'),
        'matched_album': r.get('album_name'),
        'duration': _float(r.get('duration')),
        'score': round(float(_score(r)), 3),
        'gate': md.get('gate'),
        'acceptable': bool(acceptable),
        'reject_reason': reject_reason,
        'content_kind': kind,
        'synced': synced if include_content else None,
        'plain': plain if include_content else None,
        'equivalence_to_file': equivalence,
    }


def _provenance(r, ctx):
    md = _md(r)
    return {
        'source_id': r.get('source_id'),
        'score': r.get('score'),
        'matched_title': _result_title(r),
        'matched_artist': r.get('artist_name'),
        'matched_album': r.get('album_name'),
        'matched_duration': r.get('duration'),
        'title_score': md.get('title_score'),
        'artist_score': md.get('artist_score'),
        'duration_diff': _duration_diff(r, ctx),
        'gate': md.get('gate'),
        'mb_matched': bool(md.get('mb_matched')),
        'strategy': r.get('strategy'),
    }


def _new_result(outcome='none'):
    return {'outcome': outcome, 'chosen': None, 'content': None, 'candidates': [],
            'votes': [], 'vetoes': [], 'stats': {}}


def _embedded(path, cache):
    """read_embedded_lyrics, cached per run by (path, mtime)."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if cache is not None and key in cache:
        return cache[key]
    value = read_embedded_lyrics(path)
    if cache is not None:
        cache[key] = value
    return value


def _embedded_saveable(text, query_title, artist_name, duration):
    """§2.3 step 5 save gates (the blacklist is checked by the caller)."""
    if classify_lyrics_text(text) != 'lyrics':
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and sum(1 for ln in lines if is_junk_line(ln)) * 2 > len(lines):
        return False
    v = validate_lyrics(text, track_title=query_title, artist_name=artist_name, duration_s=duration)
    if v.get('is_instrumental'):
        return False
    return not any(i['severity'] == 'error' for i in v.get('issues', []))


def _stamp_provenance(row, chosen, ctx):
    """Replace mode, same lyrics found: record where they come from, keep the file."""
    prov = _provenance(chosen, ctx)
    extra = {k: v for k, v in prov.items() if k not in ('source_id', 'score')}
    sid = prov.get('source_id')
    database.execute(
        update(TableTracks)
        .where(TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.lyrics_status == 'available',
               or_(TableTracks.lyrics_source.is_(None), TableTracks.lyrics_source == 'auto'))
        .values(lyrics_provider=_provider_of(chosen), lyrics_source_id=None if sid is None else str(sid),
                lyrics_match_score=_float(prov.get('score')),
                lyrics_match_json=json.dumps(extra, default=str, ensure_ascii=False),
                updated_at_timestamp=datetime.now())
    )


def _fill_track_artist(row, chosen):
    """track_artist from LRCLIB's MusicBrainz credit, when not known yet."""
    if getattr(row, 'track_artist', None) or not chosen:
        return
    mb_artist = _md(chosen).get('mb_artist')
    if mb_artist:
        database.execute(
            update(TableTracks)
            .where(TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.track_artist.is_(None))
            .values(track_artist=mb_artist)
        )


def _maybe_repair(content):
    if content and is_synced_lyrics(content) and validate_lrc(content).get('needs_repair'):
        return repair_lrc(content)
    return content


def run_lyrics_pipeline(track, *, eff, artist_name, album_title, alt_artist_names=(),
                        mode='download', write=False, explicit=False, embedded_cache=None):
    """The lyrics decision procedure for one track (PLAN §2.3, §1.5).

    mode='download': the full procedure on the fresh row. write=False has no
    side effects (the outcome is what *would* happen); write=True saves,
    adopts, classifies, records hints and defers as decided. explicit=True
    for user-scoped runs (a miss doesn't escalate an existing backoff).

    mode='verify' (audit): skips the row checks, sidecar/embedded/title
    steps and every write; searches with track-context acceptance and
    returns every raw candidate (gated, below-floor and blacklisted ones
    included) with acceptable/reject_reason and equivalence_to_file.
    Outcome ∈ {'ok','none','transient'}.

    Returns a PipelineResult dict: outcome, chosen, content, candidates,
    votes, vetoes, stats.
    """
    if mode == 'verify':
        if write:
            raise ValueError("run_lyrics_pipeline(mode='verify') never writes")
        return _pipeline_verify(track, eff=eff, artist_name=artist_name, album_title=album_title,
                                alt_artist_names=alt_artist_names)
    if mode != 'download':
        raise ValueError(f'unknown pipeline mode {mode!r}')
    return _pipeline_download(track, eff=eff, artist_name=artist_name, album_title=album_title,
                              alt_artist_names=alt_artist_names, write=write, explicit=explicit,
                              embedded_cache=embedded_cache)


def _search(row, eff, artist_name, album_title, alt_artist_names, query_title, ctx):
    """Search + split + merge + blacklist + rank + choose. Pure apart from the providers."""
    selection_mode = eff.get('lyrics_selection_mode', 'best_score') or 'best_score'
    min_score = _min_accept_score(eff)
    providers = _parse_providers_list(eff.get('lyrics_providers'))
    all_results, stats = collect_lyrics_results(
        row, artist_name, album_title, providers,
        query_title=query_title, alt_artist_names=alt_artist_names, strict=True,
    )
    stats = dict(stats, results=len(all_results), query_title=query_title)
    stats['transient'] = bool(stats['transient'] or (stats['attempted'] == 0 and stats['in_cooldown'] > 0))
    markers, hints, rest = _split_results(all_results)
    merged = merge_provider_results([dict(r) for r in rest], selection_mode) if rest else []
    blacklisted = get_blacklisted_hashes(row.lidarrTrackId)
    kept = [r for r in merged if not result_is_blacklisted(r, blacklisted)] if blacklisted else merged
    if len(kept) != len(merged):
        logger.debug(f"Filtered {len(merged) - len(kept)} blacklisted result(s) for '{row.title}'")
    ranked = rank_candidates(kept, selection_mode, min_score, ctx,
                             prefer_synced=bool(eff.get('prefer_synced_lyrics')))
    info = {}
    chosen, content, outcome = choose_lyrics(
        ranked, selection_mode, track_title=query_title, artist_name=artist_name,
        duration_s=ctx.duration, info=info,
    )
    return {
        'all': all_results, 'markers': markers, 'hints': hints, 'merged': kept,
        'blacklisted': blacklisted, 'ranked': ranked, 'chosen': chosen, 'content': content,
        'choose_outcome': outcome, 'info': info, 'stats': stats, 'min_score': min_score,
    }


def _query_title(row):
    tier = instrumental_title_tier(row.title)
    if tier == 'strong' and getattr(row, 'instrumental_override', None) == 'never':
        return strip_instrumental_qualifier(row.title)
    return row.title


def _pipeline_verify(track, *, eff, artist_name, album_title, alt_artist_names):
    from lyrarr.metadata.lyrics_store import _fresh_row
    row = _fresh_row(track) or track
    res = _new_result()
    query_title = _query_title(row)
    ctx = TrackContext.for_track(row.title, query_title, album_title, row.duration)
    s = _search(row, eff, artist_name, album_title, alt_artist_names, query_title, ctx)
    sr = read_sidecar(sidecar_path(row)) if getattr(row, 'path', None) else None
    file_text = sr.text if sr is not None else None
    res['candidates'] = [
        _candidate_view(r, min_score=s['min_score'], ctx=ctx, blacklisted=s['blacklisted'],
                        include_content=True, file_text=file_text)
        for r in s['all']
    ]
    res['votes'] = collect_votes(markers=s['markers'], hints=s['hints'], results=s['merged'], ctx=ctx,
                                 min_score=s['min_score'], title_tier=instrumental_title_tier(row.title),
                                 override=getattr(row, 'instrumental_override', None))
    res['stats'] = s['stats']
    if s['chosen'] is not None and s['choose_outcome'] in ('ok', 'truncated'):
        res['outcome'] = 'ok'
        res['content'] = s['content']
        res['chosen'] = _candidate_view(s['chosen'], min_score=s['min_score'], ctx=ctx,
                                        include_content=True, file_text=file_text,
                                        reject_reason=None, acceptable=True)
    else:
        res['outcome'] = 'transient' if s['stats']['transient'] else 'none'
    return res


def _pipeline_download(track, *, eff, artist_name, album_title, alt_artist_names, write, explicit,
                       embedded_cache):
    from lyrarr.metadata.lyrics_store import _fresh_row
    res = _new_result()
    row = _fresh_row(track)

    def done(outcome, **stats):
        res['outcome'] = outcome
        res['stats'].update(stats)
        return res

    # 1. Fresh row: only 'missing', or 'available' + a replace request on a NULL/auto file.
    if row is None:
        return done('skipped', reason='gone')
    status, source = row.lyrics_status, row.lyrics_source
    replace = (status == 'available' and row.lyrics_replace_requested is not None
               and source in (None, 'auto'))
    if status != 'missing' and not replace:
        return done('skipped', reason='row_changed', status=status)
    res['stats']['replace'] = replace
    override = row.instrumental_override

    # 2. The user said "instrumental".
    if not replace and override == 'always':
        if write and not set_instrumental(row.lidarrTrackId, 'user',
                                          [{'source': 'user', 'kind': 'override', 'at': _now_iso()}],
                                          actor='worker', expect_status=('missing',)):
            return done('skipped', reason='row_changed')
        return done('user_instrumental')

    # 3. The audio must be there (its root was checked by the caller).
    if not audio_present(row):
        if write:
            _defer_track(row, 'no_audio')
        return done('no_audio')

    def adopt():
        if write and not adopt_sidecar(row, keep_source=True):
            return done('skipped', reason='adopt_refused')
        return done('adopted')

    sidecar_veto = embedded_veto = sidecar_marker = False
    title_blocked = False
    vetoes = res['vetoes']
    kind, sr = read_sidecar_kind(sidecar_path(row))
    query_title = _query_title(row)
    tier = instrumental_title_tier(row.title)

    if not replace:
        # 4. An existing sidecar.
        if kind in ('lyrics', SIDECAR_UNKNOWN):
            if kind == SIDECAR_UNKNOWN or not (eff.get('overwrite_existing') and source in (None, 'auto')):
                return adopt()
            sidecar_veto = True
            vetoes.append('sidecar')
        elif kind == 'marker':
            sidecar_marker = True

        # 5. Lyrics embedded in the audio file's tags.
        emb = _embedded(row.path, embedded_cache)
        if emb is not None and emb.text and emb.text.strip():
            blacklisted = text_is_blacklisted(row.lidarrTrackId, emb.text)
            if not blacklisted and is_substantive_lyrics(emb.text):
                embedded_veto = True
                vetoes.append('embedded')
            if not sidecar_veto and not blacklisted and _embedded_saveable(
                    emb.text, query_title if tier != 'strong' else strip_instrumental_qualifier(row.title),
                    artist_name, row.duration):
                if write:
                    pr = persist_lyrics_result(
                        row, _maybe_repair(emb.text), 'embedded', source=None,
                        detect_lang=eff.get('auto_detect_language', True),
                        expect_status=('missing',), match=None,
                    )
                    if not pr:
                        if pr.reason == 'row_changed':
                            return done('skipped', reason='row_changed')
                        if pr.reason == 'no_audio':
                            _defer_track(row, 'no_audio')
                            return done('no_audio')
                        logger.warning(f"Could not save embedded lyrics for '{row.title}': {pr.message}")
                        _defer_track(row, 'normal', explicit=explicit)
                        return done('none', persist_failed=pr.reason)
                    res['stats']['persist'] = pr.info
                res['content'] = emb.text
                return done('embedded', source_tag=emb.source_tag)

        # 6. The title.
        if tier == 'strong':
            if override != 'never':
                if not sidecar_veto and not embedded_veto:
                    if write and not set_instrumental(
                            row.lidarrTrackId, 'title',
                            [{'source': 'title', 'kind': 'title', 'anchor': False, 'at': _now_iso()}],
                            actor='worker', expect_status=('missing',)):
                        return done('skipped', reason='row_changed')
                    return done('title_instrumental')
                if sidecar_veto:
                    return adopt()
                title_blocked = True  # embedded vocal lyrics veto the title

    # 7. Search.
    ctx = TrackContext.for_track(row.title, query_title, album_title, row.duration)
    s = _search(row, eff, artist_name, album_title, alt_artist_names, query_title, ctx)
    stats = s['stats']
    res['stats'].update(stats)
    min_score = s['min_score']
    res['candidates'] = [
        _candidate_view(r, min_score=min_score, ctx=ctx, blacklisted=s['blacklisted'])
        for r in s['all']
    ]
    votes = [] if replace else collect_votes(
        markers=s['markers'], hints=s['hints'], results=s['merged'], ctx=ctx, min_score=min_score,
        title_tier=tier, override=override, sidecar_marker=sidecar_marker,
    )
    if title_blocked:
        votes.insert(0, {'source': 'title', 'kind': 'title_strong', 'anchor': False, 'at': _now_iso()})
    res['votes'] = votes

    chosen, content = s['chosen'], s['content']
    ok = chosen is not None and s['choose_outcome'] in ('ok', 'truncated')
    # §2.2: with a strong LRCLIB "instrumental" flag, the vocal pick must be convincing.
    if ok and any(v.get('kind') == 'flag' for v in votes):
        md = _md(chosen)
        convincing = (md.get('title_score') or 0) >= _AFTER_INSTRUMENTAL_TITLE_MIN and (
            _score(chosen) >= min_score + 0.1 or (chosen.get('providers_agree') or 1) >= 2)
        if not convincing:
            ok = False
            res['stats']['marker_margin_failed'] = True
    # §2.2/§3.1: an acceptable vocal candidate that was only turned down because of
    # instrumental evidence (the strong-marker margin, or the stricter bar after
    # instrumental content) is a veto: the outcome is at most a hint, deferred.
    vocal_candidate = bool(res['stats'].get('marker_margin_failed')) or any(
        why == 'after_instrumental' for _p, why in s['info'].get('rejected', ()))
    if vocal_candidate and not ok:
        vetoes.append('vocal_candidate')

    if ok:
        res['chosen'] = _candidate_view(chosen, min_score=min_score, ctx=ctx, reject_reason=None, acceptable=True)
        content = _maybe_repair(content)
        res['content'] = content
        provider = _provider_of(chosen)
        same_form = is_synced_lyrics(content) == is_synced_lyrics(sr.text) if sr is not None else False
        if replace and sr is not None and sr.text and (same_form or is_synced_lyrics(sr.text)) \
                and lyrics_similarity(content, sr.text) >= _SAME_AS_FILE:
            if write:
                _stamp_provenance(row, chosen, ctx)
                clear_replace_request(row.lidarrTrackId, result='kept',
                                      details={'reason': 'equivalent', 'provider': provider})
            return done('ok', equivalent=True)
        if write:
            pr = persist_lyrics_result(
                row, content, provider, source='auto',
                detect_lang=eff.get('auto_detect_language', True),
                expect_status=('available',) if replace else ('missing',),
                expect_sources=(None, 'auto') if replace else None,
                match=_provenance(chosen, ctx),
            )
            if not pr:
                if pr.reason == 'row_changed':
                    return done('skipped', reason='row_changed')
                if pr.reason == 'no_audio':
                    _defer_track(row, 'no_audio')
                    return done('no_audio')
                logger.warning(f"Could not save lyrics for '{row.title}': {pr.message}")
                if replace:
                    clear_replace_request(row.lidarrTrackId, result='kept',
                                          details={'reason': pr.reason or 'persist_failed'})
                    return done('replaced_kept', persist_failed=pr.reason)
                if sidecar_veto:
                    return adopt()
                _defer_track(row, 'normal', explicit=explicit)
                return done('none', persist_failed=pr.reason)
            res['stats']['persist'] = pr.info
            if replace:
                clear_replace_request(row.lidarrTrackId, result='replaced', details={'provider': provider})
            _fill_track_artist(row, chosen)
        return done('ok', provider=provider)

    # 9. Nothing acceptable.
    transient = stats['transient']
    if replace:
        if transient:
            if write:
                _defer_track(row, 'transient', explicit=explicit)
            return done('transient')
        if write:
            clear_replace_request(row.lidarrTrackId, result='kept',
                                  details={'reason': 'nothing_better', 'results': stats.get('results', 0)})
        return done('replaced_kept')

    if sidecar_veto:
        return adopt()

    clean = not transient and stats.get('in_cooldown', 0) == 0 and stats.get('failed', 0) == 0
    blocking = list(vetoes) + (['override'] if override == 'never' else [])
    would_classify = consensus_reached(votes, clean=clean) or title_blocked
    if votes and would_classify and not blocking:
        if write and not set_instrumental(row.lidarrTrackId, 'consensus', votes, actor='worker',
                                          expect_status=('missing',)):
            return done('skipped', reason='row_changed')
        return done('instrumental')
    kind_ = _miss_kind(stats, has_votes=bool(votes))
    if votes:
        evidence = list(votes)
        if would_classify:
            evidence.extend(_veto_entry(v) for v in blocking)
        res['votes'] = evidence
        if write:
            # Re-record (history row + event) only when the evidence changed: a
            # provider that keeps failing retries every 15 min with the same votes.
            if _evidence_key(evidence) != _evidence_key(getattr(row, 'instrumental_evidence', None)):
                record_instrumental_hint(row.lidarrTrackId, evidence)
            _defer_track(row, kind_, explicit=explicit)
        return done('hint', transient=transient)
    if write:
        _defer_track(row, kind_, explicit=explicit)
    return done('transient' if kind_ == 'transient' else 'none')


# ---------------------------------------------------------------------------
# Missing-lyrics download run
# ---------------------------------------------------------------------------

def _resolve_track_artist(row, album_artist_name, write):
    """(query artist, alt artist names): the track's credited artist when known.

    track_artist is filled lazily from the file's ARTIST tag, or later from
    LRCLIB's MusicBrainz credit (_fill_track_artist). Without a tag it stays
    NULL (§3.2: NULL = use the album artist), so the MB credit can still fill
    it; the tag is then re-read on later runs (one cheap tag read).
    """
    artist = getattr(row, 'track_artist', None)
    if not artist and write and getattr(row, 'path', None):
        artist = (read_embedded_artist(row.path) or '').strip() or None
        if artist:
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.track_artist.is_(None))
                .values(track_artist=artist)
            )
    artist = artist or album_artist_name
    alt = (album_artist_name,) if album_artist_name and album_artist_name != artist else ()
    return artist, alt


def _select_lyrics_rows(album_ids, track_ids, explicit):
    now = datetime.now()
    backoff = or_(TableTracks.lyrics_retry_after.is_(None), TableTracks.lyrics_retry_after <= now)
    missing = select(TableTracks).where(TableTracks.lyrics_status == 'missing')
    replace = (
        select(TableTracks)
        .where(
            TableTracks.lyrics_status == 'available',
            TableTracks.lyrics_replace_requested.is_not(None),
            or_(TableTracks.lyrics_source.is_(None), TableTracks.lyrics_source == 'auto'),
        )
    )
    if not explicit:
        missing = missing.where(backoff)
        replace = replace.where(backoff)
    if album_ids:
        missing = missing.where(TableTracks.albumId.in_(album_ids))
        replace = replace.where(TableTracks.albumId.in_(album_ids))
    if track_ids:
        missing = missing.where(TableTracks.lidarrTrackId.in_(track_ids))
        replace = replace.where(TableTracks.lidarrTrackId.in_(track_ids))
    replace = replace.order_by(TableTracks.lyrics_replace_requested.asc()).limit(REPLACE_PER_PASS)
    return (list(database.execute(missing).scalars().all())
            + list(database.execute(replace).scalars().all()))


def download_missing_lyrics(album_ids=None, ignore_backoff=False, track_ids=None):
    """Download lyrics for tracks that are missing them (and replace-mode rows).

    Selects 'missing' rows plus at most REPLACE_PER_PASS 'available' rows with
    a replace request on a NULL/auto file (oldest request first), all subject
    to their retry backoff unless the run is explicit (ignore_backoff or
    track_ids). Library reachability is computed once per run, per root:
    rows under an unreachable root are skipped without touching their
    backoff (one health alert per root). Each remaining row goes through
    run_lyrics_pipeline(mode='download', write=True).

    Returns the number of tracks that got lyrics saved.
    """
    explicit = bool(ignore_backoff or track_ids)
    tracks = _select_lyrics_rows(album_ids, track_ids, explicit)
    if not tracks:
        logger.info("No tracks with missing lyrics")
        return 0

    reach = library_reachability(scope_paths=[t.path for t in tracks if t.path])
    _note_reachability(reach)
    unreachable = [t for t in tracks if t.path and not reach.ok_for(t.path)]
    if unreachable:
        roots = sorted({reach.root_of(t.path) for t in unreachable})
        logger.warning(
            f"Skipping {len(unreachable)} track(s) under unreachable music folder(s): {', '.join(roots)}"
        )
        tracks = [t for t in tracks if not (t.path and not reach.ok_for(t.path))]

    logger.info(f"Processing {len(tracks)} tracks with missing lyrics...")
    _emit_v2('download_scope', {'metadata_type': 'lyrics', 'total': len(tracks)})
    downloaded = 0
    outcomes = {}

    # Local to this run — the run holds _downloads_lock.
    album_cache = {}
    artist_cache = {}
    embedded_cache = {}

    for track in tracks:
        outcome, saved = 'skipped', False
        try:
            outcome, saved = _download_track_lyrics(track, album_cache, artist_cache, embedded_cache, explicit)
        except Exception as e:
            logger.error(f"Error processing lyrics for '{track.title}': {e}")
            try:
                _defer_track(track, 'normal')
            except Exception as e2:
                logger.debug(f"Could not schedule retry for '{track.title}': {e2}")
            outcome = 'none'
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if saved:
            downloaded += 1
        _emit_v2('download_item', {'metadata_type': 'lyrics', 'title': track.title, 'outcome': outcome})

    logger.info(f"Lyrics download complete: {downloaded}/{len(tracks)} downloaded "
                f"({', '.join(f'{k}={v}' for k, v in sorted(outcomes.items()))})")

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


def _download_track_lyrics(track, album_cache, artist_cache, embedded_cache, explicit):
    """Run the pipeline for one selected row. Returns (outcome, saved)."""
    if track.albumId not in album_cache:
        album_cache[track.albumId] = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
        ).scalars().first()
    album = album_cache[track.albumId]
    if not album:
        return 'skipped', False

    profile = _get_profile(album.profileId)
    eff = _effective_settings(album, profile)
    if not eff['download_lyrics']:
        if not profile:
            logger.debug(f"Skipping track '{track.title}' — album has no profile assigned")
        else:
            logger.debug(f"Skipping track '{track.title}' — lyrics disabled in profile '{profile.name}'")
        return 'skipped', False
    if not _parse_providers_list(eff['lyrics_providers']):
        return 'skipped', False

    if track.artistId not in artist_cache:
        artist_cache[track.artistId] = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
        ).scalars().first()
    artist = artist_cache[track.artistId]
    album_artist = artist.name if artist else None
    artist_name, alt = _resolve_track_artist(track, album_artist, write=True)

    result = run_lyrics_pipeline(
        track, eff=eff, artist_name=artist_name, album_title=album.title, alt_artist_names=alt,
        mode='download', write=True, explicit=explicit, embedded_cache=embedded_cache,
    )
    outcome = result['outcome']
    stats = result['stats']

    saved = outcome in ('ok', 'embedded') and not stats.get('equivalent')
    if saved:
        chosen = result.get('chosen') or {}
        provider = chosen.get('provider') or ('embedded' if outcome == 'embedded' else 'unknown')
        persisted = stats.get('persist') or {}
        detected_lang = persisted.get('detected_language')
        is_synced_file = persisted.get('is_synced')
        score = chosen.get('score')
        score_str = f"{score:.0%}" if isinstance(score, (int, float)) else '?'
        logger.info(f"✓ Lyrics: '{track.title}' ({provider}, score={score_str}, "
                    f"lang={detected_lang}, synced={is_synced_file})")
        event_stream(type='download_progress', payload={
            'metadata_type': 'lyrics', 'title': track.title, 'provider': provider,
            'language': detected_lang, 'is_synced': is_synced_file,
        })
        if outcome == 'ok' and result.get('content'):
            _auto_translate(track, artist, eff, result['content'], provider, detected_lang)
    elif outcome == 'title_instrumental':
        logger.info(f"⊘ Instrumental (title): '{track.title}'")
    elif outcome == 'instrumental':
        sources = sorted({v.get('source') for v in result.get('votes') or []})
        logger.info(f"⊘ Instrumental (consensus: {', '.join(sources)}): '{track.title}'")
    elif outcome == 'hint':
        logger.info(f"? Likely instrumental, kept as missing: '{track.title}'")
    elif outcome == 'transient':
        logger.info(f"Transient failure for '{track.title}' (rate limit/timeout/cooldown) — will retry soon")
    elif outcome == 'replaced_kept':
        logger.info(f"Re-search for '{track.title}': nothing better found, kept the current lyrics")
    else:
        logger.debug(f"Lyrics for '{track.title}': {outcome}")
    return outcome, saved


def _auto_translate(track, artist, eff, content, used_provider, detected_lang):
    """Translate freshly downloaded lyrics if the profile asks for it.

    The translation goes through the shared persist path as source='translated':
    the original is archived as a version (counted against the per-track cap,
    marked translated_from=<its language> so blacklisting the translation can
    also blacklist this source), and detected_language becomes the target.
    A failed or no-op translation (same content) is never saved.
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
        if not translated or content_hash(translated) == content_hash(content):
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


# ---------------------------------------------------------------------------
# Plain → synced upgrade pass
# ---------------------------------------------------------------------------

def _prefer_synced_off(eff):
    value = eff.get('prefer_synced_lyrics')
    return value is not None and not value


def upgrade_unsynced_lyrics(album_ids=None, track_ids=None, *, bypass_source_guard=None):
    """Re-search providers for tracks with plain lyrics and upgrade them to synced.

    Only replaces a plain file with a synced match of the *same* lyrics
    (plain-text equivalence ≥ 0.6) that passes the floor, gates, track
    context and timing checks and isn't blacklisted — never downgrades,
    never classifies instrumental, never touches the missing flow's backoff
    (it has its own upgrade_retry_*). Honors each album's profile: skipped
    when lyrics are disabled, the mode is prefer_plain, or "prefer synced"
    is explicitly off. STRONG instrumental titles are skipped unless the
    user said the track has vocals.

    Never touches lyrics a person chose or made ('manual', 'translated',
    'generated'). 'auto' rows must still hold what lyrarr wrote (content
    hash; a NULL hash is backfilled from history first). Unknown-origin
    (NULL) files are only replaced when the profile allows overwriting, or
    when the source guard is bypassed: bypass_source_guard None → bypassed
    for explicit track_ids requests; False → never (audit); True → always.
    """
    explicit = bool(track_ids)
    bypass = explicit if bypass_source_guard is None else bool(bypass_source_guard)
    conds = [
        TableTracks.lyrics_status == 'available',
        TableTracks.is_synced == False,
        or_(TableTracks.lyrics_source.is_(None), TableTracks.lyrics_source == 'auto'),
        TableTracks.lyrics_replace_requested.is_(None),
    ]
    if track_ids:
        conds.append(TableTracks.lidarrTrackId.in_(track_ids))
    else:
        conds.append(or_(
            TableTracks.upgrade_retry_after.is_(None),
            TableTracks.upgrade_retry_after <= datetime.now(),
        ))
    query = select(TableTracks).where(*conds)
    if album_ids:
        query = query.where(TableTracks.albumId.in_(album_ids))
    tracks = database.execute(query).scalars().all()

    if not tracks:
        logger.info("No unsynced lyrics to upgrade")
        return 0

    reach = library_reachability(scope_paths=[t.path for t in tracks if t.path])
    _note_reachability(reach)
    tracks = [t for t in tracks if not (t.path and not reach.ok_for(t.path))]

    logger.info(f"Checking {len(tracks)} unsynced track(s) for a synced upgrade...")
    upgraded = 0
    album_cache = {}
    artist_cache = {}

    for track in tracks:
        try:
            if _upgrade_track(track, album_cache, artist_cache, explicit, bypass):
                upgraded += 1
        except Exception as e:
            logger.error(f"Upgrade failed for '{track.title}': {e}")

    logger.info(f"Lyrics upgrade complete: {upgraded}/{len(tracks)} upgraded")
    return upgraded


def _upgrade_track(track, album_cache, artist_cache, explicit, bypass):
    from lyrarr.metadata.lyrics_store import _fresh_row

    if track.albumId not in album_cache:
        album_cache[track.albumId] = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
        ).scalars().first()
    album = album_cache[track.albumId]
    if not album:
        return False
    profile = _get_profile(album.profileId)
    eff = _effective_settings(album, profile)
    if not eff['download_lyrics'] or eff.get('lyrics_selection_mode') == 'prefer_plain' \
            or _prefer_synced_off(eff):
        return False

    row = _fresh_row(track)
    if row is None or row.lyrics_status != 'available' or row.lyrics_source not in (None, 'auto'):
        return False
    if is_instrumental_title(row.title) and row.instrumental_override != 'never':
        return False
    if not audio_present(row):
        return False

    path = sidecar_path(row)
    sr = read_sidecar(path)
    if sr is None or not sr.lossless or not sr.text.strip():
        return False
    if is_synced_lyrics(sr.text):
        # Already synced on disk: just fix the flag.
        database.execute(
            update(TableTracks).where(TableTracks.lidarrTrackId == row.lidarrTrackId)
            .values(is_synced=True, updated_at_timestamp=datetime.now())
        )
        return False

    source = row.lyrics_source
    if source == 'auto':
        if row.lyrics_content_hash is None:
            if backfill_content_hash(row) == 'downgraded':
                source = None
        elif row.lyrics_content_hash not in candidate_hashes(sr.text):
            # Someone else changed the file since lyrarr wrote it: unknown origin now.
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == row.lidarrTrackId, TableTracks.lyrics_source == 'auto')
                .values(lyrics_source=None)
            )
            logger.info(f"'{row.title}': lyrics file changed outside lyrarr — not upgrading it")
            return False
    if source is None and not bypass and not eff['overwrite_existing']:
        return False

    providers = _parse_providers_list(eff['lyrics_providers'])
    if not providers:
        return False

    if row.artistId not in artist_cache:
        artist_cache[row.artistId] = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == row.artistId)
        ).scalars().first()
    artist = artist_cache[row.artistId]
    artist_name, alt = _resolve_track_artist(row, artist.name if artist else None, write=True)

    query_title = _query_title(row)
    ctx = TrackContext.for_track(row.title, query_title, album.title, row.duration)
    min_score = _min_accept_score(eff)
    all_results, stats = collect_lyrics_results(
        row, artist_name, album.title, providers,
        query_title=query_title, alt_artist_names=alt, strict=True,
    )
    _markers, _hints, rest = _split_results(all_results)
    merged = merge_provider_results([dict(r) for r in rest], 'prefer_synced') if rest else []
    blacklisted = get_blacklisted_hashes(row.lidarrTrackId)
    if blacklisted:
        merged = [r for r in merged if not result_is_blacklisted(r, blacklisted)]
    # Only synced candidates of the same lyrics as the file (not another song)
    # whose timing fits the track: choose_lyrics would swap an overrunning
    # synced text for its plain one, which is no upgrade, and stop there
    # instead of trying a later synced candidate that fits.
    same_song = [
        r for r in merged
        if r.get('synced_lyrics')
        and (not ctx.duration or synced_fits_duration(strip_credit_blocks(r['synced_lyrics']), ctx.duration)[0])
        and lyrics_similarity(sr.text, strip_to_plain(r['synced_lyrics'])) >= _UPGRADE_MIN_EQUIVALENCE
    ]
    ranked = rank_candidates(same_song, 'prefer_synced', min_score, ctx)
    best, content, outcome = choose_lyrics(
        ranked, 'prefer_synced', track_title=query_title, artist_name=artist_name,
        duration_s=ctx.duration,
    )
    content = _maybe_repair(content) if outcome == 'ok' else None
    if not best or outcome != 'ok' or not content or not is_synced_lyrics(content):
        # No acceptable synced upgrade — back off so we don't re-search every run.
        transient = stats['transient'] or (stats['attempted'] == 0 and stats['in_cooldown'] > 0)
        kind = 'transient' if transient else ('cooldown' if stats['in_cooldown'] else 'normal')
        _defer_upgrade(row, kind, explicit=explicit)
        return False

    provider_name = _provider_of(best, 'upgrade')
    pr = persist_lyrics_result(
        row, content, provider_name,
        source='auto',
        detect_lang=eff.get('auto_detect_language', True),
        expect_status=('available',),
        expect_sources=(source,),
        match=_provenance(best, ctx),
        history_action=HISTORY_ACTION_UPGRADE,
    )
    if not pr:
        if pr.reason not in ('row_changed', 'no_audio'):
            _defer_upgrade(row, 'normal', explicit=explicit)
        return False
    database.execute(
        update(TableTracks).where(TableTracks.lidarrTrackId == row.lidarrTrackId)
        .values(upgrade_retry_count=0, upgrade_retry_after=None, upgrade_retry_set_at=datetime.now())
    )
    _fill_track_artist(row, best)

    logger.info(
        f"⤴ Upgraded to synced: '{row.title}' "
        f"({provider_name}, score={(best.get('score') or 0):.0%})"
    )
    event_stream(type='download_progress', payload={
        'metadata_type': 'lyrics', 'title': row.title,
        'provider': provider_name, 'is_synced': True, 'upgraded': True,
    })
    return True


def run_lyrics_upgrade(album_ids=None, track_ids=None, source='scheduled', *, bypass_source_guard=None):
    """Guarded entry point for the plain→synced upgrade pass.

    Shares the download lock so it can't run at the same time as a download run
    (or another upgrade) and double-hit providers. Skipped (not queued) if a run
    is already active.
    """
    if not _downloads_lock.acquire(blocking=False):
        logger.warning(f"Skipping {source} lyrics upgrade — a run is already in progress")
        return {'skipped': True, 'upgraded': 0}
    try:
        upgraded = upgrade_unsynced_lyrics(album_ids=album_ids, track_ids=track_ids,
                                           bypass_source_guard=bypass_source_guard) or 0
        return {'skipped': False, 'upgraded': upgraded}
    finally:
        _downloads_lock.release()
