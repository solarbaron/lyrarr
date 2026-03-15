# coding=utf-8

"""
SSE (Server-Sent Events) endpoint for real-time activity feed.
"""

import json
from flask import Blueprint, Response
from lyrarr.app.event_handler import event_queue

events_bp = Blueprint('events', __name__)


@events_bp.route('/api/events')
def sse_stream():
    """Stream server-sent events to the frontend."""
    def generate():
        while True:
            try:
                # Block for up to 30 seconds, then send a keepalive comment
                data = event_queue.get(timeout=30)
                yield f"data: {data}\n\n"
            except Exception:
                # Send keepalive to keep connection open
                yield ": keepalive\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )
