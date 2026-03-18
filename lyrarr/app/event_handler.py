# coding=utf-8

import queue
import json
import logging
import threading
from collections import deque

_lock = threading.Lock()
_subscribers = []  # list of per-client queues
_history = deque(maxlen=100)  # last 100 events for new clients


def event_stream(type, payload=None):
    """Push an event to ALL connected SSE clients (pub/sub)."""
    data = {'type': type}
    if payload:
        data['payload'] = payload
    msg = json.dumps(data)

    with _lock:
        _history.append(msg)
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)

    logging.debug(f"Event pushed: {type} → {len(_subscribers)} client(s)")


def subscribe():
    """Create a new per-client queue and return it. Also returns history."""
    client_queue = queue.Queue(maxsize=500)
    with _lock:
        # Replay recent history so the new client isn't blank
        for msg in _history:
            try:
                client_queue.put_nowait(msg)
            except queue.Full:
                break
        _subscribers.append(client_queue)
    return client_queue


def unsubscribe(client_queue):
    """Remove a client queue when the connection closes."""
    with _lock:
        if client_queue in _subscribers:
            _subscribers.remove(client_queue)
