import { useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { batchDownload, batchSyncGenerate, batchTranslate, massAssignProfile } from '../../api';
import { notifyError } from '../../notify';
import { refreshQueries } from '../../queryRefresh';

/** Which items a library action applies to. */
export interface LibraryTarget {
  albumIds?: number[];
  artistIds?: number[];
}

export type DownloadType = 'all' | 'covers' | 'lyrics';

interface ApiMessage {
  message?: string;
}

/** Server message of a refused start (e.g. the 409 "already running"), as before the overhaul. */
function refusal(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { message?: unknown } } };
  const msg = e?.response?.data?.message;
  return typeof msg === 'string' && msg ? msg : fallback;
}

/**
 * Starts a batch download (DESIGN.md 8.2/8.4). Success: a neutral "started" toast
 * (progress appears in Activity). Refused (409 etc.): a warning toast with the
 * server's message, exactly as before ("Not started").
 */
export function useDownloadMutation() {
  return useMutation({
    mutationFn: ({ target, type }: { target: LibraryTarget; type: DownloadType; message?: string }) =>
      batchDownload({ ...target, type }) as Promise<ApiMessage>,
    onSuccess: (data, { message }) => {
      notifications.show({
        title: 'Download started',
        message: message || data?.message || 'Progress appears in Activity.',
      });
    },
    onError: err => {
      notifications.show({
        title: 'Not started',
        message: refusal(err, 'Failed to start download'),
        color: 'warning',
      });
    },
  });
}

/** Assigns a profile to artists or albums and refreshes every artist/album query. */
export function useAssignProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ target, profileId }: { target: LibraryTarget; profileId: number; profileName?: string }) =>
      massAssignProfile({ profileId, ...target }) as Promise<ApiMessage>,
    onSuccess: (data, { profileName }) => {
      notifications.show({
        title: profileName ? `Profile set to ${profileName}` : 'Profile updated',
        message: data?.message || 'Profile updated',
        color: 'success',
      });
      // 'artist' → artist, artists, artist-albums; 'album' → album, albums.
      refreshQueries(queryClient, ['artist', 'album']);
    },
    onError: err => notifyError('Failed to set profile', err),
  });
}

/** Batch translate (Translate lyrics) for an artist or album. */
export function startTranslate(target: LibraryTarget) {
  return batchTranslate(target)
    .then((r: ApiMessage) => {
      notifications.show({ title: 'Translation started', message: r?.message || 'Translating lyrics...' });
    })
    .catch(err => notifyError('Failed to start translation', err));
}

/** Batch Whisper timing (Generate timing) for an artist or album. */
export function startTiming(target: LibraryTarget) {
  return batchSyncGenerate(target)
    .then((r: ApiMessage) => {
      notifications.show({ title: 'Timing generation started', message: r?.message || 'Generating synced lyrics...' });
    })
    .catch(err => notifyError('Failed to start timing generation', err));
}

/**
 * Collects every id matching the active filters, in pages of 500 (the API
 * maximum), sequentially so the shared rate limit isn't hammered.
 */
export async function fetchAllIds<T>(
  fetchPage: (page: number, pageSize: number) => Promise<{ data: T[]; total: number }>,
  getId: (row: T) => number,
): Promise<Set<number>> {
  const ids = new Set<number>();
  const pageSize = 500;
  for (let page = 1; page < 1000; page++) {
    const res = await fetchPage(page, pageSize);
    res.data.forEach(row => ids.add(getId(row)));
    if (res.data.length < pageSize || ids.size >= res.total) break;
  }
  return ids;
}
