
import logging

import requests

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)


class LidarrError(Exception):
    """Lidarr could not be queried (unreachable, timeout, auth/server error, bad JSON).

    Distinct from "not found": a failed request says nothing about whether a
    resource exists, so callers must never treat it as a deletion.
    """


class LidarrAPI:
    """Client for the Lidarr API v1.

    Single-resource getters return None only when Lidarr answers 404; every
    other failure raises LidarrError.
    """

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
        """GET an API endpoint. Returns parsed JSON, or None on HTTP 404.

        Raises LidarrError for anything else that goes wrong.
        """
        url = self._base_url + endpoint
        try:
            response = self._session.get(
                url,
                headers=self._headers,
                params=params,
                timeout=settings.lidarr.http_timeout,
                verify=getattr(settings.lidarr, 'verify_ssl', False),
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Lidarr API error ({endpoint}): {e}")
            raise LidarrError(f"{endpoint}: {e}") from e
        if response.status_code == 404:
            logger.debug(f"Lidarr API 404 ({endpoint})")
            return None
        try:
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f"Lidarr API error ({endpoint}): {e}")
            raise LidarrError(f"{endpoint}: {e}") from e

    def _get_list(self, endpoint, params=None):
        """GET a collection endpoint; always a list (raises LidarrError on failure)."""
        result = self._get(endpoint, params=params)
        if result is None:
            return []
        if not isinstance(result, list):
            raise LidarrError(f"{endpoint}: expected a list, got {type(result).__name__}")
        return result

    def test_connection(self):
        """Test the connection to Lidarr."""
        try:
            result = self._get('system/status')
        except LidarrError:
            return False
        if result:
            logger.info(f"Connected to Lidarr v{result.get('version', 'unknown')}")
            return True
        return False

    def get_artists(self):
        """Get all artists from Lidarr."""
        return self._get_list('artist')

    def get_artist(self, artist_id):
        """Get a specific artist by ID (None if Lidarr says it doesn't exist)."""
        return self._get(f'artist/{artist_id}')

    def get_albums(self, artist_id=None):
        """Get all albums, optionally filtered by artist."""
        params = {}
        if artist_id is not None:
            params['artistId'] = artist_id
        return self._get_list('album', params=params)

    def get_album(self, album_id):
        """Get a specific album by ID (None if Lidarr says it doesn't exist)."""
        return self._get(f'album/{album_id}')

    def get_tracks(self, album_id=None, artist_id=None):
        """Get tracks, filtered by album or artist (uses trackfile endpoint)."""
        params = {}
        if artist_id is not None:
            params['artistId'] = artist_id
        if album_id is not None:
            params['albumId'] = album_id
        return self._get_list('trackfile', params=params)

    def get_track_records(self, album_id=None, artist_id=None):
        """Get track metadata (title, number, duration) from the /track endpoint."""
        params = {}
        if artist_id is not None:
            params['artistId'] = artist_id
        if album_id is not None:
            params['albumId'] = album_id
        return self._get_list('track', params=params)

    def get_root_folders(self):
        """Get all root folders."""
        return self._get_list('rootfolder')

    def get_quality_profiles(self):
        """Get all quality profiles."""
        return self._get_list('qualityprofile')

    def get_metadata_profiles(self):
        """Get all metadata profiles."""
        return self._get_list('metadataprofile')


lidarr_api = LidarrAPI()
