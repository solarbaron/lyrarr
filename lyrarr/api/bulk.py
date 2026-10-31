"""Bulk (mass-edit) operations: the library selection tree and batch lyrics deletion.

Powers the Mass Edit page: one call returns the whole artist → album hierarchy
with per-album lyric stats and genres so the client can filter and multi-select
without paging, and batch-delete strips lyrics files for a selected scope.
"""

import ast
import logging
import os
import threading
from datetime import datetime

from flask import request
from flask_restx import Namespace, Resource
from sqlalchemy import case

from lyrarr.app.database import (
    TableAlbums,
    TableArtists,
    TableTracks,
    database,
    func,
    select,
    update,
)

logger = logging.getLogger(__name__)

api_ns_bulk = Namespace('Bulk', description='Bulk library operations')

# Only one batch delete at a time: two overlapping runs over the same scope
# would race on archiving/removing the same files.
_batch_delete_lock = threading.Lock()


def _parse_genres(raw):
    """Album genres are stored as a stringified Python list (from Lidarr sync)."""
    if not raw:
        return []
    try:
        val = ast.literal_eval(raw)
        if isinstance(val, list):
            return [str(g) for g in val if g]
    except (ValueError, SyntaxError):
        pass
    return []


@api_ns_bulk.route('/library/tree')
class LibraryTree(Resource):
    def get(self):
        """Full artist → album hierarchy with per-album lyric stats and genres."""
        artists = database.execute(
            select(TableArtists).order_by(TableArtists.name)
        ).scalars().all()
        albums = database.execute(
            select(TableAlbums).order_by(TableAlbums.year)
        ).scalars().all()

        stats_rows = database.execute(
            select(
                TableTracks.albumId,
                func.count(),
                func.sum(case((TableTracks.lyrics_status == 'available', 1), else_=0)),
                func.sum(case((TableTracks.is_synced == True, 1), else_=0)),
                func.sum(case((TableTracks.lyrics_status == 'missing', 1), else_=0)),
                func.sum(case((TableTracks.lyrics_status == 'instrumental', 1), else_=0)),
            ).group_by(TableTracks.albumId)
        ).all()
        stats = {row[0]: row for row in stats_rows}

        albums_by_artist = {}
        all_genres = set()
        for a in albums:
            s = stats.get(a.lidarrAlbumId)
            genres = _parse_genres(a.genres)
            all_genres.update(genres)
            albums_by_artist.setdefault(a.artistId, []).append({
                'id': a.lidarrAlbumId,
                'title': a.title,
                'year': a.year,
                'genres': genres,
                'coverStatus': a.cover_status,
                'tracks': (s[1] or 0) if s else 0,
                'withLyrics': (s[2] or 0) if s else 0,
                'synced': (s[3] or 0) if s else 0,
                'missing': (s[4] or 0) if s else 0,
                'instrumental': (s[5] or 0) if s else 0,
            })

        data = [
            {
                'id': artist.lidarrArtistId,
                'name': artist.name,
                'albums': albums_by_artist[artist.lidarrArtistId],
            }
            for artist in artists
            if artist.lidarrArtistId in albums_by_artist
        ]

        return {'artists': data, 'genres': sorted(all_genres, key=str.lower)}


@api_ns_bulk.route('/metadata/lyrics/batch-delete')
class BatchDeleteLyrics(Resource):
    def post(self):
        """Delete lyrics files in bulk for the selected scope.

        Body: { albumIds?: int[], artistIds?: int[], mode?: 'all'|'plain'|'instrumental' }
          - all: every lyrics file in scope
          - plain: only unsynced (plain-text) lyrics
          - instrumental: per track on instrumental rows — empty/marker/placeholder/
            credits-only files (or STRONG instrumental titles) are deleted; real
            lyrics are adopted instead (the flag was wrong); override 'never' skipped

        Each file is archived into the track's version history before removal,
        so a mistaken mass delete is recoverable per track.
        """
        data = request.get_json() or {}
        album_ids = data.get('albumIds', [])
        artist_ids = data.get('artistIds', [])
        mode = data.get('mode', 'all')

        if mode not in ('all', 'plain', 'instrumental'):
            return {'message': f"Unknown mode '{mode}'"}, 400
        if not isinstance(album_ids, list) or not isinstance(artist_ids, list):
            return {'message': 'albumIds and artistIds must be lists'}, 400
        if not album_ids and not artist_ids:
            return {'message': 'albumIds or artistIds required'}, 400

        if not _batch_delete_lock.acquire(blocking=False):
            return {'message': 'A batch delete is already running'}, 409
        try:
            return self._run(album_ids, artist_ids, mode)
        finally:
            _batch_delete_lock.release()

    @staticmethod
    def _run(album_ids, artist_ids, mode):
        from lyrarr.lidarr.sync import _TERMINAL_LYRICS_STATES
        from lyrarr.metadata import lyrics_store as ls
        from lyrarr.metadata.lyrics_text import instrumental_title_tier

        if artist_ids:
            albums = database.execute(
                select(TableAlbums).where(TableAlbums.artistId.in_(artist_ids))
            ).scalars().all()
            album_ids = list(set(album_ids + [a.lidarrAlbumId for a in albums]))

        query = select(TableTracks).where(TableTracks.albumId.in_(album_ids))
        if mode == 'plain':
            query = query.where(
                TableTracks.lyrics_status == 'available',
                TableTracks.is_synced == False,  # noqa: E712
            )
        elif mode == 'instrumental':
            query = query.where(TableTracks.lyrics_status == 'instrumental')
        tracks = database.execute(query).scalars().all()

        deleted = failed = unflagged = kept = txt = 0
        for track in tracks:
            if not track.path:
                continue
            if os.path.isfile(os.path.splitext(track.path)[0] + '.txt'):
                txt += 1
                logger.warning(f"Batch delete: '{track.title}' also has an old .txt lyrics file; it is left alone")
            filepath = ls.sidecar_path(track)
            if not os.path.isfile(filepath):
                continue
            if mode == 'instrumental':
                if track.instrumental_override == 'never':
                    kept += 1
                    continue
                kind, _ = ls.read_sidecar_kind(filepath)
                deletable = kind in ('empty', 'marker', 'placeholder', 'credits_only') or \
                    instrumental_title_tier(track.title) == 'strong'
                if not deletable:
                    # Real lyrics on a track flagged instrumental: the flag was
                    # wrong, not the file. Adopt it instead of deleting it.
                    if kind == 'lyrics' and ls.adopt_sidecar(track, keep_source=True):
                        unflagged += 1
                    else:
                        kept += 1
                    continue
            # Never delete a file whose archive copy couldn't be written — it
            # would be unrecoverable.
            if not ls.archive_and_remove(track, 'batch-delete'):
                failed += 1
                logger.warning(f"Batch delete: could not archive lyrics for '{track.title}'; left in place")
                continue

            now = datetime.now()
            values = {'hasLyrics': False, 'is_synced': False, 'updated_at_timestamp': now,
                      'lyrics_source': None, 'lyrics_replace_requested': None}
            values.update(ls.cleared_provenance())
            if track.language_source != 'manual':
                values['detected_language'] = None
            # Skipped and instrumental tracks keep their state (there are simply
            # no lyrics for them); everything else goes back to wanted.
            if track.lyrics_status not in _TERMINAL_LYRICS_STATES:
                values.update(
                    lyrics_status='missing',
                    lyrics_retry_count=0,
                    lyrics_retry_after=None,
                    lyrics_transient_count=0,
                    lyrics_cooldown_count=0,
                    lyrics_retry_set_at=now,
                )
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                .values(**values)
            )
            deleted += 1

        if deleted:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            from lyrarr.app.database import TableHistory
            database.execute(
                sqlite_insert(TableHistory).values(
                    action=3,
                    description=f"Batch deleted {deleted} lyrics file(s) (mode: {mode})",
                    metadata_type='lyrics',
                    provider='batch-delete',
                    timestamp=datetime.now(),
                )
            )

        parts = [f'removed {deleted} lyrics file(s)']
        if unflagged:
            parts.append(f'{unflagged} un-flagged: real lyrics found')
        if kept:
            parts.append(f'{kept} kept')
        if failed:
            parts.append(f'{failed} failed')
        if txt:
            parts.append(f'{txt} old .txt file(s) left in place')
        from lyrarr.app.event_handler import event_stream
        event_stream(type='batch_delete_complete', payload={
            'deleted': deleted,
            'failed': failed,
            'unflagged': unflagged,
            'mode': mode,
            'message': 'Batch delete: ' + ', '.join(parts),
        })

        return {
            'message': ', '.join(parts)[0].upper() + ', '.join(parts)[1:],
            'deleted': deleted,
            'failed': failed,
            'unflagged': unflagged,
            'kept': kept,
            'txt_sidecars': txt,
        }
