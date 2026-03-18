# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableAlbums, TableTracks, TableArtists, select, func

api_ns_wanted = Namespace('wanted', description='Missing metadata')


@api_ns_wanted.route('/wanted/covers')
class WantedCovers(Resource):
    def get(self):
        """List albums with missing cover art (paginated, searchable)."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)
        search = request.args.get('search', '', type=str)
        sort_by = request.args.get('sortBy', 'title', type=str)

        base = TableAlbums.cover_status == 'missing'
        query = select(TableAlbums).where(base)
        count_q = select(func.count()).select_from(TableAlbums).where(base)

        if search:
            like = f'%{search}%'
            query = query.where(TableAlbums.title.ilike(like))
            count_q = count_q.where(TableAlbums.title.ilike(like))

        total = database.execute(count_q).scalar()

        # Sorting
        sort_map = {'title': TableAlbums.title, 'year': TableAlbums.year}
        sort_col = sort_map.get(sort_by, TableAlbums.title)
        query = query.order_by(sort_col)

        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
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
        """List tracks with missing lyrics (paginated, searchable)."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)
        search = request.args.get('search', '', type=str)

        base = TableTracks.lyrics_status == 'missing'
        query = select(TableTracks).where(base)
        count_q = select(func.count()).select_from(TableTracks).where(base)

        if search:
            like = f'%{search}%'
            query = query.where(TableTracks.title.ilike(like))
            count_q = count_q.where(TableTracks.title.ilike(like))

        total = database.execute(count_q).scalar()

        query = query.order_by(TableTracks.title)
        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
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



@api_ns_wanted.route('/wanted/untimed')
class WantedUntimed(Resource):
    def get(self):
        """List tracks with available but unsynced lyrics (paginated, searchable)."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)
        search = request.args.get('search', '', type=str)

        base_conds = [
            TableTracks.lyrics_status == 'available',
            TableTracks.is_synced == False,
        ]
        query = select(TableTracks).where(*base_conds)
        count_q = select(func.count()).select_from(TableTracks).where(*base_conds)

        if search:
            like = f'%{search}%'
            query = query.where(TableTracks.title.ilike(like))
            count_q = count_q.where(TableTracks.title.ilike(like))

        total = database.execute(count_q).scalar()

        query = query.order_by(TableTracks.title)
        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist + album names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        albums = {a.lidarrAlbumId: a.title for a in database.execute(select(TableAlbums)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            d['albumTitle'] = albums.get(r.lidarrAlbumId, 'Unknown')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_wanted.route('/wanted/undetected')
class WantedUndetected(Resource):
    def get(self):
        """List tracks with available lyrics but no detected language (paginated, searchable)."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)
        search = request.args.get('search', '', type=str)

        base_conds = [
            TableTracks.lyrics_status == 'available',
            TableTracks.detected_language.is_(None),
        ]
        query = select(TableTracks).where(*base_conds)
        count_q = select(func.count()).select_from(TableTracks).where(*base_conds)

        if search:
            like = f'%{search}%'
            query = query.where(TableTracks.title.ilike(like))
            count_q = count_q.where(TableTracks.title.ilike(like))

        total = database.execute(count_q).scalar()

        query = query.order_by(TableTracks.title)
        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist + album names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        albums = {a.lidarrAlbumId: a.title for a in database.execute(select(TableAlbums)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            d['albumTitle'] = albums.get(r.lidarrAlbumId, 'Unknown')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_wanted.route('/wanted/stats')
class WantedStats(Resource):
    def get(self):
        """Get summary counts for missing metadata."""
        missing_covers = database.execute(
            select(func.count()).select_from(TableAlbums).where(TableAlbums.cover_status == 'missing')
        ).scalar()
        total_albums = database.execute(
            select(func.count()).select_from(TableAlbums)
        ).scalar()
        missing_lyrics = database.execute(
            select(func.count()).select_from(TableTracks).where(TableTracks.lyrics_status == 'missing')
        ).scalar()
        untimed_lyrics = database.execute(
            select(func.count()).select_from(TableTracks).where(
                TableTracks.lyrics_status == 'available',
                TableTracks.is_synced == False,
            )
        ).scalar()
        undetected_language = database.execute(
            select(func.count()).select_from(TableTracks).where(
                TableTracks.lyrics_status == 'available',
                TableTracks.detected_language.is_(None),
            )
        ).scalar()
        total_tracks = database.execute(
            select(func.count()).select_from(TableTracks)
        ).scalar()

        return {
            'missing_covers': missing_covers,
            'total_albums': total_albums,
            'missing_lyrics': missing_lyrics,
            'untimed_lyrics': untimed_lyrics,
            'undetected_language': undetected_language,
            'total_tracks': total_tracks,
            'covers_complete': total_albums - missing_covers if total_albums else 0,
            'lyrics_complete': total_tracks - missing_lyrics if total_tracks else 0,
        }
