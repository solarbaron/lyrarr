import { useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAlignLeft, faClockRotateLeft, faDownload, faFilterCircleXmark, faImage, faFile } from '@fortawesome/free-solid-svg-icons';
import { getHistory, runTask } from '../api';
import { notifyError } from '../notify';
import type { HistoryEntry } from '../types';
import {
  DataTable, EmptyState, FilterBar, FilterPill, PageHeader, Pager, Tag, TitleCell, type Column, type TagProps,
} from '../components/ui';
import { useUrlPatch, useUrlState } from '../hooks/useUrlState';
import { absTime } from '../format';
import { providerLabel } from './system/providers';
import c from './History.module.css';

const PAGE_SIZE = 50;

/** One label and tone per history action code (1–7), the single vocabulary for this page. */
const ACTIONS: Record<number, { label: string; tone: NonNullable<TagProps['tone']> }> = {
  1: { label: 'Downloaded', tone: 'success' },
  2: { label: 'Upgraded', tone: 'info' },
  3: { label: 'Deleted', tone: 'danger' },
  4: { label: 'Manual', tone: 'neutral' },
  5: { label: 'Published', tone: 'info' },
  // Instrumental flags, clears and "likely instrumental" hints.
  6: { label: 'Classified', tone: 'neutral' },
  // Library check repairs and re-search requests.
  7: { label: 'Audit repair', tone: 'warning' },
};

export function historyActionModel(action: number): { label: string; tone: NonNullable<TagProps['tone']> } {
  return ACTIONS[action] ?? { label: 'Unknown', tone: 'neutral' };
}

const TYPE_ICON: Record<string, typeof faImage> = { cover: faImage, lyrics: faAlignLeft };

function typeLabel(type: string | null | undefined): string {
  if (!type) return '—';
  return type.charAt(0).toUpperCase() + type.slice(1);
}

const TIME = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
const DAY = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
const DAY_YEAR = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });

function toDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dayKey(d: Date | null): string {
  return d ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` : 'unknown';
}

/** "Today", "Yesterday", "Mon 12 Oct" (with the year when it isn't this year). */
function dayLabel(d: Date | null): string {
  if (!d) return 'Unknown date';
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const that = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((today - that) / 86_400_000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.getFullYear() === now.getFullYear() ? DAY.format(d) : DAY_YEAR.format(d);
}

function itemHref(h: HistoryEntry): string | undefined {
  if (h.lidarrAlbumId) return `/albums/${h.lidarrAlbumId}`;
  if (h.lidarrArtistId) return `/artists/${h.lidarrArtistId}`;
  return undefined;
}

function countBy<T>(rows: T[], key: (row: T) => string | null | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const row of rows) {
    const k = key(row);
    if (k) out.set(k, (out.get(k) ?? 0) + 1);
  }
  return out;
}

export default function HistoryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: history = [], isLoading, isError, error, refetch, isFetching } = useQuery({ queryKey: ['history'], queryFn: getHistory });

  const [q] = useUrlState('q', '');
  const [type] = useUrlState('type', '');
  const [action] = useUrlState('action', '');
  const [provider] = useUrlState('provider', '');
  const [page, setPage] = useUrlState<number>('page', 1);
  const patchUrl = useUrlPatch();

  const downloadMutation = useMutation({
    mutationFn: () => runTask('download_metadata'),
    onSuccess: () => {
      notifications.show({ title: 'Download started', message: 'Fetching missing covers and lyrics in the background.' });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
    onError: (err) => notifyError('Couldn’t start the download', err),
  });

  // Newest first, so day groups read top-down.
  const sorted = useMemo(() => [...history].sort((a, b) => {
    const ta = toDate(a.timestamp)?.getTime() ?? 0;
    const tb = toDate(b.timestamp)?.getTime() ?? 0;
    return tb - ta;
  }), [history]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return sorted.filter(h =>
      (!needle || (h.description ?? '').toLowerCase().includes(needle)) &&
      (!type || h.metadata_type === type) &&
      (!action || String(h.action) === action) &&
      (!provider || h.provider === provider));
  }, [sorted, q, type, action, provider]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(Math.max(1, page), pages);
  const rows = useMemo(() => filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE), [filtered, current]);

  // Day header before the first entry of each day on this page.
  const groupStarts = useMemo(() => {
    const starts = new Map<number, string>();
    let last: string | null = null;
    for (const h of rows) {
      const d = toDate(h.timestamp);
      const key = dayKey(d);
      if (key !== last) starts.set(h.id, dayLabel(d));
      last = key;
    }
    return starts;
  }, [rows]);

  const typeOptions = useMemo(() => {
    const counts = countBy(history, h => h.metadata_type);
    return Array.from(counts, ([value, count]) => ({ value, label: typeLabel(value), count }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [history]);
  const actionOptions = useMemo(() => {
    const counts = countBy(history, h => String(h.action));
    return Object.entries(ACTIONS).map(([value, { label }]) => ({ value, label, count: counts.get(value) ?? 0 }));
  }, [history]);
  const providerOptions = useMemo(() => {
    const counts = countBy(history, h => h.provider);
    return Array.from(counts, ([value, count]) => ({ value, label: providerLabel(value), count }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [history]);
  // Nothing to filter by until there is some history.
  const noHistory = !isLoading && !isError && history.length === 0;

  const setFilter = (key: 'type' | 'action' | 'provider', value: string | null) => {
    patchUrl({ [key]: [value ?? '', ''], page: [1, 1] });
  };
  const filtersActive = !!(q || type || action || provider);
  const clearFilters = () => patchUrl({ q: ['', ''], type: ['', ''], action: ['', ''], provider: ['', ''], page: [1, 1] });

  const columns: Column<HistoryEntry>[] = [
    {
      key: 'time',
      header: 'Time',
      width: 76,
      mobile: 'meta',
      skeleton: 'num',
      cell: h => {
        const d = toDate(h.timestamp);
        if (!d) return <span className={c.muted}>—</span>;
        return (
          <Tooltip label={absTime(d)} openDelay={300}>
            <time className={c.time} dateTime={d.toISOString()} tabIndex={-1}>{TIME.format(d)}</time>
          </Tooltip>
        );
      },
    },
    {
      key: 'action',
      header: 'Action',
      width: 124,
      mobile: 'status',
      skeleton: 'chip',
      cell: h => {
        const m = historyActionModel(h.action);
        return <Tag tone={m.tone}>{m.label}</Tag>;
      },
    },
    {
      key: 'type',
      header: 'Type',
      width: 104,
      mobile: 'meta',
      cell: h => (
        <span className={c.type}>
          <FontAwesomeIcon icon={TYPE_ICON[h.metadata_type] ?? faFile} className={c.typeIcon} aria-hidden="true" />
          {typeLabel(h.metadata_type)}
        </span>
      ),
    },
    {
      key: 'item',
      header: 'Item',
      mobile: 'title',
      skeleton: 'title',
      cell: h => <TitleCell title={h.description || '—'} href={itemHref(h)} />,
    },
    {
      key: 'provider',
      header: 'Provider',
      width: 128,
      mobile: 'meta',
      cell: h => <span className={c.text2}>{h.provider ? providerLabel(h.provider) : '—'}</span>,
    },
    {
      key: 'path',
      header: 'Path',
      width: '28%',
      priority: 3,
      mobile: 'hidden',
      cell: h => h.metadata_path ? (
        <Tooltip label={<span className={c.pathTip}>{h.metadata_path}</span>} openDelay={300} multiline maw={560}>
          <span className={c.path}><bdi>{h.metadata_path}</bdi></span>
        </Tooltip>
      ) : <span className={c.muted}>—</span>,
    },
  ];

  const onOpen = useCallback((h: HistoryEntry) => {
    const href = itemHref(h);
    if (href) navigate(href);
  }, [navigate]);

  const empty = history.length === 0 ? (
    <EmptyState
      size="sm"
      icon={faClockRotateLeft}
      title="No history yet"
      message="Downloads, uploads and edits appear here as they happen."
      action={(
        <Button
          variant="default"
          leftSection={<FontAwesomeIcon icon={faDownload} />}
          onClick={() => downloadMutation.mutate()}
          loading={downloadMutation.isPending}
        >
          Download missing now
        </Button>
      )}
    />
  ) : (
    <EmptyState
      size="sm"
      icon={faFilterCircleXmark}
      title="No matching entries"
      message="Try a different search or clear the filters."
      action={<Button variant="default" onClick={clearFilters}>Clear filters</Button>}
    />
  );

  return (
    <div className="ly-page">
      <PageHeader
        title="History"
        count={isLoading ? undefined : history.length}
        subtitle="Downloads, uploads and edits"
      />

      <FilterBar
        className={c.filters}
        search={{
          value: q,
          onChange: v => patchUrl({ q: [v, ''], page: [1, 1] }),
          placeholder: 'Filter by description…',
        }}
        // Pill options come from the entries, so they appear with the data (an active
        // filter from the URL stays visible while loading).
        filters={noHistory || (isLoading && !filtersActive) ? undefined : (
          <>
            <FilterPill label="Type" value={type || null} options={typeOptions} onChange={v => setFilter('type', v)} />
            <FilterPill label="Action" value={action || null} options={actionOptions} onChange={v => setFilter('action', v)} />
            <FilterPill label="Provider" value={provider || null} options={providerOptions} onChange={v => setFilter('provider', v)} />
          </>
        )}
        right={filtersActive ? <Button variant="subtle" color="gray" size="xs" onClick={clearFilters}>Clear filters</Button> : undefined}
      />

      <DataTable<HistoryEntry>
        aria-label="History"
        noun="entry"
        columns={columns}
        rows={rows}
        getRowId={h => h.id}
        loading={isLoading}
        refetching={isFetching && !isLoading}
        error={isError ? { title: 'Couldn’t load history', message: (error as Error)?.message, onRetry: () => refetch() } : null}
        empty={empty}
        onOpen={onOpen}
        isOpenable={h => !!itemHref(h)}
        renderGroupHeader={h => {
          const label = groupStarts.get(h.id);
          return label ? <span className={c.day}>{label}</span> : null;
        }}
        footer={filtered.length > PAGE_SIZE ? (
          <Pager
            page={current}
            pageSize={PAGE_SIZE}
            total={filtered.length}
            onPage={p => setPage(p, { replace: false })}
            noun="entries"
          />
        ) : undefined}
      />
    </div>
  );
}
