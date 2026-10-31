import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from 'react';
import { MantineProvider } from '@mantine/core';
import {
  DEFAULT_DARK_THEME,
  DEFAULT_LIGHT_THEME,
  themeById,
  type LyrarrTheme,
  type ThemeId,
  type ThemeScheme,
} from './themes';
import { cssVariablesResolver, mantineTheme } from './mantine';

/* ------------------------------------------------------------------
   Persistence (DESIGN.md 2.3). The pre-paint script in index.html runs
   the same algorithm; keep the two in sync.
   ------------------------------------------------------------------ */

export type ThemePreference = ThemeId | 'system';

const KEY_THEME = 'lyrarr-theme';
const KEY_DARK = 'lyrarr-theme-dark';
const KEY_LIGHT = 'lyrarr-theme-light';

const LEGACY: Record<string, ThemeId> = { dark: 'afterhours', light: 'linernotes' };
const DARK_QUERY = '(prefers-color-scheme: dark)';

function readKey(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeKey(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode, quota): the choice lasts for this session only */
  }
}

/** Stored preference, with legacy 'dark'/'light' migrated and unknown values reset to 'system'. */
export function readPreference(): ThemePreference {
  const raw = readKey(KEY_THEME);
  if (raw === null) return 'system';
  const migrated = LEGACY[raw] ?? raw;
  if (themeById(migrated)) {
    if (migrated !== raw) writeKey(KEY_THEME, migrated);
    return migrated as ThemeId;
  }
  if (raw !== 'system') writeKey(KEY_THEME, 'system');
  return 'system';
}

function readPairValue(key: string, scheme: ThemeScheme): ThemeId {
  const t = themeById(readKey(key));
  if (t && t.scheme === scheme) return t.id;
  return scheme === 'dark' ? DEFAULT_DARK_THEME : DEFAULT_LIGHT_THEME;
}

export function readSystemPair(): { dark: ThemeId; light: ThemeId } {
  return { dark: readPairValue(KEY_DARK, 'dark'), light: readPairValue(KEY_LIGHT, 'light') };
}

export function getOsScheme(): ThemeScheme {
  try {
    return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
  } catch {
    return 'dark';
  }
}

export function resolveTheme(
  preference: ThemePreference,
  pair: { dark: ThemeId; light: ThemeId },
  osScheme: ThemeScheme,
): LyrarrTheme {
  const explicit = preference === 'system' ? undefined : themeById(preference);
  if (explicit) return explicit;
  return themeById(osScheme === 'dark' ? pair.dark : pair.light)
    ?? themeById(osScheme === 'dark' ? DEFAULT_DARK_THEME : DEFAULT_LIGHT_THEME)!;
}

/** The theme the stored preference resolves to right now (used by main.tsx before first render). */
export function resolveInitialTheme(): LyrarrTheme {
  return resolveTheme(readPreference(), readSystemPair(), getOsScheme());
}

/** Writes the theme onto <html>: attributes, inline --ly-* tokens and the accent tuple (DESIGN.md 2.4). */
export function applyTheme(theme: LyrarrTheme): void {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme.id);
  root.setAttribute('data-contrast', theme.contrast);
  root.setAttribute('data-mantine-color-scheme', theme.scheme);
  root.style.colorScheme = theme.scheme;
  root.style.removeProperty('background-color');
  for (const [name, value] of Object.entries(theme.tokens)) root.style.setProperty(name, value);
  theme.accent.forEach((shade, i) => root.style.setProperty(`--ly-accent-${i}`, shade));
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme.tokens['--ly-nav']);
}

/* ------------------------------------------------------------------
   Display preferences (DESIGN.md 2.5)
   ------------------------------------------------------------------ */

export type Density = 'comfortable' | 'compact';
export type MotionPref = 'system' | 'reduce';
export type LibraryView = 'table' | 'grid';
export type SidebarState = 'expanded' | 'collapsed';

const KEY_DENSITY = 'lyrarr-density';
const KEY_MOTION = 'lyrarr-motion';
const KEY_ART_TINT = 'lyrarr-art-tint';
const KEY_ALBUM_VIEW = 'lyrarr-album-view';
const KEY_ARTIST_VIEW = 'lyrarr-artist-view';
const KEY_SIDEBAR = 'lyrarr-sidebar';

function readEnum<T extends string>(key: string, values: readonly T[]): T {
  const raw = readKey(key);
  return (values as readonly string[]).includes(raw ?? '') ? (raw as T) : values[0];
}

interface StoredPreferences {
  density: Density;
  motion: MotionPref;
  artTint: boolean;
  albumView: LibraryView;
  artistView: LibraryView;
  sidebar: SidebarState;
}

function readPreferences(): StoredPreferences {
  return {
    density: readEnum(KEY_DENSITY, ['comfortable', 'compact'] as const),
    motion: readEnum(KEY_MOTION, ['system', 'reduce'] as const),
    artTint: readEnum(KEY_ART_TINT, ['on', 'off'] as const) === 'on',
    albumView: readEnum(KEY_ALBUM_VIEW, ['table', 'grid'] as const),
    artistView: readEnum(KEY_ARTIST_VIEW, ['table', 'grid'] as const),
    sidebar: readEnum(KEY_SIDEBAR, ['expanded', 'collapsed'] as const),
  };
}

/** Root attributes for the display preferences; safe to call before React renders. */
export function applyPreferences(p: Pick<StoredPreferences, 'density' | 'motion' | 'artTint' | 'sidebar'>): void {
  const root = document.documentElement;
  root.setAttribute('data-density', p.density);
  if (p.motion === 'reduce') root.setAttribute('data-motion', 'reduce');
  else root.removeAttribute('data-motion');
  root.setAttribute('data-art-tint', p.artTint ? 'on' : 'off');
  root.setAttribute('data-sidebar', p.sidebar);
}

export function applyInitialPreferences(): void {
  applyPreferences(readPreferences());
}

export interface PreferencesValue {
  density: Density;
  setDensity(v: Density): void;
  motion: MotionPref;
  setMotion(v: MotionPref): void;
  artTint: boolean;
  setArtTint(v: boolean): void;
  albumView: LibraryView;
  setAlbumView(v: LibraryView): void;
  artistView: LibraryView;
  setArtistView(v: LibraryView): void;
  sidebar: SidebarState;
  setSidebar(v: SidebarState): void;
}

/* ------------------------------------------------------------------
   Context
   ------------------------------------------------------------------ */

export interface ThemeContextValue {
  /** What is stored. */
  preference: ThemePreference;
  /** The theme currently applied (includes an active preview). */
  theme: LyrarrTheme;
  /** The theme the preference resolves to (ignores preview). */
  committedTheme: LyrarrTheme;
  systemDark: ThemeId;
  systemLight: ThemeId;
  /** Live prefers-color-scheme. */
  osScheme: ThemeScheme;
  setPreference(p: ThemePreference): void;
  setSystemPair(pair: { dark?: ThemeId; light?: ThemeId }): void;
  /** Temporary, never persisted; null reverts to committedTheme. */
  preview(id: ThemeId | null): void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);
const PreferencesContext = createContext<PreferencesValue | null>(null);

const THEME_KEYS = new Set([KEY_THEME, KEY_DARK, KEY_LIGHT]);
const PREF_KEYS = new Set([KEY_DENSITY, KEY_MOTION, KEY_ART_TINT, KEY_ALBUM_VIEW, KEY_ARTIST_VIEW, KEY_SIDEBAR]);

export function ThemeProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference);
  const [pair, setPair] = useState(readSystemPair);
  const [osScheme, setOsScheme] = useState<ThemeScheme>(getOsScheme);
  const [previewId, setPreviewId] = useState<ThemeId | null>(null);
  const [prefs, setPrefs] = useState<StoredPreferences>(readPreferences);

  const committedTheme = useMemo(() => resolveTheme(preference, pair, osScheme), [preference, pair, osScheme]);
  const theme = (previewId && themeById(previewId)) || committedTheme;

  // Applied before paint, so a switch or preview never shows a mixed frame.
  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useLayoutEffect(() => {
    applyPreferences(prefs);
  }, [prefs]);

  // Follow the OS scheme live (only matters while the preference is 'system').
  useEffect(() => {
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia(DARK_QUERY);
    } catch {
      return;
    }
    const onChange = () => setOsScheme(mq.matches ? 'dark' : 'light');
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Changes made in another tab apply here too.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || THEME_KEYS.has(e.key)) {
        setPreferenceState(readPreference());
        setPair(readSystemPair());
      }
      if (e.key === null || PREF_KEYS.has(e.key)) setPrefs(readPreferences());
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const setPreference = useCallback((p: ThemePreference) => {
    const next: ThemePreference = p !== 'system' && themeById(p) ? p : 'system';
    writeKey(KEY_THEME, next);
    setPreferenceState(next);
    setPreviewId(null);
  }, []);

  const setSystemPair = useCallback((next: { dark?: ThemeId; light?: ThemeId }) => {
    setPair(prev => {
      const dark = next.dark && themeById(next.dark)?.scheme === 'dark' ? next.dark : prev.dark;
      const light = next.light && themeById(next.light)?.scheme === 'light' ? next.light : prev.light;
      if (dark !== prev.dark) writeKey(KEY_DARK, dark);
      if (light !== prev.light) writeKey(KEY_LIGHT, light);
      return dark === prev.dark && light === prev.light ? prev : { dark, light };
    });
  }, []);

  const preview = useCallback((id: ThemeId | null) => {
    setPreviewId(id && themeById(id) ? id : null);
  }, []);

  const themeCtx = useMemo<ThemeContextValue>(() => ({
    preference,
    theme,
    committedTheme,
    systemDark: pair.dark,
    systemLight: pair.light,
    osScheme,
    setPreference,
    setSystemPair,
    preview,
  }), [preference, theme, committedTheme, pair, osScheme, setPreference, setSystemPair, preview]);

  const updatePref = useCallback(<K extends keyof StoredPreferences>(key: K, storageKey: string, value: StoredPreferences[K], stored: string) => {
    writeKey(storageKey, stored);
    setPrefs(prev => (prev[key] === value ? prev : { ...prev, [key]: value }));
  }, []);

  const prefsCtx = useMemo<PreferencesValue>(() => ({
    ...prefs,
    setDensity: v => updatePref('density', KEY_DENSITY, v, v),
    setMotion: v => updatePref('motion', KEY_MOTION, v, v),
    setArtTint: v => updatePref('artTint', KEY_ART_TINT, v, v ? 'on' : 'off'),
    setAlbumView: v => updatePref('albumView', KEY_ALBUM_VIEW, v, v),
    setArtistView: v => updatePref('artistView', KEY_ARTIST_VIEW, v, v),
    setSidebar: v => updatePref('sidebar', KEY_SIDEBAR, v, v),
  }), [prefs, updatePref]);

  return (
    <ThemeContext.Provider value={themeCtx}>
      <PreferencesContext.Provider value={prefsCtx}>
        <MantineProvider
          theme={mantineTheme}
          forceColorScheme={theme.scheme}
          cssVariablesResolver={cssVariablesResolver}
        >
          {children}
        </MantineProvider>
      </PreferencesContext.Provider>
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme() must be used inside <ThemeProvider>');
  return ctx;
}

export function usePreferences(): PreferencesValue {
  const ctx = useContext(PreferencesContext);
  if (!ctx) throw new Error('usePreferences() must be used inside <ThemeProvider>');
  return ctx;
}
