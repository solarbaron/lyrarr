# coding=utf-8

import logging
import requests

from lyrarr.app.config import settings
from lyrarr.metadata.base import CoverProvider

logger = logging.getLogger(__name__)

FANART_BASE_URL = 'https://webservice.fanart.tv/v3'


class FanartCoverProvider(CoverProvider):
    """Fetch cover art from fanart.tv (requires API key)."""

    name = 'fanart'

    @property
    def _api_key(self):
        return settings.fanart.apikey

    def search(self, mb_artist_id=None, mb_album_id=None, **kwargs):
        """
        Search for cover art by MusicBrainz Artist or Album ID.

        Returns a list of cover art results:
        [{'url': str, 'type': str, 'provider': str}]
        """
        if not self._api_key:
            logger.debug("fanart.tv API key not configured, skipping")
            return []

        results = []

        if mb_artist_id:
            results.extend(self._fetch_artist_art(mb_artist_id))

        return results

    def _fetch_artist_art(self, mb_artist_id):
        """Fetch all art for an artist."""
        results = []
        url = f"{FANART_BASE_URL}/music/{mb_artist_id}"

        try:
            response = requests.get(
                url,
                params={'api_key': self._api_key},
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()

                # Album covers
                for album_cover in data.get('albumcover', []):
                    results.append({
                        'url': album_cover.get('url', ''),
                        'type': 'albumcover',
                        'provider': self.name,
                        'likes': album_cover.get('likes', 0),
                    })

                # Artist thumbs
                for thumb in data.get('artistthumb', []):
                    results.append({
                        'url': thumb.get('url', ''),
                        'type': 'artistthumb',
                        'provider': self.name,
                        'likes': thumb.get('likes', 0),
                    })

                # Artist background/fanart
                for bg in data.get('artistbackground', []):
                    results.append({
                        'url': bg.get('url', ''),
                        'type': 'artistbackground',
                        'provider': self.name,
                        'likes': bg.get('likes', 0),
                    })

                # HD music logos
                for logo in data.get('hdmusiclogo', []):
                    results.append({
                        'url': logo.get('url', ''),
                        'type': 'hdmusiclogo',
                        'provider': self.name,
                        'likes': logo.get('likes', 0),
                    })

            elif response.status_code == 404:
                logger.debug(f"No fanart.tv art found for artist {mb_artist_id}")
            else:
                logger.warning(f"fanart.tv returned {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"fanart.tv fetch error: {e}")

        return results

    def download(self, url):
        """Download image from URL. Returns bytes or None."""
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.content
        except requests.exceptions.RequestException as e:
            logger.error(f"fanart.tv download error: {e}")
        return None
