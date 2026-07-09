"""Bulk (mass-edit) operations: the library selection tree and batch lyrics deletion.

Powers the Mass Edit page: one call returns the whole artist → album hierarchy
with per-album lyric stats and genres so the client can filter and multi-select
without paging, and batch-delete strips lyrics files for a selected scope.
"""

import ast
import logging
import os
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
          - instrumental: only files on tracks classified as instrumental

        Each file is archived into the track's version history before removal,
        so a mistaken mass delete is recoverable per track.
        """
        data = request.get_json() or {}
        album_ids = data.get('albumIds', [])
        artist_ids = data.get('artistIds', [])
        mode = data.get('mode', 'all')

        if mode not in ('all', 'plain', 'instrumental'):
            return {'message': f"Unknown mode '{mode}'"}, 400
        if not album_ids and not artist_ids:
            return {'message': 'albumIds or artistIds required'}, 400

        if artist_ids:
            albums = database.execute(
                select(TableAlbums).where(TableAlbums.artistId.in_(artist_ids))
            ).scalars().all()
            album_ids = list(set(album_ids + [a.lidarrAlbumId for a in albums]))

        query = select(TableTracks).where(TableTracks.albumId.in_(album_ids))
        if mode == 'plain':
            query = query.where(
                TableTracks.lyrics_status == 'available',
                TableTracks.is_synced == False,
            )
        elif mode == 'instrumental':
            query = query.where(TableTracks.lyrics_status == 'instrumental')
        tracks = database.execute(query).scalars().all()

        from lyrarr.metadata.lyrics_store import _archive_existing

        deleted = 0
        failed = 0
        for track in tracks:
            if not track.path:
                continue
            filepath = os.path.splitext(track.path)[0] + '.lrc'
            if not os.path.isfile(filepath):
                continue
            try:
                _archive_existing(track.lidarrTrackId, filepath, 'batch-delete')
                os.remove(filepath)
            except OSError as e:
                failed += 1
                logger.warning(f"Batch delete: could not remove lyrics for '{track.title}': {e}")
                continue

            values = {
                'hasLyrics': False,
                'is_synced': False,
                'detected_language': None,
                'updated_at_timestamp': datetime.now(),
            }
            # Instrumental tracks keep their classification (there are simply no
            # lyrics for them); everything else goes back to wanted.
            if track.lyrics_status != 'instrumental':
                values.update(
                    lyrics_status='missing',
                    lyrics_retry_count=0,
                    lyrics_retry_after=None,
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

        from lyrarr.app.event_handler import event_stream
        event_stream(type='batch_delete_complete', payload={
            'deleted': deleted,
            'failed': failed,
            'mode': mode,
            'message': f'Batch delete: removed {deleted} lyrics file(s)'
                       + (f', {failed} failed' if failed else ''),
        })

        return {
            'message': f'Deleted {deleted} lyrics file(s)'
                       + (f', {failed} failed' if failed else ''),
            'deleted': deleted,
            'failed': failed,
        }
