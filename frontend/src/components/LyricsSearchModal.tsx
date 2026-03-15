import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Modal, Loader, Badge, Button, Group } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { searchLyrics, downloadLyrics } from '../api';

interface Props {
  trackId: number;
  trackTitle: string;
  albumId: number;
  opened: boolean;
  onClose: () => void;
}

export default function LyricsSearchModal({ trackId, trackTitle, albumId, opened, onClose }: Props) {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['lyrics-search', trackId],
    queryFn: () => searchLyrics(trackId),
    enabled: opened,
  });

  const downloadMutation = useMutation({
    mutationFn: (result: any) => downloadLyrics(trackId, {
      synced_lyrics: result.synced_lyrics,
      plain_lyrics: result.plain_lyrics,
      provider: result.provider,
    }),
    onSuccess: () => {
      notifications.show({ title: 'Done', message: 'Lyrics saved!', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
      onClose();
    },
    onError: () => {
      notifications.show({ title: 'Error', message: 'Failed to save lyrics', color: 'red' });
    },
  });

  const results = data?.results || [];

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={`Lyrics: ${trackTitle}`}
      size="lg"
      styles={{
        content: { background: 'var(--surface-bg)' },
        header: { background: 'var(--surface-bg)' },
      }}
    >
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Loader color="violet" />
          <p style={{ color: 'var(--text-secondary)', marginTop: 12 }}>Searching lyrics providers...</p>
        </div>
      ) : results.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <div style={{ fontSize: 48, opacity: 0.3, marginBottom: 12 }}>📝</div>
          <p style={{ color: 'var(--text-secondary)' }}>No lyrics found from any provider.</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: 0 }}>
            {results.length} result{results.length !== 1 ? 's' : ''} found. Click to use.
          </p>
          {results.map((result: any, idx: number) => (
            <div
              key={idx}
              onClick={() => downloadMutation.mutate(result)}
              style={{
                cursor: downloadMutation.isPending ? 'wait' : 'pointer',
                borderRadius: 10,
                padding: 16,
                background: 'var(--card-bg)',
                border: '1px solid var(--card-border)',
                transition: 'all 0.2s',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
              onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--card-border)')}
            >
              <Group justify="space-between" mb={8}>
                <Group gap="xs">
                  <Badge size="sm" color="violet" variant="filled">{result.provider}</Badge>
                  {result.synced_lyrics && <Badge size="sm" color="green" variant="light">Synced</Badge>}
                  {result.plain_lyrics && !result.synced_lyrics && <Badge size="sm" color="gray" variant="light">Plain</Badge>}
                </Group>
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  Score: {Math.round((result.score || 0) * 100)}%
                </span>
              </Group>
              {result.track_name && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
                  {result.artist_name ? `${result.artist_name} — ` : ''}{result.track_name}
                </div>
              )}
              <pre style={{
                margin: 0,
                padding: 10,
                borderRadius: 6,
                background: 'rgba(0,0,0,0.2)',
                fontSize: 11,
                color: 'var(--text-secondary)',
                whiteSpace: 'pre-wrap',
                maxHeight: 120,
                overflow: 'hidden',
                lineHeight: 1.4,
              }}>
                {result.synced_preview || result.plain_preview || '(no preview)'}
                {(result.synced_lyrics?.length > 300 || result.plain_lyrics?.length > 300) ? '\n...' : ''}
              </pre>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
