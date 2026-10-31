import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Checkbox, Tooltip } from '@mantine/core';
import type { AlbumLyricsStatus } from '../../types';
import { CoverArt, type CoverArtProps } from './CoverArt';
import { StatusGlyph } from './StatusGlyph';
import { statusModel } from './status';
import { hasCjk } from '../../format';
import c from './CoverTile.module.css';

export type CoverStatus = 'missing' | 'available' | 'manual';

export interface CoverTileProps {
  href: string;
  art: Omit<CoverArtProps, 'size'> & { size?: CoverArtProps['size'] };
  title: string;
  /** "Pixies · 1990" */
  subtitle?: string;
  /** Optional status badges on the cover (bottom-left). */
  cover?: CoverStatus | string | null;
  lyrics?: AlbumLyricsStatus | string | null;
  /** Fraction for the lyrics badge tooltip ("Partial 7/11"). */
  lyricsFraction?: [number, number];
  selected?: boolean;
  /** Shows a checkbox on hover/focus/selected. */
  onSelectChange?: (v: boolean) => void;
  /** Hover overlay icon buttons (IconButton). */
  actions?: ReactNode;
  className?: string;
}

function Badge({ domain, status, fraction }: { domain: 'cover' | 'albumLyrics'; status: string | null | undefined; fraction?: [number, number] }) {
  const info = statusModel(domain, status, { fraction });
  const what = domain === 'cover' ? 'Cover' : 'Lyrics';
  const text = `${what}: ${info.label}`;
  return (
    <Tooltip label={text}>
      <span className={c.badge} data-tone={info.tone} role="img" aria-label={text}>
        <StatusGlyph glyph={info.glyph} tone={info.tone} size={12} />
      </span>
    </Tooltip>
  );
}

/**
 * Grid item for Albums/Artists grids and "Jump back in" (DESIGN.md 7.2). Status
 * badges sit on a solid raised disc with a ring, so they stay legible on any
 * cover; glyph + tooltip + aria-label, never colour only.
 */
export function CoverTile({
  href, art, title, subtitle, cover, lyrics, lyricsFraction, selected, onSelectChange, actions, className,
}: CoverTileProps) {
  const round = (art.shape ?? (art.kind === 'artist' ? 'circle' : 'square')) === 'circle';
  const showBadges = cover !== undefined || lyrics !== undefined;
  return (
    <div
      className={[c.tile, className].filter(Boolean).join(' ')}
      data-selected={selected || undefined}
      data-selectable={onSelectChange ? true : undefined}
      data-round={round || undefined}
    >
      <Link to={href} className={c.link}>
        <span className={c.art}>
          <CoverArt {...art} alt={art.alt ?? ''} size="fill" sleeve={art.sleeve ?? true} />
          {showBadges && (
            <span className={c.badges}>
              {cover !== undefined && <Badge domain="cover" status={cover} />}
              {lyrics !== undefined && <Badge domain="albumLyrics" status={lyrics} fraction={lyricsFraction} />}
            </span>
          )}
        </span>
        <span className={[c.title, hasCjk(title) ? 'cjk' : null].filter(Boolean).join(' ')} title={title}>{title}</span>
        {subtitle && <span className={c.subtitle} title={subtitle}>{subtitle}</span>}
      </Link>
      {onSelectChange && (
        <span className={c.check}>
          <Checkbox
            checked={!!selected}
            onChange={e => onSelectChange(e.currentTarget.checked)}
            aria-label={`Select ${title}`}
          />
        </span>
      )}
      {actions && <span className={c.actions}>{actions}</span>}
    </div>
  );
}
