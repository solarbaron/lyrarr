import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Group, Loader, Modal, SegmentedControl } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faClockRotateLeft, faCircleExclamation } from '@fortawesome/free-solid-svg-icons';
import { getLyricsVersions, readLyrics } from '../../../api';
import { errorMessage, errorStatus } from '../../../notify';
import { absTime, num } from '../../../format';
import { EmptyState, Tag } from '../../../components/ui';
import { providerName } from '../../../components/lyrics/providers';
import type { AuditIssue } from '../../../types';
import { diffLines, diffStats, isTimed, lyricText } from './diff';
import { canApply, issueTrackId, pinnedVersionId } from './model';
import c from './Audit.module.css';

type View = 'changes' | 'current' | 'archived';

export interface CompareModalProps {
  issue: AuditIssue | null;
  onClose(): void;
  /** Applies the issue's fix (restores the archived version through the audit). */
  onRestore(issue: AuditIssue): void;
  restoring?: boolean;
  /** A run is active: fixes can't be applied until it ends. */
  busy?: boolean;
}

/**
 * Compare / Restore for evidence issues (H-1 curated lyrics replaced, I-7 batch-deleted
 * lyrics, F-9 scrambled generated timing): the current file against the archived
 * version the issue pinned. Restore applies the issue, which archives the current file first.
 */
export function CompareModal({ issue, onClose, onRestore, restoring, busy = false }: CompareModalProps) {
  const fullScreen = useMediaQuery('(max-width: 767px)') ?? false;
  const [view, setView] = useState<View>('changes');
  const trackId = issue ? issueTrackId(issue) : null;
  const versionId = issue ? pinnedVersionId(issue) : null;

  const versions = useQuery({
    queryKey: ['lyrics-versions', trackId],
    queryFn: () => getLyricsVersions(trackId!),
    enabled: !!issue && trackId !== null,
  });
  const current = useQuery({
    queryKey: ['lyrics-read', trackId],
    queryFn: () => readLyrics(trackId!).catch(err => {
      if (errorStatus(err) === 404) return { content: '', type: 'plain' as const };
      throw err;
    }),
    enabled: !!issue && trackId !== null,
  });

  const version = versions.data?.versions?.find(v => v.id === versionId) ?? null;
  const currentText = current.data?.content ?? '';
  // Synced vs plain: compare the words, not the timestamps.
  const timed = !!version && (isTimed(currentText) || isTimed(version.content));
  const diff = useMemo(
    () => (version ? (timed ? diffLines(lyricText(currentText), lyricText(version.content)) : diffLines(currentText, version.content)) : []),
    [currentText, version, timed],
  );
  const stats = diffStats(diff);
  const loading = versions.isLoading || current.isLoading;
  const failed = versions.isError || current.isError;

  let body;
  if (!issue) body = null;
  else if (trackId === null || versionId === null) {
    body = <EmptyState size="sm" icon={faCircleExclamation} title="Nothing to compare" message="This issue doesn't point at an archived version." />;
  } else if (loading) {
    body = <div className={c.center} role="status"><Loader size="sm" /> Loading both versions…</div>;
  } else if (failed) {
    body = (
      <EmptyState
        size="sm" tone="danger" icon={faCircleExclamation} title="Couldn't load the lyrics"
        message={errorMessage(versions.error ?? current.error)}
      />
    );
  } else if (!version) {
    body = (
      <EmptyState
        size="sm" icon={faClockRotateLeft} title="The archived version is gone"
        message="It may have been restored or trimmed already. Run the check again to refresh this issue."
      />
    );
  } else {
    const text = view === 'current' ? currentText : view === 'archived' ? version.content : null;
    body = (
      <>
        <div className={c.compareMeta}>
          <span>
            Archived {absTime(version.timestamp)}
            {' · '}{providerName(version.provider)}
            {version.replaced_by ? ` · replaced by ${providerName(version.replaced_by)}` : ''}
          </span>
          <Tag>{version.lyrics_type}</Tag>
          <span className={c.compareStats}>
            {stats.removed || stats.added
              ? `${num(stats.removed)} line${stats.removed === 1 ? '' : 's'} only in the current file · ${num(stats.added)} only in the archived version`
              : 'Same text'}
            {timed ? ' · timestamps ignored' : ''}
          </span>
        </div>
        <SegmentedControl
          className={c.segmented}
          size="xs"
          value={view}
          onChange={v => setView(v as View)}
          data={[
            { value: 'changes', label: 'Changes' },
            { value: 'current', label: 'Current file' },
            { value: 'archived', label: 'Archived version' },
          ]}
          aria-label="What to show"
        />
        {text !== null ? (
          <pre className={c.compareText}>{text || 'No lyrics file now.'}</pre>
        ) : (
          <ol className={c.diff} aria-label="Line changes: minus = only in the current file, plus = only in the archived version">
            {diff.map((d, i) => (
              <li key={i} className={c.diffLine} data-kind={d.kind}>
                <span className={c.diffMark} aria-hidden="true">{d.kind === 'removed' ? '−' : d.kind === 'added' ? '+' : ' '}</span>
                <span className="visually-hidden">{d.kind === 'removed' ? 'Only in the current file: ' : d.kind === 'added' ? 'Only in the archived version: ' : ''}</span>
                <span className={c.diffText}>{d.text || ' '}</span>
              </li>
            ))}
          </ol>
        )}
      </>
    );
  }

  return (
    <Modal
      opened={!!issue}
      onClose={onClose}
      size={880}
      fullScreen={fullScreen}
      title={issue ? `Compare — ${issue.title}` : 'Compare'}
    >
      <div className={c.compare}>
        {body}
        <Group justify="flex-end" gap="xs" className={c.compareFooter}>
          <Button variant="subtle" color="gray" onClick={onClose}>Close</Button>
          {issue && version && (
            <Button
              variant="filled"
              leftSection={<FontAwesomeIcon icon={faClockRotateLeft} />}
              loading={restoring}
              // Report-only issues (e.g. scrambled timing that was edited later) are never applied.
              disabled={!canApply(issue) || busy}
              onClick={() => onRestore(issue)}
            >
              Restore archived version
            </Button>
          )}
        </Group>
      </div>
    </Modal>
  );
}
