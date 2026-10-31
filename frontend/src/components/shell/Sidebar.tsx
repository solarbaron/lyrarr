import { Fragment } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { Button, Tooltip } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAnglesLeft, faAnglesRight, faRightFromBracket, faXmark } from '@fortawesome/free-solid-svg-icons';
import { getAlbums, getArtists, getProfiles, getWantedStats } from '../../api';
import { ALBUM_TOTAL_KEY, ARTIST_TOTAL_KEY } from '../../queryRefresh';
import { compact, num } from '../../format';
import { IconButton } from '../ui';
import { BrandMark } from './BrandMark';
import { NowRunningCard } from './NowRunningCard';
import { NAV_GROUPS, NAV_ITEMS, navItemFor, type NavItem } from './nav';
import { useShell } from './ShellContext';
import c from './Sidebar.module.css';

const STALE = 60_000;

interface Counts {
  artists?: number;
  albums?: number;
  profiles?: number;
  wantedCovers?: number;
  wantedLyrics?: number;
}

/** Live nav counts (DESIGN.md 6.2). Wanted and Profiles share the pages' query keys. */
function useNavCounts(): Counts {
  const artists = useQuery({
    queryKey: ARTIST_TOTAL_KEY,
    queryFn: () => getArtists({ page: 1, pageSize: 1 }),
    staleTime: STALE,
  });
  const albums = useQuery({
    queryKey: ALBUM_TOTAL_KEY,
    queryFn: () => getAlbums({ page: 1, pageSize: 1 }),
    staleTime: STALE,
  });
  const wanted = useQuery({ queryKey: ['wanted-stats'], queryFn: getWantedStats, staleTime: STALE });
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: getProfiles, staleTime: STALE });
  return {
    artists: artists.data?.total,
    albums: albums.data?.total,
    profiles: profiles.data?.length,
    wantedCovers: wanted.data?.missing_covers,
    wantedLyrics: wanted.data?.missing_lyrics,
  };
}

export interface SidebarProps {
  /** 64px icon rail. */
  rail: boolean;
  /** Rendered as an overlay over content (768–1199px "expand"). */
  overlay?: boolean;
  /** The rail behind an open overlay: inert, so only one "Main navigation" landmark is exposed. */
  inert?: boolean;
  onClose?(): void;
  version?: string;
}

/**
 * The sidebar (DESIGN.md 6.2): brand, grouped nav with live counts, the
 * now-running card, and a footer with collapse + sign out.
 */
export function Sidebar({ rail, overlay, inert, onClose, version }: SidebarProps) {
  const counts = useNavCounts();
  const location = useLocation();
  const activePath = navItemFor(location.pathname)?.path;
  const { toggleSidebar, authType, onLogout } = useShell();
  const wantedTotal = (counts.wantedCovers ?? 0) + (counts.wantedLyrics ?? 0);
  const wantedTip = counts.wantedCovers !== undefined
    ? `${num(counts.wantedCovers)} covers · ${num(counts.wantedLyrics)} lyrics`
    : undefined;

  const countFor = (item: NavItem): { text: string; full: string } | null => {
    switch (item.count) {
      case 'artists': return counts.artists !== undefined ? { text: num(counts.artists), full: `${num(counts.artists)} artists` } : null;
      case 'albums': return counts.albums !== undefined ? { text: num(counts.albums), full: `${num(counts.albums)} albums` } : null;
      case 'profiles': return counts.profiles !== undefined ? { text: num(counts.profiles), full: `${num(counts.profiles)} profiles` } : null;
      case 'wanted': return wantedTotal > 0 ? { text: compact(wantedTotal), full: `${num(wantedTotal)} missing (${wantedTip})` } : null;
      default: return null;
    }
  };

  const link = (item: NavItem) => {
    const count = countFor(item);
    const isWanted = item.count === 'wanted';
    const node = (
      <NavLink
        key={item.path}
        to={item.path}
        end={item.path === '/'}
        // A plain string: Tooltip merges className with its child's, which breaks NavLink's function form.
        className={[c.item, activePath === item.path ? c.active : ''].filter(Boolean).join(' ')}
        aria-current={activePath === item.path ? 'page' : undefined}
        aria-label={rail ? (count ? `${item.label}, ${count.full}` : item.label) : undefined}
        onClick={overlay ? onClose : undefined}
      >
        <span className={c.icon} aria-hidden="true"><FontAwesomeIcon icon={item.icon} /></span>
        {!rail && <span className={c.label}>{item.label}</span>}
        {!rail && count && (
          isWanted ? (
            <Tooltip label={wantedTip} position="right" openDelay={300}>
              <span className={c.backlog} aria-label={count.full}>{count.text}</span>
            </Tooltip>
          ) : (
            <span className={c.count} aria-label={count.full}>{count.text}</span>
          )
        )}
        {rail && isWanted && count && <span className={c.railDot} aria-hidden="true" />}
      </NavLink>
    );
    if (!rail) return node;
    return (
      <Tooltip key={item.path} label={count ? `${item.label} · ${count.text}` : item.label} position="right" openDelay={300}>
        {node}
      </Tooltip>
    );
  };

  return (
    <nav
      className={c.sidebar}
      data-rail={rail || undefined}
      data-overlay={overlay || undefined}
      aria-label="Main navigation"
      inert={inert || undefined}
    >
      <div className={c.brand}>
        <NavLink to="/" className={c.brandLink} aria-label="Lyrarr home" onClick={overlay ? onClose : undefined}>
          <BrandMark size={28} />
          {!rail && <span className={c.wordmark}>Lyrarr</span>}
        </NavLink>
        {!rail && version && <span className={c.version}>v{version}</span>}
        {overlay && <IconButton icon={faXmark} label="Close navigation" onClick={onClose} className={c.close} />}
      </div>

      <div className={c.nav}>
        {NAV_GROUPS.map(group => (
          <Fragment key={group.id}>
            {group.label && (rail
              ? <div className={c.divider} role="separator" />
              : <div className={c.group}>{group.label}</div>)}
            {NAV_ITEMS.filter(i => i.group === group.id).map(link)}
          </Fragment>
        ))}
      </div>

      <div className={c.bottom}>
        <NowRunningCard rail={rail} />
        <div className={c.footer}>
          {authType === 'form' && !rail && (
            <Button
              variant="subtle"
              color="gray"
              size="xs"
              leftSection={<FontAwesomeIcon icon={faRightFromBracket} />}
              onClick={onLogout}
              className={c.signOut}
            >
              Sign out
            </Button>
          )}
          {authType === 'form' && rail && (
            <IconButton icon={faRightFromBracket} label="Sign out" onClick={onLogout} tooltipPosition="right" />
          )}
          {!overlay && (
            <IconButton
              icon={rail ? faAnglesRight : faAnglesLeft}
              label={rail ? 'Expand sidebar' : 'Collapse sidebar'}
              shortcut="["
              onClick={toggleSidebar}
              tooltipPosition="right"
              className={c.collapse}
            />
          )}
        </div>
      </div>
    </nav>
  );
}
