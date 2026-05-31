import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Modal, Loader, Button, Group, Textarea, Select, FileButton, SegmentedControl, Collapse } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { readLyrics, uploadLyrics, saveLyricsFromEditor, translateLyrics, generateSyncedLyrics, getLyricsVersions, restoreLyricsVersion, getTrackStreamUrl } from '../api';

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

/** Parse LRC timestamp string to seconds */
function parseLrcTimestamp(ts: string): number {
  const m = ts.match(/\[(\d{1,2}):(\d{2})[.:](\d{2,3})\]/);
  if (!m) return -1;
  const mins = parseInt(m[1]);
  const secs = parseInt(m[2]);
  const frac = m[3].length === 3 ? parseInt(m[3]) / 1000 : parseInt(m[3]) / 100;
  return mins * 60 + secs + frac;
}

/** Parse LRC content into structured lines */
function parseLrcLines(content: string) {
  const tsRegex = /^(\[\d{1,2}:\d{2}[.:]\d{2,3}\])\s*(.*)/;
  return content.split('\n').map((line, i) => {
    const m = tsRegex.exec(line.trim());
    if (m) {
      return { key: i, tag: m[1], text: m[2], isTs: true, time: parseLrcTimestamp(m[1]) };
    }
    return { key: i, tag: '', text: line, isTs: false, time: -1 };
  });
}

/** Synced lyrics preview with audio playback */
function SyncedLyricsPlayer({ content, trackId }: { content: string; trackId: number }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);

  const lines = useMemo(() => parseLrcLines(content), [content]);
  const timedLines = useMemo(() => lines.filter(l => l.isTs && l.time >= 0), [lines]);

  // Pre-build a Map from line key -> timedLines index for O(1) lookup
  const timedIndexMap = useMemo(() => {
    const map = new Map<number, number>();
    timedLines.forEach((l, i) => map.set(l.key, i));
    return map;
  }, [timedLines]);

  // Find the current active line index
  const activeIdx = useMemo(() => {
    let idx = -1;
    for (let i = 0; i < timedLines.length; i++) {
      if (timedLines[i].time <= currentTime) idx = i;
      else break;
    }
    return idx;
  }, [timedLines, currentTime]);

  // Update current time from audio element
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTimeUpdate = () => setCurrentTime(audio.currentTime);
    const onDurationChange = () => setDuration(audio.duration || 0);
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);

    audio.addEventListener('timeupdate', onTimeUpdate);
    audio.addEventListener('durationchange', onDurationChange);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);

    return () => {
      audio.removeEventListener('timeupdate', onTimeUpdate);
      audio.removeEventListener('durationchange', onDurationChange);
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
    };
  }, []);

  // Pause audio when component unmounts (modal closes)
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = '';
      }
    };
  }, []);

  // Auto-scroll to active line
  useEffect(() => {
    if (activeIdx < 0 || !containerRef.current) return;
    const activeEl = containerRef.current.querySelector(`[data-line-idx="${activeIdx}"]`);
    if (activeEl) {
      activeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [activeIdx]);

  const seekTo = useCallback((time: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = time;
    }
  }, []);

  const togglePlay = useCallback(() => {
    if (!audioRef.current) return;
    if (audioRef.current.paused) audioRef.current.play();
    else audioRef.current.pause();
  }, []);

  const formatTime = (s: number) => {
    const mins = Math.floor(s / 60);
    const secs = Math.floor(s % 60);
    return `${mins}:${String(secs).padStart(2, '0')}`;
  };

  return (
    <div>
      {/* Audio element (hidden) */}
      <audio ref={audioRef} src={getTrackStreamUrl(trackId)} preload="metadata" />

      {/* Mini player controls */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8,
        padding: '6px 10px', borderRadius: 8, background: 'rgba(0,0,0,0.3)',
      }}>
        <button
          onClick={togglePlay}
          style={{
            background: 'var(--accent-primary)', border: 'none', borderRadius: '50%',
            width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', color: '#fff', fontSize: 12, flexShrink: 0,
          }}
        >
          {isPlaying ? '⏸' : '▶'}
        </button>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', width: 40, flexShrink: 0 }}>
          {formatTime(currentTime)}
        </span>
        <input
          type="range"
          min={0}
          max={duration || 1}
          value={currentTime}
          onChange={(e) => seekTo(parseFloat(e.target.value))}
          style={{ flex: 1, height: 4, cursor: 'pointer', accentColor: 'var(--accent-primary)' }}
        />
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', width: 40, textAlign: 'right', flexShrink: 0 }}>
          {formatTime(duration)}
        </span>
      </div>

      {/* Synced lyrics display */}
      <div
        ref={containerRef}
        style={{
          padding: '10px 12px', borderRadius: 8, background: 'rgba(0,0,0,0.25)',
          fontSize: 12, lineHeight: 1.8, maxHeight: 400, overflow: 'auto',
          fontFamily: 'monospace', scrollBehavior: 'smooth',
        }}
      >
        {lines.map((l) => {
          const timedIdx = timedIndexMap.get(l.key) ?? -1;
          const isActive = timedIdx >= 0 && timedIdx === activeIdx;
          return (
            <div
              key={l.key}
              data-line-idx={timedIdx >= 0 ? timedIdx : undefined}
              style={{
                minHeight: 20,
                cursor: l.isTs ? 'pointer' : 'default',
                padding: '1px 4px',
                borderRadius: 4,
                background: isActive ? 'rgba(139, 92, 246, 0.2)' : 'transparent',
                transition: 'all 0.2s',
              }}
              onClick={() => l.isTs && l.time >= 0 && seekTo(l.time)}
            >
              {l.isTs ? (
                <>
                  <span style={{
                    color: isActive ? '#a78bfa' : '#8b5cf6',
                    fontWeight: isActive ? 700 : 600,
                  }}>
                    {l.tag}
                  </span>
                  <span style={{
                    color: isActive ? '#fff' : 'var(--text-primary)',
                    marginLeft: 4,
                    fontWeight: isActive ? 600 : 400,
                    transition: 'all 0.2s',
                  }}>
                    {l.text}
                  </span>
                </>
              ) : (
                <span style={{ color: l.text.trim() ? 'var(--text-primary)' : 'transparent' }}>
                  {l.text || '\u00A0'}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Static LRC preview (no audio) — used when track audio is unavailable */
function LrcPreview({ content }: { content: string }) {
  const lines = useMemo(() => parseLrcLines(content), [content]);

  return (
    <div style={{
      padding: '10px 12px', borderRadius: 8, background: 'rgba(0,0,0,0.25)',
      fontSize: 12, lineHeight: 1.8, maxHeight: 400, overflow: 'auto', fontFamily: 'monospace',
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
                  🎵 Synced Preview
                </div>
                <SyncedLyricsPlayer content={content} trackId={trackId} />
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
