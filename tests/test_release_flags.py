"""Release flags that must hold in a shipped build (PLAN §1.6, §7.6).

The backend gates its high-frequency events (audit progress, per-item
download progress) behind ``event_handler.FRONTEND_EVENTS_V2`` because the
pre-1.7 frontend turned every unknown event into an activity-feed item. The
bundled frontend consumes them now, so the flag must be on, and the set of
event types the server never replays must equal the set the frontend never
buffers (a mismatch either floods a refreshed tab or loses a replayed event).
"""

import re
from pathlib import Path

from lyrarr.app import event_handler

FRONTEND_SRC = Path(__file__).resolve().parent.parent / 'frontend' / 'src'


def _string_set(source: str, const_name: str) -> set[str]:
    """The string literals of `const <name> = new Set([...])` in a TS source."""
    m = re.search(r'const\s+' + re.escape(const_name) + r'\s*=\s*new\s+Set(?:<[^>]*>)?\(\s*\[(.*?)\]\s*\)',
                  source, re.S)
    assert m, f'{const_name} not found'
    return {a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", m.group(1))}


def test_frontend_events_v2_enabled():
    assert event_handler.FRONTEND_EVENTS_V2 is True


def test_no_replay_types_match_frontend_buffer():
    events_tsx = (FRONTEND_SRC / 'events.tsx').read_text(encoding='utf-8')
    assert _string_set(events_tsx, 'NO_BUFFER_TYPES') == set(event_handler._NO_REPLAY_TYPES)


def test_gated_types_are_progress_only_in_frontend():
    """Every gated (high-frequency) type is handled as progress, never as a feed item."""
    jobs_tsx = (FRONTEND_SRC / 'jobs.tsx').read_text(encoding='utf-8')
    assert set(event_handler._GATED_TYPES) <= _string_set(jobs_tsx, 'PROGRESS_ONLY_TYPES')
    assert set(event_handler._GATED_TYPES) <= set(event_handler._NO_REPLAY_TYPES)


def test_gated_events_are_delivered():
    q = event_handler.subscribe(last_event_id=event_handler._next_id)
    try:
        event_handler.event_stream('download_item', {'metadata_type': 'lyrics', 'title': 'x', 'outcome': 'ok'})
        assert not q.empty()
    finally:
        event_handler.unsubscribe(q)
