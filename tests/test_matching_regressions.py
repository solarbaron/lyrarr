"""Matching regressions (PLAN §7.3): wrong-song pairs must be gated or score
below the worker floor; legitimate spelling/version variants and collab
credits must be accepted. Pure scoring — no network, no lyrics text.
"""

import pytest

from lyrarr.metadata.base import LyricsProvider
from lyrarr.metadata.normalize import clean_for_search, comparison_key, query_form

score = LyricsProvider.score_result

# The worker's acceptance floor once the title gate is in (download_worker, WS-C).
FLOOR = 0.65


def accepted(r):
    return r["gate"] is None and r["score"] >= FLOOR


def rejected(r):
    return r["gate"] is not None or r["score"] < FLOOR


# (query title, result title) by the same artist, same duration.
WRONG_PAIRS = [
    ("Lucky", "Creep"),
    ("Exit Music (For a Film)", "Creep"),
    ("Airbag", "Creep"),
    ("Eple", "Remind Me"),
    ("Hoppípolla", "Glósóli"),
    ("Untitled 03", "Money Trees"),
    ("Untitled 03", "Untitled 05"),
    ("1979", "1985"),
    ("Another Brick in the Wall, Pt. 2", "Another Brick in the Wall, Pt. 1"),
    ("Intro", "Outro"),
    ("Track 1", "Track 12"),
    ("Flim", "Film"),
    ("Song (Reprise)", "Song"),
    ("Song", "Song (Reprise)"),
    ("Skit #1", "Skit #2"),
    ("Song (Interlude)", "Song"),
    ("Yesterday", "Let It Be"),
]

LEGIT_PAIRS = [
    ("Help! - Remastered 2009", "Help!"),
    ("Help!", "Help! - Remastered 2009"),
    ("Shine On You Crazy Diamond, Pts. 1-5", "Shine On You Crazy Diamond (Parts I-V)"),
    ("Hi-Fi", "Hi Fi"),
    ("Æon", "Aeon"),
    ("Weiß", "Weiss"),
    ("Rock 'n' Roll", "Rock n Roll"),
    ("Don't Stop", "Dont Stop"),
    ("Mr. Brightside", "Mr Brightside"),
    ("Song - Live at Wembley", "Song"),
    ("Song (2011 Remaster)", "Song"),
    ("Song (feat. Guest)", "Song"),
    ("Hoppípolla", "Hoppipolla"),
    ("Pt. 2 - Live Forever", "Pt. 2 - Live Forever"),
    ("残酷な天使のテーゼ", "残酷な天使のテーゼ"),
    ("Последний герой", "Последний герой"),
    ("Song", "Band - Song"),
]


class TestTitlePairs:
    @pytest.mark.parametrize("q,r", WRONG_PAIRS)
    @pytest.mark.parametrize("duration", [200, None])
    def test_wrong_song_rejected(self, q, r, duration):
        assert rejected(score(q, "Band", 200, r, "Band", duration))

    @pytest.mark.parametrize("q,r", LEGIT_PAIRS)
    def test_legit_variant_accepted(self, q, r):
        res = score(q, "Band", 200, r, "Band", 200)
        assert accepted(res), res
        assert res["title_score"] >= 90

    def test_dash_suffix_title_score(self):
        assert score("Help! - Remastered 2009", "The Beatles", 139, "Help!", "The Beatles", 139)["title_score"] >= 90

    @pytest.mark.parametrize("q,r", LEGIT_PAIRS[:7])
    def test_legit_variant_accepted_without_result_duration(self, q, r):
        # Genius-style (no duration): needs title ≥ 80 — real variants clear it.
        assert accepted(score(q, "Band", 200, r, "Band", None))


# (query artist, result artist)
COLLABS = [
    ("Nile Rodgers", "Daft Punk, Pharrell Williams, Nile Rodgers"),
    ("Rihanna", "Calvin Harris, Rihanna"),
    ("SZA", "Kendrick Lamar, SZA"),
    ("Daft Punk", "The Weeknd & Daft Punk"),
    ("JAY-Z", "Beyoncé & JAY-Z"),
    ("Eminem", "Rihanna feat. Eminem"),
    ("Silk Sonic", "Bruno Mars, Anderson .Paak & Silk Sonic"),
    ("Daft Punk", "The Weeknd; Daft Punk"),
    ("The Weeknd", "The Weeknd; Daft Punk"),
    ("Eminem", "Rihanna (Ft. Eminem)"),
    ("IU", "IU (아이유)"),
    ("Beatles", "The Beatles"),
    ("Earth, Wind & Fire", "Earth Wind and Fire"),
    ("Sigur Rós", "Sigur Ros"),
    ("AC/DC", "ACDC"),
    ("Artist feat. Guest", "Artist"),
]

WRONG_ARTISTS = [
    ("Bush", "Kate Bush"),
    ("Simon & Garfunkel", "Simon"),
    ("Simon & Garfunkel", "Paul Simon"),
    ("Queen", "Queen Latifah"),
    ("Lionel Richie", "Adele"),
    ("Earth, Wind & Fire", "Earth"),
]


class TestArtists:
    @pytest.mark.parametrize("q,r", COLLABS)
    def test_collab_and_spelling_accepted(self, q, r):
        res = score("Song", q, 200, "Song", r, 200)
        assert accepted(res), res

    @pytest.mark.parametrize("q,r", WRONG_ARTISTS)
    def test_different_artist_gated(self, q, r):
        res = score("Song", q, 200, "Song", r, 200)
        assert res["gate"] == "artist_mismatch", res

    def test_alias_only_match_is_marked(self):
        res = score("Song", "아이유", 200, "Song", "IU (아이유)", 200)
        assert accepted(res)
        assert res["artist_via_alias"] is True
        res = score("Song", "IU", 200, "Song", "IU (아이유)", 200)
        assert accepted(res)
        assert res["artist_via_alias"] is False

    def test_alias_both_ways(self):
        res = score("Song", "Bangtan Boys (BTS)", 200, "Song", "BTS", 200)
        assert accepted(res) and res["artist_via_alias"] is True

    def test_collab_track_on_album_artist_album_via_alt(self):
        # Track credited to B on artist A's album: the worker passes
        # artist_name=track artist (or A) and alt_artist_names=(A,).
        res = score("Song", "Artist A", 200, "Song", "Singer B", 200, alt_artists=("Singer B",))
        assert accepted(res)
        res = score("Song", "Singer B", 200, "Song", "Singer B", 200, alt_artists=("Artist A",))
        assert accepted(res)


class TestQueryForms:
    @pytest.mark.parametrize("title", ["残酷な天使のテーゼ", "Последний герой", "सपना", "사랑해요"])
    def test_scripts_survive(self, title):
        assert query_form(title) == title
        meta = clean_for_search(title, "Artist")
        assert meta["title_query"] == title
        assert meta["title_key"] == title.casefold()

    def test_beyonce_key(self):
        assert comparison_key("Beyoncé") == "beyonce"
        assert query_form("Beyoncé") == "Beyoncé"

    def test_simon_and_garfunkel_not_split(self):
        assert clean_for_search("The Boxer", "Simon & Garfunkel")["artist_primary"] == "simon and garfunkel"
