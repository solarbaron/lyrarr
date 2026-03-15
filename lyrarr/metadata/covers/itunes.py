# coding=utf-8

"""
iTunes / Apple Music Cover Art Provider.
Uses the public iTunes Search API to find album artwork.
No API key required.
"""

import logging
import requests

from lyrarr.metadata.base import CoverProvider

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = 'https://itunes.apple.com/search'


class ITunesCoverProvider(CoverProvider):
    """Fetch cover art from iTunes/Apple Music (free, no API key)."""

    name = 'itunes'

    def search(self, artist_name=None, album_name=None, **kwargs):
        """
        Search for cover art by artist + album name.

        Returns a list of cover art results:
        [{'url': str, 'url_small': str, 'url_large': str, 'type': str, 'provider': str}]
        """
        if not album_name:
            return []

        results = []
        query = f"{artist_name} {album_name}" if artist_name else album_name

        try:
            response = requests.get(
                ITUNES_SEARCH_URL,
                params={
                    'term': query,
                    'entity': 'album',
                    'limit': 5,
                },
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                for item in data.get('results', []):
                    artwork_url = item.get('artworkUrl100', '')
                    if artwork_url:
                        # iTunes URLs can be resized by changing the size in the URL
                        # e.g., 100x100bb.jpg → 600x600bb.jpg or 1200x1200bb.jpg
                        url_large = artwork_url.replace('100x100bb', '600x600bb')
                        url_xl = artwork_url.replace('100x100bb', '1200x1200bb')

                        results.append({
                            'url': url_xl,
                            'url_small': artwork_url,
                            'url_large': url_large,
                            'type': 'front',
                            'provider': self.name,
                            'title': item.get('collectionName', ''),
                            'artist': item.get('artistName', ''),
                        })
            else:
                logger.warning(f"iTunes API returned {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"iTunes cover search error: {e}")

        return results

    def download(self, url):
        """Download cover art image from URL. Returns bytes or None."""
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.content
        except requests.exceptions.RequestException as e:
            logger.error(f"iTunes cover download error: {e}")
        return None
