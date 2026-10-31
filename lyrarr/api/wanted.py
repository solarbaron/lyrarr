
from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import TableAlbums, TableArtists, TableTracks, and_, database, func, or_, select
from lyrarr.utilities.album_status import album_lyrics_status_map, apply_album_lyrics_status
from lyrarr.utilities.pagination import page_params

api_ns_wanted = Namespace('wanted', description='Missing metadata')


@api_ns_wanted.route('/wanted/covers')
class WantedCovers(Resource):
    def get(self):
        """List albums with missing cover art (paginated, searchable)."""
        page, page_size = page_params(25)
        search = request.args.get('search', '', type=str)
        sort_by = request.args.get('sortBy', 'title', type=str)

        base = TableAlbums.cover_status == 'missing'
        query = select(TableAlbums).where(base)
        count_q = select(func.count()).select_from(TableAlbums).where(base)

        if search:
            like = f'%{search}%'
            query = query.where(TableAlbums.title.ilike(like))
            count_q = count_q.where(TableAlbums.title.ilike(like))

        total = database.execute(count_q).scalar()

        # Sorting
        sort_map = {'title': TableAlbums.title, 'year': TableAlbums.year}
        sort_col = sort_map.get(sort_by, TableAlbums.title)
        query = query.order_by(sort_col)

        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            data.append(d)
        apply_album_lyrics_status(data)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_wanted.route('/wanted/lyrics')
class WantedLyrics(Resource):
    def get(self):
        """List tracks with missing lyrics (paginated, searchable)."""
        page, page_size = page_params(25)
        search = request.args.get('search', '', type=str)

        base = TableTracks.lyrics_status == 'missing'
        query = select(TableTracks).where(base)
        count_q = select(func.count()).select_from(TableTracks).where(base)

        if search:
            like = f'%{search}%'
            query = query.where(TableTracks.title.ilike(like))
            count_q = count_q.where(TableTracks.title.ilike(like))

        total = database.execute(count_q).scalar()

        query = query.order_by(TableTracks.title)
        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }



@api_ns_wanted.route('/wanted/untimed')
class WantedUntimed(Resource):
    def get(self):
        """List tracks with available but unsynced lyrics (paginated, searchable)."""
        page, page_size = page_params(25)
        search = request.args.get('search', '', type=str)

        base_conds = [
            TableTracks.lyrics_status == 'available',
            TableTracks.is_synced == False,
        ]
        query = select(TableTracks).where(*base_conds)
        count_q = select(func.count()).select_from(TableTracks).where(*base_conds)

        if search:
            like = f'%{search}%'
            query = query.where(TableTracks.title.ilike(like))
            count_q = count_q.where(TableTracks.title.ilike(like))

        total = database.execute(count_q).scalar()

        query = query.order_by(TableTracks.title)
        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist + album names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        albums = {a.lidarrAlbumId: a.title for a in database.execute(select(TableAlbums)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            d['albumTitle'] = albums.get(r.albumId, 'Unknown')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_wanted.route('/wanted/undetected')
class WantedUndetected(Resource):
    def get(self):
        """List tracks with available lyrics but no detected language (paginated, searchable)."""
        page, page_size = page_params(25)
        search = request.args.get('search', '', type=str)

        base_conds = [
            TableTracks.lyrics_status == 'available',
            TableTracks.detected_language.is_(None),
        ]
        query = select(TableTracks).where(*base_conds)
        count_q = select(func.count()).select_from(TableTracks).where(*base_conds)

        if search:
            like = f'%{search}%'
            query = query.where(TableTracks.title.ilike(like))
            count_q = count_q.where(TableTracks.title.ilike(like))

        total = database.execute(count_q).scalar()

        query = query.order_by(TableTracks.title)
        rows = database.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()

        # Resolve artist + album names
        artists = {a.lidarrArtistId: a.name for a in database.execute(select(TableArtists)).scalars().all()}
        albums = {a.lidarrAlbumId: a.title for a in database.execute(select(TableAlbums)).scalars().all()}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            d['albumTitle'] = albums.get(r.albumId, 'Unknown')
            data.append(d)

        return {
            'data': data,
            'total': total,
            'page': page,
            'pageSize': page_size,
        }


@api_ns_wanted.route('/wanted/stats')
class WantedStats(Resource):
    def get(self):
        """Get summary counts for missing metadata."""
        missing_covers = database.execute(
            select(func.count()).select_from(TableAlbums).where(TableAlbums.cover_status == 'missing')
        ).scalar()
        total_albums = database.execute(
            select(func.count()).select_from(TableAlbums)
        ).scalar()
        missing_lyrics = database.execute(
            select(func.count()).select_from(TableTracks).where(TableTracks.lyrics_status == 'missing')
        ).scalar()
        untimed_lyrics = database.execute(
            select(func.count()).select_from(TableTracks).where(
                TableTracks.lyrics_status == 'available',
                TableTracks.is_synced == False,
            )
        ).scalar()
        undetected_language = database.execute(
            select(func.count()).select_from(TableTracks).where(
                TableTracks.lyrics_status == 'available',
                TableTracks.detected_language.is_(None),
            )
        ).scalar()
        total_tracks = database.execute(
            select(func.count()).select_from(TableTracks)
        ).scalar()

        return {
            **instrumental_stats(),
            'missing_covers': missing_covers,
            'total_albums': total_albums,
            'missing_lyrics': missing_lyrics,
            'untimed_lyrics': untimed_lyrics,
            'undetected_language': undetected_language,
            'total_tracks': total_tracks,
            'covers_complete': total_albums - missing_covers if total_albums else 0,
            'lyrics_complete': total_tracks - missing_lyrics if total_tracks else 0,
        }


# ---------------------------------------------------------------------------
# Instrumental review (PLAN §1.8, §2.4, §5.3)
# ---------------------------------------------------------------------------

INSTRUMENTAL_VIEWS = ('flagged', 'likely', 'legacy', 'conflict', 'cleared', 'all')
INSTRUMENTAL_REASONS = ('title', 'consensus', 'user', 'legacy')
CLEARED_WHY = ('legacy-unverified', 'weak-title', 'periodic-recheck', 'embedded-lyrics', 'network-recheck')


def _has_veto():
    return TableTracks.instrumental_evidence.like('%"veto"%')


def instrumental_view_condition(view):
    T = TableTracks
    flagged = T.lyrics_status == 'instrumental'
    likely = and_(T.lyrics_status == 'missing', T.instrumental_evidence.is_not(None), ~_has_veto())
    legacy = and_(flagged, T.instrumental_reason.is_(None))
    conflict = and_(T.instrumental_evidence.is_not(None), _has_veto())
    cleared = and_(T.instrumental_prev.is_not(None),
                   or_(T.lyrics_status.is_(None), T.lyrics_status != 'instrumental'),
                   T.instrumental_override.is_(None))
    return {
        'flagged': flagged, 'likely': likely, 'legacy': legacy, 'conflict': conflict, 'cleared': cleared,
        'all': or_(flagged, likely, conflict, cleared),
    }[view]


def _count(cond):
    return database.execute(select(func.count()).select_from(TableTracks).where(cond)).scalar() or 0


def instrumental_stats():
    legacy = _count(instrumental_view_condition('legacy'))
    likely = _count(instrumental_view_condition('likely'))
    conflict = _count(instrumental_view_condition('conflict'))
    cleared = _count(instrumental_view_condition('cleared'))
    return {
        'instrumental_review': legacy + likely + conflict + cleared,
        'likely_instrumental': likely,
        'instrumental_cleared': cleared,
        'instrumental_conflict': conflict,
        'instrumental_legacy': legacy,
        'instrumental_flagged': _count(instrumental_view_condition('flagged')),
    }


@api_ns_wanted.route('/wanted/instrumental')
class WantedInstrumental(Resource):
    def get(self):
        """Instrumental review: flagged | likely | legacy | conflict | cleared | all."""
        page, page_size = page_params(25)
        view = request.args.get('view', 'flagged', type=str) or 'flagged'
        if view not in INSTRUMENTAL_VIEWS:
            return {'message': f"Unknown view '{view}'. Allowed: {', '.join(INSTRUMENTAL_VIEWS)}"}, 400
        T = TableTracks
        conds = [instrumental_view_condition(view)]
        reason = request.args.get('reason', '', type=str)
        if reason:
            if reason not in INSTRUMENTAL_REASONS:
                return {'message': f"Unknown reason '{reason}'"}, 400
            conds.append(T.instrumental_reason.is_(None) if reason == 'legacy' else T.instrumental_reason == reason)
        why = request.args.get('why', '', type=str)
        if why:
            conds.append(func.json_extract(T.instrumental_prev, '$.why') == why)
        search = request.args.get('search', '', type=str)
        if search:
            conds.append(T.title.ilike(f'%{search}%'))

        total = database.execute(select(func.count()).select_from(T).where(*conds)).scalar()
        query = select(T).where(*conds)
        if view == 'cleared':
            query = query.order_by(func.json_extract(T.instrumental_prev, '$.at').desc(), T.lidarrTrackId)
        else:
            query = query.order_by(T.instrumental_checked_at.desc().nulls_last(), T.title, T.lidarrTrackId)
        rows = database.execute(query.offset((page - 1) * page_size).limit(page_size)).scalars().all()

        artist_ids = {r.artistId for r in rows}
        album_ids = {r.albumId for r in rows}
        artists = dict(database.execute(select(TableArtists.lidarrArtistId, TableArtists.name)
                                        .where(TableArtists.lidarrArtistId.in_(artist_ids))).all()) if rows else {}
        albums = dict(database.execute(select(TableAlbums.lidarrAlbumId, TableAlbums.title)
                                       .where(TableAlbums.lidarrAlbumId.in_(album_ids))).all()) if rows else {}
        album_status = album_lyrics_status_map(list(album_ids)) if rows else {}
        data = []
        for r in rows:
            d = r.to_dict()
            d['artistName'] = artists.get(r.artistId, 'Unknown')
            d['albumTitle'] = albums.get(r.albumId, 'Unknown')
            d['albumLyricsStatus'] = album_status.get(r.albumId, 'unknown')
            data.append(d)
        return {'data': data, 'total': total, 'page': page, 'pageSize': page_size, 'view': view}
