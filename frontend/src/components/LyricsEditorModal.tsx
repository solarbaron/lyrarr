import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Modal, Loader, Button, Group, Textarea, Select, FileButton, SegmentedControl, Collapse, Tabs, Tooltip, Kbd, useMantineTheme,
  type ModalProps,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faChevronDown, faClockRotateLeft, faCloudArrowUp, faLanguage, faPlay, faUpload, faWandMagicSparkles,
} from '@fortawesome/free-solid-svg-icons';
import { useState, useEffect, useMemo, useRef, type KeyboardEvent } from 'react';
import { readLyrics, uploadLyrics, saveLyricsFromEditor, publishLyrics, translateLyrics, generateSyncedLyrics, getLyricsVersions, restoreLyricsVersion, getTrackStreamUrl } from '../api';
import { errorMessage, notifyError } from '../notify';
import type { LyricsVersion } from '../types';
import { refreshQueries } from '../queryRefresh';
import { absTime, num } from '../format';
import { MOD_KEY } from '../shortcuts';
import { EmptyState, Tag } from './ui';
import { AudioTransport } from './lyrics/AudioTransport';
import { useAudioPlayer } from './lyrics/useAudioPlayer';
import { parseLrcLines, type LrcLine } from './lyrics/lrc';
import { providerName } from './lyrics/providers';
import c from './lyrics/LyricsEditorModal.module.css';

interface Props {
  trackId: number;
  trackTitle: string;
  albumId: number;
  opened: boolean;
  onClose: () => void;
  /** Context line under the title ("Tyler, The Creator · CHROMAKOPIA"). */
  artistName?: string;
  albumTitle?: string;
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

const WHISPER_MODELS = [
  { value: 'tiny', label: 'Tiny (fastest, ~1GB RAM)' },
  { value: 'base', label: 'Base (balanced, ~1.5GB RAM)' },
  { value: 'small', label: 'Small (better, ~2GB RAM)' },
  { value: 'medium', label: 'Medium (best, ~5GB RAM)' },
];

const PLACEHOLDER = "Paste or type lyrics here...\n\nFor synced (LRC) format:\n[00:12.34] First line of lyrics\n[00:15.67] Second line\n\nFor plain text:\nJust type the lyrics line by line";

type SideTab = 'preview' | 'timing' | 'translate' | 'versions';

const VERSION_ACTORS: Record<string, string> = {
  'batch-delete': 'batch delete',
  'batch-sync-generate': 'generated timing',
  'user-instrumental': 'marked instrumental',
  restored: 'restore',
  upload: 'upload',
  unknown: 'unknown origin',
  embedded: 'embedded tags',
};

/** "lrclib" → "LRCLIB"; audit labels ("audit:F-6") → "library check (F-6)". */
function versionActor(label: string): string {
  const audit = /^audit:(.+)$/.exec(label);
  if (audit) return `library check (${audit[1]})`;
  if (label.startsWith('batch-translate-')) return `translation (${label.slice('batch-translate-'.length)})`;
  return VERSION_ACTORS[label] ?? providerName(label);
}

/** Live synced preview: the current line is highlighted, clicking a timed line seeks. */
function SyncedPreview({ lines, currentTime, onSeek, canSeek }: {
  lines: LrcLine[]; currentTime: number; onSeek: (t: number) => void; canSeek: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const timed = useMemo(() => lines.filter(l => l.isTs && l.time >= 0), [lines]);
  const activeKey = useMemo(() => {
    let key = -1;
    for (const l of timed) {
      if (l.time <= currentTime) key = l.key;
      else break;
    }
    return key;
  }, [timed, currentTime]);

  // Keep the active line in view inside the preview (never scrolls the modal).
  useEffect(() => {
    const box = containerRef.current;
    if (activeKey < 0 || !box) return;
    const el = box.querySelector<HTMLElement>(`[data-line="${activeKey}"]`);
    if (!el) return;
    const top = el.offsetTop - box.clientHeight / 2 + el.clientHeight / 2;
    box.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }, [activeKey]);

  if (lines.every(l => !l.text.trim() && !l.isTs)) {
    return <div className={c.previewEmpty}>Nothing to preview yet. Timed lines appear here as you type.</div>;
  }

  return (
    <div ref={containerRef} className={c.preview} aria-label="Synced preview">
      {lines.map(l => {
        const active = l.key === activeKey;
        const seekable = canSeek && l.isTs && l.time >= 0;
        const content = (
          <>
            {l.isTs && <span className={c.ts}>{l.tag}</span>}
            <span className={c.lineText}>{l.text || ' '}</span>
          </>
        );
        return seekable ? (
          <button
            key={l.key}
            type="button"
            data-line={l.key}
            className={c.line}
            data-active={active || undefined}
            onClick={() => onSeek(l.time)}
            aria-label={`Seek to ${l.tag} ${l.text}`}
          >
            {content}
          </button>
        ) : (
          <div key={l.key} data-line={l.key} className={c.line} data-active={active || undefined} data-static="">
            {content}
          </div>
        );
      })}
    </div>
  );
}

/** Static LRC preview (generated timing). */
function LrcPreview({ content }: { content: string }) {
  const lines = useMemo(() => parseLrcLines(content), [content]);
  return (
    <div className={[c.preview, c.resultPreview].join(' ')}>
      {lines.map(l => (
        <div key={l.key} className={c.line} data-static="">
          {l.isTs && <span className={c.ts}>{l.tag}</span>}
          <span className={c.lineText}>{l.text || ' '}</span>
        </div>
      ))}
    </div>
  );
}

/** The lyrics editor (DESIGN.md 8.13). */
export default function LyricsEditorModal({ trackId, trackTitle, albumId, opened, onClose, artistName, albumTitle }: Props) {
  const queryClient = useQueryClient();
  const fullScreen = useMediaQuery('(max-width: 1023px)') ?? false;
  // Modal.Root doesn't read the theme's Modal defaults: take motion and overlay from there.
  const modalDefaults = useMantineTheme().components.Modal?.defaultProps as Partial<ModalProps> | undefined;
  const [content, setContent] = useState('');
  const [isSynced, setIsSynced] = useState(false);
  const [targetLang, setTargetLang] = useState<string | null>('en');
  const [translateMode, setTranslateMode] = useState('replace');
  const [translatedContent, setTranslatedContent] = useState<string | null>(null);
  const [syncModel, setSyncModel] = useState<string | null>('base');
  const [syncedPreview, setSyncedPreview] = useState<string | null>(null);
  const [sideTab, setSideTab] = useState<SideTab>('timing');
  // Full screen stacks the side panel under the editor: picking a tab brings it into view
  // above the sticky footer (scroll-margin in the CSS) instead of leaving it half covered.
  const sideRef = useRef<HTMLDivElement>(null);
  const pickSideTab = (v: string | null) => {
    if (!v) return;
    setSideTab(v as SideTab);
    if (fullScreen) window.requestAnimationFrame(() => sideRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  };
  const [expandedVersionId, setExpandedVersionId] = useState<number | null>(null);

  const player = useAudioPlayer(getTrackStreamUrl(trackId), { preload: isSynced ? 'metadata' : 'none' });
  const { pause } = player;

  // Audio pauses when the modal closes.
  useEffect(() => {
    if (!opened) pause();
  }, [opened, pause]);

  const { data: existingLyrics, isLoading } = useQuery({
    queryKey: ['lyrics-read', trackId],
    queryFn: () => readLyrics(trackId),
    enabled: opened,
  });

  const { data: versionsData, isLoading: loadingVersions } = useQuery({
    queryKey: ['lyrics-versions', trackId],
    queryFn: () => getLyricsVersions(trackId),
    enabled: opened && sideTab === 'versions',
  });

  const invalidateTrack = () => {
    queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
    queryClient.invalidateQueries({ queryKey: ['lyrics-read', trackId] });
    // Every Wanted list (by track, by album), its stats and the sidebar counts.
    refreshQueries(queryClient, ['wanted', 'nav']);
  };

  const restoreMutation = useMutation({
    mutationFn: (versionId: number) => restoreLyricsVersion(trackId, versionId),
    onSuccess: () => {
      notifications.show({ title: 'Restored', message: 'Previous version restored', color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['lyrics-read', trackId] });
      queryClient.invalidateQueries({ queryKey: ['lyrics-versions', trackId] });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
      refreshQueries(queryClient, ['wanted', 'nav']);
    },
    // Server reason verbatim: "The audio file isn't reachable at …", "This track changed while saving …"
    onError: (err) => notifyError('Couldn’t restore this version', err),
  });

  useEffect(() => {
    if (existingLyrics?.content) {
      setContent(existingLyrics.content);
      setIsSynced(existingLyrics.type === 'synced');
      setSideTab(existingLyrics.type === 'synced' ? 'preview' : 'timing');
    } else {
      setContent('');
      setIsSynced(false);
    }
    setTranslatedContent(null);
    setSyncedPreview(null);
  }, [existingLyrics]);

  // The Preview tab only exists for synced lyrics.
  useEffect(() => {
    if (!isSynced && sideTab === 'preview') setSideTab('timing');
  }, [isSynced, sideTab]);

  const saveMutation = useMutation({
    mutationFn: () => saveLyricsFromEditor(trackId, content, isSynced),
    onSuccess: () => {
      notifications.show({ title: 'Saved', message: 'Lyrics saved', color: 'success' });
      invalidateTrack();
    },
    onError: (err) => notifyError('Lyrics not saved', err),
  });

  const publishMutation = useMutation({
    mutationFn: () => publishLyrics(trackId),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Published', message: data.message, color: 'success' });
    },
    onError: (err: unknown) => notifications.show({
      title: 'Publish failed',
      message: errorMessage(err, 'Could not publish to LRCLIB'),
      color: 'danger',
      autoClose: false,
    }),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadLyrics(trackId, file),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Uploaded', message: data.message, color: 'success' });
      invalidateTrack();
    },
    onError: (err: unknown) => notifications.show({ title: 'Upload not saved', message: errorMessage(err), color: 'danger', autoClose: false }),
  });

  const translateMutation = useMutation({
    mutationFn: () => translateLyrics(trackId, {
      content: content,
      targetLang: targetLang || 'en',
      mode: translateMode,
    }),
    onSuccess: (data: any) => {
      setTranslatedContent(data.translated);
      notifications.show({ title: 'Translated', message: `Lyrics translated to ${targetLang}`, color: 'success' });
    },
    onError: (err: unknown) => notifications.show({ title: 'Translation failed', message: errorMessage(err, 'The translator returned an error.'), color: 'danger', autoClose: false }),
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
        title: 'Timing generated',
        message: `Matched ${data.matched}/${data.total_lines} lines (${data.segments} audio segments, language: ${data.language})`,
        color: 'success',
      });
    },
    onError: (err: unknown) => notifications.show({
      title: 'Timing not generated',
      message: errorMessage(err, 'Whisper returned an error.'),
      color: 'danger',
      autoClose: false,
    }),
  });

  const applySynced = () => {
    if (syncedPreview) {
      setContent(syncedPreview);
      setIsSynced(true);
      setSyncedPreview(null);
      setSideTab('preview');
    }
  };

  const save = () => {
    if (content.trim() && !saveMutation.isPending) saveMutation.mutate();
  };

  const lines = useMemo(() => parseLrcLines(content), [content]);
  // Number only real lines (the gutter still fills the frame), so the empty
  // editor's placeholder isn't mistaken for numbered content.
  const lineCount = content.split('\n').length;
  const showTransport = isSynced;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      save();
      return;
    }
    // Space plays/pauses the preview when focus is not in a text field or on a control.
    if (e.key === ' ' && showTransport && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const t = e.target as HTMLElement;
      const interactive = t.closest('input, textarea, select, button, a, [role="tab"], [role="radio"], [role="option"], [role="slider"], [contenteditable="true"]');
      if (!interactive) {
        e.preventDefault();
        player.toggle();
      }
    }
  };

  const context = [artistName, albumTitle].filter(Boolean).join(' · ');
  const versions: LyricsVersion[] = versionsData?.versions?.slice(0, 10) ?? [];

  return (
    <Modal.Root
      opened={opened}
      onClose={onClose}
      size={1100}
      fullScreen={fullScreen}
      radius={modalDefaults?.radius ?? 'lg'}
      centered
      transitionProps={modalDefaults?.transitionProps}
    >
      <Modal.Overlay {...modalDefaults?.overlayProps} />
      <Modal.Content onKeyDown={onKeyDown}>
        <Modal.Header className={c.header}>
          <Modal.Title className={c.titleWrap}>
            <span className={c.title}>Edit lyrics</span>
            <span className={c.context}>{[trackTitle, context].filter(Boolean).join(' · ')}</span>
          </Modal.Title>
          <SegmentedControl
            className={c.format}
            value={isSynced ? 'synced' : 'plain'}
            onChange={(v) => setIsSynced(v === 'synced')}
            data={[
              { label: 'Plain', value: 'plain' },
              { label: 'Synced (LRC)', value: 'synced' },
            ]}
            aria-label="Lyrics format"
          />
          <Modal.CloseButton aria-label="Close editor" />
        </Modal.Header>
        <Modal.Body className={c.body}>
          {/* Hidden audio element for the synced preview (no request until synced). */}
          <audio {...player.audioProps} />
          {isLoading ? (
            <div className={c.loading} role="status"><Loader /><span>Loading lyrics…</span></div>
          ) : (
            <>
              {/* No transport when the file can't load: the Preview tab says so in one muted line. */}
              {showTransport && !player.error && (
                <AudioTransport player={player} label="Seek preview audio" shortcut="Space" className={c.transport} />
              )}

              <div className={c.workspace}>
                {/* Editor with a line-number gutter; the frame scrolls both together. */}
                <div className={c.editorFrame}>
                  <div className={c.editorScroll}>
                    <div className={c.gutter} aria-hidden="true">
                      {Array.from({ length: lineCount }, (_, i) => <div key={i}>{i + 1}</div>)}
                    </div>
                    <Textarea
                      className={c.editor}
                      classNames={{ input: c.editorInput }}
                      value={content}
                      onChange={(e) => setContent(e.currentTarget.value)}
                      placeholder={PLACEHOLDER}
                      minRows={18}
                      autosize
                      spellCheck={false}
                      wrap="off"
                      aria-label="Lyrics"
                      data-autofocus
                    />
                  </div>
                </div>

                <Tabs
                  ref={sideRef}
                  value={sideTab}
                  onChange={pickSideTab}
                  className={c.side}
                  classNames={{ panel: c.sidePanel }}
                >
                  <Tabs.List className={c.sideTabs}>
                    {isSynced && <Tabs.Tab value="preview" leftSection={<FontAwesomeIcon icon={faPlay} />}>Preview</Tabs.Tab>}
                    <Tabs.Tab value="timing" leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />}>Timing</Tabs.Tab>
                    <Tabs.Tab value="translate" leftSection={<FontAwesomeIcon icon={faLanguage} />}>Translate</Tabs.Tab>
                    <Tabs.Tab value="versions" leftSection={<FontAwesomeIcon icon={faClockRotateLeft} />}>Versions</Tabs.Tab>
                  </Tabs.List>

                  {isSynced && (
                    <Tabs.Panel value="preview">
                      <SyncedPreview
                        lines={lines}
                        currentTime={player.time}
                        onSeek={player.seek}
                        canSeek={!player.error}
                      />
                      {player.error && (
                        <p className={c.note}>The audio file isn't available, so the preview can't follow playback.</p>
                      )}
                    </Tabs.Panel>
                  )}

                  <Tabs.Panel value="timing">
                    <p className={c.explain}>
                      Uses AI (Whisper) to transcribe the audio and align your plain lyrics with timestamps.
                      Requires <code className={c.code}>faster-whisper</code> to be installed.
                    </p>
                    <Select
                      label="Whisper model"
                      placeholder="Whisper model"
                      data={WHISPER_MODELS}
                      value={syncModel}
                      onChange={setSyncModel}
                      allowDeselect={false}
                    />
                    <Button
                      variant="light"
                      className={c.panelAction}
                      leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />}
                      onClick={() => syncMutation.mutate()}
                      loading={syncMutation.isPending}
                      disabled={!content.trim() || isSynced}
                    >
                      Generate timing
                    </Button>
                    {syncMutation.isPending && <p className={c.note}>Transcribing… this may take a minute.</p>}
                    {isSynced && !syncedPreview && (
                      <p className={c.note}>These lyrics are already synced. Switch to Plain to generate new timing.</p>
                    )}
                    {syncedPreview && (
                      <div className={c.result}>
                        <div className={c.resultTitle}>Generated timing</div>
                        <LrcPreview content={syncedPreview} />
                        <Group gap="xs" mt="sm">
                          <Button variant="light" onClick={applySynced}>Apply to editor</Button>
                          <Button variant="subtle" color="gray" onClick={() => setSyncedPreview(null)}>Discard</Button>
                        </Group>
                      </div>
                    )}
                  </Tabs.Panel>

                  <Tabs.Panel value="translate">
                    <Select
                      label="Target language"
                      placeholder="Target language"
                      data={LANGUAGES}
                      value={targetLang}
                      onChange={setTargetLang}
                      searchable
                    />
                    <div className={c.fieldLabel}>Mode</div>
                    <SegmentedControl
                      fullWidth
                      value={translateMode}
                      onChange={setTranslateMode}
                      data={[
                        { label: 'Replace', value: 'replace' },
                        { label: 'Dual (original + translation)', value: 'dual' },
                      ]}
                      aria-label="Translation mode"
                    />
                    <Button
                      variant="light"
                      className={c.panelAction}
                      leftSection={<FontAwesomeIcon icon={faLanguage} />}
                      onClick={() => translateMutation.mutate()}
                      loading={translateMutation.isPending}
                      disabled={!content.trim() || !targetLang}
                    >
                      Translate
                    </Button>
                    {translatedContent && (
                      <div className={c.result}>
                        <div className={c.resultTitle}>Translation</div>
                        <pre className={c.resultText}>{translatedContent}</pre>
                        <Group gap="xs" mt="sm">
                          <Button variant="light" onClick={applyTranslation}>Apply to editor</Button>
                          <Button variant="subtle" color="gray" onClick={() => setTranslatedContent(null)}>Discard</Button>
                        </Group>
                      </div>
                    )}
                  </Tabs.Panel>

                  <Tabs.Panel value="versions">
                    {loadingVersions ? (
                      <div className={c.loading} role="status"><Loader size="sm" /></div>
                    ) : versions.length ? (
                      <ul className={c.versions}>
                        {versions.map(v => {
                          const open = expandedVersionId === v.id;
                          return (
                            <li key={v.id} className={c.version}>
                              <div className={c.versionRow}>
                                <div className={c.versionInfo}>
                                  <span className={c.versionDate} title={absTime(v.timestamp)}>
                                    {new Date(v.timestamp).toLocaleString()}
                                  </span>
                                  <span className={c.versionMeta}>
                                    <Tag>{v.lyrics_type}</Tag>
                                    {/* From 1.7 `provider` is the origin of the text and `replaced_by` the
                                        writer that replaced it; legacy rows only have `provider`. */}
                                    <span>
                                      {versionActor(v.provider)}
                                      {v.replaced_by && <span className={c.versionReplaced}> · replaced by {versionActor(v.replaced_by)}</span>}
                                    </span>
                                    <span className="num">{num(v.content.length)} chars</span>
                                  </span>
                                  {v.translated_from && (
                                    <span className={c.versionFrom}>Translated from {v.translated_from}</span>
                                  )}
                                </div>
                                <Group gap={4} wrap="nowrap">
                                  <Button
                                    variant="subtle"
                                    color="gray"
                                    size="xs"
                                    aria-expanded={open}
                                    rightSection={<FontAwesomeIcon icon={faChevronDown} className={c.chevron} data-open={open || undefined} />}
                                    onClick={() => setExpandedVersionId(open ? null : v.id)}
                                  >
                                    {open ? 'Hide' : 'Preview'}
                                  </Button>
                                  <Button
                                    variant="default"
                                    size="xs"
                                    onClick={() => restoreMutation.mutate(v.id)}
                                    loading={restoreMutation.isPending && restoreMutation.variables === v.id}
                                    disabled={restoreMutation.isPending && restoreMutation.variables !== v.id}
                                  >
                                    Restore
                                  </Button>
                                </Group>
                              </div>
                              <Collapse in={open}>
                                <pre className={c.resultText}>{v.content}</pre>
                              </Collapse>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <EmptyState
                        size="sm"
                        icon={faClockRotateLeft}
                        title="No previous versions yet"
                        message="Versions are saved automatically when lyrics are updated."
                      />
                    )}
                  </Tabs.Panel>
                </Tabs>
              </div>

              <div className={c.footer} data-full-screen={fullScreen || undefined}>
                <FileButton onChange={(file) => file && uploadMutation.mutate(file)} accept=".lrc,.txt">
                  {(props) => (
                    <Button
                      {...props}
                      variant="subtle"
                      color="gray"
                      leftSection={<FontAwesomeIcon icon={faUpload} />}
                      loading={uploadMutation.isPending}
                    >
                      Upload .lrc/.txt
                    </Button>
                  )}
                </FileButton>
                <Tooltip label="Contribute the saved lyrics file to the LRCLIB community database">
                  <Button
                    variant="default"
                    leftSection={<FontAwesomeIcon icon={faCloudArrowUp} />}
                    onClick={() => publishMutation.mutate()}
                    loading={publishMutation.isPending}
                    disabled={!existingLyrics?.content}
                  >
                    Publish to LRCLIB
                  </Button>
                </Tooltip>
                {publishMutation.isPending && <span className={c.note}>Publishing… (solving challenge)</span>}
                {/* Kept together and pushed right, also when the row wraps on a phone */}
                <div className={c.footerEnd}>
                  <span className={c.hint} aria-hidden="true"><Kbd>{MOD_KEY}</Kbd> <Kbd>Enter</Kbd> save</span>
                  <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
                  <Button
                    variant="filled"
                    onClick={save}
                    loading={saveMutation.isPending}
                    disabled={!content.trim()}
                  >
                    Save lyrics
                  </Button>
                </div>
              </div>
            </>
          )}
        </Modal.Body>
      </Modal.Content>
    </Modal.Root>
  );
}
