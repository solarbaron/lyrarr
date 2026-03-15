import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Loader, Code, ScrollArea, Badge, Progress } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { getSystemStatus, getTasks, getLogs, getHealth, runTask, getProviderStats } from '../api';

const PROVIDER_LABELS: Record<string, string> = {
  musicbrainz: 'MusicBrainz', fanart: 'fanart.tv', deezer: 'Deezer',
  itunes: 'iTunes', theaudiodb: 'TheAudioDB', lrclib: 'LRCLIB',
  genius: 'Genius', musixmatch: 'Musixmatch', netease: 'NetEase',
};

export default function SystemPage() {
  const queryClient = useQueryClient();
  const { data: status } = useQuery({ queryKey: ['system-status'], queryFn: getSystemStatus });
  const { data: tasks = [] } = useQuery({ queryKey: ['tasks'], queryFn: getTasks });
  const { data: logs = [], isLoading: logsLoading } = useQuery({ queryKey: ['logs'], queryFn: getLogs });
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: getHealth });
  const { data: providerStats } = useQuery({
    queryKey: ['provider-stats'],
    queryFn: getProviderStats,
    refetchInterval: 30000,
  });

  const runTaskMutation = useMutation({
    mutationFn: runTask,
    onSuccess: (_, taskId) => {
      notifications.show({ title: 'Task triggered', message: `${taskId} started`, color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });

  return (
    <div className="fade-in">
      <div className="page-header">
        <h1 className="page-title">System</h1>
        <p className="page-subtitle">System status, tasks, and logs</p>
      </div>

      {/* Status */}
      <div className="settings-section">
        <h3>📊 Status</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Version</div>
            <div style={{ fontWeight: 600 }}>{status?.version || 'dev'}</div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Python</div>
            <div style={{ fontWeight: 600 }}>{status?.python_version || '—'}</div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>OS</div>
            <div style={{ fontWeight: 600 }}>{status?.os || '—'}</div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>Lidarr</div>
            <span className={`status-badge ${health?.lidarr?.healthy ? 'available' : 'missing'}`}>
              {health?.lidarr?.status || 'Unknown'}
            </span>
          </div>
        </div>
      </div>

      {/* Provider Stats */}
      {providerStats && Object.keys(providerStats).length > 0 && (
        <div className="settings-section">
          <h3>🔌 Provider Health</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Status</th>
                <th>Success</th>
                <th>Failures</th>
                <th>Rate</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(providerStats).map(([name, stats]: [string, any]) => {
                const total = stats.successes + stats.failures;
                const rate = total > 0 ? Math.round((stats.successes / total) * 100) : 100;
                return (
                  <tr key={name}>
                    <td style={{ fontWeight: 600 }}>{PROVIDER_LABELS[name] || name}</td>
                    <td>
                      <Badge
                        size="sm"
                        color={stats.available ? 'green' : 'red'}
                        variant="light"
                      >
                        {stats.available ? 'Active' : 'Cooldown'}
                      </Badge>
                    </td>
                    <td style={{ color: 'var(--text-secondary)' }}>{stats.successes}</td>
                    <td style={{ color: stats.failures > 0 ? '#f87171' : 'var(--text-secondary)' }}>
                      {stats.failures}
                      {stats.consecutive_failures > 0 && (
                        <span style={{ fontSize: 11, opacity: 0.7 }}> ({stats.consecutive_failures} streak)</span>
                      )}
                    </td>
                    <td style={{ minWidth: 120 }}>
                      {total > 0 ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <Progress
                            value={rate}
                            color={rate > 80 ? 'green' : rate > 50 ? 'yellow' : 'red'}
                            size="sm"
                            style={{ flex: 1 }}
                          />
                          <span style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 36 }}>
                            {rate}%
                          </span>
                        </div>
                      ) : (
                        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Tasks */}
      <div className="settings-section">
        <h3>⏱️ Scheduled Tasks</h3>
        {tasks.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>No scheduled tasks</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr><th>Task</th><th>Interval</th><th>Next Run</th><th>Action</th></tr>
            </thead>
            <tbody>
              {tasks.map((task: any) => (
                <tr key={task.job_id}>
                  <td style={{ fontWeight: 600 }}>{task.name}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{task.interval}</td>
                  <td style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{task.next_run_time}</td>
                  <td>
                    <Button
                      size="xs" variant="light" color="violet"
                      onClick={() => runTaskMutation.mutate(task.job_id)}
                      loading={runTaskMutation.isPending}
                    >
                      Run Now
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Logs */}
      <div className="settings-section">
        <h3>📄 Logs</h3>
        {logsLoading ? (
          <Loader color="violet" size="sm" />
        ) : (
          <ScrollArea h={400} styles={{ viewport: { background: 'rgba(0,0,0,0.3)', borderRadius: 8, padding: 12 } }}>
            <Code block style={{ background: 'transparent', color: 'var(--text-secondary)', fontSize: 12, whiteSpace: 'pre-wrap' }}>
              {logs.length > 0 ? logs.join('\n') : 'No log entries yet.'}
            </Code>
          </ScrollArea>
        )}
      </div>
    </div>
  );
}
