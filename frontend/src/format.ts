/**
 * Display formatting helpers (DESIGN.md 7.10). Pure functions, no React.
 * Numbers are formatted for tabular display; pair them with the `.num` class.
 */

const NUM = new Intl.NumberFormat('en-US');

/** 1919 → "1,919". null/undefined/NaN → "—". */
export function num(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return NUM.format(n);
}

/** 25768 → "25.8k", 999 → "999", 1250000 → "1.3M". */
export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  const trim = (s: string) => s.replace(/\.0$/, '');
  if (abs < 1000) return NUM.format(n);
  if (abs < 100_000) return `${trim((n / 1000).toFixed(1))}k`;
  if (abs < 1_000_000) return `${Math.round(n / 1000)}k`;
  if (abs < 100_000_000) return `${trim((n / 1_000_000).toFixed(1))}M`;
  return `${Math.round(n / 1_000_000)}M`;
}

/** Seconds → "4:44"; "1:02:03" from one hour; "36 h" from a day. null/negative → "—". */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '—';
  // Over a day is not a track length (a legacy millisecond value): keep it short.
  if (seconds >= 86400) return `${num(Math.round(seconds / 3600))} h`;
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, '0');
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${ss}`;
  return `${m}:${ss}`;
}

/** Total seconds → "53 min" / "1 h 12 min" (album running time). */
export function longDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) return '—';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${Math.max(1, minutes)} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/**
 * Integer percentage 0..100. Floors, so 99.9% never reads as 100: 100 is shown
 * only when done >= total. total 0 → 0.
 */
export function pct(done: number, total: number): number {
  if (!total || total <= 0 || !Number.isFinite(done)) return 0;
  if (done >= total) return 100;
  if (done <= 0) return 0;
  return Math.min(99, Math.floor((done / total) * 100));
}

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

const DATE_SHORT = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' });
const DATE_YEAR = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

/**
 * Relative time: "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago",
 * then a short date ("12 Oct", or "12 Oct 2024" in another year). Future times
 * read "in 5 min" / "in 3 h" / "in 2 days". Invalid input → "—".
 */
export function relTime(value: string | number | Date | null | undefined, now: number = Date.now()): string {
  const d = toDate(value);
  if (!d) return '—';
  const diff = now - d.getTime();
  const future = diff < 0;
  const abs = Math.abs(diff);
  const min = Math.round(abs / 60_000);
  const hours = Math.round(abs / 3_600_000);
  const days = Math.round(abs / 86_400_000);
  const wrap = (s: string) => (future ? `in ${s}` : `${s} ago`);
  if (abs < 45_000) return future ? 'in a moment' : 'just now';
  if (min < 60) return wrap(`${Math.max(1, min)} min`);
  if (hours < 24) return wrap(`${hours} h`);
  if (days === 1) return future ? 'tomorrow' : 'yesterday';
  if (days < 7) return wrap(`${days} days`);
  return d.getFullYear() === new Date(now).getFullYear() ? DATE_SHORT.format(d) : DATE_YEAR.format(d);
}

/** Absolute date + time for tooltips: "30 Oct 2026, 14:02". Invalid → "—". */
export function absTime(value: string | number | Date | null | undefined): string {
  const d = toDate(value);
  return d ? DATE_TIME.format(d) : '—';
}

/**
 * Retry information for a wanted item (moved from Wanted.tsx). Same output as
 * before ("next run", "in 3h (2 tries)", "next run (2 tries)"), except that an
 * item that was never attempted reads "Never tried".
 */
export function formatRetry(retryAfter: string | null | undefined, retryCount: number | null | undefined): string {
  if (!retryAfter) return retryCount ? 'next run' : 'Never tried';
  const ts = new Date(retryAfter);
  if (isNaN(ts.getTime())) return 'next run';
  const diffMs = ts.getTime() - Date.now();
  const attempts = retryCount ? ` (${retryCount} tries)` : '';
  if (diffMs <= 0) return `next run${attempts}`;
  const hours = diffMs / 3600000;
  if (hours < 1) return `in ${Math.max(1, Math.round(diffMs / 60000))}m${attempts}`;
  if (hours < 48) return `in ${Math.round(hours)}h${attempts}`;
  return `in ${Math.round(hours / 24)}d${attempts}`;
}

/** "1 album" / "3 albums"; pass an explicit plural for irregular nouns. */
export function plural(n: number, noun: string, pluralNoun?: string): string {
  return `${num(n)} ${n === 1 ? noun : (pluralNoun ?? `${noun}s`)}`;
}

/**
 * Parses the API's genre field, a Python-repr list string like "['Rock', 'Indie']",
 * a JSON array, or a plain comma list. Never throws.
 */
export function parseGenres(raw: string | string[] | null | undefined): string[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.filter(Boolean);
  const s = raw.trim();
  if (!s || s === '[]') return [];
  if (s.startsWith('[')) {
    try {
      const parsed = JSON.parse(s);
      if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean);
    } catch {
      /* Python repr: fall through */
    }
    return Array.from(s.matchAll(/'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"/g))
      .map(m => (m[1] ?? m[2] ?? '').replace(/\\(.)/g, '$1').trim())
      .filter(Boolean);
  }
  return s.split(',').map(g => g.trim()).filter(Boolean);
}

/** True when the string contains CJK characters (titles get tracking 0, DESIGN.md 4.1). */
export function hasCjk(s: string | null | undefined): boolean {
  return !!s && /[぀-ヿ㐀-䶿一-鿿가-힯ｦ-ﾟ]/.test(s);
}

/** Up to two initials for a monogram: "Westside Gunn" → "WG", "2814" → "28". */
export function initials(name: string | null | undefined): string {
  const words = (name ?? '').replace(/[^\p{L}\p{N}\s]/gu, ' ').trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return Array.from(words[0]).slice(0, 2).join('').toUpperCase();
  return (Array.from(words[0])[0] + Array.from(words[1])[0]).toUpperCase();
}
