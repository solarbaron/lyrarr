# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableTracks, select, func

api_ns_tracks = Namespace('tracks', description='Track operations')


@api_ns_tracks.route('/tracks')
class TrackList(Resource):
    def get(self):
        """List tracks with pagination."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 50, type=int)
        album_id = request.args.get('albumId', None, type=int)
        artist_id = request.args.get('artistId', None, type=int)
        language = request.args.get('language', '', type=str)
        synced = request.args.get('synced', '', type=str)

        query = select(TableTracks)
        count_query = select(func.count()).select_from(TableTracks)

        if album_id is not None:
            query = query.where(TableTracks.albumId == album_id)
            count_query = count_query.where(TableTracks.albumId == album_id)

        if artist_id is not None:
            query = query.where(TableTracks.artistId == artist_id)
            count_query = count_query.where(TableTracks.artistId == artist_id)

        if language:
            if language == 'unknown':
                query = query.where(TableTracks.detected_language.is_(None))
                count_query = count_query.where(TableTracks.detected_language.is_(None))
            else:
                query = query.where(TableTracks.detected_language == language)
                count_query = count_query.where(TableTracks.detected_language == language)

        if synced == 'true':
            query = query.where(TableTracks.is_synced == True)
            count_query = count_query.where(TableTracks.is_synced == True)
        elif synced == 'false':
            query = query.where(TableTracks.is_synced == False)
            count_query = count_query.where(TableTracks.is_synced == False)

        total = database.execute(count_query).scalar()

        query = query.order_by(TableTracks.discNumber, TableTracks.trackNumber)
        query = query.offset((page - 1) * page_size).limit(page_size)

        rows = database.execute(query).scalars().all()

        return {
            'data': [r.to_dict() for r in rows],
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_tracks.route('/tracks/<int:track_id>')
class TrackItem(Resource):
    def get(self, track_id):
        """Get track by ID."""
        row = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if row:
            return row.to_dict()
        return {'message': 'Track not found'}, 404

    def put(self, track_id):
        """Update track attributes (e.g., toggle blacklist)."""
        from flask import request
        from lyrarr.app.database import update
        from datetime import datetime

        data = request.get_json() or {}
        row = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not row:
            return {'message': 'Track not found'}, 404

        updates = {}
        if 'lyrics_status' in data:
            new_status = data['lyrics_status']
            if new_status not in ('blacklisted', 'missing'):
                return {'message': f'Invalid lyrics_status: {new_status}. Allowed: blacklisted, missing'}, 400
            if new_status == 'blacklisted':
                updates['lyrics_status'] = 'blacklisted'
                # Remove existing lyrics file
                import os
                if row.path:
                    track_base = os.path.splitext(row.path)[0]
                    for ext in ['.lrc', '.txt']:
                        fpath = track_base + ext
                        if os.path.isfile(fpath):
                            try:
                                os.remove(fpath)
                            except Exception:
                                pass
                updates['hasLyrics'] = False
            elif new_status == 'missing':
                # Un-blacklist: set to missing so downloader picks it up
                updates['lyrics_status'] = 'missing'

        if 'detected_language' in data:
            lang = data['detected_language']
            if lang is None or lang == '':
                updates['detected_language'] = None
            elif isinstance(lang, str) and len(lang) <= 5:
                updates['detected_language'] = lang.lower()
            else:
                return {'message': 'Invalid detected_language value'}, 400

        if updates:
            updates['updated_at_timestamp'] = datetime.now()
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track_id)
                .values(**updates)
            )

        return database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first().to_dict()

