
import json
import logging
import os
import re
import threading
import time
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
from lyrarr.metadata import lyrics_store
from lyrarr.metadata.lyrics_text import instrumental_title_tier

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


def _upsert_album(album, track_files=None, reach=None):
    """Insert or update a single album row from a Lidarr album payload.

    `track_files` is the pre-fetched /trackfile list for this album; passing it
    avoids one Lidarr API round-trip per album during bulk syncs. The album's
    artist must already be stored (FOREIGN KEY). `reach` (a
    lyrics_store.Reachability) gates the cover status: albums under an
    unreachable library root keep theirs.
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

    cover_status = _derive_cover_status(existing, album_path, artist_id, reach)
    if cover_status is not None:
        values['cover_status'] = cover_status

    if existing:
        database.execute(
            update(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id).values(**values)
        )
    else:
        values['created_at_timestamp'] = datetime.now()
        values.setdefault('cover_status', 'missing')
        # API responses compute album lyrics status from its tracks; this
        # column is legacy and not relied upon.
        values['lyrics_status'] = 'unknown'

        default_profile_id = _existing_profile_id(settings.general.default_profile_id)
        if default_profile_id:
            values['profileId'] = default_profile_id

        from sqlalchemy.dialects.sqlite import insert
        database.execute(insert(TableAlbums).values(**values))


def _derive_cover_status(existing, album_path, artist_id, reach):
    """cover_status from the art on disk, or None to keep the stored value.

    Kept when the album folder isn't reachable (unmounted root, folder gone)
    and, for existing albums, when the album points at its artist folder
    (artist-level art says nothing about this album).
    """
    if not album_path:
        return None
    if reach is not None and not reach.ok_for(album_path):
        return None
    if not os.path.isdir(album_path):
        return None
    if existing is not None:
        artist_path = database.execute(
            select(TableArtists.path).where(TableArtists.lidarrArtistId == artist_id)
        ).scalar()
        if artist_path and os.path.normpath(artist_path) == os.path.normpath(album_path):
            return None
    from lyrarr.metadata.cover_fetch import find_cover_files
    found = find_cover_files(album_path, settings.metadata.covers.folder_art_filename)
    if found:
        current = existing.cover_status if existing is not None else None
        return current if current in ('available', 'manual') else 'available'
    return 'missing'


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


def _sync_album_tracks(album, track_files=None, track_records=None, reach=None):
    """Sync all track files for one album row. Returns number of tracks synced.

    Joins /trackfile (paths) with /track (title, number, duration, recording
    id), and reconciles each track's lyrics state against any .lrc on disk.
    Pre-fetched per-artist `track_files`/`track_records` avoid two API
    round-trips per album. Errors propagate so the caller knows the album's
    tracks are incomplete.

    Lyrics state is only derived from the disk when it can be trusted: albums
    under an unreachable library root (`reach`) and tracks whose audio file
    is missing keep their stored status, flags and language.
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
                    'duration': _duration_seconds(tr.get('duration')),
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
    album_ok = reach is None or reach.ok_for(album.path or '')
    check_tombstones = lyrics_store.tombstones_exist()

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
        audio_ok = bool(track_path) and os.path.isfile(track_path)
        trusted = audio_ok and album_ok and (reach is None or reach.ok_for(track_path))

        # Lidarr renamed/moved the file: move its .lrc along. Safe on its own
        # terms (new audio present, old audio gone), so it doesn't wait for
        # the root check, which a library-wide rename can trip for one sync
        # (it samples the old paths) — the old path would be lost by then.
        if audio_ok and existing is not None:
            _rename_moved_sidecar(existing.path, track_path)

        values = {
            'lidarrTrackId': track_id,
            'albumId': album.lidarrAlbumId,
            'artistId': album.artistId,
            'title': title,
            'trackNumber': meta.get('trackNumber'),
            'discNumber': meta.get('mediumNumber', 1),
            'duration': meta.get('duration'),
            'path': track_path,
            'updated_at_timestamp': datetime.now(),
        }
        # Only overwrite a known recording id, never blank it out.
        if meta.get('mbId'):
            values['mbId'] = meta['mbId']

        if trusted:
            values.update(_lyrics_state_values(existing, title, track_path))
        elif existing is None:
            values.update(lyrics_status='missing', hasLyrics=False, is_synced=False)

        if existing:
            # Read before the UPDATE (it synchronises the loaded row in place).
            title_flag_lost = (
                trusted and existing.lyrics_status == 'instrumental' and existing.instrumental_reason == 'title'
                and existing.instrumental_override != 'always' and existing.title != title
                and instrumental_title_tier(title) != 'strong')
            database.execute(
                update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values)
            )
            # Lidarr renamed a track flagged by its title, and the new title no
            # longer declares an instrumental: the flag has no basis left.
            if title_flag_lost:
                try:
                    lyrics_store.clear_instrumental(track_id, why='weak-title', actor='sync',
                                                    expect_status=('instrumental',))
                except Exception as e:
                    logger.warning(f"Could not clear the title flag of renamed track {track_id}: {e}")
        else:
            values['created_at_timestamp'] = datetime.now()
            from sqlalchemy.dialects.sqlite import insert
            database.execute(insert(TableTracks).values(**values))
            if check_tombstones:
                try:
                    lyrics_store.restore_from_tombstone(track_id)
                except Exception as e:
                    logger.warning(f"Could not restore the state of re-imported track {track_id}: {e}")
        synced += 1

    return synced


def _duration_seconds(ms):
    """Lidarr track duration (ms) → whole seconds, rounded (None when unknown)."""
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return int(round(ms / 1000))


def _read_lyrics_kind(track_path):
    """(classification, content) of the track's sidecar; (None, None) if absent.

    A file that isn't valid UTF-8 or can't be read is SIDECAR_UNKNOWN
    (present, content not judged) — never 'empty' from its replacement
    characters; content is then the lossy text, or '' when unreadable.
    """
    kind, sr = lyrics_store.read_sidecar_kind(os.path.splitext(track_path)[0] + '.lrc')
    if kind is None:
        return None, None
    return kind, (sr.text if sr is not None else '')


def _detect(content):
    try:
        from lyrarr.metadata.language_detect import detect_language
        return detect_language(content)
    except Exception:
        return None


def _is_synced(content):
    from lyrarr.metadata.language_detect import is_synced_lyrics
    return is_synced_lyrics(content)


def _conflict_evidence(existing_evidence):
    """Rule T entry for a STRONG-title row that has lyrics (kept if already there)."""
    ev = lyrics_store._parse_json(existing_evidence)
    if isinstance(ev, list) and any(isinstance(e, dict) and e.get('veto') for e in ev):
        return existing_evidence
    return json.dumps([{'source': 'title', 'kind': 'title', 'veto': 'sidecar',
                        'at': datetime.now().isoformat()}])


def _lyrics_state_values(existing, title, track_path):
    """Lyrics columns for a track whose audio is present under a reachable root.

    * blacklisted stays; instrumental stays unless real lyrics sit next to a
      non-STRONG title (and the user didn't say 'always') → available;
    * only 'lyrics' content counts as present (markers/placeholders don't);
      a file that isn't UTF-8 or can't be read is present but not judged
      (SIDECAR_UNKNOWN): an available row keeps its status, source and
      language, a missing row becomes available, an instrumental row stays;
    * provenance is cleared on a flip to missing, and on a flip to available
      unless the content hash proves it describes the file on disk;
    * the language is written for new rows, NULL values and presence flips,
      never None over a value and never over manual/translated;
    * retry state is reset only when the status changes;
    * STRONG-title rows that are available get the title-conflict entry.
    """
    kind, content = _read_lyrics_kind(track_path)
    unknown = kind == lyrics_store.SIDECAR_UNKNOWN
    has = lyrics_store.sidecar_present(kind)
    tier = instrumental_title_tier(title)
    now = datetime.now()
    v = {}

    if existing is None:
        v.update(lyrics_status='available' if has else 'missing', hasLyrics=has,
                 is_synced=_is_synced(content) if has else False)
        if has:
            v['detected_language'] = None if unknown else _detect(content)
            v['language_source'] = 'detected'
            if tier == 'strong':
                v['instrumental_evidence'] = _conflict_evidence(None)
        return v

    cur = existing.lyrics_status
    override = existing.instrumental_override
    user_lang = existing.language_source in ('manual', 'translated')

    if cur == 'blacklisted':
        return {'hasLyrics': False}
    if cur == 'instrumental':
        # Only real, readable lyrics lift the flag (an unknown file doesn't).
        if not (kind == 'lyrics' and tier != 'strong' and override != 'always'):
            return {'hasLyrics': False}
        new_status = 'available'
    elif unknown and cur == 'available':
        # Present but not judgeable: keep status, source, language and flags
        # (audit F-3 reports the encoding); only the title-conflict entry.
        v = {'hasLyrics': True}
        if tier == 'strong' and override != 'never' and existing.lyrics_source != 'manual':
            v['instrumental_evidence'] = _conflict_evidence(existing.instrumental_evidence)
        return v
    elif has:
        new_status = 'available'
    else:
        new_status = 'missing'

    changed = new_status != cur
    v['lyrics_status'] = new_status
    v['hasLyrics'] = has

    if new_status == 'available':
        v['is_synced'] = _is_synced(content)
        if unknown:
            if not user_lang and changed:
                v['detected_language'] = None
                v['language_source'] = 'detected'
        elif not user_lang and (changed or existing.detected_language is None):
            detected = _detect(content)
            if detected is not None or changed:
                v['detected_language'] = detected
                v['language_source'] = 'detected'
        if changed:
            v['lyrics_source'] = lyrics_store.rediscovered_source(existing, content)
            v.update(lyrics_store.stale_provenance_values(existing, content))
            v['instrumental_reason'] = None
            v['instrumental_evidence'] = None
            v['lyrics_replace_requested'] = None
            if cur == 'instrumental':
                logger.info(f"'{title}': real lyrics found next to an instrumental-flagged track — now available")
        source = v.get('lyrics_source', existing.lyrics_source)
        if tier == 'strong' and override != 'never' and source != 'manual':
            v['instrumental_evidence'] = _conflict_evidence(
                None if changed else existing.instrumental_evidence)
    else:  # missing
        v['is_synced'] = False
        if changed:
            v['lyrics_source'] = None
            v.update(lyrics_store.cleared_provenance())
            v['lyrics_replace_requested'] = None
            if not user_lang:
                v['detected_language'] = None
            ev = lyrics_store._parse_json(existing.instrumental_evidence)
            if isinstance(ev, list) and any(isinstance(e, dict) and e.get('veto') for e in ev):
                v['instrumental_evidence'] = None

    if changed:
        v.update(lyrics_retry_count=0, lyrics_retry_after=None, lyrics_transient_count=0,
                 lyrics_cooldown_count=0, lyrics_retry_set_at=now)
    return v


def _rename_moved_sidecar(old_audio, new_audio):
    """Lidarr renamed/moved a track file: move its .lrc along when it is safe.

    Only when the stem changed, `<old>.lrc` exists, `<new>.lrc` doesn't, the
    old audio is gone and the new audio exists. Returns True if renamed.
    """
    if not old_audio or not new_audio:
        return False
    old_lrc = os.path.splitext(old_audio)[0] + '.lrc'
    new_lrc = os.path.splitext(new_audio)[0] + '.lrc'
    if old_lrc == new_lrc:
        return False
    try:
        if (os.path.isfile(old_lrc) and not os.path.exists(new_lrc)
                and not os.path.exists(old_audio) and os.path.isfile(new_audio)
                and os.path.isdir(os.path.dirname(new_lrc))):
            os.replace(old_lrc, new_lrc)
            logger.info(f"Moved lyrics file along with its track: {old_lrc} → {new_lrc}")
            return True
    except OSError as e:
        logger.warning(f"Could not move lyrics file {old_lrc} → {new_lrc}: {e}")
    return False


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
    """Move user/lyrarr state from a stale track row onto its replacement.

    * history, blacklist and archived versions follow the track;
    * 'blacklisted' (a user Skip) always carries over; 'instrumental' only
      onto a replacement that is 'missing' (lyrics found on disk win);
    * override, instrumental_prev, hint evidence, a manual/translated
      language, provenance and the retry backoff carry over when they apply;
    * when Lidarr renamed the file, its .lrc is moved along if that is safe.
    """
    from lyrarr.app.database import TableBlacklist, TableHistory, TableLyricsVersions

    for table in (TableHistory, TableBlacklist, TableLyricsVersions):
        database.execute(
            update(table)
            .where(table.lidarrTrackId == old.lidarrTrackId)
            .values(lidarrTrackId=new.lidarrTrackId)
        )

    same_file = _path_stem(old.path) == _path_stem(new.path)
    if not same_file and _rename_moved_sidecar(old.path, new.path):
        same_file = True
        fresh = lyrics_store._fresh_row(new.lidarrTrackId)
        if fresh is not None and fresh.lyrics_status in ('missing', None):
            lyrics_store.adopt_sidecar(fresh, keep_source=False)
        new = lyrics_store._fresh_row(new.lidarrTrackId) or new

    values = {}
    if old.lyrics_status == 'blacklisted' and new.lyrics_status != 'blacklisted':
        values.update(lyrics_status='blacklisted', hasLyrics=False, is_synced=False, lyrics_source=None)
    elif old.lyrics_status == 'instrumental' and new.lyrics_status == 'missing' and not (
            # A title flag only carries over to a title that still declares it.
            old.instrumental_reason == 'title' and instrumental_title_tier(new.title) != 'strong'):
        values.update(
            lyrics_status='instrumental', hasLyrics=False, is_synced=False,
            instrumental_reason=old.instrumental_reason,
            instrumental_evidence=old.instrumental_evidence,
            instrumental_checked_at=old.instrumental_checked_at,
        )
    elif old.lyrics_status == 'missing' and new.lyrics_status == 'missing':
        # Keep the retry backoff so an upgrade doesn't re-hammer providers.
        values.update(
            lyrics_retry_count=old.lyrics_retry_count,
            lyrics_retry_after=old.lyrics_retry_after,
            lyrics_transient_count=old.lyrics_transient_count,
            lyrics_cooldown_count=old.lyrics_cooldown_count,
            lyrics_retry_set_at=old.lyrics_retry_set_at,
        )
        if old.instrumental_evidence and not new.instrumental_evidence:
            values.update(instrumental_evidence=old.instrumental_evidence,
                          instrumental_checked_at=old.instrumental_checked_at)

    if old.instrumental_override and not new.instrumental_override:
        values['instrumental_override'] = old.instrumental_override
    if old.instrumental_prev and not new.instrumental_prev:
        values['instrumental_prev'] = old.instrumental_prev
    if old.track_artist and not new.track_artist:
        values['track_artist'] = old.track_artist

    if same_file:
        if new.lyrics_source is None and old.lyrics_source:
            values['lyrics_source'] = old.lyrics_source
            for col in ('lyrics_provider', 'lyrics_source_id', 'lyrics_match_score', 'lyrics_match_json',
                        'lyrics_content_hash'):
                if getattr(new, col) is None and getattr(old, col) is not None:
                    values[col] = getattr(old, col)
        if old.language_source in ('manual', 'translated') and new.language_source not in ('manual', 'translated'):
            values.update(language_source=old.language_source, detected_language=old.detected_language)
        if old.lyrics_status == 'available' and new.lyrics_status == 'available' and old.upgrade_retry_count:
            values.update(upgrade_retry_count=old.upgrade_retry_count,
                          upgrade_retry_after=old.upgrade_retry_after,
                          upgrade_retry_set_at=old.upgrade_retry_set_at)
    if values.get('lyrics_status') in ('blacklisted', 'instrumental'):
        values['lyrics_source'] = None
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
    only_monitored); tracks stay if their trackfile id still exists. Removed
    rows without a replacement leave a tombstone with their user state.
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
        orphans = []
        for old in stale:
            replacement = None
            if old.mbId:
                replacement = by_recording.get((old.albumId, old.mbId))
            if replacement is None and old.path:
                replacement = by_stem.get(_path_stem(old.path))
            if replacement is not None:
                _carry_over_track_state(old, replacement)
            else:
                orphans.append(old)
        if orphans:
            lyrics_store.tombstone_tracks(orphans)
        for old in stale:
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


def _sync_artist_albums(artist_id, lidarr_albums, files_by_album, records_by_album, reach=None):
    """Upsert one artist's albums and tracks, then remove what Lidarr dropped.

    `lidarr_albums` is the artist's full album list from Lidarr. One bad album
    (FK failure, unexpected payload) is logged and skipped instead of aborting
    the sync; removal of stale rows is then skipped for this artist because its
    local state is not known to be complete.
    Returns (albums_synced, tracks_synced, albums_skipped_no_files, complete).
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
            _upsert_album(album, track_files=track_files, reach=reach)
            album_row = database.execute(
                select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
            ).scalars().first()
            if album_row:
                total_tracks += _sync_album_tracks(
                    album_row, track_files, records_by_album.get(album_id, []), reach=reach
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
            complete = False
            database.rollback()
            logger.error(f"Error removing stale albums/tracks for artist {artist_id}: {e}")
    else:
        logger.warning(f"Artist {artist_id}: sync incomplete, not removing stale albums/tracks this run")

    return synced_albums, total_tracks, skipped_no_files, complete


# ---------------------------------------------------------------------------
# Incremental (single-artist) sync
# ---------------------------------------------------------------------------

def _delete_artist(artist_id):
    """Remove a locally-stored artist (cascades to albums/tracks)."""
    existing = database.execute(
        select(TableArtists).where(TableArtists.lidarrArtistId == artist_id)
    ).scalars().first()
    if existing:
        try:
            lyrics_store.tombstone_tracks(database.execute(
                select(TableTracks).where(TableTracks.artistId == artist_id)
            ).scalars().all())
        except Exception as e:
            logger.warning(f"Could not keep the track state of removed artist {artist_id}: {e}")
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

    reach = _library_reachability()
    synced_albums, total_tracks, _, _ = _sync_artist_albums(
        artist_id, albums, files_by_album, records_by_album, reach=reach
    )

    logger.info(
        f"Incremental sync done: artist {artist_id} — "
        f"{synced_albums} album(s), {total_tracks} track(s)"
    )
    event_stream(type='sync_complete', payload={
        'message': f"Synced artist '{artist.get('artistName', '?')}'",
    })


def _refresh_library_roots():
    """Store Lidarr's root folders in table_library_roots (source 'lidarr').

    When Lidarr's list can't be fetched or is empty, the stored rows are kept;
    with none stored, lyrics_store.library_roots() falls back to the parent
    folders of the artists ('derived').
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableLibraryRoots

    try:
        folders = lidarr_api.get_root_folders()
    except Exception as e:
        logger.warning(f"Could not fetch Lidarr root folders (using the artists' parent folders): {e}")
        return False
    paths = sorted({lyrics_store.normalize_root(f.get('path')) for f in folders or []
                    if isinstance(f, dict) and f.get('path')})
    if not paths:
        return False
    now = datetime.now()
    database.execute(delete(TableLibraryRoots).where(TableLibraryRoots.path.not_in(paths)))
    for path in paths:
        database.execute(
            sqlite_insert(TableLibraryRoots)
            .values(path=path, source='lidarr', updated_at=now)
            .on_conflict_do_update(index_elements=['path'], set_={'source': 'lidarr', 'updated_at': now})
        )
    return True


def _library_reachability():
    """Per-root reachability for one sync run (None if it can't be computed)."""
    try:
        reach = lyrics_store.library_reachability()
    except Exception as e:
        logger.warning(f"Could not check library reachability: {e}")
        return None
    for status in reach.unreachable:
        if not status.exists and not status.checked:
            continue  # not mounted, but no tracks under it (unused Lidarr root)
        logger.warning(f"Music folder not reachable: {status.root} — keeping the lyrics/cover state "
                       f"of everything under it this sync")
    return reach


def update_artists(force=False):
    """Sync artists from Lidarr to the local database.

    Returns True only when Lidarr answered every request and every artist
    synced completely.
    """
    if not force and not settings.general.use_lidarr:
        logger.debug("Lidarr not enabled, skipping sync")
        return False

    logger.info("Starting artist sync from Lidarr...")
    event_stream(type='sync_start', payload={'message': 'Syncing with Lidarr...'})
    try:
        artists = lidarr_api.get_artists()
    except LidarrError as e:
        logger.error(f"Failed to fetch artists from Lidarr: {e}")
        return False

    if not artists:
        # Never treat an empty list as "everything was deleted".
        logger.warning("No artists received from Lidarr")
        return False

    logger.info(f"Fetched {len(artists)} artists from Lidarr")
    _refresh_library_roots()

    complete = True
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
            complete = False
            database.rollback()
            logger.error(f"Error syncing artist {artist.get('id')} ('{artist.get('artistName', '?')}'): {e}")

    logger.info(f"Synced {synced} artists from Lidarr")

    # The artist list is complete, so anything stored locally that Lidarr no
    # longer has was deleted there.
    local_ids = set(database.execute(select(TableArtists.lidarrArtistId)).scalars().all())
    for artist_id in local_ids - lidarr_artist_ids:
        _delete_artist(artist_id)

    # Now sync albums + tracks (only albums with files on disk)
    albums_complete = update_albums(force=force, lidarr_artist_ids=lidarr_artist_ids)
    return bool(complete and albums_complete)


def update_albums(force=False, lidarr_artist_ids=None):
    """Sync albums and their track files from Lidarr to the local database.

    Fetches the full album list in one call, then track files + track records
    per artist (two calls each) instead of per album — on large libraries this
    cuts Lidarr API round-trips by roughly 6x. Only albums that actually have
    track files are stored; presence in the /trackfile response is the source
    of truth, not the albums' statistics block. `lidarr_artist_ids` (the
    complete artist list of this sync) lets artists whose albums all vanished
    be cleaned up too. Library reachability is computed once per run; albums
    under an unreachable root keep their lyrics/cover state.

    Returns True when every artist's albums and tracks synced completely.
    """
    if not force and not settings.general.use_lidarr:
        return False

    logger.info("Starting album sync from Lidarr...")
    try:
        albums = lidarr_api.get_albums()
    except LidarrError as e:
        logger.error(f"Failed to fetch albums from Lidarr: {e}")
        return False

    if not albums:
        logger.warning("No albums received from Lidarr")
        return False

    logger.info(f"Fetched {len(albums)} albums from Lidarr")
    reach = _library_reachability()
    complete = True

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
            complete = False
            logger.error(f"Error fetching track data for artist {artist_id}: {e}")
            continue

        a, t, s, ok = _sync_artist_albums(artist_id, artist_albums, files_by_album, records_by_album,
                                          reach=reach)
        synced += a
        total_tracks += t
        skipped_no_files += s
        complete = complete and ok

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
    return complete


def run_full_sync(timeout_s: float = 1800) -> bool:
    """Run a full Lidarr sync now, synchronously (audit bootstrap phase 0, S-9).

    Same work as the scheduled full sync (artists → albums → per-artist
    tracks → reconcile, refreshing table_library_roots and rewriting every
    duration), holding the sync single-flight state: waits up to `timeout_s`
    for a running sync to finish. Returns True only when Lidarr was reachable
    and every artist synced completely; False otherwise. Never raises.
    """
    global _sync_running
    try:
        if not settings.general.use_lidarr:
            logger.info("Full sync requested but Lidarr is not enabled")
            return False
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            with _sync_lock:
                if not _sync_running:
                    _sync_running = True
                    break
            if time.monotonic() >= deadline:
                logger.warning("Full sync not run: another sync is still in progress")
                return False
            time.sleep(0.5)
        try:
            return bool(update_artists(force=True))
        finally:
            with _sync_lock:
                _sync_running = False
                pending = _sync_pending_full or bool(_pending_artist_ids)
            if pending:
                _start_sync()
    except Exception as e:
        try:
            database.rollback()
        except Exception:
            pass
        logger.error(f"Full sync failed: {e}")
        return False
