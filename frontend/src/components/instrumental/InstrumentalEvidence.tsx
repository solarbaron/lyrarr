import { absTime, relTime } from '../../format';
import { KeyValueList, Tag, type KeyValueItem } from '../ui';
import type { InstrumentalVote, Track } from '../../types';
import {
  evidenceOf, formatDelta, formatScore, instrumentalState, isVeto, prevOf, reasonKey, REASON_LABELS, vetoLabel,
  voteLabel, whyLabel,
} from './model';
import c from './Instrumental.module.css';

type EvidenceTrack = Pick<Track, 'lyrics_status' | 'title'> & Partial<Track>;

/** "Likely instrumental" / "Conflicting evidence" tag for a track row (PLAN §5.3). */
export function InstrumentalHintTag({ track }: { track: EvidenceTrack }) {
  const state = instrumentalState(track);
  if (state === 'likely') {
    return (
      <Tag tooltip="Some sources suggest there are no lyrics, but not enough to be sure. The track stays missing and is searched less often.">
        Likely instrumental
      </Tag>
    );
  }
  if (state === 'conflict') {
    return (
      <Tag tone="warning" tooltip="The title looks instrumental, but this track has lyrics. Review it in Wanted › Instrumental review.">
        Conflicting evidence
      </Tag>
    );
  }
  return null;
}

function VoteCard({ vote, columns }: { vote: InstrumentalVote; columns: 1 | 2 }) {
  if (isVeto(vote)) {
    return (
      <li className={c.vote} data-veto="">
        <div className={c.voteHead}>
          <span className={c.voteTitle}>Not instrumental: {vetoLabel(vote)}</span>
          <Tag tone="warning">Veto</Tag>
        </div>
        {vote.provider && <div className={c.voteNote}>Saved from {vote.provider}{vote.at ? ` · ${relTime(vote.at)}` : ''}</div>}
      </li>
    );
  }
  const items: KeyValueItem[] = [];
  if (vote.matched_title) items.push({ label: 'Matched title', value: vote.matched_title });
  if (vote.matched_album) items.push({ label: 'Matched album', value: vote.matched_album });
  if (vote.duration_diff !== null && vote.duration_diff !== undefined) items.push({ label: 'Duration difference', value: formatDelta(vote.duration_diff) });
  if (vote.score !== null && vote.score !== undefined) items.push({ label: 'Match score', value: formatScore(vote.score) });
  if (vote.title_score !== null && vote.title_score !== undefined) items.push({ label: 'Title match', value: formatScore(vote.title_score) });
  if (vote.strategy) items.push({ label: 'How it was found', value: vote.strategy.replace(/_/g, ' ') });
  if (vote.record_id !== null && vote.record_id !== undefined && vote.record_id !== '') items.push({ label: 'Record id', value: String(vote.record_id), mono: true });
  return (
    <li className={c.vote}>
      <div className={c.voteHead}>
        <span className={c.voteTitle}>{voteLabel(vote)}</span>
        {vote.anchor
          ? <Tag tone="success" tooltip="Duration-verified: the match has the same length as your file (and album, for generic titles).">Verified</Tag>
          : <Tag tooltip="Not duration-verified: it only counts together with a verified source.">Unverified</Tag>}
      </div>
      {items.length > 0 && <KeyValueList items={items} columns={columns} className={c.voteKv} />}
    </li>
  );
}

/**
 * "Why instrumental" (PLAN §2.4, §5.3): the reason, the user's decision, the votes
 * (source, provider, matched title/album, Δduration, score, anchor, record id) and
 * any vetoes. Used by Wanted › Instrumental review and the Wanted inspector.
 */
export function InstrumentalEvidence({ track, compact, columns = 1 }: { track: EvidenceTrack; compact?: boolean; columns?: 1 | 2 }) {
  const evidence = evidenceOf(track);
  const reason = reasonKey(track);
  const prev = prevOf(track);
  const summary: KeyValueItem[] = [];
  if (track.lyrics_status === 'instrumental') {
    summary.push({ label: 'Reason', value: reason ? REASON_LABELS[reason] : 'Not recorded' });
  }
  if (track.instrumental_override) {
    summary.push({
      label: 'Your decision',
      value: track.instrumental_override === 'never' ? 'Has vocals (never flag)' : 'Instrumental',
    });
  }
  if (track.instrumental_checked_at) {
    summary.push({ label: 'Last evaluated', value: <span title={absTime(track.instrumental_checked_at)}>{relTime(track.instrumental_checked_at)}</span> });
  }
  if (prev) {
    summary.push({ label: 'Earlier flag cleared', value: whyLabel(prev.why), source: prev.at ? relTime(prev.at) : undefined });
  }

  return (
    <div className={c.evidence} data-compact={compact || undefined}>
      {summary.length > 0 && <KeyValueList items={summary} columns={columns} />}
      {evidence.length > 0 ? (
        <ul className={c.votes} aria-label="Evidence">
          {evidence.map((v, i) => <VoteCard key={i} vote={v} columns={columns} />)}
        </ul>
      ) : (
        <p className={c.none}>
          {track.lyrics_status === 'instrumental' && reason === 'legacy'
            ? 'An older version flagged this track without recording why.'
            : 'No evidence recorded.'}
        </p>
      )}
    </div>
  );
}
