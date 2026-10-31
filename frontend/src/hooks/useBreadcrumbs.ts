import { useEffect, useSyncExternalStore } from 'react';

/** One breadcrumb in the top bar (DESIGN.md 6.3). The last crumb is the current page. */
export interface Crumb {
  label: string;
  to?: string;
}

const EMPTY: Crumb[] = [];
let current: Crumb[] = EMPTY;
let owner = 0;
let nextOwner = 1;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach(l => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/**
 * Sets the top-bar breadcrumbs for the lifetime of the calling page (detail pages):
 *
 *   useBreadcrumbs([{ label: 'Albums', to: '/albums' }, { label: artist, to: `/artists/${id}` }, { label: title }]);
 *
 * Re-runs when labels/links change (e.g. after data loads); cleared on unmount.
 */
export function useBreadcrumbs(crumbs: Crumb[]): void {
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    const id = nextOwner++;
    owner = id;
    current = JSON.parse(key) as Crumb[];
    emit();
    return () => {
      if (owner === id) {
        owner = 0;
        current = EMPTY;
        emit();
      }
    };
  }, [key]);
}

/** Read side, for the top bar / mobile back link (shell). */
export function useBreadcrumbTrail(): Crumb[] {
  return useSyncExternalStore(subscribe, () => current, () => EMPTY);
}
