import { Button, Tooltip } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faRotateLeft, faServer } from '@fortawesome/free-solid-svg-icons';
import { resetProviderHealth } from '../../api';
import { notifyError } from '../../notify';
import {
  DataTable, EmptyState, IconButton, StatusDot, StatusGlyph, Tag, useConfirm, type Column,
} from '../../components/ui';
import { absTime, num, relTime } from '../../format';
import type { ProviderHealthEntry } from '../home/types';
import { providerLabel } from './providers';
import c from './System.module.css';

interface Row extends ProviderHealthEntry {
  id: number;
  name: string;
}

const TIME = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });

function untilLabel(until: string | null): string {
  if (!until) return 'Paused';
  const d = new Date(until);
  return Number.isNaN(d.getTime()) ? 'Paused' : `Disabled until ${TIME.format(d)}`;
}

export interface ProvidersTabProps {
  providers: Record<string, ProviderHealthEntry> | undefined;
  loading: boolean;
  error: { message?: string; onRetry: () => void } | null;
}

/** System › Providers (DESIGN.md 8.11): per-provider health with reset. */
export function ProvidersTab({ providers, loading, error }: ProvidersTabProps) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();

  const resetHealthMutation = useMutation({
    mutationFn: (provider?: string) => resetProviderHealth(provider),
    onSuccess: (_, provider) => {
      notifications.show({ title: 'Reset', message: `Health stats reset for ${provider ? providerLabel(provider) : 'all providers'}`, color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['provider-health'] });
    },
    onError: (err) => notifyError('Failed to reset provider health', err),
  });

  const rows: Row[] = Object.entries(providers ?? {}).map(([name, stats], i) => ({ ...stats, name, id: i + 1 }));
  const available = rows.filter(r => r.available).length;

  const resetAll = async () => {
    const ok = await confirm({
      title: 'Reset all provider stats?',
      message: 'Success and failure counts go back to zero and any paused provider is available again straight away.',
      confirmLabel: 'Reset all',
    });
    if (ok) resetHealthMutation.mutate(undefined);
  };

  const columns: Column<Row>[] = [
    {
      key: 'name', header: 'Provider', mobile: 'title', skeleton: 'title',
      cell: r => <span className={c.strong}>{providerLabel(r.name)}</span>,
    },
    {
      key: 'type', header: 'Type', width: 96, mobile: 'meta', skeleton: 'chip',
      cell: r => <Tag>{r.type || '—'}</Tag>,
    },
    {
      key: 'status', header: 'Status', width: 190, mobile: 'status', skeleton: 'chip',
      cell: r => r.available
        ? <StatusDot domain="health" status="healthy" />
        : (
          <Tooltip label={r.disabled_until ? absTime(r.disabled_until) : 'Paused after repeated failures'}>
            <span className={c.dot} tabIndex={0}>
              <StatusGlyph glyph="alert" tone="warning" size={10} />
              {untilLabel(r.disabled_until)}
            </span>
          </Tooltip>
        ),
    },
    {
      key: 'successes', header: 'Successes', width: 104, align: 'right', priority: 2, mobile: 'meta', skeleton: 'num',
      // The card layout (< 768px) has no column headers, so it carries the unit.
      cell: r => (
        <span className={c.text2}>
          {num(r.successes)}
          <span className={c.cardOnly}> {r.successes === 1 ? 'success' : 'successes'}</span>
        </span>
      ),
    },
    {
      key: 'failures', header: 'Failures', width: 92, align: 'right', mobile: 'meta', skeleton: 'num',
      cell: r => (
        <span className={c.count} data-bad={r.failures > 0 || undefined}>
          {num(r.failures)}
          <span className={c.cardOnly}>
            {' '}{r.failures === 1 ? 'failure' : 'failures'}
            {r.consecutive_failures > 0 && ` (${num(r.consecutive_failures)} in a row)`}
          </span>
        </span>
      ),
    },
    {
      key: 'streak', header: 'Streak', width: 80, align: 'right', priority: 2, mobile: 'hidden', skeleton: 'num',
      cell: r => (
        <Tooltip label="Consecutive failures">
          <span className={c.count} data-bad={r.consecutive_failures >= 3 || undefined} tabIndex={-1}>{num(r.consecutive_failures)}</span>
        </Tooltip>
      ),
    },
    {
      key: 'last', header: 'Last failure', width: 132, priority: 3, mobile: 'meta',
      cell: r => r.last_failure
        ? (
          <Tooltip label={absTime(r.last_failure)}>
            <span className={c.text2}><span className={c.cardOnly}>Last failure </span>{relTime(r.last_failure)}</span>
          </Tooltip>
        )
        : <span className={c.muted}>—</span>,
    },
  ];

  return (
    <div className={c.stack}>
      <div className={c.toolbar}>
        <span className={c.summary}>
          {rows.length ? `${num(available)} of ${num(rows.length)} providers available` : ' '}
        </span>
        <Button
          variant="default"
          size="xs"
          leftSection={<FontAwesomeIcon icon={faRotateLeft} />}
          onClick={resetAll}
          loading={resetHealthMutation.isPending && resetHealthMutation.variables === undefined}
          disabled={!rows.length}
        >
          Reset all
        </Button>
      </div>
      <DataTable<Row>
        aria-label="Provider health"
        noun="provider"
        columns={columns}
        rows={rows}
        getRowId={r => r.id}
        loading={loading}
        error={error ? { title: 'Couldn’t load provider health', message: error.message, onRetry: error.onRetry } : null}
        empty={<EmptyState size="sm" icon={faServer} title="No providers reported" message="Provider health appears once Lyrarr has queried a provider." />}
        rowActionsWidth={56}
        // Always an action, so the mobile card's ⋯ never opens an empty menu; healthy rows get it disabled.
        rowActions={r => {
          const resettable = r.failures > 0 || !r.available;
          return (
            <IconButton
              icon={faRotateLeft}
              label={resettable ? `Reset ${providerLabel(r.name)}` : `Reset ${providerLabel(r.name)} (no failures to reset)`}
              onClick={() => resetHealthMutation.mutate(r.name)}
              loading={resetHealthMutation.isPending && resetHealthMutation.variables === r.name}
              disabled={!resettable}
            />
          );
        }}
      />
    </div>
  );
}
