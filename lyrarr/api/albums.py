# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableAlbums, TableArtists, TableTracks, TableProfiles, select, update, func

api_ns_albums = Namespace('albums', description='Album operations')


@api_ns_albums.route('/albums')
class AlbumList(Resource):
    def get(self):
        """List all albums with pagination, search, sort, and filter."""
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('pageSize', 25, type=int)
        search = request.args.get('search', '', type=str)
        artist_id = request.args.get('artistId', None, type=int)
        sort_by = request.args.get('sortBy', 'title', type=str)
        sort_dir = request.args.get('sortDir', 'asc', type=str)
        cover_status = request.args.get('coverStatus', '', type=str)
        lyrics_status = request.args.get('lyricsStatus', '', type=str)
        monitored = request.args.get('monitored', '', type=str)
        profile_id = request.args.get('profileId', None, type=int)

        query = select(TableAlbums)
        count_query = select(func.count()).select_from(TableAlbums)

        if search:
            query = query.where(TableAlbums.title.ilike(f'%{search}%'))
            count_query = count_query.where(TableAlbums.title.ilike(f'%{search}%'))

        if artist_id is not None:
            query = query.where(TableAlbums.artistId == artist_id)
            count_query = count_query.where(TableAlbums.artistId == artist_id)

        if cover_status:
            query = query.where(TableAlbums.cover_status == cover_status)
            count_query = count_query.where(TableAlbums.cover_status == cover_status)

        if lyrics_status:
            query = query.where(TableAlbums.lyrics_status == lyrics_status)
            count_query = count_query.where(TableAlbums.lyrics_status == lyrics_status)

        if monitored == 'true':
            query = query.where(TableAlbums.monitored == True)
            count_query = count_query.where(TableAlbums.monitored == True)
        elif monitored == 'false':
            query = query.where(TableAlbums.monitored == False)
            count_query = count_query.where(TableAlbums.monitored == False)

        if profile_id is not None:
            query = query.where(TableAlbums.profileId == profile_id)
            count_query = count_query.where(TableAlbums.profileId == profile_id)

        total = database.execute(count_query).scalar()

        # Sorting
        sort_map = {
            'title': TableAlbums.title,
            'year': TableAlbums.year,
        }
        sort_col = sort_map.get(sort_by, TableAlbums.title)
        if sort_dir == 'desc':
            query = query.order_by(sort_col.desc())
        else:
            query = query.order_by(sort_col)

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


@api_ns_albums.route('/albums/<int:album_id>/upload-cover')
class AlbumUploadCover(Resource):
    def post(self, album_id):
        """Upload a custom cover image for an album."""
        import os
        from datetime import datetime

        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
        ).scalars().first()
        if not album:
            return {'message': 'Album not found'}, 404

        if 'file' not in request.files:
            return {'message': 'No file provided'}, 400

        file = request.files['file']
        if not file.filename:
            return {'message': 'No file selected'}, 400

        # Validate file type
        allowed = {'png', 'jpg', 'jpeg', 'webp'}
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in allowed:
            return {'message': f'Invalid format. Allowed: {", ".join(allowed)}'}, 400

        if not album.path:
            return {'message': 'Album has no path on disk'}, 400

        # Normalize extension
        if ext == 'jpeg':
            ext = 'jpg'

        cover_data = file.read()
        filepath = os.path.join(album.path, f'cover.{ext}')
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'wb') as f:
            f.write(cover_data)

        # Update database
        database.execute(
            update(TableAlbums)
            .where(TableAlbums.lidarrAlbumId == album_id)
            .values(cover_status='available', updated_at_timestamp=datetime.now())
        )

        # Log to history
        from lyrarr.app.database import TableHistory
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        database.execute(
            sqlite_insert(TableHistory).values(
                action=1,
                description=f"Uploaded custom cover art for {album.title}",
                metadata_type='cover',
                provider='upload',
                lidarrAlbumId=album.lidarrAlbumId,
                lidarrArtistId=album.artistId,
                timestamp=datetime.now(),
                metadata_path=filepath,
            )
        )

        # Optionally embed into audio files
        try:
            profile = database.execute(
                select(TableProfiles).where(TableProfiles.id == album.profileId)
            ).scalars().first()
            if profile and getattr(profile, 'embed_cover_art', False):
                from lyrarr.metadata.download_worker import embed_cover_in_files
                embed_cover_in_files(album.path, cover_data, ext)
        except Exception:
            pass  # Non-critical

        return {'message': f'Cover art uploaded for "{album.title}"'}

