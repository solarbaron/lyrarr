import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader, TextInput, Pagination, Group, Select, Button, Checkbox, SegmentedControl } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAlbums, getProfiles, massAssignProfile } from '../api';

export default function AlbumsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<number[]>([]);
  const [assignProfileId, setAssignProfileId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState(() => localStorage.getItem('lyrarr-album-view') || 'table');
  const [sortBy, setSortBy] = useState('title');
  const [sortDir, setSortDir] = useState('asc');
  const [coverFilter, setCoverFilter] = useState('');
  const [lyricsFilter, setLyricsFilter] = useState('');
  const pageSize = 25;

  const { data: result, isLoading } = useQuery({
    queryKey: ['albums', page, search, sortBy, sortDir, coverFilter, lyricsFilter],
    queryFn: () => getAlbums({ page, pageSize, search, sortBy, sortDir, coverStatus: coverFilter || undefined, lyricsStatus: lyricsFilter || undefined }),
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const assignMutation = useMutation({
    mutationFn: () => massAssignProfile({ profileId: Number(assignProfileId), albumIds: selected }),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Done', message: data.message, color: 'green' });
      setSelected([]);
      queryClient.invalidateQueries({ queryKey: ['albums'] });
    },
  });

  const albums = result?.data || [];
  const total = result?.total || 0;
  const totalPages = Math.ceil(total / pageSize);

  const toggleSelect = (id: number) => {
    setSelected(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };

  const toggleAll = () => {
    if (selected.length === albums.length) {
      setSelected([]);
    } else {
      setSelected(albums.map((a: any) => a.lidarrAlbumId));
    }
  };

  const handleViewChange = (val: string) => {
    setViewMode(val);
    localStorage.setItem('lyrarr-album-view', val);
  };

  if (isLoading) {
    return <div className="empty-state"><Loader color="violet" size="lg" /></div>;
  }

  return (
    <div className="fade-in">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">Albums</h1>
          <p className="page-subtitle">{total} albums synced from Lidarr</p>
        </div>
        <SegmentedControl
          value={viewMode}
          onChange={handleViewChange}
          data={[
            { label: '☰ Table', value: 'table' },
            { label: '▦ Grid', value: 'grid' },
          ]}
          size="xs"
          styles={{
            root: { background: 'var(--card-bg)', border: '1px solid var(--card-border)' },
            label: { color: 'var(--text-secondary)', fontSize: 12 },
          }}
        />
      </div>

      <Group mb="lg" gap="md" align="end" wrap="wrap">
        <TextInput
          placeholder="Search albums..."
          value={search}
          onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }}
          style={{ flex: 1, minWidth: 200 }}
          styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
        />
        <Select
          placeholder="Sort by"
          data={[
            { value: 'title', label: 'Title' },
            { value: 'year', label: 'Year' },
          ]}
          value={sortBy}
          onChange={(v) => { setSortBy(v || 'title'); setPage(1); }}
          w={120}
          size="sm"
          styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
        />
        <Button variant="subtle" color="gray" size="sm" px={8}
          onClick={() => { setSortDir(d => d === 'asc' ? 'desc' : 'asc'); setPage(1); }}>
          {sortDir === 'asc' ? '↑ A-Z' : '↓ Z-A'}
        </Button>
        <Select
          placeholder="Cover status"
          data={[
            { value: '', label: 'All covers' },
            { value: 'available', label: '✓ Has cover' },
            { value: 'missing', label: '✗ Missing cover' },
          ]}
          value={coverFilter}
          onChange={(v) => { setCoverFilter(v || ''); setPage(1); }}
          w={150}
          size="sm"
          clearable
          styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
        />
        <Select
          placeholder="Lyrics status"
          data={[
            { value: '', label: 'All lyrics' },
            { value: 'available', label: '✓ Has lyrics' },
            { value: 'missing', label: '✗ Missing lyrics' },
          ]}
          value={lyricsFilter}
          onChange={(v) => { setLyricsFilter(v || ''); setPage(1); }}
          w={150}
          size="sm"
          clearable
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

      {albums.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">💿</div>
          <div className="empty-state-title">No Albums Found</div>
          <div className="empty-state-message">
            {total === 0 ? 'Sync with Lidarr to see your albums here.' : 'No albums match your search.'}
          </div>
        </div>
      ) : viewMode === 'grid' ? (
        /* Grid View */
        <>
          <div className="album-grid">
            {albums.map((album: any) => (
              <div key={album.lidarrAlbumId} className="album-card" onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
                <div className="album-card-cover">
                  {album.cover ? (
                    <img src={album.cover} alt={album.title} />
                  ) : (
                    <div className="album-card-placeholder">💿</div>
                  )}
                  <div className="album-card-badges">
                    <span className={`status-badge ${album.cover_status || 'missing'}`} style={{ fontSize: 10, padding: '2px 8px' }}>
                      {album.cover_status === 'available' ? '✓' : '✗'} Cover
                    </span>
                  </div>
                </div>
                <div className="album-card-info">
                  <div className="album-card-title">{album.title}</div>
                  <div className="album-card-artist">{album.artistName || '—'}</div>
                  {album.year && <div className="album-card-year">{album.year}</div>}
                </div>
              </div>
            ))}
          </div>

          {totalPages > 1 && (
            <Group justify="center" mt="xl">
              <Pagination total={totalPages} value={page} onChange={setPage} color="violet" />
            </Group>
          )}
        </>
      ) : (
        /* Table View */
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <Checkbox
                    checked={selected.length === albums.length && albums.length > 0}
                    indeterminate={selected.length > 0 && selected.length < albums.length}
                    onChange={toggleAll}
                    color="violet"
                  />
                </th>
                <th>Album</th>
                <th>Artist</th>
                <th>Year</th>
                <th>Profile</th>
                <th>Cover Art</th>
                <th>Lyrics</th>
              </tr>
            </thead>
            <tbody>
              {albums.map((album: any) => (
                <tr key={album.lidarrAlbumId} style={{ cursor: 'pointer' }}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={selected.includes(album.lidarrAlbumId)}
                      onChange={() => toggleSelect(album.lidarrAlbumId)}
                      color="violet"
                    />
                  </td>
                  <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      {album.cover ? (
                        <img src={album.cover} alt={album.title}
                          style={{ width: 44, height: 44, borderRadius: 8, objectFit: 'cover' }} />
                      ) : (
                        <div style={{
                          width: 44, height: 44, borderRadius: 8, background: 'var(--card-border)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, opacity: 0.5
                        }}>💿</div>
                      )}
                      <div>
                        <div style={{ fontWeight: 600 }}>{album.title}</div>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{album.albumType || ''}</div>
                      </div>
                    </div>
                  </td>
                  <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
                    <span style={{ color: 'var(--text-secondary)' }}>{album.artistName || '—'}</span>
                  </td>
                  <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>{album.year || '—'}</td>
                  <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
                    <span style={{ fontSize: 13, color: 'var(--accent-primary)' }}>{album.profileName || '—'}</span>
                  </td>
                  <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
                    <span className={`status-badge ${album.cover_status || 'missing'}`}>
                      {album.cover_status || 'Missing'}
                    </span>
                  </td>
                  <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
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
