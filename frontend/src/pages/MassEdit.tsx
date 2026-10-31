import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Menu, Select } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { useCallback, useDeferredValue, useMemo, useRef, useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faArrowDown, faArrowLeft, faArrowRight, faArrowUp, faArrowUpWideShort, faCircleExclamation, faDownload, faEllipsis, faFilterCircleXmark, faLanguage, faTrashCan,
} from '@fortawesome/free-solid-svg-icons';
import {
  getLibraryTree, batchDownload, batchTranslate, batchDeleteLyrics, upgradeLyrics,
  LibraryTreeArtist,
} from '../api';
import { SSEEvent } from '../types';
import { useServerEvents } from '../events';
import { refreshQueries } from '../queryRefresh';
import { errorMessage } from '../notify';
import { num, plural } from '../format';
import {
  BulkBar, EmptyState, FilterBar, IconButton, ListSkeleton, PageHeader, Panel,
} from '../components/ui';
import { MassEditTree, type NodeKey } from './manage/MassEditTree';
import { JobCard, RecentEvents, SelectionCard, type SelectionStats } from './manage/MassEditSide';
import { DeleteModal, TranslateModal, type DeleteMode } from './manage/MassEditModals';
import c from './manage/MassEdit.module.css';

/** With a filter active, matching artists open automatically up to this many (keeps the DOM small). */
const AUTO_EXPAND_MAX = 40;

// Events after which the tree's per-album counts may have changed.
const JOB_FINISHED_EVENTS = new Set([
  'download_complete', 'batch_translate_complete', 'batch_sync_complete', 'batch_delete_complete', 'sync_complete',
]);

export default function MassEditPage() {
  const queryClient = useQueryClient();
  const narrow = useMediaQuery('(max-width: 1023px)');
  const phone = useMediaQuery('(max-width: 479px)');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  // Filtering a big library is the expensive part; let typing stay responsive
  // and re-filter at lower priority.
  const deferredSearch = useDeferredValue(search);
  const [genre, setGenre] = useState<string | null>(null);
  const [cursor, setCursor] = useState<NodeKey | null>(null);
  const [translateOpen, setTranslateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [targetLang, setTargetLang] = useState<string | null>('en');
  const [forceTranslate, setForceTranslate] = useState(false);
  const [deleteMode, setDeleteMode] = useState<DeleteMode>('all');

  const { data: tree, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['library-tree'],
    queryFn: getLibraryTree,
  });

  // Apply search + genre filters. An artist survives if it matches by name or
  // has at least one album passing both filters.
  const filteredArtists: LibraryTreeArtist[] = useMemo(() => {
    if (!tree) return [];
    const q = deferredSearch.trim().toLowerCase();
    return tree.artists
      .map(artist => {
        const nameMatch = q && artist.name.toLowerCase().includes(q);
        const albums = artist.albums.filter(album => {
          if (genre && !album.genres.includes(genre)) return false;
          if (q && !nameMatch && !album.title.toLowerCase().includes(q)) return false;
          return true;
        });
        return { ...artist, albums };
      })
      .filter(artist => artist.albums.length > 0);
  }, [tree, deferredSearch, genre]);

  const visibleAlbumIds = useMemo(
    () => new Set(filteredArtists.flatMap(a => a.albums.map(al => al.id))),
    [filteredArtists],
  );

  // Expansion: artists start collapsed; with a filter active, matching artists
  // open automatically (the user can still close them).
  const filterKey = `${deferredSearch.trim().toLowerCase()}\u0000${genre ?? ''}`;
  const filterActive = filterKey !== '\u0000';
  const autoExpand = filterActive && filteredArtists.length <= AUTO_EXPAND_MAX;
  const [autoClosed, setAutoClosed] = useState<{ key: string; ids: Set<number> }>({ key: '', ids: new Set() });
  const closedNow = autoClosed.key === filterKey ? autoClosed.ids : null;

  const isExpanded = useCallback(
    (id: number) => (autoExpand ? !(closedNow?.has(id)) : expanded.has(id)),
    [autoExpand, closedNow, expanded],
  );

  const setArtistExpanded = useCallback((id: number, open: boolean) => {
    if (autoExpand) {
      setAutoClosed(prev => {
        const ids = new Set(prev.key === filterKey ? prev.ids : []);
        if (open) ids.delete(id); else ids.add(id);
        return { key: filterKey, ids };
      });
    } else {
      setExpanded(prev => {
        if (prev.has(id) === open) return prev;
        const next = new Set(prev);
        if (open) next.add(id); else next.delete(id);
        return next;
      });
    }
  }, [autoExpand, filterKey]);

  // Stable identity: memoized artist rows never hold a stale toggle.
  const latest = useRef({ isExpanded, setArtistExpanded });
  latest.current = { isExpanded, setArtistExpanded };
  const toggleExpanded = useCallback((id: number) => {
    const { isExpanded: open, setArtistExpanded: set } = latest.current;
    set(id, !open(id));
  }, []);

  const selectionStats: SelectionStats = useMemo(() => {
    let artists = 0, albums = 0, tracks = 0, missing = 0, unsynced = 0;
    for (const artist of tree?.artists || []) {
      let any = false;
      for (const album of artist.albums) {
        if (!selected.has(album.id)) continue;
        any = true;
        albums += 1;
        tracks += album.tracks;
        missing += album.missing;
        unsynced += Math.max(0, album.withLyrics - album.synced);
      }
      if (any) artists += 1;
    }
    return { artists, albums, tracks, missing, unsynced };
  }, [tree, selected]);

  const toggleAlbum = useCallback((id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleArtist = useCallback((artist: LibraryTreeArtist) => {
    setSelected(prev => {
      const next = new Set(prev);
      const allSelected = artist.albums.every(a => next.has(a.id));
      artist.albums.forEach(a => allSelected ? next.delete(a.id) : next.add(a.id));
      return next;
    });
  }, []);

  const selectAllVisible = () => setSelected(new Set([...selected, ...visibleAlbumIds]));
  const clearSelection = useCallback(() => setSelected(new Set()), []);
  const clearFilters = () => { setSearch(''); setGenre(null); };

  const selectedIds = useMemo(() => [...selected], [selected]);

  const onError = (err: unknown) => notifications.show({
    title: 'Not started',
    message: errorMessage(err),
    color: 'warning',
  });
  const onStarted = (data: any) => notifications.show({
    title: 'Started', message: data.message, color: 'info',
  });

  const fetchMutation = useMutation({
    mutationFn: () => batchDownload({ albumIds: selectedIds, type: 'all' }),
    onSuccess: onStarted,
    onError,
  });
  const upgradeMutation = useMutation({
    mutationFn: () => upgradeLyrics({ albumIds: selectedIds }),
    onSuccess: onStarted,
    onError,
  });
  const translateMutation = useMutation({
    mutationFn: () => batchTranslate({ albumIds: selectedIds, targetLang: targetLang || 'en', force: forceTranslate }),
    onSuccess: (data: any) => { setTranslateOpen(false); onStarted(data); },
    onError,
  });
  const deleteMutation = useMutation({
    mutationFn: () => batchDeleteLyrics({ albumIds: selectedIds, mode: deleteMode }),
    onSuccess: (data: any) => {
      setDeleteOpen(false);
      notifications.show({ title: 'Deleted', message: data.message, color: 'success' });
      // Album/track statuses, wanted lists and dashboard counts all changed.
      refreshQueries(queryClient);
    },
    onError,
  });

  // The tree's per-album counts change when a job finishes: refetch it (live
  // events only; replayed history must not trigger work).
  useServerEvents((event: SSEEvent) => {
    if (event.replay || !JOB_FINISHED_EVENTS.has(event.type)) return;
    queryClient.invalidateQueries({ queryKey: ['library-tree'] });
  });

  const actions = {
    onFetch: () => fetchMutation.mutate(),
    onUpgrade: () => upgradeMutation.mutate(),
    onTranslate: () => setTranslateOpen(true),
    onDelete: () => setDeleteOpen(true),
    fetching: fetchMutation.isPending,
    upgrading: upgradeMutation.isPending,
  };

  const libraryArtists = tree?.artists.length ?? 0;
  const libraryAlbums = useMemo(() => (tree?.artists ?? []).reduce((n, a) => n + a.albums.length, 0), [tree]);

  const subtitle = selectionStats.albums > 0
    ? `${plural(selectionStats.artists, 'artist')} · ${plural(selectionStats.albums, 'album')} · ${plural(selectionStats.tracks, 'track')} selected`
    : tree
      ? `Nothing selected · ${plural(libraryAlbums, 'album')} by ${plural(libraryArtists, 'artist')}`
      : 'Select artists or albums, then run an action';

  let body;
  if (isLoading) {
    body = <ListSkeleton rows={12} />;
  } else if (isError) {
    body = (
      <EmptyState
        size="sm"
        tone="danger"
        icon={faCircleExclamation}
        title="Couldn't load the library"
        message={errorMessage(error)}
        action={<Button variant="default" onClick={() => refetch()}>Retry</Button>}
      />
    );
  } else if (filteredArtists.length === 0) {
    body = (
      <EmptyState
        size="sm"
        icon={faFilterCircleXmark}
        title="No artists match the current filters"
        message={filterActive ? 'Try a different name, album title or genre.' : 'The library is empty.'}
        action={filterActive ? <Button variant="default" onClick={clearFilters}>Clear filters</Button> : undefined}
      />
    );
  } else {
    body = (
      <MassEditTree
        artists={filteredArtists}
        selected={selected}
        isExpanded={isExpanded}
        expansionKey={isExpanded}
        cursor={cursor}
        onCursor={setCursor}
        onToggleArtist={toggleArtist}
        onToggleAlbum={toggleAlbum}
        onToggleExpanded={toggleExpanded}
        onSetExpanded={setArtistExpanded}
      />
    );
  }

  // Below 1024px the selection actions live in the floating BulkBar instead.
  const side = (
    <aside className={c.side} aria-label="Bulk actions and progress">
      <JobCard />
      {!narrow && <SelectionCard stats={selectionStats} actions={actions} />}
      <RecentEvents collapsible={narrow} />
    </aside>
  );

  return (
    <div className="ly-page">
      <PageHeader title="Mass edit" subtitle={subtitle} />

      <div className={c.layout}>
        {/* Narrow: job progress and events come before the (very long) tree. */}
        {narrow && side}
        <Panel padding="none" className={c.libraryPanel} aria-label="Library">
          <div className={c.toolbar}>
            <FilterBar
              search={{
                value: search,
                onChange: setSearch,
                placeholder: 'Filter artists / albums…',
                'aria-label': 'Filter artists and albums',
              }}
              filters={(
                <Select
                  aria-label="Filter by genre"
                  placeholder="Any genre"
                  data={tree?.genres || []}
                  value={genre}
                  onChange={setGenre}
                  clearable
                  searchable
                  nothingFoundMessage="No genres match"
                  className={c.genre}
                />
              )}
              right={(
                <>
                  <Button variant="default" onClick={selectAllVisible} disabled={visibleAlbumIds.size === 0}>
                    Select all {plural(visibleAlbumIds.size, 'album')}
                  </Button>
                  <Button variant="subtle" color="gray" onClick={clearSelection} disabled={selected.size === 0}>
                    Clear
                  </Button>
                </>
              )}
            />
          </div>
          <div className={c.treeHead} aria-hidden="true">
            <span className={c.treeHeadLabel}>Artist / album</span>
            <span className={c.treeHeadKeys}>
              {/* Icons, not arrow characters: the UI fonts draw ↑↓ and ←→ at different sizes. */}
              <kbd><FontAwesomeIcon icon={faArrowUp} /><FontAwesomeIcon icon={faArrowDown} /></kbd> move{' '}
              <kbd><FontAwesomeIcon icon={faArrowLeft} /><FontAwesomeIcon icon={faArrowRight} /></kbd> expand{' '}
              <kbd>Space</kbd> select
            </span>
            <span className={c.treeHeadCols}>
              {/* Shares the .year class with the album rows, so the container query
                  (MassEdit.module.css) hides header cell and row cells together. */}
              <span className={c.year}>Year</span>
              <span className={c.meter}>Lyrics</span>
              <span className={c.cover}>Cover</span>
              <span className={c.openSpacer} />
            </span>
          </div>
          {body}
        </Panel>

        {!narrow && side}
      </div>

      {/* Below 1024px the action stack becomes the floating bulk bar. */}
      {narrow && (
        <BulkBar count={selectionStats.albums} noun="album" onClear={clearSelection}>
          <Button
            size="sm"
            variant="filled"
            leftSection={<FontAwesomeIcon icon={faDownload} />}
            loading={fetchMutation.isPending}
            onClick={actions.onFetch}
            aria-label={`Fetch missing (${num(selectionStats.missing)})`}
          >
            {phone ? 'Fetch' : `Fetch missing (${num(selectionStats.missing)})`}
          </Button>
          <Button
            size="sm"
            variant="default"
            visibleFrom="sm"
            leftSection={<FontAwesomeIcon icon={faArrowUpWideShort} />}
            loading={upgradeMutation.isPending}
            onClick={actions.onUpgrade}
          >
            Upgrade ({num(selectionStats.unsynced)})
          </Button>
          <Menu position="top-end" withinPortal>
            <Menu.Target>
              <IconButton icon={faEllipsis} label="More bulk actions" variant="default" size="lg" />
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item hiddenFrom="sm" leftSection={<FontAwesomeIcon icon={faArrowUpWideShort} />} onClick={actions.onUpgrade}>
                Upgrade to synced ({num(selectionStats.unsynced)})
              </Menu.Item>
              <Menu.Item leftSection={<FontAwesomeIcon icon={faLanguage} />} onClick={actions.onTranslate}>
                Translate…
              </Menu.Item>
              <Menu.Divider />
              <Menu.Item color="danger" leftSection={<FontAwesomeIcon icon={faTrashCan} />} onClick={actions.onDelete}>
                Delete lyrics…
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </BulkBar>
      )}

      <TranslateModal
        opened={translateOpen}
        onClose={() => setTranslateOpen(false)}
        albums={selectionStats.albums}
        targetLang={targetLang}
        onTargetLang={setTargetLang}
        force={forceTranslate}
        onForce={setForceTranslate}
        onSubmit={() => translateMutation.mutate()}
        loading={translateMutation.isPending}
      />

      <DeleteModal
        opened={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        albums={selectionStats.albums}
        mode={deleteMode}
        onMode={setDeleteMode}
        onSubmit={() => deleteMutation.mutate()}
        loading={deleteMutation.isPending}
      />
    </div>
  );
}
