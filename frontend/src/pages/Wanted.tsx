import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Menu } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faClockRotateLeft, faDownload, faEllipsis, faLanguage } from '@fortawesome/free-solid-svg-icons';
import { getWantedStats, runTask, resetLyricsRetry, batchRedetectLanguages } from '../api';
import { notifyError } from '../notify';
import { num, pct } from '../format';
import { useUrlPatch, useUrlState } from '../hooks/useUrlState';
import { IconButton, Meter, PageHeader, ViewTabs, useConfirm } from '../components/ui';
import { LyricsTab } from './wanted/LyricsTab';
import { CoversTab } from './wanted/CoversTab';
import { UntimedTab } from './wanted/UntimedTab';
import { LanguageTab } from './wanted/LanguageTab';
import { InstrumentalTab } from './wanted/InstrumentalTab';
import { refreshWanted, type WantedStats } from './wanted/shared';
import c from './wanted/Wanted.module.css';

type Tab = 'lyrics' | 'covers' | 'untimed' | 'language' | 'instrumental';
const TABS: Tab[] = ['lyrics', 'covers', 'untimed', 'language', 'instrumental'];

function Coverage({ label, done, total, unit }: { label: string; done: number; total: number; unit: string }) {
  const p = pct(done, total);
  return (
    <span className={c.coverage}>
      <span className={c.coverageLabel}>{label}</span>
      <Meter
        segments={[{ value: done, tone: 'success', label: label === 'Covers' ? 'Saved' : 'With lyrics' }]}
        total={total}
        size="xs"
        width={80}
        remainderLabel="Missing"
        aria-label={`${label} coverage: ${num(done)} of ${num(total)} ${unit}`}
      />
      <span className={c.coveragePct}>{p}%</span>
    </span>
  );
}

/** Wanted triage workspace (DESIGN.md 8.6). */
export default function WantedPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const patchUrl = useUrlPatch();
  const [rawTab] = useUrlState('tab', 'lyrics');
  const tab: Tab = (TABS as string[]).includes(rawTab) ? rawTab as Tab : 'lyrics';

  const { data: stats } = useQuery<WantedStats>({ queryKey: ['wanted-stats'], queryFn: getWantedStats });

  const downloadNow = useMutation({
    mutationFn: () => runTask('download_metadata'),
    onSuccess: (r: { message?: string }) => {
      notifications.show({ title: 'Download started', message: r?.message || 'Downloading missing cover art and lyrics…' });
    },
    onError: (err) => notifyError('Download failed to start', err),
  });

  const resetRetries = useMutation({
    mutationFn: () => resetLyricsRetry({ all: true }),
    onSuccess: (r: { message?: string }) => {
      notifications.show({ title: 'Retry timers reset', message: r?.message || 'Every missing track is eligible on the next run.', color: 'success' });
      refreshWanted(queryClient);
    },
    onError: (err) => notifyError('Failed to reset retry timers', err),
  });

  const redetect = useMutation({
    mutationFn: () => batchRedetectLanguages(),
    onSuccess: (r: { message?: string }) => {
      notifications.show({ title: 'Started', message: r?.message || 'Detecting languages for every track with lyrics…' });
      refreshWanted(queryClient);
    },
    onError: (err) => notifyError('Error', err, 'Failed to start language detection'),
  });

  const onResetRetries = async () => {
    const ok = await confirm({
      title: 'Reset all retry timers?',
      message: 'Tracks waiting for their next automatic lyrics search become eligible again on the next run. Use this after adding or fixing a provider.',
      confirmLabel: 'Reset timers',
    });
    if (ok) resetRetries.mutate();
  };

  const onRedetect = async () => {
    const ok = await confirm({
      title: 'Re-detect languages?',
      message: 'Detects the language of every track that has lyrics. This runs in the background and can take a while on a large library.',
      confirmLabel: 'Re-detect languages',
    });
    if (ok) redetect.mutate();
  };

  const total = stats ? (stats.missing_covers || 0) + (stats.missing_lyrics || 0) : undefined;

  const tabs = [
    { value: 'lyrics', label: 'Lyrics', count: stats?.missing_lyrics },
    { value: 'covers', label: 'Covers', count: stats?.missing_covers },
    { value: 'untimed', label: 'Untimed', count: stats?.untimed_lyrics },
    { value: 'language', label: 'Undetected language', count: stats?.undetected_language },
    { value: 'instrumental', label: 'Instrumental review', count: stats?.instrumental_review },
  ];

  return (
    <div className="ly-page">
      <PageHeader
        title="Wanted"
        count={total}
        subtitle={stats ? (
          <span className={c.coverageRow}>
            <Coverage label="Covers" done={stats.covers_complete || 0} total={stats.total_albums || 0} unit="albums" />
            <span className={c.dot} aria-hidden="true">·</span>
            <Coverage label="Lyrics" done={stats.lyrics_complete || 0} total={stats.total_tracks || 0} unit="tracks" />
          </span>
        ) : undefined}
        actions={(
          <div className={c.headerActions}>
            <Menu position="bottom-end">
              <Menu.Target>
                <IconButton icon={faEllipsis} label="More actions" variant="default" />
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<FontAwesomeIcon icon={faClockRotateLeft} />} onClick={() => void onResetRetries()}>
                  Reset all retry timers
                </Menu.Item>
                <Menu.Item leftSection={<FontAwesomeIcon icon={faLanguage} />} onClick={() => void onRedetect()}>
                  Re-detect languages
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <Button
              variant="filled"
              leftSection={<FontAwesomeIcon icon={faDownload} />}
              loading={downloadNow.isPending}
              onClick={() => downloadNow.mutate()}
            >
              Download missing now
            </Button>
          </div>
        )}
      />

      <ViewTabs
        aria-label="Wanted views"
        tabs={tabs}
        value={tab}
        controls="wanted-panel"
        onChange={v => patchUrl({ tab: [v, 'lyrics'], q: ['', ''], page: [1, 1], iview: ['', ''], reason: ['', ''], why: ['', ''] })}
        className={c.tabs}
      />

      <div id="wanted-panel" role="tabpanel" aria-label={tabs.find(t => t.value === tab)?.label} className={c.panel}>
        {tab === 'lyrics' && <LyricsTab stats={stats} />}
        {tab === 'covers' && <CoversTab stats={stats} />}
        {tab === 'untimed' && <UntimedTab stats={stats} />}
        {tab === 'language' && <LanguageTab stats={stats} />}
        {tab === 'instrumental' && <InstrumentalTab stats={stats} />}
      </div>
    </div>
  );
}
