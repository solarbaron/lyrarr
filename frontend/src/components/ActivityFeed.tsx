import { useState, useEffect, useRef } from 'react';

interface ActivityEvent {
  type: string;
  payload?: {
    message?: string;
    title?: string;
    metadata_type?: string;
    provider?: string;
    covers?: number;
    lyrics?: number;
  };
}

export default function ActivityFeed() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [open, setOpen] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const source = new EventSource('/api/events');

    source.onmessage = (e) => {
      try {
        const event: ActivityEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev.slice(-49), event]); // Keep last 50
        setHasNew(true);

        // Auto-open on download events
        if (event.type === 'download_start') {
          setOpen(true);
        }
      } catch {
        // ignore parse errors
      }
    };

    source.onerror = () => {
      // Reconnect happens automatically with EventSource
    };

    return () => source.close();
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    if (containerRef.current && open) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [events, open]);

  const getIcon = (type: string, metaType?: string) => {
    if (type === 'download_start') return '🚀';
    if (type === 'download_complete') return '✅';
    if (type === 'download_error') return '❌';
    if (metaType === 'cover') return '🎨';
    if (metaType === 'lyrics') return '📝';
    return '📡';
  };

  const formatEvent = (event: ActivityEvent) => {
    const p = event.payload;
    if (event.type === 'download_start') return p?.message || 'Download started...';
    if (event.type === 'download_complete') return p?.message || 'Download complete';
    if (event.type === 'download_progress') {
      return `${p?.metadata_type === 'cover' ? 'Cover' : 'Lyrics'}: ${p?.title || 'Unknown'} (${p?.provider})`;
    }
    if (event.type === 'download_error') return p?.message || 'Error';
    if (event.type === 'task') return 'Task status updated';
    return event.type;
  };

  return (
    <>
      {/* Toggle Button */}
      <button
        onClick={() => { setOpen(!open); setHasNew(false); }}
        style={{
          position: 'fixed',
          bottom: 20,
          right: 20,
          width: 48,
          height: 48,
          borderRadius: '50%',
          background: 'linear-gradient(135deg, #8b3dff, #6a1bfa)',
          border: 'none',
          color: 'white',
          fontSize: 20,
          cursor: 'pointer',
          boxShadow: '0 4px 20px rgba(107, 27, 250, 0.4)',
          zIndex: 1000,
          transition: 'all 0.2s',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        📡
        {hasNew && !open && (
          <span style={{
            position: 'absolute', top: -2, right: -2,
            width: 12, height: 12, borderRadius: '50%',
            background: '#ef4444', border: '2px solid var(--bg-primary)',
          }} />
        )}
      </button>

      {/* Panel */}
      {open && (
        <div style={{
          position: 'fixed',
          bottom: 80,
          right: 20,
          width: 380,
          maxHeight: 400,
          borderRadius: 16,
          background: 'var(--surface-bg, rgba(15,10,35,0.95))',
          border: '1px solid var(--card-border)',
          boxShadow: '0 8px 40px rgba(0,0,0,0.6)',
          zIndex: 999,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          backdropFilter: 'blur(20px)',
        }}>
          <div style={{
            padding: '14px 16px',
            borderBottom: '1px solid var(--card-border)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)' }}>
              Activity Feed
            </span>
            <button
              onClick={() => setEvents([])}
              style={{
                background: 'none', border: 'none', color: 'var(--text-secondary)',
                cursor: 'pointer', fontSize: 11, padding: '4px 8px',
              }}
            >
              Clear
            </button>
          </div>

          <div
            ref={containerRef}
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '8px 12px',
            }}
          >
            {events.length === 0 ? (
              <div style={{
                textAlign: 'center', padding: 30,
                color: 'var(--text-secondary)', fontSize: 13, opacity: 0.6,
              }}>
                No activity yet. Events will appear here when downloads run.
              </div>
            ) : (
              events.map((event, idx) => (
                <div key={idx} style={{
                  padding: '8px 10px',
                  borderRadius: 8,
                  marginBottom: 4,
                  background: event.type === 'download_complete' ? 'rgba(34,197,94,0.1)' :
                    event.type === 'download_error' ? 'rgba(239,68,68,0.1)' : 'transparent',
                  display: 'flex',
                  gap: 8,
                  alignItems: 'flex-start',
                  fontSize: 12,
                  lineHeight: 1.4,
                }}>
                  <span style={{ flexShrink: 0, fontSize: 14 }}>
                    {getIcon(event.type, event.payload?.metadata_type)}
                  </span>
                  <span style={{ color: 'var(--text-primary)' }}>
                    {formatEvent(event)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </>
  );
}
