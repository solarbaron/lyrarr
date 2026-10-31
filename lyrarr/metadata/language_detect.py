
"""
Lightweight language detection for lyrics text.
Uses the 'langdetect' library (Google's language-detection, MIT license).
"""

import logging

from lyrarr.metadata.lrc_repair import LRC_ENHANCED_TIMESTAMP, LRC_META_TAG, LRC_TIMESTAMP

logger = logging.getLogger(__name__)

# langdetect is non-deterministic by default: it seeds its RNG from the system
# clock, so the same lyrics can resolve to different languages across runs and
# keep churning the "undetected language" tab. A fixed seed makes detection
# reproducible.
try:
    from langdetect import DetectorFactory
    DetectorFactory.seed = 0
except Exception:  # pragma: no cover - langdetect optional at import time
    pass

# LRC timestamp ([mm:ss], [mm:ss.xx], [mm:ss.xxx], ...) and metadata tags
# ([ar:Artist], [tool:..], ...) — shared with lrc_repair so "is this synced?"
# means the same thing everywhere.
_LRC_TIMESTAMP = LRC_TIMESTAMP
_LRC_TAG = LRC_META_TAG

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
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _LRC_TAG.match(stripped):
            continue
        stripped = LRC_ENHANCED_TIMESTAMP.sub('', _LRC_TIMESTAMP.sub('', stripped)).strip()
        if stripped:
            lines.append(stripped)
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
    timestamped = sum(1 for line in lines if _LRC_TIMESTAMP.match(line.strip()))
    return timestamped / len(lines) > 0.3


def get_language_name(code: str | None) -> str:
    """Get human-readable language name from ISO code."""
    if not code:
        return 'Unknown'
    return LANGUAGE_NAMES.get(code.lower(), code.upper())
