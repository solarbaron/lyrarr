import { Button, Menu, SegmentedControl } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faCheck, faChevronDown, faCircleExclamation, faGrip, faIdBadge, faList, faPlug, faRotate, faRotateRight,
} from '@fortawesome/free-solid-svg-icons';
import { getSystemStatus, triggerSync } from '../../api';
import { notifyError } from '../../notify';
import { EmptyState } from '../ui';
import type { LibraryView } from '../../theme/ThemeProvider';
import type { Profile } from '../../types';
import c from './library.module.css';

/* ------------------------------------------------------------------
   List | Grid toggle (lyrarr-album-view / lyrarr-artist-view)
   ------------------------------------------------------------------ */

export function ViewToggle({ value, onChange, noun }: { value: LibraryView; onChange(v: LibraryView): void; noun: string }) {
  return (
    <SegmentedControl
      className={c.viewToggle}
      aria-label={`Show ${noun} as`}
      value={value}
      onChange={v => onChange(v as LibraryView)}
      data={[
        { value: 'table', label: <span className={c.viewToggleLabel}><FontAwesomeIcon icon={faList} />List</span> },
        { value: 'grid', label: <span className={c.viewToggleLabel}><FontAwesomeIcon icon={faGrip} />Grid</span> },
      ]}
    />
  );
}

/* ------------------------------------------------------------------
   Profiles: "Set profile ▸" submenu (row ⋯) and "Set profile ▾" (bulk bar)
   ------------------------------------------------------------------ */

function ProfileItems({ profiles, currentId, onPick }: { profiles: Profile[]; currentId?: number | null; onPick(p: Profile): void }) {
  if (profiles.length === 0) return <Menu.Item disabled>No profiles</Menu.Item>;
  return (
    <>
      {profiles.map(p => (
        <Menu.Item
          key={p.id}
          onClick={() => onPick(p)}
          rightSection={p.id === currentId ? <FontAwesomeIcon icon={faCheck} className={c.menuCheck} /> : null}
        >
          {p.name}
        </Menu.Item>
      ))}
    </>
  );
}

/** "Set profile ▸" inside a row ⋯ menu. */
export function SetProfileSubmenu({ profiles, currentId, onPick }: { profiles: Profile[]; currentId?: number | null; onPick(p: Profile): void }) {
  return (
    <Menu.Sub position="left-start">
      <Menu.Sub.Target>
        <Menu.Sub.Item leftSection={<FontAwesomeIcon icon={faIdBadge} />}>Set profile</Menu.Sub.Item>
      </Menu.Sub.Target>
      <Menu.Sub.Dropdown>
        <Menu.Label>Profile</Menu.Label>
        <ProfileItems profiles={profiles} currentId={currentId} onPick={onPick} />
      </Menu.Sub.Dropdown>
    </Menu.Sub>
  );
}

/** "Set profile ▾" secondary button for bulk bars. */
export function SetProfileMenuButton({ profiles, onPick, loading }: { profiles: Profile[]; onPick(p: Profile): void; loading?: boolean }) {
  return (
    <Menu position="top" withinPortal>
      <Menu.Target>
        <Button
          variant="default"
          size="sm"
          loading={loading}
          leftSection={<FontAwesomeIcon icon={faIdBadge} />}
          rightSection={<FontAwesomeIcon icon={faChevronDown} className={c.chevron} />}
        >
          Set profile
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Set profile</Menu.Label>
        <ProfileItems profiles={profiles} onPick={onPick} />
      </Menu.Dropdown>
    </Menu>
  );
}

/* ------------------------------------------------------------------
   Empty library: Sync with Lidarr when connected, otherwise Connect Lidarr
   ------------------------------------------------------------------ */

export function LibraryEmptyAction() {
  const { data: status } = useQuery({ queryKey: ['system-status'], queryFn: getSystemStatus, staleTime: 60_000 });
  if (status?.lidarr_enabled) {
    return (
      <Button
        variant="default"
        leftSection={<FontAwesomeIcon icon={faRotate} />}
        onClick={() => {
          triggerSync()
            .then((r: { message?: string }) => notifications.show({ title: 'Sync started', message: r?.message || 'Syncing artists and albums from Lidarr...' }))
            .catch(err => notifyError('Sync failed', err));
        }}
      >
        Sync with Lidarr
      </Button>
    );
  }
  return (
    <Button component={Link} to="/settings#lidarr" variant="filled" leftSection={<FontAwesomeIcon icon={faPlug} />}>
      Connect Lidarr
    </Button>
  );
}

/* ------------------------------------------------------------------
   Load error for grid views (DataTable renders its own for list views)
   ------------------------------------------------------------------ */

export interface LoadError {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

/** "Couldn't load albums" + Retry, in the same card as the grid's empty state. */
export function GridLoadError({ error, noun }: { error: LoadError; noun: string }) {
  return (
    <div className={c.gridEmpty}>
      <EmptyState
        size="sm"
        tone="danger"
        icon={faCircleExclamation}
        title={error.title ?? `Couldn't load ${noun}`}
        message={error.message}
        action={error.onRetry && (
          <Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} onClick={error.onRetry}>Retry</Button>
        )}
      />
    </div>
  );
}

/* ------------------------------------------------------------------
   Helpers
   ------------------------------------------------------------------ */

/** The API reports "None" for albums/artists without a profile. */
export function profileLabel(name: string | null | undefined): string | null {
  return name && name !== 'None' ? name : null;
}
