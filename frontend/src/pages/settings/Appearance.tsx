import { useRef, type CSSProperties, type KeyboardEvent } from 'react';
import { SegmentedControl, Select, Switch, Tooltip, UnstyledButton } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMoon, faSun } from '@fortawesome/free-solid-svg-icons';
import {
  usePreferences, useTheme, resolveTheme, type Density, type LibraryView, type MotionPref, type ThemePreference,
} from '../../theme/ThemeProvider';
import { themes, themeById, type LyrarrTheme, type ThemeId } from '../../theme/themes';
import { Panel, SettingsRow, StatusVocabulary } from '../../components/ui';
import c from './Appearance.module.css';

const DARK = themes.filter(t => t.scheme === 'dark');
const LIGHT = themes.filter(t => t.scheme === 'light');
/** System first, then dark, then light: same order as the top-bar picker. */
const ORDER: ThemePreference[] = ['system', ...DARK.map(t => t.id), ...LIGHT.map(t => t.id)];

function schemeLabel(t: LyrarrTheme): string {
  const scheme = t.scheme === 'dark' ? 'Dark' : 'Light';
  return t.contrast === 'high' ? `High contrast · ${scheme.toLowerCase()}` : scheme;
}

/** Mini UI drawn entirely with one theme's own tokens (spread as CSS variables). */
function MiniUi({ theme, className }: { theme: LyrarrTheme; className?: string }) {
  return (
    <span className={[c.mini, className].filter(Boolean).join(' ')} style={theme.tokens as CSSProperties} aria-hidden="true">
      <span className={c.miniNav}>
        <i className={c.miniNavActive} />
        <i />
        <i />
        <i className={c.miniNavShort} />
      </span>
      <span className={c.miniMain}>
        <i className={c.miniTitle} />
        <i className={c.miniText} />
        <span className={c.miniCard}>
          <span className={c.miniMissing}>Missing</span>
          <span className={c.miniButton} />
        </span>
      </span>
    </span>
  );
}

/**
 * Theme cards (DESIGN.md 8.10): a 3×3 radio group (System + 8 themes) with the
 * same semantics as the top-bar picker: arrows move focus, Space/Enter or click
 * selects; with System selected the resolved theme's card says "In use".
 */
function ThemeCards() {
  const { preference, committedTheme, systemDark, systemLight, osScheme, setPreference } = useTheme();
  const gridRef = useRef<HTMLDivElement>(null);
  const refs = useRef(new Map<ThemePreference, HTMLButtonElement>());
  const dark = themeById(systemDark)!;
  const light = themeById(systemLight)!;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = ORDER.findIndex(p => refs.current.get(p) === document.activeElement);
    if (current < 0) return;
    const cols = gridRef.current
      ? getComputedStyle(gridRef.current).gridTemplateColumns.split(' ').filter(Boolean).length || 3
      : 3;
    let next = -1;
    if (e.key === 'ArrowRight') next = Math.min(ORDER.length - 1, current + 1);
    else if (e.key === 'ArrowLeft') next = Math.max(0, current - 1);
    else if (e.key === 'ArrowDown') next = Math.min(ORDER.length - 1, current + cols);
    else if (e.key === 'ArrowUp') next = Math.max(0, current - cols);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = ORDER.length - 1;
    if (next >= 0) {
      e.preventDefault();
      refs.current.get(ORDER[next])?.focus();
    }
  };

  const card = (p: ThemePreference) => {
    const t = p === 'system' ? null : themeById(p)!;
    const checked = preference === p;
    const inUse = preference === 'system' && t?.id === committedTheme.id;
    return (
      <UnstyledButton
        key={p}
        ref={(el: HTMLButtonElement | null) => {
          if (el) refs.current.set(p, el);
          else refs.current.delete(p);
        }}
        role="radio"
        aria-checked={checked}
        aria-label={t ? `${t.name}, ${schemeLabel(t)}${inUse ? ', in use' : ''}` : `System, follows your OS (now ${osScheme})`}
        tabIndex={checked ? 0 : -1}
        className={c.card}
        data-checked={checked || undefined}
        data-in-use={inUse || undefined}
        onClick={() => setPreference(p)}
        onKeyDown={e => {
          if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            setPreference(p);
          }
        }}
      >
        {t ? (
          <span className={c.themed} style={t.tokens as CSSProperties}>
            <MiniUi theme={t} />
            <span className={c.meta}>
              <span className={c.radio} aria-hidden="true" />
              <span className={c.name}>{t.name}</span>
              <span className={c.scheme}>{inUse ? 'In use' : schemeLabel(t)}</span>
            </span>
          </span>
        ) : (
          <span className={c.system}>
            <span className={c.systemPreview}>
              <MiniUi theme={dark} />
              <MiniUi theme={light} className={c.systemLight} />
            </span>
            <span className={c.meta}>
              <span className={c.radio} aria-hidden="true" />
              <span className={c.name}>System</span>
              <span className={c.scheme}>Follows your OS (now {osScheme})</span>
            </span>
          </span>
        )}
      </UnstyledButton>
    );
  };

  return (
    <div ref={gridRef} role="radiogroup" aria-label="Theme" className={c.grid} onKeyDown={onKeyDown}>
      {ORDER.map(card)}
    </div>
  );
}

/** Settings › Appearance (DESIGN.md 8.10): client-only, applies instantly, no Save. */
export function AppearanceSection() {
  const { committedTheme, systemDark, systemLight, osScheme, setSystemPair, preference } = useTheme();
  const prefs = usePreferences();
  const hc = committedTheme.contrast === 'high';
  const resolvedSystem = resolveTheme('system', { dark: systemDark, light: systemLight }, osScheme);

  const tint = (
    <Switch
      id="set-art-tint"
      checked={prefs.artTint && !hc}
      disabled={hc}
      onChange={e => prefs.setArtTint(e.currentTarget.checked)}
      aria-describedby="set-art-tint-desc"
    />
  );

  return (
    <div id="appearance" className={c.appearance}>
      <Panel
        title="Theme"
        subtitle={preference === 'system' ? `System · using ${resolvedSystem.name}` : committedTheme.name}
        className={c.panel}
      >
        <p className={c.lead}>Applies instantly on this device. Pick a fixed theme, or follow your operating system’s light or dark setting.</p>
        <ThemeCards />
        <div className={c.rows}>
          <SettingsRow
            label="When your OS is dark, use"
            description={preference === 'system' ? 'Used while the theme is System.' : 'Used when the theme is set to System.'}
            htmlFor="set-pair-dark"
            control={(
              <Select
                id="set-pair-dark"
                data={DARK.map(t => ({ value: t.id, label: t.name }))}
                value={systemDark}
                allowDeselect={false}
                leftSection={<FontAwesomeIcon icon={faMoon} />}
                onChange={v => v && setSystemPair({ dark: v as ThemeId })}
              />
            )}
          />
          <SettingsRow
            label="When your OS is light, use"
            description={`Your OS is currently set to ${osScheme}.`}
            htmlFor="set-pair-light"
            control={(
              <Select
                id="set-pair-light"
                data={LIGHT.map(t => ({ value: t.id, label: t.name }))}
                value={systemLight}
                allowDeselect={false}
                leftSection={<FontAwesomeIcon icon={faSun} />}
                onChange={v => v && setSystemPair({ light: v as ThemeId })}
              />
            )}
          />
        </div>
      </Panel>

      <Panel title="Look & feel" className={c.panel}>
        <div className={c.rows}>
          <SettingsRow
            label="Density"
            description="Compact fits about 40% more rows in tables and uses a smaller body size."
            control={(
              <SegmentedControl
                aria-label="Density"
                value={prefs.density}
                onChange={v => prefs.setDensity(v as Density)}
                data={[{ value: 'comfortable', label: 'Comfortable' }, { value: 'compact', label: 'Compact' }]}
              />
            )}
          />
          <SettingsRow
            label="Tint album and artist pages with artwork"
            description={hc ? 'High-contrast themes never tint pages.' : 'Detail page headers pick up colour from the cover.'}
            htmlFor="set-art-tint"
            control={hc ? (
              <Tooltip label="Not available in high-contrast themes">
                <span className={c.tooltipTarget} tabIndex={0}>{tint}</span>
              </Tooltip>
            ) : tint}
          />
          <SettingsRow
            label="Albums open in"
            description="Default view for the Albums page (the toggle there changes it too)."
            control={(
              <SegmentedControl
                aria-label="Albums open in"
                value={prefs.albumView}
                onChange={v => prefs.setAlbumView(v as LibraryView)}
                data={[{ value: 'table', label: 'List' }, { value: 'grid', label: 'Grid' }]}
              />
            )}
          />
          <SettingsRow
            label="Motion"
            description="Reduce turns off animations and transitions everywhere in Lyrarr."
            control={(
              <SegmentedControl
                aria-label="Motion"
                value={prefs.motion}
                onChange={v => prefs.setMotion(v as MotionPref)}
                data={[{ value: 'system', label: 'Follow system' }, { value: 'reduce', label: 'Reduce' }]}
              />
            )}
          />
        </div>
      </Panel>

      <Panel
        title="Status vocabulary"
        subtitle={<>Shared by every table, card and filter</>}
        className={c.panel}
      >
        <p className={c.lead}>
          “Missing” is a to-do, not an error, so it stays calm. Red only ever means something failed.
        </p>
        <StatusVocabulary />
      </Panel>
    </div>
  );
}
