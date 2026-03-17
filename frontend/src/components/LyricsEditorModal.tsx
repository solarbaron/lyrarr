import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Modal, Loader, Button, Group, Textarea, Select, FileButton, SegmentedControl, Collapse } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState, useEffect, useMemo } from 'react';
import { readLyrics, uploadLyrics, saveLyricsFromEditor, translateLyrics, generateSyncedLyrics, getLyricsVersions, restoreLyricsVersion } from '../api';

interface Props {
  trackId: number;
  trackTitle: string;
  albumId: number;
  opened: boolean;
  onClose: () => void;
}

const LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'zh-CN', label: 'Chinese (Simplified)' },
  { value: 'ru', label: 'Russian' },
  { value: 'ar', label: 'Arabic' },
  { value: 'hi', label: 'Hindi' },
  { value: 'tr', label: 'Turkish' },
  { value: 'nl', label: 'Dutch' },
  { value: 'pl', label: 'Polish' },
  { value: 'sv', label: 'Swedish' },
];

/** Parse LRC timestamps and render syntax-highlighted preview */
function LrcPreview({ content }: { content: string }) {
  const lines = useMemo(() => {
    const tsRegex = /^(\[\d{1,2}:\d{2}[.:]\d{2,3}\])\s*(.*)/;
    return content.split('\n').map((line, i) => {
      const m = tsRegex.exec(line.trim());
      if (m) {
        return { key: i, tag: m[1], text: m[2], isTs: true };
      }
      return { key: i, tag: '', text: line, isTs: false };
    });
  }, [content]);

  return (
    <div style={{
      padding: '10px 12px',
      borderRadius: 8,
      background: 'rgba(0,0,0,0.25)',
      fontSize: 12,
      lineHeight: 1.8,
      maxHeight: 400,
      overflow: 'auto',
      fontFamily: 'monospace',
    }}>
      {lines.map(l => (
        <div key={l.key} style={{ minHeight: 20 }}>
          {l.isTs ? (
            <>
              <span style={{ color: '#8b5cf6', fontWeight: 600 }}>{l.tag}</span>
              <span style={{ color: 'var(--text-primary)', marginLeft: 4 }}>{l.text}</span>
            </>
          ) : (
            <span style={{ color: l.text.trim() ? 'var(--text-primary)' : 'transparent' }}>
              {l.text || '\u00A0'}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export default function LyricsEditorModal({ trackId, trackTitle, albumId, opened, onClose }: Props) {
  const queryClient = useQueryClient();
  const [content, setContent] = useState('');
  const [isSynced, setIsSynced] = useState(false);
  const [targetLang, setTargetLang] = useState<string | null>('en');
  const [translateMode, setTranslateMode] = useState('replace');
  const [translatedContent, setTranslatedContent] = useState<string | null>(null);
  const [syncModel, setSyncModel] = useState<string | null>('base');
  const [syncedPreview, setSyncedPreview] = useState<string | null>(null);
  const [showVersions, setShowVersions] = useState(false);
  const [expandedVersionId, setExpandedVersionId] = useState<number | null>(null);
  const [showLrcPreview, setShowLrcPreview] = useState(true);

  const { data: existingLyrics, isLoading } = useQuery({
    queryKey: ['lyrics-read', trackId],
    queryFn: () => readLyrics(trackId),
    enabled: opened,
  });

  const { data: versionsData } = useQuery({
    queryKey: ['lyrics-versions', trackId],
    queryFn: () => getLyricsVersions(trackId),
    enabled: opened && showVersions,
  });

  const restoreMutation = useMutation({
    mutationFn: (versionId: number) => restoreLyricsVersion(trackId, versionId),
    onSuccess: () => {
      notifications.show({ title: 'Restored', message: 'Previous version restored', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['lyrics-read', trackId] });
      queryClient.invalidateQueries({ queryKey: ['lyrics-versions', trackId] });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
    },
    onError: () => notifications.show({ title: 'Error', message: 'Failed to restore', color: 'red' }),
  });

  useEffect(() => {
    if (existingLyrics?.content) {
      setContent(existingLyrics.content);
      setIsSynced(existingLyrics.type === 'synced');
    } else {
      setContent('');
      setIsSynced(false);
    }
    setTranslatedContent(null);
    setSyncedPreview(null);
  }, [existingLyrics]);

  const saveMutation = useMutation({
    mutationFn: () => saveLyricsFromEditor(trackId, content, isSynced),
    onSuccess: () => {
      notifications.show({ title: 'Saved', message: 'Lyrics saved', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
      queryClient.invalidateQueries({ queryKey: ['lyrics-read', trackId] });
    },
    onError: () => notifications.show({ title: 'Error', message: 'Failed to save', color: 'red' }),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadLyrics(trackId, file),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Uploaded', message: data.message, color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
      queryClient.invalidateQueries({ queryKey: ['lyrics-read', trackId] });
    },
    onError: (err: any) => notifications.show({ title: 'Error', message: err?.response?.data?.message || 'Failed', color: 'red' }),
  });

  const translateMutation = useMutation({
    mutationFn: () => translateLyrics(trackId, {
      content: content,
      targetLang: targetLang || 'en',
      mode: translateMode,
    }),
    onSuccess: (data: any) => {
      setTranslatedContent(data.translated);
      notifications.show({ title: 'Translated', message: `Lyrics translated to ${targetLang}`, color: 'green' });
    },
    onError: (err: any) => notifications.show({ title: 'Error', message: err?.response?.data?.message || 'Translation failed', color: 'red' }),
  });

  const applyTranslation = () => {
    if (translatedContent) {
      setContent(translatedContent);
      setTranslatedContent(null);
    }
  };

  const syncMutation = useMutation({
    mutationFn: () => generateSyncedLyrics(trackId, {
      content: content,
      model: syncModel || 'base',
    }),
    onSuccess: (data: any) => {
      setSyncedPreview(data.synced);
      notifications.show({
        title: 'Sync Generated',
        message: `Matched ${data.matched}/${data.total_lines} lines (${data.segments} audio segments, language: ${data.language})`,
        color: 'green',
      });
    },
    onError: (err: any) => notifications.show({
      title: 'Error',
      message: err?.response?.data?.message || 'Sync generation failed',
      color: 'red',
    }),
  });

  const applySynced = () => {
    if (syncedPreview) {
      setContent(syncedPreview);
      setIsSynced(true);
      setSyncedPreview(null);
    }
  };

  const modalStyles = {
    content: { background: 'var(--surface-bg)' },
    header: { background: 'var(--surface-bg)' },
  };

  const textareaStyles = {
    input: {
      background: 'rgba(0,0,0,0.2)',
      border: '1px solid var(--card-border)',
      color: 'var(--text-primary)',
      fontFamily: 'monospace',
      fontSize: 13,
      lineHeight: 1.6,
    },
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Edit Lyrics: ${trackTitle}`} size={isSynced && showLrcPreview ? '90%' : 'xl'} styles={modalStyles}>
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Loader color="violet" />
        </div>
      ) : (
        <>
          {/* Editor + Live Preview */}
          <div style={{ display: isSynced && showLrcPreview ? 'grid' : 'block', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div>
              <Textarea
                value={content}
                onChange={(e) => setContent(e.currentTarget.value)}
                placeholder={"Paste or type lyrics here...\n\nFor synced (LRC) format:\n[00:12.34] First line of lyrics\n[00:15.67] Second line\n\nFor plain text:\nJust type the lyrics line by line"}
                minRows={14}
                maxRows={20}
                autosize
                styles={textareaStyles}
                mb="md"
              />
            </div>
            {isSynced && showLrcPreview && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>
                  🎵 LRC Preview
                </div>
                <LrcPreview content={content} />
              </div>
            )}
          </div>

          {/* Controls Row */}
          <Group justify="space-between" mb="md">
            <Group gap="sm">
              <SegmentedControl
                value={isSynced ? 'synced' : 'plain'}
                onChange={(v) => setIsSynced(v === 'synced')}
                data={[
                  { label: 'Plain Text', value: 'plain' },
                  { label: 'Synced (LRC)', value: 'synced' },
                ]}
                size="xs"
                styles={{
                  root: { background: 'var(--card-bg)', border: '1px solid var(--card-border)' },
                }}
              />
              {isSynced && (
                <Button
                  variant="subtle"
                  color="gray"
                  size="xs"
                  onClick={() => setShowLrcPreview(!showLrcPreview)}
                >
                  {showLrcPreview ? 'Hide Preview' : 'Show Preview'}
                </Button>
              )}
              <FileButton onChange={(file) => file && uploadMutation.mutate(file)} accept=".lrc,.txt">
                {(props) => (
                  <Button {...props} variant="light" color="violet" size="xs" loading={uploadMutation.isPending}>
                    Upload File
                  </Button>
                )}
              </FileButton>
            </Group>
            <Button
              variant="gradient"
              gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
              size="sm"
              onClick={() => saveMutation.mutate()}
              loading={saveMutation.isPending}
              disabled={!content.trim()}
            >
              Save Lyrics
            </Button>
          </Group>

          {/* Sync Generation Section */}
          <div style={{
            padding: 16,
            borderRadius: 12,
            background: 'var(--card-bg)',
            border: '1px solid var(--card-border)',
            marginBottom: 12,
          }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>🎤 Generate Synced Lyrics</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
              Uses AI (Whisper) to transcribe the audio and align your plain lyrics with timestamps.
              Requires <code style={{ color: 'var(--accent-primary)' }}>faster-whisper</code> to be installed.
            </div>
            <Group gap="sm" mb="sm">
              <Select
                placeholder="Whisper model"
                data={[
                  { value: 'tiny', label: 'Tiny (fastest, ~1GB RAM)' },
                  { value: 'base', label: 'Base (balanced, ~1.5GB RAM)' },
                  { value: 'small', label: 'Small (better, ~2GB RAM)' },
                  { value: 'medium', label: 'Medium (best, ~5GB RAM)' },
                ]}
                value={syncModel}
                onChange={setSyncModel}
                size="xs"
                style={{ width: 240 }}
                styles={{
                  input: { background: 'rgba(0,0,0,0.2)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' },
                }}
              />
              <Button
                variant="light"
                color="violet"
                size="xs"
                onClick={() => syncMutation.mutate()}
                loading={syncMutation.isPending}
                disabled={!content.trim() || isSynced}
              >
                {syncMutation.isPending ? 'Transcribing... (may take a minute)' : 'Generate Sync'}
              </Button>
            </Group>

            {syncedPreview && (
              <div>
                <LrcPreview content={syncedPreview} />
                <Group mt="sm" gap="sm">
                  <Button variant="gradient" gradient={{ from: '#8b3dff', to: '#6a1bfa' }} size="xs" onClick={applySynced}>
                    Apply to Editor
                  </Button>
                  <Button variant="light" color="gray" size="xs" onClick={() => setSyncedPreview(null)}>
                    Dismiss
                  </Button>
                </Group>
              </div>
            )}
          </div>

          {/* Translation Section */}
          <div style={{
            padding: 16,
            borderRadius: 12,
            background: 'var(--card-bg)',
            border: '1px solid var(--card-border)',
          }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>🌐 Translation</div>
            <Group gap="sm" mb="sm">
              <Select
                placeholder="Target language"
                data={LANGUAGES}
                value={targetLang}
                onChange={setTargetLang}
                size="xs"
                style={{ flex: 1 }}
                styles={{
                  input: { background: 'rgba(0,0,0,0.2)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' },
                }}
              />
              <SegmentedControl
                value={translateMode}
                onChange={setTranslateMode}
                data={[
                  { label: 'Replace', value: 'replace' },
                  { label: 'Dual (Original + Translation)', value: 'dual' },
                ]}
                size="xs"
                styles={{
                  root: { background: 'rgba(0,0,0,0.2)', border: '1px solid var(--card-border)' },
                }}
              />
              <Button
                variant="light"
                color="violet"
                size="xs"
                onClick={() => translateMutation.mutate()}
                loading={translateMutation.isPending}
                disabled={!content.trim() || !targetLang}
              >
                Translate
              </Button>
            </Group>

            {/* Translation Result */}
            {translatedContent && (
              <div>
                <pre style={{
                  margin: 0,
                  padding: 12,
                  borderRadius: 8,
                  background: 'rgba(0,0,0,0.3)',
                  fontSize: 12,
                  color: 'var(--text-primary)',
                  whiteSpace: 'pre-wrap',
                  maxHeight: 300,
                  overflow: 'auto',
                  lineHeight: 1.6,
                  fontFamily: 'monospace',
                }}>
                  {translatedContent}
                </pre>
                <Group mt="sm" gap="sm">
                  <Button variant="gradient" gradient={{ from: '#8b3dff', to: '#6a1bfa' }} size="xs" onClick={applyTranslation}>
                    Apply to Editor
                  </Button>
                  <Button variant="light" color="gray" size="xs" onClick={() => setTranslatedContent(null)}>
                    Dismiss
                  </Button>
                </Group>
              </div>
            )}
          </div>

          {/* Previous Versions */}
          <div style={{
            padding: 16,
            borderRadius: 12,
            background: 'var(--card-bg)',
            border: '1px solid var(--card-border)',
            marginTop: 12,
          }}>
            <div
              style={{ fontSize: 14, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
              onClick={() => setShowVersions(!showVersions)}
            >
              <span style={{ transform: showVersions ? 'rotate(90deg)' : 'none', transition: '0.2s' }}>▶</span>
              📜 Previous Versions
            </div>
            <Collapse in={showVersions}>
              <div style={{ marginTop: 8 }}>
                {versionsData?.versions?.length ? (
                  versionsData.versions.slice(0, 10).map((v: any) => (
                    <div key={v.id} style={{ marginBottom: 8 }}>
                      <div style={{
                        padding: '8px 10px',
                        borderRadius: 8,
                        background: 'rgba(0,0,0,0.2)',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        fontSize: 12,
                        cursor: 'pointer',
                      }}
                        onClick={() => setExpandedVersionId(expandedVersionId === v.id ? null : v.id)}
                      >
                        <div>
                          <span style={{ color: 'var(--text-secondary)' }}>
                            {new Date(v.timestamp).toLocaleString()}
                          </span>
                          <span style={{ marginLeft: 8, color: 'var(--accent-primary)' }}>
                            {v.lyrics_type} • {v.provider}
                          </span>
                          <span style={{ marginLeft: 8, color: 'var(--text-secondary)' }}>
                            {v.content.length} chars
                          </span>
                          {v.translated_from && (
                            <span style={{ marginLeft: 8, color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                              (translated from {v.translated_from})
                            </span>
                          )}
                        </div>
                        <Group gap="xs">
                          <Button
                            variant="subtle"
                            color="gray"
                            size="compact-xs"
                            onClick={(e: React.MouseEvent) => {
                              e.stopPropagation();
                              setExpandedVersionId(expandedVersionId === v.id ? null : v.id);
                            }}
                          >
                            {expandedVersionId === v.id ? 'Hide' : 'Preview'}
                          </Button>
                          <Button
                            variant="light"
                            color="violet"
                            size="compact-xs"
                            onClick={(e: React.MouseEvent) => { e.stopPropagation(); restoreMutation.mutate(v.id); }}
                            loading={restoreMutation.isPending}
                          >
                            Restore
                          </Button>
                        </Group>
                      </div>
                      <Collapse in={expandedVersionId === v.id}>
                        <pre style={{
                          margin: '4px 0 0',
                          padding: 10,
                          borderRadius: '0 0 8px 8px',
                          background: 'rgba(0,0,0,0.3)',
                          fontSize: 11,
                          color: 'var(--text-primary)',
                          whiteSpace: 'pre-wrap',
                          maxHeight: 200,
                          overflow: 'auto',
                          lineHeight: 1.5,
                          fontFamily: 'monospace',
                        }}>
                          {v.content}
                        </pre>
                      </Collapse>
                    </div>
                  ))
                ) : (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '8px 0' }}>
                    No previous versions yet. Versions are saved automatically when lyrics are updated.
                  </div>
                )}
              </div>
            </Collapse>
          </div>
        </>
      )}
    </Modal>
  );
}
