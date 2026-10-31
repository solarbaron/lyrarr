import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Popover, UnstyledButton } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowRight } from '@fortawesome/free-solid-svg-icons';
import { getHealth, getProviderHealth, getSystemStatus } from '../../api';
import { plural } from '../../format';
import c from './TopBar.module.css';

export type HealthLevel = 'ok' | 'warning' | 'error';

export interface HealthCheck {
  id: string;
  level: HealthLevel;
  title: string;
  reason: string;
  fix?: { label: string; to: string };
}

interface ServiceHealth { healthy?: boolean; status?: string; level?: string; error?: string }
interface ProviderHealth { available?: boolean; type?: string; disabled_until?: string | null }

const REFETCH = 60_000;

/**
 * Health checks for the top-bar pill (DESIGN.md 6.3): /system/health, whether
 * Lidarr is enabled, and provider availability. Shares query keys with System
 * and Home so they read the same cache.
 */
export function useHealthChecks(): { checks: HealthCheck[]; loading: boolean } {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: REFETCH, staleTime: REFETCH });
  const status = useQuery({ queryKey: ['system-status'], queryFn: getSystemStatus, refetchInterval: REFETCH, staleTime: REFETCH });
  const providers = useQuery({ queryKey: ['provider-health'], queryFn: getProviderHealth, refetchInterval: REFETCH, staleTime: REFETCH });

  const checks: HealthCheck[] = [];
  const h = (health.data ?? {}) as Record<string, ServiceHealth>;
  const lidarrEnabled = status.data?.lidarr_enabled;

  if (status.data && lidarrEnabled === false) {
    checks.push({
      id: 'lidarr', level: 'warning', title: 'Lidarr is off',
      reason: 'Working from the last library snapshot; new releases won’t appear.',
      fix: { label: 'Connect Lidarr', to: '/settings#lidarr' },
    });
  } else if (h.lidarr && h.lidarr.healthy === false) {
    checks.push({
      id: 'lidarr', level: 'error', title: 'Lidarr unreachable',
      reason: h.lidarr.error || 'Lyrarr can’t connect to Lidarr.',
      fix: { label: 'Check Lidarr settings', to: '/settings#lidarr' },
    });
  } else if (h.lidarr) {
    checks.push({ id: 'lidarr', level: 'ok', title: 'Lidarr connected', reason: 'Library syncs normally.' });
  }

  for (const [service, s] of Object.entries(h)) {
    if (service === 'lidarr' || !s || typeof s !== 'object') continue;
    if (service === 'auth') {
      checks.push(s.healthy === false
        ? {
          id: 'auth', level: s.level === 'error' ? 'error' : 'warning', title: 'Authentication is off',
          reason: 'Anyone who can reach Lyrarr can change settings and lyrics.',
          fix: { label: 'Open security settings', to: '/settings#security' },
        }
        : { id: 'auth', level: 'ok', title: 'Sign-in required', reason: 'Authentication is enabled.' });
      continue;
    }
    if (s.healthy === false) {
      checks.push({
        id: service, level: s.level === 'warning' ? 'warning' : 'error',
        title: `${service.charAt(0).toUpperCase()}${service.slice(1)} unhealthy`,
        reason: s.error || 'Check System for details.',
        fix: { label: 'Open System', to: '/system' },
      });
    }
  }

  const list = Object.entries((providers.data?.providers ?? {}) as Record<string, ProviderHealth>);
  if (list.length) {
    const paused = list.filter(([, p]) => p.available === false).map(([name]) => name);
    const lyrics = list.filter(([, p]) => p.type === 'lyrics');
    const lyricsDown = lyrics.length > 0 && lyrics.every(([, p]) => p.available === false);
    if (lyricsDown) {
      checks.push({
        id: 'providers', level: 'error', title: 'All lyrics providers paused',
        reason: 'No lyrics can be downloaded until a provider recovers.',
        fix: { label: 'Open providers', to: '/system?tab=providers' },
      });
    } else if (paused.length) {
      checks.push({
        id: 'providers', level: 'warning', title: `${plural(paused.length, 'provider')} paused`,
        reason: `Temporarily disabled after failures: ${paused.join(', ')}.`,
        fix: { label: 'Open providers', to: '/system?tab=providers' },
      });
    } else {
      checks.push({ id: 'providers', level: 'ok', title: 'Providers', reason: `${list.length} of ${list.length} available.` });
    }
  }

  return { checks, loading: health.isLoading || status.isLoading };
}

const LEVEL_TONE: Record<HealthLevel, 'success' | 'warning' | 'danger'> = { ok: 'success', warning: 'warning', error: 'danger' };

/** Summary label: "All good", "2 warnings", "Lidarr unreachable", "3 problems". */
export function healthSummary(checks: HealthCheck[]): { level: HealthLevel; label: string } {
  const errors = checks.filter(ch => ch.level === 'error');
  const warnings = checks.filter(ch => ch.level === 'warning');
  if (errors.length === 1 && warnings.length === 0) return { level: 'error', label: errors[0].title };
  if (errors.length) return { level: 'error', label: plural(errors.length + warnings.length, 'problem') };
  if (warnings.length) return { level: 'warning', label: plural(warnings.length, 'warning') };
  return { level: 'ok', label: 'All good' };
}

/**
 * The only persistent health indicator in the chrome (DESIGN.md 6.3). Click opens
 * a list of checks with a fix link each. At 1024px and below only the dot shows.
 */
export function HealthPill({ compact: forceCompact }: { compact?: boolean }) {
  const { checks, loading } = useHealthChecks();
  const narrow = useMediaQuery('(max-width: 1024px)', false, { getInitialValueInEffect: false });
  const [opened, setOpened] = useState(false);
  if (loading && checks.length === 0) return null;

  const { level, label } = healthSummary(checks);
  const compact = forceCompact ?? narrow;
  const tone = LEVEL_TONE[level];
  const issues = checks.filter(ch => ch.level !== 'ok');
  const ordered = [...issues, ...checks.filter(ch => ch.level === 'ok')];

  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-end" width={320} shadow="md" trapFocus returnFocus>
      <Popover.Target>
        <UnstyledButton
          className={c.healthPill}
          data-compact={compact || undefined}
          data-level={level}
          onClick={() => setOpened(o => !o)}
          aria-label={`System health: ${label}`}
          aria-expanded={opened}
          aria-haspopup="dialog"
          title={compact ? label : undefined}
        >
          <span className={c.healthDot} style={{ background: `var(--ly-${tone})` }} aria-hidden="true" />
          {!compact && <span className={c.healthLabel}>{label}</span>}
        </UnstyledButton>
      </Popover.Target>
      <Popover.Dropdown p={0} aria-label="System health">
        <div className={c.healthHead}>
          <span className={c.healthHeadTitle}>Health</span>
          <Link to="/system" className="ly-link" onClick={() => setOpened(false)}>System</Link>
        </div>
        <ul className={c.healthList}>
          {ordered.map(ch => (
            <li key={ch.id} className={c.healthItem}>
              <span className={c.healthDot} style={{ background: `var(--ly-${LEVEL_TONE[ch.level]})` }} aria-hidden="true" />
              <div className={c.healthText}>
                <div className={c.healthTitle}>
                  {ch.title}
                  <span className="visually-hidden">{ch.level === 'ok' ? ' (ok)' : ` (${ch.level})`}</span>
                </div>
                <div className={c.healthReason}>{ch.reason}</div>
                {ch.fix && (
                  <Link to={ch.fix.to} className={`ly-link ${c.healthFix}`} onClick={() => setOpened(false)}>
                    {ch.fix.label} <FontAwesomeIcon icon={faArrowRight} />
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Popover.Dropdown>
    </Popover>
  );
}
