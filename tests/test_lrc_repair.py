# coding=utf-8

"""Tests for LRC timestamp validation and repair."""

from lyrarr.metadata.lrc_repair import (
    _parse_ts_to_seconds,
    _seconds_to_ts,
    _TS_PATTERN,
    repair_lrc,
    validate_lrc,
)


class TestTimestampConversion:
    def test_round_trip(self):
        ts = "[01:23.45]"
        m = _TS_PATTERN.search(ts)
        seconds = _parse_ts_to_seconds(m)
        assert seconds == 83.45
        assert _seconds_to_ts(seconds) == "[01:23.45]"

    def test_milliseconds_precision(self):
        m = _TS_PATTERN.search("[00:10.500]")
        assert _parse_ts_to_seconds(m) == 10.5

    def test_negative_clamped(self):
        assert _seconds_to_ts(-5) == "[00:00.00]"


class TestValidateLrc:
    def test_clean_lrc_is_valid(self):
        content = "[00:01.00]first\n[00:05.00]second\n[00:09.00]third"
        result = validate_lrc(content)
        assert result["valid"] is True
        assert result["stats"]["timestamped_lines"] == 3

    def test_non_monotonic_detected(self):
        content = "[00:10.00]b\n[00:05.00]a"
        result = validate_lrc(content)
        assert result["valid"] is False
        assert any(i["type"] == "non_monotonic" for i in result["issues"])

    def test_large_gap_detected(self):
        content = "[00:01.00]a\n[01:00.00]b"  # 59s gap > MAX_LINE_GAP
        result = validate_lrc(content)
        assert any(i["type"] == "large_gap" for i in result["issues"])

    def test_missing_timestamp_in_synced(self):
        content = "[00:01.00]a\nplain line with no timestamp"
        result = validate_lrc(content)
        assert any(i["type"] == "missing_timestamp" for i in result["issues"])

    def test_empty(self):
        assert validate_lrc("")["valid"] is False


class TestRepairLrc:
    def test_sorts_non_monotonic(self):
        content = "[00:10.00]second\n[00:05.00]first"
        repaired = repair_lrc(content)
        lines = [line for line in repaired.splitlines() if line.strip()]
        assert lines[0].endswith("first")
        assert lines[1].endswith("second")
        # And the repaired output should now validate clean.
        assert validate_lrc(repaired)["valid"] is True

    def test_disambiguates_duplicate_timestamps(self):
        content = "[00:05.00]a\n[00:05.00]b"
        repaired = repair_lrc(content)
        # second line nudged forward by ~10ms so timestamps differ
        m = list(_TS_PATTERN.finditer(repaired))
        assert len(m) == 2
        assert _parse_ts_to_seconds(m[0]) != _parse_ts_to_seconds(m[1])

    def test_interpolates_plain_line(self):
        content = "[00:02.00]a\nmiddle\n[00:06.00]c"
        repaired = repair_lrc(content)
        # every content line should now carry a timestamp
        non_empty = [line for line in repaired.splitlines() if line.strip()]
        assert all(_TS_PATTERN.search(line) for line in non_empty)

    def test_no_timestamps_returns_unchanged(self):
        content = "just\nplain\nlyrics"
        assert repair_lrc(content) == content
