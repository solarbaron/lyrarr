import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Modal, Loader, SimpleGrid, Button } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { searchCovers, downloadCover } from '../api';

interface Props {
  albumId: number;
  opened: boolean;
  onClose: () => void;
}

export default function CoverSearchModal({ albumId, opened, onClose }: Props) {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['cover-search', albumId],
    queryFn: () => searchCovers(albumId),
    enabled: opened,
  });

  const downloadMutation = useMutation({
    mutationFn: (result: any) => downloadCover(albumId, { url: result.url, provider: result.provider }),
    onSuccess: () => {
      notifications.show({ title: 'Done', message: 'Cover art saved!', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
      onClose();
    },
    onError: () => {
      notifications.show({ title: 'Error', message: 'Failed to download cover art', color: 'red' });
    },
  });

  const results = data?.results || [];

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Search Cover Art"
      size="xl"
      styles={{
        content: { background: 'var(--surface-bg)' },
        header: { background: 'var(--surface-bg)' },
      }}
    >
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Loader color="violet" />
          <p style={{ color: 'var(--text-secondary)', marginTop: 12 }}>Searching providers...</p>
        </div>
      ) : results.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <div style={{ fontSize: 48, opacity: 0.3, marginBottom: 12 }}>🎨</div>
          <p style={{ color: 'var(--text-secondary)' }}>No cover art found from any provider.</p>
        </div>
      ) : (
        <>
          <p style={{ color: 'var(--text-secondary)', marginBottom: 16, fontSize: 13 }}>
            {results.length} result{results.length !== 1 ? 's' : ''} found. Click to download.
          </p>
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }} spacing="md">
            {results.map((result: any, idx: number) => (
              <div
                key={idx}
                onClick={() => downloadMutation.mutate(result)}
                style={{
                  cursor: downloadMutation.isPending ? 'wait' : 'pointer',
                  borderRadius: 12,
                  overflow: 'hidden',
                  border: '2px solid transparent',
                  transition: 'all 0.2s',
                  background: 'var(--card-bg)',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'transparent')}
              >
                <img
                  src={result.url_large || result.url_small || result.url}
                  alt="Cover art"
                  style={{
                    width: '100%',
                    aspectRatio: '1',
                    objectFit: 'cover',
                    display: 'block',
                  }}
                  loading="lazy"
                />
                <div style={{ padding: '8px 10px' }}>
                  <div style={{
                    fontSize: 11,
                    color: 'var(--text-secondary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}>
                    {result.provider} • {result.type || 'cover'}
                  </div>
                </div>
              </div>
            ))}
          </SimpleGrid>
        </>
      )}
    </Modal>
  );
}
