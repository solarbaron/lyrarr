# coding=utf-8

import logging
import requests

logger = logging.getLogger(__name__)

# Cover Art Archive base URL
CAA_BASE_URL = 'https://coverartarchive.org'


class MusicBrainzCoverProvider:
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
                logger.warning(f"CAA returned {response.status_code} for {url}")
        except requests.exceptions.RequestException as e:
            logger.error(f"MusicBrainz cover art fetch error: {e}")

        return results

    def download(self, url):
        """Download cover art image from URL. Returns bytes or None."""
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.content
        except requests.exceptions.RequestException as e:
            logger.error(f"MusicBrainz cover art download error: {e}")
        return None
