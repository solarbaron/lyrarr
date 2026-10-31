"""Synthetic legacy library for the audit tests (WS-D, PLAN §7.4).

Builds DB rows (in the test DB the importing module configured) plus two
temp music roots (<tmp>/music1, <tmp>/music2, registered in
table_library_roots) with tiny audio files and lyrics sidecars that
reproduce the bad state older versions left behind. All lyrics text is
synthetic ("line N of the verse", "la la placeholder").
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta


def configure(prefix='lyrarr-test-audit-'):
    """Point lyrarr at a throwaway config dir unless a test module already did.
    Returns False when the config resolved to the repo's data/ dir."""
    if 'lyrarr.app.get_args' not in sys.modules:
        saved = sys.argv
        sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix=prefix), '--no-tasks', '--no-signalr']
        try:
            import lyrarr.app.get_args  # noqa: F401
        finally:
            sys.argv = saved
    from lyrarr.app.get_args import args
    repo_data = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
    return os.path.abspath(args.config_dir) != os.path.abspath(repo_data)


# ---------------------------------------------------------------------------
# Synthetic text
# ---------------------------------------------------------------------------

def verse(tag='first', n=8):
    return '\n'.join(f'{tag} line number {i} of the verse' for i in range(1, n + 1))


def other_verse(n=8):
    """Synthetic lines with no words in common with verse()."""
    return '\n'.join(f'humming placeholder melody goes round {i} times' for i in range(1, n + 1))


def synced(text, start=10.0, step=5.0):
    out = []
    t = start
    for line in text.splitlines():
        m, s = divmod(t, 60)
        out.append(f'[{int(m):02d}:{s:05.2f}]{line}')
        t += step
    return '\n'.join(out)


AUDIO_BYTES = b'fLaC\x00\x00\x00\x22' + b'\x00' * 40


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

AUDIT_TABLES = ('TableAuditPins', 'TableAuditIssues', 'TableAuditIgnores', 'TableAuditRuns')


def reset_db():
    from lyrarr.app import database as db
    from lyrarr.app.database import database, delete, text
    for name in AUDIT_TABLES + ('TableLyricsVersions', 'TableBlacklist', 'TableHistory', 'TableTrackTombstones',
                                'TableTracks', 'TableAlbums', 'TableArtists', 'TableLibraryRoots'):
        database.execute(delete(getattr(db, name)))
    database.execute(text("DELETE FROM table_data_migrations WHERE id = 'audit-bootstrap-v1'"))
    profiles = database.execute(text('SELECT id FROM table_profiles ORDER BY id')).scalars().all()
    if profiles:
        database.execute(text('UPDATE table_profiles SET is_default = 0'))
        database.execute(text('UPDATE table_profiles SET is_default = 1 WHERE id = :id'), {'id': profiles[0]})
        database.execute(text("DELETE FROM table_profiles WHERE name LIKE 'Audit fixture%'"))


def add_root(path):
    from sqlalchemy import insert

    from lyrarr.app.database import TableLibraryRoots, database
    database.execute(insert(TableLibraryRoots).values(path=os.path.normpath(path), source='lidarr',
                                                      updated_at=datetime.now()))


def add_artist(artist_id, name, path):
    from sqlalchemy import insert

    from lyrarr.app.database import TableArtists, database
    os.makedirs(path, exist_ok=True)
    database.execute(insert(TableArtists).values(lidarrArtistId=artist_id, name=name, sortName=name, path=path,
                                                 created_at_timestamp=datetime.now()))


def add_album(album_id, artist_id, title, path, cover_status='missing'):
    from sqlalchemy import insert

    from lyrarr.app.database import TableAlbums, database
    os.makedirs(path, exist_ok=True)
    database.execute(insert(TableAlbums).values(lidarrAlbumId=album_id, artistId=artist_id, title=title,
                                                path=path, cover_status=cover_status, lyrics_status='unknown'))


def add_track(track_id, album_dir, album_id, artist_id, title, *, status='missing', lrc=None, lrc_bytes=None,
              audio=True, stem=None, **cols):
    """Insert a track row; write its audio file and optional sidecar."""
    from sqlalchemy import insert

    from lyrarr.app.database import TableTracks, database
    stem = stem or f'{track_id:03d} {title}'.replace('/', '_').replace('?', '')
    path = os.path.join(album_dir, stem + '.flac')
    if audio:
        with open(path, 'wb') as f:
            f.write(AUDIO_BYTES)
    if lrc is not None or lrc_bytes is not None:
        with open(os.path.splitext(path)[0] + '.lrc', 'wb') as f:
            f.write(lrc_bytes if lrc_bytes is not None else lrc.encode('utf-8'))
    values = dict(lidarrTrackId=track_id, albumId=album_id, artistId=artist_id, title=title, path=path,
                  trackNumber=track_id % 100, discNumber=1, duration=200, lyrics_status=status,
                  hasLyrics=status in ('available', 'manual'), is_synced=False,
                  created_at_timestamp=datetime.now(), updated_at_timestamp=datetime.now())
    values.update(cols)
    database.execute(insert(TableTracks).values(**values))
    return path


def lrc_path(audio_path):
    return os.path.splitext(audio_path)[0] + '.lrc'


def add_history(track_id, album_id, artist_id, *, action=1, provider='lrclib', description=None, when=None,
                metadata_path=None, metadata_type='lyrics'):
    from sqlalchemy.dialects.sqlite import insert

    from lyrarr.app.database import TableHistory, database
    res = database.execute(insert(TableHistory).values(
        action=action, description=description or f'Downloaded lyrics for track {track_id}',
        metadata_type=metadata_type, provider=provider, lidarrTrackId=track_id, lidarrAlbumId=album_id,
        lidarrArtistId=artist_id, timestamp=when or datetime.now(), metadata_path=metadata_path))
    return res.inserted_primary_key[0]


def add_version(track_id, content, *, provider='lrclib', replaced_by=None, when=None, lyrics_type=None,
                translated_from=None):
    from sqlalchemy.dialects.sqlite import insert

    from lyrarr.app.database import TableLyricsVersions, database
    from lyrarr.metadata.language_detect import is_synced_lyrics
    res = database.execute(insert(TableLyricsVersions).values(
        lidarrTrackId=track_id, content=content,
        lyrics_type=lyrics_type or ('synced' if is_synced_lyrics(content) else 'plain'),
        provider=provider, replaced_by=replaced_by, translated_from=translated_from,
        timestamp=when or datetime.now()))
    return res.inserted_primary_key[0]


def set_mtime(path, when):
    ts = when.timestamp()
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# The full legacy library
# ---------------------------------------------------------------------------

@dataclass
class LegacyLibrary:
    root: str
    music1: str
    music2: str
    paths: dict = field(default_factory=dict)        # track id -> audio path
    extra: dict = field(default_factory=dict)        # named files / ids

    def lrc(self, track_id):
        return lrc_path(self.paths[track_id])


# track id → title, for readable assertions
CREDITS = '作词 : Someone\n作曲 : Someone Else\n' + verse('credit', 6)


def build(tmp):
    """Write the full §7.4 fixture under `tmp` and return a LegacyLibrary."""
    from sqlalchemy import text

    from lyrarr.app.database import database

    reset_db()
    tmp = str(tmp)
    m1, m2 = os.path.join(tmp, 'music1'), os.path.join(tmp, 'music2')
    lib = LegacyLibrary(root=tmp, music1=m1, music2=m2)
    add_root(m1)
    add_root(m2)
    now = datetime.now()

    a1, a2, a3 = os.path.join(m1, 'Artist One'), os.path.join(m1, 'Artist Two'), os.path.join(m2, 'Artist Three')
    add_artist(1, 'Artist One', a1)
    add_artist(2, 'Artist Two', a2)
    add_artist(3, 'Artist Three', a3)
    d10, d11, d12 = (os.path.join(a1, n) for n in ('First Album', 'Second Album', 'Third Album'))
    d13, d20 = os.path.join(a2, 'Fourth Album'), os.path.join(a3, 'Album Twenty')
    add_album(10, 1, 'First Album', d10)
    add_album(11, 1, 'Second Album', d11)
    add_album(12, 1, 'Third Album', d12)
    add_album(13, 2, 'Fourth Album', d13, cover_status='available')
    add_album(20, 3, 'Album Twenty', d20)
    P = lib.paths

    def t(track_id, album_dir, album_id, artist_id, title, **kw):
        P[track_id] = add_track(track_id, album_dir, album_id, artist_id, title, **kw)
        return P[track_id]

    # --- instrumental (album 10) --------------------------------------------
    t(101, d10, 10, 1, 'For Free? (interlude)', status='instrumental', duration=130000)
    t(102, d10, 10, 1, 'Skit #3', status='instrumental', duration=24)
    t(103, d10, 10, 1, 'Intro', status='instrumental', duration=None)
    t(104, d10, 10, 1, 'Song One (Instrumental)', status='instrumental')
    t(105, d10, 10, 1, 'Real Song', status='instrumental', lrc=verse('real'))
    t(106, d10, 10, 1, 'The Instrumental', status='instrumental', instrumental_reason='title')
    t(107, d10, 10, 1, 'Song (Instrumental Intro)', status='instrumental', instrumental_reason='title')
    t(108, d10, 10, 1, 'Deleted Song', status='instrumental')
    lib.extra['v108'] = add_version(108, verse('deleted'), provider='batch-delete', when=now - timedelta(days=40))
    t(109, d10, 10, 1, 'Another Deleted', status='missing')
    lib.extra['v109'] = add_version(109, verse('another'), provider='lrclib', replaced_by='batch-delete',
                                    when=now - timedelta(days=5))

    # --- available NULL-source files (album 11) -------------------------------
    t(111, d11, 11, 1, 'Marker Track', status='available', lrc='[au: instrumental]\n')
    t(112, d11, 11, 1, 'Empty Track', status='available', lrc='')
    t(113, d11, 11, 1, 'Bom Track', status='available', lrc_bytes=b'\xef\xbb\xbf' + verse('bom').encode())
    t(114, d11, 11, 1, 'Utf16 Track', status='available', lrc_bytes=verse('wide').encode('utf-16'))
    t(115, d11, 11, 1, 'Cp1252 Track', status='available',
      lrc_bytes=verse('caf\xe9').encode('cp1252'))
    t(116, d11, 11, 1, 'Junk Track', status='available', is_synced=True,
      lrc='[00:10.00]junk line number 1 of the verse\n[00:12.00] [01:20]Outro words of the song\n'
          '[00:14.00]junk line number 3 of the verse\n[00:16.00]junk line number 4 of the verse')
    excerpt = verse('excerpt', 6) + '\n...'
    t(117, d11, 11, 1, 'Excerpt Track', status='available', lrc=excerpt)
    add_history(117, 11, 1, provider='musixmatch', description='Downloaded lyrics for Excerpt Track',
                metadata_path=lib.lrc(117), when=now)
    t(118, d11, 11, 1, 'Genius Track', status='available',
      lrc='42 ContributorsTranslationsGenius Track Lyrics[Verse 1]\n' + verse('genius'))
    t(119, d11, 11, 1, 'Sixty Track', status='available', is_synced=True,
      lrc='[00:10.00]sixty line number 1 of the verse\n[00:20.00]sixty line number 2 of the verse\n'
          '[00:60.00]sixty line number 3 of the verse\n[01:10.00]sixty line number 4 of the verse')
    t(120, d11, 11, 1, 'Fake Synced', status='available', is_synced=True,
      lrc='\n'.join(f'[00:00.00]{x}' for x in verse('fake').splitlines()))
    t(121, d11, 11, 1, 'Credits Track', status='available', lrc=CREDITS)
    t(122, d11, 11, 1, 'Overrun Track', status='available', is_synced=True, duration=60,
      lrc=synced(verse('overrun', 10), start=10, step=10))
    shared = verse('shared', 8)
    t(123, d11, 11, 1, 'Shared One', status='available', lrc=shared)
    t(124, d11, 11, 1, 'Totally Different Title', status='available', lrc=shared)

    # --- user files, history evidence (album 12) -----------------------------------
    t(131, d12, 12, 1, 'Manual Bom', status='available', lyrics_source='manual',
      lrc_bytes=b'\xef\xbb\xbf' + verse('manualbom').encode())
    t(132, d12, 12, 1, 'Manual Lrc', status='available', lyrics_source='manual', is_synced=True,
      lrc='[00:30.00]manual line number 1 of the verse\n[00:10.00]manual line number 2 of the verse\n'
          '[00:40.00]manual line number 3 of the verse\n[00:50.00]manual line number 4 of the verse')
    curated = verse('curated', 8)
    t(133, d12, 12, 1, 'Curated Song', status='available', is_synced=True, lrc=synced(curated))
    lib.extra['h133_editor'] = add_history(133, 12, 1, provider='editor', description='Saved lyrics',
                                           when=now - timedelta(days=86))
    lib.extra['h133_lrclib'] = add_history(133, 12, 1, action=2, provider='lrclib',
                                           description='Upgraded lyrics for Curated Song',
                                           when=now - timedelta(days=85), metadata_path=lib.lrc(133))
    lib.extra['v133'] = add_version(133, curated + '\nmy own extra line', provider='lrclib',
                                    when=now - timedelta(days=85))
    plain134 = verse('whisper', 8)
    lines = plain134.splitlines()
    scrambled = lines[4:] + lines[:4]
    t(134, d12, 12, 1, 'Whisper Song', status='available', lyrics_source='generated', is_synced=True,
      lrc=synced('\n'.join(scrambled)))
    for i in range(4):
        add_version(134, verse(f'older{i}', 4), provider='lrclib', when=now - timedelta(days=30 - i))
    lib.extra['v134'] = add_version(134, plain134, provider='batch-sync-generate', when=now - timedelta(days=1))
    t(135, d12, 12, 1, 'Swapped Song', status='available', is_synced=True, lyrics_source='auto',
      lrc=synced(other_verse(8)), lyrics_content_hash='x')
    lib.extra['h135'] = add_history(135, 12, 1, action=2, provider='lrclib',
                                    description='Upgraded lyrics for Swapped Song', when=now - timedelta(days=3))
    lib.extra['v135'] = add_version(135, verse('rightsong', 8), provider='lrclib', replaced_by='lrclib',
                                    when=now - timedelta(days=3))
    t(136, d12, 12, 1, 'Long Edit', status='available', is_synced=True, duration=100,
      lrc=synced(verse('edit', 10), start=18, step=10))

    # --- state (album 13, artist 2) -----------------------------------------------
    t(141, d13, 13, 2, 'Backoff Track', status='missing', duration=245000, lyrics_retry_count=3,
      lyrics_retry_after=now + timedelta(days=20), lyrics_transient_count=2, lyrics_cooldown_count=1)
    t(142, d13, 13, 2, 'Stale Source', status='missing', lyrics_source='auto')
    original = verse('original', 8)
    t(143, d13, 13, 2, 'Translated Noop', status='available', lyrics_source='translated', lrc=original)
    add_version(143, original, provider='lrclib', replaced_by='batch-translate-en', translated_from='en',
                when=now - timedelta(days=2))
    t(144, d13, 13, 2, 'Many Versions', status='available', lyrics_source='auto', lrc=verse('many'),
      lyrics_content_hash='y')
    for i in range(7):
        add_version(144, verse(f'ver{i}', 4), provider='lrclib', replaced_by='lrclib',
                    when=now - timedelta(days=20 - i))
    p145 = t(145, d13, 13, 2, 'Dup Track', status='missing')
    from sqlalchemy import insert as sa_insert

    from lyrarr.app.database import TableTracks
    database.execute(sa_insert(TableTracks).values(lidarrTrackId=146, albumId=13, artistId=2, title='Dup Track',
                                                   path=p145, lyrics_status='missing', hasLyrics=False,
                                                   is_synced=False, duration=200))
    P[146] = p145
    t(147, d13, 13, 2, 'Txt Track', status='missing')
    with open(os.path.splitext(P[147])[0] + '.txt', 'w', encoding='utf-8') as f:
        f.write(verse('txt'))
    lib.extra['orphan'] = os.path.join(d13, 'ghost song.lrc')
    with open(lib.extra['orphan'], 'w', encoding='utf-8') as f:
        f.write(verse('ghost'))
    lib.extra['tmpfile'] = os.path.join(d13, '.x.lrc.lyrarr-tmp')
    with open(lib.extra['tmpfile'], 'w', encoding='utf-8') as f:
        f.write('partial')
    set_mtime(lib.extra['tmpfile'], now - timedelta(hours=2))
    t(148, d13, 13, 2, 'Skipped Track', status='blacklisted', lrc=verse('skipped'))
    t(149, d13, 13, 2, 'Hash Set', status='available', lyrics_source='auto', lrc=verse('hashset'))
    set_mtime(lib.lrc(149), now - timedelta(hours=1))
    add_history(149, 13, 2, provider='lrclib', metadata_path=lib.lrc(149), when=now)
    t(150, d13, 13, 2, 'Hash Downgrade', status='available', lyrics_source='auto', lrc=verse('hashdown'))
    add_history(150, 13, 2, provider='lrclib', metadata_path=lib.lrc(150), when=now - timedelta(days=2))
    t(151, d13, 13, 2, 'Upgrade Backoff', status='available', lyrics_source='auto', lrc=verse('upgrade'),
      lyrics_content_hash='z', upgrade_retry_count=2, upgrade_retry_after=now + timedelta(days=9))
    t(152, d13, 13, 2, 'Gone File', status='available', lyrics_source='auto', lyrics_content_hash='g')
    t(153, d13, 13, 2, 'Found File', status='missing', lrc=verse('found'))
    t(154, d13, 13, 2, 'Theme Song (Instrumental)', status='available', lrc=verse('theme'))
    t(155, d13, 13, 2, 'Edited Elsewhere', status='available', lrc=verse('edited'))
    add_history(155, 13, 2, provider='editor', description='Saved lyrics for Edited Elsewhere',
                metadata_path=lib.lrc(155), when=now - timedelta(days=10))
    t(156, d13, 13, 2, 'Inconsistent Row', status='missing', hasLyrics=True, is_synced=True)
    t(157, d13, 13, 2, 'Text Bool', status='available', lyrics_source='auto', lrc=verse('textbool'),
      lyrics_content_hash='t')
    database.execute(text("UPDATE table_tracks SET hasLyrics = 'True' WHERE lidarrTrackId = 157"))
    database.execute(text(
        "INSERT INTO table_profiles (name, is_default, download_covers, download_lyrics, prefer_synced_lyrics, "
        "overwrite_existing, embed_cover_art) VALUES ('Audit fixture extra default', 1, 1, 1, 1, 0, 0)"))

    # --- second root (artist 3) -----------------------------------------------------
    t(201, d20, 20, 3, 'Interlude', status='instrumental')
    t(202, d20, 20, 3, 'Root2 Marker', status='available', lrc='[Instrumental]\n')
    t(203, d20, 20, 3, 'Root2 Gone', status='available', lyrics_source='auto', lyrics_content_hash='r')
    t(204, d20, 20, 3, 'Root2 Found', status='missing', lrc=verse('roottwo'))
    return lib


# Expected detections of a default dry run on the full fixture (both roots up).
EXPECTED_DRY_RUN = {
    'I-1': {101, 102, 103, 105, 108, 201},
    'I-2': {104},
    'I-5': {106, 107},
    'I-6': {154},
    'I-7': {108, 109},
    'F-1': {152, 153, 203, 204},
    'F-2': {111, 112, 202},
    'F-3': {113, 114, 115, 131},
    'F-5': {116},
    'F-6': {117},
    'F-7': {118},
    'F-8': {121},
    'F-9': {134},
    'F-10': {147},
    'F-13': {120},
    'F-15': {136},
    'M-1': {122},
    'M-2': {123, 124},
    'M-5': {135},
    'H-1': {133},
    'I-3': {105},
    'F-4': {119, 132},
    'S-1': {142},
    'S-2': {155},
    'S-3': {156},
    'S-6': {143},
    'S-13': {148},
    'S-11': {144},
    'C-1': {13},
}
# Non-track entity keys (relative to the fixture root) of the same dry run.
EXPECTED_FILE_KEYS = {
    'F-11': {'music1/Artist Two/Fourth Album/ghost song.lrc'},
    'F-12': {'music1/Artist Two/Fourth Album/.x.lrc.lyrarr-tmp'},
    'S-9': {'music1/Artist Two/Fourth Album/145 Dup Track.flac'},
}
# Track ids under music2.
MUSIC2_TRACKS = {201, 202, 203, 204}


def dump(value):
    return json.dumps(value, default=str, sort_keys=True)
