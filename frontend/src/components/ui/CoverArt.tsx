import { useState, type CSSProperties } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCompactDisc } from '@fortawesome/free-solid-svg-icons';
import { initials } from '../../format';
import c from './CoverArt.module.css';

/** URLs that failed once (e.g. Lidarr MediaCover 401s): lists never retry them. */
const failedUrls = new Set<string>();

export function isFailedImage(src: string | null | undefined): boolean {
  return !src || failedUrls.has(src);
}

export function markFailedImage(src: string | null | undefined): void {
  if (src) failedUrls.add(src);
}

export interface CoverArtProps {
  src?: string | null;
  /** Use "" for decorative thumbnails next to their title. */
  alt: string;
  kind: 'album' | 'artist';
  /** Monogram source (artist) and title tooltip. */
  name?: string;
  /** px, or 'fill' to fill the parent's width (square). */
  size: number | 'fill';
  /** Default: album square, artist circle. */
  shape?: 'square' | 'circle';
  /** Default 6 (≤40px), 8 (≤96px), 16 (>96px / fill). */
  radius?: number;
  /** Record-sleeve shadow (hero, grid tiles). */
  sleeve?: boolean;
  /** Makes it a button: "View cover full size". */
  onClick?: () => void;
  loading?: 'lazy' | 'eager';
  className?: string;
  style?: CSSProperties;
}

function defaultRadius(size: number | 'fill'): number {
  if (size === 'fill') return 16;
  if (size <= 40) return 6;
  if (size <= 96) return 8;
  return 16;
}

/**
 * Album cover / artist image with a graceful fallback (DESIGN.md 7.2). Missing or
 * broken images never show alt text or a broken-image icon: albums get a disc
 * glyph, artists a monogram, both on --ly-neutral-soft.
 */
export function CoverArt({
  src, alt, kind, name, size, shape, radius, sleeve, onClick, loading = 'lazy', className, style,
}: CoverArtProps) {
  const [failed, setFailed] = useState(() => isFailedImage(src));
  const [loaded, setLoaded] = useState(false);
  const [prevSrc, setPrevSrc] = useState(src);
  // Reset during render when the URL changes (no effect: a cached image can fire
  // onLoad before an effect would run and get hidden again).
  if (prevSrc !== src) {
    setPrevSrc(src);
    setFailed(isFailedImage(src));
    setLoaded(false);
  }

  const round = (shape ?? (kind === 'artist' ? 'circle' : 'square')) === 'circle';
  const box: CSSProperties = {
    ...(size === 'fill' ? { width: '100%' } : { width: size, height: size }),
    borderRadius: round ? '50%' : (radius ?? defaultRadius(size)),
    ...style,
  };
  const showImage = !!src && !failed;
  const label = alt || name || '';

  const content = showImage ? (
    <img
      key={src!}
      className={c.img}
      src={src!}
      alt={onClick ? '' : alt}
      loading={loading}
      decoding="async"
      draggable={false}
      data-loaded={loaded || undefined}
      onLoad={() => setLoaded(true)}
      onError={() => {
        markFailedImage(src);
        setFailed(true);
      }}
    />
  ) : kind === 'artist' ? (
    <span className={c.monogram} aria-hidden="true">
      {/* Tiny avatars keep one letter so the text can stay at the 11.5px minimum. */}
      {typeof size === 'number' && size < 30 ? Array.from(initials(name || alt))[0] : initials(name || alt)}
    </span>
  ) : (
    <span className={c.icon} aria-hidden="true"><FontAwesomeIcon icon={faCompactDisc} /></span>
  );

  const classes = [c.root, sleeve ? 'ly-sleeve' : null, className].filter(Boolean).join(' ');

  if (onClick) {
    return (
      <button
        type="button"
        className={classes}
        style={box}
        onClick={onClick}
        aria-label={label ? `View cover full size: ${label}` : 'View cover full size'}
        data-fill={size === 'fill' || undefined}
        data-fallback={!showImage || undefined}
      >
        {content}
      </button>
    );
  }

  return (
    <span
      className={classes}
      style={box}
      data-fill={size === 'fill' || undefined}
      data-fallback={!showImage || undefined}
      // A fallback still names the image when it isn't decorative.
      role={!showImage && alt ? 'img' : undefined}
      aria-label={!showImage && alt ? alt : undefined}
      title={name}
    >
      {content}
    </span>
  );
}
