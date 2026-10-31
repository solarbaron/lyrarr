/**
 * The single status vocabulary (DESIGN.md 7.3). Every domain status maps to one
 * label, glyph shape, tone and tooltip. Dense tables render it as a StatusDot,
 * detail pages as a StatusChip. Status is never colour-only: shape + word always.
 * "Missing" is a to-do, never an error: neutral, dashed, hollow ring.
 */

export type StatusDomain =
  | 'cover' | 'albumLyrics' | 'trackLyrics' | 'job' | 'health' | 'monitored'
  /** Library audit: severity of a check or issue (critical … info). */
  | 'auditSeverity'
  /** Library audit: state of one issue (open, re-search pending, fixed, …). */
  | 'auditIssue';
export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'accent';
export type Glyph =
  | 'check' | 'ring' | 'half' | 'hand' | 'diamond' | 'clock' | 'lines' | 'note'
  | 'ban' | 'alert' | 'spinner' | 'dot' | 'dot-hollow';

export interface StatusInfo {
  tone: Tone;
  glyph: Glyph;
  label: string;
  tooltip?: string;
  outline?: 'dashed' | 'none';
}

export interface StatusOptions {
  /** Track lyrics: the lyrics file is LRC (time-synced). */
  synced?: boolean;
  /** Album lyrics: [tracks with lyrics, tracks with files] → "Partial 7/11". */
  fraction?: [number, number];
  /** Track lyrics: the user added them (adds a tooltip). */
  manual?: boolean;
  /**
   * Track lyrics, instrumental rows: why it is instrumental (PLAN §2.4). 'legacy' =
   * no reason recorded (flagged by an older version). Omitted: the generic tooltip.
   */
  reason?: 'title' | 'consensus' | 'user' | 'legacy';
  /** Instrumental by consensus: the agreeing sources ("LRCLIB", "NetEase"). */
  sources?: string[];
  /** Instrumental by title: the track title, quoted in the tooltip. */
  title?: string;
}

function instrumentalTooltip(opts: StatusOptions): string {
  switch (opts.reason) {
    case 'title':
      return opts.title ? `Instrumental: the title says so (“${opts.title}”)` : 'Instrumental: the title says so';
    case 'consensus': {
      const n = opts.sources?.length ?? 0;
      return n >= 2
        ? `Instrumental: ${n} sources agree there are no lyrics (${opts.sources!.join(', ')})`
        : 'Instrumental: several sources agree there are no lyrics';
    }
    case 'user':
      return 'Marked instrumental by you';
    case 'legacy':
      return 'Instrumental (flag from an older version, not verified)';
    default:
      return 'Instrumental: no lyrics needed';
  }
}

const MISSING: StatusInfo = { tone: 'neutral', glyph: 'ring', label: 'Missing', outline: 'dashed' };
const NUM = new Intl.NumberFormat('en-US');

function unknown(label = 'Unknown', tooltip?: string): StatusInfo {
  return { tone: 'neutral', glyph: 'diamond', label, tooltip };
}

function titleCase(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ') : s;
}

function fractionText(f?: [number, number]): string {
  return f && f[1] > 0 ? ` ${NUM.format(f[0])}/${NUM.format(f[1])}` : '';
}

/** Maps a domain status (API value) to its display model. Unknown values never throw. */
export function statusModel(
  domain: StatusDomain,
  status: string | boolean | null | undefined,
  opts: StatusOptions = {},
): StatusInfo {
  const s = typeof status === 'boolean' ? String(status) : (status ?? '').toString().trim().toLowerCase();

  switch (domain) {
    case 'cover':
      if (s === 'available' || s === 'saved') return { tone: 'success', glyph: 'check', label: 'Saved' };
      if (s === 'manual') return { tone: 'info', glyph: 'hand', label: 'Manual', tooltip: 'Uploaded by you' };
      if (s === 'missing' || s === '') return MISSING;
      if (s === 'failed' || s === 'error') return { tone: 'danger', glyph: 'alert', label: 'Failed' };
      return unknown(titleCase(s));

    case 'albumLyrics':
      if (s === 'complete' || s === 'available') return { tone: 'success', glyph: 'check', label: 'Complete' };
      if (s === 'partial') return { tone: 'warning', glyph: 'half', label: `Partial${fractionText(opts.fraction)}` };
      if (s === 'missing') return MISSING;
      return unknown('Unknown', 'No tracks with files yet');

    case 'trackLyrics':
      if (s === 'available' || s === 'manual') {
        const tooltip = s === 'manual' || opts.manual ? 'Added manually' : undefined;
        return opts.synced
          ? { tone: 'success', glyph: 'clock', label: 'Synced', tooltip }
          : { tone: 'info', glyph: 'lines', label: 'Plain', tooltip };
      }
      if (s === 'instrumental') return { tone: 'neutral', glyph: 'note', label: 'Instrumental', tooltip: instrumentalTooltip(opts) };
      if (s === 'blacklisted' || s === 'skipped') {
        return {
          tone: 'neutral', glyph: 'ban', label: 'Skipped',
          tooltip: "Skipped: the downloader won't search lyrics for this track",
        };
      }
      if (s === 'missing' || s === '') return MISSING;
      return unknown(titleCase(s));

    case 'job':
      if (s === 'running' || s === 'active' || s === 'started') return { tone: 'accent', glyph: 'spinner', label: 'Running…' };
      if (s === 'queued' || s === 'pending' || s === 'scheduled') return { tone: 'neutral', glyph: 'clock', label: 'Queued' };
      if (s === 'done' || s === 'complete' || s === 'completed' || s === 'success') return { tone: 'success', glyph: 'check', label: 'Done' };
      if (s === 'failed' || s === 'error') return { tone: 'danger', glyph: 'alert', label: 'Failed' };
      if (s === 'cancelled' || s === 'canceled') return { tone: 'neutral', glyph: 'ban', label: 'Cancelled' };
      if (s === 'aborted') return { tone: 'warning', glyph: 'alert', label: 'Aborted', tooltip: 'Stopped early: see the run details' };
      if (s === 'idle' || s === '') return { tone: 'neutral', glyph: 'dot-hollow', label: 'Idle' };
      return unknown(titleCase(s));

    case 'health':
      if (s === 'healthy' || s === 'ok' || s === 'true') return { tone: 'success', glyph: 'check', label: 'OK' };
      if (s === 'warning' || s === 'warn') return { tone: 'warning', glyph: 'alert', label: 'Warning' };
      if (s === 'error' || s === 'unhealthy' || s === 'false' || s === 'failed') return { tone: 'danger', glyph: 'alert', label: 'Error' };
      if (s === 'disabled' || s === 'off') return { tone: 'neutral', glyph: 'dot', label: 'Off' };
      return unknown(titleCase(s) || 'Unknown');

    case 'auditSeverity':
      if (s === 'critical') return { tone: 'danger', glyph: 'alert', label: 'Critical' };
      if (s === 'high') return { tone: 'danger', glyph: 'diamond', label: 'High' };
      if (s === 'medium') return { tone: 'warning', glyph: 'half', label: 'Medium' };
      if (s === 'low') return { tone: 'info', glyph: 'dot', label: 'Low' };
      if (s === 'info') return { tone: 'neutral', glyph: 'dot-hollow', label: 'Info' };
      return unknown(titleCase(s) || 'Unknown');

    case 'auditIssue':
      if (s === 'open' || s === '') return { tone: 'neutral', glyph: 'ring', label: 'Open', outline: 'dashed' };
      if (s === 'pending') {
        return {
          tone: 'accent', glyph: 'clock', label: 'Re-search pending',
          tooltip: 'The downloader will search this track again and keep the current file if nothing better turns up',
        };
      }
      if (s === 'fixed') return { tone: 'success', glyph: 'check', label: 'Fixed' };
      if (s === 'failed') return { tone: 'danger', glyph: 'alert', label: 'Failed' };
      if (s === 'skipped') return { tone: 'neutral', glyph: 'dot-hollow', label: 'Skipped', tooltip: 'No longer applied when the fix ran' };
      if (s === 'ignored') return { tone: 'neutral', glyph: 'ban', label: 'Ignored', tooltip: 'Hidden from future checks' };
      return unknown(titleCase(s));

    case 'monitored':
      if (s === 'true' || s === 'monitored' || s === '1') return { tone: 'neutral', glyph: 'dot', label: 'Monitored' };
      return { tone: 'neutral', glyph: 'dot-hollow', label: 'Unmonitored' };

    default:
      return unknown();
  }
}

/** Every value of every domain, in display order: drives the status legend and the /__ui gallery. */
export const STATUS_VOCABULARY: { domain: StatusDomain; title: string; items: { status: string; opts?: StatusOptions }[] }[] = [
  { domain: 'cover', title: 'Cover', items: [{ status: 'available' }, { status: 'manual' }, { status: 'missing' }] },
  {
    domain: 'albumLyrics', title: 'Album lyrics',
    items: [{ status: 'complete' }, { status: 'partial', opts: { fraction: [7, 11] } }, { status: 'missing' }, { status: 'unknown' }],
  },
  {
    domain: 'trackLyrics', title: 'Track lyrics',
    items: [
      { status: 'available', opts: { synced: true } }, { status: 'available', opts: { synced: false } },
      { status: 'instrumental' }, { status: 'blacklisted' }, { status: 'missing' },
    ],
  },
  { domain: 'job', title: 'Jobs', items: [{ status: 'running' }, { status: 'done' }, { status: 'failed' }, { status: 'cancelled' }] },
  {
    domain: 'auditSeverity', title: 'Audit severity',
    items: [{ status: 'critical' }, { status: 'high' }, { status: 'medium' }, { status: 'low' }, { status: 'info' }],
  },
  {
    domain: 'auditIssue', title: 'Audit issues',
    items: [{ status: 'open' }, { status: 'pending' }, { status: 'fixed' }, { status: 'failed' }, { status: 'ignored' }],
  },
  { domain: 'health', title: 'Health', items: [{ status: 'healthy' }, { status: 'warning' }, { status: 'error' }, { status: 'disabled' }] },
  { domain: 'monitored', title: 'Monitoring', items: [{ status: 'true' }, { status: 'false' }] },
];

/** Foreground colour token for a tone (text, glyphs). Neutral reads as secondary text. */
export function toneColor(tone: Tone): string {
  if (tone === 'neutral') return 'var(--ly-text-2)';
  if (tone === 'accent') return 'var(--ly-accent-text)';
  return `var(--ly-${tone})`;
}

/** Soft background token for a tone (chips, icon tiles). */
export function toneSoft(tone: Tone): string {
  if (tone === 'neutral') return 'var(--ly-neutral-soft)';
  if (tone === 'accent') return 'var(--ly-accent-soft)';
  return `var(--ly-${tone}-soft)`;
}

/** Fill token for a tone in meters and bars. Neutral segments (instrumental) use muted. */
export function toneFill(tone: Tone): string {
  if (tone === 'neutral') return 'var(--ly-muted)';
  if (tone === 'accent') return 'var(--ly-accent)';
  return `var(--ly-${tone})`;
}
