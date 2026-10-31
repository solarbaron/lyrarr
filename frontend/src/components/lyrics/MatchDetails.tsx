import { Tooltip } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons';
import type { LyricsResult } from '../../types';
import { Meter } from '../ui';
import type { Tone } from '../ui';
import c from './LyricsCandidateList.module.css';

/** Score tone (DESIGN.md 5.4): success ≥ 80, warning 50–79, danger < 50. */
export function scoreTone(score: number): Tone {
  if (score >= 80) return 'success';
  if (score >= 50) return 'warning';
  return 'danger';
}

/** Overall match score of a result as an integer percentage. */
export function resultScore(r: LyricsResult): number {
  return Math.round((r.score || 0) * 100);
}

function ScoreBar({ label, score, detail }: { label: string; score: number; detail?: string }) {
  const value = Math.max(0, Math.min(100, Math.round(score)));
  return (
    <div className={c.detailRow}>
      <span className={c.detailLabel}>{label}</span>
      <Meter
        segments={[{ value, tone: scoreTone(value), label: `${label} match` }]}
        total={100}
        size="xs"
        width="100%"
        withTooltip={false}
        aria-label={`${label} match`}
        className={c.detailMeter}
      />
      <span className={c.detailValue}>{value}%</span>
      {detail ? (
        <Tooltip label={detail}>
          <span className={c.detailInfo} tabIndex={0} aria-label={`${label}: ${detail}`}>
            <FontAwesomeIcon icon={faCircleInfo} />
          </span>
        </Tooltip>
      ) : <span className={c.detailInfo} aria-hidden="true" />}
    </div>
  );
}

/** Title / Artist / Duration match bars with the query → result tooltips. */
export function MatchDetails({ result }: { result: LyricsResult }) {
  const md = result.match_details || {};
  if (md.title_score === undefined && md.artist_score === undefined && md.duration_score === undefined) {
    return <div className={c.detailEmpty}>No match details from this provider.</div>;
  }
  return (
    <div className={c.details}>
      {md.title_score !== undefined && (
        <ScoreBar label="Title" score={md.title_score} detail={`"${md.title_query || ''}" → "${md.title_result || ''}"`} />
      )}
      {md.artist_score !== undefined && (
        <ScoreBar label="Artist" score={md.artist_score} detail={`"${md.artist_query || ''}" → "${md.artist_result || ''}"`} />
      )}
      {md.duration_score !== undefined && (
        <ScoreBar
          label="Duration"
          score={md.duration_score}
          detail={md.duration_diff != null ? `${md.duration_diff.toFixed(1)}s difference` : 'No duration data'}
        />
      )}
    </div>
  );
}
