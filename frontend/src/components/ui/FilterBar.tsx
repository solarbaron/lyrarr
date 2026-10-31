import { useEffect, useRef, useState, type ReactNode } from 'react';
import { CloseButton, Kbd, TextInput } from '@mantine/core';
import { useHotkeys } from '@mantine/hooks';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faMagnifyingGlass } from '@fortawesome/free-solid-svg-icons';
import c from './Controls.module.css';

export interface FilterBarProps {
  search?: {
    value: string;
    onChange(v: string): void;
    placeholder: string;
    /** Debounce for onChange while typing (default 250ms). */
    debounce?: number;
    'aria-label'?: string;
  };
  /** FilterPills. */
  filters?: ReactNode;
  /** Sort, view toggle, density. */
  right?: ReactNode;
  /** Bind `f` to focus the search input (default true; DESIGN.md 6.4 list scope). */
  hotkey?: boolean;
  className?: string;
}

/** True while any modal/drawer is open: page shortcuts must not fire behind it. */
function dialogOpen(): boolean {
  return !!document.querySelector('[role="dialog"][aria-modal="true"], .mantine-Modal-content, .mantine-Drawer-content');
}

/**
 * Search + filter pills + right slot (DESIGN.md 7.5). The input is debounced and
 * keeps its own text so typing never lags behind a URL update.
 */
export function FilterBar({ search, filters, right, hotkey = true, className }: FilterBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(search?.value ?? '');
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef(search?.onChange);
  latest.current = search?.onChange;

  // External changes (Back, "Clear filters") win over local text.
  useEffect(() => {
    setText(search?.value ?? '');
  }, [search?.value]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  useHotkeys(hotkey && search ? [['f', e => {
    if (dialogOpen()) return;
    e.preventDefault();
    inputRef.current?.focus();
    inputRef.current?.select();
  }]] : []);

  const commit = (v: string, now = false) => {
    setText(v);
    window.clearTimeout(timer.current);
    const delay = search?.debounce ?? 250;
    if (now || delay <= 0) {
      latest.current?.(v);
      return;
    }
    timer.current = window.setTimeout(() => latest.current?.(v), delay);
  };

  return (
    <div className={[c.filterBar, className].filter(Boolean).join(' ')}>
      {search && (
        <TextInput
          ref={inputRef}
          className={c.filterSearch}
          type="search"
          value={text}
          placeholder={search.placeholder}
          aria-label={search['aria-label'] ?? search.placeholder}
          data-ly-filter=""
          leftSection={<FontAwesomeIcon icon={faMagnifyingGlass} className={c.filterSearchIcon} />}
          rightSection={text
            ? <CloseButton size="sm" aria-label="Clear search" onClick={() => { commit('', true); inputRef.current?.focus(); }} />
            : (hotkey ? <Kbd className={c.filterKbd}>f</Kbd> : null)}
          rightSectionPointerEvents={text ? 'all' : 'none'}
          onChange={e => commit(e.currentTarget.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') commit(text, true);
            if (e.key === 'Escape') {
              if (text) commit('', true);
              else inputRef.current?.blur();
            }
          }}
        />
      )}
      {filters && <div className={c.filterPills}>{filters}</div>}
      <div className={c.filterSpacer} />
      {right && <div className={c.filterRight}>{right}</div>}
    </div>
  );
}
