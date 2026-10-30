
from datetime import datetime, timedelta

from flask_restx import Namespace, Resource
from sqlalchemy import case

from lyrarr.app.database import TableAlbums, TableArtists, TableHistory, TableTracks, database, func, select
from lyrarr.utilities.album_status import SATISFIED_TRACK_STATES

api_ns_dashboard = Namespace('dashboard', description='Dashboard statistics')


@api_ns_dashboard.route('/dashboard/stats')
class DashboardStats(Resource):
    def get(self):
        """Get dashboard chart data: download history + per-artist completion."""

        # --- Downloads over time (last 30 days) ---
        thirty_days_ago = datetime.now() - timedelta(days=30)
        history_rows = database.execute(
            select(TableHistory)
            .where(TableHistory.timestamp >= thirty_days_ago)
            .order_by(TableHistory.timestamp)
        ).scalars().all()

        # Group by date
        daily = {}
        for h in history_rows:
            day = h.timestamp.strftime('%Y-%m-%d') if h.timestamp else 'unknown'
            if day not in daily:
                daily[day] = {'date': day, 'covers': 0, 'lyrics': 0}
            if h.metadata_type == 'cover':
                daily[day]['covers'] += 1
            elif h.metadata_type == 'lyrics':
                daily[day]['lyrics'] += 1

        # Fill in missing days
        chart_data = []
        for i in range(30):
            day = (datetime.now() - timedelta(days=29 - i)).strftime('%Y-%m-%d')
            chart_data.append(daily.get(day, {'date': day, 'covers': 0, 'lyrics': 0}))

        # --- Per-artist completion ---
        # Two aggregate queries instead of four per artist; the 20 artists with
        # the most outstanding work (missing lyrics + missing covers) are shown.
        album_rows = database.execute(
            select(
                TableAlbums.artistId,
                func.count(),
                func.sum(case((TableAlbums.cover_status.in_(('available', 'manual')), 1), else_=0)),
            ).group_by(TableAlbums.artistId)
        ).all()
        track_rows = database.execute(
            select(
                TableTracks.artistId,
                func.sum(case((TableTracks.lyrics_status != 'blacklisted', 1), else_=0)),
                func.sum(case((TableTracks.lyrics_status.in_(SATISFIED_TRACK_STATES), 1), else_=0)),
            ).group_by(TableTracks.artistId)
        ).all()
        tracks_by_artist = {row[0]: (row[1] or 0, row[2] or 0) for row in track_rows}

        candidates = []
        for artist_id, total_albums, covers_done in album_rows:
            if not total_albums:
                continue
            covers_done = covers_done or 0
            total_tracks, lyrics_done = tracks_by_artist.get(artist_id, (0, 0))
            outstanding = (total_tracks - lyrics_done) + (total_albums - covers_done)
            candidates.append((outstanding, artist_id, total_albums, covers_done, total_tracks, lyrics_done))
        candidates.sort(key=lambda c: (-c[0], -c[2]))
        top = candidates[:20]

        names = {
            a.lidarrArtistId: a.name
            for a in database.execute(
                select(TableArtists).where(TableArtists.lidarrArtistId.in_([c[1] for c in top]))
            ).scalars().all()
        } if top else {}

        artist_stats = [
            {
                'name': names.get(artist_id, 'Unknown'),
                'id': artist_id,
                'totalAlbums': total_albums,
                'coversDone': covers_done,
                'coverPct': round(covers_done / total_albums * 100) if total_albums else 0,
                'totalTracks': total_tracks,
                'lyricsDone': lyrics_done,
                'lyricsPct': round(lyrics_done / total_tracks * 100) if total_tracks else 0,
                'missing': outstanding,
            }
            for outstanding, artist_id, total_albums, covers_done, total_tracks, lyrics_done in top
        ]

        return {
            'downloadHistory': chart_data,
            'artistCompletion': artist_stats,
        }
