import type { QueryClient } from '@tanstack/react-query';

// Query-key families touched when background work changes the library.
// A family matches its exact key, the plural ('album' → 'albums') and any
// 'family-…' key ('artist' → 'artist-albums', 'dashboard' → 'dashboard-stats').
// 'lyrics-search' is deliberately absent: refetching it re-queries providers.
export const LIBRARY_FAMILIES = [
  'artist', 'album', 'track', 'wanted', 'dashboard', 'history',
  'library', 'language', 'lyrics-read', 'lyrics-versions',
];

/** Invalidate every cached query whose key belongs to one of `families`. */
export function refreshQueries(queryClient: QueryClient, families: string[] = LIBRARY_FAMILIES) {
  return queryClient.invalidateQueries({
    predicate: (q) => {
      const k = q.queryKey[0];
      return typeof k === 'string' &&
        families.some(f => k === f || k === `${f}s` || k.startsWith(`${f}-`));
    },
  });
}
