
import logging

import requests

from lyrarr.metadata.base import CoverProvider
from lyrarr.metadata.cover_fetch import safe_download
from lyrarr.metadata.provider_utils import is_transient_status, note_transient_error

logger = logging.getLogger(__name__)

# Cover Art Archive base URL
CAA_BASE_URL = 'https://coverartarchive.org'


class MusicBrainzCoverProvider(CoverProvider):
    """Fetch cover art from the MusicBrainz Cover Art Archive (free, no API key needed)."""

    name = 'musicbrainz'

    def search(self, mb_release_id=None, mb_release_group_id=None, **kwargs):
        """
        Search for cover art by MusicBrainz Release or Release Group ID.

        Returns a list of cover art results:
        [{'url': str, 'type': str, 'size': str, 'provider': str}]
        """
        results = []

        if mb_release_group_id:
            results.extend(self._fetch_from_release_group(mb_release_group_id))

        if mb_release_id:
            results.extend(self._fetch_from_release(mb_release_id))

        return results

    def _fetch_from_release_group(self, release_group_id):
        """Fetch cover art for a release group."""
        url = f"{CAA_BASE_URL}/release-group/{release_group_id}"
        return self._fetch_images(url)

    def _fetch_from_release(self, release_id):
        """Fetch cover art for a specific release."""
        url = f"{CAA_BASE_URL}/release/{release_id}"
        return self._fetch_images(url)

    def _fetch_images(self, url):
        """Fetch image list from a CAA endpoint."""
        results = []
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                for image in data.get('images', []):
                    if image.get('front', False):
                        # Prefer front cover
                        thumbnails = image.get('thumbnails', {})
                        results.append({
                            'url': image.get('image', ''),
                            'url_small': thumbnails.get('small', thumbnails.get('250', '')),
                            'url_large': thumbnails.get('large', thumbnails.get('500', '')),
                            'type': 'front',
                            'provider': self.name,
                        })
                    elif 'Front' in image.get('types', []):
                        thumbnails = image.get('thumbnails', {})
                        results.append({
                            'url': image.get('image', ''),
                            'url_small': thumbnails.get('small', thumbnails.get('250', '')),
                            'url_large': thumbnails.get('large', thumbnails.get('500', '')),
                            'type': 'front',
                            'provider': self.name,
                        })
            elif response.status_code == 404:
                logger.debug(f"No cover art found at {url}")
            else:
                if is_transient_status(response.status_code):
                    note_transient_error()
                logger.warning(f"CAA returned {response.status_code} for {url}")
        except requests.exceptions.RequestException as e:
            note_transient_error()
            logger.error(f"MusicBrainz cover art fetch error: {e}")

        return results

    def download(self, url):
        """Download cover art image from URL (allow-listed hosts only). Returns bytes or None."""
        return safe_download(url, self.name)
