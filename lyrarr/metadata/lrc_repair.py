
"""
LRC timestamp validation and repair.

Validates synced lyrics (LRC format) for common timestamp issues and
automatically repairs them when possible:
  - Non-monotonic timestamps (out of order)
  - Duplicate timestamps on different lines
  - Large gaps between lines (reported only — never triggers a rewrite)
  - Mixed synced/plain content (some lines missing timestamps)
  - Malformed timestamp formats
  - Compressed LRC (several timestamps sharing one line) — expanded to one
    line per timestamp
"""

import logging
import re

logger = logging.getLogger(__name__)

# Parse LRC timestamp: [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx] or [mm:ss:xx]
_TS_PATTERN = re.compile(r'\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]')

# One or more timestamps at the start of a line ("compressed" LRC puts every
# time a chorus is sung on a single line: [00:20.00][01:10.00]Chorus).
_LEADING_TS = re.compile(r'^((?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+)(.*)$')

# LRC timestamp anywhere in a line, for stripping (shared with validation,
# language detection and coherence so every module agrees on what's synced).
LRC_TIMESTAMP = re.compile(r'\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*')

# LRC metadata/ID tags (not timestamps): the standard ones in any case, plus
# any other lowercase "[key:value]" tag ([tool:..], [#:..], [lang:..]) so
# unknown tags are preserved as tags instead of being turned into lyric lines.
# Capitalised "[Chorus: Artist]" section headers deliberately don't match.
_META_TAG = re.compile(
    r'^\[(?:(?i:ar|ti|al|au|length|by|offset|re|ve|id|la)|[a-z#][a-z#_-]{0,15}):[^\]]*\]$'
)
LRC_META_TAG = _META_TAG

# Maximum reasonable gap between lines (seconds)
MAX_LINE_GAP = 30.0

# Issue types validate_lrc reports but repair_lrc can't (and shouldn't) fix.
_REPORT_ONLY_ISSUES = {'large_gap'}


def validate_lrc(content):
    """Validate LRC timestamp integrity.

    Args:
        content: LRC formatted lyrics text

    Returns:
        Dict with:
            valid: bool — True if no issues found
            issues: list of dicts with line, type, detail
            stats: dict with total_lines, timestamped_lines,
                   non_monotonic, duplicates, gaps
    """
    if not content:
        return {'valid': False, 'needs_repair': False, 'issues': [], 'stats': {}}

    lines = content.strip().splitlines()
    issues = []
    timestamps = []  # (line_index, timestamp_seconds, line_text)

    total_lines = 0
    timestamped_lines = 0
    meta_lines = 0
    compressed = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Skip metadata tags
        if _META_TAG.match(stripped):
            meta_lines += 1
            continue

        total_lines += 1

        # Extract timestamp(s) from the line
        parsed = _parse_line_timestamps(stripped)
        if parsed:
            stamps, text_after = parsed
            timestamped_lines += 1
            if len(stamps) > 1:
                compressed += 1
                issues.append({
                    'line': i + 1,
                    'type': 'compressed_timestamps',
                    'detail': f'{len(stamps)} timestamps on one line',
                })
            # Sequence checks run on the line's first time; a compressed
            # line's later repeats are expected to jump ahead.
            timestamps.append((i + 1, stamps[0], text_after))
        else:
            # Non-timestamped content line
            if timestamped_lines > 0:
                # Some lines have timestamps, this one doesn't — mixed content
                issues.append({
                    'line': i + 1,
                    'type': 'missing_timestamp',
                    'detail': f'Line has no timestamp in synced content: "{stripped[:50]}"',
                })

    # Check for malformed timestamps
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and stripped.startswith('[') and not _META_TAG.match(stripped):
            # Looks like it should be a timestamp line but doesn't match pattern
            if not _TS_PATTERN.search(stripped) and re.match(r'\[\d', stripped):
                issues.append({
                    'line': i + 1,
                    'type': 'malformed_timestamp',
                    'detail': f'Malformed timestamp format: "{stripped[:50]}"',
                })

    # Analyze timestamp sequence
    non_monotonic = 0
    duplicate_ts = 0
    large_gaps = 0

    for idx in range(1, len(timestamps)):
        prev_line, prev_ts, prev_text = timestamps[idx - 1]
        curr_line, curr_ts, curr_text = timestamps[idx]

        # Non-monotonic (out of order)
        if curr_ts < prev_ts:
            non_monotonic += 1
            issues.append({
                'line': curr_line,
                'type': 'non_monotonic',
                'detail': f'Timestamp {_seconds_to_ts(curr_ts)} is before '
                          f'previous {_seconds_to_ts(prev_ts)}',
            })

        # Duplicate timestamps
        if abs(curr_ts - prev_ts) < 0.01 and curr_text and prev_text:
            duplicate_ts += 1
            issues.append({
                'line': curr_line,
                'type': 'duplicate_timestamp',
                'detail': f'Same timestamp {_seconds_to_ts(curr_ts)} as previous line',
            })

        # Large gaps
        gap = curr_ts - prev_ts
        if gap > MAX_LINE_GAP and curr_ts > 0 and prev_ts > 0:
            large_gaps += 1
            issues.append({
                'line': curr_line,
                'type': 'large_gap',
                'detail': f'{gap:.1f}s gap between lines ({_seconds_to_ts(prev_ts)} → '
                          f'{_seconds_to_ts(curr_ts)})',
            })

    stats = {
        'total_lines': total_lines,
        'timestamped_lines': timestamped_lines,
        'meta_lines': meta_lines,
        'non_monotonic': non_monotonic,
        'duplicates': duplicate_ts,
        'gaps': large_gaps,
        'missing_timestamps': total_lines - timestamped_lines if timestamped_lines > 0 else 0,
        'compressed': compressed,
    }

    return {
        'valid': len(issues) == 0,
        # Large gaps are normal (instrumental breaks) and repair can't fix
        # them — callers should only rewrite a file for the other issues.
        'needs_repair': any(i['type'] not in _REPORT_ONLY_ISSUES for i in issues),
        'issues': issues,
        'stats': stats,
    }


def repair_lrc(content):
    """Attempt to fix common LRC issues.

    Repairs:
    1. Non-monotonic: re-sort lines by timestamp
    2. Duplicate timestamps: add small offset (10ms) to disambiguate
    3. Mixed synced/plain: interpolate missing timestamps between known ones
    4. Malformed format: normalize to [mm:ss.xx] format
    5. Compressed LRC: one output line per timestamp

    Metadata/ID tags (known or not) are kept, at the top.

    Args:
        content: LRC formatted lyrics text

    Returns:
        Repaired LRC content string
    """
    if not content:
        return content

    lines = content.strip().splitlines()
    meta_lines = []
    ts_lines = []     # (timestamp_seconds, text, original_line)
    plain_lines = []   # (index_among_all_content, text)

    content_index = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if _META_TAG.match(stripped):
            meta_lines.append(stripped)
            continue

        parsed = _parse_line_timestamps(stripped)
        if parsed:
            stamps, text = parsed
            for ts_seconds in stamps:
                ts_lines.append((ts_seconds, text, content_index))
        else:
            plain_lines.append((content_index, stripped))

        content_index += 1

    # --- Repair 1: Sort by timestamp (fix non-monotonic) ---
    ts_lines.sort(key=lambda x: x[0])

    # If no timestamped lines exist, return original content unchanged
    if not ts_lines:
        return content

    # --- Repair 2: Disambiguate duplicate timestamps ---
    for i in range(1, len(ts_lines)):
        if abs(ts_lines[i][0] - ts_lines[i - 1][0]) < 0.01:
            # Add 10ms offset
            ts_lines[i] = (ts_lines[i - 1][0] + 0.01, ts_lines[i][1], ts_lines[i][2])

    # --- Repair 3: Interpolate timestamps for plain lines ---
    if plain_lines and ts_lines:
        # First time of each timestamped line, in file order — the neighbours
        # a plain line sits between (an expanded compressed line has several).
        first_ts_by_line = {}
        for ts, _text, idx in ts_lines:
            if idx not in first_ts_by_line or ts < first_ts_by_line[idx]:
                first_ts_by_line[idx] = ts
        for plain_idx, plain_text in plain_lines:
            # Find surrounding timestamps
            before = [i for i in first_ts_by_line if i < plain_idx]
            after = [i for i in first_ts_by_line if i > plain_idx]
            prev_ts = first_ts_by_line[max(before)] if before else None
            next_ts = first_ts_by_line[min(after)] if after else None

            if prev_ts is not None and next_ts is not None:
                # Interpolate
                interpolated = prev_ts + (next_ts - prev_ts) * 0.5
            elif prev_ts is not None:
                interpolated = prev_ts + 2.0  # Default 2s after previous
            elif next_ts is not None:
                interpolated = max(0, next_ts - 2.0)
            else:
                continue  # Can't interpolate

            ts_lines.append((interpolated, plain_text, plain_idx))

        # Re-sort after adding interpolated lines
        ts_lines.sort(key=lambda x: x[0])

    # --- Build output ---
    output_lines = []

    # Metadata first
    for meta in meta_lines:
        output_lines.append(meta)

    if meta_lines and ts_lines:
        output_lines.append('')  # Blank line after metadata

    # Timestamped lyrics
    for ts, text, _ in ts_lines:
        ts_str = _seconds_to_ts(ts)
        output_lines.append(f'{ts_str} {text}' if text else ts_str)

    return '\n'.join(output_lines)


def _parse_line_timestamps(line):
    """Split a line into (list of leading timestamps in seconds, text) or None.

    Only timestamps at the start of the line count, so a "[01:23]" that
    happens to appear inside lyric text isn't mistaken for a time tag.
    """
    m = _LEADING_TS.match(line)
    if not m:
        return None
    stamps = [_parse_ts_to_seconds(t) for t in _TS_PATTERN.finditer(m.group(1))]
    return stamps, m.group(2).strip()


def _parse_ts_to_seconds(match):
    """Convert a regex match of [mm:ss(.fraction)] to seconds."""
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3)
    # The fraction's digit count is its scale: .5 = 500ms, .50 = 500ms, .500 = 500ms
    frac = int(fraction) / (10 ** len(fraction)) if fraction else 0.0
    return minutes * 60 + seconds + frac


def _seconds_to_ts(seconds):
    """Convert seconds to LRC timestamp format [mm:ss.xx]."""
    if seconds < 0:
        seconds = 0
    # Work in integer centiseconds to avoid float truncation. The naive
    # int((seconds - int(seconds)) * 100) loses small offsets: 5.01 is stored
    # as 5.00999…, so the centisecond part truncates to 0 and the 10ms offset
    # repair_lrc adds to disambiguate duplicate timestamps would vanish.
    total_cs = int(round(seconds * 100))
    minutes = total_cs // 6000
    secs = (total_cs % 6000) // 100
    centiseconds = total_cs % 100
    return f'[{minutes:02d}:{secs:02d}.{centiseconds:02d}]'
