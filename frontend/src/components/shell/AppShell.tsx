import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { getSystemStatus } from '../../api';
import { useShortcut } from '../../shortcuts';
import { usePreferences } from '../../theme/ThemeProvider';
import { reserveBottom, releaseBottom } from '../../hooks/useBottomReserve';
import { ConfirmProvider } from '../ui';
import { ActivityDrawer } from './ActivityDrawer';
import { CommandPalette } from './CommandPalette';
import { MobileTabBar } from './MobileTabBar';
import { ShortcutSheet } from './ShortcutSheet';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { ShellContext, type ShellContextValue } from './ShellContext';
import c from './AppShell.module.css';

export interface AppShellProps {
  children: ReactNode;
  /** 'form' when sign-in is enabled (shows Sign out). */
  authType: string | null;
  username?: string;
  onLogout(): void;
}

/**
 * The app chrome (DESIGN.md 6): sidebar (or rail / mobile tab bar), sticky top
 * bar, command palette, Activity drawer and shortcut sheet around the routed page.
 */
export function AppShell({ children, authType, username, onLogout }: AppShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const { sidebar, setSidebar } = usePreferences();
  const mobile = useMediaQuery('(max-width: 767px)', false, { getInitialValueInEffect: false });
  const tablet = useMediaQuery('(max-width: 1199px)', false, { getInitialValueInEffect: false }) && !mobile;
  // Checklist 9.4: at 1024px the search is already an icon and the health pill a dot.
  const narrow = useMediaQuery('(max-width: 1024px)', false, { getInitialValueInEffect: false });
  const { data: status } = useQuery({ queryKey: ['system-status'], queryFn: getSystemStatus, staleTime: 60_000 });

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [overlayOpen, setOverlayOpen] = useState(false);

  const rail = tablet || (!mobile && sidebar === 'collapsed');

  // The overlay sidebar (tablets) closes on navigation and when leaving the tablet range.
  useEffect(() => { setOverlayOpen(false); }, [location.pathname, tablet]);
  // Navigating (including the browser Back button) dismisses the command palette:
  // on phones it is a full-screen sheet with no overlay to tap.
  useEffect(() => { setPaletteOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!overlayOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOverlayOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [overlayOpen]);

  const toggleSidebar = useCallback(() => {
    if (tablet) setOverlayOpen(o => !o);
    else if (!mobile) setSidebar(sidebar === 'collapsed' ? 'expanded' : 'collapsed');
  }, [tablet, mobile, sidebar, setSidebar]);

  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const openActivity = useCallback(() => setActivityOpen(true), []);
  const openShortcuts = useCallback(() => setShortcutsOpen(true), []);

  useShortcut('palette', () => setPaletteOpen(o => !o));
  useShortcut('palette-slash', openPalette);
  useShortcut('shortcuts', openShortcuts);
  useShortcut('sidebar', toggleSidebar);
  useShortcut('go-home', () => navigate('/'));
  useShortcut('go-artists', () => navigate('/artists'));
  useShortcut('go-albums', () => navigate('/albums'));
  useShortcut('go-wanted', () => navigate('/wanted'));
  useShortcut('go-bulk', () => navigate('/bulk'));
  useShortcut('go-history', () => navigate('/history'));
  useShortcut('go-profiles', () => navigate('/profiles'));
  useShortcut('go-settings', () => navigate('/settings'));
  useShortcut('go-system', () => navigate('/system'));

  const ctx = useMemo<ShellContextValue>(() => ({
    openPalette,
    openActivity,
    openShortcuts,
    toggleSidebar,
    authType,
    username,
    onLogout,
    reserveBottom,
    releaseBottom,
  }), [openPalette, openActivity, openShortcuts, toggleSidebar, authType, username, onLogout]);

  return (
    <ShellContext.Provider value={ctx}>
      <ConfirmProvider>
        <div className={c.shell} data-rail={rail || undefined} data-mobile={mobile || undefined}>
          <a href="#main" className={c.skip}>Skip to content</a>
          {!mobile && <Sidebar rail={rail} inert={tablet && overlayOpen} version={status?.version} />}
          {tablet && overlayOpen && (
            <>
              <div className={c.scrim} aria-hidden="true" onClick={() => setOverlayOpen(false)} />
              <Sidebar rail={false} overlay onClose={() => setOverlayOpen(false)} version={status?.version} />
            </>
          )}
          <div className={c.column}>
            <TopBar mobile={mobile} narrow={narrow} />
            <main id="main" className={c.content} tabIndex={-1}>
              {children}
            </main>
          </div>
          {mobile && <MobileTabBar />}
        </div>
        <CommandPalette opened={paletteOpen} onClose={() => setPaletteOpen(false)} />
        <ActivityDrawer opened={activityOpen} onClose={() => setActivityOpen(false)} />
        <ShortcutSheet opened={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      </ConfirmProvider>
    </ShellContext.Provider>
  );
}

export default AppShell;
