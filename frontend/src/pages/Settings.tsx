import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TextInput, Switch, Button, NumberInput, Loader, FileButton, Select } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState, useEffect } from 'react';
import { getSettings, saveSettings, testLidarr, testNotification, exportBackup, importBackup, getProfiles } from '../api';

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const { data: settings, isLoading } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const [form, setForm] = useState<any>({});
  const { data: profiles } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  useEffect(() => {
    if (settings) setForm(settings);
  }, [settings]);

  const updateField = (section: string, key: string, value: any) => {
    setForm((prev: any) => ({ ...prev, [section]: { ...prev[section], [key]: value } }));
  };

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      const flattened: Record<string, any> = {};
      Object.entries(data).forEach(([section, values]: [string, any]) => {
        Object.entries(values).forEach(([key, val]) => {
          flattened[`settings-${section}-${key}`] = val;
        });
      });
      return saveSettings(flattened);
    },
    onSuccess: () => {
      notifications.show({ title: 'Settings saved', message: 'Changes applied', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
    onError: () => notifications.show({ title: 'Error', message: 'Failed to save settings', color: 'red' }),
  });

  const testMutation = useMutation({
    mutationFn: () => testLidarr({
      ip: form.lidarr?.ip, port: form.lidarr?.port,
      base_url: form.lidarr?.base_url, apikey: form.lidarr?.apikey, ssl: form.lidarr?.ssl,
    }),
    onSuccess: (data: any) => notifications.show({ title: 'Success', message: data.message || 'Connected!', color: 'green' }),
    onError: (e: any) => notifications.show({ title: 'Error', message: e?.response?.data?.message || 'Failed', color: 'red' }),
  });

  const testNotifMutation = useMutation({
    mutationFn: testNotification,
    onSuccess: (data: any) => notifications.show({ title: 'Sent', message: data.message || 'Test sent', color: 'green' }),
    onError: () => notifications.show({ title: 'Error', message: 'Failed to send', color: 'red' }),
  });

  const handleExport = async () => {
    try {
      const data = await exportBackup();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url; a.download = 'lyrarr-backup.json'; a.click();
      URL.revokeObjectURL(url);
      notifications.show({ title: 'Exported', message: 'Backup downloaded', color: 'green' });
    } catch {
      notifications.show({ title: 'Error', message: 'Export failed', color: 'red' });
    }
  };

  const handleImport = async (file: File | null) => {
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      await importBackup(data);
      notifications.show({ title: 'Restored', message: 'Backup imported', color: 'green' });
      queryClient.invalidateQueries();
    } catch {
      notifications.show({ title: 'Error', message: 'Import failed', color: 'red' });
    }
  };

  if (isLoading) return <div className="empty-state"><Loader color="violet" /></div>;

  const inputStyles = {
    input: { background: 'rgba(25,20,50,0.4)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' },
    label: { color: 'var(--text-secondary)', marginBottom: 4 },
  };

  return (
    <div className="fade-in">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">Configure Lyrarr</p>
        </div>
        <Button variant="gradient" gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
          onClick={() => saveMutation.mutate(form)} loading={saveMutation.isPending}>
          Save Settings
        </Button>
      </div>

      {/* Lidarr */}
      <div className="settings-section">
        <h3>🔗 Lidarr Connection</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <Switch label="Enable Lidarr" checked={form.general?.use_lidarr || false}
            onChange={(e) => updateField('general', 'use_lidarr', e.currentTarget.checked)}
            styles={{ label: { color: 'var(--text-secondary)' } }} />
          <div />
          <TextInput label="Host" placeholder="127.0.0.1" value={form.lidarr?.ip || ''}
            onChange={(e) => updateField('lidarr', 'ip', e.currentTarget.value)} styles={inputStyles} />
          <NumberInput label="Port" placeholder="8686" min={1} max={65535}
            value={form.lidarr?.port || 8686} onChange={(v) => updateField('lidarr', 'port', v)} styles={inputStyles} />
          <TextInput label="Base URL" placeholder="/" value={form.lidarr?.base_url || ''}
            onChange={(e) => updateField('lidarr', 'base_url', e.currentTarget.value)} styles={inputStyles} />
          <TextInput label="API Key" placeholder="Your Lidarr API key" value={form.lidarr?.apikey || ''}
            onChange={(e) => updateField('lidarr', 'apikey', e.currentTarget.value)} styles={inputStyles} />
          <Switch label="SSL" checked={form.lidarr?.ssl || false}
            onChange={(e) => updateField('lidarr', 'ssl', e.currentTarget.checked)}
            styles={{ label: { color: 'var(--text-secondary)' } }} />
          <Button variant="light" color="violet" onClick={() => testMutation.mutate()} loading={testMutation.isPending}>
            Test Connection
          </Button>
        </div>
      </div>

      {/* Providers */}
      <div className="settings-section">
        <h3>🔑 Provider API Keys</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <TextInput label="fanart.tv API Key" placeholder="Optional" value={form.fanart?.apikey || ''}
            onChange={(e) => updateField('fanart', 'apikey', e.currentTarget.value)} styles={inputStyles} />
          <TextInput label="Genius API Key" placeholder="Optional" value={form.genius?.apikey || ''}
            onChange={(e) => updateField('genius', 'apikey', e.currentTarget.value)} styles={inputStyles} />
        </div>
      </div>

      {/* Notifications */}
      <div className="settings-section">
        <h3>🔔 Notifications</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <Switch label="Enable Notifications" checked={form.notifications?.enabled || false}
            onChange={(e) => updateField('notifications', 'enabled', e.currentTarget.checked)}
            styles={{ label: { color: 'var(--text-secondary)' } }} />
          <div />
          <TextInput label="Discord Webhook URL" placeholder="https://discord.com/api/webhooks/..."
            value={form.notifications?.discord_webhook || ''}
            onChange={(e) => updateField('notifications', 'discord_webhook', e.currentTarget.value)} styles={inputStyles} />
          <div />
          <TextInput label="Telegram Bot Token" placeholder="123456:ABC-DEF..."
            value={form.notifications?.telegram_bot_token || ''}
            onChange={(e) => updateField('notifications', 'telegram_bot_token', e.currentTarget.value)} styles={inputStyles} />
          <TextInput label="Telegram Chat ID" placeholder="Chat or group ID"
            value={form.notifications?.telegram_chat_id || ''}
            onChange={(e) => updateField('notifications', 'telegram_chat_id', e.currentTarget.value)} styles={inputStyles} />
          <Button variant="light" color="violet" onClick={() => testNotifMutation.mutate()} loading={testNotifMutation.isPending}>
            Send Test Notification
          </Button>
        </div>
      </div>

      {/* Security */}
      <div className="settings-section">
        <h3>🔒 Security</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <TextInput label="API Key" placeholder="Leave empty to disable auth"
            description="Require X-API-KEY header on all API requests"
            value={form.auth?.api_key || ''}
            onChange={(e) => updateField('auth', 'api_key', e.currentTarget.value)} styles={inputStyles} />
        </div>
      </div>

      {/* General */}
      <div className="settings-section">
        <h3>⚙️ General</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <TextInput label="Instance Name" placeholder="Lyrarr" value={form.general?.instance_name || ''}
            onChange={(e) => updateField('general', 'instance_name', e.currentTarget.value)} styles={inputStyles} />
          <NumberInput label="Port" min={1} max={65535} value={form.general?.port || 6868}
            onChange={(v) => updateField('general', 'port', v)} styles={inputStyles} />
          <Select
            label="Default Profile for New Albums"
            description="Automatically assign this profile to newly synced albums"
            placeholder="None (no auto-assign)"
            clearable
            data={(profiles || []).map((p: any) => ({ value: String(p.id), label: p.name }))}
            value={form.general?.default_profile_id ? String(form.general.default_profile_id) : null}
            onChange={(v) => updateField('general', 'default_profile_id', v ? Number(v) : null)}
            styles={inputStyles}
          />
        </div>
      </div>

      {/* Whisper */}
      <div className="settings-section">
        <h3>🎤 Whisper (AI Lyrics Sync)</h3>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: -8, marginBottom: 16 }}>
          Settings for generating synced lyrics from plain text using audio transcription.
          Requires <code>faster-whisper</code> and <code>ffmpeg</code>.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <Select label="Model" description="Larger = more accurate but slower"
            data={[
              { value: 'tiny', label: 'Tiny (~1GB RAM, fastest)' },
              { value: 'base', label: 'Base (~1.5GB RAM, balanced)' },
              { value: 'small', label: 'Small (~2GB RAM, better)' },
              { value: 'medium', label: 'Medium (~5GB RAM, best)' },
              { value: 'large-v3', label: 'Large v3 (~10GB RAM, highest)' },
            ]}
            value={form.metadata?.whisper?.model || 'base'}
            onChange={(v) => {
              setForm((prev: any) => ({
                ...prev,
                metadata: {
                  ...prev.metadata,
                  whisper: { ...prev.metadata?.whisper, model: v },
                },
              }));
            }}
            styles={inputStyles} />
          <Select label="Device" description="GPU (cuda) is much faster if available"
            data={[
              { value: 'cpu', label: 'CPU' },
              { value: 'cuda', label: 'CUDA (GPU)' },
              { value: 'auto', label: 'Auto-detect' },
            ]}
            value={form.metadata?.whisper?.device || 'cpu'}
            onChange={(v) => {
              setForm((prev: any) => ({
                ...prev,
                metadata: {
                  ...prev.metadata,
                  whisper: { ...prev.metadata?.whisper, device: v },
                },
              }));
            }}
            styles={inputStyles} />
          <Select label="Compute Type" description="int8 is fastest, float32 is most accurate"
            data={[
              { value: 'int8', label: 'int8 (fastest)' },
              { value: 'float16', label: 'float16 (balanced)' },
              { value: 'float32', label: 'float32 (most accurate)' },
            ]}
            value={form.metadata?.whisper?.compute_type || 'int8'}
            onChange={(v) => {
              setForm((prev: any) => ({
                ...prev,
                metadata: {
                  ...prev.metadata,
                  whisper: { ...prev.metadata?.whisper, compute_type: v },
                },
              }));
            }}
            styles={inputStyles} />
        </div>
      </div>

      {/* Backup */}
      <div className="settings-section">
        <h3>💾 Backup & Restore</h3>
        <div style={{ display: 'flex', gap: 12 }}>
          <Button variant="light" color="violet" onClick={handleExport}>Export Backup</Button>
          <FileButton onChange={handleImport} accept="application/json">
            {(props) => <Button variant="light" color="grape" {...props}>Import Backup</Button>}
          </FileButton>
        </div>
      </div>
    </div>
  );
}
