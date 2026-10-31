import { Tooltip } from '@mantine/core';
import { statusModel, type StatusDomain, type StatusOptions } from './status';
import { StatusGlyph } from './StatusGlyph';
import c from './Status.module.css';

export interface StatusChipProps {
  domain: StatusDomain;
  status: string | boolean | null | undefined;
  synced?: boolean;
  fraction?: [number, number];
  /** Track lyrics added by the user (tooltip). */
  manual?: boolean;
  /** Extra model options (instrumental reason, agreeing sources, title) for the tooltip. */
  options?: Pick<StatusOptions, 'reason' | 'sources' | 'title'>;
  size?: 'sm' | 'md';
  /** Show the model's tooltip on hover/focus (default true when there is one). */
  withTooltip?: boolean;
  className?: string;
}

/**
 * Status pill for detail pages, track lists, inspector and legends (DESIGN.md 7.3):
 * tone soft background + tone text, glyph + word. Missing is a dashed outline.
 */
export function StatusChip({ domain, status, synced, fraction, manual, options, size = 'md', withTooltip = true, className }: StatusChipProps) {
  const info = statusModel(domain, status, { synced, fraction, manual, ...options });
  const dashed = info.outline === 'dashed';
  const chip = (
    <span
      className={[c.chip, dashed ? null : 'ly-chip', className].filter(Boolean).join(' ')}
      data-tone={info.tone}
      data-dashed={dashed || undefined}
      data-size={size}
      tabIndex={withTooltip && info.tooltip ? 0 : undefined}
      aria-label={info.tooltip ? `${info.label}. ${info.tooltip}` : undefined}
    >
      <StatusGlyph glyph={info.glyph} tone={info.tone} size={size === 'sm' ? 11 : 12} />
      <span className={c.chipLabel}>{info.label}</span>
    </span>
  );
  if (!withTooltip || !info.tooltip) return chip;
  return <Tooltip label={info.tooltip}>{chip}</Tooltip>;
}
