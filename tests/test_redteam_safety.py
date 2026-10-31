"""Red-team data-safety probes for the audit, bootstrap and storage paths.

Each test asserts the SAFE behaviour. The B-1/M-1/M-2 tests were strict
xfail repros of defects; the defects are fixed and they are now plain
regression tests. The others pin safety properties that held already.

All lyrics are synthetic. No network: the upgrade pass is stopped before it
reaches a provider.
"""

import os
from datetime import datetime, timedelta

import pytest

from tests.fixtures import legacy_library as L

if not L.configure('lyrarr-test-redteam-'):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

import lyrarr.utilities.audit as audit  # noqa: E402
from lyrarr.app import event_handler  # noqa: E402
from lyrarr.app.database import (  # noqa: E402
    TableTracks,
    database,
    init_db,
    select,
)
from lyrarr.metadata import download_worker as w  # noqa: E402
from lyrarr.metadata import lyrics_store as ls  # noqa: E402

init_db()


@pytest.fixture
def lib(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    L.reset_db()
    root = os.path.join(str(tmp_path), 'music')
    L.add_root(root)
    artist = os.path.join(root, 'Artist')
    album = os.path.join(artist, 'Album')
    L.add_artist(1, 'Artist', artist)
    L.add_album(10, 1, 'Album', album)
    yield {'root': root, 'album': album, 'events': events}
    database.remove()


def _row(track_id):
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == track_id)).scalars().first()


class _ReachedProviders(Exception):
    pass


def _upgrade_would_search(monkeypatch, track_id):
    """True when the plain->synced upgrade pass would go on to replace this
    file (it reaches the provider search); False when its hash guard stops it."""
    def boom(*a, **k):
        raise _ReachedProviders()
    monkeypatch.setattr(w, 'collect_lyrics_results', boom)
    row = _row(track_id)
    try:
        w._upgrade_track(row, {}, {}, False, None)
    except _ReachedProviders:
        return True
    return False


# ---------------------------------------------------------------------------
# B-1: audit file rewrites re-stamp lyrics_content_hash unconditionally
# ---------------------------------------------------------------------------

def test_f3_bom_fix_must_not_launder_a_user_edited_auto_file(lib, monkeypatch):
    # lyrarr wrote the original text (hash recorded); the user later corrected
    # the file in an editor that saved it with a UTF-8 BOM (e.g. Notepad).
    lyrarr_text = L.verse('downloaded', 6)
    user_text = L.verse('corrected by the user', 6)
    path = L.add_track(1, lib['album'], 10, 1, 'Edited Song', status='available', lyrics_source='auto',
                       lyrics_content_hash=ls.content_hash(lyrarr_text),
                       lrc_bytes=b'\xef\xbb\xbf' + user_text.encode('utf-8'))
    # The worker's guard sees the edit: the hash no longer matches the file.
    assert ls.content_hash(lyrarr_text) not in ls.candidate_hashes(user_text)

    run_id = audit.start_run('apply', ['F-3'], wait=True)
    assert run_id is not None
    with open(L.lrc_path(path), 'rb') as f:
        assert f.read().decode('utf-8').strip() == user_text  # BOM stripped, text kept

    row = _row(1)
    # SAFE: the stored hash must still describe what lyrarr wrote (or be cleared
    # with the source downgraded), so the user edit stays protected.
    assert row.lyrics_content_hash not in ls.candidate_hashes(user_text) or row.lyrics_source != 'auto'
    assert not _upgrade_would_search(monkeypatch, 1)


def test_f13_rewrite_must_not_hide_a_post_download_edit_from_backfill(lib, monkeypatch):
    # Legacy 'auto' row (written before hashes existed). lyrarr's download is
    # recorded in history; the file was edited by the user a day later.
    downloaded = datetime.now() - timedelta(days=2)
    text = L.verse('the user retimed', 5)
    fake = '\n'.join(f'[00:00.00]{line}' for line in text.splitlines())
    path = L.add_track(2, lib['album'], 10, 1, 'Retimed Song', status='available', lyrics_source='auto',
                       is_synced=True, lrc=fake)
    L.add_history(2, 10, 1, provider='lrclib', when=downloaded, metadata_path=L.lrc_path(path))
    L.set_mtime(L.lrc_path(path), downloaded + timedelta(days=1))

    audit.start_run('apply', ['F-13'], wait=True)
    row = _row(2)
    assert not row.is_synced  # F-13 did convert it to plain
    # SAFE: provenance is still unknown, so the backfill downgrades it.
    assert ls.backfill_content_hash(2) == 'downgraded'


# ---------------------------------------------------------------------------
# M-1: S-14 shrinks a genuinely long track even after a successful Lidarr sync
# ---------------------------------------------------------------------------

def test_bootstrap_keeps_long_track_duration_after_successful_sync(lib, monkeypatch):
    from lyrarr.lidarr import sync
    monkeypatch.setattr(sync, 'run_full_sync', lambda *a, **k: True)  # Lidarr reachable, seconds written
    L.add_track(3, lib['album'], 10, 1, 'Four Hour Continuous Mix', duration=14400)
    run_id = audit.run_bootstrap()
    assert run_id is not None
    assert _row(3).duration == 14400


# ---------------------------------------------------------------------------
# M-2: S-6 demotes a no-op "translation" of the user's own lyrics to 'auto'
# ---------------------------------------------------------------------------

def test_s6_keeps_manual_origin_of_failed_translation(lib, monkeypatch):
    original = L.verse('typed in by the user', 6)
    when = datetime.now() - timedelta(days=3)
    L.add_track(4, lib['album'], 10, 1, 'Own Lyrics', status='available', lyrics_source='translated',
                language_source='translated', lrc=original)
    # The archived pre-translation text: origin 'editor' (1.7 semantics),
    # replaced by the batch translation that failed and saved it unchanged.
    L.add_version(4, original, provider='editor', replaced_by='batch-translate-de', translated_from='en', when=when)
    L.add_history(4, 10, 1, action=4, provider='editor', when=when - timedelta(days=1))
    L.add_history(4, 10, 1, action=4, provider='batch-translate-de', when=when)

    audit.start_run('apply', ['S-6'], wait=True)
    row = _row(4)
    assert row.lyrics_source == 'manual'
    assert not _upgrade_would_search(monkeypatch, 4)


# ---------------------------------------------------------------------------
# Properties that hold today
# ---------------------------------------------------------------------------

def test_unreachable_root_bootstrap_writes_nothing_and_flips_no_available_row(tmp_path, monkeypatch):
    """Bootstrap with one root unmounted: nothing is created under it, no
    available row becomes missing, and the run is not recorded complete."""
    monkeypatch.setattr(event_handler, 'event_stream', lambda type, payload=None: None)
    from lyrarr.lidarr import sync
    monkeypatch.setattr(sync, 'run_full_sync', lambda *a, **k: False)
    L.reset_db()
    up, down = os.path.join(str(tmp_path), 'up'), os.path.join(str(tmp_path), 'down')
    L.add_root(up)
    L.add_root(down)
    L.add_artist(1, 'Up', os.path.join(up, 'Up'))
    L.add_album(10, 1, 'Up Album', os.path.join(up, 'Up', 'A'))
    L.add_artist(2, 'Down', os.path.join(down, 'Down'))
    L.add_album(20, 2, 'Down Album', os.path.join(down, 'Down', 'B'))
    for i in range(1, 6):
        L.add_track(i, os.path.join(up, 'Up', 'A'), 10, 1, f'Up {i}', status='available', lrc=L.verse(f'up{i}'))
    for i in range(11, 16):
        L.add_track(i, os.path.join(down, 'Down', 'B'), 20, 2, f'Down {i}', status='available',
                    lrc=L.verse(f'down{i}'))
    import shutil
    shutil.rmtree(down)  # unmounted
    assert audit.run_bootstrap() is not None
    assert not os.path.exists(down)
    for i in range(11, 16):
        assert _row(i).lyrics_status == 'available'
    from lyrarr.app.database import data_migration_applied
    assert data_migration_applied('audit-bootstrap-v1') is None
