import { Tooltip } from '@mantine/core';
import { num } from '../../format';
import { toneFill, type Tone } from './status';
import c from './Meter.module.css';

export interface MeterSegment {
  value: number;
  tone: Tone;
  label: string;
  /** Clarification shown in a tooltip on legends (e.g. "Includes manual uploads"). */
  note?: string;
}

export interface MeterProps {
  segments: MeterSegment[];
  total: number;
  /** Bar height: xs 4px, sm 6px (default), md 8px. */
  size?: 'xs' | 'sm' | 'md';
  /** "7/11" after the bar. */
  showCount?: boolean;
  width?: number | string;
  /** Hover/focus tooltip listing each segment (default true). */
  withTooltip?: boolean;
  /** Extra legend entry for the unfilled remainder in the tooltip, e.g. "Missing". */
  remainderLabel?: string;
  'aria-label': string;
  className?: string;
}

/**
 * Coverage/progress bar that is not a running job (DESIGN.md 7.3). Segments are
 * drawn left to right on a --ly-track; when nothing is done the track is hatched
 * so 0% reads "not started", not "broken".
 */
export function Meter({
  segments, total, size = 'sm', showCount, width, withTooltip = true, remainderLabel, className,
  'aria-label': ariaLabel,
}: MeterProps) {
  const safeTotal = Math.max(0, total || 0);
  const sum = segments.reduce((acc, s) => acc + Math.max(0, s.value || 0), 0);
  const clamped = Math.min(sum, safeTotal || sum);
  const empty = clamped <= 0;
  const valueText = `${num(clamped)} of ${num(safeTotal)}`;

  const bar = (
    <span
      className={[c.root, className].filter(Boolean).join(' ')}
      data-size={size}
      style={width !== undefined ? { width } : undefined}
      role="meter"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={safeTotal}
      aria-valuenow={clamped}
      aria-valuetext={valueText}
      tabIndex={withTooltip ? 0 : undefined}
    >
      <span className={[c.track, empty ? 'ly-hatch' : null].filter(Boolean).join(' ')}>
        {!empty && safeTotal > 0 && segments.map((s, i) => {
          const w = (Math.max(0, s.value) / safeTotal) * 100;
          if (w <= 0) return null;
          return <span key={i} className={c.seg} style={{ width: `${Math.min(100, w)}%`, background: toneFill(s.tone) }} />;
        })}
      </span>
      {showCount && (
        <span className={c.count}>
          {num(clamped)}/{num(safeTotal)}
        </span>
      )}
    </span>
  );

  if (!withTooltip) return bar;
  const remainder = Math.max(0, safeTotal - clamped);
  const tip = (
    <span className={c.tip}>
      <span className={c.tipTitle}>{ariaLabel}</span>
      {segments.map((s, i) => (
        <span key={i} className={c.tipRow}>
          <span className={c.tipSwatch} style={{ background: toneFill(s.tone) }} />
          {s.label}
          <span className={c.tipNum}>{num(s.value)}</span>
        </span>
      ))}
      {remainderLabel && (
        <span className={c.tipRow}>
          <span className={c.tipRing} />
          {remainderLabel}
          <span className={c.tipNum}>{num(remainder)}</span>
        </span>
      )}
    </span>
  );
  return <Tooltip label={tip}>{bar}</Tooltip>;
}

export interface LyricsMeterProps {
  synced: number;
  plain: number;
  instrumental?: number;
  total: number;
  size?: MeterProps['size'];
  /** Default 64 (table cell width). */
  width?: number | string;
  showCount?: boolean;
  withTooltip?: boolean;
  'aria-label'?: string;
  className?: string;
}

/** Meter preset for lyrics coverage: synced (success), plain (info), instrumental (neutral); "n/m". */
export function LyricsMeter({
  synced, plain, instrumental = 0, total, size = 'sm', width = 64, showCount = true, withTooltip = true, className,
  'aria-label': ariaLabel = 'Lyrics coverage',
}: LyricsMeterProps) {
  return (
    <Meter
      segments={[
        { value: synced, tone: 'success', label: 'Synced' },
        { value: plain, tone: 'info', label: 'Plain' },
        { value: instrumental, tone: 'neutral', label: 'Instrumental' },
      ]}
      total={total}
      size={size}
      width={width}
      showCount={showCount}
      withTooltip={withTooltip}
      remainderLabel="Missing"
      aria-label={ariaLabel}
      className={className}
    />
  );
}
