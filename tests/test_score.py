# coding=utf-8

"""Tests for the fuzzy match scorer used to rank lyrics provider results."""

from lyrarr.metadata.base import LyricsProvider

score = LyricsProvider.score_result


class TestScoreResult:
    def test_exact_match_is_perfect(self):
        r = score("Hello", "Adele", 295000, "Hello", "Adele", 295)
        assert r["title_score"] == 100
        assert r["artist_score"] == 100
        assert r["duration_score"] == 100
        assert r["score"] == 1.0

    def test_title_mismatch_lowers_score(self):
        good = score("Hello", "Adele", 295000, "Hello", "Adele", 295)["score"]
        bad = score("Hello", "Adele", 295000, "Completely Different", "Adele", 295)["score"]
        assert bad < good

    def test_duration_penalty_is_ten_percent_per_second(self):
        r = score("Hello", "Adele", 300000, "Hello", "Adele", 295)
        # 5 second difference => 100 - 5*10 = 50
        assert r["duration_score"] == 50

    def test_duration_far_off_floors_at_zero(self):
        r = score("Hello", "Adele", 300000, "Hello", "Adele", 100)
        assert r["duration_score"] == 0

    def test_both_titles_empty_is_neutral_100(self):
        r = score("", "Adele", 295000, "", "Adele", 295)
        assert r["title_score"] == 100

    def test_one_sided_artist_is_thirty(self):
        r = score("Hello", "Adele", 295000, "Hello", "", 295)
        assert r["artist_score"] == 30

    def test_missing_durations_are_neutral_70(self):
        r = score("Hello", "Adele", None, "Hello", "Adele", None)
        assert r["duration_score"] == 70

    def test_composite_weighting(self):
        # title=100, artist=0 (total mismatch), duration=100
        # composite = (100*.45 + 0*.35 + 100*.20)/100 = 0.65
        r = score("Hello", "Adele", 295000, "Hello", "Zxqw Vbnm", 295)
        # artist is a hard mismatch but SequenceMatcher rarely yields exactly 0;
        # assert it is meaningfully below a full-credit composite.
        assert r["score"] < 0.8
        assert r["title_score"] == 100
        assert r["duration_score"] == 100

    def test_primary_artist_match_for_compound(self):
        # query has compound artist, result has only the primary
        r = score("Song", "Daft Punk & Pharrell", 200000, "Song", "Daft Punk", 200)
        assert r["artist_score"] >= 80
