
"""Tests for the fuzzy match scorer used to rank lyrics provider results."""

from lyrarr.metadata.base import LyricsProvider

score = LyricsProvider.score_result


class TestScoreResult:
    def test_exact_match_is_perfect(self):
        r = score("Hello", "Adele", 295, "Hello", "Adele", 295)
        assert r["title_score"] == 100
        assert r["artist_score"] == 100
        assert r["duration_score"] == 100
        assert r["score"] == 1.0

    def test_title_mismatch_lowers_score(self):
        good = score("Hello", "Adele", 295, "Hello", "Adele", 295)["score"]
        bad = score("Hello", "Adele", 295, "Completely Different", "Adele", 295)["score"]
        assert bad < good

    def test_duration_penalty_is_ten_percent_per_second(self):
        r = score("Hello", "Adele", 300, "Hello", "Adele", 295)
        # 5 second difference => 100 - 5*10 = 50
        assert r["duration_score"] == 50

    def test_duration_far_off_floors_at_zero(self):
        r = score("Hello", "Adele", 300, "Hello", "Adele", 100)
        assert r["duration_score"] == 0

    def test_both_titles_empty_is_neutral_100(self):
        r = score("", "Adele", 295, "", "Adele", 295)
        assert r["title_score"] == 100

    def test_one_sided_artist_is_thirty(self):
        r = score("Hello", "Adele", 295, "Hello", "", 295)
        assert r["artist_score"] == 30

    def test_missing_durations_score_60(self):
        # 60, not 70: an unknown duration must not outrank a near-exact one.
        r = score("Hello", "Adele", None, "Hello", "Adele", None)
        assert r["duration_score"] == 60
        assert r["duration_diff"] is None
        near = score("Hello", "Adele", 295, "Hello", "Adele", 298)
        assert near["score"] > r["score"]

    def test_composite_weighting(self):
        # title=100, artist=0 (total mismatch), duration=100
        # composite = (100*.45 + 0*.35 + 100*.20)/100 = 0.65
        r = score("Hello", "Adele", 295, "Hello", "Zxqw Vbnm", 295)
        # artist is a hard mismatch but SequenceMatcher rarely yields exactly 0;
        # assert it is meaningfully below a full-credit composite.
        assert r["score"] < 0.8
        assert r["title_score"] == 100
        assert r["duration_score"] == 100

    def test_primary_artist_match_for_explicit_feat(self):
        # query credits a guest explicitly, result has only the primary
        r = score("Song", "Daft Punk feat. Pharrell", 200, "Song", "Daft Punk", 200)
        assert r["artist_score"] >= 80
        assert r["gate"] is None

    def test_query_side_parts_are_secondary(self):
        # "&" joins a group name, not a guest: one member is not the group.
        r = score("Song", "Daft Punk & Pharrell", 200, "Song", "Daft Punk", 200)
        assert r["gate"] == "artist_mismatch"


MATCH_DETAIL_KEYS = ("title_score", "artist_score", "duration_diff", "gate", "artist_via_alias",
                     "karaoke", "mb_artist")


class TestMatchDetails:
    def test_contract_keys_always_present(self):
        for args in [("Song", "Band", 200, "Song", "Band", 200),
                     ("Song", None, None, "Other", "", None),
                     ("", "", None, "", "", None)]:
            r = score(*args)
            for key in MATCH_DETAIL_KEYS:
                assert key in r
        assert score("Song", "Band", None, "Song", "Band", 200)["duration_diff"] is None

    def test_alt_artists_take_the_max(self):
        r = score("Song", "Album Artist", 200, "Song", "Guest Singer", 200)
        assert r["gate"] == "artist_mismatch"
        r = score("Song", "Album Artist", 200, "Song", "Guest Singer", 200,
                  alt_artists=("Someone Else", "Guest Singer"))
        assert r["gate"] is None and r["artist_score"] == 100


class TestVersionGate:
    def test_vocal_query_instrumental_result_gated(self):
        r = score("Song", "Band", 200, "Song (Instrumental)", "Band", 200)
        assert r["gate"] == "version_mismatch"
        assert r["score"] <= 0.3

    def test_instrumental_query_vocal_result_gated(self):
        r = score("Song (Instrumental)", "Band", 200, "Song", "Band", 200)
        assert r["gate"] == "version_mismatch"

    def test_off_vocal_and_dash_forms(self):
        assert score("Song", "Band", 200, "Song (Off Vocal)", "Band", 200)["gate"] == "version_mismatch"
        assert score("Song - Instrumental", "Band", 200, "Song", "Band", 200)["gate"] == "version_mismatch"

    def test_karaoke_not_gated_but_flagged(self):
        r = score("Song", "Band", 200, "Song (Karaoke Version)", "Band", 200)
        assert r["gate"] is None
        assert r["karaoke"] is True
        assert score("Song", "Band", 200, "Song", "Band", 200)["karaoke"] is False

    def test_both_instrumental_not_gated(self):
        r = score("Instrumental", "Band", 200, "Instrumental", "Band", 200)
        assert r["gate"] is None
        r = score("Song (Instrumental)", "Band", 200, "Song (Instrumental)", "Band", 200)
        assert r["gate"] is None

    def test_weak_instrumental_title_is_not_a_version_mismatch(self):
        # WEAK tier ("Instrumental Intro") never triggers the version gate. The
        # intro tag is on one side only, so the titles are still different
        # works (content-tag cap) — but not a version mismatch.
        r = score("Song (Instrumental Intro)", "Band", 200, "Song", "Band", 200)
        assert r["gate"] != "version_mismatch"
        r = score("Song (Instrumental Intro)", "Band", 200, "Song (Instrumental Intro)", "Band", 200)
        assert r["gate"] is None

    def test_mb_match_never_lifts_version_gate(self):
        r = score("Song", "Band", 200, "Song (Instrumental)", "Other", 200, mb_matched=True)
        assert r["gate"] == "version_mismatch"


class TestTitleGate:
    def test_different_title_same_artist_gated(self):
        r = score("Yesterday", "Band", 125, "Let It Be", "Band", 125)
        assert r["gate"] == "title_mismatch"

    def test_no_result_duration_needs_80(self):
        # Flim/Film-like near-miss stays gated; a real match passes.
        assert score("Song Title", "Band", 200, "Song Title", "Band", None)["gate"] is None
        r = score("Hello There", "Band", 200, "Hello Where", "Band", None)
        assert r["title_score"] < 80 and r["gate"] == "title_mismatch"

    def test_exact_get_and_mb_exempt_from_title_gate(self):
        assert score("Yesterday", "Band", 125, "Let It Be", "Band", 125, exact_get=True)["gate"] is None
        assert score("Yesterday", "Band", 125, "Let It Be", "Band", 125, mb_matched=True)["gate"] is None

    def test_duration_gate_still_applies_to_mb(self):
        r = score("Song", "Band", 200, "Song", "Band", 260, mb_matched=True)
        assert r["gate"] == "duration_mismatch"

    def test_mb_lifts_artist_gate(self):
        r = score("Song", "Beatles Tribute", 200, "Song", "Completely Other", 200, mb_matched=True)
        assert r["gate"] is None
