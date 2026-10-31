"""Cover checks C-1..C-3.

Only files lyrarr can prove it wrote (a cover history row with that exact
metadata_path) are ever renamed or deleted; folder.* and unknown files are
never touched, and directories are never deleted.
"""

from __future__ import annotations

import os

from ..registry import Check, CheckSpec, Issue, failed, fixed, register, skipped


def _folder_art():
    try:
        from lyrarr.app.config import settings
        return settings.metadata.covers.folder_art_filename or 'cover'
    except Exception:
        return 'cover'


def _albums_query():
    from lyrarr.app.database import TableAlbums, select
    return select(TableAlbums).order_by(TableAlbums.lidarrAlbumId)


def _artist_paths():
    from lyrarr.app.database import TableArtists, database, select
    return dict(database.execute(select(TableArtists.lidarrArtistId, TableArtists.path)).all())


def _lyrarr_cover_paths(paths):
    from lyrarr.app.database import TableHistory, database, select
    if not paths:
        return set()
    return set(database.execute(
        select(TableHistory.metadata_path).where(TableHistory.metadata_type == 'cover',
                                                 TableHistory.metadata_path.in_(list(paths)))
    ).scalars().all())


def _album_issue(album, title, fix_action=None, *, fix_mode=None, details=None, path=None, key=None):
    return Issue(entity_type='album', entity_id=album.lidarrAlbumId, entity_key=key or f'album:{album.lidarrAlbumId}',
                 title=title, fix_action=fix_action, fix_mode=fix_mode, details=dict(details or {}),
                 path=path or album.path, album_id=album.lidarrAlbumId, artist_id=album.artistId)


@register
class CoverStatusDrift(Check):
    spec = CheckSpec(
        id='covers.status_drift', code='C-1',
        title='Cover marked available but missing',
        description='Albums whose cover is marked available but no cover file exists in the album folder. '
                    'Not applied automatically when it would affect more than half the albums.',
        category='covers', severity='medium', cost='fs_stat', fix_mode='auto', needs_library=True, order=80,
    )

    def _examine(self, album, artist_paths, art):
        from lyrarr.metadata.cover_fetch import find_cover_files
        if album.cover_status != 'available' or not album.path or not os.path.isdir(album.path):
            return None
        ap = artist_paths.get(album.artistId)
        if ap and os.path.normpath(ap) == os.path.normpath(album.path):
            return None
        if find_cover_files(album.path, art):
            return None
        return _album_issue(album, f"'{album.title}': cover marked available but no file", 'cover_missing')

    def detect(self, ctx):
        art, artist_paths = _folder_art(), _artist_paths()
        albums = ctx.albums(self, _albums_query())
        issues = []
        with_dir = 0
        for i, album in enumerate(albums, 1):
            if ctx.cancelled:
                return
            ctx.progress(i, len(albums))
            if album.path and os.path.isdir(album.path):
                with_dir += 1
            issue = self._examine(album, artist_paths, art)
            if issue is not None:
                issues.append(issue)
        if with_dir and len(issues) > with_dir / 2:
            ctx.note('more than half of the albums would flip: reported only (check the music folder)')
            for issue in issues:
                issue.fix_mode = 'report'
        yield from issues

    def recheck(self, ctx, issue_row):
        from lyrarr.app.database import TableAlbums, database, select
        album = database.execute(select(TableAlbums).where(TableAlbums.lidarrAlbumId == issue_row.entity_id)
                                 ).scalars().first()
        if album is None or not ctx.path_ok(album.path):
            return None
        return self._examine(album, _artist_paths(), _folder_art())

    def fix_cover_missing(self, ctx, issue):
        from datetime import datetime

        from lyrarr.app.database import TableAlbums, database, update
        n = database.execute(update(TableAlbums).where(TableAlbums.lidarrAlbumId == issue.entity_id,
                                                       TableAlbums.cover_status == 'available')
                             .values(cover_status='missing', cover_retry_count=0, cover_retry_after=None,
                                     updated_at_timestamp=datetime.now())).rowcount
        return fixed('cover marked missing') if n else skipped('the album changed')


@register
class CoverFileProblems(Check):
    spec = CheckSpec(
        id='covers.file_problems', code='C-2', requires_download_lock=True,
        title='Cover file problems',
        description='Cover files whose extension does not match their content (renamed when lyrarr wrote '
                    'them), unreadable or empty covers (review), and albums with several cover files (report).',
        category='covers', severity='low', cost='fs_read', fix_mode='auto', needs_library=True, order=81,
    )

    def _issues(self, album, art):
        from lyrarr.metadata.cover_fetch import find_cover_files, sniff_image
        files = find_cover_files(album.path, art)
        if not files:
            return []
        out = []
        ours = _lyrarr_cover_paths(files)
        stems = {}
        for f in files:
            stems.setdefault(os.path.splitext(os.path.basename(f))[0].casefold(), []).append(f)
            try:
                with open(f, 'rb') as fh:
                    head = fh.read(32)
                size = os.path.getsize(f)
            except OSError:
                continue
            sniff = sniff_image(head) if size else None
            ext = os.path.splitext(f)[1].lower().lstrip('.')
            ext = 'jpg' if ext == 'jpeg' else ext
            key = f'file:{f}'
            if sniff is None:
                out.append(_album_issue(album, f"'{album.title}': {os.path.basename(f)} is not a readable image",
                                        'delete_cover' if f in ours else None,
                                        fix_mode='review' if f in ours else 'report', path=f, key=key,
                                        details={'size': size}))
            elif sniff[0] != ext:
                target = os.path.splitext(f)[0] + '.' + sniff[0]
                can = f in ours and not os.path.exists(target)
                out.append(_album_issue(album, f"'{album.title}': {os.path.basename(f)} is really {sniff[0]}",
                                        'rename_cover' if can else None, fix_mode=None if can else 'report',
                                        path=f, key=key, details={'target': target}))
        if len(files) > 1:
            out.append(_album_issue(album, f"'{album.title}': {len(files)} cover files", None, fix_mode='report',
                                    key=f'album:{album.lidarrAlbumId}:multiple',
                                    details={'files': [os.path.basename(f) for f in files]}))
        return out

    def detect(self, ctx):
        art = _folder_art()
        albums = ctx.albums(self, _albums_query())
        for i, album in enumerate(albums, 1):
            if ctx.cancelled:
                return
            ctx.progress(i, len(albums))
            if album.path and os.path.isdir(album.path):
                yield from self._issues(album, art)

    def recheck(self, ctx, issue_row):
        from lyrarr.app.database import TableAlbums, database, select
        album = database.execute(select(TableAlbums).where(TableAlbums.lidarrAlbumId == issue_row.entity_id)
                                 ).scalars().first()
        if album is None or not album.path or not ctx.path_ok(album.path) or not os.path.isdir(album.path):
            return None
        return next((i for i in self._issues(album, _folder_art()) if i.entity_key == issue_row.entity_key), None)

    def fix_rename_cover(self, ctx, issue):
        target = issue.details.get('target')
        if not target or os.path.exists(target):
            return skipped('the target name is taken')
        try:
            os.replace(issue.path, target)
        except OSError as e:
            return failed(f'could not rename: {e}')
        return fixed(f'renamed to {os.path.basename(target)}')

    def fix_delete_cover(self, ctx, issue):
        from datetime import datetime

        from lyrarr.app.database import TableAlbums, database, update
        try:
            os.remove(issue.path)
        except FileNotFoundError:
            pass
        except OSError as e:
            return failed(f'could not delete: {e}')
        database.execute(update(TableAlbums).where(TableAlbums.lidarrAlbumId == issue.entity_id)
                         .values(cover_status='missing', cover_retry_count=0, cover_retry_after=None,
                                 updated_at_timestamp=datetime.now()))
        return fixed('deleted the broken cover; marked missing')


@register
class ArtistRootAndOrphanDirs(Check):
    spec = CheckSpec(
        id='covers.artist_root_and_orphan_dirs', code='C-3', requires_download_lock=True,
        title='Files lyrarr left in the wrong place',
        description='Covers lyrarr wrote into an artist folder, and album folders with no audio that hold '
                    'only files lyrarr wrote. Review: delete those files (never folders, never other files).',
        category='covers', severity='low', cost='fs_stat', fix_mode='review', needs_library=True, order=82,
    )

    def _artist_issue(self, artist, art):
        from lyrarr.metadata.cover_fetch import find_cover_files
        files = sorted(f for f in _lyrarr_cover_paths(find_cover_files(artist.path, art)) if not _is_folder_file(f))
        if not files:
            return None
        return Issue(entity_type='artist', entity_id=artist.lidarrArtistId,
                     entity_key=f'artist:{artist.lidarrArtistId}:root-covers', path=artist.path,
                     title=f"{artist.name}: cover written into the artist folder", fix_action='delete_files',
                     artist_id=artist.lidarrArtistId, details={'files': files})

    def _album_issue(self, album, artist_paths):
        from .files import AUDIO_EXTENSIONS
        if not album.path or not os.path.isdir(album.path):
            return None
        ap = artist_paths.get(album.artistId)
        if ap and os.path.normpath(ap) == os.path.normpath(album.path):
            return None
        try:
            entries = [os.path.join(album.path, e) for e in os.listdir(album.path)]
        except OSError:
            return None
        files = [e for e in entries if os.path.isfile(e)]
        if not files or any(os.path.isdir(e) for e in entries):
            return None
        if any(os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS for f in files):
            return None
        ours = _lyrarr_written(files)
        if set(files) != ours:
            return None
        deletable = sorted(f for f in files if not _is_folder_file(f))
        if not deletable:
            return None
        return _album_issue(album, f"'{album.title}': folder has no audio, only files lyrarr wrote", 'delete_files',
                            details={'files': deletable})

    def detect(self, ctx):
        from lyrarr.app.database import TableArtists, select
        art = _folder_art()
        for artist in ctx.artists(self, select(TableArtists)):
            if artist.path and os.path.isdir(artist.path):
                issue = self._artist_issue(artist, art)
                if issue is not None:
                    yield issue
        artist_paths = _artist_paths()
        for album in ctx.albums(self, _albums_query()):
            issue = self._album_issue(album, artist_paths)
            if issue is not None:
                yield issue

    def recheck(self, ctx, issue_row):
        from lyrarr.app.database import TableAlbums, TableArtists, database, select
        if issue_row.entity_type == 'artist':
            artist = database.execute(select(TableArtists).where(TableArtists.lidarrArtistId == issue_row.entity_id)
                                      ).scalars().first()
            if artist is None or not ctx.path_ok(artist.path):
                return None
            return self._artist_issue(artist, _folder_art())
        album = database.execute(select(TableAlbums).where(TableAlbums.lidarrAlbumId == issue_row.entity_id)
                                 ).scalars().first()
        if album is None or not ctx.path_ok(album.path):
            return None
        return self._album_issue(album, _artist_paths())

    def fix_delete_files(self, ctx, issue):
        from lyrarr.metadata import lyrics_store as ls
        files = issue.details.get('files') or []
        ours = _lyrarr_written(files)
        removed = 0
        for f in files:
            if f not in ours or _is_folder_file(f) or not os.path.isfile(f):
                continue
            if f.lower().endswith('.lrc'):
                track_id = _history_track(f)
                if track_id is None or not ls._archive_existing(track_id, f, origin_provider='unknown',
                                                                replaced_by='audit:C-3'):
                    continue
            try:
                os.remove(f)
                removed += 1
            except OSError:
                continue
        return fixed(f'deleted {removed} file(s)') if removed else skipped('nothing deleted')


def _is_folder_file(path):
    """folder.* is never deleted by C-3 (PLAN §4.4), even when history attributes it to lyrarr."""
    return os.path.splitext(os.path.basename(path))[0].casefold() == 'folder'


def _lyrarr_written(files):
    from lyrarr.app.database import TableHistory, database, select
    if not files:
        return set()
    return set(database.execute(select(TableHistory.metadata_path)
                                .where(TableHistory.metadata_path.in_(list(files)))).scalars().all())


def _history_track(path):
    from lyrarr.app.database import TableHistory, database, select
    return database.execute(select(TableHistory.lidarrTrackId)
                            .where(TableHistory.metadata_path == path, TableHistory.lidarrTrackId.is_not(None))
                            .limit(1)).scalar()

