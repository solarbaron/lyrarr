
"""
SSE (Server-Sent Events) endpoint for real-time activity feed.
Uses per-client pub/sub so all connected browsers receive every event.
"""

from flask import Blueprint, Response, request

from lyrarr.app.event_handler import subscribe, unsubscribe

events_bp = Blueprint('events', __name__)


@events_bp.route('/api/events')
def sse_stream():
    """Stream server-sent events to the frontend.

    Sends an `id:` with every event; on auto-reconnect the browser echoes the
    last one back via the Last-Event-ID header so only missed events are
    replayed instead of the whole history.
    """
    last_event_id = None
    raw = request.headers.get('Last-Event-ID')
    if raw:
        try:
            last_event_id = int(raw)
        except ValueError:
            pass

    client_queue = subscribe(last_event_id=last_event_id)

    def generate():
        # Tell the browser how long to wait before reconnecting.
        yield "retry: 5000\n\n"
        try:
            while True:
                try:
                    # Block for up to 30 seconds, then send a keepalive comment
                    event_id, data = client_queue.get(timeout=30)
                    yield f"id: {event_id}\ndata: {data}\n\n"
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
