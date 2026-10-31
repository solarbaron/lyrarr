import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

type UrlValue = string | number | boolean | null;

interface SetOptions {
  /** Replace the history entry (default true: typing and filter tweaks don't spam Back). */
  replace?: boolean;
}

function encode(value: UrlValue): string | null {
  if (value === null || value === '') return null;
  return String(value);
}

function decode<T extends UrlValue>(raw: string | null, fallback: T): T {
  if (raw === null) return fallback;
  if (typeof fallback === 'number') {
    const n = Number(raw);
    return (Number.isFinite(n) ? n : fallback) as T;
  }
  if (typeof fallback === 'boolean') return (raw === 'true' || raw === '1') as T;
  return raw as T;
}

/**
 * Builds the next query string from the *live* URL, so several setters called in
 * the same tick (e.g. a filter change plus a page reset) compose instead of
 * clobbering each other. Params equal to their default are removed.
 */
function nextSearch(patch: Record<string, { value: UrlValue; fallback: UrlValue }>): URLSearchParams {
  const params = new URLSearchParams(window.location.search);
  for (const [key, { value, fallback }] of Object.entries(patch)) {
    const enc = encode(value);
    if (enc === null || enc === encode(fallback)) params.delete(key);
    else params.set(key, enc);
  }
  return params;
}

/**
 * One piece of list state (filter, tab, sort, page, page size) kept in the URL
 * (DESIGN.md 7.5): bookmarkable, survives reload, Back works.
 *
 *   const [page, setPage] = useUrlState('page', 1);
 *   const [view, setView] = useUrlState('view', 'all');
 */
export function useUrlState<T extends UrlValue>(key: string, defaultValue: T): [T, (value: T, opts?: SetOptions) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get(key);
  const value = useMemo(() => decode(raw, defaultValue), [raw, defaultValue]);
  const set = useCallback((next: T, opts?: SetOptions) => {
    setParams(nextSearch({ [key]: { value: next, fallback: defaultValue } }), { replace: opts?.replace ?? true });
  }, [key, defaultValue, setParams]);
  return [value, set];
}

/**
 * Sets several URL params at once, e.g. a filter and `page: 1`:
 *
 *   const patchUrl = useUrlPatch();
 *   patchUrl({ view: ['missing', 'all'], page: [1, 1] });
 *
 * Each entry is `[value, default]`; values equal to the default are removed.
 */
export function useUrlPatch(): (patch: Record<string, [UrlValue, UrlValue]>, opts?: SetOptions) => void {
  const [, setParams] = useSearchParams();
  return useCallback((patch, opts) => {
    const mapped: Record<string, { value: UrlValue; fallback: UrlValue }> = {};
    for (const [k, [value, fallback]] of Object.entries(patch)) mapped[k] = { value, fallback };
    setParams(nextSearch(mapped), { replace: opts?.replace ?? true });
  }, [setParams]);
}
