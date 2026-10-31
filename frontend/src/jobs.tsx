import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { notifications } from '@mantine/notifications';
import { useQueryClient } from '@tanstack/react-query';
import type { SSEEvent } from './types';
import { useServerEvents } from './events';
import { refreshQueries } from './queryRefresh';
import {
  auditAdvanced, auditEventEndsJob, auditFraction, auditJobLabel, auditParts, auditStarted, auditStepText,
  type AuditProgress,
} from './auditProgress';

/**
 * Background-job and activity state for the whole app (DESIGN.md 6.7). This is
 * the event logic that used to live in the floating ActivityFeed, unchanged in
 * behaviour: ignored types, replay rules, the 50-item cap, query refreshes and
 * the toast rules. The shell renders it (now-running card, top-bar progress
 * line, Activity drawer, mobile job bar).
 */

export type ActivityKind = 'cover' | 'lyrics' | 'sync' | 'health' | 'batch' | 'error' | 'info';
export type ActivityTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export interface ActivityItem {
  id: string;
  kind: ActivityKind;
  title: string;
  provider?: string;
  /** Date.now() on arrival for live events; null for replayed history ("Earlier"). */
  receivedAt: number | null;
  tone: ActivityTone;
}

/** What kind of work a job is; picks its icon and label. */
export type JobType = 'download' | 'sync' | 'translate' | 'timing' | 'audit';

export interface JobPartState {
  /** 'Covers' | 'Lyrics' | 'Tracks' | 'Checks' | the current audit check's title. */
  label: string;
  /** 'albums' | 'tracks' | 'checks' | 'items'. */
  unit: string;
  done: number;
  total: number;
  /**
   * The part is driven by per-item `download_item` events (every processed item), so the
   * saved-only `download_progress` must not advance it too.
   */
  byItems?: boolean;
}

export interface JobState {
  active: boolean;
  label: string;
  kind: ActivityKind;
  /** Extra (beyond DESIGN.md 6.7): the specific job, for its icon. */
  type: JobType;
  parts: JobPartState[];
  /** Title of the last item processed (download_progress payload.title). */
  currentTitle?: string;
  startedAt: number;
  /** Items per minute, once at least 10 progress events have been observed. */
  ratePerMin?: number;
  /** Set when the job ended with an error (shown until seen). */
  error?: string;
  /** Extra: false while totals are known but no per-item progress is reported. */
  determinate: boolean;
  /** Library audit runs: the per-check progress (PLAN §5.2 running state). */
  audit?: AuditProgress;
}

export interface JobsValue {
  items: ActivityItem[];
  /** The job to show: a running download/batch first, then a sync, then an unseen failure. */
  job: JobState | null;
  /** Every running job (a sync can run alongside a download). */
  running: JobState[];
  unseen: number;
  unseenError: boolean;
  markSeen(): void;
  clear(): void;
  /** The running library audit (from live progress events), or null. */
  audit: AuditProgress | null;
}

// Event types that are pure backend chatter — no value in the feed.
const IGNORED_TYPES = new Set(['task', 'lidarr_event', 'signalr']);

// High-frequency progress events (PLAN §1.6): they move progress bars but never add
// a feed item or light the "new" badge (one per tick would flood the Activity drawer).
const PROGRESS_ONLY_TYPES = new Set(['lyrics_audit_progress', 'download_scope', 'download_item']);

// Batch jobs whose *_complete event means library data changed.
const BATCH_COMPLETE_TYPES = new Set([
  'batch_translate_complete', 'batch_sync_complete', 'batch_delete_complete', 'lyrics_audit_complete',
]);

// Which running batch job a *_complete event ends.
const BATCH_JOB_TYPES: Record<string, JobType> = {
  batch_translate_complete: 'translate',
  batch_sync_complete: 'timing',
  lyrics_audit_complete: 'audit',
};

const MAX_ITEMS = 50;
// A sync that never reports completion (e.g. Lidarr unreachable mid-run) stops showing as running.
const SYNC_TIMEOUT_MS = 15 * 60 * 1000;
const MIN_EVENTS_FOR_RATE = 10;
const MIN_MINUTES_FOR_RATE = 0.25;

type Slot = 'download' | 'batch' | 'sync';
type Slots = Partial<Record<Slot, JobState & { progressEvents: number }>>;

const JobsContext = createContext<JobsValue | null>(null);

let nextItemId = 1;

function withRate(job: JobState & { progressEvents: number }): JobState & { progressEvents: number } {
  const minutes = (Date.now() - job.startedAt) / 60000;
  return {
    ...job,
    // Only once the rate means something: enough items over a long enough window.
    ratePerMin: job.progressEvents >= MIN_EVENTS_FOR_RATE && minutes >= MIN_MINUTES_FOR_RATE
      ? job.progressEvents / minutes
      : undefined,
  };
}

export function JobsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [failed, setFailed] = useState<JobState | null>(null);
  const [unseen, setUnseen] = useState(0);
  const [unseenError, setUnseenError] = useState(false);

  const showError = (title: string, message: string) => {
    notifications.show({ title, message, color: 'danger', autoClose: false });
  };

  const addItem = (live: boolean, item: Omit<ActivityItem, 'id' | 'receivedAt'>) => {
    setItems(prev => [...prev.slice(-(MAX_ITEMS - 1)), { ...item, id: `a${nextItemId++}`, receivedAt: live ? Date.now() : null }]);
  };

  // Slots live in a ref (events arrive faster than renders) and are mirrored into state.
  const slotsRef = useRef<Slots>({});
  const [slots, setSlots] = useState<Slots>({});
  const commitSlots = (next: Slots) => {
    slotsRef.current = next;
    setSlots(next);
  };
  const updateSlot = (slot: Slot, fn: (job: NonNullable<Slots[Slot]>) => NonNullable<Slots[Slot]>) => {
    const job = slotsRef.current[slot];
    if (job) commitSlots({ ...slotsRef.current, [slot]: fn(job) });
  };

  const startJob = (slot: Slot, job: Omit<JobState, 'active' | 'startedAt'>) => {
    commitSlots({ ...slotsRef.current, [slot]: { ...job, active: true, startedAt: Date.now(), progressEvents: 0 } });
  };

  /** Ends a job (only one of `type` when given); an error keeps it as the failed job until seen. */
  const endJob = (slot: Slot, opts: { error?: string; type?: JobType; failedLabel?: string } = {}) => {
    const ended = slotsRef.current[slot];
    const matches = !!ended && (!opts.type || ended.type === opts.type);
    if (opts.error) {
      const base: JobState = matches && ended
        ? ended
        : { label: '', kind: 'error', type: opts.type ?? 'download', parts: [], startedAt: Date.now(), determinate: false, active: false };
      setFailed({ ...base, label: opts.failedLabel ?? base.label, active: false, error: opts.error });
    }
    if (!matches) return;
    const next = { ...slotsRef.current };
    delete next[slot];
    commitSlots(next);
  };

  /** download_scope / download_item / lyrics_audit_progress: progress only, no feed item, no badge. */
  const handleProgress = (event: SSEEvent) => {
    const p = event.payload;
    if (event.type === 'download_scope') {
      // The real number of items this run will process replaces the start estimate.
      const cover = isCover(p?.metadata_type);
      const unit = cover ? 'albums' : 'tracks';
      const total = typeof p?.total === 'number' ? p.total : 0;
      updateSlot('download', job => {
        const exists = job.parts.some(part => part.unit === unit);
        const parts = exists
          ? job.parts.map(part => (part.unit === unit ? { ...part, total, byItems: true } : part))
          : [...job.parts, { label: cover ? 'Covers' : 'Lyrics', unit, done: 0, total, byItems: true }];
        return { ...job, parts, determinate: true };
      });
      return;
    }
    if (event.type === 'download_item') {
      const unit = isCover(p?.metadata_type) ? 'albums' : 'tracks';
      updateSlot('download', job => withRate({
        ...job,
        parts: job.parts.map(part => (part.unit === unit ? { ...part, done: part.done + 1, byItems: true } : part)),
        currentTitle: p?.title || job.currentTitle,
        progressEvents: job.progressEvents + 1,
      }));
      return;
    }
    if (event.type === 'lyrics_audit_progress') {
      // Re-label the job per check (PLAN §5.1 point 2). After a page refresh mid-run the
      // start event was replayed (no job), so the first live progress event creates it.
      const current = slotsRef.current.batch;
      const prev = current?.type === 'audit' ? current.audit ?? null : null;
      const audit = auditAdvanced(prev, p);
      setAudit(audit);
      if (current?.type === 'audit') {
        updateSlot('batch', job => ({
          ...job,
          label: auditJobLabel(audit.mode),
          parts: auditParts(audit),
          currentTitle: audit.checkTitle,
          determinate: audit.checks > 0,
          audit,
        }));
      } else if (!current) {
        startJob('batch', {
          label: auditJobLabel(audit.mode), kind: 'batch', type: 'audit', determinate: audit.checks > 0,
          parts: auditParts(audit), currentTitle: audit.checkTitle, audit,
        });
      }
    }
  };

  const [audit, setAudit] = useState<AuditProgress | null>(null);

  /** The running audit job's progress (null when the batch slot holds no audit). */
  const auditJob = (): AuditProgress | null => {
    const job = slotsRef.current.batch;
    return job?.type === 'audit' ? job.audit ?? null : null;
  };

  const handleEvent = (event: SSEEvent) => {
    const p = event.payload;
    // Replayed history fills the feed on (re)connect but must not fire
    // toasts, animate progress, or light up the "new" badge — it already
    // happened, possibly long ago.
    const live = !event.replay;

    if (IGNORED_TYPES.has(event.type)) return;

    if (PROGRESS_ONLY_TYPES.has(event.type)) {
      // The server never replays these; ignore any that slip through.
      if (live) handleProgress(event);
      return;
    }

    let error = false;

    switch (event.type) {
      case 'download_start': {
        if (live) {
          const parts: JobPartState[] = [];
          if (p?.total_covers) parts.push({ label: 'Covers', unit: 'albums', done: 0, total: p.total_covers });
          if (p?.total_lyrics) parts.push({ label: 'Lyrics', unit: 'tracks', done: 0, total: p.total_lyrics });
          startJob('download', {
            label: 'Downloading metadata',
            kind: !p?.total_covers && p?.total_lyrics ? 'lyrics' : 'cover',
            type: 'download',
            parts,
            determinate: parts.length > 0,
          });
        }
        addItem(live, { kind: 'info', tone: 'neutral', title: p?.message || 'Download started...' });
        break;
      }

      case 'download_progress': {
        const cover = isCover(p?.metadata_type);
        if (live) {
          const unit = cover ? 'albums' : 'tracks';
          // Saved items only: when download_item drives this part it already counted them.
          updateSlot('download', job => (job.parts.some(part => part.unit === unit && part.byItems) ? job : withRate({
            ...job,
            parts: job.parts.map(part => (part.unit === unit ? { ...part, done: part.done + 1 } : part)),
            currentTitle: p?.title || job.currentTitle,
            progressEvents: job.progressEvents + 1,
          })));
        }
        addItem(live, {
          kind: cover ? 'cover' : 'lyrics',
          tone: 'neutral',
          title: p?.title || 'Unknown',
          provider: p?.provider,
        });
        break;
      }

      case 'download_complete': {
        // Always end the "Downloading…" state — failed runs included.
        if (live) endJob('download', { error: p?.error || undefined, failedLabel: 'Download failed' });
        if (p?.error) {
          error = true;
          addItem(live, { kind: 'error', tone: 'danger', title: p?.message || `Download failed: ${p.error}` });
          if (live) showError('Download failed', p.error);
        } else {
          addItem(live, { kind: 'info', tone: 'success', title: p?.message || 'Download complete' });
        }
        const total = (p?.covers || 0) + (p?.lyrics || 0);
        // Only toast when something was actually downloaded — the scheduled
        // task also completes (with zero) every couple of hours.
        if (live && total > 0) {
          notifications.show({
            title: 'Download complete',
            message: `Downloaded ${p?.covers || 0} covers and ${p?.lyrics || 0} lyrics`,
            color: 'success',
            autoClose: 8000,
          });
        }
        if (live && (total > 0 || p?.error)) refreshQueries(queryClient);
        break;
      }

      case 'download_error':
        error = true;
        addItem(live, { kind: 'error', tone: 'danger', title: p?.message || 'Error' });
        break;

      case 'sync_complete':
        addItem(live, { kind: 'sync', tone: 'success', title: p?.message || 'Sync complete' });
        // No toast: background syncs run every few minutes — the feed entry
        // and the refreshed data are the signal.
        if (live) {
          endJob('sync');
          refreshQueries(queryClient, ['artist', 'album', 'dashboard', 'wanted', 'library', 'nav']);
        }
        break;

      case 'sync_start':
        if (live) startJob('sync', { label: 'Syncing with Lidarr', kind: 'sync', type: 'sync', parts: [], determinate: false });
        addItem(live, { kind: 'sync', tone: 'neutral', title: p?.message || 'Syncing with Lidarr...' });
        break;

      case 'lyrics_audit_error':
        error = true;
        addItem(live, { kind: 'error', tone: 'danger', title: p?.message || 'Library check failed' });
        // A replayed error (the run failed while the stream was down) still ends its job, silently.
        if (auditEventEndsJob(auditJob(), p, live)) {
          if (live) showError('Library check failed', p?.message || 'Unknown error');
          endJob('batch', { error: p?.message || 'Unknown error', type: 'audit', failedLabel: 'Library check failed' });
          setAudit(null);
          refreshQueries(queryClient, ['audit']);
        }
        break;

      case 'instrumental_classified': {
        // Worker and user decisions, one per track (never replayed by the server).
        const title = p?.title || 'Track';
        const text = p?.action === 'flagged'
          ? `Marked instrumental: ${title}${p?.reason ? ` (${p.reason})` : ''}`
          : p?.action === 'cleared'
            ? `Instrumental flag cleared: ${title}`
            : p?.action === 'hint'
              ? `Likely instrumental: ${title}`
              : (p?.message || `Instrumental status changed: ${title}`);
        addItem(live, { kind: 'lyrics', tone: 'neutral', title: text });
        break;
      }

      case 'instrumental_bulk': {
        // One per audit check (or bulk request) that changed instrumental state.
        const parts = [
          p?.flagged ? `${p.flagged} flagged` : '',
          p?.cleared ? `${p.cleared} cleared` : '',
          p?.hinted ? `${p.hinted} likely` : '',
        ].filter(Boolean).join(', ');
        addItem(live, { kind: 'batch', tone: 'neutral', title: p?.message || `Instrumental flags updated${parts ? `: ${parts}` : ''}` });
        if (live) refreshQueries(queryClient, ['wanted', 'track', 'album', 'nav', 'audit']);
        break;
      }

      case 'health': {
        const healthy = p?.healthy;
        error = !healthy;
        addItem(live, {
          kind: 'health',
          tone: healthy ? 'success' : 'danger',
          title: p?.message || 'Service health changed',
        });
        // Health flips are rare and important — always worth a toast (live only).
        if (live) {
          const isLibrary = String(p?.service ?? '').startsWith('library:');
          notifications.show({
            title: isLibrary
              ? (healthy ? 'Music folder back' : 'Music folder not reachable')
              : (healthy ? 'Connection restored' : 'Connection problem'),
            message: p?.message || '',
            color: healthy ? 'success' : 'danger',
            autoClose: healthy ? 8000 : false,
          });
        }
        break;
      }

      default:
        if (BATCH_COMPLETE_TYPES.has(event.type)) {
          const batchFailed = !!p?.error;
          const cancelled = p?.status === 'cancelled' || p?.status === 'aborted';
          error = batchFailed;
          addItem(live, {
            kind: batchFailed ? 'error' : 'batch',
            tone: batchFailed ? 'danger' : cancelled ? 'neutral' : (p?.failed || p?.totals?.failed ? 'warning' : 'success'),
            title: p?.message || (batchFailed ? `Failed: ${p?.error}` : event.type),
          });
          if (live) {
            const type = BATCH_JOB_TYPES[event.type];
            if (type) {
              endJob('batch', {
                error: p?.error || undefined, type,
                failedLabel: type === 'audit' ? 'Library check failed' : 'Batch job failed',
              });
            }
            if (event.type === 'lyrics_audit_complete') setAudit(null);
            else if (p?.error) {
              setFailed({
                label: 'Batch job failed', kind: 'error', type: 'download', parts: [],
                startedAt: Date.now(), determinate: false, active: false, error: p.error,
              });
            }
            if (batchFailed) showError('Batch job failed', p?.error || 'Unknown error');
            refreshQueries(queryClient);
          } else if (event.type === 'lyrics_audit_complete' && auditEventEndsJob(auditJob(), p, false)) {
            // Replayed on reconnect: the run we show as running finished while the stream was
            // down. End its job (no toast, no badge) and reload what it changed.
            endJob('batch', { error: p?.error || undefined, type: 'audit', failedLabel: 'Library check failed' });
            setAudit(null);
            refreshQueries(queryClient);
          }
          break;
        }
        // Batch jobs with a known size (mirrors Mass edit's former progress monitor).
        if (live && event.type === 'batch_translate_start') {
          startJob('batch', {
            label: 'Translating lyrics', kind: 'batch', type: 'translate', determinate: false,
            parts: [{ label: 'Tracks', unit: 'tracks', done: 0, total: p?.total || 0 }],
          });
        } else if (live && event.type === 'batch_sync_start') {
          startJob('batch', {
            label: 'Generating synced lyrics', kind: 'batch', type: 'timing', determinate: false,
            parts: [{ label: 'Tracks', unit: 'tracks', done: 0, total: p?.total || 0 }],
          });
        } else if (live && event.type === 'lyrics_audit_start') {
          // `checks` = number of checks; the old `total` (tracks) is gone (PLAN §5.1).
          const audit = auditStarted(p);
          setAudit(audit);
          startJob('batch', {
            label: auditJobLabel(audit.mode), kind: 'batch', type: 'audit', determinate: audit.checks > 0,
            parts: auditParts(audit), audit,
          });
          refreshQueries(queryClient, ['audit']);
        }
        addItem(live, { kind: 'info', tone: 'neutral', title: p?.message || event.type });
    }

    // The badge means "something happened while you weren't looking" —
    // replayed history doesn't qualify.
    if (live) {
      setUnseen(n => n + 1);
      if (error) setUnseenError(true);
    }
  };

  useServerEvents((event) => handleEvent(event));

  // A sync with no completion event stops counting as running after a while.
  const syncStartedAt = slots.sync?.startedAt;
  useEffect(() => {
    if (syncStartedAt === undefined) return;
    const left = syncStartedAt + SYNC_TIMEOUT_MS - Date.now();
    const timer = setTimeout(() => endJob('sync'), Math.max(0, left));
    return () => clearTimeout(timer);
  }, [syncStartedAt]);

  const markSeen = useCallback(() => {
    setUnseen(0);
    setUnseenError(false);
    setFailed(null);
  }, []);

  const clear = useCallback(() => {
    setItems([]);
  }, []);

  const value = useMemo<JobsValue>(() => {
    const running = (['download', 'batch', 'sync'] as const)
      .map(s => slots[s])
      .filter((j): j is NonNullable<typeof j> => !!j)
      .map(({ progressEvents: _progressEvents, ...job }) => job);
    return {
      items,
      job: running[0] ?? failed,
      running,
      unseen,
      unseenError,
      markSeen,
      clear,
      audit: slots.batch?.type === 'audit' ? (slots.batch.audit ?? audit) : null,
    };
  }, [items, slots, failed, unseen, unseenError, markSeen, clear, audit]);

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

const NO_JOBS: JobsValue = {
  items: [], job: null, running: [], unseen: 0, unseenError: false, markSeen: () => {}, clear: () => {}, audit: null,
};

/**
 * Fixed job state for the /__ui gallery (and tests): renders the real shell parts
 * (now-running card, rail ring, Activity drawer, top-bar job line, mobile job bar,
 * unseen pip) in running/failed states without starting backend jobs. `job`
 * defaults to the first running job, as in JobsProvider.
 */
export function MockJobsProvider({ value, children }: { value: Partial<JobsValue>; children: ReactNode }) {
  const merged = useMemo<JobsValue>(() => {
    const running = value.running ?? [];
    return { ...NO_JOBS, ...value, running, job: value.job !== undefined ? value.job : (running[0] ?? null) };
  }, [value]);
  return <JobsContext.Provider value={merged}>{children}</JobsContext.Provider>;
}

/** Activity items and the running job (inside JobsProvider; inert outside it). */
export function useJobs(): JobsValue {
  return useContext(JobsContext) ?? NO_JOBS;
}

/** metadata_type of download events: 'cover' (download_progress) or 'covers' (download_scope/item). */
function isCover(type: string | undefined): boolean {
  return !!type && type.startsWith('cover');
}

/** Overall progress of a job, 0–1, or null when indeterminate. */
export function jobFraction(job: JobState): number | null {
  if (job.type === 'audit' && job.audit) return auditFraction(job.audit);
  if (!job.determinate) return null;
  const total = job.parts.reduce((s, p) => s + p.total, 0);
  if (total <= 0) return null;
  const done = job.parts.reduce((s, p) => s + Math.min(p.done, p.total), 0);
  return Math.min(1, done / total);
}

/** "~4 min left", from the observed rate (only after enough progress events). */
export function jobEta(job: JobState): string | undefined {
  // Audit checks differ wildly in cost: an ETA from the check count would be fiction.
  if (job.type === 'audit') return undefined;
  if (!job.determinate || !job.ratePerMin) return undefined;
  const left = job.parts.reduce((s, p) => s + Math.max(0, p.total - p.done), 0);
  if (left <= 0) return undefined;
  const minutes = left / job.ratePerMin;
  if (minutes < 1) return 'less than a minute left';
  if (minutes < 90) return `~${Math.round(minutes)} min left`;
  return `~${Math.round(minutes / 60)} h left`;
}

/** "418 / 1,174" across all parts. */
export function jobCount(job: JobState): string | undefined {
  if (job.type === 'audit' && job.audit) return auditStepText(job.audit);
  const total = job.parts.reduce((s, p) => s + p.total, 0);
  if (total <= 0) return undefined;
  const done = job.parts.reduce((s, p) => s + Math.min(p.done, p.total), 0);
  const unit = job.parts.length === 1 ? ` ${job.parts[0].unit}` : '';
  return job.determinate
    ? `${done.toLocaleString()} / ${total.toLocaleString()}${unit}`
    : `${total.toLocaleString()}${unit}`;
}
