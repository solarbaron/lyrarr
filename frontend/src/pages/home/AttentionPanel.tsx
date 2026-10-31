import { useState } from 'react';
import { Link } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faArrowRight, faFolderOpen, faListCheck, faMusic, faPlug, faShieldHalved, faTriangleExclamation,
} from '@fortawesome/free-solid-svg-icons';
import { Banner, Panel, type BannerProps } from '../../components/ui';
import { plural } from '../../format';
import { providerLabel } from '../system/providers';
import type { ProviderHealthEntry, ServiceHealth, WantedStats } from './types';
import type { AuditSummary } from '../../types';
import c from './Home.module.css';

const DISMISS_KEY = 'lyrarr-dismissed-notices';

function readDismissed(): string[] {
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(DISMISS_KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

function writeDismissed(ids: string[]) {
  try {
    window.sessionStorage.setItem(DISMISS_KEY, JSON.stringify(ids));
  } catch {
    /* storage unavailable: the dismissal lasts until the page unmounts */
  }
}

/**
 * "Authentication is disabled — enable it in Settings if …" → title + sentence,
 * so the banner doesn't repeat itself. Falls back to a fixed title.
 */
function splitMessage(text: string | undefined, fallbackTitle: string): { title: string; message?: string } {
  if (!text) return { title: fallbackTitle };
  const [head, ...rest] = text.split(/\s+[—–-]\s+/);
  if (!rest.length) return { title: fallbackTitle, message: text };
  const tail = rest.join(' — ').trim();
  const sentence = tail.charAt(0).toUpperCase() + tail.slice(1);
  return { title: `${head.trim()}.`, message: /[.!?]$/.test(sentence) ? sentence : `${sentence}.` };
}

function BannerLink({ to, children }: { to: string; children: string }) {
  return (
    <Link to={to} className={`ly-link ${c.bannerLink}`}>
      {children}
      <FontAwesomeIcon icon={faArrowRight} />
    </Link>
  );
}

interface Item extends Omit<BannerProps, 'onDismiss'> {
  id: string;
  dismissible?: boolean;
}

export interface AttentionPanelProps {
  lidarrEnabled: boolean | undefined;
  health: Record<string, ServiceHealth> | undefined;
  providers: Record<string, ProviderHealthEntry> | undefined;
  /** GET /api/audit/summary (absent on servers without the library audit). */
  audit?: AuditSummary;
  wanted?: WantedStats;
}

/**
 * "Needs attention" (DESIGN.md 8.1): persistent problems as banners, hidden when
 * there are none. The auth notice can be dismissed for the session; Lidarr can't.
 */
export function AttentionPanel({ lidarrEnabled, health, providers, audit, wanted }: AttentionPanelProps) {
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);
  const items: Item[] = [];

  // A library root that isn't mounted: file checks and downloads skip everything under it.
  for (const root of audit?.unreachable_roots ?? []) {
    items.push({
      id: `root:${root}`, tone: 'danger', icon: faFolderOpen,
      title: `Music folder not reachable: ${root}.`,
      message: 'Tracks under it were skipped. Check that it is mounted.',
      action: <BannerLink to="/system?tab=audit">System › Audit</BannerLink>,
    });
  }

  if (lidarrEnabled === false) {
    items.push({
      id: 'lidarr', tone: 'warning', icon: faPlug,
      title: 'Lidarr integration is off.',
      message: 'Lyrarr is working from its last library snapshot.',
      action: <BannerLink to="/settings#lidarr">Open Lidarr settings</BannerLink>,
    });
  } else if (health?.lidarr && health.lidarr.healthy === false) {
    items.push({
      id: 'lidarr', tone: 'danger', icon: faPlug,
      title: 'Lidarr is unreachable.',
      message: health.lidarr.error || 'Lyrarr can’t connect to Lidarr, so the library isn’t syncing.',
      action: <BannerLink to="/settings#lidarr">Check Lidarr settings</BannerLink>,
    });
  }

  for (const [service, s] of Object.entries(health ?? {})) {
    if (service === 'lidarr' || !s || typeof s !== 'object' || s.healthy !== false) continue;
    if (service === 'auth') {
      const text = splitMessage(s.error, 'Authentication is disabled.');
      items.push({
        id: 'auth', tone: s.level === 'error' ? 'danger' : 'warning', icon: faShieldHalved,
        ...text,
        action: <BannerLink to="/settings#security">Security settings</BannerLink>,
        dismissible: true,
      });
      continue;
    }
    if (service.startsWith('library:')) {
      // One banner per unmounted root: the audit's (above) when it reports it, else this one.
      const root = service.slice('library:'.length);
      if ((audit?.unreachable_roots ?? []).includes(root)) continue;
      items.push({
        id: `root:${root}`, tone: s.level === 'warning' ? 'warning' : 'danger', icon: faFolderOpen,
        title: `Music folder not reachable: ${root}.`,
        message: s.error || 'Tracks under it are skipped. Check that it is mounted.',
        action: <BannerLink to="/system">Open System</BannerLink>,
      });
      continue;
    }
    const name = service.charAt(0).toUpperCase() + service.slice(1);
    items.push({
      id: service, tone: s.level === 'warning' ? 'warning' : 'danger', icon: faTriangleExclamation,
      title: `${name} is unhealthy.`,
      message: s.error || s.status,
      action: <BannerLink to="/system">Open System</BannerLink>,
    });
  }

  const paused = Object.entries(providers ?? {}).filter(([, p]) => p && p.available === false);
  if (paused.length) {
    items.push({
      id: 'providers', tone: 'warning', icon: faTriangleExclamation,
      title: `${plural(paused.length, 'provider')} temporarily disabled.`,
      message: `Paused after repeated failures: ${paused.map(([name]) => providerLabel(name)).join(', ')}.`,
      action: <BannerLink to="/system?tab=providers">System › Providers</BannerLink>,
    });
  }

  // Library audit: open critical/high issues, or the post-upgrade check left items to review.
  const sev = audit?.latest?.open_by_severity ?? {};
  const serious = (sev.critical ?? 0) + (sev.high ?? 0);
  const bootstrapReview = audit?.bootstrap?.review ?? 0;
  if (serious > 0) {
    items.push({
      id: 'audit', tone: 'warning', icon: faListCheck,
      title: `${plural(serious, 'library problem')} need${serious === 1 ? 's' : ''} attention.`,
      message: 'The last library check found critical or high-severity issues.',
      action: <BannerLink to="/system?tab=audit">Review</BannerLink>,
    });
  } else if (bootstrapReview > 0) {
    items.push({
      id: 'audit', tone: 'warning', icon: faListCheck,
      title: `Lyrarr checked your library after the upgrade: ${plural(audit?.bootstrap?.changed ?? 0, 'fix', 'fixes')} applied.`,
      message: `${plural(bootstrapReview, 'item needs', 'items need')} your review.`,
      action: <BannerLink to="/system?tab=audit">Review</BannerLink>,
    });
  }

  // Old instrumental flags the upgrade cleared: the real instrumentals need a confirm.
  const cleared = wanted?.instrumental_cleared ?? audit?.instrumental_cleared ?? 0;
  if (cleared > 0) {
    items.push({
      id: 'instrumental-cleared', tone: 'info', icon: faMusic,
      title: `${plural(cleared, 'instrumental flag')} from older versions ${cleared === 1 ? 'was' : 'were'} cleared.`,
      message: 'Review the real instrumentals, so their albums count as complete again.',
      action: <BannerLink to="/wanted?tab=instrumental&iview=cleared">Review</BannerLink>,
    });
  }
  const review = Math.max(0, (audit?.instrumental_review ?? wanted?.instrumental_review ?? 0) - cleared);
  if (review > 0) {
    items.push({
      id: 'instrumental-review', tone: 'info', icon: faMusic,
      title: `${plural(review, 'track')} may be instrumental.`,
      message: 'Some sources say there are no lyrics, or the evidence conflicts. Confirm or reject them.',
      action: <BannerLink to="/wanted?tab=instrumental">Instrumental review</BannerLink>,
      dismissible: true,
    });
  }

  const visible = items.filter(item => !(item.dismissible && dismissed.includes(item.id)));
  if (!visible.length) return null;

  const dismiss = (id: string) => {
    const next = Array.from(new Set([...dismissed, id]));
    writeDismissed(next);
    setDismissed(next);
  };

  return (
    <Panel padding="none" title="Needs attention" aria-label="Needs attention">
      <div className="ly-banner-list">
        {visible.map(({ id, dismissible, ...banner }) => (
          <Banner key={id} {...banner} onDismiss={dismissible ? () => dismiss(id) : undefined} />
        ))}
      </div>
    </Panel>
  );
}
