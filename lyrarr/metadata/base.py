
"""
Abstract base classes for metadata providers.
All cover art and lyrics providers should inherit from these ABCs
to ensure consistent interface implementation.
"""

import re
import unicodedata
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from typing import Any

from lyrarr.metadata.normalize import (
    artist_key,
    comparison_key_variants,
    key_digits,
    normalize_artist,
    parse_artist_credit,
)


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
    def search(self, **kwargs) -> list[dict[str, Any]]:
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
    def download(self, url: str) -> bytes | None:
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
    def search(self, **kwargs) -> list[dict[str, Any]]:
        """Search for lyrics.

        Common kwargs:
            track_name: Track/song title
            artist_name: Artist name
            album_name: Album name
            duration: Track duration in seconds (TableTracks.duration)
            mb_recording_id: MusicBrainz Recording ID (optional)
            alt_artist_names: other names the track may be credited to; the
                best artist score wins (default ())
            strict: True for the automatic worker (gated candidates are not
                fetched); False for manual search, which also returns gated
                or low candidates for review (default True)

        Returns:
            List of dicts, each containing at minimum:
                - plain_lyrics: str or None — plain text lyrics
                - synced_lyrics: str or None — synced/LRC lyrics
                - provider: str — provider name
                - score: float — confidence score (0.0-1.0)
                - match_details: dict — component score breakdown
                - strategy: str — how it was found (LRCLIB: 'mb_exact',
                  'exact_album', 'exact_noalbum', 'variant', 'search_struct',
                  'search_q', 'search_title'; others: 'search')
                - source_id, track_name, artist_name, album_name (None when
                  unknown), duration (seconds, None when unknown)
            Optional keys: 'instrumental' (LRCLIB strong marker, no lyrics),
            'instrumental_hint' (NetEase/Musixmatch "no lyrics" signal, no
            lyrics), 'plain_truncated' (Musixmatch excerpt).
        """
        ...

    # Hard gates: a result failing one is a different recording no matter how
    # well the rest matches ("Hello" by Adele is not "Hello" by Lionel Richie),
    # so its score is capped at GATE_SCORE_CAP — below the worker's acceptance
    # floor — and match_details['gate'] names the reason.
    ARTIST_GATE_MIN = 60          # artist similarity (0-100) required when the query has an artist
    DURATION_GATE_MAX_S = 10.0    # max |duration difference| in seconds when both are known
    TITLE_GATE_MIN = 60           # title similarity (0-100) required
    TITLE_GATE_MIN_NO_DURATION = 80   # ... when the result has no duration (Genius)
    GATE_SCORE_CAP = 0.3
    # Caps applied to title similarity when the titles name different works.
    DIGIT_CAP = 40                # different numbers: Pt. 1 / Pt. 2, 1979 / 1985, Track 1 / Track 12
    CONTENT_TAG_CAP = 50          # intro/outro/interlude/skit/reprise on one side only
    # Artist: a name contained in a longer credit ("Bush" / "Kate Bush") and
    # query-side parts ("Simon & Garfunkel" vs "Simon") never pass the gate alone.
    ARTIST_SUBSET_CAP = 50
    ARTIST_PART_MIN = 90          # a single credited name counts when ≥ this similar
    LEADER_TITLE_MIN = 90         # band credited by its leader: title_score needed ...
    LEADER_DURATION_MAX_S = 2.0   # ... and |Δduration| at most this (seconds)
    MISSING_DURATION_SCORE = 60

    @staticmethod
    def score_result(query_title, query_artist, query_duration_s,
                     result_title, result_artist, result_duration_s=None, *,
                     alt_artists=(), exact_get=False, mb_matched=False):
        """Compute a weighted fuzzy match score with component breakdown.

        Titles are compared on comparison keys (normalize.comparison_key):
        title_score is the best similarity over the key variants, capped
        when the number sets differ (Pt. 1 / Pt. 2) or a content tag
        (intro/outro/interlude/skit/reprise) is on one side only.

        Weights: title=0.45, artist=0.35, duration=0.20

        Hard gates (score capped at GATE_SCORE_CAP, 'gate' set), first match wins:
          - 'artist_missing': the query has an artist but the result has none
          - 'artist_mismatch': artist similarity below ARTIST_GATE_MIN
          - 'duration_mismatch': both durations known and > DURATION_GATE_MAX_S apart
          - 'version_mismatch': exactly one RAW title is a STRONG instrumental
            version (lyrics_text tiers), and that side is not karaoke
          - 'title_mismatch': title_score < 60, or < 80 when the result has no
            duration
        mb_matched results (identified through MusicBrainz) are exempt from the
        artist and title gates; exact_get results from the title gate. Neither
        ever lifts the duration or version gate.

        Args:
            query_title / query_artist / query_duration_s: the library track
            result_title / result_artist / result_duration_s: the provider hit
            alt_artists: other names the track may be credited to (album
                artist, file tag artist); the best artist score wins
            exact_get: the result came from an exact-match lookup of the raw title
            mb_matched: the result came from a MusicBrainz-identified lookup

        Returns:
            Dict with score, raw_score, gate, title_score, artist_score,
            duration_score, duration_diff (None when unknown), title_query,
            title_result, artist_query, artist_result, artist_via_alias,
            karaoke, mb_artist (None; LRCLIB fills it), mb_matched, exact_get.
        """
        from lyrarr.metadata.lyrics_text import (
            instrumental_qualifier_kind,
            instrumental_title_tier,
            version_tags,
        )

        cls = LyricsProvider
        query_title = query_title or ''
        result_title = result_title or ''

        # --- Title Score ---
        q_keys = comparison_key_variants(query_title)
        r_keys = comparison_key_variants(result_title, result_artist)
        for k in comparison_key_variants(result_title, query_artist):
            if k not in r_keys:
                r_keys.append(k)
        if q_keys and r_keys:
            title_score, best_q, best_r = -1, q_keys[0], r_keys[0]
            for qk in q_keys:
                for rk in r_keys:
                    sim = _key_similarity(qk, rk)
                    if sim > title_score:
                        title_score, best_q, best_r = sim, qk, rk
            # Caps judge the FULL titles, never the bracket-stripped variants.
            if _cap_digits(query_title, q_keys[0]) != _cap_digits(result_title, r_keys[0]):
                title_score = min(title_score, cls.DIGIT_CAP)
            q_tags = version_tags(query_title) & _CONTENT_TAGS
            r_tags = version_tags(result_title) & _CONTENT_TAGS
            if q_tags != r_tags:
                title_score = min(title_score, cls.CONTENT_TAG_CAP)
        elif not q_keys and not r_keys:
            title_score, best_q, best_r = 100, '', ''  # Both empty — neutral
        else:
            title_score = 0
            best_q = q_keys[0] if q_keys else ''
            best_r = r_keys[0] if r_keys else ''

        # --- Artist Score ---
        q_artist_present = bool((query_artist or '').strip())
        r_artist_present = bool((result_artist or '').strip())
        artist_via_alias = False
        if q_artist_present and r_artist_present:
            artist_score, artist_via_alias = _artist_similarity(query_artist, result_artist)
            for alt in alt_artists or ():
                if alt and alt.strip():
                    alt_score, alt_alias = _artist_similarity(alt, result_artist)
                    if alt_score > artist_score:
                        artist_score, artist_via_alias = alt_score, alt_alias
        elif not q_artist_present and not r_artist_present:
            artist_score = 100  # Both empty — neutral
        else:
            artist_score = 30  # One side missing — low but not zero

        # --- Duration Score ---
        duration_diff = None
        duration_score = cls.MISSING_DURATION_SCORE  # No duration info — neutral-low
        if query_duration_s and result_duration_s:
            try:
                # Both sides are seconds — no unit guessing.
                duration_diff = abs(float(query_duration_s) - float(result_duration_s))
                # 10% penalty per second of difference, floored at 0
                duration_score = max(0, int(100 - duration_diff * 10))
            except (ValueError, TypeError):
                duration_diff = None

        # --- Composite ---
        composite = (title_score * 0.45 + artist_score * 0.35 + duration_score * 0.20) / 100.0

        # --- Version (instrumental) check on RAW titles ---
        q_strong = instrumental_title_tier(query_title) == 'strong'
        r_strong = instrumental_title_tier(result_title) == 'strong'
        karaoke = (instrumental_qualifier_kind(result_title) == 'karaoke'
                   or instrumental_qualifier_kind(query_title) == 'karaoke')
        version_mismatch = False
        if q_strong != r_strong:
            strong_side = query_title if q_strong else result_title
            version_mismatch = instrumental_qualifier_kind(strong_side) != 'karaoke'

        # A band credited by its leader alone ("Jimi Hendrix" for "The Jimi
        # Hendrix Experience", "Bob Marley" for "Bob Marley & The Wailers"):
        # the RESULT's name (2+ words) sits, in order, inside the LIBRARY name.
        # Only with near-certain title and duration agreement, and marked as a
        # relaxed match (never instrumental evidence). The other direction
        # ("Bush" in the library, "Kate Bush" in the result) stays gated.
        if (q_artist_present and r_artist_present and artist_score < cls.ARTIST_GATE_MIN and not mb_matched
                and title_score >= cls.LEADER_TITLE_MIN and duration_diff is not None
                and duration_diff <= cls.LEADER_DURATION_MAX_S
                and any(_leader_credit(a, result_artist) for a in (query_artist, *(alt_artists or ())) if a)):
            artist_score, artist_via_alias = cls.ARTIST_GATE_MIN, True
            composite = (title_score * 0.45 + artist_score * 0.35 + duration_score * 0.20) / 100.0

        # --- Hard gates ---
        gate = None
        if q_artist_present and not r_artist_present and not mb_matched:
            gate = 'artist_missing'
        elif q_artist_present and artist_score < cls.ARTIST_GATE_MIN and not mb_matched:
            gate = 'artist_mismatch'
        elif duration_diff is not None and duration_diff > cls.DURATION_GATE_MAX_S:
            gate = 'duration_mismatch'
        elif version_mismatch:
            gate = 'version_mismatch'
        elif not (mb_matched or exact_get):
            title_min = cls.TITLE_GATE_MIN if result_duration_s else cls.TITLE_GATE_MIN_NO_DURATION
            if title_score < title_min:
                gate = 'title_mismatch'
        score = min(composite, cls.GATE_SCORE_CAP) if gate else composite

        return {
            'score': round(score, 3),
            'raw_score': round(composite, 3),
            'gate': gate,
            'title_score': title_score,
            'artist_score': artist_score,
            'duration_score': duration_score,
            'title_query': best_q,
            'title_result': best_r,
            'artist_query': normalize_artist(query_artist or ''),
            'artist_result': normalize_artist(result_artist or ''),
            'duration_diff': duration_diff,
            'artist_via_alias': artist_via_alias,
            'karaoke': karaoke,
            'mb_artist': None,
            'mb_matched': bool(mb_matched),
            'exact_get': bool(exact_get),
        }


_CONTENT_TAGS = frozenset({'intro', 'outro', 'interlude', 'skit', 'reprise'})


def _key_similarity(a: str, b: str) -> int:
    """0-100: 70% character sequence ratio + 30% token overlap."""
    if a == b:
        return 100
    if not a or not b:
        return 0
    seq_ratio = SequenceMatcher(None, a, b).ratio()
    a_tokens, b_tokens = set(a.split()), set(b.split())
    token_ratio = len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))
    return int((seq_ratio * 0.7 + token_ratio * 0.3) * 100)


def _name_similarity(a: str, b: str) -> int:
    """0-100 similarity of two artist keys (artist_key output).

    Spaces are ignored for the ratio ("AC/DC" = "ACDC", "Jay-Z" = "Jay Z").
    A name whose words are a strict subset of the other's ("Bush" /
    "Kate Bush", "Queen" / "Queen Latifah") is a different artist until
    proven otherwise: capped at ARTIST_SUBSET_CAP.
    """
    if not a or not b:
        return 0
    a_ns, b_ns = a.replace(' ', ''), b.replace(' ', '')
    if a_ns == b_ns:
        return 100
    score = int(SequenceMatcher(None, a_ns, b_ns).ratio() * 100)
    a_tok, b_tok = set(a.split()), set(b.split())
    if a_tok < b_tok or b_tok < a_tok:
        score = min(score, LyricsProvider.ARTIST_SUBSET_CAP)
    return score


_LOOSE_DROP = frozenset({'and', 'the'})

# Brackets that only date or describe a recording ("(2013 demo)",
# "(rehearsals 08-20-1986)", "[Live 1999]"): their numbers don't make the
# song a different one, so the digit cap ignores them. "(Pt. 2)" stays.
_VERSION_BRACKET_RE = re.compile(
    r'[(\[](?=[^)\]]*\b(?:demo|live|rehears\w*|remaster\w*|mix|version|edit|sessions?|take|recorded|'
    r'mono|stereo|bootleg|acoustic|radio|session)\b)[^)\]]*[)\]]',
    re.IGNORECASE)


def _cap_digits(raw_title: str, full_key: str) -> frozenset:
    """Numbers that tell songs apart: the full key's, minus those that only
    appear inside version/date brackets of the raw title."""
    digits = key_digits(full_key)
    if not digits or not _VERSION_BRACKET_RE.search(raw_title or ''):
        return digits
    from lyrarr.metadata.normalize import comparison_key
    return key_digits(comparison_key(_VERSION_BRACKET_RE.sub(' ', raw_title)))


_ENSEMBLE_WORDS = frozenset({
    'experience', 'band', 'orchestra', 'group', 'trio', 'quartet', 'quintet', 'sextet', 'ensemble',
    'combo', 'allstars', 'all-stars', 'singers', 'project', 'collective', 'revue', 'gang',
})
_BAND_MARKERS = frozenset({'the', 'his', 'her', 'their'})


def _words(name: str) -> list[str]:
    return re.findall(r"[\w'-]+", unicodedata.normalize('NFKC', name or '').casefold())


def _leader_credit(library_artist: str, result_artist: str) -> bool:
    """Whether the result credits a band by its leader alone: the result (one
    artist, 2+ words) is a contiguous run inside the library name, and the rest
    of the library name is a backing-band phrase — '<leader> & The <band>',
    '<leader> and His <band>', 'The <leader> Experience'. 'Daft Punk & Pharrell'
    vs 'Daft Punk' (a guest, not a band) and one-word results ('Bush', 'Queen')
    never qualify."""
    if parse_artist_credit(result_artist)['parts']:
        return False
    r = _words(result_artist)
    if r and r[0] == 'the':
        r = r[1:]
    q = _words(library_artist)
    n = len(r)
    if n < 2 or len(q) <= n:
        return False
    for i in range(len(q) - n + 1):
        if q[i:i + n] != r:
            continue
        prefix, suffix = q[:i], q[i + n:]
        if prefix not in ([], ['the']):
            continue
        if suffix and suffix[0] == 'and':
            suffix = suffix[1:]
        if len(suffix) >= 2 and suffix[0] in _BAND_MARKERS:
            return True
        if len(suffix) == 1 and suffix[0] in _ENSEMBLE_WORDS:
            return True
    return False


def _loose_key(key: str) -> str:
    """artist_key without 'and'/'the' and spaces: '&' vs ',' and dropped articles compare equal."""
    return ''.join(t for t in (key or '').split() if t not in _LOOSE_DROP)


def _artist_similarity(query_artist: str, result_artist: str) -> tuple[int, bool]:
    """(artist_score, via_alias) for two RAW credit strings.

    score = max of
      * full credit vs full credit (bracketed aliases removed),
      * explicit primaries (before feat./ft./x/vs) vs the other side,
      * the whole query vs each result part — counted only when ≥ 90
        ('Rihanna' vs 'Calvin Harris, Rihanna'),
      * query parts vs the result — a secondary signal capped below the gate
        ('Simon & Garfunkel' vs 'Simon' stays gated).
    When that stays below the gate, a bracketed alias that matches ≥ 90
    ('아이유' vs 'IU (아이유)') counts, with via_alias=True.
    """
    q = parse_artist_credit(query_artist)
    r = parse_artist_credit(result_artist)
    q_main, r_main = artist_key(q['main']), artist_key(r['main'])
    q_pri, r_pri = artist_key(q['primary']), artist_key(r['primary'])
    r_parts = [artist_key(p) for p in r['parts']]
    q_parts = [artist_key(p) for p in q['parts']]

    score = _name_similarity(q_main, r_main)
    score = max(score, _name_similarity(q_pri, r_main), _name_similarity(q_pri, r_pri),
                _name_similarity(q_main, r_pri))
    for part in r_parts:
        for qn in {q_main, q_pri}:
            s = _name_similarity(qn, part)
            if s >= LyricsProvider.ARTIST_PART_MIN:
                score = max(score, s)
    for part in q_parts:
        for rn in [r_main, *r_parts]:
            s = _name_similarity(part, rn)
            if s >= LyricsProvider.ARTIST_PART_MIN:
                score = max(score, min(s, LyricsProvider.ARTIST_SUBSET_CAP))
    if score < 100 and r['parts']:
        # A library name that itself contains a separator ("Tyler, The Creator",
        # "Joey Valence & Brae") is split apart by the result's credit parsing:
        # match it against contiguous runs of the result's parts
        # ("Tyler, The Creator, Kali Uchis", "Joey Valence, Brae").
        targets = {k for k in (_loose_key(q_main), _loose_key(q_pri)) if k}
        raw_parts = r['parts']
        for i in range(len(raw_parts)):
            for j in range(i + 2, len(raw_parts) + 1):
                if _loose_key(artist_key(' '.join(raw_parts[i:j]))) in targets:
                    score = 100
                    break
            if score == 100:
                break

    via_alias = False
    if score < LyricsProvider.ARTIST_GATE_MIN and (q['aliases'] or r['aliases']):
        q_names = [q_main] + [artist_key(a) for a in q['aliases']]
        r_names = [r_main] + [artist_key(a) for a in r['aliases']]
        for i, qn in enumerate(q_names):
            for j, rn in enumerate(r_names):
                if i == 0 and j == 0:
                    continue
                s = _name_similarity(qn, rn)
                if s >= LyricsProvider.ARTIST_PART_MIN and s > score:
                    score, via_alias = s, True
    return score, via_alias
