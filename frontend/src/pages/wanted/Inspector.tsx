import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Kbd, Loader } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faArrowRight, faBan, faCircleExclamation, faDownload, faMagnifyingGlass, faMicrophone, faMusic,
  faPenToSquare, faRotateRight,
} from '@fortawesome/free-solid-svg-icons';
import { searchLyrics, downloadLyrics, blacklistLyrics, getTrackStreamUrl } from '../../api';
import type { LyricsResult } from '../../types';
import { notifyError, errorMessage } from '../../notify';
import { duration as fmtDuration, num, plural } from '../../format';
import { CoverArt, EmptyState, IconButton, LyricsMeter, Panel, StatusDot } from '../../components/ui';
import LyricsCandidateList from '../../components/LyricsCandidateList';
import { AudioTransport } from '../../components/lyrics/AudioTransport';
import { useAudioPlayer } from '../../components/lyrics/useAudioPlayer';
import { providerName } from '../../components/lyrics/providers';
import type { AlbumCounts } from '../../hooks/useAlbumCounts';
import { InstrumentalEvidence, InstrumentalHintTag } from '../../components/instrumental/InstrumentalEvidence';
import { instrumentalState } from '../../components/instrumental/model';
import { useInstrumentalActions } from '../../components/instrumental/useInstrumentalActions';
import type { Track } from '../../types';
import { Attempts, refreshWanted, type WantedAlbum } from './shared';
import c from './Wanted.module.css';

/** Provider results stay fresh for 5 minutes so moving the cursor back never re-queries providers. */
export const SEARCH_STALE = 5 * 60_000;

export const lyricsSearchKey = (trackId: number) => ['lyrics-search', trackId, ''] as const;

export interface InspectorTrack {
  id: number;
  title: string;
  trackNumber?: number | null;
  discNumber?: number | null;
  duration?: number | null;
  albumId: number;
  albumTitle?: string;
  artistName?: string;
  retryAfter?: string | null;
  retryCount?: number | null;
  /** The track row, for the "Why instrumental" section (hints, flags, conflicts). */
  row?: Pick<Track, 'lyrics_status' | 'title'> & Partial<Track>;
}

export interface InspectorAlbum {
  album: WantedAlbum;
  counts?: AlbumCounts;
  /** Missing tracks once the album was expanded. */
  missing?: number;
}

export interface InspectorHandle {
  /** Search providers for the current track (s). */
  search(): void;
  /** Highlight candidate i (1–9). Returns true when a candidate exists. */
  highlight(i: number): boolean;
  /** Apply the highlighted candidate (Enter). Returns true when something was applied. */
  apply(): boolean;
  /** Candidates are on screen. */
  hasCandidates(): boolean;
}

interface Props {
  track: InspectorTrack | null;
  album: InspectorAlbum | null;
  /** The user asked to search this track (s / Search / Enter). */
  requested: boolean;
  onRequestSearch(trackId: number): void;
  /** A candidate was saved: the page moves the cursor on. */
  onApplied(trackId: number): void;
  onEdit(track: InspectorTrack): void;
  /** Open the full "Find lyrics" dialog (custom query, two-pane preview). */
  onOpenSearch(track: InspectorTrack): void;
  onSkip(track: InspectorTrack): void;
  onDownloadAlbum(album: WantedAlbum): void;
  downloadingAlbum?: boolean;
}

function TrackAudio({ track }: { track: InspectorTrack }) {
  const player = useAudioPlayer(getTrackStreamUrl(track.id), { preload: 'none', knownDuration: track.duration ?? undefined });
  return (
    <>
      <audio {...player.audioProps} />
      <AudioTransport player={player} label="Seek audio preview" className={c.audio} />
    </>
  );
}

/** Keyboard hints: [keys, label, separator] ("1–9 pick", "j/k move"). */
function Hints({ items }: { items: [string[], string, string?][] }) {
  return (
    <div className={c.hints} aria-hidden="true">
      {items.map(([keys, label, sep]) => (
        <span key={label} className={c.hint}>
          {keys.map((k, i) => (
            <span key={i} className={c.hintKey}>
              {i > 0 && sep && <span className={c.hintSep}>{sep}</span>}
              <Kbd>{k}</Kbd>
            </span>
          ))}
          <span className={c.hintLabel}>{label}</span>
        </span>
      ))}
    </div>
  );
}

/**
 * Wanted inspector (DESIGN.md 8.6): follows the table cursor. Shows the track,
 * an audio preview, on-demand provider search and the candidate list; never
 * searches by itself (auto-search lives in the page and is off by default).
 */
export const Inspector = forwardRef<InspectorHandle, Props>(function Inspector(
  { track, album, requested, onRequestSearch, onApplied, onEdit, onOpenSearch, onSkip, onDownloadAlbum, downloadingAlbum },
  ref,
) {
  const queryClient = useQueryClient();
  const trackId = track?.id ?? 0;
  const [highlighted, setHighlighted] = useState(0);
  const instrumental = useInstrumentalActions();

  const search = useQuery({
    queryKey: lyricsSearchKey(trackId),
    queryFn: () => searchLyrics(trackId),
    enabled: !!track && requested,
    staleTime: SEARCH_STALE,
  });
  const results = useMemo(() => search.data?.results ?? [], [search.data]);
  const hasResults = !!search.data;

  useEffect(() => { setHighlighted(0); }, [trackId, results]);
  const current = results[Math.min(highlighted, Math.max(0, results.length - 1))] as LyricsResult | undefined;

  const applyMutation = useMutation({
    mutationFn: ({ result }: { result: LyricsResult; track: InspectorTrack }) => downloadLyrics(trackId, {
      synced_lyrics: result.synced_lyrics,
      plain_lyrics: result.plain_lyrics,
      provider: result.provider,
    }),
    onSuccess: (_data, { result, track: t }) => {
      notifications.show({
        title: 'Lyrics saved',
        message: `Saved ${result.synced_lyrics ? 'synced' : 'plain'} lyrics from ${providerName(result.provider)}`,
        color: 'success',
      });
      refreshWanted(queryClient);
      queryClient.invalidateQueries({ queryKey: ['album', String(t.albumId)] });
      queryClient.invalidateQueries({ queryKey: ['lyrics-read', t.id] });
      onApplied(t.id);
    },
    onError: (err) => notifyError('Lyrics not saved', err),
  });

  const blacklistMutation = useMutation({
    mutationFn: (result: LyricsResult) => blacklistLyrics(trackId, {
      synced_lyrics: result.synced_lyrics,
      plain_lyrics: result.plain_lyrics,
      provider: result.provider,
      rescan: false,
    }),
    onSuccess: () => {
      notifications.show({ title: 'Result blocked', message: 'This result won\'t be auto-selected again', color: 'warning' });
      queryClient.invalidateQueries({ queryKey: lyricsSearchKey(trackId) });
    },
    onError: (err) => notifyError('Result not blocked', err),
  });

  const apply = (r: LyricsResult | undefined) => {
    if (!track || !r || applyMutation.isPending) return false;
    applyMutation.mutate({ result: r, track });
    return true;
  };

  /*
   * "Search providers" is disabled while loading and unmounts when results (or an error)
   * arrive, so the browser drops its focus to <body>. Once it is gone, hand focus to the
   * highlighted candidate (or the next control) so Enter, 1–9 and j/k carry on from the
   * keyboard (DESIGN.md 9.3).
   */
  const headRef = useRef<HTMLDivElement>(null);
  const searchButton = useRef<HTMLButtonElement | null>(null);
  const searchHadFocus = useRef(false);
  const searchButtonRef = useCallback((el: HTMLButtonElement | null) => {
    searchButton.current = el;
    if (!el) return undefined;
    return () => {
      if (document.activeElement === el) searchHadFocus.current = true;
      searchButton.current = null;
    };
  }, []);
  useEffect(() => { searchHadFocus.current = false; }, [trackId]);
  useEffect(() => {
    if (!searchHadFocus.current || searchButton.current) return;
    searchHadFocus.current = false;
    const active = document.activeElement;
    if (active && active !== document.body) return;
    const panel = headRef.current?.parentElement;
    const target = panel?.querySelector<HTMLElement>('li[data-highlighted]')
      ?? panel?.querySelector<HTMLElement>('[data-scroll] button')
      ?? panel?.querySelector<HTMLElement>('[data-inspector-foot] button');
    target?.focus({ preventScroll: true });
  });

  useImperativeHandle(ref, () => ({
    search: () => { if (track) onRequestSearch(track.id); },
    highlight: (i: number) => {
      if (i < 0 || i >= results.length) return false;
      setHighlighted(i);
      return true;
    },
    apply: () => apply(current),
    hasCandidates: () => results.length > 0,
  }));

  /* ---------- album cursor ---------- */
  if (!track && album) {
    const a = album.album;
    const counts = album.counts;
    return (
      <Panel className={c.inspector} aria-label="Inspector" padding="none">
        <div className={c.inspectorBody}>
          <div className={c.albumHead}>
            <CoverArt src={a.cover} alt="" kind="album" name={a.title} size={72} sleeve />
            <div className={c.albumHeadText}>
              <div className="ly-overline">Album{a.year ? ` · ${a.year}` : ''}</div>
              <div className={c.inspectorTitle} title={a.title}>{a.title}</div>
              <div className={c.inspectorSub}>{a.artistName || 'Unknown artist'}</div>
            </div>
          </div>
          <div className={c.albumStat}>
            {counts ? (
              <>
                <LyricsMeter synced={counts.synced} plain={counts.plain} instrumental={counts.instrumental} total={counts.total} size="md" width="100%" />
                <span className={c.albumStatText}>{plural(counts.missing, 'track')} missing lyrics</span>
              </>
            ) : (
              <StatusDot domain="albumLyrics" status={a.lyrics_status} />
            )}
          </div>
          <div className={c.inspectorStack}>
            <Button
              variant="light"
              fullWidth
              leftSection={<FontAwesomeIcon icon={faDownload} />}
              loading={downloadingAlbum}
              onClick={() => onDownloadAlbum(a)}
            >
              Download lyrics for this album
            </Button>
            <Button
              component={Link}
              to={`/albums/${a.lidarrAlbumId}`}
              variant="default"
              fullWidth
              rightSection={<FontAwesomeIcon icon={faArrowRight} />}
            >
              Open album
            </Button>
          </div>
          <p className={c.inspectorNote}>
            Expand the album to triage its tracks one by one, or download every missing track at once.
          </p>
        </div>
        <div className={c.inspectorFoot}>
          <Hints items={[[['→'], 'expand'], [['←'], 'collapse'], [['j', 'k'], 'move', '/'], [['o'], 'album']]} />
        </div>
      </Panel>
    );
  }

  /* ---------- nothing under the cursor ---------- */
  if (!track) {
    return (
      <Panel className={c.inspector} aria-label="Inspector" padding="none">
        <div className={c.inspectorBody}>
          <EmptyState
            size="sm"
            icon={faAlignLeft}
            title="Pick a track"
            message={<>Click a row or move the cursor with <Kbd>j</Kbd> <Kbd>k</Kbd>. Search providers to see lyrics candidates here.</>}
          />
        </div>
      </Panel>
    );
  }

  /* ---------- track cursor ---------- */
  const eyebrow = [
    track.trackNumber ? `Track ${track.discNumber && track.discNumber > 1 ? `${track.discNumber}·` : ''}${track.trackNumber}` : 'Track',
    track.albumTitle,
  ].filter(Boolean).join(' · ');
  const searching = search.isFetching && !hasResults;

  const showCandidates = hasResults && results.length > 0;
  const instr = track.row ? instrumentalState(track.row) : null;
  const instrTarget = { id: track.id, title: track.title, hasLyrics: track.row?.lyrics_status === 'available' || track.row?.lyrics_status === 'manual' };

  return (
    <Panel className={c.inspector} aria-label="Inspector" padding="none">
      {/* Track header and player stay put; only the candidates scroll. */}
      <div className={c.inspectorHead} ref={headRef}>
        <div>
          <div className={`ly-overline ${c.eyebrow}`} title={eyebrow}>{eyebrow}</div>
          <div className={c.inspectorTitle} title={track.title}>{track.title}</div>
          <div className={c.inspectorSubRow}>
            <span className={c.inspectorSub}>
              {[track.artistName, track.duration ? fmtDuration(track.duration) : null].filter(Boolean).join(' · ')}
            </span>
            <Attempts after={track.retryAfter} count={track.retryCount} />
          </div>
        </div>

        <TrackAudio key={track.id} track={track} />

        {!hasResults && !search.isError && (
          <Button
            ref={searchButtonRef}
            variant="default"
            fullWidth
            leftSection={searching ? undefined : <FontAwesomeIcon icon={faMagnifyingGlass} />}
            rightSection={searching ? undefined : <Kbd>s</Kbd>}
            loading={searching}
            onClick={e => {
              searchHadFocus.current = document.activeElement === e.currentTarget;
              onRequestSearch(track.id);
            }}
          >
            Search providers
          </Button>
        )}
        {searching && <div className={c.inspectorNote} role="status">Searching lyrics providers…</div>}

        {hasResults && (
          <div className={c.candidatesHead}>
            <span className="ly-overline">Candidates</span>
            <span className={c.candidatesCount}>
              {search.isFetching ? <Loader size={12} /> : null}
              {results.length ? plural(results.length, 'result') : 'No results'}
              {showCandidates && (
                <>
                  <span aria-hidden="true">·</span>
                  <button type="button" className={`ly-link ${c.linkButton}`} onClick={() => onOpenSearch(track)}>Refine search</button>
                </>
              )}
            </span>
          </div>
        )}
      </div>

      {(search.isError && !hasResults) || hasResults || instr ? (
        <div className={c.inspectorBody} data-scroll="">
          {instr && track.row && (
            <section aria-label="Why instrumental" className={c.instrSection}>
              <div className={c.instrHead}>
                <span className="ly-overline">Why instrumental</span>
                <InstrumentalHintTag track={track.row} />
              </div>
              <InstrumentalEvidence track={track.row} compact />
              <div className={c.instrActions}>
                <Button
                  variant="light"
                  size="xs"
                  leftSection={<FontAwesomeIcon icon={faMicrophone} />}
                  loading={instrumental.isBusy(`vocal-${track.id}`)}
                  onClick={() => void instrumental.notInstrumental(instrTarget)}
                >
                  Not instrumental — search again
                </Button>
                {track.row.lyrics_status !== 'instrumental' && (
                  <Button
                    variant="default"
                    size="xs"
                    leftSection={<FontAwesomeIcon icon={faMusic} />}
                    loading={instrumental.isBusy(`mark-${track.id}`)}
                    onClick={() => void instrumental.markInstrumental(instrTarget)}
                  >
                    Confirm instrumental
                  </Button>
                )}
              </div>
            </section>
          )}
          {search.isError && !hasResults && (
            <EmptyState
              size="sm"
              tone="danger"
              icon={faCircleExclamation}
              title="Search failed"
              message={errorMessage(search.error)}
              action={<Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} onClick={() => search.refetch()}>Retry</Button>}
            />
          )}

          {hasResults && (
            <section aria-label="Candidates" className={c.candidates}>
              {showCandidates ? (
                <LyricsCandidateList
                  results={results}
                  highlighted={highlighted}
                  onHighlight={setHighlighted}
                  onApply={r => apply(r)}
                  onNeverSelect={r => blacklistMutation.mutate(r)}
                  applying={applyMutation.isPending}
                  neverSelecting={blacklistMutation.isPending}
                  compact
                />
              ) : (
                <EmptyState
                  size="sm"
                  icon={faAlignLeft}
                  title="No lyrics found from any provider"
                  message="Try a custom query, open the editor to paste lyrics, or skip the track if it has none."
                  action={<Button variant="default" leftSection={<FontAwesomeIcon icon={faMagnifyingGlass} />} onClick={() => onOpenSearch(track)}>Refine search</Button>}
                />
              )}
            </section>
          )}
        </div>
      ) : <div className={c.inspectorFill} />}

      <div className={c.inspectorFoot} data-inspector-foot="">
        {/* One row: Use (when there is a candidate) · Editor · Skip · Open album */}
        <div className={c.inspectorActions}>
          {current && (
            <Button
              variant="filled"
              className={c.useButton}
              rightSection={<Kbd className={c.onAccentKbd}>⏎</Kbd>}
              loading={applyMutation.isPending}
              onClick={() => apply(current)}
            >
              Use {providerName(current.provider)}
            </Button>
          )}
          <Button
            variant="default"
            className={c.editorButton}
            aria-label="Open editor"
            // Next to Use the row is tight: the pen icon only shows on its own
            leftSection={current ? undefined : <FontAwesomeIcon icon={faPenToSquare} />}
            rightSection={<Kbd>e</Kbd>}
            onClick={() => onEdit(track)}
          >
            {current ? 'Editor' : 'Open editor'}
          </Button>
          {!current && <span className={c.spacer} />}
          <IconButton icon={faBan} label="Skip track" shortcut="b" onClick={() => onSkip(track)} />
          <IconButton
            icon={faArrowRight}
            label="Open album"
            shortcut="o"
            to={`/albums/${track.albumId}`}
          />
        </div>
        {/* A single line: skip/search keys live in the buttons' tooltips once candidates show */}
        <Hints
          items={showCandidates ? [
            [results.length > 1 ? ['1', String(Math.min(9, results.length))] : ['1'], 'pick', '–'],
            [['⏎'], 'use'],
            [['j', 'k'], 'move', '/'],
            [['o'], 'album'],
          ] : [
            [['s'], 'search'],
            [['j', 'k'], 'move', '/'],
            [['b'], 'skip'],
            [['o'], 'album'],
          ]}
        />
        {results.length > 9 && <span className={c.inspectorNote}>{num(results.length - 9)} more below without a number key.</span>}
      </div>
    </Panel>
  );
});
