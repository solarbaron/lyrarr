import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Loader, SegmentedControl, Switch, Tooltip } from '@mantine/core';
import { useLocalStorage, useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faBan, faChevronRight, faDownload, faMagnifyingGlass, faPenToSquare, faRotateRight,
} from '@fortawesome/free-solid-svg-icons';
import {
  batchDownload, getAlbum, getAlbums, getProviderHealth, getWantedLyrics, searchLyrics, updateTrack,
} from '../../api';
import { notifyError, errorMessage } from '../../notify';
import { isSequencePending } from '../../shortcuts';
import { duration as fmtDuration, plural } from '../../format';
import { useUrlPatch, useUrlState } from '../../hooks/useUrlState';
import { useAlbumCounts } from '../../hooks/useAlbumCounts';
import {
  BulkBar, DataTable, EmptyState, FilterBar, IconButton, LyricsMeter, Pager, StatusDot, TitleCell, useConfirm,
  type Column,
} from '../../components/ui';
import LyricsSearchModal from '../../components/LyricsSearchModal';
import LyricsEditorModal from '../../components/LyricsEditorModal';
import { Inspector, SEARCH_STALE, lyricsSearchKey, type InspectorHandle, type InspectorTrack } from './Inspector';
import {
  Attempts, DEFAULT_PAGE_SIZE, PAGE_SIZES, albumFolder, closeRowMenu, fileName, refreshWanted,
  type WantedAlbum, type WantedStats, type WantedTrack,
} from './shared';
import { useFitToViewport } from './useFitToViewport';
import { InstrumentalHintTag } from '../../components/instrumental/InstrumentalEvidence';
import c from './Wanted.module.css';

type Row =
  | { kind: 'album'; id: number; album: WantedAlbum }
  | { kind: 'track'; id: number; track: WantedTrack; album?: WantedAlbum };

const trackRowId = (trackId: number) => -trackId;

function toInspectorTrack(row: Row & { kind: 'track' }): InspectorTrack {
  const t = row.track;
  return {
    id: t.lidarrTrackId,
    title: t.title,
    trackNumber: t.trackNumber,
    discNumber: t.discNumber,
    duration: t.duration,
    albumId: t.albumId,
    // By track: /api/wanted/lyrics has no album title, the folder name stands in.
    albumTitle: row.album?.title ?? t.albumTitle ?? (albumFolder(t.path) || undefined),
    artistName: row.album?.artistName ?? t.artistName,
    retryAfter: t.lyrics_retry_after,
    retryCount: t.lyrics_retry_count,
    row: t,
  };
}

function focusedElement(): HTMLElement | null {
  const el = document.activeElement;
  return el instanceof HTMLElement && el !== document.body ? el : null;
}

/** Typing targets and open dialogs/menus never receive Wanted's single-key shortcuts. */
function ignoreKeys(e: KeyboardEvent): boolean {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return true;
  const t = e.target as HTMLElement | null;
  if (!t || typeof t.closest !== 'function') return false;
  if (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) {
    const type = (t as HTMLInputElement).type;
    if (!(t.tagName === 'INPUT' && ['checkbox', 'radio', 'range'].includes(type))) return true;
  }
  if (t.closest('[role="dialog"], [aria-modal="true"], [role="menu"], [role="listbox"]')) return true;
  return !!document.querySelector('.mantine-Modal-content, .mantine-Drawer-content');
}

/** Provider health says no lyrics provider can be asked right now. */
function allLyricsProvidersDown(health: unknown): boolean {
  const providers = (health as { providers?: Record<string, { type?: string; available?: boolean }> } | undefined)?.providers ?? {};
  const lyrics = Object.values(providers).filter(p => p.type === 'lyrics' || p.type === 'both');
  return lyrics.length > 0 && lyrics.every(p => p.available === false);
}

interface Props {
  stats?: WantedStats;
}

/** Lyrics tab of the Wanted triage workspace (DESIGN.md 8.6). */
export function LyricsTab({ stats }: Props) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const patchUrl = useUrlPatch();
  const [q] = useUrlState('q', '');
  const [page] = useUrlState('page', 1);
  const [size] = useUrlState('size', DEFAULT_PAGE_SIZE);
  const [group] = useUrlState<'album' | 'track'>('group', 'album');
  const byAlbum = group !== 'track';
  const showInspector = useMediaQuery('(min-width: 1280px)') ?? false;
  const isMobile = useMediaQuery('(max-width: 767px)') ?? false;

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [cursor, setCursor] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [requested, setRequested] = useState<Set<number>>(new Set());
  const [searchFor, setSearchFor] = useState<InspectorTrack | null>(null);
  const [editorFor, setEditorFor] = useState<InspectorTrack | null>(null);
  const [downloading, setDownloading] = useState<Set<number>>(new Set());
  const [autoSearch, setAutoSearch] = useLocalStorage<boolean>({ key: 'lyrarr-wanted-autosearch', defaultValue: false });
  const inspectorRef = useRef<InspectorHandle>(null);
  const tableRef = useRef<HTMLDivElement>(null);
  /** Where focus goes back to when the search modal or the editor closes (DESIGN.md 9.3). */
  const returnFocus = useRef<{ el: HTMLElement | null; rowId: number } | null>(null);
  const autoOpened = useRef<string | null>(null);

  // Selection, cursor and expansion reset when the list changes (as before).
  useEffect(() => {
    setSelected(new Set());
    setCursor(null);
    setExpanded(new Set());
    autoOpened.current = null;
  }, [q, page, size, group]);

  /* ---------- data ---------- */
  const albumsQuery = useQuery({
    queryKey: ['wanted-lyrics-albums', page, q, size],
    queryFn: () => getAlbums({ lyricsStatus: 'incomplete', search: q || undefined, page, pageSize: size }),
    placeholderData: keepPreviousData,
    enabled: byAlbum,
  });
  const tracksQuery = useQuery({
    queryKey: ['wanted-lyrics', page, q, size],
    queryFn: () => getWantedLyrics({ page, pageSize: size, search: q || undefined }),
    placeholderData: keepPreviousData,
    enabled: !byAlbum,
  });
  const counts = useAlbumCounts(byAlbum);

  const expandedIds = useMemo(() => [...expanded], [expanded]);
  const albumDetails = useQueries({
    queries: expandedIds.map(id => ({
      queryKey: ['album', String(id)],
      queryFn: () => getAlbum(id),
    })),
  });

  // The first album of a fresh page opens by itself, so the triage starts on a track.
  useEffect(() => {
    if (!byAlbum || albumsQuery.isPlaceholderData) return;
    const first = albumsQuery.data?.data?.[0];
    const key = `${page}|${q}|${size}`;
    if (!first || autoOpened.current === key) return;
    autoOpened.current = key;
    setExpanded(new Set([first.lidarrAlbumId]));
  }, [byAlbum, albumsQuery.data, albumsQuery.isPlaceholderData, page, q, size]);

  const rows: Row[] = useMemo(() => {
    if (!byAlbum) {
      return (tracksQuery.data?.data ?? []).map(t => ({ kind: 'track' as const, id: trackRowId(t.lidarrTrackId), track: t as WantedTrack }));
    }
    const out: Row[] = [];
    const details = new Map<number, (typeof albumDetails)[number]>();
    expandedIds.forEach((id, i) => details.set(id, albumDetails[i]));
    for (const album of (albumsQuery.data?.data ?? []) as WantedAlbum[]) {
      out.push({ kind: 'album', id: album.lidarrAlbumId, album });
      if (!expanded.has(album.lidarrAlbumId)) continue;
      const tracks = details.get(album.lidarrAlbumId)?.data?.tracks ?? [];
      tracks
        .filter(t => t.lyrics_status === 'missing')
        .sort((a, b) => (a.discNumber - b.discNumber) || (a.trackNumber - b.trackNumber))
        .forEach(t => out.push({ kind: 'track', id: trackRowId(t.lidarrTrackId), track: t as WantedTrack, album }));
    }
    return out;
  }, [byAlbum, tracksQuery.data, albumsQuery.data, expanded, expandedIds, albumDetails]);

  const rowById = useMemo(() => new Map(rows.map(r => [r.id, r])), [rows]);
  const cursorRow = cursor !== null ? rowById.get(cursor) ?? null : null;
  const cursorTrack = cursorRow?.kind === 'track' ? toInspectorTrack(cursorRow) : null;
  const multiDisc = useCallback((albumId: number) => {
    const i = expandedIds.indexOf(albumId);
    const tracks = i >= 0 ? albumDetails[i]?.data?.tracks : undefined;
    return !!tracks?.some(t => t.discNumber > 1);
  }, [expandedIds, albumDetails]);

  const activeQuery = byAlbum ? albumsQuery : tracksQuery;
  const total = activeQuery.data?.total ?? 0;

  /* ---------- actions ---------- */
  const toggleExpand = useCallback((albumId: number, open?: boolean) => {
    setExpanded(prev => {
      const next = new Set(prev);
      const willOpen = open ?? !next.has(albumId);
      if (willOpen) next.add(albumId);
      else next.delete(albumId);
      return next;
    });
  }, []);

  const focusRow = (id: number) => {
    const el = tableRef.current?.querySelector<HTMLElement>(`[data-row-id="${id}"]`);
    el?.focus({ preventScroll: true });
    el?.scrollIntoView({ block: 'nearest' });
  };

  /**
   * The modals unmount on close (so Mantine can't return focus itself): remember
   * the trigger, or the track's row when the trigger goes away (mobile ⋯ popover).
   */
  const restoreFocus = () => {
    const target = returnFocus.current;
    returnFocus.current = null;
    if (!target) return;
    window.setTimeout(() => {
      if (target.el?.isConnected && !target.el.closest('.mantine-Popover-dropdown')) target.el.focus({ preventScroll: true });
      else focusRow(target.rowId);
    }, 0);
  };
  const openSearch = useCallback((t: InspectorTrack) => {
    returnFocus.current = { el: focusedElement(), rowId: trackRowId(t.id) };
    setSearchFor(t);
  }, []);
  const openEditor = useCallback((t: InspectorTrack) => {
    returnFocus.current = { el: focusedElement(), rowId: trackRowId(t.id) };
    setEditorFor(t);
  }, []);

  const requestSearch = useCallback((t: InspectorTrack) => {
    if (showInspector) {
      setCursor(trackRowId(t.id));
      setRequested(prev => (prev.has(t.id) ? prev : new Set(prev).add(t.id)));
    } else {
      openSearch(t);
    }
  }, [showInspector, openSearch]);

  const downloadAlbums = useMutation({
    mutationFn: (albumIds: number[]) => {
      setDownloading(new Set(albumIds));
      return batchDownload({ albumIds, type: 'lyrics' });
    },
    onSuccess: (r: { message?: string }, albumIds) => {
      notifications.show({ title: 'Download started', message: r?.message || `Downloading lyrics for ${plural(albumIds.length, 'album')}…` });
      setSelected(new Set());
    },
    onError: (err) => notifyError('Download failed to start', err),
    onSettled: () => setDownloading(new Set()),
  });

  const skipMutation = useMutation({
    mutationFn: (t: InspectorTrack) => updateTrack(t.id, { lyrics_status: 'blacklisted' }),
    onSuccess: (_d, t) => {
      notifications.show({ title: 'Skipped', message: `${t.title} will be skipped by the downloader`, color: 'gray' });
      refreshWanted(queryClient);
      queryClient.invalidateQueries({ queryKey: ['album', String(t.albumId)] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
    },
    onError: (err) => notifyError('Failed to update track status', err),
  });

  const skip = useCallback(async (t: InspectorTrack) => {
    const ok = await confirm({
      title: 'Skip lyrics for this track?',
      message: (
        <>
          <strong>{t.title}</strong> will be skipped by the downloader. If it has no vocals, mark it instrumental
          from its album page instead, so the album counts it as complete.
        </>
      ),
      confirmLabel: 'Skip track',
    });
    if (ok) skipMutation.mutate(t);
  }, [confirm, skipMutation]);

  /** The row after a track (the next track, else whatever row follows). */
  const rowAfter = useCallback((trackId: number) => {
    const id = trackRowId(trackId);
    const i = rows.findIndex(r => r.id === id);
    return rows.slice(i + 1).find(r => r.kind === 'track') ?? rows[i + 1] ?? null;
  }, [rows]);

  /** After a save the cursor moves to the next track (DESIGN.md 8.6). */
  const onApplied = useCallback((trackId: number) => {
    const next = rowAfter(trackId);
    if (next) {
      setCursor(next.id);
      window.setTimeout(() => focusRow(next.id), 0);
    }
  }, [rowAfter]);

  /** Saved from the "Find lyrics" dialog: same move, focus lands there when it closes. */
  const onModalApplied = useCallback((trackId: number) => {
    refreshWanted(queryClient);
    const next = rowAfter(trackId);
    if (next) {
      setCursor(next.id);
      returnFocus.current = { el: null, rowId: next.id };
    }
  }, [rowAfter, queryClient]);

  /* ---------- auto-search next (off by default) ---------- */
  const autoFails = useRef(0);
  const inFlight = useRef(false);
  const pauseAuto = useCallback((message: string) => {
    setAutoSearch(false);
    autoFails.current = 0;
    notifications.show({ title: 'Auto-search paused', message, color: 'info' });
  }, [setAutoSearch]);

  const cursorTrackId = cursorTrack?.id ?? null;
  useEffect(() => {
    if (!autoSearch || !showInspector || cursorTrackId === null) return;
    if (queryClient.getQueryData(lyricsSearchKey(cursorTrackId))) return;
    const timer = window.setTimeout(async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const res = await queryClient.fetchQuery({
          queryKey: lyricsSearchKey(cursorTrackId),
          queryFn: () => searchLyrics(cursorTrackId),
          staleTime: SEARCH_STALE,
        });
        autoFails.current = res?.results?.length ? 0 : autoFails.current + 1;
      } catch {
        autoFails.current += 1;
      } finally {
        inFlight.current = false;
      }
      if (autoFails.current >= 3) pauseAuto('The last 3 searches found nothing. Press s to search a track yourself, or turn auto-search back on.');
    }, 600);
    return () => window.clearTimeout(timer);
  }, [autoSearch, showInspector, cursorTrackId, queryClient, pauseAuto]);

  // While it runs, auto-search pauses as soon as every lyrics provider is unavailable
  // (same key and cadence as the top bar's health pill, so no extra polling).
  const providerHealth = useQuery({
    queryKey: ['provider-health'],
    queryFn: getProviderHealth,
    enabled: autoSearch && showInspector,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
  useEffect(() => {
    if (!autoSearch || !showInspector || !providerHealth.data) return;
    if (allLyricsProvidersDown(providerHealth.data)) {
      pauseAuto('Every lyrics provider is cooling down after errors. Try again later.');
    }
  }, [autoSearch, showInspector, providerHealth.data, pauseAuto]);

  const toggleAuto = (on: boolean) => {
    setAutoSearch(on);
    autoFails.current = 0;
    // Turning it on checks provider health right away (the effect above); health is
    // advisory, so a failed health request keeps auto-search on.
    if (on) void queryClient.invalidateQueries({ queryKey: ['provider-health'] });
  };

  /* ---------- keyboard (wanted scope, DESIGN.md 6.4) ---------- */
  const keyState = useRef({ cursorRow, cursorTrack, rows, showInspector });
  keyState.current = { cursorRow, cursorTrack, rows, showInspector };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (ignoreKeys(e)) return;
      // Leave the second key of a global "g x" sequence (g s = Settings) to the shell.
      if (e.key.length === 1 && isSequencePending()) return;
      const { cursorRow: row, cursorTrack: t, rows: list, showInspector: inspector } = keyState.current;
      const target = e.target as HTMLElement;
      // Enter applies only from a row, a candidate card or the page itself; buttons keep their own Enter.
      const onRowOrPage = target === document.body || target.hasAttribute('data-row') || target.tagName === 'LI';
      const key = e.key;

      if (/^[1-9]$/.test(key) && inspector) {
        if (inspectorRef.current?.highlight(Number(key) - 1)) e.preventDefault();
        return;
      }
      if (key === 'Enter' && onRowOrPage && inspector && inspectorRef.current?.hasCandidates()) {
        if (inspectorRef.current.apply()) e.preventDefault();
        return;
      }
      // j/k keep moving the table cursor after a click in the inspector or a focus loss
      // (arrows stay with the page scroll); inside the table DataTable handles them itself.
      const inInspector = !!target.closest?.('[data-wanted-inspector]');
      // The page itself: <body>, or <main> after a click on empty page background.
      const fromPage = target === document.body || target === document.documentElement || target.tagName === 'MAIN';
      const step = key === 'j' ? 1 : key === 'k' ? -1 : 0;
      if (step && (fromPage || inInspector) && list.length > 0) {
        e.preventDefault();
        const idx = row ? list.findIndex(r => r.id === row.id) : -1;
        const next = list[idx < 0 ? 0 : Math.max(0, Math.min(list.length - 1, idx + step))];
        setCursor(next.id);
        focusRow(next.id);
        return;
      }
      if (!row) return;
      if (key === 's' && t) {
        e.preventDefault();
        requestSearch(t);
      } else if (key === 'e' && t) {
        e.preventDefault();
        openEditor(t);
      } else if (key === 'b' && t) {
        e.preventDefault();
        void skip(t);
      } else if (key === 'o') {
        e.preventDefault();
        navigate(`/albums/${row.kind === 'album' ? row.album.lidarrAlbumId : row.track.albumId}`);
      } else if ((key === 'ArrowRight' || key === 'ArrowLeft') && target.hasAttribute('data-row')) {
        if (row.kind === 'album') {
          e.preventDefault();
          toggleExpand(row.album.lidarrAlbumId, key === 'ArrowRight');
        } else if (key === 'ArrowLeft' && row.album) {
          e.preventDefault();
          const parent = row.album.lidarrAlbumId;
          setCursor(parent);
          focusRow(parent);
        }
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [navigate, requestSearch, openEditor, skip, toggleExpand]);

  /* ---------- columns ---------- */
  const trackActions = (row: Row & { kind: 'track' }) => {
    const t = toInspectorTrack(row);
    return (
      <>
        <IconButton icon={faMagnifyingGlass} label="Search providers" shortcut="s" onClick={e => { closeRowMenu(e); requestSearch(t); }} />
        <IconButton icon={faPenToSquare} label="Edit lyrics" shortcut="e" onClick={e => { closeRowMenu(e); openEditor(t); }} />
        <IconButton icon={faBan} label="Skip track" shortcut="b" onClick={e => { closeRowMenu(e); void skip(t); }} />
      </>
    );
  };

  const albumStatus = (album: WantedAlbum) => {
    const n = counts.get(album.lidarrAlbumId);
    if (!n) return <StatusDot domain="albumLyrics" status={album.lyrics_status} />;
    return (
      <span className={c.albumMeter}>
        <LyricsMeter
          synced={n.synced}
          plain={n.plain}
          instrumental={n.instrumental}
          total={n.total}
          width={showInspector ? 48 : 64}
          aria-label={`Lyrics for ${album.title}`}
        />
        {/* Next to the inspector the count is in the meter (and the inspector); the title gets the room. */}
        {!showInspector && <span className={c.albumMissing}>{plural(n.missing, 'missing', 'missing')}</span>}
      </span>
    );
  };

  const albumColumns: Column<Row>[] = [
    {
      key: 'title',
      header: 'Album / track',
      mobile: 'title',
      cell: row => {
        if (row.kind === 'album') {
          const a = row.album;
          const open = expanded.has(a.lidarrAlbumId);
          const i = expandedIds.indexOf(a.lidarrAlbumId);
          const detail = open ? albumDetails[i] : undefined;
          const loading = !!detail?.isLoading;
          return (
            <div className={c.albumCell}>
              <span className={c.expander}>
                {loading ? <Loader size={14} aria-label="Loading tracks" /> : detail?.isError ? (
                  <IconButton
                    icon={faRotateRight}
                    label={`Couldn't load tracks: ${errorMessage(detail.error)}. Retry`}
                    size="sm"
                    tabIndex={-1}
                    onClick={() => void detail.refetch()}
                  />
                ) : (
                  <IconButton
                    icon={faChevronRight}
                    label={open ? 'Collapse tracks' : 'Show missing tracks'}
                    size="sm"
                    aria-expanded={open}
                    className={c.chevron}
                    data-open={open || undefined}
                    tabIndex={-1}
                    onClick={() => toggleExpand(a.lidarrAlbumId)}
                  />
                )}
              </span>
              <TitleCell
                title={a.title}
                href={`/albums/${a.lidarrAlbumId}`}
                art={{ src: a.cover, kind: 'album', name: a.title }}
                secondary={[a.artistName, a.year || null].filter(Boolean).join(' · ')}
              />
            </div>
          );
        }
        const t = row.track;
        const no = t.trackNumber ? (multiDisc(t.albumId) ? `${t.discNumber}·${t.trackNumber}` : String(t.trackNumber)) : '–';
        return (
          <div className={c.trackCell}>
            <span className={c.trackNo}>{no}</span>
            <span className={c.trackTitle} title={t.title}>{t.title}</span>
            <InstrumentalHintTag track={t} />
          </div>
        );
      },
    },
    {
      key: 'time', header: 'Time', width: 64, align: 'right', priority: 2, mobile: 'hidden',
      cell: row => (row.kind === 'track' ? fmtDuration(row.track.duration) : ''),
    },
    {
      key: 'status', header: 'Lyrics / attempts', width: showInspector ? 156 : 184, priority: 1, mobile: 'meta',
      cell: row => (row.kind === 'album'
        ? albumStatus(row.album)
        : <span className={c.trackMeta}><Attempts after={row.track.lyrics_retry_after} count={row.track.lyrics_retry_count} /></span>),
    },
  ];

  const trackColumns: Column<Row>[] = [
    {
      key: 'track', header: 'Track', mobile: 'title',
      cell: row => (row.kind === 'track'
        ? (
          <TitleCell
            title={row.track.title}
            secondary={isMobile ? undefined : fileName(row.track.path) || undefined}
            extra={<InstrumentalHintTag track={row.track} />}
          />
        )
        : null),
    },
    {
      key: 'artist', header: 'Artist', width: showInspector ? 130 : 150, priority: 2,
      cell: row => (row.kind === 'track' ? <span className={c.cellText}>{row.track.artistName || '—'}</span> : null),
    },
    {
      key: 'album', header: 'Album', width: 110, priority: 3,
      cell: row => (row.kind === 'track'
        ? <Link className="ly-link" to={`/albums/${row.track.albumId}`}>Open album</Link>
        : null),
    },
    {
      key: 'time', header: 'Time', width: 64, align: 'right', priority: 2,
      cell: row => (row.kind === 'track' ? fmtDuration(row.track.duration) : ''),
    },
    {
      key: 'attempts', header: 'Attempts', width: showInspector ? 156 : 180, priority: 1, mobile: 'status',
      cell: row => (row.kind === 'track' ? <Attempts after={row.track.lyrics_retry_after} count={row.track.lyrics_retry_count} /> : null),
    },
  ];

  /* ---------- selection → albums ---------- */
  const selectedAlbumIds = useMemo(() => {
    const ids = new Set<number>();
    selected.forEach(id => {
      const r = rowById.get(id);
      if (r?.kind === 'album') ids.add(r.album.lidarrAlbumId);
      else if (r?.kind === 'track') ids.add(r.track.albumId);
    });
    return [...ids];
  }, [selected, rowById]);
  const selectedKinds = useMemo(() => new Set([...selected].map(id => rowById.get(id)?.kind)), [selected, rowById]);
  const bulkNoun = selectedKinds.size > 1 ? 'item' : (selectedKinds.has('album') ? 'album' : 'track');

  /* ---------- empty / error ---------- */
  const zero = (stats?.missing_lyrics ?? 0) === 0 && !q;
  const empty = q ? (
    <EmptyState
      size="sm"
      icon={faMagnifyingGlass}
      title={`No ${byAlbum ? 'albums' : 'tracks'} match “${q}”`}
      message={byAlbum ? 'The filter matches album titles.' : 'The filter matches track titles.'}
      action={<Button variant="default" onClick={() => patchUrl({ q: ['', ''], page: [1, 1] })}>Clear filter</Button>}
    />
  ) : (
    <EmptyState
      size="sm"
      tone={zero ? 'success' : 'neutral'}
      icon={faAlignLeft}
      title="Every track has lyrics"
      message="Nothing is missing. New tracks from Lidarr show up here until the downloader finds their lyrics."
    />
  );

  const table = (
    <div ref={tableRef} className={c.list}>
      <DataTable<Row>
        aria-label={byAlbum ? 'Albums with missing lyrics' : 'Tracks with missing lyrics'}
        columns={byAlbum ? albumColumns : trackColumns}
        rows={rows}
        getRowId={r => r.id}
        getRowLabel={r => (r.kind === 'album' ? r.album.title : r.track.title)}
        noun={byAlbum ? 'album' : 'track'}
        loading={activeQuery.isLoading}
        refetching={activeQuery.isFetching && activeQuery.isPlaceholderData}
        error={activeQuery.isError ? { message: errorMessage(activeQuery.error), onRetry: () => activeQuery.refetch() } : null}
        empty={empty}
        selection={{ selected, onChange: setSelected }}
        cursor={{ id: cursor, onChange: setCursor }}
        onOpen={row => {
          if (row.kind === 'album') toggleExpand(row.album.lidarrAlbumId);
          else requestSearch(toInspectorTrack(row));
        }}
        rowActions={row => (row.kind === 'album'
          ? (
            <IconButton
              icon={faDownload}
              label="Download lyrics for this album"
              loading={downloading.has(row.album.lidarrAlbumId)}
              onClick={() => downloadAlbums.mutate([row.album.lidarrAlbumId])}
            />
          )
          : trackActions(row))}
        rowActionsWidth={124}
        rowProps={row => ({ 'data-kind': row.kind, 'data-row-id': row.id })}
        breakpoints={showInspector ? { 2: 600, 3: 820 } : undefined}
        footer={total > 0 && (
          <Pager
            page={page}
            pageSize={size}
            total={total}
            onPage={p => patchUrl({ page: [p, 1] })}
            pageSizeOptions={PAGE_SIZES}
            onPageSize={n => patchUrl({ size: [n, DEFAULT_PAGE_SIZE], page: [1, 1] })}
            noun={byAlbum ? 'albums' : 'tracks'}
          />
        )}
        className={byAlbum ? c.table : undefined}
      />
    </div>
  );

  return (
    <>
      <FilterBar
        search={{
          value: q,
          onChange: v => patchUrl({ q: [v, ''], page: [1, 1] }),
          placeholder: byAlbum ? 'Filter by album title…' : 'Filter by track title…',
        }}
        filters={(
          <SegmentedControl
            aria-label="Group"
            value={byAlbum ? 'album' : 'track'}
            onChange={v => patchUrl({ group: [v, 'album'], page: [1, 1] })}
            data={[{ value: 'album', label: 'By album' }, { value: 'track', label: 'By track' }]}
          />
        )}
        right={showInspector ? (
          <Tooltip label="Search the next track automatically when the cursor moves (one request at a time, pauses after 3 empty results)">
            <Switch
              label="Auto-search next"
              checked={autoSearch}
              onChange={e => toggleAuto(e.currentTarget.checked)}
              labelPosition="left"
            />
          </Tooltip>
        ) : null}
      />

      {showInspector ? (
        <div className={c.workspace}>
          {table}
          <InspectorColumn className={c.inspectorCol}>
            <Inspector
              ref={inspectorRef}
              track={cursorTrack}
              album={cursorRow?.kind === 'album' ? {
                album: cursorRow.album,
                counts: counts.get(cursorRow.album.lidarrAlbumId),
              } : null}
              requested={!!cursorTrack && requested.has(cursorTrack.id)}
              onRequestSearch={id => setRequested(prev => (prev.has(id) ? prev : new Set(prev).add(id)))}
              onApplied={onApplied}
              onEdit={openEditor}
              onOpenSearch={openSearch}
              onSkip={t => void skip(t)}
              onDownloadAlbum={a => downloadAlbums.mutate([a.lidarrAlbumId])}
              downloadingAlbum={cursorRow?.kind === 'album' && downloading.has(cursorRow.album.lidarrAlbumId)}
            />
          </InspectorColumn>
        </div>
      ) : table}

      <BulkBar count={selected.size} noun={bulkNoun} onClear={() => setSelected(new Set())}>
        <Button
          variant="filled"
          size="sm"
          leftSection={<FontAwesomeIcon icon={faDownload} />}
          loading={downloadAlbums.isPending}
          disabled={selectedAlbumIds.length === 0}
          onClick={() => downloadAlbums.mutate(selectedAlbumIds)}
          className={c.bulkButton}
        >
          {isMobile ? 'Download lyrics' : `Download lyrics for ${plural(selectedAlbumIds.length, 'album')}`}
        </Button>
      </BulkBar>

      {searchFor && (
        <LyricsSearchModal
          trackId={searchFor.id}
          trackTitle={searchFor.title}
          albumId={searchFor.albumId}
          artistName={searchFor.artistName}
          albumTitle={searchFor.albumTitle}
          opened={!!searchFor}
          onApplied={() => onModalApplied(searchFor.id)}
          onClose={() => { setSearchFor(null); restoreFocus(); }}
        />
      )}
      {editorFor && (
        <LyricsEditorModal
          trackId={editorFor.id}
          trackTitle={editorFor.title}
          albumId={editorFor.albumId}
          artistName={editorFor.artistName}
          albumTitle={editorFor.albumTitle}
          opened={!!editorFor}
          onClose={() => { setEditorFor(null); restoreFocus(); }}
        />
      )}
    </>
  );
}

/** Sticky inspector column sized to the visible viewport. */
function InspectorColumn({ className, children }: { className: string; children: React.ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  useFitToViewport(ref);
  return <aside ref={ref} className={className} data-wanted-inspector="">{children}</aside>;
}
