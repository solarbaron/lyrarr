import { Fragment, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Avatar, Kbd, Menu, UnstyledButton } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faChevronLeft, faChevronRight, faMagnifyingGlass, faRightFromBracket, faWaveSquare,
} from '@fortawesome/free-solid-svg-icons';
import { useBreadcrumbTrail } from '../../hooks/useBreadcrumbs';
import { useJobs, jobFraction } from '../../jobs';
import { MOD_KEY } from '../../shortcuts';
import { initials } from '../../format';
import { IconButton } from '../ui';
import { BrandMark } from './BrandMark';
import { HealthPill } from './HealthPill';
import { ThemePicker } from './ThemePicker';
import { navItemFor } from './nav';
import { useShell } from './ShellContext';
import c from './TopBar.module.css';

/** 2px progress line along the bottom edge of the top bar while a job runs (DESIGN.md 6.3). */
function JobLine() {
  const { running } = useJobs();
  const job = running[0];
  if (!job) return null;
  const fraction = jobFraction(job);
  return (
    <div
      className={c.jobLine}
      role="progressbar"
      aria-label="Background job progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}
      aria-valuetext={fraction === null ? `${job.label}…` : `${job.label}: ${Math.round(fraction * 100)}%`}
    >
      {fraction === null
        ? <span className={c.jobLineIndeterminate} />
        : <span className={c.jobLineFill} style={{ width: `${fraction * 100}%` }} />}
    </div>
  );
}

function ActivityButton() {
  const { openActivity } = useShell();
  const { unseen, unseenError } = useJobs();
  const label = unseen > 0 ? `Activity (${unseen} new${unseenError ? ', including errors' : ''})` : 'Activity';
  return (
    <span className={c.pipWrap}>
      <IconButton icon={faWaveSquare} label={label} onClick={openActivity} tooltipPosition="bottom" />
      {unseen > 0 && <span className={c.pip} data-tone={unseenError ? 'danger' : 'accent'} aria-hidden="true" />}
    </span>
  );
}

function UserMenu() {
  const { authType, username, onLogout } = useShell();
  if (authType !== 'form') return null;
  const name = username || 'Signed in';
  return (
    <Menu position="bottom-end" width={200}>
      <Menu.Target>
        <UnstyledButton className={c.avatarButton} aria-label={`Account: ${name}`}>
          <Avatar size={28} radius="xl" className={c.avatar}>{initials(username || 'User')}</Avatar>
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>{name}</Menu.Label>
        <Menu.Item leftSection={<FontAwesomeIcon icon={faRightFromBracket} />} onClick={onLogout}>
          Sign out
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

export interface TopBarProps {
  /** < 768px: brand + page title layout (DESIGN.md 6.5). */
  mobile: boolean;
  /** ≤ 1024px: the search trigger collapses to an icon (the rail's expand button opens the overlay sidebar). */
  narrow: boolean;
}

export function TopBar({ mobile, narrow }: TopBarProps) {
  const { openPalette } = useShell();
  const crumbs = useBreadcrumbTrail();
  const location = useLocation();
  const page = navItemFor(location.pathname);

  const search = narrow || mobile ? (
    <IconButton icon={faMagnifyingGlass} label="Search" shortcut={`${MOD_KEY} K`} onClick={openPalette} tooltipPosition="bottom" />
  ) : (
    <UnstyledButton className={c.searchTrigger} onClick={openPalette} aria-label="Search artists, albums, tracks (command palette)" aria-haspopup="dialog">
      <FontAwesomeIcon icon={faMagnifyingGlass} className={c.searchIcon} />
      <span className={c.searchText}>Search artists, albums, tracks…</span>
      <Kbd>{MOD_KEY} K</Kbd>
    </UnstyledButton>
  );

  let left: ReactNode = null;
  if (mobile) {
    const parent = crumbs.length > 1 ? [...crumbs].slice(0, -1).reverse().find(cr => cr.to) : undefined;
    left = (
      <div className={c.mobileTitle}>
        {parent?.to ? (
          <Link to={parent.to} className={c.back} aria-label={`Back to ${parent.label}`}>
            <FontAwesomeIcon icon={faChevronLeft} />
            <span className={c.backLabel}>{parent.label}</span>
          </Link>
        ) : (
          <>
            <Link to="/" aria-label="Lyrarr home" className={c.mobileBrand}><BrandMark size={26} /></Link>
            <span className={c.pageTitle}>{page?.label ?? 'Lyrarr'}</span>
          </>
        )}
      </div>
    );
  } else if (crumbs.length) {
    left = (
      <nav aria-label="Breadcrumb" className={c.crumbs}>
        <ol>
          {crumbs.map((cr, i) => {
            const last = i === crumbs.length - 1;
            return (
              <Fragment key={`${i}-${cr.label}`}>
                {i > 0 && <li aria-hidden="true" className={c.sep}><FontAwesomeIcon icon={faChevronRight} /></li>}
                <li className={last ? c.crumbCurrent : c.crumb} title={cr.label}>
                  {last || !cr.to
                    ? <span aria-current={last ? 'page' : undefined}>{cr.label}</span>
                    : <Link to={cr.to}>{cr.label}</Link>}
                </li>
              </Fragment>
            );
          })}
        </ol>
      </nav>
    );
  }

  return (
    <header className={c.bar} data-mobile={mobile || undefined}>
      <div className={c.left}>{left}</div>
      <div className={c.center}>{!(narrow || mobile) && search}</div>
      <div className={c.right}>
        {(narrow || mobile) && search}
        <HealthPill />
        <ActivityButton />
        <ThemePicker />
        {!mobile && <UserMenu />}
      </div>
      <JobLine />
    </header>
  );
}
