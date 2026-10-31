import { useState, type ReactNode } from 'react';
import { useTheme, usePreferences } from '../../theme/ThemeProvider';
import { hasCjk } from '../../format';
import { CoverArt, isFailedImage, markFailedImage } from './CoverArt';
import { Tag } from './Tag';
import c from './Hero.module.css';

export interface HeroProps {
  art: { src?: string | null; alt: string; kind: 'album' | 'artist'; name: string; onClick?: () => void };
  /** "ALBUM · 2024 · UNMONITORED" (overline). */
  eyebrow?: string;
  /** Display style, clamped to 2 lines; full text in the title attribute. */
  title: string;
  /** Artist avatar + link · "14 tracks · 53 min". */
  byline?: ReactNode;
  /** Genres: first 4 as neutral tags + "+N" (tooltip lists the rest). */
  tags?: string[];
  /** One primary, secondaries, ⋯. */
  actions?: ReactNode;
  /** Optional right-hand summary (desktop only). */
  aside?: ReactNode;
  className?: string;
}

/**
 * Album and artist detail header (DESIGN.md 7.1): full-bleed band with the art on
 * a blurred-art backdrop. The --ly-hero-scrim layer guarantees text contrast over
 * any pixel; high-contrast themes and the "tint" preference off draw no backdrop.
 * Text never uses the accent.
 */
export function Hero({ art, eyebrow, title, byline, tags, actions, aside, className }: HeroProps) {
  const { theme } = useTheme();
  const { artTint } = usePreferences();
  const [bgFailed, setBgFailed] = useState(false);
  const tint = artTint && theme.contrast !== 'high' && !!art.src && !bgFailed && !isFailedImage(art.src);
  const shown = (tags ?? []).slice(0, 4);
  const rest = (tags ?? []).slice(4);
  const size = art.kind === 'artist' ? 180 : 220;

  return (
    <section className={[c.hero, className].filter(Boolean).join(' ')} data-kind={art.kind} data-tint={tint || undefined}>
      {tint && (
        <div className={c.backdrop} aria-hidden="true">
          <img
            src={art.src!}
            alt=""
            className={c.backdropImg}
            onError={() => {
              markFailedImage(art.src);
              setBgFailed(true);
            }}
          />
          <div className={c.scrim} />
        </div>
      )}
      <div className={c.inner}>
        <CoverArt
          className={c.art}
          src={art.src}
          alt={art.alt}
          name={art.name}
          kind={art.kind}
          size={size}
          sleeve
          loading="eager"
          onClick={art.onClick}
        />
        <div className={c.text}>
          {eyebrow && <div className="ly-overline">{eyebrow}</div>}
          <h1 className={[c.title, hasCjk(title) ? 'cjk' : null].filter(Boolean).join(' ')} title={title}>{title}</h1>
          {byline && <div className={c.byline}>{byline}</div>}
          {shown.length > 0 && (
            <div className={c.tags}>
              {shown.map(t => <Tag key={t}>{t}</Tag>)}
              {rest.length > 0 && <Tag tooltip={rest.join(', ')}>+{rest.length}</Tag>}
            </div>
          )}
          {actions && <div className={c.actions}>{actions}</div>}
        </div>
        {aside && <div className={c.aside}>{aside}</div>}
      </div>
    </section>
  );
}
