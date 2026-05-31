# coding=utf-8

"""
LRC timestamp validation and repair.

Validates synced lyrics (LRC format) for common timestamp issues and
automatically repairs them when possible:
  - Non-monotonic timestamps (out of order)
  - Duplicate timestamps on different lines
  - Large gaps between lines
  - Mixed synced/plain content (some lines missing timestamps)
  - Malformed timestamp formats
"""

import re
import logging

logger = logging.getLogger(__name__)

# Parse LRC timestamp: [mm:ss.xx] or [mm:ss:xx] or [mm:ss.xxx]
_TS_PATTERN = re.compile(r'\[(\d{1,3}):(\d{2})[.:](\d{2,3})\]')

# LRC metadata tags (not timestamps)
_META_TAG = re.compile(r'^\[(?:ar|ti|al|au|length|by|offset|re|ve|id|la):.*\]$', re.IGNORECASE)

# Maximum reasonable gap between lines (seconds)
MAX_LINE_GAP = 30.0


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
        return {'valid': False, 'issues': [], 'stats': {}}

    lines = content.strip().splitlines()
    issues = []
    timestamps = []  # (line_index, timestamp_seconds, line_text)

    total_lines = 0
    timestamped_lines = 0
    meta_lines = 0

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
        ts_match = _TS_PATTERN.search(stripped)
        if ts_match:
            timestamped_lines += 1
            ts_seconds = _parse_ts_to_seconds(ts_match)
            text_after = _TS_PATTERN.sub('', stripped).strip()
            timestamps.append((i + 1, ts_seconds, text_after))
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
    }

    return {
        'valid': len(issues) == 0,
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

        ts_match = _TS_PATTERN.search(stripped)
        if ts_match:
            ts_seconds = _parse_ts_to_seconds(ts_match)
            text = _TS_PATTERN.sub('', stripped).strip()
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
        # Build a map of content_index → timestamp for known lines
        ts_map = {idx: ts for ts, text, idx in ts_lines}

        for plain_idx, plain_text in plain_lines:
            # Find surrounding timestamps
            prev_ts = None
            next_ts = None

            for ts, text, idx in ts_lines:
                if idx < plain_idx:
                    prev_ts = ts
                elif idx > plain_idx and next_ts is None:
                    next_ts = ts
                    break

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


def _parse_ts_to_seconds(match):
    """Convert a regex match of [mm:ss.xx] to seconds."""
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    centiseconds = match.group(3)
    # Normalize to 2-digit centiseconds
    if len(centiseconds) == 3:
        ms = int(centiseconds)
        frac = ms / 1000.0
    else:
        cs = int(centiseconds)
        frac = cs / 100.0
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
