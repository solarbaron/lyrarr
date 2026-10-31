import { Button, Tooltip } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faClock, faPlay } from '@fortawesome/free-solid-svg-icons';
import { runTask } from '../../api';
import { notifyError } from '../../notify';
import { DataTable, EmptyState, StatusChip, StatusDot, type Column } from '../../components/ui';
import { absTime, relTime } from '../../format';
import c from './System.module.css';

export interface Task {
  job_id: string;
  name: string;
  interval: string;
  next_run_time: string;
  job_running?: boolean;
}

interface Row extends Task {
  id: number;
}

/** The "Next run:" prefix only shows in the < 768px cards, which have no column headers. */
function NextRun({ value }: { value: string }) {
  const d = value ? new Date(value) : null;
  const prefix = <span className={c.cardOnly}>Next run: </span>;
  if (!d || Number.isNaN(d.getTime())) {
    return value ? <span className={c.text2}>{prefix}{value}</span> : <span className={c.text2}>—</span>;
  }
  return (
    <Tooltip label={absTime(d)}>
      <span className={c.text2} tabIndex={-1}>{prefix}{relTime(d)}</span>
    </Tooltip>
  );
}

function capitalise(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

export interface TasksTabProps {
  tasks: Task[];
  loading: boolean;
  error: { message?: string; onRetry: () => void } | null;
}

/** System › Tasks (DESIGN.md 8.11): scheduled jobs with Run now. */
export function TasksTab({ tasks, loading, error }: TasksTabProps) {
  const queryClient = useQueryClient();
  const runTaskMutation = useMutation({
    mutationFn: runTask,
    onSuccess: (_, taskId) => {
      notifications.show({ title: 'Task triggered', message: `${taskId} started`, color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
    onError: (err, taskId) => notifyError(`Failed to run ${taskId}`, err),
  });

  const rows: Row[] = tasks.map((t, i) => ({ ...t, id: i + 1 }));

  const columns: Column<Row>[] = [
    { key: 'name', header: 'Task', mobile: 'title', skeleton: 'title', cell: t => <span className={c.strong}>{t.name}</span> },
    { key: 'interval', header: 'Interval', width: 150, mobile: 'meta', cell: t => <span className={c.text2}>{capitalise(t.interval)}</span> },
    { key: 'next', header: 'Next run', width: 150, mobile: 'meta', cell: t => <NextRun value={t.next_run_time} /> },
    {
      key: 'state', header: 'State', width: 120, mobile: 'status', skeleton: 'chip',
      cell: t => t.job_running ? <StatusChip domain="job" status="running" size="sm" /> : <StatusDot domain="job" status="idle" />,
    },
    {
      key: 'run', header: <span className="visually-hidden">Run</span>, width: 120, align: 'right', mobile: 'status', skeleton: 'actions',
      cell: t => {
        const button = (
          <Button
            variant="default"
            size="xs"
            leftSection={<FontAwesomeIcon icon={faPlay} />}
            onClick={() => runTaskMutation.mutate(t.job_id)}
            loading={runTaskMutation.isPending && runTaskMutation.variables === t.job_id}
            disabled={!!t.job_running}
            aria-label={`Run ${t.name} now`}
          >
            Run now
          </Button>
        );
        return t.job_running
          ? <Tooltip label="Already running"><span className={c.inline}>{button}</span></Tooltip>
          : button;
      },
    },
  ];

  return (
    <DataTable<Row>
      aria-label="Scheduled tasks"
      noun="task"
      columns={columns}
      rows={rows}
      getRowId={t => t.id}
      loading={loading}
      error={error ? { title: 'Couldn’t load tasks', message: error.message, onRetry: error.onRetry } : null}
      empty={<EmptyState size="sm" icon={faClock} title="No scheduled tasks" />}
    />
  );
}
