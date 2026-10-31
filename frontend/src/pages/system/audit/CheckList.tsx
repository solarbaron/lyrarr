import { Loader } from '@mantine/core';
import { faChevronRight } from '@fortawesome/free-solid-svg-icons';
import { num } from '../../../format';
import { DataTable, IconButton, StatusDot, Tag, type Column } from '../../../components/ui';
import { COST_HINTS, COST_LABELS, FIX_HINTS, FIX_LABELS, type CheckRow } from './model';
import { IssueTable } from './IssueTable';
import c from './Audit.module.css';

export interface CheckListProps {
  rows: CheckRow[];
  runId: number | null;
  expanded: string | null;
  onExpand(id: string | null): void;
  status: string;
  severity: string | null;
  /** Check id the running audit is on. */
  runningCheck?: string | null;
  busy: boolean;
  loading?: boolean;
  empty?: React.ReactNode;
  label: string;
}

function Counts({ row, status, running }: { row: CheckRow; status: string; running: boolean }) {
  if (running) {
    return <span className={c.counts}><Loader size={12} aria-hidden="true" /> Checking…</span>;
  }
  const e = row.entry;
  if (!e) return <span className={c.countsMuted}>Not in this run</span>;
  if (e.status === 'skipped' || e.status === 'aborted' || e.status === 'cancelled') {
    const label = e.status === 'skipped' ? 'Skipped' : e.status === 'aborted' ? 'Stopped early' : 'Cancelled';
    return <span className={c.countsMuted} title={e.note ?? undefined}>{label}{e.note ? `: ${e.note}` : ''}</span>;
  }
  if (row.found === 0) return <span className={c.countsMuted}>Nothing found</span>;
  const parts: string[] = [];
  if (status === 'open' || !status) {
    parts.push(`${num(row.open)} open`);
    if (row.pending) parts.push(`${num(row.pending)} pending`);
    if (row.fixed) parts.push(`${num(row.fixed)} fixed`);
  } else {
    parts.push(`${num(row.found)} found`);
  }
  return (
    <span className={c.counts} title={e.note ?? undefined}>
      {parts.join(' · ')}
      {e.status === 'detect_only' && <Tag tooltip="Found during the upgrade check and kept for your review.">Kept for review</Tag>}
    </span>
  );
}

/**
 * One row per check (PLAN §5.2): severity, title + description, cost and fix-mode
 * tags, counts. Expanding a row (click, Enter or →) shows its issues.
 */
export function CheckList({
  rows, runId, expanded, onExpand, status, severity, runningCheck, busy, loading, empty, label,
}: CheckListProps) {
  const columns: Column<CheckRow>[] = [
    {
      key: 'severity', header: 'Severity', width: 132, priority: 1, mobile: 'status',
      cell: r => (
        <span className={c.sevCell}>
          <IconButton
            icon={faChevronRight}
            label={expanded === r.spec.id ? `Hide issues of ${r.spec.title}` : `Show issues of ${r.spec.title}`}
            size="sm"
            tabIndex={-1}
            className={c.chevron}
            data-open={expanded === r.spec.id || undefined}
            aria-expanded={expanded === r.spec.id}
            disabled={!runId}
            onClick={() => onExpand(expanded === r.spec.id ? null : r.spec.id)}
          />
          <StatusDot domain="auditSeverity" status={r.spec.severity} />
        </span>
      ),
    },
    {
      key: 'check', header: 'Check', mobile: 'title',
      cell: r => {
        const cost = COST_LABELS[r.spec.cost];
        // A check can downgrade its issues at run time (e.g. "report only:
        // Lidarr is not enabled"); then the spec's fix mode would mislead.
        const reportOnly = r.entry?.note?.startsWith('report only') ?? false;
        const fixMode = reportOnly ? 'report' : r.spec.fix_mode;
        return (
          <div className={c.checkCell}>
            <div className={c.checkTitleRow}>
              <span className={c.checkTitle}>{r.spec.title}</span>
              {cost && <Tag tooltip={COST_HINTS[r.spec.cost]}>{cost}</Tag>}
              <Tag tone={fixMode === 'auto' ? 'success' : undefined} tooltip={reportOnly ? r.entry?.note ?? undefined : FIX_HINTS[fixMode]}>
                {FIX_LABELS[fixMode]}
              </Tag>
              {r.spec.bootstrap_only && (
                <Tag tone="info" tooltip="Repairs state left by older versions. Runs once after the upgrade, or when you pick it in Run options.">
                  One-time repair after upgrade
                </Tag>
              )}
            </div>
            <div className={c.checkDesc} title={r.spec.description}>{r.spec.description}</div>
          </div>
        );
      },
    },
    {
      key: 'counts', header: 'Found', width: 210, priority: 1, mobile: 'meta',
      cell: r => <Counts row={r} status={status} running={runningCheck === r.spec.id} />,
    },
  ];

  return (
    <DataTable<CheckRow>
      aria-label={label}
      framed={false}
      columns={columns}
      rows={rows}
      getRowId={r => r.index}
      getRowLabel={r => r.spec.title}
      loading={loading}
      empty={empty}
      onOpen={r => { if (runId) onExpand(expanded === r.spec.id ? null : r.spec.id); }}
      isOpenable={() => !!runId}
      rowProps={r => ({ 'data-zero': r.found === 0 || undefined })}
      expandable={{
        isExpanded: r => expanded === r.spec.id,
        toggle: r => { if (runId) onExpand(expanded === r.spec.id ? null : r.spec.id); },
        render: r => (runId ? (
          <IssueTable runId={runId} check={r.spec} status={status} severity={severity} busy={busy} />
        ) : null),
      }}
      breakpoints={{ 2: 600, 3: 760 }}
      className={c.checkTable}
    />
  );
}
