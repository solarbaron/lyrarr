import { useState, useEffect, useRef } from 'react';
import { notifications } from '@mantine/notifications';
import type { SSEEvent } from '../types';

interface DownloadItem {
  icon: string;
  title: string;
  provider?: string;
  type: 'cover' | 'lyrics' | 'info' | 'error' | 'success';
}

interface DownloadProgress {
  totalCovers: number;
  totalLyrics: number;
  currentCover: number;
  currentLyric: number;
  active: boolean;
}

export default function ActivityFeed() {
  const [items, setItems] = useState<DownloadItem[]>([]);
  const [progress, setProgress] = useState<DownloadProgress>({
    totalCovers: 0, totalLyrics: 0, currentCover: 0, currentLyric: 0, active: false,
  });
  const [open, setOpen] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const source = new EventSource('/api/events');

    source.onmessage = (e) => {
      try {
        const event: SSEEvent = JSON.parse(e.data);
        handleEvent(event);
      } catch {
        // ignore parse errors
      }
    };

    source.onerror = () => {
      // Reconnect happens automatically with EventSource
    };

    return () => source.close();
  }, []);

  const handleEvent = (event: SSEEvent) => {
    const p = event.payload;

    switch (event.type) {
      case 'download_start':
        setProgress({
          totalCovers: p?.total_covers || 0,
          totalLyrics: p?.total_lyrics || 0,
          currentCover: 0,
          currentLyric: 0,
          active: true,
        });
        addItem({ icon: '🚀', title: p?.message || 'Download started...', type: 'info' });
        setOpen(true);
        break;

      case 'download_progress':
        setProgress(prev => ({
          ...prev,
          currentCover: p?.metadata_type === 'cover' ? (prev.currentCover + 1) : prev.currentCover,
          currentLyric: p?.metadata_type === 'lyrics' ? (prev.currentLyric + 1) : prev.currentLyric,
        }));
        addItem({
          icon: p?.metadata_type === 'cover' ? '🎨' : '📝',
          title: p?.title || 'Unknown',
          provider: p?.provider,
          type: p?.metadata_type === 'cover' ? 'cover' : 'lyrics',
        });
        break;

      case 'download_complete':
        setProgress(prev => ({ ...prev, active: false }));
        addItem({ icon: '✅', title: p?.message || 'Download complete', type: 'success' });
        // Toast notification
        notifications.show({
          title: '✅ Download Complete',
          message: `Downloaded ${p?.covers || 0} covers and ${p?.lyrics || 0} lyrics`,
          color: 'green',
          autoClose: 8000,
        });
        break;

      case 'download_error':
        addItem({ icon: '❌', title: p?.message || 'Error', type: 'error' });
        break;

      case 'sync_complete':
        addItem({ icon: '🔄', title: p?.message || 'Sync complete', type: 'success' });
        // Toast notification for sync
        notifications.show({
          title: '🔄 Sync Complete',
          message: p?.message || `Synced ${p?.artists_synced || 0} artists, ${p?.albums_synced || 0} albums`,
          color: 'blue',
          autoClose: 8000,
        });
        break;

      case 'sync_start':
        addItem({ icon: '🔄', title: p?.message || 'Syncing with Lidarr...', type: 'info' });
        break;

      default:
        addItem({ icon: '📡', title: p?.message || event.type, type: 'info' });
    }

    setHasNew(true);
  };

  const addItem = (item: DownloadItem) => {
    setItems(prev => [...prev.slice(-49), item]);
  };

  // Auto-scroll to bottom
  useEffect(() => {
    if (containerRef.current && open) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [items, open]);

  const coverPct = progress.totalCovers > 0
    ? Math.round((progress.currentCover / progress.totalCovers) * 100) : 0;
  const lyricsPct = progress.totalLyrics > 0
    ? Math.round((progress.currentLyric / progress.totalLyrics) * 100) : 0;

  return (
    <>
      {/* Toggle Button */}
      <button
        onClick={() => { setOpen(!open); setHasNew(false); }}
        style={{
          position: 'fixed', bottom: 20, right: 20,
          width: 48, height: 48, borderRadius: '50%',
          background: progress.active
            ? 'linear-gradient(135deg, #06b6d4, #3b82f6)'
            : 'linear-gradient(135deg, #8b3dff, #6a1bfa)',
          border: 'none', color: 'white', fontSize: 20, cursor: 'pointer',
          boxShadow: progress.active
            ? '0 4px 20px rgba(6, 182, 212, 0.5)'
            : '0 4px 20px rgba(107, 27, 250, 0.4)',
          zIndex: 1000, transition: 'all 0.3s',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          animation: progress.active ? 'pulse 2s ease-in-out infinite' : 'none',
        }}
      >
        {progress.active ? '⏳' : '📡'}
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
          position: 'fixed', bottom: 80, right: 20,
          width: 400, maxHeight: 450, borderRadius: 16,
          background: 'var(--surface-bg, rgba(15,10,35,0.95))',
          border: '1px solid var(--card-border)',
          boxShadow: '0 8px 40px rgba(0,0,0,0.6)',
          zIndex: 999, display: 'flex', flexDirection: 'column',
          overflow: 'hidden', backdropFilter: 'blur(20px)',
        }}>
          {/* Header */}
          <div style={{
            padding: '14px 16px',
            borderBottom: '1px solid var(--card-border)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)' }}>
              {progress.active ? '⏳ Downloading...' : '📡 Activity'}
            </span>
            <button
              onClick={() => setItems([])}
              style={{
                background: 'none', border: 'none', color: 'var(--text-secondary)',
                cursor: 'pointer', fontSize: 11, padding: '4px 8px',
              }}
            >
              Clear
            </button>
          </div>

          {/* Progress Bars */}
          {progress.active && (
            <div className="download-queue-progress" style={{ padding: '12px 16px' }}>
              {progress.totalCovers > 0 && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
                    <span>🎨 Covers</span>
                    <span>{progress.currentCover}/{progress.totalCovers}</span>
                  </div>
                  <div className="download-queue-bar">
                    <div className="download-queue-bar-fill covers" style={{ width: `${coverPct}%` }} />
                  </div>
                </div>
              )}
              {progress.totalLyrics > 0 && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
                    <span>📝 Lyrics</span>
                    <span>{progress.currentLyric}/{progress.totalLyrics}</span>
                  </div>
                  <div className="download-queue-bar">
                    <div className="download-queue-bar-fill lyrics" style={{ width: `${lyricsPct}%` }} />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Items List */}
          <div
            ref={containerRef}
            style={{ flex: 1, overflowY: 'auto', padding: '4px 12px' }}
          >
            {items.length === 0 ? (
              <div style={{
                textAlign: 'center', padding: 30,
                color: 'var(--text-secondary)', fontSize: 13, opacity: 0.6,
              }}>
                No activity yet. Events appear during downloads and syncs.
              </div>
            ) : (
              items.map((item, idx) => (
                <div key={idx} className="download-queue-item">
                  <span className="icon">{item.icon}</span>
                  <span className="title">{item.title}</span>
                  {item.provider && <span className="provider">{item.provider}</span>}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </>
  );
}
