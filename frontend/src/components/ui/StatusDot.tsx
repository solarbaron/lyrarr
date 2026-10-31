import { Tooltip } from '@mantine/core';
import { statusModel, type StatusDomain, type StatusOptions } from './status';
import { StatusGlyph } from './StatusGlyph';
import c from './Status.module.css';

export interface StatusDotProps {
  domain: StatusDomain;
  status: string | boolean | null | undefined;
  synced?: boolean;
  fraction?: [number, number];
  manual?: boolean;
  /** Extra model options (instrumental reason, agreeing sources, title) for the tooltip. */
  options?: Pick<StatusOptions, 'reason' | 'sources' | 'title'>;
  /** Glyph only; the label moves into the tooltip and the accessible name. */
  hideLabel?: boolean;
  className?: string;
}

/**
 * Compact status for dense tables (DESIGN.md 7.3): a 10px glyph in the tone colour
 * (ring/diamond in secondary text) + a 13px secondary label. Rows of "Missing"
 * read as quiet grey text with a hollow ring.
 */
export function StatusDot({ domain, status, synced, fraction, manual, options, hideLabel, className }: StatusDotProps) {
  const info = statusModel(domain, status, { synced, fraction, manual, ...options });
  const tip = hideLabel ? [info.label, info.tooltip].filter(Boolean).join(' · ') : info.tooltip;
  const dot = (
    <span
      className={[c.dot, className].filter(Boolean).join(' ')}
      data-tone={info.tone}
      role={hideLabel ? 'img' : undefined}
      aria-label={hideLabel ? info.label : undefined}
      tabIndex={tip ? 0 : undefined}
    >
      <StatusGlyph glyph={info.glyph} tone={info.tone} size={10} />
      {!hideLabel && <span className={c.dotLabel}>{info.label}</span>}
    </span>
  );
  if (!tip) return dot;
  return <Tooltip label={tip}>{dot}</Tooltip>;
}
