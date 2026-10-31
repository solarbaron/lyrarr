
import logging
import os
import re
import threading

from flask import request
from flask_restx import Namespace, Resource, abort
from sqlalchemy import or_

from lyrarr.app.database import TableAlbums, TableArtists, TableTracks, database, func, select
from lyrarr.metadata.manager import cover_providers, lyrics_providers, save_cover_art, save_lyrics
from lyrarr.metadata.merge import merge_provider_results

logger = logging.getLogger(__name__)

api_ns_metadata = Namespace('metadata', description='Metadata search and download')


# ---------------------------------------------------------------------------
# Request parsing helpers — bad input is a 400, never a 500
# ---------------------------------------------------------------------------

def _json_body():
    """The request's JSON object body ({} when there is none); 400 if malformed."""
    data = request.get_json(silent=True)
    if data is None:
        if request.get_data(cache=True).strip():
            abort(400, 'Request body must be a JSON object')
        return {}
    if not isinstance(data, dict):
        abort(400, 'Request body must be a JSON object')
    return data


def _id_list(data, key):
    """List of integer IDs under `key`, or None when the key is absent/null.

    An explicitly provided empty list stays [] so callers can tell "nothing
    selected" apart from "no selection given" (which may mean the whole library).
    """
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    ):
        abort(400, f'{key} must be a list of integer IDs')
    return value


def _str_field(data, key, default=None):
    """String field under `key` (default when absent/null); 400 on other types."""
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        abort(400, f'{key} must be a string')
    return value


def _resolve_album_ids(album_ids, artist_ids):
    """Union of explicit album IDs and every album of the given artists."""
    resolved = set(album_ids or [])
    if artist_ids:
        rows = database.execute(
            select(TableAlbums.lidarrAlbumId).where(TableAlbums.artistId.in_(artist_ids))
        ).scalars().all()
        resolved.update(rows)
    return sorted(resolved)


def _blacklist_hash(track_id, album_id, h, provider=None):
    """Blacklist a precomputed lyrics content hash for a track (idempotent)."""
    from datetime import datetime

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from lyrarr.app.database import TableBlacklist

    exists = database.execute(
        select(TableBlacklist.id).where(
            TableBlacklist.lidarrTrackId == track_id,
            TableBlacklist.metadata_type == 'lyrics',
            TableBlacklist.content_hash == h,
        )
    ).first()
    if exists:
        return
    database.execute(
        sqlite_insert(TableBlacklist).values(
            metadata_type='lyrics', provider=provider, content_hash=h,
            lidarrTrackId=track_id, lidarrAlbumId=album_id, timestamp=datetime.now(),
        )
    )


# PersistResult.reason → HTTP status (PLAN §1.8 error contract).
_PERSIST_STATUS = {
    'no_audio': 409,
    'row_changed': 409,
    'lossy_existing': 409,
    'archive_failed': 500,
    'write_failed': 500,
    'empty': 400,
    'not_found': 404,
}

# Short per-track reasons for batch-job completion messages.
_REASON_SHORT = {
    'no_audio': 'audio not reachable',
    'row_changed': 'changed while saving',
    'lossy_existing': 'existing file not UTF-8',
    'archive_failed': 'could not archive the old file',
    'write_failed': 'could not write the file',
    'empty': 'nothing to save',
    'not_found': 'track not found',
    'translation_failed': 'translation failed',
    'no_file': 'no lyrics file',
    'not_utf8': 'lyrics file not UTF-8',
}


def _save_failure(result, default='Failed to save lyrics'):
    """(body, status) for a failed save_lyrics()/persist result."""
    reason = getattr(result, 'reason', None) or 'write_failed'
    message = getattr(result, 'message', None) or default
    return {'message': message, 'code': reason}, _PERSIST_STATUS.get(reason, 500)


def _save_response(result, ok_message):
    """Map a save_lyrics() result (PersistResult, or a bare bool) to a response."""
    if result:
        return {'message': ok_message}
    return _save_failure(result)


def _reason_summary(reasons):
    """'3 skipped: audio not reachable, 1 skipped: changed while saving'."""
    from collections import Counter
    parts = [f"{n} skipped: {_REASON_SHORT.get(r, r)}" for r, n in Counter(reasons).most_common()]
    return ', '.join(parts)


def _read_lyrics_file(track):
    """(path, SidecarRead|None) of the track's .lrc; read strictly (BOM/UTF-16 aware)."""
    from lyrarr.metadata.lyrics_store import read_sidecar, sidecar_path
    path = sidecar_path(track)
    if not path or not os.path.isfile(path):
        return path, None
    return path, read_sidecar(path)


# ---------------------------------------------------------------------------
# Background jobs — one run per job type at a time
# ---------------------------------------------------------------------------

# 'download' covers batch-download and the upgrade pass, which share the
# download worker's lock; the API-level lock makes a double-click a clean 409
# instead of a second thread that silently skips.
_job_locks = {
    name: threading.Lock()
    for name in ('download', 'translate', 'sync_generate', 'redetect', 'sidecar_import', 'audit')
}


def _spawn_thread(target):
    threading.Thread(target=target, daemon=True).start()


def _start_job(name, target):
    """Run target in a background thread unless a job of this type is running.

    Returns False (caller answers 409) when one already is. The lock is held
    until target returns, whether it succeeds or raises.
    """
    lock = _job_locks[name]
    if not lock.acquire(blocking=False):
        return False

    def _wrapped():
        try:
            target()
        finally:
            lock.release()

    try:
        _spawn_thread(_wrapped)
    except Exception:
        lock.release()
        raise
    return True


def _job_busy():
    return {'message': 'This job is already running — try again when it finishes'}, 409


# ---------------------------------------------------------------------------
# Whisper (synced-lyrics generation)
# ---------------------------------------------------------------------------

WHISPER_MODELS = ('tiny', 'base', 'small', 'medium', 'large-v3')

# Loaded models keyed by (size, device, compute_type). Only one is kept: each
# can hold several GB of RAM, so switching sizes evicts the previous model.
_whisper_models = {}
_whisper_models_lock = threading.Lock()
# Transcription is CPU/RAM heavy — run one at a time across the whole app.
_whisper_transcribe_lock = threading.Lock()


def _whisper_download_root():
    """Persistent model cache under the config dir (survives container recreation)."""
    from lyrarr.app.get_args import args
    path = os.path.join(args.config_dir, 'cache', 'whisper')
    os.makedirs(path, exist_ok=True)
    return path


def _get_whisper_model(model_size, device, compute_type):
    from faster_whisper import WhisperModel

    key = (model_size, device, compute_type)
    with _whisper_models_lock:
        model = _whisper_models.get(key)
        if model is None:
            _whisper_models.clear()
            try:
                model = WhisperModel(model_size, device=device, compute_type=compute_type,
                                     download_root=_whisper_download_root())
            except TypeError:
                # Very old faster-whisper without download_root.
                model = WhisperModel(model_size, device=device, compute_type=compute_type)
            _whisper_models[key] = model
        return model


def _transcribe(model, audio_path):
    """Transcribe audio into [{'start','end','text'}]. Caller holds the transcribe lock.

    faster-whisper returns a lazy generator — the actual work happens while
    iterating, so the segments are fully consumed here.
    """
    segments, info = model.transcribe(audio_path, word_timestamps=True, language=None)
    result = [
        {'start': s.start, 'end': s.end, 'text': s.text.strip()}
        for s in segments
    ]
    return result, getattr(info, 'language', None) or 'unknown'


def _align_lyrics(lyric_lines, segments, threshold=0.3):
    """Time plain lyric lines against Whisper segments.

    Lines keep their original order. Each line is matched (fuzzy) only against
    segments after the previous match, so a repeated chorus maps to its next
    occurrence; unmatched lines are interpolated between neighbouring matches.
    Timestamps are strictly increasing. Returns (lrc_lines, matched_count).
    """
    from difflib import SequenceMatcher

    from lyrarr.metadata.lrc_repair import _seconds_to_ts

    n = len(lyric_lines)
    if not n or not segments:
        return [], 0
    total_duration = max(segments[-1]['end'] or 0, segments[-1]['start'] or 0)

    anchors = [None] * n
    next_seg = 0
    for i, line in enumerate(lyric_lines):
        lower = line.lower()
        best_score, best_idx = 0, -1
        for idx in range(next_seg, len(segments)):
            score = SequenceMatcher(None, lower, segments[idx]['text'].lower()).ratio()
            if score > best_score:
                best_score, best_idx = score, idx
        if best_idx >= 0 and best_score > threshold:
            anchors[i] = segments[best_idx]['start']
            next_seg = best_idx + 1
    matched = sum(1 for a in anchors if a is not None)

    times = []
    for i in range(n):
        if anchors[i] is not None:
            times.append(anchors[i])
            continue
        prev_i = next((j for j in range(i - 1, -1, -1) if anchors[j] is not None), None)
        next_i = next((j for j in range(i + 1, n) if anchors[j] is not None), None)
        lo_i, lo_t = (prev_i, anchors[prev_i]) if prev_i is not None else (-1, 0.0)
        hi_i, hi_t = (next_i, anchors[next_i]) if next_i is not None else (n, total_duration)
        hi_t = max(hi_t, lo_t)
        times.append(lo_t + (hi_t - lo_t) * (i - lo_i) / (hi_i - lo_i))

    lrc_lines = []
    prev_cs = -1
    for line, t in zip(lyric_lines, times):
        # Round to centiseconds first (so 59.996 becomes 01:00.00, not 00:60.00),
        # then force strictly increasing timestamps.
        cs = max(int(round(max(t, 0) * 100)), prev_cs + 1)
        prev_cs = cs
        lrc_lines.append(f"{_seconds_to_ts(cs / 100)} {line}")
    return lrc_lines, matched


@api_ns_metadata.route('/metadata/covers/search/<int:album_id>')
class CoverSearch(Resource):
    def get(self, album_id):
        """Search for cover art for an album across all providers."""
        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
        ).scalars().first()
        if not album:
            return {'message': 'Album not found'}, 404

        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
        ).scalars().first()

        results = []
        for name, provider in cover_providers.items():
            try:
                if name == 'musicbrainz' and album.mbId:
                    hits = provider.search(mb_release_group_id=album.mbId)
                elif name == 'fanart' and album.mbId:
                    hits = provider.search(
                        mb_artist_id=artist.mbId if artist else None,
                        mb_album_id=album.mbId,
                    )
                elif name in ('deezer', 'itunes', 'theaudiodb'):
                    hits = provider.search(
                        artist_name=artist.name if artist else None,
                        album_name=album.title,
                    )
                else:
                    hits = []
                for h in hits:
                    h['provider'] = name
                results.extend(hits)
            except Exception as e:
                logger.warning(f"Cover search failed for provider '{name}' (album {album_id}): {e}")

        return {'results': results, 'albumId': album_id}


@api_ns_metadata.route('/metadata/covers/download/<int:album_id>')
class CoverDownload(Resource):
    def post(self, album_id):
        """Download a specific cover art image and save it."""
        from urllib.parse import urlparse

        data = _json_body()
        url = _str_field(data, 'url', '').strip()
        provider_name = _str_field(data, 'provider', 'musicbrainz')

        if not url:
            return {'message': 'url is required'}, 400
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return {'message': 'url must be an http(s) URL'}, 400

        provider = cover_providers.get(provider_name)
        if not provider:
            return {'message': 'Invalid provider'}, 400

        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
        ).scalars().first()
        if not album:
            return {'message': 'Album not found'}, 404

        # The provider's download() only fetches from its own hosts, caps the
        # size and checks the bytes are an image — None means it refused or failed.
        try:
            image_data = provider.download(url)
        except Exception as e:
            logger.warning(f"Cover download via '{provider_name}' failed: {e}")
            image_data = None
        if not image_data:
            return {'message': f"Could not download a valid image from {provider_name} "
                               f"(URL rejected or unreachable)"}, 502

        ok, reason = save_cover_art(album_id, image_data, provider_name)
        if ok:
            return {'message': 'Cover art saved successfully'}
        from lyrarr.metadata.cover_fetch import COVER_REASONS, cover_reason_message
        if reason in COVER_REASONS:
            return {'message': cover_reason_message(reason, album), 'code': reason}, 409
        if reason == 'not_image':
            return {'message': 'The downloaded file is not a JPEG/PNG/WebP/GIF image', 'code': reason}, 400
        if reason == 'not_found':
            return {'message': 'Album not found', 'code': reason}, 404
        return {'message': 'Failed to save cover art', 'code': reason or 'write_failed'}, 500


@api_ns_metadata.route('/metadata/lyrics/search/<int:track_id>')
class LyricsSearch(Resource):
    def get(self, track_id):
        """Search for lyrics for a track across all providers.

        Optional query param:
            custom_query: Override automatic title/artist with a manual search string
        """
        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track:
            return {'message': 'Track not found'}, 404

        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
        ).scalars().first()

        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
        ).scalars().first()

        # Custom query override: user manually entered a search string
        custom_query = request.args.get('custom_query', '').strip()

        results = []
        for name, provider in lyrics_providers.items():
            try:
                if custom_query:
                    # Use custom query as track name, no artist filter
                    hits = provider.search(
                        track_name=custom_query,
                        artist_name=None,
                        album_name=None,
                        duration=track.duration,
                        strict=False,
                    )
                else:
                    album_artist = artist.name if artist else None
                    query_artist = getattr(track, 'track_artist', None) or album_artist
                    hits = provider.search(
                        track_name=track.title,
                        artist_name=query_artist,
                        album_name=album.title if album else None,
                        duration=track.duration,
                        mb_recording_id=track.mbId if track.mbId else None,
                        alt_artist_names=(album_artist,) if album_artist and album_artist != query_artist else (),
                        strict=False,
                    )
                for h in hits:
                    h['provider'] = name
                    h['_provider'] = name  # Used by merge_provider_results()
                    # Truncate lyrics for preview (first 300 chars)
                    if h.get('synced_lyrics'):
                        h['synced_preview'] = h['synced_lyrics'][:300]
                    if h.get('plain_lyrics'):
                        h['plain_preview'] = h['plain_lyrics'][:300]

                    # match_details is now computed inside each provider
                    # via LyricsProvider.score_result() — no duplicate scoring needed

                results.extend(hits)
            except Exception as e:
                logger.warning(f"Lyrics search failed for provider '{name}' (track {track_id}): {e}")

        # Drop instrumental markers (LRCLIB "no lyrics exist" answers) — they
        # carry no content, so there is nothing for the user to preview or save.
        results = [r for r in results if r.get('synced_lyrics') or r.get('plain_lyrics')]

        # Merge and de-duplicate cross-provider results
        results = merge_provider_results(results)

        # Hide results the user has blacklisted for this track.
        from lyrarr.metadata.lyrics_store import get_blacklisted_hashes, result_is_blacklisted
        blacklisted = get_blacklisted_hashes(track_id)
        if blacklisted:
            results = [r for r in results if not result_is_blacklisted(r, blacklisted)]

        # Sort by score
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        return {'results': results, 'trackId': track_id}


@api_ns_metadata.route('/metadata/lyrics/download/<int:track_id>')
class LyricsDownload(Resource):
    def post(self, track_id):
        """Download/save specific lyrics for a track."""
        data = _json_body()
        lyrics_data = {
            'synced_lyrics': _str_field(data, 'synced_lyrics'),
            'plain_lyrics': _str_field(data, 'plain_lyrics'),
        }
        provider_name = _str_field(data, 'provider', 'lrclib')

        if not lyrics_data['synced_lyrics'] and not lyrics_data['plain_lyrics']:
            return {'message': 'synced_lyrics or plain_lyrics is required'}, 400

        # Interactive pick or editor save — always a manual source.
        result = save_lyrics(track_id, lyrics_data, provider_name, source='manual')
        return _save_response(result, 'Lyrics saved successfully')


@api_ns_metadata.route('/metadata/lyrics/blacklist/<int:track_id>')
class LyricsBlacklist(Resource):
    def get(self, track_id):
        """List blacklisted lyrics entries for a track."""
        from lyrarr.app.database import TableBlacklist
        rows = database.execute(
            select(TableBlacklist).where(
                TableBlacklist.lidarrTrackId == track_id,
                TableBlacklist.metadata_type == 'lyrics',
            )
        ).scalars().all()
        return {'trackId': track_id, 'blacklist': [r.to_dict() for r in rows]}

    def post(self, track_id):
        """Blacklist a specific lyrics result so it's never auto-selected again.

        Body: { content?, synced_lyrics?, plain_lyrics?, provider?, rescan? }
        If no content is given, blacklists the currently saved .lrc file.
        rescan (default true): when the blacklisted content IS the file on
        disk, archive and remove it and re-queue the track so the downloader
        picks a different match on the next run. A blacklisted or
        instrumental track keeps its status (only the file goes).
        """
        from datetime import datetime

        from lyrarr.app.database import update
        from lyrarr.metadata import lyrics_store
        from lyrarr.metadata.lyrics_store import (
            archive_and_remove,
            blacklist_content,
            cleared_provenance,
            content_hash,
        )

        data = _json_body()
        provider = _str_field(data, 'provider')
        rescan = data.get('rescan', True)

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track:
            return {'message': 'Track not found'}, 404

        content = (
            _str_field(data, 'content')
            or _str_field(data, 'synced_lyrics')
            or _str_field(data, 'plain_lyrics')
        )

        fpath, sr = _read_lyrics_file(track)
        file_content = sr.text if sr is not None else None
        # Fall back to the currently saved lyrics file
        if not content:
            content = file_content

        if not content or not content.strip():
            return {'message': 'No lyrics content to blacklist'}, 400

        if not blacklist_content(track_id, content, provider):
            return {'message': 'Could not blacklist (empty content)'}, 400

        is_file = bool(file_content) and content_hash(content) == content_hash(file_content)
        # Blacklisting the file on disk: if it is derived content (e.g. a
        # translation), its own hash never matches what providers return, so
        # also blacklist the hashes of the source it was made from.
        if is_file:
            hashes_for_blacklist = getattr(lyrics_store, 'hashes_for_blacklist', None)
            if hashes_for_blacklist is None:
                logger.debug("lyrics_store.hashes_for_blacklist unavailable — blacklisting file content only")
            else:
                try:
                    extra = set(hashes_for_blacklist(track_id, content) or ())
                except Exception as e:
                    logger.warning(f"Could not resolve source hashes for track {track_id}: {e}")
                    extra = set()
                extra.discard(content_hash(content))
                for h in extra:
                    if h:
                        _blacklist_hash(track_id, track.albumId, h, provider)

        if track.path and os.path.isfile(os.path.splitext(track.path)[0] + '.txt'):
            logger.warning(f"A legacy .txt lyrics file exists next to '{track.title}' — it is not managed by lyrarr")

        # Only the file that was blacklisted is removed — never a different
        # file the user may have saved since the search result was shown.
        did_rescan = bool(rescan and fpath and is_file)
        if did_rescan:
            if not archive_and_remove(track, 'blacklist'):
                return {'message': 'Lyrics result blacklisted, but the current file could not be archived, '
                                   'so it was not removed', 'rescan': False}
            now = datetime.now()
            values = dict(
                hasLyrics=False,
                is_synced=False,
                lyrics_source=None,
                lyrics_replace_requested=None,
                lyrics_retry_count=0,
                lyrics_retry_after=None,
                lyrics_transient_count=0,
                lyrics_cooldown_count=0,
                lyrics_retry_set_at=now,
                updated_at_timestamp=now,
                **cleared_provenance(),
            )
            if track.language_source not in ('manual',):
                values['detected_language'] = None
            # Skip and "instrumental" are terminal user/policy states: keep them.
            if track.lyrics_status not in ('blacklisted', 'instrumental'):
                values['lyrics_status'] = 'missing'
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track_id)
                .values(**values)
            )

        return {'message': 'Lyrics result blacklisted', 'rescan': did_rescan}

    def delete(self, track_id):
        """Clear all blacklisted lyrics entries for a track."""
        from lyrarr.metadata.lyrics_store import clear_blacklist
        count = clear_blacklist(track_id)
        return {'message': f'Cleared {count} blacklist entries', 'count': count}


@api_ns_metadata.route('/metadata/lyrics/upgrade')
class LyricsUpgrade(Resource):
    def post(self):
        """Re-search plain-lyrics tracks for a synced upgrade (background).

        Body: { trackIds?, albumIds?, artistIds?, all? }. With no scope at all
        (or all=true), every track that currently has unsynced lyrics is
        checked. A scope that is given but selects nothing (e.g. an artist with
        no albums, or an empty list) is a no-op — never the whole library.
        """
        from lyrarr.app.event_handler import event_stream
        from lyrarr.metadata.download_worker import downloads_in_progress, run_lyrics_upgrade

        data = _json_body()
        track_ids = _id_list(data, 'trackIds')
        album_ids = _id_list(data, 'albumIds')
        artist_ids = _id_list(data, 'artistIds')
        scope_all = bool(data.get('all', False))

        scoped_tracks = scoped_albums = None
        if not scope_all and (track_ids is not None or album_ids is not None or artist_ids is not None):
            if track_ids:
                scoped_tracks = track_ids
            else:
                scoped_albums = _resolve_album_ids(album_ids, artist_ids)
                if not scoped_albums:
                    return {'message': 'Nothing selected — no tracks to upgrade', 'count': 0}

        # Advisory check — same reasoning as the batch-download endpoint.
        if downloads_in_progress():
            return {'message': 'Another download run is in progress — try again when it finishes'}, 409

        def _run():
            payload = {'covers': 0, 'lyrics': 0}
            try:
                result = run_lyrics_upgrade(album_ids=scoped_albums, track_ids=scoped_tracks, source='manual',
                                            bypass_source_guard=None)
                if result.get('skipped'):
                    payload['message'] = 'Another run is already in progress'
                else:
                    payload['lyrics'] = result['upgraded']
                    payload['message'] = f"Upgraded {result['upgraded']} track(s) to synced lyrics"
            except Exception as e:
                logger.exception(f"Lyrics upgrade error: {e}")
                payload['error'] = str(e)
                payload['message'] = f'Lyrics upgrade failed: {e}'
            finally:
                try:
                    event_stream(type='download_complete', payload=payload)
                finally:
                    database.remove()

        if not _start_job('download', _run):
            return _job_busy()
        return {'message': 'Lyrics upgrade started'}


@api_ns_metadata.route('/metadata/lyrics/reset-retry')
class LyricsResetRetry(Resource):
    def post(self):
        """Reset retry backoff state for tracks so they are re-checked immediately.

        Body: { trackIds: [1,2,3] }  OR  { all: true }
        """
        from datetime import datetime

        from lyrarr.app.database import update

        data = _json_body()
        track_ids = _id_list(data, 'trackIds') or []
        reset_all = bool(data.get('all', False))

        now = datetime.now()
        values = dict(lyrics_retry_count=0, lyrics_retry_after=None, lyrics_transient_count=0,
                      lyrics_cooldown_count=0, lyrics_retry_set_at=now, updated_at_timestamp=now)
        # Only rows still waiting for lyrics: a reset never changes a status
        # (an available, skipped or instrumental row stays what it is).
        missing = TableTracks.lyrics_status == 'missing'
        if reset_all:
            cond = (missing, or_(TableTracks.lyrics_retry_count > 0, TableTracks.lyrics_retry_after.is_not(None)))
        elif track_ids:
            cond = (missing, TableTracks.lidarrTrackId.in_(track_ids))
        else:
            return {'message': 'trackIds or all=true required'}, 400
        count = database.execute(update(TableTracks).where(*cond).values(**values)).rowcount or 0
        return {'message': f'Reset retry state for {count} tracks', 'count': count}


@api_ns_metadata.route('/metadata/lyrics/coherence/<int:album_id>')
class LyricsCoherence(Resource):
    def get(self, album_id):
        """Run album-level lyrics coherence check."""
        from lyrarr.metadata.coherence import check_album_coherence
        return check_album_coherence(album_id)


@api_ns_metadata.route('/metadata/lyrics/validate-lrc/<int:track_id>')
class LyricsValidateLrc(Resource):
    def get(self, track_id):
        """Validate LRC timestamps for a track's lyrics."""
        from lyrarr.metadata.lrc_repair import validate_lrc

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found'}, 404

        filepath, sr = _read_lyrics_file(track)
        if sr is None:
            return {'message': 'No lyrics file found'}, 404
        return validate_lrc(sr.text)

    def post(self, track_id):
        """Repair LRC timestamps when they need it (archived first, atomic write).

        Large gaps alone are reported, never rewritten. The status and source
        of the track are kept; a hash-verified 'auto' file keeps its hash in
        step with the repaired content.
        """
        from datetime import datetime

        from lyrarr.app.database import update
        from lyrarr.metadata.lrc_repair import repair_lrc, validate_lrc
        from lyrarr.metadata.lyrics_store import (
            PERSIST_REASONS,
            _archive_existing,
            _origin_provider,
            atomic_write_text,
            audio_present,
            content_hash,
        )
        from lyrarr.metadata.lyrics_text import normalize_text_for_write

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found'}, 404

        filepath, sr = _read_lyrics_file(track)
        if sr is None:
            return {'message': 'No lyrics file found'}, 404

        validation = validate_lrc(sr.text)
        if not validation.get('needs_repair'):
            message = 'LRC is already valid' if validation.get('valid') else \
                'Nothing to repair (large gaps between lines are normal and are left alone)'
            return {'message': message, 'validation': validation}
        if not audio_present(track):
            return {'message': PERSIST_REASONS['no_audio'].format(path=track.path), 'code': 'no_audio'}, 409
        if not sr.lossless:
            return {'message': PERSIST_REASONS['lossy_existing'], 'code': 'lossy_existing'}, 409

        repaired = normalize_text_for_write(repair_lrc(sr.text))
        if not repaired:
            return {'message': PERSIST_REASONS['empty'], 'code': 'empty'}, 400
        if not _archive_existing(track.lidarrTrackId, filepath, origin_provider=_origin_provider(track),
                                 replaced_by='lrc-repair', new_content=repaired):
            return {'message': PERSIST_REASONS['archive_failed'], 'code': 'archive_failed'}, 500
        try:
            atomic_write_text(filepath, repaired)
        except OSError as e:
            return {'message': PERSIST_REASONS['write_failed'].format(path=filepath, error=e),
                    'code': 'write_failed'}, 500

        from lyrarr.metadata.language_detect import is_synced_lyrics
        values = {'is_synced': is_synced_lyrics(repaired), 'updated_at_timestamp': datetime.now()}
        if track.lyrics_content_hash and track.lyrics_content_hash == content_hash(sr.text):
            values['lyrics_content_hash'] = content_hash(repaired)
        database.execute(
            update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values)
        )
        return {
            'message': 'LRC repaired and saved (the previous version was archived)',
            'before': validation,
            'after': validate_lrc(repaired),
        }


@api_ns_metadata.route('/metadata/lyrics/versions/<int:track_id>')
class LyricsVersions(Resource):
    def get(self, track_id):
        """List previous lyrics versions stored in-app."""
        from lyrarr.app.database import TableLyricsVersions
        versions = database.execute(
            select(TableLyricsVersions)
            .where(TableLyricsVersions.lidarrTrackId == track_id)
            .order_by(TableLyricsVersions.timestamp.desc())
        ).scalars().all()
        return {'versions': [v.to_dict() for v in versions], 'trackId': track_id}

    def post(self, track_id):
        """Restore a previous lyrics version by ID."""
        data = _json_body()
        version_id = data.get('versionId')
        if not version_id:
            return {'message': 'versionId is required'}, 400
        if not isinstance(version_id, int) or isinstance(version_id, bool):
            return {'message': 'versionId must be an integer'}, 400

        from lyrarr.app.database import TableLyricsVersions
        version = database.execute(
            select(TableLyricsVersions).where(TableLyricsVersions.id == version_id)
        ).scalars().first()
        if not version or version.lidarrTrackId != track_id:
            return {'message': 'Version not found'}, 404

        lyrics_data = {}
        if version.lyrics_type == 'synced':
            lyrics_data['synced_lyrics'] = version.content
        else:
            lyrics_data['plain_lyrics'] = version.content

        result = save_lyrics(track_id, lyrics_data, 'restored', source='manual')
        return _save_response(result, 'Lyrics version restored')


@api_ns_metadata.route('/metadata/lyrics/read/<int:track_id>')
class LyricsRead(Resource):
    def get(self, track_id):
        """Read existing lyrics content from disk for a track."""
        import os

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found or has no path'}, 404

        # Fix stale DB state if the file was deleted/added outside lyrarr.
        from lyrarr.metadata.lyrics_store import reconcile_track_lyrics
        reconcile_track_lyrics(track)

        content = None
        lyrics_type = None
        lossless = True

        filepath, sr = _read_lyrics_file(track)
        if filepath and os.path.isfile(filepath) and sr is None:
            return {'message': f'Could not read lyrics file {filepath}'}, 500
        if sr is not None:
            # A non-UTF-8 file still shows (with replacement chars) instead of
            # looking like "no lyrics"; `lossless` tells the editor.
            content = sr.text
            lossless = sr.lossless
            from lyrarr.metadata.language_detect import is_synced_lyrics
            lyrics_type = 'synced' if is_synced_lyrics(content) else 'plain'

        return {
            'trackId': track_id,
            'content': content,
            'type': lyrics_type,
            'has_lyrics': content is not None,
            'lossless': lossless,
        }


@api_ns_metadata.route('/metadata/lyrics/publish/<int:track_id>')
class LyricsPublish(Resource):
    def post(self, track_id):
        """Publish the track's current lyrics file to LRCLIB (community contribution).

        Solves LRCLIB's proof-of-work challenge, so the request can take a few
        seconds. User-triggered only.
        """

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found or has no path'}, 404

        # LRCLIB matches lyrics by duration — publishing without one would
        # store an entry nobody can ever match.
        if not track.duration or track.duration <= 0:
            return {'message': 'Track has no duration — sync with Lidarr first; '
                               'LRCLIB needs the exact track length to publish'}, 400

        # Translations are not the song's lyrics; generated (Whisper) timing and
        # dual-language files need an explicit confirmation.
        source = getattr(track, 'lyrics_source', None)
        if source == 'translated':
            return {'message': "These lyrics are a translation, not the song's original lyrics, "
                               "so they can't be published to LRCLIB.", 'code': 'translated'}, 400

        data = _json_body()
        filepath, sr = _read_lyrics_file(track)
        if sr is None:
            return {'message': 'No lyrics file on disk for this track'}, 404
        if not sr.lossless:
            return {'message': 'The lyrics file is not valid UTF-8 — fix its encoding before publishing',
                    'code': 'lossy_existing'}, 409
        content = sr.text
        if not content.strip():
            return {'message': 'Lyrics file is empty'}, 400

        if not data.get('confirm'):
            if source == 'generated':
                return {'message': 'These lyrics were timed automatically (Whisper). Check the timing, then '
                                   'confirm to publish them to LRCLIB.', 'code': 'confirm_required'}, 409
            if _looks_dual_language(content):
                return {'message': 'This file looks like it holds two languages (original and translation). '
                                   'Confirm to publish it to LRCLIB anyway.', 'code': 'confirm_required'}, 409

        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
        ).scalars().first()
        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
        ).scalars().first()

        from lyrarr.metadata.language_detect import is_synced_lyrics
        from lyrarr.metadata.lrc_repair import repair_lrc, validate_lrc
        from lyrarr.metadata.lyrics.lrclib_publish import LrclibPublishError, publish_lyrics
        from lyrarr.metadata.merge import strip_lrc_timestamps

        if is_synced_lyrics(content):
            synced = repair_lrc(content) if validate_lrc(content).get('needs_repair') else content
            if not is_synced_lyrics(synced):  # fake timing: publish as plain only
                synced, plain = None, synced
            else:
                plain = strip_lrc_timestamps(synced)
        else:
            synced, plain = None, content
        if plain and any(re.match(r'^\s*\[\d{1,3}:\d{1,2}', line) for line in plain.splitlines()):
            return {'message': 'The plain lyrics still contain timestamps — repair the file before publishing',
                    'code': 'timestamps_in_plain'}, 400

        try:
            publish_lyrics(
                track_name=track.title,
                artist_name=artist.name if artist else None,
                album_name=album.title if album else None,
                duration_s=track.duration,  # already seconds
                plain_lyrics=plain,
                synced_lyrics=synced,
            )
        except LrclibPublishError as e:
            return {'message': str(e)}, 502

        from datetime import datetime

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from lyrarr.app.database import HISTORY_ACTION_PUBLISH, TableHistory
        database.execute(
            sqlite_insert(TableHistory).values(
                action=HISTORY_ACTION_PUBLISH,
                description=f"Published lyrics for {track.title} to LRCLIB",
                metadata_type='lyrics',
                provider='lrclib-publish',
                lidarrTrackId=track.lidarrTrackId,
                lidarrArtistId=track.artistId,
                lidarrAlbumId=track.albumId,
                timestamp=datetime.now(),
                metadata_path=filepath,
            )
        )
        return {'message': 'Lyrics published to LRCLIB — thanks for contributing!'}


def _looks_dual_language(content):
    """Heuristic for an original + translation file: alternate lines detect
    as two different languages. False when it can't tell."""
    from lyrarr.metadata.language_detect import detect_language, strip_lrc_formatting
    lines = [ln for ln in strip_lrc_formatting(content).splitlines() if ln.strip()]
    if len(lines) < 6:
        return False
    try:
        even = detect_language('\n'.join(lines[0::2]))
        odd = detect_language('\n'.join(lines[1::2]))
    except Exception:
        return False
    return bool(even and odd and even != odd)


@api_ns_metadata.route('/metadata/lyrics/upload/<int:track_id>')
class LyricsUpload(Resource):
    def post(self, track_id):
        """Upload a lyrics file (.lrc) for a track."""
        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track:
            return {'message': 'Track not found'}, 404

        if 'file' not in request.files:
            return {'message': 'No file provided'}, 400

        file = request.files['file']
        if not file.filename:
            return {'message': 'No file selected'}, 400

        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext != 'lrc':
            return {'message': 'Invalid format. Only .lrc files are accepted'}, 400

        from lyrarr.metadata.language_detect import is_synced_lyrics
        from lyrarr.metadata.lyrics_store import decode_lyrics_bytes

        decoded = decode_lyrics_bytes(file.read())
        if not decoded.lossless:
            return {'message': 'The file is not valid UTF-8 or UTF-16 text. Save it as UTF-8 and upload it again.',
                    'code': 'not_utf8'}, 400
        content = decoded.text

        # Determine if synced or plain from content, not extension
        is_synced = is_synced_lyrics(content)
        lyrics_data = {}
        if is_synced:
            lyrics_data['synced_lyrics'] = content
        else:
            lyrics_data['plain_lyrics'] = content

        result = save_lyrics(track_id, lyrics_data, 'upload', source='manual')
        return _save_response(result, 'Lyrics uploaded for track')


@api_ns_metadata.route('/metadata/lyrics/translate/<int:track_id>')
class LyricsTranslate(Resource):
    def post(self, track_id):
        """Translate lyrics to a target language.

        Body: { content: str, targetLang: str, mode: "replace" | "dual" }
        """
        data = _json_body()
        content = _str_field(data, 'content', '')
        target_lang = _str_field(data, 'targetLang', 'en')
        mode = _str_field(data, 'mode', 'replace')  # 'replace' or 'dual'

        if not content.strip():
            return {'message': 'No lyrics content provided'}, 400

        try:
            # Use deep-translator (more reliable, lightweight)
            from deep_translator import GoogleTranslator
            try:
                translator = GoogleTranslator(source='auto', target=target_lang)
            except Exception as e:
                return {'message': f'Unsupported target language {target_lang!r}: {e}'}, 400

            # Split into lines, translate non-timestamp lines
            lines = content.split('\n')
            translated_lines = []
            original_lines = []
            calls = failures = 0

            for line in lines:
                stripped = line.strip()
                # Skip LRC timestamp lines' timestamps but translate text
                if stripped.startswith('[') and ']' in stripped:
                    bracket_end = stripped.index(']') + 1
                    tag = stripped[:bracket_end]
                    text = stripped[bracket_end:].strip()

                    if text and not tag.startswith('[ti:') and not tag.startswith('[ar:') and not tag.startswith('[al:'):
                        calls += 1
                        try:
                            translated = translator.translate(text)
                        except Exception:
                            failures += 1
                            translated = text
                        translated_lines.append(f"{tag} {translated}")
                        original_lines.append(stripped)
                    else:
                        translated_lines.append(stripped)
                        original_lines.append(stripped)
                elif stripped:
                    calls += 1
                    try:
                        translated = translator.translate(stripped)
                    except Exception:
                        failures += 1
                        translated = stripped
                    translated_lines.append(translated)
                    original_lines.append(stripped)
                else:
                    translated_lines.append('')
                    original_lines.append('')

            if calls and failures == calls:
                return {'message': 'Translation failed: the translation service did not answer. '
                                   'Try again later.', 'code': 'translation_failed'}, 502

            translated_content = '\n'.join(translated_lines)

            if mode == 'dual':
                # Build dual display: original line + translated line interleaved
                dual_lines = []
                for orig, trans in zip(original_lines, translated_lines):
                    if orig.strip():
                        dual_lines.append(orig)
                        if trans.strip() != orig.strip():
                            dual_lines.append(f"  → {trans.strip() if not trans.strip().startswith('[') else trans.strip().split(']', 1)[-1].strip()}")
                    else:
                        dual_lines.append('')
                translated_content = '\n'.join(dual_lines)

            return {
                'translated': translated_content,
                'targetLang': target_lang,
                'mode': mode,
            }

        except ImportError:
            return {'message': 'Translation requires deep-translator package. Install with: pip install deep-translator'}, 500
        except Exception as e:
            return {'message': f'Translation failed: {str(e)}'}, 500


@api_ns_metadata.route('/metadata/lyrics/sync-generate/<int:track_id>')
class LyricsSyncGenerate(Resource):
    def post(self, track_id):
        """Generate synced lyrics from plain lyrics by aligning with audio.

        Uses faster-whisper to transcribe the audio, then aligns the existing
        plain lyrics lines to the transcription timestamps using fuzzy matching.

        Body: { content: str, model?: str }
        model override is optional; defaults to settings.metadata.whisper.model
        """
        from lyrarr.app.config import settings

        data = _json_body()
        plain_lyrics = _str_field(data, 'content', '').strip()

        # Read from config, allow per-request override
        model_size = _str_field(data, 'model') or settings.metadata.whisper.model
        device = settings.metadata.whisper.device
        compute_type = settings.metadata.whisper.compute_type

        if not plain_lyrics:
            return {'message': 'No lyrics content provided'}, 400
        # Only known model names — faster-whisper also accepts arbitrary local
        # paths / Hugging Face repos, which a request must not be able to pick.
        if model_size not in WHISPER_MODELS:
            return {'message': f"Unknown Whisper model {model_size!r} "
                               f"(expected one of: {', '.join(WHISPER_MODELS)})"}, 400

        # Parse plain lyrics into lines (skip empty)
        lyric_lines = [line.strip() for line in plain_lyrics.split('\n') if line.strip()]

        # Get track audio path
        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found or has no audio file'}, 404

        if not os.path.isfile(track.path):
            return {'message': f'Audio file not found: {track.path}'}, 404

        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return {
                'message': 'Sync generation requires faster-whisper. '
                           'Install with: pip install faster-whisper'
            }, 500

        # One transcription at a time app-wide; an interactive request doesn't
        # queue behind a batch for minutes — it is told to retry.
        if not _whisper_transcribe_lock.acquire(blocking=False):
            return {'message': 'Another transcription is running — try again when it finishes'}, 409
        try:
            model = _get_whisper_model(model_size, device, compute_type)
            transcribed_segments, language = _transcribe(model, track.path)
        except Exception as e:
            logger.exception(f"Sync generation failed for track {track_id}: {e}")
            return {'message': f'Sync generation failed: {str(e)}'}, 500
        finally:
            _whisper_transcribe_lock.release()

        if not transcribed_segments:
            return {'message': 'Could not transcribe any audio from the track'}, 400

        lrc_lines, matched = _align_lyrics(lyric_lines, transcribed_segments)

        return {
            'synced': '\n'.join(lrc_lines),
            'segments': len(transcribed_segments),
            'matched': matched,
            'total_lines': len(lyric_lines),
            'language': language,
        }


@api_ns_metadata.route('/metadata/batch-download')
class BatchDownload(Resource):
    def post(self):
        """Trigger metadata downloads for specific albums/artists in background."""
        from lyrarr.app.event_handler import event_stream
        from lyrarr.metadata.download_worker import downloads_in_progress, run_downloads

        data = _json_body()
        album_ids = _id_list(data, 'albumIds')
        artist_ids = _id_list(data, 'artistIds')
        dtype = _str_field(data, 'type', 'all')  # 'covers', 'lyrics', 'all'

        if dtype not in ('covers', 'lyrics', 'all'):
            return {'message': "type must be 'covers', 'lyrics' or 'all'"}, 400
        if album_ids is None and artist_ids is None:
            return {'message': 'albumIds or artistIds required'}, 400

        # Resolve artists to albums. A selection that resolves to nothing (e.g.
        # an artist with no albums) is a no-op — passing an empty scope to the
        # worker would mean "every album in the library".
        album_ids = _resolve_album_ids(album_ids, artist_ids)
        count = len(album_ids)
        if not count:
            return {'message': 'No albums in the selection — nothing to download', 'albumCount': 0}

        # Advisory check so the user gets told up front instead of a silent
        # skip buried in the activity feed. run_downloads' lock stays the
        # authoritative guard against the race.
        if downloads_in_progress():
            return {'message': 'Another download run is in progress — try again when it finishes'}, 409

        do_covers = dtype in ('covers', 'all')
        do_lyrics = dtype in ('lyrics', 'all')
        # Progress totals in the units the progress events count: covers per
        # album, lyrics per track.
        total_covers = total_lyrics = 0
        if do_covers:
            total_covers = database.execute(
                select(func.count()).select_from(TableAlbums).where(
                    TableAlbums.lidarrAlbumId.in_(album_ids),
                    TableAlbums.cover_status == 'missing',
                )
            ).scalar() or 0
        if do_lyrics:
            total_lyrics = database.execute(
                select(func.count()).select_from(TableTracks).where(
                    TableTracks.albumId.in_(album_ids),
                    TableTracks.lyrics_status == 'missing',
                )
            ).scalar() or 0

        def _run():
            payload = {'covers': 0, 'lyrics': 0}
            try:
                event_stream(type='download_start', payload={
                    'message': f'Batch download started for {count} album(s)',
                    'total_covers': total_covers,
                    'total_lyrics': total_lyrics,
                })

                # run_downloads holds the shared lock, so this manual batch can't
                # run concurrently with the scheduled job (or another batch) and
                # double-process the same tracks.
                result = run_downloads(
                    album_ids=album_ids,
                    do_covers=do_covers,
                    do_lyrics=do_lyrics,
                    source='manual batch',
                    # The user explicitly picked these albums/artists — retry
                    # backed-off tracks now instead of silently skipping them.
                    ignore_backoff=True,
                )

                if result.get('skipped'):
                    payload['message'] = 'Another download run is already in progress'
                else:
                    payload.update(covers=result['covers'], lyrics=result['lyrics'])
                    payload['message'] = f"Batch: {result['covers']} covers, {result['lyrics']} lyrics downloaded"
            except Exception as e:
                logger.exception(f"Batch download error: {e}")
                payload['error'] = str(e)
                payload['message'] = f'Batch download failed: {e}'
            finally:
                try:
                    event_stream(type='download_complete', payload=payload)
                finally:
                    database.remove()  # Clean up scoped session for this thread

        if not _start_job('download', _run):
            return _job_busy()

        return {'message': f'Batch download started for {count} album(s)', 'albumCount': count}


@api_ns_metadata.route('/metadata/lyrics/batch-translate')
class BatchTranslate(Resource):
    def post(self):
        """Translate lyrics in bulk for tracks with no detected_language.

        Body: { albumIds?: int[], artistIds?: int[], targetLang?: str }
        Only processes tracks where lyrics_status='available' AND detected_language IS NULL.
        Runs in a background thread.
        """
        from lyrarr.app.database import update
        from lyrarr.app.event_handler import event_stream

        data = _json_body()
        album_ids = _id_list(data, 'albumIds')
        artist_ids = _id_list(data, 'artistIds')
        target_lang = _str_field(data, 'targetLang', 'en')

        if album_ids is None and artist_ids is None:
            return {'message': 'albumIds or artistIds required'}, 400

        # Resolve artist → album IDs (an empty result simply matches no tracks)
        album_ids = _resolve_album_ids(album_ids, artist_ids)

        # Find eligible tracks: available lyrics but no detected language
        force = bool(data.get('force'))

        # Default pass fills in missing language detection (translating only
        # when detection fails); force mode translates everything in scope
        # that isn't already in the target language.
        conditions = [
            TableTracks.albumId.in_(album_ids),
            TableTracks.lyrics_status == 'available',
        ]
        if force:
            conditions.append(or_(
                TableTracks.detected_language.is_(None),
                TableTracks.detected_language != target_lang,
            ))
        else:
            conditions.append(TableTracks.detected_language.is_(None))
        tracks = database.execute(select(TableTracks).where(*conditions)).scalars().all()

        track_list = [(t.lidarrTrackId, t.path, t.title) for t in tracks]
        count = len(track_list)

        if count == 0:
            return {'message': 'No eligible tracks found (all tracks already have detected language)'}, 200

        def _run():
            log = logger
            translated = 0
            failed = 0
            reasons = []
            error = None

            try:
                event_stream(type='batch_translate_start', payload={
                    'message': f'Translating lyrics for {count} track(s)',
                    'total': count,
                })

                from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
                from lyrarr.metadata.lyrics_store import read_sidecar
                from lyrarr.metadata.manager import translate_lyrics_content

                for track_id, track_path, track_title in track_list:
                    try:
                        if not track_path:
                            continue

                        sr = read_sidecar(os.path.splitext(track_path)[0] + '.lrc')
                        if sr is None or not sr.text.strip():
                            continue
                        if not sr.lossless:
                            reasons.append('not_utf8')
                            continue
                        content = sr.text

                        # Detect language first
                        lang = detect_language(content)
                        if lang and (not force or lang == target_lang):
                            # Already in a known language (or already the target
                            # in force mode) — record it and skip translation.
                            database.execute(
                                update(TableTracks)
                                .where(TableTracks.lidarrTrackId == track_id,
                                       or_(TableTracks.language_source.is_(None),
                                           TableTracks.language_source.notin_(('manual', 'translated'))))
                                .values(detected_language=lang)
                            )
                            continue

                        # Any failed translator call (or a no-op) → nothing is saved.
                        translated_content = translate_lyrics_content(content, target_lang, 'replace')
                        if not translated_content:
                            failed += 1
                            reasons.append('translation_failed')
                            log.warning(f"Batch translate: translation of '{track_title}' failed or changed nothing")
                            continue

                        is_synced = is_synced_lyrics(translated_content)
                        lyrics_data = {
                            'synced_lyrics': translated_content if is_synced else None,
                            'plain_lyrics': None if is_synced else translated_content,
                        }
                        # The original is archived as the translation's source,
                        # so blacklisting the translation also blacklists it.
                        result = save_lyrics(track_id, lyrics_data, f'batch-translate-{target_lang}',
                                             source='translated', archive_translated_from=lang or 'und',
                                             detected_language=target_lang)
                        if not result:
                            failed += 1
                            reasons.append(getattr(result, 'reason', None) or 'write_failed')
                            log.warning(f"Batch translate: could not save translation for '{track_title}': "
                                        f"{getattr(result, 'message', '')}")
                            continue

                        translated += 1
                        log.debug(f"Batch translate: translated '{track_title}' → {target_lang}")

                    except Exception as e:
                        failed += 1
                        log.warning(f"Batch translate failed for '{track_title}': {e}")

            except ImportError:
                error = 'Batch translate requires deep-translator. Install with: pip install deep-translator'
                log.error(error)
            except Exception as e:
                error = str(e)
                log.exception(f"Batch translate error: {e}")
            finally:
                message = f'Batch translate: {translated} translated, {failed} failed'
                if reasons:
                    message += f' ({_reason_summary(reasons)})'
                payload = {
                    'translated': translated,
                    'failed': failed,
                    'message': message,
                }
                if error:
                    payload['error'] = error
                    payload['message'] = f'Batch translate failed: {error}'
                try:
                    event_stream(type='batch_translate_complete', payload=payload)
                finally:
                    database.remove()

        if not _start_job('translate', _run):
            return _job_busy()

        return {'message': f'Batch translate started for {count} track(s)', 'trackCount': count}


@api_ns_metadata.route('/metadata/lyrics/batch-sync-generate')
class BatchSyncGenerate(Resource):
    def post(self):
        """Generate synced LRC in bulk for tracks with untimed lyrics.

        Body: { albumIds?: int[], artistIds?: int[], trackIds?: int[] }
        Only processes tracks where lyrics_status='available' AND is_synced=False.
        Uses Whisper to transcribe audio and align with existing plain lyrics.
        Runs in a background thread.
        """
        from lyrarr.app.config import settings
        from lyrarr.app.event_handler import event_stream

        data = _json_body()
        album_ids = _id_list(data, 'albumIds')
        artist_ids = _id_list(data, 'artistIds')
        track_ids = _id_list(data, 'trackIds')

        if album_ids is None and artist_ids is None and track_ids is None:
            return {'message': 'albumIds, artistIds, or trackIds required'}, 400

        # Empty selections match nothing (in_([]) is false) — never the library.
        if track_ids:
            # Direct track selection
            scope = TableTracks.lidarrTrackId.in_(track_ids)
        else:
            scope = TableTracks.albumId.in_(_resolve_album_ids(album_ids, artist_ids))

        # Find eligible tracks: available lyrics but not synced
        tracks = database.execute(
            select(TableTracks).where(
                scope,
                TableTracks.lyrics_status == 'available',
                TableTracks.is_synced == False,
            )
        ).scalars().all()

        track_list = [(t.lidarrTrackId, t.path, t.title) for t in tracks]
        count = len(track_list)

        if count == 0:
            return {'message': 'No eligible tracks found (all available lyrics are already synced)'}, 200

        model_size = settings.metadata.whisper.model
        device = settings.metadata.whisper.device
        compute_type = settings.metadata.whisper.compute_type

        def _run():
            log = logger
            synced = 0
            failed = 0
            reasons = []
            error = None

            try:
                event_stream(type='batch_sync_start', payload={
                    'message': f'Generating synced lyrics for {count} track(s)',
                    'total': count,
                })

                import faster_whisper  # noqa: F401  (fail fast if missing)

                for track_id, track_path, track_title in track_list:
                    try:
                        if not track_path or not os.path.isfile(track_path):
                            continue

                        # Read current lyrics file
                        from lyrarr.metadata.lyrics_store import read_sidecar
                        sr = read_sidecar(os.path.splitext(track_path)[0] + '.lrc')
                        if sr is None or not sr.text.strip():
                            continue
                        if not sr.lossless:
                            reasons.append('not_utf8')
                            continue
                        content = sr.text

                        # Parse lyrics into non-empty lines
                        lyric_lines = [line.strip() for line in content.split('\n') if line.strip()]
                        if not lyric_lines:
                            continue

                        # Transcribe audio — waits its turn behind any other
                        # transcription (interactive or batch).
                        with _whisper_transcribe_lock:
                            model = _get_whisper_model(model_size, device, compute_type)
                            transcribed_segments, _lang = _transcribe(model, track_path)

                        if not transcribed_segments:
                            log.debug(f"Batch sync: no transcription for '{track_title}'")
                            continue

                        lrc_lines, _matched = _align_lyrics(lyric_lines, transcribed_segments)
                        synced_content = '\n'.join(lrc_lines)

                        # Save synced lyrics
                        lyrics_data = {'synced_lyrics': synced_content, 'plain_lyrics': None}
                        result = save_lyrics(track_id, lyrics_data, 'batch-sync-generate', source='generated')
                        if not result:
                            failed += 1
                            reasons.append(getattr(result, 'reason', None) or 'write_failed')
                            log.warning(f"Batch sync: could not save generated lyrics for '{track_title}': "
                                        f"{getattr(result, 'message', '')}")
                            continue

                        synced += 1
                        log.debug(f"Batch sync: generated timing for '{track_title}'")

                    except Exception as e:
                        failed += 1
                        log.warning(f"Batch sync failed for '{track_title}': {e}")

            except ImportError:
                error = 'Batch sync requires faster-whisper. Install with: pip install faster-whisper'
                log.error(error)
            except Exception as e:
                error = str(e)
                log.exception(f"Batch sync error: {e}")
            finally:
                message = f'Batch sync: {synced} synced, {failed} failed'
                if reasons:
                    message += f' ({_reason_summary(reasons)})'
                payload = {
                    'synced': synced,
                    'failed': failed,
                    'message': message,
                }
                if error:
                    payload['error'] = error
                    payload['message'] = f'Batch sync failed: {error}'
                try:
                    event_stream(type='batch_sync_complete', payload=payload)
                finally:
                    database.remove()

        if not _start_job('sync_generate', _run):
            return _job_busy()

        return {'message': f'Batch sync generation started for {count} track(s)', 'trackCount': count}


@api_ns_metadata.route('/metadata/lyrics/batch-redetect')
class LyricsBatchRedetect(Resource):
    def post(self):
        """Re-detect language and synced status for lyrics files.

        Body (optional): { trackIds?: int[] }
        If trackIds provided, only processes those tracks. Otherwise processes all available.
        """
        from lyrarr.app.database import update
        from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics

        data = _json_body()
        track_ids = _id_list(data, 'trackIds')
        # trackIds given but empty (e.g. "re-detect all listed" on an empty
        # list) is a no-op — only an absent trackIds means the whole library.
        if track_ids is not None and not track_ids:
            return {'message': 'No tracks selected — nothing to re-detect'}

        def _run():
            try:
                query = select(TableTracks).where(TableTracks.lyrics_status == 'available')
                if track_ids is not None:
                    query = query.where(TableTracks.lidarrTrackId.in_(track_ids))
                tracks = database.execute(query).scalars().all()

                updated = 0
                for track in tracks:
                    if not track.path:
                        continue
                    _fpath, sr = _read_lyrics_file(track)
                    if sr is None or not sr.text.strip():
                        continue
                    values = {'is_synced': is_synced_lyrics(sr.text)}
                    # A language the user set (or a translation's target) is
                    # never overwritten by detection; neither is one detected
                    # from a file that isn't valid UTF-8.
                    if track.language_source not in ('manual', 'translated') and sr.lossless:
                        values['detected_language'] = detect_language(sr.text)
                        values['language_source'] = 'detected'
                    database.execute(
                        update(TableTracks)
                        .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                        .values(**values)
                    )
                    updated += 1

                logger.info(f"Batch re-detect complete: {updated}/{len(tracks)} tracks updated")
            except Exception as e:
                logger.exception(f"Batch re-detect error: {e}")
            finally:
                database.remove()  # Clean up scoped session for this thread

        if not _start_job('redetect', _run):
            return _job_busy()

        return {'message': 'Batch language re-detection started'}


@api_ns_metadata.route('/metadata/lyrics/language-stats')
class LyricsLanguageStats(Resource):
    def get(self):
        """Get language distribution, synced/plain breakdown, and provider stats."""
        from lyrarr.app.database import TableHistory, func

        # Language distribution
        lang_rows = database.execute(
            select(TableTracks.detected_language, func.count())
            .where(TableTracks.lyrics_status == 'available')
            .group_by(TableTracks.detected_language)
        ).all()
        languages = {(row[0] or 'unknown'): row[1] for row in lang_rows}

        # Synced vs plain
        total_available = database.execute(
            select(func.count()).select_from(TableTracks)
            .where(TableTracks.lyrics_status == 'available')
        ).scalar() or 0
        total_synced = database.execute(
            select(func.count()).select_from(TableTracks)
            .where(TableTracks.lyrics_status == 'available')
            .where(TableTracks.is_synced == True)
        ).scalar() or 0
        total_plain = total_available - total_synced

        # Provider distribution (from history)
        # Only automatic downloads/upgrades: not manual saves, publishes,
        # classifications or audit repairs (incl. pre-2.0 rows that recorded
        # manual saves and publishes as downloads).
        from lyrarr.app.database import legacy_non_download_filter
        provider_rows = database.execute(
            select(TableHistory.provider, func.count())
            .where(TableHistory.metadata_type == 'lyrics',
                   TableHistory.action.in_((1, 2)),
                   legacy_non_download_filter())
            .group_by(TableHistory.provider)
        ).all()
        providers = {(row[0] or 'unknown'): row[1] for row in provider_rows}

        # Total tracks
        total_tracks = database.execute(
            select(func.count()).select_from(TableTracks)
        ).scalar() or 0
        total_missing = database.execute(
            select(func.count()).select_from(TableTracks)
            .where(TableTracks.lyrics_status == 'missing')
        ).scalar() or 0

        return {
            'languages': languages,
            'synced': total_synced,
            'plain': total_plain,
            'total_available': total_available,
            'total_tracks': total_tracks,
            'total_missing': total_missing,
            'providers': providers,
        }


@api_ns_metadata.route('/metadata/lyrics/import-sidecar')
class LyricsSidecarImport(Resource):
    def post(self):
        """Scan for existing .lrc sidecar files on disk and import into DB.

        For each track with lyrics_status='missing', checks if a .lrc
        file already exists alongside the audio file. If found, updates the DB
        with detected language and synced status.
        """
        from lyrarr.app.database import database as db_session
        from lyrarr.metadata.lyrics_store import adopt_sidecar, sidecar_path

        def _run():
            log = logger

            try:
                tracks = db_session.execute(
                    select(TableTracks).where(
                        TableTracks.lyrics_status.in_(['missing', 'unknown'])
                    )
                ).scalars().all()

                imported = 0
                for track in tracks:
                    path = sidecar_path(track)
                    if not path or not os.path.isfile(path):
                        continue
                    try:
                        # Same rules as sync/reconcile: only real lyrics are
                        # adopted (markers, placeholders, empty files are not).
                        if adopt_sidecar(track, keep_source=True):
                            imported += 1
                    except Exception as e:
                        log.warning(f"Failed to import sidecar for track {track.lidarrTrackId}: {e}")

                log.info(f"Sidecar import complete: {imported}/{len(tracks)} tracks updated")
            except Exception as e:
                log.exception(f"Sidecar import error: {e}")
            finally:
                db_session.remove()  # Clean up scoped session for this thread

        if not _start_job('sidecar_import', _run):
            return _job_busy()
        return {'message': 'Sidecar import started — scanning for existing lyrics files'}


@api_ns_metadata.route('/metadata/lyrics/audit')
class LyricsAudit(Resource):
    def post(self):
        """Check the library's lyrics state against the disk (legacy endpoint).

        A thin delegate to the audit: starts an apply run of the file/status
        drift checks. Returns {message, run_id}, or 409 while another audit
        runs. The full audit lives under /api/audit.
        """
        try:
            from lyrarr.utilities.audit.runner import LEGACY_ENDPOINT_CHECKS, start_run
        except ImportError as e:
            logger.error(f"Audit unavailable: {e}")
            return {'message': 'The library audit is not available in this build'}, 503
        run_id = start_run('apply', check_ids=list(LEGACY_ENDPOINT_CHECKS), trigger='legacy_endpoint')
        if run_id is None:
            return {'message': 'A library check is already running — try again when it finishes'}, 409
        return {'message': 'Library check started — see System ▸ Audit for the results', 'run_id': run_id}


@api_ns_metadata.route('/metadata/providers/health')
class ProviderHealth(Resource):
    def get(self):
        """Get health stats for all metadata providers."""
        from lyrarr.metadata.provider_utils import health_tracker
        from lyrarr.metadata.registry import cover_providers, lyrics_providers

        stats = health_tracker.get_stats()

        # Include all known providers even if they have no stats yet
        all_providers = {}
        for name in cover_providers:
            entry = stats.get(name, {
                'successes': 0, 'failures': 0, 'consecutive_failures': 0,
                'last_failure': None, 'disabled_until': None, 'available': True,
            })
            entry['type'] = 'cover'
            all_providers[name] = entry

        for name in lyrics_providers:
            if name in all_providers:
                all_providers[name]['type'] = 'both'
            else:
                entry = stats.get(name, {
                    'successes': 0, 'failures': 0, 'consecutive_failures': 0,
                    'last_failure': None, 'disabled_until': None, 'available': True,
                })
                entry['type'] = 'lyrics'
                all_providers[name] = entry

        return {'providers': all_providers}

    def post(self):
        """Reset provider health stats."""
        from lyrarr.metadata.provider_utils import health_tracker
        data = _json_body()
        provider_name = _str_field(data, 'provider')
        health_tracker.reset(provider_name)
        return {'message': f'Health stats reset for {"all providers" if not provider_name else provider_name}'}
