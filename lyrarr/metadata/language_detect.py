# coding=utf-8

"""
Lightweight language detection for lyrics text.
Uses the 'langdetect' library (Google's language-detection, MIT license).
"""

import logging
import re

logger = logging.getLogger(__name__)

# LRC timestamp pattern: [mm:ss.xx] or [mm:ss:xx]
_LRC_TIMESTAMP = re.compile(r'\[\d{1,2}:\d{2}[.:]\d{2,3}\]\s*')
# LRC metadata tags: [ar:Artist Name], [ti:Title], etc.
_LRC_TAG = re.compile(r'^\[(?:ar|ti|al|au|length|by|offset|re|ve|id):.*\]$', re.MULTILINE)

# Map of common language codes to display names
LANGUAGE_NAMES = {
    'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German',
    'it': 'Italian', 'pt': 'Portuguese', 'ja': 'Japanese', 'ko': 'Korean',
    'zh-cn': 'Chinese (Simplified)', 'zh-tw': 'Chinese (Traditional)',
    'ru': 'Russian', 'ar': 'Arabic', 'hi': 'Hindi', 'tr': 'Turkish',
    'nl': 'Dutch', 'pl': 'Polish', 'sv': 'Swedish', 'th': 'Thai',
    'vi': 'Vietnamese', 'id': 'Indonesian', 'uk': 'Ukrainian',
    'cs': 'Czech', 'ro': 'Romanian', 'hu': 'Hungarian', 'el': 'Greek',
    'da': 'Danish', 'fi': 'Finnish', 'no': 'Norwegian', 'he': 'Hebrew',
    'bg': 'Bulgarian', 'hr': 'Croatian', 'sk': 'Slovak', 'sl': 'Slovenian',
    'lt': 'Lithuanian', 'lv': 'Latvian', 'et': 'Estonian',
    'af': 'Afrikaans', 'sw': 'Swahili', 'tl': 'Filipino', 'bn': 'Bengali',
    'ta': 'Tamil', 'te': 'Telugu', 'ml': 'Malayalam', 'mr': 'Marathi',
}


def strip_lrc_formatting(text: str) -> str:
    """Remove LRC timestamps and metadata tags to get raw lyrics text."""
    text = _LRC_TAG.sub('', text)
    text = _LRC_TIMESTAMP.sub('', text)
    # Remove empty lines resulting from stripping
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return '\n'.join(lines)


def detect_language(text: str, min_confidence: float = 0.5) -> str | None:
    """Detect the language of lyrics text.

    Args:
        text: Lyrics content (may include LRC timestamps)
        min_confidence: Minimum confidence threshold (0-1)

    Returns:
        ISO 639-1 language code (e.g. 'en', 'ja', 'ko') or None if detection fails.
    """
    if not text or len(text.strip()) < 20:
        return None

    try:
        from langdetect import detect_langs
        from langdetect.detector_factory import LangDetectException

        # Strip LRC formatting before detection
        clean_text = strip_lrc_formatting(text)

        if len(clean_text.strip()) < 20:
            return None

        results = detect_langs(clean_text)
        if results and results[0].prob >= min_confidence:
            lang = results[0].lang
            logger.debug(f"Language detected: {lang} ({results[0].prob:.0%})")
            return lang

        return None

    except Exception as e:
        logger.debug(f"Language detection failed: {e}")
        return None


def is_synced_lyrics(text: str) -> bool:
    """Check if lyrics text contains LRC timestamps (synced format)."""
    if not text:
        return False
    # Count lines with timestamps — if more than 30% have timestamps, it's synced
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    timestamped = sum(1 for line in lines if _LRC_TIMESTAMP.search(line))
    return timestamped / len(lines) > 0.3


def get_language_name(code: str | None) -> str:
    """Get human-readable language name from ISO code."""
    if not code:
        return 'Unknown'
    return LANGUAGE_NAMES.get(code.lower(), code.upper())
