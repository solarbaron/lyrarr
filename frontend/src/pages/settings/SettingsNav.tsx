import { useCallback, useEffect, useState, type MouseEvent } from 'react';
import { useMediaQuery, useReducedMotion } from '@mantine/hooks';
import { ViewTabs } from '../../components/ui';
import c from './Settings.module.css';

export interface SettingsNavItem {
  id: string;
  label: string;
  /** Health dot ("Lidarr is off"). */
  alert?: { tone: 'warning' | 'danger'; label: string };
}

/** Scrolls to a section, respecting reduced motion (OS or in-app). */
export function scrollToSection(id: string, smooth = true): void {
  const el = document.getElementById(id);
  if (!el) return;
  const reduce = document.documentElement.getAttribute('data-motion') === 'reduce'
    || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: smooth && !reduce ? 'smooth' : 'auto', block: 'start' });
}

/** Which section is under the top of the viewport (scroll-spy). */
function useActiveSection(ids: string[]): [string, (id: string) => void] {
  const [active, setActive] = useState(ids[0]);
  const key = ids.join(',');

  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      // Just below whatever is pinned at the top: the top bar, plus the tab
      // row on narrow screens.
      const top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ly-sticky-top')) || 56;
      const row = document.querySelector(`.${c.tabsRow}`)?.getBoundingClientRect().bottom ?? 0;
      const line = Math.max(top, row) + 48;
      let current = ids[0];
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= line) current = id;
      }
      // At the very bottom the last (short) sections can't reach the line.
      const doc = document.scrollingElement || document.documentElement;
      if (doc.scrollTop + window.innerHeight >= doc.scrollHeight - 4) current = ids[ids.length - 1];
      setActive(current);
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(measure); };
    measure();
    document.addEventListener('scroll', onScroll, { capture: true, passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      document.removeEventListener('scroll', onScroll, { capture: true });
      window.removeEventListener('resize', onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return [active, setActive];
}

/**
 * Settings sub-nav (DESIGN.md 8.10): sticky 200px list with scroll-spy at
 * ≥ 1024px, a horizontally scrolling ViewTabs row below. Health dots mirror the
 * top-bar health checks.
 */
export function SettingsNav({ items }: { items: SettingsNavItem[] }) {
  const narrow = useMediaQuery('(max-width: 1023px)');
  const reduceMotion = useReducedMotion();
  const [active, setActive] = useActiveSection(items.map(i => i.id));

  const go = useCallback((id: string) => {
    setActive(id);
    scrollToSection(id, !reduceMotion);
    // Keep the address bookmarkable without a router navigation (no re-scroll).
    window.history.replaceState(window.history.state, '', `#${id}`);
  }, [reduceMotion, setActive]);

  // Keep the active tab in view in the scrolling row.
  useEffect(() => {
    if (!narrow) return;
    document.querySelector<HTMLElement>(`.${c.tabsRow} [role="tab"][aria-selected="true"]`)
      ?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  }, [narrow, active]);

  if (narrow) {
    return (
      <div className={c.tabsRow}>
        <ViewTabs
          aria-label="Settings sections"
          tabs={items.map(i => ({ value: i.id, label: i.label, dot: i.alert }))}
          value={active}
          onChange={go}
          wrap={false}
        />
      </div>
    );
  }

  return (
    <nav className={c.nav} aria-label="Settings sections">
      <ul>
        {items.map(item => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              className={c.navItem}
              aria-current={active === item.id ? 'location' : undefined}
              data-active={active === item.id || undefined}
              onClick={(e: MouseEvent<HTMLAnchorElement>) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                e.preventDefault();
                go(item.id);
              }}
            >
              <span>{item.label}</span>
              {item.alert && (
                <span className={c.navDot} data-tone={item.alert.tone} title={item.alert.label}>
                  <span className="visually-hidden">{item.alert.label}</span>
                </span>
              )}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
