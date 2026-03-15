# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import (
    database, TableAlbums, TableTracks, TableArtists,
    select
)
from lyrarr.metadata.manager import (
    cover_providers, lyrics_providers,
    save_cover_art, save_lyrics
)

api_ns_metadata = Namespace('metadata', description='Metadata search and download')


@api_ns_metadata.route('/metadata/covers/search/<int:album_id>')
class CoverSearch(Resource):
    def get(self, album_id):
        """Search for cover art for an album across all providers."""
        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
        ).scalars().first()
        if not album:
            return {'message': 'Album not found'}, 404

        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
        ).scalars().first()

        results = []
        for name, provider in cover_providers.items():
            try:
                if name == 'musicbrainz' and album.mbId:
                    hits = provider.search(mb_release_group_id=album.mbId)
                elif name == 'fanart' and artist and artist.mbId:
                    hits = provider.search(mb_artist_id=artist.mbId)
                else:
                    hits = []
                for h in hits:
                    h['provider'] = name
                results.extend(hits)
            except Exception as e:
                pass

        return {'results': results, 'albumId': album_id}


@api_ns_metadata.route('/metadata/covers/download/<int:album_id>')
class CoverDownload(Resource):
    def post(self, album_id):
        """Download a specific cover art image and save it."""
        data = request.get_json() or {}
        url = data.get('url')
        provider_name = data.get('provider', 'musicbrainz')

        if not url:
            return {'message': 'url is required'}, 400

        provider = cover_providers.get(provider_name)
        if not provider:
            return {'message': 'Invalid provider'}, 400

        image_data = provider.download(url)
        if not image_data:
            return {'message': 'Failed to download image'}, 500

        success = save_cover_art(album_id, image_data, provider_name)
        if success:
            return {'message': 'Cover art saved successfully'}
        return {'message': 'Failed to save cover art'}, 500


@api_ns_metadata.route('/metadata/lyrics/search/<int:track_id>')
class LyricsSearch(Resource):
    def get(self, track_id):
        """Search for lyrics for a track across all providers."""
        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track:
            return {'message': 'Track not found'}, 404

        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
        ).scalars().first()

        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
        ).scalars().first()

        results = []
        for name, provider in lyrics_providers.items():
            try:
                hits = provider.search(
                    track_name=track.title,
                    artist_name=artist.name if artist else None,
                    album_name=album.title if album else None,
                    duration=track.duration,
                )
                for h in hits:
                    h['provider'] = name
                    # Truncate lyrics for preview (first 200 chars)
                    if h.get('synced_lyrics'):
                        h['synced_preview'] = h['synced_lyrics'][:300]
                    if h.get('plain_lyrics'):
                        h['plain_preview'] = h['plain_lyrics'][:300]
                results.extend(hits)
            except Exception as e:
                pass

        # Sort by score
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        return {'results': results, 'trackId': track_id}


@api_ns_metadata.route('/metadata/lyrics/download/<int:track_id>')
class LyricsDownload(Resource):
    def post(self, track_id):
        """Download/save specific lyrics for a track."""
        data = request.get_json() or {}
        lyrics_data = {
            'synced_lyrics': data.get('synced_lyrics'),
            'plain_lyrics': data.get('plain_lyrics'),
        }
        provider_name = data.get('provider', 'lrclib')

        if not lyrics_data['synced_lyrics'] and not lyrics_data['plain_lyrics']:
            return {'message': 'synced_lyrics or plain_lyrics is required'}, 400

        success = save_lyrics(track_id, lyrics_data, provider_name)
        if success:
            return {'message': 'Lyrics saved successfully'}
        return {'message': 'Failed to save lyrics'}, 500
