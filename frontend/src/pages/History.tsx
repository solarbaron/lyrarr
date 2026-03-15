import { useQuery } from '@tanstack/react-query';
import { Loader } from '@mantine/core';
import { getHistory } from '../api';

const actionLabels: Record<number, string> = {
  1: 'Downloaded',
  2: 'Upgraded',
  3: 'Deleted',
  4: 'Manual',
};

export default function HistoryPage() {
  const { data: history = [], isLoading } = useQuery({ queryKey: ['history'], queryFn: getHistory });

  if (isLoading) {
    return (
      <div className="empty-state"><Loader color="violet" size="lg" /></div>
    );
  }

  return (
    <div className="fade-in">
      <div className="page-header">
        <h1 className="page-title">History</h1>
        <p className="page-subtitle">Recent metadata actions</p>
      </div>

      {history.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">📋</div>
          <div className="empty-state-title">No History</div>
          <div className="empty-state-message">Metadata actions will appear here.</div>
        </div>
      ) : (
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
      )}
    </div>
  );
}
