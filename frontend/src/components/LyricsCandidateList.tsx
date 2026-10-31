import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { Button, Collapse, Kbd } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBan, faCheck, faChevronDown } from '@fortawesome/free-solid-svg-icons';
import type { LyricsResult } from '../types';
import { duration as fmtDuration } from '../format';
import { IconButton, Meter, StatusChip, Tag } from './ui';
import { parseLrcLines } from './lyrics/lrc';
import { MatchDetails, resultScore, scoreTone } from './lyrics/MatchDetails';
import { providerName } from './lyrics/providers';
import c from './lyrics/LyricsCandidateList.module.css';

export interface LyricsCandidateListProps {
  results: LyricsResult[];
  /** Index of the highlighted candidate (keys 1–9, click, focus). */
  highlighted: number;
  onHighlight(i: number): void;
  onApply(r: LyricsResult): void;
  /** "Never auto-select this result" (blacklist with the result payload). */
  onNeverSelect(r: LyricsResult): void;
  /** A save is running: Use buttons are disabled, the highlighted one spins. */
  applying?: boolean;
  /** A blacklist is running: the highlighted card's ban button spins. */
  neverSelecting?: boolean;
  /** Narrow layout (Wanted inspector): tighter cards, preview lines only on the highlighted card. */
  compact?: boolean;
  /** Preview lines on the cards (default true). Off when a separate preview pane shows them. */
  showPreview?: boolean;
  /** Mark the highlighted card as a dialog's initial focus (Mantine's data-autofocus). */
  autoFocusHighlighted?: boolean;
  'aria-label'?: string;
  className?: string;
}

const PREVIEW_LINES = 4;

/** Signed seconds between the result and the track ("Δ 0s", or a warning "55s off" past 2s). */
function DurationDelta({ diff }: { diff: number | undefined }) {
  if (diff === undefined || diff === null || !Number.isFinite(diff)) return null;
  const abs = Math.abs(diff);
  if (abs <= 2) return <span className={c.delta}>Δ {Math.round(abs)}s</span>;
  return (
    <Tag tone="warning" tooltip={`Duration differs from the track by ${diff.toFixed(1)}s`}>
      {Math.round(abs)}s off
    </Tag>
  );
}

function PreviewLines({ result }: { result: LyricsResult }) {
  const text = result.synced_preview || result.plain_preview || '';
  if (!text.trim()) return <div className={c.previewEmpty}>(no preview)</div>;
  const lines = parseLrcLines(text).filter(l => l.text.trim() || l.isTs).slice(0, PREVIEW_LINES);
  return (
    <div className={c.preview} aria-label="Preview">
      {lines.map(l => (
        <div key={l.key} className={c.previewLine}>
          {l.isTs && <span className={c.previewTs}>{l.tag}</span>}
          <span className={c.previewText}>{l.text || ' '}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Provider results as cards (DESIGN.md 8.15), shared by LyricsSearchModal and the
 * Wanted inspector. A card is highlighted by click, focus or its number key; the
 * owner applies the highlighted one on Enter. Each card keeps its own secondary
 * "Use" button and a "Never auto-select this result" icon button.
 */
export default function LyricsCandidateList({
  results, highlighted, onHighlight, onApply, onNeverSelect, applying, neverSelecting, compact, showPreview = true,
  autoFocusHighlighted, className, 'aria-label': ariaLabel = 'Lyrics candidates',
}: LyricsCandidateListProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const refs = useRef<(HTMLLIElement | null)[]>([]);
  // Unique per list: the inspector and the search modal can be mounted together.
  const idBase = useId();

  // Number keys highlight cards that may be scrolled away: bring the card into view.
  // When a card has focus, focus follows the highlight so the two never split.
  useEffect(() => {
    const el = refs.current[highlighted];
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    const active = document.activeElement;
    if (el && active !== el && refs.current.some(card => card === active)) el.focus({ preventScroll: true });
  }, [highlighted]);

  const toggleDetails = (i: number) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    return next;
  });

  const onCardKey = (e: KeyboardEvent<HTMLLIElement>, i: number) => {
    if (e.target !== e.currentTarget) return; // buttons inside keep their keys
    let next = -1;
    if (e.key === 'ArrowDown') next = Math.min(results.length - 1, i + 1);
    else if (e.key === 'ArrowUp') next = Math.max(0, i - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = results.length - 1;
    if (next >= 0) {
      e.preventDefault();
      e.stopPropagation();
      onHighlight(next);
      refs.current[next]?.focus();
    }
  };

  return (
    <ol className={[c.list, className].filter(Boolean).join(' ')} aria-label={ariaLabel} data-compact={compact || undefined}>
      {results.map((r, i) => {
        const score = resultScore(r);
        const isHi = i === highlighted;
        const md = r.match_details || {};
        const open = expanded.has(i);
        const detailsId = `${idBase}-details-${i}`;
        const preview = showPreview && (!compact || isHi);
        return (
          <li
            key={`${r.provider}-${i}`}
            ref={el => { refs.current[i] = el; }}
            className={c.card}
            data-highlighted={isHi || undefined}
            aria-current={isHi || undefined}
            data-autofocus={(autoFocusHighlighted && isHi) || undefined}
            aria-label={`${providerName(r.provider)}, ${r.synced_lyrics ? 'synced' : 'plain'}, ${score}% match`}
            tabIndex={0}
            onClick={() => { if (!isHi) onHighlight(i); }}
            onFocus={e => { if (e.target === e.currentTarget && !isHi) onHighlight(i); }}
            onKeyDown={e => onCardKey(e, i)}
          >
            <div className={c.head}>
              {i < 9 ? <Kbd className={c.key} aria-hidden="true">{i + 1}</Kbd> : <span className={c.keySpacer} />}
              <div className={c.headMain}>
                <span className={c.provider}>{providerName(r.provider)}</span>
                <StatusChip domain="trackLyrics" status="available" synced={!!r.synced_lyrics} size="sm" withTooltip={false} />
                {(r.providers_agree ?? 0) > 1 && (
                  <Tag tooltip={r.agreement_providers?.length ? r.agreement_providers.map(providerName).join(', ') : undefined}>
                    <FontAwesomeIcon icon={faCheck} className={c.tagIcon} />
                    {r.providers_agree} providers agree
                  </Tag>
                )}
                {r.is_composite && <Tag tooltip="Combined from several providers' results">Composite</Tag>}
              </div>
              <span className={c.score} title={`${score}% match`}>
                <Meter
                  segments={[{ value: score, tone: scoreTone(score), label: 'Match' }]}
                  total={100}
                  size="xs"
                  width={compact ? 40 : 60}
                  withTooltip={false}
                  aria-label="Match score"
                />
                <span className={c.scoreValue}>{score}%</span>
              </span>
            </div>

            {(r.track_name || r.artist_name || r.duration != null || md.duration_diff != null) && (
              <div className={c.meta}>
                {(r.artist_name || r.track_name) && (
                  <span className={c.metaTitle} title={`${r.artist_name ? `${r.artist_name} — ` : ''}${r.track_name || ''}`}>
                    {r.artist_name ? `${r.artist_name} — ` : ''}{r.track_name || ''}
                  </span>
                )}
                {r.duration != null && <span className={c.metaDur}>{fmtDuration(r.duration)}</span>}
                <DurationDelta diff={md.duration_diff} />
              </div>
            )}

            {preview && <PreviewLines result={r} />}

            <div className={c.foot}>
              <Button
                variant="subtle"
                color="gray"
                size="compact-xs"
                className={c.detailsToggle}
                aria-expanded={open}
                aria-controls={detailsId}
                rightSection={<FontAwesomeIcon icon={faChevronDown} className={c.chevron} data-open={open || undefined} />}
                onClick={e => { e.stopPropagation(); toggleDetails(i); }}
              >
                Match details
              </Button>
              <span className={c.spacer} />
              <IconButton
                icon={faBan}
                label="Never auto-select this result"
                size="md"
                loading={isHi && neverSelecting}
                disabled={neverSelecting && !isHi}
                onClick={e => { e.stopPropagation(); onHighlight(i); onNeverSelect(r); }}
              />
              <Button
                variant="default"
                size="xs"
                loading={isHi && applying}
                disabled={applying && !isHi}
                onClick={e => { e.stopPropagation(); onHighlight(i); onApply(r); }}
              >
                Use
              </Button>
            </div>
            <Collapse
              in={open}
              id={detailsId}
              // Opened details on the last visible card scroll into view.
              onTransitionEnd={() => { if (open) document.getElementById(detailsId)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }}
            >
              <MatchDetails result={r} />
            </Collapse>
          </li>
        );
      })}
    </ol>
  );
}
