/**
 * Pure helpers for the library-audit job (PLAN §1.6, §5.1). No imports, so
 * scripts/check-events.mjs can load this file directly with Node's type stripping.
 *
 * Event contract:
 *   lyrics_audit_start    {run_id, mode, trigger, message, checks}      checks = number of checks
 *   lyrics_audit_progress {run_id, phase, check_id, check_title, check_index, checks, current, total, message}
 *                         current/total = items of the current check; check_index is 1-based ("check 4 of 23")
 *                         (no `mode`: phase 'apply' implies an apply run)
 */

export interface AuditProgress {
  runId: number | null;
  mode: string;
  phase?: string;
  checkId?: string;
  checkTitle?: string;
  /** 1-based position of the current check; 0 before the first progress event. */
  checkIndex: number;
  checks: number;
  /** Items of the current check. */
  current: number;
  total: number;
}

export interface AuditEventPayload {
  run_id?: number;
  mode?: string;
  phase?: string;
  checks?: number;
  check_id?: string;
  check_title?: string;
  check_index?: number;
  current?: number;
  total?: number;
}

const int = (v: unknown, fallback = 0): number => (typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.floor(v)) : fallback);

/** Job label for a run mode. */
export function auditJobLabel(mode: string | undefined): string {
  return mode === 'apply' ? 'Applying fixes' : 'Checking library';
}

/** Progress state right after `lyrics_audit_start` (it no longer carries a track `total`). */
export function auditStarted(p: AuditEventPayload | undefined): AuditProgress {
  return {
    runId: typeof p?.run_id === 'number' ? p.run_id : null,
    mode: p?.mode || 'dry_run',
    checkIndex: 0,
    checks: int(p?.checks),
    current: 0,
    total: 0,
  };
}

/**
 * Folds one `lyrics_audit_progress` into the state. A progress event for another
 * run (or with no prior start, e.g. after a page refresh mid-run) starts fresh.
 */
export function auditAdvanced(prev: AuditProgress | null, p: AuditEventPayload | undefined): AuditProgress {
  const runId = typeof p?.run_id === 'number' ? p.run_id : null;
  const base = prev && (runId === null || prev.runId === null || prev.runId === runId)
    ? prev
    : { ...auditStarted(p), runId };
  const checks = int(p?.checks, base.checks);
  const sameCheck = !!p?.check_id && p.check_id === base.checkId;
  return {
    ...base,
    runId: runId ?? base.runId,
    // Progress events carry no `mode` (PLAN §1.6); the apply phase only happens in apply runs.
    mode: p?.mode || (p?.phase === 'apply' ? 'apply' : base.mode),
    phase: p?.phase || base.phase,
    checkId: p?.check_id || base.checkId,
    checkTitle: p?.check_title || (sameCheck ? base.checkTitle : p?.check_id) || base.checkTitle,
    checkIndex: Math.min(Math.max(int(p?.check_index, base.checkIndex), 1), Math.max(checks, 1)),
    checks,
    current: int(p?.current),
    total: int(p?.total),
  };
}

/**
 * Whether a `lyrics_audit_complete` / `lyrics_audit_error` ends the running audit job.
 * Live events always do. A replayed one (missed while the event stream was down and
 * sent again on reconnect) does only when it is for the run the job shows, so a run
 * that finished during the gap does not stay "running" until a page reload.
 */
export function auditEventEndsJob(job: AuditProgress | null | undefined, p: AuditEventPayload | undefined, live: boolean): boolean {
  if (live) return true;
  return !!job && job.runId !== null && typeof p?.run_id === 'number' && p.run_id === job.runId;
}

/** Whole-run progress 0–1 (finished checks plus the share of the current one), or null when unknown. */
export function auditFraction(a: AuditProgress): number | null {
  if (a.checks <= 0) return null;
  const done = Math.max(0, a.checkIndex - 1);
  const within = a.total > 0 ? Math.min(1, a.current / a.total) : 0;
  return Math.min(1, (done + (a.checkIndex > 0 ? within : 0)) / a.checks);
}

/** "Check 4 of 23" (or "Starting…" before the first check reports). */
export function auditStepText(a: AuditProgress): string {
  if (a.checkIndex <= 0 || a.checks <= 0) return 'Starting…';
  return `Check ${a.checkIndex} of ${a.checks}`;
}

/** Job parts for the shell: the checks, then the items of the current check. */
export function auditParts(a: AuditProgress): { label: string; unit: string; done: number; total: number }[] {
  const parts = [{ label: 'Checks', unit: 'checks', done: Math.max(0, a.checkIndex - 1), total: a.checks }];
  if (a.checkIndex > 0 && a.total > 0) {
    parts.push({ label: a.checkTitle || 'Current check', unit: 'items', done: Math.min(a.current, a.total), total: a.total });
  }
  return parts;
}
