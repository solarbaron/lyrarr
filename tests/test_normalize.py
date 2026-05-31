# coding=utf-8

"""Tests for track/artist normalization used in lyrics matching."""

from lyrarr.metadata.normalize import (
    clean_for_search,
    duration_ms_to_seconds,
    extract_featuring,
    get_primary_artist,
    normalize_artist,
    normalize_title,
    split_artists,
)


class TestNormalizeTitle:
    def test_strips_remaster_suffix(self):
        assert normalize_title("Song (Remastered 2011)") == "song"
        assert normalize_title("Song (2011 Remaster)") == "song"

    def test_strips_live_and_edition_noise(self):
        assert normalize_title("Song (Live at Wembley)") == "song"
        assert normalize_title("Song (Deluxe Edition)") == "song"
        assert normalize_title("Song [Explicit]") == "song"

    def test_strips_featuring(self):
        assert normalize_title("Track (feat. Someone)") == "track"
        assert normalize_title("Track feat. Someone") == "track"

    def test_unicode_folding(self):
        assert normalize_title("Beyoncé") == "beyonce"
        assert normalize_title("Mötörhead") == "motorhead"

    def test_ampersand_becomes_and(self):
        assert normalize_title("Salt & Pepper") == "salt and pepper"

    def test_empty(self):
        assert normalize_title("") == ""
        assert normalize_title(None) == ""


class TestNormalizeArtist:
    def test_basic(self):
        assert normalize_artist("Adele") == "adele"

    def test_unicode_and_ampersand(self):
        assert normalize_artist("Sigur Rós") == "sigur ros"
        assert normalize_artist("Simon & Garfunkel") == "simon and garfunkel"


class TestGetPrimaryArtist:
    def test_ampersand(self):
        assert get_primary_artist("Artist1 & Artist2") == "artist1"

    def test_comma(self):
        assert get_primary_artist("Artist1, Artist2, Artist3") == "artist1"

    def test_featuring(self):
        assert get_primary_artist("Artist1 feat. Artist2") == "artist1"

    def test_single(self):
        assert get_primary_artist("Solo") == "solo"


class TestExtractFeaturing:
    def test_end_of_title(self):
        title, feat = extract_featuring("Title (feat. X & Y)")
        assert title == "Title"
        assert feat == ["X", "Y"]

    def test_mid_title(self):
        title, feat = extract_featuring("Title feat. X")
        assert title == "Title"
        assert feat == ["X"]

    def test_none(self):
        title, feat = extract_featuring("Plain Title")
        assert title == "Plain Title"
        assert feat == []


class TestSplitArtists:
    def test_ampersand(self):
        assert split_artists("A & B") == ["A", "B"]

    def test_strips_featuring(self):
        assert split_artists("A & B feat. C") == ["A", "B"]


class TestCleanForSearch:
    def test_structure(self):
        meta = clean_for_search("Song (Remastered)", "Artist1 & Artist2")
        assert meta["title_clean"] == "song"
        assert meta["artist_primary"] == "artist1"
        assert isinstance(meta["title_variants"], list)
        assert meta["title_variants"]  # non-empty

    def test_variants_are_deduped(self):
        meta = clean_for_search("Hello", "Adele")
        assert len(meta["title_variants"]) == len(set(meta["title_variants"]))


class TestDurationMsToSeconds:
    def test_milliseconds(self):
        assert duration_ms_to_seconds(180000) == 180

    def test_already_seconds(self):
        # values <= 1000 are treated as already-seconds (edge case)
        assert duration_ms_to_seconds(200) == 200

    def test_none(self):
        assert duration_ms_to_seconds(None) is None

    def test_zero_and_negative(self):
        assert duration_ms_to_seconds(0) is None
        assert duration_ms_to_seconds(-5) is None

    def test_non_numeric(self):
        assert duration_ms_to_seconds("abc") is None
