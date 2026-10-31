"""Red-team tests for the instrumental policy (PLAN §2).

Each test here encodes a hole found by an independent audit: a path through
which a real VOCAL track ends up 'instrumental' (or a true instrumental gets
another song's lyrics). They are expected to FAIL until the hole is fixed.

All lyrics text is synthetic. Providers are fakes; nothing hits the network.
"""

import os
import sys
import tempfile

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-redteam-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.metadata.lyrics_text import (  # noqa: E402
    instrumental_title_tier,
    needs_album_context,
)
from tests import test_metadata_worker as _worker_tests  # noqa: E402
from tests.test_metadata_worker import (  # noqa: E402
    ENGLISH,
    FakeProvider,
    _set_track,
    _track,
    hint,
    marker,
    mk,
)

env = _worker_tests.env  # the shared worker fixture (throwaway DB, fake providers)


def run(**kw):
    return w.download_missing_lyrics(**kw)


# ---------------------------------------------------------------------------
# 1. Title tiers: a dash prefix turns a vocal title into a STRONG one
# ---------------------------------------------------------------------------
#
# A vocal song titled "Karaoke" (several real vocal songs carry that title) is
# tier None, and "Instrumental" alone is only WEAK. In the Lidarr duplicate-row
# form seen on the real snapshot ("Artist - Album - 02 - Title", 251 such rows)
# the dash rule reads the bare title as a version tag on the "song" before it
# and makes it STRONG, so the worker flags the vocal track instrumental by
# title (terminal, no search). After a bare track-number segment the last
# segment IS the title: its tier must not exceed the bare title's tier.
# (The plan already special-cases this format for "… - 05 - The Instrumental".)

@pytest.mark.parametrize('bare', ['Karaoke', 'Instrumental', 'No Vocals', 'Backing Track', 'Minus One'])
def test_numbered_duplicate_form_never_raises_the_tier(bare):
    rank = {None: 0, 'weak': 1, 'strong': 2}
    title = 'Some Artist - Some Album - 02 - ' + bare
    assert rank[instrumental_title_tier(title)] <= rank[instrumental_title_tier(bare)], \
        f"{title!r} is {instrumental_title_tier(title)!r}, bare {bare!r} is {instrumental_title_tier(bare)!r}"


@pytest.mark.parametrize('title', ['Some Artist - Some Album - 02 - Karaoke'])
def test_vocal_song_titled_karaoke_in_dash_form_gets_lyrics(env, title):
    _set_track(title=title)
    env['providers']['p1'] = p1 = FakeProvider('p1', [mk('p1', plain=ENGLISH, title=title)])
    run()
    t = _track()
    assert t.lyrics_status != 'instrumental', f"vocal track {title!r} flagged ({t.instrumental_reason})"
    assert p1.calls == 1


# A featured/producer credit in brackets is not a version tag.
@pytest.mark.parametrize('title', [
    'Song (feat. Instrumental)',
    'Song (prod. by Instrumental)',
    'Song (prod. Backing Track)',
])
def test_credit_brackets_are_not_strong(title):
    assert instrumental_title_tier(title) != 'strong'


# ---------------------------------------------------------------------------
# 2. Consensus on an unclean pass: a provider that CRASHED is treated as clean
# ---------------------------------------------------------------------------
#
# §2.1: consensus requires a clean search pass — "a provider that failed or
# was skipped might have had the lyrics". collect_lyrics_results only flags
# ProviderTransientError as transient; any other exception (auth error,
# library API change, parse bug — genius.py lets these propagate on purpose)
# leaves stats.transient False and in_cooldown 0, so the pass counts as clean.

def test_crashed_provider_blocks_consensus(env):
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2', duration_diff=1.0)])
    env['providers']['p3'] = FakeProvider('p3', exc=RuntimeError('page layout changed'))
    run()
    t = _track()
    assert t.lyrics_status == 'missing', \
        f"classified {t.instrumental_reason!r} although provider p3 failed during the pass"


# ---------------------------------------------------------------------------
# 3. E4 hints are anchors without any title-confidence floor
# ---------------------------------------------------------------------------
#
# E2 needs title_score ≥ 90 and E3 needs title_score ≥ 90, but an E4 hint
# (NetEase pureMusic / Musixmatch instrumental==1) votes — as an ANCHOR — with
# any ungated match (title_score ≥ 60, score ≥ 0.5). Both providers return
# their single best search hit, so when neither has the vocal song "Lights"
# both pick the same artist's instrumental "Lights Out" (title_score ~67).
# Their errors are correlated, and two anchored hints are a consensus.

def test_low_title_confidence_hints_are_not_a_consensus(env):
    _set_track(title='Lights')

    def weak_hint(provider):
        r = hint(provider, title='Lights Out', duration_diff=1.0)
        r['instrumental_hint']['title_score'] = 67
        r['instrumental_hint']['score'] = 0.8
        r['match_details']['title_score'] = 67
        r['track_name'] = 'Lights Out'
        return r

    env['providers']['p1'] = FakeProvider('p1', [weak_hint('p1')])
    env['providers']['p2'] = FakeProvider('p2', [weak_hint('p2')])
    run()
    t = _track()
    assert t.lyrics_status != 'instrumental', \
        'two hints about a different-titled song (title_score 67) flagged the vocal track'


# ---------------------------------------------------------------------------
# 4. Converse: a generic instrumental title with a remaster suffix loses the
#    album-context rule and can take another album's lyrics
# ---------------------------------------------------------------------------
#
# "Intro - Remastered 2011" (Spotify/Lidarr style) is not generic and carries
# no context tag, so §2.7 does not apply: any same-titled result from ANOTHER
# album within the 10 s duration gate is accepted.

@pytest.mark.parametrize('title', ['Intro - Remastered 2011', 'Interlude - Live', 'Skit - 2009 Remaster'])
def test_generic_title_with_dash_suffix_needs_album_context(title):
    assert needs_album_context(title)


def test_remastered_intro_does_not_take_another_albums_lyrics(env):
    title = 'Intro - Remastered 2011'
    _set_track(title=title, duration=95)
    other_album = mk('p1', plain=ENGLISH, title=title, album='A Completely Different Record',
                     duration=101, track_duration=95, score=0.9)
    env['providers']['p1'] = FakeProvider('p1', [other_album])
    run()
    t = _track()
    assert t.lyrics_status != 'available' or not os.path.exists(t.path[:-5] + '.lrc'), \
        "a generic intro took another album's lyrics (Δ 6 s, not album-verified)"


# §2.1 "Never instrumental evidence: … a candidate matched only through relaxed
# alias or transliteration". E3 drops artist_via_alias members, but _hint_vote
# never looks at the hint result's match_details, so an alias-matched NetEase /
# Musixmatch hint still votes as an anchor.

def test_alias_matched_hint_never_votes(env):
    alias_hint = hint('p2', duration_diff=1.0)
    alias_hint['match_details']['artist_via_alias'] = True
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [alias_hint])
    run()
    t = _track()
    assert t.lyrics_status != 'instrumental', 'an alias-matched hint completed a consensus'
