import { useEffect, useRef, type RefObject } from 'react';
import { getHotkeyHandler } from '@mantine/hooks';

/**
 * Keyboard shortcuts: the single source of truth (DESIGN.md 6.4). The shortcut
 * sheet renders SHORTCUTS; global keys are bound with useShortcut().
 *
 * Only entries with `hotkeys` are bound here. The list/wanted/editor keys are
 * handled by the components that own them (DataTable, FilterBar, Wanted, the
 * lyrics editor) and are listed for the sheet only.
 */
export interface Shortcut {
  id: string;
  /** Display form, e.g. "Ctrl K" or "g a". */
  keys: string;
  label: string;
  scope: 'global' | 'list' | 'wanted' | 'editor' | 'modal';
  /**
   * Bindings handled by useShortcut: Mantine hotkey strings ("mod+K"), single
   * printable characters ("/", "?", "[") matched by the produced character, or
   * two-key sequences ("g a", 1s window).
   */
  hotkeys?: string[];
}

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
/** "⌘" on macOS, "Ctrl" elsewhere. */
export const MOD_KEY = isMac ? '⌘' : 'Ctrl';

export const SHORTCUTS: Shortcut[] = [
  { id: 'palette', keys: `${MOD_KEY} K`, label: 'Open command palette', scope: 'global', hotkeys: ['mod+K'] },
  { id: 'palette-slash', keys: '/', label: 'Open command palette', scope: 'global', hotkeys: ['/'] },
  { id: 'shortcuts', keys: '?', label: 'Show keyboard shortcuts', scope: 'global', hotkeys: ['?'] },
  { id: 'sidebar', keys: '[', label: 'Collapse or expand the sidebar', scope: 'global', hotkeys: ['['] },
  { id: 'go-home', keys: 'g h', label: 'Go to Home', scope: 'global', hotkeys: ['g h'] },
  { id: 'go-artists', keys: 'g a', label: 'Go to Artists', scope: 'global', hotkeys: ['g a'] },
  { id: 'go-albums', keys: 'g l', label: 'Go to Albums', scope: 'global', hotkeys: ['g l'] },
  { id: 'go-wanted', keys: 'g w', label: 'Go to Wanted', scope: 'global', hotkeys: ['g w'] },
  { id: 'go-bulk', keys: 'g m', label: 'Go to Mass edit', scope: 'global', hotkeys: ['g m'] },
  { id: 'go-history', keys: 'g y', label: 'Go to History', scope: 'global', hotkeys: ['g y'] },
  { id: 'go-profiles', keys: 'g p', label: 'Go to Profiles', scope: 'global', hotkeys: ['g p'] },
  { id: 'go-settings', keys: 'g s', label: 'Go to Settings', scope: 'global', hotkeys: ['g s'] },
  { id: 'go-system', keys: 'g x', label: 'Go to System', scope: 'global', hotkeys: ['g x'] },

  { id: 'list-move', keys: 'j / k', label: 'Move the row cursor down / up (also ↓ / ↑)', scope: 'list' },
  { id: 'list-select', keys: 'x', label: 'Select or deselect the cursor row', scope: 'list' },
  { id: 'list-range', keys: 'Shift x', label: 'Select a range from the last selected row', scope: 'list' },
  { id: 'list-open', keys: 'Enter / o', label: 'Open the cursor row', scope: 'list' },
  { id: 'list-clear', keys: 'Esc', label: 'Clear the selection', scope: 'list' },
  { id: 'list-filter', keys: 'f', label: "Focus the page's filter", scope: 'list' },

  { id: 'wanted-search', keys: 's', label: 'Search providers for the cursor track', scope: 'wanted' },
  { id: 'wanted-candidate', keys: '1 – 9', label: 'Highlight candidate N', scope: 'wanted' },
  { id: 'wanted-apply', keys: 'Enter', label: 'Apply the highlighted candidate', scope: 'wanted' },
  { id: 'wanted-open', keys: 'o', label: 'Open the album', scope: 'wanted' },
  { id: 'wanted-edit', keys: 'e', label: 'Open the lyrics editor', scope: 'wanted' },
  { id: 'wanted-skip', keys: 'b', label: 'Skip the track', scope: 'wanted' },
  { id: 'wanted-expand', keys: '→ / ←', label: 'Expand / collapse the cursor album group', scope: 'wanted' },

  { id: 'editor-play', keys: 'Space', label: 'Play or pause the preview audio', scope: 'editor' },
  { id: 'modal-primary', keys: `${MOD_KEY} Enter`, label: "Run the dialog's main action", scope: 'modal' },
];

export const SHORTCUT_SCOPES: { scope: Shortcut['scope']; title: string }[] = [
  { scope: 'global', title: 'Anywhere' },
  { scope: 'list', title: 'Lists and tables' },
  { scope: 'wanted', title: 'Wanted' },
  { scope: 'editor', title: 'Lyrics editor' },
  { scope: 'modal', title: 'Dialogs' },
];

/* ------------------------------------------------------------------
   Dispatcher: one keydown listener for every bound shortcut.
   ------------------------------------------------------------------ */

type Handler = (e: KeyboardEvent) => void;
interface Binding { id: string; handler: RefObject<Handler> }

const bindings: Binding[] = [];
const SEQUENCE_WINDOW = 1000;
let pendingPrefix: { key: string; at: number } | null = null;
let installed = false;

const byId = new Map(SHORTCUTS.map(s => [s.id, s]));

/** Typing targets never trigger single-key shortcuts. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return target.getAttribute('role') === 'textbox' || target.getAttribute('role') === 'combobox';
}

/** Global shortcuts belong to no dialog, so they never fire from inside one. */
function inDialog(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest('[role="dialog"], [role="alertdialog"], [aria-modal="true"]');
}

function isPrintable(hotkey: string): boolean {
  return hotkey.length === 1;
}

function matches(hotkey: string, e: KeyboardEvent): boolean {
  if (isPrintable(hotkey)) {
    // Match the produced character ("?" is Shift+/ on US layouts, not elsewhere).
    return e.key === hotkey && !e.ctrlKey && !e.metaKey && !e.altKey;
  }
  // Mantine's parser (mod = Ctrl or ⌘), used as a matcher only.
  let hit = false;
  getHotkeyHandler([[hotkey, () => { hit = true; }, { preventDefault: false }]])(e);
  return hit;
}

function fire(id: string, e: KeyboardEvent): boolean {
  // The most recently mounted handler for an id wins (e.g. a page overriding a default).
  for (let i = bindings.length - 1; i >= 0; i--) {
    if (bindings[i].id === id) {
      e.preventDefault();
      bindings[i].handler.current?.(e);
      return true;
    }
  }
  return false;
}

function onKeyDown(e: KeyboardEvent) {
  if (e.defaultPrevented || e.isComposing) return;
  const typing = isTyping(e.target);
  const dialog = inDialog(e.target) || inDialog(document.activeElement);
  const bound = new Set(bindings.map(b => b.id));
  const plain = !e.ctrlKey && !e.metaKey && !e.altKey;

  // Second key of a sequence ("g a").
  if (pendingPrefix && Date.now() - pendingPrefix.at <= SEQUENCE_WINDOW && plain && !typing && !dialog) {
    const prefix = pendingPrefix.key;
    pendingPrefix = null;
    const combo = `${prefix} ${e.key.toLowerCase()}`;
    for (const id of bound) {
      if (byId.get(id)?.hotkeys?.includes(combo) && fire(id, e)) return;
    }
  }
  pendingPrefix = null;

  for (const id of bound) {
    const hotkeys = byId.get(id)?.hotkeys ?? [];
    for (const hk of hotkeys) {
      if (hk.includes(' ')) continue;
      const modified = !isPrintable(hk) && /(mod|ctrl|meta|alt)\+/i.test(hk);
      // Single keys never fire while typing or inside a dialog; modifier combos
      // (Ctrl/⌘ K) work from inputs but still not from inside another dialog.
      if (dialog || (typing && !modified)) continue;
      if (matches(hk, e)) {
        fire(id, e);
        return;
      }
    }
  }

  // Start of a sequence.
  if (plain && !typing && !dialog && e.key.length === 1) {
    const key = e.key.toLowerCase();
    const starts = [...bound].some(id => byId.get(id)?.hotkeys?.some(hk => hk.startsWith(`${key} `)));
    if (starts) pendingPrefix = { key, at: Date.now() };
  }
}

/**
 * True while the first key of a sequence ("g") was pressed and its second key is
 * still expected. Components with their own single-key handlers (DataTable,
 * Wanted) skip letters then, so "g x" reaches the shell.
 */
export function isSequencePending(): boolean {
  return !!pendingPrefix && Date.now() - pendingPrefix.at <= SEQUENCE_WINDOW;
}

function install() {
  if (installed || typeof document === 'undefined') return;
  installed = true;
  document.documentElement.addEventListener('keydown', onKeyDown);
}

/**
 * Binds a shortcut from SHORTCUTS by id for the lifetime of the caller. The latest
 * handler is always used, so it doesn't need to be memoized.
 */
export function useShortcut(id: string, handler: (e: KeyboardEvent) => void, enabled = true): void {
  const ref = useRef<Handler>(handler);
  ref.current = handler;
  useEffect(() => {
    if (!enabled) return;
    install();
    const binding: Binding = { id, handler: ref };
    bindings.push(binding);
    return () => {
      const i = bindings.indexOf(binding);
      if (i >= 0) bindings.splice(i, 1);
    };
  }, [id, enabled]);
}

/** Display keys of a shortcut, split for Kbd rendering ("Ctrl K" → ["Ctrl", "K"]). */
export function shortcutKeys(id: string): string[] {
  return (byId.get(id)?.keys ?? '').split(' ').filter(Boolean);
}
