import { useEffect, useState } from 'react';
import { Button, Checkbox, Group, Modal, Radio, Select, TextInput } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { num, plural } from '../../format';
import c from './MassEdit.module.css';

export const LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'zh-CN', label: 'Chinese (Simplified)' },
  { value: 'ru', label: 'Russian' },
  { value: 'nl', label: 'Dutch' },
  { value: 'pl', label: 'Polish' },
  { value: 'sv', label: 'Swedish' },
  { value: 'tr', label: 'Turkish' },
];

export type DeleteMode = 'all' | 'plain' | 'instrumental';

/** Above this many albums, deleting asks the user to type the count (DESIGN.md 8.8). */
const TYPE_TO_CONFIRM_OVER = 100;

interface TranslateModalProps {
  opened: boolean;
  onClose(): void;
  albums: number;
  targetLang: string | null;
  onTargetLang(v: string | null): void;
  force: boolean;
  onForce(v: boolean): void;
  onSubmit(): void;
  loading: boolean;
}

export function TranslateModal({
  opened, onClose, albums, targetLang, onTargetLang, force, onForce, onSubmit, loading,
}: TranslateModalProps) {
  const mobile = useMediaQuery('(max-width: 767px)');
  return (
    <Modal opened={opened} onClose={onClose} title="Translate lyrics" size={440} fullScreen={!!mobile}>
      <div className={c.modalStack}>
        <p className={c.modalLead}>
          Translates the lyrics of {plural(albums, 'selected album')}.
        </p>
        <Select
          label="Target language"
          data={LANGUAGES}
          value={targetLang}
          onChange={onTargetLang}
          allowDeselect={false}
        />
        <Checkbox
          label="Force re-translate tracks whose language is already known"
          description="Without this, only tracks with an undetected language are processed."
          checked={force}
          onChange={e => onForce(e.currentTarget.checked)}
        />
        <p className={c.modalNote}>Uses the translation engine configured in Settings (Google or DeepL).</p>
        <Group justify="flex-end" gap="xs" className={c.modalFooter}>
          <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
          <Button variant="filled" onClick={onSubmit} loading={loading} disabled={!targetLang}>
            Translate {plural(albums, 'album')}
          </Button>
        </Group>
      </div>
    </Modal>
  );
}

interface DeleteModalProps {
  opened: boolean;
  onClose(): void;
  albums: number;
  mode: DeleteMode;
  onMode(v: DeleteMode): void;
  onSubmit(): void;
  loading: boolean;
}

export function DeleteModal({ opened, onClose, albums, mode, onMode, onSubmit, loading }: DeleteModalProps) {
  const mobile = useMediaQuery('(max-width: 767px)');
  const requireText = albums > TYPE_TO_CONFIRM_OVER ? String(albums) : null;
  const [typed, setTyped] = useState('');
  useEffect(() => { if (opened) setTyped(''); }, [opened]);
  const blocked = requireText !== null && typed.replace(/[,\s]/g, '') !== requireText;

  return (
    <Modal opened={opened} onClose={onClose} title="Delete lyrics" size={440} fullScreen={!!mobile}>
      <form
        className={c.modalStack}
        onSubmit={e => { e.preventDefault(); if (!blocked) onSubmit(); }}
        onKeyDown={e => {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !blocked) { e.preventDefault(); onSubmit(); }
        }}
      >
        <p className={c.modalLead}>
          Delete lyrics files from <strong>{plural(albums, 'selected album')}</strong>?
        </p>
        <Radio.Group value={mode} onChange={v => onMode(v as DeleteMode)} label="What to delete">
          <div className={c.radioStack}>
            <Radio value="all" label="All lyrics files" description="Every lyrics file in the selection" />
            <Radio value="plain" label="Plain (unsynced) lyrics only" description="Keep synced .lrc files, remove plain-text ones" />
            <Radio
              value="instrumental"
              label="Instrumental tracks only"
              description="Remove leftover marker or empty files from instrumental tracks. Files with real lyrics are kept and the track is un-flagged."
            />
          </div>
        </Radio.Group>
        <p className={c.modalNote}>
          Files are archived to each track's version history before deletion, so
          individual tracks can be restored from the lyrics editor.
        </p>
        {requireText !== null && (
          <TextInput
            label={`Type ${num(albums)} to confirm`}
            value={typed}
            onChange={e => setTyped(e.currentTarget.value)}
            autoComplete="off"
            inputMode="numeric"
            data-autofocus
          />
        )}
        <Group justify="flex-end" gap="xs" className={c.modalFooter}>
          <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="filled" color="danger" loading={loading} disabled={blocked}>
            Delete lyrics
          </Button>
        </Group>
      </form>
    </Modal>
  );
}
