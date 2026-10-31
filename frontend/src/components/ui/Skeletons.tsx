import type { CSSProperties, ReactNode } from 'react';
import { useDelayedFlag } from '../../hooks/useDelayedFlag';
import c from './Feedback.module.css';

/** Shown only after `delay` ms (default 150) so fast loads never flash (DESIGN.md 7.4). */
function Delayed({ delay = 150, children, minHeight }: { delay?: number; children: ReactNode; minHeight?: number | string }) {
  const show = useDelayedFlag(true, delay);
  if (!show) return minHeight ? <div style={{ minHeight }} aria-hidden="true" /> : null;
  return <>{children}</>;
}

export function Bone({ w = '100%', h = 12, r = 6, style, className }: { w?: number | string; h?: number | string; r?: number | string; style?: CSSProperties; className?: string }) {
  return <span className={[c.bone, 'ly-pulse', className].filter(Boolean).join(' ')} style={{ width: w, height: h, borderRadius: r, ...style }} />;
}

export type SkeletonColumnKind = 'title' | 'text' | 'chip' | 'num' | 'meter' | 'check' | 'actions';

export interface SkeletonColumn {
  header?: ReactNode;
  width?: number | string;
  kind?: SkeletonColumnKind;
  align?: 'left' | 'right' | 'center';
}

export interface TableSkeletonProps {
  rows?: number;
  /** Real header labels + cell kinds, or a column count. */
  columns?: SkeletonColumn[] | number;
  delay?: number;
  /** Render without the panel frame (inside an existing Panel / DataTable). */
  bare?: boolean;
  /**
   * Below 768px DataTable renders two-line cards (DESIGN.md 7.6), so by default the
   * skeleton does too. 'table' keeps the table shape on phones (fixed-width lists).
   */
  mobile?: 'cards' | 'table';
  'aria-label'?: string;
}

const BAR_WIDTHS = ['62%', '48%', '70%', '40%', '56%', '66%', '44%', '58%'];

function Cell({ kind, i }: { kind: SkeletonColumnKind; i: number }) {
  switch (kind) {
    case 'check':
      return <Bone w={16} h={16} r={4} />;
    case 'title':
      return (
        <span className={c.boneTitle}>
          <Bone w="var(--ly-thumb, 40px)" h="var(--ly-thumb, 40px)" r={6} style={{ flex: 'none' }} />
          <span className={c.boneLines}>
            <Bone w={BAR_WIDTHS[i % BAR_WIDTHS.length]} h={12} />
            <Bone w="34%" h={10} className={c.boneSecond} />
          </span>
        </span>
      );
    case 'chip':
      return <Bone w={76} h={20} r={999} />;
    case 'meter':
      return <Bone w={64} h={6} r={999} />;
    case 'num':
      return <Bone w={36} h={12} style={{ marginLeft: 'auto' }} />;
    case 'actions':
      return null;
    default:
      return <Bone w={BAR_WIDTHS[(i + 3) % BAR_WIDTHS.length]} h={12} />;
  }
}

/** Card-row placeholders matching DataTable's < 768px layout: thumb + title/meta lines + status pills. */
function CardRows({ rows, cols }: { rows: number; cols: SkeletonColumn[] }) {
  const hasThumb = cols.some(col => col.kind === 'title');
  const status = cols.filter(col => col.kind === 'chip' || col.kind === 'meter').slice(0, 2);
  const check = cols.some(col => col.kind === 'check');
  return (
    <ul className={c.skelCards} aria-hidden="true">
      {Array.from({ length: rows }).map((_, r) => (
        <li key={r} className={c.skelCardRow}>
          {check && <Bone w={16} h={16} r={4} style={{ marginTop: 2 }} />}
          {hasThumb && <Bone w="var(--ly-thumb, 40px)" h="var(--ly-thumb, 40px)" r={6} />}
          <span className={c.boneLines}>
            <Bone w={BAR_WIDTHS[r % BAR_WIDTHS.length]} h={12} />
            <Bone w={BAR_WIDTHS[(r + 3) % BAR_WIDTHS.length]} h={10} style={{ maxWidth: '50%' }} />
            {status.length > 0 && (
              <span className={c.boneRow} style={{ marginTop: 2 }}>
                {status.map((col, i) => col.kind === 'meter'
                  ? <Bone key={i} w={64} h={6} r={999} />
                  : <Bone key={i} w={68} h={18} r={999} />)}
              </span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Table placeholder: the real header plus rows with a thumb, 40–70% bars and chip pills. */
export function TableSkeleton({ rows = 10, columns = 4, delay, bare, mobile = 'cards', 'aria-label': ariaLabel = 'Loading' }: TableSkeletonProps) {
  const cols: SkeletonColumn[] = typeof columns === 'number'
    ? Array.from({ length: columns }, (_, i) => ({ kind: i === 0 ? 'title' : i === columns - 1 ? 'chip' : 'text' }))
    : columns;
  const hasHeader = cols.some(col => col.header !== undefined);
  const table = (
    <table className={c.skelTable} aria-hidden="true">
      <colgroup>
        {cols.map((col, i) => <col key={i} style={col.width !== undefined ? { width: col.width } : undefined} />)}
      </colgroup>
      {hasHeader && (
        <thead>
          <tr>{cols.map((col, i) => <th key={i} style={{ textAlign: col.align ?? 'left' }}>{col.header}</th>)}</tr>
        </thead>
      )}
      <tbody>
        {Array.from({ length: rows }).map((_, r) => (
          <tr key={r}>
            {cols.map((col, i) => <td key={i}><Cell kind={col.kind ?? 'text'} i={r + i} /></td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <Delayed delay={delay}>
      <div
        className={bare ? c.skelRoot : `${c.skelRoot} ${c.skelFrame}`}
        data-mobile={mobile}
        role="status"
        aria-label={ariaLabel}
        aria-busy="true"
      >
        {table}
        {mobile === 'cards' && <CardRows rows={rows} cols={cols} />}
      </div>
    </Delayed>
  );
}

/** Card placeholder (dashboard coverage card shape). */
export function CardSkeleton({ delay, height }: { delay?: number; height?: number }) {
  return (
    <Delayed delay={delay}>
      <div className={c.skelCard} role="status" aria-label="Loading" aria-busy="true" style={height ? { minHeight: height } : undefined}>
        <span className={c.boneRow}>
          <Bone w={28} h={28} r={8} />
          <Bone w="38%" h={14} />
        </span>
        <Bone w="30%" h={30} r={8} />
        <Bone w="100%" h={8} r={999} />
        <span className={c.boneRow}>
          <Bone w="40%" h={12} />
          <Bone w="35%" h={12} />
        </span>
      </div>
    </Delayed>
  );
}

/** Detail hero placeholder. */
export function HeroSkeleton({ delay, kind = 'album' }: { delay?: number; kind?: 'album' | 'artist' }) {
  const size = kind === 'artist' ? 180 : 220;
  return (
    <Delayed delay={delay}>
      <div className={c.skelHero} role="status" aria-label="Loading" aria-busy="true">
        <Bone w={size} h={size} r={kind === 'artist' ? '50%' : 16} style={{ flex: 'none' }} className={c.skelHeroArt} />
        <span className={c.boneLines} style={{ gap: 12, flex: 1 }}>
          <Bone w={160} h={11} />
          <Bone w="55%" h={32} r={8} />
          <Bone w="35%" h={14} />
          <span className={c.boneRow}>
            <Bone w={140} h={34} r={8} />
            <Bone w={120} h={34} r={8} />
          </span>
        </span>
      </div>
    </Delayed>
  );
}

/** Cover grid placeholder. Never fewer than two columns (phones get ~170px tiles, not one huge one). */
export function GridSkeleton({ count = 12, delay, min = 168 }: { count?: number; delay?: number; min?: number }) {
  return (
    <Delayed delay={delay}>
      <div className={c.skelGrid} style={{ gridTemplateColumns: `repeat(auto-fill, minmax(min(${min}px, calc(50% - 8px)), 1fr))` }} role="status" aria-label="Loading" aria-busy="true">
        {Array.from({ length: count }).map((_, i) => (
          <span key={i} className={c.boneLines} style={{ gap: 8 }}>
            <Bone w="100%" h="auto" r={16} style={{ aspectRatio: '1 / 1' }} />
            <Bone w={BAR_WIDTHS[i % BAR_WIDTHS.length]} h={13} />
            <Bone w="45%" h={11} />
          </span>
        ))}
      </div>
    </Delayed>
  );
}

/** Simple list placeholder (tree rows, activity, settings). */
export function ListSkeleton({ rows = 8, delay, avatar = true }: { rows?: number; delay?: number; avatar?: boolean }) {
  return (
    <Delayed delay={delay}>
      <div className={c.skelList} role="status" aria-label="Loading" aria-busy="true">
        {Array.from({ length: rows }).map((_, i) => (
          <span key={i} className={c.boneRow} style={{ minHeight: 40 }}>
            {avatar && <Bone w={24} h={24} r="50%" style={{ flex: 'none' }} />}
            <Bone w={BAR_WIDTHS[i % BAR_WIDTHS.length]} h={12} />
          </span>
        ))}
      </div>
    </Delayed>
  );
}
