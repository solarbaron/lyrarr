"""Regression tests for the fixes made after the red-team audits (beyond the
auditors' own repro files): title-flag carry-over on Lidarr renames, the audit
summary's notion of "latest run", staggered mass requeues, blacklist-after-
persist, bare-"Instrumental" version tags, the hint title floor and the
dash-suffix generic titles.

All lyrics are synthetic; nothing touches the network.
"""

import json
import os
from datetime import datetime

import pytest
from flask import Flask
from flask_restx import Api

from tests import test_core_sync as S
from tests.fixtures import legacy_library as L
from tests.test_core_sync import clean_db  # noqa: F401  (autouse: clean tables, Lidarr patched)

if not L.configure('lyrarr-test-followups-'):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from lyrarr.api import audit as audit_api  # noqa: E402
from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.database import TableAuditRuns, database, init_db  # noqa: E402
from lyrarr.metadata.download_worker import TrackContext, candidate_reject_reason  # noqa: E402
from lyrarr.metadata.lyrics_text import (  # noqa: E402
    instrumental_title_tier,
    is_generic_title,
    needs_album_context,
)

init_db()

VERSE = 'first line of the verse\nsecond line of the verse\nthird line of the verse\n'


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('title', ['01 Intro', 'Intro Skit', 'Outro Skit', 'Intro / Outro', 'Interludio',
                                   'Intermission', 'Prologue', 'Epilogue', 'Overture',
                                   'Untitled #8 - 2022 Remaster', 'Interlude - Live'])
def test_more_generic_titles_need_album_context(title):
    assert is_generic_title(title) and needs_album_context(title)


@pytest.mark.parametrize('title', ['99 Problems', '1 Thing', 'Intro to Love', 'Love - Live', 'Opening Theme',
                                   '7 Rings', 'The Instrumental'])
def test_specific_titles_stay_specific(title):
    assert not is_generic_title(title)


@pytest.mark.parametrize('title,tier', [
    ('Song (feat. Instrumental)', None),
    ('Song (Instrumental)', 'strong'),
    ('Song (feat. X) (Instrumental)', 'strong'),
    ('Song - Instrumental', 'strong'),
    ('Artist - Album - 02 - Song (Instrumental)', 'strong'),
    ('Artist - Album - 02 - Instrumental', 'weak'),
    ('Song (with Instrumental Outro)', 'weak'),
])
def test_title_tiers_after_fixes(title, tier):
    assert instrumental_title_tier(title) == tier


def test_bare_instrumental_title_rejects_untagged_result():
    ctx = TrackContext.for_track('Instrumental', None, 'Album', 100)
    vocal = {'track_name': 'The Instrumental', 'score': 0.9, 'match_details': {}}
    same = {'track_name': 'Instrumental', 'score': 0.9, 'match_details': {}}
    assert candidate_reject_reason(vocal, 0.5, ctx) == 'version_tag'
    assert candidate_reject_reason(same, 0.5, ctx) is None
    # A normal title is unaffected.
    ctx2 = TrackContext.for_track('Lights', None, 'Album', 100)
    assert candidate_reject_reason({'track_name': 'Lights', 'score': 0.9, 'match_details': {}}, 0.5, ctx2) is None


# ---------------------------------------------------------------------------
# Sync: a title flag does not survive a rename to a plain title
# ---------------------------------------------------------------------------

class TestTitleFlagOnRename:
    def test_rename_to_plain_title_clears_title_flag(self, monkeypatch, tmp_path):
        lib = S.Lib(tmp_path, titles=('Song (Instrumental)',))
        lib.sync(monkeypatch)
        S._set(lyrics_status='instrumental', instrumental_reason='title',
               instrumental_evidence=json.dumps([{'source': 'title', 'kind': 'title'}]))
        lib.records[0] = {**lib.records[0], 'title': 'Song'}
        lib.sync(monkeypatch)
        row = S._row()
        assert row.title == 'Song'
        assert row.lyrics_status == 'missing' and row.instrumental_reason is None
        assert json.loads(row.instrumental_prev)['why'] == 'weak-title'

    def test_rename_keeps_user_and_strong_flags(self, monkeypatch, tmp_path):
        lib = S.Lib(tmp_path, titles=('Song (Instrumental)', 'Other (Instrumental)'))
        lib.sync(monkeypatch)
        S._set(100, lyrics_status='instrumental', instrumental_reason='title')
        S._set(101, lyrics_status='instrumental', instrumental_reason='user', instrumental_override='always')
        lib.records[0] = {**lib.records[0], 'title': 'Song (Instrumental Version)'}
        lib.records[1] = {**lib.records[1], 'title': 'Other'}
        lib.sync(monkeypatch)
        assert S._row(100).lyrics_status == 'instrumental'
        assert S._row(101).lyrics_status == 'instrumental'

    def test_title_flag_not_carried_onto_plain_titled_reimport(self, monkeypatch, tmp_path):
        lib = S.Lib(tmp_path, titles=('Song (Instrumental)',))
        lib.sync(monkeypatch)
        S._set(lyrics_status='instrumental', instrumental_reason='title')
        lib.records[0] = {**lib.records[0], 'title': 'Song'}
        new = S.TestCarryOver()._reimport(monkeypatch, lib)
        assert new.lyrics_status == 'missing'


# ---------------------------------------------------------------------------
# Audit summary: the latest COMPLETE top-level run
# ---------------------------------------------------------------------------

def _client():
    app = Flask(__name__)
    api = Api(app)
    api.add_namespace(audit_api.api_ns_audit, '/')
    return app.test_client()


def _add_run(status, mode='dry_run', parent=None):
    from sqlalchemy.dialects.sqlite import insert
    now = datetime.now()
    return database.execute(insert(TableAuditRuns).values(
        mode=mode, trigger='user', status=status, parent_run_id=parent, started_at=now, finished_at=now,
    )).inserted_primary_key[0]


def test_summary_latest_skips_cancelled_and_child_runs(monkeypatch):
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: None)
    L.reset_db()
    database.execute(TableAuditRuns.__table__.delete())
    done = _add_run('done')
    _add_run('done', mode='apply', parent=done)   # a child apply run
    _add_run('cancelled')                          # a partial check
    latest = _client().get('/audit/summary').get_json()['latest']
    assert latest['run_id'] == done
    fresh_apply = _add_run('done', mode='apply')   # "check and apply" (top level)
    assert _client().get('/audit/summary').get_json()['latest']['run_id'] == fresh_apply


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------

def test_mark_missing_takes_a_staggered_retry(tmp_path, monkeypatch):
    from lyrarr.utilities.audit import repairs as rp
    L.reset_db()
    root = os.path.join(str(tmp_path), 'music')
    L.add_root(root)
    album = os.path.join(root, 'Artist', 'Album')
    L.add_artist(1, 'Artist', os.path.join(root, 'Artist'))
    L.add_album(10, 1, 'Album', album)
    L.add_track(1, album, 10, 1, 'Song', status='available', lrc=VERSE)
    later = datetime(2030, 1, 1)
    assert rp.mark_missing(S._row(1), retry_after=later)
    row = S._row(1)
    assert row.lyrics_status == 'missing' and row.lyrics_retry_after == later


# ---------------------------------------------------------------------------
# Matching: a band credited by its leader alone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('library,credit', [
    ('The Jimi Hendrix Experience', 'Jimi Hendrix'),
    ('Captain Beefheart & His Magic Band', 'Captain Beefheart'),
    ('Bob Marley & The Wailers', 'Bob Marley'),
    ('Tom Petty and the Heartbreakers', 'Tom Petty'),
])
def test_band_credited_by_leader_is_accepted_on_exact_title_and_duration(library, credit):
    from lyrarr.metadata.base import LyricsProvider as P
    m = P.score_result('Song', library, 200, 'Song', credit, 201)
    assert m['gate'] is None and m['artist_via_alias'] is True  # relaxed: never instrumental evidence
    # Without near-exact duration agreement the credit stays gated.
    assert P.score_result('Song', library, 200, 'Song', credit, 205)['gate'] == 'artist_mismatch'
    assert P.score_result('Song', library, 200, 'Other Song', credit, 200)['gate'] == 'artist_mismatch'


@pytest.mark.parametrize('library,credit', [
    ('Daft Punk & Pharrell', 'Daft Punk'),
    ('Bush', 'Kate Bush'),
    ('Kate Bush', 'Bush'),
    ('Simon & Garfunkel', 'Simon'),
    ('Joey Valence & Brae', 'Joey Valence'),
])
def test_member_or_superset_credit_stays_gated(library, credit):
    from lyrarr.metadata.base import LyricsProvider as P
    assert P.score_result('Song', library, 200, 'Song', credit, 200)['gate'] == 'artist_mismatch'


@pytest.mark.parametrize('query,result,gated', [
    ('All U Got (2013 demo)', 'All U Got', False),
    ('Denial of Life (rehearsals 08-20-1986)', 'Denial of Life', False),
    ('Song (Pt. 2)', 'Song (Pt. 1)', True),
    ('Clueless 2', 'Clueless', True),
    ('Song 2 (Live)', 'Song (Live)', True),
])
def test_digit_cap_ignores_dates_in_version_brackets(query, result, gated):
    from lyrarr.metadata.base import LyricsProvider as P
    assert (P.score_result(query, 'A', 200, result, 'A', 200)['gate'] == 'title_mismatch') is gated


def test_bootstrap_retry_skips_a_second_full_sync(tmp_path, monkeypatch):
    """A bootstrap left incomplete (unmounted root) retries without another
    full Lidarr sync once one has succeeded; long durations stay intact."""
    import shutil

    import lyrarr.utilities.audit as audit
    from lyrarr.lidarr import sync as lidarr_sync
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: None)
    calls = []
    monkeypatch.setattr(lidarr_sync, 'run_full_sync', lambda *a, **k: calls.append(1) or True)
    L.reset_db()
    database.execute(TableAuditRuns.__table__.delete())
    up, down = os.path.join(str(tmp_path), 'up'), os.path.join(str(tmp_path), 'down')
    L.add_root(up)
    L.add_root(down)
    L.add_artist(1, 'Up', os.path.join(up, 'Up'))
    L.add_album(10, 1, 'Up Album', os.path.join(up, 'Up', 'A'))
    L.add_artist(2, 'Down', os.path.join(down, 'Down'))
    L.add_album(20, 2, 'Down Album', os.path.join(down, 'Down', 'B'))
    L.add_track(1, os.path.join(up, 'Up', 'A'), 10, 1, 'Long Mix', duration=14400)
    L.add_track(11, os.path.join(down, 'Down', 'B'), 20, 2, 'Down 1', status='available', lrc=VERSE)
    shutil.rmtree(down)
    assert audit.run_bootstrap() is not None
    assert audit.run_bootstrap() is not None
    assert len(calls) == 1
    assert S._row(1).duration == 14400


# ---------------------------------------------------------------------------
# Consensus: a WEAK title needs an LRCLIB anchor (E2/E3), not one hint
# ---------------------------------------------------------------------------

def test_weak_title_plus_single_hint_is_no_consensus():
    from lyrarr.metadata.download_worker import consensus_reached
    weak = {'source': 'title_weak', 'kind': 'title', 'anchor': False}
    hint_vote = {'source': 'netease', 'kind': 'pure_music', 'anchor': True}
    flag = {'source': 'lrclib', 'kind': 'flag', 'anchor': True}
    content = {'source': 'lrclib', 'kind': 'marker_content', 'anchor': True}
    other_hint = {'source': 'musixmatch', 'kind': 'mxm_instrumental', 'anchor': True}
    assert not consensus_reached([weak, hint_vote])
    assert consensus_reached([weak, flag])
    assert consensus_reached([weak, content])
    assert consensus_reached([hint_vote, other_hint])        # two independent providers still count
    assert not consensus_reached([flag, hint_vote], clean=False)
