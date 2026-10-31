import type {
  AuditCategory, AuditCheck, AuditCost, AuditFixMode, AuditIssue, AuditRun, AuditRunCheck, AuditSeverity,
} from '../../../types';
import type { MeterSegment } from '../../../components/ui';
import { duration, num } from '../../../format';

export const CATEGORIES: { value: AuditCategory; label: string }[] = [
  { value: 'instrumental', label: 'Instrumental' },
  { value: 'files', label: 'Files' },
  { value: 'matching', label: 'Matching' },
  { value: 'state', label: 'State' },
  { value: 'history', label: 'History' },
  { value: 'covers', label: 'Covers' },
];

export const SEVERITIES: AuditSeverity[] = ['critical', 'high', 'medium', 'low', 'info'];
export const SEVERITY_LABELS: Record<AuditSeverity, string> = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info',
};
export const severityRank = (s: string | null | undefined) => {
  const i = SEVERITIES.indexOf(s as AuditSeverity);
  return i < 0 ? SEVERITIES.length : i;
};

/** Issue statuses offered by the Status filter ('open' is the default view). */
export const ISSUE_STATUS_OPTIONS = [
  { value: 'pending', label: 'Re-search pending' },
  { value: 'fixed', label: 'Fixed' },
  { value: 'failed', label: 'Failed' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'ignored', label: 'Ignored' },
];

/** Cost tag: nothing for database-only checks (they are instant). */
export const COST_LABELS: Record<AuditCost, string | null> = {
  db: null,
  fs_stat: 'file check',
  fs_read: 'file scan',
  audio: 'audio tags',
  network: 'network',
};

export const COST_HINTS: Record<AuditCost, string> = {
  db: 'Database only: instant.',
  fs_stat: 'Checks that files exist: seconds on a local disk, a minute or two on a network share.',
  fs_read: 'Reads every lyrics file once: a few minutes on a large library.',
  audio: 'Reads tags from every audio file: slow, off by default.',
  network: 'Asks the lyrics providers again: slow, rate-limited, off by default.',
};

export const FIX_LABELS: Record<AuditFixMode, string> = { auto: 'Safe fix', review: 'Review', report: 'Report' };
export const FIX_HINTS: Record<AuditFixMode, string> = {
  auto: 'Applied by “Apply safe fixes”: only changes Lyrarr’s records or re-queues searches; files are archived before any rewrite.',
  review: 'Applied only to the items you select.',
  report: 'Shown for information; nothing to apply.',
};

export const TRIGGER_LABELS: Record<string, string> = {
  user: 'You',
  scheduled: 'Scheduled',
  bootstrap: 'After upgrade',
  legacy_endpoint: 'Home menu',
};

export const MODE_LABELS: Record<string, string> = { dry_run: 'Dry run', apply: 'Apply' };

/** Checks whose issues keep earlier lyrics (pinned versions) until reviewed. */
export const EVIDENCE_CHECKS = new Set([
  'history.curated_superseded', 'instrumental.batch_deleted_restorable', 'files.whisper_scrambled',
]);
/** Checks whose rows land in Wanted › Instrumental review › Cleared after upgrade. */
export const CLEARED_VIEW_CHECKS = new Set(['instrumental.legacy_unknown', 'instrumental.weak_title_only']);
export const UNREACHABLE_CHECK = 'files.library_unreachable';
export const CLEARED_VIEW_LINK = '/wanted?tab=instrumental&iview=cleared';

export interface CheckRow {
  index: number;
  spec: AuditCheck;
  /** Entry of the shown run (absent: the check did not run). */
  entry?: AuditRunCheck;
  found: number;
  open: number;
  pending: number;
  fixed: number;
}

/** Joins the catalog with the shown run's per-check results. */
export function checkRows(catalog: AuditCheck[], run: AuditRun | null | undefined): CheckRow[] {
  const entries = new Map((run?.checks ?? []).map(e => [e.check_id, e]));
  return catalog.map((spec, index) => {
    const entry = entries.get(spec.id);
    const live = run && spec.last && spec.last.run_id === run.id ? spec.last : null;
    const found = live?.found ?? entry?.found ?? 0;
    const resolved = (entry?.fixed ?? 0) + (entry?.failed ?? 0) + (entry?.skipped ?? 0) + (entry?.ignored ?? 0);
    return {
      index,
      spec,
      entry,
      found,
      open: live?.open ?? Math.max(0, found - resolved),
      pending: live?.pending ?? 0,
      fixed: live?.fixed ?? entry?.fixed ?? 0,
    };
  });
}

/** Open issues by severity → Meter segments (danger = critical + high, warning = medium, info = low, neutral = info). */
export function severitySegments(bySeverity: Partial<Record<AuditSeverity, number>>): { segments: MeterSegment[]; total: number } {
  const n = (s: AuditSeverity) => Math.max(0, bySeverity[s] ?? 0);
  const segments: MeterSegment[] = [
    { value: n('critical') + n('high'), tone: 'danger', label: 'Critical and high' },
    { value: n('medium'), tone: 'warning', label: 'Medium' },
    { value: n('low'), tone: 'info', label: 'Low' },
    { value: n('info'), tone: 'neutral', label: 'Info' },
  ];
  return { segments, total: segments.reduce((s, x) => s + x.value, 0) };
}

/** Open issues per severity for a run, from its rows (fallback: the run totals). */
export function openBySeverity(rows: CheckRow[], run: AuditRun | null | undefined): Partial<Record<AuditSeverity, number>> {
  const out: Partial<Record<AuditSeverity, number>> = {};
  let any = false;
  for (const r of rows) {
    if (r.open <= 0) continue;
    any = true;
    out[r.spec.severity] = (out[r.spec.severity] ?? 0) + r.open;
  }
  return any ? out : { ...(run?.totals?.by_severity ?? {}) };
}

/** Where an issue's item lives in the app. */
export function issueHref(issue: AuditIssue): string | undefined {
  const albumId = issue.links?.albumId ?? issue.lidarrAlbumId;
  const artistId = issue.links?.artistId ?? issue.lidarrArtistId;
  if (albumId) return `/albums/${albumId}`;
  if (artistId) return `/artists/${artistId}`;
  return undefined;
}

export function issueTrackId(issue: AuditIssue): number | null {
  return issue.links?.trackId ?? issue.lidarrTrackId ?? (issue.entity_type === 'track' ? issue.entity_id : null) ?? null;
}

/** Archived version an evidence issue (H-1 / I-7 / F-9) points at. */
export function pinnedVersionId(issue: AuditIssue): number | null {
  const ids = issue.details?.pin_version_ids;
  if (Array.isArray(ids) && typeof ids[0] === 'number') return ids[0];
  const one = issue.details?.version_id;
  return typeof one === 'number' ? one : null;
}

// Internal references (ids, hashes, timestamps) mean nothing to a reader.
const SKIP_DETAIL_KEYS = new Set([
  'pin_version_ids', 'pin_history_ids', 'degraded', 'version_id', 'version_at', 'history_id', 'file_hash',
  'content_hash', 'source_id', 'match', 'chosen_content', 'legacy_title_rule',
]);

const DETAIL_LABELS: Record<string, string> = {
  duration: 'Length',
  needs_album_context: 'Generic title',
  upgrade_retry_count: 'Upgrade retries',
  old: 'Stored',
  new: 'Corrected',
  provider: 'Provider',
  chosen_provider: 'Replacement from',
  score: 'Match score',
  title_score: 'Title match',
};

/** Seconds as m:ss / h:mm:ss; values far beyond a track's length are shown as stored. */
function detailDuration(v: number): string {
  return v > 86400 ? `${num(v)} (stored value)` : duration(v);
}

function humanize(text: string): string {
  const t = text.replace(/_/g, ' ');
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function detailValue(key: string, v: string | number | boolean): string {
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number' && (key === 'duration' || key === 'old' || key === 'new')) return detailDuration(v);
  if (typeof v === 'number' && key.endsWith('score') && v <= 1) return `${Math.round(v * 100)}%`;
  return typeof v === 'string' ? v.replace(/_/g, ' ') : String(v);
}

/** One-line summary of an issue's details: its message, else a few key facts. */
export function detailsSummary(issue: AuditIssue): string {
  const d = issue.details ?? {};
  for (const key of ['summary', 'message', 'note']) {
    const v = d[key];
    if (typeof v === 'string' && v.trim()) return v;
  }
  // 'reason' is usually an enum ("different_titles"): show it readable.
  if (typeof d.reason === 'string' && d.reason.trim()) return humanize(d.reason);
  const facts: string[] = [];
  for (const [k, v] of Object.entries(d)) {
    if (SKIP_DETAIL_KEYS.has(k) || k.endsWith('_id') || k.endsWith('_at') || v === null || v === undefined
      || typeof v === 'object') continue;
    if (k === 'needs_album_context' && v !== true) continue;
    facts.push(`${DETAIL_LABELS[k] ?? humanize(k)}: ${detailValue(k, v as string | number | boolean)}`);
    if (facts.length >= 3) break;
  }
  return facts.join(' · ');
}

/** Fix label for an issue ("Re-search", "Adopt the lyrics file", …). */
export function fixLabel(issue: AuditIssue): string {
  return issue.fix?.label || (issue.fix_action ? issue.fix_action.replace(/_/g, ' ') : 'Apply fix');
}

export function canApply(issue: AuditIssue): boolean {
  return issue.status === 'open' && issue.fix_mode !== 'report' && issue.fix?.mode !== 'report';
}
