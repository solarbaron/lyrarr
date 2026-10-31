"""Regression tests for lyrics matching / parsing in lyrarr.metadata (no DB, no network).

Covers: seconds-based durations end to end, hard score gates (wrong artist,
missing artist, duration), feat/with normalization and curly quotes, Genius
section headers vs instrumental detection, LRC repair of compressed lines /
unknown tags / [mm:ss], provider attribution and synced bias in merge, and
candidate ranking (floor + gates before prefer_synced).
"""

import sys
import tempfile

if 'lyrarr.app.get_args' not in sys.modules:
    # Provider modules import app config — point it at a throwaway dir, never data/.
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-metadata-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

import pytest  # noqa: E402

from lyrarr.metadata.base import LyricsProvider  # noqa: E402
from lyrarr.metadata.language_detect import is_synced_lyrics  # noqa: E402
from lyrarr.metadata.lrc_repair import repair_lrc, validate_lrc  # noqa: E402
from lyrarr.metadata.merge import merge_provider_results  # noqa: E402
from lyrarr.metadata.normalize import (  # noqa: E402
    extract_featuring,
    get_primary_artist,
    normalize_artist,
    normalize_title,
)
from lyrarr.metadata.validation import validate_lyrics  # noqa: E402

score = LyricsProvider.score_result

GENIUS_STYLE = """[Intro]
Yeah, yeah

[Verse 1]
I've been walking down this lonely road tonight
Searching for a reason to believe in the light
[Chorus]
Hold on, hold on to me
[Outro]
Hold on"""


class TestDurationUnits:
    def test_long_track_scores_on_seconds(self):
        # 20-minute track: was "1200 > 1000 so it's ms" -> 1 s, failing every match.
        r = score("Epic", "Band", 1200, "Epic", "Band", 1201)
        assert r["duration_diff"] == 1
        assert r["gate"] is None
        assert r["score"] > 0.9

    def test_truncation_check_uses_seconds(self):
        r = validate_lyrics("one line only here\nand another one", duration_s=240)
        assert r["is_truncated"] is True
        r = validate_lyrics("one line only here\nand another one", duration_s=90)
        assert r["is_truncated"] is False


class TestScoreGates:
    def test_same_title_different_artist_rejected(self):
        r = score("Hello", "Lionel Richie", 250, "Hello", "Adele", 295)
        assert r["gate"] in ("artist_mismatch", "duration_mismatch")
        assert r["score"] < 0.5

    def test_result_without_artist_rejected(self):
        r = score("Intro", "Metallica", 90, "Intro", "", 91)
        assert r["gate"] == "artist_missing"
        assert r["score"] < 0.5

    def test_duration_gate(self):
        r = score("Hello", "Adele", 295, "Hello", "Adele", 330)
        assert r["gate"] == "duration_mismatch"
        assert r["score"] < 0.5
        assert r["raw_score"] > 0.7

    def test_small_duration_difference_ok(self):
        r = score("Hello", "Adele", 295, "Hello", "Adele", 298)
        assert r["gate"] is None

    def test_no_query_artist_no_artist_gate(self):
        # Interactive custom-query search passes no artist — can't gate on it.
        r = score("Hello", None, None, "Hello", "Adele", None)
        assert r["gate"] is None

    def test_spelling_variant_passes(self):
        r = score("Halo", "Beyoncé", 261, "Halo", "Beyonce", 261)
        assert r["gate"] is None and r["score"] == 1.0


class TestNormalization:
    @pytest.mark.parametrize("title", [
        "Left Outside Alone", "Lift Me Up", "Stay With Me", "Dancing With Myself",
        "Soft Rain", "Drift Away", "Gift of Love",
    ])
    def test_words_kept(self, title):
        assert normalize_title(title) == title.lower()

    def test_feat_still_stripped(self):
        assert normalize_title("Song (feat. X)") == "song"
        assert normalize_title("Song [ft. X & Y]") == "song"
        assert normalize_title("Song feat. X") == "song"
        assert normalize_title("Song (with Y)") == "song"
        assert extract_featuring("Song (feat. A & B)") == ("Song", ["A", "B"])
        assert extract_featuring("Song featuring A") == ("Song", ["A"])

    def test_artist_not_cut_at_ft(self):
        assert get_primary_artist("Soft Cell") == "soft cell"
        assert get_primary_artist("Left Boy") == "left boy"
        assert get_primary_artist("Artist feat. Other") == "artist"

    @pytest.mark.parametrize("raw", ["Don’t Stop Me Now", "Don‘t Stop Me Now", "Don´t Stop Me Now",
                                     "Don`t Stop Me Now"])
    def test_curly_quotes_fold(self, raw):
        assert normalize_title(raw) == "don't stop me now"
        assert normalize_artist("Guns N’ Roses") == "guns n' roses"


class TestInstrumentalDetection:
    def test_genius_headers_are_not_instrumental(self):
        r = validate_lyrics(GENIUS_STYLE, track_title="Hold On", duration_s=200)
        assert r["is_instrumental"] is False
        assert r["valid"] is True

    def test_marker_only_content_is_instrumental(self):
        assert validate_lyrics("[Instrumental]")["is_instrumental"] is True
        assert validate_lyrics("[Intro]\n[Instrumental]\n[Outro]")["is_instrumental"] is True
        assert validate_lyrics("[00:00.00]Instrumental")["is_instrumental"] is True

    def test_marker_line_inside_synced_lyrics(self):
        r = validate_lyrics("[00:01.00]Hello\n[00:05.00]Intro\n[00:10.00]world is big today")
        assert r["is_instrumental"] is False

    def test_tiny_content_is_too_short_not_instrumental(self):
        r = validate_lyrics("Lyrics not available")
        assert r["is_instrumental"] is False
        assert r["valid"] is False and any(i["type"] == "placeholder" for i in r["issues"])
        r = validate_lyrics("[00:01.00]La la la la la\n[00:05.00]Na na na")
        assert r["is_instrumental"] is False

    def test_metadata_only_reports_own_error(self):
        r = validate_lyrics("[ar:Someone]\n[ti:Song]\n[length:03:20]")
        assert r["is_instrumental"] is False
        types = {i["type"] for i in r["issues"]}
        assert "metadata_only" in types
        assert r["valid"] is False

    def test_commercial_footer_is_error(self):
        text = "line one of the song\nline two of the song\n...\n\n******* This Lyrics is NOT for Commercial use *******"
        r = validate_lyrics(text)
        assert r["valid"] is False and r["is_truncated"] is True


class TestLrcRepair:
    def test_compressed_lines_expanded_and_sorted(self):
        v = "[ar:A]\n[00:10.00]Verse one\n[00:20.00][01:10.00][02:00.00]Chorus line\n[00:50.00]Verse two\n"
        result = validate_lrc(v)
        assert result["needs_repair"] is True
        out = repair_lrc(v)
        lines = [ln for ln in out.splitlines() if ln.startswith("[0")]
        assert lines == [
            "[00:10.00] Verse one",
            "[00:20.00] Chorus line",
            "[00:50.00] Verse two",
            "[01:10.00] Chorus line",
            "[02:00.00] Chorus line",
        ]
        assert out.startswith("[ar:A]")

    def test_unknown_tags_preserved(self):
        v = "[tool:LRCMaker]\n[#:comment]\n[re:x]\n[ve:1]\n[00:10.00]Hello\n[00:12.00]World\n"
        assert validate_lrc(v)["valid"] is True
        out = repair_lrc(v)
        assert "[tool:LRCMaker]" in out.splitlines()
        assert "[#:comment]" in out.splitlines()
        assert not any(ln.endswith("[tool:LRCMaker]") and ln.startswith("[0") for ln in out.splitlines())

    def test_no_fraction_timestamps(self):
        v = "[00:10]Hello there\n[00:12]Again\n"
        assert is_synced_lyrics(v) is True
        r = validate_lrc(v)
        assert r["valid"] is True and r["stats"]["timestamped_lines"] == 2
        assert repair_lrc(v).splitlines()[0] == "[00:10.00] Hello there"

    def test_large_gap_reported_but_no_rewrite_needed(self):
        r = validate_lrc("[00:10.00]Hello\n[00:50.00]World")
        assert any(i["type"] == "large_gap" for i in r["issues"])
        assert r["needs_repair"] is False


class TestMerge:
    def test_provider_attribution_preserved(self):
        rs = [
            {"synced_lyrics": "[00:01.00]a b c d e f\n[00:02.00]g h i", "_provider": "lrclib", "score": 0.9},
            {"plain_lyrics": "something else entirely here", "_provider": "genius", "score": 0.7},
        ]
        out = merge_provider_results(rs)
        assert sorted(r["_provider"] for r in out) == ["genius", "lrclib"]

    def test_synced_copy_not_lost_to_first_seen_plain(self):
        rs = [
            {"plain_lyrics": "x y z\nfoo", "_provider": "lrclib", "score": 0.60},
            {"synced_lyrics": "[00:01.00]x y z\n[00:02.00]foo", "_provider": "netease", "score": 0.62},
        ]
        out = merge_provider_results(rs)
        deduped = [r for r in out if not r.get("is_composite")]
        assert len(deduped) == 1
        assert deduped[0]["_provider"] == "netease"
        assert deduped[0]["synced_lyrics"]


def _res(provider, score, synced=False, gate=None, mb=False, text="words of the song here"):
    body = f"[00:01.00]{text}\n[00:05.00]more {text}" if synced else f"{text}\nmore {text}"
    return {
        "synced_lyrics": body if synced else None,
        "plain_lyrics": None if synced else body,
        "_provider": provider,
        "score": score,
        "match_details": {"gate": gate, "mb_matched": mb, "title_score": 100},
    }


class TestRankCandidates:
    @pytest.fixture(autouse=True)
    def _worker(self):
        from lyrarr.metadata import download_worker
        self.w = download_worker

    def test_weak_synced_does_not_beat_strong_plain(self):
        ranked = self.w.rank_candidates([_res("a", 0.35, synced=True), _res("b", 0.95)], "prefer_synced", 0.5)
        assert [r["_provider"] for r in ranked] == ["b"]

    def test_synced_preferred_within_margin(self):
        ranked = self.w.rank_candidates([_res("a", 0.85, synced=True), _res("b", 0.95)], "prefer_synced", 0.5)
        assert [r["_provider"] for r in ranked] == ["a", "b"]

    def test_best_score_mode(self):
        ranked = self.w.rank_candidates([_res("a", 0.7, synced=True), _res("b", 0.9)], "best_score", 0.5)
        assert ranked[0]["_provider"] == "b"

    def test_gated_result_rejected(self):
        assert self.w.rank_candidates([_res("a", 0.9, gate="artist_mismatch")], "best_score", 0.5) == []

    def test_mb_matched_bypasses_floor_but_not_duration_gate(self):
        assert self.w.rank_candidates([_res("a", 0.4, mb=True)], "best_score", 0.5)
        assert not self.w.rank_candidates([_res("a", 0.3, mb=True, gate="duration_mismatch")], "best_score", 0.5)

    def test_truncated_musixmatch_plain_never_chosen(self):
        cand = _res("musixmatch", 0.95)
        cand["plain_truncated"] = True
        good = _res("lrclib", 0.8, text="the complete text of the song")
        result, content, outcome = self.w.choose_lyrics([cand, good], "best_score", track_title="T")
        assert outcome == "ok" and result is good

    def test_instrumental_content_is_never_a_verdict(self):
        inst = {"plain_lyrics": "[Instrumental]", "_provider": "genius", "score": 0.9}
        info = {}
        _, _, outcome = self.w.choose_lyrics([inst], "best_score", info=info)
        assert outcome == "none" and info["instrumental_seen"] is True
        inst2 = {"plain_lyrics": "Instrumental", "_provider": "netease", "score": 0.9}
        _, _, outcome = self.w.choose_lyrics([inst, inst2], "best_score")
        assert outcome == "none"  # votes are counted by the pipeline, not here

    def test_after_instrumental_content_vocal_needs_confident_title(self):
        inst = {"plain_lyrics": "[Instrumental]", "_provider": "lrclib", "score": 1.0}
        weak = _res("genius", 0.7)
        weak["match_details"]["title_score"] = 70
        assert self.w.choose_lyrics([inst, weak], "best_score")[2] == "none"
        weak["match_details"]["title_score"] = 95
        assert self.w.choose_lyrics([inst, weak], "best_score")[2] == "ok"

    def test_clean_candidate_beats_truncated_looking_one(self):
        short = {"plain_lyrics": "First line here\nSecond line here also", "_provider": "a", "score": 0.95}
        full = {"plain_lyrics": "\n".join(f"line number {i} of the song" for i in range(10)),
                "_provider": "b", "score": 0.8}
        result, _, _ = self.w.choose_lyrics([short, full], "best_score", duration_s=240)
        assert result is full
        result, _, outcome = self.w.choose_lyrics([short], "best_score", duration_s=240)
        assert outcome == "truncated" and result is short  # last resort (never an upgrade)

    def test_prefer_plain_counts_dual_results(self):
        dual = _res("lrclib", 0.9, synced=True)
        dual["plain_lyrics"] = "words of the song here\nmore words of the song here"
        plain = _res("genius", 0.8)
        ranked = self.w.rank_candidates([plain, dual], "prefer_plain", 0.5)
        assert [r["_provider"] for r in ranked] == ["lrclib", "genius"]

    def test_best_score_with_prefer_synced(self):
        synced = _res("lrclib", 0.92, synced=True)
        plain = _res("genius", 0.94)
        ranked = self.w.rank_candidates([plain, synced], "best_score", 0.5, prefer_synced=True)
        assert ranked[0]["_provider"] == "lrclib"
        ranked = self.w.rank_candidates([plain, synced], "best_score", 0.5, prefer_synced=False)
        assert ranked[0]["_provider"] == "genius"

    def test_placeholder_lrc_with_credits_rejected(self):
        lrc = "[00:00.00]作词 : Someone\n[00:01.00]作曲 : Someone\n[00:02.00]纯音乐，请欣赏"
        cand = {"synced_lyrics": lrc, "_provider": "netease", "score": 0.9}
        assert self.w.choose_lyrics([cand], "best_score")[2] == "none"

    def test_credit_blocks_stripped_before_saving(self):
        body = "\n".join(f"[00:{10 + i:02d}.00]placeholder line {i} of the song" for i in range(6))
        lrc = "[00:00.00]作词 : Someone\n[00:01.00]作曲 : Someone\n" + body
        _, content, outcome = self.w.choose_lyrics([{"synced_lyrics": lrc, "_provider": "netease", "score": 0.9}],
                                                   "best_score")
        assert outcome == "ok" and "作词" not in content and content.strip() == body

    def test_overrunning_synced_falls_back_to_its_plain(self):
        synced = "\n".join(f"[0{i}:00.00]placeholder line {i}" for i in range(6))  # last at 5:00
        cand = {"synced_lyrics": synced, "plain_lyrics": "plain placeholder one\nplain placeholder two\n"
                "plain placeholder three\nplain placeholder four", "_provider": "lrclib", "score": 0.9}
        _, content, outcome = self.w.choose_lyrics([cand], "best_score", duration_s=200)
        assert outcome == "ok" and content.startswith("plain placeholder one")
        _, content, _ = self.w.choose_lyrics([cand], "best_score", duration_s=320)
        assert content == synced


def test_publish_rejects_ms_divided_duration():
    from lyrarr.metadata.lyrics.lrclib_publish import LrclibPublishError, publish_lyrics
    with pytest.raises(LrclibPublishError):
        publish_lyrics("T", "A", "Al", 0.215, plain_lyrics="x")

