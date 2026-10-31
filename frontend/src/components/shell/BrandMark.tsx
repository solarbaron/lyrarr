/**
 * The Lyrarr mark (DESIGN.md 4.8): an accent tile with three "lyric lines" of
 * decreasing length and a sync dot, drawn in --ly-on-accent. Same geometry as
 * the favicon in index.html; recolours with the theme.
 */
export function BrandMark({ size = 28, className, title }: { size?: number; className?: string; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className={className}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <rect width="32" height="32" rx="8" fill="var(--ly-accent)" />
      <g fill="var(--ly-on-accent)">
        <rect x="8" y="10" width="16" height="2.6" rx="1.3" />
        <rect x="8" y="15" width="12" height="2.6" rx="1.3" />
        <rect x="8" y="20" width="8" height="2.6" rx="1.3" />
        <circle cx="22" cy="21.3" r="2.3" />
      </g>
    </svg>
  );
}

export default BrandMark;
