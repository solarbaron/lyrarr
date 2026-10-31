"""Audit check detection on crafted rows (PLAN §4.4, §7.1; WS-D).

Every registered check runs its detect() against the synthetic legacy
library; targeted cases cover the subtle selections: S-4/S-5 only touch
backoff written by older versions, S-14's millisecond bounds, I-7/F-9 match
both version labelling schemes, I-1's degraded mode, and the network checks
with a fake verify pipeline. All lyrics are synthetic.
"""

import os
import threading
from datetime import datetime, timedelta

import pytest

from tests.fixtures import legacy_library as L

if not L.configure('lyrarr-test-audit-checks-'):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from sqlalchemy import insert  # noqa: E402

import lyrarr.utilities.audit as audit  # noqa: E402
from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableBlacklist,
    TableProfiles,
    TableTracks,
    database,
    init_db,
    select,
    update,
)
from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.metadata import (
    embed,  # noqa: E402
    lyrics_store,  # noqa: E402
)
from lyrarr.metadata.lyrics_store import library_reachability  # noqa: E402
from lyrarr.utilities.audit.context import AuditContext  # noqa: E402
from lyrarr.utilities.audit.registry import REGISTRY, Issue, get_check  # noqa: E402

init_db()


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: None)
    library = L.build(tmp_path)
    yield library
    database.remove()


@pytest.fixture
def small(tmp_path, monkeypatch):
    """One root, one artist, one album; tests add their own tracks."""
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: None)
    L.reset_db()
    root = os.path.join(str(tmp_path), 'music')
    L.add_root(root)
    artist = os.path.join(root, 'Artist')
    album = os.path.join(artist, 'Album')
    L.add_artist(1, 'Artist', artist)
    L.add_album(10, 1, 'Album', album)
    yield {'root': root, 'album': album}
    database.remove()


def ctx_for(**kw):
    c = AuditContext(1, kw.pop('mode', 'dry_run'), kw.pop('trigger', 'user'), cancel_event=threading.Event(), **kw)
    c.reach = library_reachability(sample_per_root=200, max_missing_ratio=0.05)
    return c


def detect(code, ctx=None):
    check = get_check(code)
    ctx = ctx or ctx_for()
    ctx.begin_check(check, 1, 1)
    return list(check.detect(ctx))


def ids(issues_):
    return {i.entity_id for i in issues_}


def add(small, track_id, title='Song', **kw):
    return L.add_track(track_id, small['album'], 10, 1, title, **kw)


def row(track_id):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


# ---------------------------------------------------------------------------
# Every check
# ---------------------------------------------------------------------------

def _fake_pipeline(track, **kw):
    return {'outcome': 'none', 'chosen': None, 'content': None, 'candidates': [], 'votes': [], 'vetoes': [],
            'stats': {}}


@pytest.mark.parametrize('check_id', sorted(REGISTRY))
def test_every_check_detects_on_the_legacy_library(lib, monkeypatch, check_id):
    monkeypatch.setattr(w, 'run_lyrics_pipeline', _fake_pipeline)
    monkeypatch.setattr(embed, 'read_embedded_lyrics', lambda path: None)
    check = REGISTRY[check_id]
    ctx = ctx_for(network_limit=50)
    ctx.begin_check(check, 1, 1)
    found = list(check.detect(ctx))
    assert all(isinstance(i, Issue) and i.entity_key and i.title for i in found)
    keys = [i.entity_key for i in found]
    assert len(keys) == len(set(keys)), 'one issue per entity'
    expected = L.EXPECTED_DRY_RUN.get(check.spec.code)
    if expected is not None:
        assert ids(found) == expected


def test_spec_flags_follow_the_plan():
    spec = {c.spec.code: c.spec for c in REGISTRY.values()}
    for code in ('S-4', 'S-5', 'S-14', 'S-15'):
        assert spec[code].bootstrap_only and not spec[code].scheduled and not spec[code].default_enabled
    assert not spec['S-9'].scheduled
    for code in ('I-9', 'M-4', 'I-4', 'F-14', 'S-12'):
        assert not spec[code].scheduled and not spec[code].default_enabled
    assert {c for c, s in spec.items() if s.pins_evidence} >= {'H-1', 'I-7', 'F-9'}
    assert spec['S-14'].order < spec['S-2'].order < spec['I-1'].order
    assert spec['S-11'].order == max(s.order for s in spec.values())
    assert spec['I-1'].degrades_without_library and spec['S-3'].degrades_without_library
    assert spec['F-15'].fix_mode == 'review' and spec['M-5'].fix_mode == 'review'


# ---------------------------------------------------------------------------
# Instrumental
# ---------------------------------------------------------------------------

def test_i1_excludes_strong_titles_and_user_always(small):
    add(small, 1, 'Plain', status='instrumental')
    add(small, 2, 'Plain (Instrumental)', status='instrumental')
    add(small, 3, 'Mine', status='instrumental', instrumental_override='always')
    add(small, 4, 'With Lyrics', status='instrumental', lrc=L.verse('x'))
    found = {i.entity_id: i for i in detect('I-1')}
    assert set(found) == {1, 4}
    assert found[1].fix_action == 'requeue_legacy' and found[4].fix_action == 'adopt'


def test_i1_degraded_under_unreachable_root(small):
    add(small, 1, 'Plain', status='instrumental', lrc=L.verse('x'))
    os.rename(small['root'], small['root'] + '_off')
    try:
        found = detect('I-1')
        # Report only: nothing is cleared while the file can't be checked.
        assert [(i.entity_id, i.fix_action, i.fix_mode, i.details.get('degraded')) for i in found] == \
            [(1, None, 'report', True)]
        assert detect('I-3') == []
    finally:
        os.rename(small['root'] + '_off', small['root'])


def test_i1_repair_records_prev_and_staggers_large_batches(small):
    for tid in range(1, 26):
        add(small, tid, f'Plain {tid}', status='instrumental')
    check = get_check('I-1')
    ctx = ctx_for(mode='apply', trigger='bootstrap')
    ctx.begin_check(check, 1, 1)
    found = list(check.detect(ctx))
    ctx.stagger_n = len(found)
    now = datetime.now()
    for issue in found:
        assert check.repair(ctx, issue).status == 'fixed'
    rows = [row(t) for t in range(1, 26)]
    assert all(r.lyrics_status == 'missing' and '"bootstrap"' in r.instrumental_prev for r in rows)
    afters = [r.lyrics_retry_after for r in rows]
    assert all(now < a <= now + timedelta(hours=6, minutes=1) for a in afters)
    assert ctx.counts['cleared'] == 25


def test_i5_weak_title_and_i8_stale_consensus(small):
    add(small, 1, 'The Instrumental', status='instrumental', instrumental_reason='title')
    add(small, 2, 'Song (Instrumental)', status='instrumental', instrumental_reason='title')
    add(small, 3, 'Old', status='instrumental', instrumental_reason='consensus',
        instrumental_checked_at=datetime.now() - timedelta(days=200))
    add(small, 4, 'New', status='instrumental', instrumental_reason='consensus',
        instrumental_checked_at=datetime.now() - timedelta(days=10))
    assert ids(detect('I-5')) == {1}
    assert ids(detect('I-8')) == {3}


def test_i6_karaoke_is_report_only(small):
    add(small, 1, 'Song (Instrumental)', status='available', lrc=L.verse('v'))
    add(small, 2, 'Song (Karaoke Version)', status='available', lrc=L.verse('k'))
    add(small, 3, 'Song (Instrumental)', status='available', lrc=L.verse('m'), lyrics_source='manual')
    add(small, 4, 'Other (Instrumental)', status='available', lrc=L.verse('n'), instrumental_override='never')
    found = {i.entity_id: i for i in detect('I-6')}
    assert set(found) == {1, 2}
    assert found[1].fix_action == 'archive_flag_instrumental'
    assert found[2].fix_mode == 'report'


def test_i7_matches_both_labelling_schemes(small):
    add(small, 1, 'Legacy', status='missing')
    add(small, 2, 'New Style', status='missing')
    add(small, 3, 'Other Replacer', status='missing')
    add(small, 4, 'Marker Only', status='missing')
    add(small, 5, 'Skipped', status='blacklisted')
    add(small, 6, 'User File Later', status='available', lrc=L.verse('user'), lyrics_source='manual')
    L.add_version(1, L.verse('a'), provider='batch-delete', replaced_by=None)
    L.add_version(2, L.verse('b'), provider='lrclib', replaced_by='batch-delete')
    L.add_version(3, L.verse('c'), provider='batch-delete', replaced_by='editor')
    L.add_version(4, '[Instrumental]', provider='batch-delete')
    L.add_version(5, L.verse('e'), provider='batch-delete')
    L.add_version(6, L.verse('f'), provider='batch-delete')
    found = {i.entity_id: i for i in detect('I-7')}
    assert set(found) == {1, 2}
    assert found[1].details['pin_version_ids']


def test_f9_matches_both_labelling_schemes(small):
    plain = L.verse('w', 6)
    lines = plain.splitlines()
    scrambled = L.synced('\n'.join(lines[3:] + lines[:3]))
    add(small, 1, 'Legacy', status='available', lyrics_source='generated', lrc=scrambled)
    add(small, 2, 'New', status='available', lyrics_source='generated', lrc=scrambled)
    add(small, 3, 'Same Order', status='available', lyrics_source='generated', lrc=L.synced(plain))
    L.add_version(1, plain, provider='batch-sync-generate', replaced_by=None)
    L.add_version(2, plain, provider='lrclib', replaced_by='batch-sync-generate')
    L.add_version(3, plain, provider='batch-sync-generate')
    assert ids(detect('F-9')) == {1, 2}


def test_i9_network_recheck_with_fake_pipeline(small, monkeypatch):
    add(small, 1, 'Was Flagged', status='instrumental', instrumental_reason='consensus')
    add(small, 2, 'Still Nothing', status='instrumental', instrumental_reason='consensus')
    add(small, 3, 'User Says', status='instrumental', instrumental_reason='user', instrumental_override='always')
    seen = []

    def fake(track, **kw):
        seen.append(track.lidarrTrackId)
        assert kw['mode'] == 'verify' and kw['write'] is False
        if track.lidarrTrackId == 1:
            return {'outcome': 'ok', 'chosen': {'provider': 'lrclib', 'acceptable': True, 'score': 0.9},
                    'candidates': []}
        return {'outcome': 'none', 'chosen': None, 'candidates': []}

    monkeypatch.setattr(w, 'run_lyrics_pipeline', fake)
    assert ids(detect('I-9', ctx_for(network_limit=10))) == {1}
    assert seen == [1, 2]
    seen.clear()
    assert ids(detect('I-9', ctx_for(network_limit=1))) == {1} and seen == [1]


def test_m4_classifies_confirmed_wrong_ok_and_unknown(small, monkeypatch):
    good_file = L.verse('right', 8)
    wrong_file = L.verse('wrong', 8)
    for tid, text in ((1, good_file), (2, wrong_file), (3, L.verse('unknown', 8))):
        add(small, tid, f'Song {tid}', status='available', lrc=text, lyrics_source='auto')

    def cand(text, acceptable, reason=None, provider='lrclib'):
        return {'provider': provider, 'acceptable': acceptable, 'reject_reason': reason, 'score': 0.9,
                'plain': text, 'synced': None, 'matched_title': 'X', 'source_id': 7, 'equivalence_to_file': None}

    def fake(track, **kw):
        if track.lidarrTrackId == 1:
            c = cand(good_file, True)
            return {'outcome': 'ok', 'chosen': c, 'candidates': [c]}
        if track.lidarrTrackId == 2:
            right = cand(L.other_verse(8), True, provider='netease')
            return {'outcome': 'ok', 'chosen': right,
                    'candidates': [cand(wrong_file, False, 'gate:title_mismatch'), right]}
        return {'outcome': 'none', 'chosen': None, 'candidates': []}

    monkeypatch.setattr(w, 'run_lyrics_pipeline', fake)
    ctx = ctx_for(scope={'albumIds': [10]}, network_limit=10)
    found = {i.entity_id: i for i in detect('M-4', ctx)}
    assert found[1].fix_action == 'stamp_provenance' and found[1].fix_mode == 'auto'
    assert found[2].fix_action == 'replace_with_candidate'
    assert found[3].fix_action == 'request_replace'
    check = get_check('M-4')
    assert check.repair(ctx, found[2]).status == 'fixed'
    assert lyrics_store.read_sidecar(L.lrc_path(row(2).path)).text.strip() == L.other_verse(8)
    bl = database.execute(select(TableBlacklist).where(TableBlacklist.lidarrTrackId == 2)).scalars().all()
    assert {b.content_hash for b in bl} == {lyrics_store.content_hash(wrong_file)}
    assert check.repair(ctx, found[1]).status == 'fixed'
    assert row(1).lyrics_provider == 'lrclib' and row(1).lyrics_source_id == '7'


# ---------------------------------------------------------------------------
# Files / matching
# ---------------------------------------------------------------------------

def test_f15_and_m1_split_the_overrun_range(small):
    add(small, 1, 'Small Overrun', status='available', duration=100, lrc=L.synced(L.verse('a', 10), 18, 10))
    add(small, 2, 'Big Overrun', status='available', duration=60, lrc=L.synced(L.verse('b', 10), 10, 10))
    add(small, 3, 'Fits', status='available', duration=200, lrc=L.synced(L.verse('c', 10), 10, 10))
    add(small, 4, 'Manual Overrun', status='available', duration=60, lyrics_source='manual',
        lrc=L.synced(L.verse('d', 10), 10, 10))
    f15 = {i.entity_id: i for i in detect('F-15')}
    assert set(f15) == {1, 4} and f15[4].fix_mode == 'report'
    assert ids(detect('M-1')) == {2}


def test_m2_same_generic_title_on_another_album(small):
    L.add_album(11, 1, 'Album B', os.path.join(os.path.dirname(small['album']), 'Album B'))
    text = L.verse('intro', 6)
    add(small, 1, 'Intro', status='available', lrc=text)
    L.add_track(2, os.path.join(os.path.dirname(small['album']), 'Album B'), 11, 1, 'Intro', status='available',
                lrc=text)
    add(small, 3, 'Song', status='available', lrc=L.verse('song', 6))
    L.add_track(4, os.path.join(os.path.dirname(small['album']), 'Album B'), 11, 1, 'Song (Live)',
                status='available', lrc=L.verse('song', 6))
    found = {i.entity_id: i for i in detect('M-2')}
    assert set(found) == {1, 2}
    assert found[1].details['reason'] == 'same_generic_title_other_album'


def test_m2_ignores_duplicate_rows_of_one_file(small):
    """Two rows for one audio file (Lidarr duplicate, filename-style title) share the
    sidecar trivially: not a wrong-song match. A third, distinct file with the same
    content and another title still is."""
    text = L.synced(L.verse('dup', 6))
    path = add(small, 1, 'Song Title', status='available', lrc=text)
    L.add_track(2, small['album'], 10, 1, 'Artist - Album - 01 - Song Title', status='available',
                stem=os.path.splitext(os.path.basename(path))[0])
    assert detect('M-2') == []
    add(small, 3, 'Other Title', status='available', lrc=text)
    assert ids(detect('M-2')) == {1, 2, 3}


def test_f12_skips_fresh_temp_files(small):
    fresh = os.path.join(small['album'], '.a.lrc.lyrarr-tmp')
    old = os.path.join(small['album'], '.b.lrc.lyrarr-tmp')
    for p in (fresh, old):
        with open(p, 'w') as f:
            f.write('x')
    L.set_mtime(old, datetime.now() - timedelta(hours=1))
    assert {i.path for i in detect('F-12')} == {old}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def test_s4_only_selects_backoff_from_older_versions(small):
    later = datetime.now() + timedelta(days=10)
    add(small, 1, 'Legacy', lyrics_retry_count=3, lyrics_retry_after=later)
    add(small, 2, 'Deferred By Worker')
    w._defer_track(row(2), 'normal')
    add(small, 3, 'Hinted', lyrics_retry_count=2, lyrics_retry_after=later,
        instrumental_evidence='[{"source": "lrclib", "kind": "flag"}]')
    add(small, 4, 'Counters Only', lyrics_transient_count=2)
    add(small, 5, 'User Always', lyrics_retry_count=1, instrumental_override='always')
    add(small, 6, 'Fresh')
    assert row(2).lyrics_retry_set_at is not None
    found = detect('S-4')
    assert ids(found) == {1, 4}
    check = get_check('S-4')
    ctx = ctx_for(mode='apply', trigger='bootstrap')
    for issue in found:
        assert check.repair(ctx, issue).status == 'fixed'
    assert row(1).lyrics_retry_count == 0 and row(1).lyrics_retry_set_at is not None
    assert detect('S-4') == []


def test_s5_ignores_stamped_rows_and_prefer_plain_albums(small):
    later = datetime.now() + timedelta(days=5)
    add(small, 1, 'Legacy Upgrade', status='available', lyrics_source='auto', lrc=L.verse('a'),
        upgrade_retry_count=2, upgrade_retry_after=later)
    add(small, 2, 'Stamped', status='available', lyrics_source='auto', lrc=L.verse('b'),
        upgrade_retry_count=1, upgrade_retry_after=later, upgrade_retry_set_at=datetime.now())
    add(small, 3, 'Manual', status='available', lyrics_source='manual', lrc=L.verse('c'),
        upgrade_retry_count=1, upgrade_retry_after=later)
    assert ids(detect('S-5')) == {1}
    database.execute(update(TableAlbums).where(TableAlbums.lidarrAlbumId == 10).values(override_prefer_synced=False))
    assert detect('S-5') == []


@pytest.mark.parametrize('duration,expected', [
    (245000, 245), (130000, 130), (10801, 11), (20000000, None), (10800, 'skip'), (200, 'skip')])
def test_s14_conversion_bounds(small, duration, expected):
    add(small, 1, 'Song', duration=duration)
    found = detect('S-14')
    if expected == 'skip':
        assert found == []
        return
    assert found[0].details['new'] == expected
    assert get_check('S-14').repair(ctx_for(mode='apply'), found[0]).status == 'fixed'
    assert row(1).duration == expected


def test_s7_defaults(small):
    database.execute(insert(TableProfiles).values(name='Audit fixture second', is_default=True))
    found = detect('S-7')
    assert len(found) == 1 and found[0].fix_action == 'fix_defaults'
    get_check('S-7').repair(ctx_for(mode='apply'), found[0])
    assert database.execute(select(TableProfiles).where(TableProfiles.is_default == True)).scalars().all().__len__() == 1  # noqa: E712
    assert detect('S-7') == []


def test_s3_fixes_invariants(small):
    add(small, 1, 'Has Flag', status='missing', hasLyrics=True)
    add(small, 2, 'Skipped Synced', status='blacklisted', is_synced=True, detected_language='en')
    add(small, 3, 'Missing With File', status='missing', is_synced=True, lrc='[Instrumental]')
    add(small, 4, 'Unknown Status', status='weird', lrc=L.verse('w'))
    found = {i.entity_id: i for i in detect('S-3')}
    assert set(found) == {1, 2, 4}
    ctx = ctx_for(mode='apply')
    for issue in found.values():
        assert get_check('S-3').repair(ctx, issue).status == 'fixed'
    assert row(1).hasLyrics is False
    assert (row(2).is_synced, row(2).detected_language) == (False, None)
    assert row(4).lyrics_status == 'available'


def test_stagger_window():
    ctx = ctx_for()
    assert ctx.stagger(20) is None
    now = datetime.now()
    draws = [ctx.stagger(150) for _ in range(200)]
    assert all(now < d <= now + timedelta(hours=12, seconds=5) for d in draws)
    assert max(draws) - min(draws) > timedelta(hours=6)
    assert max(ctx.stagger(100000) for _ in range(50)) <= datetime.now() + timedelta(hours=72, seconds=5)


def test_audit_package_api():
    assert set(audit.LEGACY_ENDPOINT_CHECKS) <= set(REGISTRY)
    assert set(audit.EVIDENCE_CHECKS) <= set(REGISTRY)
    assert callable(audit.start_run) and callable(audit.cancel_run)


# ---------------------------------------------------------------------------
# Review follow-ups: user decisions are never overridden
# ---------------------------------------------------------------------------

def _txt(audio_path, text):
    with open(os.path.splitext(audio_path)[0] + '.txt', 'w', encoding='utf-8') as f:
        f.write(text)
    return os.path.splitext(audio_path)[0] + '.txt'


def test_f10_imports_only_onto_missing_rows_without_user_decisions(small):
    text = L.verse('legacy', 8)
    _txt(add(small, 1, 'Fresh'), text)
    p2 = _txt(add(small, 2, 'Rejected', status='blacklisted'), text)
    p3 = _txt(add(small, 3, 'Mine', status='instrumental', instrumental_override='always'), text)
    _txt(add(small, 4, 'Always but missing', instrumental_override='always'), text)
    _txt(add(small, 5, 'Blacklisted text'), text)
    database.execute(insert(TableBlacklist).values(metadata_type='lyrics', provider='user', lidarrTrackId=5,
                                                   content_hash=lyrics_store.content_hash(text)))
    found = {i.entity_id: i for i in detect('F-10')}
    assert set(found) == {1, 2, 3, 4, 5}
    assert found[1].fix_action == 'import_txt'
    for tid in (2, 3, 4, 5):
        assert found[tid].fix_action is None and found[tid].fix_mode == 'report', tid
    assert 'blacklisted' in found[5].details['reason']

    check = get_check('F-10')
    ctx = ctx_for(mode='apply')
    ctx.begin_check(check, 1, 1, 'apply')
    # A forged / stale import issue for a user-decided row is refused, nothing changes.
    for tid, path in ((2, p2), (3, p3)):
        forged = Issue(entity_type='track', entity_id=tid, entity_key=f'track:{tid}', title='x', path=path,
                       fix_action='import_txt', track_id=tid, album_id=10, artist_id=1)
        assert check.repair(ctx, forged).status == 'skipped', tid
        assert os.path.exists(path) and not os.path.exists(os.path.splitext(path)[0] + '.lrc')
    assert row(2).lyrics_status == 'blacklisted'
    assert row(3).lyrics_status == 'instrumental' and row(3).instrumental_override == 'always'

    assert check.repair(ctx, found[1]).status == 'fixed'
    assert row(1).lyrics_status == 'available'


def test_f10_validation_rejects_marker_text(small):
    _txt(add(small, 1, 'Marker'), '[Instrumental]\n')
    _txt(add(small, 2, 'Short'), 'la\n')
    found = {i.entity_id: i for i in detect('F-10')}
    assert all(i.fix_action is None for i in found.values())


def test_s9_is_report_only_without_lidarr(small, monkeypatch):
    from lyrarr.app.config import settings
    p = add(small, 1, 'Dup')
    L.add_track(2, small['album'], 10, 1, 'Dup', stem=os.path.splitext(os.path.basename(p))[0], audio=False)
    monkeypatch.setitem(settings.general, 'use_lidarr', False)
    ctx = ctx_for()
    found = detect('S-9', ctx)
    assert len(found) == 1 and found[0].fix_action is None and found[0].fix_mode == 'report'
    assert any('Lidarr' in n for n in ctx.notes)
    assert get_check('S-9').repair(ctx_for(mode='apply'), Issue(
        entity_type='file', entity_id=None, entity_key=f'file:{p}', title='x', path=p,
        fix_action='full_sync')).status == 'skipped'
    monkeypatch.setitem(settings.general, 'use_lidarr', True)
    assert [i.fix_action for i in detect('S-9')] == ['full_sync']


def test_m2_apply_rebuilds_groups_once_and_rechecks_one_group(small, monkeypatch):
    from lyrarr.utilities.audit.checks import matching
    text = L.verse('shared', 6)
    for tid, title in ((1, 'Alpha'), (2, 'Beta'), (3, 'Gamma'), (4, 'Delta')):
        add(small, tid, title, status='available', lrc=text, lyrics_source='auto')
    check = get_check('M-2')
    ctx = ctx_for(mode='apply')
    ctx.begin_check(check, 1, 1)
    found = list(check.detect(ctx))
    assert ids(found) == {1, 2, 3, 4}
    calls = []
    real = matching._row_hash
    monkeypatch.setattr(matching, '_row_hash', lambda c, r: calls.append(r.lidarrTrackId) or real(c, r))
    progress = []
    monkeypatch.setattr(ctx, 'progress', lambda *a, **k: progress.append(a))
    ctx.begin_check(check, 1, 1, 'apply')
    for issue in found:
        assert check.recheck(ctx, issue) is not None
    # One rebuild (4 rows) + one group re-verification per recheck (4 × 4), not 4 rebuilds over the library.
    assert len(calls) == 4 + 4 * 4
    assert progress == []
    # The group re-verification sees a change made during the apply.
    os.remove(L.lrc_path(row(2).path))
    os.remove(L.lrc_path(row(3).path))
    os.remove(L.lrc_path(row(4).path))
    ctx.forget(L.lrc_path(row(2).path))
    assert check.recheck(ctx, found[0]) is None


def test_i3_not_instrumental_rolls_back_override_when_adopt_fails(small, monkeypatch):
    add(small, 1, 'Theme (Instrumental)', status='instrumental', instrumental_reason='consensus',
        instrumental_override=None, instrumental_prev='{"status": "missing"}', lrc=L.verse('x'))
    found = detect('I-3')
    assert len(found) == 1 and 'not_instrumental' in found[0].details['alt_actions']
    issue = found[0]
    issue.fix_action = 'not_instrumental'
    monkeypatch.setattr(lyrics_store, 'adopt_sidecar', lambda *a, **k: False)
    assert get_check('I-3').repair(ctx_for(mode='apply'), issue).status == 'failed'
    r = row(1)
    assert r.instrumental_override is None and r.instrumental_prev == '{"status": "missing"}'
    assert r.lyrics_status == 'instrumental'


def test_c3_never_offers_folder_art(small, monkeypatch):
    from lyrarr.app.config import settings
    monkeypatch.setitem(settings.metadata.covers, 'folder_art_filename', 'folder')
    artist_dir = os.path.dirname(small['album'])
    folder = os.path.join(artist_dir, 'folder.jpg')
    cover = os.path.join(artist_dir, 'cover.jpg')
    for p in (folder, cover):
        with open(p, 'wb') as f:
            f.write(b'\xff\xd8\xff\xe0 synthetic')
        L.add_history(None, None, 1, metadata_type='cover', metadata_path=p, description='Downloaded cover')
    found = [i for i in detect('C-3') if i.entity_type == 'artist']
    assert len(found) == 1 and found[0].details['files'] == [cover]
    found[0].details['files'] = [folder, cover]  # a stale / forged list still never deletes folder.*
    assert get_check('C-3').repair(ctx_for(mode='apply'), found[0]).status == 'fixed'
    assert os.path.exists(folder) and not os.path.exists(cover)
