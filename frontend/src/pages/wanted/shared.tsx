import type { QueryClient } from '@tanstack/react-query';
import { Tooltip } from '@mantine/core';
import type { Album, Track } from '../../types';
import { refreshQueries } from '../../queryRefresh';
import { absTime, formatRetry } from '../../format';
import { StatusGlyph } from '../../components/ui';
import c from './Wanted.module.css';

export const LANG_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ru', label: 'Russian' },
  { value: 'ar', label: 'Arabic' },
  { value: 'hi', label: 'Hindi' },
  { value: 'nl', label: 'Dutch' },
  { value: 'sv', label: 'Swedish' },
  { value: 'pl', label: 'Polish' },
  { value: 'tr', label: 'Turkish' },
  { value: 'da', label: 'Danish' },
  { value: 'fi', label: 'Finnish' },
  { value: 'no', label: 'Norwegian' },
  { value: 'el', label: 'Greek' },
  { value: 'he', label: 'Hebrew' },
  { value: 'th', label: 'Thai' },
  { value: 'vi', label: 'Vietnamese' },
  { value: 'id', label: 'Indonesian' },
  { value: 'uk', label: 'Ukrainian' },
  { value: 'cs', label: 'Czech' },
  { value: 'ro', label: 'Romanian' },
  { value: 'hu', label: 'Hungarian' },
];

/** Wanted rows carry retry bookkeeping the shared Track/Album types don't declare. */
export type WantedTrack = Track & {
  artistName?: string;
  albumTitle?: string;
  lyrics_retry_count?: number | null;
  lyrics_retry_after?: string | null;
};

export type WantedAlbum = Album & {
  cover_retry_count?: number | null;
  cover_retry_after?: string | null;
};

export interface WantedStats {
  missing_covers: number;
  total_albums: number;
  missing_lyrics: number;
  untimed_lyrics: number;
  undetected_language: number;
  total_tracks: number;
  covers_complete: number;
  lyrics_complete: number;
  /** Wanted › Instrumental review: legacy-null + likely + conflict + cleared (PLAN §1.8). */
  instrumental_review?: number;
  likely_instrumental?: number;
  /** Rows in "Cleared after upgrade". */
  instrumental_cleared?: number;
}

export const PAGE_SIZES = [25, 50, 100];
export const DEFAULT_PAGE_SIZE = 50;

/** Every wanted list + the stats + the sidebar counts. */
export function refreshWanted(queryClient: QueryClient) {
  return refreshQueries(queryClient, ['wanted', 'nav']);
}

/** File name of a track path ("…/01 - Screen Shot.flac" → "01 - Screen Shot.flac"). */
export function fileName(path: string | null | undefined): string {
  return path ? path.split('/').pop() || path : '';
}

/**
 * Album folder of a track path, for rows that carry no album title
 * ("…/Swans/To Be Kind (2014)/01 - Screen Shot.flac" → "To Be Kind").
 */
export function albumFolder(path: string | null | undefined): string {
  const parts = (path ?? '').split('/').filter(Boolean);
  if (parts.length < 2) return '';
  return parts[parts.length - 2].replace(/\s*[([]\d{4}[)\]]\s*$/, '').trim();
}

/**
 * Row actions on mobile live in DataTable's ⋯ popover, which stays open on its own.
 * Actions that open a dialog close it first (an outside press is what closes it),
 * so it never floats over the full-screen modal.
 */
export function closeRowMenu(e: { currentTarget: EventTarget }) {
  const el = e.currentTarget as HTMLElement;
  if (typeof el.closest === 'function' && el.closest('.mantine-Popover-dropdown')) {
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
  }
}

/**
 * "Never tried" / "in 3h (2 tries)" / "next run" with a glyph: a hollow ring when
 * nothing is scheduled, a warning clock while a retry is waiting (DESIGN.md 8.6).
 */
export function Attempts({ after, count }: { after: string | null | undefined; count: number | null | undefined }) {
  const text = formatRetry(after, count);
  const waiting = !!after && new Date(after).getTime() > Date.now();
  const label = waiting ? `Retry ${text}` : text.charAt(0).toUpperCase() + text.slice(1);
  const tip = after ? `Next automatic try: ${absTime(after)}${count ? ` · ${count} tries so far` : ''}` : (count ? `${count} tries so far` : 'The downloader hasn\'t searched for this yet');
  return (
    <Tooltip label={tip}>
      <span className={c.attempts} data-waiting={waiting || undefined}>
        <StatusGlyph glyph={waiting ? 'clock' : 'ring'} tone={waiting ? 'warning' : 'neutral'} size={10} />
        <span className={c.attemptsText}>{label}</span>
      </span>
    </Tooltip>
  );
}
