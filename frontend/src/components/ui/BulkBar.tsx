import { useEffect, type ReactNode } from 'react';
import { Portal } from '@mantine/core';
import { faXmark } from '@fortawesome/free-solid-svg-icons';
import { num } from '../../format';
import { BOTTOM_LAYER, useBottomReserve } from '../../hooks/useBottomReserve';
import { IconButton } from './IconButton';
import c from './BulkBar.module.css';

export interface BulkBarProps {
  count: number;
  /** Singular noun: "album" → "3 albums selected". */
  noun: string;
  /** Plural when not noun + "s". */
  pluralNoun?: string;
  /** Actions: ONE filled primary + secondaries + ⋯ (size sm). */
  children: ReactNode;
  onClear(): void;
}

const HEIGHT = 52;

function overlayOpen(): boolean {
  return !!document.querySelector(
    '.mantine-Modal-content, .mantine-Drawer-content, .mantine-Menu-dropdown, .mantine-Popover-dropdown, .mantine-Combobox-dropdown',
  );
}

/**
 * Floating selection bar (DESIGN.md 7.6). Fixed at the bottom of the content area,
 * above the mobile tab/job bars; it reserves its height (6.11) so it never covers
 * the last rows and toasts stack above it. Esc clears the selection.
 */
export function BulkBar({ count, noun, pluralNoun, children, onClear }: BulkBarProps) {
  const visible = count > 0;
  const below = useBottomReserve('bulk-bar', HEIGHT + 24, visible, BOTTOM_LAYER.bar);

  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || overlayOpen()) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return;
      onClear();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [visible, onClear]);

  if (!visible) return null;
  const label = `${num(count)} ${count === 1 ? noun : (pluralNoun ?? `${noun}s`)} selected`;
  return (
    <Portal>
      <div
        className={c.bar}
        role="region"
        aria-label="Bulk actions"
        style={{ bottom: `calc(16px + ${below}px + env(safe-area-inset-bottom, 0px))` }}
      >
        <span className={c.count} aria-live="polite">{label}</span>
        <div className={c.actions}>{children}</div>
        <span className={c.divider} aria-hidden="true" />
        <IconButton icon={faXmark} label="Clear selection" shortcut="Esc" onClick={onClear} />
      </div>
    </Portal>
  );
}
