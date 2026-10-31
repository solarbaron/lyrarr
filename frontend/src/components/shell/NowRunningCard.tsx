import { Link } from 'react-router-dom';
import { Tooltip, UnstyledButton } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faCircleExclamation, faClock, faImage, faLanguage, faListCheck, faRotate,
  faWandMagicSparkles, type IconDefinition,
} from '@fortawesome/free-solid-svg-icons';
import { getTasks } from '../../api';
import { useJobs, jobCount, jobEta, jobFraction, type JobState } from '../../jobs';
import { plural } from '../../format';
import { useShell } from './ShellContext';
import c from './NowRunningCard.module.css';

interface Task { name: string; next_run_time?: string | null; job_running?: boolean }

/** Icon for a job (DESIGN.md 6.6). */
export function jobIcon(job: Pick<JobState, 'type' | 'kind'>): IconDefinition {
  switch (job.type) {
    case 'sync': return faRotate;
    case 'translate': return faLanguage;
    case 'timing': return faWandMagicSparkles;
    case 'audit': return faListCheck;
    default: return job.kind === 'lyrics' ? faAlignLeft : faImage;
  }
}

/** Three animated bars while a job runs (static under reduced motion). */
export function Equalizer() {
  return (
    <span className={c.eq} aria-hidden="true">
      <span /><span /><span />
    </span>
  );
}

function useSchedulerSummary(): string {
  const { data } = useQuery({ queryKey: ['tasks'], queryFn: getTasks, staleTime: 60_000, refetchInterval: 120_000 });
  const tasks = (Array.isArray(data) ? data : []) as Task[];
  if (!tasks.length) return 'Scheduler idle';
  const scheduled = tasks.filter(t => t.next_run_time && t.next_run_time !== 'Never');
  if (!scheduled.length) return 'Scheduler idle';
  const next = [...scheduled].sort((a, b) => String(a.next_run_time).localeCompare(String(b.next_run_time)))[0];
  return `${plural(tasks.length, 'scheduled task')} · next: ${next.name}`;
}

/** Circular progress ring (rail variant). */
function Ring({ fraction, icon, tone }: { fraction: number | null; icon: IconDefinition; tone: 'accent' | 'danger' | 'neutral' }) {
  const r = 17;
  const circ = 2 * Math.PI * r;
  return (
    <span className={c.ring} data-tone={tone}>
      <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true" className={c.ringSvg}>
        <circle cx="20" cy="20" r={r} className={c.ringTrack} />
        {tone === 'accent' && (
          <circle
            cx="20" cy="20" r={r}
            className={fraction === null ? c.ringSpin : c.ringFill}
            strokeDasharray={fraction === null ? `${circ * 0.3} ${circ}` : `${circ * fraction} ${circ}`}
          />
        )}
      </svg>
      <FontAwesomeIcon icon={icon} className={c.ringIcon} />
    </span>
  );
}

/**
 * The sidebar's now-running card (DESIGN.md 6.6): idle (next scheduled task),
 * running (job, current item, progress, ETA) or failed (until seen). No Cancel:
 * the API has none.
 */
export function NowRunningCard({ rail = false }: { rail?: boolean }) {
  const { job } = useJobs();
  const { openActivity } = useShell();
  const idleText = useSchedulerSummary();

  if (!job) {
    const label = `Idle · ${idleText}`;
    if (rail) {
      return (
        <Tooltip label={label} position="right" openDelay={300}>
          <UnstyledButton component={Link} to="/system?tab=tasks" className={c.railButton} aria-label={`${label}. Open scheduled tasks`}>
            <Ring fraction={null} icon={faClock} tone="neutral" />
          </UnstyledButton>
        </Tooltip>
      );
    }
    return (
      <UnstyledButton component={Link} to="/system?tab=tasks" className={c.card} data-state="idle" aria-label={`${label}. Open scheduled tasks`}>
        <span className={c.tile}><FontAwesomeIcon icon={faClock} /></span>
        <span className={c.text}>
          <span className={c.title}>Idle</span>
          <span className={c.sub}>{idleText}</span>
        </span>
      </UnstyledButton>
    );
  }

  if (!job.active) {
    const label = `${job.label || 'Job failed'}: ${job.error ?? ''}`;
    if (rail) {
      return (
        <Tooltip label={label} position="right" openDelay={300}>
          <UnstyledButton className={c.railButton} onClick={openActivity} aria-label={`${label}. View details`}>
            <Ring fraction={null} icon={faCircleExclamation} tone="danger" />
          </UnstyledButton>
        </Tooltip>
      );
    }
    return (
      <UnstyledButton className={c.card} data-state="failed" onClick={openActivity} aria-label={`${label}. View details`}>
        <span className={c.tile}><FontAwesomeIcon icon={faCircleExclamation} /></span>
        <span className={c.text}>
          <span className={c.title} title={job.label || 'Job failed'}>{job.label || 'Job failed'}</span>
          <span className={c.link}>View details</span>
        </span>
      </UnstyledButton>
    );
  }

  const fraction = jobFraction(job);
  const count = jobCount(job);
  const eta = jobEta(job);
  const summary = [job.label, count, eta].filter(Boolean).join(' · ');

  if (rail) {
    return (
      <Tooltip label={summary} position="right" openDelay={300}>
        <UnstyledButton className={c.railButton} onClick={openActivity} aria-label={`${summary}. Open Activity`}>
          <Ring fraction={fraction} icon={jobIcon(job)} tone="accent" />
        </UnstyledButton>
      </Tooltip>
    );
  }

  return (
    <UnstyledButton className={c.card} data-state="running" onClick={openActivity} aria-label={`${summary}. Open Activity`}>
      <span className={c.head}>
        <span className={c.tile}><FontAwesomeIcon icon={jobIcon(job)} /></span>
        <span className={c.text}>
          <span className={c.title} title={job.label}>{job.label}</span>
          {job.currentTitle && <span className={c.sub} title={job.currentTitle}>{job.currentTitle}</span>}
        </span>
      </span>
      <span className={c.bar} aria-hidden="true">
        {fraction === null
          ? <span className={c.barIndeterminate} />
          : <span className={c.barFill} style={{ width: `${fraction * 100}%` }} />}
      </span>
      <span className={c.meta}>
        <Equalizer />
        {count && <span className="num">{count}</span>}
        {eta && <span className={c.eta}>{eta}</span>}
      </span>
    </UnstyledButton>
  );
}
