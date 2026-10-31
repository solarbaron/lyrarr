/** Response shapes used by Home (the API module types these loosely). */

export interface WantedStats {
  missing_covers: number;
  total_albums: number;
  missing_lyrics: number;
  untimed_lyrics: number;
  undetected_language: number;
  total_tracks: number;
  covers_complete: number;
  lyrics_complete: number;
  instrumental_review?: number;
  likely_instrumental?: number;
  /** Instrumental flags an automatic clear removed, waiting for review ("Cleared after upgrade"). */
  instrumental_cleared?: number;
}

export interface LanguageStats {
  languages: Record<string, number>;
  synced: number;
  plain: number;
  total_available: number;
  total_tracks: number;
  total_missing: number;
  providers?: Record<string, number>;
}

export type { ArtistCompletion } from '../../types';

export interface DownloadDay {
  date: string;
  covers: number;
  lyrics: number;
}

export interface ServiceHealth {
  healthy?: boolean;
  status?: string;
  level?: string;
  error?: string;
}

export interface ProviderHealthEntry {
  successes: number;
  failures: number;
  consecutive_failures: number;
  last_failure: string | null;
  disabled_until: string | null;
  available: boolean;
  type: string;
}
