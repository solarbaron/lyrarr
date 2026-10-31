"""Daily maintenance (lyrarr/utilities/maintenance.py): reconcile incl.
instrumental rows, per-root reachability, tombstone purge and history pruning
that keeps rows pinned as audit evidence. Synthetic lyrics only."""

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-maint-'), '--no-tasks', '--no-signalr']
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from lyrarr.app.get_args import args as _args  # noqa: E402

_REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
if os.path.abspath(_args.config_dir) == os.path.abspath(_REPO_DATA):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from sqlalchemy import insert  # noqa: E402

from lyrarr.app.config import settings  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableAuditIssues,
    TableAuditPins,
    TableAuditRuns,
    TableHistory,
    TableLibraryRoots,
    TableLyricsVersions,
    TableTracks,
    TableTrackTombstones,
    database,
    delete,
    init_db,
    select,
    update,
)
from lyrarr.metadata import lyrics_store  # noqa: E402
from lyrarr.utilities import maintenance  # noqa: E402

init_db()

VERSE = "first line of the verse\nsecond line of the verse\nla la placeholder chorus"
_TABLES = (TableAuditPins, TableAuditIssues, TableAuditRuns, TableLyricsVersions, TableHistory,
           TableTrackTombstones, TableTracks, TableAlbums, TableArtists, TableLibraryRoots)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    for table in _TABLES:
        database.execute(delete(table))
    monkeypatch.setattr(lyrics_store, 'detect_language', lambda text: 'en')
    rows = {}
    tid = 100
    for n in (1, 2):
        root = tmp_path / f'm{n}'
        album_dir = root / 'Artist' / 'Album'
        album_dir.mkdir(parents=True)
        database.execute(insert(TableLibraryRoots).values(path=str(root), source='lidarr'))
        database.execute(insert(TableArtists).values(lidarrArtistId=n, name=f'A{n}', path=str(root / 'Artist')))
        database.execute(insert(TableAlbums).values(lidarrAlbumId=n * 10, artistId=n, title='Album',
                                                    path=str(album_dir)))
        for name in ('a', 'b', 'c'):
            audio = album_dir / f'{name}.flac'
            audio.write_bytes(b'audio')
            database.execute(insert(TableTracks).values(
                lidarrTrackId=tid, albumId=n * 10, artistId=n, title=f'Song {name}', path=str(audio),
                lyrics_status='missing'))
            rows[(n, name)] = (tid, str(audio)[:-5] + '.lrc')
            tid += 1
    yield {'rows': rows, 'tmp': tmp_path}
    for table in _TABLES:
        database.execute(delete(table))
    database.remove()


def _row(track_id):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


def _set(track_id, **values):
    database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == track_id).values(**values))


def test_instrumental_with_real_lyrics_adopted_strong_title_stays(lib):
    a, a_lrc = lib['rows'][(1, 'a')]
    b, b_lrc = lib['rows'][(1, 'b')]
    for path in (a_lrc, b_lrc):
        open(path, 'w').write(VERSE)
    _set(a, lyrics_status='instrumental', title='Interlude')
    _set(b, lyrics_status='instrumental', instrumental_reason='title', title='Song (Instrumental)')
    maintenance.run_maintenance()
    assert _row(a).lyrics_status == 'available'
    assert _row(b).lyrics_status == 'instrumental' and _row(b).instrumental_evidence is None


def test_available_strong_title_gets_conflict_entry(lib):
    a, a_lrc = lib['rows'][(1, 'a')]
    open(a_lrc, 'w').write(VERSE)
    _set(a, lyrics_status='available', title='Song (Karaoke Version)')
    maintenance.run_maintenance()
    assert _row(a).to_dict()['instrumental_evidence'][0]['veto'] == 'sidecar'


def test_unreachable_root_untouched(lib):
    m1_a, _ = lib['rows'][(1, 'a')]
    m2_a, _ = lib['rows'][(2, 'a')]
    _set(m1_a, lyrics_status='available')
    _set(m2_a, lyrics_status='available')
    os.rename(lib['tmp'] / 'm2', lib['tmp'] / 'm2-away')
    maintenance.run_maintenance()
    assert _row(m1_a).lyrics_status == 'missing'  # its .lrc was deleted, audio present
    assert _row(m2_a).lyrics_status == 'available'  # root unmounted: left alone
    assert not (lib['tmp'] / 'm2').exists()


def test_tombstone_purge(lib):
    for days in (1, 31):
        database.execute(insert(TableTrackTombstones).values(
            lidarrAlbumId=10, path_stem=f'/x/{days}', lidarrTrackId=days, state='{}',
            removed_at=datetime.now() - timedelta(days=days)))
    maintenance.run_maintenance()
    left = database.execute(select(TableTrackTombstones.lidarrTrackId)).scalars().all()
    assert left == [1]


def test_prune_history_keeps_pinned_rows(lib, monkeypatch):
    monkeypatch.setattr(settings.general, 'history_retention_days', 30)
    old = datetime.now() - timedelta(days=85)
    ids = []
    for i in range(3):
        database.execute(insert(TableHistory).values(action=4, description=f'old {i}', metadata_type='lyrics',
                                                     provider='editor', lidarrTrackId=100, timestamp=old))
    ids = database.execute(select(TableHistory.id).order_by(TableHistory.id)).scalars().all()
    database.execute(insert(TableHistory).values(action=1, description='new', metadata_type='lyrics',
                                                 lidarrTrackId=100, timestamp=datetime.now()))
    database.execute(insert(TableAuditRuns).values(id=1, mode='apply', trigger='bootstrap', status='done'))
    database.execute(insert(TableAuditIssues).values(id=1, run_id=1, check_id='history.curated_superseded',
                                                     entity_key='track:100', status='open'))
    database.execute(insert(TableAuditPins).values(issue_id=1, kind='history', ref_id=ids[0]))
    maintenance._prune_history()
    left = database.execute(select(TableHistory.description).order_by(TableHistory.id)).scalars().all()
    assert left == ['old 0', 'new']
    # Once the issue is resolved the pin no longer protects it.
    database.execute(update(TableAuditIssues).values(status='fixed'))
    maintenance._prune_history()
    assert database.execute(select(TableHistory.description)).scalars().all() == ['new']
