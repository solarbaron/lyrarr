/** LRC helpers shared by the lyrics editor, previews and candidate lists. */

export interface LrcLine {
  key: number;
  /** "[00:12.48]" or "" for untimed lines. */
  tag: string;
  text: string;
  isTs: boolean;
  /** Seconds, or -1 for untimed lines. */
  time: number;
}

/** Parse an LRC timestamp ("[mm:ss.xx]" / "[mm:ss.xxx]") to seconds; -1 when invalid. */
export function parseLrcTimestamp(ts: string): number {
  const m = ts.match(/\[(\d{1,2}):(\d{2})[.:](\d{2,3})\]/);
  if (!m) return -1;
  const mins = parseInt(m[1]);
  const secs = parseInt(m[2]);
  const frac = m[3].length === 3 ? parseInt(m[3]) / 1000 : parseInt(m[3]) / 100;
  return mins * 60 + secs + frac;
}

/** Parse LRC content into structured lines (untimed lines are kept as-is). */
export function parseLrcLines(content: string): LrcLine[] {
  const tsRegex = /^(\[\d{1,2}:\d{2}[.:]\d{2,3}\])\s*(.*)/;
  return content.split('\n').map((line, i) => {
    const m = tsRegex.exec(line.trim());
    if (m) {
      return { key: i, tag: m[1], text: m[2], isTs: true, time: parseLrcTimestamp(m[1]) };
    }
    return { key: i, tag: '', text: line, isTs: false, time: -1 };
  });
}
