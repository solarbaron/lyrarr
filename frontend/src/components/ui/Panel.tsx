import type { ReactNode } from 'react';
import c from './Layout.module.css';

export interface PanelProps {
  /** H2 16/600. */
  title?: ReactNode;
  /** 13px muted, inline after the title. */
  subtitle?: ReactNode;
  /** Right side of the header: ghost buttons, ⋯ menu, link. */
  actions?: ReactNode;
  /** none: tables flush to the edges (header gets a bottom border); md (default) = --ly-card-pad. */
  padding?: 'none' | 'sm' | 'md';
  id?: string;
  children: ReactNode;
  className?: string;
  /** Heading level for the title (default 2). */
  headingLevel?: 2 | 3;
  'aria-label'?: string;
}

/** The card/section container (DESIGN.md 7.1). */
export function Panel({
  title, subtitle, actions, padding = 'md', id, children, className, headingLevel = 2, 'aria-label': ariaLabel,
}: PanelProps) {
  const H = headingLevel === 3 ? 'h3' : 'h2';
  const hasHeader = title !== undefined || actions !== undefined;
  return (
    <section
      id={id}
      className={[c.panel, className].filter(Boolean).join(' ')}
      data-padding={padding}
      aria-label={ariaLabel}
    >
      {hasHeader && (
        <div className={c.panelHeader}>
          <div className={c.panelHeading}>
            {title !== undefined && <H className={c.panelTitle}>{title}</H>}
            {subtitle && <span className={c.panelSubtitle}>{subtitle}</span>}
          </div>
          {actions && <div className={c.panelActions}>{actions}</div>}
        </div>
      )}
      <div className={c.panelBody}>{children}</div>
    </section>
  );
}
