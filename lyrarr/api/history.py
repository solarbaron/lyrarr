# coding=utf-8

from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableHistory, select

api_ns_history = Namespace('history', description='History operations')


@api_ns_history.route('/history')
class HistoryList(Resource):
    def get(self):
        """List history entries."""
        rows = database.execute(
            select(TableHistory).order_by(TableHistory.timestamp.desc()).limit(100)
        ).scalars().all()
        return [r.to_dict() for r in rows]
