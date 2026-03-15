import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader, Tabs, Pagination, Group, TextInput, Button, Progress } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getWantedCovers, getWantedLyrics, getWantedStats, searchCovers, searchLyrics } from '../api';

export default function WantedPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [coversPage, setCoversPage] = useState(1);
  const [lyricsPage, setLyricsPage] = useState(1);
  const [coversSearch, setCoversSearch] = useState('');
  const [lyricsSearch, setLyricsSearch] = useState('');
  const pageSize = 25;

  const { data: stats } = useQuery({ queryKey: ['wanted-stats'], queryFn: getWantedStats });

  const { data: coversResult, isLoading: loadingCovers } = useQuery({
    queryKey: ['wanted-covers', coversPage, coversSearch],
    queryFn: () => getWantedCovers({ page: coversPage, pageSize, search: coversSearch || undefined }),
  });
  const { data: lyricsResult, isLoading: loadingLyrics } = useQuery({
    queryKey: ['wanted-lyrics', lyricsPage, lyricsSearch],
    queryFn: () => getWantedLyrics({ page: lyricsPage, pageSize, search: lyricsSearch || undefined }),
  });

  const wantedCovers = coversResult?.data || [];
  const coversTotal = coversResult?.total || 0;
  const coversTotalPages = Math.ceil(coversTotal / pageSize);

  const wantedLyrics = lyricsResult?.data || [];
  const lyricsTotal = lyricsResult?.total || 0;
  const lyricsTotalPages = Math.ceil(lyricsTotal / pageSize);

  // Re-search mutations
  const [searchingCoverId, setSearchingCoverId] = useState<number | null>(null);
  const [searchingLyricsId, setSearchingLyricsId] = useState<number | null>(null);

  const coverSearchMutation = useMutation({
    mutationFn: (albumId: number) => {
      setSearchingCoverId(albumId);
      return searchCovers(albumId);
    },
    onSuccess: (data: any, albumId) => {
      const count = data?.results?.length || 0;
      notifications.show({
        title: 'Cover Search',
        message: count > 0 ? `Found ${count} cover(s). Go to album page to select.` : 'No covers found.',
        color: count > 0 ? 'green' : 'yellow',
      });
      setSearchingCoverId(null);
    },
    onError: () => { setSearchingCoverId(null); },
  });

  const lyricsSearchMutation = useMutation({
    mutationFn: (trackId: number) => {
      setSearchingLyricsId(trackId);
      return searchLyrics(trackId);
    },
    onSuccess: (data: any, trackId) => {
      const count = data?.results?.length || 0;
      notifications.show({
        title: 'Lyrics Search',
        message: count > 0 ? `Found ${count} result(s). Go to album page to select.` : 'No lyrics found.',
        color: count > 0 ? 'green' : 'yellow',
      });
      setSearchingLyricsId(null);
    },
    onError: () => { setSearchingLyricsId(null); },
  });

  const coversPct = stats ? Math.round(((stats.covers_complete || 0) / Math.max(stats.total_albums, 1)) * 100) : 0;
  const lyricsPct = stats ? Math.round(((stats.lyrics_complete || 0) / Math.max(stats.total_tracks, 1)) * 100) : 0;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h1 className="page-title">Wanted</h1>
        <p className="page-subtitle">Missing metadata that needs to be downloaded</p>
      </div>

      {/* Summary Stats */}
      {stats && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginBottom: 24 }}>
          <div className="settings-section" style={{ margin: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>🎨 Cover Art</span>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {stats.covers_complete}/{stats.total_albums} complete
              </span>
            </div>
            <Progress value={coversPct} color={coversPct > 80 ? 'green' : coversPct > 50 ? 'yellow' : 'red'} size="lg" />
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
              {stats.missing_covers} missing
            </div>
          </div>
          <div className="settings-section" style={{ margin: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>📝 Lyrics</span>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {stats.lyrics_complete}/{stats.total_tracks} complete
              </span>
            </div>
            <Progress value={lyricsPct} color={lyricsPct > 80 ? 'green' : lyricsPct > 50 ? 'yellow' : 'red'} size="lg" />
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
              {stats.missing_lyrics} missing
            </div>
          </div>
        </div>
      )}

      <Tabs defaultValue="covers" styles={{
        tab: { color: 'var(--text-secondary)', '&[data-active]': { color: 'var(--text-primary)' } }
      }}>
        <Tabs.List mb="lg">
          <Tabs.Tab value="covers">Missing Covers ({coversTotal})</Tabs.Tab>
          <Tabs.Tab value="lyrics">Missing Lyrics ({lyricsTotal})</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="covers">
          <Group mb="md">
            <TextInput
              placeholder="Search albums..."
              value={coversSearch}
              onChange={(e) => { setCoversSearch(e.currentTarget.value); setCoversPage(1); }}
              style={{ flex: 1 }}
              styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
            />
          </Group>

          {loadingCovers ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : wantedCovers.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🎨</div>
              <div className="empty-state-title">No Missing Covers</div>
              <div className="empty-state-message">
                {coversSearch ? 'No albums match your search.' : 'All albums have cover art.'}
              </div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr><th>Album</th><th>Artist</th><th>Year</th><th>Type</th><th>Action</th></tr>
                </thead>
                <tbody>
                  {wantedCovers.map((album: any) => (
                    <tr key={album.lidarrAlbumId} style={{ cursor: 'pointer' }}>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                          <div style={{
                            width: 36, height: 36, borderRadius: 6, background: 'var(--card-border)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, opacity: 0.5
                          }}>💿</div>
                          <span style={{ fontWeight: 600 }}>{album.title}</span>
                        </div>
                      </td>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)} style={{ color: 'var(--text-secondary)' }}>
                        {album.artistName || '—'}
                      </td>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>{album.year || '—'}</td>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>{album.albumType || '—'}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <Button
                          size="xs" variant="light" color="violet"
                          loading={searchingCoverId === album.lidarrAlbumId}
                          onClick={() => coverSearchMutation.mutate(album.lidarrAlbumId)}
                        >
                          🔍 Search
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {coversTotalPages > 1 && (
                <Group justify="center" mt="xl">
                  <Pagination total={coversTotalPages} value={coversPage} onChange={setCoversPage} color="violet" />
                </Group>
              )}
            </>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="lyrics">
          <Group mb="md">
            <TextInput
              placeholder="Search tracks..."
              value={lyricsSearch}
              onChange={(e) => { setLyricsSearch(e.currentTarget.value); setLyricsPage(1); }}
              style={{ flex: 1 }}
              styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
            />
          </Group>

          {loadingLyrics ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : wantedLyrics.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📝</div>
              <div className="empty-state-title">No Missing Lyrics</div>
              <div className="empty-state-message">
                {lyricsSearch ? 'No tracks match your search.' : 'All tracks have lyrics.'}
              </div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr><th>Track</th><th>Artist</th><th>File</th><th>Action</th></tr>
                </thead>
                <tbody>
                  {wantedLyrics.map((track: any) => (
                    <tr key={track.lidarrTrackId}>
                      <td style={{ fontWeight: 600 }}>{track.title}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{track.artistName || '—'}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {track.path ? track.path.split('/').pop() : '—'}
                      </td>
                      <td>
                        <Button
                          size="xs" variant="light" color="violet"
                          loading={searchingLyricsId === track.lidarrTrackId}
                          onClick={() => lyricsSearchMutation.mutate(track.lidarrTrackId)}
                        >
                          🔍 Search
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {lyricsTotalPages > 1 && (
                <Group justify="center" mt="xl">
                  <Pagination total={lyricsTotalPages} value={lyricsPage} onChange={setLyricsPage} color="violet" />
                </Group>
              )}
            </>
          )}
        </Tabs.Panel>
      </Tabs>
    </div>
  );
}
