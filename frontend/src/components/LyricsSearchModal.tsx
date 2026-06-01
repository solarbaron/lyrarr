import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Modal, Loader, Badge, Button, Group, Progress, Tooltip, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { searchLyrics, downloadLyrics, blacklistLyrics } from '../api';
import type { LyricsResult } from '../types';
import api from '../api';

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
  const [customQuery, setCustomQuery] = useState('');
  const [useCustom, setUseCustom] = useState(false);

  // Custom search function that supports manual query override
  const searchFn = async () => {
    if (useCustom && customQuery.trim()) {
      // Send a manual search query as track_name with no artist
      const resp = await api.get(`/metadata/lyrics/search/${trackId}`, {
        params: { custom_query: customQuery.trim() },
      });
      return resp.data as { results: LyricsResult[] };
    }
    return searchLyrics(trackId);
  };

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['lyrics-search', trackId, useCustom ? customQuery : ''],
    queryFn: searchFn,
    enabled: opened,
  });

  const downloadMutation = useMutation({
    mutationFn: (result: LyricsResult) => downloadLyrics(trackId, {
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

  const blacklistMutation = useMutation({
    mutationFn: (result: LyricsResult) => blacklistLyrics(trackId, {
      synced_lyrics: result.synced_lyrics,
      plain_lyrics: result.plain_lyrics,
      provider: result.provider,
      rescan: false,  // just record it; don't touch the saved file from here
    }),
    onSuccess: () => {
      notifications.show({ title: 'Blacklisted', message: 'This result won\'t be auto-selected again', color: 'orange' });
      refetch();  // re-run so the blacklisted result drops out of the list
    },
    onError: () => {
      notifications.show({ title: 'Error', message: 'Failed to blacklist', color: 'red' });
    },
  });

  const results = data?.results || [];
  const bestScore = results.length > 0 ? Math.round((results[0]?.score || 0) * 100) : 0;
  const hasLowConfidence = results.length > 0 && bestScore < 60;

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
      {/* Custom Query Override */}
      <div style={{
        padding: '10px 14px',
        borderRadius: 10,
        background: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
        marginBottom: 12,
      }}>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
          Override automatic search with a custom query:
        </div>
        <Group gap="sm">
          <TextInput
            placeholder="e.g. Bohemian Rhapsody Queen"
            value={customQuery}
            onChange={(e) => setCustomQuery(e.currentTarget.value)}
            size="xs"
            style={{ flex: 1 }}
            styles={{
              input: {
                background: 'rgba(0,0,0,0.2)',
                border: '1px solid var(--card-border)',
                color: 'var(--text-primary)',
              },
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && customQuery.trim()) {
                setUseCustom(true);
                refetch();
              }
            }}
          />
          <Button
            variant="light"
            color="violet"
            size="xs"
            onClick={() => {
              if (customQuery.trim()) {
                setUseCustom(true);
                refetch();
              } else {
                setUseCustom(false);
                refetch();
              }
            }}
          >
            {customQuery.trim() ? 'Search' : 'Reset'}
          </Button>
          {useCustom && (
            <Button
              variant="subtle"
              color="gray"
              size="xs"
              onClick={() => {
                setCustomQuery('');
                setUseCustom(false);
                refetch();
              }}
            >
              Clear
            </Button>
          )}
        </Group>
      </div>

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Loader color="violet" />
          <p style={{ color: 'var(--text-secondary)', marginTop: 12 }}>Searching lyrics providers...</p>
        </div>
      ) : results.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <div style={{ fontSize: 48, opacity: 0.3, marginBottom: 12 }}>📝</div>
          <p style={{ color: 'var(--text-secondary)' }}>No lyrics found from any provider.</p>
          {!useCustom && (
            <p style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 8 }}>
              Try entering a custom search query above with different title/artist spelling.
            </p>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Low confidence warning */}
          {hasLowConfidence && (
            <div style={{
              padding: '10px 14px',
              borderRadius: 10,
              background: 'rgba(255, 170, 0, 0.1)',
              border: '1px solid rgba(255, 170, 0, 0.3)',
              fontSize: 13,
              color: '#ffa600',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}>
              <span style={{ fontSize: 18 }}>⚠️</span>
              <div>
                <strong>Low confidence match</strong> (best: {bestScore}%) — these lyrics may not
                match this track. Consider using a custom search query above or manually verifying.
              </div>
            </div>
          )}

          <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: 0 }}>
            {results.length} result{results.length !== 1 ? 's' : ''} found. Click "Use" to save.
          </p>

          {results.map((result: LyricsResult, idx: number) => {
            const md = result.match_details || {};
            const overallScore = Math.round((result.score || 0) * 100);
            const scoreColor = overallScore >= 80 ? 'green' : overallScore >= 60 ? 'yellow' : overallScore >= 40 ? 'orange' : 'red';

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
                    {(result.providers_agree ?? 0) > 1 && (
                      <Badge size="sm" color="teal" variant="light">✓ {result.providers_agree} providers agree</Badge>
                    )}
                    {result.is_composite && (
                      <Badge size="sm" color="cyan" variant="light">Composite</Badge>
                    )}
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
                    <Tooltip label="Never auto-select this result for this track">
                      <Button
                        variant="subtle"
                        color="red"
                        size="compact-xs"
                        onClick={(e: React.MouseEvent) => { e.stopPropagation(); blacklistMutation.mutate(result); }}
                        loading={blacklistMutation.isPending}
                      >
                        Blacklist
                      </Button>
                    </Tooltip>
                  </Group>
                </Group>

                {/* Result metadata */}
                {(result.track_name || result.artist_name) && (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
                    {result.artist_name ? `${result.artist_name} — ` : ''}{result.track_name || ''}
                    {result.duration != null && (
                      <span style={{ marginLeft: 8, opacity: 0.6 }}>
                        ({Math.floor(result.duration / 60)}:{String(Math.round(result.duration % 60)).padStart(2, '0')})
                      </span>
                    )}
                  </div>
                )}

                {/* Always-visible Match Breakdown */}
                {(md.title_score !== undefined || md.artist_score !== undefined || md.duration_score !== undefined) && (
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
                        detail={`"${md.title_query || ''}" → "${md.title_result || ''}"`}
                      />
                    )}
                    {md.artist_score !== undefined && (
                      <MatchScoreBar
                        label="Artist"
                        score={md.artist_score}
                        detail={`"${md.artist_query || ''}" → "${md.artist_result || ''}"`}
                      />
                    )}
                    {md.duration_score !== undefined && (
                      <MatchScoreBar
                        label="Duration"
                        score={md.duration_score}
                        detail={md.duration_diff != null ? `${md.duration_diff.toFixed(1)}s difference` : 'No duration data'}
                      />
                    )}
                  </div>
                )}

                {/* Lyrics preview */}
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
                  {(result.synced_lyrics?.length && result.synced_lyrics.length > 300) || (result.plain_lyrics?.length && result.plain_lyrics.length > 300) ? '\n...' : ''}
                </pre>
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
