import { useMemo, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQueries, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { Button, Menu, Select } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faArrowLeft, faCircleExclamation, faCompactDisc, faDownload, faEllipsis, faLanguage,
  faRotateRight, faUser, faWandMagicSparkles,
} from '@fortawesome/free-solid-svg-icons';
import { notifyError } from '../notify';
import { getArtist, getAlbums, getProfiles, updateArtist } from '../api';
import {
  EmptyState, Hero, HeroSkeleton, IconButton, Pager, Panel, TableSkeleton, ViewTabs, useConfirm,
} from '../components/ui';
import { AlbumTable, type AlbumRow } from '../components/library/AlbumTable';
import { AlbumGrid } from '../components/library/AlbumGrid';
import { ViewToggle } from '../components/library/LibraryControls';
import { AboutPanel } from '../components/library/AboutPanel';
import { startTiming, startTranslate, useAssignProfileMutation, useDownloadMutation } from '../components/library/actions';
import { useBreadcrumbs } from '../hooks/useBreadcrumbs';
import { useUrlPatch, useUrlState } from '../hooks/useUrlState';
import { usePreferences } from '../theme/ThemeProvider';
import { plural } from '../format';
import c from './LibraryDetail.module.css';

const LANGUAGES = [
  { value: 'en', label: 'English' }, { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' }, { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' }, { value: 'pt', label: 'Portuguese' },
  { value: 'ja', label: 'Japanese' }, { value: 'ko', label: 'Korean' },
  { value: 'zh-cn', label: 'Chinese (Simplified)' }, { value: 'ru', label: 'Russian' },
  { value: 'ar', label: 'Arabic' }, { value: 'hi', label: 'Hindi' },
  { value: 'tr', label: 'Turkish' }, { value: 'nl', label: 'Dutch' },
];

const languageName = (code: string | null | undefined) =>
  code ? (LANGUAGES.find(l => l.value === code)?.label ?? code.toUpperCase()) : null;

/** Album views on the artist page (DESIGN.md 8.3). */
const VIEWS = [
  { value: 'all', label: 'All', filter: {} },
  { value: 'missing-cover', label: 'Missing cover', filter: { coverStatus: 'missing' } },
  { value: 'missing-lyrics', label: 'Missing lyrics', filter: { lyricsStatus: 'missing' } },
  { value: 'complete', label: 'Complete', filter: { lyricsStatus: 'complete' } },
] as const satisfies readonly { value: string; label: string; filter: { coverStatus?: string; lyricsStatus?: string } }[];

type ViewId = (typeof VIEWS)[number]['value'];

/** Same page sizes and default as Albums / Artists. */
const PAGE_SIZES = [25, 50, 100];
const DEFAULT_PAGE_SIZE = 50;

export default function ArtistDetailPage() {
  const { artistId } = useParams();
  const id = Number(artistId);
  const queryClient = useQueryClient();
  const { albumView, setAlbumView } = usePreferences();
  const patchUrl = useUrlPatch();
  const [viewParam] = useUrlState<string>('view', 'all');
  const [page, setPage] = useUrlState<number>('page', 1);
  const [pageSize] = useUrlState<number>('size', DEFAULT_PAGE_SIZE);
  const [savingLang, setSavingLang] = useState(false);
  // Phones: the panel tools move out of the header into their own row.
  const narrow = useMediaQuery('(max-width: 767px)') ?? false;

  const view: ViewId = VIEWS.some(v => v.value === viewParam) ? (viewParam as ViewId) : 'all';
  const viewFilter: { coverStatus?: string; lyricsStatus?: string } = VIEWS.find(v => v.value === view)!.filter;

  const { data: artist, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['artist', artistId],
    queryFn: () => getArtist(id),
    enabled: !!artistId,
  });

  const albumParams = useMemo(() => ({ artistId: id, ...viewFilter }), [id, viewFilter]);
  const albumsQuery = useQuery({
    queryKey: ['artist-albums', artistId, page, pageSize, view],
    queryFn: () => getAlbums({ page, pageSize, ...albumParams }),
    enabled: !!artistId,
    placeholderData: keepPreviousData,
  });

  const counts = useQueries({
    queries: VIEWS.map(v => ({
      queryKey: ['artist-albums', artistId, 'count', v.value],
      queryFn: () => getAlbums({ artistId: id, ...v.filter, page: 1, pageSize: 1 }),
      enabled: !!artistId,
      staleTime: 30_000,
    })),
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const download = useDownloadMutation();
  const assign = useAssignProfileMutation();
  const confirm = useConfirm();

  useBreadcrumbs([{ label: 'Artists', to: '/artists' }, { label: artist?.name ?? 'Artist' }]);

  if (isLoading) {
    return (
      <div className="ly-page">
        <HeroSkeleton kind="artist" />
        <TableSkeleton rows={8} columns={5} />
      </div>
    );
  }

  const notFound = (error as { response?: { status?: number } } | null)?.response?.status === 404;
  if (isError && !notFound) {
    return (
      <div className="ly-page">
        <EmptyState
          tone="danger"
          icon={faCircleExclamation}
          title="Couldn't load this artist"
          message={(error as Error)?.message}
          action={<Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} onClick={() => refetch()}>Retry</Button>}
        />
      </div>
    );
  }

  if (!artist) {
    return (
      <div className="ly-page">
        <EmptyState
          icon={faUser}
          title="Artist not found"
          message="It may have been removed from Lidarr."
          action={<Button component={Link} to="/artists" variant="default" leftSection={<FontAwesomeIcon icon={faArrowLeft} />}>Back to artists</Button>}
        />
      </div>
    );
  }

  const albums: AlbumRow[] = albumsQuery.data?.data || [];
  const totalInView = albumsQuery.data?.total || 0;
  // Tab counts fall back to the list's own total for the active view.
  const tabCounts = VIEWS.map((v, i) => counts[i].data?.total
    ?? (v.value === view && albumsQuery.data && !albumsQuery.isPlaceholderData ? totalInView : undefined));
  const totalAlbums = tabCounts[0];
  const albumsError = albumsQuery.isError
    ? { title: "Couldn't load albums", message: (albumsQuery.error as Error)?.message, onRetry: () => albumsQuery.refetch() }
    : null;
  const target = { artistIds: [id] };

  const ownProfile = profiles.find(p => p.id === artist.profileId);
  const defaultProfile = profiles.find(p => p.is_default);
  const effectiveProfile = ownProfile ?? defaultProfile;
  const profileTarget = effectiveProfile ? languageName(effectiveProfile.translate_target_lang) : null;

  const saveLanguage = (v: string | null) => {
    setSavingLang(true);
    updateArtist(id, { translate_target_override: v || null }).then(() => {
      queryClient.invalidateQueries({ queryKey: ['artist', artistId] });
      notifications.show({
        title: 'Translation target saved',
        message: v ? `Lyrics by ${artist.name} translate to ${languageName(v)}.` : `${artist.name} uses the profile's target language.`,
        color: 'success',
      });
    }).catch((err) => notifyError('Failed to save language override', err))
      .finally(() => setSavingLang(false));
  };

  const albumEmpty = view === 'all' ? (
    <EmptyState size="sm" icon={faCompactDisc} title="No albums" message="No downloaded albums for this artist." />
  ) : (
    <EmptyState
      size="sm"
      icon={faCompactDisc}
      tone="success"
      title={view === 'complete' ? 'No complete albums yet' : 'Nothing missing here'}
      action={<Button variant="default" onClick={() => patchUrl({ view: ['all', 'all'], page: [1, 1] })}>Show all albums</Button>}
    />
  );

  const pager = totalInView > 0 ? (
    <Pager
      page={page}
      pageSize={pageSize}
      total={totalInView}
      onPage={p => setPage(p, { replace: false })}
      pageSizeOptions={PAGE_SIZES}
      // The size picker only matters when there's more than one small page.
      onPageSize={totalInView > PAGE_SIZES[0] || pageSize !== DEFAULT_PAGE_SIZE
        ? n => patchUrl({ size: [n, DEFAULT_PAGE_SIZE], page: [1, 1] })
        : undefined}
      noun="albums"
    />
  ) : undefined;

  const panelTools = (
    <div className={c.panelTools}>
      <ViewTabs
        aria-label="Album views"
        tabs={VIEWS.map((v, i) => ({ value: v.value, label: v.label, count: tabCounts[i] }))}
        value={view}
        onChange={v => patchUrl({ view: [v, 'all'], page: [1, 1] })}
      />
      <ViewToggle value={albumView} onChange={setAlbumView} noun="albums" />
    </div>
  );

  return (
    <div className="ly-page">
      <Hero
        art={{ src: artist.poster, alt: artist.name, kind: 'artist', name: artist.name }}
        eyebrow={`Artist · ${artist.monitored ? 'Monitored' : 'Unmonitored'}`}
        title={artist.name}
        byline={(
          <>
            {totalAlbums !== undefined && <span>{plural(totalAlbums, 'album')}</span>}
            {artist.path && (
              <>
                {totalAlbums !== undefined && <span className={c.sep} aria-hidden="true">·</span>}
                <span className={c.path} title={artist.path}>{artist.path}</span>
              </>
            )}
          </>
        )}
        actions={(
          <>
            <Button
              variant="filled"
              leftSection={<FontAwesomeIcon icon={faDownload} />}
              loading={download.isPending && download.variables?.type === 'all'}
              onClick={() => download.mutate({ target, type: 'all', message: 'Downloading missing covers + lyrics for this artist...' })}
            >
              Download all missing
            </Button>
            <Button
              variant="default"
              leftSection={<FontAwesomeIcon icon={faAlignLeft} />}
              loading={download.isPending && download.variables?.type === 'lyrics'}
              onClick={() => download.mutate({ target, type: 'lyrics', message: 'Downloading missing lyrics for all albums by this artist...' })}
            >
              Download missing lyrics
            </Button>
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <IconButton icon={faEllipsis} label="More actions" variant="default" size="lg" />
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<FontAwesomeIcon icon={faLanguage} />} onClick={() => startTranslate(target)}>
                  Translate lyrics
                </Menu.Item>
                <Menu.Item leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />} onClick={() => startTiming(target)}>
                  Generate timing
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </>
        )}
      />

      <div className={c.layout}>
        <div className={c.main}>
          <Panel
            padding="none"
            title="Albums"
            subtitle={totalAlbums !== undefined ? plural(totalAlbums, 'album') : undefined}
            actions={narrow ? undefined : panelTools}
          >
            {narrow && <div className={c.panelTabsRow}>{panelTools}</div>}
            {albumView === 'grid' ? (
              <div className={c.gridWrap}>
                <AlbumGrid
                  aria-label={`Albums by ${artist.name}`}
                  rows={albums}
                  loading={albumsQuery.isLoading}
                  refetching={albumsQuery.isPlaceholderData}
                  error={albumsError}
                  empty={albumEmpty}
                  footer={pager}
                  hideArtist
                />
              </div>
            ) : (
              <AlbumTable
                aria-label={`Albums by ${artist.name}`}
                rows={albums}
                profiles={profiles}
                loading={albumsQuery.isLoading}
                refetching={albumsQuery.isPlaceholderData}
                error={albumsError}
                empty={albumEmpty}
                footer={pager}
                hideArtist
                framed={false}
                // Type (priority 3) moves under the title while the side panel shares the row.
                breakpoints={{ 2: 600, 3: 860 }}
              />
            )}
          </Panel>
        </div>

        <aside className={c.side} aria-label="Artist settings and details">
          <Panel title="Settings" headingLevel={3}>
            <div className={c.field}>
              <Select
                label="Profile"
                placeholder={defaultProfile ? `None (uses ${defaultProfile.name})` : 'Select profile...'}
                data={profiles.map(p => ({ value: String(p.id), label: p.name }))}
                value={artist.profileId ? String(artist.profileId) : null}
                allowDeselect={false}
                disabled={assign.isPending}
                onChange={async v => {
                  const p = profiles.find(x => String(x.id) === v);
                  if (!p) return;
                  // Reassigns every album by the artist, so ask first (the old page had a separate Set Profile button).
                  const ok = await confirm({
                    title: 'Change profile?',
                    message: `Assign ${p.name} to ${totalAlbums !== undefined ? plural(totalAlbums, 'album') : 'every album'} by ${artist.name}? Albums with their own profile are reassigned too.`,
                    confirmLabel: 'Assign profile',
                  });
                  if (ok) assign.mutate({ target, profileId: p.id, profileName: p.name });
                }}
              />
              <p className={c.source}>
                {totalAlbums !== undefined ? `Applies to ${plural(totalAlbums, 'album')}` : 'Applies to every album by this artist'}
              </p>
            </div>
            <div className={c.field}>
              <Select
                label="Translation target"
                placeholder="Profile default"
                data={LANGUAGES}
                value={artist.translate_target_override || null}
                clearable
                searchable
                disabled={savingLang}
                onChange={saveLanguage}
              />
              <p className={c.source}>
                {artist.translate_target_override
                  ? 'Override for this artist'
                  : effectiveProfile
                    ? `From profile ${effectiveProfile.name}${profileTarget ? `: ${profileTarget}` : ''}`
                    : "Uses the profile's target language"}
              </p>
            </div>
          </Panel>

          {artist.overview && <AboutPanel text={artist.overview} />}
        </aside>
      </div>
    </div>
  );
}
