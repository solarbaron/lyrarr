import { useRef, useState, type KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { Popover, Select, Tooltip, UnstyledButton } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowRight, faMoon, faSun } from '@fortawesome/free-solid-svg-icons';
import { useTheme, resolveTheme, type ThemePreference } from '../../theme/ThemeProvider';
import { themes, themeById, type LyrarrTheme, type ThemeId } from '../../theme/themes';
import { Tag } from '../ui';
import c from './ThemePicker.module.css';

/** Three-part swatch: bg / surface / accent (DESIGN.md 6.9). */
export function ThemeSwatch({ theme, className }: { theme: LyrarrTheme; className?: string }) {
  const [bg, surface, accent] = theme.swatch;
  return (
    <span className={[c.swatch, className].filter(Boolean).join(' ')} aria-hidden="true">
      <span style={{ background: bg }} />
      <span style={{ background: surface }} />
      <span style={{ background: accent }} />
    </span>
  );
}

/** Diagonal swatch for System: chosen dark | chosen light. */
function SystemSwatch({ dark, light }: { dark: LyrarrTheme; light: LyrarrTheme }) {
  return (
    <span className={c.systemSwatch} aria-hidden="true">
      <span className={c.systemDark} style={{ background: dark.swatch[0] }}>
        <span style={{ background: dark.swatch[2] }} />
      </span>
      <span className={c.systemLight} style={{ background: light.swatch[0] }}>
        <span style={{ background: light.swatch[2] }} />
      </span>
    </span>
  );
}

const DARK = themes.filter(t => t.scheme === 'dark');
const LIGHT = themes.filter(t => t.scheme === 'light');
const ORDER: ThemePreference[] = ['system', ...DARK.map(t => t.id), ...LIGHT.map(t => t.id)];

/**
 * The theme picker popover (DESIGN.md 6.9): one radio group (System + 8 themes)
 * with live preview on hover/focus, plus the System dark/light pairing.
 */
export function ThemePicker() {
  const { preference, committedTheme, theme, systemDark, systemLight, osScheme, setPreference, setSystemPair, preview } = useTheme();
  const [opened, setOpened] = useState(false);
  const optionRefs = useRef(new Map<ThemePreference, HTMLButtonElement>());
  const darkTheme = themeById(systemDark)!;
  const lightTheme = themeById(systemLight)!;

  const close = () => {
    preview(null);
    setOpened(false);
  };

  const previewOption = (p: ThemePreference) => {
    if (p === 'system') {
      const resolved = resolveTheme('system', { dark: systemDark, light: systemLight }, osScheme);
      preview(resolved.id);
    } else {
      preview(p);
    }
  };

  const commit = (p: ThemePreference) => {
    setPreference(p);
    preview(null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = ORDER.findIndex(p => optionRefs.current.get(p) === document.activeElement);
    if (current < 0) return;
    let next = -1;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') next = (current + 1) % ORDER.length;
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') next = (current - 1 + ORDER.length) % ORDER.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = ORDER.length - 1;
    if (next >= 0) {
      e.preventDefault();
      optionRefs.current.get(ORDER[next])?.focus();
    }
  };

  // Roving tabindex: the selected option is the tab stop.
  const tabStop = preference;

  const option = (p: ThemePreference) => {
    const t = p === 'system' ? null : themeById(p)!;
    const checked = preference === p;
    const inUse = preference === 'system' && t?.id === committedTheme.id;
    return (
      <UnstyledButton
        key={p}
        ref={(el: HTMLButtonElement | null) => {
          if (el) optionRefs.current.set(p, el);
          else optionRefs.current.delete(p);
        }}
        role="radio"
        aria-checked={checked}
        tabIndex={p === tabStop ? 0 : -1}
        className={c.option}
        data-checked={checked || undefined}
        onClick={() => commit(p)}
        onMouseEnter={() => previewOption(p)}
        onFocus={() => previewOption(p)}
        onKeyDown={e => {
          if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            commit(p);
          }
        }}
      >
        {t ? <ThemeSwatch theme={t} /> : <SystemSwatch dark={darkTheme} light={lightTheme} />}
        <span className={c.optionName}>
          {t ? t.name : 'System'}
          {t?.contrast === 'high' && <Tag code title="High contrast">HC</Tag>}
        </span>
        {!t && <span className={c.optionMeta}>follows your OS (now: {osScheme})</span>}
        {inUse && <span className={c.optionMeta}>In use</span>}
        <span className={c.radio} aria-hidden="true" />
      </UnstyledButton>
    );
  };

  const pairData = (list: LyrarrTheme[]) => list.map(t => ({ value: t.id, label: t.name }));

  return (
    <Popover
      opened={opened}
      onChange={o => (o ? setOpened(true) : close())}
      onClose={() => preview(null)}
      position="bottom-end"
      width={360}
      trapFocus
      returnFocus
      shadow="md"
    >
      <Popover.Target>
        <Tooltip label={`Theme: ${committedTheme.name}`} disabled={opened}>
          <UnstyledButton
            className={c.trigger}
            onClick={() => (opened ? close() : setOpened(true))}
            aria-label={`Theme: ${committedTheme.name}`}
            aria-expanded={opened}
            aria-haspopup="dialog"
          >
            <span className={c.triggerHalfBg} />
            <span className={c.triggerHalfAccent} />
          </UnstyledButton>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown p={0} aria-label="Appearance" onKeyDownCapture={e => { if (e.key === 'Escape') preview(null); }}>
        <div className={c.head}>
          <span className={c.title}>Appearance</span>
          <span className={c.current}>{theme.id !== committedTheme.id ? `Previewing ${theme.name}` : committedTheme.name}</span>
        </div>
        <div
          role="radiogroup"
          aria-label="Theme"
          className={c.list}
          onKeyDown={onKeyDown}
          onMouseLeave={() => preview(null)}
        >
          {option('system')}
          <div className={c.group} aria-hidden="true">Dark</div>
          {DARK.map(t => option(t.id))}
          <div className={c.group} aria-hidden="true">Light</div>
          {LIGHT.map(t => option(t.id))}
        </div>
        <div className={c.footer} onFocus={() => preview(null)}>
          <div className={c.pairRow}>
            <span className={c.pairLabel}>System uses</span>
            <Tooltip label="Used when your OS is in dark mode">
              <Select
                aria-label="Theme used when your OS is in dark mode"
                size="xs"
                data={pairData(DARK)}
                value={systemDark}
                allowDeselect={false}
                onChange={v => v && setSystemPair({ dark: v as ThemeId })}
                leftSection={<FontAwesomeIcon icon={faMoon} />}
                comboboxProps={{ withinPortal: false }}
                className={c.pairSelect}
              />
            </Tooltip>
            <Tooltip label="Used when your OS is in light mode">
              <Select
                aria-label="Theme used when your OS is in light mode"
                size="xs"
                data={pairData(LIGHT)}
                value={systemLight}
                allowDeselect={false}
                onChange={v => v && setSystemPair({ light: v as ThemeId })}
                leftSection={<FontAwesomeIcon icon={faSun} />}
                comboboxProps={{ withinPortal: false }}
                className={c.pairSelect}
              />
            </Tooltip>
          </div>
          <Link to="/settings#appearance" className={`ly-link ${c.more}`} onClick={close}>
            More appearance settings <FontAwesomeIcon icon={faArrowRight} />
          </Link>
        </div>
      </Popover.Dropdown>
    </Popover>
  );
}
