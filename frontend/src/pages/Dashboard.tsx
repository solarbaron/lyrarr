import type { ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Menu, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faCircleExclamation, faDownload, faEllipsis, faFileImport, faImage, faLanguage,
  faListCheck, faPlug, faRotate, faRotateRight,
} from '@fortawesome/free-solid-svg-icons';
import { notifyError } from '../notify';
import { ARTIST_TOTAL_KEY } from '../queryRefresh';
import {
  getArtists, getSystemStatus, triggerSync, getDashboardStats, getLanguageStats, getWantedStats, getHealth,
  getProviderHealth, getProfiles, getTasks, runTask, batchRedetectLanguages, importSidecarLyrics, getAuditSummary,
} from '../api';
import type { AuditNavState } from './system/AuditTab';
import { CardSkeleton, CoverageCard, EmptyState, IconButton, PageHeader, Panel } from '../components/ui';
import { plural } from '../format';
import { AttentionPanel } from './home/AttentionPanel';
import { LibraryCard } from './home/LibraryCard';
import { BiggestGaps } from './home/BiggestGaps';
import { DownloadsChart } from './home/DownloadsChart';
import { LyricsBreakdown } from './home/LyricsBreakdown';
import { JumpBackIn } from './home/JumpBackIn';
import type { DownloadDay, LanguageStats, ProviderHealthEntry, ServiceHealth, WantedStats } from './home/types';
import c from './home/Home.module.css';

interface Task { job_id: string; job_running?: boolean }

function CardError({ title, onRetry }: { title: string; onRetry: () => void }) {
  return (
    <Panel className={c.cardError}>
      <EmptyState
        size="sm"
        tone="danger"
        icon={faCircleExclamation}
        title={title}
        action={<Button variant="default" size="xs" leftSection={<FontAwesomeIcon icon={faRotateRight} />} onClick={onRetry}>Retry</Button>}
      />
    </Panel>
  );
}

export default function DashboardPage() {
  const queryClient = useQueryClient();
  // Same key and request as the sidebar count and the Artists "All" tab, so it is fetched once.
  const { data: artistsResult } = useQuery({
    queryKey: ARTIST_TOTAL_KEY,
    queryFn: () => getArtists({ page: 1, pageSize: 1 }),
    staleTime: 60_000,
  });
  const { data: status, isLoading: statusLoading } = useQuery({ queryKey: ['system-status'], queryFn: getSystemStatus });
  const wanted = useQuery({ queryKey: ['wanted-stats'], queryFn: () => getWantedStats() as Promise<WantedStats> });
  const lang = useQuery({ queryKey: ['language-stats'], queryFn: () => getLanguageStats() as Promise<LanguageStats> });
  const dash = useQuery({ queryKey: ['dashboard-stats'], queryFn: getDashboardStats });
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: () => getHealth() as Promise<Record<string, ServiceHealth>> });
  const { data: providerHealth } = useQuery({
    queryKey: ['provider-health'],
    queryFn: () => getProviderHealth() as Promise<{ providers?: Record<string, ProviderHealthEntry> }>,
  });
  const { data: profiles } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });
  const { data: tasks } = useQuery({ queryKey: ['tasks'], queryFn: () => getTasks() as Promise<Task[]> });
  // Library audit summary for the attention banners; absent (404) on servers without the audit.
  const { data: auditSummary } = useQuery({ queryKey: ['audit', 'summary'], queryFn: getAuditSummary, retry: false, staleTime: 60_000 });
  const navigate = useNavigate();

  const syncMutation = useMutation({
    mutationFn: triggerSync,
    onSuccess: () => {
      notifications.show({ title: 'Sync started', message: 'Syncing artists and albums from Lidarr…' });
      setTimeout(() => {
        for (const key of ['nav-counts', 'artists', 'albums', 'wanted-stats', 'dashboard-stats', 'dashboard-artists', 'dashboard-albums', 'dashboard-tracks', 'wanted-covers', 'wanted-lyrics']) {
          queryClient.invalidateQueries({ queryKey: [key] });
        }
      }, 5000);
    },
    onError: (err) => notifyError('Sync failed to start', err),
  });

  const downloadMutation = useMutation({
    mutationFn: () => runTask('download_metadata'),
    onSuccess: () => {
      notifications.show({ title: 'Download started', message: 'Fetching missing covers and lyrics in the background.' });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
    // A 409 carries the server's reason (e.g. the task is already running).
    onError: (err) => notifyError('Couldn’t start the download', err),
  });

  const redetectMutation = useMutation({
    mutationFn: () => batchRedetectLanguages(),
    onSuccess: () => {
      notifications.show({ title: 'Started', message: 'Re-detecting languages for all lyrics…' });
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['language-stats'] }), 10000);
    },
    onError: (err) => notifyError('Failed to start language detection', err),
  });

  const importSidecar = () => {
    importSidecarLyrics().then(() => {
      notifications.show({ title: 'Started', message: 'Scanning for existing .lrc sidecar files…' });
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['language-stats'] }), 15000);
    }).catch((err) => notifyError('Failed to start sidecar import', err));
  };

  // "Check library…": System › Audit, which starts a dry run (nothing changes until fixes are applied).
  const checkLibrary = () => navigate('/system?tab=audit', { state: { startAudit: true } satisfies AuditNavState });

  const w = wanted.data;
  const ls = lang.data;
  const downloadRunning = !!tasks?.find(t => t.job_id === 'download_metadata')?.job_running;
  const lidarrEnabled: boolean | undefined = status?.lidarr_enabled;

  const facts = [
    artistsResult && plural(artistsResult.total, 'artist'),
    w && plural(w.total_albums, 'album'),
    w && plural(w.total_tracks, 'track'),
    status?.version && `Lyrarr v${status.version}`,
  ].filter(Boolean);

  const downloadButton = (
    <Button
      variant="default"
      leftSection={<FontAwesomeIcon icon={faDownload} />}
      onClick={() => downloadMutation.mutate()}
      loading={downloadMutation.isPending}
      disabled={downloadRunning}
    >
      Download missing now
    </Button>
  );

  // State-aware primary: connect Lidarr while it's off, otherwise sync with it.
  // Disabled only while the status loads: if it can't be fetched, Sync stays usable
  // (as before the overhaul) and the server explains any failure in a toast.
  const primary = lidarrEnabled === false ? (
    <Button variant="filled" component={Link} to="/settings#lidarr" leftSection={<FontAwesomeIcon icon={faPlug} />}>
      Connect Lidarr
    </Button>
  ) : (
    <Button
      variant="filled"
      leftSection={<FontAwesomeIcon icon={faRotate} />}
      onClick={() => syncMutation.mutate()}
      loading={syncMutation.isPending}
      disabled={statusLoading}
    >
      Sync with Lidarr
    </Button>
  );

  const lyricsMenu = (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <IconButton icon={faEllipsis} label="Lyrics actions" variant="subtle" size="md" />
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item
          leftSection={<FontAwesomeIcon icon={faLanguage} />}
          onClick={() => redetectMutation.mutate()}
          disabled={redetectMutation.isPending}
        >
          Re-detect languages
        </Menu.Item>
        <Menu.Item leftSection={<FontAwesomeIcon icon={faFileImport} />} onClick={importSidecar}>
          Import sidecar .lrc files
        </Menu.Item>
        <Menu.Item leftSection={<FontAwesomeIcon icon={faListCheck} />} onClick={checkLibrary}>
          Check library…
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );

  let cards: ReactNode;
  if (wanted.isError) {
    cards = (
      <>
        <CardError title="Couldn’t load cover coverage" onRetry={() => wanted.refetch()} />
        <CardError title="Couldn’t load lyrics coverage" onRetry={() => { wanted.refetch(); lang.refetch(); }} />
      </>
    );
  } else if (!w) {
    cards = <><CardSkeleton height={260} /><CardSkeleton height={260} /></>;
  } else {
    cards = (
      <>
        <CoverageCard
          icon={faImage}
          title="Cover art"
          done={w.covers_complete}
          total={w.total_albums}
          unit="albums"
          segments={[{ value: w.covers_complete, tone: 'success', label: 'Saved (incl. manual)' }]}
          missingLabel="Missing"
          missingCount={w.missing_covers}
          stateChip
          link={{ label: 'Review in Wanted', to: '/wanted?tab=covers' }}
        />
        <CoverageCard
          icon={faAlignLeft}
          title="Lyrics"
          done={w.lyrics_complete}
          total={w.total_tracks}
          unit="tracks"
          segments={[
            { value: ls?.synced ?? 0, tone: 'success', label: 'Synced' },
            { value: ls?.plain ?? 0, tone: 'info', label: 'Plain' },
          ]}
          missingLabel="Missing"
          missingCount={w.missing_lyrics}
          stateChip
          menu={lyricsMenu}
          link={{ label: 'Review in Wanted', to: '/wanted?tab=lyrics' }}
        />
      </>
    );
  }

  const gaps = dash.data?.artistCompletion ?? [];
  const history = (dash.data?.downloadHistory ?? []) as DownloadDay[];

  return (
    <div className={`ly-page ${c.page}`}>
      <PageHeader
        title="Home"
        subtitle={facts.length ? facts.join(' · ') : undefined}
        actions={(
          <div className={c.headerActions}>
            {downloadRunning
              ? <Tooltip label="The download task is already running"><span>{downloadButton}</span></Tooltip>
              : downloadButton}
            {primary}
          </div>
        )}
      />

      <AttentionPanel
        lidarrEnabled={lidarrEnabled}
        health={health}
        providers={providerHealth?.providers}
        audit={auditSummary}
        wanted={w}
      />

      <div className={c.cards}>
        {cards}
        {w || !wanted.isLoading ? (
          <LibraryCard
            artists={artistsResult?.total}
            albums={w?.total_albums}
            tracks={w?.total_tracks}
            profiles={profiles?.length}
            untimed={w?.untimed_lyrics}
            undetected={w?.undetected_language}
          />
        ) : <CardSkeleton height={260} />}
      </div>

      <div className={c.split}>
        {dash.isError ? (
          <>
            <CardError title="Couldn’t load artist gaps" onRetry={() => dash.refetch()} />
            <CardError title="Couldn’t load downloads" onRetry={() => dash.refetch()} />
          </>
        ) : dash.isLoading ? (
          <>
            <CardSkeleton height={420} />
            <CardSkeleton height={300} />
          </>
        ) : (
          <>
            {gaps.length > 0 ? <BiggestGaps artists={gaps} /> : (
              <Panel title="Biggest gaps">
                <EmptyState size="sm" tone="success" icon={faListCheck} title="No gaps" message="Every artist has its covers and lyrics." />
              </Panel>
            )}
            <DownloadsChart data={history} />
          </>
        )}
      </div>

      {ls && ls.total_available > 0 && <LyricsBreakdown stats={ls} />}

      <JumpBackIn />
    </div>
  );
}
