import { Tooltip } from '@mantine/core';
import { absTime, num, plural, relTime } from '../../../format';
import { DataTable, Panel, StatusDot, Tag, type Column } from '../../../components/ui';
import type { AuditRun } from '../../../types';
import { MODE_LABELS, TRIGGER_LABELS } from './model';
import c from './Audit.module.css';

export interface RunHistoryProps {
  runs: AuditRun[];
  loading?: boolean;
  shownId: number | null;
  onShow(run: AuditRun): void;
}

/** Recent runs (PLAN §5.2): started, trigger, mode, status, issues, fixed, failed; a row loads that run's report. */
export function RunHistory({ runs, loading, shownId, onShow }: RunHistoryProps) {
  const columns: Column<AuditRun>[] = [
    {
      key: 'started', header: 'Started', mobile: 'title',
      cell: r => (
        <Tooltip label={absTime(r.started_at)} openDelay={300}>
          <button
            type="button"
            className={`ly-link ${c.linkButton}`}
            aria-current={r.id === shownId ? 'true' : undefined}
            onClick={() => onShow(r)}
          >
            {relTime(r.started_at)}
            {r.id === shownId && <span className={c.shown}> · shown</span>}
          </button>
        </Tooltip>
      ),
    },
    { key: 'trigger', header: 'Trigger', width: 130, priority: 2, mobile: 'meta', cell: r => <Tag>{TRIGGER_LABELS[r.trigger] ?? r.trigger}</Tag> },
    { key: 'mode', header: 'Mode', width: 96, priority: 2, mobile: 'meta', cell: r => <span className={c.text2}>{MODE_LABELS[r.mode] ?? r.mode}</span> },
    { key: 'status', header: 'Status', width: 120, priority: 1, mobile: 'status', cell: r => <StatusDot domain="job" status={r.status} /> },
    {
      key: 'issues', header: 'Issues', width: 80, align: 'right', priority: 1, mobile: 'meta',
      cell: (r, ctx) => <span className="num">{ctx.mobile ? plural(r.totals?.issues ?? 0, 'issue') : num(r.totals?.issues ?? 0)}</span>,
    },
    {
      key: 'fixed', header: 'Fixed', width: 80, align: 'right', priority: 3, mobile: 'meta',
      cell: (r, ctx) => <span className="num">{ctx.mobile ? `${num(r.totals?.fixed ?? 0)} fixed` : num(r.totals?.fixed ?? 0)}</span>,
    },
    {
      // Fixes that were tried and failed (apply runs); the run itself still ends "Done".
      key: 'failed', header: 'Failed', width: 80, align: 'right', priority: 2, mobile: 'meta',
      cell: (r, ctx) => {
        const failed = r.totals?.failed ?? 0;
        if (ctx.mobile) return failed > 0 ? <span className={`num ${c.warnText}`}>{num(failed)} failed</span> : null;
        return <span className={`num${failed > 0 ? ` ${c.warnText}` : ''}`}>{num(failed)}</span>;
      },
    },
  ];
  return (
    <Panel title="Run history" padding="none" headingLevel={3}>
      <DataTable<AuditRun>
        aria-label="Library check runs"
        framed={false}
        stickyHeader={false}
        columns={columns}
        rows={runs}
        getRowId={r => r.id}
        loading={loading}
        onOpen={onShow}
        rowProps={r => ({ 'data-state': r.id === shownId ? 'shown' : undefined })}
        empty={<p className={c.emptyLine}>No runs yet.</p>}
        breakpoints={{ 2: 520, 3: 640 }}
      />
    </Panel>
  );
}
