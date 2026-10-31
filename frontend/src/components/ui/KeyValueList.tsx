import { Fragment, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import c from './DataDisplay.module.css';

export interface KeyValueItem {
  label: string;
  value: ReactNode;
  /** "profile: Default", "inherited", "override". */
  source?: string;
  mono?: boolean;
  href?: string;
}

export interface KeyValueListProps {
  items: KeyValueItem[];
  columns?: 1 | 2;
  className?: string;
}

/** Paths and URLs break after "/" (or "\\") rather than mid-word (DESIGN.md 7.7). */
function breakable(value: ReactNode): ReactNode {
  if (typeof value !== 'string') return value;
  const parts = value.split(/(?<=[/\\])/);
  if (parts.length < 2) return value;
  return parts.map((part, i) => <Fragment key={i}>{part}{i < parts.length - 1 && <wbr />}</Fragment>);
}

/** Label/value rows for Status, Settings and About panels (DESIGN.md 7.7). */
export function KeyValueList({ items, columns = 1, className }: KeyValueListProps) {
  return (
    <dl className={[c.kv, className].filter(Boolean).join(' ')} data-columns={columns}>
      {items.map((item, i) => (
        <div className={c.kvRow} key={`${item.label}-${i}`}>
          <dt className={c.kvLabel}>{item.label}</dt>
          <dd className={c.kvValue}>
            <span className={item.mono ? c.kvMono : c.kvText}>
              {(() => {
                const value = item.mono ? breakable(item.value) : item.value;
                return item.href ? <Link to={item.href} className="ly-link">{value}</Link> : value;
              })()}
            </span>
            {item.source && <span className={c.kvSource}>{item.source}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}
