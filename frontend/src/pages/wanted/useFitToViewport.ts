import { useEffect, type RefObject } from 'react';

/**
 * Keeps a sticky side pane inside the viewport: writes `--fit-max-height` on the
 * element = the space from its current top (or its sticky top) to the bottom of the
 * viewport, minus reserved bottom space (bulk bar, mobile bars). The pane's footer
 * therefore stays visible before the page has scrolled far enough to stick.
 */
export function useFitToViewport(ref: RefObject<HTMLElement | null>, gap = 16) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const styles = getComputedStyle(document.documentElement);
      const stickyTop = parseFloat(styles.getPropertyValue('--ly-sticky-top')) || 56;
      const reserved = parseFloat(styles.getPropertyValue('--ly-bottom-reserved')) || 0;
      const top = Math.max(el.getBoundingClientRect().top, stickyTop + gap);
      const max = Math.max(320, window.innerHeight - top - gap - reserved);
      el.style.setProperty('--fit-max-height', `${Math.round(max)}px`);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    measure();
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    const observer = new MutationObserver(schedule);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      observer.disconnect();
    };
  }, [ref, gap]);
}
