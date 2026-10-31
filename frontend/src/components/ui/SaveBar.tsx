import { Button } from '@mantine/core';
import { useBottomReserve, BOTTOM_LAYER } from '../../hooks/useBottomReserve';
import { useUnsavedChangesPrompt } from '../../hooks/useUnsavedChangesPrompt';
import c from './Forms.module.css';

export interface SaveBarProps {
  dirty: boolean;
  saving?: boolean;
  onSave(): void;
  onDiscard(): void;
  /** Default "Unsaved changes". */
  message?: string;
  /** Label of the save button (default "Save changes"). */
  saveLabel?: string;
  /** Ask before navigating away while dirty (default true). */
  guard?: boolean;
}

/**
 * Sticky unsaved-changes bar for Settings and the Profiles editor (DESIGN.md 7.8).
 * Hidden when clean; while dirty, Save is the view's single primary. Reserves
 * bottom space and asks before leaving with unsaved changes.
 */
export function SaveBar({ dirty, saving, onSave, onDiscard, message = 'Unsaved changes', saveLabel = 'Save changes', guard = true }: SaveBarProps) {
  const below = useBottomReserve('save-bar', 52 + 24, dirty, BOTTOM_LAYER.bar);
  useUnsavedChangesPrompt(guard && dirty);
  if (!dirty) return null;
  return (
    <div
      className={c.saveBar}
      role="region"
      aria-label="Unsaved changes"
      style={{ bottom: `calc(16px + ${below}px + env(safe-area-inset-bottom, 0px))` }}
      onKeyDown={e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          onSave();
        }
      }}
    >
      <span className={c.saveDot} aria-hidden="true" />
      <span className={c.saveMessage} aria-live="polite" title={message}>{message}</span>
      <span className={c.saveSpacer} />
      <Button className={c.saveButton} variant="subtle" color="gray" onClick={onDiscard} disabled={saving}>Discard</Button>
      <Button className={c.saveButton} variant="filled" onClick={onSave} loading={saving}>{saveLabel}</Button>
    </div>
  );
}
