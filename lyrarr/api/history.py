from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import TableHistory, database, select

api_ns_history = Namespace('history', description='History operations')

ACTION_LABELS = {
    1: 'Downloaded',
    2: 'Upgraded',
    3: 'Deleted',
    4: 'Saved',
    5: 'Published',
    6: 'Classified',
    7: 'Audit repair',
}


def history_to_dict(row):
    d = row.to_dict()  # details parsed into an object (null on invalid JSON)
    d['actionLabel'] = ACTION_LABELS.get(row.action, 'Other')
    return d


@api_ns_history.route('/history')
class HistoryList(Resource):
    def get(self):
        """Recent history entries (newest first).

        Filters: action (one id or a comma list, e.g. 6,7), type (lyrics|cover),
        trackId, albumId; limit (1..500, default 100).
        """
        limit = max(1, min(request.args.get('limit', 100, type=int) or 100, 500))
        query = select(TableHistory)
        action = request.args.get('action', '', type=str)
        if action:
            try:
                actions = [int(a) for a in action.split(',') if a.strip()]
            except ValueError:
                return {'message': 'action must be a number or a comma-separated list of numbers'}, 400
            unknown = [a for a in actions if a not in ACTION_LABELS]
            if unknown:
                return {'message': f'Unknown action(s): {unknown}'}, 400
            query = query.where(TableHistory.action.in_(actions))
        mtype = request.args.get('type', '', type=str)
        if mtype:
            if mtype not in ('lyrics', 'cover'):
                return {'message': "type must be 'lyrics' or 'cover'"}, 400
            query = query.where(TableHistory.metadata_type == mtype)
        track_id = request.args.get('trackId', None, type=int)
        if track_id is not None:
            query = query.where(TableHistory.lidarrTrackId == track_id)
        album_id = request.args.get('albumId', None, type=int)
        if album_id is not None:
            query = query.where(TableHistory.lidarrAlbumId == album_id)
        rows = database.execute(
            query.order_by(TableHistory.timestamp.desc(), TableHistory.id.desc()).limit(limit)
        ).scalars().all()
        return [history_to_dict(r) for r in rows]
