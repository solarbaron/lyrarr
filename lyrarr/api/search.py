# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableArtists, TableAlbums, TableTracks, select

api_ns_search = Namespace('search', description='Global search')


@api_ns_search.route('/search')
class GlobalSearch(Resource):
    def get(self):
        """Search artists, albums, and tracks simultaneously."""
        q = request.args.get('q', '', type=str).strip()
        limit = request.args.get('limit', 5, type=int)

        if not q or len(q) < 2:
            return {'artists': [], 'albums': [], 'tracks': []}

        pattern = f'%{q}%'

        # Search artists
        artists = database.execute(
            select(TableArtists)
            .where(TableArtists.name.ilike(pattern))
            .limit(limit)
        ).scalars().all()

        # Search albums
        albums = database.execute(
            select(TableAlbums)
            .where(TableAlbums.title.ilike(pattern))
            .limit(limit)
        ).scalars().all()

        # Search tracks
        tracks = database.execute(
            select(TableTracks)
            .where(TableTracks.title.ilike(pattern))
            .limit(limit)
        ).scalars().all()

        # Resolve artist names for albums and tracks
        artist_map = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}

        return {
            'artists': [
                {'id': a.lidarrArtistId, 'name': a.name, 'poster': a.poster}
                for a in artists
            ],
            'albums': [
                {
                    'id': a.lidarrAlbumId, 'title': a.title, 'cover': a.cover,
                    'artistName': artist_map.get(a.artistId, 'Unknown'), 'year': a.year,
                }
                for a in albums
            ],
            'tracks': [
                {
                    'id': t.lidarrTrackId, 'title': t.title, 'albumId': t.albumId,
                    'artistName': artist_map.get(t.artistId, 'Unknown'),
                }
                for t in tracks
            ],
        }
