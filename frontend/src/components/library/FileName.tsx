import c from './library.module.css';

/** Last path segment ("music/x/01 - Song.flac" → "01 - Song.flac"). */
export function baseName(path: string | null | undefined): string | null {
  if (!path) return null;
  return path.split(/[\\/]/).pop() || null;
}

/**
 * The prefix every name shares ("Artist - Album - "), cut back to a word
 * boundary so it never ends mid-word. Empty for fewer than two names.
 */
export function sharedPrefix(names: string[]): string {
  if (names.length < 2) return '';
  let prefix = names[0];
  for (const n of names) {
    let i = 0;
    while (i < prefix.length && i < n.length && prefix[i] === n[i]) i++;
    prefix = prefix.slice(0, i);
    if (!prefix) return '';
  }
  const cut = Math.max(prefix.lastIndexOf(' '), prefix.lastIndexOf('-'), prefix.lastIndexOf('_'), prefix.lastIndexOf('.'));
  return cut >= 0 ? prefix.slice(0, cut + 1) : '';
}

/**
 * A file name that shows its identifying tail ("01 - St. Chroma.flac"); the full
 * name is in the tooltip. A prefix shared by every track collapses to a fixed
 * "…" (still read by screen readers), so every row is cut the same way; a lone
 * file ellipsizes its head ("Artist - Album - ") before the tail.
 */
export function FileName({ name, prefix = '' }: { name: string; prefix?: string }) {
  const collapsed = !!prefix && name.startsWith(prefix) && name.length > prefix.length;
  let head = collapsed ? prefix : '';
  if (!head) {
    const i = name.lastIndexOf(' - ');
    if (i > 0) head = name.slice(0, i + 3);
  }
  const tail = name.slice(head.length);
  return (
    <span className={c.fileName} title={name}>
      {collapsed && <span className={c.fileElided} aria-hidden="true">…</span>}
      {head && <span className={c.fileHead} data-collapsed={collapsed || undefined}>{head}</span>}
      <span className={c.fileTail}>{tail}</span>
    </span>
  );
}
