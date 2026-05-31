# coding=utf-8

"""
Track title and artist name normalization for lyrics matching.

Cleans metadata before sending to lyrics providers to dramatically improve
match accuracy. Handles:
  - Parenthetical suffixes: (Remastered), (Live), (Deluxe Edition), etc.
  - Bracket suffixes: [Bonus Track], [Explicit], etc.
  - Featuring artists: feat., ft., featuring, with
  - Artist separators: &, +, ,, /
  - Unicode/diacritic folding: Beyoncé → Beyonce
  - Punctuation and whitespace normalization
"""

import re
import unicodedata
from typing import Optional


# ---------- Constants ----------

# Parenthetical/bracket terms that are noise for lyrics matching
_NOISE_TERMS = re.compile(
    r'\s*[\(\[]\s*'
    r'(?:'
    r'(?:(?:\d{4}\s+)?re-?master(?:ed)?(?:\s+\d{4})?)'  # (Remastered), (2011 Remaster), (Remastered 2009)
    r'|(?:deluxe(?:\s+edition)?)'           # (Deluxe), (Deluxe Edition)
    r'|(?:expanded(?:\s+edition)?)'         # (Expanded Edition)
    r'|(?:anniversary(?:\s+edition)?)'      # (Anniversary Edition)
    r'|(?:special(?:\s+edition)?)'          # (Special Edition)
    r'|(?:bonus(?:\s+track)?)'             # (Bonus Track), [Bonus Track]
    r'|(?:super\s+deluxe)'                 # (Super Deluxe)
    r'|(?:explicit)'                        # [Explicit]
    r'|(?:clean)'                           # [Clean]
    r'|(?:radio\s+edit)'                    # (Radio Edit)
    r'|(?:single\s+version)'               # (Single Version)
    r'|(?:album\s+version)'                # (Album Version)
    r'|(?:original\s+mix)'                 # (Original Mix)
    r'|(?:mono)'                           # (Mono)
    r'|(?:stereo)'                         # (Stereo)
    r'|(?:live(?:\s+(?:at|from|in)\s+[^\)\]]*)?)'  # (Live), (Live at Wembley)
    r'|(?:demo(?:\s+version)?)'            # (Demo), (Demo Version)
    r'|(?:acoustic(?:\s+version)?)'        # (Acoustic), (Acoustic Version)
    r'|(?:unplugged)'                      # (Unplugged)
    r'|(?:instrumental)'                   # (Instrumental)
    r'|(?:remix(?:ed)?(?:\s+by\s+[^\)\]]*)?)'  # (Remix), (Remixed by X)
    r'|(?:version)'                        # (Version)
    r'|(?:edit)'                           # (Edit)
    r'|(?:from\s+["\'][^\)\]]*["\'](?:\s+soundtrack)?)'  # (From "Movie" Soundtrack)
    r')'
    r'\s*[\)\]]',
    re.IGNORECASE
)

# Featuring patterns — captures the featured artist(s)
_FEAT_PATTERN = re.compile(
    r'\s*[\(\[]?\s*'
    r'(?:feat\.?|ft\.?|featuring|with)\s+'
    r'([^\)\]]+?)'
    r'\s*[\)\]]?\s*$',
    re.IGNORECASE
)

# Featuring in the middle of a title (e.g. "Title feat. X - Remix")
_FEAT_MID_PATTERN = re.compile(
    r'\s+(?:feat\.?|ft\.?|featuring)\s+([^(\[\-]+)',
    re.IGNORECASE
)

# Artist separators for splitting compound artist names
_ARTIST_SEPARATORS = re.compile(
    r'\s*(?:\s+&\s+|\s+and\s+|\s+x\s+|\s*,\s+|\s*/\s+|\s+vs\.?\s+)\s*',
    re.IGNORECASE
)

# Punctuation to remove (keep apostrophes and hyphens for now)
_STRIP_PUNCTUATION = re.compile(r'[!@#$%^*()_+=\[\]{};:"|\\<>?/~`]')

# Collapse whitespace
_MULTI_SPACE = re.compile(r'\s{2,}')

# Quotation marks to normalize
_QUOTES = re.compile(r'[''""«»„]')


# ---------- Core Functions ----------

def _unicode_fold(text: str) -> str:
    """Fold Unicode characters to closest ASCII equivalents.

    Beyoncé → Beyonce, Ñ → N, ü → u, etc.
    """
    # NFKD decomposition splits characters from diacritics
    decomposed = unicodedata.normalize('NFKD', text)
    # Filter out combining marks (diacritics)
    return ''.join(c for c in decomposed if unicodedata.category(c) != 'Mn')


def normalize_title(title: str) -> str:
    """Normalize a track title for lyrics matching.

    Removes parenthetical noise (Remastered, Live, etc.), extracts featuring
    artists, folds Unicode, and collapses whitespace.

    Args:
        title: Raw track title from Lidarr

    Returns:
        Cleaned, lowercased title suitable for search queries
    """
    if not title:
        return ''

    text = title

    # Remove noise parentheticals/brackets
    text = _NOISE_TERMS.sub('', text)

    # Remove featuring info (we extract it separately if needed)
    text = _FEAT_PATTERN.sub('', text)
    text = _FEAT_MID_PATTERN.sub('', text)

    # Remove any remaining empty parentheses/brackets
    text = re.sub(r'\s*[\(\[]\s*[\)\]]\s*', '', text)

    # Normalize quotes
    text = _QUOTES.sub("'", text)

    # Unicode → ASCII
    text = _unicode_fold(text)

    # Normalize ampersand
    text = text.replace(' & ', ' and ')

    # Strip problematic punctuation (keep apostrophes and hyphens)
    text = _STRIP_PUNCTUATION.sub('', text)

    # Collapse whitespace and strip
    text = _MULTI_SPACE.sub(' ', text).strip()

    return text.lower()


def normalize_artist(artist: str) -> str:
    """Normalize an artist name for lyrics matching.

    Handles compound artists by extracting the primary artist,
    folds Unicode, and normalizes whitespace.

    Args:
        artist: Raw artist name from Lidarr

    Returns:
        Cleaned, lowercased artist name
    """
    if not artist:
        return ''

    text = artist

    # Normalize quotes
    text = _QUOTES.sub("'", text)

    # Unicode → ASCII
    text = _unicode_fold(text)

    # Normalize ampersand symbol to word
    text = text.replace(' & ', ' and ')

    # Strip problematic punctuation
    text = _STRIP_PUNCTUATION.sub('', text)

    # Collapse whitespace and strip
    text = _MULTI_SPACE.sub(' ', text).strip()

    return text.lower()


def get_primary_artist(artist: str) -> str:
    """Extract the primary (first) artist from a compound artist string.

    "Artist1 & Artist2" → "artist1"
    "Artist1, Artist2, Artist3" → "artist1"
    "Artist1 feat. Artist2" → "artist1"

    Args:
        artist: Raw artist name

    Returns:
        Lowercased primary artist name
    """
    if not artist:
        return ''

    # First strip any featuring suffix
    text = _FEAT_PATTERN.sub('', artist)
    text = _FEAT_MID_PATTERN.sub('', text)

    # Split by separators and take the first
    parts = _ARTIST_SEPARATORS.split(text)
    primary = parts[0].strip() if parts else text.strip()

    return normalize_artist(primary)


def extract_featuring(title: str) -> tuple:
    """Extract featuring artists from a track title.

    "Title (feat. Artist1 & Artist2)" → ("Title", ["Artist1", "Artist2"])
    "Title" → ("Title", [])

    Args:
        title: Raw track title

    Returns:
        Tuple of (clean_title, list_of_featured_artists)
    """
    if not title:
        return ('', [])

    featured = []

    # Try end-of-title pattern first: "Title (feat. X)"
    match = _FEAT_PATTERN.search(title)
    if match:
        feat_str = match.group(1).strip()
        clean_title = title[:match.start()].strip()
        # Split featured artists by separators
        feat_artists = _ARTIST_SEPARATORS.split(feat_str)
        featured = [a.strip() for a in feat_artists if a.strip()]
        return (clean_title, featured)

    # Try mid-title pattern: "Title feat. X"
    match = _FEAT_MID_PATTERN.search(title)
    if match:
        feat_str = match.group(1).strip()
        clean_title = title[:match.start()].strip()
        feat_artists = _ARTIST_SEPARATORS.split(feat_str)
        featured = [a.strip() for a in feat_artists if a.strip()]
        return (clean_title, featured)

    return (title, [])


def split_artists(artist: str) -> list:
    """Split a compound artist string into individual artists.

    "Artist1 & Artist2" → ["Artist1", "Artist2"]
    "Artist1, Artist2, Artist3" → ["Artist1", "Artist2", "Artist3"]

    Args:
        artist: Raw artist string

    Returns:
        List of individual artist names (not normalized)
    """
    if not artist:
        return []

    # First remove any featuring portion
    text = _FEAT_PATTERN.sub('', artist)
    text = _FEAT_MID_PATTERN.sub('', text)

    parts = _ARTIST_SEPARATORS.split(text)
    return [p.strip() for p in parts if p.strip()]


def clean_for_search(title: str, artist: str) -> dict:
    """Generate all normalized variants for a multi-strategy search.

    Returns a dict with original + normalized + variant forms that providers
    can use for cascading queries (try exact first, then broader).

    Args:
        title: Raw track title
        artist: Raw artist name

    Returns:
        Dict with keys:
            title          — original title
            title_clean    — normalized title (noise removed, lowercased)
            artist         — original artist
            artist_clean   — normalized full artist
            artist_primary — first/primary artist only
            featuring      — list of featured artist names
            title_variants — list of title strings to try (most specific → broadest)
    """
    clean_title = normalize_title(title)
    clean_artist = normalize_artist(artist)
    primary_artist = get_primary_artist(artist)
    feat_title, featuring = extract_featuring(title)

    # Generate title variants from most specific to broadest
    title_variants = []

    # 1. Cleaned title (noise stripped but structure preserved)
    if clean_title:
        title_variants.append(clean_title)

    # 2. Title without any parentheticals at all
    no_parens = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', '', title).strip()
    no_parens_clean = normalize_title(no_parens)
    if no_parens_clean and no_parens_clean != clean_title:
        title_variants.append(no_parens_clean)

    # 3. Title with featuring stripped (if different)
    feat_clean = normalize_title(feat_title)
    if feat_clean and feat_clean not in title_variants:
        title_variants.append(feat_clean)

    # 4. Original title normalized (in case noise regex was too aggressive)
    orig_normalized = normalize_artist(title)  # basic normalize only
    if orig_normalized and orig_normalized not in title_variants:
        title_variants.append(orig_normalized)

    # De-dup while preserving order
    seen = set()
    unique_variants = []
    for v in title_variants:
        if v not in seen:
            seen.add(v)
            unique_variants.append(v)

    return {
        'title': title or '',
        'title_clean': clean_title,
        'artist': artist or '',
        'artist_clean': clean_artist,
        'artist_primary': primary_artist,
        'featuring': featuring,
        'title_variants': unique_variants,
    }


def duration_ms_to_seconds(duration) -> Optional[int]:
    """Safely convert a track duration to seconds.

    Handles the ambiguity of Lidarr's duration field which stores
    values in milliseconds (typically > 1000).

    Args:
        duration: Duration value (int or None), expected in milliseconds

    Returns:
        Duration in whole seconds, or None
    """
    if duration is None:
        return None

    try:
        dur = int(duration)
    except (ValueError, TypeError):
        return None

    if dur <= 0:
        return None

    # Lidarr stores duration in milliseconds
    # Anything > 1000 is almost certainly ms (a 1-second track is implausible)
    if dur > 1000:
        return dur // 1000

    # Already in seconds (edge case / manual input)
    return dur
