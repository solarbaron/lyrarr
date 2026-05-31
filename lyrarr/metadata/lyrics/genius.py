# coding=utf-8

import logging

from lyrarr.app.config import settings
from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import clean_for_search

logger = logging.getLogger(__name__)


class GeniusProvider(LyricsProvider):
    """Fetch plain lyrics from Genius using the lyricsgenius library.

    Uses normalized title/artist for search queries and computes real
    match scores against the returned results.
    """

    name = 'genius'

    @property
    def _api_key(self):
        return settings.genius.apikey

    def _get_client(self):
        """Create a lyricsgenius client instance."""
        import lyricsgenius

        genius = lyricsgenius.Genius(
            self._api_key,
            verbose=False,
            remove_section_headers=False,  # Keep [Chorus], [Verse] etc.
            retries=2,
        )
        genius.timeout = 15
        # Skip non-song results (articles, interviews, book excerpts)
        genius.skip_non_songs = True
        genius.excluded_terms = [
            "(Remix)", "(Live)", "(Demo)",
        ]
        return genius

    def search(self, track_name=None, artist_name=None, duration=None, **kwargs):
        """
        Search for lyrics on Genius.

        Returns a list of lyrics results with computed match scores:
        [{'plain_lyrics': str, 'synced_lyrics': None, 'provider': str,
          'score': float, 'match_details': dict}]
        """
        if not self._api_key:
            logger.debug("Genius API key not configured, skipping")
            return []

        if not track_name:
            return []

        meta = clean_for_search(track_name, artist_name or '')
        results = []

        try:
            genius = self._get_client()

            # Add featuring artists to excluded terms to avoid matching
            # a completely different song by a featured artist
            for feat in meta.get('featuring', []):
                genius.excluded_terms.append(f"({feat})")

            # Search with normalized title and primary artist
            song = genius.search_song(
                meta['title_clean'],
                meta['artist_primary'] or meta['artist_clean']
            )

            if song and song.lyrics:
                lyrics = song.lyrics

                # lyricsgenius sometimes prepends the song title + "Lyrics" header
                # and appends an embed/contributor count suffix — clean those
                lyrics = self._clean_lyrics(lyrics, song.title)

                if lyrics and len(lyrics.strip()) > 10:
                    # Compute real match score (Genius has no duration info)
                    match = self.score_result(
                        track_name, artist_name, duration,
                        song.title or '', song.artist or '',
                        None  # Genius doesn't provide duration
                    )

                    results.append({
                        'synced_lyrics': None,  # Genius doesn't provide synced lyrics
                        'plain_lyrics': lyrics,
                        'provider': self.name,
                        'score': match['score'],
                        'match_details': match,
                        'source_url': song.url or '',
                        'track_name': song.title or '',
                        'artist_name': song.artist or '',
                    })

        except ImportError:
            logger.error(
                "lyricsgenius package not installed. "
                "Install with: pip install lyricsgenius"
            )
        except Exception as e:
            logger.error(f"Genius search error: {e}")

        return results

    @staticmethod
    def _clean_lyrics(lyrics, title=None):
        """Clean up lyrics text returned by lyricsgenius.

        The library sometimes includes:
        - A title header line like "Song Title Lyrics" at the top
        - A contributor/embed suffix like "123Embed" at the end
        - "You might also like" text injected between sections
        """
        import re

        lines = lyrics.split('\n')

        # Strip leading title line (e.g. "Song Title Lyrics" or "ContributorsSong Title Lyrics")
        if lines and lines[0].rstrip().endswith('Lyrics'):
            lines = lines[1:]

        # Strip trailing embed line (e.g. "123Embed" or "42Embed")
        if lines and re.match(r'^\d*Embed\s*$', lines[-1]):
            lines = lines[:-1]

        # Remove "You might also like" injections
        lines = [line for line in lines if line.strip() != 'You might also like']

        lyrics = '\n'.join(lines).strip()

        # Collapse excessive blank lines
        lyrics = re.sub(r'\n{3,}', '\n\n', lyrics)

        return lyrics
