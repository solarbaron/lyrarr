import type { ReactNode } from 'react';
import { Button, FileButton, NumberInput, PasswordInput, SegmentedControl, Select, Switch, TextInput } from '@mantine/core';
import { useClipboard } from '@mantine/hooks';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCheck, faCopy, faDownload, faPaperPlane, faPlug, faUpload } from '@fortawesome/free-solid-svg-icons';
import { IconButton, Panel, SettingsRow, StatusGlyph } from '../../components/ui';
import c from './Settings.module.css';

/* The settings document is free-form (sections of key/value pairs from the server). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type SettingsForm = Record<string, any>;

export interface TestResult {
  ok: boolean;
  message: string;
}

export interface ServerSectionsProps {
  form: SettingsForm;
  updateField(section: string, key: string, value: unknown): void;
  updateWhisper(key: string, value: unknown): void;
  profiles: { id: number; name: string }[];
  lidarrPortError?: string;
  generalPortError?: string;
  /** Library audit number fields (blocked from saving while invalid). */
  auditErrors?: { network?: string; retention?: string };
  onTestLidarr(): void;
  testingLidarr: boolean;
  lidarrResult: TestResult | null;
  onTestNotification(): void;
  testingNotification: boolean;
  notificationResult: TestResult | null;
  /** The notification fields differ from the saved settings (the test uses saved ones). */
  notificationsDirty: boolean;
  onExport(): void;
  onImport(file: File | null): void;
}

/** Inline result of a connection test: glyph + message, success or danger. */
function TestOutcome({ result }: { result: TestResult | null }) {
  if (!result) return null;
  return (
    <div className={c.outcome} data-tone={result.ok ? 'success' : 'danger'} role="status">
      <StatusGlyph glyph={result.ok ? 'check' : 'alert'} tone={result.ok ? 'success' : 'danger'} size={12} />
      <span>{result.message}</span>
    </div>
  );
}

// APScheduler cron day_of_week: 0 = Monday … 6 = Sunday.
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
  .map((label, i) => ({ value: String(i), label }));
const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${String(h).padStart(2, '0')}:00` }));

function Section({ id, title, subtitle, children }: { id: string; title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <Panel id={id} title={title} subtitle={subtitle} className={c.section}>
      <div className={c.rows}>{children}</div>
    </Panel>
  );
}

/** The server-side settings sections (one form, one save; DESIGN.md 8.10). */
export function ServerSections({
  form, updateField, updateWhisper, profiles, lidarrPortError, generalPortError, auditErrors,
  onTestLidarr, testingLidarr, lidarrResult, onTestNotification, testingNotification, notificationResult,
  notificationsDirty, onExport, onImport,
}: ServerSectionsProps) {
  const clipboard = useClipboard({ timeout: 1500 });
  const v = (section: string, key: string) => form[section]?.[key] || '';
  const text = (section: string, key: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    updateField(section, key, e.currentTarget.value);

  return (
    <>
      <Section id="general" title="General">
        <SettingsRow
          label="Instance name"
          htmlFor="set-instance-name"
          control={<TextInput id="set-instance-name" placeholder="Lyrarr" value={v('general', 'instance_name')} onChange={text('general', 'instance_name')} />}
        />
        <SettingsRow
          label="Port"
          description="Requires a restart."
          htmlFor="set-general-port"
          control={(
            <NumberInput
              id="set-general-port"
              min={1}
              max={65535}
              allowDecimal={false}
              value={form.general?.port ?? 6868}
              onChange={val => updateField('general', 'port', val)}
              error={generalPortError}
              aria-describedby="set-general-port-desc"
            />
          )}
        />
        <SettingsRow
          label="URL base"
          description="For reverse proxies serving Lyrarr under a subpath. Requires a restart."
          htmlFor="set-url-base"
          control={(
            <TextInput
              id="set-url-base"
              placeholder="/lyrarr"
              value={v('general', 'base_url')}
              onChange={text('general', 'base_url')}
              aria-describedby="set-url-base-desc"
            />
          )}
        />
        <SettingsRow
          label="Default profile for new albums"
          description="Automatically assign this profile to newly synced albums."
          htmlFor="set-default-profile"
          control={(
            <Select
              id="set-default-profile"
              placeholder="None (no auto-assign)"
              clearable
              data={profiles.map(p => ({ value: String(p.id), label: p.name }))}
              value={form.general?.default_profile_id ? String(form.general.default_profile_id) : null}
              onChange={val => updateField('general', 'default_profile_id', val ? Number(val) : null)}
              aria-describedby="set-default-profile-desc"
            />
          )}
        />
      </Section>

      <Section id="lidarr" title="Lidarr" subtitle="Where Lyrarr gets your artists and albums">
        <SettingsRow
          label="Enable Lidarr"
          description="Sync the library from Lidarr. When off, Lyrarr works from the last snapshot."
          htmlFor="set-use-lidarr"
          control={(
            <Switch
              id="set-use-lidarr"
              checked={form.general?.use_lidarr || false}
              onChange={e => updateField('general', 'use_lidarr', e.currentTarget.checked)}
              aria-describedby="set-use-lidarr-desc"
            />
          )}
        />
        <SettingsRow
          label="Host"
          htmlFor="set-lidarr-host"
          control={<TextInput id="set-lidarr-host" placeholder="127.0.0.1" value={v('lidarr', 'ip')} onChange={text('lidarr', 'ip')} />}
        />
        <SettingsRow
          label="Port"
          htmlFor="set-lidarr-port"
          control={(
            <NumberInput
              id="set-lidarr-port"
              placeholder="8686"
              min={1}
              max={65535}
              allowDecimal={false}
              value={form.lidarr?.port ?? 8686}
              onChange={val => updateField('lidarr', 'port', val)}
              error={lidarrPortError}
            />
          )}
        />
        <SettingsRow
          label="Base URL"
          htmlFor="set-lidarr-base"
          control={<TextInput id="set-lidarr-base" placeholder="/" value={v('lidarr', 'base_url')} onChange={text('lidarr', 'base_url')} />}
        />
        <SettingsRow
          label="API key"
          description="Lidarr › Settings › General › Security."
          htmlFor="set-lidarr-key"
          control={(
            <PasswordInput
              id="set-lidarr-key"
              placeholder="Your Lidarr API key"
              value={v('lidarr', 'apikey')}
              onChange={text('lidarr', 'apikey')}
              autoComplete="off"
              aria-describedby="set-lidarr-key-desc"
            />
          )}
        />
        <SettingsRow
          label="Use SSL"
          description="Connect over https."
          htmlFor="set-lidarr-ssl"
          control={<Switch id="set-lidarr-ssl" aria-describedby="set-lidarr-ssl-desc" checked={form.lidarr?.ssl || false} onChange={e => updateField('lidarr', 'ssl', e.currentTarget.checked)} />}
        />
        <SettingsRow
          label="Verify SSL certificate"
          description="Turn off for self-signed certificates."
          htmlFor="set-lidarr-verify"
          control={<Switch id="set-lidarr-verify" aria-describedby="set-lidarr-verify-desc" checked={form.lidarr?.verify_ssl || false} onChange={e => updateField('lidarr', 'verify_ssl', e.currentTarget.checked)} />}
        />
        <SettingsRow
          label="Connection"
          description={<span id="set-lidarr-test-desc">Tests the values above, saved or not.</span>}
          control={(
            <div className={c.testControl}>
              <Button
                variant="default"
                leftSection={<FontAwesomeIcon icon={faPlug} />}
                onClick={onTestLidarr}
                aria-describedby="set-lidarr-test-desc"
                loading={testingLidarr}
              >
                Test connection
              </Button>
              <TestOutcome result={lidarrResult} />
            </div>
          )}
        />
      </Section>

      <Section id="providers" title="Providers & keys" subtitle="Deezer, iTunes, LRCLIB and NetEase work without keys">
        <SettingsRow
          label="fanart.tv API key"
          description="Optional, for artist art."
          htmlFor="set-fanart"
          control={<PasswordInput id="set-fanart" aria-describedby="set-fanart-desc" placeholder="Optional" value={v('fanart', 'apikey')} onChange={text('fanart', 'apikey')} autoComplete="off" />}
        />
        <SettingsRow
          label="Genius API key"
          description="Optional, for plain lyrics."
          htmlFor="set-genius"
          control={<PasswordInput id="set-genius" aria-describedby="set-genius-desc" placeholder="Optional" value={v('genius', 'apikey')} onChange={text('genius', 'apikey')} autoComplete="off" />}
        />
        <SettingsRow
          label="Musixmatch API key"
          description="Optional, for synced lyrics. Free tier: 1,000 requests a day (developer.musixmatch.com)."
          htmlFor="set-musixmatch"
          control={<PasswordInput id="set-musixmatch" aria-describedby="set-musixmatch-desc" placeholder="Optional" value={v('musixmatch', 'apikey')} onChange={text('musixmatch', 'apikey')} autoComplete="off" />}
        />
        <SettingsRow
          label="TheAudioDB API key"
          description="The free test key “2” is used by default. Upgrade at theaudiodb.com/patreon."
          htmlFor="set-audiodb"
          control={<PasswordInput id="set-audiodb" aria-describedby="set-audiodb-desc" placeholder="Default: free test key" value={v('theaudiodb', 'apikey')} onChange={text('theaudiodb', 'apikey')} autoComplete="off" />}
        />
        <SettingsRow
          label="LRCLIB instance"
          description="Point at a self-hosted LRCLIB server, or leave the default."
          htmlFor="set-lrclib"
          control={<TextInput id="set-lrclib" aria-describedby="set-lrclib-desc" placeholder="https://lrclib.net" value={v('lrclib', 'instance')} onChange={text('lrclib', 'instance')} />}
        />
      </Section>

      <Section id="translation" title="Translation" subtitle="Lyrics editor, auto-translate and Mass edit translation">
        <SettingsRow
          label="Engine"
          htmlFor="set-engine"
          control={(
            <Select
              id="set-engine"
              data={[
                { value: 'google', label: 'Google Translate (free, no key)' },
                { value: 'deepl', label: 'DeepL (API key required)' },
              ]}
              value={form.translation?.engine || 'google'}
              allowDeselect={false}
              onChange={val => updateField('translation', 'engine', val || 'google')}
            />
          )}
        />
        <SettingsRow
          label="DeepL API key"
          description="Free-tier keys (ending in :fx) are detected automatically."
          htmlFor="set-deepl"
          control={(
            <PasswordInput
              id="set-deepl" aria-describedby="set-deepl-desc"
              placeholder="xxxxxxxx-xxxx-…:fx"
              value={v('translation', 'deepl_api_key')}
              onChange={text('translation', 'deepl_api_key')}
              autoComplete="off"
            />
          )}
        />
      </Section>

      <Section
        id="whisper"
        title="Whisper (AI sync)"
        subtitle={<>AI lyrics sync from audio. Needs <code className="ly-mono">faster-whisper</code> (in the Docker image)</>}
      >
        <SettingsRow
          label="Model"
          description="Larger is more accurate but slower."
          htmlFor="set-whisper-model"
          control={(
            <Select
              id="set-whisper-model" aria-describedby="set-whisper-model-desc"
              data={[
                { value: 'tiny', label: 'Tiny (~1 GB RAM, fastest)' },
                { value: 'base', label: 'Base (~1.5 GB RAM, balanced)' },
                { value: 'small', label: 'Small (~2 GB RAM, better)' },
                { value: 'medium', label: 'Medium (~5 GB RAM, best)' },
                { value: 'large-v3', label: 'Large v3 (~10 GB RAM, highest)' },
              ]}
              value={form.metadata?.whisper?.model || 'base'}
              allowDeselect={false}
              onChange={val => updateWhisper('model', val)}
            />
          )}
        />
        <SettingsRow
          label="Device"
          description="GPU (CUDA) is much faster if available."
          htmlFor="set-whisper-device"
          control={(
            <Select
              id="set-whisper-device" aria-describedby="set-whisper-device-desc"
              data={[
                { value: 'cpu', label: 'CPU' },
                { value: 'cuda', label: 'CUDA (GPU)' },
                { value: 'auto', label: 'Auto-detect' },
              ]}
              value={form.metadata?.whisper?.device || 'cpu'}
              allowDeselect={false}
              onChange={val => updateWhisper('device', val)}
            />
          )}
        />
        <SettingsRow
          label="Compute type"
          description="int8 is fastest, float32 is most accurate."
          htmlFor="set-whisper-compute"
          control={(
            <Select
              id="set-whisper-compute" aria-describedby="set-whisper-compute-desc"
              data={[
                { value: 'int8', label: 'int8 (fastest)' },
                { value: 'float16', label: 'float16 (balanced)' },
                { value: 'float32', label: 'float32 (most accurate)' },
              ]}
              value={form.metadata?.whisper?.compute_type || 'int8'}
              allowDeselect={false}
              onChange={val => updateWhisper('compute_type', val)}
            />
          )}
        />
      </Section>

      <Section id="notifications" title="Notifications">
        <SettingsRow
          label="Enable notifications"
          htmlFor="set-notif-enabled"
          control={<Switch id="set-notif-enabled" checked={form.notifications?.enabled || false} onChange={e => updateField('notifications', 'enabled', e.currentTarget.checked)} />}
        />
        <SettingsRow
          label="Discord webhook URL"
          htmlFor="set-discord"
          control={(
            <PasswordInput
              id="set-discord"
              placeholder="https://discord.com/api/webhooks/…"
              value={v('notifications', 'discord_webhook')}
              onChange={text('notifications', 'discord_webhook')}
              autoComplete="off"
            />
          )}
        />
        <SettingsRow
          label="Telegram bot token"
          htmlFor="set-telegram-token"
          control={(
            <PasswordInput
              id="set-telegram-token"
              placeholder="123456:ABC-DEF…"
              value={v('notifications', 'telegram_bot_token')}
              onChange={text('notifications', 'telegram_bot_token')}
              autoComplete="off"
            />
          )}
        />
        <SettingsRow
          label="Telegram chat ID"
          htmlFor="set-telegram-chat"
          control={<TextInput id="set-telegram-chat" placeholder="Chat or group ID" value={v('notifications', 'telegram_chat_id')} onChange={text('notifications', 'telegram_chat_id')} />}
        />
        <SettingsRow
          label="Test"
          description={(
            <span id="set-notif-test-desc">
              {notificationsDirty
                ? 'Save first: the test uses the saved settings, not your unsaved changes.'
                : 'Sends a test message to every configured channel.'}
            </span>
          )}
          control={(
            <div className={c.testControl}>
              <Button
                variant="default"
                leftSection={<FontAwesomeIcon icon={faPaperPlane} />}
                onClick={onTestNotification}
                aria-describedby="set-notif-test-desc"
                loading={testingNotification}
              >
                Send test notification
              </Button>
              <TestOutcome result={notificationResult} />
            </div>
          )}
        />
      </Section>

      <Section id="security" title="Security" subtitle="Web UI and API sign-in. Changes take effect after a restart">
        <SettingsRow
          label="Authentication"
          description="How users sign in to Lyrarr."
          htmlFor="set-auth-type"
          control={(
            <Select
              id="set-auth-type"
              data={[
                { value: 'none', label: 'None (no authentication)' },
                { value: 'form', label: 'Forms (login page)' },
                { value: 'basic', label: 'Basic (browser popup)' },
              ]}
              value={form.auth?.type || 'none'}
              allowDeselect={false}
              onChange={val => updateField('auth', 'type', val === 'none' ? null : val)}
              aria-describedby="set-auth-type-desc"
            />
          )}
        />
        <SettingsRow
          label="API key"
          description="Send as the X-API-KEY header. Works with every authentication type."
          htmlFor="set-api-key"
          control={(
            <div className={c.withButton}>
              <PasswordInput
                id="set-api-key"
                className={c.grow}
                placeholder="Auto-generated key for external access"
                value={v('auth', 'apikey')}
                onChange={text('auth', 'apikey')}
                autoComplete="off"
                aria-describedby="set-api-key-desc"
              />
              <IconButton
                icon={clipboard.copied ? faCheck : faCopy}
                label={clipboard.copied ? 'Copied' : 'Copy API key'}
                variant="default"
                size={36}
                disabled={!v('auth', 'apikey')}
                onClick={() => clipboard.copy(v('auth', 'apikey'))}
              />
            </div>
          )}
        />
        {form.auth?.type && (
          <>
            <SettingsRow
              label="Username"
              htmlFor="set-auth-user"
              control={<TextInput id="set-auth-user" placeholder="Admin username" value={v('auth', 'username')} onChange={text('auth', 'username')} autoComplete="username" />}
            />
            <SettingsRow
              label="Password"
              htmlFor="set-auth-password"
              control={(
                <PasswordInput
                  id="set-auth-password"
                  placeholder="Admin password"
                  value={v('auth', 'password')}
                  onChange={text('auth', 'password')}
                  autoComplete="new-password"
                />
              )}
            />
          </>
        )}
      </Section>

      <Section id="audit" title="Library audit" subtitle="Checks lyrics files and records against each other and repairs what it safely can">
        <SettingsRow
          label="Scheduled check"
          description="Run a library check on a schedule. You can always run one from System › Audit."
          htmlFor="set-audit-schedule"
          control={(
            <Select
              id="set-audit-schedule"
              data={[{ value: 'off', label: 'Off' }, { value: 'daily', label: 'Daily' }, { value: 'weekly', label: 'Weekly' }]}
              value={form.audit?.schedule || 'off'}
              allowDeselect={false}
              onChange={val => updateField('audit', 'schedule', val || 'off')}
              aria-describedby="set-audit-schedule-desc"
            />
          )}
        />
        {form.audit?.schedule === 'weekly' && (
          <SettingsRow
            label="Day"
            htmlFor="set-audit-day"
            control={(
              <Select
                id="set-audit-day"
                data={WEEKDAYS}
                value={String(form.audit?.schedule_day ?? 6)}
                allowDeselect={false}
                onChange={val => updateField('audit', 'schedule_day', Number(val ?? 6))}
              />
            )}
          />
        )}
        {form.audit?.schedule && form.audit.schedule !== 'off' && (
          <>
            <SettingsRow
              label="Time"
              description="Server time."
              htmlFor="set-audit-hour"
              control={(
                <Select
                  id="set-audit-hour"
                  data={HOURS}
                  value={String(form.audit?.schedule_hour ?? 3)}
                  allowDeselect={false}
                  onChange={val => updateField('audit', 'schedule_hour', Number(val ?? 3))}
                  aria-describedby="set-audit-hour-desc"
                />
              )}
            />
            <SettingsRow
              label="Scheduled runs"
              description={(
                <span id="set-audit-mode-desc">
                  Scheduled runs never reset download retry timers: they skip the one-time repairs after an upgrade and
                  the online checks. Items that need your review are only ever listed.
                </span>
              )}
              control={(
                <SegmentedControl
                  aria-label="Scheduled runs"
                  aria-describedby="set-audit-mode-desc"
                  fullWidth
                  data={[{ value: 'dry_run', label: 'Report only' }, { value: 'apply_safe', label: 'Apply safe fixes' }]}
                  value={form.audit?.scheduled_mode || 'dry_run'}
                  onChange={val => updateField('audit', 'scheduled_mode', val)}
                />
              )}
            />
            <SettingsRow
              label="Read lyrics files on scheduled runs"
              description="Finds empty, marker-only and broken files. Takes a few minutes on a large library."
              htmlFor="set-audit-file-scan"
              control={(
                <Switch
                  id="set-audit-file-scan"
                  checked={form.audit?.scheduled_include_file_scan ?? true}
                  onChange={e => updateField('audit', 'scheduled_include_file_scan', e.currentTarget.checked)}
                  aria-describedby="set-audit-file-scan-desc"
                />
              )}
            />
          </>
        )}
        <SettingsRow
          label="Fix safe issues automatically after upgrades"
          description="Once after an upgrade, repairs state left by older versions (records only; files are archived before any change) and keeps anything that needs your review."
          htmlFor="set-audit-upgrade"
          control={(
            <Switch
              id="set-audit-upgrade"
              checked={form.audit?.auto_fix_on_upgrade ?? true}
              onChange={e => updateField('audit', 'auto_fix_on_upgrade', e.currentTarget.checked)}
              aria-describedby="set-audit-upgrade-desc"
            />
          )}
        />
        <SettingsRow
          label="Online re-verify limit"
          description="Most tracks one online check may ask the providers about (1–5,000)."
          htmlFor="set-audit-network"
          control={(
            <NumberInput
              id="set-audit-network"
              min={1}
              max={5000}
              allowDecimal={false}
              thousandSeparator=","
              suffix=" tracks"
              value={form.audit?.network_max_tracks ?? 200}
              onChange={val => updateField('audit', 'network_max_tracks', val)}
              error={auditErrors?.network}
              aria-describedby="set-audit-network-desc"
            />
          )}
        />
        <SettingsRow
          label="Keep reports"
          description="Number of past runs kept. The latest upgrade check and reports with lyrics waiting to be restored are always kept."
          htmlFor="set-audit-retention"
          control={(
            <NumberInput
              id="set-audit-retention"
              min={1}
              max={100}
              allowDecimal={false}
              value={form.audit?.report_retention ?? 10}
              onChange={val => updateField('audit', 'report_retention', val)}
              error={auditErrors?.retention}
              aria-describedby="set-audit-retention-desc"
            />
          )}
        />
      </Section>

      <Section id="backup" title="Backup & restore">
        <SettingsRow
          label="Export backup"
          description={<span id="set-export-desc">Downloads your profiles and settings as a JSON file.</span>}
          control={(
            <Button aria-describedby="set-export-desc" variant="default" leftSection={<FontAwesomeIcon icon={faDownload} />} onClick={onExport} className={c.endButton}>
              Export backup
            </Button>
          )}
        />
        <SettingsRow
          label="Import backup"
          description={<span id="set-import-desc">Restores profiles and settings from a backup file. You'll be asked to confirm.</span>}
          control={(
            <FileButton onChange={onImport} accept="application/json">
              {props => (
                <Button variant="default" leftSection={<FontAwesomeIcon icon={faUpload} />} className={c.endButton} aria-describedby="set-import-desc" {...props}>
                  Import backup
                </Button>
              )}
            </FileButton>
          )}
        />
      </Section>
    </>
  );
}
