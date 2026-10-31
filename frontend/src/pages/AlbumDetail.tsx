import { useEffect, useMemo, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, FileButton, Menu, Select } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faArrowLeft, faBan, faCircleExclamation, faCompactDisc, faCopy, faEllipsis, faImage,
  faLanguage, faMagnifyingGlass, faMicrophone, faMusic, faPenToSquare, faRotateLeft, faRotateRight, faUpload,
  faWandMagicSparkles, faRepeat,
} from '@fortawesome/free-solid-svg-icons';
import { getAlbum, getArtist, getProfiles, uploadAlbumCover, updateTrack } from '../api';
import { notifyError } from '../notify';
import CoverSearchModal from '../components/CoverSearchModal';
import LyricsSearchModal from '../components/LyricsSearchModal';
import LyricsEditorModal from '../components/LyricsEditorModal';
import ImageLightbox from '../components/ImageLightbox';
import {
  CoverArt, DataTable, EmptyState, Hero, HeroSkeleton, IconButton, KeyValueList, LangTag, LyricsMeter, Panel,
  StatusChip, TableSkeleton, TitleCell, ViewTabs, isFailedImage, useConfirm, type Column, type KeyValueItem,
} from '../components/ui';
import { AboutPanel } from '../components/library/AboutPanel';
import { InstrumentalHintTag } from '../components/instrumental/InstrumentalEvidence';
import { statusOptions } from '../components/instrumental/model';
import { useInstrumentalActions } from '../components/instrumental/useInstrumentalActions';
import { FileName, baseName, sharedPrefix } from '../components/library/FileName';
import { profileLabel } from '../components/library/LibraryControls';
import { startTiming, startTranslate, useAssignProfileMutation, useDownloadMutation } from '../components/library/actions';
import { useBreadcrumbs } from '../hooks/useBreadcrumbs';
import { useRecentAlbums } from '../hooks/useRecentAlbums';
import { useUrlState } from '../hooks/useUrlState';
import { duration, longDuration, num, parseGenres, plural } from '../format';
import type { Profile, Track } from '../types';
import c from './LibraryDetail.module.css';

type TrackRow = Track;

type TrackKind = 'synced' | 'plain' | 'instrumental' | 'skipped' | 'missing';

function trackKind(t: TrackRow): TrackKind {
  if (t.lyrics_status === 'blacklisted') return 'skipped';
  if (t.lyrics_status === 'instrumental') return 'instrumental';
  if (t.lyrics_status === 'available' || t.lyrics_status === 'manual') return t.is_synced ? 'synced' : 'plain';
  return 'missing';
}

const TRACK_VIEWS = [
  { value: 'all', label: 'All' },
  { value: 'missing', label: 'Missing' },
  { value: 'plain', label: 'Plain' },
  { value: 'synced', label: 'Synced' },
  { value: 'instrumental', label: 'Instrumental' },
  { value: 'skipped', label: 'Skipped' },
] as const;

type TrackView = (typeof TRACK_VIEWS)[number]['value'];

const yesNo = (v: boolean | null | undefined) => (v ? 'Yes' : 'No');

/** Override value when set, otherwise the profile's, with where it came from (DESIGN.md 8.5). */
function inherited<T>(override: T | null | undefined, fromProfile: T | undefined, profile: Profile | undefined, fmt: (v: T) => string): Pick<KeyValueItem, 'value' | 'source'> {
  if (override !== null && override !== undefined) return { value: fmt(override), source: 'override' };
  if (profile && fromProfile !== undefined) return { value: fmt(fromProfile), source: `from profile ${profile.name}` };
  return { value: '—' };
}

export default function AlbumDetailPage() {
  const { albumId } = useParams();
  const id = Number(albumId);
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  // Phones: the view tabs move out of the panel header into their own row.
  const narrow = useMediaQuery('(max-width: 767px)') ?? false;
  const { push: pushRecent } = useRecentAlbums();
  const [coverSearchOpen, setCoverSearchOpen] = useState(false);
  const [lyricsTrack, setLyricsTrack] = useState<TrackRow | null>(null);
  const [editorTrack, setEditorTrack] = useState<TrackRow | null>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [trackViewParam, setTrackView] = useUrlState<string>('tracks', 'all');
  const trackView: TrackView = TRACK_VIEWS.some(v => v.value === trackViewParam) ? (trackViewParam as TrackView) : 'all';

  const { data: album, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['album', albumId],
    queryFn: () => getAlbum(id),
    enabled: !!albumId,
  });

  // Artist avatar for the byline; shares the Artist detail cache entry.
  const artistKey = album?.artistId ? String(album.artistId) : undefined;
  const { data: artist } = useQuery({
    queryKey: ['artist', artistKey],
    queryFn: () => getArtist(album!.artistId),
    enabled: !!artistKey,
    staleTime: 5 * 60_000,
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const download = useDownloadMutation();
  const assign = useAssignProfileMutation();
  const instrumental = useInstrumentalActions();

  // Blacklist (skip) or un-blacklist a track's lyrics.
  const trackStatusMutation = useMutation({
    mutationFn: ({ track, status }: { track: TrackRow; status: 'missing' | 'blacklisted' }) =>
      updateTrack(track.lidarrTrackId, { lyrics_status: status }),
    onSuccess: (_data, { track, status }) => {
      queryClient.invalidateQueries({ queryKey: ['album', albumId] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
      queryClient.invalidateQueries({ queryKey: ['wanted-lyrics'] });
      queryClient.invalidateQueries({ queryKey: ['wanted-stats'] });
      notifications.show({
        title: status === 'blacklisted' ? 'Track skipped' : 'Track unskipped',
        message: `${track.title} ${status === 'blacklisted' ? 'will be skipped by the downloader' : 'is now eligible for lyrics download'}`,
        color: 'success',
      });
    },
    onError: (err) => notifyError('Failed to update track status', err),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadAlbumCover(id, file),
    onSuccess: (data: { message?: string }) => {
      notifications.show({ title: 'Cover uploaded', message: data?.message || 'Cover art saved.', color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['album', albumId] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
    },
    // 409 reasons verbatim ("This album has no folder of its own …", "The album folder isn't reachable …").
    onError: (err: unknown) => notifyError('Cover not uploaded', err),
  });

  // "Jump back in" on Home.
  useEffect(() => {
    if (!album) return;
    pushRecent({ id: album.lidarrAlbumId, title: album.title, artistName: album.artistName, cover: album.cover || null, year: album.year || null });
  }, [album?.lidarrAlbumId, album?.title, album?.artistName, album?.cover, album?.year, pushRecent]); // eslint-disable-line react-hooks/exhaustive-deps

  useBreadcrumbs(album
    ? [{ label: 'Albums', to: '/albums' }, { label: album.artistName || 'Artist', to: `/artists/${album.artistId}` }, { label: album.title }]
    : [{ label: 'Albums', to: '/albums' }, { label: 'Album' }]);

  const tracks: TrackRow[] = useMemo(() => (album?.tracks as TrackRow[] | undefined) ?? [], [album?.tracks]);
  const multiDisc = useMemo(() => new Set(tracks.map(t => t.discNumber || 1)).size > 1, [tracks]);
  // "Artist - Album - " shared by every file name: it ellipsizes first.
  const filePrefix = useMemo(
    () => sharedPrefix(tracks.map(t => baseName(t.path)).filter((n): n is string => !!n)),
    [tracks],
  );

  const stats = useMemo(() => {
    const s = { synced: 0, plain: 0, instrumental: 0, skipped: 0, missing: 0 };
    tracks.forEach(t => { s[trackKind(t)] += 1; });
    return s;
  }, [tracks]);

  const visibleTracks = useMemo(
    () => (trackView === 'all' ? tracks : tracks.filter(t => trackKind(t) === trackView)),
    [tracks, trackView],
  );

  // First track of each disc gets a "Disc N" header row.
  const discStarts = useMemo(() => {
    const starts = new Map<number, number>();
    if (!multiDisc) return starts;
    let last = -1;
    visibleTracks.forEach(t => {
      const disc = t.discNumber || 1;
      if (disc !== last) starts.set(t.lidarrTrackId, disc);
      last = disc;
    });
    return starts;
  }, [visibleTracks, multiDisc]);

  const askSkip = async (track: TrackRow) => {
    const hasLyrics = !!track.lyrics_status && track.lyrics_status !== 'missing';
    const ok = await confirm({
      title: 'Skip lyrics for this track?',
      message: (
        <div className={c.skipMessage}>
          <p>
            <strong>{track.title}</strong> will be skipped by the downloader. If the track has no vocals,
            use Mark instrumental instead, so the album counts it as complete.
          </p>
          {hasLyrics && (
            <p>
              Its current lyrics file will be removed. A copy is archived to the track's version history,
              so it can be restored from the lyrics editor.
            </p>
          )}
        </div>
      ),
      confirmLabel: 'Skip track',
      tone: 'danger',
    });
    if (ok) trackStatusMutation.mutate({ track, status: 'blacklisted' });
  };

  const columns = useMemo<Column<TrackRow>[]>(() => [
    {
      key: 'no',
      header: '#',
      width: 56,
      align: 'right',
      priority: 1,
      mobile: 'hidden',
      skeleton: 'num',
      cell: (t) => (
        <span className={c.trackNo}>
          {multiDisc ? `${t.discNumber || 1}.` : ''}{t.trackNumber || tracks.indexOf(t) + 1}
        </span>
      ),
    },
    {
      key: 'title',
      header: 'Title',
      priority: 1,
      mobile: 'title',
      cell: (t) => {
        const file = baseName(t.path);
        return (
          <TitleCell
            title={t.title}
            secondary={file ? <FileName name={file} prefix={filePrefix} /> : undefined}
            extra={<InstrumentalHintTag track={t} />}
          />
        );
      },
    },
    {
      key: 'lang',
      header: 'Language',
      width: 96,
      priority: 2,
      mobile: 'meta',
      cell: (t) => <LangTag code={t.detected_language} />,
    },
    {
      key: 'time',
      header: 'Time',
      width: 72,
      align: 'right',
      priority: 2,
      // Mobile cards: the duration shares the lyrics chip's line.
      mobile: 'status',
      skeleton: 'num',
      cell: (t) => <span className={c.time}>{t.duration ? duration(t.duration) : '—'}</span>,
    },
    {
      key: 'lyrics',
      header: 'Lyrics',
      width: 132,
      priority: 1,
      mobile: 'status',
      skeleton: 'chip',
      cell: (t) => (
        <span className={c.chipCell}>
          <StatusChip domain="trackLyrics" status={t.lyrics_status} synced={t.is_synced} options={statusOptions(t)} size="sm" />
        </span>
      ),
    },
  ], [multiDisc, tracks, filePrefix]);

  if (isLoading) {
    return (
      <div className="ly-page">
        <HeroSkeleton kind="album" />
        <TableSkeleton rows={10} columns={5} />
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
          title="Couldn't load this album"
          message={(error as Error)?.message}
          action={<Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} onClick={() => refetch()}>Retry</Button>}
        />
      </div>
    );
  }

  if (!album) {
    return (
      <div className="ly-page">
        <EmptyState
          icon={faCompactDisc}
          title="Album not found"
          message="It may have been removed from Lidarr."
          action={<Button component={Link} to="/albums" variant="default" leftSection={<FontAwesomeIcon icon={faArrowLeft} />}>Back to albums</Button>}
        />
      </div>
    );
  }

  const target = { albumIds: [id] };
  const totalSeconds = tracks.reduce((sum, t) => sum + (t.duration || 0), 0);
  const withLyrics = stats.synced + stats.plain + stats.instrumental;
  const wanted = tracks.length - stats.skipped;
  const missing = stats.missing;
  const coverMissing = !album.cover_status || album.cover_status === 'missing';
  const canZoom = !!album.cover && !isFailedImage(album.cover);
  const genres = parseGenres(album.genres);

  const ownProfile = profiles.find(p => p.id === album.profileId);
  const defaultProfile = profiles.find(p => p.is_default);
  const effectiveProfile = ownProfile ?? defaultProfile;
  const albumLyricsStatus = album.lyrics_status || (tracks.length === 0 ? 'unknown' : missing === 0 ? 'complete' : withLyrics === 0 ? 'missing' : 'partial');

  const eyebrow = [album.albumType || 'Album', album.year || null, album.monitored ? 'Monitored' : 'Unmonitored'].filter(Boolean).join(' · ');

  const searchCoversButton = (primary: boolean) => (
    <Button
      variant={primary ? 'filled' : 'default'}
      leftSection={<FontAwesomeIcon icon={faImage} />}
      onClick={() => setCoverSearchOpen(true)}
    >
      Search covers
    </Button>
  );

  const actions = (
    <>
      {missing > 0 && (
        <Button
          variant="filled"
          leftSection={<FontAwesomeIcon icon={faAlignLeft} />}
          loading={download.isPending}
          onClick={() => download.mutate({ target, type: 'lyrics', message: 'Downloading missing lyrics for this album...' })}
        >
          Find lyrics for {plural(missing, 'track')}
        </Button>
      )}
      {searchCoversButton(missing === 0 && coverMissing)}
      <FileButton onChange={(file) => file && uploadMutation.mutate(file)} accept="image/png,image/jpeg,image/webp">
        {(props) => (
          <Button {...props} variant="default" leftSection={<FontAwesomeIcon icon={faUpload} />} loading={uploadMutation.isPending}>
            Upload cover
          </Button>
        )}
      </FileButton>
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <IconButton icon={faEllipsis} label="More actions" variant="default" size="lg" />
        </Menu.Target>
        <Menu.Dropdown>
          {/* Kept reachable when the client-side count says nothing is missing (the
              server decides what is actually wanted), as the old page always offered it. */}
          {missing === 0 && (
            <Menu.Item
              leftSection={<FontAwesomeIcon icon={faAlignLeft} />}
              disabled={download.isPending}
              onClick={() => download.mutate({ target, type: 'lyrics', message: 'Downloading missing lyrics for this album...' })}
            >
              Download missing lyrics
            </Menu.Item>
          )}
          <Menu.Item leftSection={<FontAwesomeIcon icon={faLanguage} />} onClick={() => startTranslate(target)}>
            Translate lyrics
          </Menu.Item>
          <Menu.Item leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />} onClick={() => startTiming(target)}>
            Generate timing
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </>
  );

  const byline = (
    <>
      <Link to={`/artists/${album.artistId}`} className={c.artistLink}>
        <CoverArt src={artist?.poster} alt="" kind="artist" name={album.artistName} size={24} />
        {album.artistName || 'Unknown artist'}
      </Link>
      <span className={c.sep} aria-hidden="true">·</span>
      <span>{plural(tracks.length, 'track')}</span>
      {totalSeconds > 0 && (
        <>
          <span className={c.sep} aria-hidden="true">·</span>
          <span>{longDuration(totalSeconds)}</span>
        </>
      )}
    </>
  );

  const settingsItems: KeyValueItem[] = [
    { label: 'Cover format', ...inherited(album.override_cover_format, effectiveProfile?.cover_format, effectiveProfile, v => String(v)) },
    { label: 'Prefer synced', ...inherited(album.override_prefer_synced, effectiveProfile?.prefer_synced_lyrics, effectiveProfile, yesNo) },
    { label: 'Download covers', ...inherited(album.override_download_covers, effectiveProfile?.download_covers, effectiveProfile, yesNo) },
    { label: 'Download lyrics', ...inherited(album.override_download_lyrics, effectiveProfile?.download_lyrics, effectiveProfile, yesNo) },
  ];

  const trackEmpty = tracks.length === 0 ? (
    <EmptyState size="sm" icon={faMusic} title="No track files synced for this album yet." />
  ) : (
    <EmptyState
      size="sm"
      icon={faMusic}
      title={`No ${TRACK_VIEWS.find(v => v.value === trackView)?.label.toLowerCase()} tracks`}
      action={<Button variant="default" onClick={() => setTrackView('all')}>Show all tracks</Button>}
    />
  );

  const trackTabs = (
    <ViewTabs
      aria-label="Track views"
      tabs={TRACK_VIEWS.map(v => ({
        value: v.value,
        label: v.label,
        count: v.value === 'all' ? tracks.length : stats[v.value],
      }))}
      value={trackView}
      onChange={setTrackView}
    />
  );

  return (
    <div className="ly-page">
      <Hero
        art={{
          src: album.cover,
          alt: album.title,
          kind: 'album',
          name: album.title,
          onClick: canZoom ? () => setLightboxOpen(true) : undefined,
        }}
        eyebrow={eyebrow}
        title={album.title}
        byline={byline}
        tags={genres}
        actions={actions}
      />

      <div className={c.layout} data-wide-side="">
        <div className={c.main}>
          <Panel
            padding="none"
            title="Tracks"
            subtitle={`${num(withLyrics)} of ${num(tracks.length)} with lyrics`}
            actions={tracks.length > 0 && !narrow ? trackTabs : undefined}
          >
            {tracks.length > 0 && narrow && <div className={c.panelTabsRow}>{trackTabs}</div>}
            <DataTable<TrackRow>
              aria-label={`Tracks of ${album.title}`}
              framed={false}
              columns={columns}
              rows={visibleTracks}
              getRowId={(t) => t.lidarrTrackId}
              getRowLabel={(t) => t.title}
              noun="track"
              empty={trackEmpty}
              breakpoints={{ 2: 520, 3: 640 }}
              onOpen={(t) => { if (t.lyrics_status !== 'blacklisted') setEditorTrack(t); }}
              renderGroupHeader={(t) => {
                const disc = discStarts.get(t.lidarrTrackId);
                return disc !== undefined ? <span className={c.disc}>Disc {disc}</span> : null;
              }}
              rowActionsWidth={156}
              rowActions={(t) => {
                const skipped = t.lyrics_status === 'blacklisted';
                const isInstrumental = t.lyrics_status === 'instrumental';
                const target = {
                  id: t.lidarrTrackId,
                  title: t.title,
                  hasLyrics: t.lyrics_status === 'available' || t.lyrics_status === 'manual',
                };
                return (
                  <>
                    {/* Instrumental rows keep search and edit: finding lyrics is how a wrong flag gets fixed. */}
                    <IconButton icon={faMagnifyingGlass} label="Search lyrics" disabled={skipped} onClick={() => setLyricsTrack(t)} />
                    <IconButton icon={faPenToSquare} label="Edit lyrics" disabled={skipped} onClick={() => setEditorTrack(t)} />
                    {skipped ? (
                      <IconButton
                        icon={faRotateLeft}
                        label="Unskip: allow lyrics downloads again"
                        loading={trackStatusMutation.isPending && trackStatusMutation.variables?.track.lidarrTrackId === t.lidarrTrackId}
                        onClick={() => trackStatusMutation.mutate({ track: t, status: 'missing' })}
                      />
                    ) : isInstrumental ? (
                      <IconButton
                        icon={faMicrophone}
                        label="Not instrumental — search again"
                        loading={instrumental.isBusy(`vocal-${t.lidarrTrackId}`)}
                        onClick={() => void instrumental.notInstrumental(target)}
                      />
                    ) : (
                      <IconButton icon={faBan} label="Skip (no lyrics)" onClick={() => askSkip(t)} />
                    )}
                    <Menu position="bottom-end" withinPortal>
                      <Menu.Target>
                        <IconButton icon={faEllipsis} label={`More actions for ${t.title}`} />
                      </Menu.Target>
                      <Menu.Dropdown>
                        {isInstrumental ? (
                          <>
                            <Menu.Item leftSection={<FontAwesomeIcon icon={faRepeat} />} onClick={() => void instrumental.recheck(target)}>
                              Re-check
                            </Menu.Item>
                            <Menu.Item leftSection={<FontAwesomeIcon icon={faBan} />} onClick={() => askSkip(t)}>
                              Skip (no lyrics)
                            </Menu.Item>
                          </>
                        ) : (
                          <Menu.Item leftSection={<FontAwesomeIcon icon={faMusic} />} onClick={() => void instrumental.markInstrumental(target)}>
                            Mark instrumental
                          </Menu.Item>
                        )}
                      </Menu.Dropdown>
                    </Menu>
                  </>
                );
              }}
            />
          </Panel>
        </div>

        <aside className={c.side} aria-label="Album status and settings">
          <Panel title="Status" headingLevel={3}>
            <div className={c.statusRows}>
              <div className={c.statusRow}>
                <span>Cover</span>
                <StatusChip domain="cover" status={album.cover_status} />
              </div>
              <div className={c.statusRow}>
                <span>Lyrics</span>
                <StatusChip domain="albumLyrics" status={albumLyricsStatus} fraction={[withLyrics, wanted]} />
              </div>
            </div>
            {wanted > 0 && (
              <>
                <div className={c.meterRow}>
                  <LyricsMeter
                    synced={stats.synced}
                    plain={stats.plain}
                    instrumental={stats.instrumental}
                    total={wanted}
                    size="md"
                    width="100%"
                    aria-label="Tracks with lyrics"
                  />
                </div>
                <ul className={c.legend} aria-label="Lyrics breakdown">
                  {([
                    ['Synced', stats.synced, 'success'],
                    ['Plain', stats.plain, 'info'],
                    ...(stats.instrumental ? [['Instrumental', stats.instrumental, 'neutral'] as const] : []),
                  ] as const).map(([label, value, tone]) => (
                    <li key={label} className={c.legendItem} data-zero={value === 0 || undefined}>
                      <span className={c.swatch} data-tone={tone} aria-hidden="true" />
                      {label}
                      <span className={c.legendCount}>{num(value)}</span>
                    </li>
                  ))}
                  <li className={c.legendItem} data-zero={missing === 0 || undefined}>
                    <span className={c.ring} aria-hidden="true" />
                    Missing
                    <span className={c.legendCount}>{num(missing)}</span>
                  </li>
                  {stats.skipped > 0 && (
                    <li className={c.legendItem}>
                      <FontAwesomeIcon icon={faBan} className={c.legendIcon} aria-hidden="true" />
                      Skipped
                      <span className={c.legendCount}>{num(stats.skipped)}</span>
                    </li>
                  )}
                </ul>
              </>
            )}
          </Panel>

          <Panel title="Settings" headingLevel={3}>
            <div className={c.field}>
              <Select
                label="Profile"
                placeholder={defaultProfile ? `None (uses ${defaultProfile.name})` : 'Select profile...'}
                data={profiles.map(p => ({ value: String(p.id), label: p.name }))}
                value={album.profileId ? String(album.profileId) : null}
                allowDeselect={false}
                disabled={assign.isPending}
                onChange={(v) => {
                  const p = profiles.find(x => String(x.id) === v);
                  if (p) assign.mutate({ target, profileId: p.id, profileName: p.name });
                }}
              />
              <p className={c.source}>
                {ownProfile
                  ? `Assigned to this album`
                  : defaultProfile
                    ? `Uses the default profile (${defaultProfile.name})`
                    : profileLabel((album as { profileName?: string }).profileName) ?? 'No profile'}
              </p>
            </div>
            <div className={c.field}>
              <KeyValueList items={settingsItems} />
            </div>
          </Panel>

          {album.overview && <AboutPanel text={album.overview} />}

          {album.path && (
            <Panel title="Folder" headingLevel={3}>
              <div className={c.folder}>
                <p className={c.folderPath}>{album.path}</p>
                <IconButton
                  icon={faCopy}
                  label="Copy path"
                  onClick={() => {
                    navigator.clipboard?.writeText(album.path).then(
                      () => notifications.show({ title: 'Copied', message: 'Folder path copied to the clipboard.' }),
                      () => notifyError('Copy failed', undefined, "The browser didn't allow clipboard access."),
                    );
                  }}
                />
              </div>
            </Panel>
          )}
        </aside>
      </div>

      <CoverSearchModal
        albumId={id}
        album={album}
        opened={coverSearchOpen}
        onClose={() => setCoverSearchOpen(false)}
      />

      {lyricsTrack && (
        <LyricsSearchModal
          trackId={lyricsTrack.lidarrTrackId}
          trackTitle={lyricsTrack.title}
          albumId={id}
          artistName={album.artistName}
          albumTitle={album.title}
          opened={!!lyricsTrack}
          onClose={() => setLyricsTrack(null)}
        />
      )}

      {editorTrack && (
        <LyricsEditorModal
          trackId={editorTrack.lidarrTrackId}
          trackTitle={editorTrack.title}
          albumId={id}
          artistName={album.artistName}
          albumTitle={album.title}
          opened={!!editorTrack}
          onClose={() => setEditorTrack(null)}
        />
      )}

      {lightboxOpen && album.cover && (
        <ImageLightbox src={album.cover} alt={album.title} onClose={() => setLightboxOpen(false)} />
      )}
    </div>
  );
}
