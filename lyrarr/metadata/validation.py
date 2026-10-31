"""
Lyrics quality validation pipeline.

Catches common quality issues in downloaded lyrics before saving to disk:
  - Marker-only content ("[Instrumental]") — reported as is_instrumental,
    which is a *vote* for the instrumental policy, never a verdict by itself
  - Provider placeholders ("Lyrics not available") and credit-only text
  - Truncated content from free-tier APIs
  - Duplicate/repeated lines suggesting parsing errors
  - Garbage content (encoding artifacts, HTML entities, excessive special characters)
  - LRC files with only metadata tags but no actual lyrics

A language-mismatch check exists (artist_language), but the download worker
doesn't pass an artist language (there is no reliable per-artist language),
so it only runs for callers that supply one.

synced_fits_duration() checks synced lyrics against the track length.
"""

import logging
import re
from collections import Counter

from lyrarr.metadata.lrc_repair import (
    LRC_ENHANCED_TIMESTAMP,
    LRC_META_TAG,
    LRC_TIMESTAMP,
    is_fake_synced,
    lyric_timestamps,
)
from lyrarr.metadata.lyrics_text import (
    classify_lyrics_text,
    instrumental_title_tier,
    is_credit_line,
    is_placeholder_line,
)

logger = logging.getLogger(__name__)

# Section header line ("[Verse 1]", "[Chorus: Artist]"). Parenthesised lines
# are ad-libs/backing vocals — real lyrics — so they don't count.
_SECTION_HEADER = re.compile(r'^\s*\[[^\]]*\]\s*$')

# Musixmatch's free-tier footer must never end up in a saved file.
_PROVIDER_DISCLAIMER = re.compile(r'This Lyrics is NOT for Commercial use', re.IGNORECASE)

# Synced lyrics may end this long after the track before they're "another edit".
SYNC_OVERRUN_TOLERANCE_S = 3.0


def is_instrumental_title(title):
    """Whether a track title is a STRONG instrumental/karaoke title (PLAN §1.2).

    "Song (Instrumental)", "Song (Off Vocal)", "Song - Instrumental",
    "Song (Karaoke Version)"... but not "The Instrumental", "Song
    (Instrumental Intro)" or a bare "Instrumental" (those are weak or none).
    """
    if not title:
        return False
    return instrumental_title_tier(title) == 'strong'


def synced_fits_duration(content, duration_s):
    """Whether synced lyrics fit the track length. Returns (ok, reason).

    reason: 'overrun' (last lyric timestamp > duration + 3 s: timed for a
    longer edit), 'starts_after_end' (first lyric timestamp beyond the end)
    or 'fake_synced' (the timing carries no information). `[offset:]` is
    honoured. An unknown duration or untimed content is (True, None).
    """
    try:
        d = float(duration_s) if duration_s else None
    except (TypeError, ValueError):
        d = None
    stamps = lyric_timestamps(content)
    if stamps and is_fake_synced(content, d):
        return False, 'fake_synced'
    if not d or d <= 0 or not stamps:
        return True, None
    if stamps[0] > d:
        return False, 'starts_after_end'
    if stamps[-1] > d + SYNC_OVERRUN_TOLERANCE_S:
        return False, 'overrun'
    return True, None

# LRC timestamps / metadata tags (shared definitions — [mm:ss] without a
# fraction and unknown [tool:..] tags included)
_LRC_TS = LRC_TIMESTAMP
_LRC_META = LRC_META_TAG

# Garbage character patterns (encoding artifacts, unescaped HTML entities)
_GARBAGE_CHARS = re.compile(
    r'[ïœãâ]{3,}|\\x[0-9a-f]{2}|&#x?[0-9a-fA-F]+;|&(?:amp|quot|lt|gt|apos|nbsp);'
)

# Minimum meaningful lyrics length (characters, after stripping)
MIN_LYRICS_LENGTH = 20

# Minimum line count for a track > 2 minutes
MIN_LINES_LONG_TRACK = 4

# Duplicate line threshold (percentage)
DUPLICATE_LINE_THRESHOLD = 0.5


def validate_lyrics(content, track_title=None, artist_name=None,
                    duration_s=None, detected_language=None,
                    artist_language=None):
    """Validate lyrics content quality and return issues found.

    Args:
        content: Lyrics text (may include LRC timestamps)
        track_title: Track title for context
        artist_name: Artist name for context
        duration_s: Track duration in seconds (TableTracks.duration)
        detected_language: Language detected in the lyrics (ISO 639-1)
        artist_language: Known language for this artist (ISO 639-1)

    Returns:
        Dict with:
            valid: bool — True if lyrics pass all checks
            issues: list of dicts with type, severity, message
            is_instrumental: bool
            is_truncated: bool
            language_mismatch: bool
    """
    issues = []
    is_instrumental = False
    is_truncated = False
    language_mismatch = False

    if not content or not content.strip():
        return {
            'valid': False,
            'issues': [{'type': 'empty', 'severity': 'error',
                        'message': 'Lyrics content is empty'}],
            'is_instrumental': False,
            'is_truncated': False,
            'language_mismatch': False,
        }

    # Strip LRC timestamps and metadata for analysis
    clean_lines = _strip_to_text_lines(content)
    clean_text = '\n'.join(clean_lines)
    line_count = len(clean_lines)

    # ------------------------------------------------------------------
    # 1. Instrumental detection (by title or by content)
    # ------------------------------------------------------------------
    if is_instrumental_title(track_title) or classify_lyrics_text(content) == 'marker':
        is_instrumental = True
        issues.append({
            'type': 'instrumental',
            'severity': 'info',
            'message': 'Track appears to be instrumental (no lyrics)',
        })
        return {
            'valid': True,  # Not an error — just informational
            'issues': issues,
            'is_instrumental': True,
            'is_truncated': False,
            'language_mismatch': False,
        }

    metadata_only = _is_metadata_only(content)

    if _is_placeholder(clean_lines):
        issues.append({
            'type': 'placeholder',
            'severity': 'error',
            'message': 'Content is a provider placeholder, not lyrics',
        })

    # ------------------------------------------------------------------
    # 2. Too short / minimal content (metadata-only LRC reports its own error)
    # ------------------------------------------------------------------
    if len(clean_text) < MIN_LYRICS_LENGTH and not metadata_only:
        issues.append({
            'type': 'too_short',
            'severity': 'error',
            'message': f'Lyrics content is too short ({len(clean_text)} chars)',
        })

    # ------------------------------------------------------------------
    # 3. Truncation detection
    # ------------------------------------------------------------------
    try:
        duration_s = float(duration_s) if duration_s else None
    except (TypeError, ValueError):
        duration_s = None
    if duration_s and duration_s > 120 and not metadata_only:  # Track > 2 minutes
        if line_count < MIN_LINES_LONG_TRACK:
            is_truncated = True
            issues.append({
                'type': 'truncated',
                'severity': 'warning',
                'message': f'Only {line_count} lines for a {int(duration_s)}s track — '
                           f'lyrics may be truncated (free API tier?)',
            })

    # Musixmatch's commercial-use footer marks a free-tier excerpt
    if _PROVIDER_DISCLAIMER.search(content):
        is_truncated = True
        issues.append({
            'type': 'provider_disclaimer',
            'severity': 'error',
            'message': 'Lyrics contain a provider "not for commercial use" footer (partial excerpt)',
        })

    # Also check for Musixmatch truncation marker
    if '...' in content[-50:] and line_count < 20:
        is_truncated = True
        issues.append({
            'type': 'truncated_ellipsis',
            'severity': 'warning',
            'message': 'Lyrics end with "..." — likely truncated by provider',
        })

    # ------------------------------------------------------------------
    # 4. Duplicate/repeated lines
    # ------------------------------------------------------------------
    if line_count >= 5:
        line_counts = Counter(clean_lines)
        unique_ratio = len(line_counts) / line_count if line_count > 0 else 1.0

        if unique_ratio < DUPLICATE_LINE_THRESHOLD:
            issues.append({
                'type': 'excessive_duplicates',
                'severity': 'warning',
                'message': f'Only {len(line_counts)}/{line_count} unique lines '
                           f'({unique_ratio:.0%}) — possible parsing error',
            })

    # ------------------------------------------------------------------
    # 5. Garbage detection
    # ------------------------------------------------------------------
    if _GARBAGE_CHARS.search(clean_text):
        issues.append({
            'type': 'garbage_chars',
            'severity': 'warning',
            'message': 'Content contains encoding artifacts or garbage characters',
        })

    # ------------------------------------------------------------------
    # 6. LRC metadata-only (no actual lyrics lines)
    # ------------------------------------------------------------------
    if metadata_only:
        issues.append({
            'type': 'metadata_only',
            'severity': 'error',
            'message': 'LRC file contains only metadata tags but no lyrics lines',
        })

    # ------------------------------------------------------------------
    # 7. Language mismatch
    # ------------------------------------------------------------------
    if detected_language and artist_language:
        # Only flag if the languages are from completely different families
        # Don't flag en/es or zh-cn/zh-tw mismatches
        if _is_language_mismatch(detected_language, artist_language):
            language_mismatch = True
            issues.append({
                'type': 'language_mismatch',
                'severity': 'warning',
                'message': f'Detected language ({detected_language}) differs significantly '
                           f'from artist language ({artist_language})',
            })

    # Determine overall validity
    has_errors = any(i['severity'] == 'error' for i in issues)

    return {
        'valid': not has_errors,
        'issues': issues,
        'is_instrumental': is_instrumental,
        'is_truncated': is_truncated,
        'language_mismatch': language_mismatch,
    }


def _strip_to_text_lines(content):
    """Strip LRC timestamps and metadata, return only text lines."""
    lines = []
    for line in content.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip LRC metadata tags
        if _LRC_META.match(stripped):
            continue
        # Remove LRC timestamps
        cleaned = LRC_ENHANCED_TIMESTAMP.sub('', _LRC_TS.sub('', stripped)).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _is_placeholder(clean_lines):
    """At least one provider placeholder line, and every other line is a
    placeholder, a credit line or a section header (no lyrics at all)."""
    has_placeholder = False
    for line in clean_lines:
        if is_placeholder_line(line):
            has_placeholder = True
        elif not (is_credit_line(line) or _SECTION_HEADER.match(line)):
            return False
    return has_placeholder


def _is_metadata_only(content):
    """Check if LRC content has only metadata tags and no lyrics."""
    has_metadata = False
    has_lyrics = False

    for line in content.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _LRC_META.match(stripped):
            has_metadata = True
        elif _LRC_TS.match(stripped):
            # Has a timestamp — check if there's text after it
            text_after = _LRC_TS.sub('', stripped).strip()
            if text_after:
                has_lyrics = True
        elif stripped and not stripped.startswith('['):
            has_lyrics = True

    return has_metadata and not has_lyrics


# Language family groups for mismatch detection
_LANG_FAMILIES = {
    'cjk': {'zh', 'zh-cn', 'zh-tw', 'ja', 'ko'},
    'latin_west': {'en', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'sv', 'da', 'no', 'fi'},
    'latin_east': {'pl', 'cs', 'sk', 'hr', 'sl', 'ro', 'hu', 'lt', 'lv', 'et'},
    'cyrillic': {'ru', 'uk', 'bg'},
    'arabic': {'ar', 'he'},
    'indic': {'hi', 'bn', 'ta', 'te', 'ml', 'mr'},
    'southeast_asian': {'th', 'vi', 'id', 'tl'},
}

_LANG_TO_FAMILY = {}
for family, langs in _LANG_FAMILIES.items():
    for lang in langs:
        _LANG_TO_FAMILY[lang] = family


def _is_language_mismatch(detected, expected):
    """Check if two languages are from completely different families."""
    det_family = _LANG_TO_FAMILY.get(detected.lower())
    exp_family = _LANG_TO_FAMILY.get(expected.lower())

    # If we can't determine families, don't flag
    if not det_family or not exp_family:
        return False

    # Same family = not a mismatch
    return det_family != exp_family
