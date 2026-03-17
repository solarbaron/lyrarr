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
