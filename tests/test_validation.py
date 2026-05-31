# coding=utf-8

"""Tests for the lyrics quality validation pipeline."""

from lyrarr.metadata.validation import (
    _is_language_mismatch,
    is_instrumental_title,
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
        r = validate_lyrics(GOOD_LYRICS, duration_ms=200000)
        assert r["valid"] is True
        assert r["is_instrumental"] is False

    def test_truncation_for_long_track(self):
        # Two short lines for a 4-minute track => likely truncated.
        r = validate_lyrics("First line here\nSecond line here also", duration_ms=240000)
        assert r["is_truncated"] is True
        assert any(i["type"] == "truncated" for i in r["issues"])

    def test_excessive_duplicates_warning(self):
        dupe = "\n".join(["la la la"] * 8 + ["different line"])
        r = validate_lyrics(dupe)
        assert any(i["type"] == "excessive_duplicates" for i in r["issues"])

    def test_language_mismatch_flagged(self):
        r = validate_lyrics(
            GOOD_LYRICS, duration_ms=200000,
            detected_language="ja", artist_language="en",
        )
        assert r["language_mismatch"] is True


class TestInstrumentalTitle:
    def test_detects_instrumental_markers(self):
        for title in [
            "Song (Instrumental)",
            "Song [Instrumental]",
            "Song - Instrumental",
            "Song (Instrumental Version)",
            "Song (Karaoke Version)",
            "Theme (Off Vocal)",
            "Track (No Vocals)",
            "Foo (Backing Track)",
            "Outro Instrumental",
        ]:
            assert is_instrumental_title(title) is True, title

    def test_ignores_non_instrumental_titles(self):
        for title in [
            "Love Song",
            "Instrumentality",          # substring, not a real marker
            "Fundamental",              # contains "mental", not "instrumental"
            "The Instrumental Band Jam",  # mid-title word, not a marker
            "",
            None,
        ]:
            assert is_instrumental_title(title) is False, title

    def test_validate_lyrics_marks_instrumental_by_title(self):
        # Even with real-looking lyrics content, an instrumental title wins so a
        # wrong vocal-version match isn't saved.
        r = validate_lyrics(GOOD_LYRICS, track_title="Song (Instrumental)", duration_ms=200000)
        assert r["is_instrumental"] is True
        assert r["valid"] is True


class TestLanguageMismatch:
    def test_different_families_mismatch(self):
        assert _is_language_mismatch("ja", "en") is True
        assert _is_language_mismatch("ru", "es") is True

    def test_same_family_not_mismatch(self):
        assert _is_language_mismatch("en", "es") is False
        assert _is_language_mismatch("zh-cn", "ja") is False  # both cjk

    def test_unknown_language_not_flagged(self):
        assert _is_language_mismatch("xx", "en") is False
