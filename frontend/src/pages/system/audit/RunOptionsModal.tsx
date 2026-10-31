import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import {
  Button, Checkbox, Collapse, Group, Modal, MultiSelect, NumberInput, SegmentedControl, Stack,
} from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faChevronDown, faPlay } from '@fortawesome/free-solid-svg-icons';
import { getAlbums, getArtists, getSettings } from '../../../api';
import { Tag } from '../../../components/ui';
import type { AuditCheck, AuditCost, AuditMode, StartAuditRunBody } from '../../../types';
import { CATEGORIES, COST_HINTS, COST_LABELS, FIX_LABELS } from './model';
import c from './Audit.module.css';

type Scope = 'library' | 'artists' | 'albums';

export interface RunOptionsModalProps {
  opened: boolean;
  onClose(): void;
  checks: AuditCheck[];
  onStart(body: StartAuditRunBody): void;
  starting?: boolean;
}

/** Which costs a run includes; db and fs_stat checks always run. */
interface Include { fs_read: boolean; audio: boolean; network: boolean }

function costIncluded(cost: AuditCost, inc: Include): boolean {
  if (cost === 'fs_read') return inc.fs_read;
  if (cost === 'audio') return inc.audio;
  if (cost === 'network') return inc.network;
  return true;
}

/** Search-as-you-type select for artists or albums (the API caps pages at 500). */
function EntityPicker({ kind, value, onChange }: { kind: 'artists' | 'albums'; value: string[]; onChange(v: string[]): void }) {
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 250);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const query = useQuery({
    queryKey: [kind === 'artists' ? 'artists' : 'albums', 'audit-picker', debounced],
    queryFn: async () => {
      if (kind === 'artists') {
        const r = await getArtists({ search: debounced || undefined, page: 1, pageSize: 50 });
        return r.data.map(a => ({ value: String(a.lidarrArtistId), label: a.name }));
      }
      const r = await getAlbums({ search: debounced || undefined, page: 1, pageSize: 50 });
      return r.data.map(a => ({ value: String(a.lidarrAlbumId), label: a.artistName ? `${a.title} · ${a.artistName}` : a.title }));
    },
    staleTime: 60_000,
  });
  // Keep labels of picked items once they scroll out of the search results.
  useEffect(() => {
    if (!query.data) return;
    setLabels(prev => {
      const next = { ...prev };
      query.data.forEach(o => { next[o.value] = o.label; });
      return next;
    });
  }, [query.data]);
  const data = useMemo(() => {
    const seen = new Set<string>();
    const out: { value: string; label: string }[] = [];
    for (const v of value) { seen.add(v); out.push({ value: v, label: labels[v] ?? `#${v}` }); }
    for (const o of query.data ?? []) if (!seen.has(o.value)) out.push(o);
    return out;
  }, [value, labels, query.data]);
  return (
    <MultiSelect
      label={kind === 'artists' ? 'Artists' : 'Albums'}
      placeholder={value.length ? undefined : `Search ${kind}…`}
      searchable
      searchValue={search}
      onSearchChange={setSearch}
      filter={({ options }) => options}
      data={data}
      value={value}
      onChange={onChange}
      nothingFoundMessage={query.isFetching ? 'Searching…' : `No ${kind} match`}
      clearable
      maxDropdownHeight={260}
    />
  );
}

/**
 * Run options (PLAN §5.2): mode, what to read (lyrics files, audio tags, providers
 * online), scope, and per-check toggles. Bootstrap-only checks are one-time
 * repairs after an upgrade and are never part of a default run.
 */
export function RunOptionsModal({ opened, onClose, checks, onStart, starting }: RunOptionsModalProps) {
  const fullScreen = useMediaQuery('(max-width: 767px)') ?? false;
  const settings = useQuery({ queryKey: ['settings'], queryFn: getSettings, enabled: opened, staleTime: 60_000 });
  const defaultLimit = Number(settings.data?.audit?.network_max_tracks) || 200;

  const [mode, setMode] = useState<AuditMode>('dry_run');
  const [include, setInclude] = useState<Include>({ fs_read: true, audio: false, network: false });
  const [limit, setLimit] = useState<number | string>(defaultLimit);
  const [scope, setScope] = useState<Scope>('library');
  const [artistIds, setArtistIds] = useState<string[]>([]);
  const [albumIds, setAlbumIds] = useState<string[]>([]);
  const [advanced, setAdvanced] = useState(false);
  /** null = server default (default_enabled checks matching the includes). */
  const [picked, setPicked] = useState<Set<string> | null>(null);

  useEffect(() => { setLimit(defaultLimit); }, [defaultLimit]);
  useEffect(() => {
    if (!opened) return;
    setMode('dry_run');
    setInclude({ fs_read: true, audio: false, network: false });
    setScope('library');
    setArtistIds([]);
    setAlbumIds([]);
    setAdvanced(false);
    setPicked(null);
  }, [opened]);

  const defaults = useMemo(
    // Default checks, plus the audio / online checks once their toggle is on (they are off by default only for cost).
    () => new Set(checks.filter(ch => !ch.bootstrap_only && costIncluded(ch.cost, include)
      && (ch.default_enabled || ch.cost === 'audio' || ch.cost === 'network')).map(ch => ch.id)),
    [checks, include],
  );
  const effective = useMemo(
    () => (picked ? new Set(checks.filter(ch => picked.has(ch.id) && costIncluded(ch.cost, include)).map(ch => ch.id)) : defaults),
    [picked, defaults, checks, include],
  );
  const limitNum = typeof limit === 'number' ? limit : Number(limit);
  const limitError = include.network && !(Number.isInteger(limitNum) && limitNum >= 1 && limitNum <= 5000)
    ? 'Enter 1 to 5,000 tracks' : undefined;
  const scopeEmpty = (scope === 'artists' && !artistIds.length) || (scope === 'albums' && !albumIds.length);
  const nothing = effective.size === 0;

  const toggleCheck = (id: string, on: boolean) => {
    const next = new Set(effective);
    if (on) next.add(id); else next.delete(id);
    setPicked(next);
  };

  const submit = () => {
    if (limitError || scopeEmpty || nothing) return;
    const body: StartAuditRunBody = { mode, include: { ...include } };
    if (picked) body.checks = checks.filter(ch => picked.has(ch.id) && costIncluded(ch.cost, include)).map(ch => ch.id);
    if (include.network) body.network_limit = limitNum;
    if (scope === 'artists') body.scope = { artistIds: artistIds.map(Number) };
    if (scope === 'albums') body.scope = { albumIds: albumIds.map(Number) };
    onStart(body);
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Run a library check" size={640} fullScreen={fullScreen}>
      <form onSubmit={e => { e.preventDefault(); submit(); }}>
        <Stack gap="md">
          <div>
            <div className={c.fieldLabel} id="audit-mode-label">Mode</div>
            <SegmentedControl
              aria-labelledby="audit-mode-label"
              value={mode}
              onChange={v => setMode(v as AuditMode)}
              data={[{ value: 'dry_run', label: 'Dry run' }, { value: 'apply', label: 'Apply safe fixes' }]}
            />
            <p className={c.fieldHint}>
              {mode === 'dry_run'
                ? 'Only reports what it finds. Nothing changes until you apply fixes.'
                : 'Checks again and applies the safe fixes it finds. Review items are listed for you to choose.'}
            </p>
          </div>

          <div className={c.optionGroup}>
            <div className={c.fieldLabel}>What to read</div>
            <Checkbox
              label="Read lyrics files"
              description={COST_HINTS.fs_read}
              checked={include.fs_read}
              onChange={e => { setInclude({ ...include, fs_read: e.currentTarget.checked }); setPicked(null); }}
            />
            <Checkbox
              label="Read audio tags (slow)"
              description={COST_HINTS.audio}
              checked={include.audio}
              onChange={e => { setInclude({ ...include, audio: e.currentTarget.checked }); setPicked(null); }}
            />
            <Checkbox
              label="Re-verify online (slow)"
              description="Asks the lyrics providers again for suspect tracks, within their rate limits. Never writes instrumental."
              checked={include.network}
              onChange={e => { setInclude({ ...include, network: e.currentTarget.checked }); setPicked(null); }}
            />
            {include.network && (
              <NumberInput
                className={c.limit}
                label="At most this many tracks"
                min={1}
                max={5000}
                allowDecimal={false}
                value={limit}
                onChange={setLimit}
                error={limitError}
              />
            )}
          </div>

          <div className={c.optionGroup}>
            <div className={c.fieldLabel} id="audit-scope-label">Scope</div>
            <SegmentedControl
              className={c.segmented}
              aria-labelledby="audit-scope-label"
              value={scope}
              onChange={v => setScope(v as Scope)}
              data={[{ value: 'library', label: 'Whole library' }, { value: 'artists', label: 'Artists' }, { value: 'albums', label: 'Albums' }]}
            />
            {scope === 'artists' && <EntityPicker kind="artists" value={artistIds} onChange={setArtistIds} />}
            {scope === 'albums' && <EntityPicker kind="albums" value={albumIds} onChange={setAlbumIds} />}
          </div>

          <div>
            <Button
              variant="subtle"
              color="gray"
              size="compact-sm"
              aria-expanded={advanced}
              rightSection={<FontAwesomeIcon icon={faChevronDown} className={c.chevron} data-open={advanced || undefined} />}
              onClick={() => setAdvanced(a => !a)}
            >
              Choose checks ({effective.size} of {checks.length})
            </Button>
            <Collapse in={advanced}>
              <div className={c.checkPicker}>
                {CATEGORIES.map(cat => {
                  const list = checks.filter(ch => ch.category === cat.value);
                  if (!list.length) return null;
                  return (
                    <fieldset key={cat.value} className={c.checkGroup}>
                      <legend className="ly-overline">{cat.label}</legend>
                      {list.map(ch => {
                        const cost = COST_LABELS[ch.cost];
                        const off = !costIncluded(ch.cost, include);
                        return (
                          <Checkbox
                            key={ch.id}
                            disabled={off}
                            checked={effective.has(ch.id) && !off}
                            onChange={e => toggleCheck(ch.id, e.currentTarget.checked)}
                            label={(
                              <span className={c.checkLabel}>
                                {ch.title}
                                {cost && <Tag>{cost}</Tag>}
                                <Tag>{FIX_LABELS[ch.fix_mode]}</Tag>
                                {ch.bootstrap_only && <Tag tone="info">One-time repair after upgrade</Tag>}
                              </span>
                            )}
                            description={off ? `Needs “${ch.cost === 'audio' ? 'Read audio tags' : ch.cost === 'network' ? 'Re-verify online' : 'Read lyrics files'}”.` : ch.description}
                          />
                        );
                      })}
                    </fieldset>
                  );
                })}
              </div>
            </Collapse>
          </div>

          <Group justify="flex-end" gap="xs">
            <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
            <Button
              type="submit"
              variant="filled"
              leftSection={<FontAwesomeIcon icon={faPlay} />}
              loading={starting}
              disabled={!!limitError || scopeEmpty || nothing}
            >
              {mode === 'dry_run' ? 'Start check' : 'Check and apply safe fixes'}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
