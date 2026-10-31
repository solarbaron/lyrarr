/**
 * Line diff for the audit's Compare dialog (current file vs an archived version).
 * Pure, no imports (scripts/check-events.mjs exercises it). LCS over lines, capped
 * so a pathological file can't freeze the tab.
 */

export interface DiffLine {
  kind: 'same' | 'removed' | 'added';
  text: string;
}

const MAX_LINES = 1500;

function lines(text: string): string[] {
  const out = text.replace(/\r\n?/g, '\n').split('\n');
  if (out.length && out[out.length - 1] === '') out.pop();
  return out;
}

/**
 * Diff `before` → `after`. Lines only in `before` are 'removed', only in `after`
 * 'added'. Inputs over the cap fall back to "all removed, all added".
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = lines(before);
  const b = lines(after);
  if (a.length > MAX_LINES || b.length > MAX_LINES) {
    return [...a.map(text => ({ kind: 'removed' as const, text })), ...b.map(text => ({ kind: 'added' as const, text }))];
  }
  const n = a.length;
  const m = b.length;
  // lcs[i][j] = LCS length of a[i:] and b[j:], flattened.
  const lcs = new Uint16Array((n + 1) * (m + 1));
  const at = (i: number, j: number) => i * (m + 1) + j;
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[at(i, j)] = a[i] === b[j] ? lcs[at(i + 1, j + 1)] + 1 : Math.max(lcs[at(i + 1, j)], lcs[at(i, j + 1)]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] });
      i++; j++;
    } else if (lcs[at(i + 1, j)] >= lcs[at(i, j + 1)]) {
      out.push({ kind: 'removed', text: a[i++] });
    } else {
      out.push({ kind: 'added', text: b[j++] });
    }
  }
  while (i < n) out.push({ kind: 'removed', text: a[i++] });
  while (j < m) out.push({ kind: 'added', text: b[j++] });
  return out;
}

/** Counts for the dialog header: "12 lines differ". */
export function diffStats(diff: DiffLine[]): { added: number; removed: number; same: number } {
  let added = 0; let removed = 0; let same = 0;
  for (const d of diff) {
    if (d.kind === 'added') added++;
    else if (d.kind === 'removed') removed++;
    else same++;
  }
  return { added, removed, same };
}

const LRC_TIME = /^\s*(?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]\s*)+/;
const LRC_META = /^\s*\[[a-z#]{1,16}:[^\]]*\]\s*$/i;

/** True when the text has LRC line timestamps. */
export function isTimed(text: string): boolean {
  return text.split('\n').some(l => LRC_TIME.test(l));
}

/**
 * The lyric text without LRC timestamps and header tags, so a synced file can be
 * compared with a plain one line by line (the words are what matter for "is this the
 * same song").
 */
export function lyricText(text: string): string {
  return text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .filter(l => !LRC_META.test(l))
    .map(l => l.replace(LRC_TIME, '').replace(/<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>/g, '').trimEnd())
    .join('\n');
}
