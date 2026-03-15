import axios from 'axios';
import type {
  Artist, Album, Track, Profile, HistoryEntry,
  PaginatedResponse, PaginationParams, DashboardStats,
  CoverResult, LyricsResult,
} from './types';

const api = axios.create({ baseURL: '/api' });

// Re-export types for convenience
export type { PaginatedResponse, PaginationParams };

// ---------- Artists ----------
export const getArtists = (params?: PaginationParams) =>
  api.get('/artists', { params }).then(r => r.data as PaginatedResponse<Artist>);
export const getArtist = (id: number) => api.get(`/artists/${id}`).then(r => r.data as Artist);

// ---------- Albums ----------
export const getAlbums = (params?: PaginationParams & { artistId?: number }) =>
  api.get('/albums', { params }).then(r => r.data as PaginatedResponse<Album>);
export const getAlbum = (id: number) => api.get(`/albums/${id}`).then(r => r.data as Album & { tracks: Track[]; artistName: string; artistMbId: string; profileName: string });

// ---------- Tracks ----------
export const getTracks = (params?: PaginationParams & { albumId?: number; artistId?: number }) =>
  api.get('/tracks', { params }).then(r => r.data as PaginatedResponse<Track>);
export const getTrack = (id: number) => api.get(`/tracks/${id}`).then(r => r.data as Track);

// ---------- Profiles ----------
export const getProfiles = () => api.get('/profiles').then(r => r.data as Profile[]);
export const getProfile = (id: number) => api.get(`/profiles/${id}`).then(r => r.data as Profile);
export const createProfile = (data: Partial<Profile>) => api.post('/profiles', data).then(r => r.data);
export const updateProfile = (id: number, data: Partial<Profile>) => api.put(`/profiles/${id}`, data).then(r => r.data);
export const deleteProfile = (id: number) => api.delete(`/profiles/${id}`).then(r => r.data);
export const massAssignProfile = (data: { profileId: number; artistIds?: number[]; albumIds?: number[] }) =>
  api.post('/profiles/mass-assign', data).then(r => r.data);
export const bulkAssignProfile = (data: { profileId: number; mode: 'all' | 'unassigned' }) =>
  api.post('/profiles/bulk-assign', data).then(r => r.data);

// ---------- Metadata ----------
export const searchCovers = (albumId: number) =>
  api.get(`/metadata/covers/search/${albumId}`).then(r => r.data as { results: CoverResult[] });
export const downloadCover = (albumId: number, data: { url: string; provider: string }) =>
  api.post(`/metadata/covers/download/${albumId}`, data).then(r => r.data);
export const searchLyrics = (trackId: number) =>
  api.get(`/metadata/lyrics/search/${trackId}`).then(r => r.data as { results: LyricsResult[] });
export const downloadLyrics = (trackId: number, data: { synced_lyrics?: string; plain_lyrics?: string; provider: string }) =>
  api.post(`/metadata/lyrics/download/${trackId}`, data).then(r => r.data);
export const readLyrics = (trackId: number) =>
  api.get(`/metadata/lyrics/read/${trackId}`).then(r => r.data as { content: string; type: 'synced' | 'plain' });
export const uploadLyrics = (trackId: number, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post(`/metadata/lyrics/upload/${trackId}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data);
};
export const saveLyricsFromEditor = (trackId: number, content: string, isSynced: boolean) =>
  api.post(`/metadata/lyrics/download/${trackId}`, {
    [isSynced ? 'synced_lyrics' : 'plain_lyrics']: content,
    provider: 'editor',
  }).then(r => r.data);
export const translateLyrics = (trackId: number, data: { content: string; targetLang: string; mode: string }) =>
  api.post(`/metadata/lyrics/translate/${trackId}`, data).then(r => r.data);
export const generateSyncedLyrics = (trackId: number, data: { content: string; model?: string }) =>
  api.post(`/metadata/lyrics/sync-generate/${trackId}`, data).then(r => r.data);

// ---------- History ----------
export const getHistory = () => api.get('/history').then(r => r.data as HistoryEntry[]);

// ---------- Wanted ----------
export const getWantedCovers = (params?: PaginationParams) =>
  api.get('/wanted/covers', { params }).then(r => r.data as PaginatedResponse<Album>);
export const getWantedLyrics = (params?: PaginationParams) =>
  api.get('/wanted/lyrics', { params }).then(r => r.data as PaginatedResponse<Track & { artistName?: string; albumTitle?: string }>);

// ---------- System ----------
export const getTasks = () => api.get('/system/tasks').then(r => r.data);
export const getSystemStatus = () => api.get('/system/status').then(r => r.data);
export const getSettings = () => api.get('/system/settings').then(r => r.data);
export const saveSettings = (data: Record<string, unknown>) => api.post('/system/settings', data).then(r => r.data);
export const runTask = (taskId: string) => api.post('/system/tasks', { taskId }).then(r => r.data);
export const getLogs = () => api.get('/system/logs').then(r => r.data);
export const getHealth = () => api.get('/system/health').then(r => r.data);
export const testLidarr = (params?: { ip?: string; port?: number; base_url?: string; apikey?: string; ssl?: boolean }) =>
  api.post('/system/test/lidarr', params || {}).then(r => r.data);
export const triggerSync = () => api.post('/system/sync').then(r => r.data);
export const testNotification = () => api.post('/system/test/notification').then(r => r.data);
export const exportBackup = () => api.get('/system/backup').then(r => r.data);
export const importBackup = (data: Record<string, unknown>) => api.post('/system/restore', data).then(r => r.data);

// ---------- Search ----------
export const globalSearch = (q: string) => api.get('/search', { params: { q } }).then(r => r.data);

// ---------- Dashboard ----------
export const getDashboardStats = () => api.get('/dashboard/stats').then(r => r.data as DashboardStats);

// ---------- Album Updates ----------
export const updateAlbum = (id: number, data: Partial<Album>) => api.put(`/albums/${id}`, data).then(r => r.data);
export const uploadAlbumCover = (id: number, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post(`/albums/${id}/upload-cover`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data);
};

export default api;
