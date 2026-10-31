import { Button, Menu } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowDownWideShort, faArrowUpWideShort, faCheck, faChevronDown } from '@fortawesome/free-solid-svg-icons';
import c from './Controls.module.css';

export interface SortOption {
  value: string;
  label: string;
  /** Direction wording, e.g. { asc: 'A–Z', desc: 'Z–A' } or { asc: 'Oldest first', desc: 'Newest first' }. */
  dirLabels?: { asc: string; desc: string };
}

export interface SortMenuProps {
  options: SortOption[];
  value: string;
  dir: 'asc' | 'desc';
  onChange(value: string, dir: 'asc' | 'desc'): void;
}

/** Secondary sort button ("Title A–Z") with a menu of fields and directions (DESIGN.md 7.5). */
export function SortMenu({ options, value, dir, onChange }: SortMenuProps) {
  const current = options.find(o => o.value === value) ?? options[0];
  const dirText = (o: SortOption | undefined, d: 'asc' | 'desc') =>
    o?.dirLabels ? o.dirLabels[d] : d === 'asc' ? 'Ascending' : 'Descending';
  const short = current?.dirLabels ? `${current.label} ${current.dirLabels[dir]}` : current?.label ?? 'Sort';
  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <Button
          variant="default"
          leftSection={<FontAwesomeIcon icon={dir === 'asc' ? faArrowUpWideShort : faArrowDownWideShort} />}
          rightSection={<FontAwesomeIcon icon={faChevronDown} className={c.pillChevron} />}
          aria-label={`Sort: ${current?.label ?? ''}, ${dirText(current, dir)}`}
        >
          {short}
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Sort by</Menu.Label>
        {options.map(o => (
          <Menu.Item
            key={o.value}
            onClick={() => onChange(o.value, o.value === value ? dir : 'asc')}
            rightSection={o.value === value ? <FontAwesomeIcon icon={faCheck} className={c.pillCheck} /> : null}
          >
            {o.label}
          </Menu.Item>
        ))}
        <Menu.Divider />
        <Menu.Label>Order</Menu.Label>
        {(['asc', 'desc'] as const).map(d => (
          <Menu.Item
            key={d}
            leftSection={<FontAwesomeIcon icon={d === 'asc' ? faArrowUpWideShort : faArrowDownWideShort} />}
            onClick={() => onChange(current?.value ?? value, d)}
            rightSection={d === dir ? <FontAwesomeIcon icon={faCheck} className={c.pillCheck} /> : null}
          >
            {dirText(current, d)}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}
