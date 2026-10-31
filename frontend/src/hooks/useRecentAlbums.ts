import { useCallback, useSyncExternalStore } from 'react';

/** One recently opened album (DESIGN.md 7.10). */
export interface RecentAlbum {
  id: number;
  title: string;
  artistName: string;
  cover: string | null;
  /** Year, when known (shown as "artist · year"). */
  year?: number | null;
  ts: number;
}

const KEY = 'lyrarr-recent-albums';
const MAX = 24;
const EMPTY: RecentAlbum[] = [];

let cache: RecentAlbum[] | null = null;
const listeners = new Set<() => void>();

function read(): RecentAlbum[] {
  if (cache) return cache;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(KEY) ?? '[]');
    cache = Array.isArray(parsed)
      ? parsed.filter((a): a is RecentAlbum => a && typeof a.id === 'number' && typeof a.title === 'string').slice(0, MAX)
      : EMPTY;
  } catch {
    cache = EMPTY;
  }
  return cache;
}

function write(next: RecentAlbum[]) {
  cache = next;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: keep the in-memory list for this session */
  }
  listeners.forEach(l => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY || e.key === null) {
      cache = null;
      listener();
    }
  };
  window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('storage', onStorage);
  };
}

/** Recently opened albums, newest first (max 24). AlbumDetail calls `push()` after load. */
export function useRecentAlbums(): {
  items: RecentAlbum[];
  push(album: Omit<RecentAlbum, 'ts'>): void;
  remove(id: number): void;
  clear(): void;
} {
  const items = useSyncExternalStore(subscribe, read, () => EMPTY);
  const push = useCallback((album: Omit<RecentAlbum, 'ts'>) => {
    const rest = read().filter(a => a.id !== album.id);
    write([{ ...album, ts: Date.now() }, ...rest].slice(0, MAX));
  }, []);
  const remove = useCallback((id: number) => write(read().filter(a => a.id !== id)), []);
  const clear = useCallback(() => write(EMPTY), []);
  return { items, push, remove, clear };
}
