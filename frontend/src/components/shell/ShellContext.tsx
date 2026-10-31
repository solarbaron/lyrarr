import { createContext, useContext } from 'react';
import { reserveBottom, releaseBottom, BOTTOM_LAYER } from '../../hooks/useBottomReserve';

/**
 * Shell-wide state (DESIGN.md 6): overlays opened from anywhere (command palette,
 * Activity drawer, shortcut sheet), the sidebar, the signed-in user, and bottom
 * space reservation for floating bars (6.11). Breadcrumbs live in
 * hooks/useBreadcrumbs (pages call useBreadcrumbs, the top bar reads the trail).
 */
export interface ShellContextValue {
  openPalette(): void;
  openActivity(): void;
  openShortcuts(): void;
  /** `[`: collapse/expand on desktop, open/close the overlay sidebar on tablets. */
  toggleSidebar(): void;
  /** 'form' when sign-in is enabled. */
  authType: string | null;
  username?: string;
  onLogout(): void;
  /** Reserve `px` at the bottom of the viewport (bulk bar, save bar, tab bar). */
  reserveBottom(id: string, px: number, layer?: number): void;
  releaseBottom(id: string): void;
}

const noop = () => {};

const FALLBACK: ShellContextValue = {
  openPalette: noop,
  openActivity: noop,
  openShortcuts: noop,
  toggleSidebar: noop,
  authType: null,
  onLogout: noop,
  reserveBottom: (id, px, layer = BOTTOM_LAYER.bar) => reserveBottom(id, px, layer),
  releaseBottom,
};

export const ShellContext = createContext<ShellContextValue>(FALLBACK);

/** Shell actions for pages and shell parts (inert outside the shell, e.g. the /__ui gallery). */
export function useShell(): ShellContextValue {
  return useContext(ShellContext);
}

export { BOTTOM_LAYER };
