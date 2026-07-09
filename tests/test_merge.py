
"""Tests for cross-provider lyrics result merging and de-duplication."""

from lyrarr.metadata.merge import _content_hash, merge_provider_results, strip_lrc_timestamps

LYRICS_A = "first line\nsecond line\nthird line\nfourth line"
SYNCED_A = "[00:01.00]first line\n[00:05.00]second line\n[00:09.00]third line\n[00:12.00]fourth line"


class TestContentHash:
    def test_ignores_timestamps(self):
        assert _content_hash(LYRICS_A) == _content_hash(SYNCED_A)

    def test_ignores_case_and_whitespace(self):
        assert _content_hash("Hello World") == _content_hash("  hello world  ")

    def test_empty_is_none(self):
        assert _content_hash("") is None
        assert _content_hash("   ") is None


class TestStripLrcTimestamps:
    def test_strips_timestamps(self):
        assert strip_lrc_timestamps(SYNCED_A) == LYRICS_A

    def test_drops_metadata_tags(self):
        lrc = "[ar:Artist]\n[ti:Title]\n[00:01.00]hello\n[00:02.00]world"
        assert strip_lrc_timestamps(lrc) == "hello\nworld"

    def test_plain_text_passthrough(self):
        assert strip_lrc_timestamps(LYRICS_A) == LYRICS_A

    def test_empty_input(self):
        assert strip_lrc_timestamps("") == ""
        assert strip_lrc_timestamps(None) == ""

    def test_preserves_blank_lines_between_sections(self):
        lrc = "[00:01.00]verse one\n\n[00:10.00]verse two"
        assert strip_lrc_timestamps(lrc) == "verse one\n\nverse two"


class TestMergeProviderResults:
    def test_single_result_unchanged(self):
        results = [{"plain_lyrics": LYRICS_A, "provider": "lrclib", "score": 0.8}]
        merged = merge_provider_results(results)
        assert len(merged) == 1

    def test_dedup_identical_across_providers(self):
        results = [
            {"plain_lyrics": LYRICS_A, "provider": "lrclib", "score": 0.7, "_provider": "lrclib"},
            {"plain_lyrics": LYRICS_A, "provider": "genius", "score": 0.6, "_provider": "genius"},
        ]
        merged = merge_provider_results(results)
        # Identical content collapses to one entry.
        assert len(merged) == 1
        assert merged[0]["providers_agree"] == 2

    def test_agreement_boosts_score(self):
        results = [
            {"plain_lyrics": LYRICS_A, "provider": "lrclib", "score": 0.7, "_provider": "lrclib"},
            {"plain_lyrics": LYRICS_A, "provider": "genius", "score": 0.7, "_provider": "genius"},
        ]
        merged = merge_provider_results(results)
        # AGREEMENT_BOOST (0.05) per extra agreeing provider.
        assert merged[0]["score"] > 0.7

    def test_composite_from_synced_plus_plain(self):
        results = [
            {"synced_lyrics": SYNCED_A, "provider": "lrclib", "score": 0.8, "_provider": "lrclib"},
            {"plain_lyrics": LYRICS_A, "provider": "genius", "score": 0.7, "_provider": "genius"},
        ]
        merged = merge_provider_results(results)
        composites = [r for r in merged if r.get("is_composite")]
        assert composites
        comp = composites[0]
        assert comp["synced_lyrics"] == SYNCED_A
        assert comp["plain_lyrics"] == LYRICS_A

    def test_internal_keys_stripped(self):
        results = [
            {"plain_lyrics": LYRICS_A, "provider": "lrclib", "score": 0.7, "_provider": "lrclib"},
            {"plain_lyrics": "totally different content here\nmore lines\nand more", "provider": "genius", "score": 0.6, "_provider": "genius"},
        ]
        merged = merge_provider_results(results)
        for r in merged:
            assert "_synced_hash" not in r
            assert "_plain_hash" not in r
            assert "_content_hash" not in r
