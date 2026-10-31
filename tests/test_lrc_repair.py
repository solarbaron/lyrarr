
"""Tests for LRC timestamp validation and repair."""

from lyrarr.metadata.lrc_repair import (
    _TS_PATTERN,
    _parse_ts_to_seconds,
    _seconds_to_ts,
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


def _stamps(text):
    return [m.group(0) for m in _TS_PATTERN.finditer(text)]


def _assert_fixed_point(content):
    once = repair_lrc(content)
    assert repair_lrc(once) == once
    assert validate_lrc(once)["needs_repair"] is False
    return once


class TestRepairRewrite:
    def test_runs_of_three_or_more_duplicates(self):
        content = "\n".join([
            "[00:05.00]one", "[00:05.00]two", "[00:05.00]three", "[00:05.00]four",
            "[00:09.00]five", "[00:12.00]six", "[00:15.00]seven", "[00:18.00]eight",
        ])
        out = _assert_fixed_point(content)
        assert _stamps(out)[:5] == ["[00:05.00]", "[00:05.01]", "[00:05.02]", "[00:05.03]", "[00:09.00]"]
        assert [line.split("] ", 1)[1] for line in out.splitlines()][:4] == ["one", "two", "three", "four"]

    def test_consecutive_plain_lines_interpolated_evenly(self):
        out = _assert_fixed_point("[00:02.00]a\nfirst gap line\nsecond gap line\nthird gap line\n[00:06.00]e")
        assert _stamps(out) == ["[00:02.00]", "[00:03.00]", "[00:04.00]", "[00:05.00]", "[00:06.00]"]

    def test_interpolation_uses_original_times_before_dedupe(self):
        out = _assert_fixed_point("[00:04.00]a\nmiddle\n[00:04.00]b\n[00:08.00]c")
        assert [line.split("] ", 1)[1] for line in out.splitlines()] == ["a", "middle", "b", "c"]

    def test_untimed_section_headers_dropped(self):
        out = _assert_fixed_point("[Verse 1]\n[00:01.00]a\n[Chorus]\n[00:03.00]b\n[00:05.00][Bridge]")
        assert "[Verse 1]" not in out and "[Chorus]" not in out
        assert "[00:05.00] [Bridge]" in out  # a timed header keeps its time

    def test_all_zero_input_becomes_plain(self):
        content = "\n".join(f"[00:00.00]line {i} of placeholder text" for i in range(6))
        out = _assert_fixed_point(content)
        assert not _stamps(out)
        assert out.splitlines()[0] == "line 0 of placeholder text"

    def test_invalid_seconds_normalised(self):
        content = "[00:58.00]a\n[00:60.00]b\n[01:05.00]c"
        v = validate_lrc(content)
        assert v["needs_repair"] is True and any(i["type"] == "invalid_seconds" for i in v["issues"])
        out = _assert_fixed_point(content)
        assert _stamps(out) == ["[00:58.00]", "[01:00.00]", "[01:05.00]"]

    def test_stable_sort_keeps_file_order_on_ties(self):
        out = _assert_fixed_point("[00:10.00]late\n[00:02.00]early one\n[00:02.00]early two")
        assert [line.split("] ", 1)[1] for line in out.splitlines()] == ["early one", "early two", "late"]

    def test_compressed_and_meta_idempotent(self):
        content = "[ar:A]\n[00:10.00]Verse\n[00:20.00][01:10.00]Chorus\n[00:50.00]Verse two\n"
        _assert_fixed_point(content)

    def test_bom_and_lenient_seconds(self):
        out = _assert_fixed_point("\ufeff[0:5.20]a\n[00:07.00]b")
        assert _stamps(out) == ["[00:05.20]", "[00:07.00]"]

    def test_large_gap_only_is_left_alone(self):
        v = validate_lrc("[00:10.00]Hello\n[00:50.00]World")
        assert v["needs_repair"] is False


class TestFakeSynced:
    def test_detection(self):
        from lyrarr.metadata.lrc_repair import is_fake_synced
        assert is_fake_synced("[00:00.00]a\n[00:00.00]b\n[00:00.00]c")
        assert is_fake_synced("[00:00.00]a\n[00:00.01]b\n[00:00.02]c\n[00:00.03]d")  # old repair output
        assert not is_fake_synced("[00:05.00]a\n[00:05.00]b\n[00:05.00]c\n[00:10.00]d\n[00:12.00]e")
        assert not is_fake_synced("[00:00.00]a\n[00:00.00]b")
        assert is_fake_synced("[00:10.00]a\n[00:11.00]b\n[00:12.00]c", duration_s=300)
        assert not is_fake_synced("[00:10.00]a\n[01:11.00]b\n[02:12.00]c", duration_s=300)
