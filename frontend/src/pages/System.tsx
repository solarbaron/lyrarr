import { useQuery } from '@tanstack/react-query';
import { Tabs } from '@mantine/core';
import { getSystemStatus, getTasks, getHealth, getProviderHealth } from '../api';
import { PageHeader } from '../components/ui';
import { useUrlState } from '../hooks/useUrlState';
import { StatusTab, type SystemStatus } from './system/StatusTab';
import { ProvidersTab } from './system/ProvidersTab';
import { TasksTab, type Task } from './system/TasksTab';
import { LogsTab } from './system/LogsTab';
import { AuditTab } from './system/AuditTab';
import type { ProviderHealthEntry, ServiceHealth } from './home/types';
import c from './system/System.module.css';

const TABS = ['status', 'audit', 'providers', 'tasks', 'logs'] as const;
type Tab = typeof TABS[number];

/** "3.13.15" → "3.13" for the subtitle (the full version is in Status). */
function shortVersion(v: string | undefined): string | undefined {
  return v?.split('.').slice(0, 2).join('.');
}

export default function SystemPage() {
  const [tabParam, setTab] = useUrlState<string>('tab', 'status');
  const tab: Tab = (TABS as readonly string[]).includes(tabParam) ? tabParam as Tab : 'status';

  const { data: status } = useQuery({ queryKey: ['system-status'], queryFn: () => getSystemStatus() as Promise<SystemStatus> });
  const tasks = useQuery({ queryKey: ['tasks'], queryFn: () => getTasks() as Promise<Task[]> });
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: () => getHealth() as Promise<Record<string, ServiceHealth>> });
  const providerHealth = useQuery({
    queryKey: ['provider-health'],
    queryFn: () => getProviderHealth() as Promise<{ providers?: Record<string, ProviderHealthEntry> }>,
    refetchInterval: 15000,
  });

  const subtitle = [
    `Lyrarr v${status?.version || 'dev'}`,
    status?.python_version && `Python ${shortVersion(status.python_version)}`,
    status?.os,
  ].filter(Boolean).join(' · ');

  return (
    <div className="ly-page">
      <PageHeader title="System" subtitle={subtitle} />

      <Tabs value={tab} onChange={v => setTab((v as Tab) || 'status', { replace: false })}>
        <Tabs.List aria-label="System sections">
          <Tabs.Tab value="status">Status</Tabs.Tab>
          <Tabs.Tab value="audit">Audit</Tabs.Tab>
          <Tabs.Tab value="providers">Providers</Tabs.Tab>
          <Tabs.Tab value="tasks">Tasks</Tabs.Tab>
          <Tabs.Tab value="logs">Logs</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="status" className={c.tabPanel}>
          <StatusTab status={status} health={health} providers={providerHealth.data?.providers} />
        </Tabs.Panel>
        <Tabs.Panel value="audit" className={c.tabPanel}>
          {tab === 'audit' && <AuditTab />}
        </Tabs.Panel>
        <Tabs.Panel value="providers" className={c.tabPanel}>
          <ProvidersTab
            providers={providerHealth.data?.providers}
            loading={providerHealth.isLoading}
            error={providerHealth.isError
              ? { message: (providerHealth.error as Error)?.message, onRetry: () => providerHealth.refetch() }
              : null}
          />
        </Tabs.Panel>
        <Tabs.Panel value="tasks" className={c.tabPanel}>
          <TasksTab
            tasks={tasks.data ?? []}
            loading={tasks.isLoading}
            error={tasks.isError ? { message: (tasks.error as Error)?.message, onRetry: () => tasks.refetch() } : null}
          />
        </Tabs.Panel>
        <Tabs.Panel value="logs" className={c.tabPanel}>
          <LogsTab />
        </Tabs.Panel>
      </Tabs>
    </div>
  );
}
