import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { useState, useEffect, useMemo, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { faCircleExclamation } from '@fortawesome/free-solid-svg-icons';
import { getSettings, saveSettings, testLidarr, testNotification, exportBackup, importBackup, getProfiles } from '../api';
import { errorMessage, notifyError } from '../notify';
import { CardSkeleton, EmptyState, PageHeader, Panel, SaveBar, useConfirm } from '../components/ui';
import { useHealthChecks } from '../components/shell';
import { ServerSections, type SettingsForm, type TestResult } from './settings/ServerSections';
import { AppearanceSection } from './settings/Appearance';
import { SettingsNav, scrollToSection, type SettingsNavItem } from './settings/SettingsNav';
import c from './settings/Settings.module.css';

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const location = useLocation();
  const phone = useMediaQuery('(max-width: 479px)');
  const { data: settings, isLoading, isError, error, refetch } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const [form, setForm] = useState<SettingsForm>({});
  const [baseline, setBaseline] = useState<SettingsForm>({});
  const { data: profiles } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });
  const { checks } = useHealthChecks();
  const [lidarrResult, setLidarrResult] = useState<TestResult | null>(null);
  const [notificationResult, setNotificationResult] = useState<TestResult | null>(null);

  const dirty = !!settings && !same(form, baseline);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // Load (and reload after a save) from the server, but never clobber edits in
  // progress when the query refetches in the background.
  useEffect(() => {
    if (settings && !dirtyRef.current) {
      setForm(settings);
      setBaseline(settings);
    }
  }, [settings]);

  const updateField = (section: string, key: string, value: unknown) => {
    setForm(prev => ({ ...prev, [section]: { ...prev[section], [key]: value } }));
  };

  const updateWhisper = (key: string, value: unknown) => {
    setForm(prev => ({
      ...prev,
      metadata: {
        ...prev.metadata,
        whisper: { ...prev.metadata?.whisper, [key]: value },
      },
    }));
  };

  // Mantine's NumberInput reports a cleared field as ''. Keep that in the form
  // so the box can be emptied and retyped, but never send it: saving is
  // blocked until every port is a valid number.
  const portError = (v: unknown) => {
    const n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
    return (typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= 65535)
      ? undefined : 'Enter a port between 1 and 65535';
  };
  const lidarrPortError = form.lidarr ? portError(form.lidarr.port ?? 8686) : undefined;
  const generalPortError = form.general ? portError(form.general.port ?? 6868) : undefined;
  const rangeError = (v: unknown, min: number, max: number) => {
    const n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
    return (typeof n === 'number' && Number.isInteger(n) && n >= min && n <= max) ? undefined : `Enter ${min} to ${max.toLocaleString()}`;
  };
  const auditErrors = form.audit ? {
    network: rangeError(form.audit.network_max_tracks ?? 200, 1, 5000),
    retention: rangeError(form.audit.report_retention ?? 10, 1, 100),
  } : undefined;
  const auditInvalid = !!(auditErrors?.network || auditErrors?.retention);

  const saveMutation = useMutation({
    mutationFn: (data: SettingsForm) => {
      const flattened: Record<string, unknown> = {};
      Object.entries(data).forEach(([section, values]) => {
        Object.entries(values as Record<string, unknown>).forEach(([key, val]) => {
          flattened[`settings-${section}-${key}`] = val;
        });
      });
      return saveSettings(flattened);
    },
    onSuccess: (_data, saved) => {
      notifications.show({ title: 'Settings saved', message: 'Changes applied', color: 'success' });
      setBaseline(saved);
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
    onError: (err) => notifyError('Failed to save settings', err),
  });

  const handleSave = () => {
    if (lidarrPortError || generalPortError || auditInvalid) {
      notifications.show({ title: 'Not saved', message: 'Fix the highlighted fields first', color: 'warning' });
      scrollToSection(generalPortError ? 'general' : lidarrPortError ? 'lidarr' : 'audit');
      return;
    }
    saveMutation.mutate(form);
  };

  const testMutation = useMutation({
    mutationFn: () => testLidarr({
      ip: form.lidarr?.ip, port: form.lidarr?.port,
      base_url: form.lidarr?.base_url, apikey: form.lidarr?.apikey, ssl: form.lidarr?.ssl,
      verify_ssl: form.lidarr?.verify_ssl,
    }),
    onMutate: () => setLidarrResult(null),
    onSuccess: (data: any) => {
      const message = data?.message || 'Connected!';
      setLidarrResult({ ok: true, message });
      notifications.show({ title: 'Success', message, color: 'success' });
    },
    onError: (e: unknown) => {
      const message = errorMessage(e, 'Failed');
      setLidarrResult({ ok: false, message });
      notifications.show({ title: 'Error', message, color: 'danger' });
    },
  });

  const testNotifMutation = useMutation({
    mutationFn: testNotification,
    onMutate: () => setNotificationResult(null),
    onSuccess: (data: any) => {
      const message = data?.message || 'Test sent';
      setNotificationResult({ ok: true, message });
      notifications.show({ title: 'Sent', message, color: 'success' });
    },
    onError: (err) => {
      setNotificationResult({ ok: false, message: errorMessage(err, 'Failed to send') });
      notifyError('Error', err, 'Failed to send');
    },
  });

  const handleExport = async () => {
    try {
      const data = await exportBackup();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url; a.download = 'lyrarr-backup.json'; a.click();
      URL.revokeObjectURL(url);
      notifications.show({ title: 'Exported', message: 'Backup downloaded', color: 'success' });
    } catch {
      notifications.show({ title: 'Error', message: 'Export failed', color: 'danger' });
    }
  };

  const handleImport = async (file: File | null) => {
    if (!file) return;
    const ok = await confirm({
      title: 'Restore from backup?',
      message: (
        <>
          Restore settings and profiles from <strong>{file.name}</strong>? Values in the file replace the current ones.
          {dirty && ' Your unsaved changes on this page are discarded.'}
        </>
      ),
      confirmLabel: 'Restore backup',
      tone: 'danger',
    });
    if (!ok) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      await importBackup(data);
      notifications.show({ title: 'Restored', message: 'Backup imported', color: 'success' });
      // Show the restored values, even over unsaved edits (the user confirmed).
      await queryClient.invalidateQueries();
      const fresh = queryClient.getQueryData<SettingsForm>(['settings']);
      if (fresh) { setForm(fresh); setBaseline(fresh); }
    } catch {
      notifications.show({ title: 'Error', message: 'Import failed', color: 'danger' });
    }
  };

  // Health dots on the sub-nav mirror the top-bar checks.
  const navItems: SettingsNavItem[] = useMemo(() => {
    const alertFor = (id: string) => {
      const check = checks.find(ch => ch.id === id);
      if (!check || check.level === 'ok') return undefined;
      return { tone: check.level === 'error' ? 'danger' as const : 'warning' as const, label: check.title };
    };
    return [
      { id: 'general', label: 'General' },
      { id: 'lidarr', label: 'Lidarr', alert: alertFor('lidarr') },
      { id: 'providers', label: 'Providers & keys' },
      { id: 'translation', label: 'Translation' },
      { id: 'whisper', label: 'Whisper (AI sync)' },
      { id: 'notifications', label: 'Notifications' },
      { id: 'security', label: 'Security', alert: alertFor('auth') },
      { id: 'audit', label: 'Library audit' },
      { id: 'backup', label: 'Backup & restore' },
      { id: 'appearance', label: 'Appearance' },
    ];
  }, [checks]);

  // Deep links (/settings#lidarr from the health pill, #appearance from the
  // theme picker): scroll once the sections exist.
  const ready = !isLoading;
  useEffect(() => {
    const id = location.hash.replace(/^#/, '');
    if (!ready || !id) return;
    const t = requestAnimationFrame(() => scrollToSection(id, false));
    return () => cancelAnimationFrame(t);
  }, [ready, location.hash, location.key]);

  const notificationsDirty = !!settings && !same(form.notifications, baseline.notifications);

  let server;
  if (isLoading) {
    server = (
      <>
        <CardSkeleton height={320} />
        <CardSkeleton height={420} />
      </>
    );
  } else if (isError) {
    server = (
      <Panel id="general">
        <EmptyState
          size="sm"
          tone="danger"
          icon={faCircleExclamation}
          title="Couldn't load settings"
          message={errorMessage(error)}
          action={<Button variant="default" onClick={() => refetch()}>Retry</Button>}
        />
      </Panel>
    );
  } else {
    server = (
      <ServerSections
        form={form}
        updateField={updateField}
        updateWhisper={updateWhisper}
        profiles={profiles || []}
        lidarrPortError={lidarrPortError}
        generalPortError={generalPortError}
        auditErrors={auditErrors}
        onTestLidarr={() => testMutation.mutate()}
        testingLidarr={testMutation.isPending}
        lidarrResult={lidarrResult}
        onTestNotification={() => testNotifMutation.mutate()}
        testingNotification={testNotifMutation.isPending}
        notificationResult={notificationResult}
        notificationsDirty={notificationsDirty}
        onExport={handleExport}
        onImport={handleImport}
      />
    );
  }

  return (
    <div className="ly-page">
      <PageHeader
        title="Settings"
        subtitle="Connections, providers, notifications and security are saved together; appearance applies instantly"
      />
      <div className={c.layout}>
        <SettingsNav items={navItems} />
        <div className={c.sections}>
          {server}
          <AppearanceSection />
          <SaveBar
            dirty={dirty}
            saving={saveMutation.isPending}
            onSave={handleSave}
            onDiscard={() => setForm(baseline)}
            message={phone ? 'Unsaved' : undefined}
            saveLabel={phone ? 'Save' : 'Save settings'}
          />
        </div>
      </div>
    </div>
  );
}
