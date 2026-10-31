import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { bulkInstrumental, setInstrumentalOverride } from '../../api';
import { errorCode, errorMessage, notifyError } from '../../notify';
import { refreshQueries } from '../../queryRefresh';
import { plural } from '../../format';
import { useConfirm } from '../ui';
import type { InstrumentalAction, InstrumentalBulkResult } from '../../types';

export interface InstrumentalTarget {
  id: number;
  title: string;
  /** The track has a lyrics file now (status available/manual): marking it instrumental archives it. */
  hasLyrics?: boolean;
}

/** What changed after an instrumental action: tracks, albums (complete/partial), Wanted, audit counts. */
const FAMILIES = ['track', 'album', 'wanted', 'nav', 'audit', 'library', 'lyrics-read', 'lyrics-versions', 'dashboard'];

const SKIP_REASONS: Record<string, string> = {
  user_lyrics: 'lyrics you saved, translated or generated',
  archive_failed: 'the current lyrics file could not be archived',
  not_found: 'no longer in the library',
};

function skippedText(skipped: InstrumentalBulkResult['skipped']): string {
  const byReason = new Map<string, number>();
  skipped.forEach(s => byReason.set(s.reason, (byReason.get(s.reason) ?? 0) + 1));
  return [...byReason.entries()].map(([r, n]) => `${n} skipped: ${SKIP_REASONS[r] ?? r.replace(/_/g, ' ')}`).join('; ');
}

/**
 * The user's instrumental decisions (PLAN §2.4, §5.3), shared by Album detail, Wanted
 * ▸ Instrumental review and the inspector. Every error toast shows the server message.
 */
export function useInstrumentalActions() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [busy, setBusy] = useState<Set<string>>(new Set());

  const track = (key: string, on: boolean) => setBusy(prev => {
    const next = new Set(prev);
    if (on) next.add(key); else next.delete(key);
    return next;
  });
  const refresh = useCallback(() => refreshQueries(queryClient, FAMILIES), [queryClient]);

  /** "Not instrumental — search again": override never + an immediate search. No confirm. */
  const notInstrumental = useCallback(async (t: InstrumentalTarget, opts: { search?: boolean } = {}) => {
    const search = opts.search ?? true;
    const key = `vocal-${t.id}`;
    track(key, true);
    try {
      const r = await setInstrumentalOverride(t.id, 'never', { search });
      notifications.show({
        title: search ? `Searching lyrics for ${t.title}` : 'Kept as vocal',
        message: search
          ? (r?.queued === false ? 'Marked as not instrumental. A search is already running; it is picked up on the next run.' : 'Marked as not instrumental. Results appear when the search finishes.')
          : `${t.title} won't be flagged instrumental automatically again.`,
        color: 'success',
      });
      await refresh();
    } catch (err) {
      notifyError('Not changed', err);
    } finally {
      track(key, false);
    }
  }, [refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  /** "Re-check": clears the flag (override untouched) so the policy evaluates the track again. */
  const recheck = useCallback(async (t: InstrumentalTarget) => {
    const key = `recheck-${t.id}`;
    track(key, true);
    try {
      await bulkInstrumental({ trackIds: [t.id], action: 'recheck' });
      notifications.show({
        title: 'Re-check queued',
        message: `${t.title} is searched again on the next run; it is flagged again only with fresh evidence.`,
        color: 'success',
      });
      await refresh();
    } catch (err) {
      notifyError('Not changed', err);
    } finally {
      track(key, false);
    }
  }, [refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * "Mark instrumental": override always. An automatic lyrics file is archived first
   * (confirm); a 409 user_lyrics asks again and retries with force.
   */
  const markInstrumental = useCallback(async (t: InstrumentalTarget) => {
    if (t.hasLyrics) {
      const ok = await confirm({
        title: 'Mark as instrumental?',
        message: `${t.title} will be marked instrumental and never searched again. Its current lyrics file is archived to version history first, so it can be restored from the lyrics editor.`,
        confirmLabel: 'Mark instrumental',
      });
      if (!ok) return;
    }
    const key = `mark-${t.id}`;
    track(key, true);
    const done = async () => {
      notifications.show({ title: 'Marked instrumental', message: `${t.title} won't be searched for lyrics.`, color: 'success' });
      await refresh();
    };
    try {
      await setInstrumentalOverride(t.id, 'always');
      await done();
    } catch (err) {
      if (errorCode(err) === 'user_lyrics') {
        const ok = await confirm({
          title: 'Mark instrumental anyway?',
          message: 'This track has lyrics you saved. Mark it instrumental anyway? The file is archived first.',
          confirmLabel: 'Mark anyway',
          tone: 'danger',
        });
        if (ok) {
          try {
            await setInstrumentalOverride(t.id, 'always', { force: true });
            await done();
          } catch (err2) {
            notifyError('Not marked instrumental', err2);
          }
        }
      } else {
        notifyError('Not marked instrumental', err);
      }
    } finally {
      track(key, false);
    }
  }, [confirm, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Bulk action over track ids. `confirm` on rows with user lyrics comes back in
   * `skipped` (reason user_lyrics): offer "Mark anyway" with force for those.
   * Returns the result (null on error) so callers can clear their selection.
   */
  const bulk = useCallback(async (
    ids: number[], action: InstrumentalAction, opts: { search?: boolean; successTitle: string; successMessage: (n: number) => string },
  ): Promise<InstrumentalBulkResult | null> => {
    const key = `bulk-${action}`;
    track(key, true);
    try {
      const r = await bulkInstrumental({ trackIds: ids, action, search: opts.search });
      const skipped = r.skipped ?? [];
      const userLyrics = skipped.filter(s => s.reason === 'user_lyrics').map(s => s.trackId);
      notifications.show({
        title: opts.successTitle,
        message: [opts.successMessage(r.updated ?? 0), skipped.length ? skippedText(skipped) : ''].filter(Boolean).join('. '),
        color: skipped.length ? 'warning' : 'success',
        autoClose: skipped.length ? 10000 : undefined,
      });
      await refresh();
      if (action === 'confirm' && userLyrics.length) {
        const ok = await confirm({
          title: 'Mark the rest as instrumental?',
          message: `${plural(userLyrics.length, 'track has', 'tracks have')} lyrics you saved, translated or generated, so ${userLyrics.length === 1 ? 'it was' : 'they were'} skipped. Mark ${userLyrics.length === 1 ? 'it' : 'them'} instrumental anyway? Each file is archived to version history first.`,
          confirmLabel: 'Mark anyway',
          tone: 'danger',
        });
        if (ok) {
          const forced = await bulkInstrumental({ trackIds: userLyrics, action: 'confirm', force: true });
          const left = forced.skipped ?? [];
          notifications.show({
            title: 'Marked instrumental',
            message: [`${plural(forced.updated ?? 0, 'track')} marked instrumental`, left.length ? skippedText(left) : ''].filter(Boolean).join('. '),
            color: left.length ? 'warning' : 'success',
          });
          await refresh();
        }
      }
      return r;
    } catch (err) {
      notifications.show({ title: 'Not changed', message: errorMessage(err), color: 'danger' });
      return null;
    } finally {
      track(key, false);
    }
  }, [confirm, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const isBusy = useCallback((key: string) => busy.has(key), [busy]);

  return { notInstrumental, recheck, markInstrumental, bulk, isBusy };
}
