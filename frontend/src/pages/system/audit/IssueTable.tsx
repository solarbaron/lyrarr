import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Menu, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faCodeCompare, faEye, faEyeSlash, faMagnifyingGlass, faWandMagicSparkles,
} from '@fortawesome/free-solid-svg-icons';
import { applyAuditRun, getAuditIssues, ignoreAuditIssues, unignoreAuditIssue } from '../../../api';
import { errorMessage, notifyError } from '../../../notify';
import { refreshQueries } from '../../../queryRefresh';
import { num, plural } from '../../../format';
import { useUrlPatch, useUrlState } from '../../../hooks/useUrlState';
import {
  BulkBar, DataTable, EmptyState, IconButton, Pager, StatusChip, TitleCell, useConfirm, type Column,
} from '../../../components/ui';
import { FileName, baseName } from '../../../components/library/FileName';
import type { AuditCheck, AuditFixOption, AuditIssue } from '../../../types';
import { CompareModal } from './CompareModal';
import {
  CLEARED_VIEW_CHECKS, CLEARED_VIEW_LINK, EVIDENCE_CHECKS, canApply, detailsSummary, fixLabel, issueHref,
  pinnedVersionId,
} from './model';
import c from './Audit.module.css';

const PAGE_SIZE = 25;

export interface IssueTableProps {
  runId: number;
  check: AuditCheck;
  /** Issue status filter ('open' by default). */
  status: string;
  severity: string | null;
  /** A run is active: applying would be refused (409). */
  busy: boolean;
}

/**
 * Issues of one check in one run (PLAN §5.2): paginated (URL `ipage`), selectable,
 * with Apply fix / Ignore in the bulk bar. Review fixes confirm what changes;
 * destructive ones say the current file is archived first.
 */
export function IssueTable({ runId, check, status, severity, busy }: IssueTableProps) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const patchUrl = useUrlPatch();
  const isMobile = useMediaQuery('(max-width: 767px)') ?? false;
  const [page] = useUrlState('ipage', 1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [compare, setCompare] = useState<AuditIssue | null>(null);

  useEffect(() => { setSelected(new Set()); }, [runId, check.id, status, severity, page]);

  const query = useQuery({
    queryKey: ['audit', 'issues', runId, check.id, status, severity, page],
    queryFn: () => getAuditIssues(runId, {
      check: check.id, status: status || undefined, severity: severity || undefined, page, pageSize: PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
  });
  const issues = query.data?.data ?? [];
  const total = query.data?.total ?? 0;
  const selectedIssues = issues.filter(i => selected.has(i.id));
  const applicable = selectedIssues.filter(canApply);
  const evidence = EVIDENCE_CHECKS.has(check.id);
  // The server ignores open and failed issues (a failed fix can be dismissed too).
  const isIgnorable = (i: AuditIssue) => !i.ignored && (i.status === 'open' || i.status === 'failed');

  const done = () => refreshQueries(queryClient, ['audit']);

  const apply = useMutation({
    mutationFn: ({ ids, actions }: { ids: number[]; actions?: Record<number, string> }) =>
      applyAuditRun(runId, actions ? { issueIds: ids, actions } : { issueIds: ids }),
    onSuccess: (r, { ids }) => {
      notifications.show({
        title: 'Applying fixes',
        message: r?.message || `Fixing ${plural(ids.length, 'issue')}. Progress appears in Activity; the list updates when it finishes.`,
      });
      setSelected(new Set());
      setCompare(null);
      done();
    },
    onError: err => notifyError('Fixes not started', err),
  });

  const ignore = useMutation({
    mutationFn: (ids: number[]) => ignoreAuditIssues(ids),
    onSuccess: (_r, ids) => {
      notifications.show({ title: 'Ignored', message: `${plural(ids.length, 'issue')} hidden from future checks. Filter by “Ignored” to bring them back.`, color: 'success' });
      setSelected(new Set());
      done();
    },
    onError: err => notifyError('Not ignored', err),
  });

  const unignore = useMutation({
    mutationFn: (issue: AuditIssue) => unignoreAuditIssue(issue.check_id, issue.entity_key),
    onSuccess: () => {
      notifications.show({ title: 'Shown again', message: 'The next check reports this item again.', color: 'success' });
      done();
    },
    onError: err => notifyError('Not changed', err),
  });

  /** `choice`: an alternative repair for a single issue (from fix.alternatives). */
  const askApply = async (list: AuditIssue[], choice?: AuditFixOption) => {
    const ok = list.filter(canApply);
    if (!ok.length) return;
    const destructive = choice ? choice.destructive : ok.some(i => i.fix?.destructive);
    const labels = choice?.label ? [choice.label] : Array.from(new Set(ok.map(fixLabel)));
    const confirmed = await confirm({
      title: ok.length === 1 ? `${labels[0]}?` : `Apply fix to ${num(ok.length)} issues?`,
      message: (
        <div className={c.confirmBody}>
          <p>{ok.length === 1 ? <strong>{ok[0].title}</strong> : `${plural(ok.length, 'item')} of “${check.title}”.`}</p>
          <p>Fix: {labels.join(', ')}.</p>
          {destructive ? (
            <p>The current file is archived first, so it can be restored from the track's version history.</p>
          ) : (choice ? choice.action === 'request_replace' : ok.every(i => i.fix_action === 'request_replace')) ? (
            <p>The track is searched again. The current file stays unless a better match turns up.</p>
          ) : (
            <p>Only Lyrarr's records change, or the track is queued for a new search. No file is deleted.</p>
          )}
        </div>
      ),
      // Long repair names ("Re-search (keep the file if nothing better)") stay in the message.
      confirmLabel: ok.length === 1 && labels[0].length <= 28 ? labels[0] : ok.length === 1 ? 'Apply fix' : 'Apply fixes',
      tone: destructive ? 'danger' : 'default',
    });
    if (!confirmed) return;
    const ids = ok.map(i => i.id);
    apply.mutate(choice?.action ? { ids, actions: Object.fromEntries(ids.map(id => [id, choice.action!])) } : { ids });
  };

  const columns: Column<AuditIssue>[] = [
    {
      key: 'item', header: 'Item', mobile: 'title',
      cell: i => <TitleCell title={i.title} href={issueHref(i)} secondary={i.message || undefined} />,
    },
    {
      key: 'path', header: 'File', width: '20%', priority: 3, mobile: 'hidden',
      cell: i => {
        const name = baseName(i.path);
        if (!name) return <span className={c.muted}>—</span>;
        return (
          <Tooltip label={<span className={c.pathTip}>{i.path}</span>} openDelay={300} multiline maw={560}>
            <span className={c.fileCell} tabIndex={-1}><FileName name={name} /></span>
          </Tooltip>
        );
      },
    },
    {
      key: 'details', header: 'Details', width: '24%', priority: 2, mobile: 'meta',
      cell: i => {
        const text = detailsSummary(i);
        return text ? <span className={c.cellText} title={text}>{text}</span> : <span className={c.muted}>—</span>;
      },
    },
    {
      key: 'status', header: 'Status', width: 168, priority: 1, mobile: 'status',
      cell: i => <StatusChip domain="auditIssue" status={i.ignored && i.status === 'open' ? 'ignored' : i.status} size="sm" />,
    },
  ];

  const rowActions = (i: AuditIssue) => (
    <>
      {pinnedVersionId(i) !== null && (
        <IconButton icon={faCodeCompare} label="Compare with the archived version" onClick={() => setCompare(i)} />
      )}
      {canApply(i) && (i.fix?.alternatives?.length ? (
        <Menu position="bottom-end" withinPortal>
          <Menu.Target>
            <IconButton icon={faWandMagicSparkles} label="Choose a fix" disabled={busy} />
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Fix</Menu.Label>
            {[i.fix, ...i.fix.alternatives].filter(o => o.action).map(o => (
              <Menu.Item key={o.action} onClick={() => void askApply([i], o)}>
                {o.label || o.action}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>
      ) : (
        <IconButton icon={faWandMagicSparkles} label={fixLabel(i)} disabled={busy} onClick={() => void askApply([i])} />
      ))}
      {i.status === 'ignored' || i.ignored ? (
        <IconButton icon={faEye} label="Show again in future checks" onClick={() => unignore.mutate(i)} />
      ) : (i.status === 'open' || i.status === 'failed') && (
        <IconButton icon={faEyeSlash} label="Ignore" onClick={() => ignore.mutate([i.id])} />
      )}
    </>
  );

  return (
    <div className={c.issues}>
      {CLEARED_VIEW_CHECKS.has(check.id) && (
        <p className={c.issuesNote}>
          Cleared flags are listed in <Link className="ly-link" to={CLEARED_VIEW_LINK}>Wanted › Instrumental review › Cleared after upgrade</Link>,
          where you can confirm the real instrumentals.
        </p>
      )}
      {evidence && (
        <p className={c.issuesNote}>
          The earlier lyrics are kept until you review them. Compare, then restore the ones you want back.
        </p>
      )}
      <DataTable<AuditIssue>
        aria-label={`Issues of ${check.title}`}
        framed={false}
        stickyHeader={false}
        columns={columns}
        rows={issues}
        getRowId={i => i.id}
        getRowLabel={i => i.title}
        noun="issue"
        loading={query.isLoading}
        refetching={query.isFetching && query.isPlaceholderData}
        error={query.isError ? { title: 'Couldn’t load issues', message: errorMessage(query.error), onRetry: () => query.refetch() } : null}
        empty={(
          <EmptyState
            size="sm"
            icon={faMagnifyingGlass}
            title={status === 'open' ? 'No open issues' : 'No issues with this status'}
            message={status === 'open' ? 'Everything this check found has been handled.' : undefined}
          />
        )}
        selection={{ selected, onChange: setSelected }}
        rowActions={rowActions}
        rowActionsWidth={evidence || issues.some(r => pinnedVersionId(r) !== null) ? 120 : 84}
        breakpoints={{ 2: 640, 3: 820 }}
        footer={total > PAGE_SIZE ? (
          <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={p => patchUrl({ ipage: [p, 1] })} noun="issues" />
        ) : undefined}
      />

      <BulkBar count={selected.size} noun="issue" onClear={() => setSelected(new Set())}>
        {isMobile ? (
          <IconButton
            icon={faEyeSlash}
            label="Ignore"
            variant="default"
            size="input-sm"
            loading={ignore.isPending}
            disabled={!selectedIssues.some(isIgnorable)}
            onClick={() => ignore.mutate(selectedIssues.filter(isIgnorable).map(i => i.id))}
          />
        ) : (
          <Button
            size="sm"
            variant="default"
            leftSection={<FontAwesomeIcon icon={faEyeSlash} />}
            loading={ignore.isPending}
            disabled={!selectedIssues.some(isIgnorable)}
            onClick={() => ignore.mutate(selectedIssues.filter(isIgnorable).map(i => i.id))}
          >
            Ignore {num(selected.size)}
          </Button>
        )}
        <Button
          size="sm"
          variant="filled"
          leftSection={<FontAwesomeIcon icon={faWandMagicSparkles} />}
          loading={apply.isPending}
          disabled={!applicable.length || busy}
          onClick={() => void askApply(selectedIssues)}
        >
          {isMobile ? 'Apply fix' : `Apply fix to ${num(applicable.length)}`}
        </Button>
      </BulkBar>

      <CompareModal
        issue={compare}
        onClose={() => setCompare(null)}
        onRestore={i => apply.mutate({ ids: [i.id] })}
        restoring={apply.isPending}
        busy={busy}
      />
    </div>
  );
}
