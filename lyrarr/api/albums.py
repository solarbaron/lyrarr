
import logging

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import TableAlbums, TableArtists, TableProfiles, TableTracks, database, func, select, update
from lyrarr.utilities.album_status import (
    album_track_stats,
    filter_values,
    lyrics_status_expr,
    status_from_track_states,
)
from lyrarr.utilities.pagination import page_params

logger = logging.getLogger(__name__)

api_ns_albums = Namespace('albums', description='Album operations')


@api_ns_albums.route('/albums')
class AlbumList(Resource):
    def get(self):
        """List all albums with pagination, search, sort, and filter."""
        page, page_size = page_params(25)
        search = request.args.get('search', '', type=str)
        artist_id = request.args.get('artistId', None, type=int)
        sort_by = request.args.get('sortBy', 'title', type=str)
        sort_dir = request.args.get('sortDir', 'asc', type=str)
        cover_status = request.args.get('coverStatus', '', type=str)
        lyrics_status = request.args.get('lyricsStatus', '', type=str)
        monitored = request.args.get('monitored', '', type=str)
        profile_id = request.args.get('profileId', None, type=int)

        # Album lyrics status is derived from the tracks (see album_status).
        stats = album_track_stats()
        status_expr = lyrics_status_expr(stats)
        query = (
            select(TableAlbums, status_expr.label('computed_lyrics_status'))
            .outerjoin(stats, stats.c.album_id == TableAlbums.lidarrAlbumId)
        )

        if search:
            query = query.where(TableAlbums.title.ilike(f'%{search}%'))

        if artist_id is not None:
            query = query.where(TableAlbums.artistId == artist_id)

        if cover_status:
            # 'available' covers both downloaded and user-uploaded art.
            if cover_status == 'available':
                query = query.where(TableAlbums.cover_status.in_(('available', 'manual')))
            else:
                query = query.where(TableAlbums.cover_status == cover_status)

        if lyrics_status:
            wanted = filter_values(lyrics_status)
            if wanted is None:
                return {'message': f'Unknown lyricsStatus: {lyrics_status}'}, 400
            query = query.where(status_expr.in_(wanted))

        if monitored == 'true':
            query = query.where(TableAlbums.monitored == True)
        elif monitored == 'false':
            query = query.where(TableAlbums.monitored == False)

        if profile_id is not None:
            query = query.where(TableAlbums.profileId == profile_id)

        total = database.execute(select(func.count()).select_from(query.subquery())).scalar()

        # Sorting
        sort_map = {
            'title': TableAlbums.title,
            'year': TableAlbums.year,
            'added': TableAlbums.created_at_timestamp,
            'lyricsStatus': status_expr,
        }
        sort_col = sort_map.get(sort_by, TableAlbums.title)
        if sort_dir == 'desc':
            query = query.order_by(sort_col.desc(), TableAlbums.title)
        else:
            query = query.order_by(sort_col, TableAlbums.title)

        query = query.offset((page - 1) * page_size).limit(page_size)

        rows = database.execute(query).all()

        # Resolve artist names and profile names
        artist_ids = {album.artistId for album, _ in rows}
        artists = {
            a.lidarrArtistId: a.name
            for a in database.execute(
                select(TableArtists).where(TableArtists.lidarrArtistId.in_(artist_ids))
            ).scalars().all()
        } if artist_ids else {}
        profiles = {p.id: p.name for p in database.execute(select(TableProfiles)).scalars().all()}

        data = []
        for r, computed_status in rows:
            d = r.to_dict()
            d['lyrics_status'] = computed_status
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
        track_query = (
            select(TableTracks)
            .where(TableTracks.albumId == album_id)
            .order_by(TableTracks.discNumber, TableTracks.trackNumber)
        )
        tracks = database.execute(track_query).scalars().all()

        # Reconcile against disk on view, so lyrics files deleted (or added)
        # outside lyrarr show correctly without waiting for the next sync.
        from lyrarr.metadata.lyrics_store import reconcile_track_lyrics
        changed = [reconcile_track_lyrics(t) for t in tracks]
        if any(changed):
            tracks = database.execute(track_query).scalars().all()

        # Get profile name
        profiles = {p.id: p.name for p in database.execute(select(TableProfiles)).scalars().all()}

        d = album.to_dict()
        d['lyrics_status'] = status_from_track_states(t.lyrics_status for t in tracks)
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
                # None/'' clears the override; False is a real value ("disabled")
                # and must be preserved, not collapsed into "no override".
                val = data[field]
                values[field] = None if val is None or val == '' else val

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
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from lyrarr.app.database import TableHistory
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
                from lyrarr.metadata.embed import embed_cover_in_files
                track_paths = database.execute(
                    select(TableTracks.path).where(TableTracks.albumId == album.lidarrAlbumId)
                ).scalars().all()
                embed_cover_in_files(album.path, cover_data, ext, track_paths=[p for p in track_paths if p])
        except Exception as e:
            logger.warning(f"Embedding uploaded cover for album {album.lidarrAlbumId} failed: {e}")

        return {'message': f'Cover art uploaded for "{album.title}"'}

