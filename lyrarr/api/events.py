# coding=utf-8

"""
SSE (Server-Sent Events) endpoint for real-time activity feed.
Uses per-client pub/sub so all connected browsers receive every event.
"""

from flask import Blueprint, Response
from lyrarr.app.event_handler import subscribe, unsubscribe

events_bp = Blueprint('events', __name__)


@events_bp.route('/api/events')
def sse_stream():
    """Stream server-sent events to the frontend."""
    client_queue = subscribe()

    def generate():
        try:
            while True:
                try:
                    # Block for up to 30 seconds, then send a keepalive comment
                    data = client_queue.get(timeout=30)
                    yield f"data: {data}\n\n"
                except Exception:
                    # Send keepalive to keep connection open
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(client_queue)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )
