"""Per-library-root reachability (PLAN §1.3, §3.4 writes-to-unreachable-library).

Two temp roots /m1 and /m2; /m2 is renamed away ("unmounted"). Reachability
is judged per root, sync only changes lyrics/cover state under the reachable
root, and a root without tracks is fine as long as it exists.
"""

import os
import sys
import tempfile

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-reach-'), '--no-tasks', '--no-signalr']
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from lyrarr.app.get_args import args as _args  # noqa: E402

_REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
if os.path.abspath(_args.config_dir) == os.path.abspath(_REPO_DATA):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from sqlalchemy import insert  # noqa: E402

import lyrarr.lidarr.sync as sync  # noqa: E402
from lyrarr.app.config import settings  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableBlacklist,
    TableHistory,
    TableLibraryRoots,
    TableLyricsVersions,
    TableTracks,
    TableTrackTombstones,
    database,
    delete,
    init_db,
    select,
)
from lyrarr.lidarr.api_client import lidarr_api  # noqa: E402
from lyrarr.metadata import lyrics_store  # noqa: E402

init_db()

VERSE = "first line of the verse\nsecond line of the verse\nla la placeholder chorus"
_TABLES = (TableLyricsVersions, TableBlacklist, TableHistory, TableTrackTombstones, TableTracks, TableAlbums,
           TableArtists, TableLibraryRoots)


@pytest.fixture
def two_roots(tmp_path, monkeypatch):
    for table in _TABLES:
        database.execute(delete(table))
    monkeypatch.setattr(settings.general, 'use_lidarr', True)
    monkeypatch.setattr(settings.lidarr, 'only_monitored', False)
    monkeypatch.setattr(settings.general, 'default_profile_id', None)
    monkeypatch.setattr(sync, 'event_stream', lambda *a, **k: None)
    monkeypatch.setattr(sync, '_detect', lambda content: 'en')

    roots, artists, albums, files, records = {}, [], [], [], []
    for n in (1, 2):
        root = tmp_path / f'm{n}'
        album_dir = root / f'Artist{n}' / 'Album'
        album_dir.mkdir(parents=True)
        roots[n] = root
        artists.append({'id': n, 'artistName': f'Artist{n}', 'path': str(root / f'Artist{n}'), 'monitored': True})
        albums.append({'id': n * 10, 'artistId': n, 'title': f'Album{n}', 'monitored': True})
        for i in range(3):
            audio = album_dir / f'0{i + 1} - t.flac'
            audio.write_bytes(b'audio')
            (album_dir / f'0{i + 1} - t.lrc').write_text(VERSE)
            fid = n * 100 + i
            files.append({'id': fid, 'albumId': n * 10, 'artistId': n, 'path': str(audio)})
            records.append({'id': fid + 5000, 'trackFileId': fid, 'albumId': n * 10, 'artistId': n,
                            'title': f'Song {i}', 'duration': 180_000})
        (album_dir / 'cover.jpg').write_bytes(b'\xff\xd8\xff')

    monkeypatch.setattr(lidarr_api, 'get_root_folders', lambda: [{'path': str(r)} for r in roots.values()])
    monkeypatch.setattr(lidarr_api, 'get_artists', lambda: artists)
    monkeypatch.setattr(lidarr_api, 'get_albums', lambda artist_id=None: [
        a for a in albums if artist_id is None or a['artistId'] == artist_id])
    monkeypatch.setattr(lidarr_api, 'get_tracks', lambda album_id=None, artist_id=None: [
        f for f in files if f['artistId'] == artist_id])
    monkeypatch.setattr(lidarr_api, 'get_track_records', lambda album_id=None, artist_id=None: [
        r for r in records if r['artistId'] == artist_id])
    yield {'roots': roots, 'tmp': tmp_path}
    for table in _TABLES:
        database.execute(delete(table))
    database.remove()


def _statuses(artist_id):
    database.remove()
    return [t.lyrics_status for t in database.execute(
        select(TableTracks).where(TableTracks.artistId == artist_id).order_by(TableTracks.lidarrTrackId)
    ).scalars()]


def _cover(album_id):
    database.remove()
    return database.execute(select(TableAlbums.cover_status).where(TableAlbums.lidarrAlbumId == album_id)).scalar()


def test_ok_for_per_root(two_roots):
    sync.update_artists()
    m1, m2 = two_roots['roots'][1], two_roots['roots'][2]
    reach = lyrics_store.library_reachability()
    assert set(reach.roots) == {str(m1), str(m2)} and reach.all_ok
    os.rename(m2, two_roots['tmp'] / 'm2-away')
    reach = lyrics_store.library_reachability()
    assert reach.ok_for(str(m1 / 'Artist1' / 'Album' / '01 - t.flac')) is True
    assert reach.ok_for(str(m2 / 'Artist2' / 'Album' / '01 - t.flac')) is False
    assert reach.ok_for('/somewhere/else/x.flac') is True  # outside every root: per-track check decides
    assert [s.root for s in reach.unreachable] == [str(m2)]
    assert reach.any_ok and not reach.all_ok
    agg = lyrics_store.library_reachable()
    assert agg.ok is False and agg.root is None


def test_sync_updates_reachable_root_and_keeps_the_other(two_roots):
    sync.update_artists()
    assert _statuses(1) == ['available'] * 3 and _statuses(2) == ['available'] * 3
    assert _cover(10) == 'available' and _cover(20) == 'available'
    m1, m2 = two_roots['roots'][1], two_roots['roots'][2]
    os.remove(m1 / 'Artist1' / 'Album' / '01 - t.lrc')
    os.remove(m1 / 'Artist1' / 'Album' / 'cover.jpg')
    os.rename(m2, two_roots['tmp'] / 'm2-away')
    sync.update_artists()
    assert _statuses(1) == ['missing', 'available', 'available']
    assert _statuses(2) == ['available'] * 3
    assert _cover(10) == 'missing' and _cover(20) == 'available'
    assert not m2.exists()  # nothing was created under the unmounted root


def test_root_without_tracks_is_ok_when_it_exists(two_roots):
    database.execute(insert(TableLibraryRoots).values(path=str(two_roots['tmp'] / 'empty-root'), source='lidarr'))
    (two_roots['tmp'] / 'empty-root').mkdir()
    reach = lyrics_store.library_reachability()
    st = reach.roots[str(two_roots['tmp'] / 'empty-root')]
    assert st.ok and st.exists and st.checked == 0


def test_derived_roots_without_lidarr(two_roots):
    database.execute(insert(TableArtists).values(lidarrArtistId=9, name='X', path=str(two_roots['tmp'] / 'm3' / 'X')))
    assert lyrics_store.library_roots() == [str(two_roots['tmp'] / 'm3')]
    reach = lyrics_store.library_reachability()
    assert reach.roots[str(two_roots['tmp'] / 'm3')].ok is False  # folder doesn't exist


def test_half_missing_root_is_not_ok(two_roots):
    sync.update_artists()
    m1 = two_roots['roots'][1]
    os.remove(m1 / 'Artist1' / 'Album' / '01 - t.flac')
    reach = lyrics_store.library_reachability(max_missing_ratio=0.2)
    st = reach.roots[str(m1)]
    assert st.exists and st.checked == 3 and st.missing == 1 and not st.ok
    assert lyrics_store.library_reachability(max_missing_ratio=0.5).roots[str(m1)].ok


def test_unmounted_root_reports_its_tracks_as_checked(two_roots):
    sync.update_artists()
    m2 = two_roots['roots'][2]
    os.rename(m2, two_roots['tmp'] / 'm2-away')
    st = lyrics_store.library_reachability().roots[str(m2)]
    assert not st.exists and not st.ok and st.checked == 3 and st.missing == 3
    assert st.sample_path and st.sample_path.startswith(str(m2))


def test_unused_unmounted_root_is_informational(two_roots, monkeypatch, caplog):
    import logging

    from lyrarr.utilities import health
    events = []
    import lyrarr.app.event_handler as eh
    monkeypatch.setattr(eh, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    monkeypatch.setattr(health, '_last_state', {})
    monkeypatch.setattr(lyrics_store, '_root_log_state', {})
    unused = str(two_roots['tmp'] / 'second-lidarr-root')  # not mounted into the container, no tracks
    database.execute(insert(TableLibraryRoots).values(path=unused, source='lidarr'))
    with caplog.at_level(logging.WARNING, logger='lyrarr.metadata.lyrics_store'):
        reach = lyrics_store.library_reachability()
    st = reach.roots[unused]
    assert not st.exists and st.checked == 0 and not st.ok
    assert not any(unused in r.getMessage() for r in caplog.records)
    assert health._check_library_roots(reach) == {} and events == []


def test_unreachable_root_warning_is_rate_limited(two_roots, monkeypatch, caplog):
    import logging
    sync.update_artists()
    monkeypatch.setattr(lyrics_store, '_root_log_state', {})
    m2 = two_roots['roots'][2]
    os.rename(m2, two_roots['tmp'] / 'm2-away')

    def warnings():
        return [r for r in caplog.records if r.levelno == logging.WARNING and str(m2) in r.getMessage()]

    with caplog.at_level(logging.WARNING, logger='lyrarr.metadata.lyrics_store'):
        lyrics_store.library_reachability()
        lyrics_store.library_reachability()  # health polling a minute later
        assert len(warnings()) == 1
        lyrics_store._root_log_state[str(m2)] -= lyrics_store._ROOT_WARN_INTERVAL_S
        lyrics_store.library_reachability()
        assert len(warnings()) == 2
