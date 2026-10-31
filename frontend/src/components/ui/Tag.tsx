import type { ReactNode } from 'react';
import { Tooltip } from '@mantine/core';
import c from './Tag.module.css';

export interface TagProps {
  children: ReactNode;
  /** Neutral by default; tones only for meaning (e.g. a warning "‑55s off"). */
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info';
  /** Uppercase code style (language codes, "HC"). */
  code?: boolean;
  title?: string;
  tooltip?: ReactNode;
  className?: string;
}

/**
 * Small neutral tag for types, genres, "Default", "HC", "Composite", "N providers
 * agree" (DESIGN.md 3.1 --ly-neutral-soft). Not a status: use StatusChip for that.
 */
export function Tag({ children, tone = 'neutral', code, title, tooltip, className }: TagProps) {
  const tag = (
    <span
      className={[c.tag, tone === 'neutral' ? null : 'ly-chip', className].filter(Boolean).join(' ')}
      data-tone={tone}
      data-code={code || undefined}
      title={title}
      tabIndex={tooltip ? 0 : undefined}
    >
      {children}
    </span>
  );
  return tooltip ? <Tooltip label={tooltip}>{tag}</Tooltip> : tag;
}

export interface LangTagProps {
  code: string | null | undefined;
  className?: string;
}

/** Detected language code (DESIGN.md 7.7): neutral uppercase tag; null → muted "—". */
export function LangTag({ code, className }: LangTagProps) {
  if (!code) {
    return <span className={[c.none, className].filter(Boolean).join(' ')} aria-label="No language detected">—</span>;
  }
  return <Tag code className={className} title={code.toUpperCase()}>{code}</Tag>;
}
