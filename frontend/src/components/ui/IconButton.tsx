import { forwardRef, type MouseEventHandler, type Ref } from 'react';
import { Link } from 'react-router-dom';
import { ActionIcon, Kbd, Tooltip, type ActionIconProps } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import type { IconDefinition } from '@fortawesome/free-solid-svg-icons';
import c from './IconButton.module.css';

export interface IconButtonProps extends Omit<ActionIconProps, 'children'> {
  icon: IconDefinition;
  /** Accessible name and tooltip text (DESIGN.md 4.6: icon-only buttons need both). */
  label: string;
  /** Shown as a Kbd hint in the tooltip, e.g. "[" or "Ctrl K". */
  shortcut?: string;
  /** Renders a router <Link> to this path instead of a <button>. */
  to?: string;
  onClick?: MouseEventHandler<HTMLElement>;
  type?: 'button' | 'submit' | 'reset';
  /** Tooltip placement (default top). */
  tooltipPosition?: 'top' | 'bottom' | 'left' | 'right';
  /** Mark as pressed (toggle buttons). */
  'aria-pressed'?: boolean;
  'aria-expanded'?: boolean;
  'aria-controls'?: string;
  tabIndex?: number;
  id?: string;
}

/**
 * The only way to render an icon-only button (DESIGN.md 7.5): ActionIcon
 * (ghost by default) + aria-label + a tooltip with the same text. Forwards its
 * ref, so it works as a Menu.Target / Popover.Target. With `to` it renders a
 * router link (e.g. "Open artist" in a tree row).
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, shortcut, to, type = 'button', tooltipPosition = 'top', className, ...rest },
  ref,
) {
  const tip = shortcut ? (
    <span className={c.tip}>
      {label}
      <Kbd>{shortcut}</Kbd>
    </span>
  ) : label;
  const cls = [c.root, className].filter(Boolean).join(' ');
  const glyph = <FontAwesomeIcon icon={icon} />;
  return (
    <Tooltip label={tip} position={tooltipPosition} openDelay={400}>
      {to !== undefined ? (
        <ActionIcon ref={ref as unknown as Ref<HTMLAnchorElement>} component={Link} to={to} aria-label={label} className={cls} {...rest}>
          {glyph}
        </ActionIcon>
      ) : (
        <ActionIcon ref={ref} type={type} aria-label={label} className={cls} {...rest}>
          {glyph}
        </ActionIcon>
      )}
    </Tooltip>
  );
});
