import type { ReactNode } from 'react';
import { NumberInput, Select, Slider, Switch, TextInput } from '@mantine/core';
import { SettingsGroup, SettingsRow } from '../../components/ui';
import { ProviderOrder } from './ProviderOrder';
import {
  ALL_COVER_PROVIDERS, ALL_LYRICS_PROVIDERS, AUTO_TRANSLATE, COVER_FORMATS, PROFILE_LANGUAGES, SELECTION_MODE_HINT, SELECTION_MODES,
  type ProfileForm,
} from './profileForm';
import c from './Profiles.module.css';

export interface ProfileEditorProps {
  form: ProfileForm;
  onChange(next: ProfileForm): void;
  nameError?: string | null;
  /** Prefix for control ids (the editor can render twice: panel and drawer). */
  idPrefix?: string;
}

/** A label + description above a full-width control (provider lists). */
function BlockRow({ label, description, children }: { label: string; description?: ReactNode; children: ReactNode }) {
  return (
    <div className={c.blockRow}>
      <div className={c.blockLabel}>{label}</div>
      {description && <div className={c.blockDesc}>{description}</div>}
      <div className={c.blockControl}>{children}</div>
    </div>
  );
}

/**
 * Every profile field from the old create/edit modal, as SettingsRows grouped
 * under overlines (DESIGN.md 8.9).
 */
export function ProfileEditor({ form, onChange, nameError, idPrefix = 'profile' }: ProfileEditorProps) {
  const set = <K extends keyof ProfileForm>(key: K, value: ProfileForm[K]) => onChange({ ...form, [key]: value });
  const id = (s: string) => `${idPrefix}-${s}`;
  const switchRow = (key: keyof ProfileForm, label: string, description?: string) => (
    <SettingsRow
      label={label}
      description={description}
      htmlFor={id(key)}
      control={(
        <Switch
          id={id(key)}
          checked={form[key] as boolean}
          onChange={e => set(key, e.currentTarget.checked as never)}
          aria-describedby={description ? `${id(key)}-desc` : undefined}
        />
      )}
    />
  );

  return (
    <div className={c.editor}>
      <SettingsGroup title="Profile">
        <SettingsRow
          label="Name"
          description="Shown in the artist and album settings panels."
          htmlFor={id('name')}
          control={(
            <TextInput
              id={id('name')}
              value={form.name}
              placeholder="e.g. Synced only"
              onChange={e => set('name', e.currentTarget.value)}
              error={nameError || undefined}
              aria-describedby={`${id('name')}-desc`}
              required
            />
          )}
        />
      </SettingsGroup>

      <SettingsGroup title="Downloads">
        {switchRow('download_covers', 'Download cover art')}
        {switchRow('download_lyrics', 'Download lyrics')}
        {switchRow('overwrite_existing', 'Overwrite existing files', 'Replace cover and lyrics files that are already on disk.')}
        {switchRow('embed_cover_art', 'Embed cover art in audio files', 'Write the cover into each track’s tags as well as the folder.')}
      </SettingsGroup>

      <SettingsGroup title="Cover art">
        <SettingsRow
          label="Cover format"
          htmlFor={id('cover-format')}
          control={(
            <Select
              id={id('cover-format')}
              data={COVER_FORMATS}
              value={form.cover_format}
              allowDeselect={false}
              onChange={v => set('cover_format', v || 'jpg')}
            />
          )}
        />
        <BlockRow label="Providers (priority order)" description="Order matters: the first provider is tried first.">
          <ProviderOrder
            label="Cover art providers"
            options={ALL_COVER_PROVIDERS}
            value={form.cover_providers}
            onChange={v => set('cover_providers', v)}
          />
        </BlockRow>
      </SettingsGroup>

      <SettingsGroup title="Lyrics">
        <SettingsRow
          label="Selection mode"
          description={`How to pick the best lyrics result from all providers. ${SELECTION_MODE_HINT[form.lyrics_selection_mode] ?? ''}.`}
          htmlFor={id('mode')}
          control={(
            <Select
              id={id('mode')}
              data={SELECTION_MODES}
              renderOption={({ option }) => (
                <span className={c.option}>
                  <span>{option.label}</span>
                  {SELECTION_MODE_HINT[option.value] && <span className={c.optionHint}>{SELECTION_MODE_HINT[option.value]}</span>}
                </span>
              )}
              value={form.lyrics_selection_mode}
              allowDeselect={false}
              onChange={v => set('lyrics_selection_mode', v || 'best_score')}
              aria-describedby={`${id('mode')}-desc`}
            />
          )}
        />
        <SettingsRow
          label="Minimum score"
          description="Reject lyrics below this match % (0 accepts all)."
          htmlFor={id('score')}
          control={(
            <div className={c.score}>
              <Slider
                className={c.scoreSlider}
                min={0}
                max={100}
                step={5}
                value={form.score_threshold}
                onChange={v => set('score_threshold', v)}
                label={v => `${v}%`}
                thumbLabel="Minimum score"
              />
              <NumberInput
                id={id('score')}
                className={c.scoreInput}
                value={form.score_threshold}
                onChange={v => set('score_threshold', Number(v) || 0)}
                min={0}
                max={100}
                step={5}
                clampBehavior="strict"
                allowDecimal={false}
                suffix="%"
                aria-describedby={`${id('score')}-desc`}
              />
            </div>
          )}
        />
        {switchRow('prefer_synced_lyrics', 'Prefer synced lyrics', 'Keep time-synced (LRC) lyrics when both kinds are available.')}
        <BlockRow label="Providers (priority order)" description="All enabled providers are queried; results are ranked by the selection mode.">
          <ProviderOrder
            label="Lyrics providers"
            options={ALL_LYRICS_PROVIDERS}
            value={form.lyrics_providers}
            onChange={v => set('lyrics_providers', v)}
          />
        </BlockRow>
      </SettingsGroup>

      <SettingsGroup title="Language & translation">
        {switchRow('auto_detect_language', 'Auto-detect language', 'Identify the language of downloaded lyrics.')}
        <SettingsRow
          label="Auto-translate"
          description="Automatically translate lyrics after download."
          htmlFor={id('auto-translate')}
          control={(
            <Select
              id={id('auto-translate')}
              data={AUTO_TRANSLATE}
              value={form.auto_translate}
              allowDeselect={false}
              onChange={v => set('auto_translate', v || 'off')}
              aria-describedby={`${id('auto-translate')}-desc`}
            />
          )}
        />
        {form.auto_translate !== 'off' && (
          <>
            <SettingsRow
              label="Target language"
              htmlFor={id('target')}
              control={(
                <Select
                  id={id('target')}
                  data={PROFILE_LANGUAGES}
                  value={form.translate_target_lang}
                  allowDeselect={false}
                  searchable
                  onChange={v => set('translate_target_lang', v || 'en')}
                />
              )}
            />
            {switchRow('translate_only_foreign', 'Only translate foreign languages', 'Skip translation when lyrics are already in the target language.')}
          </>
        )}
      </SettingsGroup>
    </div>
  );
}
