# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableAlbums, TableArtists, TableTracks, TableProfiles, select, update, func

api_ns_albums = Namespace('albums', description='Album operations')


@api_ns_albums.route('/albums')
class AlbumList(Resource):
    def get(self):
        """List all albums with pagination."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)
        search = request.args.get('search', '', type=str)
        artist_id = request.args.get('artistId', None, type=int)

        query = select(TableAlbums)
        count_query = select(func.count()).select_from(TableAlbums)

        if search:
            query = query.where(TableAlbums.title.ilike(f'%{search}%'))
            count_query = count_query.where(TableAlbums.title.ilike(f'%{search}%'))

        if artist_id is not None:
            query = query.where(TableAlbums.artistId == artist_id)
            count_query = count_query.where(TableAlbums.artistId == artist_id)

        total = database.execute(count_query).scalar()

        query = query.order_by(TableAlbums.title)
        query = query.offset((page - 1) * page_size).limit(page_size)

        rows = database.execute(query).scalars().all()

        # Resolve artist names and profile names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        profiles = {p.id: p.name for p in database.execute(select(TableProfiles)).scalars().all()}

        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            d['profileName'] = profiles.get(r.profileId, 'None')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_albums.route('/albums/<int:album_id>')
class AlbumItem(Resource):
    def get(self, album_id):
        """Get album by ID with tracks."""
        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
        ).scalars().first()
        if not album:
            return {'message': 'Album not found'}, 404

        # Get artist name
        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
        ).scalars().first()

        # Get tracks for this album
        tracks = database.execute(
            select(TableTracks)
            .where(TableTracks.albumId == album_id)
            .order_by(TableTracks.discNumber, TableTracks.trackNumber)
        ).scalars().all()

        # Get profile name
        profiles = {p.id: p.name for p in database.execute(select(TableProfiles)).scalars().all()}

        d = album.to_dict()
        d['artistName'] = artist.name if artist else 'Unknown'
        d['artistMbId'] = artist.mbId if artist else None
        d['profileName'] = profiles.get(album.profileId, 'None')
        d['tracks'] = [t.to_dict() for t in tracks]

        return d

    def put(self, album_id):
        """Update album override settings."""
        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
        ).scalars().first()
        if not album:
            return {'message': 'Album not found'}, 404

        data = request.get_json() or {}
        from datetime import datetime

        values = {'updated_at_timestamp': datetime.now()}
        for field in ['override_cover_format', 'override_prefer_synced',
                      'override_download_covers', 'override_download_lyrics']:
            if field in data:
                values[field] = data[field] if data[field] else None

        database.execute(
            update(TableAlbums)
            .where(TableAlbums.lidarrAlbumId == album_id)
            .values(**values)
        )

        return {'message': 'Album updated'}
