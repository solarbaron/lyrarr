import type { QueryClient } from '@tanstack/react-query';

// Query-key families touched when background work changes the library.
// A family matches its exact key, the plural ('album' → 'albums') and any
// 'family-…' key ('artist' → 'artist-albums', 'dashboard' → 'dashboard-stats').
// 'lyrics-search' is deliberately absent: refetching it re-queries providers.
// 'nav' covers the sidebar counts ('nav-counts').
// 'audit' covers the library audit (checks, runs, issues, summary): a finished
// download can resolve pending "Re-search" issues, a finished audit changes tracks.
export const LIBRARY_FAMILIES = [
  'artist', 'album', 'track', 'wanted', 'dashboard', 'history',
  'library', 'language', 'lyrics-read', 'lyrics-versions', 'nav', 'audit',
];

/**
 * Unfiltered library totals. Same key (and request) as the "All" tab count on
 * Artists / Albums with no filter set ({ search: undefined, … } hashes as {}),
 * so the sidebar, Home and the list pages share one pageSize=1 request.
 */
export const ARTIST_TOTAL_KEY = ['artists', 'count', 'all', {}] as const;
export const ALBUM_TOTAL_KEY = ['albums', 'count', 'all', {}] as const;

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
