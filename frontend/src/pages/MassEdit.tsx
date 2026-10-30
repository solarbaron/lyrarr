import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Badge, Button, Checkbox, Group, Loader, Modal, Progress, Radio,
  Select, TextInput,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { memo, useCallback, useDeferredValue, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import {
  getLibraryTree, batchDownload, batchTranslate, batchDeleteLyrics, upgradeLyrics,
  LibraryTreeArtist,
} from '../api';
import { SSEEvent } from '../types';
import { useServerEvents } from '../events';
import { refreshQueries } from '../queryRefresh';
import { errorMessage } from '../notify';

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
  { value: 'nl', label: 'Dutch' },
  { value: 'pl', label: 'Polish' },
  { value: 'sv', label: 'Swedish' },
  { value: 'tr', label: 'Turkish' },
];

interface MonitorEntry {
  id: number;
  time: string;
  color: string;
  message: string;
  replay?: boolean;
}

interface JobPart {
  label: string;
  unit: string;
  total: number;
  done: number;
}

interface ActiveJob {
  label: string;
  // Downloads report covers (albums) and lyrics (tracks) separately; other
  // batches have a single part.
  parts: JobPart[];
}

let monitorId = 0;

/** Right-hand live progress monitor fed by the app's shared SSE stream. */
function ProgressMonitor({ onJobFinished }: { onJobFinished: () => void }) {
  const [entries, setEntries] = useState<MonitorEntry[]>([]);
  const [job, setJob] = useState<ActiveJob | null>(null);
  const jobRef = useRef<ActiveJob | null>(null);
  jobRef.current = job;

  const bump = (unit: string) => setJob(j => (j ? {
    ...j,
    parts: j.parts.map(part => (part.unit === unit ? { ...part, done: part.done + 1 } : part)),
  } : j));

  useServerEvents((event: SSEEvent) => {
    const p = event.payload || {};
    const live = !event.replay;

    const push = (color: string, message?: string) => {
      if (!message) return;
      setEntries(prev => [{
        id: monitorId++,
        time: new Date().toLocaleTimeString(),
        color,
        message,
        replay: event.replay,
      }, ...prev].slice(0, 50));
    };

    // Every batch emits its *_complete event even when it fails (with an
    // `error` key) — always clear the running job and surface the error.
    const finish = (okColor?: string) => {
      if (p.error) {
        push('red', p.message ? `${p.message} — ${p.error}` : `Failed: ${p.error}`);
      } else {
        push(okColor || ((p.failed || 0) > 0 ? 'orange' : 'green'), p.message);
      }
      if (live) { setJob(null); onJobFinished(); }
    };

    switch (event.type) {
      case 'download_start': {
        push('violet', p.message);
        if (live) {
          const parts: JobPart[] = [];
          if (p.total_covers) parts.push({ label: 'Covers', unit: 'albums', total: p.total_covers, done: 0 });
          if (p.total_lyrics) parts.push({ label: 'Lyrics', unit: 'tracks', total: p.total_lyrics, done: 0 });
          setJob({ label: 'Downloading metadata', parts });
        }
        break;
      }
      case 'download_progress':
        if (live && jobRef.current) bump(p.metadata_type === 'cover' ? 'albums' : 'tracks');
        if (p.title) {
          push('gray', `${p.metadata_type === 'cover' ? 'Cover' : 'Lyrics'}: ${p.title} (${p.provider})`);
        }
        break;
      case 'download_complete':
        finish('green');
        break;
      case 'batch_translate_start':
        push('violet', p.message);
        if (live) setJob({ label: 'Translating lyrics', parts: [{ label: 'Tracks', unit: 'tracks', total: p.total || 0, done: 0 }] });
        break;
      case 'batch_sync_start':
        push('violet', p.message);
        if (live) setJob({ label: 'Generating synced lyrics', parts: [{ label: 'Tracks', unit: 'tracks', total: p.total || 0, done: 0 }] });
        break;
      case 'batch_translate_complete':
      case 'batch_sync_complete':
      case 'batch_delete_complete':
        finish();
        break;
      case 'sync_complete':
        push('blue', p.message);
        if (live) onJobFinished();
        break;
      case 'health':
        push(p.healthy ? 'green' : 'red', p.message);
        break;
      default:
        break;
    }
  });

  return (
    <div style={{
      padding: 16, borderRadius: 12, background: 'var(--card-bg)',
      border: '1px solid var(--card-border)', position: 'sticky', top: 16,
      maxHeight: 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>📡 Progress Monitor</div>

      {job ? (
        <div style={{ marginBottom: 12 }} role="status">
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            <Loader size={10} color="violet" style={{ marginRight: 6 }} />
            {job.label}
          </div>
          {(job.parts.length ? job.parts : [{ label: '', unit: '', total: 0, done: 0 }]).map(part => (
            <div key={part.unit || 'none'} style={{ marginBottom: 6 }}>
              {part.total > 0 && (
                <Group justify="space-between" mb={2}>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{part.label}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                    {Math.min(part.done, part.total)} / {part.total} {part.unit}
                  </span>
                </Group>
              )}
              <Progress
                value={part.total ? Math.min(100, (part.done / part.total) * 100) : 100}
                animated
                color="violet"
                size="sm"
              />
            </div>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
          No bulk job running. Actions started here report progress live.
        </div>
      )}

      <div style={{ overflowY: 'auto', flex: 1 }}>
        {entries.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>No recent activity.</div>
        )}
        {entries.map(entry => (
          <div key={entry.id} style={{
            display: 'flex', gap: 8, alignItems: 'baseline',
            padding: '4px 0', fontSize: 12, opacity: entry.replay ? 0.55 : 1,
            borderBottom: '1px solid var(--card-border)',
          }}>
            <span style={{ color: 'var(--text-secondary)', flexShrink: 0, fontSize: 11 }}>{entry.time}</span>
            <Badge color={entry.color} size="xs" variant="light" style={{ flexShrink: 0 }}>•</Badge>
            <span style={{ wordBreak: 'break-word' }}>{entry.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface ArtistRowProps {
  artist: LibraryTreeArtist;
  selected: Set<number>;
  isExpanded: boolean;
  onToggleArtist: (artist: LibraryTreeArtist) => void;
  onToggleAlbum: (id: number) => void;
  onToggleExpanded: (id: number) => void;
}

/**
 * One artist (and, when expanded, its albums). Memoized so toggling one
 * album or typing in the filter doesn't re-render every row of a large
 * library: a row only re-renders when its own albums' selection changes.
 */
const ArtistRow = memo(function ArtistRow({
  artist, selected, isExpanded, onToggleArtist, onToggleAlbum, onToggleExpanded,
}: ArtistRowProps) {
  const selectedCount = artist.albums.filter(a => selected.has(a.id)).length;
  const allSelected = selectedCount === artist.albums.length;
  const regionId = `mass-edit-artist-${artist.id}`;
  return (
    <div style={{
      borderBottom: '1px solid var(--card-border)',
      // Let the browser skip layout/paint for off-screen rows.
      contentVisibility: 'auto', containIntrinsicSize: 'auto 41px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px' }}>
        <Checkbox
          size="xs"
          color="violet"
          checked={allSelected}
          indeterminate={selectedCount > 0 && !allSelected}
          onChange={() => onToggleArtist(artist)}
          aria-label={`Select all albums by ${artist.name}`}
        />
        <button
          type="button"
          className="unstyled-button"
          aria-expanded={isExpanded}
          aria-controls={regionId}
          style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
          onClick={() => onToggleExpanded(artist.id)}
        >
          <span aria-hidden="true" style={{
            fontSize: 11, transform: isExpanded ? 'rotate(90deg)' : 'none',
            transition: '0.15s', color: 'var(--text-secondary)',
          }}>▶</span>
          <span style={{ fontWeight: 600, fontSize: 13 }}>{artist.name}</span>
          <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            {artist.albums.length} album{artist.albums.length === 1 ? '' : 's'}
          </span>
        </button>
        {selectedCount > 0 && (
          <Badge size="xs" color="violet" variant="light">{selectedCount} selected</Badge>
        )}
        <Link to={`/artists/${artist.id}`} style={{ fontSize: 11, color: 'var(--text-secondary)' }}
          aria-label={`Open ${artist.name}`}>
          Open
        </Link>
      </div>

      {isExpanded && (
        <div id={regionId}>
          {artist.albums.map(album => (
            <div
              key={album.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '6px 12px 6px 40px', fontSize: 12,
                background: selected.has(album.id) ? 'rgba(139, 92, 246, 0.08)' : 'transparent',
              }}
            >
              <Checkbox
                size="xs"
                color="violet"
                checked={selected.has(album.id)}
                onChange={() => onToggleAlbum(album.id)}
                style={{ flex: 1 }}
                label={
                  <>
                    {album.title}
                    {album.year ? <span style={{ color: 'var(--text-secondary)' }}> ({album.year})</span> : null}
                  </>
                }
              />
              <Group gap={4} wrap="nowrap">
                {album.synced > 0 && (
                  <Badge size="xs" color="green" variant="light">{album.synced} synced</Badge>
                )}
                {album.withLyrics - album.synced > 0 && (
                  <Badge size="xs" color="yellow" variant="light">{album.withLyrics - album.synced} plain</Badge>
                )}
                {album.missing > 0 && (
                  <Badge size="xs" color="red" variant="light">{album.missing} missing</Badge>
                )}
                {album.instrumental > 0 && (
                  <Badge size="xs" color="gray" variant="light">{album.instrumental} instr.</Badge>
                )}
              </Group>
              <Link to={`/albums/${album.id}`} style={{ fontSize: 11, color: 'var(--text-secondary)' }}
                aria-label={`Open ${album.title}`}>
                Open
              </Link>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}, (prev, next) => {
  if (prev.artist !== next.artist || prev.isExpanded !== next.isExpanded) return false;
  if (prev.selected === next.selected) return true;
  // Callbacks are stable; only this artist's slice of the selection matters.
  return prev.artist.albums.every(a => prev.selected.has(a.id) === next.selected.has(a.id));
});

export default function MassEditPage() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  // Filtering a big library is the expensive part; let typing stay responsive
  // and re-filter at lower priority.
  const deferredSearch = useDeferredValue(search);
  const [genre, setGenre] = useState<string | null>(null);
  const [translateOpen, setTranslateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [targetLang, setTargetLang] = useState<string | null>('en');
  const [forceTranslate, setForceTranslate] = useState(false);
  const [deleteMode, setDeleteMode] = useState<'all' | 'plain' | 'instrumental'>('all');

  const { data: tree, isLoading } = useQuery({
    queryKey: ['library-tree'],
    queryFn: getLibraryTree,
  });

  // Apply search + genre filters. An artist survives if it matches by name or
  // has at least one album passing both filters.
  const filteredArtists: LibraryTreeArtist[] = useMemo(() => {
    if (!tree) return [];
    const q = deferredSearch.trim().toLowerCase();
    return tree.artists
      .map(artist => {
        const nameMatch = q && artist.name.toLowerCase().includes(q);
        const albums = artist.albums.filter(album => {
          if (genre && !album.genres.includes(genre)) return false;
          if (q && !nameMatch && !album.title.toLowerCase().includes(q)) return false;
          return true;
        });
        return { ...artist, albums };
      })
      .filter(artist => artist.albums.length > 0);
  }, [tree, deferredSearch, genre]);

  const visibleAlbumIds = useMemo(
    () => new Set(filteredArtists.flatMap(a => a.albums.map(al => al.id))),
    [filteredArtists],
  );

  const selectionStats = useMemo(() => {
    let albums = 0, tracks = 0, missing = 0, unsynced = 0;
    for (const artist of tree?.artists || []) {
      for (const album of artist.albums) {
        if (!selected.has(album.id)) continue;
        albums += 1;
        tracks += album.tracks;
        missing += album.missing;
        unsynced += Math.max(0, album.withLyrics - album.synced);
      }
    }
    return { albums, tracks, missing, unsynced };
  }, [tree, selected]);

  const toggleAlbum = useCallback((id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleArtist = useCallback((artist: LibraryTreeArtist) => {
    setSelected(prev => {
      const next = new Set(prev);
      const allSelected = artist.albums.every(a => next.has(a.id));
      artist.albums.forEach(a => allSelected ? next.delete(a.id) : next.add(a.id));
      return next;
    });
  }, []);

  const toggleExpanded = useCallback((id: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const selectAllVisible = () => setSelected(new Set([...selected, ...visibleAlbumIds]));
  const clearSelection = () => setSelected(new Set());

  const selectedIds = useMemo(() => [...selected], [selected]);

  const onError = (err: unknown) => notifications.show({
    title: 'Not Started',
    message: errorMessage(err),
    color: 'orange',
  });
  const onStarted = (data: any) => notifications.show({
    title: 'Started', message: data.message, color: 'violet',
  });

  const fetchMutation = useMutation({
    mutationFn: () => batchDownload({ albumIds: selectedIds, type: 'all' }),
    onSuccess: onStarted,
    onError,
  });
  const upgradeMutation = useMutation({
    mutationFn: () => upgradeLyrics({ albumIds: selectedIds }),
    onSuccess: onStarted,
    onError,
  });
  const translateMutation = useMutation({
    mutationFn: () => batchTranslate({ albumIds: selectedIds, targetLang: targetLang || 'en', force: forceTranslate }),
    onSuccess: (data: any) => { setTranslateOpen(false); onStarted(data); },
    onError,
  });
  const deleteMutation = useMutation({
    mutationFn: () => batchDeleteLyrics({ albumIds: selectedIds, mode: deleteMode }),
    onSuccess: (data: any) => {
      setDeleteOpen(false);
      notifications.show({ title: 'Deleted', message: data.message, color: 'green' });
      // Album/track statuses, wanted lists and dashboard counts all changed.
      refreshQueries(queryClient);
    },
    onError,
  });

  const refreshTree = useCallback(
    () => { queryClient.invalidateQueries({ queryKey: ['library-tree'] }); },
    [queryClient],
  );

  const inputStyles = {
    input: { background: 'rgba(0,0,0,0.2)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' },
  };

  return (
    <div style={{ paddingBottom: selected.size > 0 ? 90 : 0 }}>
      <PageHeader
        title="Mass Edit"
        subtitle="Select artists, albums or genres and run bulk operations"
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 340px', gap: 16, alignItems: 'start' }}>
        {/* Left: selection workspace */}
        <div>
          <Group gap="sm" mb="md" wrap="wrap">
            <TextInput
              aria-label="Filter artists and albums"
              placeholder="Filter artists / albums…"
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
              size="xs"
              style={{ minWidth: 220, flex: 1 }}
              styles={inputStyles}
            />
            <Select
              aria-label="Filter by genre"
              placeholder="Genre"
              data={tree?.genres || []}
              value={genre}
              onChange={setGenre}
              clearable
              searchable
              size="xs"
              style={{ width: 180 }}
              styles={inputStyles}
            />
            <Button variant="light" color="violet" size="xs" onClick={selectAllVisible}>
              Select All ({visibleAlbumIds.size})
            </Button>
            <Button variant="subtle" color="gray" size="xs" onClick={clearSelection} disabled={selected.size === 0}>
              Clear
            </Button>
          </Group>

          {isLoading ? (
            <div style={{ textAlign: 'center', padding: 60 }}><Loader color="violet" /></div>
          ) : (
            <div style={{
              borderRadius: 12, border: '1px solid var(--card-border)',
              background: 'var(--card-bg)', overflow: 'hidden',
            }}>
              {filteredArtists.length === 0 && (
                <div style={{ padding: 24, fontSize: 13, color: 'var(--text-secondary)', textAlign: 'center' }}>
                  No artists match the current filters.
                </div>
              )}
              {filteredArtists.map(artist => (
                <ArtistRow
                  key={artist.id}
                  artist={artist}
                  selected={selected}
                  isExpanded={expanded.has(artist.id)}
                  onToggleArtist={toggleArtist}
                  onToggleAlbum={toggleAlbum}
                  onToggleExpanded={toggleExpanded}
                />
              ))}
            </div>
          )}
        </div>

        {/* Right: live progress monitor */}
        <ProgressMonitor onJobFinished={refreshTree} />
      </div>

      {/* Floating batch-action toolbar */}
      {selected.size > 0 && (
        <div style={{
          position: 'fixed', bottom: 16, left: '50%', transform: 'translateX(-50%)',
          zIndex: 100, display: 'flex', alignItems: 'center', gap: 12,
          padding: '10px 18px', borderRadius: 14,
          background: 'var(--surface-bg)', border: '1px solid var(--card-border)',
          boxShadow: '0 8px 30px rgba(0,0,0,0.45)', flexWrap: 'wrap', maxWidth: '95vw',
        }}>
          <span style={{ fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap' }}>
            {selectionStats.albums} album{selectionStats.albums === 1 ? '' : 's'} · {selectionStats.tracks} tracks
          </span>
          <Button size="xs" variant="light" color="violet"
            onClick={() => fetchMutation.mutate()} loading={fetchMutation.isPending}>
            Fetch Missing ({selectionStats.missing})
          </Button>
          <Button size="xs" variant="light" color="blue"
            onClick={() => upgradeMutation.mutate()} loading={upgradeMutation.isPending}>
            Upgrade to Synced ({selectionStats.unsynced})
          </Button>
          <Button size="xs" variant="light" color="teal" onClick={() => setTranslateOpen(true)}>
            Translate…
          </Button>
          <Button size="xs" variant="light" color="red" onClick={() => setDeleteOpen(true)}>
            Delete Lyrics…
          </Button>
          <Button size="xs" variant="subtle" color="gray" onClick={clearSelection} aria-label="Clear selection">✕</Button>
        </div>
      )}

      {/* Translate modal */}
      <Modal opened={translateOpen} onClose={() => setTranslateOpen(false)} title="Mass Translate" size="sm">
        <Select
          label="Target language"
          data={LANGUAGES}
          value={targetLang}
          onChange={setTargetLang}
          mb="sm"
          styles={inputStyles}
        />
        <Checkbox
          label="Force re-translate tracks whose language is already known"
          description="Without this, only tracks with an undetected language are processed."
          size="xs"
          color="violet"
          checked={forceTranslate}
          onChange={(e) => setForceTranslate(e.currentTarget.checked)}
          mb="md"
        />
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
          Uses the translation engine configured in Settings (Google or DeepL).
        </div>
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" size="xs" onClick={() => setTranslateOpen(false)}>Cancel</Button>
          <Button color="teal" size="xs" onClick={() => translateMutation.mutate()}
            loading={translateMutation.isPending} disabled={!targetLang}>
            Translate {selectionStats.albums} album{selectionStats.albums === 1 ? '' : 's'}
          </Button>
        </Group>
      </Modal>

      {/* Delete modal */}
      <Modal opened={deleteOpen} onClose={() => setDeleteOpen(false)} title="Mass Delete Lyrics" size="sm">
        <Radio.Group value={deleteMode} onChange={(v) => setDeleteMode(v as typeof deleteMode)} mb="md">
          <Radio value="all" color="red" size="xs" label="All lyrics files" mb={6}
            description="Every lyrics file in the selection" />
          <Radio value="plain" color="red" size="xs" label="Plain (unsynced) lyrics only" mb={6}
            description="Keep synced .lrc files, remove plain-text ones" />
          <Radio value="instrumental" color="red" size="xs" label="Instrumental tracks only"
            description="Remove stale lyrics files from tracks classified as instrumental" />
        </Radio.Group>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
          Files are archived to each track's version history before deletion, so
          individual tracks can be restored from the lyrics editor.
        </div>
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" size="xs" onClick={() => setDeleteOpen(false)}>Cancel</Button>
          <Button color="red" size="xs" onClick={() => deleteMutation.mutate()} loading={deleteMutation.isPending}>
            Delete from {selectionStats.albums} album{selectionStats.albums === 1 ? '' : 's'}
          </Button>
        </Group>
      </Modal>
    </div>
  );
}
