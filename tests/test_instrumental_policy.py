"""Instrumental policy regression fixtures (PLAN §2, §7.2).

Priority 1: a vocal track must never be flagged instrumental. Priority 2: a
real instrumental should not get random lyrics. Every scenario runs the real
worker against a throwaway DB with fake providers. All lyrics are synthetic.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-instrumental-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from flask import Flask  # noqa: E402
from flask_restx import Api  # noqa: E402

from lyrarr.app.database import TableAlbums, TableTracks, database, select, update  # noqa: E402
from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.metadata import lyrics_store  # noqa: E402
from lyrarr.metadata.embed import EmbeddedLyrics  # noqa: E402
from lyrarr.metadata.provider_utils import ProviderTransientError  # noqa: E402
from tests import test_metadata_worker as _worker_tests  # noqa: E402
from tests.test_metadata_worker import (  # noqa: E402
    ENGLISH,
    MARKER,
    SYNCED,
    FakeProvider,
    _history,
    _read,
    _set_profile,
    _set_track,
    _track,
    _write,
    hint,
    marker,
    mk,
)

env = _worker_tests.env  # the shared worker fixture

VOCAL_12 = "\n".join(f"line {i} of a made up vocal placeholder song" for i in range(12))


def run(**kw):
    return w.download_missing_lyrics(**kw)


def evidence(t=None):
    t = t or _track()
    return t.to_dict().get('instrumental_evidence') or []


def sources(t=None):
    return {v.get('source') for v in evidence(t) if not v.get('veto')}


def vetoes(t=None):
    return {v.get('veto') for v in evidence(t) if v.get('veto')}


def lyrics_result(provider='p1', **kw):
    return mk(provider, plain=ENGLISH, **kw)


@pytest.fixture
def embedded(monkeypatch):
    """Set the embedded tag lyrics the worker will read."""
    holder = {'value': None}
    monkeypatch.setattr(w, 'read_embedded_lyrics', lambda path: holder['value'])

    def set_text(text, synced=False):
        holder['value'] = EmbeddedLyrics(text, synced, 'LYRICS') if text else None
    return set_text


# ---------------------------------------------------------------------------
# 1-2. Vocal tracks with lyrics, and single-provider content: never instrumental
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('title, duration', [
    ('The Instrumental', 206),
    ('Mostly Instrumental', 200),
    ('Song (Instrumental Intro)', 200),
    ('For Free? (interlude)', 130),
    ('Skit #3', 24),
])
def test_vocal_titles_get_their_lyrics(env, title, duration):
    _set_track(title=title, duration=duration)
    env['providers']['p1'] = FakeProvider('p1', [lyrics_result(
        title=title, duration=duration, track_duration=duration, strategy='exact_album')])
    assert run() == 1
    t = _track()
    assert t.lyrics_status == 'available' and t.instrumental_reason is None


@pytest.mark.parametrize('content', [
    "[Intro]\n" + ENGLISH,
    "[Outro]\n" + ENGLISH,
    "Intro\n" + ENGLISH,
    "Yo, pass the placeholder mic\nAight",
    "[00:10.00]Robot placeholder",
    ENGLISH.replace("\n", "\n[Instrumental]\n", 1),
])
def test_single_provider_content_is_never_instrumental(env, content):
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=content, duration=None)])
    run()
    t = _track()
    assert t.lyrics_status in ('available', 'missing')
    assert not evidence(t)


# ---------------------------------------------------------------------------
# 3. Worker scenarios (a)-(o)
# ---------------------------------------------------------------------------

def test_a_marker_plus_real_lyrics_saves_lyrics(env):
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [mk('p2', synced=SYNCED, score=0.95)])
    assert run() == 1
    assert _track().lyrics_status == 'available' and 'first line' in _read(env['lrc'])


def test_a2_marker_with_unconvincing_lyrics_defers(env):
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [mk('p2', plain=ENGLISH, score=0.7, title_score=80)])
    assert run() == 0
    t = _track()
    assert t.lyrics_status == 'missing' and not os.path.exists(env['lrc'])


def test_b_strong_marker_alone_is_a_hint(env):
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    run()
    t = _track()
    assert t.lyrics_status == 'missing'
    assert sources(t) == {'p1'} and evidence(t)[0]['anchor'] is True
    assert t.lyrics_retry_after > datetime.now() + timedelta(days=13, hours=23)
    assert t.instrumental_checked_at is not None


def test_c_weak_marker_is_no_vote(env):
    env['providers']['p1'] = FakeProvider('p1', [marker('p1', strategy='exact_noalbum', duration=None)])
    run()
    t = _track()
    assert t.lyrics_status == 'missing' and not evidence(t)
    assert t.lyrics_retry_after < datetime.now() + timedelta(days=2)


@pytest.mark.parametrize('gate', ['version_mismatch', None])
def test_d_instrumental_version_results_never_vote(env, gate):
    for p in ('p1', 'p2'):
        env['providers'][p] = FakeProvider(p, [mk(p, plain=MARKER, title='Song (Instrumental)', gate=gate,
                                                 score=0.3 if gate else 0.9)])
    run()
    t = _track()
    assert t.lyrics_status == 'missing' and not evidence(t)


def test_e_karaoke_result_is_a_candidate_but_never_votes(env):
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, title='Song (Karaoke Version)',
                                                   md={'karaoke': True})])
    env['providers']['p2'] = FakeProvider('p2', [mk('p2', plain=MARKER)])
    run()
    t = _track()
    assert t.lyrics_status == 'missing' and sources(t) == {'p2'}
    _set_track(lyrics_retry_after=None, instrumental_evidence=None)
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=ENGLISH, title='Song (Karaoke Version)',
                                                   md={'karaoke': True})])
    env['providers']['p2'] = FakeProvider('p2', [])
    assert run() == 1


def test_f_override_never_strong_title_searched_without_qualifier(env):
    _set_track(title='Song (Instrumental)', instrumental_override='never')
    env['providers']['p1'] = p1 = FakeProvider('p1', [lyrics_result()])
    assert run() == 1
    assert p1.kwargs[0]['track_name'] == 'Song'
    t = _track()
    assert t.lyrics_status == 'available' and not evidence(t)


def test_g_real_lrc_with_strong_title_is_adopted(env):
    _set_track(title='Song (Instrumental)')
    _write(env['lrc'], ENGLISH)
    env['providers']['p1'] = p1 = FakeProvider('p1', [marker('p1')])
    run()
    t = _track()
    assert t.lyrics_status == 'available' and p1.calls == 0
    assert vetoes(t) == {'sidecar'}  # listed under "Conflicting evidence"


def test_h_embedded_lyrics_with_two_anchored_votes(env, embedded):
    embedded(VOCAL_12)
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
    run()
    t = _track()
    assert t.lyrics_status == 'available' and t.lyrics_provider == 'embedded'


def test_h2_embedded_veto_when_not_saveable(env, embedded):
    # Substantive vocal lyrics that fail a save gate: nothing saved, but the
    # veto holds — the votes can only make a hint.
    embedded(VOCAL_12 + "\n\nThis Lyrics is NOT for Commercial use")
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
    run()
    t = _track()
    assert t.lyrics_status == 'missing' and not os.path.exists(env['lrc'])
    assert sources(t) == {'p1', 'p2'} and vetoes(t) == {'embedded'}


def test_i_sidecar_marker_and_lrclib_flag_are_one_source(env):
    _write(env['lrc'], '[au: instrumental]\n')
    env['providers']['lrclib'] = FakeProvider('lrclib', [marker('lrclib')])
    _set_profile(lyrics_providers='["lrclib"]')
    run()
    t = _track()
    assert t.lyrics_status == 'missing'
    assert sources(t) == {'lrclib'} and len(evidence(t)) == 2


def test_j_override_never_rerun_with_marker_stays_missing(env):
    _set_track(instrumental_override='never')
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
    run()
    t = _track()
    assert t.lyrics_status == 'missing'
    assert vetoes(t) == {'override'}


def test_k_strong_title_with_embedded_vocals_saved_as_embedded(env, embedded):
    _set_track(title='Night Drive (Instrumental)')
    embedded(VOCAL_12)
    env['providers']['p1'] = p1 = FakeProvider('p1', [marker('p1', title='Night Drive (Instrumental)')])
    run()
    t = _track()
    assert t.lyrics_status == 'available' and t.instrumental_reason is None
    assert t.lyrics_provider == 'embedded' and t.lyrics_source is None
    assert _read(env['lrc']).strip() == VOCAL_12
    assert p1.calls == 0
    assert vetoes(t) == {'saved_lyrics'}  # rule T: shows up for review (audit I-6)


class TestE3Strictness:
    def test_lrclib_marker_far_off_plus_genius(self, env):
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, duration=207)])
        env['providers']['p2'] = FakeProvider('p2', [mk('p2', plain=MARKER, duration=None, strategy='search')])
        run()
        t = _track()
        assert t.lyrics_status == 'missing' and sources(t) == {'p2'}
        assert evidence(t)[0]['anchor'] is False

    def test_genius_plus_weak_title_is_a_hint(self, env):
        _set_track(title='Instrumental')
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, title='Instrumental', duration=None)])
        run()
        t = _track()
        assert t.lyrics_status == 'missing' and sources(t) == {'p1', 'title_weak'}

    def test_netease_hint_without_duration_is_no_vote(self, env):
        env['providers']['p1'] = FakeProvider('p1', [hint('p1', duration_diff=None)])
        run()
        assert not evidence()

    def test_netease_hint_far_off_is_no_vote(self, env):
        env['providers']['p1'] = FakeProvider('p1', [hint('p1', duration_diff=8.0)])
        run()
        assert not evidence()

    def test_transient_provider_makes_it_a_hint(self, env):
        env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
        env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
        env['providers']['p3'] = FakeProvider('p3', exc=ProviderTransientError('timeout'))
        run()
        t = _track()
        assert t.lyrics_status == 'missing' and sources(t) == {'p1', 'p2'}
        assert t.lyrics_retry_after < datetime.now() + timedelta(hours=1)  # transient retry

    def test_alias_matched_marker_never_votes(self, env):
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, md={'artist_via_alias': True})])
        run()
        assert not evidence()

    def test_gated_merged_copy_is_not_counted(self, env):
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, duration=201)])
        env['providers']['p2'] = FakeProvider('p2', [mk('p2', plain=MARKER, duration=None, gate='title_mismatch',
                                                       title='Other Song', title_score=30, score=0.3)])
        run()
        t = _track()
        assert t.lyrics_status == 'missing' and sources(t) == {'p1'}

    def test_lrclib_search_marker_plus_genius_page_is_consensus(self, env):
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, duration=201)])
        env['providers']['p2'] = FakeProvider('p2', [mk('p2', plain='Instrumental', duration=None)])
        run()
        t = _track()
        assert t.lyrics_status == 'instrumental' and t.instrumental_reason == 'consensus'


class TestAlbumContext:
    """(m) Track 'Intro' on 'Album A', 95 s."""

    @pytest.fixture(autouse=True)
    def intro(self, env):
        _set_track(title='Intro', duration=95)
        database.execute(update(TableAlbums).values(title='Album A'))

    def r(self, provider='p1', **kw):
        kw.setdefault('track_duration', 95)
        return mk(provider, plain=ENGLISH, title='Intro', **kw)

    def test_genius_other_album_refused(self, env):
        env['providers']['p1'] = FakeProvider('p1', [self.r(album='Album B', duration=None)])
        assert run() == 0
        assert _track().lyrics_status == 'missing' and _track().lyrics_retry_count == 1

    def test_search_other_album_off_by_6s_refused(self, env):
        env['providers']['p1'] = FakeProvider('p1', [self.r(album='Album B', duration=101)])
        assert run() == 0

    def test_exact_album_saved(self, env):
        env['providers']['p1'] = FakeProvider('p1', [self.r(album='Album A', duration=95, strategy='exact_album')])
        assert run() == 1

    def test_same_album_name_saved(self, env):
        env['providers']['p1'] = FakeProvider('p1', [self.r(album='Album A', duration=99)])
        assert run() == 1

    def test_other_album_within_1s_saved_residual_risk(self, env):
        env['providers']['p1'] = FakeProvider('p1', [self.r(album='Album B', duration=96)])
        assert run() == 1

    def test_legacy_requeued_interlude_gets_no_random_lyrics(self, env):
        _set_track(title='Interlude', lyrics_status='instrumental', instrumental_reason=None)
        assert lyrics_store.clear_instrumental(100, why='legacy-unverified', actor='bootstrap', emit_event=False)
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=ENGLISH, title='Interlude', album='Album B',
                                                       duration=None, track_duration=95)])
        assert run() == 0
        t = _track()
        assert t.lyrics_status == 'missing' and t.to_dict()['instrumental_prev']['why'] == 'legacy-unverified'

    def test_album_context_votes_need_album(self, env):
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, title='Intro', album='Album B',
                                                       duration=95, track_duration=95)])
        env['providers']['p2'] = FakeProvider('p2', [hint('p2', title='Intro', album='Album B')])
        run()
        t = _track()
        assert t.lyrics_status == 'missing'  # no anchor: neither vote is album-verified
        assert all(not v['anchor'] for v in evidence(t))


def test_n_overwrite_with_real_null_source_sidecar_is_adopted(env):
    _set_profile(overwrite_existing=True)
    _write(env['lrc'], ENGLISH)
    env['providers']['p1'] = FakeProvider('p1', [])
    run()
    t = _track()
    assert t.lyrics_status == 'available' and _read(env['lrc']) == ENGLISH
    # The disk check sync/reconcile do keeps it available (no flip-flop).
    lyrics_store.reconcile_track_lyrics(_track())
    assert _track().lyrics_status == 'available'


def test_n2_overwrite_replaces_null_source_sidecar_when_found(env):
    _set_profile(overwrite_existing=True)
    _write(env['lrc'], ENGLISH)
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', synced=SYNCED)])
    assert run() == 1
    assert _read(env['lrc']).startswith('[00:10.00]')


class TestRuleMSticky:
    """(o) Manual saves make the track "has vocals" for good."""

    @pytest.fixture
    def client(self):
        import lyrarr.api.metadata as m
        app = Flask(__name__)
        api = Api(app)
        api.add_namespace(m.api_ns_metadata, '/')
        return app.test_client()

    def _flow(self, env, client):
        r = client.post('/metadata/lyrics/download/100', json={'plain_lyrics': ENGLISH, 'provider': 'editor'})
        assert r.status_code == 200, r.json
        assert _track().instrumental_override == 'never'
        r = client.post('/metadata/lyrics/blacklist/100', json={})
        assert r.status_code == 200 and r.json['rescan'] is True
        assert not os.path.exists(env['lrc']) and _track().lyrics_status == 'missing'
        env['providers']['p1'] = p1 = FakeProvider('p1', [marker('p1')])
        env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
        run()
        t = _track()
        assert t.lyrics_status == 'missing'
        assert 'override' in vetoes(t)
        return p1

    def test_strong_title(self, env, client):
        _set_track(title='Song (Instrumental)')
        assert lyrics_store.set_instrumental(100, 'title', [{'source': 'title'}])
        p1 = self._flow(env, client)
        assert p1.kwargs[0]['track_name'] == 'Song'

    def test_weak_title(self, env, client):
        _set_track(title='Instrumental')
        self._flow(env, client)

    def test_cleared_row(self, env, client):
        _set_track(instrumental_prev='{"why": "legacy-unverified"}')
        self._flow(env, client)


# ---------------------------------------------------------------------------
# Embedded tags (§3.1 embedded-lyrics-ignored)
# ---------------------------------------------------------------------------

class TestEmbedded:
    def test_embedded_vocals_plus_marker_never_instrumental(self, env, embedded):
        embedded(VOCAL_12)
        env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
        run()
        t = _track()
        assert t.lyrics_status == 'available' and t.lyrics_provider == 'embedded'
        assert [h.action for h in _history()] == [1]

    def test_blacklisted_embedded_neither_saved_nor_vetoes(self, env, embedded):
        embedded(VOCAL_12)
        lyrics_store.blacklist_content(100, VOCAL_12)
        env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
        env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
        run()
        t = _track()
        assert not os.path.exists(env['lrc'])
        assert t.lyrics_status == 'instrumental' and t.instrumental_reason == 'consensus'

    def test_embedded_failing_validation_is_searched(self, env, embedded):
        embedded(VOCAL_12 + "\n\nThis Lyrics is NOT for Commercial use")
        env['providers']['p1'] = p1 = FakeProvider('p1', [mk('p1', synced=SYNCED)])
        assert run() == 1
        assert p1.calls == 1 and _track().lyrics_provider == 'p1'

    def test_saved_embedded_untouched_by_upgrade(self, env, embedded):
        embedded(VOCAL_12)
        env['providers']['p1'] = FakeProvider('p1', [])
        run()
        assert _track().lyrics_source is None
        env['providers']['p1'] = FakeProvider('p1', [mk('p1', synced=SYNCED)])
        assert w.upgrade_unsynced_lyrics() == 0
        assert _read(env['lrc']).strip() == VOCAL_12


# ---------------------------------------------------------------------------
# Pure selection checks
# ---------------------------------------------------------------------------

def test_da_funk_case_is_none():
    lrclib_marker = mk('lrclib', plain=MARKER, score=1.0)
    other_song = mk('genius', plain=ENGLISH, title='Around the Placeholder', title_score=40, score=0.57,
                    duration=None)
    ctx = w.TrackContext.for_track('Song', duration=200)
    ranked = w.rank_candidates([lrclib_marker, other_song], 'best_score', 0.5, ctx)
    _, _, outcome = w.choose_lyrics(ranked, 'best_score', track_title='Song', duration_s=200)
    assert outcome == 'none'


def test_merge_rank_votes_one_anchor_consensus():
    from lyrarr.metadata.merge import merge_provider_results
    results = [mk('lrclib', plain=MARKER, duration=201, _provider='lrclib'),
               mk('genius', plain='Instrumental', duration=None, _provider='genius')]
    ctx = w.TrackContext.for_track('Song', duration=200)
    merged = merge_provider_results(results)
    votes = w.collect_votes(markers=[], hints=[], results=merged, ctx=ctx, min_score=0.65)
    assert {v['source'] for v in votes} == {'lrclib', 'genius'}
    assert sum(v['anchor'] for v in votes) == 1
    assert w.consensus_reached(votes)


def test_merged_duplicates_count_per_qualifying_provider():
    from lyrarr.metadata.merge import merge_provider_results
    results = [mk('lrclib', plain=MARKER, duration=201, _provider='lrclib'),
               mk('genius', plain=MARKER, duration=None, _provider='genius', gate='title_mismatch',
                  title_score=30, score=0.3)]
    merged = merge_provider_results(results)
    assert len(merged) == 1
    votes = w.collect_votes(markers=[], hints=[], results=merged, min_score=0.65,
                            ctx=w.TrackContext.for_track('Song', duration=200))
    assert [v['source'] for v in votes] == ['lrclib']


# ---------------------------------------------------------------------------
# True positives: the policy still works
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('title', ['Night Drive (Instrumental)', 'Song (Off Vocal)',
                                   'Bite the Pain (1996 demo, instrumental)'])
def test_strong_titles_are_instrumental(env, title):
    _set_track(title=title)
    env['providers']['p1'] = p1 = FakeProvider('p1', [lyrics_result()])
    run()
    t = _track()
    assert t.lyrics_status == 'instrumental' and t.instrumental_reason == 'title'
    assert p1.calls == 0


def test_marker_plus_netease_hint_is_consensus(env):
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2', duration_diff=2.0)])
    run()
    t = _track()
    assert t.lyrics_status == 'instrumental' and t.instrumental_reason == 'consensus'
    assert sources(t) == {'p1', 'p2'} and len(evidence(t)) == 2


def test_two_marker_contents_within_3s_are_consensus(env):
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, duration=202)])
    env['providers']['p2'] = FakeProvider('p2', [mk('p2', plain='[00:00.00]Instrumental', duration=199)])
    run()
    assert _track().lyrics_status == 'instrumental'


def test_override_always_is_user(env):
    _set_track(instrumental_override='always')
    env['providers']['p1'] = p1 = FakeProvider('p1', [lyrics_result()])
    run()
    t = _track()
    assert t.lyrics_status == 'instrumental' and t.instrumental_reason == 'user' and p1.calls == 0


def test_consensus_during_cooldown_is_only_a_hint(env):
    from tests.test_metadata_worker import _cooldown
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
    env['providers']['p3'] = FakeProvider('p3', [])
    _cooldown('p3')
    run()
    t = _track()
    assert t.lyrics_status == 'missing' and sources(t) == {'p1', 'p2'}
    assert t.lyrics_cooldown_count == 1


def test_no_events_for_track_rows_selected_stale(env):
    # A row flagged meanwhile is not touched by a stale snapshot.
    snapshot = database.execute(select(TableTracks)).scalars().first()
    lyrics_store.set_instrumental(100, 'user', None)
    res = w.run_lyrics_pipeline(snapshot, eff=w._effective_settings(
        database.execute(select(TableAlbums)).scalars().first(), w._get_profile(1)),
        artist_name='Artist', album_title='Album', write=True)
    assert res['outcome'] == 'skipped' and _track().instrumental_reason == 'user'


# ---------------------------------------------------------------------------
# Acceptable vocal candidates turned down by instrumental evidence veto (§2.2, §3.1)
# ---------------------------------------------------------------------------

def test_marker_margin_failure_is_a_veto_not_consensus(env):
    # E2 + an anchored NetEase hint would be consensus, but an acceptable vocal
    # candidate (score ≥ floor, no gate) only failed the strong-marker margin.
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])  # LRCLIB-style flag, exact_album
    env['providers']['p2'] = FakeProvider('p2', [hint('p2', duration_diff=1.0)])
    env['providers']['p3'] = FakeProvider('p3', [mk('p3', plain=ENGLISH, score=0.70, title_score=80)])
    assert run() == 0
    t = _track()
    assert t.lyrics_status == 'missing' and t.instrumental_reason is None
    assert not os.path.exists(env['lrc'])
    assert 'vocal_candidate' in vetoes(t)
    assert t.lyrics_retry_after > datetime.now() + timedelta(days=13)  # deferred as a hint


def test_after_instrumental_rejection_is_a_veto_not_consensus(env):
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, strategy='exact_album')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2', duration_diff=1.0)])
    env['providers']['p3'] = FakeProvider('p3', [mk('p3', plain=ENGLISH, score=0.9, title_score=80)])
    assert run() == 0
    t = _track()
    assert t.lyrics_status == 'missing' and t.instrumental_reason is None
    assert 'vocal_candidate' in vetoes(t)


def test_after_instrumental_truncated_fallback_is_a_veto(env):
    short = "\n".join(ENGLISH.splitlines()[:2])
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=MARKER, strategy='exact_album')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2', duration_diff=1.0)])
    env['providers']['p3'] = FakeProvider('p3', [mk('p3', plain=short, score=0.9, title_score=80)])
    run()
    t = _track()
    assert t.lyrics_status != 'instrumental'


def test_unchanged_hint_is_not_re_recorded_on_transient_retries(env):
    events = []
    import lyrarr.metadata.lyrics_store as ls
    real = ls.record_instrumental_hint

    def spy(*a, **k):
        events.append(a)
        return real(*a, **k)
    w.record_instrumental_hint = spy
    try:
        env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
        env['providers']['p2'] = FakeProvider('p2', exc=ProviderTransientError('down'))
        run()
        assert len(events) == 1 and _track().lyrics_status == 'missing'
        for _ in range(3):
            _set_track(lyrics_retry_after=datetime.now() - timedelta(minutes=1))
            run()
        assert len(events) == 1
        assert _track().lyrics_transient_count == 4
    finally:
        w.record_instrumental_hint = real
