import { useEffect, useState } from 'react';
import { Button, Drawer, SegmentedControl, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faCheck, faCircleExclamation, faCircleInfo, faHeartPulse, faImage, faLayerGroup, faRotate,
  faWaveSquare, type IconDefinition,
} from '@fortawesome/free-solid-svg-icons';
import { useJobs, jobCount, jobEta, type ActivityItem, type JobState } from '../../jobs';
import { relTime } from '../../format';
import { EmptyState, JobProgress } from '../ui';
import { Equalizer, jobIcon } from './NowRunningCard';
import c from './ActivityDrawer.module.css';

const KIND_ICON: Record<ActivityItem['kind'], IconDefinition> = {
  cover: faImage,
  lyrics: faAlignLeft,
  sync: faRotate,
  health: faHeartPulse,
  batch: faLayerGroup,
  error: faCircleExclamation,
  info: faCircleInfo,
};

function itemIcon(item: ActivityItem): IconDefinition {
  if (item.kind === 'info' && item.tone === 'success') return faCheck;
  return KIND_ICON[item.kind];
}

function minuteLabel(ts: number | null, now: number): string {
  if (ts === null) return 'Earlier';
  if (now - ts < 60_000) return 'Just now';
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function RunningJob({ job }: { job: JobState }) {
  const eta = jobEta(job);
  const count = jobCount(job);
  const rate = job.ratePerMin ? `${Math.round(job.ratePerMin)} / min` : undefined;
  return (
    <div className={c.job} role="status">
      <div className={c.jobHead}>
        <span className={c.jobTile}><FontAwesomeIcon icon={jobIcon(job)} /></span>
        <div className={c.jobText}>
          <div className={c.jobLabel}>{job.label}</div>
          {job.currentTitle && <div className={c.jobCurrent} title={job.currentTitle}>{job.currentTitle}</div>}
        </div>
        <Equalizer />
      </div>
      <JobProgress
        parts={job.determinate ? job.parts.map(p => ({ label: p.label, unit: p.unit, done: Math.min(p.done, p.total), total: p.total })) : []}
        indeterminate={!job.determinate}
        eta={[rate, eta].filter(Boolean).join(' · ') || undefined}
      />
      {!job.determinate && count && <div className={c.jobCount}>{count}</div>}
    </div>
  );
}

export interface ActivityDrawerProps {
  opened: boolean;
  onClose(): void;
}

/**
 * Activity drawer (DESIGN.md 6.10): running jobs with per-part progress and the
 * live event log (newest first, grouped by minute). Opening it marks events seen.
 */
export function ActivityDrawer({ opened, onClose }: ActivityDrawerProps) {
  const { items, running, job, markSeen, clear, unseen } = useJobs();
  const mobile = useMediaQuery('(max-width: 767px)', false, { getInitialValueInEffect: false });
  const [filter, setFilter] = useState<'all' | 'errors'>('all');
  const [now, setNow] = useState(() => Date.now());

  // Everything that arrives while the drawer is open counts as seen.
  useEffect(() => {
    if (opened && (unseen > 0 || (job && !job.active))) markSeen();
  }, [opened, unseen, job, markSeen]);

  // Keep relative times fresh while open.
  useEffect(() => {
    if (!opened) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, [opened]);

  const visible = [...items].reverse().filter(i => filter === 'all' || i.tone === 'danger');
  const groups: { label: string; items: ActivityItem[] }[] = [];
  for (const item of visible) {
    const label = minuteLabel(item.receivedAt, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(item);
    else groups.push({ label, items: [item] });
  }

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position={mobile ? 'bottom' : 'right'}
      size={mobile ? '85vh' : 420}
      title="Activity"
      classNames={{ header: c.header, body: c.body, content: mobile ? `${c.content} ${c.sheet}` : c.content }}
    >
      <div className={c.toolbar}>
        <SegmentedControl
          size="xs"
          value={filter}
          onChange={v => setFilter(v as 'all' | 'errors')}
          data={[{ value: 'all', label: 'All' }, { value: 'errors', label: 'Errors' }]}
          aria-label="Filter activity"
        />
        <Button variant="subtle" color="gray" size="xs" onClick={clear} disabled={items.length === 0}>
          Clear
        </Button>
      </div>

      {running.length > 0 && (
        <section className={c.section} aria-label="Running">
          <div className={c.overline}>Running</div>
          {running.map(j => <RunningJob key={`${j.type}-${j.startedAt}`} job={j} />)}
        </section>
      )}

      <section className={c.section} aria-label="Events">
        {visible.length === 0 ? (
          filter === 'errors' && items.length > 0 ? (
            <EmptyState size="sm" icon={faCheck} tone="success" title="No errors" message="Nothing has failed since you opened Lyrarr." />
          ) : (
            <EmptyState
              size="sm"
              icon={faWaveSquare}
              title="No activity yet"
              message="Downloads, syncs and health changes appear here as they happen."
            />
          )
        ) : null}
        <div role="log" aria-live="polite" aria-label="Activity events" className={c.log}>
          {groups.map(g => (
            <div key={`${g.label}-${g.items[0].id}`} className={c.group}>
              <div className={c.overline}>{g.label}</div>
              <ul className={c.list}>
                {g.items.map(item => (
                  <li key={item.id} className={c.row} data-tone={item.tone}>
                    <span className={c.tile} data-tone={item.tone} aria-hidden="true">
                      <FontAwesomeIcon icon={itemIcon(item)} />
                    </span>
                    <div className={c.rowText}>
                      <Tooltip label={item.title} openDelay={600} position="top-start" disabled={item.tone === 'danger'}>
                        <span className={c.rowTitle}>
                          {item.tone === 'danger' && <span className="visually-hidden">Error: </span>}
                          {item.title}
                        </span>
                      </Tooltip>
                      {item.provider && <span className={c.provider}>{item.provider}</span>}
                    </div>
                    {item.receivedAt !== null && (
                      <span className={c.time} title={new Date(item.receivedAt).toLocaleString()}>
                        {relTime(item.receivedAt, now)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>
    </Drawer>
  );
}
