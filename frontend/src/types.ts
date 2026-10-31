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

export type AlbumLyricsStatus = 'complete' | 'partial' | 'missing' | 'unknown';

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
  /**
   * Derived from the album's tracks (blacklisted ignored, instrumental counts
   * as satisfied): complete = all tracks have lyrics, partial = some,
   * missing = none, unknown = no tracks with files.
   */
  lyrics_status: AlbumLyricsStatus;
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
  lyrics_status: TrackLyricsStatus;
  detected_language: string | null;
  is_synced: boolean;
  created_at_timestamp: string | null;
  updated_at_timestamp: string | null;
  // ---- Instrumental policy and provenance (PLAN §1.1.1); optional so older servers still type-check ----
  /** Why the row is instrumental: 'title' | 'consensus' | 'user'; null on an instrumental row = legacy/unknown. */
  instrumental_reason?: InstrumentalReason | null;
  /** Votes (and veto records) behind a flag, a "likely instrumental" hint or a title conflict. */
  instrumental_evidence?: InstrumentalVote[] | null;
  /** The user's decision: 'never' (has vocals) or 'always' (is instrumental). */
  instrumental_override?: InstrumentalOverride | null;
  instrumental_checked_at?: string | null;
  /** The flag an automatic clear removed (bootstrap / audit / recheck): drives "Cleared after upgrade". */
  instrumental_prev?: InstrumentalPrev | null;
  language_source?: 'detected' | 'manual' | 'translated' | null;
  lyrics_source?: 'auto' | 'manual' | 'translated' | 'generated' | null;
  /** Provider that produced the current file. */
  lyrics_provider?: string | null;
  lyrics_source_id?: string | null;
  lyrics_match_score?: number | null;
  lyrics_match_json?: LyricsMatch | null;
  /** Set while an audit "Re-search" asked the worker to try to replace the file. */
  lyrics_replace_requested?: string | null;
  lyrics_retry_count?: number | null;
  lyrics_retry_after?: string | null;
}

export type TrackLyricsStatus = 'missing' | 'available' | 'manual' | 'blacklisted' | 'instrumental';
export type InstrumentalReason = 'title' | 'consensus' | 'user';
export type InstrumentalOverride = 'never' | 'always';

/**
 * One piece of evidence about instrumental-ness (PLAN §1.1.1). Veto records
 * (`veto` set) are why a track with an instrumental-looking title kept its lyrics.
 */
export interface InstrumentalVote {
  source?: string | null;
  provider?: string | null;
  kind?: string | null;
  /** Duration-verified vote; consensus needs at least one. */
  anchor?: boolean | null;
  album_verified?: boolean | null;
  record_id?: string | number | null;
  score?: number | null;
  title_score?: number | null;
  duration_diff?: number | null;
  strategy?: string | null;
  matched_title?: string | null;
  matched_album?: string | null;
  /** 'sidecar' | 'embedded' | 'saved_lyrics' | …: this record blocks instrumental. */
  veto?: string | null;
  at?: string | null;
}

/** A flag removed by an automatic clear (PLAN §1.1.1 instrumental_prev). */
export interface InstrumentalPrev {
  /** 'legacy-unverified' | 'weak-title' | 'periodic-recheck' | 'embedded-lyrics' | … */
  why?: string | null;
  reason?: InstrumentalReason | null;
  actor?: string | null;
  at?: string | null;
  evidence_count?: number | null;
}

export interface LyricsMatch {
  matched_title?: string | null;
  matched_artist?: string | null;
  matched_album?: string | null;
  matched_duration?: number | null;
  title_score?: number | null;
  artist_score?: number | null;
  duration_diff?: number | null;
  gate?: string | null;
  mb_matched?: boolean | null;
  strategy?: string | null;
}

/** Row of GET /api/wanted/instrumental (PLAN §1.8). */
export interface InstrumentalRow extends Track {
  artistName?: string | null;
  albumTitle?: string | null;
  /** Lyrics status of the album (complete / partial / missing / unknown). */
  albumLyricsStatus?: AlbumLyricsStatus | null;
}

export type InstrumentalView = 'flagged' | 'likely' | 'conflict' | 'legacy' | 'cleared' | 'all';
export type InstrumentalAction = 'not_instrumental' | 'confirm' | 'recheck' | 'dismiss_cleared';

export interface InstrumentalBulkResult {
  updated: number;
  skipped: { trackId: number; reason: string }[];
  queued: boolean;
  message?: string;
}

/** Archived lyrics (GET /metadata/lyrics/versions/<track>). */
export interface LyricsVersion {
  id: number;
  timestamp: string;
  lyrics_type: string;
  /** Origin of the archived text (legacy rows: the writer that replaced it). */
  provider: string;
  /** Who replaced or removed it; null on rows from before 2.0. */
  replaced_by?: string | null;
  content: string;
  translated_from?: string | null;
}

/** Error body of the API (`{message, code}`; older endpoints may send `error`). */
export interface ApiError {
  message: string;
  code?: string;
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
  /** 1 downloaded, 2 upgraded, 3 deleted, 4 manual, 5 published, 6 classified, 7 audit repair. */
  action: number;
  /** JSON details (classification evidence, audit repairs); parsed by the server when present. */
  details?: Record<string, unknown> | string | null;
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
  artistCompletion?: ArtistCompletion[];
}

/** One row of dashboard/stats `artistCompletion` (sorted by `missing`, top 20). */
export interface ArtistCompletion {
  id: number;
  name: string;
  totalAlbums: number;
  coversDone: number;
  coverPct: number;
  totalTracks: number;
  lyricsDone: number;
  lyricsPct: number;
  missing: number;
}

// ---------- SSE Events ----------

export interface SSEEvent {
  type: string;
  /** True when this event is history replayed on (re)connect, not a live event. */
  replay?: boolean;
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
    total?: number;
    translated?: number;
    failed?: number;
    deleted?: number;
    mode?: string;
    language?: string;
    is_synced?: boolean;
    service?: string;
    healthy?: boolean;
    synced?: number;
    /** Set on *_complete events when the batch failed. */
    error?: string;
    // ---- Library audit (PLAN §1.6) ----
    run_id?: number;
    trigger?: string;
    /** lyrics_audit_start / _progress: number of checks in the run (start no longer carries `total`). */
    checks?: number;
    phase?: 'detect' | 'apply';
    check_id?: string;
    check_title?: string;
    /** Position of the current check in the run ("check i of n"). */
    check_index?: number;
    /** Items of the current check. */
    current?: number;
    status?: string;
    totals?: Partial<AuditRunTotals>;
    // ---- Instrumental classification (instrumental_classified / instrumental_bulk) ----
    track_id?: number;
    album_id?: number;
    action?: 'flagged' | 'cleared' | 'hint';
    reason?: string | null;
    actor?: string;
    flagged?: number;
    cleared?: number;
    hinted?: number;
    // ---- download_item ----
    outcome?: string;
  };
}

// ---------- Library audit (PLAN §1.8, §4) ----------

export type AuditSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type AuditCategory = 'instrumental' | 'files' | 'matching' | 'state' | 'history' | 'covers';
export type AuditCost = 'db' | 'fs_stat' | 'fs_read' | 'audio' | 'network';
export type AuditFixMode = 'auto' | 'review' | 'report';
export type AuditMode = 'dry_run' | 'apply';
export type AuditRunStatus = 'running' | 'done' | 'failed' | 'cancelled' | 'aborted';
export type AuditIssueStatus = 'open' | 'fixed' | 'failed' | 'skipped' | 'ignored' | 'pending';

export interface AuditCheck {
  id: string;
  title: string;
  description: string;
  category: AuditCategory;
  severity: AuditSeverity;
  cost: AuditCost;
  fix_mode: AuditFixMode;
  needs_library: boolean;
  degrades_without_library?: boolean;
  bootstrap?: boolean;
  /** One-time repair of state left by older versions; never scheduled, never in a default run. */
  bootstrap_only?: boolean;
  scheduled?: boolean;
  default_enabled: boolean;
  pins_evidence?: boolean;
  last: { run_id: number; found: number; open: number; fixed: number; pending: number } | null;
}

export interface AuditRunning {
  run_id: number;
  mode: AuditMode;
  phase?: 'detect' | 'apply' | null;
  check_id?: string | null;
  current?: number | null;
  total?: number | null;
}

export interface AuditRunTotals {
  /** Dry runs: issues found. Apply runs: issues the run worked on. */
  issues: number;
  /** Issues of this run still open when it finished. */
  open: number;
  fixable: number;
  fixable_auto: number;
  review: number;
  fixed: number;
  failed: number;
  skipped: number;
  ignored: number;
  /** Re-searches queued by an apply run that the downloader has not handled yet. */
  pending: number;
  by_severity: Partial<Record<AuditSeverity, number>>;
}

export interface AuditRunCheck {
  check_id: string;
  found: number;
  fixed: number;
  failed: number;
  skipped: number;
  ignored: number;
  status: 'done' | 'skipped' | 'aborted' | 'cancelled' | 'detect_only' | string;
  note?: string | null;
}

export interface AuditRunOptions {
  checks?: string[];
  include?: { fs_read?: boolean; audio?: boolean; network?: boolean };
  scope?: { artistIds?: number[]; albumIds?: number[] };
  issue_ids?: number[];
  network_limit?: number;
}

export interface AuditRun {
  id: number;
  mode: AuditMode;
  trigger: 'user' | 'scheduled' | 'bootstrap' | 'legacy_endpoint' | string;
  status: AuditRunStatus;
  parent_run_id?: number | null;
  options?: AuditRunOptions | null;
  library?: { ok: boolean; checked: number; missing: number; ratio: number } | null;
  totals?: Partial<AuditRunTotals> | null;
  checks?: AuditRunCheck[] | null;
  progress?: { check_id?: string; current?: number; total?: number; cursor?: number | null } | null;
  error?: string | null;
  started_at: string | null;
  finished_at?: string | null;
  duration_s?: number | null;
}

export interface AuditIssue {
  id: number;
  run_id: number;
  check_id: string;
  entity_type: string;
  entity_id: number | null;
  entity_key: string;
  path: string | null;
  severity: AuditSeverity;
  title: string;
  details?: Record<string, unknown> | null;
  fix_action?: string | null;
  fix_mode: AuditFixMode;
  status: AuditIssueStatus;
  message?: string | null;
  lidarrTrackId?: number | null;
  lidarrAlbumId?: number | null;
  lidarrArtistId?: number | null;
  created_at?: string | null;
  resolved_at?: string | null;
  links?: { trackId?: number | null; albumId?: number | null; artistId?: number | null } | null;
  fix?: (AuditFixOption & {
    /** Other repairs the user may pick for this issue (sent as `actions: {issueId: action}`). */
    alternatives?: AuditFixOption[];
  }) | null;
  ignored?: boolean;
}

export interface AuditFixOption {
  action: string | null;
  label: string | null;
  mode: AuditFixMode;
  /** The repair rewrites or removes a file (always archived first). */
  destructive: boolean;
}

export interface AuditSummary {
  latest: {
    run_id: number;
    mode: AuditMode;
    finished_at: string | null;
    open_by_severity: Partial<Record<AuditSeverity, number>>;
    fixable_auto: number;
    review: number;
  } | null;
  bootstrap: {
    run_id: number;
    finished_at: string | null;
    changed: number;
    review: number;
    instrumental_cleared: number;
  } | null;
  instrumental_review: number;
  instrumental_cleared: number;
  unreachable_roots: string[];
}

export interface StartAuditRunBody {
  mode: AuditMode;
  checks?: string[];
  include?: { fs_read?: boolean; audio?: boolean; network?: boolean };
  scope?: { artistIds?: number[]; albumIds?: number[] };
  network_limit?: number;
  resume_run_id?: number;
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
  synced_preview?: string;
  plain_preview?: string;
  provider: string;
  score?: number;
  title?: string;
  artist?: string;
  track_name?: string;
  artist_name?: string;
  source_id?: number;
  source_url?: string;
  duration?: number;
  match_details?: {
    score?: number;
    title_score?: number;
    artist_score?: number;
    duration_score?: number;
    title_query?: string;
    title_result?: string;
    artist_query?: string;
    artist_result?: string;
    duration_diff?: number;
  };
  providers_agree?: number;
  is_composite?: boolean;
  agreement_providers?: string[];
}
