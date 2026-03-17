// ---------- Core Entities ----------

export interface Artist {
  lidarrArtistId: number;
  mbId: string;
  name: string;
  sortName: string;
  path: string;
  monitored: boolean;
  overview: string;
  fanart: string;
  poster: string;
  tags: string;
  metadata_status: string;
  language_override: string | null;
  translate_target_override: string | null;
  profileId: number | null;
  created_at_timestamp: string | null;
  updated_at_timestamp: string | null;
}

export interface Album {
  lidarrAlbumId: number;
  mbId: string;
  artistId: number;
  title: string;
  year: number;
  path: string;
  monitored: boolean;
  overview: string;
  cover: string;
  genres: string;
  albumType: string;
  cover_status: 'missing' | 'available' | 'manual';
  lyrics_status: 'unknown' | 'partial' | 'complete';
  missing_covers: string;
  missing_lyrics: string;
  profileId: number | null;
  override_cover_format: string | null;
  override_prefer_synced: boolean | null;
  override_download_covers: boolean | null;
  override_download_lyrics: boolean | null;
  created_at_timestamp: string | null;
  updated_at_timestamp: string | null;
  // Joined fields
  artistName?: string;
}

export interface Track {
  lidarrTrackId: number;
  mbId: string;
  albumId: number;
  artistId: number;
  title: string;
  trackNumber: number;
  discNumber: number;
  duration: number;
  path: string;
  hasLyrics: boolean;
  lyrics_status: 'missing' | 'available' | 'manual' | 'blacklisted';
  detected_language: string | null;
  is_synced: boolean;
  created_at_timestamp: string | null;
  updated_at_timestamp: string | null;
}

export interface Profile {
  id: number;
  name: string;
  is_default: boolean;
  download_covers: boolean;
  download_lyrics: boolean;
  cover_providers: string;
  lyrics_providers: string;
  prefer_synced_lyrics: boolean;
  lyrics_selection_mode: string;
  auto_detect_language: boolean;
  auto_translate: string;
  translate_target_lang: string;
  translate_only_foreign: boolean;
  score_threshold: number;
  cover_format: string;
  overwrite_existing: boolean;
  embed_cover_art: boolean;
  created_at_timestamp: string | null;
  updated_at_timestamp: string | null;
}

export interface HistoryEntry {
  id: number;
  action: number;
  description: string;
  metadata_type: string;
  provider: string;
  lidarrAlbumId: number | null;
  lidarrArtistId: number | null;
  lidarrTrackId: number | null;
  timestamp: string;
  metadata_path: string;
}

// ---------- API Responses ----------

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PaginationParams {
  page?: number;
  pageSize?: number;
  search?: string;
}

export interface DashboardStats {
  total_artists: number;
  total_albums: number;
  total_tracks: number;
  covers_available: number;
  covers_missing: number;
  lyrics_available: number;
  lyrics_missing: number;
  recent_downloads: HistoryEntry[];
  downloadHistory?: { date: string; covers: number; lyrics: number }[];
  artistCompletion?: { name: string; covers: number; lyrics: number; total: number }[];
}

// ---------- SSE Events ----------

export interface SSEEvent {
  type: string;
  payload?: {
    message?: string;
    title?: string;
    metadata_type?: string;
    provider?: string;
    covers?: number;
    lyrics?: number;
    total_covers?: number;
    total_lyrics?: number;
    current_cover?: number;
    current_lyric?: number;
    artists_synced?: number;
    albums_synced?: number;
  };
}

// ---------- Cover/Lyrics Search ----------

export interface CoverResult {
  url: string;
  url_large?: string;
  url_small?: string;
  provider: string;
  size?: string;
}

export interface LyricsResult {
  synced_lyrics?: string;
  plain_lyrics?: string;
  provider: string;
  score?: number;
  title?: string;
  artist?: string;
}
