"""Instrumental API (PLAN §1.8, §2.4; WS-D): the track PUT override paths, the
bulk instrumental actions, the Wanted instrumental views incl. "Cleared after
upgrade", track filters, history labels, the batch-delete instrumental mode
and the CSRF rule. All lyrics are synthetic."""

import os

import pytest

from tests.fixtures import legacy_library as L

if not L.configure('lyrarr-test-api-instrumental-'):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from flask import Flask  # noqa: E402
from flask_restx import Api  # noqa: E402

from lyrarr.api import audit as audit_api  # noqa: E402
from lyrarr.api import bulk, history, tracks, wanted  # noqa: E402
from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableHistory,
    TableLyricsVersions,
    TableTracks,
    database,
    init_db,
    select,
)
from lyrarr.metadata import lyrics_store  # noqa: E402

init_db()


@pytest.fixture
def env(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    queued = []
    monkeypatch.setattr(tracks, '_queue_search', lambda ids: queued.append(list(ids)) or True)
    L.reset_db()
    root = os.path.join(str(tmp_path), 'music')
    L.add_root(root)
    artist = os.path.join(root, 'Artist')
    album = os.path.join(artist, 'Album')
    L.add_artist(1, 'Artist', artist)
    L.add_album(10, 1, 'Album', album)
    app = Flask(__name__)
    api = Api(app)
    for ns in (tracks.api_ns_tracks, wanted.api_ns_wanted, bulk.api_ns_bulk, history.api_ns_history,
               audit_api.api_ns_audit):
        api.add_namespace(ns, '/')
    yield {'c': app.test_client(), 'album': album, 'events': events, 'queued': queued, 'root': root}
    database.remove()


def add(env, track_id, title='Song', **kw):
    return L.add_track(track_id, env['album'], 10, 1, title, **kw)


def row(track_id):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


def history_rows(track_id, action):
    return database.execute(select(TableHistory).where(TableHistory.lidarrTrackId == track_id,
                                                       TableHistory.action == action)).scalars().all()


# ---------------------------------------------------------------------------
# PUT /tracks/<id>
# ---------------------------------------------------------------------------

def test_put_never_clears_and_queues(env):
    add(env, 1, 'Interlude', status='instrumental', instrumental_reason='consensus',
        instrumental_evidence='[{"source": "lrclib"}]')
    r = env['c'].put('/tracks/1', json={'instrumental_override': 'never', 'search': True})
    assert r.status_code == 200
    body = r.get_json()
    assert body['queued'] is True and body['track']['lyrics_status'] == 'missing'
    t = row(1)
    assert (t.lyrics_status, t.instrumental_override, t.instrumental_reason, t.instrumental_prev) == \
        ('missing', 'never', None, None)
    assert env['queued'] == [[1]]
    assert history_rows(1, 6)


def test_put_never_on_available_conflict_row(env):
    add(env, 1, 'Song (Instrumental)', status='available', lrc=L.verse('a'),
        instrumental_evidence='[{"source": "title", "kind": "title", "veto": "sidecar"}]')
    r = env['c'].put('/tracks/1', json={'instrumental_override': 'never'})
    assert r.status_code == 200 and r.get_json()['queued'] is False
    t = row(1)
    assert t.lyrics_status == 'available' and t.instrumental_override == 'never' and t.instrumental_evidence is None


def test_put_always_and_null(env):
    p = add(env, 1, 'Song', status='available', lrc=L.verse('auto'), lyrics_source='auto')
    r = env['c'].put('/tracks/1', json={'instrumental_override': 'always'})
    assert r.status_code == 200
    t = row(1)
    assert (t.lyrics_status, t.instrumental_reason, t.instrumental_override) == ('instrumental', 'user', 'always')
    assert not os.path.exists(L.lrc_path(p))
    v = database.execute(select(TableLyricsVersions).where(TableLyricsVersions.lidarrTrackId == 1)).scalars().all()
    assert [x.replaced_by for x in v] == ['user-instrumental']
    r = env['c'].put('/tracks/1', json={'instrumental_override': None})
    assert r.status_code == 200
    t = row(1)
    assert t.instrumental_override is None and t.lyrics_status == 'instrumental'
    assert env['c'].put('/tracks/1', json={'instrumental_override': 'maybe'}).status_code == 400
    assert env['c'].put('/tracks/1', json=[1]).status_code == 400
    assert env['c'].put('/tracks/999', json={}).status_code == 404


def test_put_always_on_blacklisted_is_allowed(env):
    add(env, 1, 'Song', status='blacklisted')
    r = env['c'].put('/tracks/1', json={'instrumental_override': 'always'})
    assert r.status_code == 200 and row(1).lyrics_status == 'instrumental'


def test_put_always_on_manual_row_needs_force(env):
    p = add(env, 1, 'Song', status='available', lrc=L.verse('mine'), lyrics_source='manual')
    r = env['c'].put('/tracks/1', json={'instrumental_override': 'always'})
    assert r.status_code == 409 and r.get_json()['code'] == 'user_lyrics'
    assert os.path.exists(L.lrc_path(p)) and row(1).lyrics_status == 'available'
    r = env['c'].put('/tracks/1', json={'instrumental_override': 'always', 'force': True})
    assert r.status_code == 200 and not os.path.exists(L.lrc_path(p))
    assert database.execute(select(TableLyricsVersions).where(TableLyricsVersions.lidarrTrackId == 1)).first()


def test_put_always_archive_failure_is_500(env, monkeypatch):
    p = add(env, 1, 'Song', status='available', lrc=L.verse('auto'), lyrics_source='auto')
    monkeypatch.setattr(lyrics_store, '_archive_existing', lambda *a, **k: False)
    r = env['c'].put('/tracks/1', json={'instrumental_override': 'always'})
    assert r.status_code == 500 and r.get_json()['message'] == lyrics_store.PERSIST_REASONS['archive_failed']
    assert os.path.exists(L.lrc_path(p)) and row(1).lyrics_status == 'available'


def test_unskip_keeps_override_and_writes_history(env):
    add(env, 1, 'Song (Instrumental)', status='instrumental', instrumental_reason='title',
        instrumental_override='never')
    r = env['c'].put('/tracks/1', json={'lyrics_status': 'missing'})
    assert r.status_code == 200
    t = row(1)
    assert t.lyrics_status == 'missing' and t.instrumental_override == 'never'
    assert history_rows(1, 6)
    add(env, 2, 'Other', status='blacklisted', instrumental_override='always', is_synced=True)
    assert env['c'].put('/tracks/2', json={'lyrics_status': 'missing'}).status_code == 200
    t = row(2)
    assert (t.lyrics_status, t.instrumental_override, t.is_synced) == ('missing', 'always', False)
    add(env, 3, 'Avail', status='available', lrc=L.verse('x'))
    assert env['c'].put('/tracks/3', json={'lyrics_status': 'missing'}).status_code == 400


def test_language_put_marks_manual(env):
    add(env, 1, 'Song', status='available', lrc=L.verse('x'))
    env['c'].put('/tracks/1', json={'detected_language': 'JA'})
    t = row(1)
    assert (t.detected_language, t.language_source) == ('ja', 'manual')


def test_blacklist_archives_with_origin_attribution(env):
    p = add(env, 1, 'Song', status='available', lrc=L.verse('x'), lyrics_source='auto', lyrics_provider='lrclib')
    r = env['c'].put('/tracks/1', json={'lyrics_status': 'blacklisted'})
    assert r.status_code == 200 and r.get_json()['lyrics_status'] == 'blacklisted'
    assert not os.path.exists(L.lrc_path(p))
    v = database.execute(select(TableLyricsVersions)).scalars().one()
    assert (v.provider, v.replaced_by) == ('lrclib', 'blacklist')
    t = row(1)
    assert t.lyrics_source is None and t.lyrics_provider is None


# ---------------------------------------------------------------------------
# POST /tracks/instrumental
# ---------------------------------------------------------------------------

def test_bulk_confirm_archives_auto_skips_manual_and_dismisses(env):
    pa = add(env, 1, 'Auto', status='available', lrc=L.verse('a'), lyrics_source='auto',
             instrumental_prev='{"why": "legacy-unverified", "actor": "bootstrap", "at": "2026-10-01T00:00:00"}')
    pm = add(env, 2, 'Manual', status='available', lrc=L.verse('m'), lyrics_source='manual',
             instrumental_prev='{"why": "legacy-unverified", "actor": "bootstrap", "at": "2026-10-02T00:00:00"}')
    add(env, 3, 'Cleared', status='missing',
        instrumental_prev='{"why": "weak-title", "actor": "audit", "at": "2026-10-03T00:00:00"}')
    r = env['c'].post('/tracks/instrumental', json={'trackIds': [1, 2, 404], 'action': 'confirm'}).get_json()
    assert r['updated'] == 1
    assert sorted(r['skipped'], key=lambda s: s['trackId']) == [
        {'trackId': 2, 'reason': 'user_lyrics'}, {'trackId': 404, 'reason': 'not_found'}]
    assert not os.path.exists(L.lrc_path(pa)) and os.path.exists(L.lrc_path(pm))
    t = row(1)
    assert (t.lyrics_status, t.instrumental_override, t.instrumental_prev) == ('instrumental', 'always', None)
    r = env['c'].post('/tracks/instrumental', json={'trackIds': [3], 'action': 'dismiss_cleared'}).get_json()
    assert r['updated'] == 1 and row(3).instrumental_prev is None and row(3).lyrics_status == 'missing'
    view = env['c'].get('/wanted/instrumental?view=cleared').get_json()
    assert [d['lidarrTrackId'] for d in view['data']] == [2]


def test_bulk_validation_and_recheck(env):
    c = env['c']
    assert c.post('/tracks/instrumental', json={'trackIds': [], 'action': 'confirm'}).status_code == 400
    assert c.post('/tracks/instrumental', json={'trackIds': [1], 'action': 'nope'}).status_code == 400
    assert c.post('/tracks/instrumental', json={'trackIds': ['1'], 'action': 'confirm'}).status_code == 400
    assert c.post('/tracks/instrumental', json={'trackIds': [1], 'action': 'confirm', 'force': 1}).status_code == 400
    add(env, 1, 'Flagged', status='instrumental', instrumental_reason='consensus', instrumental_override='always')
    add(env, 2, 'Plain', status='missing')
    r = c.post('/tracks/instrumental', json={'trackIds': [1, 2], 'action': 'recheck', 'search': True}).get_json()
    assert r['updated'] == 1 and r['skipped'] == [{'trackId': 2, 'reason': 'not_instrumental'}]
    assert row(1).lyrics_status == 'missing' and row(1).instrumental_override == 'always'
    assert env['queued'] == [[1]] and r['queued'] is True


def test_bulk_over_20_emits_one_event(env):
    for tid in range(1, 26):
        add(env, tid, f'T{tid}', status='instrumental')
    ids = list(range(1, 26))
    r = env['c'].post('/tracks/instrumental', json={'trackIds': ids, 'action': 'not_instrumental'}).get_json()
    assert r['updated'] == 25
    types = [t for t, _ in env['events']]
    assert types.count('instrumental_bulk') == 1 and 'instrumental_classified' not in types
    assert all(row(t).instrumental_override == 'never' for t in ids)
    assert len(history_rows(1, 6)) == 1


# ---------------------------------------------------------------------------
# Views, filters, stats
# ---------------------------------------------------------------------------

def test_wanted_views_and_stats(env):
    add(env, 1, 'Legacy', status='instrumental')
    add(env, 2, 'Titled (Instrumental)', status='instrumental', instrumental_reason='title')
    add(env, 3, 'Hinted', status='missing', instrumental_evidence='[{"source": "lrclib", "kind": "flag"}]')
    add(env, 4, 'Conflict (Instrumental)', status='available', lrc=L.verse('c'),
        instrumental_evidence='[{"source": "title", "kind": "title", "veto": "sidecar"}]')
    add(env, 5, 'Cleared', status='missing',
        instrumental_prev='{"why": "legacy-unverified", "actor": "bootstrap", "at": "2026-10-01T00:00:00"}')
    add(env, 6, 'Cleared Kept Vocal', status='missing', instrumental_override='never',
        instrumental_prev='{"why": "legacy-unverified", "actor": "bootstrap", "at": "2026-10-02T00:00:00"}')
    c = env['c']

    def view(name, extra=''):
        body = c.get(f'/wanted/instrumental?view={name}{extra}').get_json()
        return {d['lidarrTrackId'] for d in body['data']}

    assert view('flagged') == {1, 2}
    assert view('flagged', '&reason=legacy') == {1}
    assert view('legacy') == {1}
    assert view('likely') == {3}
    assert view('conflict') == {4}
    assert view('cleared') == {5}
    assert view('cleared', '&why=weak-title') == set()
    assert view('all') == {1, 2, 3, 4, 5}
    body = c.get('/wanted/instrumental?view=cleared').get_json()
    assert body['data'][0]['albumTitle'] == 'Album' and body['data'][0]['instrumental_prev']['why']
    assert body['data'][0]['albumLyricsStatus'] in ('partial', 'missing', 'complete')
    assert c.get('/wanted/instrumental?view=bogus').status_code == 400
    stats = c.get('/wanted/stats').get_json()
    assert (stats['likely_instrumental'], stats['instrumental_cleared']) == (1, 1)
    assert stats['instrumental_review'] == 1 + 1 + 1 + 1
    summary = c.get('/audit/summary').get_json()
    assert summary['instrumental_cleared'] == 1 and summary['unreachable_roots'] == []

    def tracks_ids(q):
        return {d['lidarrTrackId'] for d in c.get(f'/tracks?{q}').get_json()['data']}

    assert tracks_ids('lyricsStatus=instrumental&instrumentalReason=legacy') == {1}
    assert tracks_ids('instrumentalReason=title') == {2}
    assert tracks_ids('hint=true') == {3}
    assert tracks_ids('cleared=true') == {5, 6}
    assert c.get('/tracks?lyricsStatus=nope').status_code == 400


def test_cleared_view_lists_i1_clears_and_album_status(env):
    import lyrarr.utilities.audit as audit
    add(env, 1, 'Interlude', status='instrumental')
    add(env, 2, 'Only Other', status='instrumental', instrumental_reason='title')
    audit.start_run('apply', ['I-1'], wait=True)
    body = env['c'].get('/wanted/instrumental?view=cleared').get_json()
    assert [d['lidarrTrackId'] for d in body['data']] == [1]
    assert body['data'][0]['albumLyricsStatus'] == 'partial'
    assert body['data'][0]['instrumental_prev']['actor'] == 'audit'
    env['c'].post('/tracks/instrumental', json={'trackIds': [1], 'action': 'confirm'})
    assert env['c'].get('/wanted/instrumental?view=cleared').get_json()['total'] == 0
    albums_status = wanted.album_lyrics_status_map([10])
    assert albums_status[10] == 'complete'


def test_history_labels_and_filters(env):
    add(env, 1, 'Song', status='instrumental')
    lyrics_store.clear_instrumental(1, why='user-recheck', actor='user')
    L.add_history(1, 10, 1, action=5, provider='lrclib-publish', description='Published lyrics for Song')
    body = env['c'].get('/history?action=5,6').get_json()
    assert {(h['action'], h['actionLabel']) for h in body} == {(5, 'Published'), (6, 'Classified')}
    assert isinstance(next(h for h in body if h['action'] == 6)['details'], dict)
    assert env['c'].get('/history?action=x').status_code == 400
    assert env['c'].get('/history?trackId=1&type=lyrics').get_json()


# ---------------------------------------------------------------------------
# Batch delete, instrumental mode
# ---------------------------------------------------------------------------

def test_batch_delete_instrumental_mode_keeps_real_lyrics(env):
    real = add(env, 1, 'Real', status='instrumental', lrc=L.verse('real'))
    marker = add(env, 2, 'Marker', status='instrumental', lrc='[Instrumental]\n')
    strong = add(env, 3, 'Song (Instrumental)', status='instrumental', instrumental_reason='title',
                 lrc=L.verse('strong'))
    never = add(env, 4, 'Never', status='instrumental', instrumental_override='never', lrc='[Instrumental]')
    with open(os.path.splitext(real)[0] + '.txt', 'w') as f:
        f.write('old')
    r = env['c'].post('/metadata/lyrics/batch-delete', json={'albumIds': [10], 'mode': 'instrumental'}).get_json()
    assert (r['deleted'], r['unflagged'], r['kept'], r['txt_sidecars']) == (2, 1, 1, 1)
    assert os.path.exists(L.lrc_path(real)) and row(1).lyrics_status == 'available'
    assert not os.path.exists(L.lrc_path(marker)) and row(2).lyrics_status == 'instrumental'
    assert not os.path.exists(L.lrc_path(strong)) and row(3).lyrics_status == 'instrumental'
    assert os.path.exists(L.lrc_path(never))
    v = database.execute(select(TableLyricsVersions).where(TableLyricsVersions.lidarrTrackId == 2)).scalars().one()
    assert v.replaced_by == 'batch-delete'


def test_batch_delete_keeps_terminal_states(env):
    add(env, 1, 'Skipped', status='blacklisted', lrc=L.verse('s'))
    add(env, 2, 'Avail', status='available', lrc=L.verse('a'), lyrics_source='auto', lyrics_retry_count=3)
    r = env['c'].post('/metadata/lyrics/batch-delete', json={'albumIds': [10]}).get_json()
    assert r['deleted'] == 2
    assert row(1).lyrics_status == 'blacklisted'
    t = row(2)
    assert (t.lyrics_status, t.lyrics_source, t.lyrics_retry_count) == ('missing', None, 0)
    assert t.lyrics_retry_set_at is not None


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def test_non_json_posts_are_refused():
    from lyrarr.api import api_bp
    from lyrarr.app.app import create_app
    from lyrarr.app.config import normalize_base_url, settings
    app = create_app()
    app.testing = True
    prefix = normalize_base_url(settings.general.base_url) + '/api'
    app.register_blueprint(api_bp, url_prefix=prefix)
    c = app.test_client()
    for path in ('/tracks/instrumental', '/audit/runs', '/audit/ignore', '/audit/runs/1/apply'):
        r = c.post(prefix + path, data='trackIds=1', content_type='application/x-www-form-urlencoded')
        assert r.status_code == 403, path
    r = c.put(prefix + '/tracks/1', data='x=1', content_type='text/plain')
    assert r.status_code == 403
