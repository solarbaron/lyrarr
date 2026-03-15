# coding=utf-8

from flask_restx import Namespace, Resource
from datetime import datetime, timedelta
from lyrarr.app.database import (
    database, TableHistory, TableAlbums, TableTracks, TableArtists,
    select, func
)

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
        artists = database.execute(select(TableArtists)).scalars().all()
        artist_stats = []

        for artist in artists[:20]:  # Limit to top 20
            total_albums = database.execute(
                select(func.count()).select_from(TableAlbums)
                .where(TableAlbums.artistId == artist.lidarrArtistId)
            ).scalar() or 0

            if total_albums == 0:
                continue

            covers_done = database.execute(
                select(func.count()).select_from(TableAlbums)
                .where(TableAlbums.artistId == artist.lidarrArtistId)
                .where(TableAlbums.cover_status == 'available')
            ).scalar() or 0

            total_tracks = database.execute(
                select(func.count()).select_from(TableTracks)
                .where(TableTracks.artistId == artist.lidarrArtistId)
            ).scalar() or 0

            lyrics_done = database.execute(
                select(func.count()).select_from(TableTracks)
                .where(TableTracks.artistId == artist.lidarrArtistId)
                .where(TableTracks.lyrics_status == 'available')
            ).scalar() or 0

            artist_stats.append({
                'name': artist.name,
                'id': artist.lidarrArtistId,
                'totalAlbums': total_albums,
                'coversDone': covers_done,
                'coverPct': round(covers_done / total_albums * 100) if total_albums else 0,
                'totalTracks': total_tracks,
                'lyricsDone': lyrics_done,
                'lyricsPct': round(lyrics_done / total_tracks * 100) if total_tracks else 0,
            })

        # Sort by total albums descending
        artist_stats.sort(key=lambda x: x['totalAlbums'], reverse=True)

        return {
            'downloadHistory': chart_data,
            'artistCompletion': artist_stats,
        }
