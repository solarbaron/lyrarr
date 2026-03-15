import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

// ---------- Paginated types ----------
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

// ---------- Artists ----------
export const getArtists = (params?: PaginationParams) =>
  api.get('/artists', { params }).then(r => r.data as PaginatedResponse<any>);
export const getArtist = (id: number) => api.get(`/artists/${id}`).then(r => r.data);

// ---------- Albums ----------
export const getAlbums = (params?: PaginationParams & { artistId?: number }) =>
  api.get('/albums', { params }).then(r => r.data as PaginatedResponse<any>);
export const getAlbum = (id: number) => api.get(`/albums/${id}`).then(r => r.data);

// ---------- Tracks ----------
export const getTracks = (params?: PaginationParams & { albumId?: number; artistId?: number }) =>
  api.get('/tracks', { params }).then(r => r.data as PaginatedResponse<any>);
export const getTrack = (id: number) => api.get(`/tracks/${id}`).then(r => r.data);

// ---------- Profiles ----------
export const getProfiles = () => api.get('/profiles').then(r => r.data);
export const getProfile = (id: number) => api.get(`/profiles/${id}`).then(r => r.data);
export const createProfile = (data: any) => api.post('/profiles', data).then(r => r.data);
export const updateProfile = (id: number, data: any) => api.put(`/profiles/${id}`, data).then(r => r.data);
export const deleteProfile = (id: number) => api.delete(`/profiles/${id}`).then(r => r.data);
export const massAssignProfile = (data: { profileId: number; artistIds?: number[]; albumIds?: number[] }) =>
  api.post('/profiles/mass-assign', data).then(r => r.data);
export const bulkAssignProfile = (data: { profileId: number; mode: 'all' | 'unassigned' }) =>
  api.post('/profiles/bulk-assign', data).then(r => r.data);

// ---------- Metadata ----------
export const searchCovers = (albumId: number) => api.get(`/metadata/covers/search/${albumId}`).then(r => r.data);
export const downloadCover = (albumId: number, data: { url: string; provider: string }) =>
  api.post(`/metadata/covers/download/${albumId}`, data).then(r => r.data);
export const searchLyrics = (trackId: number) => api.get(`/metadata/lyrics/search/${trackId}`).then(r => r.data);
export const downloadLyrics = (trackId: number, data: { synced_lyrics?: string; plain_lyrics?: string; provider: string }) =>
  api.post(`/metadata/lyrics/download/${trackId}`, data).then(r => r.data);
export const readLyrics = (trackId: number) => api.get(`/metadata/lyrics/read/${trackId}`).then(r => r.data);
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
export const getHistory = () => api.get('/history').then(r => r.data);

// ---------- Wanted ----------
export const getWantedCovers = (params?: PaginationParams) =>
  api.get('/wanted/covers', { params }).then(r => r.data as PaginatedResponse<any>);
export const getWantedLyrics = (params?: PaginationParams) =>
  api.get('/wanted/lyrics', { params }).then(r => r.data as PaginatedResponse<any>);

// ---------- System ----------
export const getTasks = () => api.get('/system/tasks').then(r => r.data);
export const getSystemStatus = () => api.get('/system/status').then(r => r.data);
export const getSettings = () => api.get('/system/settings').then(r => r.data);
export const saveSettings = (data: any) => api.post('/system/settings', data).then(r => r.data);
export const runTask = (taskId: string) => api.post('/system/tasks', { taskId }).then(r => r.data);
export const getLogs = () => api.get('/system/logs').then(r => r.data);
export const getHealth = () => api.get('/system/health').then(r => r.data);
export const testLidarr = (params?: { ip?: string; port?: number; base_url?: string; apikey?: string; ssl?: boolean }) =>
  api.post('/system/test/lidarr', params || {}).then(r => r.data);
export const triggerSync = () => api.post('/system/sync').then(r => r.data);
export const testNotification = () => api.post('/system/test/notification').then(r => r.data);
export const exportBackup = () => api.get('/system/backup').then(r => r.data);
export const importBackup = (data: any) => api.post('/system/restore', data).then(r => r.data);

// ---------- Search ----------
export const globalSearch = (q: string) => api.get('/search', { params: { q } }).then(r => r.data);

// ---------- Dashboard ----------
export const getDashboardStats = () => api.get('/dashboard/stats').then(r => r.data);

// ---------- Album Updates ----------
export const updateAlbum = (id: number, data: any) => api.put(`/albums/${id}`, data).then(r => r.data);
export const uploadAlbumCover = (id: number, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post(`/albums/${id}/upload-cover`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data);
};

export default api;
