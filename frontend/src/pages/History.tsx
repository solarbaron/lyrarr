import { useQuery } from '@tanstack/react-query';
import { getHistory } from '../api';
import PageHeader from '../components/PageHeader';
import { SkeletonTable } from '../components/Skeleton';

const actionLabels: Record<number, string> = {
  1: 'Downloaded',
  2: 'Upgraded',
  3: 'Deleted',
  4: 'Manual',
};

export default function HistoryPage() {
  const { data: history = [], isLoading } = useQuery({ queryKey: ['history'], queryFn: getHistory });

  return (
    <div className="fade-in">
      <PageHeader title="History" subtitle="Recent metadata actions" />

      {isLoading ? (
        <SkeletonTable rows={8} />
      ) : history.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">📋</div>
          <div className="empty-state-title">No History</div>
          <div className="empty-state-message">Metadata actions will appear here.</div>
        </div>
      ) : (
        <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Action</th>
              <th>Type</th>
              <th>Description</th>
              <th>Provider</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h: any) => (
              <tr key={h.id}>
                <td>
                  <span className={`status-badge ${h.action === 1 ? 'available' : h.action === 3 ? 'missing' : 'unknown'}`}>
                    {actionLabels[h.action] || 'Unknown'}
                  </span>
                </td>
                <td style={{ textTransform: 'capitalize' }}>{h.metadata_type}</td>
                <td>{h.description}</td>
                <td style={{ color: 'var(--text-secondary)' }}>{h.provider}</td>
                <td style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                  {h.timestamp ? new Date(h.timestamp).toLocaleString() : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
