import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Modal, Loader, Badge, Button, Group, Progress, Tooltip, Collapse } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { searchLyrics, downloadLyrics } from '../api';

interface Props {
  trackId: number;
  trackTitle: string;
  albumId: number;
  opened: boolean;
  onClose: () => void;
}

function MatchScoreBar({ label, score, detail }: { label: string; score: number; detail?: string }) {
  const color = score >= 90 ? 'green' : score >= 70 ? 'yellow' : score >= 50 ? 'orange' : 'red';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, marginBottom: 4 }}>
      <span style={{ width: 50, color: 'var(--text-secondary)', flexShrink: 0 }}>{label}</span>
      <Progress value={score} color={color} size="sm" style={{ flex: 1 }} />
      <span style={{ width: 30, textAlign: 'right', color: 'var(--text-secondary)', flexShrink: 0 }}>{score}%</span>
      {detail && (
        <Tooltip label={detail} withArrow>
          <span style={{ cursor: 'help', color: 'var(--text-secondary)', fontSize: 10 }}>ⓘ</span>
        </Tooltip>
      )}
    </div>
  );
}

export default function LyricsSearchModal({ trackId, trackTitle, albumId, opened, onClose }: Props) {
  const queryClient = useQueryClient();
  const [expandedMatch, setExpandedMatch] = useState<number | null>(null);

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
          {results.map((result: any, idx: number) => {
            const md = result.match_details || {};
            const overallScore = Math.round((result.score || 0) * 100);
            const scoreColor = overallScore >= 80 ? 'green' : overallScore >= 50 ? 'yellow' : 'red';

            return (
              <div
                key={idx}
                style={{
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
                  <Group gap="xs">
                    <Badge size="sm" color={scoreColor} variant="light">{overallScore}% match</Badge>
                    <Button
                      variant="gradient"
                      gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
                      size="compact-xs"
                      onClick={(e: React.MouseEvent) => { e.stopPropagation(); downloadMutation.mutate(result); }}
                      loading={downloadMutation.isPending}
                    >
                      Use
                    </Button>
                  </Group>
                </Group>

                {result.track_name && (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
                    {result.artist_name ? `${result.artist_name} — ` : ''}{result.track_name}
                  </div>
                )}

                {/* Fuzzy Match Confidence Breakdown */}
                {Object.keys(md).length > 0 && (
                  <div
                    style={{ cursor: 'pointer', fontSize: 11, color: 'var(--accent-primary)', marginBottom: 6 }}
                    onClick={(e) => { e.stopPropagation(); setExpandedMatch(expandedMatch === idx ? null : idx); }}
                  >
                    {expandedMatch === idx ? '▼' : '▶'} Match Details
                  </div>
                )}
                <Collapse in={expandedMatch === idx}>
                  <div style={{
                    padding: '8px 10px',
                    borderRadius: 8,
                    background: 'rgba(0,0,0,0.15)',
                    marginBottom: 8,
                  }}>
                    {md.title_score !== undefined && (
                      <MatchScoreBar
                        label="Title"
                        score={md.title_score}
                        detail={`"${md.title_query}" → "${md.title_result}"`}
                      />
                    )}
                    {md.artist_score !== undefined && (
                      <MatchScoreBar
                        label="Artist"
                        score={md.artist_score}
                        detail={`"${md.artist_query}" → "${md.artist_result}"`}
                      />
                    )}
                    {md.duration_score !== undefined && (
                      <MatchScoreBar
                        label="Duration"
                        score={md.duration_score}
                        detail={`${md.duration_diff?.toFixed(1)}s difference`}
                      />
                    )}
                  </div>
                </Collapse>

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
            );
          })}
        </div>
      )}
    </Modal>
  );
}
