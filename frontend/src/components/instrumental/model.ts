import type { InstrumentalPrev, InstrumentalVote, Track } from '../../types';
import { providerName } from '../lyrics/providers';
import type { StatusOptions } from '../ui/status';

/**
 * Instrumental policy vocabulary for the UI (PLAN §2.4). A track is in one of:
 *   flagged   — lyrics_status 'instrumental' (reason title / consensus / user, or legacy null)
 *   likely    — 'missing' with insufficient votes recorded (a hint, never terminal)
 *   conflict  — evidence carries a veto record (e.g. an instrumental title with saved lyrics)
 *   cleared   — an automatic clear removed an old flag (instrumental_prev), awaiting review
 */
export type InstrumentalState = 'flagged' | 'likely' | 'conflict' | null;

type TrackLike = Pick<Track, 'lyrics_status' | 'title'> & Partial<Pick<Track,
  'instrumental_reason' | 'instrumental_evidence' | 'instrumental_override' | 'instrumental_prev' | 'instrumental_checked_at'
>>;

export function evidenceOf(t: TrackLike): InstrumentalVote[] {
  return Array.isArray(t.instrumental_evidence) ? t.instrumental_evidence.filter(v => v && typeof v === 'object') : [];
}

export const isVeto = (v: InstrumentalVote) => !!v.veto;

export function votesOf(t: TrackLike): InstrumentalVote[] {
  return evidenceOf(t).filter(v => !isVeto(v));
}

export function vetoesOf(t: TrackLike): InstrumentalVote[] {
  return evidenceOf(t).filter(isVeto);
}

export function instrumentalState(t: TrackLike): InstrumentalState {
  if (t.lyrics_status === 'instrumental') return 'flagged';
  if (vetoesOf(t).length) return 'conflict';
  if (t.lyrics_status === 'missing' && votesOf(t).length) return 'likely';
  return null;
}

/** True when the API reports the policy fields at all (older servers don't). */
export function hasPolicyFields(t: object): boolean {
  return 'instrumental_reason' in t;
}

/** Reason key for the status tooltip; undefined when the server sends no policy fields. */
export function reasonKey(t: TrackLike): StatusOptions['reason'] {
  if (t.lyrics_status !== 'instrumental' || !hasPolicyFields(t)) return undefined;
  const r = t.instrumental_reason;
  return r === 'title' || r === 'consensus' || r === 'user' ? r : 'legacy';
}

/** Display name of a vote's source ("LRCLIB", "NetEase", "Title (weak)"). */
export function sourceName(v: InstrumentalVote): string {
  switch (v.source) {
    case 'title': return 'Title';
    case 'title_weak': return 'Title (weak)';
    case 'sidecar_marker': return 'Lyrics file marker';
    default: return providerName(v.provider || v.source || 'unknown');
  }
}

const KIND_LABELS: Record<string, string> = {
  flag: 'instrumental flag',
  pure_music: 'pure music',
  mxm_instrumental: 'instrumental',
  marker_content: '“Instrumental” page',
  title: 'title',
  sidecar_marker: 'file marker',
};

/** "LRCLIB instrumental flag", "NetEase pure music", "Title". */
export function voteLabel(v: InstrumentalVote): string {
  const source = sourceName(v);
  if (v.source === 'title' || v.source === 'title_weak' || v.source === 'sidecar_marker') return source;
  const kind = v.kind ? KIND_LABELS[v.kind] ?? v.kind.replace(/_/g, ' ') : '';
  return kind ? `${source} ${kind}` : source;
}

const VETO_LABELS: Record<string, string> = {
  sidecar: 'its lyrics file has real lyrics',
  embedded: 'the audio file has embedded lyrics',
  saved_lyrics: 'lyrics were saved for it',
  vocal_candidate: 'a provider had matching vocal lyrics',
  override: 'you marked it as having vocals',
};

export function vetoLabel(v: InstrumentalVote): string {
  return v.veto ? VETO_LABELS[v.veto] ?? v.veto.replace(/_/g, ' ') : '';
}

/** Distinct agreeing sources for the consensus tooltip. */
export function consensusSources(t: TrackLike): string[] {
  return Array.from(new Set(votesOf(t).map(sourceName)));
}

/** Options for StatusChip/StatusDot (tooltip by reason). */
export function statusOptions(t: TrackLike): Pick<StatusOptions, 'reason' | 'sources' | 'title'> | undefined {
  const reason = reasonKey(t);
  if (!reason) return undefined;
  return { reason, sources: reason === 'consensus' ? consensusSources(t) : undefined, title: t.title };
}

/** "LRCLIB instrumental flag (verified) · NetEase pure music". Anchored votes are marked. */
export function evidenceSummary(t: TrackLike): string {
  const votes = votesOf(t);
  if (!votes.length) {
    const vetoes = vetoesOf(t);
    return vetoes.length ? `Kept lyrics: ${vetoes.map(vetoLabel).join('; ')}` : '';
  }
  return votes.map(v => `${voteLabel(v)}${v.anchor ? ' (verified)' : ''}`).join(' · ');
}

export const REASON_LABELS: Record<string, string> = {
  title: 'Title',
  consensus: 'Sources agree',
  user: 'Marked by you',
  legacy: 'Older version',
};

export function reasonLabel(t: TrackLike): string {
  const r = reasonKey(t);
  return r ? REASON_LABELS[r] : '—';
}

const WHY_LABELS: Record<string, string> = {
  'legacy-unverified': 'Older version, not verified',
  'weak-title': 'Title no longer counts',
  'periodic-recheck': 'Periodic re-check',
  'embedded-lyrics': 'Embedded lyrics found',
  'user-recheck': 'Re-check',
  'user-not-instrumental': 'Marked as vocal',
  'user-unskip': 'Unskipped',
  'network-recheck': 'Re-checked online',
};

/** Why an automatic clear removed a flag (instrumental_prev.why). */
export function whyLabel(why: string | null | undefined): string {
  if (!why) return 'Unknown';
  return WHY_LABELS[why] ?? why.charAt(0).toUpperCase() + why.slice(1).replace(/[-_]/g, ' ');
}

export const CLEARED_WHY_OPTIONS = ['legacy-unverified', 'weak-title', 'periodic-recheck', 'embedded-lyrics', 'network-recheck']
  .map(value => ({ value, label: WHY_LABELS[value] }));

export function prevOf(t: TrackLike): InstrumentalPrev | null {
  const p = t.instrumental_prev;
  return p && typeof p === 'object' ? p : null;
}

/** Signed seconds, "+1.4 s" / "−3 s"; null → "—". */
export function formatDelta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const abs = Math.abs(seconds);
  const text = abs >= 10 || Number.isInteger(abs) ? String(Math.round(abs)) : abs.toFixed(1);
  return `${seconds < 0 ? '−' : seconds > 0 ? '+' : ''}${text} s`;
}

/** 0.82 → "82%"; 82 → "82"; null → "—". */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return '—';
  return score <= 1 ? `${Math.round(score * 100)}%` : String(Math.round(score));
}
