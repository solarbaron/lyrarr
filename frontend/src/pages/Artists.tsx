import { useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQueries, useQuery, keepPreviousData } from '@tanstack/react-query';
import { Button, Menu } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowRight, faDownload, faEllipsis, faFilterCircleXmark, faUser } from '@fortawesome/free-solid-svg-icons';
import { getArtists, getProfiles } from '../api';
import {
  BulkBar, CoverTile, DataTable, EmptyState, FilterBar, FilterPill, GridSkeleton, IconButton, PageHeader, Pager,
  SortMenu, StatusDot, TitleCell, ViewTabs, type Column, type SortOption,
} from '../components/ui';
import { GridLoadError, LibraryEmptyAction, SetProfileMenuButton, SetProfileSubmenu, ViewToggle, profileLabel } from '../components/library/LibraryControls';
import { fetchAllIds, useAssignProfileMutation, useDownloadMutation } from '../components/library/actions';
import { useUrlPatch, useUrlState } from '../hooks/useUrlState';
import { usePreferences } from '../theme/ThemeProvider';
import type { Artist } from '../types';
import lc from '../components/library/library.module.css';

type ArtistRow = Artist & { profileName?: string };

/** Saved views (DESIGN.md 8.2). */
const VIEWS = [
  { value: 'all', label: 'All', monitored: undefined },
  { value: 'monitored', label: 'Monitored', monitored: 'true' },
  { value: 'unmonitored', label: 'Unmonitored', monitored: 'false' },
] as const;

type ViewId = (typeof VIEWS)[number]['value'];

const SORT_OPTIONS: SortOption[] = [
  { value: 'name', label: 'Name', dirLabels: { asc: 'A–Z', desc: 'Z–A' } },
];

const PAGE_SIZES = [25, 50, 100];

const getArtistId = (a: Artist) => a.lidarrArtistId;

/** "music/Westside Gunn": the last two path segments. */
function shortPath(path: string | null | undefined): string | null {
  return path ? path.split('/').filter(Boolean).slice(-2).join('/') : null;
}

export default function ArtistsPage() {
  const { artistView, setArtistView } = usePreferences();
  const patchUrl = useUrlPatch();
  const [viewParam] = useUrlState<string>('view', 'all');
  const [search] = useUrlState<string>('q', '');
  const [profileFilter] = useUrlState<string>('profile', '');
  const [sortDirParam] = useUrlState<string>('dir', 'asc');
  const [page, setPage] = useUrlState<number>('page', 1);
  const [pageSize] = useUrlState<number>('size', 50);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [selectingAll, setSelectingAll] = useState(false);

  const view: ViewId = VIEWS.some(v => v.value === viewParam) ? (viewParam as ViewId) : 'all';
  const sortDir: 'asc' | 'desc' = sortDirParam === 'desc' ? 'desc' : 'asc';
  const monitored = VIEWS.find(v => v.value === view)?.monitored;

  const base = useMemo(() => ({
    search: search || undefined,
    profileId: profileFilter ? Number(profileFilter) : undefined,
  }), [search, profileFilter]);

  const params = useMemo(() => ({ ...base, sortDir, monitored }), [base, sortDir, monitored]);

  const { data: result, isLoading, isError, error, refetch, isPlaceholderData } = useQuery({
    queryKey: ['artists', page, pageSize, params],
    queryFn: () => getArtists({ page, pageSize, ...params }),
    placeholderData: keepPreviousData,
  });

  const counts = useQueries({
    queries: VIEWS.map(v => ({
      queryKey: ['artists', 'count', v.value, base],
      queryFn: () => getArtists({ ...base, monitored: v.monitored, page: 1, pageSize: 1 }),
      staleTime: 30_000,
      placeholderData: keepPreviousData,
    })),
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const download = useDownloadMutation();
  const assign = useAssignProfileMutation();

  const artists: ArtistRow[] = result?.data || [];
  const total = result?.total || 0;
  // Tab counts fall back to the list's own total for the active view when their
  // count query hasn't answered (e.g. rate limited).
  const tabCounts = VIEWS.map((v, i) => counts[i].data?.total
    ?? (v.value === view && !isPlaceholderData ? result?.total : undefined));
  const libraryTotal = tabCounts[0];
  const loadError = isError
    ? { title: "Couldn't load artists", message: (error as Error)?.message, onRetry: () => refetch() }
    : null;
  const filtered = !!(search || profileFilter || view !== 'all');
  const resetPage = { page: [1, 1] as [number, number] };

  const setSortDir = (_key: string, dir: 'asc' | 'desc') => patchUrl({ dir: [dir, 'asc'], ...resetPage });
  const clearFilters = () => patchUrl({ q: ['', ''], profile: ['', ''], view: ['all', 'all'], ...resetPage });
  const clearSelection = useCallback(() => setSelected(new Set()), []);
  const selectedIds = [...selected];

  const selectAllMatching = useCallback(async () => {
    setSelectingAll(true);
    try {
      setSelected(await fetchAllIds((p, size) => getArtists({ ...params, page: p, pageSize: size }), getArtistId));
    } finally {
      setSelectingAll(false);
    }
  }, [params]);

  const columns = useMemo<Column<ArtistRow>[]>(() => [
    {
      key: 'artist',
      header: 'Artist',
      sortKey: 'name',
      priority: 1,
      mobile: 'title',
      cell: a => (
        <TitleCell
          title={a.name}
          href={`/artists/${a.lidarrArtistId}`}
          art={{ src: a.poster, kind: 'artist', name: a.name, shape: 'circle' }}
          secondary={a.sortName && a.sortName.toLowerCase() !== a.name.toLowerCase() ? a.sortName : undefined}
        />
      ),
    },
    {
      key: 'path',
      header: 'Path',
      width: '30%',
      priority: 3,
      mobile: 'hidden',
      cell: a => {
        const p = shortPath(a.path);
        return p ? <span className={lc.mono} title={a.path}>{p}</span> : <span className={lc.mutedText}>—</span>;
      },
    },
    {
      key: 'profile',
      header: 'Profile',
      width: 140,
      priority: 2,
      mobile: 'meta',
      cell: a => {
        const name = profileLabel(a.profileName);
        return name ? <span className={lc.secondaryText}>{name}</span> : <span className={lc.mutedText}>—</span>;
      },
    },
    {
      key: 'monitored',
      header: 'Monitored',
      width: 140,
      priority: 2,
      // Phones: shares the meta line with the profile (one line shorter cards).
      mobile: 'meta',
      skeleton: 'chip',
      cell: a => <span className={lc.inlineStatus}><StatusDot domain="monitored" status={a.monitored} /></span>,
    },
  ], []);

  const rowActions = useCallback((a: ArtistRow) => (
    <>
      <IconButton
        icon={faDownload}
        label="Download missing"
        onClick={() => download.mutate({ target: { artistIds: [a.lidarrArtistId] }, type: 'all', message: `Downloading missing covers and lyrics for ${a.name}...` })}
      />
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <IconButton icon={faEllipsis} label={`More actions for ${a.name}`} />
        </Menu.Target>
        <Menu.Dropdown>
          <SetProfileSubmenu
            profiles={profiles}
            currentId={a.profileId}
            onPick={p => assign.mutate({ target: { artistIds: [a.lidarrArtistId] }, profileId: p.id, profileName: p.name })}
          />
          <Menu.Item component={Link} to={`/artists/${a.lidarrArtistId}`} leftSection={<FontAwesomeIcon icon={faArrowRight} />}>
            Open
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </>
  ), [download, assign, profiles]);

  const empty = libraryTotal === 0 && !search && !profileFilter ? (
    <EmptyState
      size="sm"
      icon={faUser}
      title="No artists yet"
      message="Sync with Lidarr to see your library here."
      action={<LibraryEmptyAction />}
    />
  ) : (
    <EmptyState
      size="sm"
      icon={faFilterCircleXmark}
      title="No artists match"
      action={filtered ? <Button variant="default" onClick={clearFilters}>Clear filters</Button> : undefined}
    />
  );

  const pager = (
    <Pager
      page={page}
      pageSize={pageSize}
      total={total}
      onPage={p => setPage(p, { replace: false })}
      pageSizeOptions={PAGE_SIZES}
      onPageSize={n => patchUrl({ size: [n, 50], ...resetPage })}
      noun="artists"
    />
  );

  const toggleOne = (id: number, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setSelected(next);
  };

  return (
    <div className="ly-page">
      <PageHeader
        title="Artists"
        count={libraryTotal}
        subtitle="Synced from Lidarr"
        actions={<ViewToggle value={artistView} onChange={setArtistView} noun="artists" />}
      />

      <div className={lc.tabsRow}>
        <ViewTabs
          aria-label="Artist views"
          tabs={VIEWS.map((v, i) => ({ value: v.value, label: v.label, count: tabCounts[i] }))}
          value={view}
          onChange={v => patchUrl({ view: [v, 'all'], ...resetPage })}
        />
      </div>

      <FilterBar
        className={lc.filters}
        search={{
          value: search,
          onChange: v => patchUrl({ q: [v, ''], ...resetPage }),
          placeholder: 'Filter artists…',
          'aria-label': 'Filter artists by name',
        }}
        filters={(
          <FilterPill
            label="Profile"
            value={profileFilter || null}
            options={profiles.map(p => ({ value: String(p.id), label: p.name }))}
            onChange={v => patchUrl({ profile: [v ?? '', ''], ...resetPage })}
          />
        )}
        right={<SortMenu options={SORT_OPTIONS} value="name" dir={sortDir} onChange={setSortDir} />}
      />

      {artistView === 'grid' ? (
        loadError ? <GridLoadError error={loadError} noun="artists" /> : isLoading ? <GridSkeleton count={12} /> : artists.length === 0 ? (
          <div className={lc.gridEmpty}>{empty}</div>
        ) : (
          <>
            <ul className={lc.grid} aria-label="Artists" data-dim={isPlaceholderData || undefined}>
              {artists.map(a => (
                <li key={a.lidarrArtistId}>
                  <CoverTile
                    href={`/artists/${a.lidarrArtistId}`}
                    art={{ src: a.poster, alt: '', kind: 'artist', name: a.name }}
                    title={a.name}
                    subtitle={a.monitored ? 'Monitored' : 'Unmonitored'}
                    selected={selected.has(a.lidarrArtistId)}
                    onSelectChange={v => toggleOne(a.lidarrArtistId, v)}
                    actions={(
                      <IconButton
                        icon={faDownload}
                        label="Download missing"
                        variant="default"
                        size="sm"
                        onClick={() => download.mutate({ target: { artistIds: [a.lidarrArtistId] }, type: 'all', message: `Downloading missing covers and lyrics for ${a.name}...` })}
                      />
                    )}
                  />
                </li>
              ))}
            </ul>
            <div className={lc.gridFooter}>{pager}</div>
          </>
        )
      ) : (
        <DataTable<ArtistRow>
          aria-label="Artists"
          columns={columns}
          rows={artists}
          getRowId={getArtistId}
          getRowLabel={a => a.name}
          noun="artist"
          loading={isLoading}
          refetching={isPlaceholderData}
          error={loadError}
          empty={empty}
          rowHref={a => `/artists/${a.lidarrArtistId}`}
          selection={{ selected, onChange: setSelected }}
          selectAllMatching={{ total, onSelectAll: selectAllMatching, busy: selectingAll }}
          sort={{ key: 'name', dir: sortDir, onChange: setSortDir }}
          rowActions={rowActions}
          rowActionsWidth={88}
          footer={total > 0 ? pager : undefined}
        />
      )}

      <BulkBar count={selected.size} noun="artist" onClear={clearSelection}>
        <Button
          size="sm"
          variant="filled"
          leftSection={<FontAwesomeIcon icon={faDownload} />}
          loading={download.isPending}
          onClick={() => download.mutate({ target: { artistIds: selectedIds }, type: 'all' }, { onSuccess: clearSelection })}
        >
          Download missing
        </Button>
        <SetProfileMenuButton
          profiles={profiles}
          loading={assign.isPending}
          onPick={p => assign.mutate(
            { target: { artistIds: selectedIds }, profileId: p.id, profileName: p.name },
            { onSuccess: clearSelection },
          )}
        />
      </BulkBar>
    </div>
  );
}
