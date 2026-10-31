
import logging

import requests

from lyrarr.app.config import settings
from lyrarr.metadata.base import CoverProvider
from lyrarr.metadata.cover_fetch import safe_download
from lyrarr.metadata.provider_utils import is_transient_status, note_transient_error

logger = logging.getLogger(__name__)

FANART_BASE_URL = 'https://webservice.fanart.tv/v3'


class FanartCoverProvider(CoverProvider):
    """Fetch album cover art from fanart.tv (requires API key)."""

    name = 'fanart'

    @property
    def _api_key(self):
        return settings.fanart.apikey

    def search(self, mb_artist_id=None, mb_album_id=None, mb_release_group_id=None, **kwargs):
        """
        Search for an album's cover art by MusicBrainz Release Group ID.

        fanart.tv v3 nests album art per release group:
        {"albums": {"<release-group-mbid>": {"albumcover": [...], "cdart": [...]}}}.
        Only that release group's albumcover entries are returned (best-liked
        first) — artist thumbs, backgrounds and logos aren't album covers, and
        another album's cover would be wrong. Without a release group ID there
        is nothing to return.

        Returns a list of cover art results:
        [{'url': str, 'type': str, 'provider': str, 'likes': int}]
        """
        if not self._api_key:
            logger.debug("fanart.tv API key not configured, skipping")
            return []

        release_group = mb_album_id or mb_release_group_id
        if not release_group:
            logger.debug("fanart.tv: no release group ID — artist art isn't an album cover, skipping")
            return []

        data = self._fetch(f"{FANART_BASE_URL}/music/albums/{release_group}")
        if data is None and mb_artist_id:
            # Fall back to the artist document, which carries the same albums map.
            data = self._fetch(f"{FANART_BASE_URL}/music/{mb_artist_id}")
        return self.parse_album_covers(data, release_group)

    def parse_album_covers(self, data, release_group):
        """Extract one release group's album covers from a fanart.tv v3 document."""
        if not isinstance(data, dict):
            return []
        albums = data.get('albums')
        if not isinstance(albums, dict):
            return []
        album = albums.get(release_group) or albums.get(str(release_group).lower())
        if not isinstance(album, dict):
            return []

        covers = [c for c in (album.get('albumcover') or []) if isinstance(c, dict) and c.get('url')]

        def _likes(c):
            try:
                return int(c.get('likes') or 0)
            except (TypeError, ValueError):
                return 0

        covers.sort(key=_likes, reverse=True)
        return [{
            'url': c['url'],
            'type': 'front',
            'provider': self.name,
            'likes': _likes(c),
        } for c in covers]

    def _fetch(self, url):
        """GET a fanart.tv v3 document; None on 404/errors (transient ones flagged)."""
        try:
            response = requests.get(
                url,
                params={'api_key': self._api_key},
                timeout=15
            )
        except requests.exceptions.RequestException as e:
            note_transient_error()
            logger.error(f"fanart.tv fetch error: {e}")
            return None

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                logger.warning("fanart.tv returned a non-JSON body")
                return None
        if response.status_code == 404:
            logger.debug(f"No fanart.tv art found at {url}")
        else:
            if is_transient_status(response.status_code):
                note_transient_error()
            logger.warning(f"fanart.tv returned {response.status_code}")
        return None

    def download(self, url):
        """Download image from URL (allow-listed hosts only). Returns bytes or None."""
        return safe_download(url, self.name)
