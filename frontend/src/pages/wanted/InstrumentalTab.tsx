import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query';
import { Button, Menu, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faCheck, faCircleCheck, faEllipsis, faEyeSlash, faMagnifyingGlass, faMicrophone, faMusic, faRepeat,
} from '@fortawesome/free-solid-svg-icons';
import { getWantedInstrumental } from '../../api';
import { errorMessage } from '../../notify';
import { absTime, duration as fmtDuration, num, plural, relTime } from '../../format';
import { useUrlPatch, useUrlState } from '../../hooks/useUrlState';
import {
  Banner, BulkBar, DataTable, EmptyState, FilterBar, FilterPill, IconButton, Pager, StatusDot, Tag, TitleCell,
  ViewTabs, useConfirm, type Column,
} from '../../components/ui';
import { fetchAllIds } from '../../components/library/actions';
import { InstrumentalEvidence, InstrumentalHintTag } from '../../components/instrumental/InstrumentalEvidence';
import {
  CLEARED_WHY_OPTIONS, evidenceSummary, prevOf, REASON_LABELS, statusOptions, whyLabel,
} from '../../components/instrumental/model';
import { useInstrumentalActions } from '../../components/instrumental/useInstrumentalActions';
import { providerName } from '../../components/lyrics/providers';
import type { InstrumentalRow, InstrumentalView } from '../../types';
import { DEFAULT_PAGE_SIZE, PAGE_SIZES, closeRowMenu, type WantedStats } from './shared';
import c from './Wanted.module.css';

type SubView = Exclude<InstrumentalView, 'all'>;

const VIEWS: { value: SubView; label: string; hint: string }[] = [
  { value: 'flagged', label: 'Flagged', hint: 'Tracks marked instrumental, with the reason.' },
  { value: 'likely', label: 'Likely instrumental', hint: 'Some sources say there are no lyrics, but not enough to be sure: these stay missing.' },
  { value: 'conflict', label: 'Conflicting evidence', hint: 'An instrumental-looking title, but the track has lyrics.' },
  { value: 'legacy', label: 'From older versions', hint: 'Flagged by an older Lyrarr without a recorded reason.' },
  { value: 'cleared', label: 'Cleared after upgrade', hint: 'Old flags Lyrarr removed so the tracks are searched again.' },
];

const REASON_OPTIONS = (['title', 'consensus', 'user'] as const).map(value => ({ value, label: REASON_LABELS[value] }));

function isSubView(v: string): v is SubView {
  return VIEWS.some(x => x.value === v);
}

/**
 * Wanted › Instrumental review (PLAN §2.4, §5.3): flagged (by reason), likely
 * (hints), conflicting evidence, legacy-unverified and "Cleared after upgrade",
 * the undo surface for the bulk clears of the post-upgrade library check.
 */
export function InstrumentalTab({ stats }: { stats?: WantedStats }) {
  const patchUrl = useUrlPatch();
  const confirm = useConfirm();
  const isMobile = useMediaQuery('(max-width: 767px)') ?? false;
  const actions = useInstrumentalActions();
  const [rawView] = useUrlState<string>('iview', 'flagged');
  const view: SubView = isSubView(rawView) ? rawView : 'flagged';
  const [q] = useUrlState<string>('q', '');
  const [page] = useUrlState('page', 1);
  const [size] = useUrlState('size', DEFAULT_PAGE_SIZE);
  const [reason] = useUrlState<string>('reason', '');
  const [why] = useUrlState<string>('why', '');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<number | null>(null);
  const [selectingAll, setSelectingAll] = useState(false);

  const cleared = view === 'cleared';
  const filter = useMemo(() => ({
    view,
    search: q || undefined,
    reason: view === 'flagged' && reason ? reason : undefined,
    why: cleared && why ? why : undefined,
  }), [view, q, reason, why, cleared]);

  useEffect(() => { setSelected(new Set()); setExpanded(null); }, [view, q, page, size, reason, why]);

  const query = useQuery({
    queryKey: ['wanted-instrumental', filter, page, size],
    queryFn: () => getWantedInstrumental({ ...filter, page, pageSize: size }),
    placeholderData: keepPreviousData,
  });
  const rows = query.data?.data ?? [];
  const total = query.data?.total ?? 0;

  // Counts per sub-view (one pageSize=1 request each; stats has likely + cleared).
  const counts = useQueries({
    queries: VIEWS.map(v => ({
      queryKey: ['wanted-instrumental', 'count', v.value],
      queryFn: () => getWantedInstrumental({ view: v.value, page: 1, pageSize: 1 }),
      staleTime: 30_000,
    })),
  });
  const countOf = (v: SubView, i: number): number | undefined => {
    if (v === 'likely' && stats?.likely_instrumental !== undefined) return stats.likely_instrumental;
    if (v === 'cleared' && stats?.instrumental_cleared !== undefined) return stats.instrumental_cleared;
    return counts[i]?.data?.total;
  };

  const ids = [...selected];
  const bulkBusy = actions.isBusy('bulk-confirm') || actions.isBusy('bulk-not_instrumental')
    || actions.isBusy('bulk-recheck') || actions.isBusy('bulk-dismiss_cleared');

  const selectAll = useCallback(async () => {
    setSelectingAll(true);
    try {
      setSelected(await fetchAllIds(
        (p, ps) => getWantedInstrumental({ ...filter, page: p, pageSize: ps }),
        (r: InstrumentalRow) => r.lidarrTrackId,
      ));
    } finally {
      setSelectingAll(false);
    }
  }, [filter]);

  /* ---------- actions ---------- */
  const confirmInstrumental = async (trackIds: number[]) => {
    const ok = await confirm({
      title: trackIds.length === 1 ? 'Confirm instrumental?' : `Confirm ${num(trackIds.length)} tracks as instrumental?`,
      message: (
        <>
          {trackIds.length === 1 ? 'This track is' : `These ${num(trackIds.length)} tracks are`} marked instrumental and
          never searched for lyrics again; their albums count them as done. Existing automatic lyrics files are archived
          to version history first. Lyrics you saved yourself are left alone (you're asked separately).
        </>
      ),
      confirmLabel: 'Confirm instrumental',
    });
    if (!ok) return;
    const r = await actions.bulk(trackIds, 'confirm', {
      successTitle: 'Confirmed instrumental',
      successMessage: n => `${plural(n, 'track')} marked instrumental`,
    });
    if (r) setSelected(new Set());
  };

  const notInstrumental = async (trackIds: number[], search: boolean) => {
    const r = await actions.bulk(trackIds, 'not_instrumental', {
      search,
      successTitle: search ? 'Searching lyrics' : 'Kept as vocal',
      successMessage: n => (search
        ? `${plural(n, 'track')} marked as not instrumental; a lyrics search was queued`
        : `${plural(n, 'track')} won't be flagged instrumental automatically again`),
    });
    if (r) setSelected(new Set());
  };

  const recheck = async (trackIds: number[]) => {
    const r = await actions.bulk(trackIds, 'recheck', {
      successTitle: 'Re-check queued',
      successMessage: n => `${plural(n, 'track')} searched again on the next run; flagged again only with fresh evidence`,
    });
    if (r) setSelected(new Set());
  };

  const dismiss = async (trackIds: number[]) => {
    const r = await actions.bulk(trackIds, 'dismiss_cleared', {
      successTitle: 'Dismissed',
      successMessage: n => `${plural(n, 'track')} removed from this list (status unchanged)`,
    });
    if (r) setSelected(new Set());
  };

  /* ---------- columns ---------- */
  const trackCell = (r: InstrumentalRow) => (
    <TitleCell
      title={r.title}
      href={`/albums/${r.albumId}`}
      secondary={[r.artistName, r.albumTitle].filter(Boolean).join(' · ') || undefined}
    />
  );

  const reviewColumns: Column<InstrumentalRow>[] = [
    { key: 'track', header: 'Track', mobile: 'title', cell: trackCell },
    {
      key: 'state', header: view === 'flagged' || view === 'legacy' ? 'Reason' : 'Status', width: 170, priority: 1, mobile: 'status',
      cell: r => (
        <span className={c.instrState}>
          {/* Flagged rows: the glyph carries the status (tooltip says why), the text says the reason. */}
          <StatusDot
            domain="trackLyrics"
            status={r.lyrics_status}
            synced={r.is_synced}
            options={statusOptions(r)}
            hideLabel={r.lyrics_status === 'instrumental'}
          />
          {r.lyrics_status === 'instrumental'
            ? <span className={c.instrReasonText}>{REASON_LABELS[statusOptions(r)?.reason ?? ''] ?? 'Instrumental'}</span>
            : <InstrumentalHintTag track={r} />}
        </span>
      ),
    },
    {
      key: 'evidence', header: 'Evidence', priority: 2, mobile: 'meta',
      cell: r => {
        const text = evidenceSummary(r);
        return text ? <span className={c.cellText} title={text}>{text}</span> : <span className={c.muted}>None recorded</span>;
      },
    },
    {
      key: 'time', header: 'Time', width: 64, align: 'right', priority: 3, mobile: 'hidden',
      cell: r => fmtDuration(r.duration),
    },
  ];

  const clearedColumns: Column<InstrumentalRow>[] = [
    { key: 'track', header: 'Track', mobile: 'title', cell: trackCell },
    {
      key: 'album', header: 'Album', width: 150, priority: 2, mobile: 'meta',
      // Albums that turned from complete to partial when the flag was cleared show here.
      cell: r => (r.albumLyricsStatus
        ? <StatusDot domain="albumLyrics" status={r.albumLyricsStatus} />
        : <span className={c.muted}>—</span>),
    },
    {
      key: 'cleared', header: 'Cleared', width: 110, priority: 3, mobile: 'hidden',
      cell: r => {
        const at = prevOf(r)?.at;
        return at ? <Tooltip label={absTime(at)}><span className={c.cellText} tabIndex={-1}>{relTime(at)}</span></Tooltip> : '—';
      },
    },
    {
      key: 'why', header: 'Why', width: 190, priority: 2, mobile: 'meta',
      cell: r => <Tag>{whyLabel(prevOf(r)?.why)}</Tag>,
    },
    {
      key: 'now', header: 'Now', width: 170, priority: 1, mobile: 'status',
      cell: r => (
        <span className={c.instrState}>
          <StatusDot domain="trackLyrics" status={r.lyrics_status} synced={r.is_synced} options={statusOptions(r)} />
          {r.lyrics_provider && (r.lyrics_status === 'available' || r.lyrics_status === 'manual') && (
            <span className={c.instrReason}>{providerName(r.lyrics_provider)}</span>
          )}
        </span>
      ),
    },
  ];

  const target = (r: InstrumentalRow) => ({ id: r.lidarrTrackId, title: r.title, hasLyrics: r.lyrics_status === 'available' || r.lyrics_status === 'manual' });

  const rowActions = (r: InstrumentalRow) => {
    if (cleared) {
      return (
        <>
          <IconButton icon={faMusic} label="Confirm instrumental" onClick={e => { closeRowMenu(e); void confirmInstrumental([r.lidarrTrackId]); }} />
          <IconButton
            icon={faMicrophone}
            label="Keep as vocal"
            loading={actions.isBusy(`vocal-${r.lidarrTrackId}`)}
            onClick={() => void actions.notInstrumental(target(r), { search: false })}
          />
          <IconButton icon={faEyeSlash} label="Dismiss from this list" onClick={() => void dismiss([r.lidarrTrackId])} />
        </>
      );
    }
    return (
      <>
        <IconButton
          icon={faMicrophone}
          label="Not instrumental — search again"
          loading={actions.isBusy(`vocal-${r.lidarrTrackId}`)}
          onClick={() => void actions.notInstrumental(target(r))}
        />
        <Menu position="bottom-end" withinPortal>
          <Menu.Target>
            <IconButton icon={faEllipsis} label={`More actions for ${r.title}`} />
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item leftSection={<FontAwesomeIcon icon={faMusic} />} onClick={() => void confirmInstrumental([r.lidarrTrackId])}>
              Confirm instrumental
            </Menu.Item>
            <Menu.Item leftSection={<FontAwesomeIcon icon={faRepeat} />} onClick={() => void actions.recheck(target(r))}>
              Re-check
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </>
    );
  };

  /* ---------- empty / filters ---------- */
  const filtered = !!(q || (view === 'flagged' && reason) || (cleared && why));
  const empty = filtered ? (
    <EmptyState
      size="sm"
      icon={faMagnifyingGlass}
      title="No tracks match these filters"
      action={<Button variant="default" onClick={() => patchUrl({ q: ['', ''], reason: ['', ''], why: ['', ''], page: [1, 1] })}>Clear filters</Button>}
    />
  ) : (
    <EmptyState
      size="sm"
      tone="success"
      icon={faCircleCheck}
      title="Nothing left to review"
      message={VIEWS.find(v => v.value === view)?.hint}
    />
  );

  const viewTabs = VIEWS.map((v, i) => ({ value: v.value, label: v.label, count: countOf(v.value, i) }));

  return (
    <>
      <ViewTabs
        aria-label="Instrumental review views"
        tabs={viewTabs}
        value={view}
        onChange={v => patchUrl({ iview: [v, 'flagged'], page: [1, 1], reason: ['', ''], why: ['', ''] })}
        className={c.subTabs}
      />

      {cleared && (
        <Banner
          tone="info"
          title="Older versions flagged these tracks as instrumental without recording why."
          message="Lyrarr cleared the flags and searches them again. Confirm the ones that really are instrumental; their albums count as complete again."
        />
      )}

      <FilterBar
        search={{ value: q, onChange: v => patchUrl({ q: [v, ''], page: [1, 1] }), placeholder: 'Filter by track title…' }}
        filters={view === 'flagged' ? (
          <FilterPill label="Reason" value={reason || null} options={REASON_OPTIONS} onChange={v => patchUrl({ reason: [v ?? '', ''], page: [1, 1] })} />
        ) : cleared ? (
          <FilterPill label="Why" value={why || null} options={CLEARED_WHY_OPTIONS} onChange={v => patchUrl({ why: [v ?? '', ''], page: [1, 1] })} />
        ) : undefined}
      />

      <DataTable<InstrumentalRow>
        aria-label={VIEWS.find(v => v.value === view)?.label ?? 'Instrumental review'}
        columns={cleared ? clearedColumns : reviewColumns}
        rows={rows}
        getRowId={r => r.lidarrTrackId}
        getRowLabel={r => r.title}
        noun="track"
        loading={query.isLoading}
        refetching={query.isFetching && query.isPlaceholderData}
        error={query.isError ? { message: errorMessage(query.error), onRetry: () => query.refetch() } : null}
        empty={empty}
        selection={{ selected, onChange: setSelected }}
        selectAllMatching={{ total, onSelectAll: selectAll, busy: selectingAll }}
        expandable={{
          isExpanded: r => expanded === r.lidarrTrackId,
          toggle: r => setExpanded(prev => (prev === r.lidarrTrackId ? null : r.lidarrTrackId)),
          render: r => (
            <div className={c.instrExpand}>
              <div className="ly-overline">Why instrumental</div>
              <InstrumentalEvidence track={r} compact columns={isMobile ? 1 : 2} />
              <Link className="ly-link" to={`/albums/${r.albumId}`}>Open album</Link>
            </div>
          ),
        }}
        onOpen={r => setExpanded(prev => (prev === r.lidarrTrackId ? null : r.lidarrTrackId))}
        rowActions={rowActions}
        rowActionsWidth={cleared ? 120 : 84}
        breakpoints={{ 2: 700, 3: 900 }}
        footer={total > 0 && (
          <Pager
            page={page}
            pageSize={size}
            total={total}
            onPage={p => patchUrl({ page: [p, 1] })}
            pageSizeOptions={PAGE_SIZES}
            onPageSize={n => patchUrl({ size: [n, DEFAULT_PAGE_SIZE], page: [1, 1] })}
            noun="tracks"
          />
        )}
      />

      <BulkBar count={selected.size} noun="track" onClear={() => setSelected(new Set())}>
        {cleared ? (
          <>
            {/* Phones: secondaries as labelled icon buttons so the primary keeps its words. */}
            {isMobile ? (
              <>
                <IconButton icon={faEyeSlash} label="Dismiss" variant="default" size="input-sm" disabled={bulkBusy} onClick={() => void dismiss(ids)} />
                <IconButton icon={faMicrophone} label="Keep as vocal" variant="default" size="input-sm" disabled={bulkBusy} onClick={() => void notInstrumental(ids, false)} />
              </>
            ) : (
              <>
                <Button size="sm" variant="default" leftSection={<FontAwesomeIcon icon={faEyeSlash} />} disabled={bulkBusy} onClick={() => void dismiss(ids)}>
                  Dismiss
                </Button>
                <Button size="sm" variant="default" leftSection={<FontAwesomeIcon icon={faMicrophone} />} disabled={bulkBusy} onClick={() => void notInstrumental(ids, false)}>
                  Keep as vocal
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="filled"
              className={c.bulkButton}
              leftSection={<FontAwesomeIcon icon={faCheck} />}
              loading={actions.isBusy('bulk-confirm')}
              disabled={bulkBusy && !actions.isBusy('bulk-confirm')}
              onClick={() => void confirmInstrumental(ids)}
            >
              {isMobile ? 'Confirm' : 'Confirm instrumental'}
            </Button>
          </>
        ) : (
          <>
            {isMobile ? (
              <>
                <IconButton icon={faRepeat} label="Re-check" variant="default" size="input-sm" disabled={bulkBusy} onClick={() => void recheck(ids)} />
                <IconButton
                  icon={faMusic}
                  label="Confirm instrumental"
                  variant="default"
                  size="input-sm"
                  loading={actions.isBusy('bulk-confirm')}
                  disabled={bulkBusy && !actions.isBusy('bulk-confirm')}
                  onClick={() => void confirmInstrumental(ids)}
                />
              </>
            ) : (
              <>
                <Button size="sm" variant="default" leftSection={<FontAwesomeIcon icon={faRepeat} />} disabled={bulkBusy} onClick={() => void recheck(ids)}>
                  Re-check
                </Button>
                <Button
                  size="sm"
                  variant={view === 'likely' ? 'filled' : 'default'}
                  leftSection={<FontAwesomeIcon icon={faMusic} />}
                  loading={actions.isBusy('bulk-confirm')}
                  disabled={bulkBusy && !actions.isBusy('bulk-confirm')}
                  onClick={() => void confirmInstrumental(ids)}
                >
                  Confirm instrumental
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant={view === 'likely' && !isMobile ? 'default' : 'filled'}
              className={c.bulkButton}
              leftSection={<FontAwesomeIcon icon={faMicrophone} />}
              loading={actions.isBusy('bulk-not_instrumental')}
              disabled={bulkBusy && !actions.isBusy('bulk-not_instrumental')}
              onClick={() => void notInstrumental(ids, true)}
            >
              {isMobile ? 'Not instrumental' : 'Not instrumental — search again'}
            </Button>
          </>
        )}
      </BulkBar>
    </>
  );
}
