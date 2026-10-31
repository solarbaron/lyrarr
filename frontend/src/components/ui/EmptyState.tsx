import type { ReactNode } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import type { IconDefinition } from '@fortawesome/free-solid-svg-icons';
import c from './Feedback.module.css';

export interface EmptyStateProps {
  icon: IconDefinition;
  title: string;
  message?: ReactNode;
  /** One action: secondary unless it is the page's primary. */
  action?: ReactNode;
  /** success for "all done" states; danger only for real failures (load errors). */
  tone?: 'neutral' | 'success' | 'danger';
  /** sm inside tables/cards (padding 32), md for whole pages (padding 64). */
  size?: 'sm' | 'md';
  className?: string;
}

/** Centred empty / all-done / error state (DESIGN.md 7.4). */
export function EmptyState({ icon, title, message, action, tone = 'neutral', size = 'md', className }: EmptyStateProps) {
  return (
    <div
      className={[c.empty, className].filter(Boolean).join(' ')}
      data-size={size}
      data-tone={tone}
      role={tone === 'danger' ? 'alert' : undefined}
    >
      <span className={c.emptyIcon} aria-hidden="true"><FontAwesomeIcon icon={icon} /></span>
      <div className={c.emptyTitle}>{title}</div>
      {message && <div className={c.emptyMessage}>{message}</div>}
      {action && <div className={c.emptyAction}>{action}</div>}
    </div>
  );
}
