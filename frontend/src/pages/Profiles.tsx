import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader, Button, TextInput, Modal, Checkbox, Group, Menu } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { getProfiles, createProfile, updateProfile, deleteProfile, bulkAssignProfile } from '../api';

interface ProfileForm {
  name: string;
  download_covers: boolean;
  download_lyrics: boolean;
  cover_providers: string;
  lyrics_providers: string;
  prefer_synced_lyrics: boolean;
  cover_format: string;
  overwrite_existing: boolean;
  embed_cover_art: boolean;
}

const DEFAULT_FORM: ProfileForm = {
  name: '',
  download_covers: true,
  download_lyrics: true,
  cover_providers: '["musicbrainz","deezer","itunes","fanart","theaudiodb"]',
  lyrics_providers: '["lrclib","musixmatch","netease","genius"]',
  prefer_synced_lyrics: true,
  cover_format: 'jpg',
  overwrite_existing: false,
  embed_cover_art: false,
};

export default function ProfilesPage() {
  const queryClient = useQueryClient();
  const { data: profiles = [], isLoading } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [form, setForm] = useState<ProfileForm>(DEFAULT_FORM);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (editId) return updateProfile(editId, form);
      return createProfile(form);
    },
    onSuccess: () => {
      notifications.show({ title: 'Saved', message: 'Profile saved', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      setModalOpen(false);
      setEditId(null);
      setForm(DEFAULT_FORM);
    },
    onError: (err: any) => {
      notifications.show({ title: 'Error', message: err?.response?.data?.message || 'Failed', color: 'red' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteProfile,
    onSuccess: () => {
      notifications.show({ title: 'Deleted', message: 'Profile deleted', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
    onError: (err: any) => {
      notifications.show({ title: 'Error', message: err?.response?.data?.message || 'Failed', color: 'red' });
    },
  });

  const setDefaultMutation = useMutation({
    mutationFn: (id: number) => updateProfile(id, { is_default: true }),
    onSuccess: () => {
      notifications.show({ title: 'Done', message: 'Default profile updated', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
  });

  const bulkAssignMutation = useMutation({
    mutationFn: (data: { profileId: number; mode: 'all' | 'unassigned' }) => bulkAssignProfile(data),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Done', message: data.message, color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['artists'] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
    },
    onError: (err: any) => {
      notifications.show({ title: 'Error', message: err?.response?.data?.message || 'Failed', color: 'red' });
    },
  });

  const openCreate = () => {
    setEditId(null);
    setForm(DEFAULT_FORM);
    setModalOpen(true);
  };

  const openEdit = (profile: any) => {
    setEditId(profile.id);
    setForm({
      name: profile.name,
      download_covers: profile.download_covers ?? true,
      download_lyrics: profile.download_lyrics ?? true,
      cover_providers: profile.cover_providers || '["musicbrainz","deezer","itunes","fanart","theaudiodb"]',
      lyrics_providers: profile.lyrics_providers || '["lrclib","musixmatch","netease","genius"]',
      prefer_synced_lyrics: profile.prefer_synced_lyrics ?? true,
      cover_format: profile.cover_format || 'jpg',
      overwrite_existing: profile.overwrite_existing ?? false,
      embed_cover_art: profile.embed_cover_art ?? false,
    });
    setModalOpen(true);
  };

  const inputStyle = { input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } };

  if (isLoading) {
    return <div className="empty-state"><Loader color="violet" size="lg" /></div>;
  }

  return (
    <div className="fade-in">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">Metadata Profiles</h1>
          <p className="page-subtitle">Control what metadata gets downloaded per artist or album</p>
        </div>
        <Button variant="gradient" gradient={{ from: '#8b3dff', to: '#6a1bfa' }} onClick={openCreate}>
          New Profile
        </Button>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Covers</th>
            <th>Lyrics</th>
            <th>Synced Lyrics</th>
            <th>Default</th>
            <th style={{ width: 280 }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((p: any) => (
            <tr key={p.id}>
              <td><span style={{ fontWeight: 600 }}>{p.name}</span></td>
              <td>
                <span className={`status-badge ${p.download_covers ? 'available' : 'missing'}`}>
                  {p.download_covers ? 'Yes' : 'No'}
                </span>
              </td>
              <td>
                <span className={`status-badge ${p.download_lyrics ? 'available' : 'missing'}`}>
                  {p.download_lyrics ? 'Yes' : 'No'}
                </span>
              </td>
              <td>{p.prefer_synced_lyrics ? 'Preferred' : 'No'}</td>
              <td>
                {p.is_default ? (
                  <span style={{ color: 'var(--accent-primary)', fontWeight: 600 }}>✓ Default</span>
                ) : (
                  <Button variant="subtle" color="gray" size="xs" onClick={() => setDefaultMutation.mutate(p.id)}>
                    Set Default
                  </Button>
                )}
              </td>
              <td>
                <Group gap="xs">
                  <Button variant="subtle" color="violet" size="xs" onClick={() => openEdit(p)}>Edit</Button>
                  <Menu shadow="md" width={200}>
                    <Menu.Target>
                      <Button variant="subtle" color="violet" size="xs">Assign ▾</Button>
                    </Menu.Target>
                    <Menu.Dropdown styles={{ dropdown: { background: 'var(--surface-bg)', border: '1px solid var(--card-border)' } }}>
                      <Menu.Item onClick={() => bulkAssignMutation.mutate({ profileId: p.id, mode: 'all' })}>
                        Set to All Artists/Albums
                      </Menu.Item>
                      <Menu.Item onClick={() => bulkAssignMutation.mutate({ profileId: p.id, mode: 'unassigned' })}>
                        Set to Unassigned Only
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                  {!p.is_default && (
                    <Button variant="subtle" color="red" size="xs" onClick={() => deleteMutation.mutate(p.id)}>
                      Delete
                    </Button>
                  )}
                </Group>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Create/Edit Modal */}
      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editId ? 'Edit Profile' : 'Create Profile'}
        styles={{ content: { background: 'var(--surface-bg)' }, header: { background: 'var(--surface-bg)' } }}
      >
        <TextInput
          label="Profile Name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.currentTarget.value })}
          mb="md"
          styles={inputStyle}
        />
        <Checkbox
          label="Download Cover Art"
          checked={form.download_covers}
          onChange={(e) => setForm({ ...form, download_covers: e.currentTarget.checked })}
          mb="sm"
          color="violet"
        />
        <Checkbox
          label="Download Lyrics"
          checked={form.download_lyrics}
          onChange={(e) => setForm({ ...form, download_lyrics: e.currentTarget.checked })}
          mb="sm"
          color="violet"
        />
        <Checkbox
          label="Prefer Synced Lyrics (.lrc)"
          checked={form.prefer_synced_lyrics}
          onChange={(e) => setForm({ ...form, prefer_synced_lyrics: e.currentTarget.checked })}
          mb="sm"
          color="violet"
        />
        <Checkbox
          label="Overwrite Existing Files"
          checked={form.overwrite_existing}
          onChange={(e) => setForm({ ...form, overwrite_existing: e.currentTarget.checked })}
          mb="sm"
          color="violet"
        />
        <Checkbox
          label="Embed Cover Art in Audio Files"
          checked={form.embed_cover_art}
          onChange={(e) => setForm({ ...form, embed_cover_art: e.currentTarget.checked })}
          mb="md"
          color="violet"
        />
        <TextInput
          label="Cover Format"
          value={form.cover_format}
          onChange={(e) => setForm({ ...form, cover_format: e.currentTarget.value })}
          mb="md"
          styles={inputStyle}
        />
        <Button
          fullWidth
          variant="gradient"
          gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
          onClick={() => saveMutation.mutate()}
          loading={saveMutation.isPending}
        >
          {editId ? 'Save Changes' : 'Create Profile'}
        </Button>
      </Modal>
    </div>
  );
}
