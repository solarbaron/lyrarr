
import json
import logging
import queue
import threading
import time
from collections import deque

_lock = threading.Lock()
_subscribers = []  # list of per-client queues
_history = deque(maxlen=100)  # (event_id, data_dict) of recent events for new clients
_next_id = 0

# Types that are pure in-the-moment signals: replaying them to a client that
# reconnects/refreshes is only noise (per-track progress ticks, scheduler job
# state flips, raw SignalR chatter, per-track instrumental decisions).
# frontend/src/events.tsx keeps a copy of this set; they must match.
_NO_REPLAY_TYPES = frozenset({
    'task', 'download_progress', 'lidarr_event',
    'lyrics_audit_progress', 'download_scope', 'download_item', 'instrumental_classified',
})

# Compatibility gate (PLAN §1.6). The pre-2.0 frontend added an activity-feed
# item for every unknown event, so the high-frequency event types below were
# dropped until the new frontend shipped. The bundled frontend consumes them
# now (tests/test_release_flags.py keeps this True). While False, gated types
# are dropped and emit_throttled() is a no-op.
FRONTEND_EVENTS_V2 = True
_GATED_TYPES = frozenset({'lyrics_audit_progress', 'download_scope', 'download_item'})

_throttle_lock = threading.Lock()
_throttle_last = {}  # key -> monotonic time of the last emitted event
_THROTTLE_MAX_KEYS = 1000


class ClientQueue(queue.Queue):
    """Per-client event queue. `closed` tells the SSE stream to end.

    A client that falls too far behind (queue full) is dropped; ending its
    stream makes the browser's EventSource reconnect and catch up via
    Last-Event-ID, instead of silently receiving nothing ever again.
    """

    def __init__(self, maxsize=0):
        super().__init__(maxsize=maxsize)
        self.closed = False

    def close(self):
        self.closed = True
        try:
            self.put_nowait(None)  # wake a blocked reader
        except queue.Full:
            pass  # a full queue wakes the reader anyway


def event_stream(type, payload=None):
    """Push an event to ALL connected SSE clients (pub/sub).

    Gated types (_GATED_TYPES) are dropped while FRONTEND_EVENTS_V2 is False.
    """
    global _next_id
    if type in _GATED_TYPES and not FRONTEND_EVENTS_V2:
        return
    data = {'type': type}
    if payload:
        data['payload'] = payload
    msg = json.dumps(data)

    with _lock:
        _next_id += 1
        event_id = _next_id
        if type not in _NO_REPLAY_TYPES:
            _history.append((event_id, data))
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait((event_id, msg))
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)
            q.closed = True

    logging.debug(f"Event pushed: {type} → {len(_subscribers)} client(s)")


def emit_throttled(type, payload, key, min_interval=1.0, force=False):
    """Emit at most one event per `key` every `min_interval` seconds.

    Intermediate events for the same key are dropped (progress ticks); pass
    force=True for the first and last item of a phase so the client always
    sees the start and the end. No-op while FRONTEND_EVENTS_V2 is False.
    Returns True when the event was emitted.
    """
    if not FRONTEND_EVENTS_V2:
        return False
    now = time.monotonic()
    with _throttle_lock:
        last = _throttle_last.get(key)
        if not force and last is not None and now - last < min_interval:
            return False
        if len(_throttle_last) >= _THROTTLE_MAX_KEYS and key not in _throttle_last:
            _throttle_last.clear()
        _throttle_last[key] = now
    event_stream(type, payload)
    return True


def subscribe(last_event_id=None):
    """Create a new per-client queue, pre-filled with replayable history.

    Replayed events are marked with `"replay": true` so the frontend can fill
    its activity feed without firing toasts/progress animations for things
    that already happened. When the browser auto-reconnects it sends the SSE
    Last-Event-ID header; only events after that id are replayed, so a brief
    network blip doesn't duplicate the whole feed.
    """
    client_queue = ClientQueue(maxsize=500)
    with _lock:
        for event_id, data in _history:
            if last_event_id is not None and event_id <= last_event_id:
                continue
            try:
                client_queue.put_nowait((event_id, json.dumps({**data, 'replay': True})))
            except queue.Full:
                break
        _subscribers.append(client_queue)
    return client_queue


def unsubscribe(client_queue):
    """Remove a client queue when the connection closes."""
    with _lock:
        if client_queue in _subscribers:
            _subscribers.remove(client_queue)


def close_all_streams():
    """End every open SSE stream (used on shutdown)."""
    with _lock:
        clients = list(_subscribers)
        _subscribers.clear()
    for q in clients:
        q.close()
