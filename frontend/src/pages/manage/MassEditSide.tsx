import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button, Tooltip } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faArrowUpWideShort, faCheck, faCircleExclamation, faCircleInfo, faDownload, faHeartPulse,
  faImage, faLanguage, faLayerGroup, faRotate, faTrashCan, faXmark, type IconDefinition,
} from '@fortawesome/free-solid-svg-icons';
import { useJobs, jobCount, jobEta, type ActivityItem, type JobState } from '../../jobs';
import { IconButton, JobProgress, Panel, StatusChip } from '../../components/ui';
import { Equalizer, jobIcon } from '../../components/shell/NowRunningCard';
import { num, plural, relTime } from '../../format';
import c from './MassEdit.module.css';

export interface SelectionStats {
  artists: number;
  albums: number;
  tracks: number;
  missing: number;
  unsynced: number;
}

/* ------------------------------------------------------------------ */
/* Running job                                                         */
/* ------------------------------------------------------------------ */

interface FinishedJob {
  label: string;
  type: JobState['type'];
  kind: JobState['kind'];
  at: number;
}

/**
 * Running job card (DESIGN.md 8.8): the shared job state from useJobs()
 * (replaces the old in-page progress monitor), shown while a job runs, when it
 * failed, and for a little while after it finished.
 */
export function JobCard() {
  const { job, items } = useJobs();
  const [finished, setFinished] = useState<FinishedJob | null>(null);
  const prev = useRef<JobState | null>(null);

  useEffect(() => {
    const was = prev.current;
    if (job?.active) setFinished(null);
    else if (was?.active && !job) setFinished({ label: was.label, type: was.type, kind: was.kind, at: Date.now() });
    prev.current = job;
  }, [job]);

  if (job?.active) {
    const eta = jobEta(job);
    const count = jobCount(job);
    const rate = job.ratePerMin ? `${Math.round(job.ratePerMin)} / min` : undefined;
    return (
      <Panel title="Running" actions={<StatusChip domain="job" status="running" size="sm" />} className={c.sideCard}>
        <div role="status" className={c.job}>
          <div className={c.jobHead}>
            <span className={c.jobTile} aria-hidden="true"><FontAwesomeIcon icon={jobIcon(job)} /></span>
            <div className={c.jobText}>
              <div className={c.jobLabel}>{job.label}</div>
              {job.currentTitle && <div className={c.jobCurrent} title={job.currentTitle}>{job.currentTitle}</div>}
            </div>
            <Equalizer />
          </div>
          <JobProgress
            parts={job.determinate
              ? job.parts.map(p => ({ label: p.label, unit: p.unit, done: Math.min(p.done, p.total), total: p.total }))
              : []}
            indeterminate={!job.determinate}
            eta={[rate, eta].filter(Boolean).join(' · ') || undefined}
          />
          {!job.determinate && count && <div className={c.jobCount}>{count}</div>}
        </div>
      </Panel>
    );
  }

  if (job?.error) {
    return (
      <Panel title="Last job" actions={<StatusChip domain="job" status="failed" size="sm" />} className={c.sideCard}>
        <div role="status" className={c.job}>
          <div className={c.jobHead}>
            <span className={c.jobTile} data-tone="danger" aria-hidden="true"><FontAwesomeIcon icon={faCircleExclamation} /></span>
            <div className={c.jobText}>
              <div className={c.jobLabel}>{job.label}</div>
              <div className={c.jobError}>{job.error}</div>
            </div>
          </div>
        </div>
      </Panel>
    );
  }

  if (finished) {
    const last = items[items.length - 1];
    return (
      <Panel
        title="Last job"
        actions={(
          <>
            <StatusChip domain="job" status="done" size="sm" />
            <IconButton icon={faXmark} label="Dismiss" size="sm" onClick={() => setFinished(null)} />
          </>
        )}
        className={c.sideCard}
      >
        <div role="status" className={c.job}>
          <div className={c.jobHead}>
            <span className={c.jobTile} data-tone="success" aria-hidden="true"><FontAwesomeIcon icon={jobIcon(finished)} /></span>
            <div className={c.jobText}>
              <div className={c.jobLabel}>{finished.label}</div>
              {last && last.receivedAt !== null && last.receivedAt >= finished.at - 5000 && (
                <div className={c.jobCurrent} title={last.title}>{last.title}</div>
              )}
            </div>
          </div>
        </div>
      </Panel>
    );
  }

  return null;
}

/* ------------------------------------------------------------------ */
/* Selection + actions                                                 */
/* ------------------------------------------------------------------ */

/** A disabled button doesn't fire pointer events, so the tooltip wraps a span. */
function WhyDisabled({ reason, children }: { reason: string | null; children: ReactNode }) {
  if (!reason) return <>{children}</>;
  return (
    <Tooltip label={reason}>
      <span className={c.disabledWrap} tabIndex={0}>{children}</span>
    </Tooltip>
  );
}

export interface ActionHandlers {
  onFetch(): void;
  onUpgrade(): void;
  onTranslate(): void;
  onDelete(): void;
  fetching: boolean;
  upgrading: boolean;
}

export function SelectionCard({ stats, actions }: { stats: SelectionStats; actions: ActionHandlers }) {
  const none = stats.albums === 0;
  const reason = none ? 'Select albums or artists first' : null;
  return (
    <Panel
      title="Selection"
      subtitle={none ? 'Nothing selected' : `${plural(stats.albums, 'album')} · ${plural(stats.tracks, 'track')}`}
      className={c.sideCard}
    >
      <dl className={c.facts}>
        <div><dt>Artists</dt><dd className="num">{num(stats.artists)}</dd></div>
        <div><dt>Tracks without lyrics</dt><dd className="num">{num(stats.missing)}</dd></div>
        <div><dt>Plain, not synced</dt><dd className="num">{num(stats.unsynced)}</dd></div>
      </dl>

      <div className={c.actionStack}>
        <WhyDisabled reason={reason}>
          <Button
            fullWidth
            variant="filled"
            classNames={{ label: c.btnLabel }}
            leftSection={<FontAwesomeIcon icon={faDownload} />}
            rightSection={<span className="num">{num(stats.missing)}</span>}
            disabled={none}
            loading={actions.fetching}
            onClick={actions.onFetch}
          >
            Fetch missing
          </Button>
        </WhyDisabled>
        <WhyDisabled reason={reason}>
          <Button
            fullWidth
            classNames={{ label: c.btnLabel }}
            variant="default"
            leftSection={<FontAwesomeIcon icon={faArrowUpWideShort} />}
            rightSection={<span className="num">{num(stats.unsynced)}</span>}
            disabled={none}
            loading={actions.upgrading}
            onClick={actions.onUpgrade}
          >
            Upgrade to synced
          </Button>
        </WhyDisabled>
        <WhyDisabled reason={reason}>
          <Button
            fullWidth
            classNames={{ label: c.btnLabel }}
            variant="default"
            leftSection={<FontAwesomeIcon icon={faLanguage} />}
            disabled={none}
            onClick={actions.onTranslate}
          >
            Translate…
          </Button>
        </WhyDisabled>
      </div>

      <div className={c.danger} role="group" aria-labelledby="mass-edit-danger">
        <div id="mass-edit-danger" className={`ly-overline ${c.dangerTitle}`}>Danger zone</div>
        <WhyDisabled reason={reason}>
          <Button
            fullWidth
            classNames={{ label: c.btnLabel }}
            variant="light"
            color="danger"
            leftSection={<FontAwesomeIcon icon={faTrashCan} />}
            disabled={none}
            onClick={actions.onDelete}
          >
            Delete lyrics…
          </Button>
        </WhyDisabled>
        <p className={c.dangerNote}>Files are archived to version history first, so tracks can be restored.</p>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Recent events                                                       */
/* ------------------------------------------------------------------ */

const KIND_ICON: Record<ActivityItem['kind'], IconDefinition> = {
  cover: faImage,
  lyrics: faAlignLeft,
  sync: faRotate,
  health: faHeartPulse,
  batch: faLayerGroup,
  error: faCircleExclamation,
  info: faCircleInfo,
};

/**
 * Last 8 activity events (from useJobs, the same feed as the Activity drawer).
 * `collapsible` (narrow layouts, where the pane sits above the tree): shows the
 * latest event with a toggle for the rest.
 */
export function RecentEvents({ collapsible = false }: { collapsible?: boolean }) {
  const { items } = useJobs();
  const [now, setNow] = useState(() => Date.now());
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  const recent = items.slice(-8).reverse();
  const collapsed = collapsible && !open;
  const shown = collapsed ? recent.slice(0, 1) : recent;
  const toggle = collapsible && recent.length > 1 ? (
    <Button
      variant="subtle"
      color="gray"
      size="compact-sm"
      onClick={() => setOpen(o => !o)}
      aria-expanded={open}
      aria-controls="mass-edit-recent-events"
    >
      {open ? 'Show latest only' : `Show all ${recent.length}`}
    </Button>
  ) : undefined;

  return (
    <Panel
      title="Recent events"
      subtitle={recent.length ? undefined : 'Actions started here report progress live'}
      actions={toggle}
      className={c.sideCard}
    >
      {recent.length === 0 ? (
        <p className={c.eventsEmpty}>No activity yet.</p>
      ) : (
        <ul id="mass-edit-recent-events" className={c.events} aria-label="Recent events">
          {shown.map(item => (
            <li key={item.id} className={c.event} data-replay={item.receivedAt === null || undefined}>
              <span className={c.eventTile} data-tone={item.tone} aria-hidden="true">
                <FontAwesomeIcon icon={item.kind === 'info' && item.tone === 'success' ? faCheck : KIND_ICON[item.kind]} />
              </span>
              <span className={c.eventTitle} title={item.title}>
                {item.tone === 'danger' && <span className="visually-hidden">Error: </span>}
                {item.title}
              </span>
              <span className={c.eventTime}>
                {item.receivedAt === null ? 'Earlier' : relTime(item.receivedAt, now)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
