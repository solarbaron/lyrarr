import { useEffect, useId, useMemo, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Kbd, Loader, Modal, UnstyledButton } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faAlignLeft, faCheck, faCircleHalfStroke, faDownload, faKeyboard, faListCheck, faMagnifyingGlass, faMusic,
  faRotate, faTableColumns, type IconDefinition,
} from '@fortawesome/free-solid-svg-icons';
import { getSystemStatus, globalSearch, runTask, triggerSync } from '../../api';
import { notifyError } from '../../notify';
import { useRecentAlbums } from '../../hooks/useRecentAlbums';
import { useTheme } from '../../theme/ThemeProvider';
import { themes } from '../../theme/themes';
import { CoverArt } from '../ui';
import { NAV_ITEMS } from './nav';
import { useShell } from './ShellContext';
import c from './CommandPalette.module.css';

interface SearchResults {
  artists: { id: number; name: string; poster?: string }[];
  albums: { id: number; title: string; cover?: string; artistName: string; year?: number }[];
  tracks: { id: number; title: string; albumId: number; artistName: string }[];
}

type Group = 'Recent' | 'Artists' | 'Albums' | 'Tracks' | 'Actions';

interface Option {
  key: string;
  group: Group;
  label: string;
  sub?: string;
  /** Leading visual: an icon, or a cover/artist image. */
  icon?: IconDefinition;
  art?: { kind: 'album' | 'artist'; src?: string | null; name: string };
  /** Extra words matched by the filter (actions only). */
  keywords?: string;
  /** Marks the option that is already in effect (the theme in use). */
  current?: boolean;
  run(): void;
}

const GROUP_ORDER: Group[] = ['Recent', 'Artists', 'Albums', 'Tracks', 'Actions'];

export interface CommandPaletteProps {
  opened: boolean;
  onClose(): void;
}

/**
 * Command palette (DESIGN.md 6.8), replacing the sidebar search box: global
 * search (debounced, previous request cancelled) plus navigation, jobs and
 * theme actions. ↑/↓ move, Enter opens, Esc closes.
 */
export function CommandPalette({ opened, onClose }: CommandPaletteProps) {
  const mobile = useMediaQuery('(max-width: 767px)', false, { getInitialValueInEffect: false });
  return (
    <Modal.Root
      opened={opened}
      onClose={onClose}
      size={640}
      yOffset="12vh"
      padding={0}
      fullScreen={mobile}
      zIndex={210}
      centered={false}
      transitionProps={{ transition: 'pop', duration: 180 }}
    >
      <Modal.Overlay />
      {/* Compound form so the dialog itself carries the accessible name (there is no visible title). */}
      <Modal.Content className={c.content} aria-label="Command palette">
        <Modal.Body className={c.body}>
          {opened && <PaletteBody onClose={onClose} mobile={mobile} />}
        </Modal.Body>
      </Modal.Content>
    </Modal.Root>
  );
}

function PaletteBody({ onClose, mobile }: { onClose(): void; mobile: boolean }) {
  const navigate = useNavigate();
  const { openShortcuts, toggleSidebar } = useShell();
  const { preference, setPreference } = useTheme();
  const recent = useRecentAlbums();
  const { data: status } = useQuery({ queryKey: ['system-status'], queryFn: getSystemStatus, staleTime: 60_000 });
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResults | null>(null);
  const [searching, setSearching] = useState(false);
  const [active, setActive] = useState(0);
  const baseId = useId();

  const trimmed = query.trim();

  // Each query change cancels the previous request, and a response is only
  // applied if its effect is still current — so a slow reply for an older
  // query (or one arriving after the box was cleared) can't overwrite the
  // results.
  useEffect(() => {
    if (trimmed.length < 2) {
      setResults(null);
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    let current = true;
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const data = await globalSearch(trimmed, controller.signal);
        if (!current) return;
        setResults(data);
      } catch {
        if (current) setResults(null);
      } finally {
        if (current) setSearching(false);
      }
    }, 300);

    return () => {
      current = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed]);

  const go = (to: string) => {
    onClose();
    navigate(to);
  };

  const runJob = async (fn: () => Promise<{ message?: string } | undefined>, title: string, errorTitle: string) => {
    onClose();
    try {
      const res = await fn();
      notifications.show({ title, message: res?.message || 'Progress appears in Activity.', autoClose: 5000 });
    } catch (err) {
      notifyError(errorTitle, err);
    }
  };

  const actions = useMemo<Option[]>(() => {
    const list: Option[] = NAV_ITEMS.map(item => ({
      key: `go-${item.path}`,
      group: 'Actions',
      label: `Go to ${item.label}`,
      icon: item.icon,
      keywords: 'navigate page open',
      run: () => go(item.path),
    }));
    if (status?.lidarr_enabled) {
      list.push({
        key: 'sync', group: 'Actions', label: 'Sync with Lidarr', icon: faRotate, keywords: 'lidarr refresh library',
        run: () => runJob(triggerSync, 'Sync started', 'Sync failed'),
      });
    }
    list.push({
      key: 'download', group: 'Actions', label: 'Download missing now', icon: faDownload,
      keywords: 'covers lyrics fetch metadata task run',
      run: () => runJob(() => runTask('download_metadata'), 'Download started', 'Could not start download'),
    });
    list.push({
      key: 'audit', group: 'Actions', label: 'Check library (audit)', icon: faListCheck,
      keywords: 'audit repair scan fix problems verify library check',
      // System › Audit starts a dry run when it sees this state (nothing changes until fixes are applied).
      run: () => { onClose(); navigate('/system?tab=audit', { state: { startAudit: true } }); },
    });
    list.push({
      key: 'instrumental-review', group: 'Actions', label: 'Instrumental review', icon: faMusic,
      keywords: 'instrumental wanted flags cleared confirm vocal',
      run: () => go('/wanted?tab=instrumental'),
    });
    list.push({
      key: 'theme-system', group: 'Actions', label: 'Theme: System', icon: faCircleHalfStroke, keywords: 'appearance os auto dark light',
      current: preference === 'system',
      run: () => { setPreference('system'); onClose(); },
    });
    for (const t of themes) {
      list.push({
        key: `theme-${t.id}`, group: 'Actions', label: `Theme: ${t.name}`, icon: faCircleHalfStroke,
        keywords: `appearance ${t.scheme} ${t.contrast === 'high' ? 'high contrast' : ''}`,
        current: preference === t.id,
        run: () => { setPreference(t.id); onClose(); },
      });
    }
    list.push({ key: 'sidebar', group: 'Actions', label: 'Toggle sidebar', icon: faTableColumns, keywords: 'collapse expand rail', run: () => { onClose(); toggleSidebar(); } });
    list.push({ key: 'shortcuts', group: 'Actions', label: 'Keyboard shortcuts', icon: faKeyboard, keywords: 'keys help hotkeys', run: () => { onClose(); openShortcuts(); } });
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.lidarr_enabled, preference]);

  const options = useMemo<Option[]>(() => {
    const q = trimmed.toLowerCase();
    if (!q) {
      const recents: Option[] = recent.items.slice(0, 6).map(a => ({
        key: `recent-${a.id}`,
        group: 'Recent',
        label: a.title,
        sub: [a.artistName, a.year].filter(Boolean).join(' · '),
        art: { kind: 'album', src: a.cover, name: a.title },
        run: () => go(`/albums/${a.id}`),
      }));
      return [...recents, ...actions];
    }
    const out: Option[] = [];
    if (results && q.length >= 2) {
      for (const a of results.artists ?? []) {
        out.push({ key: `artist-${a.id}`, group: 'Artists', label: a.name, art: { kind: 'artist', src: a.poster, name: a.name }, run: () => go(`/artists/${a.id}`) });
      }
      for (const a of results.albums ?? []) {
        out.push({
          key: `album-${a.id}`, group: 'Albums', label: a.title,
          sub: [a.artistName, a.year].filter(Boolean).join(' · '),
          art: { kind: 'album', src: a.cover, name: a.title },
          run: () => go(`/albums/${a.id}`),
        });
      }
      for (const t of results.tracks ?? []) {
        out.push({ key: `track-${t.id}`, group: 'Tracks', label: t.title, sub: t.artistName, icon: faAlignLeft, run: () => go(`/albums/${t.albumId}`) });
      }
    }
    const words = q.split(/\s+/);
    for (const a of actions) {
      const hay = `${a.label} ${a.keywords ?? ''}`.toLowerCase();
      if (words.every(w => hay.includes(w))) out.push(a);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trimmed, results, actions, recent.items]);

  // Keep the highlight in range and visible.
  useEffect(() => { setActive(0); }, [trimmed, results]);
  const activeIndex = Math.min(active, Math.max(0, options.length - 1));
  const activeId = options.length ? `${baseId}-opt-${activeIndex}` : undefined;
  useEffect(() => {
    if (!activeId) return;
    document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' });
  }, [activeId]);

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (options.length) setActive((activeIndex + 1) % options.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (options.length) setActive((activeIndex - 1 + options.length) % options.length);
    } else if (e.key === 'Home' && e.ctrlKey) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === 'End' && e.ctrlKey) {
      e.preventDefault();
      setActive(Math.max(0, options.length - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      options[activeIndex]?.run();
    }
  };

  const grouped: { group: Group; items: { option: Option; index: number }[] }[] = [];
  options.forEach((option, index) => {
    let g = grouped.find(x => x.group === option.group);
    if (!g) {
      g = { group: option.group, items: [] };
      grouped.push(g);
    }
    g.items.push({ option, index });
  });
  grouped.sort((a, b) => GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group));

  const searchHits = options.filter(o => o.group !== 'Actions').length;
  const showEmpty = trimmed.length >= 2 && !searching && results !== null && searchHits === 0;

  return (
    <div className={c.palette}>
      <div className={c.inputRow}>
        <FontAwesomeIcon icon={faMagnifyingGlass} className={c.inputIcon} />
        <input
          className={c.input}
          data-autofocus
          autoFocus
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={mobile ? 'Search or type a command' : 'Search artists, albums, tracks, or type a command'}
          aria-label="Search artists, albums, tracks, or type a command"
          role="combobox"
          aria-expanded={options.length > 0}
          aria-controls={`${baseId}-list`}
          aria-activedescendant={activeId}
          aria-autocomplete="list"
          autoComplete="off"
          spellCheck={false}
        />
        {searching && <Loader size={16} />}
        {/* Phones have no Esc key and the full-screen sheet covers the overlay: a visible Cancel closes it. */}
        {mobile
          ? <UnstyledButton className={c.cancel} onClick={onClose}>Cancel</UnstyledButton>
          : <Kbd className={c.esc}>Esc</Kbd>}
      </div>

      <div className={c.results} role="listbox" id={`${baseId}-list`} aria-label="Results">
        {showEmpty && (
          <div className={c.empty} role="presentation">
            <div className={c.emptyTitle}>No results for “{trimmed}”</div>
            <div className={c.emptyHint}>Search covers and lyrics providers from an album page.</div>
          </div>
        )}
        {grouped.map(({ group, items }) => (
          <div key={group} role="group" aria-labelledby={`${baseId}-g-${group}`} className={c.group}>
            <div className={c.groupLabel} id={`${baseId}-g-${group}`} role="presentation">{group}</div>
            {items.map(({ option, index }) => {
              const selected = index === activeIndex;
              const visual = option.art ? (
                <CoverArt
                  src={option.art.src}
                  alt=""
                  kind={option.art.kind}
                  name={option.art.name}
                  size={32}
                />
              ) : option.icon ? (
                <span className={c.iconTile}><FontAwesomeIcon icon={option.icon} /></span>
              ) : null;
              return (
                <div
                  key={option.key}
                  id={`${baseId}-opt-${index}`}
                  role="option"
                  aria-selected={selected}
                  className={c.option}
                  data-active={selected || undefined}
                  onMouseMove={() => { if (!selected) setActive(index); }}
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => option.run()}
                >
                  {visual}
                  <span className={c.optionText}>
                    <span className={c.optionLabel}>{option.label}</span>
                    {option.sub && <span className={c.optionSub}>{option.sub}</span>}
                  </span>
                  {option.current && (
                    <span className={c.current}>
                      <FontAwesomeIcon icon={faCheck} aria-hidden="true" />
                      In use
                    </span>
                  )}
                  {selected && !mobile && <Kbd className={c.enter} aria-hidden="true">Enter</Kbd>}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
