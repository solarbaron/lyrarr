# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableArtists, TableProfiles, select, func

api_ns_artists = Namespace('artists', description='Artist operations')


@api_ns_artists.route('/artists')
class ArtistList(Resource):
    def get(self):
        """List all artists with pagination, search, sort, and filter."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)
        search = request.args.get('search', '', type=str)
        sort_by = request.args.get('sortBy', 'name', type=str)
        sort_dir = request.args.get('sortDir', 'asc', type=str)
        monitored = request.args.get('monitored', '', type=str)
        profile_id = request.args.get('profileId', None, type=int)

        query = select(TableArtists)
        count_query = select(func.count()).select_from(TableArtists)

        if search:
            query = query.where(TableArtists.name.ilike(f'%{search}%'))
            count_query = count_query.where(TableArtists.name.ilike(f'%{search}%'))

        if monitored == 'true':
            query = query.where(TableArtists.monitored == True)
            count_query = count_query.where(TableArtists.monitored == True)
        elif monitored == 'false':
            query = query.where(TableArtists.monitored == False)
            count_query = count_query.where(TableArtists.monitored == False)

        if profile_id is not None:
            query = query.where(TableArtists.profileId == profile_id)
            count_query = count_query.where(TableArtists.profileId == profile_id)

        total = database.execute(count_query).scalar()

        # Sorting
        sort_col = TableArtists.sortName if sort_by == 'name' else TableArtists.sortName
        if sort_dir == 'desc':
            query = query.order_by(sort_col.desc())
        else:
            query = query.order_by(sort_col)

        query = query.offset((page - 1) * page_size).limit(page_size)

        rows = database.execute(query).scalars().all()

        # Attach profile names
        profiles = {p.id: p.name for p in database.execute(select(TableProfiles)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['profileName'] = profiles.get(r.profileId, 'None')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_artists.route('/artists/<int:artist_id>')
class ArtistItem(Resource):
    def get(self, artist_id):
        """Get artist by ID."""
        row = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == artist_id)
        ).scalars().first()
        if row:
            profiles = {p.id: p.name for p in database.execute(select(TableProfiles)).scalars().all()}
            d = row.to_dict()
            d['profileName'] = profiles.get(row.profileId, 'None')
            return d
        return {'message': 'Artist not found'}, 404
