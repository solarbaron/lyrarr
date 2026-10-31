import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { Drawer, UnstyledButton } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faEllipsis, faRightFromBracket, faWaveSquare } from '@fortawesome/free-solid-svg-icons';
import { getWantedStats } from '../../api';
import { useJobs, jobCount, jobFraction } from '../../jobs';
import { num } from '../../format';
import { useBottomReserve, BOTTOM_LAYER } from '../../hooks/useBottomReserve';
import { NAV_ITEMS, navItemFor } from './nav';
import { jobIcon } from './NowRunningCard';
import { useShell } from './ShellContext';
import c from './MobileTabBar.module.css';

const TAB_BAR_H = 60;
const JOB_BAR_H = 36;
const TABS = ['/', '/albums', '/wanted'];
const SHEET_HEADER_H = 56;
const SHEET_ROW_H = 48;

/** Job bar directly above the tab bar while a job runs (DESIGN.md 6.5). */
function JobBar({ preview }: { preview: boolean }) {
  const { running } = useJobs();
  const { openActivity } = useShell();
  const job = running[0];
  // A preview uses its own id: releasing 'job-bar' would drop the real bar's reservation.
  useBottomReserve(preview ? 'job-bar-preview' : 'job-bar', JOB_BAR_H, !!job && !preview, BOTTOM_LAYER.jobBar);
  if (!job) return null;
  const fraction = jobFraction(job);
  const count = jobCount(job);
  return (
    <UnstyledButton className={c.jobBar} onClick={openActivity} aria-label={`${job.label}${count ? `, ${count}` : ''}. Open Activity`}>
      <FontAwesomeIcon icon={jobIcon(job)} className={c.jobIcon} />
      <span className={c.jobLabel} title={job.label}>{job.label}</span>
      {count && <span className={`${c.jobCount} num`}>{count}</span>}
      <span className={c.jobTrack} aria-hidden="true">
        {fraction === null
          ? <span className={c.jobIndeterminate} />
          : <span className={c.jobFill} style={{ width: `${fraction * 100}%` }} />}
      </span>
    </UnstyledButton>
  );
}

export interface MobileTabBarProps {
  /**
   * Gallery preview: reserves no bottom space. Render it inside a transformed
   * frame so its fixed positioning is relative to the frame.
   */
  preview?: boolean;
}

/** Bottom tab bar below 768px: Home, Albums, Wanted, Activity, More (DESIGN.md 6.5). */
export function MobileTabBar({ preview = false }: MobileTabBarProps) {
  const { openActivity, authType, onLogout } = useShell();
  const { unseen, unseenError } = useJobs();
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const { data: wanted } = useQuery({ queryKey: ['wanted-stats'], queryFn: getWantedStats, staleTime: 60_000 });
  const backlog = (wanted?.missing_covers ?? 0) + (wanted?.missing_lyrics ?? 0);
  useBottomReserve(preview ? 'tab-bar-preview' : 'tab-bar', TAB_BAR_H, !preview, BOTTOM_LAYER.tabBar);

  const current = navItemFor(location.pathname);
  const moreItems = NAV_ITEMS.filter(i => !TABS.includes(i.path));
  const moreActive = !!current && !TABS.includes(current.path);

  // Header + rows + body padding, plus the home-indicator inset the body pads for.
  const sheetRows = moreItems.length + (authType === 'form' ? 1 : 0);
  const sheetSize = `calc(${SHEET_HEADER_H + sheetRows * SHEET_ROW_H + 24}px + env(safe-area-inset-bottom, 0px))`;

  return (
    <>
      <JobBar preview={preview} />
      <nav className={c.bar} aria-label={preview ? 'Main navigation (preview)' : 'Main navigation'}>
        {NAV_ITEMS.filter(i => TABS.includes(i.path)).map(item => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            className={({ isActive }) => [c.tab, isActive ? c.active : ''].filter(Boolean).join(' ')}
          >
            <span className={c.iconWrap}>
              <FontAwesomeIcon icon={item.icon} />
              {item.count === 'wanted' && backlog > 0 && (
                <span className={c.dot} aria-hidden="true" />
              )}
            </span>
            <span className={c.label}>{item.label}</span>
            {item.count === 'wanted' && backlog > 0 && <span className="visually-hidden">, {num(backlog)} missing</span>}
          </NavLink>
        ))}
        <UnstyledButton className={c.tab} onClick={openActivity} aria-label={unseen ? `Activity, ${unseen} new` : 'Activity'}>
          <span className={c.iconWrap}>
            <FontAwesomeIcon icon={faWaveSquare} />
            {unseen > 0 && <span className={c.pip} data-tone={unseenError ? 'danger' : 'accent'} aria-hidden="true" />}
          </span>
          <span className={c.label}>Activity</span>
        </UnstyledButton>
        <UnstyledButton
          className={[c.tab, moreActive ? c.active : ''].filter(Boolean).join(' ')}
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
        >
          <span className={c.iconWrap}><FontAwesomeIcon icon={faEllipsis} /></span>
          <span className={c.label}>More</span>
        </UnstyledButton>
      </nav>

      <Drawer opened={moreOpen} onClose={() => setMoreOpen(false)} position="bottom" size={sheetSize} title="More" classNames={{ body: c.sheetBody, content: c.sheetContent }}>
        <ul className={c.sheet}>
          {moreItems.map(item => (
            <li key={item.path}>
              <NavLink
                to={item.path}
                className={({ isActive }) => [c.sheetItem, isActive ? c.sheetActive : ''].filter(Boolean).join(' ')}
                onClick={() => setMoreOpen(false)}
              >
                <FontAwesomeIcon icon={item.icon} className={c.sheetIcon} />
                {item.label}
              </NavLink>
            </li>
          ))}
          {authType === 'form' && (
            <li>
              <UnstyledButton className={c.sheetItem} onClick={() => { setMoreOpen(false); onLogout(); }}>
                <FontAwesomeIcon icon={faRightFromBracket} className={c.sheetIcon} />
                Sign out
              </UnstyledButton>
            </li>
          )}
        </ul>
      </Drawer>
    </>
  );
}
