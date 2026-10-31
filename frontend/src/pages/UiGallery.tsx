/**
 * /__ui — dev-only gallery of every shared component in every state (DESIGN.md 7).
 * Reviewers and page engineers use it to check components in each theme and
 * density, including states the live library can't produce (running jobs, errors,
 * login, toasts…). Nothing here writes to the backend: the only request is a
 * read-only album page used for realistic titles and cover URLs.
 */
import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Anchor, Button, Checkbox, Group, Kbd, Loader, Menu, Modal, Notification, PasswordInput, Progress, Radio,
  SegmentedControl, Select, Switch, Tabs, TextInput, Textarea,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faArrowUpRightDots, faBan, faCircleExclamation, faClock, faClockRotateLeft, faCompactDisc, faDownload,
  faEllipsis, faImage, faInbox, faLanguage, faLayerGroup, faMagnifyingGlass, faPenToSquare, faPlug, faRotate,
  faRotateLeft, faTrash, faUpload, faUser, faWandMagicSparkles, faWaveSquare, faCheck, faMusic,
} from '@fortawesome/free-solid-svg-icons';
import { getAlbums } from '../api';
import type { Album } from '../types';
import { MockJobsProvider, type ActivityItem, type JobState, type JobsValue } from '../jobs';
import { ActivityDrawer } from '../components/shell/ActivityDrawer';
import { MobileTabBar } from '../components/shell/MobileTabBar';
import { NowRunningCard } from '../components/shell/NowRunningCard';
import { ShellContext, useShell, type ShellContextValue } from '../components/shell/ShellContext';
import { TopBar } from '../components/shell/TopBar';
import { themes, type ThemeId } from '../theme/themes';
import { useTheme } from '../theme/ThemeProvider';
import { duration, formatRetry, num, parseGenres, relTime } from '../format';
import {
  Banner, BulkBar, CardSkeleton, CoverArt, CoverTile, CoverageCard, DataTable, EmptyState, FilterBar, FilterPill,
  GridSkeleton, Hero, HeroSkeleton, IconButton, JobProgress, KeyValueList, LangTag, ListSkeleton, LyricsMeter, Meter,
  PageHeader, Pager, Panel, SaveBar, SettingsGroup, SettingsRow, SortMenu, StatusChip, StatusDot, StatusVocabulary,
  StatusGlyph, TableSkeleton, Tag, TitleCell, ViewTabs, useConfirm, ConfirmProvider, type Column,
} from '../components/ui';
import c from './UiGallery.module.css';
import detail from './LibraryDetail.module.css';

/* ------------------------------------------------------------------
   Sample data
   ------------------------------------------------------------------ */

interface SampleAlbum {
  id: number;
  title: string;
  artist: string;
  year: number;
  type: string;
  genres: string[];
  profile: string | null;
  cover: string | null;
  coverStatus: 'missing' | 'available' | 'manual';
  lyricsStatus: 'complete' | 'partial' | 'missing' | 'unknown';
  counts: { synced: number; plain: number; instrumental: number; total: number } | null;
  retryAfter: string | null;
  retryCount: number;
}

const FALLBACK: Omit<SampleAlbum, 'id' | 'cover' | 'coverStatus' | 'lyricsStatus' | 'counts' | 'retryAfter' | 'retryCount'>[] = [
  { title: 'Bossanova', artist: 'Pixies', year: 1990, type: 'Album', genres: ['Alternative Rock', 'Indie Rock'], profile: null },
  { title: 'Brand New Eyes', artist: 'Paramore', year: 2009, type: 'Album', genres: ['Alternative Rock', 'Pop Punk'], profile: 'Default' },
  { title: '空中キャンプ', artist: 'フィッシュマンズ', year: 1996, type: 'Album', genres: ['Dream Pop', 'Dub'], profile: 'Default' },
  { title: 'Working Title For The Album Secret Waters', artist: 'Cities Aviv', year: 2022, type: 'Album', genres: ['Experimental'], profile: null },
  { title: 'CHROMAKOPIA', artist: 'Tyler, The Creator', year: 2024, type: 'Album', genres: ['Hip Hop', 'Neo Soul', 'Hardcore Hip Hop', 'Jazz Rap', 'Funk', 'R&B'], profile: 'Default' },
  { title: 'To Be Kind', artist: 'Swans', year: 2014, type: 'Album', genres: ['Post-Rock', 'Experimental Rock'], profile: 'Default' },
  { title: 'FLYGOD', artist: 'Westside Gunn', year: 2016, type: 'Album', genres: ['Hip Hop'], profile: null },
  { title: 'Bread EP', artist: 'The Alchemist', year: 2018, type: 'EP', genres: ['Hip Hop'], profile: 'Default' },
  { title: 'Bright Green Field', artist: 'Squid', year: 2021, type: 'Album', genres: ['Math Rock', 'Post-Punk'], profile: null },
  { title: 'Braces', artist: 'fakemink', year: 2025, type: 'Single', genres: [], profile: null },
  { title: '新しい日の誕生', artist: 'フィッシュマンズ', year: 1991, type: 'EP', genres: ['Dub'], profile: 'Default' },
  { title: 'Burial', artist: 'Burial', year: 2006, type: 'Album', genres: ['Dubstep', 'Electronic'], profile: 'Default' },
];

const COVER_CYCLE: SampleAlbum['coverStatus'][] = ['missing', 'available', 'missing', 'manual', 'available', 'missing'];
const LYRIC_CYCLE: { status: SampleAlbum['lyricsStatus']; counts: SampleAlbum['counts'] }[] = [
  { status: 'missing', counts: { synced: 0, plain: 0, instrumental: 0, total: 14 } },
  { status: 'partial', counts: { synced: 5, plain: 2, instrumental: 0, total: 11 } },
  { status: 'complete', counts: { synced: 9, plain: 2, instrumental: 1, total: 12 } },
  { status: 'unknown', counts: { synced: 0, plain: 0, instrumental: 0, total: 0 } },
  { status: 'partial', counts: null },
  { status: 'missing', counts: { synced: 0, plain: 0, instrumental: 0, total: 10 } },
];

const HOUR = 3_600_000;

function buildSample(live: Album[] | undefined): SampleAlbum[] {
  const base = (live && live.length >= 6)
    ? live.map(a => ({
      title: a.title.replace(/^"(.*)"$/, '$1'),
      artist: a.artistName ?? 'Unknown artist',
      year: a.year,
      type: a.albumType || 'Album',
      genres: parseGenres(a.genres),
      profile: (a as Album & { profileName?: string }).profileName === 'None' ? null : ((a as Album & { profileName?: string }).profileName ?? null),
      cover: a.cover || null,
      id: a.lidarrAlbumId,
    }))
    : FALLBACK.map((a, i) => ({ ...a, cover: null as string | null, id: 1000 + i }));
  return base.slice(0, 12).map((a, i) => ({
    ...a,
    coverStatus: COVER_CYCLE[i % COVER_CYCLE.length],
    lyricsStatus: LYRIC_CYCLE[i % LYRIC_CYCLE.length].status,
    counts: LYRIC_CYCLE[i % LYRIC_CYCLE.length].counts,
    retryAfter: i % 3 === 1 ? new Date(Date.now() + (i + 2) * HOUR).toISOString() : null,
    retryCount: i % 3 === 1 ? 2 : i % 3 === 2 ? 1 : 0,
  }));
}

interface SampleTrack {
  id: number;
  disc: number;
  no: number;
  title: string;
  file: string;
  lang: string | null;
  seconds: number;
  status: 'available' | 'manual' | 'missing' | 'blacklisted' | 'instrumental';
  synced: boolean;
}

const TRACKS: SampleTrack[] = [
  { id: 1, disc: 1, no: 1, title: 'St. Chroma', file: '01 - St. Chroma.flac', lang: 'en', seconds: 197, status: 'available', synced: true },
  { id: 2, disc: 1, no: 2, title: 'Rah Tah Tah', file: '02 - Rah Tah Tah.flac', lang: 'en', seconds: 164, status: 'available', synced: false },
  { id: 3, disc: 1, no: 3, title: 'Noid', file: '03 - Noid.flac', lang: null, seconds: 284, status: 'missing', synced: false },
  { id: 4, disc: 1, no: 4, title: 'Darling, I', file: '04 - Darling, I.flac', lang: 'en', seconds: 253, status: 'manual', synced: true },
  { id: 5, disc: 2, no: 1, title: 'Interlude (Instrumental)', file: '2-01 - Interlude.flac', lang: null, seconds: 92, status: 'instrumental', synced: false },
  { id: 6, disc: 2, no: 2, title: 'Bring the Sun / Toussaint L’Ouverture', file: '2-02 - Bring the Sun.flac', lang: 'fr', seconds: 2045, status: 'blacklisted', synced: false },
  { id: 7, disc: 2, no: 3, title: '空中キャンプ', file: '2-03 - 空中キャンプ.flac', lang: 'ja', seconds: 301, status: 'missing', synced: false },
];

/* ------------------------------------------------------------------
   Small gallery helpers
   ------------------------------------------------------------------ */

function Section({ id, title, note, children }: { id: string; title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className={c.section} aria-labelledby={`${id}-title`}>
      <div className={c.sectionHead}>
        <h2 id={`${id}-title`} className={c.sectionTitle}>{title}</h2>
        {note && <span className={c.sectionNote}>{note}</span>}
      </div>
      {children}
    </section>
  );
}

function Demo({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={c.demo} data-wide={wide || undefined}>
      <div className="ly-overline">{label}</div>
      <div className={c.demoBody}>{children}</div>
    </div>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className={c.row}>{children}</div>;
}

const SURFACE_TOKENS = ['--ly-bg', '--ly-nav', '--ly-surface', '--ly-raised', '--ly-hover', '--ly-active', '--ly-accent-soft', '--ly-neutral-soft'];
const LINE_TOKENS = ['--ly-border', '--ly-border-strong', '--ly-control', '--ly-focus'];
const TEXT_TOKENS = ['--ly-text', '--ly-text-2', '--ly-muted', '--ly-accent-text'];
const TONE_TOKENS = ['success', 'warning', 'danger', 'info'];

function BrandTile({ size = 28 }: { size?: number }) {
  // Same geometry as the favicon (DESIGN.md 4.8); the shell's BrandMark replaces this.
  return (
    <span className={c.brand} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 32 32" width={size} height={size}>
        <rect x="8" y="10" width="16" height="2.6" rx="1.3" />
        <rect x="8" y="15" width="12" height="2.6" rx="1.3" />
        <rect x="8" y="20" width="8" height="2.6" rx="1.3" />
        <circle cx="22" cy="21.3" r="2.3" />
      </svg>
    </span>
  );
}

/* ------------------------------------------------------------------
   Shell live states: the real shell parts fed by MockJobsProvider
   ------------------------------------------------------------------ */

type MockKey = 'idle' | 'download' | 'sync' | 'failed' | 'busy';

function buildJobMocks(): Record<MockKey, Partial<JobsValue>> {
  const now = Date.now();
  const download: JobState = {
    active: true, label: 'Downloading metadata', kind: 'lyrics', type: 'download', determinate: true,
    parts: [{ label: 'Covers', unit: 'albums', done: 40, total: 40 }, { label: 'Lyrics', unit: 'tracks', done: 418, total: 1174 }],
    currentTitle: 'Noid · Tyler, The Creator', startedAt: now - 6 * 60_000, ratePerMin: 96,
  };
  const sync: JobState = {
    active: true, label: 'Syncing with Lidarr', kind: 'sync', type: 'sync', determinate: false, parts: [], startedAt: now - 20_000,
  };
  const translate: JobState = {
    active: true, label: 'Translating lyrics', kind: 'batch', type: 'translate', determinate: false,
    parts: [{ label: 'Tracks', unit: 'tracks', done: 0, total: 212 }], startedAt: now - 90_000,
  };
  const failed: JobState = {
    active: false, label: 'Download failed', kind: 'error', type: 'download', determinate: false, parts: [],
    startedAt: now - 4 * 60_000, error: 'Provider LRCLIB returned 503',
  };
  // Oldest first, as JobsProvider stores them (the drawer shows newest first).
  const raw: Omit<ActivityItem, 'id'>[] = [
    { kind: 'sync', tone: 'success', title: 'Sync complete: 498 artists, 1,919 albums', receivedAt: null },
    { kind: 'info', tone: 'success', title: 'Download complete', receivedAt: null },
    { kind: 'info', tone: 'neutral', title: 'Download started: 40 covers, 1,174 lyrics', receivedAt: now - 6 * 60_000 },
    { kind: 'cover', tone: 'neutral', title: 'CHROMAKOPIA', provider: 'Cover Art Archive', receivedAt: now - 6 * 60_000 + 5_000 },
    { kind: 'error', tone: 'danger', title: 'Download failed: Provider LRCLIB returned 503 for “Darling, I”', receivedAt: now - 3 * 60_000 },
    { kind: 'lyrics', tone: 'neutral', title: 'St. Chroma', provider: 'LRCLIB', receivedAt: now - 3 * 60_000 + 4_000 },
    { kind: 'health', tone: 'success', title: 'Lidarr is reachable again', receivedAt: now - 30_000 },
    { kind: 'batch', tone: 'warning', title: 'Translated 210 of 212 tracks (2 failed)', receivedAt: now - 12_000 },
    { kind: 'lyrics', tone: 'neutral', title: 'Noid', provider: 'LRCLIB', receivedAt: now - 4_000 },
  ];
  const items: ActivityItem[] = raw.map((it, i) => ({ ...it, id: `mock-${i}` }));
  return {
    idle: {},
    download: { running: [download], items, unseen: 3 },
    sync: { running: [sync], items: items.slice(0, 2), unseen: 1, unseenError: true },
    failed: { job: failed, items, unseen: 2, unseenError: true },
    busy: { running: [download, sync, translate], items, unseen: 5, unseenError: true },
  };
}

const MOCK_LABELS: Record<MockKey, string> = {
  idle: 'Idle',
  download: 'Running (determinate)',
  sync: 'Running (indeterminate)',
  failed: 'Failed (until seen)',
  busy: 'Three jobs at once',
};

/** Shell actions + job state for one mock (openActivity opens the gallery's mock drawer). */
function Mock({ shell, jobs, children }: { shell: ShellContextValue; jobs: Partial<JobsValue>; children: ReactNode }) {
  return (
    <ShellContext.Provider value={shell}>
      <MockJobsProvider value={jobs}>{children}</MockJobsProvider>
    </ShellContext.Provider>
  );
}

/**
 * NowRunningCard, rail ring, top bar (job line + Activity pip), mobile job bar +
 * tab bar and the Activity drawer, rendered with fixed job state. Clicking a
 * card or the Activity button opens the drawer with the same mock.
 */
function ShellStates() {
  const mocks = useMemo(buildJobMocks, []);
  const shell = useShell();
  const [drawer, setDrawer] = useState<MockKey | null>(null);
  const props = (k: MockKey) => ({ shell: { ...shell, openActivity: () => setDrawer(k) }, jobs: mocks[k] });
  const keys: MockKey[] = ['idle', 'download', 'sync', 'failed'];

  return (
    <>
      <div className={c.grid2}>
        <Demo label="Now-running card (sidebar, 240px)">
          <div className={c.sidebarFrame}>
            {keys.map(k => (
              <div key={k} className={c.stack}>
                <span className={c.frameCaption}>{MOCK_LABELS[k]}</span>
                <Mock {...props(k)}><NowRunningCard /></Mock>
              </div>
            ))}
          </div>
        </Demo>
        <div className={c.stack}>
          <Demo label="Rail ring (64px rail)">
            <div className={c.railFrame}>
              {keys.map(k => (
                <div key={k} className={c.railCell}>
                  <Mock {...props(k)}><NowRunningCard rail /></Mock>
                  <span className={c.frameCaption}>{MOCK_LABELS[k]}</span>
                </div>
              ))}
            </div>
          </Demo>
          <Demo label="Activity drawer">
            <Row>
              {(['download', 'busy', 'failed', 'idle'] as MockKey[]).map(k => (
                <Button key={k} size="xs" variant="default" onClick={() => setDrawer(k)}>
                  {k === 'idle' ? 'Empty' : MOCK_LABELS[k]}
                </Button>
              ))}
            </Row>
          </Demo>
        </div>
      </div>

      <Demo label="Top bar: job line (determinate) + Activity pip" wide>
        <div className={c.barFrame}>
          <Mock {...props('download')}><TopBar mobile={false} narrow={false} /></Mock>
        </div>
      </Demo>
      <Demo label="Top bar: job line (indeterminate) + error pip · narrow layout (search icon)" wide>
        <div className={c.barFrame}>
          <Mock {...props('sync')}><TopBar mobile={false} narrow /></Mock>
        </div>
      </Demo>

      <Demo label="Phone (390px): top bar, job bar above the tab bar" wide>
        <div className={c.phoneRow}>
          {(['download', 'sync'] as MockKey[]).map(k => (
            <div key={k} className={c.phoneFrame}>
              <Mock {...props(k)}>
                <TopBar mobile narrow />
                <div className={c.phoneBody}>{MOCK_LABELS[k]}</div>
                <MobileTabBar preview />
              </Mock>
            </div>
          ))}
        </div>
      </Demo>

      {drawer && (
        <Mock {...props(drawer)}>
          <ActivityDrawer opened onClose={() => setDrawer(null)} />
        </Mock>
      )}
    </>
  );
}

/* ------------------------------------------------------------------
   Page
   ------------------------------------------------------------------ */

export default function UiGalleryPage() {
  return (
    <ConfirmProvider>
      <Gallery />
    </ConfirmProvider>
  );
}

function Gallery() {
  const { preference, setPreference, theme } = useTheme();
  const confirm = useConfirm();
  const [density, setDensity] = useState<'comfortable' | 'compact'>('comfortable');

  const { data: live } = useQuery({
    queryKey: ['ui-gallery-albums'],
    queryFn: () => getAlbums({ page: 1, pageSize: 12, sortBy: 'title', sortDir: 'asc' }),
    staleTime: Infinity,
    retry: false,
  });
  const albums = useMemo(() => buildSample(live?.data), [live]);

  // Table state
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [cursorId, setCursorId] = useState<number | null>(null);
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'title', dir: 'asc' });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [view, setView] = useState('all');
  const [search, setSearch] = useState('');
  const [profile, setProfile] = useState<string | null>(null);
  const [monitored, setMonitored] = useState<string | null>('true');
  const [sortField, setSortField] = useState<{ value: string; dir: 'asc' | 'desc' }>({ value: 'title', dir: 'asc' });
  const [selectingAll, setSelectingAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set([TRACKS[0].id]));
  const [trackTab, setTrackTab] = useState('all');
  const [dirty, setDirty] = useState(false);
  const [lastConfirm, setLastConfirm] = useState<string>('—');
  const [modalOpen, setModalOpen] = useState(false);
  const [banners, setBanners] = useState(true);

  const sortedAlbums = useMemo(() => {
    const filtered = albums.filter(a => !search || a.title.toLowerCase().includes(search.toLowerCase()));
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => {
      if (sort.key === 'year') return (a.year - b.year) * dir;
      if (sort.key === 'artist') return a.artist.localeCompare(b.artist) * dir;
      return a.title.localeCompare(b.title) * dir;
    });
  }, [albums, search, sort]);

  const albumColumns: Column<SampleAlbum>[] = [
    {
      key: 'title', header: 'Album', sortKey: 'title', priority: 1, mobile: 'title',
      cell: a => (
        <TitleCell
          title={a.title}
          href={`/albums/${a.id}`}
          art={{ src: a.cover, kind: 'album', name: a.title }}
          secondary={[a.type, ...a.genres.slice(0, 2)].join(' · ')}
        />
      ),
    },
    { key: 'artist', header: 'Artist', sortKey: 'artist', priority: 2, width: '18%', cell: a => <span className={c.text2}>{a.artist}</span> },
    { key: 'year', header: 'Year', sortKey: 'year', priority: 3, width: 72, align: 'right', cell: a => a.year || '—' },
    { key: 'profile', header: 'Profile', priority: 3, width: 96, cell: a => a.profile ?? <span className={c.muted}>—</span> },
    { key: 'cover', header: 'Cover', priority: 1, width: 116, mobile: 'status', skeleton: 'chip', cell: a => <StatusDot domain="cover" status={a.coverStatus} /> },
    {
      key: 'lyrics', header: 'Lyrics', priority: 1, width: 210, mobile: 'status', skeleton: 'meter',
      cell: a => a.counts && a.counts.total > 0 ? (
        <span className={c.lyricsCell}>
          <LyricsMeter synced={a.counts.synced} plain={a.counts.plain} instrumental={a.counts.instrumental} total={a.counts.total} showCount={false} />
          <StatusDot
            domain="albumLyrics"
            status={a.lyricsStatus}
            fraction={[a.counts.synced + a.counts.plain + a.counts.instrumental, a.counts.total]}
          />
        </span>
      ) : <StatusDot domain="albumLyrics" status={a.lyricsStatus} />,
    },
  ];

  const albumActions = (a: SampleAlbum) => (
    <>
      <IconButton icon={faImage} label={`Search covers for ${a.title}`} size="md" />
      <IconButton icon={faAlignLeft} label={`Download missing lyrics for ${a.title}`} size="md" />
      <Menu position="bottom-end">
        <Menu.Target>
          <IconButton icon={faEllipsis} label="More actions" size="md" />
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item leftSection={<FontAwesomeIcon icon={faDownload} />}>Download all missing</Menu.Item>
          <Menu.Item leftSection={<FontAwesomeIcon icon={faUser} />}>Open artist</Menu.Item>
          <Menu.Divider />
          <Menu.Item color="danger" leftSection={<FontAwesomeIcon icon={faTrash} />}>Delete lyrics…</Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </>
  );

  const trackRows = TRACKS.filter(t => {
    if (trackTab === 'missing') return t.status === 'missing';
    if (trackTab === 'synced') return (t.status === 'available' || t.status === 'manual') && t.synced;
    if (trackTab === 'plain') return (t.status === 'available' || t.status === 'manual') && !t.synced;
    if (trackTab === 'skipped') return t.status === 'blacklisted';
    return true;
  });
  const trackColumns: Column<SampleTrack>[] = [
    { key: 'no', header: '#', width: 52, align: 'right', cell: t => <span className={c.muted}>{t.disc > 1 ? `${t.disc}.${t.no}` : t.no}</span> },
    { key: 'title', header: 'Title', mobile: 'title', cell: t => <TitleCell title={t.title} secondary={t.file} /> },
    { key: 'lang', header: 'Lang', width: 72, priority: 2, cell: t => <LangTag code={t.lang} /> },
    { key: 'time', header: 'Time', width: 80, align: 'right', priority: 2, cell: t => duration(t.seconds) },
    {
      key: 'lyrics', header: 'Lyrics', width: 140, mobile: 'status', skeleton: 'chip',
      cell: t => <StatusChip domain="trackLyrics" status={t.status} synced={t.synced} size="sm" />,
    },
  ];
  const trackActions = (t: SampleTrack) => (
    <>
      <IconButton icon={faMagnifyingGlass} label={`Search lyrics for ${t.title}`} size="md" disabled={t.status === 'blacklisted'} />
      <IconButton icon={faPenToSquare} label={`Edit lyrics for ${t.title}`} size="md" disabled={t.status === 'blacklisted'} />
      {t.status === 'blacklisted'
        ? <IconButton icon={faRotateLeft} label={`Unskip ${t.title}`} size="md" />
        : <IconButton icon={faBan} label={`Skip ${t.title}`} size="md" />}
    </>
  );

  const toneSwatch = (name: string): CSSProperties => ({ background: `var(${name})` });

  return (
    <div className={`ly-page ${c.page}`} data-density={density}>
      <PageHeader
        title="UI gallery"
        count={albums.length}
        subtitle={<>Every shared component in every state · <span className="ly-mono">components/ui</span> · dev only</>}
        actions={(
          <>
            <SegmentedControl
              aria-label="Gallery density"
              value={density}
              onChange={v => setDensity(v as 'comfortable' | 'compact')}
              data={[{ value: 'comfortable', label: 'Comfortable' }, { value: 'compact', label: 'Compact' }]}
            />
            <Select
              aria-label="Theme"
              value={preference}
              onChange={v => v && setPreference(v as ThemeId | 'system')}
              data={[{ value: 'system', label: 'System' }, ...themes.map(t => ({ value: t.id, label: t.name }))]}
              allowDeselect={false}
              w={180}
            />
          </>
        )}
      />

      <nav className={c.toc} aria-label="Gallery sections">
        {[
          ['tokens', 'Tokens'], ['buttons', 'Buttons'], ['status', 'Status'], ['meters', 'Meters'],
          ['coverage', 'Coverage'], ['feedback', 'Feedback'], ['images', 'Images'], ['hero', 'Hero'],
          ['layout', 'Layout'], ['lists', 'Tables'], ['display', 'Data display'], ['forms', 'Forms'],
          ['dialogs', 'Dialogs'], ['shell', 'Shell'], ['states', 'Live states'],
        ].map(([id, label]) => <a key={id} href={`#${id}`} className={c.tocLink}>{label}</a>)}
      </nav>

      {/* ---------------------------------------------------------------- */}
      <Section id="tokens" title="Tokens" note={`${theme.name} · ${theme.scheme}${theme.contrast === 'high' ? ' · high contrast' : ''}`}>
        <div className={c.themeStrip}>
          {themes.map(t => (
            <button
              key={t.id}
              type="button"
              className={c.themeChip}
              data-active={t.id === theme.id || undefined}
              onClick={() => setPreference(t.id)}
              aria-pressed={t.id === theme.id}
            >
              <span className={c.themeSwatch} aria-hidden="true">
                {t.swatch.map((col, i) => <span key={i} style={{ background: col }} />)}
              </span>
              {t.name}
              {t.contrast === 'high' && <Tag code>HC</Tag>}
            </button>
          ))}
        </div>
        <div className={c.grid4}>
          <Demo label="Surfaces">
            <div className={c.swatches}>
              {SURFACE_TOKENS.map(n => (
                <div key={n} className={c.swatch}><span className={c.swatchBox} style={toneSwatch(n)} /><code>{n}</code></div>
              ))}
            </div>
          </Demo>
          <Demo label="Lines & focus">
            <div className={c.swatches}>
              {LINE_TOKENS.map(n => (
                <div key={n} className={c.swatch}><span className={c.swatchLine} style={{ borderColor: `var(${n})` }} /><code>{n}</code></div>
              ))}
            </div>
          </Demo>
          <Demo label="Text">
            <div className={c.swatches}>
              {TEXT_TOKENS.map(n => (
                <div key={n} className={c.swatch}><span className={c.swatchText} style={{ color: `var(${n})` }}>Aa</span><code>{n}</code></div>
              ))}
            </div>
          </Demo>
          <Demo label="Accent, status, charts">
            <div className={c.swatches}>
              <div className={c.swatch}><span className={c.swatchBox} style={toneSwatch('--ly-accent')} /><code>--ly-accent</code></div>
              {TONE_TOKENS.map(t => (
                <div key={t} className={c.swatch}><span className={c.swatchBox} style={toneSwatch(`--ly-${t}`)} /><code>--ly-{t}</code></div>
              ))}
              {[1, 2, 3, 4].map(i => (
                <div key={i} className={c.swatch}><span className={c.swatchBox} style={toneSwatch(`--ly-chart-${i}`)} /><code>--ly-chart-{i}</code></div>
              ))}
            </div>
          </Demo>
        </div>
        <Demo label="Typography" wide>
          <div className={c.type}>
            <div className={c.tDisplay}>CHROMAKOPIA</div>
            <div className={c.tH1}>Albums <span className={c.tCount}>1,919</span></div>
            <div className={c.tH2}>Biggest gaps <span className={c.tMuted}>Artists with the most missing items</span></div>
            <div className={c.tBody}>Body 14/1.5 — The quick brown fox jumps over the lazy dog. <span className="num">0123456789</span></div>
            <div className={c.tSmall}>Secondary 13 · <span className="ly-link">Link text</span> · <Anchor href="#tokens">Anchor</Anchor></div>
            <div className="ly-overline">Overline · Library</div>
            <div className="ly-mono">/music/Tyler, The Creator/CHROMAKOPIA (2024)/03 - Noid.lrc  [00:12.48]</div>
          </div>
        </Demo>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="buttons" title="Buttons & inputs" note={<>A bare <code>{"<Button>"}</code> is secondary (<code>variant="default"</code>); the one primary per view opts in with <code>variant="filled"</code>.</>}>
        <div className={c.grid2}>
          <Demo label="Buttons">
            <Row>
              <Button variant="filled" leftSection={<FontAwesomeIcon icon={faPlug} />}>Connect Lidarr</Button>
              <Button leftSection={<FontAwesomeIcon icon={faDownload} />}>Download missing now</Button>
              <Button variant="light" leftSection={<FontAwesomeIcon icon={faDownload} />}>Find 1,919 covers</Button>
              <Button variant="subtle" color="gray">Cancel</Button>
            </Row>
            <Row>
              <Button variant="light" color="danger" leftSection={<FontAwesomeIcon icon={faTrash} />}>Delete lyrics…</Button>
              <Button variant="filled" color="danger">Delete lyrics</Button>
              <Button variant="filled" loading>Saving</Button>
              <Button variant="filled" disabled>Disabled primary</Button>
              <Button disabled>Disabled</Button>
            </Row>
            <Row>
              <Button size="xs">Extra small</Button>
              <Button size="sm">Small (default)</Button>
              <Button size="md">Medium</Button>
              <Button variant="outline">Outline</Button>
            </Row>
          </Demo>
          <Demo label="Icon buttons (aria-label + tooltip)">
            <Row>
              <IconButton icon={faMagnifyingGlass} label="Search providers" shortcut="s" />
              <IconButton icon={faPenToSquare} label="Edit lyrics" shortcut="e" />
              <IconButton icon={faBan} label="Skip track" shortcut="b" />
              <IconButton icon={faWaveSquare} label="Activity" />
              <IconButton icon={faEllipsis} label="More" variant="default" />
              <IconButton icon={faDownload} label="Download (disabled)" disabled />
              <IconButton icon={faRotate} label="Sync with Lidarr" variant="light" />
            </Row>
            <Row>
              <span className={c.text2}>Keys</span>
              <Kbd>Ctrl K</Kbd><Kbd>/</Kbd><Kbd>j</Kbd><Kbd>k</Kbd><Kbd>x</Kbd><Kbd>Esc</Kbd>
            </Row>
          </Demo>
          <Demo label="Inputs">
            <div className={c.formGrid}>
              <TextInput label="Instance name" placeholder="Lyrarr" description="Shown in the browser tab" />
              <TextInput label="Port" defaultValue="99999" error="Port must be between 1 and 65535" />
              <PasswordInput label="API key" defaultValue="0123456789abcdef" />
              <Select label="Cover format" data={['jpg', 'png', 'webp']} defaultValue="jpg" />
              <TextInput label="Disabled" defaultValue="Read only" disabled />
              <Textarea label="Notes" placeholder="Plain text…" autosize minRows={2} />
            </div>
          </Demo>
          <Demo label="Choices">
            <Row>
              <Checkbox label="Checked" defaultChecked />
              <Checkbox label="Unchecked" />
              <Checkbox label="Indeterminate" indeterminate readOnly />
              <Checkbox label="Disabled" disabled />
            </Row>
            <Row>
              <Radio.Group defaultValue="all" aria-label="Delete mode">
                <Group gap="md">
                  <Radio value="all" label="All lyrics" />
                  <Radio value="plain" label="Plain only" />
                  <Radio value="instrumental" label="Instrumental" />
                </Group>
              </Radio.Group>
            </Row>
            <Row>
              <Switch label="Download lyrics" defaultChecked />
              <Switch label="Overwrite existing" />
              <Switch label="Disabled" disabled />
            </Row>
            <Row>
              <SegmentedControl data={['Plain', 'Synced (LRC)']} defaultValue="Synced (LRC)" />
              <SegmentedControl data={[{ value: 'album', label: 'By album' }, { value: 'track', label: 'By track' }]} defaultValue="album" />
            </Row>
          </Demo>
          <Demo label="Tabs" wide>
            <Tabs defaultValue="status">
              <Tabs.List>
                <Tabs.Tab value="status">Status</Tabs.Tab>
                <Tabs.Tab value="providers">Providers</Tabs.Tab>
                <Tabs.Tab value="tasks">Tasks</Tabs.Tab>
                <Tabs.Tab value="logs">Logs</Tabs.Tab>
              </Tabs.List>
              <Tabs.Panel value="status" pt="md"><span className={c.text2}>Tab panel content.</span></Tabs.Panel>
              <Tabs.Panel value="providers" pt="md"><span className={c.text2}>Providers.</span></Tabs.Panel>
            </Tabs>
          </Demo>
        </div>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="status" title="Status" note="statusModel() → StatusChip (detail) / StatusDot (dense tables)">
        <Panel>
          <StatusVocabulary />
        </Panel>
        <div className={c.grid2}>
          <Demo label="Chip sizes, fractions, manual">
            <Row>
              <StatusChip domain="albumLyrics" status="partial" fraction={[7, 11]} size="sm" />
              <StatusChip domain="albumLyrics" status="partial" />
              <StatusChip domain="trackLyrics" status="manual" synced />
              <StatusChip domain="trackLyrics" status="manual" />
              <StatusChip domain="cover" status="missing" size="sm" />
              <StatusChip domain="cover" status="failed" />
              <StatusChip domain="job" status="queued" />
            </Row>
          </Demo>
          <Demo label="Dots: glyph only (label in tooltip)">
            <Row>
              <StatusDot domain="cover" status="available" hideLabel />
              <StatusDot domain="cover" status="manual" hideLabel />
              <StatusDot domain="cover" status="missing" hideLabel />
              <StatusDot domain="trackLyrics" status="available" synced hideLabel />
              <StatusDot domain="trackLyrics" status="blacklisted" hideLabel />
              <StatusDot domain="health" status="warning" hideLabel />
              <StatusDot domain="health" status="error" hideLabel />
            </Row>
          </Demo>
          <Demo label="Tags">
            <Row>
              <Tag>EP</Tag><Tag>Single</Tag><Tag>Default</Tag><Tag code>HC</Tag><Tag>Composite</Tag>
              <Tag>3 providers agree</Tag><Tag tone="warning">-55s off</Tag><Tag tooltip="Jazz Rap, Funk, R&B">+3</Tag>
            </Row>
          </Demo>
          <Demo label="Language tags">
            <Row>
              <LangTag code="en" /><LangTag code="ja" /><LangTag code="fr" /><LangTag code="pt-BR" /><LangTag code={null} />
            </Row>
          </Demo>
        </div>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="meters" title="Meters & progress" note="0% is hatched (not started), never red">
        <div className={c.grid2}>
          <Demo label="Meter">
            <div className={c.stack}>
              <Meter aria-label="Covers" total={1919} segments={[{ value: 0, tone: 'success', label: 'Saved' }]} size="md" remainderLabel="Missing" />
              <Meter aria-label="Covers" total={1919} segments={[{ value: 412, tone: 'success', label: 'Saved' }, { value: 38, tone: 'info', label: 'Manual' }]} size="md" remainderLabel="Missing" />
              <Meter aria-label="Covers" total={1919} segments={[{ value: 1919, tone: 'success', label: 'Saved' }]} size="md" />
              <Row>
                <Meter aria-label="Covers 0%" total={20} segments={[{ value: 0, tone: 'success', label: 'Saved' }]} size="xs" width={80} />
                <Meter aria-label="Lyrics 35%" total={20} segments={[{ value: 7, tone: 'success', label: 'Done' }]} size="xs" width={80} />
                <Meter aria-label="Score 96%" total={100} segments={[{ value: 96, tone: 'success', label: 'Score' }]} size="xs" width={60} withTooltip={false} />
                <Meter aria-label="Score 64%" total={100} segments={[{ value: 64, tone: 'warning', label: 'Score' }]} size="xs" width={60} withTooltip={false} />
                <Meter aria-label="Score 31%" total={100} segments={[{ value: 31, tone: 'danger', label: 'Score' }]} size="xs" width={60} withTooltip={false} />
              </Row>
            </div>
          </Demo>
          <Demo label="LyricsMeter (n/m)">
            <div className={c.stack}>
              <LyricsMeter synced={0} plain={0} total={14} width={160} />
              <LyricsMeter synced={5} plain={2} total={11} width={160} />
              <LyricsMeter synced={9} plain={2} instrumental={1} total={12} width={160} />
              <LyricsMeter synced={0} plain={0} total={0} width={160} />
              <Row>
                <LyricsMeter synced={3} plain={1} total={10} />
                <LyricsMeter synced={3} plain={1} total={10} size="xs" />
                <LyricsMeter synced={3} plain={1} total={10} size="md" />
              </Row>
            </div>
          </Demo>
          <Demo label="Running job (accent progress)">
            <div className={c.stack}>
              <JobProgress
                parts={[
                  { label: 'Covers', unit: 'albums', done: 12, total: 40 },
                  { label: 'Lyrics', unit: 'tracks', done: 418, total: 1174 },
                ]}
                eta="~4 min left"
              />
              <JobProgress parts={[]} indeterminate eta="Syncing with Lidarr…" />
            </div>
          </Demo>
          <Demo label="Mantine Progress / Loader">
            <div className={c.stack}>
              <Progress value={36} aria-label="Progress 36%" />
              <Progress value={100} aria-label="Progress 100%" />
              <Row><Loader /><Loader size="xs" /><Loader size="md" /><span className={c.text2}>Searching lyrics providers…</span></Row>
            </div>
          </Demo>
        </div>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="coverage" title="Coverage cards">
        <div className={c.grid3}>
          <CoverageCard
            icon={faImage}
            title="Cover art"
            done={0}
            total={1919}
            unit="albums"
            segments={[{ value: 0, tone: 'success', label: 'Saved', note: 'Includes manual uploads' }]}
            missingLabel="Missing"
            link={{ label: 'Review in Wanted', to: '/wanted?tab=covers' }}
            action={<Button variant="light" size="xs" leftSection={<FontAwesomeIcon icon={faDownload} />}>Find 1,919 covers</Button>}
            stateChip
          />
          <CoverageCard
            icon={faAlignLeft}
            title="Lyrics"
            done={9412}
            total={23849}
            unit="tracks"
            segments={[{ value: 6120, tone: 'success', label: 'Synced' }, { value: 3292, tone: 'info', label: 'Plain' }]}
            missingLabel="Missing"
            link={{ label: 'Review in Wanted', to: '/wanted?tab=lyrics' }}
            stateChip
            menu={(
              <Menu position="bottom-end">
                <Menu.Target><IconButton icon={faEllipsis} label="Lyrics actions" size="md" /></Menu.Target>
                <Menu.Dropdown>
                  <Menu.Item leftSection={<FontAwesomeIcon icon={faLanguage} />}>Re-detect languages</Menu.Item>
                  <Menu.Item leftSection={<FontAwesomeIcon icon={faUpload} />}>Import sidecar .lrc files</Menu.Item>
                  <Menu.Item leftSection={<FontAwesomeIcon icon={faCheck} />}>Audit lyrics state</Menu.Item>
                </Menu.Dropdown>
              </Menu>
            )}
          />
          <CoverageCard
            icon={faImage}
            title="Cover art"
            done={1919}
            total={1919}
            unit="albums"
            segments={[{ value: 1880, tone: 'success', label: 'Saved' }, { value: 39, tone: 'info', label: 'Manual' }]}
            missingLabel="Missing"
            link={{ label: 'Open Wanted', to: '/wanted' }}
            stateChip
          />
        </div>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="feedback" title="Feedback & empty states">
        <Panel padding="none" title="Needs attention" subtitle="Banners in a padding=&quot;none&quot; panel">
          <div className="ly-banner-list">
            <Banner
              tone="warning"
              icon={faPlug}
              title="Lidarr integration is off."
              message="Lyrarr is working from its last library snapshot."
              action={<Anchor href="#feedback" size="sm" fw={600}>Open Lidarr settings</Anchor>}
            />
            {banners && (
              <Banner
                tone="warning"
                title="Authentication is disabled."
                message="Enable it if Lyrarr is reachable beyond a trusted network."
                action={<Anchor href="#feedback" size="sm" fw={600}>Security settings</Anchor>}
                onDismiss={() => setBanners(false)}
              />
            )}
            <Banner tone="danger" title="All lyrics providers are disabled." message="LRCLIB, Genius and Musixmatch failed 5 times in a row." action={<Button size="xs">View providers</Button>} />
          </div>
        </Panel>
        <div className={c.grid2}>
          <Banner tone="info" title="2 providers temporarily disabled." message="They come back automatically at 14:20." />
          <Banner tone="success" title="Connected to Lidarr." message="Version 2.9.6 · 498 artists" />
          <Banner tone="neutral" title="Scheduler idle." message="Every next run is “Never”." />
          <Banner tone="danger" title="Invalid credentials" />
        </div>
        <div className={c.grid3}>
          <Panel padding="none"><EmptyState size="sm" icon={faDownload} title="Nothing downloaded in the last 30 days" message="Covers and lyrics fetched by the scheduler or by you will chart here." /></Panel>
          <Panel padding="none"><EmptyState size="sm" tone="success" icon={faCheck} title="Every track with lyrics is already synced" message="Nothing to time right now." /></Panel>
          <Panel padding="none">
            <EmptyState
              size="sm"
              tone="danger"
              icon={faCircleExclamation}
              title="Couldn't load albums"
              message="Request failed with status code 502"
              action={<Button leftSection={<FontAwesomeIcon icon={faRotate} />}>Retry</Button>}
            />
          </Panel>
        </div>
        <Panel padding="none">
          <EmptyState
            icon={faUser}
            title="No artists yet"
            message="Sync with Lidarr to see your library here."
            action={<Button variant="filled" leftSection={<FontAwesomeIcon icon={faPlug} />}>Connect Lidarr</Button>}
          />
        </Panel>
        <div className={c.grid2}>
          <Demo label="TableSkeleton (real header)">
            <TableSkeleton
              delay={0}
              rows={4}
              columns={[
                { kind: 'check', width: 44 }, { header: 'Album', kind: 'title' }, { header: 'Artist', kind: 'text', width: '22%' },
                { header: 'Cover', kind: 'chip', width: 110 }, { header: 'Lyrics', kind: 'meter', width: 110 },
              ]}
            />
          </Demo>
          <Demo label="CardSkeleton / ListSkeleton">
            <div className={c.grid2Inner}>
              <CardSkeleton delay={0} />
              <ListSkeleton delay={0} rows={4} />
            </div>
          </Demo>
          <Demo label="HeroSkeleton" wide>
            <HeroSkeleton delay={0} />
          </Demo>
          <Demo label="GridSkeleton" wide>
            <GridSkeleton delay={0} count={6} />
          </Demo>
        </div>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="images" title="Images" note="Missing or broken art falls back to a disc glyph / monogram, never alt text">
        <div className={c.grid2}>
          <Demo label="CoverArt sizes (live cover URLs)">
            <Row>
              {[28, 40, 64, 96, 140].map(s => (
                <CoverArt key={s} src={albums[1]?.cover} alt={albums[1]?.title ?? ''} kind="album" size={s} sleeve={s > 64} />
              ))}
            </Row>
          </Demo>
          <Demo label="Fallbacks">
            <Row>
              <CoverArt src={null} alt="No cover" kind="album" size={96} />
              <CoverArt src="/__ui/broken-image.jpg" alt="Broken cover" kind="album" size={96} />
              <CoverArt src={null} alt="" kind="artist" name="Westside Gunn" size={96} />
              <CoverArt src="data:image/jpeg;base64,AAAA" alt="2814" kind="artist" name="2814" size={96} />
              <CoverArt src={null} alt="" kind="artist" name="フィッシュマンズ" size={64} />
              <CoverArt src={null} alt="" kind="artist" name="Swans" size={40} />
              <CoverArt src={null} alt="" kind="artist" name="Tyler, The Creator" size={28} />
            </Row>
          </Demo>
          <Demo label="Clickable (lightbox trigger)">
            <Row>
              <CoverArt src={albums[4]?.cover} alt={albums[4]?.title ?? ''} kind="album" size={120} sleeve onClick={() => notifications.show({ message: 'Would open the lightbox' })} />
              <CoverArt src={null} alt="Missing cover" kind="album" size={120} sleeve onClick={() => undefined} />
            </Row>
          </Demo>
          <Demo label="Square artist / circle album overrides">
            <Row>
              <CoverArt src={null} alt="" kind="artist" name="Burial" size={72} shape="square" />
              <CoverArt src={albums[2]?.cover} alt="" kind="album" size={72} shape="circle" />
            </Row>
          </Demo>
        </div>
        <Demo label="CoverTile grid: status badges, selection, hover actions, round artist tiles, long titles" wide>
          <div className={c.tileGrid}>
            {albums.slice(0, 6).map((a, i) => (
              <CoverTile
                key={a.id}
                href={`/albums/${a.id}`}
                art={{ src: a.cover, alt: '', kind: 'album', name: a.title }}
                title={a.title}
                subtitle={`${a.artist} · ${a.year}`}
                cover={a.coverStatus}
                lyrics={a.lyricsStatus}
                lyricsFraction={a.counts ? [a.counts.synced + a.counts.plain + a.counts.instrumental, a.counts.total] : undefined}
                selected={i === 1}
                onSelectChange={() => undefined}
                actions={(
                  <>
                    <IconButton icon={faImage} label="Search covers" size="md" />
                    <IconButton icon={faDownload} label="Download missing" size="md" />
                  </>
                )}
              />
            ))}
            <CoverTile href="/artists/1" art={{ src: null, alt: '', kind: 'artist', name: 'Westside Gunn' }} title="Westside Gunn" subtitle="Monitored" />
            <CoverTile href="/artists/2" art={{ src: null, alt: '', kind: 'artist', name: 'フィッシュマンズ' }} title="フィッシュマンズ" subtitle="Unmonitored" />
          </div>
        </Demo>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="hero" title="Hero" note="Art tint uses the computed --ly-hero-scrim; off in HC themes">
        <Hero
          art={{ src: albums[4]?.cover, alt: `Cover of ${albums[4]?.title ?? ''}`, kind: 'album', name: albums[4]?.title ?? 'Album', onClick: () => undefined }}
          eyebrow={`Album · ${albums[4]?.year ?? 2024} · Monitored`}
          title={albums[4]?.title ?? 'CHROMAKOPIA'}
          byline={(
            <>
              {/* Same as Album detail: hero text never takes the accent (7.1). */}
              <a className={detail.artistLink} href="#hero">
                <CoverArt src={null} alt="" kind="artist" name={albums[4]?.artist} size={24} />
                {albums[4]?.artist}
              </a>
              <span>· 14 tracks · 53 min</span>
            </>
          )}
          tags={['Hip Hop', 'Neo Soul', 'Hardcore Hip Hop', 'Jazz Rap', 'Funk', 'R&B']}
          actions={(
            <>
              <Button variant="filled" leftSection={<FontAwesomeIcon icon={faAlignLeft} />}>Find lyrics for 14 tracks</Button>
              <Button leftSection={<FontAwesomeIcon icon={faImage} />}>Search covers</Button>
              <Button leftSection={<FontAwesomeIcon icon={faUpload} />}>Upload cover</Button>
              <Menu position="bottom-end">
                <Menu.Target><IconButton icon={faEllipsis} label="More album actions" variant="default" /></Menu.Target>
                <Menu.Dropdown>
                  <Menu.Item leftSection={<FontAwesomeIcon icon={faLanguage} />}>Translate lyrics</Menu.Item>
                  <Menu.Item leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />}>Generate timing</Menu.Item>
                </Menu.Dropdown>
              </Menu>
            </>
          )}
          aside={(
            <KeyValueList items={[
              { label: 'Cover', value: <StatusChip domain="cover" status="missing" size="sm" /> },
              { label: 'Lyrics', value: <LyricsMeter synced={3} plain={2} total={14} width={110} /> },
            ]} />
          )}
        />
        <Hero
          art={{ src: 'data:image/jpeg;base64,AAAB', alt: 'Westside Gunn', kind: 'artist', name: 'Westside Gunn' }}
          eyebrow="Artist · Unmonitored"
          title="Westside Gunn"
          byline={<><span>26 albums</span><span>·</span><span className="ly-mono">/music/music/Westside Gunn</span></>}
          actions={(
            <>
              <Button variant="filled" leftSection={<FontAwesomeIcon icon={faDownload} />}>Download all missing</Button>
              <Button>Download missing lyrics</Button>
            </>
          )}
        />
        <Hero
          art={{ src: null, alt: 'No cover', kind: 'album', name: '空中キャンプ' }}
          eyebrow="EP · 2025 · Unmonitored"
          title="空中キャンプ 〜 Working Title For The Album Secret Waters (Deluxe Edition, Remastered 2025, Binary EP 0101 0011)"
          byline={<span>フィッシュマンズ · 12 tracks · 1 h 4 min</span>}
          tags={['Dream Pop']}
        />
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="layout" title="Layout">
        <Panel>
          <PageHeader
            title="Albums"
            count={1919}
            subtitle="1,919 albums from 498 artists"
            actions={<SegmentedControl data={['List', 'Grid']} defaultValue="List" aria-label="View" />}
          />
          <PageHeader
            title="Home"
            subtitle="498 artists · 1,919 albums · 23,849 tracks · Lyrarr v1.6.0"
            actions={(
              <>
                <Button leftSection={<FontAwesomeIcon icon={faDownload} />}>Download missing now</Button>
                <Button variant="filled" leftSection={<FontAwesomeIcon icon={faPlug} />}>Connect Lidarr</Button>
              </>
            )}
          />
        </Panel>
        <div className={c.grid3}>
          <Panel title="Settings" subtitle="inherited values" actions={<IconButton icon={faEllipsis} label="Panel actions" size="md" />}>
            <span className={c.text2}>Panel padding md (= --ly-card-pad).</span>
          </Panel>
          <Panel title="About" padding="sm">
            <span className={c.text2}>Panel padding sm.</span>
          </Panel>
          <Panel title="Tracks" subtitle="0 of 14 with lyrics" padding="none">
            <div className={c.padded}><span className={c.text2}>Panel padding none: header gets a bottom border.</span></div>
          </Panel>
        </div>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="lists" title="Lists & tables" note="Click a row, then j/k/↑/↓ move · x / Shift+x select · Enter/o open · Esc clears">
        <div className={c.stack}>
          <ViewTabs
            aria-label="Album views"
            value={view}
            onChange={setView}
            tabs={[
              { value: 'all', label: 'All', count: 1919 },
              { value: 'cover', label: 'Missing cover', count: 1919 },
              { value: 'lyrics', label: 'Missing lyrics', count: 1919 },
              { value: 'partial', label: 'Partial', count: 0 },
              { value: 'complete', label: 'Complete', count: 0 },
            ]}
          />
          <FilterBar
            search={{ value: search, onChange: setSearch, placeholder: 'Filter by album title…' }}
            filters={(
              <>
                <FilterPill
                  label="Monitored"
                  value={monitored}
                  onChange={setMonitored}
                  options={[{ value: 'true', label: 'Monitored', count: 1404 }, { value: 'false', label: 'Unmonitored', count: 515 }]}
                />
                <FilterPill
                  label="Profile"
                  value={profile}
                  onChange={setProfile}
                  options={[{ value: '1', label: 'Default', count: 1404 }, { value: 'none', label: 'None', count: 515 }]}
                />
              </>
            )}
            right={(
              <SortMenu
                value={sortField.value}
                dir={sortField.dir}
                onChange={(value, dir) => setSortField({ value, dir })}
                options={[
                  { value: 'title', label: 'Title', dirLabels: { asc: 'A–Z', desc: 'Z–A' } },
                  { value: 'year', label: 'Year', dirLabels: { asc: 'Oldest first', desc: 'Newest first' } },
                  { value: 'added', label: 'Recently added' },
                  { value: 'lyricsStatus', label: 'Lyrics status' },
                ]}
              />
            )}
          />
          <DataTable
            aria-label="Albums"
            noun="album"
            columns={albumColumns}
            rows={sortedAlbums}
            getRowId={a => a.id}
            getRowLabel={a => a.title}
            selection={{ selected, onChange: setSelected }}
            selectAllMatching={{
              total: 1919,
              busy: selectingAll,
              onSelectAll: () => {
                setSelectingAll(true);
                window.setTimeout(() => {
                  const pageIds = sortedAlbums.map(a => a.id);
                  // Stand-in ids for the 1,919 matching rows (real pages fetch them in pages of 500).
                  setSelected(new Set([...pageIds, ...Array.from({ length: 1919 - pageIds.length }, (_, i) => -(i + 1))]));
                  setSelectingAll(false);
                }, 400);
              },
            }}
            cursor={{ id: cursorId, onChange: setCursorId }}
            onOpen={a => notifications.show({ message: `Would open ${a.title}` })}
            rowActions={albumActions}
            sort={{ key: sort.key, dir: sort.dir, onChange: (key, dir) => setSort({ key, dir }) }}
            empty={<EmptyState size="sm" icon={faCompactDisc} title="No albums match" message="Try a different title." action={<Button onClick={() => setSearch('')}>Clear filters</Button>} />}
            footer={<Pager page={page} pageSize={pageSize} total={1919} onPage={setPage} onPageSize={setPageSize} noun="albums" />}
          />
          <span className={c.text2}>
            Cursor: {cursorId === null ? 'none' : (albums.find(a => a.id === cursorId)?.title ?? cursorId)} · Selected: {num(selected.size)}
            {' '}· Select every row on this page to see the “select all matching” banner; any selection shows the bulk bar.
          </span>
        </div>

        <BulkBar count={selected.size} noun="album" onClear={() => setSelected(new Set())}>
          <Button variant="filled" size="xs" leftSection={<FontAwesomeIcon icon={faDownload} />}>Download missing</Button>
          <Button size="xs" leftSection={<FontAwesomeIcon icon={faImage} />}>Covers</Button>
          <Button size="xs" leftSection={<FontAwesomeIcon icon={faAlignLeft} />}>Lyrics</Button>
          <Menu position="top">
            <Menu.Target><Button size="xs" variant="subtle" color="gray">Set profile</Button></Menu.Target>
            <Menu.Dropdown><Menu.Item>Default</Menu.Item><Menu.Item>None</Menu.Item></Menu.Dropdown>
          </Menu>
        </BulkBar>

        <div className={c.grid2Wide}>
          <Panel title="Tracks" subtitle="2 of 7 with lyrics · disc groups, expandable rows, chips" padding="none">
            <div className={c.panelTabs}>
              <ViewTabs
                aria-label="Track views"
                value={trackTab}
                onChange={setTrackTab}
                tabs={[
                  { value: 'all', label: 'All', count: TRACKS.length },
                  { value: 'missing', label: 'Missing', count: TRACKS.filter(t => t.status === 'missing').length },
                  { value: 'plain', label: 'Plain', count: 1 },
                  { value: 'synced', label: 'Synced', count: 2 },
                  { value: 'skipped', label: 'Skipped', count: 1 },
                ]}
              />
            </div>
            <DataTable
              framed={false}
              breakpoints={{ 2: 520, 3: 640 }}
              aria-label="Tracks"
              noun="track"
              columns={trackColumns}
              rows={trackRows}
              getRowId={t => t.id}
              getRowLabel={t => t.title}
              onOpen={t => notifications.show({ message: `Would open the editor for ${t.title}` })}
              rowActions={trackActions}
              renderGroupHeader={t => {
                const i = trackRows.indexOf(t);
                return i === 0 || trackRows[i - 1].disc !== t.disc ? `Disc ${t.disc}` : null;
              }}
              expandable={{
                isExpanded: t => expanded.has(t.id),
                toggle: t => setExpanded(prev => {
                  const next = new Set(prev);
                  if (next.has(t.id)) next.delete(t.id); else next.add(t.id);
                  return next;
                }),
                render: t => (
                  <div className={c.expandBody}>
                    <span className="ly-mono">[00:12.48] {t.title} — preview line one</span>
                    <span className={c.muted}>Attempts: {formatRetry(null, 0)} · Added {relTime(Date.now() - 3 * HOUR)}</span>
                  </div>
                ),
              }}
              empty={<EmptyState size="sm" icon={faMusic} title="No tracks in this view" />}
            />
          </Panel>
          <div className={c.stack}>
            <Demo label="DataTable: loading">
              <DataTable aria-label="Loading example" columns={albumColumns.slice(0, 3)} rows={[] as SampleAlbum[]} getRowId={a => a.id} loading selection={{ selected: new Set(), onChange: () => undefined }} />
            </Demo>
            <Demo label="DataTable: refetching (dimmed)">
              <DataTable aria-label="Refetching example" columns={albumColumns.slice(0, 2)} rows={albums.slice(0, 2)} getRowId={a => a.id} refetching />
            </Demo>
            <Demo label="DataTable: error">
              <DataTable
                aria-label="Error example"
                noun="album"
                columns={albumColumns.slice(0, 2)}
                rows={[] as SampleAlbum[]}
                getRowId={a => a.id}
                error={{ message: 'Request failed with status code 502', onRetry: () => undefined }}
              />
            </Demo>
          </div>
        </div>

        <Demo label="Wanted-style rows: retry info, attempts" wide>
          <DataTable
            aria-label="Wanted covers"
            noun="album"
            columns={[
              albumColumns[0],
              { key: 'artist', header: 'Artist', width: '22%', priority: 2, cell: a => a.artist },
              { key: 'type', header: 'Type', width: 90, priority: 3, cell: a => <Tag>{a.type}</Tag> },
              {
                key: 'attempts', header: 'Attempts', width: 210, mobile: 'meta',
                cell: a => a.retryAfter
                  ? <span className={c.retry}><StatusGlyph glyph="alert" tone="warning" size={10} />{formatRetry(a.retryAfter, a.retryCount)}</span>
                  : <span className={c.text2}>{formatRetry(a.retryAfter, a.retryCount)}</span>,
              },
            ]}
            rows={albums.slice(0, 4)}
            getRowId={a => a.id}
            rowActions={a => (
              <>
                <IconButton icon={faMagnifyingGlass} label={`Search covers for ${a.title}`} size="md" />
                <IconButton icon={faDownload} label={`Download cover for ${a.title}`} size="md" />
              </>
            )}
            empty={<EmptyState size="sm" tone="success" icon={faCheck} title="Every album has cover art" />}
          />
        </Demo>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="display" title="Data display">
        <div className={c.grid2}>
          <Panel title="Settings" subtitle="KeyValueList with sources">
            <KeyValueList items={[
              { label: 'Profile', value: 'None', source: 'default profile: Default' },
              { label: 'Cover format', value: 'jpg', source: 'from profile Default' },
              { label: 'Prefer synced', value: 'Yes', source: 'override' },
              { label: 'Download lyrics', value: 'Yes', source: 'from profile Default' },
              { label: 'Folder', value: '/music/music/Tyler, The Creator/CHROMAKOPIA (2024)', mono: true },
              { label: 'Artist', value: 'Tyler, The Creator', href: '/artists/1' },
            ]} />
          </Panel>
          <Panel title="Status" subtitle="2 columns">
            <KeyValueList columns={2} items={[
              { label: 'Version', value: 'v1.6.0' },
              { label: 'Python', value: '3.13.1' },
              { label: 'OS', value: 'Linux' },
              { label: 'Lidarr', value: <StatusChip domain="health" status="disabled" size="sm" /> },
              { label: 'Auth', value: <StatusChip domain="health" status="warning" size="sm" /> },
              { label: 'Providers', value: <StatusChip domain="health" status="healthy" size="sm" /> },
            ]} />
          </Panel>
        </div>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="forms" title="Forms" note="SettingsRow + SaveBar (appears when dirty)">
        <Panel title="Profile editor" subtitle="Default" actions={<Tag>Default</Tag>}>
          <SettingsGroup title="Downloads">
            <SettingsRow label="Download cover art" htmlFor="g-covers" control={<Switch id="g-covers" defaultChecked onChange={() => setDirty(true)} aria-label="Download cover art" />} />
            <SettingsRow
              label="Overwrite existing files"
              description="Replace cover and lyrics files that already exist next to the music."
              htmlFor="g-overwrite"
              control={<Switch id="g-overwrite" onChange={() => setDirty(true)} aria-label="Overwrite existing files" />}
            />
          </SettingsGroup>
          <SettingsGroup title="Cover art">
            <SettingsRow
              label="Cover format"
              htmlFor="g-format"
              source="from profile Default"
              control={<Select id="g-format" data={['jpg', 'png', 'webp']} defaultValue="jpg" onChange={() => setDirty(true)} allowDeselect={false} />}
            />
            <SettingsRow
              label="Minimum score"
              description="Results below this score are never auto-selected."
              htmlFor="g-score"
              control={<TextInput id="g-score" defaultValue="70" onChange={() => setDirty(true)} />}
            />
          </SettingsGroup>
          <Row>
            <Button onClick={() => setDirty(d => !d)}>{dirty ? 'Mark clean' : 'Make dirty (shows the SaveBar)'}</Button>
          </Row>
          <SaveBar dirty={dirty} onSave={() => setDirty(false)} onDiscard={() => setDirty(false)} guard={false} />
        </Panel>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="dialogs" title="Dialogs" note={`Last confirm result: ${lastConfirm}`}>
        <Row>
          <Button onClick={async () => {
            const ok = await confirm({ title: 'Reset all retry timers?', message: 'Every wanted item becomes eligible on the next scheduled run.', confirmLabel: 'Reset timers' });
            setLastConfirm(ok ? 'confirmed' : 'cancelled');
          }}>Confirm (default)</Button>
          <Button variant="light" color="danger" onClick={async () => {
            const ok = await confirm({
              title: 'Skip lyrics for this track?',
              message: <>Skip lyrics for <em>Noid</em>? Its lyrics file is archived to version history and the downloader stops searching for it.</>,
              confirmLabel: 'Skip track',
              tone: 'danger',
            });
            setLastConfirm(ok ? 'confirmed' : 'cancelled');
          }}>Confirm (danger)</Button>
          <Button variant="light" color="danger" onClick={async () => {
            const ok = await confirm({
              title: 'Delete lyrics for 1,919 albums?',
              message: 'All lyrics files for the selected albums are archived to version history and removed.',
              confirmLabel: 'Delete lyrics',
              tone: 'danger',
              requireText: '1919',
            });
            setLastConfirm(ok ? 'confirmed' : 'cancelled');
          }}>Confirm (type to confirm)</Button>
          <Button onClick={() => setModalOpen(true)}>Modal with footer pattern</Button>
        </Row>
        <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title="Translate lyrics">
          <div className={c.stack}>
            <Select label="Target language" data={['English', 'Japanese', 'French']} defaultValue="English" />
            <Checkbox label="Force re-translate tracks that already have a translation" />
            <Group justify="flex-end" gap="xs" mt="md">
              <Button variant="subtle" color="gray" onClick={() => setModalOpen(false)}>Cancel</Button>
              <Button variant="filled" onClick={() => setModalOpen(false)}>Translate 3 albums</Button>
            </Group>
          </div>
        </Modal>
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="shell" title="Shell live states" note="The real shell components fed by MockJobsProvider (jobs.tsx): no backend jobs needed">
        <ShellStates />
      </Section>

      {/* ---------------------------------------------------------------- */}
      <Section id="states" title="Live states" note="Rendered with mock data: the snapshot can't produce these">
        <div className={c.grid3}>
          <Demo label="Toasts (static)">
            <div className={c.stack}>
              <Notification color="success" title="Saved synced lyrics from LRCLIB" withCloseButton={false}>Noid · Tyler, The Creator</Notification>
              <Notification color="warning" title="Auto-search paused" withCloseButton={false}>3 searches in a row returned nothing.</Notification>
              <Notification color="danger" title="Download failed" onClose={() => undefined}>Request failed with status code 500</Notification>
              <Notification title="Sync started" withCloseButton={false}>Syncing with Lidarr…</Notification>
              <Notification color="info" title="Lidarr health changed" withCloseButton={false}>Lidarr is reachable again.</Notification>
            </div>
          </Demo>
          <Demo label="Live toasts (client only)">
            <Row>
              <Button size="xs" onClick={() => notifications.show({ title: 'Download complete', message: '12 covers, 418 lyrics', color: 'success' })}>Success</Button>
              <Button size="xs" onClick={() => notifications.show({ title: 'Not started', message: 'A download is already running.', color: 'warning' })}>Warning</Button>
              <Button size="xs" onClick={() => notifications.show({ title: 'Download failed', message: 'Request failed with status code 500', color: 'danger', autoClose: false })}>Error (sticky)</Button>
              <Button size="xs" onClick={() => notifications.show({ title: 'Download started', message: 'Fetching missing metadata…' })}>Neutral</Button>
            </Row>
          </Demo>
          <Demo label="Health checks">
            <div className={c.healthList}>
              {[
                { s: 'disabled', t: 'Lidarr', r: 'Integration is off' },
                { s: 'warning', t: 'Authentication', r: 'Disabled: anyone on the network can change settings' },
                { s: 'healthy', t: 'Providers', r: '9 of 9 available' },
                { s: 'error', t: 'Lidarr', r: 'Connection refused (unreachable)' },
              ].map((h, i) => (
                <div key={i} className={c.healthRow}>
                  <StatusDot domain="health" status={h.s} hideLabel />
                  <span className={c.healthTitle}>{h.t}</span>
                  <span className={c.healthReason}>{h.r}</span>
                  <StatusChip domain="health" status={h.s} size="sm" />
                </div>
              ))}
            </div>
          </Demo>
          <Panel title="Job states">
            <div className={c.stack}>
              <Banner tone="danger" icon={faCircleExclamation} title="Download failed" message="Provider LRCLIB returned 503." action={<Button size="xs">View details</Button>} />
              <Banner tone="success" title="Download complete" message="12 covers · 418 lyrics" />
              <Row>
                <StatusChip domain="job" status="running" />
                <StatusChip domain="job" status="done" />
                <StatusChip domain="job" status="failed" />
                <StatusChip domain="job" status="idle" />
              </Row>
              <Row>
                {[faImage, faAlignLeft, faRotate, faLanguage, faWandMagicSparkles, faArrowUpRightDots, faInbox, faLayerGroup, faClockRotateLeft, faClock].map((ic, i) => (
                  <span key={i} className={c.jobIcon}><FontAwesomeIcon icon={ic} /></span>
                ))}
              </Row>
            </div>
          </Panel>
          <Demo label="Login card">
            <form className={c.login} onSubmit={e => e.preventDefault()} aria-label="Sign in (mock)">
              <BrandTile size={40} />
              <h2 className={c.loginTitle}>Sign in to Lyrarr</h2>
              <TextInput label="Username" autoComplete="off" defaultValue="admin" />
              <PasswordInput label="Password" autoComplete="off" defaultValue="hunter2" />
              <Banner tone="danger" title="Invalid credentials" />
              <Button type="submit" variant="filled" fullWidth>Sign in</Button>
            </form>
          </Demo>
        </div>
        <Demo label="Auth check: server unreachable" wide>
          <div className={c.authWait}>
            <Loader size="lg" />
            <span className={c.text2} role="status">Can't reach the Lyrarr server — retrying…</span>
            <Button variant="subtle" color="gray" size="xs">Retry now</Button>
          </div>
        </Demo>
      </Section>
    </div>
  );
}
