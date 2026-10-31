// App shell (DESIGN.md 6). Pages normally need only useShell, BrandMark and the health/theme helpers.
export { AppShell, type AppShellProps } from './AppShell';
export { BrandMark } from './BrandMark';
export { useShell, BOTTOM_LAYER, type ShellContextValue } from './ShellContext';
export { HealthPill, useHealthChecks, healthSummary, type HealthCheck, type HealthLevel } from './HealthPill';
export { ThemePicker, ThemeSwatch } from './ThemePicker';
export { NAV_ITEMS, NAV_GROUPS, navItemFor, type NavItem } from './nav';
