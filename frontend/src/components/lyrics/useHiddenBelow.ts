import { useCallback, useEffect, useState } from 'react';

/**
 * How many list items of a scroll container sit (all but) below its visible area, so a
 * pane that happens to end flush with a card edge can still say "3 more below".
 * Returns a callback ref for the scroll container and the count.
 */
export function useHiddenBelow(itemSelector: string, deps: unknown[] = []) {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [hidden, setHidden] = useState(0);

  const measure = useCallback(() => {
    if (!el) { setHidden(0); return; }
    const bottom = el.getBoundingClientRect().bottom;
    let n = 0;
    el.querySelectorAll<HTMLElement>(itemSelector).forEach(item => {
      // A card showing more than its first ~40px already announces itself.
      if (item.getBoundingClientRect().top > bottom - 40) n += 1;
    });
    setHidden(n);
  }, [el, itemSelector]);

  useEffect(() => {
    if (!el) { setHidden(0); return undefined; }
    measure();
    el.addEventListener('scroll', measure, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => {
      el.removeEventListener('scroll', measure);
      ro.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps re-measure after content changes
  }, [el, measure, ...deps]);

  return { ref: setEl, hidden, element: el };
}
