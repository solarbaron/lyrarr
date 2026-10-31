
"""Tests for the lyrics quality validation pipeline."""

import pytest

from lyrarr.metadata.validation import (
    _is_language_mismatch,
    is_instrumental_title,
    synced_fits_duration,
    validate_lyrics,
)

# A realistic, varied block of lyrics that passes the basic quality gates.
GOOD_LYRICS = "\n".join([
    "I walked along the empty street",
    "The city lights were burning low",
    "And every step I took felt sweet",
    "As memories began to flow",
    "I thought about the years gone by",
    "And all the things I left behind",
])


class TestValidateLyrics:
    def test_empty_is_invalid(self):
        r = validate_lyrics("")
        assert r["valid"] is False
        assert any(i["type"] == "empty" for i in r["issues"])

    def test_instrumental_marker(self):
        r = validate_lyrics("[Instrumental]")
        assert r["is_instrumental"] is True
        assert r["valid"] is True

    def test_too_short_is_error(self):
        # 3 lines so it isn't caught by the short-content instrumental heuristic,
        # but the combined text is still under MIN_LYRICS_LENGTH.
        r = validate_lyrics("a\nb\nc")
        assert any(i["type"] == "too_short" for i in r["issues"])
        assert r["valid"] is False

    def test_good_lyrics_pass(self):
        r = validate_lyrics(GOOD_LYRICS, duration_s=200)
        assert r["valid"] is True
        assert r["is_instrumental"] is False

    def test_truncation_for_long_track(self):
        # Two short lines for a 4-minute track => likely truncated.
        r = validate_lyrics("First line here\nSecond line here also", duration_s=240)
        assert r["is_truncated"] is True
        assert any(i["type"] == "truncated" for i in r["issues"])

    def test_excessive_duplicates_warning(self):
        dupe = "\n".join(["la la la"] * 8 + ["different line"])
        r = validate_lyrics(dupe)
        assert any(i["type"] == "excessive_duplicates" for i in r["issues"])

    def test_language_mismatch_flagged(self):
        r = validate_lyrics(
            GOOD_LYRICS, duration_s=200,
            detected_language="ja", artist_language="en",
        )
        assert r["language_mismatch"] is True


class TestInstrumentalTitle:
    """is_instrumental_title is the STRONG tier (PLAN §1.2)."""

    @pytest.mark.parametrize('title', [
        "Song (Instrumental)",
        "Song [Instrumental]",
        "Song - Instrumental",
        "Song (Instrumental Version)",
        "Song (Karaoke Version)",
        "Theme (Off Vocal)",
        "Track (No Vocals)",
        "Foo (Backing Track)",
        "Song (Inst.)",
        "Bite the Pain (1996 demo, instrumental)",
    ])
    def test_strong(self, title):
        assert is_instrumental_title(title) is True, title

    @pytest.mark.parametrize('title', [
        "Love Song",
        "Instrumentality",          # substring, not a real marker
        "Fundamental",              # contains "mental", not "instrumental"
        "The Instrumental Band Jam",  # mid-title word, not a marker
        "The Instrumental",
        "Mostly Instrumental",
        "Outro Instrumental",
        "Instrumental",             # weak: a vote, never terminal
        "Song (Instrumental Intro)",
        "Song (Vocal & Instrumental Mix)",
        "",
        None,
    ])
    def test_not_strong(self, title):
        assert is_instrumental_title(title) is False, title

    def test_validate_lyrics_marks_instrumental_by_title(self):
        r = validate_lyrics(GOOD_LYRICS, track_title="Song (Instrumental)", duration_s=200)
        assert r["is_instrumental"] is True
        assert r["valid"] is True


class TestContentChecks:
    def test_intro_outro_lines_are_not_instrumental(self):
        assert validate_lyrics("Intro")["is_instrumental"] is False
        assert validate_lyrics("[Intro]\n" + GOOD_LYRICS)["is_instrumental"] is False

    def test_html_entities_are_garbage(self):
        r = validate_lyrics(GOOD_LYRICS + "\nwe&#x27;re here &amp; there")
        assert any(i["type"] == "garbage_chars" for i in r["issues"])

    def test_placeholder_with_credits(self):
        r = validate_lyrics("作词 : Someone\n作曲 : Someone\nLyrics not available")
        assert any(i["type"] == "placeholder" for i in r["issues"]) and r["valid"] is False

    def test_placeholder_line_inside_lyrics_is_fine(self):
        r = validate_lyrics(GOOD_LYRICS + "\nLyrics not available")
        assert not any(i["type"] == "placeholder" for i in r["issues"])

    def test_language_mismatch_needs_artist_language(self):
        r = validate_lyrics(GOOD_LYRICS, duration_s=200, detected_language="ja")
        assert r["language_mismatch"] is False


def _lrc(times, text='placeholder words here'):
    return "\n".join(f"[{int(t // 60):02d}:{t % 60:05.2f}]{text} {i}" for i, t in enumerate(times))


class TestSyncedFitsDuration:
    def test_fits(self):
        assert synced_fits_duration(_lrc([10, 60, 120, 199]), 200) == (True, None)

    def test_overrun_beyond_three_seconds(self):
        assert synced_fits_duration(_lrc([10, 60, 202]), 200) == (True, None)
        assert synced_fits_duration(_lrc([10, 60, 204]), 200) == (False, 'overrun')

    def test_starts_after_end(self):
        assert synced_fits_duration(_lrc([210, 220]), 200) == (False, 'starts_after_end')

    def test_offset_tag_is_honoured(self):
        content = "[offset:+5000]\n" + _lrc([10, 60, 206])
        assert synced_fits_duration(content, 200) == (True, None)

    def test_fake_synced(self):
        assert synced_fits_duration(_lrc([0, 0, 0, 0]), 200) == (False, 'fake_synced')
        assert synced_fits_duration(_lrc([10, 11, 12, 13]), 300) == (False, 'fake_synced')  # < 10% of the track

    def test_unknown_duration_or_plain(self):
        assert synced_fits_duration(_lrc([10, 500]), None) == (True, None)
        assert synced_fits_duration("just plain words", 200) == (True, None)

    def test_end_marker_lines_ignored(self):
        assert synced_fits_duration(_lrc([10, 60, 190]) + "\n[03:40.00]", 200) == (True, None)


class TestLanguageMismatch:
    def test_different_families_mismatch(self):
        assert _is_language_mismatch("ja", "en") is True
        assert _is_language_mismatch("ru", "es") is True

    def test_same_family_not_mismatch(self):
        assert _is_language_mismatch("en", "es") is False
        assert _is_language_mismatch("zh-cn", "ja") is False  # both cjk

    def test_unknown_language_not_flagged(self):
        assert _is_language_mismatch("xx", "en") is False
