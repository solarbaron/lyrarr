import {
  Fragment, isValidElement, useCallback, useEffect, useMemo, useRef, useState,
  type KeyboardEvent, type MouseEvent, type ReactNode,
} from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, Checkbox, Popover, Table } from '@mantine/core';
import { useElementSize, useMediaQuery } from '@mantine/hooks';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowDown, faArrowUp, faCircleExclamation, faEllipsis, faRotateRight } from '@fortawesome/free-solid-svg-icons';
import { num } from '../../format';
import { CoverArt, type CoverArtProps } from './CoverArt';
import { EmptyState } from './EmptyState';
import { IconButton } from './IconButton';
import { TableSkeleton, type SkeletonColumnKind } from './Skeletons';
import { LangTag } from './Tag';
import { isSequencePending } from '../../shortcuts';
import c from './DataTable.module.css';

const PLACEHOLDERS = new Set(['', '—', '–', '-']);

/**
 * True when a cell renders only an empty placeholder ("—", null, an empty
 * LangTag), so the mobile card meta line can drop it instead of showing
 * "Crystal Castles · 2012 · —".
 */
function isBlankCell(node: ReactNode): boolean {
  if (node === null || node === undefined || typeof node === 'boolean') return true;
  if (typeof node === 'string') return PLACEHOLDERS.has(node.trim());
  if (Array.isArray(node)) return node.every(isBlankCell);
  if (isValidElement<{ children?: ReactNode; code?: unknown }>(node)) {
    if (node.type === LangTag) return !node.props.code;
    if (typeof node.type === 'string' || node.type === Fragment) return isBlankCell(node.props.children);
  }
  return false;
}

/* ------------------------------------------------------------------
   Types (DESIGN.md 7.6)
   ------------------------------------------------------------------ */

/** Layout facts a cell can adapt to (e.g. show the artist under the title once its column hides). */
export interface CellContext {
  /** Keys of columns hidden at the current container width (empty in the mobile card layout). */
  hidden: ReadonlySet<string>;
  /** True in the < 768px two-line card layout. */
  mobile: boolean;
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  width?: number | string;
  align?: 'left' | 'right' | 'center';
  /** 1 = always shown; 2 hidden on narrow containers; 3 hidden first. Status columns are always 1. */
  priority?: 1 | 2 | 3;
  /** Header becomes a sort button. */
  sortKey?: string;
  cell: (row: T, ctx: CellContext) => ReactNode;
  /** Placement in the < 768px two-line card layout (default: first column title unless another column claims it, others meta). */
  mobile?: 'title' | 'meta' | 'status' | 'hidden';
  /** Placeholder shape while loading (default: first column title, others text). */
  skeleton?: SkeletonColumnKind;
  /** Extra class on this column's cells. */
  className?: string;
}

export interface DataTableProps<T> {
  'aria-label': string;
  columns: Column<T>[];
  rows: T[];
  getRowId: (row: T) => number;
  loading?: boolean;
  /** Dims rows to 60% while new data loads (keepPreviousData). */
  refetching?: boolean;
  /** Usually <EmptyState size="sm" />. */
  empty?: ReactNode;
  /** Load error: an inline EmptyState with Retry replaces the rows. */
  error?: { title?: string; message?: ReactNode; onRetry?: () => void } | null;
  /** Row click / Enter navigates here; render the title with <TitleCell href=…> so it is a real link. */
  rowHref?: (row: T) => string;
  /** Enter / double-click when there is no rowHref. */
  onOpen?: (row: T) => void;
  /** Rows that have nothing to open (no pointer cursor, Enter / double-click do nothing). Default: all rows open. */
  isOpenable?: (row: T) => boolean;
  selection?: { selected: Set<number>; onChange: (next: Set<number>) => void };
  selectAllMatching?: { total: number; onSelectAll: () => Promise<void> | void; busy?: boolean };
  /** Controlled keyboard cursor (e.g. the Wanted inspector follows it). Uncontrolled when omitted. */
  cursor?: { id: number | null; onChange: (id: number | null) => void };
  /** IconButtons revealed on hover / focus / cursor; always visible on touch. */
  rowActions?: (row: T) => ReactNode;
  /** Width of the row actions column (default 112). */
  rowActionsWidth?: number;
  sort?: { key: string; dir: 'asc' | 'desc'; onChange: (key: string, dir: 'asc' | 'desc') => void };
  /** Usually <Pager />. */
  footer?: ReactNode;
  stickyHeader?: boolean;
  /** Full-width header row before a row (disc headers, day groups, album groups). */
  renderGroupHeader?: (row: T) => ReactNode | null;
  expandable?: { isExpanded(row: T): boolean; toggle(row: T): void; render(row: T): ReactNode };
  /** Accessible row name for checkboxes ("Select Bossanova"). */
  getRowLabel?: (row: T) => string;
  /** Singular noun for the select-all banner ("album"). */
  noun?: string;
  /**
   * Container-width thresholds below which priority 2 / 3 columns hide (default 860 / 1000,
   * calibrated for full-width lists: Albums at 1280px viewport hides priority 3). Columns
   * also hide, 3 first, whenever they would not fit, so the table never scrolls sideways.
   * Narrow panels (Album detail tracks, Wanted with inspector) pass lower values.
   */
  breakpoints?: { 2?: number; 3?: number };
  /** Wrap in a panel frame (default true). Set false inside an existing Panel padding="none". */
  framed?: boolean;
  /** Extra attributes for a row (e.g. data-state). */
  rowProps?: (row: T) => Record<string, string | number | boolean | undefined>;
  className?: string;
}

/* ------------------------------------------------------------------
   Title cell helper: thumb + title link + optional second line
   ------------------------------------------------------------------ */

export interface TitleCellProps {
  title: string;
  /** Makes the title a real <Link> (the row's primary link). */
  href?: string;
  /** Second line, 12.5 muted; hidden in compact density. */
  secondary?: ReactNode;
  /** Thumbnail art (decorative: alt "" because the title is adjacent). */
  art?: Omit<CoverArtProps, 'size' | 'alt'> & { alt?: string };
  /** Anything else after the title (tags). */
  extra?: ReactNode;
}

export function TitleCell({ title, href, secondary, art, extra }: TitleCellProps) {
  return (
    <div className={c.titleCell}>
      {art && (
        <span className={c.thumb}>
          <CoverArt {...art} alt={art.alt ?? ''} size="fill" radius={art.shape === 'circle' ? undefined : 6} />
        </span>
      )}
      <div className={c.titleText}>
        <div className={c.titleLine}>
          {href
            ? <Link to={href} className={c.titleLink} title={title} data-row-link="">{title}</Link>
            : <span className={c.titlePlain} title={title}>{title}</span>}
          {extra}
        </div>
        {secondary && <div className={c.secondary}>{secondary}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
   Helpers
   ------------------------------------------------------------------ */

const INTERACTIVE = 'a, button, input, select, textarea, label, [role="button"], [role="menuitem"], [role="checkbox"], [role="switch"], [data-no-row-click]';

function isTypingTarget(el: HTMLElement): boolean {
  if (el.isContentEditable) return true;
  if (el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') return true;
  if (el.tagName === 'INPUT') {
    const type = (el as HTMLInputElement).type;
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range'].includes(type);
  }
  return false;
}

function isInteractive(target: EventTarget | null, row: HTMLElement): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== 'function') return false;
  // React bubbles events from portals (row-action menus, the mobile ⋯ popover) through
  // the row: a click there is never a row click.
  if (!row.contains(el)) return true;
  const hit = el.closest(INTERACTIVE);
  return !!hit && row.contains(hit) && hit !== row;
}

/** Minimum width of a column without an explicit width (the title column). */
const MIN_FLEX = 160;

function plural(n: number, noun: string) {
  return `${num(n)} ${n === 1 ? noun : `${noun}s`}`;
}

/* ------------------------------------------------------------------
   DataTable
   ------------------------------------------------------------------ */

/**
 * The list/table primitive (DESIGN.md 7.6): sticky header, priority-based column
 * hiding (no horizontal scroll ≥ 1024px), selection with shift ranges, a keyboard
 * cursor (j/k/↑/↓, x / Shift+x, Enter/o, Esc), hover row actions, select-all-
 * matching banner, loading/refetching/empty/error states and a two-line card
 * layout below 768px. Cursor = focus outline; selection = fill + accent edge.
 */
export function DataTable<T>(props: DataTableProps<T>) {
  const {
    columns, rows, getRowId, loading, refetching, empty, error, rowHref, onOpen, isOpenable, selection, selectAllMatching,
    cursor, rowActions, rowActionsWidth = 120, sort, footer, stickyHeader = true, renderGroupHeader, expandable,
    getRowLabel, noun = 'row', breakpoints, framed = true, rowProps, className,
    'aria-label': ariaLabel,
  } = props;

  const navigate = useNavigate();
  const { ref: sizeRef, width } = useElementSize<HTMLDivElement>();
  const isMobile = useMediaQuery('(max-width: 767px)') ?? false;
  const [innerCursor, setInnerCursor] = useState<number | null>(null);
  // Mobile cards: the row whose ⋯ actions popover is open (closes after any action inside it).
  const [actionsFor, setActionsFor] = useState<number | null>(null);
  const cursorId = cursor ? cursor.id : innerCursor;
  const setCursor = cursor ? cursor.onChange : setInnerCursor;
  const anchor = useRef<number | null>(null);
  const rowEls = useRef(new Map<number, HTMLElement>());

  const ids = useMemo(() => rows.map(getRowId), [rows, getRowId]);
  const p2 = breakpoints?.[2] ?? 860;
  const p3 = breakpoints?.[3] ?? 1000;
  const visible = useMemo(() => {
    if (!width) return columns; // not measured yet
    const fixed = (selection ? 44 : 0) + (rowActions ? rowActionsWidth : 0);
    // Minimum width a set of columns needs: fixed widths, percentages of the
    // container, and MIN_FLEX for each column without a width.
    const need = (cols: Column<T>[]) => cols.reduce((sum, col) => {
      if (typeof col.width === 'number') return sum + col.width;
      if (typeof col.width === 'string' && col.width.endsWith('%')) return sum + (parseFloat(col.width) / 100) * width;
      if (typeof col.width === 'string' && col.width.endsWith('px')) return sum + parseFloat(col.width);
      return sum + MIN_FLEX;
    }, fixed);
    let cols = columns.filter(col => col.priority !== 3 || width >= p3);
    cols = cols.filter(col => col.priority !== 2 || width >= p2);
    // Never overflow: drop priority 3, then 2, while the columns don't fit.
    if (need(cols) > width) cols = cols.filter(col => col.priority !== 3);
    if (need(cols) > width) cols = cols.filter(col => col.priority !== 2);
    return cols;
  }, [columns, width, p2, p3, selection, rowActions, rowActionsWidth]);

  const tableCtx = useMemo<CellContext>(() => ({
    hidden: new Set(columns.filter(col => !visible.includes(col)).map(col => col.key)),
    mobile: false,
  }), [columns, visible]);
  const cardCtx = useMemo<CellContext>(() => ({ hidden: new Set(), mobile: true }), []);

  const selected = selection?.selected;
  const pageSelectedCount = selected ? ids.filter(id => selected.has(id)).length : 0;
  const allOnPage = ids.length > 0 && pageSelectedCount === ids.length;
  const someOnPage = pageSelectedCount > 0 && !allOnPage;

  const rowById = useCallback((id: number) => rows[ids.indexOf(id)], [rows, ids]);

  const canOpen = useCallback(
    (row: T) => !!(rowHref || onOpen) && (isOpenable?.(row) ?? true),
    [rowHref, onOpen, isOpenable],
  );

  const open = useCallback((row: T) => {
    if (!canOpen(row)) return;
    if (rowHref) navigate(rowHref(row));
    else onOpen?.(row);
  }, [rowHref, onOpen, navigate, canOpen]);

  const toggle = useCallback((id: number, range: boolean) => {
    if (!selection) return;
    const next = new Set(selection.selected);
    const turnOn = !next.has(id);
    const from = anchor.current !== null ? ids.indexOf(anchor.current) : -1;
    const to = ids.indexOf(id);
    if (range && from >= 0 && to >= 0) {
      const [a, b] = from < to ? [from, to] : [to, from];
      for (let i = a; i <= b; i++) {
        if (turnOn) next.add(ids[i]);
        else next.delete(ids[i]);
      }
    } else if (turnOn) {
      next.add(id);
    } else {
      next.delete(id);
    }
    anchor.current = id;
    selection.onChange(next);
  }, [selection, ids]);

  const togglePage = useCallback(() => {
    if (!selection) return;
    const next = new Set(selection.selected);
    if (allOnPage) ids.forEach(id => next.delete(id));
    else ids.forEach(id => next.add(id));
    selection.onChange(next);
  }, [selection, ids, allOnPage]);

  const focusRow = (id: number) => {
    const el = rowEls.current.get(id);
    if (el) {
      el.focus({ preventScroll: true });
      el.scrollIntoView({ block: 'nearest' });
    }
  };

  const moveTo = (index: number) => {
    if (ids.length === 0) return;
    const i = Math.max(0, Math.min(ids.length - 1, index));
    setCursor(ids[i]);
    focusRow(ids[i]);
  };

  // j/k from the page itself (nothing focused, e.g. right after load) move the
  // cursor into the first list on the page; from then on onKeyDown below owns
  // every list key. Pages with their own page-level j/k (Wanted) claim the
  // event first with preventDefault.
  const pageKey = useRef<(e: globalThis.KeyboardEvent) => void>(() => undefined);
  pageKey.current = e => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.key !== 'j' && e.key !== 'k') return;
    const target = e.target as HTMLElement | null;
    const fromPage = target === document.body || target === document.documentElement || target?.tagName === 'MAIN';
    if (!fromPage || isSequencePending() || loading || ids.length === 0) return;
    const root = sizeRef.current;
    if (!root || document.querySelector('[data-ly-list]') !== root) return;
    e.preventDefault();
    const idx = cursorId === null ? -1 : ids.indexOf(cursorId);
    moveTo(idx < 0 ? 0 : idx + (e.key === 'j' ? 1 : -1));
  };
  useEffect(() => {
    const onPageKey = (e: globalThis.KeyboardEvent) => pageKey.current(e);
    document.addEventListener('keydown', onPageKey);
    return () => document.removeEventListener('keydown', onPageKey);
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const target = e.target as HTMLElement;
    if (isTypingTarget(target)) return;
    // The second key of a global "g x" sequence belongs to the shell (g x = System).
    if (e.key.length === 1 && isSequencePending()) return;
    const idx = cursorId === null ? -1 : ids.indexOf(cursorId);
    const onRowItself = target.hasAttribute('data-row');
    switch (e.key) {
      case 'j':
      case 'ArrowDown':
        moveTo(idx < 0 ? 0 : idx + 1);
        break;
      case 'k':
      case 'ArrowUp':
        moveTo(idx < 0 ? 0 : idx - 1);
        break;
      case 'Home':
        if (!onRowItself) return;
        moveTo(0);
        break;
      case 'End':
        if (!onRowItself) return;
        moveTo(ids.length - 1);
        break;
      case 'x':
      case 'X':
        if (!selection || cursorId === null) return;
        toggle(cursorId, e.shiftKey);
        break;
      case 'Enter':
      case 'o':
        // Links and buttons inside the row keep their own Enter.
        if (!onRowItself || cursorId === null) return;
        { const row = rowById(cursorId); if (row) open(row); }
        break;
      case 'ArrowRight':
      case 'ArrowLeft': {
        if (!expandable || cursorId === null || !onRowItself) return;
        const row = rowById(cursorId);
        if (!row) return;
        const isOpen = expandable.isExpanded(row);
        if ((e.key === 'ArrowRight') !== isOpen) expandable.toggle(row);
        break;
      }
      case 'Escape':
        if (selection && selection.selected.size > 0) {
          selection.onChange(new Set());
        } else {
          (document.activeElement as HTMLElement | null)?.blur();
        }
        break;
      default:
        return;
    }
    e.preventDefault();
  };

  const onRowClick = (e: MouseEvent<HTMLElement>, row: T, id: number) => {
    if (isInteractive(e.target, e.currentTarget)) return;
    if (window.getSelection()?.toString()) return;
    setCursor(id);
    if (rowHref && canOpen(row) && !e.metaKey && !e.ctrlKey && !e.shiftKey) navigate(rowHref(row));
  };

  const onRowDoubleClick = (e: MouseEvent<HTMLElement>, row: T) => {
    if (rowHref || !onOpen || !canOpen(row) || isInteractive(e.target, e.currentTarget)) return;
    onOpen(row);
  };

  const registerRow = (id: number) => (el: HTMLElement | null) => {
    if (el) rowEls.current.set(id, el);
    else rowEls.current.delete(id);
  };

  // Roving tab stop: the cursor row (or the first row) is the table's single tab stop.
  const tabStop = cursorId !== null && ids.includes(cursorId) ? cursorId : ids[0];

  /* ---------- banner ---------- */
  let banner: ReactNode = null;
  if (selection && selectAllMatching && allOnPage && selectAllMatching.total > rows.length) {
    const all = selection.selected.size >= selectAllMatching.total;
    banner = (
      <div className={c.banner} role="status">
        {all ? (
          <>
            <span>All {plural(selectAllMatching.total, noun)} are selected.</span>
            <Button variant="subtle" size="xs" onClick={() => selection.onChange(new Set())}>Clear selection</Button>
          </>
        ) : (
          <>
            <span>All {num(rows.length)} on this page are selected.</span>
            <Button variant="subtle" size="xs" loading={selectAllMatching.busy} onClick={() => void selectAllMatching.onSelectAll()}>
              Select all {num(selectAllMatching.total)} matching
            </Button>
          </>
        )}
      </div>
    );
  }

  /* ---------- states ---------- */
  const colCount = visible.length + (selection ? 1 : 0) + (rowActions ? 1 : 0);
  let stateBody: ReactNode = null;
  if (error) {
    stateBody = (
      <EmptyState
        size="sm"
        tone="danger"
        icon={faCircleExclamation}
        title={error.title ?? `Couldn't load ${noun}s`}
        message={error.message}
        action={error.onRetry && (
          <Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} onClick={error.onRetry}>Retry</Button>
        )}
      />
    );
  } else if (!loading && rows.length === 0) {
    stateBody = empty ?? null;
  }

  const skeletonColumns = [
    ...(selection ? [{ kind: 'check' as const, width: 44 }] : []),
    ...visible.map((col, i) => ({
      header: col.header, width: col.width, align: col.align,
      kind: col.skeleton ?? (i === 0 ? 'title' as const : 'text' as const),
    })),
    ...(rowActions ? [{ kind: 'actions' as const, width: rowActionsWidth }] : []),
  ];

  /* ---------- mobile: two-line cards ---------- */
  const renderCards = () => {
    const explicitTitle = columns.some(col => col.mobile === 'title');
    const placement = (col: Column<T>, i: number) => col.mobile ?? (i === 0 && !explicitTitle ? 'title' : 'meta');
    const titleCols = columns.filter((col, i) => placement(col, i) === 'title');
    const metaCols = columns.filter((col, i) => placement(col, i) === 'meta');
    const statusCols = columns.filter((col, i) => placement(col, i) === 'status');
    return (
      <ul className={c.cards} aria-label={ariaLabel} data-refetching={refetching || undefined}>
        {rows.map(row => {
          const id = getRowId(row);
          const isSel = !!selected?.has(id);
          const group = renderGroupHeader?.(row);
          return (
            <Fragment key={id}>
              {group != null && <li className={c.cardGroup}>{group}</li>}
              <li
                ref={registerRow(id)}
                className={c.card}
                data-row=""
                data-selected={isSel || undefined}
                data-cursor={cursorId === id || undefined}
                data-clickable={canOpen(row) || undefined}
                tabIndex={id === tabStop ? 0 : -1}
                onFocus={e => { if (e.target === e.currentTarget) setCursor(id); }}
                onClick={e => onRowClick(e, row, id)}
                {...rowProps?.(row)}
              >
                {selection && (
                  <Checkbox
                    className={c.cardCheck}
                    checked={isSel}
                    onChange={() => undefined}
                    onClick={e => toggle(id, e.shiftKey)}
                    aria-label={getRowLabel ? `Select ${getRowLabel(row)}` : 'Select row'}
                  />
                )}
                <div className={c.cardMain}>
                  <div className={c.cardLine1}>
                    {titleCols.map(col => <div key={col.key} className={c.cardTitle}>{col.cell(row, cardCtx)}</div>)}
                  </div>
                  {(() => {
                    const meta = metaCols
                      .map(col => ({ key: col.key, node: col.cell(row, cardCtx) }))
                      .filter(m => !isBlankCell(m.node));
                    return meta.length > 0 && (
                      <div className={c.cardMeta}>
                        {meta.map(m => <span key={m.key} className={c.cardMetaItem}>{m.node}</span>)}
                      </div>
                    );
                  })()}
                  {statusCols.length > 0 && (
                    <div className={c.cardStatus}>
                      {statusCols.map(col => <span key={col.key}>{col.cell(row, cardCtx)}</span>)}
                    </div>
                  )}
                  {expandable?.isExpanded(row) && <div className={c.cardExpand}>{expandable.render(row)}</div>}
                </div>
                {rowActions && (
                  <Popover
                    position="bottom-end"
                    withinPortal
                    opened={actionsFor === id}
                    onChange={o => setActionsFor(o ? id : null)}
                    returnFocus
                  >
                    <Popover.Target>
                      <IconButton
                        icon={faEllipsis}
                        label="Actions"
                        className={c.cardMore}
                        onClick={() => setActionsFor(actionsFor === id ? null : id)}
                      />
                    </Popover.Target>
                    {/* Any action closes the popover, so it never sits above a modal the action opens.
                        Menu triggers inside stay open; their (portalled) items bubble here through React. */}
                    <Popover.Dropdown
                      className={c.cardActions}
                      onClick={e => {
                        const hit = (e.target as HTMLElement).closest('button, a, [role="menuitem"]');
                        if (hit && !hit.hasAttribute('aria-haspopup')) setActionsFor(null);
                      }}
                    >
                      {rowActions(row)}
                    </Popover.Dropdown>
                  </Popover>
                )}
              </li>
            </Fragment>
          );
        })}
      </ul>
    );
  };

  /* ---------- desktop table ---------- */
  const renderTable = () => (
    <Table
      className={c.table}
      stickyHeader={stickyHeader}
      highlightOnHover
      aria-label={ariaLabel}
      aria-busy={loading || refetching || undefined}
      data-sticky={stickyHeader || undefined}
    >
      <colgroup>
        {selection && <col style={{ width: 44 }} />}
        {visible.map(col => <col key={col.key} style={col.width !== undefined ? { width: col.width } : undefined} />)}
        {rowActions && <col style={{ width: rowActionsWidth }} />}
      </colgroup>
      <Table.Thead>
        <Table.Tr>
          {selection && (
            <Table.Th className={c.checkCell}>
              <Checkbox
                checked={allOnPage}
                indeterminate={someOnPage}
                disabled={rows.length === 0}
                onChange={togglePage}
                aria-label={allOnPage ? 'Deselect all on this page' : 'Select all on this page'}
              />
            </Table.Th>
          )}
          {visible.map(col => {
            const active = sort && col.sortKey && sort.key === col.sortKey;
            return (
              <Table.Th
                key={col.key}
                style={{ textAlign: col.align ?? 'left' }}
                aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                data-sorted={active || undefined}
              >
                {sort && col.sortKey ? (
                  <button
                    type="button"
                    className={c.sortButton}
                    data-align={col.align}
                    onClick={() => sort.onChange(col.sortKey!, active && sort.dir === 'asc' ? 'desc' : 'asc')}
                  >
                    {col.header}
                    {active && <FontAwesomeIcon icon={sort.dir === 'asc' ? faArrowUp : faArrowDown} className={c.sortIcon} />}
                  </button>
                ) : col.header}
              </Table.Th>
            );
          })}
          {rowActions && <Table.Th className={c.actionsHead}><span className="visually-hidden">Actions</span></Table.Th>}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody data-refetching={refetching || undefined}>
        {banner && (
          <Table.Tr className={c.bannerRow}>
            <Table.Td colSpan={colCount}>{banner}</Table.Td>
          </Table.Tr>
        )}
        {rows.map(row => {
          const id = getRowId(row);
          const isSel = !!selected?.has(id);
          const isCursor = cursorId === id;
          const group = renderGroupHeader?.(row);
          const expanded = expandable?.isExpanded(row);
          return (
            <Fragment key={id}>
              {group != null && (
                <Table.Tr className={c.groupRow}>
                  <Table.Th colSpan={colCount} scope="colgroup" className={c.groupCell}>{group}</Table.Th>
                </Table.Tr>
              )}
              <Table.Tr
                ref={registerRow(id)}
                className={c.row}
                data-row=""
                data-selected={isSel || undefined}
                data-cursor={isCursor || undefined}
                data-clickable={canOpen(row) || undefined}
                aria-selected={selection ? isSel : undefined}
                aria-expanded={expandable ? !!expanded : undefined}
                tabIndex={id === tabStop ? 0 : -1}
                onFocus={() => { if (cursorId !== id) setCursor(id); }}
                onClick={e => onRowClick(e, row, id)}
                onDoubleClick={e => onRowDoubleClick(e, row)}
                {...rowProps?.(row)}
              >
                {selection && (
                  <Table.Td className={c.checkCell}>
                    <Checkbox
                      checked={isSel}
                      onChange={() => undefined}
                      onClick={e => toggle(id, e.shiftKey)}
                      aria-label={getRowLabel ? `Select ${getRowLabel(row)}` : 'Select row'}
                      tabIndex={-1}
                    />
                  </Table.Td>
                )}
                {visible.map(col => (
                  <Table.Td
                    key={col.key}
                    className={col.className}
                    style={col.align ? { textAlign: col.align } : undefined}
                    data-align={col.align}
                  >
                    {col.cell(row, tableCtx)}
                  </Table.Td>
                ))}
                {rowActions && (
                  <Table.Td className={c.actionsCell}>
                    <div className={c.actions}>{rowActions(row)}</div>
                  </Table.Td>
                )}
              </Table.Tr>
              {expanded && (
                <Table.Tr className={c.expandRow}>
                  <Table.Td colSpan={colCount} className={c.expandCell}>{expandable!.render(row)}</Table.Td>
                </Table.Tr>
              )}
            </Fragment>
          );
        })}
        {stateBody && (
          <Table.Tr className={c.stateRow}>
            <Table.Td colSpan={colCount}>{stateBody}</Table.Td>
          </Table.Tr>
        )}
      </Table.Tbody>
    </Table>
  );

  let body: ReactNode;
  if (loading && !error) {
    body = <TableSkeleton bare rows={10} columns={skeletonColumns} aria-label={`Loading ${ariaLabel}`} />;
  } else if (isMobile) {
    body = (
      <>
        {banner}
        {rows.length > 0 && renderCards()}
        {stateBody}
      </>
    );
  } else {
    body = renderTable();
  }

  return (
    <div
      ref={sizeRef}
      data-ly-list=""
      className={[c.root, framed ? c.framed : null, className].filter(Boolean).join(' ')}
      onKeyDown={onKeyDown}
    >
      {body}
      {footer && <div className={c.footer}>{footer}</div>}
    </div>
  );
}
