import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader, Button, TextInput, NumberInput, Modal, Checkbox, Group, Menu, MultiSelect, Select } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { getProfiles, createProfile, updateProfile, deleteProfile, bulkAssignProfile } from '../api';

interface ProfileForm {
  name: string;
  download_covers: boolean;
  download_lyrics: boolean;
  cover_providers: string[];
  lyrics_providers: string[];
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
}

const ALL_COVER_PROVIDERS = [
  { value: 'musicbrainz', label: 'MusicBrainz (Cover Art Archive)', description: 'Free — no API key' },
  { value: 'deezer', label: 'Deezer', description: 'Free — no API key' },
  { value: 'itunes', label: 'iTunes / Apple Music', description: 'Free — no API key' },
  { value: 'fanart', label: 'fanart.tv', description: 'Requires API key' },
  { value: 'theaudiodb', label: 'TheAudioDB', description: 'Free test key included' },
];

const ALL_LYRICS_PROVIDERS = [
  { value: 'lrclib', label: 'LRCLIB', description: 'Free — synced + plain' },
  { value: 'musixmatch', label: 'Musixmatch', description: 'API key — synced + plain' },
  { value: 'netease', label: 'NetEase Cloud Music', description: 'Free — synced + plain' },
  { value: 'genius', label: 'Genius', description: 'API key — plain only' },
];

const DEFAULT_COVERS = ['musicbrainz', 'deezer', 'itunes', 'fanart', 'theaudiodb'];
const DEFAULT_LYRICS = ['lrclib', 'musixmatch', 'netease', 'genius'];

function parseProviders(json: string | string[] | null, fallback: string[]): string[] {
  if (Array.isArray(json)) return json;
  if (!json) return fallback;
  try { return JSON.parse(json); } catch { return fallback; }
}

const LANGUAGES = [
  { value: 'en', label: 'English' }, { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' }, { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' }, { value: 'pt', label: 'Portuguese' },
  { value: 'ja', label: 'Japanese' }, { value: 'ko', label: 'Korean' },
  { value: 'zh-cn', label: 'Chinese (Simplified)' }, { value: 'ru', label: 'Russian' },
  { value: 'ar', label: 'Arabic' }, { value: 'hi', label: 'Hindi' },
  { value: 'tr', label: 'Turkish' }, { value: 'nl', label: 'Dutch' },
  { value: 'pl', label: 'Polish' }, { value: 'sv', label: 'Swedish' },
];

const DEFAULT_FORM: ProfileForm = {
  name: '',
  download_covers: true,
  download_lyrics: true,
  cover_providers: DEFAULT_COVERS,
  lyrics_providers: DEFAULT_LYRICS,
  prefer_synced_lyrics: true,
  lyrics_selection_mode: 'best_score',
  auto_detect_language: true,
  auto_translate: 'off',
  translate_target_lang: 'en',
  translate_only_foreign: true,
  score_threshold: 0,
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
      const payload = {
        ...form,
        cover_providers: JSON.stringify(form.cover_providers),
        lyrics_providers: JSON.stringify(form.lyrics_providers),
      };
      if (editId) return updateProfile(editId, payload);
      return createProfile(payload);
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
      cover_providers: parseProviders(profile.cover_providers, DEFAULT_COVERS),
      lyrics_providers: parseProviders(profile.lyrics_providers, DEFAULT_LYRICS),
      prefer_synced_lyrics: profile.prefer_synced_lyrics ?? true,
      lyrics_selection_mode: profile.lyrics_selection_mode || 'best_score',
      auto_detect_language: profile.auto_detect_language ?? true,
      auto_translate: profile.auto_translate || 'off',
      translate_target_lang: profile.translate_target_lang || 'en',
      translate_only_foreign: profile.translate_only_foreign ?? true,
      score_threshold: profile.score_threshold ?? 0,
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
        <Select
          label="Cover Format"
          data={[
            { value: 'jpg', label: 'JPEG (.jpg)' },
            { value: 'png', label: 'PNG (.png)' },
            { value: 'webp', label: 'WebP (.webp)' },
          ]}
          value={form.cover_format}
          onChange={(v) => setForm({ ...form, cover_format: v || 'jpg' })}
          mb="md"
          styles={inputStyle}
        />

        {/* Lyrics Intelligence Section */}
        <div style={{
          padding: 16, borderRadius: 12, marginBottom: 16,
          background: 'rgba(139, 61, 255, 0.06)', border: '1px solid rgba(139, 61, 255, 0.2)',
        }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12, color: 'var(--accent-primary)' }}>
            🧠 Lyrics Intelligence
          </div>
          <Select
            label="Selection Mode"
            description="How to pick the best lyrics result from all providers"
            data={[
              { value: 'best_score', label: 'Best Score (highest match %, synced tiebreaker)' },
              { value: 'prefer_synced', label: 'Prefer Synced (synced always wins, then score)' },
              { value: 'prefer_plain', label: 'Prefer Plain Text (for incompatible players)' },
            ]}
            value={form.lyrics_selection_mode}
            onChange={(v) => setForm({ ...form, lyrics_selection_mode: v || 'best_score' })}
            mb="md"
            styles={inputStyle}
          />
          <NumberInput
            label="Minimum Score Threshold"
            description="Reject lyrics below this match % (0 = accept all)"
            value={form.score_threshold}
            onChange={(v) => setForm({ ...form, score_threshold: Number(v) || 0 })}
            min={0}
            max={100}
            step={5}
            mb="md"
            styles={inputStyle}
          />
          <Checkbox
            label="Auto-detect Language"
            description="Identify the language of downloaded lyrics"
            checked={form.auto_detect_language}
            onChange={(e) => setForm({ ...form, auto_detect_language: e.currentTarget.checked })}
            mb="sm"
            color="violet"
          />
          <Select
            label="Auto-translate"
            description="Automatically translate lyrics after download"
            data={[
              { value: 'off', label: 'Off' },
              { value: 'dual', label: 'Dual (Original + Translation)' },
              { value: 'replace', label: 'Replace (Translation only)' },
            ]}
            value={form.auto_translate}
            onChange={(v) => setForm({ ...form, auto_translate: v || 'off' })}
            mb="md"
            styles={inputStyle}
          />
          {form.auto_translate !== 'off' && (
            <>
              <Select
                label="Target Language"
                data={LANGUAGES}
                value={form.translate_target_lang}
                onChange={(v) => setForm({ ...form, translate_target_lang: v || 'en' })}
                mb="sm"
                styles={inputStyle}
                searchable
              />
              <Checkbox
                label="Only translate foreign languages"
                description="Skip translation when lyrics are already in the target language"
                checked={form.translate_only_foreign}
                onChange={(e) => setForm({ ...form, translate_only_foreign: e.currentTarget.checked })}
                mb="sm"
                color="violet"
              />
            </>
          )}
        </div>

        <MultiSelect
          label="Cover Art Providers"
          description="Order matters — first provider is tried first"
          data={ALL_COVER_PROVIDERS}
          value={form.cover_providers}
          onChange={(v) => setForm({ ...form, cover_providers: v })}
          mb="md"
          styles={inputStyle}
          searchable
          clearable
        />
        <MultiSelect
          label="Lyrics Providers"
          description="All providers are queried — results are ranked by selection mode"
          data={ALL_LYRICS_PROVIDERS}
          value={form.lyrics_providers}
          onChange={(v) => setForm({ ...form, lyrics_providers: v })}
          mb="md"
          styles={inputStyle}
          searchable
          clearable
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
