import { useCallback, useMemo, useState } from 'react';
import { useQueries, useQuery, keepPreviousData } from '@tanstack/react-query';
import { Button } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAlignLeft, faCompactDisc, faDownload, faFilterCircleXmark, faImage, faSliders } from '@fortawesome/free-solid-svg-icons';
import { getAlbums, getArtists, getProfiles } from '../api';
import { ARTIST_TOTAL_KEY } from '../queryRefresh';
import {
  BulkBar, EmptyState, FilterBar, FilterPill, PageHeader, Pager, SortMenu, ViewTabs, type SortOption,
} from '../components/ui';
import { AlbumTable, getAlbumId, type AlbumRow } from '../components/library/AlbumTable';
import { AlbumGrid } from '../components/library/AlbumGrid';
import { LibraryEmptyAction, SetProfileMenuButton, ViewToggle } from '../components/library/LibraryControls';
import { fetchAllIds, useAssignProfileMutation, useDownloadMutation, type DownloadType } from '../components/library/actions';
import { useUrlPatch, useUrlState } from '../hooks/useUrlState';
import { usePreferences } from '../theme/ThemeProvider';
import { plural } from '../format';
import lc from '../components/library/library.module.css';

/** Saved views (DESIGN.md 8.4): each is a cover / lyrics filter. */
const VIEWS = [
  { value: 'all', label: 'All', filter: {} },
  { value: 'missing-cover', label: 'Missing cover', filter: { coverStatus: 'missing' } },
  { value: 'missing-lyrics', label: 'Missing lyrics', filter: { lyricsStatus: 'missing' } },
  { value: 'partial', label: 'Partial', filter: { lyricsStatus: 'partial' } },
  { value: 'complete', label: 'Complete', filter: { lyricsStatus: 'complete' } },
] as const satisfies readonly { value: string; label: string; filter: { coverStatus?: string; lyricsStatus?: string } }[];

type ViewId = (typeof VIEWS)[number]['value'];

const COVER_OPTIONS = [
  { value: 'available', label: 'Saved or manual' },
  { value: 'missing', label: 'Missing' },
];

const LYRICS_OPTIONS = [
  { value: 'complete', label: 'Complete' },
  { value: 'incomplete', label: 'Incomplete' },
  { value: 'partial', label: 'Partial' },
  { value: 'missing', label: 'Missing' },
  { value: 'unknown', label: 'Unknown' },
];

const MONITORED_OPTIONS = [
  { value: 'true', label: 'Monitored' },
  { value: 'false', label: 'Unmonitored' },
];

const SORT_OPTIONS: SortOption[] = [
  { value: 'title', label: 'Title', dirLabels: { asc: 'A–Z', desc: 'Z–A' } },
  { value: 'year', label: 'Year', dirLabels: { asc: 'oldest first', desc: 'newest first' } },
  { value: 'added', label: 'Date added', dirLabels: { asc: 'oldest first', desc: 'newest first' } },
  { value: 'lyricsStatus', label: 'Lyrics status' },
];

const PAGE_SIZES = [25, 50, 100];

export default function AlbumsPage() {
  const { albumView, setAlbumView } = usePreferences();
  const patchUrl = useUrlPatch();
  const [viewParam] = useUrlState<string>('view', 'all');
  const [search] = useUrlState<string>('q', '');
  const [coverFilter] = useUrlState<string>('cover', '');
  const [lyricsFilter] = useUrlState<string>('lyrics', '');
  const [monitored] = useUrlState<string>('monitored', '');
  const [profileFilter] = useUrlState<string>('profile', '');
  const [sortBy] = useUrlState<string>('sort', 'title');
  const [sortDirParam] = useUrlState<string>('dir', 'asc');
  const [page, setPage] = useUrlState<number>('page', 1);
  const [pageSize] = useUrlState<number>('size', 50);
  const [showMore, setShowMore] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [selectingAll, setSelectingAll] = useState(false);

  const view: ViewId = VIEWS.some(v => v.value === viewParam) ? (viewParam as ViewId) : 'all';
  const sortDir: 'asc' | 'desc' = sortDirParam === 'desc' ? 'desc' : 'asc';
  const viewFilter: { coverStatus?: string; lyricsStatus?: string } = VIEWS.find(v => v.value === view)!.filter;

  // Filters shared by the list and the tab counts (everything except the view).
  const base = useMemo(() => ({
    search: search || undefined,
    monitored: monitored || undefined,
    profileId: profileFilter ? Number(profileFilter) : undefined,
  }), [search, monitored, profileFilter]);

  // A pill (More filters) wins over the view for the same field.
  const params = useMemo(() => ({
    ...base,
    sortBy,
    sortDir,
    coverStatus: coverFilter || viewFilter.coverStatus,
    lyricsStatus: lyricsFilter || viewFilter.lyricsStatus,
  }), [base, sortBy, sortDir, coverFilter, lyricsFilter, viewFilter.coverStatus, viewFilter.lyricsStatus]);

  const { data: result, isLoading, isError, error, refetch, isPlaceholderData } = useQuery({
    queryKey: ['albums', page, pageSize, params],
    queryFn: () => getAlbums({ page, pageSize, ...params }),
    placeholderData: keepPreviousData,
  });

  // Tab counts: five cached pageSize=1 queries.
  const counts = useQueries({
    queries: VIEWS.map(v => ({
      queryKey: ['albums', 'count', v.value, base],
      queryFn: () => getAlbums({ ...base, ...v.filter, page: 1, pageSize: 1 }),
      staleTime: 30_000,
      placeholderData: keepPreviousData,
    })),
  });

  const { data: artistTotal } = useQuery({
    queryKey: ARTIST_TOTAL_KEY,
    queryFn: () => getArtists({ page: 1, pageSize: 1 }),
    staleTime: 5 * 60_000,
    select: d => d.total,
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const download = useDownloadMutation();
  const assign = useAssignProfileMutation();

  const albums: AlbumRow[] = result?.data || [];
  const total = result?.total || 0;
  // Tab counts fall back to the list's own total for the active view (no pill
  // overriding it) when their count query hasn't answered (e.g. rate limited).
  const tabCounts = VIEWS.map((v, i) => counts[i].data?.total
    ?? (v.value === view && !coverFilter && !lyricsFilter && !isPlaceholderData ? result?.total : undefined));
  const libraryTotal = tabCounts[0];
  const filtered = !!(search || coverFilter || lyricsFilter || monitored || profileFilter);
  const loadError = isError
    ? { title: "Couldn't load albums", message: (error as Error)?.message, onRetry: () => refetch() }
    : null;

  const resetPage = { page: [1, 1] as [number, number] };

  const setView = (v: string) => {
    // A view sets the same field as the matching pill: drop the pill.
    const f: { coverStatus?: string; lyricsStatus?: string } = VIEWS.find(x => x.value === v)?.filter ?? {};
    patchUrl({
      view: [v, 'all'],
      ...(f.coverStatus ? { cover: ['', ''] as [string, string] } : {}),
      ...(f.lyricsStatus ? { lyrics: ['', ''] as [string, string] } : {}),
      ...resetPage,
    });
  };

  const setPill = (key: 'cover' | 'lyrics' | 'monitored' | 'profile', value: string | null) => {
    const conflicts =
      (key === 'cover' && value && viewFilter.coverStatus) ||
      (key === 'lyrics' && value && viewFilter.lyricsStatus);
    patchUrl({ [key]: [value ?? '', ''], ...(conflicts ? { view: ['all', 'all'] } : {}), ...resetPage });
  };

  const clearFilters = () => {
    patchUrl({
      q: ['', ''], cover: ['', ''], lyrics: ['', ''], monitored: ['', ''], profile: ['', ''], view: ['all', 'all'], ...resetPage,
    });
  };

  const setSort = (key: string, dir: 'asc' | 'desc') => patchUrl({ sort: [key, 'title'], dir: [dir, 'asc'], ...resetPage });

  const selectAllMatching = useCallback(async () => {
    setSelectingAll(true);
    try {
      const ids = await fetchAllIds((p, size) => getAlbums({ ...params, page: p, pageSize: size }), getAlbumId);
      setSelected(ids);
    } finally {
      setSelectingAll(false);
    }
  }, [params]);

  const clearSelection = useCallback(() => setSelected(new Set()), []);
  const selectedIds = [...selected];

  const runDownload = (type: DownloadType) =>
    download.mutate({ target: { albumIds: selectedIds }, type }, { onSuccess: clearSelection });

  const moreOpen = showMore || !!coverFilter || !!lyricsFilter;
  const profileOptions = profiles.map(p => ({ value: String(p.id), label: p.name }));

  let empty;
  if (libraryTotal === 0 && !filtered) {
    empty = (
      <EmptyState
        size="sm"
        icon={faCompactDisc}
        title="No albums yet"
        message="Sync with Lidarr to see your library here."
        action={<LibraryEmptyAction />}
      />
    );
  } else if (view === 'complete' && !filtered) {
    empty = (
      <EmptyState
        size="sm"
        tone="success"
        icon={faAlignLeft}
        title="No complete albums yet"
        message="Albums show up here once every track has lyrics."
      />
    );
  } else {
    empty = (
      <EmptyState
        size="sm"
        icon={faFilterCircleXmark}
        title="No albums match these filters"
        action={<Button variant="default" onClick={clearFilters}>Clear filters</Button>}
      />
    );
  }

  const pager = (
    <Pager
      page={page}
      pageSize={pageSize}
      total={total}
      onPage={p => setPage(p, { replace: false })}
      pageSizeOptions={PAGE_SIZES}
      onPageSize={n => patchUrl({ size: [n, 50], ...resetPage })}
      noun="albums"
    />
  );

  const selection = { selected, onChange: setSelected };

  return (
    <div className="ly-page">
      <PageHeader
        title="Albums"
        count={libraryTotal}
        subtitle={artistTotal !== undefined ? `from ${plural(artistTotal, 'artist')}` : undefined}
        actions={<ViewToggle value={albumView} onChange={setAlbumView} noun="albums" />}
      />

      <div className={lc.tabsRow}>
        <ViewTabs
          aria-label="Album views"
          tabs={VIEWS.map((v, i) => ({ value: v.value, label: v.label, count: tabCounts[i] }))}
          value={view}
          onChange={setView}
        />
      </div>

      <FilterBar
        className={lc.filters}
        search={{
          value: search,
          onChange: v => patchUrl({ q: [v, ''], ...resetPage }),
          placeholder: 'Filter by album title…',
          'aria-label': 'Filter albums by title',
        }}
        filters={(
          <>
            <FilterPill label="Monitored" value={monitored || null} options={MONITORED_OPTIONS} onChange={v => setPill('monitored', v)} />
            <FilterPill label="Profile" value={profileFilter || null} options={profileOptions} onChange={v => setPill('profile', v)} />
            {moreOpen && (
              <>
                <FilterPill label="Cover" value={coverFilter || null} options={COVER_OPTIONS} onChange={v => setPill('cover', v)} />
                <FilterPill label="Lyrics" value={lyricsFilter || null} options={LYRICS_OPTIONS} onChange={v => setPill('lyrics', v)} />
              </>
            )}
            {!coverFilter && !lyricsFilter && (
              <Button
                variant="subtle"
                color="gray"
                size="sm"
                className={lc.moreButton}
                data-active={moreOpen || undefined}
                aria-expanded={moreOpen}
                leftSection={<FontAwesomeIcon icon={faSliders} />}
                onClick={() => setShowMore(o => !o)}
              >
                {moreOpen ? 'Fewer filters' : 'More filters'}
              </Button>
            )}
          </>
        )}
        right={<SortMenu options={SORT_OPTIONS} value={sortBy} dir={sortDir} onChange={setSort} />}
      />

      {albumView === 'grid' ? (
        <AlbumGrid
          aria-label="Albums"
          rows={albums}
          loading={isLoading}
          refetching={isPlaceholderData}
          error={loadError}
          empty={empty}
          footer={pager}
          selection={selection}
        />
      ) : (
        <AlbumTable
          aria-label="Albums"
          rows={albums}
          profiles={profiles}
          loading={isLoading}
          refetching={isPlaceholderData}
          error={loadError}
          empty={empty}
          footer={total > 0 ? pager : undefined}
          selection={selection}
          selectAllMatching={{ total, onSelectAll: selectAllMatching, busy: selectingAll }}
          sort={{ key: sortBy, dir: sortDir, onChange: setSort }}
        />
      )}

      <BulkBar count={selected.size} noun="album" onClear={clearSelection}>
        <Button
          size="sm"
          variant="filled"
          leftSection={<FontAwesomeIcon icon={faDownload} />}
          loading={download.isPending && download.variables?.type === 'all'}
          disabled={download.isPending}
          onClick={() => runDownload('all')}
        >
          Download missing
        </Button>
        <Button
          size="sm"
          variant="default"
          leftSection={<FontAwesomeIcon icon={faImage} />}
          loading={download.isPending && download.variables?.type === 'covers'}
          disabled={download.isPending}
          onClick={() => runDownload('covers')}
        >
          Covers
        </Button>
        <Button
          size="sm"
          variant="default"
          leftSection={<FontAwesomeIcon icon={faAlignLeft} />}
          loading={download.isPending && download.variables?.type === 'lyrics'}
          disabled={download.isPending}
          onClick={() => runDownload('lyrics')}
        >
          Lyrics
        </Button>
        <SetProfileMenuButton
          profiles={profiles}
          loading={assign.isPending}
          onPick={p => assign.mutate(
            { target: { albumIds: selectedIds }, profileId: p.id, profileName: p.name },
            { onSuccess: clearSelection },
          )}
        />
      </BulkBar>
    </div>
  );
}
