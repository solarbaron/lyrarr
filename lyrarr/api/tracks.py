"""Track API: list/detail, the per-track PUT (skip/unskip, language, instrumental
override) and the bulk instrumental actions (PLAN §1.8)."""

import logging
import os
import threading
from datetime import datetime

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import TableTracks, and_, database, func, or_, select, update
from lyrarr.utilities.pagination import page_params

logger = logging.getLogger(__name__)

api_ns_tracks = Namespace('tracks', description='Track operations')

USER_LYRICS_SOURCES = ('manual', 'translated', 'generated')
INSTRUMENTAL_ACTIONS = ('not_instrumental', 'confirm', 'recheck', 'dismiss_cleared')
BULK_EVENT_THRESHOLD = 20
USER_LYRICS_MESSAGE = ("This track has lyrics you saved, translated or generated. Send force:true to mark it "
                       "instrumental anyway (the file is archived first).")


def _row(track_id):
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


def _queue_search(track_ids):
    """Start one lyrics search for the tracks in a background thread (the worker's
    job lock decides; a busy worker picks the rows up on its next pass).
    Returns True when the search could start now."""
    from lyrarr.metadata import download_worker as dw
    busy = dw.downloads_in_progress()
    ids = list(track_ids)

    def run():
        try:
            try:
                dw.run_downloads(track_ids=ids, do_covers=False, ignore_backoff=True, source='manual')
            except TypeError:
                # Worker without track scoping: search the tracks' albums instead.
                album_ids = sorted({r.albumId for r in database.execute(
                    select(TableTracks).where(TableTracks.lidarrTrackId.in_(ids))).scalars().all()})
                dw.run_downloads(album_ids=album_ids, do_covers=False, ignore_backoff=True, source='manual')
        except Exception as e:
            logger.error(f"Lyrics search for tracks {ids[:5]}... failed: {e}")
        finally:
            database.remove()

    threading.Thread(target=run, name='track-search', daemon=True).start()
    return not busy


def _emit_bulk(flagged=0, cleared=0, message=''):
    try:
        from lyrarr.app.event_handler import event_stream
        event_stream(type='instrumental_bulk', payload={'run_id': None, 'check_id': None, 'flagged': flagged,
                                                        'cleared': cleared, 'hinted': 0, 'message': message})
    except Exception as e:
        logger.debug(f"instrumental_bulk event failed: {e}")


def set_not_instrumental(row, *, emit_event=True):
    """User says "this track has vocals": override 'never', flag/hint cleared."""
    from lyrarr.metadata.lyrics_store import clear_instrumental
    if row.lyrics_status == 'instrumental' or (row.lyrics_status == 'missing' and row.instrumental_evidence):
        if clear_instrumental(row.lidarrTrackId, why='user-not-instrumental', actor='user', override='never',
                              expect_status=(row.lyrics_status,), emit_event=emit_event):
            return True
    values = {'instrumental_override': 'never', 'instrumental_prev': None, 'updated_at_timestamp': datetime.now()}
    if row.instrumental_evidence:
        values.update(instrumental_evidence=None, instrumental_reason=None)
    database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == row.lidarrTrackId).values(**values))
    return True


def set_always_instrumental(row, *, force=False, emit_event=True):
    """User says "this is instrumental". Returns (ok, reason) with reason in
    'user_lyrics' | 'archive_failed' | 'row_changed'."""
    from lyrarr.metadata import lyrics_store as ls
    if row.lyrics_source in USER_LYRICS_SOURCES and not force:
        return False, 'user_lyrics'
    path = ls.sidecar_path(row)
    if path and os.path.isfile(path):
        if not ls.archive_and_remove(row, 'user-instrumental'):
            return False, 'archive_failed'
    ok = ls.set_instrumental(row.lidarrTrackId, 'user',
                             [{'source': 'user', 'kind': 'user', 'at': datetime.now().isoformat()}],
                             actor='user', emit_event=emit_event,
                             expect_status=('missing', 'instrumental', 'available', 'blacklisted', 'manual', None))
    return (True, None) if ok else (False, 'row_changed')


def _txt_warning(row):
    if row.path and os.path.isfile(os.path.splitext(row.path)[0] + '.txt'):
        logger.warning(f"'{row.title}': an old .txt lyrics file exists next to the audio (audit F-10 handles it)")


@api_ns_tracks.route('/tracks')
class TrackList(Resource):
    def get(self):
        """List tracks with pagination and filters."""
        page, page_size = page_params(50)
        album_id = request.args.get('albumId', None, type=int)
        artist_id = request.args.get('artistId', None, type=int)
        language = request.args.get('language', '', type=str)
        synced = request.args.get('synced', '', type=str)
        lyrics_status = request.args.get('lyricsStatus', '', type=str)
        reason = request.args.get('instrumentalReason', '', type=str)
        hint = request.args.get('hint', '', type=str)
        cleared = request.args.get('cleared', '', type=str)

        T = TableTracks
        conds = []
        if album_id is not None:
            conds.append(T.albumId == album_id)
        if artist_id is not None:
            conds.append(T.artistId == artist_id)
        if language:
            conds.append(T.detected_language.is_(None) if language == 'unknown' else T.detected_language == language)
        if synced == 'true':
            conds.append(T.is_synced == True)  # noqa: E712
        elif synced == 'false':
            conds.append(T.is_synced == False)  # noqa: E712
        if lyrics_status:
            statuses = [s.strip() for s in lyrics_status.split(',') if s.strip()]
            from lyrarr.app.database import LYRICS_STATUSES
            bad = [s for s in statuses if s not in LYRICS_STATUSES]
            if bad:
                return {'message': f"Unknown lyricsStatus: {', '.join(bad)}"}, 400
            conds.append(T.lyrics_status.in_(statuses))
        if reason:
            if reason not in ('title', 'consensus', 'user', 'legacy'):
                return {'message': f"Unknown instrumentalReason '{reason}'"}, 400
            conds.append(T.lyrics_status == 'instrumental')
            conds.append(T.instrumental_reason.is_(None) if reason == 'legacy' else T.instrumental_reason == reason)
        if hint == 'true':
            conds.append(and_(T.lyrics_status == 'missing', T.instrumental_evidence.is_not(None)))
        if cleared == 'true':
            conds.append(T.instrumental_prev.is_not(None))

        total = database.execute(select(func.count()).select_from(T).where(*conds)).scalar()
        query = select(T).where(*conds).order_by(T.discNumber, T.trackNumber)
        rows = database.execute(query.offset((page - 1) * page_size).limit(page_size)).scalars().all()
        return {'data': [r.to_dict() for r in rows], 'total': total, 'page': page, 'pageSize': page_size}


@api_ns_tracks.route('/tracks/instrumental')
class TrackInstrumentalBulk(Resource):
    def post(self):
        """Bulk instrumental actions: not_instrumental | confirm | recheck | dismiss_cleared."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {'message': 'Body must be a JSON object'}, 400
        ids = data.get('trackIds')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 5000
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            return {'message': 'trackIds must be a list of 1..5000 track ids'}, 400
        action = data.get('action')
        if action not in INSTRUMENTAL_ACTIONS:
            return {'message': f"action must be one of: {', '.join(INSTRUMENTAL_ACTIONS)}"}, 400
        search = data.get('search', False)
        force = data.get('force', False)
        if not isinstance(search, bool) or not isinstance(force, bool):
            return {'message': 'search and force must be booleans'}, 400

        ids = list(dict.fromkeys(ids))
        rows = {r.lidarrTrackId: r for r in database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId.in_(ids))).scalars().all()}
        skipped = [{'trackId': i, 'reason': 'not_found'} for i in ids if i not in rows]
        bulk = len(ids) > BULK_EVENT_THRESHOLD
        emit = not bulk
        updated = flagged = cleared = 0

        if action == 'dismiss_cleared':
            from lyrarr.metadata.lyrics_store import dismiss_cleared
            updated = dismiss_cleared([i for i in ids if i in rows])
            return {'updated': updated, 'skipped': skipped, 'queued': False}

        for track_id in ids:
            row = rows.get(track_id)
            if row is None:
                continue
            if action == 'not_instrumental':
                was = row.lyrics_status == 'instrumental'
                set_not_instrumental(row, emit_event=emit)
                updated += 1
                cleared += int(was)
            elif action == 'confirm':
                ok, reason = set_always_instrumental(row, force=force, emit_event=emit)
                if ok:
                    updated += 1
                    flagged += 1
                else:
                    skipped.append({'trackId': track_id, 'reason': reason})
            elif action == 'recheck':
                from lyrarr.metadata.lyrics_store import clear_instrumental
                if row.lyrics_status == 'instrumental' or (row.lyrics_status == 'missing' and row.instrumental_evidence):
                    if clear_instrumental(track_id, why='user-recheck', actor='user',
                                          expect_status=(row.lyrics_status,), emit_event=emit):
                        updated += 1
                        cleared += int(row.lyrics_status == 'instrumental')
                        continue
                skipped.append({'trackId': track_id, 'reason': 'not_instrumental'})

        queued = False
        if search and action in ('not_instrumental', 'recheck') and updated:
            targets = [i for i in ids if i in rows and not any(s['trackId'] == i for s in skipped)]
            queued = _queue_search(targets)
        if bulk and (flagged or cleared):
            parts = ([f'{flagged} marked instrumental'] if flagged else []) + \
                    ([f'{cleared} instrumental flag(s) cleared'] if cleared else [])
            _emit_bulk(flagged, cleared, 'Instrumental review: ' + ', '.join(parts))
        return {'updated': updated, 'skipped': skipped, 'queued': queued}


@api_ns_tracks.route('/tracks/<int:track_id>')
class TrackItem(Resource):
    def get(self, track_id):
        """Get track by ID."""
        row = _row(track_id)
        if row:
            return row.to_dict()
        return {'message': 'Track not found'}, 404

    def put(self, track_id):
        """Update a track: lyrics_status (skip/unskip), detected_language, instrumental_override."""
        from lyrarr.metadata import lyrics_store as ls

        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return {'message': 'Body must be a JSON object'}, 400
        row = _row(track_id)
        if not row:
            return {'message': 'Track not found'}, 404
        force = data.get('force', False) is True
        search = data.get('search', False) is True
        queued = False

        if 'instrumental_override' in data:
            override = data['instrumental_override']
            if override not in ('never', 'always', None):
                return {'message': "instrumental_override must be 'never', 'always' or null"}, 400
            if override == 'never':
                set_not_instrumental(row)
                if search:
                    queued = _queue_search([track_id])
            elif override == 'always':
                ok, reason = set_always_instrumental(row, force=force)
                if not ok:
                    if reason == 'user_lyrics':
                        return {'message': USER_LYRICS_MESSAGE, 'code': 'user_lyrics'}, 409
                    if reason == 'archive_failed':
                        return {'message': ls.PERSIST_REASONS['archive_failed'], 'code': 'archive_failed'}, 500
                    return {'message': ls.PERSIST_REASONS['row_changed'], 'code': 'row_changed'}, 409
            else:
                database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == track_id)
                                 .values(instrumental_override=None, updated_at_timestamp=datetime.now()))
            row = _row(track_id)

        if 'lyrics_status' in data:
            new_status = data['lyrics_status']
            if new_status not in ('blacklisted', 'missing'):
                return {'message': f'Invalid lyrics_status: {new_status}. Allowed: blacklisted, missing'}, 400
            if new_status == 'blacklisted':
                err = self._blacklist(row)
                if err:
                    return err
            elif row.lyrics_status != 'missing':
                err = self._unskip(row)
                if err:
                    return err
            row = _row(track_id)

        if 'detected_language' in data:
            lang = data['detected_language']
            if lang is None or lang == '':
                values = {'detected_language': None, 'language_source': None}
            elif isinstance(lang, str) and len(lang) <= 5:
                values = {'detected_language': lang.lower(), 'language_source': 'manual'}
            else:
                return {'message': 'Invalid detected_language value'}, 400
            values['updated_at_timestamp'] = datetime.now()
            database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values))

        track = _row(track_id).to_dict()
        return {**track, 'track': track, 'queued': queued}

    @staticmethod
    def _blacklist(row):
        from lyrarr.metadata import lyrics_store as ls
        _txt_warning(row)
        fpath = ls.sidecar_path(row)
        if fpath and os.path.isfile(fpath):
            # Archived into the track's version history first so it can be restored.
            if not ls._archive_existing(row.lidarrTrackId, fpath, origin_provider=ls._origin_provider(row),
                                        replaced_by='blacklist'):
                return {'message': 'Could not archive the existing lyrics; the file was left in place',
                        'code': 'archive_failed'}, 500
            try:
                os.remove(fpath)
            except OSError as e:
                return {'message': f'Could not remove the lyrics file: {e}'}, 500
        now = datetime.now()
        values = dict(lyrics_status='blacklisted', hasLyrics=False, is_synced=False, lyrics_source=None,
                      lyrics_replace_requested=None, instrumental_reason=None, updated_at_timestamp=now)
        values.update(ls.cleared_provenance())
        if row.language_source != 'manual':
            values['detected_language'] = None
        database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == row.lidarrTrackId).values(**values))
        return None

    @staticmethod
    def _unskip(row):
        from lyrarr.metadata import lyrics_store as ls
        if row.lyrics_status not in ('blacklisted', 'instrumental'):
            return {'message': f"Only skipped or instrumental tracks can be set to missing "
                               f"(this one is '{row.lyrics_status}')"}, 400
        if row.lyrics_status == 'instrumental':
            # Never touches the override (no `override` argument).
            if not ls.clear_instrumental(row.lidarrTrackId, why='user-unskip', actor='user',
                                         expect_status=('instrumental',)):
                return {'message': ls.PERSIST_REASONS['row_changed'], 'code': 'row_changed'}, 409
        now = datetime.now()
        values = dict(lyrics_status='missing', hasLyrics=False, lyrics_source=None, lyrics_retry_count=0,
                      lyrics_retry_after=None, lyrics_transient_count=0, lyrics_cooldown_count=0,
                      lyrics_retry_set_at=now, lyrics_replace_requested=None, updated_at_timestamp=now)
        kind, _ = ls.read_sidecar_kind(ls.sidecar_path(row))
        if kind is None:
            values['is_synced'] = False
            if row.language_source != 'manual':
                values['detected_language'] = None
        database.execute(update(TableTracks).where(
            TableTracks.lidarrTrackId == row.lidarrTrackId,
            or_(TableTracks.lyrics_status == 'blacklisted', TableTracks.lyrics_status == 'missing')).values(**values))
        if kind == 'lyrics':
            ls.adopt_sidecar(row.lidarrTrackId, keep_source=False)
        return None


@api_ns_tracks.route('/tracks/<int:track_id>/stream')
class TrackStream(Resource):
    def get(self, track_id):
        """Stream a track's audio file for in-browser playback.

        Supports Range headers for seeking. Used by the synced lyrics preview player.
        """
        import os

        from flask import send_file

        row = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not row or not row.path:
            return {'message': 'Track not found'}, 404

        if not os.path.isfile(row.path):
            return {'message': 'Audio file not found on disk'}, 404

        # Map extension to MIME type
        ext = os.path.splitext(row.path)[1].lower()
        mime_types = {
            '.mp3': 'audio/mpeg',
            '.flac': 'audio/flac',
            '.m4a': 'audio/mp4',
            '.ogg': 'audio/ogg',
            '.opus': 'audio/opus',
            '.wav': 'audio/wav',
            '.wma': 'audio/x-ms-wma',
            '.aac': 'audio/aac',
        }
        mimetype = mime_types.get(ext, 'application/octet-stream')

        return send_file(
            row.path,
            mimetype=mimetype,
            conditional=True,  # Enables Range header support
        )

