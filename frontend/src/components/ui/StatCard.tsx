import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Tooltip } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowRight, type IconDefinition } from '@fortawesome/free-solid-svg-icons';
import { num, pct } from '../../format';
import { Meter, type MeterSegment } from './Meter';
import { StatusGlyph } from './StatusGlyph';
import { toneFill } from './status';
import c from './DataDisplay.module.css';

export interface CoverageCardProps {
  icon: IconDefinition;
  /** "Cover art" */
  title: string;
  done: number;
  total: number;
  /** "albums" */
  unit: string;
  /** saved/manual or synced/plain/instrumental. */
  segments: MeterSegment[];
  /** Legend entry for the remainder (hollow ring), e.g. "Missing". */
  missingLabel?: string;
  /** Count for the missing entry (default total - done). */
  missingCount?: number;
  /** "Review in Wanted" */
  link?: { label: string; to: string };
  /** Soft button (never filled). */
  action?: ReactNode;
  /** ⋯ Menu. */
  menu?: ReactNode;
  /** "Not started" / "In progress" / "Complete" chip. */
  stateChip?: boolean;
  className?: string;
}

/**
 * Legend label: short text on one line; any clarification (a segment's `note`,
 * or a trailing "(…)" in the label, e.g. "Saved (incl. manual)") moves to a
 * tooltip so the count stays next to its label in a narrow card.
 */
function LegendLabel({ label, note }: { label: string; note?: string }) {
  const m = /^(.+?)\s*\((.+)\)$/.exec(label);
  const text = m ? m[1] : label;
  const hint = note ?? m?.[2];
  if (!hint) return <span className={c.legendLabel}>{label}</span>;
  return (
    <Tooltip label={hint} openDelay={300}>
      <span className={`${c.legendLabel} ${c.legendHint}`} tabIndex={0}>
        {text}
        <span className="visually-hidden"> ({hint})</span>
      </span>
    </Tooltip>
  );
}

/** Coverage card for the Dashboard and Wanted (DESIGN.md 7.7). */
export function CoverageCard({
  icon, title, done, total, unit, segments, missingLabel, missingCount, link, action, menu, stateChip, className,
}: CoverageCardProps) {
  const p = pct(done, total);
  const state = total > 0 && done >= total ? 'complete' : done > 0 ? 'progress' : 'none';
  const missing = missingCount ?? Math.max(0, total - done);
  return (
    <section className={[c.coverage, className].filter(Boolean).join(' ')} aria-label={title}>
      <div className={c.coverageHead}>
        <span className={c.iconTile} aria-hidden="true"><FontAwesomeIcon icon={icon} /></span>
        <h2 className={c.coverageTitle}>{title}</h2>
        {stateChip && (
          <span className={c.stateChip} data-state={state}>
            <StatusGlyph glyph={state === 'complete' ? 'check' : state === 'progress' ? 'half' : 'ring'} size={11} />
            {state === 'complete' ? 'Complete' : state === 'progress' ? 'In progress' : 'Not started'}
          </span>
        )}
        {menu && <span className={c.coverageMenu}>{menu}</span>}
      </div>
      <div className={c.coverageStat}>
        <span className={c.statValue}>{p}%</span>
        <span className={c.statOf}>{num(done)} of {num(total)} {unit}</span>
      </div>
      <Meter
        segments={segments}
        total={total}
        size="md"
        aria-label={`${title} coverage`}
        remainderLabel={missingLabel}
      />
      <ul className={c.legend}>
        {segments.map(s => (
          <li key={s.label} className={c.legendItem} data-zero={s.value === 0 || undefined}>
            <span className={c.legendSwatch} style={{ background: toneFill(s.tone) }} aria-hidden="true" />
            <LegendLabel label={s.label} note={s.note} />
            <span className={c.legendCount}>{num(s.value)}</span>
          </li>
        ))}
        {missingLabel && (
          <li className={c.legendItem} data-zero={missing === 0 || undefined}>
            <span className={c.legendRing} aria-hidden="true" />
            <span className={c.legendLabel}>{missingLabel}</span>
            <span className={c.legendCount}>{num(missing)}</span>
          </li>
        )}
      </ul>
      {(action || link) && (
        <div className={c.coverageFoot}>
          <div>{action}</div>
          {link && (
            <Link to={link.to} className={`ly-link ${c.footLink}`}>
              {link.label}
              <FontAwesomeIcon icon={faArrowRight} />
            </Link>
          )}
        </div>
      )}
    </section>
  );
}

export interface JobPart {
  label: string;
  unit?: string;
  done: number;
  total: number;
}

export interface JobProgressProps {
  parts: JobPart[];
  /** Indeterminate (no totals yet, e.g. sync). */
  indeterminate?: boolean;
  /** "~4 min left" */
  eta?: string;
  className?: string;
}

/**
 * Running-job progress bars ("Covers 12 / 40 albums"), shared by the now-running
 * card, the Activity drawer and Mass edit (DESIGN.md 6.6, 6.10). Accent on track:
 * the only place progress uses the accent.
 */
export function JobProgress({ parts, indeterminate, eta, className }: JobProgressProps) {
  if (indeterminate || parts.length === 0) {
    return (
      <div className={[c.job, className].filter(Boolean).join(' ')}>
        <div className={c.jobTrack} role="progressbar" aria-label="Job progress" aria-busy="true">
          <span className={c.jobIndeterminate} />
        </div>
        {eta && <span className={c.jobEta}>{eta}</span>}
      </div>
    );
  }
  return (
    <div className={[c.job, className].filter(Boolean).join(' ')}>
      {parts.map(part => {
        const p = part.total > 0 ? Math.min(100, (part.done / part.total) * 100) : 0;
        return (
          <div key={part.label} className={c.jobPart}>
            <div className={c.jobLine}>
              <span className={c.jobLabel}>{part.label}</span>
              <span className={c.jobCount}>
                {num(part.done)} / {num(part.total)}{part.unit ? ` ${part.unit}` : ''}
              </span>
            </div>
            <div
              className={c.jobTrack}
              role="progressbar"
              aria-label={`${part.label} progress`}
              aria-valuemin={0}
              aria-valuemax={part.total}
              aria-valuenow={part.done}
              aria-valuetext={`${num(part.done)} of ${num(part.total)}`}
            >
              <span className={c.jobFill} style={{ width: `${p}%` }} />
            </div>
          </div>
        );
      })}
      {eta && <span className={c.jobEta}>{eta}</span>}
    </div>
  );
}
