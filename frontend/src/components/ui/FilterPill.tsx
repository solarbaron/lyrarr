import { Menu } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCheck, faChevronDown, faXmark } from '@fortawesome/free-solid-svg-icons';
import { num } from '../../format';
import c from './Controls.module.css';

export interface FilterPillOption {
  value: string;
  label: string;
  count?: number;
}

export interface FilterPillProps {
  label: string;
  value: string | null;
  options: FilterPillOption[];
  onChange(v: string | null): void;
  /** Label of the "no filter" option (default "Any"). */
  anyLabel?: string;
}

/**
 * Filter dropdown pill (DESIGN.md 7.5). Unset: dashed control outline, "Profile Any ▾".
 * Set: accent-soft, "Profile: Default ×" where × clears.
 */
export function FilterPill({ label, value, options, onChange, anyLabel = 'Any' }: FilterPillProps) {
  const current = value === null ? undefined : options.find(o => o.value === value);
  const set = !!current;
  return (
    <span className={c.pill} data-set={set || undefined}>
      <Menu position="bottom-start" withinPortal>
        <Menu.Target>
          <button type="button" className={c.pillButton} aria-label={`${label}: ${current?.label ?? anyLabel}. Change filter`}>
            <span className={c.pillLabel}>{label}{set ? ':' : ''}</span>
            <span className={c.pillValue}>{current?.label ?? anyLabel}</span>
            {!set && <FontAwesomeIcon icon={faChevronDown} className={c.pillChevron} />}
          </button>
        </Menu.Target>
        <Menu.Dropdown className={c.pillMenu}>
          <Menu.Label>{label}</Menu.Label>
          <Menu.Item
            onClick={() => onChange(null)}
            rightSection={!set ? <FontAwesomeIcon icon={faCheck} className={c.pillCheck} /> : null}
          >
            {anyLabel}
          </Menu.Item>
          {options.map(o => (
            <Menu.Item
              key={o.value}
              onClick={() => onChange(o.value)}
              aria-checked={o.value === value}
              rightSection={
                <span className={c.pillOptionRight}>
                  {o.count !== undefined && <span className={c.pillCount}>{num(o.count)}</span>}
                  {o.value === value && <FontAwesomeIcon icon={faCheck} className={c.pillCheck} />}
                </span>
              }
            >
              {o.label}
            </Menu.Item>
          ))}
        </Menu.Dropdown>
      </Menu>
      {set && (
        <button type="button" className={c.pillClear} aria-label={`Clear ${label} filter`} onClick={() => onChange(null)}>
          <FontAwesomeIcon icon={faXmark} />
        </button>
      )}
    </span>
  );
}
