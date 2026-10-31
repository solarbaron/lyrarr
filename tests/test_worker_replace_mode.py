"""Replace mode (PLAN §2.8): an audit "re-search" on an 'available' row.

The worker re-searches the row and replaces a NULL/auto file only with
something acceptable; otherwise it keeps the file and clears the request.
Never classifies, never adopts, never deletes. All lyrics are synthetic.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-replace-mode-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from sqlalchemy import insert  # noqa: E402

from lyrarr.app.database import TableLyricsVersions, TableTracks, database, select  # noqa: E402
from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.metadata import lyrics_store  # noqa: E402
from lyrarr.metadata.provider_utils import ProviderTransientError  # noqa: E402
from tests import test_metadata_worker as _worker_tests  # noqa: E402
from tests.test_metadata_worker import (  # noqa: E402
    ENGLISH,
    OTHER,
    SYNCED,
    FakeProvider,
    _history,
    _read,
    _set_track,
    _track,
    _write,
    hint,
    marker,
    mk,
)

env = _worker_tests.env  # the shared worker fixture


def _request(env, text=ENGLISH, source='auto', track_id=100):
    if track_id == 100:
        _write(env['lrc'], text)
    _set_track(track_id, lyrics_status='available', lyrics_source=source, hasLyrics=True,
               lyrics_content_hash=lyrics_store.content_hash(text))
    assert lyrics_store.request_replace(track_id, 'M-1')


def _replace_history(result):
    return [h for h in _history(action=7)
            if (h.details or '') and f'"replace_result": "{result}"' in h.details]


def test_nothing_found_keeps_file_and_clears_flag(env):
    _request(env)
    env['providers']['p1'] = FakeProvider('p1', [])
    assert w.download_missing_lyrics() == 0
    t = _track()
    assert t.lyrics_status == 'available' and t.lyrics_replace_requested is None
    assert _read(env['lrc']) == ENGLISH
    assert len(_replace_history('kept')) == 1


def test_acceptable_candidate_replaces_and_archives(env):
    _request(env, text=OTHER)
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', synced=SYNCED)])
    assert w.download_missing_lyrics() == 1
    t = _track()
    assert t.lyrics_replace_requested is None and t.lyrics_source == 'auto' and t.is_synced
    assert _read(env['lrc']).startswith('[00:10.00]')
    versions = database.execute(select(TableLyricsVersions)).scalars().all()
    assert [v.content for v in versions] == [OTHER]
    assert len(_replace_history('replaced')) == 1


def test_equivalent_candidate_only_stamps_provenance(env):
    _request(env)
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', plain=ENGLISH)])
    w.download_missing_lyrics()
    t = _track()
    assert t.lyrics_replace_requested is None and t.lyrics_provider == 'p1'
    assert _read(env['lrc']) == ENGLISH
    assert not database.execute(select(TableLyricsVersions)).scalars().all()


def test_transient_keeps_the_request(env):
    _request(env)
    env['providers']['p1'] = FakeProvider('p1', exc=ProviderTransientError('503'))
    w.download_missing_lyrics()
    t = _track()
    assert t.lyrics_replace_requested is not None
    assert t.lyrics_retry_after < datetime.now() + timedelta(hours=1)
    assert _read(env['lrc']) == ENGLISH


def test_never_classifies_instrumental(env):
    _request(env)
    env['providers']['p1'] = FakeProvider('p1', [marker('p1')])
    env['providers']['p2'] = FakeProvider('p2', [hint('p2')])
    w.download_missing_lyrics()
    t = _track()
    assert t.lyrics_status == 'available' and t.instrumental_evidence is None
    assert _read(env['lrc']) == ENGLISH


def test_manual_rows_are_never_selected(env):
    _write(env['lrc'], ENGLISH)
    _set_track(lyrics_status='available', lyrics_source='manual', lyrics_replace_requested=datetime.now())
    env['providers']['p1'] = p1 = FakeProvider('p1', [mk('p1', synced=SYNCED)])
    w.download_missing_lyrics()
    assert p1.calls == 0 and _read(env['lrc']) == ENGLISH


def test_source_changed_mid_run_is_not_overwritten(env):
    _request(env)

    def user_saves():
        _set_track(lyrics_source='manual')
    env['providers']['p1'] = FakeProvider('p1', [mk('p1', synced=SYNCED)], on_search=user_saves)
    assert w.download_missing_lyrics() == 0
    assert _read(env['lrc']) == ENGLISH


def test_staggered_request_waits_for_its_time(env):
    _write(env['lrc'], ENGLISH)
    _set_track(lyrics_status='available', lyrics_source='auto')
    assert lyrics_store.request_replace(100, 'M-1', retry_after=datetime.now() + timedelta(hours=3))
    env['providers']['p1'] = p1 = FakeProvider('p1', [])
    w.download_missing_lyrics()
    assert p1.calls == 0
    w.download_missing_lyrics(track_ids=[100])
    assert p1.calls == 1


def test_replace_per_pass_cap_oldest_first(env):
    album_dir = env['album_dir']
    base = datetime.now() - timedelta(days=1)
    n = w.REPLACE_PER_PASS + 10
    for i in range(n):
        tid = 1000 + i
        audio = album_dir / f'{i:03d} Track.flac'
        audio.write_bytes(b'audio')
        _write(str(audio)[:-5] + '.lrc', ENGLISH)
        database.execute(insert(TableTracks).values(
            lidarrTrackId=tid, albumId=10, artistId=1, title='Song', duration=200, path=str(audio),
            lyrics_status='available', lyrics_source='auto', track_artist='Artist',
            lyrics_replace_requested=base + timedelta(minutes=n - i)))  # higher ids requested earlier
    _set_track(lyrics_status='available', lyrics_source='auto')  # track 100: not requested
    _write(env['lrc'], ENGLISH)
    env['providers']['p1'] = p1 = FakeProvider('p1', [])
    w.download_missing_lyrics()
    assert p1.calls == w.REPLACE_PER_PASS
    left = database.execute(
        select(TableTracks.lidarrTrackId).where(TableTracks.lyrics_replace_requested.is_not(None))
    ).scalars().all()
    assert sorted(left) == list(range(1000, 1010))  # the 10 newest requests wait for the next pass
    assert not os.path.exists(str(album_dir / 'unexpected.lrc'))
