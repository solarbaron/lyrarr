interface SkeletonProps {
  height?: number | string;
  width?: number | string;
  radius?: number;
  mb?: number;
}

/** A single shimmering placeholder block. */
export function Skeleton({ height = 16, width = '100%', radius = 8, mb = 0 }: SkeletonProps) {
  return <div className="skel" style={{ height, width, borderRadius: radius, marginBottom: mb }} />;
}

/** Placeholder grid matching the album/artist card layout. */
export function SkeletonGrid({ count = 12 }: { count?: number }) {
  return (
    <div className="album-grid">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skel-card">
          <div className="skel" style={{ width: '100%', aspectRatio: '1', borderRadius: 0 }} />
          <div style={{ padding: 12 }}>
            <Skeleton height={13} width="85%" mb={8} />
            <Skeleton height={11} width="55%" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Placeholder rows matching a data table. */
export function SkeletonTable({ rows = 8 }: { rows?: number }) {
  return (
    <div className="table-wrap" style={{ padding: '8px 0' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '13px 16px' }}
        >
          <Skeleton height={18} width={18} radius={6} />
          <Skeleton height={14} width={`${40 + (i % 4) * 12}%`} />
          <div style={{ flex: 1 }} />
          <Skeleton height={22} width={70} radius={11} />
        </div>
      ))}
    </div>
  );
}
