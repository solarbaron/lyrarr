import { useRef, type FocusEvent, type KeyboardEvent } from 'react';
import { num } from '../../format';
import c from './Controls.module.css';

export interface ViewTab {
  value: string;
  label: string;
  count?: number;
  /** A status marker after the label (e.g. a Settings section with a health warning). */
  dot?: { tone: 'warning' | 'danger'; label: string };
}

export interface ViewTabsProps {
  tabs: ViewTab[];
  value: string;
  onChange(v: string): void;
  'aria-label': string;
  /** id of the panel these tabs control (aria-controls). */
  controls?: string;
  className?: string;
  /** Phones: wrap onto more lines (default) or keep one horizontally scrolling row (sticky sub-navs). */
  wrap?: boolean;
}

/**
 * Saved views with counts (DESIGN.md 7.5): segmented look, count after the label,
 * zero counts stay clickable. Roving focus: ←/→/Home/End move and select.
 */
export function ViewTabs({ tabs, value, onChange, controls, className, wrap = true, 'aria-label': ariaLabel }: ViewTabsProps) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = Math.max(0, tabs.findIndex(t => t.value === value));

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (index + 1) % tabs.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tabs.length - 1;
    if (next < 0) return;
    e.preventDefault();
    onChange(tabs[next].value);
    refs.current[next]?.focus();
  };

  // When the row overflows, the browser only scrolls a focused tab into view if
  // the tab itself is hidden, so an edge tab's focus ring could sit clipped
  // against the scroller. Scroll horizontally to show the ring as well.
  const onFocus = (e: FocusEvent<HTMLDivElement>) => {
    const scroller = e.currentTarget;
    const tab = e.target;
    if (scroller.scrollWidth <= scroller.clientWidth || !(tab instanceof HTMLElement)) return;
    const pad = 6;
    const s = scroller.getBoundingClientRect();
    const t = tab.getBoundingClientRect();
    if (t.left - pad < s.left) scroller.scrollLeft -= s.left - (t.left - pad);
    else if (t.right + pad > s.right) scroller.scrollLeft += t.right + pad - s.right;
  };

  return (
    <div className={[c.viewTabsScroll, className].filter(Boolean).join(' ')} data-nowrap={!wrap || undefined} onFocus={onFocus}>
      <div className={c.viewTabs} role="tablist" aria-label={ariaLabel} onKeyDown={onKeyDown}>
        {tabs.map((t, i) => {
          const active = i === index;
          return (
            <button
              key={t.value}
              ref={el => { refs.current[i] = el; }}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={controls}
              tabIndex={active ? 0 : -1}
              className={c.viewTab}
              data-active={active || undefined}
              onClick={() => onChange(t.value)}
            >
              <span>{t.label}</span>
              {t.count !== undefined && <span className={c.viewTabCount}>{num(t.count)}</span>}
              {t.dot && (
                <span className={c.viewTabDot} data-tone={t.dot.tone} title={t.dot.label}>
                  <span className="visually-hidden">{t.dot.label}</span>
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
