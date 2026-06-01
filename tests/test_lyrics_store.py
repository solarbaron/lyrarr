"""Tests for shared lyrics content hashing and blacklist matching.

Only the dependency-free helpers are tested here (content_hash and
result_is_blacklisted); persist_lyrics / blacklist_content touch the database and
are exercised via the API in integration use.
"""

from lyrarr.metadata.lyrics_store import (
    content_hash,
    pick_best_synced,
    result_is_blacklisted,
)

PLAIN = "first line\nsecond line\nthird line"
SYNCED = "[00:01.00]first line\n[00:05.00]second line\n[00:09.00]third line"


class TestContentHash:
    def test_synced_and_plain_hash_equal(self):
        # The same lyrics in synced and plain form must hash identically so a
        # blacklist entry catches both representations.
        assert content_hash(PLAIN) == content_hash(SYNCED)

    def test_case_and_edge_whitespace_insensitive(self):
        # Lowercased and per-line leading/trailing whitespace stripped (internal
        # spacing is intentionally preserved).
        assert content_hash("Hello World") == content_hash("  hello world  ")

    def test_empty_is_none(self):
        assert content_hash("") is None
        assert content_hash(None) is None
        assert content_hash("   ") is None


class TestResultIsBlacklisted:
    def test_matches_plain(self):
        bl = {content_hash(PLAIN)}
        assert result_is_blacklisted({"plain_lyrics": PLAIN}, bl) is True

    def test_matches_synced_against_plain_hash(self):
        # Blacklisted from a plain copy; a synced result of the same song matches.
        bl = {content_hash(PLAIN)}
        assert result_is_blacklisted({"synced_lyrics": SYNCED}, bl) is True

    def test_non_match_passes(self):
        bl = {content_hash(PLAIN)}
        other = {"plain_lyrics": "completely different words here\nand more\nlines"}
        assert result_is_blacklisted(other, bl) is False

    def test_empty_blacklist_never_matches(self):
        assert result_is_blacklisted({"plain_lyrics": PLAIN}, set()) is False

    def test_result_without_content(self):
        bl = {content_hash(PLAIN)}
        assert result_is_blacklisted({}, bl) is False


class TestPickBestSynced:
    def test_picks_highest_scoring_synced(self):
        results = [
            {"synced_lyrics": SYNCED, "score": 0.6},
            {"synced_lyrics": SYNCED, "score": 0.9},
            {"synced_lyrics": SYNCED, "score": 0.7},
        ]
        assert pick_best_synced(results)["score"] == 0.9

    def test_ignores_plain_only_results(self):
        results = [
            {"plain_lyrics": PLAIN, "score": 0.95},
            {"synced_lyrics": SYNCED, "score": 0.5},
        ]
        best = pick_best_synced(results)
        assert best is not None
        assert best.get("synced_lyrics") == SYNCED

    def test_none_when_no_synced(self):
        results = [{"plain_lyrics": PLAIN, "score": 0.9}]
        assert pick_best_synced(results) is None

    def test_respects_min_score(self):
        results = [{"synced_lyrics": SYNCED, "score": 0.4}]
        assert pick_best_synced(results, min_score=0.5) is None
        assert pick_best_synced(results, min_score=0.3) is not None

    def test_empty(self):
        assert pick_best_synced([]) is None
