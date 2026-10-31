import { Pagination, Select } from '@mantine/core';
import { num } from '../../format';
import c from './Pager.module.css';

export interface PagerProps {
  page: number;
  pageSize: number;
  total: number;
  onPage(p: number): void;
  /** Default [25, 50, 100]. */
  pageSizeOptions?: number[];
  /** Shows the "50 per page" select when given. */
  onPageSize?(n: number): void;
  /** Plural noun for "Showing 1–50 of 1,919 albums" (default "items"). */
  noun?: string;
  className?: string;
}

/** Table footer: range text, pagination, page size (DESIGN.md 7.6). */
export function Pager({ page, pageSize, total, onPage, pageSizeOptions = [25, 50, 100], onPageSize, noun = 'items', className }: PagerProps) {
  const pages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const current = Math.min(Math.max(1, page), pages);
  const from = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const to = Math.min(total, current * pageSize);
  return (
    <div className={[c.pager, className].filter(Boolean).join(' ')}>
      <span className={c.range} aria-live="polite">
        {total === 0 ? `No ${noun}` : <>Showing {num(from)}–{num(to)} of {num(total)} {noun}</>}
      </span>
      <div className={c.controls}>
        {pages > 1 && (
          <Pagination
            total={pages}
            value={current}
            onChange={onPage}
            siblings={1}
            boundaries={1}
            getControlProps={control => ({
              'aria-label': control === 'previous' ? 'Previous page' : control === 'next' ? 'Next page' : undefined,
            })}
          />
        )}
        {onPageSize && (
          <Select
            className={c.size}
            aria-label="Rows per page"
            value={String(pageSize)}
            data={pageSizeOptions.map(n => ({ value: String(n), label: `${n} per page` }))}
            onChange={v => { if (v) onPageSize(Number(v)); }}
            allowDeselect={false}
            comboboxProps={{ width: 140, position: 'top-end' }}
          />
        )}
      </div>
    </div>
  );
}
