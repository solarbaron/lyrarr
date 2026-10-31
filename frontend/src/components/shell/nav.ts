import {
  faHouse, faUser, faCompactDisc, faInbox, faLayerGroup, faClockRotateLeft,
  faIdBadge, faSliders, faServer, type IconDefinition,
} from '@fortawesome/free-solid-svg-icons';

/** Sidebar group a nav item belongs to (DESIGN.md 6.2). */
export type NavGroup = 'home' | 'library' | 'todo' | 'manage';

export interface NavItem {
  label: string;
  path: string;
  icon: IconDefinition;
  group: NavGroup;
  /** Shortcut id in shortcuts.ts ("g a"). */
  shortcut: string;
  /** Which sidebar count to show on the right. */
  count?: 'artists' | 'albums' | 'wanted' | 'profiles';
}

export const NAV_ITEMS: NavItem[] = [
  { label: 'Home', path: '/', icon: faHouse, group: 'home', shortcut: 'go-home' },
  { label: 'Artists', path: '/artists', icon: faUser, group: 'library', shortcut: 'go-artists', count: 'artists' },
  { label: 'Albums', path: '/albums', icon: faCompactDisc, group: 'library', shortcut: 'go-albums', count: 'albums' },
  { label: 'Wanted', path: '/wanted', icon: faInbox, group: 'todo', shortcut: 'go-wanted', count: 'wanted' },
  { label: 'Mass edit', path: '/bulk', icon: faLayerGroup, group: 'todo', shortcut: 'go-bulk' },
  { label: 'History', path: '/history', icon: faClockRotateLeft, group: 'todo', shortcut: 'go-history' },
  { label: 'Profiles', path: '/profiles', icon: faIdBadge, group: 'manage', shortcut: 'go-profiles', count: 'profiles' },
  { label: 'Settings', path: '/settings', icon: faSliders, group: 'manage', shortcut: 'go-settings' },
  { label: 'System', path: '/system', icon: faServer, group: 'manage', shortcut: 'go-system' },
];

export const NAV_GROUPS: { id: NavGroup; label: string | null }[] = [
  { id: 'home', label: null },
  { id: 'library', label: 'Library' },
  { id: 'todo', label: 'To do' },
  { id: 'manage', label: 'Manage' },
];

/** The nav item a pathname belongs to ("/albums/12" → Albums). */
export function navItemFor(pathname: string): NavItem | undefined {
  if (pathname === '/' || pathname === '') return NAV_ITEMS[0];
  return NAV_ITEMS.find(i => i.path !== '/' && (pathname === i.path || pathname.startsWith(`${i.path}/`)));
}
