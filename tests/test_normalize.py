
"""Tests for track/artist normalization used in lyrics matching."""

from lyrarr.metadata.normalize import (
    artist_key,
    clean_for_search,
    comparison_key,
    duration_seconds,
    explicit_primary_artist,
    extract_featuring,
    get_primary_artist,
    key_digits,
    normalize_artist,
    normalize_title,
    parse_artist_credit,
    query_form,
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
        meta = clean_for_search("Song (Remastered)", "Artist1 feat. Artist2")
        assert meta["title_clean"] == "song"
        assert meta["title_query"] == "Song"
        assert meta["title_key"] == "song"
        assert meta["artist_primary"] == "artist1"
        assert isinstance(meta["title_variants"], list)
        assert meta["title_variants"]  # non-empty

    def test_artist_primary_only_on_explicit_separators(self):
        assert clean_for_search("Song", "Simon & Garfunkel")["artist_primary"] == "simon and garfunkel"
        assert clean_for_search("Song", "Tyler, The Creator")["artist_primary"] == "tyler, the creator"
        assert clean_for_search("Song", "Artist x Other")["artist_primary"] == "artist"
        assert clean_for_search("Song", "Artist vs. Other")["artist_primary"] == "artist"
        assert clean_for_search("Song", "Artist ft. Other")["artist_primary"] == "artist"

    def test_strong_instrumental_bracket_never_dropped(self):
        meta = clean_for_search("Bite the Pain (1996 demo, instrumental)", "Band")
        assert "bite the pain" not in meta["title_variants"]
        assert all("instrumental" in v for v in meta["title_variants"])
        meta = clean_for_search("Lose Yourself (Instrumental)", "Band")
        assert "lose yourself" not in meta["title_variants"]
        assert meta["title_clean"] != "lose yourself"
        assert "Instrumental" in meta["title_query"]

    def test_weak_instrumental_bracket_may_be_dropped(self):
        meta = clean_for_search("Song (Instrumental Intro)", "Band")
        assert "song" in meta["title_variants"]

    def test_variants_are_deduped(self):
        meta = clean_for_search("Hello", "Adele")
        assert len(meta["title_variants"]) == len(set(meta["title_variants"]))


class TestDurationSeconds:
    def test_seconds_pass_through(self):
        assert duration_seconds(180) == 180

    def test_long_tracks_are_not_mistaken_for_ms(self):
        # A 20-minute track is 1200 s — the old ">1000 means ms" heuristic made it 1 s.
        assert duration_seconds(1200) == 1200
        assert duration_seconds(3600) == 3600

    def test_float_rounds(self):
        assert duration_seconds(245.6) == 246

    def test_none(self):
        assert duration_seconds(None) is None

    def test_zero_and_negative(self):
        assert duration_seconds(0) is None
        assert duration_seconds(-5) is None

    def test_non_numeric(self):
        assert duration_seconds("abc") is None


class TestUnicodeFold:
    def test_latin_only(self):
        assert normalize_title("Tiếng Việt") == "tieng viet"
        assert normalize_artist("Mötley Crüe") == "motley crue"

    def test_other_scripts_keep_marks(self):
        # Hangul stays syllables, kana keep voicing marks, Cyrillic й/ё survive.
        assert normalize_title("사랑해요") == "사랑해요"
        assert normalize_title("テーゼ") == "テーゼ"
        assert normalize_title("Ёлка") == "ёлка"
        assert normalize_title("Мой герой") == "мой герой"


class TestQueryForm:
    def test_scripts_unchanged_except_case(self):
        for title in ("残酷な天使のテーゼ", "Последний герой", "सपना", "사랑해요"):
            assert query_form(title) == title
            assert comparison_key(title) == title.casefold()

    def test_noise_and_feat_removed_case_kept(self):
        assert query_form("Help! (Remastered 2009)") == "Help!"
        assert query_form("Song  (feat. Guest)  [Explicit]") == "Song"
        assert query_form("Don't Stop") == "Don't Stop"

    def test_instrumental_bracket_kept(self):
        assert query_form("Song (Instrumental)") == "Song (Instrumental)"

    def test_nfc(self):
        decomposed = "Beyonce\u0301"
        assert query_form(decomposed) == "Beyonc\u00e9"


class TestComparisonKey:
    def test_latin_fold_and_letters(self):
        assert comparison_key("Beyoncé") == "beyonce"
        assert comparison_key("Æon") == "aeon"
        assert comparison_key("Weiß") == "weiss"
        assert comparison_key("Mø") == "mo"
        assert comparison_key("Łódź") == "lodz"

    def test_apostrophes_and_punctuation(self):
        assert comparison_key("Rock 'n' Roll") == comparison_key("Rock n Roll")
        assert comparison_key("Don’t Stop") == "dont stop"
        assert comparison_key("Hi-Fi") == comparison_key("Hi Fi")
        assert comparison_key("Mr. Brightside") == comparison_key("Mr Brightside")

    def test_dash_suffix_version_tags(self):
        assert comparison_key("Help! - Remastered 2009") == "help"
        assert comparison_key("Song - 2011 Remaster") == "song"
        assert comparison_key("Song - Live at Wembley") == "song"
        assert comparison_key("Song - Radio Edit") == "song"
        assert comparison_key("Song - Mono") == "song"
        assert comparison_key("Song - Single Version") == "song"

    def test_live_forever_not_stripped(self):
        assert comparison_key("Pt. 2 - Live Forever") == "part 2 live forever"
        assert comparison_key("Live Forever") == "live forever"

    def test_leading_artist_stripped(self):
        assert comparison_key("Adele - Hello", "Adele") == "hello"
        assert comparison_key("Adele - Hello") == "adele hello"

    def test_parts_and_roman_numerals(self):
        assert comparison_key("Shine On, Pts. 1-5") == comparison_key("Shine On (Parts I-V)")
        assert comparison_key("Song, Pt. 2") == "song part 2"
        assert comparison_key("Rocky II") == "rocky 2"
        assert comparison_key("I Want You") == "i want you"  # a lone "I" is a word
        assert key_digits(comparison_key("Untitled 03")) == {"3"}

    def test_noise_brackets(self):
        assert comparison_key("Song (Live 1986)") == "song"
        assert comparison_key("Song (2011 Remastered Version)") == "song"
        assert comparison_key("Song (feat. Guest)") == "song"


class TestArtistCredit:
    def test_split(self):
        assert parse_artist_credit("Calvin Harris, Rihanna")["parts"] == ["Calvin Harris", "Rihanna"]
        assert parse_artist_credit("The Weeknd; Daft Punk")["parts"] == ["The Weeknd", "Daft Punk"]
        assert parse_artist_credit("Rihanna feat. Eminem")["parts"] == ["Rihanna", "Eminem"]
        assert parse_artist_credit("Adele")["parts"] == []

    def test_aliases(self):
        c = parse_artist_credit("IU (아이유)")
        assert c["main"] == "IU" and c["aliases"] == ["아이유"]
        c = parse_artist_credit("BTS(防弹少年团)")
        assert c["main"] == "BTS" and c["aliases"] == ["防弹少年团"]
        c = parse_artist_credit("Rihanna (Ft. Eminem)")
        assert c["aliases"] == [] and "Eminem" in c["parts"]

    def test_explicit_primary(self):
        assert explicit_primary_artist("Simon & Garfunkel") == "simon and garfunkel"
        assert explicit_primary_artist("Artist feat. Other") == "artist"
        assert explicit_primary_artist("Charli XCX") == "charli xcx"
        assert explicit_primary_artist("Malcolm X") == "malcolm x"

    def test_artist_key(self):
        assert artist_key("The Beatles") == artist_key("Beatles")
        assert artist_key("Simon & Garfunkel") == artist_key("Simon and Garfunkel")
        assert artist_key("Sigur Rós") == "sigur ros"
        assert artist_key("The The") == "the"
