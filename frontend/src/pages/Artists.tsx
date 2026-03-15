import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader, TextInput, Pagination, Group, Select, Button, Checkbox } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getArtists, getProfiles, massAssignProfile } from '../api';

export default function ArtistsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<number[]>([]);
  const [assignProfileId, setAssignProfileId] = useState<string | null>(null);
  const pageSize = 25;

  const { data: result, isLoading } = useQuery({
    queryKey: ['artists', page, search],
    queryFn: () => getArtists({ page, pageSize, search }),
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const assignMutation = useMutation({
    mutationFn: () => massAssignProfile({ profileId: Number(assignProfileId), artistIds: selected }),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Done', message: data.message, color: 'green' });
      setSelected([]);
      queryClient.invalidateQueries({ queryKey: ['artists'] });
    },
  });

  const artists = result?.data || [];
  const total = result?.total || 0;
  const totalPages = Math.ceil(total / pageSize);

  const toggleSelect = (id: number) => {
    setSelected(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };

  const toggleAll = () => {
    if (selected.length === artists.length) {
      setSelected([]);
    } else {
      setSelected(artists.map((a: any) => a.lidarrArtistId));
    }
  };

  if (isLoading) {
    return <div className="empty-state"><Loader color="violet" size="lg" /></div>;
  }

  return (
    <div className="fade-in">
      <div className="page-header">
        <h1 className="page-title">Artists</h1>
        <p className="page-subtitle">{total} artists synced from Lidarr</p>
      </div>

      <Group mb="lg" gap="md" align="end">
        <TextInput
          placeholder="Search artists..."
          value={search}
          onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }}
          style={{ flex: 1 }}
          styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
        />
        {selected.length > 0 && (
          <>
            <Select
              placeholder="Assign profile..."
              data={profiles.map((p: any) => ({ value: String(p.id), label: p.name }))}
              value={assignProfileId}
              onChange={setAssignProfileId}
              styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
            />
            <Button
              variant="gradient"
              gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
              disabled={!assignProfileId}
              onClick={() => assignMutation.mutate()}
              loading={assignMutation.isPending}
            >
              Set Profile ({selected.length})
            </Button>
          </>
        )}
      </Group>

      {artists.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">🎤</div>
          <div className="empty-state-title">No Artists Found</div>
          <div className="empty-state-message">
            {total === 0 ? 'Sync with Lidarr to see your artists here.' : 'No artists match your search.'}
          </div>
        </div>
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <Checkbox
                    checked={selected.length === artists.length && artists.length > 0}
                    indeterminate={selected.length > 0 && selected.length < artists.length}
                    onChange={toggleAll}
                    color="violet"
                  />
                </th>
                <th>Artist</th>
                <th>Path</th>
                <th>Profile</th>
                <th>Monitored</th>
              </tr>
            </thead>
            <tbody>
              {artists.map((artist: any) => (
                <tr key={artist.lidarrArtistId} style={{ cursor: 'pointer' }}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={selected.includes(artist.lidarrArtistId)}
                      onChange={() => toggleSelect(artist.lidarrArtistId)}
                      color="violet"
                    />
                  </td>
                  <td onClick={() => navigate(`/artists/${artist.lidarrArtistId}`)}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      {artist.poster ? (
                        <img src={artist.poster} alt={artist.name}
                          style={{ width: 40, height: 40, borderRadius: 8, objectFit: 'cover' }} />
                      ) : (
                        <div style={{
                          width: 40, height: 40, borderRadius: 8, background: 'var(--card-border)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, opacity: 0.5
                        }}>🎤</div>
                      )}
                      <div style={{ fontWeight: 600 }}>{artist.name}</div>
                    </div>
                  </td>
                  <td onClick={() => navigate(`/artists/${artist.lidarrArtistId}`)}>
                    <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                      {artist.path ? artist.path.split('/').slice(-2).join('/') : '—'}
                    </span>
                  </td>
                  <td onClick={() => navigate(`/artists/${artist.lidarrArtistId}`)}>
                    <span style={{ fontSize: 13, color: 'var(--accent-primary)' }}>
                      {artist.profileName || '—'}
                    </span>
                  </td>
                  <td onClick={() => navigate(`/artists/${artist.lidarrArtistId}`)}>
                    <span className={`status-badge ${artist.monitored ? 'available' : 'missing'}`}>
                      {artist.monitored ? 'Yes' : 'No'}
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
