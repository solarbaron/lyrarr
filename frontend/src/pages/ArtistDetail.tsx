import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { Loader, Button, Select, Group, Badge, Pagination } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowLeft } from '@fortawesome/free-solid-svg-icons';
import { getArtist, getAlbums, getProfiles, massAssignProfile } from '../api';

export default function ArtistDetailPage() {
  const { artistId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [profileId, setProfileId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 25;

  const { data: artist, isLoading } = useQuery({
    queryKey: ['artist', artistId],
    queryFn: () => getArtist(Number(artistId)),
    enabled: !!artistId,
  });

  const { data: albumsResult } = useQuery({
    queryKey: ['artist-albums', artistId, page],
    queryFn: () => getAlbums({ page, pageSize, artistId: Number(artistId) }),
    enabled: !!artistId,
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const assignMutation = useMutation({
    mutationFn: () => massAssignProfile({ profileId: Number(profileId), artistIds: [Number(artistId)] }),
    onSuccess: () => {
      notifications.show({ title: 'Done', message: 'Profile updated', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['artist', artistId] });
    },
  });

  if (isLoading) {
    return <div className="empty-state"><Loader color="violet" size="lg" /></div>;
  }

  if (!artist) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">Artist Not Found</div>
      </div>
    );
  }

  const albums = albumsResult?.data || [];
  const totalAlbums = albumsResult?.total || 0;
  const totalPages = Math.ceil(totalAlbums / pageSize);

  return (
    <div className="fade-in">
      <Button
        variant="subtle"
        color="gray"
        leftSection={<FontAwesomeIcon icon={faArrowLeft} />}
        onClick={() => navigate('/artists')}
        mb="md"
      >
        Back to Artists
      </Button>

      {/* Artist Header */}
      <div className="glass-card" style={{ padding: 24, marginBottom: 24 }}>
        <div style={{ display: 'flex', gap: 24 }}>
          {artist.poster ? (
            <img
              src={artist.poster}
              alt={artist.name}
              style={{ width: 160, height: 160, borderRadius: 12, objectFit: 'cover', flexShrink: 0 }}
            />
          ) : (
            <div style={{
              width: 160, height: 160, borderRadius: 12, background: 'var(--card-border)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 48, opacity: 0.3, flexShrink: 0
            }}>🎤</div>
          )}
          <div style={{ flex: 1 }}>
            <h2 style={{ margin: '0 0 4px', fontSize: 28, fontWeight: 700, color: 'var(--text-primary)' }}>
              {artist.name}
            </h2>
            <p style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-secondary)' }}>
              {artist.path}
            </p>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
              <Badge color={artist.monitored ? 'green' : 'gray'}>
                {artist.monitored ? 'Monitored' : 'Unmonitored'}
              </Badge>
              <Badge variant="outline" color="violet">{totalAlbums} albums</Badge>
            </div>

            <Group gap="sm">
              <Select
                placeholder="Select profile..."
                data={profiles.map((p: any) => ({ value: String(p.id), label: p.name }))}
                value={profileId || (artist.profileId ? String(artist.profileId) : null)}
                onChange={setProfileId}
                size="sm"
                styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
              />
              <Button
                variant="gradient"
                gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
                size="sm"
                disabled={!profileId}
                onClick={() => assignMutation.mutate()}
                loading={assignMutation.isPending}
              >
                Set Profile
              </Button>
            </Group>

            {artist.overview && (
              <p style={{ marginTop: 12, fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                {artist.overview}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Albums */}
      <h3 style={{ marginBottom: 12, color: 'var(--text-primary)' }}>
        Albums ({totalAlbums})
      </h3>

      {albums.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">💿</div>
          <div className="empty-state-title">No Albums</div>
          <div className="empty-state-message">No downloaded albums for this artist.</div>
        </div>
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>Album</th>
                <th>Year</th>
                <th>Type</th>
                <th>Cover Art</th>
                <th>Lyrics</th>
              </tr>
            </thead>
            <tbody>
              {albums.map((album: any) => (
                <tr
                  key={album.lidarrAlbumId}
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}
                >
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      {album.cover ? (
                        <img src={album.cover} alt={album.title}
                          style={{ width: 40, height: 40, borderRadius: 8, objectFit: 'cover' }} />
                      ) : (
                        <div style={{
                          width: 40, height: 40, borderRadius: 8, background: 'var(--card-border)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, opacity: 0.5
                        }}>💿</div>
                      )}
                      <div style={{ fontWeight: 600 }}>{album.title}</div>
                    </div>
                  </td>
                  <td>{album.year || '—'}</td>
                  <td><span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{album.albumType || '—'}</span></td>
                  <td>
                    <span className={`status-badge ${album.cover_status || 'missing'}`}>
                      {album.cover_status || 'Missing'}
                    </span>
                  </td>
                  <td>
                    <span className={`status-badge ${album.lyrics_status || 'unknown'}`}>
                      {album.lyrics_status || 'Unknown'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {totalPages > 1 && (
            <Group justify="center" mt="xl">
              <Pagination total={totalPages} value={page} onChange={setPage} color="violet" />
            </Group>
          )}
        </>
      )}
    </div>
  );
}
