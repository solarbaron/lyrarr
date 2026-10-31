"""
LRC timestamp validation and repair.

Validates synced lyrics (LRC format) for common timestamp issues and
repairs them when possible:
  - Non-monotonic timestamps (out of order)
  - Duplicate timestamps on different lines (runs of any length)
  - Large gaps between lines (reported only — never triggers a rewrite)
  - Mixed synced/plain content (some lines missing timestamps)
  - Malformed timestamp formats and invalid seconds ([00:60.00])
  - Compressed LRC (several timestamps sharing one line) — expanded to one
    line per timestamp
  - Fake-synced files (every lyric line on one timestamp) — turned into plain
    lyrics, since the timing carries no information

All timing arithmetic is done in integer centiseconds, so repairing is
exact and idempotent: repair_lrc(repair_lrc(x)) == repair_lrc(x), and the
output of repair_lrc never needs repair again.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Parse LRC timestamp: [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx] or [mm:ss:xx]
_TS_PATTERN = re.compile(r'\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]')

# Lenient form used by repair only: a single-digit seconds field ([0:5.20])
# is malformed but its meaning is clear.
_TS_LENIENT = re.compile(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]')

# One or more timestamps at the start of a line ("compressed" LRC puts every
# time a chorus is sung on a single line: [00:20.00][01:10.00]Chorus).
_LEADING_TS = re.compile(r'^((?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+)(.*)$')
_LEADING_TS_LENIENT = re.compile(r'^((?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]\s*)+)(.*)$')

# LRC timestamp anywhere in a line, for stripping (shared with validation,
# language detection, merge and coherence so every module agrees on what's
# synced).
LRC_TIMESTAMP = re.compile(r'\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*')

# Enhanced (word-level) LRC timestamps: "<00:12.34>word".
LRC_ENHANCED_TIMESTAMP = re.compile(r'<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>\s*')

# LRC metadata/ID tags (not timestamps): the standard ones in any case, plus
# any other lowercase "[key:value]" tag ([tool:..], [#:..], [lang:..]) so
# unknown tags are preserved as tags instead of being turned into lyric lines.
# Capitalised "[Chorus: Artist]" section headers deliberately don't match.
_META_TAG = re.compile(
    r'^\[(?:(?i:ar|ti|al|au|length|by|offset|re|ve|id|la)|[a-z#][a-z#_-]{0,15}):[^\]]*\]$'
)
LRC_META_TAG = _META_TAG

# A whole line that is one bracket group ("[Verse 1]", "[Chorus: X]").
_SECTION_HEADER = re.compile(r'^\s*\[[^\]]*\]\s*$')

_OFFSET_TAG = re.compile(r'^\[offset:\s*([+-]?\d+)\s*\]$', re.IGNORECASE)

# Maximum reasonable gap between lines (seconds)
MAX_LINE_GAP = 30.0

# Untimed lines after the last timed line are spaced this far apart (cs).
_TRAILING_STEP_CS = 200

# Issue types validate_lrc reports but repair_lrc can't (and shouldn't) fix.
_REPORT_ONLY_ISSUES = {'large_gap'}


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _frac_to_cs(fraction):
    """Fraction digits → centiseconds: '5' = 50, '50' = 50, '505' = 51 (rounded)."""
    if not fraction:
        return 0
    if len(fraction) == 1:
        return int(fraction) * 10
    if len(fraction) == 2:
        return int(fraction)
    return int(round(int(fraction) / 10))


def _match_to_cs(match):
    return int(match.group(1)) * 6000 + int(match.group(2)) * 100 + _frac_to_cs(match.group(3))


def _parse_ts_to_seconds(match):
    """Convert a regex match of [mm:ss(.fraction)] to seconds."""
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3)
    # The fraction's digit count is its scale: .5 = 500ms, .50 = 500ms, .500 = 500ms
    frac = int(fraction) / (10 ** len(fraction)) if fraction else 0.0
    return minutes * 60 + seconds + frac


def _cs_to_ts(cs):
    cs = max(int(cs), 0)
    return f'[{cs // 6000:02d}:{(cs % 6000) // 100:02d}.{cs % 100:02d}]'


def _seconds_to_ts(seconds):
    """Convert seconds to LRC timestamp format [mm:ss.xx] (via integer centiseconds)."""
    if seconds < 0:
        seconds = 0
    return _cs_to_ts(int(round(seconds * 100)))


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


def _parse_line_cs(line, lenient=False):
    """(list of leading timestamps in centiseconds, text) or None."""
    m = (_LEADING_TS_LENIENT if lenient else _LEADING_TS).match(line)
    if not m:
        return None
    pattern = _TS_LENIENT if lenient else _TS_PATTERN
    return [_match_to_cs(t) for t in pattern.finditer(m.group(1))], m.group(2).strip()


def _offset_ms(lines):
    for line in lines:
        m = _OFFSET_TAG.match(line.strip())
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return 0
    return 0


def lyric_timestamps(content):
    """Sorted start times (seconds) of the timed lines that carry lyric text.

    Compressed lines count once per timestamp; `[offset:±ms]` is applied
    (a positive offset shows the lyrics earlier). Empty timed lines (end
    markers) are ignored.
    """
    if not content:
        return []
    lines = content.lstrip('\ufeff').splitlines()
    offset = _offset_ms(lines)
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or _META_TAG.match(stripped):
            continue
        parsed = _parse_line_cs(stripped, lenient=True)
        if not parsed:
            continue
        stamps, text = parsed
        if not LRC_ENHANCED_TIMESTAMP.sub('', text).strip():
            continue
        out.extend((cs - offset / 10.0) / 100.0 for cs in stamps)
    return sorted(out)


def is_fake_synced(content, duration_s=None):
    """Timing that carries no information.

    True when at least 3 timed lyric lines exist and either the most common
    timestamp is shared by at least 80% of them (e.g. every line at
    [00:00.00]), or all of them fit in half a second (the 10 ms steps an
    older repair produced from such a file), or — with a known duration over
    60 s — they span less than 10% of the track.
    """
    stamps = lyric_timestamps(content)
    n = len(stamps)
    if n < 3:
        return False
    counts = {}
    for s in stamps:
        key = round(s * 100)
        counts[key] = counts.get(key, 0) + 1
    top = max(counts.values())
    if top >= 3 and top >= 0.8 * n:
        return True
    span = stamps[-1] - stamps[0]
    if span < 0.5:
        return True
    try:
        d = float(duration_s) if duration_s else None
    except (TypeError, ValueError):
        d = None
    return bool(d and d > 60 and span < 0.1 * d)


def strip_to_plain(content):
    """Plain lyrics of an LRC text: timestamps, enhanced tags and meta tags
    removed, line order kept (compressed lines once)."""
    out = []
    for line in (content or '').lstrip('\ufeff').splitlines():
        stripped = line.strip()
        if _META_TAG.match(stripped):
            continue
        parsed = _parse_line_cs(stripped, lenient=True)
        text = parsed[1] if parsed else stripped
        out.append(LRC_ENHANCED_TIMESTAMP.sub('', text).rstrip())
    return '\n'.join(out).strip()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_lrc(content):
    """Validate LRC timestamp integrity.

    Returns a dict with:
        valid: bool — True if no issues found
        needs_repair: bool — True when repair_lrc would fix something
            (large gaps alone never need a rewrite)
        issues: list of dicts with line, type, detail
        stats: dict with total_lines, timestamped_lines, non_monotonic,
               duplicates, gaps, missing_timestamps, compressed, invalid_seconds
    """
    if not content:
        return {'valid': False, 'needs_repair': False, 'issues': [], 'stats': {}}

    lines = content.lstrip('\ufeff').strip().splitlines()
    issues = []
    timestamps = []  # (line_number, first_cs, text)

    total_lines = 0
    timestamped_lines = 0
    meta_lines = 0
    compressed = 0
    invalid_seconds = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _META_TAG.match(stripped):
            meta_lines += 1
            continue

        total_lines += 1
        m = _LEADING_TS.match(stripped)
        if m:
            stamps = list(_TS_PATTERN.finditer(m.group(1)))
            text_after = m.group(2).strip()
            timestamped_lines += 1
            if any(int(s.group(2)) >= 60 for s in stamps):
                invalid_seconds += 1
                issues.append({
                    'line': i + 1,
                    'type': 'invalid_seconds',
                    'detail': f'Seconds field is 60 or more: "{stripped[:50]}"',
                })
            if len(stamps) > 1:
                compressed += 1
                issues.append({
                    'line': i + 1,
                    'type': 'compressed_timestamps',
                    'detail': f'{len(stamps)} timestamps on one line',
                })
            # Sequence checks run on the line's first time; a compressed
            # line's later repeats are expected to jump ahead.
            timestamps.append((i + 1, _match_to_cs(stamps[0]), text_after))
        elif re.match(r'\[\d{1,3}:\d', stripped):
            issues.append({
                'line': i + 1,
                'type': 'malformed_timestamp',
                'detail': f'Malformed timestamp format: "{stripped[:50]}"',
            })
        elif timestamped_lines > 0:
            # Some lines have timestamps, this one doesn't — mixed content
            issues.append({
                'line': i + 1,
                'type': 'missing_timestamp',
                'detail': f'Line has no timestamp in synced content: "{stripped[:50]}"',
            })

    non_monotonic = 0
    duplicate_ts = 0
    large_gaps = 0
    for idx in range(1, len(timestamps)):
        _prev_line, prev_cs, prev_text = timestamps[idx - 1]
        curr_line, curr_cs, curr_text = timestamps[idx]
        if curr_cs < prev_cs:
            non_monotonic += 1
            issues.append({
                'line': curr_line,
                'type': 'non_monotonic',
                'detail': f'Timestamp {_cs_to_ts(curr_cs)} is before previous {_cs_to_ts(prev_cs)}',
            })
        if curr_cs == prev_cs and curr_text and prev_text:
            duplicate_ts += 1
            issues.append({
                'line': curr_line,
                'type': 'duplicate_timestamp',
                'detail': f'Same timestamp {_cs_to_ts(curr_cs)} as previous line',
            })
        gap = (curr_cs - prev_cs) / 100.0
        if gap > MAX_LINE_GAP and curr_cs > 0 and prev_cs > 0:
            large_gaps += 1
            issues.append({
                'line': curr_line,
                'type': 'large_gap',
                'detail': f'{gap:.1f}s gap between lines ({_cs_to_ts(prev_cs)} → {_cs_to_ts(curr_cs)})',
            })

    if timestamped_lines and is_fake_synced(content):
        issues.append({
            'line': 0,
            'type': 'fake_synced',
            'detail': 'Every lyric line sits on (almost) the same timestamp — the timing carries no information',
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
        'invalid_seconds': invalid_seconds,
    }
    return {
        'valid': len(issues) == 0,
        # Large gaps are normal (instrumental breaks) and repair can't fix
        # them — callers should only rewrite a file for the other issues.
        'needs_repair': any(i['type'] not in _REPORT_ONLY_ISSUES for i in issues),
        'issues': issues,
        'stats': stats,
    }


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def repair_lrc(content):
    """Fix common LRC issues; idempotent.

    1. Compressed lines are expanded to one line per timestamp.
    2. Untimed section headers ("[Chorus]") are dropped; other untimed lines
       get interpolated times: a run of n untimed lines between timed lines
       at p and q gets p + k(q-p)/(n+1) (k = 1..n), computed on the original
       times before any de-duplication.
    3. Stable sort on (time, original position).
    4. One monotonic pass makes every time strictly increasing in 10 ms
       steps (cs[i] = max(cs[i], cs[i-1] + 1)), which also separates runs of
       duplicates of any length.
    5. Fake-synced input (every lyric line on one timestamp) becomes plain
       lyrics instead — the timing is meaningless.

    Metadata/ID tags (known or not) are kept, at the top. Content without
    any timestamp is returned unchanged.
    """
    if not content:
        return content

    lines = content.lstrip('\ufeff').strip().splitlines()
    meta_lines = []
    items = []  # in file order: ('timed', [cs...], text) | ('plain', None, text)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _META_TAG.match(stripped):
            meta_lines.append(stripped)
            continue
        parsed = _parse_line_cs(stripped, lenient=True)
        if parsed:
            items.append(('timed', parsed[0], parsed[1]))
        elif _SECTION_HEADER.match(stripped):
            continue  # untimed "[Verse 1]" header: not a lyric line
        else:
            items.append(('plain', None, stripped))

    if not any(kind == 'timed' for kind, _s, _t in items):
        return content

    if is_fake_synced(content):
        return strip_to_plain(content)

    # Interpolate untimed runs between their timed neighbours (original times).
    entries = []  # (cs, file_index, sub_index, text)
    n_items = len(items)
    i = 0
    while i < n_items:
        kind, stamps, text = items[i]
        if kind == 'timed':
            for sub, cs in enumerate(stamps):
                entries.append((cs, i, sub, text))
            i += 1
            continue
        j = i
        while j < n_items and items[j][0] == 'plain':
            j += 1
        run = items[i:j]
        prev_cs = items[i - 1][1][0] if i > 0 else None
        next_cs = items[j][1][0] if j < n_items else None
        n = len(run)
        for k, (_kind, _stamps, run_text) in enumerate(run, start=1):
            if prev_cs is not None and next_cs is not None:
                hi = max(next_cs, prev_cs)
                cs = prev_cs + (k * (hi - prev_cs)) // (n + 1)
            elif prev_cs is not None:
                cs = prev_cs + k * _TRAILING_STEP_CS
            else:
                cs = (k * next_cs) // (n + 1)
            entries.append((cs, i + k - 1, 0, run_text))
        i = j

    entries.sort(key=lambda e: (e[0], e[1], e[2]))

    out_cs = []
    for idx, (cs, _fi, _sub, _text) in enumerate(entries):
        out_cs.append(cs if idx == 0 else max(cs, out_cs[-1] + 1))

    output_lines = list(meta_lines)
    if meta_lines:
        output_lines.append('')
    for cs, (_c, _fi, _sub, text) in zip(out_cs, entries):
        ts = _cs_to_ts(cs)
        output_lines.append(f'{ts} {text}' if text else ts)
    return '\n'.join(output_lines)
