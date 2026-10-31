import { useRef } from 'react';
import { Checkbox } from '@mantine/core';
import { faArrowDown, faArrowUp } from '@fortawesome/free-solid-svg-icons';
import { IconButton } from '../../components/ui';
import type { ProviderOption } from './profileForm';
import c from './Profiles.module.css';

export interface ProviderOrderProps {
  /** Accessible name of the list ("Cover art providers"). */
  label: string;
  options: ProviderOption[];
  /** Enabled providers in priority order (what gets saved). */
  value: string[];
  onChange(next: string[]): void;
}

/**
 * Ordered provider editor (DESIGN.md 8.9, replaces the MultiSelect because order
 * is priority): enabled providers first in their saved order, then the disabled
 * ones. Enabling appends to the end of the order; ↑/↓ reorder enabled rows.
 */
export function ProviderOrder({ label, options, value, onChange }: ProviderOrderProps) {
  const listRef = useRef<HTMLOListElement>(null);
  const known = new Map(options.map(o => [o.value, o]));
  const enabled = value.map(v => known.get(v) ?? { value: v, label: v, description: 'Unknown provider' });
  const disabled = options.filter(o => !value.includes(o.value));
  const rows = [...enabled, ...disabled];

  const move = (from: number, to: number) => {
    if (to < 0 || to >= value.length) return;
    const next = [...value];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
    // Keep focus on the moved row's button (the original may now be disabled).
    requestAnimationFrame(() => {
      const btn = listRef.current?.querySelector<HTMLButtonElement>(
        `[data-provider="${item}"] [data-dir="${to < from ? 'up' : 'down'}"]:not(:disabled)`,
      ) ?? listRef.current?.querySelector<HTMLButtonElement>(`[data-provider="${item}"] button:not(:disabled)`);
      btn?.focus();
    });
  };

  const toggle = (id: string, on: boolean) => {
    onChange(on ? [...value, id] : value.filter(v => v !== id));
  };

  return (
    <ol ref={listRef} className={c.providers} aria-label={label}>
      {rows.map(p => {
        const index = value.indexOf(p.value);
        const on = index >= 0;
        return (
          <li key={p.value} className={c.provider} data-provider={p.value} data-off={!on || undefined}>
            <Checkbox
              checked={on}
              onChange={e => toggle(p.value, e.currentTarget.checked)}
              aria-label={`Use ${p.label}`}
            />
            <span className={c.providerRank} aria-hidden="true">{on ? index + 1 : '–'}</span>
            <span className={c.providerText}>
              <span className={c.providerName}>{p.label}</span>
              <span className={c.providerDesc}>{on ? p.description : `Off · ${p.description}`}</span>
            </span>
            <span className={c.providerMove}>
              <IconButton
                icon={faArrowUp}
                label={`Move ${p.label} up`}
                size="sm"
                data-dir="up"
                disabled={!on || index === 0}
                onClick={() => move(index, index - 1)}
              />
              <IconButton
                icon={faArrowDown}
                label={`Move ${p.label} down`}
                size="sm"
                data-dir="down"
                disabled={!on || index === value.length - 1}
                onClick={() => move(index, index + 1)}
              />
            </span>
          </li>
        );
      })}
    </ol>
  );
}
