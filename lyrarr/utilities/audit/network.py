"""Network verification shared by I-9 and M-4.

Calls download_worker.run_lyrics_pipeline(mode='verify') (WS-C), which uses
the providers' rate limiter and health tracker and never writes. The audit
caps the number of tracks per run (network_max_tracks / network_limit),
records a resume cursor (the last processed track id), never runs while a
download run is active, and stops early when the providers keep failing.
"""

from __future__ import annotations

import logging

from . import repairs as rp

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_TRANSIENT = 3


def _context_for(row, cache):
    from lyrarr.app.database import TableAlbums, TableArtists, database, select
    from lyrarr.metadata import download_worker as dw
    album = cache.get(('album', row.albumId))
    if album is None:
        album = database.execute(select(TableAlbums).where(TableAlbums.lidarrAlbumId == row.albumId)).scalars().first()
        cache[('album', row.albumId)] = album
    artist_name = cache.get(('artist', row.artistId))
    if artist_name is None:
        artist_name = database.execute(
            select(TableArtists.name).where(TableArtists.lidarrArtistId == row.artistId)).scalar() or ''
        cache[('artist', row.artistId)] = artist_name
    eff = dw._effective_settings(album, dw._get_profile(getattr(album, 'profileId', None))) if album else {}
    return eff, artist_name, getattr(album, 'title', None)


def verify_track(row, cache):
    """run_lyrics_pipeline(mode='verify') for one track; raises when unavailable."""
    from lyrarr.metadata import download_worker as dw
    eff, album_artist, album_title = _context_for(row, cache)
    artist_name = getattr(row, 'track_artist', None) or album_artist
    alt = (album_artist,) if album_artist and album_artist != artist_name else ()
    return dw.run_lyrics_pipeline(row, eff=eff, artist_name=artist_name, album_title=album_title,
                                  alt_artist_names=alt, mode='verify', write=False)


def verify_rows(check, ctx, query, judge):
    """Yield judge(ctx, row, result) issues for the rows of `query` (network budget aware)."""
    if rp.downloads_running():
        ctx.note('skipped: a download run is in progress')
        return
    rows = ctx.tracks(check, query)
    total = len(rows)
    cache = {}
    transient_streak = 0
    for i, row in enumerate(rows, 1):
        if ctx.cancelled:
            return
        if not ctx.network_take():
            ctx.note(f'stopped after {ctx.network_used} tracks (network limit); resume to continue')
            ctx.cache['network_incomplete'] = True
            return
        ctx.progress(i, total, cursor=row.lidarrTrackId)
        try:
            result = verify_track(row, cache)
        except Exception as e:
            logger.warning(f"audit {check.spec.code}: verify failed for track {row.lidarrTrackId}: {e}")
            continue
        if (result or {}).get('outcome') == 'transient':
            transient_streak += 1
            if transient_streak >= _MAX_CONSECUTIVE_TRANSIENT:
                ctx.note('stopped early: the lyrics providers are unavailable or cooling down')
                ctx.cache['network_incomplete'] = True
                return
            continue
        transient_streak = 0
        issue = judge(ctx, row, result or {})
        if issue is not None:
            yield issue
