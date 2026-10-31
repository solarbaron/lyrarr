import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Select } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faLanguage, faMagnifyingGlass } from '@fortawesome/free-solid-svg-icons';
import { batchRedetectLanguages, getWantedUndetected, updateTrack } from '../../api';
import { notifyError, errorMessage } from '../../notify';
import { useUrlPatch, useUrlState } from '../../hooks/useUrlState';
import { BulkBar, DataTable, EmptyState, FilterBar, IconButton, Pager, StatusDot, TitleCell, type Column } from '../../components/ui';
import { DEFAULT_PAGE_SIZE, LANG_OPTIONS, PAGE_SIZES, fileName, type WantedStats, type WantedTrack } from './shared';
import c from './Wanted.module.css';

/** Undetected language tab (DESIGN.md 8.6). */
export function LanguageTab({ stats }: { stats?: WantedStats }) {
  const queryClient = useQueryClient();
  const patchUrl = useUrlPatch();
  const isMobile = useMediaQuery('(max-width: 767px)') ?? false;
  const [q] = useUrlState('q', '');
  const [page] = useUrlState('page', 1);
  const [size] = useUrlState('size', DEFAULT_PAGE_SIZE);
  const [selectedLangTracks, setSelectedLangTracks] = useState<Set<number>>(new Set());
  const [detectingTrackId, setDetectingTrackId] = useState<number | null>(null);
  const [busy, setBusy] = useState<'selected' | 'page' | null>(null);

  useEffect(() => { setSelectedLangTracks(new Set()); }, [q, page, size]);

  const query = useQuery({
    queryKey: ['wanted-undetected', page, q, size],
    queryFn: () => getWantedUndetected({ page, pageSize: size, search: q || undefined }),
    placeholderData: keepPreviousData,
  });
  const undetectedTracks = (query.data?.data ?? []) as WantedTrack[];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['wanted-undetected'] });
    queryClient.invalidateQueries({ queryKey: ['wanted-stats'] });
  };

  const detectSelected = () => {
    setBusy('selected');
    batchRedetectLanguages({ trackIds: Array.from(selectedLangTracks) }).then(() => {
      notifications.show({ title: 'Started', message: `Detecting language for ${selectedLangTracks.size} track(s)...` });
      setSelectedLangTracks(new Set());
      invalidate();
    }).catch((err) => notifyError('Error', err, 'Failed to start language detection'))
      .finally(() => setBusy(null));
  };

  const detectPage = () => {
    const allIds = undetectedTracks.map(t => t.lidarrTrackId);
    setBusy('page');
    batchRedetectLanguages({ trackIds: allIds }).then(() => {
      notifications.show({ title: 'Started', message: `Detecting language for all ${allIds.length} track(s)...` });
      invalidate();
    }).catch((err) => notifyError('Error', err, 'Failed to start language detection'))
      .finally(() => setBusy(null));
  };

  const detectOne = (t: WantedTrack) => {
    setDetectingTrackId(t.lidarrTrackId);
    batchRedetectLanguages({ trackIds: [t.lidarrTrackId] }).then(() => {
      notifications.show({ title: 'Started', message: 'Detecting language...' });
      setDetectingTrackId(null);
      invalidate();
    }).catch((err) => {
      setDetectingTrackId(null);
      notifyError('Failed to start language detection', err);
    });
  };

  const setLanguage = (t: WantedTrack, val: string | null) => {
    if (!val) return;
    updateTrack(t.lidarrTrackId, { detected_language: val }).then(() => {
      notifications.show({ title: 'Updated', message: `Set language to ${val.toUpperCase()}`, color: 'success' });
      invalidate();
    }).catch((err) => notifyError('Failed to set language', err));
  };

  const columns: Column<WantedTrack>[] = [
    {
      key: 'track', header: 'Track', mobile: 'title',
      // Phone cards already show "Artist · Album" below: the file name would repeat the artist.
      cell: t => <TitleCell title={t.title} secondary={isMobile ? undefined : fileName(t.path) || undefined} />,
    },
    {
      key: 'artist', header: 'Artist', width: '18%', priority: 2,
      // Phone cards: "Artist · Album" as one meta item that ellipsizes as a whole.
      cell: t => <span className={c.cellText}>{(isMobile ? [t.artistName, t.albumTitle].filter(Boolean).join(' · ') : t.artistName) || '—'}</span>,
    },
    { key: 'album', header: 'Album', width: '20%', priority: 3, mobile: 'hidden', cell: t => <span className={c.cellText}>{t.albumTitle || '—'}</span> },
    {
      key: 'lyrics', header: 'Lyrics', width: 100, priority: 1, mobile: 'status',
      // A fixed-width slot on phone cards so the "Set…" selects line up
      cell: t => <span className={c.lyricsSlot}><StatusDot domain="trackLyrics" status={t.lyrics_status || 'available'} synced={t.is_synced} /></span>,
    },
    {
      key: 'set', header: 'Set language', width: 176, priority: 1, mobile: 'status',
      cell: t => (
        <Select
          placeholder="Set…"
          data={LANG_OPTIONS}
          size="xs"
          w={160}
          searchable
          clearable
          aria-label={`Set language for ${t.title}`}
          onChange={(val) => setLanguage(t, val)}
          comboboxProps={{ withinPortal: true }}
        />
      ),
    },
  ];

  const zero = (stats?.undetected_language ?? 0) === 0 && !q;
  // No lyrics at all yet: nothing to celebrate, say why the list is empty instead.
  const noLyrics = stats !== undefined && (stats.lyrics_complete ?? 0) === 0;

  return (
    <>
      <FilterBar
        search={{ value: q, onChange: v => patchUrl({ q: [v, ''], page: [1, 1] }), placeholder: 'Filter by track title…' }}
        right={(
          <Button
            variant="default"
            leftSection={<FontAwesomeIcon icon={faLanguage} />}
            disabled={undetectedTracks.length === 0}
            loading={busy === 'page'}
            onClick={detectPage}
          >
            Detect all on this page
          </Button>
        )}
      />
      <DataTable<WantedTrack>
        aria-label="Tracks without a detected language"
        columns={columns}
        rows={undetectedTracks}
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
            icon={faLanguage}
            title={noLyrics ? 'No lyrics downloaded yet' : 'Every track with lyrics has a detected language'}
            message={noLyrics
              ? "Once tracks have lyrics, any whose language couldn't be detected show up here."
              : "Tracks whose lyrics language couldn't be detected show up here. Detect again or set the language by hand."}
          />
        )}
        selection={{ selected: selectedLangTracks, onChange: setSelectedLangTracks }}
        rowActions={t => (
          <IconButton
            icon={faLanguage}
            label="Detect language"
            loading={detectingTrackId === t.lidarrTrackId}
            onClick={() => detectOne(t)}
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

      <BulkBar count={selectedLangTracks.size} noun="track" onClear={() => setSelectedLangTracks(new Set())}>
        <Button variant="filled" size="sm" className={c.bulkButton} leftSection={<FontAwesomeIcon icon={faLanguage} />} loading={busy === 'selected'} onClick={detectSelected}>
          Detect language
        </Button>
      </BulkBar>
    </>
  );
}
