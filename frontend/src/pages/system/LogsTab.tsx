import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Switch } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCopy, faRotateRight, faCircleExclamation } from '@fortawesome/free-solid-svg-icons';
import { getLogs } from '../../api';
import { EmptyState, FilterBar, FilterPill, IconButton, ListSkeleton, Panel } from '../../components/ui';
import { num } from '../../format';
import c from './System.module.css';

type Level = 'ERROR' | 'WARNING' | 'INFO' | 'DEBUG' | 'CRITICAL';
const LEVELS: Level[] = ['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'];

interface LogLine {
  raw: string;
  time?: string;
  level?: Level;
  /** Level of the entry this line belongs to (continuation lines such as tracebacks inherit it). */
  entryLevel?: Level;
  source?: string;
  message?: string;
}

// "2026-03-16 13:35:36 - ERROR - lyrarr.metadata.download_worker - Error saving cover …"
const LINE = /^(\d{4}-\d{2}-\d{2}[ T][\d:,.]+)\s+-\s+([A-Z]+)\s+-\s+(\S+)\s+-\s+([\s\S]*)$/;

function parse(raw: string): LogLine {
  const m = LINE.exec(raw);
  if (!m || !LEVELS.includes(m[2] as Level)) return { raw };
  return { raw, time: m[1], level: m[2] as Level, source: m[3], message: m[4] };
}

/** System › Logs (DESIGN.md 8.11): mono viewer with filter, level, follow, copy, refresh. */
export function LogsTab() {
  const [follow, setFollow] = useState(true);
  const [text, setText] = useState('');
  const [level, setLevel] = useState<string | null>(null);
  const viewer = useRef<HTMLDivElement>(null);

  const { data: logs = [], isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['logs'],
    queryFn: () => getLogs() as Promise<string[]>,
    // While following, pick up new lines without a manual refresh.
    refetchInterval: follow ? 15_000 : false,
  });

  const lines = useMemo(() => {
    let current: Level | undefined;
    return logs.map(raw => {
      const l = parse(raw);
      if (l.level) current = l.level;
      return { ...l, entryLevel: current };
    });
  }, [logs]);
  const counts = useMemo(() => {
    const out = new Map<string, number>();
    for (const l of lines) if (l.level) out.set(l.level, (out.get(l.level) ?? 0) + 1);
    return out;
  }, [lines]);
  const visible = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return lines.filter(l => (!level || l.entryLevel === level) && (!needle || l.raw.toLowerCase().includes(needle)));
  }, [lines, text, level]);

  useEffect(() => {
    if (follow && viewer.current) viewer.current.scrollTop = viewer.current.scrollHeight;
  }, [follow, visible]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(visible.map(l => l.raw).join('\n'));
      notifications.show({ title: 'Copied', message: `${num(visible.length)} log lines copied to the clipboard.` });
    } catch {
      notifications.show({ title: 'Couldn’t copy', message: 'The browser blocked clipboard access.', color: 'danger' });
    }
  };

  const levelOptions = LEVELS
    .filter(l => counts.has(l) || l !== 'CRITICAL')
    .map(l => ({ value: l, label: l.charAt(0) + l.slice(1).toLowerCase(), count: counts.get(l) ?? 0 }));

  let body;
  if (isLoading) {
    body = <div className={c.logPad}><ListSkeleton rows={12} avatar={false} /></div>;
  } else if (isError) {
    body = (
      <EmptyState
        size="sm"
        tone="danger"
        icon={faCircleExclamation}
        title="Couldn’t load logs"
        message={(error as Error)?.message}
        action={(
          <Button
            variant="default"
            size="xs"
            leftSection={<FontAwesomeIcon icon={faRotateRight} />}
            onClick={() => refetch()}
            loading={isFetching}
          >
            Retry
          </Button>
        )}
      />
    );
  } else if (logs.length === 0) {
    body = <div className={c.logEmpty}>No log entries yet.</div>;
  } else if (visible.length === 0) {
    body = <div className={c.logEmpty}>No log lines match the filter.</div>;
  } else {
    body = (
      <div className={c.logLines}>
        {visible.map((l, i) => (
          <div key={i} className={c.logLine}>
            {l.level ? (
              <>
                <span className={c.logTime}>{l.time}</span>{' '}
                <span className={c.logLevel} data-level={l.level}>{l.level}</span>{' '}
                <span className={c.logSource}>{l.source}</span>{' '}
                <span>{l.message}</span>
              </>
            ) : l.raw}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className={c.stack}>
      <FilterBar
        search={{ value: text, onChange: setText, placeholder: 'Filter log lines…', debounce: 150 }}
        filters={<FilterPill label="Level" value={level} options={levelOptions} onChange={setLevel} />}
        right={(
          <div className={c.logTools}>
            <Switch label="Follow" checked={follow} onChange={e => setFollow(e.currentTarget.checked)} />
            <IconButton icon={faCopy} label="Copy visible lines" onClick={copy} disabled={!visible.length} />
            <IconButton icon={faRotateRight} label="Refresh logs" onClick={() => refetch()} loading={isFetching && !isLoading} />
          </div>
        )}
      />
      <Panel padding="none" className={c.logPanel}>
        <div
          ref={viewer}
          className={c.logViewer}
          role="log"
          aria-label="Server log"
          aria-live="off"
          tabIndex={0}
        >
          {body}
        </div>
        {logs.length > 0 && (
          <div className={c.logFoot}>
            {visible.length === logs.length
              ? `${num(logs.length)} lines`
              : `${num(visible.length)} of ${num(logs.length)} lines`}
          </div>
        )}
      </Panel>
    </div>
  );
}
