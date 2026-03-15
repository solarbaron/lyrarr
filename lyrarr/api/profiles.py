# coding=utf-8

from datetime import datetime
from flask import request
from flask_restx import Namespace, Resource
from lyrarr.app.database import (
    database, TableProfiles, TableArtists, TableAlbums,
    select, update, delete
)

api_ns_profiles = Namespace('profiles', description='Metadata profile operations')


@api_ns_profiles.route('/profiles')
class ProfileList(Resource):
    def get(self):
        """List all profiles."""
        rows = database.execute(
            select(TableProfiles).order_by(TableProfiles.name)
        ).scalars().all()
        return [r.to_dict() for r in rows]

    def post(self):
        """Create a new profile."""
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        if not name:
            return {'message': 'Profile name is required'}, 400

        # Check for duplicate name
        existing = database.execute(
            select(TableProfiles).where(TableProfiles.name == name)
        ).scalars().first()
        if existing:
            return {'message': f'Profile "{name}" already exists'}, 409

        from sqlalchemy.dialects.sqlite import insert
        now = datetime.now()
        result = database.execute(
            insert(TableProfiles).values(
                name=name,
                is_default='False',
                download_covers=data.get('download_covers', 'True'),
                download_lyrics=data.get('download_lyrics', 'True'),
                cover_providers=data.get('cover_providers', '["musicbrainz","fanart"]'),
                lyrics_providers=data.get('lyrics_providers', '["lrclib","genius"]'),
                prefer_synced_lyrics=data.get('prefer_synced_lyrics', 'True'),
                cover_format=data.get('cover_format', 'jpg'),
                overwrite_existing=data.get('overwrite_existing', 'False'),
                created_at_timestamp=now,
                updated_at_timestamp=now,
            )
        )

        profile = database.execute(
            select(TableProfiles).where(TableProfiles.id == result.lastrowid)
        ).scalars().first()
        return profile.to_dict(), 201


@api_ns_profiles.route('/profiles/<int:profile_id>')
class ProfileItem(Resource):
    def get(self, profile_id):
        """Get profile by ID."""
        profile = database.execute(
            select(TableProfiles).where(TableProfiles.id == profile_id)
        ).scalars().first()
        if not profile:
            return {'message': 'Profile not found'}, 404
        return profile.to_dict()

    def put(self, profile_id):
        """Update a profile."""
        profile = database.execute(
            select(TableProfiles).where(TableProfiles.id == profile_id)
        ).scalars().first()
        if not profile:
            return {'message': 'Profile not found'}, 404

        data = request.get_json() or {}
        values = {'updated_at_timestamp': datetime.now()}

        for field in ['name', 'download_covers', 'download_lyrics', 'cover_providers',
                      'lyrics_providers', 'prefer_synced_lyrics', 'cover_format', 'overwrite_existing',
                      'embed_cover_art']:
            if field in data:
                values[field] = data[field]

        # If setting as default, unset all others first
        if data.get('is_default') == 'True':
            database.execute(
                update(TableProfiles).values(is_default='False')
            )
            values['is_default'] = 'True'

        database.execute(
            update(TableProfiles)
            .where(TableProfiles.id == profile_id)
            .values(**values)
        )

        updated = database.execute(
            select(TableProfiles).where(TableProfiles.id == profile_id)
        ).scalars().first()
        return updated.to_dict()

    def delete(self, profile_id):
        """Delete a profile. Cannot delete the default profile."""
        profile = database.execute(
            select(TableProfiles).where(TableProfiles.id == profile_id)
        ).scalars().first()
        if not profile:
            return {'message': 'Profile not found'}, 404
        if profile.is_default == 'True':
            return {'message': 'Cannot delete the default profile'}, 400

        # Get the default profile to reassign
        default = database.execute(
            select(TableProfiles).where(TableProfiles.is_default == 'True')
        ).scalars().first()
        default_id = default.id if default else None

        # Reassign artists and albums to default
        database.execute(
            update(TableArtists)
            .where(TableArtists.profileId == profile_id)
            .values(profileId=default_id)
        )
        database.execute(
            update(TableAlbums)
            .where(TableAlbums.profileId == profile_id)
            .values(profileId=default_id)
        )

        database.execute(
            delete(TableProfiles).where(TableProfiles.id == profile_id)
        )
        return {'message': 'Profile deleted'}


@api_ns_profiles.route('/profiles/mass-assign')
class ProfileMassAssign(Resource):
    def post(self):
        """Mass-assign a profile to artists and/or albums."""
        data = request.get_json() or {}
        profile_id = data.get('profileId')
        artist_ids = data.get('artistIds', [])
        album_ids = data.get('albumIds', [])

        if profile_id is None:
            return {'message': 'profileId is required'}, 400

        # Verify profile exists
        profile = database.execute(
            select(TableProfiles).where(TableProfiles.id == profile_id)
        ).scalars().first()
        if not profile:
            return {'message': 'Profile not found'}, 404

        updated_artists = 0
        updated_albums = 0

        if artist_ids:
            for aid in artist_ids:
                # Update the artist's own profileId
                database.execute(
                    update(TableArtists)
                    .where(TableArtists.lidarrArtistId == aid)
                    .values(profileId=profile_id)
                )
                updated_artists += 1

                # Cascade: set the same profile on all of this artist's albums
                artist_albums = database.execute(
                    select(TableAlbums).where(TableAlbums.artistId == aid)
                ).scalars().all()
                for album in artist_albums:
                    database.execute(
                        update(TableAlbums)
                        .where(TableAlbums.lidarrAlbumId == album.lidarrAlbumId)
                        .values(profileId=profile_id)
                    )
                    updated_albums += 1

        if album_ids:
            for aid in album_ids:
                database.execute(
                    update(TableAlbums)
                    .where(TableAlbums.lidarrAlbumId == aid)
                    .values(profileId=profile_id)
                )
                updated_albums += 1

        return {
            'message': f'Assigned profile "{profile.name}" to {updated_artists} artists, {updated_albums} albums',
            'updated_artists': updated_artists,
            'updated_albums': updated_albums,
        }
