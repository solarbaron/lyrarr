
import json
import logging
import queue
import threading
from collections import deque

_lock = threading.Lock()
_subscribers = []  # list of per-client queues
_history = deque(maxlen=100)  # (event_id, data_dict) of recent events for new clients
_next_id = 0

# Types that are pure in-the-moment signals: replaying them to a client that
# reconnects/refreshes is only noise (per-track progress ticks, scheduler job
# state flips, raw SignalR chatter).
_NO_REPLAY_TYPES = frozenset({'task', 'download_progress', 'lidarr_event'})


def event_stream(type, payload=None):
    """Push an event to ALL connected SSE clients (pub/sub)."""
    global _next_id
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

    logging.debug(f"Event pushed: {type} → {len(_subscribers)} client(s)")


def subscribe(last_event_id=None):
    """Create a new per-client queue, pre-filled with replayable history.

    Replayed events are marked with `"replay": true` so the frontend can fill
    its activity feed without firing toasts/progress animations for things
    that already happened. When the browser auto-reconnects it sends the SSE
    Last-Event-ID header; only events after that id are replayed, so a brief
    network blip doesn't duplicate the whole feed.
    """
    client_queue = queue.Queue(maxsize=500)
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
