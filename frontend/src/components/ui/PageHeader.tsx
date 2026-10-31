import type { ReactNode } from 'react';
import { num } from '../../format';
import c from './Layout.module.css';

export interface PageHeaderProps {
  title: string;
  /** Rendered after the title: 16/500 muted, tabular, "1,919". */
  count?: number;
  /** One line of facts separated by " · ". */
  subtitle?: ReactNode;
  /** Right-aligned; ghost/secondary first, the single primary LAST. */
  actions?: ReactNode;
  className?: string;
}

/** Header for top-level pages (DESIGN.md 7.1). Detail pages use Hero. */
export function PageHeader({ title, count, subtitle, actions, className }: PageHeaderProps) {
  return (
    <header className={[c.pageHeader, className].filter(Boolean).join(' ')}>
      <div className={c.pageHeaderText}>
        <h1 className={c.pageTitle}>
          {title}
          {count !== undefined && count !== null && <span className={c.pageCount}>{num(count)}</span>}
        </h1>
        {subtitle && <div className={c.pageSubtitle}>{subtitle}</div>}
      </div>
      {actions && <div className={c.pageActions}>{actions}</div>}
    </header>
  );
}
