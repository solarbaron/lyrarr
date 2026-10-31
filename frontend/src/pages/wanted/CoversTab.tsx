import { useEffect, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faDownload, faImage, faMagnifyingGlass } from '@fortawesome/free-solid-svg-icons';
import { batchDownload, getWantedCovers } from '../../api';
import { notifyError, errorMessage } from '../../notify';
import { plural } from '../../format';
import { useUrlPatch, useUrlState } from '../../hooks/useUrlState';
import { BulkBar, DataTable, EmptyState, FilterBar, IconButton, Pager, Tag, TitleCell, type Column } from '../../components/ui';
import CoverSearchModal from '../../components/CoverSearchModal';
import { Attempts, DEFAULT_PAGE_SIZE, PAGE_SIZES, refreshWanted, type WantedAlbum, type WantedStats } from './shared';
import c from './Wanted.module.css';

/** Covers tab of the Wanted workspace (DESIGN.md 8.6). */
export function CoversTab({ stats }: { stats?: WantedStats }) {
  const queryClient = useQueryClient();
  const patchUrl = useUrlPatch();
  const [q] = useUrlState('q', '');
  const [page] = useUrlState('page', 1);
  const [size] = useUrlState('size', DEFAULT_PAGE_SIZE);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [coverSearch, setCoverSearch] = useState<number | null>(null);
  const [downloading, setDownloading] = useState<Set<number>>(new Set());

  useEffect(() => { setSelected(new Set()); }, [q, page, size]);

  const query = useQuery({
    queryKey: ['wanted-covers', page, q, size],
    queryFn: () => getWantedCovers({ page, pageSize: size, search: q || undefined }),
    placeholderData: keepPreviousData,
  });
  const rows = (query.data?.data ?? []) as WantedAlbum[];

  const download = useMutation({
    mutationFn: (albumIds: number[]) => {
      setDownloading(new Set(albumIds));
      return batchDownload({ albumIds, type: 'covers' });
    },
    onSuccess: (r: { message?: string }, albumIds) => {
      notifications.show({ title: 'Download started', message: r?.message || `Downloading cover art for ${plural(albumIds.length, 'album')}…` });
      setSelected(new Set());
    },
    onError: (err) => notifyError('Download failed to start', err),
    onSettled: () => setDownloading(new Set()),
  });

  const columns: Column<WantedAlbum>[] = [
    {
      key: 'album', header: 'Album', mobile: 'title',
      cell: a => (
        <TitleCell
          title={a.title}
          href={`/albums/${a.lidarrAlbumId}`}
          art={{ src: a.cover, kind: 'album', name: a.title }}
        />
      ),
    },
    { key: 'artist', header: 'Artist', width: '22%', priority: 1, cell: a => <span className={c.cellText}>{a.artistName || '—'}</span> },
    { key: 'year', header: 'Year', width: 72, align: 'right', priority: 3, cell: a => (a.year ? String(a.year) : '—') },
    { key: 'type', header: 'Type', width: 110, priority: 3, cell: a => (a.albumType ? <Tag>{a.albumType}</Tag> : '—') },
    {
      key: 'attempts', header: 'Attempts', width: 190, priority: 2, mobile: 'status',
      cell: a => <Attempts after={a.cover_retry_after} count={a.cover_retry_count} />,
    },
  ];

  const zero = (stats?.missing_covers ?? 0) === 0 && !q;

  return (
    <>
      <FilterBar
        search={{ value: q, onChange: v => patchUrl({ q: [v, ''], page: [1, 1] }), placeholder: 'Filter by album title…' }}
      />
      <DataTable<WantedAlbum>
        aria-label="Albums with missing cover art"
        columns={columns}
        rows={rows}
        getRowId={a => a.lidarrAlbumId}
        getRowLabel={a => a.title}
        rowHref={a => `/albums/${a.lidarrAlbumId}`}
        noun="album"
        loading={query.isLoading}
        refetching={query.isFetching && query.isPlaceholderData}
        error={query.isError ? { message: errorMessage(query.error), onRetry: () => query.refetch() } : null}
        empty={q ? (
          <EmptyState
            size="sm"
            icon={faMagnifyingGlass}
            title={`No albums match “${q}”`}
            action={<Button variant="default" onClick={() => patchUrl({ q: ['', ''], page: [1, 1] })}>Clear filter</Button>}
          />
        ) : (
          <EmptyState
            size="sm"
            tone={zero ? 'success' : 'neutral'}
            icon={faImage}
            title="Every album has cover art"
            message="Nothing is missing. New albums from Lidarr show up here until a cover is saved."
          />
        )}
        selection={{ selected, onChange: setSelected }}
        rowActions={a => (
          <>
            <IconButton icon={faMagnifyingGlass} label="Search covers" onClick={() => setCoverSearch(a.lidarrAlbumId)} />
            <IconButton
              icon={faDownload}
              label="Download cover"
              loading={downloading.has(a.lidarrAlbumId)}
              onClick={() => download.mutate([a.lidarrAlbumId])}
            />
          </>
        )}
        rowActionsWidth={96}
        footer={(query.data?.total ?? 0) > 0 && (
          <Pager
            page={page}
            pageSize={size}
            total={query.data?.total ?? 0}
            onPage={p => patchUrl({ page: [p, 1] })}
            pageSizeOptions={PAGE_SIZES}
            onPageSize={n => patchUrl({ size: [n, DEFAULT_PAGE_SIZE], page: [1, 1] })}
            noun="albums"
          />
        )}
      />

      <BulkBar count={selected.size} noun="album" onClear={() => setSelected(new Set())}>
        <Button
          variant="filled"
          size="sm"
          leftSection={<FontAwesomeIcon icon={faDownload} />}
          loading={download.isPending}
          onClick={() => download.mutate([...selected])}
        >
          Download covers
        </Button>
      </BulkBar>

      {coverSearch !== null && (
        <CoverSearchModal
          albumId={coverSearch}
          opened
          onClose={() => {
            setCoverSearch(null);
            // A cover picked in the modal removes the album from this list.
            refreshWanted(queryClient);
          }}
        />
      )}
    </>
  );
}
