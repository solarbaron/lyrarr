
"""
Track title and artist name normalization for lyrics matching.

Cleans metadata before sending to lyrics providers to dramatically improve
match accuracy. Handles:
  - Parenthetical suffixes: (Remastered), (Live), (Deluxe Edition), etc.
  - Bracket suffixes: [Bonus Track], [Explicit], etc.
  - Featuring artists: feat., ft., featuring, with
  - Artist separators: &, +, ,, /
  - Unicode/diacritic folding: Beyoncé → Beyonce (Latin letters only)
  - Punctuation and whitespace normalization

Three forms of a title are used (PLAN §3.2):
  - the RAW title (what Lidarr has; always tried first),
  - the QUERY form (``query_form``): NFC, whitespace collapsed, noise
    parentheticals and feat. credits removed; script, punctuation and case
    kept, so exact-match APIs still find "残酷な天使のテーゼ" or "Help!",
  - the COMPARISON key (``comparison_key``): an aggressive, script-safe key
    used only to score results against the query, never sent to a provider.
"""

import re
import unicodedata

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

# Featuring patterns — captures the featured artist(s). Two forms:
#   - bracketed: "(feat. X)", "[ft. X]", "(with X)" — "with" only counts inside
#     brackets, so titles like "Stay With Me" keep their words;
#   - bare: " feat. X" / " featuring X" at the end, which needs a preceding
#     space so "Left", "Lift" or "Soft Cell" aren't cut at their "ft".
# extract_featuring reads group(1) or group(2).
_FEAT_PATTERN = re.compile(
    r'\s*(?:'
    r'[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]]+?)\s*[\)\]]'
    r'|(?<=\s)(?:feat\.?|ft\.?|featuring)\s+([^\(\)\[\]]+?)'
    r')\s*$',
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

# Quotation marks to normalize (curly/typographic quotes, primes, acute and
# grave accents used as apostrophes) — all fold to a plain apostrophe.
_QUOTES = re.compile('[\u2018\u2019\u201a\u201b\u2032\u00b4`\u201c\u201d\u201e\u201f"\u00ab\u00bb]')


# ---------- Core Functions ----------

def _is_latin(ch: str) -> bool:
    return 'LATIN' in unicodedata.name(ch, '')


def _unicode_fold(text: str) -> str:
    """Fold diacritics on LATIN letters only: Beyoncé → Beyonce, Ñ → N.

    Combining marks are dropped only when their base letter is Latin, then
    the text is recomposed (NFC). Other scripts keep their marks, so Hangul
    stays syllables, kana keep their voicing marks (ゼ is not セ) and Cyrillic
    й/ё stay distinct letters.
    """
    if not text:
        return ''
    out = []
    base_latin = False
    for ch in unicodedata.normalize('NFD', text):
        if unicodedata.category(ch) == 'Mn':
            if base_latin:
                continue
        else:
            base_latin = _is_latin(ch)
        out.append(ch)
    return unicodedata.normalize('NFC', ''.join(out))


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
        feat_str = (match.group(1) or match.group(2)).strip()
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


# ---------- Comparison key (scoring only) ----------

# Letters that NFKD does not decompose but that users and databases spell
# both ways (Æon/Aeon, Weiß/Weiss, Mø/Mo, Łódź/Lodz).
_LETTER_FOLD = str.maketrans({
    'ß': 'ss', 'æ': 'ae', 'œ': 'oe', 'ø': 'o', 'ł': 'l', 'đ': 'd', 'ð': 'd',
    'þ': 'th', 'ı': 'i', 'ħ': 'h',
})

# Anchored dash-suffix version tags ("Help! - Remastered 2009",
# "Song - Live at Wembley", "Song - Radio Edit"). Applied to the casefolded
# string. Never strips a title that merely starts with one of the words
# ("Pt. 2 - Live Forever" keeps "Live Forever").
_DASH_SUFFIX_RE = re.compile(
    r'\s+[-–—]\s+(?:'
    r'(?:\d{4}\s+)?(?:digital(?:ly)?\s+)?re-?master(?:ed)?(?:\s+(?:version|edition|\d{4}))*'
    r'|\d{4}\s+(?:re-?master(?:ed)?|(?:stereo\s+|mono\s+)?(?:re)?mix|version)(?:\s+version)?'
    r'|live(?:\s+(?:at|from|in|on)\s.*|\s+(?:version|recording|edit))?'
    r'|(?:radio|single|album|lp|original|extended|clean|explicit|main|short)\s+(?:edit|version|mix)'
    r'|edit|single|bonus\s+track|explicit|clean'
    r'|(?:mono|stereo)(?:\s+(?:version|mix))?'
    r')\s*$',
    re.IGNORECASE,
)

# Bracket groups made only of release noise ("(Live 1986)", "(2011 Mono)").
_BRACKET_GROUP_RE = re.compile(r'\(([^()]*)\)|\[([^\[\]]*)\]|（([^（）]*)）|【([^【】]*)】')
_BRACKET_NOISE_WORDS = frozenset({
    'remaster', 'remastered', 'version', 'edit', 'mix', 'mono', 'stereo', 'live', 'deluxe',
    'edition', 'explicit', 'clean', 'bonus', 'track', 'single', 'album', 'radio', 'original',
    'digital', 'digitally', 'expanded', 'anniversary', 'special', 'super', 'lp', 'ep',
})
_YEAR_RE = re.compile(r"^(?:\d{4}|'\d{2})$")

_APOSTROPHES = re.compile("['‘’‚‛′´`]")

_ROMAN = {
    'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10,
    'xi': 11, 'xii': 12, 'xiii': 13, 'xiv': 14, 'xv': 15, 'xvi': 16, 'xvii': 17, 'xviii': 18,
    'xix': 19, 'xx': 20,
}
_PART_WORDS = frozenset({'pt', 'pts', 'part', 'parts'})
# Words after which a single-letter roman numeral (I, V, X) is a number.
_NUMBERING_WORDS = _PART_WORDS | frozenset({'vol', 'volume', 'chapter', 'no', 'act', 'book', 'op', 'movement'})


def fold_text(text: str) -> str:
    """NFKC, casefold, Latin-only diacritic fold, ß/æ/ø/ł folds."""
    if not text:
        return ''
    t = unicodedata.normalize('NFKC', text).casefold().translate(_LETTER_FOLD)
    return _unicode_fold(t)


def _punct_to_space(text: str) -> str:
    return ''.join(' ' if unicodedata.category(c)[0] in 'PSZ' else c for c in text)


def _drop_noise_brackets(text: str) -> str:
    def repl(m):
        content = next(g for g in m.groups() if g is not None)
        words = [w for w in re.split(r'[\s,;/&\-]+', content.casefold()) if w]
        if words and all(w in _BRACKET_NOISE_WORDS or _YEAR_RE.match(w) for w in words):
            return ' '
        return m.group(0)
    return _BRACKET_GROUP_RE.sub(repl, text)


def _strip_leading_artist(folded_title: str, artist: str | None) -> str:
    """'adele - hello' → 'hello' when the artist is 'Adele'."""
    if not artist:
        return folded_title
    a = re.sub(r'\s+', ' ', fold_text(artist)).strip()
    if not a or not folded_title.startswith(a):
        return folded_title
    m = re.match(r'\s*[-–—:]\s+', folded_title[len(a):])
    if not m:
        return folded_title
    rest = folded_title[len(a) + m.end():].strip()
    return rest or folded_title


def _map_numbers(tokens):
    out = []
    numbering = False
    for tok in tokens:
        if tok in _PART_WORDS:
            out.append('part')
            numbering = True
            continue
        if tok in _ROMAN and (len(tok) >= 2 or numbering):
            out.append(str(_ROMAN[tok]))
            continue
        if tok.isdigit():
            try:
                out.append(str(int(tok)))
            except ValueError:
                out.append(tok)
            continue
        numbering = tok in _NUMBERING_WORDS
        out.append(tok)
    return out


def comparison_key(title: str, artist: str | None = None) -> str:
    """Script-safe comparison key for a title (scoring only, never queried).

    NFKC + casefold, Latin-only diacritic fold, ß→ss, æ→ae, ø→o, ł→l,
    apostrophes dropped, anchored dash-suffix version tags removed, noise
    parentheticals and feat. credits removed, a leading "<artist> - " removed
    (when ``artist`` is given), punctuation → spaces, roman numerals and
    Pt/Pts/Parts mapped to "part N".
    """
    if not title:
        return ''
    t = fold_text(title)
    t = _QUOTES.sub("'", t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = _strip_leading_artist(t, artist)
    t = _DASH_SUFFIX_RE.sub('', t)
    t = _NOISE_TERMS.sub(' ', t)
    t = _drop_noise_brackets(t)
    t = _FEAT_PATTERN.sub('', t)
    t = _FEAT_MID_PATTERN.sub(' ', t)
    t = t.replace('&', ' and ')
    t = _APOSTROPHES.sub('', t)
    t = _punct_to_space(t)
    return ' '.join(_map_numbers(t.split()))


def comparison_key_variants(title: str, artist: str | None = None) -> list[str]:
    """The full key, plus the key with every bracket group removed."""
    keys = []
    for v in (title or '', _BRACKET_GROUP_RE.sub(' ', title or '')):
        k = comparison_key(v, artist)
        if k and k not in keys:
            keys.append(k)
    return keys


def key_digits(key: str) -> frozenset:
    """Numbers in a comparison key (Pt 1 vs Pt 2, 1979 vs 1985)."""
    return frozenset(tok for tok in (key or '').split() if tok.isdigit())


# ---------- Query form (sent to providers) ----------

def _has_instrumental_qualifier(text: str) -> bool:
    from lyrarr.metadata.lyrics_text import QUALIFIER
    return bool(re.search(QUALIFIER, text or '', re.IGNORECASE | re.VERBOSE))


def _is_strong_instrumental(title: str) -> bool:
    from lyrarr.metadata.lyrics_text import instrumental_title_tier
    return instrumental_title_tier(title) == 'strong'


def query_form(title: str) -> str:
    """Title as a provider should be queried after the raw title.

    NFC, whitespace collapsed, noise parentheticals ("(Remastered 2011)",
    "[Explicit]") and feat. credits removed. Script, punctuation and case are
    kept. A bracket holding an instrumental qualifier is never removed.
    """
    if not title:
        return ''
    t = unicodedata.normalize('NFC', title)
    t = re.sub(r'\s+', ' ', t).strip()

    def noise(m):
        return m.group(0) if _has_instrumental_qualifier(m.group(0)) else ' '

    out = _NOISE_TERMS.sub(noise, t)
    out = _FEAT_PATTERN.sub('', out)
    out = _FEAT_MID_PATTERN.sub(' ', out)
    out = re.sub(r'\s*[\(\[]\s*[\)\]]\s*', ' ', out)
    out = re.sub(r'\s+', ' ', out).strip()
    return out or t


def artist_query_form(artist: str) -> str:
    """Artist as sent to providers: NFC and collapsed whitespace only."""
    if not artist:
        return ''
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', artist)).strip()


# ---------- Artist credits ----------

# Separators inside a RAW credit string: , ; / & and x vs feat. ft. featuring with
_CREDIT_SPLIT_RE = re.compile(
    r'\s*(?:[,;/，、]|\s&\s|\s+and\s+|\s+x\s+|\s+vs\.?\s+'
    r'|\s+(?:feat\.?|ft\.?|featuring|with)\s+)\s*',
    re.IGNORECASE,
)
# Only the explicit "this is a guest" separators (artist_primary).
_EXPLICIT_PRIMARY_RE = re.compile(
    r'\s+(?:feat\.?|ft\.?|featuring|x|vs\.?)\s+', re.IGNORECASE)
_FEAT_BRACKET_RE = re.compile(r'^\s*(?:feat\.?|ft\.?|featuring|with)\s+', re.IGNORECASE)


def parse_artist_credit(artist: str) -> dict:
    """Split a RAW artist credit.

    Returns {'main': str, 'aliases': [str], 'parts': [str], 'primary': str}:
      main     the credit with bracketed aliases removed ('IU (아이유)' → 'IU'),
      aliases  bracketed alternative names ('아이유'); '(feat. X)' groups are
               guest credits and go to parts instead,
      parts    individual names split on , ; / & and x vs feat. ft. featuring with
               (only when there is more than one),
      primary  the name before an explicit feat./ft./x/vs separator (or main).
    """
    raw = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', artist or '')).strip()
    aliases, guests = [], []

    def repl(m):
        content = next(g for g in m.groups() if g is not None).strip()
        if _FEAT_BRACKET_RE.match(content):
            guests.extend(p for p in _CREDIT_SPLIT_RE.split(_FEAT_BRACKET_RE.sub('', content)) if p.strip())
        elif content:
            aliases.append(content)
        return ' '

    main = re.sub(r'\s+', ' ', _BRACKET_GROUP_RE.sub(repl, raw)).strip()
    if not main and aliases:
        main = aliases.pop(0)
    parts = [p.strip() for p in _CREDIT_SPLIT_RE.split(main) if p and p.strip()]
    parts += [g.strip() for g in guests]
    if len(parts) < 2:
        parts = []
    primary = _EXPLICIT_PRIMARY_RE.split(main)[0].strip() if main else ''
    return {'main': main, 'aliases': aliases, 'parts': parts, 'primary': primary or main}


def explicit_primary_artist(artist: str) -> str:
    """Normalised first artist, split ONLY on feat./ft./featuring/x/vs.

    'Simon & Garfunkel' and 'Tyler, The Creator' stay whole;
    'Artist feat. Other' / 'Artist x Other' → 'artist'.
    """
    if not artist:
        return ''
    text = _FEAT_PATTERN.sub('', artist)
    text = _FEAT_MID_PATTERN.sub('', text)
    first = _EXPLICIT_PRIMARY_RE.split(text)[0]
    return normalize_artist(first.strip()) or normalize_artist(artist)


def artist_key(name: str) -> str:
    """Comparison key for an artist name: folded, '&' → 'and', apostrophes
    dropped, punctuation → spaces, leading 'the' removed."""
    if not name:
        return ''
    t = fold_text(name)
    t = _QUOTES.sub("'", t)
    t = t.replace('&', ' and ').replace('+', ' and ')
    t = _APOSTROPHES.sub('', t)
    t = _punct_to_space(t)
    toks = t.split()
    if len(toks) > 1 and toks[0] == 'the':
        toks = toks[1:]
    return ' '.join(toks)


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
            title_query    — query form (noise/feat. removed, script and case kept)
            title_key      — comparison key (scoring form)
            title_clean    — normalized title (noise removed, lowercased)
            artist         — original artist
            artist_query   — artist query form (NFC, whitespace collapsed)
            artist_clean   — normalized full artist
            artist_primary — first artist before an explicit feat./ft./x/vs
                             separator ('Simon & Garfunkel' is not split)
            featuring      — list of featured artist names
            title_variants — list of title strings to try (most specific → broadest)

    A title whose bracket/dash holds a STRONG instrumental qualifier
    ("Bite the Pain (1996 demo, instrumental)") never yields a variant without
    the qualifier: querying the vocal title would fetch the vocal lyrics.
    """
    strong = _is_strong_instrumental(title or '')
    clean_title = normalize_title(title)
    if strong and not _has_instrumental_qualifier(clean_title):
        clean_title = normalize_artist(title)  # basic normalisation only: keeps "(Instrumental)"
    clean_artist = normalize_artist(artist)
    primary_artist = explicit_primary_artist(artist)
    feat_title, featuring = extract_featuring(title)

    # Generate title variants from most specific to broadest
    title_variants = []

    # 1. Cleaned title (noise stripped but structure preserved)
    if clean_title:
        title_variants.append(clean_title)

    # 2. Title without any parentheticals at all
    no_parens = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', '', title or '').strip()
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

    if strong:
        title_variants = [v for v in title_variants if _has_instrumental_qualifier(v)]

    # De-dup while preserving order
    seen = set()
    unique_variants = []
    for v in title_variants:
        if v not in seen:
            seen.add(v)
            unique_variants.append(v)

    return {
        'title': title or '',
        'title_query': query_form(title),
        'title_key': comparison_key(title),
        'title_clean': clean_title,
        'artist': artist or '',
        'artist_query': artist_query_form(artist),
        'artist_clean': clean_artist,
        'artist_primary': primary_artist,
        'featuring': featuring,
        'title_variants': unique_variants,
    }


def duration_seconds(duration) -> int | None:
    """Safely coerce a track duration to whole seconds.

    Durations are seconds everywhere in lyrarr (Lidarr sync converts its
    milliseconds once, at import). No unit guessing: a 1200 s track is 1200 s.

    Args:
        duration: Duration in seconds (int/float/str or None)

    Returns:
        Duration in whole seconds, or None when missing/invalid/non-positive
    """
    if duration is None:
        return None

    try:
        dur = int(round(float(duration)))
    except (ValueError, TypeError):
        return None

    if dur <= 0:
        return None
    return dur
