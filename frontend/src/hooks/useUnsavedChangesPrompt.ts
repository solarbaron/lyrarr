import { useEffect, useRef } from 'react';
import { useHref, useNavigate } from 'react-router-dom';
import { useConfirm } from '../components/ui/ConfirmProvider';

/**
 * Asks before leaving a dirty form (DESIGN.md 7.8). The app uses <BrowserRouter>
 * (not a data router), so `useBlocker` is unavailable; instead this intercepts
 * in-app link clicks (capture phase, before React Router sees them) and warns on
 * tab close / reload via `beforeunload`.
 */
export function useUnsavedChangesPrompt(dirty: boolean, message = 'You have unsaved changes. Leave this page and discard them?'): void {
  const confirm = useConfirm();
  const navigate = useNavigate();
  const base = useHref('/');
  const state = useRef({ confirm, navigate, base, message });
  state.current = { confirm, navigate, base, message };

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return; // same page / hash
      e.preventDefault();
      e.stopPropagation();
      const { confirm: ask, navigate: go, base: prefix, message: msg } = state.current;
      const root = prefix.replace(/\/$/, '');
      const path = root && url.pathname.startsWith(root) ? url.pathname.slice(root.length) || '/' : url.pathname;
      void ask({ title: 'Discard unsaved changes?', message: msg, confirmLabel: 'Discard changes', tone: 'danger' })
        .then(ok => { if (ok) go(path + url.search + url.hash); });
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onClick, true);
    };
  }, [dirty]);
}
