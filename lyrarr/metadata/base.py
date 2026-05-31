# coding=utf-8

"""
Abstract base classes for metadata providers.
All cover art and lyrics providers should inherit from these ABCs
to ensure consistent interface implementation.
"""

from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Any

from lyrarr.metadata.normalize import normalize_title, normalize_artist, get_primary_artist


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
            duration: Track duration in milliseconds (from Lidarr)
            mb_recording_id: MusicBrainz Recording ID (optional)

        Returns:
            List of dicts, each containing at minimum:
                - plain_lyrics: str or None — plain text lyrics
                - synced_lyrics: str or None — synced/LRC lyrics
                - provider: str — provider name
                - score: float — confidence score (0.0-1.0)
                - match_details: dict — component score breakdown
        """
        ...

    @staticmethod
    def score_result(query_title, query_artist, query_duration_ms,
                     result_title, result_artist, result_duration_s=None):
        """Compute a weighted fuzzy match score with component breakdown.

        Uses normalized forms of title/artist for comparison. Combines
        SequenceMatcher ratio with token-set overlap for robust matching.

        Weights: title=0.45, artist=0.35, duration=0.20

        Args:
            query_title: Original track title from library
            query_artist: Original artist name from library
            query_duration_ms: Track duration in milliseconds (or None)
            result_title: Title returned by the lyrics provider
            result_artist: Artist returned by the lyrics provider
            result_duration_s: Duration in seconds from provider (or None)

        Returns:
            Dict with:
                score: float 0.0-1.0 (weighted composite)
                title_score: int 0-100
                artist_score: int 0-100
                duration_score: int 0-100
                title_query: str (normalized query title)
                title_result: str (normalized result title)
                artist_query: str (normalized query artist)
                artist_result: str (normalized result artist)
                duration_diff: float (difference in seconds, or None)
        """
        # Normalize inputs
        q_title = normalize_title(query_title or '')
        r_title = normalize_title(result_title or '')
        q_artist = normalize_artist(query_artist or '')
        r_artist = normalize_artist(result_artist or '')
        q_primary = get_primary_artist(query_artist or '')

        # --- Title Score ---
        if q_title and r_title:
            # SequenceMatcher ratio
            seq_ratio = SequenceMatcher(None, q_title, r_title).ratio()

            # Token-set overlap (handles word reordering)
            q_tokens = set(q_title.split())
            r_tokens = set(r_title.split())
            if q_tokens and r_tokens:
                intersection = q_tokens & r_tokens
                token_ratio = len(intersection) / max(len(q_tokens), len(r_tokens))
            else:
                token_ratio = 0.0

            # Weighted combination: 70% sequence, 30% token overlap
            title_score = int((seq_ratio * 0.7 + token_ratio * 0.3) * 100)
        elif not q_title and not r_title:
            title_score = 100  # Both empty — neutral
        else:
            title_score = 0

        # --- Artist Score ---
        if q_artist and r_artist:
            # Full artist comparison
            seq_ratio = SequenceMatcher(None, q_artist, r_artist).ratio()

            # Also check if primary artist matches
            primary_ratio = 0.0
            if q_primary:
                primary_ratio = SequenceMatcher(None, q_primary, r_artist).ratio()
                # Also check if primary artist is contained in result
                r_primary = get_primary_artist(result_artist or '')
                if r_primary:
                    primary_direct = SequenceMatcher(None, q_primary, r_primary).ratio()
                    primary_ratio = max(primary_ratio, primary_direct)

            # Best of full match or primary match
            artist_score = int(max(seq_ratio, primary_ratio) * 100)
        elif not q_artist and not r_artist:
            artist_score = 100  # Both empty — neutral
        else:
            artist_score = 30  # One side missing — low but not zero

        # --- Duration Score ---
        duration_diff = None
        if query_duration_ms is not None and result_duration_s is not None:
            try:
                q_dur_s = int(query_duration_ms) / 1000 if int(query_duration_ms) > 1000 else int(query_duration_ms)
                r_dur_s = float(result_duration_s)
                duration_diff = abs(q_dur_s - r_dur_s)
                # 10% penalty per second of difference, floored at 0
                duration_score = max(0, int(100 - duration_diff * 10))
            except (ValueError, TypeError):
                duration_score = 70  # Can't parse — neutral
        else:
            duration_score = 70  # No duration info — neutral

        # --- Composite ---
        composite = (title_score * 0.45 + artist_score * 0.35 + duration_score * 0.20) / 100.0

        return {
            'score': round(composite, 3),
            'title_score': title_score,
            'artist_score': artist_score,
            'duration_score': duration_score,
            'title_query': q_title,
            'title_result': r_title,
            'artist_query': q_artist,
            'artist_result': r_artist,
            'duration_diff': duration_diff,
        }
