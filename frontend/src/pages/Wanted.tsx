import { useQuery } from '@tanstack/react-query';
import { Loader, Tabs, Pagination, Group } from '@mantine/core';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getWantedCovers, getWantedLyrics } from '../api';

export default function WantedPage() {
  const navigate = useNavigate();
  const [coversPage, setCoversPage] = useState(1);
  const [lyricsPage, setLyricsPage] = useState(1);
  const pageSize = 25;

  const { data: coversResult, isLoading: loadingCovers } = useQuery({
    queryKey: ['wanted-covers', coversPage],
    queryFn: () => getWantedCovers({ page: coversPage, pageSize }),
  });
  const { data: lyricsResult, isLoading: loadingLyrics } = useQuery({
    queryKey: ['wanted-lyrics', lyricsPage],
    queryFn: () => getWantedLyrics({ page: lyricsPage, pageSize }),
  });

  const wantedCovers = coversResult?.data || [];
  const coversTotal = coversResult?.total || 0;
  const coversTotalPages = Math.ceil(coversTotal / pageSize);

  const wantedLyrics = lyricsResult?.data || [];
  const lyricsTotal = lyricsResult?.total || 0;
  const lyricsTotalPages = Math.ceil(lyricsTotal / pageSize);

  return (
    <div className="fade-in">
      <div className="page-header">
        <h1 className="page-title">Wanted</h1>
        <p className="page-subtitle">Missing metadata that needs to be downloaded</p>
      </div>

      <Tabs defaultValue="covers" styles={{
        tab: { color: 'var(--text-secondary)', '&[data-active]': { color: 'var(--text-primary)' } }
      }}>
        <Tabs.List mb="lg">
          <Tabs.Tab value="covers">Missing Covers ({coversTotal})</Tabs.Tab>
          <Tabs.Tab value="lyrics">Missing Lyrics ({lyricsTotal})</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="covers">
          {loadingCovers ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : wantedCovers.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🎨</div>
              <div className="empty-state-title">No Missing Covers</div>
              <div className="empty-state-message">All albums have cover art.</div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr><th>Album</th><th>Artist</th><th>Year</th><th>Type</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {wantedCovers.map((album: any) => (
                    <tr
                      key={album.lidarrAlbumId}
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}
                    >
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                          {album.cover ? (
                            <img src={album.cover} alt={album.title}
                              style={{ width: 36, height: 36, borderRadius: 6, objectFit: 'cover' }} />
                          ) : (
                            <div style={{
                              width: 36, height: 36, borderRadius: 6, background: 'var(--card-border)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, opacity: 0.5
                            }}>💿</div>
                          )}
                          <span style={{ fontWeight: 600 }}>{album.title}</span>
                        </div>
                      </td>
                      <td style={{ color: 'var(--text-secondary)' }}>{album.artistName || '—'}</td>
                      <td>{album.year || '—'}</td>
                      <td>{album.albumType || '—'}</td>
                      <td><span className="status-badge missing">Missing</span></td>
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
          {loadingLyrics ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : wantedLyrics.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📝</div>
              <div className="empty-state-title">No Missing Lyrics</div>
              <div className="empty-state-message">All tracks have lyrics.</div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr><th>Track</th><th>Artist</th><th>File</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {wantedLyrics.map((track: any) => (
                    <tr key={track.lidarrTrackId}>
                      <td style={{ fontWeight: 600 }}>{track.title}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{track.artistName || '—'}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {track.path ? track.path.split('/').pop() : '—'}
                      </td>
                      <td><span className="status-badge missing">Missing</span></td>
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
