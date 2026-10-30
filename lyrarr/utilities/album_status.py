"""Album lyrics status, computed from the album's tracks.

The table_albums.lyrics_status column was only ever set to 'unknown' at
insert, so API responses derive the status from the tracks instead:
blacklisted tracks are ignored, instrumental ones count as satisfied.

  'complete' — every remaining track has lyrics (or is instrumental)
  'partial'  — some do
  'missing'  — none do
  'unknown'  — the album has no tracks with files
"""

from sqlalchemy import case, func, select

from lyrarr.app.database import TableTracks, database

SATISFIED_TRACK_STATES = ('available', 'manual', 'instrumental')

# Values accepted by the albums list `lyricsStatus` filter.
_FILTER_VALUES = {
    'complete': ('complete',),
    'available': ('complete',),  # legacy alias
    'partial': ('partial',),
    'missing': ('missing',),
    'incomplete': ('partial', 'missing'),
    'unknown': ('unknown',),
}


def album_track_stats():
    """Per-album track counts as a subquery (album_id, total, blacklisted, satisfied)."""
    return select(
        TableTracks.albumId.label('album_id'),
        func.count().label('total'),
        func.sum(case((TableTracks.lyrics_status == 'blacklisted', 1), else_=0)).label('blacklisted'),
        func.sum(case((TableTracks.lyrics_status.in_(SATISFIED_TRACK_STATES), 1), else_=0)).label('satisfied'),
    ).group_by(TableTracks.albumId).subquery('album_track_stats')


def lyrics_status_expr(stats):
    """SQL CASE computing the status from an (outer-joined) album_track_stats()."""
    total = func.coalesce(stats.c.total, 0)
    wanted = total - func.coalesce(stats.c.blacklisted, 0)
    satisfied = func.coalesce(stats.c.satisfied, 0)
    return case(
        (total == 0, 'unknown'),
        (satisfied >= wanted, 'complete'),
        (satisfied == 0, 'missing'),
        else_='partial',
    )


def filter_values(value):
    """Statuses matched by a `lyricsStatus` filter value, or None if unrecognized."""
    return _FILTER_VALUES.get((value or '').strip().lower())


def status_from_track_states(states):
    """Same rules as lyrics_status_expr, for track statuses already in memory."""
    states = list(states)
    if not states:
        return 'unknown'
    wanted = [s for s in states if s != 'blacklisted']
    satisfied = sum(1 for s in wanted if s in SATISFIED_TRACK_STATES)
    if satisfied >= len(wanted):
        return 'complete'
    if satisfied == 0:
        return 'missing'
    return 'partial'


def album_lyrics_status_map(album_ids):
    """{album_id: status} for the given albums, in one aggregate query."""
    album_ids = [a for a in album_ids if a is not None]
    if not album_ids:
        return {}
    stats = album_track_stats()
    rows = database.execute(
        select(stats.c.album_id, lyrics_status_expr(stats)).where(stats.c.album_id.in_(album_ids))
    ).all()
    result = {album_id: 'unknown' for album_id in album_ids}
    result.update({album_id: status for album_id, status in rows})
    return result


def apply_album_lyrics_status(album_dicts, id_key='lidarrAlbumId'):
    """Overwrite `lyrics_status` in album dicts with the computed value."""
    statuses = album_lyrics_status_map([d.get(id_key) for d in album_dicts])
    for d in album_dicts:
        d['lyrics_status'] = statuses.get(d.get(id_key), 'unknown')
    return album_dicts
