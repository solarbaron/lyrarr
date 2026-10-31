import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Modal, Loader, Button, Group, TextInput, Collapse, Kbd } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAlignLeft, faArrowDown, faChevronDown, faCircleExclamation, faMagnifyingGlass, faRotateRight, faTriangleExclamation } from '@fortawesome/free-solid-svg-icons';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { errorMessage, notifyError } from '../notify';
import { refreshQueries } from '../queryRefresh';
import { searchLyrics, downloadLyrics, blacklistLyrics } from '../api';
import type { LyricsResult } from '../types';
import api from '../api';
import { MOD_KEY } from '../shortcuts';
import { Banner, EmptyState, Meter, StatusChip, Tag } from './ui';
import LyricsCandidateList from './LyricsCandidateList';
import { parseLrcLines } from './lyrics/lrc';
import { providerName } from './lyrics/providers';
import { useHiddenBelow } from './lyrics/useHiddenBelow';
import { MatchDetails, resultScore, scoreTone } from './lyrics/MatchDetails';
import c from './lyrics/LyricsSearchModal.module.css';

interface Props {
  trackId: number;
  trackTitle: string;
  albumId: number;
  opened: boolean;
  onClose: () => void;
  /** Context line under the title ("Swans · To Be Kind"). */
  artistName?: string;
  albumTitle?: string;
  /** Called after a result was saved (before onClose), e.g. to move the Wanted cursor. */
  onApplied?: (result: LyricsResult) => void;
}

/** Full text of a candidate for the preview pane: synced lines with muted timestamps. */
function FullPreview({ result }: { result: LyricsResult }) {
  const text = result.synced_lyrics || result.plain_lyrics || result.synced_preview || result.plain_preview || '';
  const lines = useMemo(() => parseLrcLines(text), [text]);
  if (!text.trim()) return <div className={c.previewEmpty}>(no preview)</div>;
  return (
    <div className={c.fullPreview} tabIndex={0} aria-label={`Lyrics from ${providerName(result.provider)}`}>
      {lines.map(l => (
        <div key={l.key} className={c.fullLine}>
          {l.isTs && <span className={c.fullTs}>{l.tag}</span>}
          <span>{l.text || ' '}</span>
        </div>
      ))}
    </div>
  );
}

/** The "Find lyrics" picker (DESIGN.md 8.14). */
export default function LyricsSearchModal({ trackId, trackTitle, albumId, opened, onClose, artistName, albumTitle, onApplied }: Props) {
  const queryClient = useQueryClient();
  const fullScreen = useMediaQuery('(max-width: 767px)') ?? false;
  const twoPane = useMediaQuery('(min-width: 1024px)') ?? true;
  // `customQuery` is what's in the input box; `submittedQuery` is what we
  // actually searched for. Only submitting (Enter / Search) changes the query
  // key, so typing doesn't fire a provider search per keystroke.
  const [customQuery, setCustomQuery] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [highlighted, setHighlighted] = useState(0);
  // null = automatic (open only when there is nothing to pick from)
  const [refineToggled, setRefineToggled] = useState<boolean | null>(null);

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['lyrics-search', trackId, submittedQuery],
    queryFn: async () => {
      if (submittedQuery) {
        // Send a manual search query as track_name with no artist
        const resp = await api.get(`/metadata/lyrics/search/${trackId}`, {
          params: { custom_query: submittedQuery },
        });
        return resp.data as { results: LyricsResult[] };
      }
      return searchLyrics(trackId);
    },
    enabled: opened,
  });

  const submitSearch = () => setSubmittedQuery(customQuery.trim());
  const clearSearch = () => { setCustomQuery(''); setSubmittedQuery(''); };

  const results = useMemo(() => data?.results || [], [data]);
  useEffect(() => { setHighlighted(0); }, [results]);
  const current = results[Math.min(highlighted, Math.max(0, results.length - 1))] as LyricsResult | undefined;

  const downloadMutation = useMutation({
    mutationFn: (result: LyricsResult) => downloadLyrics(trackId, {
      synced_lyrics: result.synced_lyrics,
      plain_lyrics: result.plain_lyrics,
      provider: result.provider,
    }),
    onSuccess: (_data, result) => {
      notifications.show({
        title: 'Lyrics saved',
        message: `Saved ${result.synced_lyrics ? 'synced' : 'plain'} lyrics from ${providerName(result.provider)}`,
        color: 'success',
      });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
      queryClient.invalidateQueries({ queryKey: ['lyrics-read', trackId] });
      // Every Wanted list (by track, by album), its stats and the sidebar counts.
      refreshQueries(queryClient, ['wanted', 'nav']);
      onApplied?.(result);
      onClose();
    },
    onError: (err) => notifyError('Lyrics not saved', err),
  });

  const blacklistMutation = useMutation({
    mutationFn: (result: LyricsResult) => blacklistLyrics(trackId, {
      synced_lyrics: result.synced_lyrics,
      plain_lyrics: result.plain_lyrics,
      provider: result.provider,
      rescan: false,  // just record it; don't touch the saved file from here
    }),
    onSuccess: () => {
      notifications.show({ title: 'Result blocked', message: 'This result won\'t be auto-selected again', color: 'warning' });
      // Re-run the current search so the blacklisted result drops out.
      queryClient.invalidateQueries({ queryKey: ['lyrics-search', trackId, submittedQuery] });
    },
    onError: (err) => notifyError('Result not blocked', err),
  });

  // Once the first results arrive, focus moves from the header close button (Mantine's
  // initial focus) to the highlighted candidate, unless the user already moved it.
  const contentRef = useRef<HTMLDivElement>(null);
  const hasResults = results.length > 0;
  useEffect(() => {
    if (!opened || fullScreen || !hasResults) return;
    // After Mantine's focus trap has placed its initial focus (also a timeout).
    const timer = window.setTimeout(() => {
      const active = document.activeElement as HTMLElement | null;
      if (active && active !== document.body && !active.closest('.mantine-Modal-header')) return;
      contentRef.current?.querySelector<HTMLElement>('li[aria-current]')?.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [opened, fullScreen, hasResults]);

  // The list pane can end flush with a card edge: say how many cards sit below it.
  const listPane = useHiddenBelow(':scope > ol > li', [results, highlighted, refineToggled, submittedQuery]);

  const bestScore = results.length > 0 ? resultScore(results[0]) : 0;
  const hasLowConfidence = results.length > 0 && bestScore < 60;
  const refineOpen = refineToggled ?? (!isLoading && (results.length === 0 || !!submittedQuery));

  const apply = () => {
    if (current && !downloadMutation.isPending) downloadMutation.mutate(current);
  };

  // On the modal root, so the keys work from the moment it opens (Mantine focuses
  // the header close button first, which sits outside the body).
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      apply();
      return;
    }
    const t = e.target as HTMLElement;
    const typing = t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable;
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (/^[1-9]$/.test(e.key)) {
      const i = Number(e.key) - 1;
      if (i < results.length) {
        e.preventDefault();
        setHighlighted(i);
      }
    }
  };

  const context = [artistName, albumTitle].filter(Boolean).join(' · ');

  const list = (
    <LyricsCandidateList
      results={results}
      highlighted={highlighted}
      onHighlight={setHighlighted}
      onApply={r => downloadMutation.mutate(r)}
      onNeverSelect={r => blacklistMutation.mutate(r)}
      applying={downloadMutation.isPending}
      neverSelecting={blacklistMutation.isPending}
      showPreview={!twoPane}
      autoFocusHighlighted={!fullScreen}
    />
  );

  let body;
  if (isLoading) {
    body = (
      <div className={c.loading} role="status">
        <Loader />
        <span>Searching lyrics providers…</span>
      </div>
    );
  } else if (isError) {
    body = (
      <EmptyState
        size="sm"
        tone="danger"
        icon={faCircleExclamation}
        title="Couldn't search lyrics providers"
        message={errorMessage(error, 'The search request failed.')}
        action={<Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} loading={isFetching} onClick={() => refetch()}>Retry</Button>}
      />
    );
  } else if (results.length === 0) {
    body = (
      <EmptyState
        size="sm"
        icon={faAlignLeft}
        title="No lyrics found from any provider"
        message={submittedQuery
          ? 'Try a different spelling of the title or artist, or clear the custom query to go back to the automatic search.'
          : 'Try a custom search query above with a different title or artist spelling.'}
      />
    );
  } else if (twoPane && current) {
    const score = resultScore(current);
    body = (
      <div className={c.panes}>
        <div className={c.listPane} ref={listPane.ref}>
          {list}
          {listPane.hidden > 0 && (
            <div className={c.moreBelow}>
              {/* Pointer shortcut only: keyboard users move with the arrows / 1–9 and hear the list size. */}
              <button
                type="button"
                tabIndex={-1}
                aria-hidden="true"
                className={c.moreBelowPill}
                onClick={() => listPane.element?.scrollBy({ top: listPane.element.clientHeight * 0.8, behavior: 'smooth' })}
              >
                <FontAwesomeIcon icon={faArrowDown} />
                {listPane.hidden} more below
              </button>
            </div>
          )}
        </div>
        <section className={c.previewPane} aria-label="Preview of the highlighted result">
          <div className={c.previewHead}>
            <span className={c.previewProvider}>{providerName(current.provider)}</span>
            <StatusChip domain="trackLyrics" status="available" synced={!!current.synced_lyrics} size="sm" withTooltip={false} />
            {current.is_composite && <Tag>Composite</Tag>}
            <span className={c.spacer} />
            <Meter
              segments={[{ value: score, tone: scoreTone(score), label: 'Match' }]}
              total={100}
              size="xs"
              width={60}
              withTooltip={false}
              aria-label="Match score"
            />
            <span className={c.previewScore}>{score}%</span>
          </div>
          {(current.artist_name || current.track_name) && (
            <div className={c.previewMeta}>
              {current.artist_name ? `${current.artist_name} — ` : ''}{current.track_name || ''}
            </div>
          )}
          <MatchDetails result={current} />
          <FullPreview result={current} />
        </section>
      </div>
    );
  } else {
    body = list;
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      size={880}
      fullScreen={fullScreen}
      classNames={{ content: c.modalContent, body: c.body, title: c.modalTitle }}
      onKeyDown={onKeyDown}
      title={(
        <span className={c.titleWrap}>
          <span className={c.title}>Find lyrics — {trackTitle}</span>
          {context && <span className={c.context}>{context}</span>}
        </span>
      )}
    >
      <div className={c.layout} data-full-screen={fullScreen || undefined}>
        <div className={c.toolbar}>
          <span className={c.summary} aria-live="polite">
            {isLoading
              ? 'Searching…'
              : isError
                ? 'Search failed'
                : results.length > 0
                  ? <>{results.length} result{results.length !== 1 ? 's' : ''} · best {bestScore}%{submittedQuery ? <> · custom query “{submittedQuery}”</> : null}</>
                  : submittedQuery ? <>No results for “{submittedQuery}”</> : 'No results'}
          </span>
          <Button
            variant="subtle"
            color="gray"
            size="xs"
            aria-expanded={refineOpen}
            aria-controls="lyrics-refine"
            leftSection={<FontAwesomeIcon icon={faMagnifyingGlass} />}
            rightSection={<FontAwesomeIcon icon={faChevronDown} className={c.chevron} data-open={refineOpen || undefined} />}
            onClick={() => setRefineToggled(!refineOpen)}
          >
            Refine search
          </Button>
        </div>

        <Collapse in={refineOpen} id="lyrics-refine">
          <div className={c.refine}>
            <TextInput
              placeholder="e.g. Bohemian Rhapsody Queen"
              value={customQuery}
              onChange={(e) => setCustomQuery(e.currentTarget.value)}
              className={c.refineInput}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitSearch();
              }}
              aria-label="Custom lyrics search query"
              description="Overrides the automatic title and artist search"
            />
            <Group gap="xs" className={c.refineButtons}>
              <Button
                variant="default"
                onClick={submitSearch}
                loading={isFetching && !isLoading}
              >
                {customQuery.trim() || !submittedQuery ? 'Search' : 'Reset'}
              </Button>
              {(submittedQuery || customQuery) && (
                <Button variant="subtle" color="gray" onClick={clearSearch}>
                  Clear
                </Button>
              )}
            </Group>
          </div>
        </Collapse>

        {hasLowConfidence && (
          <div className={c.banner}>
            <Banner
              tone="warning"
              icon={faTriangleExclamation}
              title={`Low confidence match (best: ${bestScore}%)`}
              message="These lyrics may not match this track. Try a custom search query or check the preview before using one."
            />
          </div>
        )}

        <div className={c.content} ref={contentRef}>{body}</div>

        <div className={c.footer} data-full-screen={fullScreen || undefined}>
          {results.length > 0 && (
            <span className={c.hints} aria-hidden="true">
              <Kbd>1</Kbd>–<Kbd>{Math.min(9, results.length)}</Kbd> pick
              <span className={c.hintGap} />
              <Kbd>{MOD_KEY}</Kbd> <Kbd>Enter</Kbd> use
            </span>
          )}
          <Group justify="flex-end" gap="xs" className={c.footerButtons}>
            {/* No candidate (loading, error or no results): nothing to use, only a way out. */}
            <Button variant={current ? 'subtle' : 'default'} color={current ? 'gray' : undefined} onClick={onClose}>
              {current ? 'Cancel' : 'Close'}
            </Button>
            {current && (
              <Button
                variant="filled"
                onClick={apply}
                loading={downloadMutation.isPending}
              >
                Use {providerName(current.provider)}
              </Button>
            )}
          </Group>
        </div>
      </div>
    </Modal>
  );
}
