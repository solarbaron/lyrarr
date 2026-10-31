import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Menu, Switch } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faArrowRight, faCircleCheck, faCircleExclamation, faClockRotateLeft, faEllipsis, faFolderOpen, faListCheck,
  faPlay, faRotateRight, faSliders, faStop, faTriangleExclamation, faWandMagicSparkles,
} from '@fortawesome/free-solid-svg-icons';
import {
  applyAuditRun, cancelAuditRun, getAuditChecks, getAuditIssues, getAuditRun, getAuditRuns, getAuditSummary,
  startAuditRun,
} from '../../api';
import { errorMessage, errorStatus, notifyError } from '../../notify';
import { refreshQueries } from '../../queryRefresh';
import { absTime, num, plural, relTime } from '../../format';
import { useJobs } from '../../jobs';
import { auditFraction, auditStepText, type AuditProgress } from '../../auditProgress';
import { useUrlPatch, useUrlState } from '../../hooks/useUrlState';
import { useDelayedFlag } from '../../hooks/useDelayedFlag';
import {
  Banner, CardSkeleton, EmptyState, FilterPill, IconButton, Meter, Panel, StatusChip, ViewTabs, useConfirm,
} from '../../components/ui';
import type { AuditCategory, AuditCheck, AuditRun, StartAuditRunBody } from '../../types';
import {
  CATEGORIES, CLEARED_VIEW_LINK, EVIDENCE_CHECKS, ISSUE_STATUS_OPTIONS, MODE_LABELS, SEVERITIES, SEVERITY_LABELS,
  TRIGGER_LABELS, UNREACHABLE_CHECK, checkRows, openBySeverity, severityRank, severitySegments,
} from './audit/model';
import { CheckList } from './audit/CheckList';
import { RunHistory } from './audit/RunHistory';
import { RunOptionsModal } from './audit/RunOptionsModal';
import c from './audit/Audit.module.css';

/** Navigation state that asks the tab to start a dry run (Home "Check library…", command palette). */
export interface AuditNavState { startAudit?: boolean }

/** "Apply · 2 min ago · 3 fixes applied · 251 failed · 12 issues open" (failed in the warning tone). */
function RunLine({ run, open, fixable }: { run: AuditRun; open: number; fixable: number }) {
  const when = run.finished_at || run.started_at;
  const head = [
    `${MODE_LABELS[run.mode] ?? run.mode}${run.trigger !== 'user' ? ` (${(TRIGGER_LABELS[run.trigger] ?? run.trigger).toLowerCase()})` : ''}`,
    when ? relTime(when) : null,
  ].filter(Boolean).join(' · ');
  if (run.mode !== 'apply') {
    return <>{head} · {plural(open, 'issue')}{fixable ? ` (${num(fixable)} with a safe fix)` : ''}</>;
  }
  const failed = run.totals?.failed ?? 0;
  const pending = run.totals?.pending ?? 0;
  return (
    <>
      {head} · {plural(run.totals?.fixed ?? 0, 'fix', 'fixes')} applied
      {failed > 0 && <> · <span className={c.warnText}>{num(failed)} failed</span></>}
      {pending > 0 && ` · ${plural(pending, 're-search', 're-searches')} pending`}
      {open > 0 && ` · ${plural(open, 'issue')} open`}
    </>
  );
}

/** The running state: current check, "check i of n", a per-check meter, Cancel. */
function RunningPanel({ progress, fallback, checks, onCancel, cancelling }: {
  progress: AuditProgress | null;
  fallback: { run_id: number; mode: string; check_id?: string | null; current?: number | null; total?: number | null } | null;
  checks: AuditCheck[];
  onCancel(): void;
  cancelling: boolean;
}) {
  // Live progress from the event stream; else the polled /audit/checks snapshot (e.g. while the stream reconnects).
  const p: AuditProgress | null = progress ?? (fallback ? {
    runId: fallback.run_id,
    mode: fallback.mode,
    checkId: fallback.check_id ?? undefined,
    checkTitle: checks.find(ch => ch.id === fallback.check_id)?.title ?? fallback.check_id ?? undefined,
    checkIndex: Math.max(0, checks.findIndex(ch => ch.id === fallback.check_id) + 1),
    checks: 0,
    current: fallback.current ?? 0,
    total: fallback.total ?? 0,
  } : null);
  if (!p) return null;
  const overall = auditFraction(p);
  return (
    <div className={c.running} aria-live="polite">
      <div className={c.runningHead}>
        <StatusChip domain="job" status="running" />
        <span className={c.runningTitle}>{p.mode === 'apply' ? 'Applying fixes' : 'Checking library'}</span>
        {overall !== null && <span className={c.runningPct}>{Math.round(overall * 100)}%</span>}
        <span className={c.spacer} />
        <IconButton icon={faStop} label="Cancel the check" variant="default" loading={cancelling} onClick={onCancel} />
      </div>
      <div className={c.runningCheck}>
        <span className={c.runningCheckTitle} title={p.checkTitle}>{p.checkTitle || 'Starting…'}</span>
        {p.checks > 0 && <span className={c.runningStep}>{auditStepText(p)}</span>}
      </div>
      <Meter
        segments={[{ value: p.current, tone: 'accent', label: 'Checked' }]}
        total={p.total}
        size="md"
        width="100%"
        showCount={p.total > 0}
        withTooltip={false}
        aria-label={p.checkTitle ? `Progress of ${p.checkTitle}` : 'Progress of the current check'}
      />
    </div>
  );
}

/**
 * System › Audit (PLAN §4, §5.2): run a library check, apply safe fixes, review
 * the rest per check, and see past runs. Detects and repairs state left by older
 * versions; nothing changes until a fix is applied.
 */
export function AuditTab() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const location = useLocation();
  const navigate = useNavigate();
  const patchUrl = useUrlPatch();
  const { audit: liveAudit, running: jobs } = useJobs();
  const liveStartedAt = jobs.find(j => j.type === 'audit')?.startedAt ?? null;
  const [runParam] = useUrlState('run', 0);
  const [cat] = useUrlState<string>('cat', 'all');
  const [sev] = useUrlState<string>('sev', '');
  const [ist] = useUrlState<string>('ist', 'open');
  const [expanded] = useUrlState<string>('check', '');
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [showQuiet, setShowQuiet] = useState(false);

  const checksQ = useQuery({
    queryKey: ['audit', 'checks'],
    queryFn: getAuditChecks,
    // Also poll while a live run has no server snapshot from after its start yet.
    refetchInterval: q => (q.state.data?.running || (liveStartedAt !== null && q.state.dataUpdatedAt <= liveStartedAt) ? 5000 : false),
  });
  const runsQ = useQuery({ queryKey: ['audit', 'runs'], queryFn: () => getAuditRuns(10) });
  const summaryQ = useQuery({ queryKey: ['audit', 'summary'], queryFn: getAuditSummary, retry: false });

  const catalog = useMemo(() => checksQ.data?.checks ?? [], [checksQ.data]);
  const runs = useMemo(() => runsQ.data?.data ?? [], [runsQ.data]);
  const running = checksQ.data?.running ?? null;
  // The server is authoritative once /audit/checks answered after the live job started: if it
  // reports that run as no longer running, the job's end was missed (e.g. the event stream was
  // down when it finished), so the tab must not stay stuck in the running state.
  const liveStale = !!liveAudit && liveStartedAt !== null && checksQ.isSuccess && checksQ.dataUpdatedAt > liveStartedAt
    && (!running || (liveAudit.runId !== null && running.run_id !== liveAudit.runId));
  // Progress events carry no mode: prefer the server's for the same run (after a refresh mid-run).
  const liveProgress: AuditProgress | null = liveAudit && !liveStale ? {
    ...liveAudit,
    mode: running?.mode && running.run_id === liveAudit.runId ? running.mode : liveAudit.mode,
  } : null;
  const isRunning = !!running || !!liveProgress;
  const latest = runs[0] ?? null;
  // The report: the chosen run, else the newest COMPLETE top-level run (a dry run, or a fresh
  // "check and apply" run — its issues are its own), else the latest finished run. Child apply
  // runs keep their issues in the parent run, and a cancelled run only checked part of the library.
  const autoRun = runs.find(r => r.status === 'done' && !r.parent_run_id)
    ?? runs.find(r => r.status !== 'running' && !r.parent_run_id)
    ?? runs.find(r => r.status !== 'running') ?? null;
  const shownId = runParam || autoRun?.id || null;
  const listed = runs.find(r => r.id === shownId);
  const runQ = useQuery({
    queryKey: ['audit', 'run', shownId],
    queryFn: () => getAuditRun(shownId!),
    enabled: !!shownId && !listed,
  });
  const shown: AuditRun | null = listed ?? runQ.data ?? null;

  // F-0: one danger banner per unreachable library root.
  const unreachableQ = useQuery({
    queryKey: ['audit', 'issues', shown?.id, UNREACHABLE_CHECK, 'open'],
    queryFn: () => getAuditIssues(shown!.id, { check: UNREACHABLE_CHECK, status: 'open', pageSize: 50 }),
    enabled: !!shown,
  });

  const rows = useMemo(() => checkRows(catalog, shown), [catalog, shown]);
  const bySeverity = useMemo(() => openBySeverity(rows, shown), [rows, shown]);
  const meter = severitySegments(bySeverity);
  const openTotal = rows.reduce((s, r) => s + r.open, 0);
  const summary = summaryQ.data;
  // The server's latest complete top-level run (dry run or fresh apply run): its open auto issues
  // are live, so the count stays right after applying some of them.
  const latestDry = summary?.latest ?? null;
  const fixable = latestDry?.fixable_auto ?? 0;
  // A cancelled or failed run checked only part of the library: never call it clean.
  const partial = !!shown && (shown.status === 'cancelled' || shown.status === 'failed');
  const ranChecks = shown?.checks?.length ?? 0;
  const plannedChecks = shown?.options?.checks?.length || catalog.length;

  /* ---------- mutations ---------- */
  const refresh = () => refreshQueries(queryClient, ['audit']);

  // A missed end: reload the runs and the report once.
  useEffect(() => {
    if (liveStale) refreshQueries(queryClient, ['audit']);
  }, [liveStale, queryClient]);

  const start = useMutation({
    mutationFn: (body: StartAuditRunBody) => startAuditRun(body),
    onSuccess: (r, body) => {
      notifications.show({
        title: body.mode === 'apply' ? 'Applying safe fixes' : 'Library check started',
        message: r?.message || 'Progress appears here and in Activity.',
      });
      setOptionsOpen(false);
      patchUrl({ run: [0, 0], check: ['', ''], ipage: [1, 1] });
      refresh();
    },
    onError: err => notifyError('Check not started', err),
  });

  const applySafe = useMutation({
    // Every check id: the server applies only the open *auto* issues of that run (review items need explicit ids).
    mutationFn: (runId: number) => applyAuditRun(runId, { checks: catalog.map(ch => ch.id) }),
    onSuccess: r => {
      notifications.show({ title: 'Applying safe fixes', message: r?.message || 'Progress appears here and in Activity.' });
      refresh();
    },
    onError: err => notifyError('Fixes not started', err),
  });

  const cancel = useMutation({
    mutationFn: (runId: number) => cancelAuditRun(runId),
    onSuccess: () => {
      notifications.show({ title: 'Cancelling', message: 'The check stops after the current item. What it found so far is kept.' });
      refresh();
    },
    onError: err => notifyError('Not cancelled', err),
  });

  const askApplySafe = async () => {
    if (!latestDry) return;
    const ok = await confirm({
      title: `Apply ${plural(fixable, 'safe fix', 'safe fixes')}?`,
      message: 'Safe fixes correct Lyrarr’s records, adopt lyrics files that are already on disk, and queue tracks to be searched again. A file is only rewritten or removed after it is archived to version history. Items that need your judgement are not touched.',
      confirmLabel: 'Apply safe fixes',
    });
    if (ok) applySafe.mutate(latestDry.run_id);
  };

  // Home "Check library…" and the command palette navigate here with { startAudit: true }.
  const handledNav = useRef<string | null>(null);
  useEffect(() => {
    const state = location.state as AuditNavState | null;
    if (!state?.startAudit || handledNav.current === location.key || !checksQ.isSuccess) return;
    handledNav.current = location.key;
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
    if (!checksQ.data?.running) start.mutate({ mode: 'dry_run' });
    else notifications.show({ title: 'A check is already running', message: 'Its progress is shown below.' });
  }, [location.key, location.state, checksQ.isSuccess]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------- filters ---------- */
  const category = (CATEGORIES.some(x => x.value === cat) ? cat : 'all') as AuditCategory | 'all';
  const sevFilter = SEVERITIES.includes(sev as never) ? sev : null;
  const byCategory = (r: (typeof rows)[number]) => category === 'all' || r.spec.category === category;
  const bySev = (r: (typeof rows)[number]) => !sevFilter || r.spec.severity === sevFilter;
  const visible = rows
    .filter(r => byCategory(r) && bySev(r))
    .sort((a, b) => (Number(b.open + b.pending > 0) - Number(a.open + a.pending > 0)) || (Number(b.found > 0) - Number(a.found > 0))
      || (severityRank(a.spec.severity) - severityRank(b.spec.severity)) || (a.index - b.index));
  // Open view: checks with something left to do; other statuses: checks that found anything.
  const hasWork = (r: (typeof rows)[number]) => (ist === 'open' ? r.open > 0 || r.pending > 0 : r.found > 0);
  const withIssues = visible.filter(r => hasWork(r) || r.spec.id === expanded);
  const quiet = visible.length - withIssues.length;
  const listRows = showQuiet || !shown ? visible : withIssues;

  const tabs = [
    { value: 'all', label: 'All', count: rows.filter(bySev).reduce((s, r) => s + r.open, 0) },
    ...CATEGORIES.map(x => ({
      value: x.value, label: x.label,
      count: rows.filter(r => r.spec.category === x.value && bySev(r)).reduce((s, r) => s + r.open, 0),
    })),
  ];

  const evidenceOpen = rows.filter(r => EVIDENCE_CHECKS.has(r.spec.id)).reduce((s, r) => s + r.open, 0);
  const unreachable = unreachableQ.data?.data ?? [];
  const unreachableFallback = !unreachable.length ? (summary?.unreachable_roots ?? []) : [];
  const bootstrap = summary?.bootstrap ?? null;
  // Fixes that failed in the latest apply run: their issues sit in the parent dry run, status 'failed'.
  const latestFailed = latest && latest.mode === 'apply' && latest.status !== 'running' ? (latest.totals?.failed ?? 0) : 0;
  const failedRunId = latestFailed > 0 && latest ? (latest.parent_run_id ?? latest.id) : null;
  // Open count for the "Last check" line: live counts when that run (or an apply run's parent) is shown.
  const latestOpen = !latest ? 0
    : latest.id === shown?.id || (latest.mode === 'apply' && latest.parent_run_id === shown?.id && !!shown) ? openTotal
      : latest.mode === 'apply' ? (latest.totals?.open ?? 0)
        : (latest.totals?.open ?? latest.totals?.issues ?? 0);
  const showBootstrap = !!bootstrap && (bootstrap.review > 0 || (summary?.instrumental_cleared ?? 0) > 0 || latest?.trigger === 'bootstrap');

  const skeleton = useDelayedFlag(checksQ.isLoading || runsQ.isLoading);

  /* ---------- unavailable / loading ---------- */
  if (checksQ.isError) {
    const missing = errorStatus(checksQ.error) === 404;
    return (
      <Panel>
        <EmptyState
          size="sm"
          tone={missing ? 'neutral' : 'danger'}
          icon={faCircleExclamation}
          title={missing ? 'Library checks aren’t available on this server' : 'Couldn’t load the library check'}
          message={missing ? 'Update Lyrarr to use the library audit.' : errorMessage(checksQ.error)}
          action={!missing && <Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} onClick={() => checksQ.refetch()}>Retry</Button>}
        />
      </Panel>
    );
  }
  if (checksQ.isLoading || runsQ.isLoading) {
    return skeleton ? <div className={c.stack}><CardSkeleton height={180} /><CardSkeleton height={360} /></div> : null;
  }

  const runButton = (
    <Button
      variant="filled"
      leftSection={<FontAwesomeIcon icon={faPlay} />}
      loading={start.isPending}
      disabled={isRunning}
      onClick={() => start.mutate({ mode: 'dry_run' })}
    >
      Run check
    </Button>
  );

  const headerActions = (
    <div className={c.headerActions}>
      <Button
        variant="default"
        leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />}
        disabled={isRunning || !latestDry || fixable === 0}
        loading={applySafe.isPending}
        onClick={() => void askApplySafe()}
      >
        Apply safe fixes{fixable ? ` (${num(fixable)})` : ''}
      </Button>
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <IconButton icon={faEllipsis} label="More check options" variant="default" size="lg" />
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item leftSection={<FontAwesomeIcon icon={faSliders} />} disabled={isRunning} onClick={() => setOptionsOpen(true)}>
            Run with options…
          </Menu.Item>
          {latest?.status === 'cancelled' && (
            <Menu.Item
              leftSection={<FontAwesomeIcon icon={faRotateRight} />}
              disabled={isRunning}
              onClick={() => start.mutate({ mode: latest.mode, resume_run_id: latest.id })}
            >
              Resume the cancelled run
            </Menu.Item>
          )}
          <Menu.Item component={Link} to="/settings#audit" leftSection={<FontAwesomeIcon icon={faClockRotateLeft} />}>
            Schedule…
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      {runButton}
    </div>
  );

  const progressFallback = running ? { ...running, mode: running.mode ?? (running.phase === 'apply' ? 'apply' : 'dry_run') } : null;

  return (
    <div className={c.stack}>
      <Panel
        title="Last check"
        subtitle={!isRunning && latest ? (
          <span title={absTime(latest.finished_at || latest.started_at)}>
            <RunLine run={latest} open={latestOpen} fixable={latest.id === latestDry?.run_id ? fixable : 0} />
          </span>
        ) : undefined}
        actions={headerActions}
      >
        {isRunning ? (
          <RunningPanel
            progress={liveProgress}
            fallback={progressFallback}
            checks={catalog}
            onCancel={() => { const id = liveProgress?.runId ?? running?.run_id; if (id) cancel.mutate(id); }}
            cancelling={cancel.isPending}
          />
        ) : !latest ? (
          <EmptyState
            size="sm"
            icon={faListCheck}
            title="No library check yet"
            message="A check looks for lyrics files and records that don't agree, instrumental flags without evidence, and leftovers from older versions. A dry run changes nothing."
          />
        ) : (
          <div className={c.summary}>
            <div className={c.summaryHead}>
              <StatusChip domain="job" status={latest.status} />
              {latest.error && <span className={c.errorText}>{latest.error}</span>}
            </div>
            {shown && (
              <div className={c.severity}>
                <Meter
                  segments={meter.segments}
                  total={meter.total}
                  size="md"
                  width="100%"
                  aria-label="Open issues by severity"
                />
                <ul className={c.legend} aria-label="Open issues by severity">
                  {meter.segments.map(s => (
                    <li key={s.label} className={c.legendItem} data-zero={s.value === 0 || undefined}>
                      <span className={c.swatch} data-tone={s.tone} aria-hidden="true" />
                      {s.label}
                      <span className={c.legendCount}>{num(s.value)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Panel>

      {unreachable.map(issue => {
        const d = issue.details ?? {};
        const root = issue.path || issue.entity_key.replace(/^root:/, '');
        const skipped = typeof d.tracks === 'number' ? d.tracks : typeof d.skipped === 'number' ? d.skipped : null;
        return (
          <Banner
            key={issue.id}
            tone="danger"
            icon={faFolderOpen}
            title={`Music folder not reachable: ${root}`}
            message={`${skipped !== null ? `${plural(skipped, 'track')} under it were` : 'Tracks under it were'} skipped by file checks. Other folders were checked normally. Check that it is mounted, then run the check again.`}
          />
        );
      })}
      {unreachableFallback.map(root => (
        <Banner
          key={root}
          tone="danger"
          icon={faFolderOpen}
          title={`Music folder not reachable: ${root}`}
          message="Tracks under it were skipped. Other folders were checked normally."
        />
      ))}

      {failedRunId !== null && (
        <Banner
          tone="warning"
          icon={faTriangleExclamation}
          title={`${plural(latestFailed, 'fix', 'fixes')} could not be applied.`}
          message="Each item shows why it failed. Run the check again to retry them."
          action={failedRunId === shown?.id && ist === 'failed' ? undefined : (
            <button
              type="button"
              className={`ly-link ${c.linkButton}`}
              onClick={() => patchUrl({
                run: [failedRunId === autoRun?.id ? 0 : failedRunId, 0],
                ist: ['failed', 'open'], cat: ['all', 'all'], sev: ['', ''], check: ['', ''], ipage: [1, 1],
              })}
            >
              Show failed
            </button>
          )}
        />
      )}

      {showBootstrap && bootstrap && (
        <Banner
          tone="info"
          title={`After the upgrade Lyrarr fixed ${plural(bootstrap.changed, 'item')} automatically.`}
          message={bootstrap.review > 0 || (summary?.instrumental_cleared ?? 0) > 0
            ? `Review ${plural(bootstrap.review + (summary?.instrumental_cleared ?? 0), 'remaining item')}${(summary?.instrumental_cleared ?? 0) > 0 ? `, including ${plural(summary!.instrumental_cleared, 'instrumental flag')} from older versions that were cleared` : ''}.`
            : 'Nothing is waiting for review.'}
          action={(summary?.instrumental_cleared ?? 0) > 0 ? (
            <Link className={`ly-link ${c.bannerLink}`} to={CLEARED_VIEW_LINK}>
              Review cleared flags <FontAwesomeIcon icon={faArrowRight} />
            </Link>
          ) : bootstrap.run_id !== shown?.id ? (
            <button type="button" className={`ly-link ${c.linkButton}`} onClick={() => patchUrl({ run: [bootstrap.run_id, 0], check: ['', ''] })}>
              Show that run
            </button>
          ) : undefined}
        />
      )}

      {evidenceOpen > 0 && (
        <Banner
          tone="warning"
          icon={faClockRotateLeft}
          title="Some earlier lyrics can be restored."
          message="They are kept until you review them: lyrics you had chosen that were replaced later, and files removed by a batch delete."
          action={(
            <button type="button" className={`ly-link ${c.linkButton}`} onClick={() => patchUrl({ cat: ['history', 'all'], ist: ['open', 'open'] })}>
              Review
            </button>
          )}
        />
      )}

      {latest && (
        <Panel
          padding="none"
          title={shown && shown.id !== autoRun?.id ? `Report of ${relTime(shown.started_at)}` : 'Report'}
          subtitle={shown ? `${MODE_LABELS[shown.mode] ?? shown.mode} · ${TRIGGER_LABELS[shown.trigger] ?? shown.trigger}${partial ? ` · partial (${num(ranChecks)} of ${plural(plannedChecks, 'check')})` : ''}` : undefined}
          actions={runParam ? (
            <Button variant="subtle" size="xs" onClick={() => patchUrl({ run: [0, 0], check: ['', ''] })}>Back to the latest</Button>
          ) : undefined}
        >
          <div className={c.reportBar}>
            <ViewTabs
              aria-label="Check categories"
              tabs={tabs}
              value={category}
              onChange={v => patchUrl({ cat: [v, 'all'], check: ['', ''], ipage: [1, 1] })}
            />
            <div className={c.reportFilters}>
              <FilterPill
                label="Severity"
                value={sevFilter}
                options={SEVERITIES.map(s => ({ value: s, label: SEVERITY_LABELS[s], count: bySeverity[s] ?? 0 }))}
                onChange={v => patchUrl({ sev: [v ?? '', ''], ipage: [1, 1] })}
              />
              <FilterPill
                label="Status"
                anyLabel="Open"
                value={ist === 'open' ? null : ist}
                options={ISSUE_STATUS_OPTIONS}
                onChange={v => patchUrl({ ist: [v ?? 'open', 'open'], ipage: [1, 1] })}
              />
              {quiet > 0 && shown && (
                <Switch
                  size="sm"
                  label={ist === 'open' ? `Show ${plural(quiet, 'check')} with nothing open` : `Show ${plural(quiet, 'check')} with nothing found`}
                  checked={showQuiet}
                  onChange={e => setShowQuiet(e.currentTarget.checked)}
                />
              )}
            </div>
          </div>
          <CheckList
            label="Checks"
            rows={listRows}
            // A child apply run's issues live in its parent run (with their fixed/failed status).
            runId={shown ? (shown.mode === 'apply' && shown.parent_run_id ? shown.parent_run_id : shown.id) : null}
            expanded={expanded || null}
            onExpand={id => patchUrl({ check: [id ?? '', ''], ipage: [1, 1] })}
            status={ist}
            severity={sevFilter}
            runningCheck={liveProgress?.checkId ?? running?.check_id ?? null}
            busy={isRunning}
            loading={!!shownId && !shown && runQ.isLoading}
            empty={ist === 'open' && visible.every(r => r.open + r.pending === 0) ? (
              partial ? (
                <EmptyState
                  size="sm"
                  icon={faTriangleExclamation}
                  title={shown?.status === 'failed' ? 'This check did not finish' : 'This check was cancelled'}
                  message={`Only ${num(ranChecks)} of ${plural(plannedChecks, 'check')} ran, so this is not a full report. Run the check again to see every problem.`}
                />
              ) : (
                <EmptyState
                  size="sm"
                  tone="success"
                  icon={faCircleCheck}
                  title="No problems found"
                  message={category === 'all' && !sevFilter ? 'Every check came back clean.' : 'Nothing open with these filters.'}
                />
              )
            ) : (
              <EmptyState size="sm" icon={faListCheck} title="No checks match these filters" />
            )}
          />
        </Panel>
      )}

      {runs.length > 0 && (
        <RunHistory
          runs={runs}
          loading={runsQ.isFetching && !runsQ.data}
          shownId={shown?.id ?? null}
          onShow={r => patchUrl({ run: [r.id === autoRun?.id ? 0 : r.id, 0], check: ['', ''], ipage: [1, 1] })}
        />
      )}

      <RunOptionsModal
        opened={optionsOpen}
        onClose={() => setOptionsOpen(false)}
        checks={catalog}
        onStart={body => start.mutate(body)}
        starting={start.isPending}
      />
    </div>
  );
}
