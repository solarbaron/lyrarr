import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { SimpleGrid, Loader, Button, Progress } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faUser, faCompactDisc, faMusic, faMagnifyingGlass, faRotate, faLanguage, faFileImport } from '@fortawesome/free-solid-svg-icons';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { getArtists, getAlbums, getTracks, getWantedCovers, getWantedLyrics, getSystemStatus, triggerSync, getDashboardStats, getLanguageStats, batchRedetectLanguages, importSidecarLyrics } from '../api';

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { data: artistsResult } = useQuery({ queryKey: ['dashboard-artists'], queryFn: () => getArtists({ page: 1, pageSize: 1 }) });
  const { data: albumsResult } = useQuery({ queryKey: ['dashboard-albums'], queryFn: () => getAlbums({ page: 1, pageSize: 1 }) });
  const { data: tracksResult } = useQuery({ queryKey: ['dashboard-tracks'], queryFn: () => getTracks({ page: 1, pageSize: 1 }) });
  const { data: wantedCoversResult } = useQuery({ queryKey: ['wanted-covers'], queryFn: () => getWantedCovers({ page: 1, pageSize: 1 }) });
  const { data: wantedLyricsResult } = useQuery({ queryKey: ['wanted-lyrics'], queryFn: () => getWantedLyrics({ page: 1, pageSize: 1 }) });
  const { data: status } = useQuery({ queryKey: ['system-status'], queryFn: getSystemStatus });
  const { data: chartData } = useQuery({ queryKey: ['dashboard-stats'], queryFn: getDashboardStats });
  const { data: langStats } = useQuery({ queryKey: ['language-stats'], queryFn: getLanguageStats });

  const syncMutation = useMutation({
    mutationFn: triggerSync,
    onSuccess: () => {
      notifications.show({ title: 'Sync started', message: 'Syncing artists and albums from Lidarr...', color: 'violet' });
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['dashboard-artists'] });
        queryClient.invalidateQueries({ queryKey: ['dashboard-albums'] });
        queryClient.invalidateQueries({ queryKey: ['dashboard-tracks'] });
        queryClient.invalidateQueries({ queryKey: ['wanted-covers'] });
        queryClient.invalidateQueries({ queryKey: ['wanted-lyrics'] });
      }, 5000);
    },
  });

  const redetectMutation = useMutation({
    mutationFn: () => batchRedetectLanguages(),
    onSuccess: () => {
      notifications.show({ title: 'Started', message: 'Re-detecting languages for all lyrics...', color: 'violet' });
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['language-stats'] }), 10000);
    },
  });

  const stats = [
    { label: 'Artists', value: artistsResult?.total || 0, icon: faUser, color: '#8b3dff' },
    { label: 'Albums', value: albumsResult?.total || 0, icon: faCompactDisc, color: '#6a1bfa' },
    { label: 'Tracks', value: tracksResult?.total || 0, icon: faMusic, color: '#5a11e0' },
    { label: 'Missing Covers', value: wantedCoversResult?.total || 0, icon: faMagnifyingGlass, color: '#f87171' },
    { label: 'Missing Lyrics', value: wantedLyricsResult?.total || 0, icon: faMagnifyingGlass, color: '#fbbf24' },
  ];

  // Prepare language chart data
  const langEntries = langStats?.languages ? Object.entries(langStats.languages as Record<string, number>)
    .sort((a, b) => b[1] - a[1]) : [];
  const langColors = ['#8b3dff', '#6a1bfa', '#f87171', '#fbbf24', '#34d399', '#38bdf8', '#fb923c', '#a78bfa', '#e879f9', '#94a3b8'];

  return (
    <div className="fade-in">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-subtitle">
            {status?.lidarr_enabled
              ? `Connected to Lidarr • v${status?.version || 'dev'}`
              : 'Configure Lidarr in Settings to get started'}
          </p>
        </div>
        <Button
          variant="gradient"
          gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
          leftSection={<FontAwesomeIcon icon={faRotate} />}
          onClick={() => syncMutation.mutate()}
          loading={syncMutation.isPending}
        >
          Sync with Lidarr
        </Button>
      </div>

      <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 5 }} spacing="lg">
        {stats.map((stat) => (
          <div key={stat.label} className="stat-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <div style={{
                width: 36, height: 36, borderRadius: 10,
                background: `${stat.color}22`, display: 'flex',
                alignItems: 'center', justifyContent: 'center',
                color: stat.color, fontSize: 16
              }}>
                <FontAwesomeIcon icon={stat.icon} />
              </div>
            </div>
            <div className="stat-card-value">{stat.value}</div>
            <div className="stat-card-label">{stat.label}</div>
          </div>
        ))}
      </SimpleGrid>

      {/* Lyrics Intelligence Stats */}
      {langStats && langStats.total_available > 0 && (
        <div className="stat-card" style={{ marginTop: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
            <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
              <FontAwesomeIcon icon={faLanguage} style={{ color: '#8b3dff' }} />
              Lyrics Intelligence
            </h3>
            <Button
              variant="light" color="violet" size="xs"
              onClick={() => redetectMutation.mutate()}
              loading={redetectMutation.isPending}
            >
              Re-detect Languages
            </Button>
            <Button
              variant="light" color="grape" size="xs"
              leftSection={<FontAwesomeIcon icon={faFileImport} />}
              onClick={() => {
                importSidecarLyrics().then(() => {
                  notifications.show({ title: 'Started', message: 'Scanning for existing .lrc/.txt sidecar files...', color: 'violet' });
                  setTimeout(() => queryClient.invalidateQueries({ queryKey: ['language-stats'] }), 15000);
                });
              }}
            >
              Import Sidecar Files
            </Button>
          </div>

          {/* Quick Stats Row */}
          <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="md" mb="lg">
            <div style={{ textAlign: 'center', padding: 12, borderRadius: 10, background: 'rgba(139,61,255,0.06)' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent-primary)' }}>{langStats.total_available}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Available</div>
            </div>
            <div style={{ textAlign: 'center', padding: 12, borderRadius: 10, background: 'rgba(52,211,153,0.06)' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#34d399' }}>{langStats.synced}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Synced (LRC)</div>
            </div>
            <div style={{ textAlign: 'center', padding: 12, borderRadius: 10, background: 'rgba(251,191,36,0.06)' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#fbbf24' }}>{langStats.plain}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Plain Text</div>
            </div>
            <div style={{ textAlign: 'center', padding: 12, borderRadius: 10, background: 'rgba(248,113,113,0.06)' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#f87171' }}>{langStats.total_missing}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Missing</div>
            </div>
          </SimpleGrid>

          {/* Language Distribution + Provider Distribution side by side */}
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
            {/* Language Distribution */}
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: 'var(--text-secondary)' }}>
                Language Distribution
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {langEntries.slice(0, 10).map(([lang, count], i) => (
                  <div key={lang}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ fontSize: 12, fontWeight: 500, textTransform: 'uppercase' }}>{lang}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                        {count} ({Math.round((count as number) / langStats.total_available * 100)}%)
                      </span>
                    </div>
                    <Progress value={(count as number) / langStats.total_available * 100}
                      color={langColors[i % langColors.length]} size="sm" radius="xl" />
                  </div>
                ))}
              </div>
            </div>

            {/* Provider Distribution */}
            {langStats.providers && Object.keys(langStats.providers).length > 0 && (
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: 'var(--text-secondary)' }}>
                  Provider Distribution
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {Object.entries(langStats.providers as Record<string, number>)
                    .sort((a, b) => b[1] - a[1])
                    .map(([provider, count], i) => (
                    <div key={provider}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                        <span style={{ fontSize: 12, fontWeight: 500 }}>{provider}</span>
                        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{count} tracks</span>
                      </div>
                      <Progress value={Math.min(100, (count as number) / Math.max(1, ...Object.values(langStats.providers as Record<string, number>)) * 100)}
                        color={langColors[i % langColors.length]} size="sm" radius="xl" />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </SimpleGrid>
        </div>
      )}

      {/* Downloads Over Time Chart */}
      {(chartData?.downloadHistory?.length ?? 0) > 0 && (
        <div className="stat-card" style={{ marginTop: 24 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 20 }}>Downloads Over Time (30 days)</h3>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={chartData!.downloadHistory!}>
              <defs>
                <linearGradient id="colorCovers" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#8b3dff" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#8b3dff" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="colorLyrics" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#6a1bfa" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#6a1bfa" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(139,61,255,0.1)" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-secondary)' }}
                tickFormatter={(v: string) => v.slice(5)} />
              <YAxis tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} width={30} />
              <Tooltip
                contentStyle={{
                  background: 'rgba(15,10,35,0.95)', border: '1px solid var(--card-border)',
                  borderRadius: 8, fontSize: 12, color: 'var(--text-primary)',
                }}
              />
              <Area type="monotone" dataKey="covers" stroke="#8b3dff" fill="url(#colorCovers)" name="Covers" />
              <Area type="monotone" dataKey="lyrics" stroke="#6a1bfa" fill="url(#colorLyrics)" name="Lyrics" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Per-Artist Completion */}
      {(chartData?.artistCompletion?.length ?? 0) > 0 && (
        <div className="stat-card" style={{ marginTop: 20 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 20 }}>Artist Completion</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {chartData!.artistCompletion!.slice(0, 10).map((a: any) => (
              <div key={a.id}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontWeight: 500, fontSize: 13 }}>{a.name}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                    {a.totalAlbums} albums · {a.totalTracks} tracks
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 4 }}>
                      Covers {a.coverPct}%
                    </div>
                    <Progress value={a.coverPct} color="violet" size="sm" radius="xl" />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 4 }}>
                      Lyrics {a.lyricsPct}%
                    </div>
                    <Progress value={a.lyricsPct} color="grape" size="sm" radius="xl" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!status?.lidarr_enabled && (
        <div className="empty-state" style={{ marginTop: 40 }}>
          <div className="empty-state-icon">🎵</div>
          <div className="empty-state-title">Welcome to Lyrarr</div>
          <div className="empty-state-message">
            Configure your Lidarr connection in Settings to start syncing your music library
            and managing cover art and lyrics.
          </div>
        </div>
      )}
    </div>
  );
}
