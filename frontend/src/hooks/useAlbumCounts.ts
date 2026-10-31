import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getLibraryTree, type LibraryTreeAlbum } from '../api';

/** Per-album lyric counts for meters and "n/m" labels (DESIGN.md 8.0). */
export interface AlbumCounts {
  /** Tracks with files. */
  total: number;
  synced: number;
  /** Tracks with lyrics that are not synced (includes manual uploads and skipped tracks). */
  plain: number;
  instrumental: number;
  missing: number;
  /** synced + plain + instrumental: the "n" of "n/m". */
  done: number;
  coverStatus: string;
}

/**
 * Converts a library-tree album into meter counts. `missing` is authoritative;
 * everything that is not missing counts as done, which matches the server's album
 * lyrics_status (complete when nothing is missing).
 */
export function countsFromTreeAlbum(a: LibraryTreeAlbum): AlbumCounts {
  const total = Math.max(0, a.tracks || 0);
  const missing = Math.min(total, Math.max(0, a.missing || 0));
  const done = total - missing;
  const synced = Math.min(done, Math.max(0, a.synced || 0));
  const instrumental = Math.min(done - synced, Math.max(0, a.instrumental || 0));
  const plain = Math.max(0, done - synced - instrumental);
  return { total, synced, plain, instrumental, missing, done, coverStatus: a.coverStatus };
}

export interface ArtistCounts {
  albums: number;
  tracks: number;
  lyricsDone: number;
  coversDone: number;
}

/**
 * Shared library-tree query (`['library-tree']`, ≈490 KB, 5 min stale) also used by
 * Mass edit. Until it loads, or when it fails, `get()` returns undefined and callers
 * show a StatusDot without a fraction: never fake numbers.
 */
export function useAlbumCounts(enabled = true) {
  const query = useQuery({
    queryKey: ['library-tree'],
    queryFn: getLibraryTree,
    staleTime: 5 * 60_000,
    enabled,
  });

  const { albums, artists } = useMemo(() => {
    const albumMap = new Map<number, AlbumCounts>();
    const artistMap = new Map<number, ArtistCounts>();
    for (const artist of query.data?.artists ?? []) {
      const agg: ArtistCounts = { albums: 0, tracks: 0, lyricsDone: 0, coversDone: 0 };
      for (const album of artist.albums ?? []) {
        const c = countsFromTreeAlbum(album);
        albumMap.set(album.id, c);
        agg.albums += 1;
        agg.tracks += c.total;
        agg.lyricsDone += c.done;
        if (c.coverStatus !== 'missing') agg.coversDone += 1;
      }
      artistMap.set(artist.id, agg);
    }
    return { albums: albumMap, artists: artistMap };
  }, [query.data]);

  return {
    /** Counts for one album, or undefined while loading / on error / unknown id. */
    get: (albumId: number) => albums.get(albumId),
    /** Aggregates for one artist (Artist detail coverage). */
    getArtist: (artistId: number) => artists.get(artistId),
    albums,
    artists,
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
  };
}
