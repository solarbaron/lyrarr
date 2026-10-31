import type { CSSProperties } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faBan, faBars, faCheck, faCircleExclamation, faCircleHalfStroke, faClock, faHand, faMusic, faTriangleExclamation,
  type IconDefinition,
} from '@fortawesome/free-solid-svg-icons';
import type { Glyph, Tone } from './status';
import c from './Status.module.css';

const ICONS: Partial<Record<Glyph, IconDefinition>> = {
  check: faCheck,
  half: faCircleHalfStroke,
  hand: faHand,
  clock: faClock,
  lines: faBars,
  note: faMusic,
  ban: faBan,
};

export interface StatusGlyphProps {
  glyph: Glyph;
  tone?: Tone;
  /** px; the glyph box is square. Default 12. */
  size?: number;
  className?: string;
}

/**
 * Status shape (DESIGN.md 4.7): FontAwesome where an icon exists, CSS for the
 * hollow ring (Missing), diamond (Unknown), dots and the spinner. Always
 * decorative: the label or tooltip next to it carries the meaning.
 */
export function StatusGlyph({ glyph, tone = 'neutral', size = 12, className }: StatusGlyphProps) {
  const style = { '--g': `${size}px` } as CSSProperties;
  const cls = [c.glyph, className].filter(Boolean).join(' ');
  if (glyph === 'alert') {
    return (
      <span className={cls} style={style} aria-hidden="true">
        <FontAwesomeIcon icon={tone === 'warning' ? faTriangleExclamation : faCircleExclamation} />
      </span>
    );
  }
  const icon = ICONS[glyph];
  if (icon) {
    return (
      <span className={cls} style={style} aria-hidden="true">
        <FontAwesomeIcon icon={icon} />
      </span>
    );
  }
  return <span className={cls} style={style} data-shape={glyph} aria-hidden="true" />;
}
