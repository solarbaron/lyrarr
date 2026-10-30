import { useState, useEffect, useRef } from 'react';
import { notifications } from '@mantine/notifications';
import { useQueryClient } from '@tanstack/react-query';
import type { SSEEvent } from '../types';
import { useServerEvents } from '../events';
import { refreshQueries } from '../queryRefresh';

interface DownloadItem {
  icon: string;
  title: string;
  provider?: string;
  type: 'cover' | 'lyrics' | 'info' | 'error' | 'success';
}

interface DownloadProgress {
  totalCovers: number;   // albums
  totalLyrics: number;   // tracks
  currentCover: number;
  currentLyric: number;
  active: boolean;
}

// Event types that are pure backend chatter — no value in the feed.
const IGNORED_TYPES = new Set(['task', 'lidarr_event', 'signalr']);

// Batch jobs whose *_complete event means library data changed.
const BATCH_COMPLETE_TYPES = new Set([
  'batch_translate_complete', 'batch_sync_complete', 'batch_delete_complete', 'lyrics_audit_complete',
]);

export default function ActivityFeed() {
  const [items, setItems] = useState<DownloadItem[]>([]);
  const [progress, setProgress] = useState<DownloadProgress>({
    totalCovers: 0, totalLyrics: 0, currentCover: 0, currentLyric: 0, active: false,
  });
  const [open, setOpen] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  useServerEvents((event) => handleEvent(event));

  const showError = (title: string, message: string) => {
    notifications.show({ title, message, color: 'red', autoClose: false });
  };

  const handleEvent = (event: SSEEvent) => {
    const p = event.payload;
    // Replayed history fills the feed on (re)connect but must not fire
    // toasts, animate progress, or light up the "new" badge — it already
    // happened, possibly long ago.
    const live = !event.replay;

    if (IGNORED_TYPES.has(event.type)) return;

    switch (event.type) {
      case 'download_start':
        if (live) {
          setProgress({
            totalCovers: p?.total_covers || 0,
            totalLyrics: p?.total_lyrics || 0,
            currentCover: 0,
            currentLyric: 0,
            active: true,
          });
        }
        addItem({ icon: '🚀', title: p?.message || 'Download started...', type: 'info' });
        break;

      case 'download_progress':
        if (live) {
          setProgress(prev => ({
            ...prev,
            currentCover: p?.metadata_type === 'cover' ? (prev.currentCover + 1) : prev.currentCover,
            currentLyric: p?.metadata_type === 'lyrics' ? (prev.currentLyric + 1) : prev.currentLyric,
          }));
        }
        addItem({
          icon: p?.metadata_type === 'cover' ? '🎨' : '📝',
          title: p?.title || 'Unknown',
          provider: p?.provider,
          type: p?.metadata_type === 'cover' ? 'cover' : 'lyrics',
        });
        break;

      case 'download_complete': {
        // Always end the "Downloading…" state — failed runs included.
        if (live) setProgress(prev => ({ ...prev, active: false }));
        if (p?.error) {
          addItem({ icon: '❌', title: p?.message || `Download failed: ${p.error}`, type: 'error' });
          if (live) showError('Download Failed', p.error);
        } else {
          addItem({ icon: '✅', title: p?.message || 'Download complete', type: 'success' });
        }
        const total = (p?.covers || 0) + (p?.lyrics || 0);
        // Only toast when something was actually downloaded — the scheduled
        // task also completes (with zero) every couple of hours.
        if (live && total > 0) {
          notifications.show({
            title: '✅ Download Complete',
            message: `Downloaded ${p?.covers || 0} covers and ${p?.lyrics || 0} lyrics`,
            color: 'green',
            autoClose: 8000,
          });
        }
        if (live && (total > 0 || p?.error)) refreshQueries(queryClient);
        break;
      }

      case 'download_error':
        addItem({ icon: '❌', title: p?.message || 'Error', type: 'error' });
        break;

      case 'sync_complete':
        addItem({ icon: '🔄', title: p?.message || 'Sync complete', type: 'success' });
        // No toast: background syncs run every few minutes — the feed entry
        // and the refreshed data are the signal.
        if (live) {
          refreshQueries(queryClient, ['artist', 'album', 'dashboard', 'wanted', 'library']);
        }
        break;

      case 'sync_start':
        addItem({ icon: '🔄', title: p?.message || 'Syncing with Lidarr...', type: 'info' });
        break;

      case 'lyrics_audit_error':
        addItem({ icon: '❌', title: p?.message || 'Lyrics audit failed', type: 'error' });
        if (live) showError('Lyrics Audit Failed', p?.message || 'Unknown error');
        break;

      case 'health': {
        const healthy = p?.healthy;
        addItem({
          icon: healthy ? '💚' : '💔',
          title: p?.message || 'Service health changed',
          type: healthy ? 'success' : 'error',
        });
        // Health flips are rare and important — always worth a toast (live only).
        if (live) {
          notifications.show({
            title: healthy ? 'Connection Restored' : 'Connection Problem',
            message: p?.message || '',
            color: healthy ? 'green' : 'red',
            autoClose: healthy ? 8000 : false,
          });
        }
        break;
      }

      default:
        if (BATCH_COMPLETE_TYPES.has(event.type)) {
          const failed = !!p?.error;
          addItem({
            icon: failed ? '❌' : (p?.failed ? '⚠️' : '✅'),
            title: p?.message || (failed ? `Failed: ${p?.error}` : event.type),
            type: failed ? 'error' : 'success',
          });
          if (live) {
            if (failed) showError('Batch Job Failed', p?.error || 'Unknown error');
            refreshQueries(queryClient);
          }
          break;
        }
        addItem({ icon: '📡', title: p?.message || event.type, type: 'info' });
    }

    // The badge means "something happened while you weren't looking" —
    // replayed history doesn't qualify.
    if (live) setHasNew(true);
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
    ? Math.min(100, Math.round((progress.currentCover / progress.totalCovers) * 100)) : 0;
  const lyricsPct = progress.totalLyrics > 0
    ? Math.min(100, Math.round((progress.currentLyric / progress.totalLyrics) * 100)) : 0;

  return (
    <>
      {/* Toggle Button */}
      <button
        onClick={() => { setOpen(!open); setHasNew(false); }}
        aria-label={open ? 'Hide activity feed' : 'Show activity feed'}
        aria-expanded={open}
        title="Activity"
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
        <span aria-hidden="true">{progress.active ? '⏳' : '📡'}</span>
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
        <div role="log" aria-label="Activity feed" style={{
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
                    <span>{Math.min(progress.currentCover, progress.totalCovers)}/{progress.totalCovers} albums</span>
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
                    <span>{Math.min(progress.currentLyric, progress.totalLyrics)}/{progress.totalLyrics} tracks</span>
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
