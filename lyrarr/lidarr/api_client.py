# coding=utf-8

import logging
import requests
from urllib.parse import urljoin

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)


class LidarrAPI:
    """Client for the Lidarr API v1."""

    def __init__(self):
        self._session = requests.Session()

    @property
    def _base_url(self):
        protocol = 'https' if settings.lidarr.ssl else 'http'
        base = f"{protocol}://{settings.lidarr.ip}:{settings.lidarr.port}"
        api_base = settings.lidarr.base_url.rstrip('/') + '/api/v1/'
        return base + api_base

    @property
    def _headers(self):
        return {
            'X-Api-Key': settings.lidarr.apikey,
            'Content-Type': 'application/json',
        }

    def _get(self, endpoint, params=None):
        url = self._base_url + endpoint
        try:
            response = self._session.get(
                url,
                headers=self._headers,
                params=params,
                timeout=settings.lidarr.http_timeout,
                verify=False
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Lidarr API error ({endpoint}): {e}")
            return None

    def test_connection(self):
        """Test the connection to Lidarr."""
        result = self._get('system/status')
        if result:
            logger.info(f"Connected to Lidarr v{result.get('version', 'unknown')}")
            return True
        return False

    def get_artists(self):
        """Get all artists from Lidarr."""
        return self._get('artist') or []

    def get_artist(self, artist_id):
        """Get a specific artist by ID."""
        return self._get(f'artist/{artist_id}')

    def get_albums(self, artist_id=None):
        """Get all albums, optionally filtered by artist."""
        params = {}
        if artist_id is not None:
            params['artistId'] = artist_id
        return self._get('album', params=params) or []

    def get_album(self, album_id):
        """Get a specific album by ID."""
        return self._get(f'album/{album_id}')

    def get_tracks(self, album_id=None, artist_id=None):
        """Get tracks, filtered by album or artist (uses trackfile endpoint)."""
        params = {}
        if artist_id is not None:
            params['artistId'] = artist_id
        if album_id is not None:
            params['albumId'] = album_id
        return self._get('trackfile', params=params) or []

    def get_track_records(self, album_id=None, artist_id=None):
        """Get track metadata (title, number, duration) from the /track endpoint."""
        params = {}
        if artist_id is not None:
            params['artistId'] = artist_id
        if album_id is not None:
            params['albumId'] = album_id
        return self._get('track', params=params) or []

    def get_root_folders(self):
        """Get all root folders."""
        return self._get('rootfolder') or []

    def get_quality_profiles(self):
        """Get all quality profiles."""
        return self._get('qualityprofile') or []

    def get_metadata_profiles(self):
        """Get all metadata profiles."""
        return self._get('metadataprofile') or []


lidarr_api = LidarrAPI()
