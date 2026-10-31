
from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import TableArtists, TableProfiles, database, func, select, update
from lyrarr.utilities.pagination import page_params

api_ns_artists = Namespace('artists', description='Artist operations')


@api_ns_artists.route('/artists')
class ArtistList(Resource):
    def get(self):
        """List all artists with pagination, search, sort, and filter."""
        page, page_size = page_params(25)
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

        # Sorting (sortName can be blank for artists Lidarr sent without one)
        name_col = func.lower(func.coalesce(func.nullif(TableArtists.sortName, ''), TableArtists.name))
        sort_map = {
            'name': name_col,
            'sortName': name_col,
            'added': TableArtists.created_at_timestamp,
            'monitored': TableArtists.monitored,
        }
        sort_col = sort_map.get(sort_by, name_col)
        if sort_dir == 'desc':
            query = query.order_by(sort_col.desc(), name_col)
        else:
            query = query.order_by(sort_col, name_col)

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

    def put(self, artist_id):
        """Update artist settings (language override, profile, etc.)."""
        from datetime import datetime
        data = request.get_json() or {}
        allowed = ['language_override', 'translate_target_override', 'profileId']
        values = {k: data[k] for k in allowed if k in data}
        if not values:
            return {'message': 'No valid fields to update'}, 400

        values['updated_at_timestamp'] = datetime.now()
        database.execute(
            update(TableArtists)
            .where(TableArtists.lidarrArtistId == artist_id)
            .values(**values)
        )
        return {'message': 'Artist updated'}
