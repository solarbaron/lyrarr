# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableAlbums, TableTracks, TableArtists, select, func

api_ns_wanted = Namespace('wanted', description='Missing metadata')


@api_ns_wanted.route('/wanted/covers')
class WantedCovers(Resource):
    def get(self):
        """List albums with missing cover art (paginated)."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)

        base = select(TableAlbums).where(TableAlbums.cover_status == 'missing')
        count_q = select(func.count()).select_from(TableAlbums).where(TableAlbums.cover_status == 'missing')

        total = database.execute(count_q).scalar()
        rows = database.execute(
            base.order_by(TableAlbums.title)
            .offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_wanted.route('/wanted/lyrics')
class WantedLyrics(Resource):
    def get(self):
        """List tracks with missing lyrics (paginated)."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)

        base = select(TableTracks).where(TableTracks.lyrics_status == 'missing')
        count_q = select(func.count()).select_from(TableTracks).where(TableTracks.lyrics_status == 'missing')

        total = database.execute(count_q).scalar()
        rows = database.execute(
            base.order_by(TableTracks.title)
            .offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }
