# coding=utf-8

"""
Abstract base classes for metadata providers.
All cover art and lyrics providers should inherit from these ABCs
to ensure consistent interface implementation.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any


class CoverProvider(ABC):
    """Abstract base class for cover art providers.

    Subclasses must implement:
        - name: str property
        - search(): find cover art for an album
        - download(): download image data from a URL
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of this provider (e.g., 'musicbrainz', 'deezer')."""
        ...

    @abstractmethod
    def search(self, **kwargs) -> List[Dict[str, Any]]:
        """Search for cover art.

        Common kwargs:
            mb_release_group_id: MusicBrainz release group ID
            mb_release_id: MusicBrainz release ID
            mb_artist_id: MusicBrainz artist ID
            mb_album_id: MusicBrainz album ID
            artist_name: Artist name
            album_name: Album name

        Returns:
            List of dicts, each containing at minimum:
                - url: str — URL to the cover image
                - provider: str — provider name
                - type: str — image type (e.g., 'front', 'back')
        """
        ...

    @abstractmethod
    def download(self, url: str) -> Optional[bytes]:
        """Download image data from a URL.

        Args:
            url: URL to download

        Returns:
            Image bytes, or None on failure.
        """
        ...


class LyricsProvider(ABC):
    """Abstract base class for lyrics providers.

    Subclasses must implement:
        - name: str property
        - search(): find lyrics for a track
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of this provider (e.g., 'lrclib', 'genius')."""
        ...

    @abstractmethod
    def search(self, **kwargs) -> List[Dict[str, Any]]:
        """Search for lyrics.

        Common kwargs:
            track_name: Track/song title
            artist_name: Artist name
            album_name: Album name
            duration: Track duration in seconds

        Returns:
            List of dicts, each containing at minimum:
                - plain_lyrics: str or None — plain text lyrics
                - synced_lyrics: str or None — synced/LRC lyrics
                - provider: str — provider name
                - score: float — confidence score (0.0-1.0)
        """
        ...
