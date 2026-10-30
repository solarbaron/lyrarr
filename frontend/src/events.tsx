import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react';
import { getEventsUrl } from './api';
import type { SSEEvent } from './types';

type Listener = (event: SSEEvent) => void;

interface EventsHub {
  subscribe: (listener: Listener) => () => void;
}

const EventsContext = createContext<EventsHub | null>(null);

// How many recent (non-progress) events to keep for late subscribers, e.g. the
// Mass Edit monitor mounting after the activity feed already received them.
const BUFFER_SIZE = 100;
// Matches the server's _NO_REPLAY_TYPES: momentary signals not worth replaying.
const NO_BUFFER_TYPES = new Set(['task', 'download_progress', 'lidarr_event']);

/**
 * Owns the app's single EventSource connection to /api/events and fans events
 * out to subscribers, so each tab holds one SSE connection no matter how many
 * components listen.
 */
export function EventsProvider({ children }: { children: ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const buffer = useRef<SSEEvent[]>([]);
  const hub = useRef<EventsHub | null>(null);

  if (!hub.current) {
    hub.current = {
      subscribe: (listener) => {
        // Bring a new subscriber up to date with what this tab has already
        // seen, marked as replay so it doesn't toast or animate.
        for (const ev of buffer.current) listener({ ...ev, replay: true });
        listeners.current.add(listener);
        return () => { listeners.current.delete(listener); };
      },
    };
  }

  useEffect(() => {
    const source = new EventSource(getEventsUrl());
    source.onmessage = (e) => {
      let event: SSEEvent;
      try {
        event = JSON.parse(e.data);
      } catch {
        return;
      }
      if (!NO_BUFFER_TYPES.has(event.type)) {
        buffer.current.push(event);
        if (buffer.current.length > BUFFER_SIZE) buffer.current.shift();
      }
      for (const listener of [...listeners.current]) {
        try {
          listener(event);
        } catch (err) {
          console.error('SSE listener failed', err);
        }
      }
    };
    // EventSource reconnects on its own after transient errors.
    return () => source.close();
  }, []);

  return <EventsContext.Provider value={hub.current}>{children}</EventsContext.Provider>;
}

/**
 * Subscribe to server-sent events for the lifetime of the calling component.
 * The latest `handler` is always used, so it doesn't need to be memoized.
 */
export function useServerEvents(handler: Listener) {
  const hub = useContext(EventsContext);
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!hub) return;
    return hub.subscribe((event) => handlerRef.current(event));
  }, [hub]);
}
