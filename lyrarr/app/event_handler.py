# coding=utf-8

import queue
import json
import logging

event_queue = queue.Queue()


def event_stream(type, payload=None):
    """Push an event to the SSE queue for the frontend."""
    data = {'type': type}
    if payload:
        data['payload'] = payload
    event_queue.put(json.dumps(data))
    logging.debug(f"Event pushed: {type}")
