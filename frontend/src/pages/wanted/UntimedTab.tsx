import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowUpRightDots, faClock, faMagnifyingGlass, faWandMagicSparkles } from '@fortawesome/free-solid-svg-icons';
import { batchSyncGenerate, getWantedUntimed, upgradeLyrics } from '../../api';
import { notifyError, errorMessage } from '../../notify';
import { useUrlPatch, useUrlState } from '../../hooks/useUrlState';
import { BulkBar, DataTable, EmptyState, FilterBar, IconButton, LangTag, Pager, TitleCell, type Column } from '../../components/ui';
import { DEFAULT_PAGE_SIZE, PAGE_SIZES, fileName, type WantedStats, type WantedTrack } from './shared';
import c from './Wanted.module.css';

/** Untimed tab: plain lyrics that could be synced (DESIGN.md 8.6). */
export function UntimedTab({ stats }: { stats?: WantedStats }) {
  const queryClient = useQueryClient();
  const patchUrl = useUrlPatch();
  const isMobile = useMediaQuery('(max-width: 767px)') ?? false;
  const [q] = useUrlState('q', '');
  const [page] = useUrlState('page', 1);
  const [size] = useUrlState('size', DEFAULT_PAGE_SIZE);
  const [selectedTracks, setSelectedTracks] = useState<Set<number>>(new Set());
  const [syncingTrackId, setSyncingTrackId] = useState<number | null>(null);
  const [busy, setBusy] = useState<'upgrade' | 'sync' | 'page' | null>(null);

  useEffect(() => { setSelectedTracks(new Set()); }, [q, page, size]);

  const query = useQuery({
    queryKey: ['wanted-untimed', page, q, size],
    queryFn: () => getWantedUntimed({ page, pageSize: size, search: q || undefined }),
    placeholderData: keepPreviousData,
  });
  const untimedTracks = (query.data?.data ?? []) as WantedTrack[];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['wanted-untimed'] });
    queryClient.invalidateQueries({ queryKey: ['wanted-stats'] });
  };

  const upgradeSelected = () => {
    setBusy('upgrade');
    upgradeLyrics({ trackIds: Array.from(selectedTracks) }).then((r: any) => {
      notifications.show({ title: 'Started', message: r.message || `Searching synced lyrics for ${selectedTracks.size} track(s)...` });
      setSelectedTracks(new Set());
      invalidate();
    }).catch((err: any) => {
      notifications.show({ title: 'Not started', message: errorMessage(err, 'Failed to start upgrade'), color: 'warning' });
    }).finally(() => setBusy(null));
  };

  const syncSelected = () => {
    setBusy('sync');
    batchSyncGenerate({ trackIds: Array.from(selectedTracks) }).then((r: any) => {
      notifications.show({ title: 'Started', message: r.message || `Syncing ${selectedTracks.size} track(s)...` });
      setSelectedTracks(new Set());
      invalidate();
    }).catch((err) => notifyError('Error', err, 'Failed to start sync generation'))
      .finally(() => setBusy(null));
  };

  const syncPage = () => {
    const allIds = untimedTracks.map(t => t.lidarrTrackId);
    setBusy('page');
    batchSyncGenerate({ trackIds: allIds }).then((r: any) => {
      notifications.show({ title: 'Started', message: r.message || `Syncing all ${allIds.length} track(s)...` });
      invalidate();
    }).catch((err) => notifyError('Error', err, 'Failed to start sync generation'))
      .finally(() => setBusy(null));
  };

  const syncOne = (t: WantedTrack) => {
    setSyncingTrackId(t.lidarrTrackId);
    batchSyncGenerate({ trackIds: [t.lidarrTrackId] }).then((r: any) => {
      notifications.show({ title: 'Started', message: r.message || 'Generating timing...' });
      setSyncingTrackId(null);
    }).catch((err) => {
      setSyncingTrackId(null);
      notifyError('Failed to start timing generation', err);
    });
  };

  const columns: Column<WantedTrack>[] = [
    {
      key: 'track', header: 'Track', mobile: 'title',
      // Phone cards already show "Artist · Album" below: the file name would repeat the artist.
      cell: t => <TitleCell title={t.title} secondary={isMobile ? undefined : fileName(t.path) || undefined} />,
    },
    {
      key: 'artist', header: 'Artist', width: '20%', priority: 2,
      // Phone cards: "Artist · Album" as one meta item that ellipsizes as a whole.
      cell: t => <span className={c.cellText}>{(isMobile ? [t.artistName, t.albumTitle].filter(Boolean).join(' · ') : t.artistName) || '—'}</span>,
    },
    { key: 'album', header: 'Album', width: '22%', priority: 3, mobile: 'hidden', cell: t => <span className={c.cellText}>{t.albumTitle || '—'}</span> },
    { key: 'lang', header: 'Language', width: 96, priority: 1, mobile: 'status', cell: t => <LangTag code={t.detected_language} /> },
  ];

  const zero = (stats?.untimed_lyrics ?? 0) === 0 && !q;
  // No lyrics at all yet: nothing to celebrate, say why the list is empty instead.
  const noLyrics = stats !== undefined && (stats.lyrics_complete ?? 0) === 0;

  return (
    <>
      <FilterBar
        search={{ value: q, onChange: v => patchUrl({ q: [v, ''], page: [1, 1] }), placeholder: 'Filter by track title…' }}
        right={(
          <Button
            variant="default"
            leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />}
            disabled={untimedTracks.length === 0}
            loading={busy === 'page'}
            onClick={syncPage}
          >
            Generate timing for this page
          </Button>
        )}
      />
      <DataTable<WantedTrack>
        aria-label="Tracks with plain lyrics"
        columns={columns}
        rows={untimedTracks}
        getRowId={t => t.lidarrTrackId}
        getRowLabel={t => t.title}
        noun="track"
        loading={query.isLoading}
        refetching={query.isFetching && query.isPlaceholderData}
        error={query.isError ? { message: errorMessage(query.error), onRetry: () => query.refetch() } : null}
        empty={q ? (
          <EmptyState
            size="sm"
            icon={faMagnifyingGlass}
            title={`No tracks match “${q}”`}
            action={<Button variant="default" onClick={() => patchUrl({ q: ['', ''], page: [1, 1] })}>Clear filter</Button>}
          />
        ) : (
          <EmptyState
            size="sm"
            tone={zero && !noLyrics ? 'success' : 'neutral'}
            icon={faClock}
            title={noLyrics ? 'No lyrics downloaded yet' : 'Every track with lyrics is already synced'}
            message={noLyrics
              ? 'Once tracks have lyrics, the plain (untimed) ones show up here so you can find a synced version or generate timing.'
              : 'Tracks with plain (untimed) lyrics show up here. Find a synced version or generate timing from the audio.'}
          />
        )}
        selection={{ selected: selectedTracks, onChange: setSelectedTracks }}
        rowActions={t => (
          <IconButton
            icon={faWandMagicSparkles}
            label="Generate timing"
            loading={syncingTrackId === t.lidarrTrackId}
            onClick={() => syncOne(t)}
          />
        )}
        rowActionsWidth={56}
        footer={(query.data?.total ?? 0) > 0 && (
          <Pager
            page={page}
            pageSize={size}
            total={query.data?.total ?? 0}
            onPage={p => patchUrl({ page: [p, 1] })}
            pageSizeOptions={PAGE_SIZES}
            onPageSize={n => patchUrl({ size: [n, DEFAULT_PAGE_SIZE], page: [1, 1] })}
            noun="tracks"
          />
        )}
      />

      <BulkBar count={selectedTracks.size} noun="track" onClear={() => setSelectedTracks(new Set())}>
        {/* Short labels on phones so both actions fit next to the count */}
        {isMobile ? (
          <IconButton
            icon={faWandMagicSparkles}
            label="Generate timing"
            variant="default"
            size="input-sm"
            loading={busy === 'sync'}
            onClick={syncSelected}
          />
        ) : (
          <Button variant="default" size="sm" leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />} loading={busy === 'sync'} onClick={syncSelected}>
            Generate timing
          </Button>
        )}
        <Button
          variant="filled"
          size="sm"
          className={c.bulkButton}
          leftSection={<FontAwesomeIcon icon={faArrowUpRightDots} />}
          loading={busy === 'upgrade'}
          onClick={upgradeSelected}
        >
          {isMobile ? 'Find synced' : 'Find synced versions'}
        </Button>
      </BulkBar>
    </>
  );
}
