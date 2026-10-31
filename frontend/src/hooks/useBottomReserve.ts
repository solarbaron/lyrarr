import { useEffect, useSyncExternalStore } from 'react';

/**
 * Bottom-space reservation for floating bars (DESIGN.md 6.11). Each floating bar
 * reserves its height on a layer; the total is written to `--ly-bottom-reserved` on
 * <html> (page padding and toasts read it), and a bar sits above every reservation
 * on a lower layer. Stacking bottom-up: tab bar → job bar → bulk/save bar → toasts.
 */
export const BOTTOM_LAYER = { tabBar: 0, jobBar: 1, bar: 2 } as const;

interface Entry { px: number; layer: number }
const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();
let version = 0;

function commit() {
  version++;
  let total = 0;
  entries.forEach(e => { total += e.px; });
  document.documentElement.style.setProperty('--ly-bottom-reserved', `${total}px`);
  listeners.forEach(l => l());
}

export function reserveBottom(id: string, px: number, layer: number = BOTTOM_LAYER.bar): void {
  const prev = entries.get(id);
  if (prev && prev.px === px && prev.layer === layer) return;
  entries.set(id, { px, layer });
  commit();
}

export function releaseBottom(id: string): void {
  if (entries.delete(id)) commit();
}

/** Sum of reservations on layers below `layer` (where a bar on `layer` should sit). */
export function bottomOffsetBelow(layer: number): number {
  let sum = 0;
  entries.forEach(e => { if (e.layer < layer) sum += e.px; });
  return sum;
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/**
 * Reserves `px` on `layer` while `active`, releases on unmount. Returns the space
 * taken by lower layers (e.g. the mobile tab bar) so the caller can sit above it.
 */
export function useBottomReserve(id: string, px: number, active: boolean, layer: number = BOTTOM_LAYER.bar): number {
  useEffect(() => {
    if (!active) {
      releaseBottom(id);
      return;
    }
    reserveBottom(id, px, layer);
    return () => releaseBottom(id);
  }, [id, px, active, layer]);
  useSyncExternalStore(subscribe, () => version, () => 0);
  return bottomOffsetBelow(layer);
}
