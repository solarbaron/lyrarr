import { Link } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowRight, faPlug, faServer, faShieldHalved, type IconDefinition } from '@fortawesome/free-solid-svg-icons';
import { KeyValueList, Panel, StatusChip } from '../../components/ui';
import type { ProviderHealthEntry, ServiceHealth } from '../home/types';
import c from './System.module.css';

export interface SystemStatus {
  version?: string;
  python_version?: string;
  os?: string;
  lidarr_configured?: boolean;
  lidarr_enabled?: boolean;
}

interface Check {
  id: string;
  icon: IconDefinition;
  name: string;
  status: 'healthy' | 'warning' | 'error' | 'disabled' | 'unknown';
  detail: string;
  link?: { label: string; to: string };
}

function lidarrCheck(status: SystemStatus | undefined, lidarr: ServiceHealth | undefined): Check {
  if (status && status.lidarr_enabled === false) {
    return {
      // A warning, as in the top-bar health pill and the Home banners ("2 warnings").
      id: 'lidarr', icon: faPlug, name: 'Lidarr', status: 'warning',
      detail: status.lidarr_configured
        ? 'Integration is turned off. Lyrarr works from its last library snapshot.'
        : 'Not configured. Lyrarr works from its last library snapshot.',
      link: { label: 'Lidarr settings', to: '/settings#lidarr' },
    };
  }
  if (!lidarr) return { id: 'lidarr', icon: faPlug, name: 'Lidarr', status: 'unknown', detail: 'No health report yet.' };
  return lidarr.healthy
    ? { id: 'lidarr', icon: faPlug, name: 'Lidarr', status: 'healthy', detail: lidarr.status ? `Status: ${lidarr.status}` : 'Connected.' }
    : {
      id: 'lidarr', icon: faPlug, name: 'Lidarr', status: 'error',
      detail: lidarr.error || lidarr.status || 'Lyrarr can’t connect to Lidarr.',
      link: { label: 'Lidarr settings', to: '/settings#lidarr' },
    };
}

export interface StatusTabProps {
  status: SystemStatus | undefined;
  health: Record<string, ServiceHealth> | undefined;
  providers: Record<string, ProviderHealthEntry> | undefined;
}

/** Server text as a sentence ("…beyond a trusted network" → "…beyond a trusted network."), as on Home. */
function asSentence(text: string): string {
  const t = text.trim();
  if (!t) return t;
  const s = t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?…]$/.test(s) ? s : `${s}.`;
}

/** System › Status (DESIGN.md 8.11): versions plus every health check with its reason. */
export function StatusTab({ status, health, providers }: StatusTabProps) {
  const checks: Check[] = [lidarrCheck(status, health?.lidarr)];

  for (const [service, s] of Object.entries(health ?? {})) {
    if (service === 'lidarr' || !s || typeof s !== 'object') continue;
    if (service === 'auth') {
      checks.push(s.healthy
        ? { id: 'auth', icon: faShieldHalved, name: 'Authentication', status: 'healthy', detail: 'Sign-in is required.' }
        : {
          // Disabled auth is a warning, not an outage; the server explains why.
          id: 'auth', icon: faShieldHalved, name: 'Authentication', status: s.level === 'error' ? 'error' : 'warning',
          detail: asSentence(s.error || s.status || 'Authentication is disabled.'),
          link: { label: 'Security settings', to: '/settings#security' },
        });
      continue;
    }
    const name = service.charAt(0).toUpperCase() + service.slice(1);
    checks.push({
      id: service, icon: faServer, name,
      status: s.healthy ? 'healthy' : s.level === 'warning' ? 'warning' : 'error',
      detail: s.error ? asSentence(s.error) : (s.status ? `Status: ${s.status}` : s.healthy ? 'Healthy.' : 'Unhealthy.'),
    });
  }

  const list = Object.entries(providers ?? {});
  if (list.length) {
    const paused = list.filter(([, p]) => !p.available).length;
    checks.push({
      id: 'providers', icon: faServer, name: 'Providers',
      status: paused === 0 ? 'healthy' : paused === list.length ? 'error' : 'warning',
      detail: paused === 0
        ? `${list.length} of ${list.length} available.`
        : `${list.length - paused} of ${list.length} available; ${paused} paused after failures.`,
      link: { label: 'Providers', to: '/system?tab=providers' },
    });
  }

  const lidarrValue = !status
    ? '—'
    : `${status.lidarr_configured ? 'Configured' : 'Not configured'} · ${status.lidarr_enabled ? 'Enabled' : 'Disabled'}`;

  return (
    <div className={c.stack}>
      <Panel title="About">
        <KeyValueList
          columns={2}
          items={[
            { label: 'Version', value: status?.version || 'dev' },
            { label: 'Python', value: status?.python_version || '—' },
            { label: 'OS', value: status?.os || '—' },
            { label: 'Lidarr', value: lidarrValue },
          ]}
        />
      </Panel>

      <Panel title="Health" padding="none">
        <ul className={c.checks} aria-label="Health checks">
          {checks.map(check => (
            <li key={check.id} className={c.check}>
              <span className={c.checkIcon} aria-hidden="true"><FontAwesomeIcon icon={check.icon} /></span>
              <div className={c.checkText}>
                <span className={c.checkName}>{check.name}</span>
                <span className={c.checkDetail} role={check.status === 'warning' || check.status === 'error' ? 'alert' : undefined}>
                  {check.detail}
                </span>
              </div>
              {check.link && (
                <Link to={check.link.to} className={`ly-link ${c.checkLink}`}>
                  {check.link.label}
                  <FontAwesomeIcon icon={faArrowRight} />
                </Link>
              )}
              <StatusChip domain="health" status={check.status} size="sm" />
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
