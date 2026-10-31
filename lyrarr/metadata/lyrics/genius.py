
import html
import logging
import re

import requests

from lyrarr.app.config import settings
from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import clean_for_search
from lyrarr.metadata.provider_utils import note_transient_error

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
        """Create a lyricsgenius client instance.

        The Genius() constructor signature varies across lyricsgenius releases
        (e.g. some drop `verbose`), so only pass kwargs the installed version
        actually accepts and set the rest as attributes afterwards.
        """
        import inspect

        import lyricsgenius

        kwargs = {
            'verbose': False,
            'remove_section_headers': False,  # Keep [Chorus], [Verse] etc.
            'retries': 2,
        }
        try:
            params = inspect.signature(lyricsgenius.Genius.__init__).parameters
            if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                kwargs = {k: v for k, v in kwargs.items() if k in params}
        except (TypeError, ValueError):
            pass

        # Newer releases dropped `verbose` and log status messages instead.
        logging.getLogger('lyricsgenius').setLevel(logging.WARNING)

        genius = lyricsgenius.Genius(self._api_key, **kwargs)
        genius.timeout = 15
        # Skip non-song results (articles, interviews, book excerpts)
        genius.skip_non_songs = True
        # Keep lyricsgenius' defaults (tracklist, liner notes, credits, ...):
        # extend, never replace.
        genius.excluded_terms.extend(_EXTRA_EXCLUDED_TERMS)
        return genius

    def search(self, track_name=None, artist_name=None, album_name=None, duration=None,
               mb_recording_id=None, *, alt_artist_names=(), strict=True, **kwargs):
        """
        Search for lyrics on Genius.

        Every song hit of one search_all() call is scored; lyrics are fetched
        only for the best ungated hit (with strict=False: the best hit), and
        for the next one if that page is empty. Genius' own "instrumental"
        flag is never used.

        Returns at most one result:
        [{'plain_lyrics': str, 'synced_lyrics': None, 'provider', 'score',
          'match_details', 'strategy': 'search', 'source_id', 'source_url',
          'track_name', 'artist_name', 'album_name': None, 'duration': None}]
        """
        if not self._api_key:
            logger.debug("Genius API key not configured, skipping")
            return []

        if not track_name:
            return []

        meta = clean_for_search(track_name, artist_name or '')
        alt = tuple(a for a in alt_artist_names or () if a)
        results = []

        try:
            genius = self._get_client()

            # Featured artists' names in a hit's title usually mean a different
            # song by the guest — unless the library title names them too.
            for feat in meta.get('featuring', []):
                genius.excluded_terms.append(f"({feat})")

            title_q = meta['title_query'] or meta['title_clean']
            query = f"{title_q} {meta['artist_primary'] or meta['artist_query']}".strip()
            response = genius.search_all(query, per_page=5)

            candidates = []
            for hit in _song_hits(response):
                title = hit.get('title') or ''
                if self._excluded(title, genius.excluded_terms, track_name):
                    continue
                if hit.get('lyrics_state') not in (None, 'complete'):
                    continue
                match = None
                for result_artist in _hit_artists(hit):
                    m = self.score_result(
                        track_name, artist_name, duration,
                        title, result_artist, None,  # Genius has no duration
                        alt_artists=alt,
                    )
                    if match is None or _rank(m) > _rank(match):
                        match = m
                if match is None:
                    continue
                candidates.append((hit, match))

            candidates.sort(key=lambda c: _rank(c[1]), reverse=True)
            if strict:
                candidates = [c for c in candidates if not c[1].get('gate')]

            for hit, match in candidates[:2]:
                lyrics = genius.lyrics(song_id=hit.get('id'), song_url=hit.get('url'),
                                       remove_section_headers=False)
                lyrics = self._clean_lyrics(lyrics or '', hit.get('title'))
                if not lyrics or len(lyrics.strip()) <= 10:
                    continue
                primary = (hit.get('primary_artist') or {}).get('name') if isinstance(
                    hit.get('primary_artist'), dict) else None
                results.append({
                    'synced_lyrics': None,  # Genius doesn't provide synced lyrics
                    'plain_lyrics': lyrics,
                    'provider': self.name,
                    'score': match['score'],
                    'match_details': match,
                    'strategy': 'search',
                    'source_id': hit.get('id'),
                    'source_url': hit.get('url') or '',
                    'track_name': hit.get('title') or '',
                    'artist_name': hit.get('artist_names') or primary or '',
                    'album_name': None,
                    'duration': None,
                })
                break

        except ImportError:
            logger.error(
                "lyricsgenius package not installed. "
                "Install with: pip install lyricsgenius"
            )
        except Exception as e:
            # Network-ish failures are transient: flag them so the track is
            # retried soon, but don't fail the provider's health streak on a
            # single slow request. Anything else (auth errors, library API
            # changes, parse bugs) propagates so the worker records a failure
            # and the health tracker can bench the provider.
            if isinstance(e, (requests.exceptions.RequestException, TimeoutError)):
                note_transient_error()
                logger.warning(f"Genius transient error: {e}")
            else:
                raise

        return results

    @staticmethod
    def _excluded(title, terms, query_title):
        """lyricsgenius-style literal, case-insensitive exclusion — except for
        terms the library title itself contains ("Song (Live)" may match a
        "(Live)" page)."""
        t = (title or '').casefold()
        q = (query_title or '').casefold()
        for term in terms or ():
            term_cf = (term or '').casefold()
            if term_cf and term_cf in t and term_cf not in q:
                return True
        return False

    @staticmethod
    def _clean_lyrics(lyrics, title=None):
        """Clean up lyrics text returned by lyricsgenius.

        The library sometimes includes:
        - A title header line like "Song Title Lyrics" at the top
        - A contributor/embed suffix like "123Embed" at the end
        - "You might also like" text injected between sections
        - HTML entities (&amp;, &#x27;) left in the scraped text
        """
        lyrics = html.unescape(lyrics or '')
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


_EXTRA_EXCLUDED_TERMS = ["(Remix)", "(Live)", "(Demo)"]


def _song_hits(response):
    """Song results from a search_all() response (top hit + song section),
    de-duplicated by id, in Genius' order."""
    seen = set()
    out = []
    sections = (response or {}).get('sections') if isinstance(response, dict) else None
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        for hit in section.get('hits') or []:
            if not isinstance(hit, dict):
                continue
            if section.get('type') not in ('song', 'top_hit') or hit.get('type', 'song') != 'song':
                continue
            result = hit.get('result')
            if not isinstance(result, dict):
                continue
            key = result.get('id') or result.get('url') or id(result)
            if key in seen:
                continue
            seen.add(key)
            out.append(result)
    return out


def _hit_artists(hit):
    names = []
    full = hit.get('artist_names')
    if full:
        names.append(full)
    primary = hit.get('primary_artist')
    if isinstance(primary, dict) and primary.get('name') and primary.get('name') not in names:
        names.append(primary['name'])
    return names or ['']


def _rank(match):
    """Ungated before gated, then score."""
    return (0 if match.get('gate') else 1, match['score'], match.get('raw_score', match['score']))
