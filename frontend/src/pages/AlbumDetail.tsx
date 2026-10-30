import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import { Loader, Button, Select, Group, Badge, FileButton, Modal } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowLeft, faMagnifyingGlass, faUpload, faPenToSquare, faBan, faRotateLeft, faDownload, faLanguage, faClock } from '@fortawesome/free-solid-svg-icons';
import { getAlbum, getProfiles, massAssignProfile, uploadAlbumCover, updateTrack, batchDownload, batchTranslate, batchSyncGenerate } from '../api';
import { lyricsStatusColor, lyricsStatusLabel } from '../albumStatus';
import { notifyError } from '../notify';
import CoverSearchModal from '../components/CoverSearchModal';
import LyricsSearchModal from '../components/LyricsSearchModal';
import LyricsEditorModal from '../components/LyricsEditorModal';
import ImageLightbox from '../components/ImageLightbox';

export default function AlbumDetailPage() {
  const { albumId } = useParams();
  const queryClient = useQueryClient();
  const [profileId, setProfileId] = useState<string | null>(null);
  const [coverSearchOpen, setCoverSearchOpen] = useState(false);
  const [lyricsTrack, setLyricsTrack] = useState<any>(null);
  const [editorTrack, setEditorTrack] = useState<any>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  // Track awaiting confirmation before being marked "no lyrics" (blacklisted).
  const [skipTrack, setSkipTrack] = useState<any>(null);

  const { data: album, isLoading } = useQuery({
    queryKey: ['album', albumId],
    queryFn: () => getAlbum(Number(albumId)),
    enabled: !!albumId,
  });

  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const assignMutation = useMutation({
    mutationFn: () => massAssignProfile({ profileId: Number(profileId), albumIds: [Number(albumId)] }),
    onSuccess: () => {
      notifications.show({ title: 'Done', message: 'Profile updated', color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['album', albumId] });
    },
    onError: (err) => notifyError('Failed to set profile', err),
  });

  // Blacklist (skip) or un-blacklist a track's lyrics.
  const trackStatusMutation = useMutation({
    mutationFn: ({ track, status }: { track: any; status: 'missing' | 'blacklisted' }) =>
      updateTrack(track.lidarrTrackId, { lyrics_status: status }),
    onSuccess: (_data, { track, status }) => {
      setSkipTrack(null);
      queryClient.invalidateQueries({ queryKey: ['album', albumId] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
      queryClient.invalidateQueries({ queryKey: ['wanted-lyrics'] });
      queryClient.invalidateQueries({ queryKey: ['wanted-stats'] });
      notifications.show({
        title: status === 'blacklisted' ? 'Blacklisted' : 'Un-blacklisted',
        message: `${track.title} ${status === 'blacklisted' ? 'will be skipped by the downloader' : 'is now eligible for lyrics download'}`,
        color: status === 'blacklisted' ? 'red' : 'green',
      });
    },
    onError: (err) => notifyError('Failed to update track status', err),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadAlbumCover(Number(albumId), file),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Uploaded', message: data.message, color: 'green' });
      queryClient.invalidateQueries({ queryKey: ['album', albumId] });
    },
    onError: (err: any) => {
      notifications.show({ title: 'Error', message: err?.response?.data?.message || 'Upload failed', color: 'red' });
    },
  });

  if (isLoading) {
    return <div className="empty-state"><Loader color="violet" size="lg" /></div>;
  }

  if (!album) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">Album Not Found</div>
      </div>
    );
  }

  const tracks = album.tracks || [];

  return (
    <div className="fade-in">
      <Button
        variant="subtle"
        color="gray"
        leftSection={<FontAwesomeIcon icon={faArrowLeft} />}
        component={Link}
        to="/albums"
        mb="md"
      >
        Back to Albums
      </Button>

      {/* Album Header */}
      <div className="glass-card" style={{ padding: 24, marginBottom: 24 }}>
        <div style={{ display: 'flex', gap: 24 }}>
          <div style={{ position: 'relative', flexShrink: 0 }}>
            {album.cover ? (
              <img
                src={album.cover}
                alt={album.title}
                style={{ width: 180, height: 180, borderRadius: 12, objectFit: 'cover', cursor: 'pointer' }}
                onClick={() => setLightboxOpen(true)}
                role="button"
                tabIndex={0}
                aria-label="View cover full size"
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setLightboxOpen(true); } }}
              />
            ) : (
              <div style={{
                width: 180, height: 180, borderRadius: 12, background: 'var(--card-border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 48, opacity: 0.3
              }}>💿</div>
            )}
            <Button
              variant="gradient"
              gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
              size="xs"
              leftSection={<FontAwesomeIcon icon={faMagnifyingGlass} />}
              onClick={() => setCoverSearchOpen(true)}
              style={{ position: 'absolute', bottom: 8, left: 8, right: '50%', marginRight: 2 }}
            >
              Search
            </Button>
            <FileButton
              onChange={(file) => file && uploadMutation.mutate(file)}
              accept="image/png,image/jpeg,image/webp"
            >
              {(props) => (
                <Button
                  {...props}
                  variant="light"
                  color="violet"
                  size="xs"
                  leftSection={<FontAwesomeIcon icon={faUpload} />}
                  loading={uploadMutation.isPending}
                  style={{ position: 'absolute', bottom: 8, left: '50%', right: 8, marginLeft: 2 }}
                >
                  Upload
                </Button>
              )}
            </FileButton>
          </div>
          <div style={{ flex: 1 }}>
            <h2 style={{ margin: '0 0 4px', fontSize: 28, fontWeight: 700, color: 'var(--text-primary)' }}>
              {album.title}
            </h2>
            <p style={{ margin: '0 0 12px', fontSize: 16, color: 'var(--text-secondary)' }}>
              {album.artistName} {album.year ? `• ${album.year}` : ''}
            </p>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
              {album.albumType && <Badge variant="outline" color="violet">{album.albumType}</Badge>}
              <Badge color={album.cover_status === 'available' || album.cover_status === 'manual' ? 'green' : 'red'}>
                Cover: {album.cover_status || 'missing'}
              </Badge>
              <Badge color={lyricsStatusColor(album.lyrics_status)}>
                Lyrics: {lyricsStatusLabel(album.lyrics_status)}
              </Badge>
            </div>

            <Group gap="sm">
              <Select
                placeholder="Select profile..."
                data={profiles.map((p: any) => ({ value: String(p.id), label: p.name }))}
                value={profileId || (album.profileId ? String(album.profileId) : null)}
                onChange={setProfileId}
                size="sm"
                styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
              />
              <Button
                variant="gradient"
                gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
                size="sm"
                disabled={!profileId}
                onClick={() => assignMutation.mutate()}
                loading={assignMutation.isPending}
              >
                Set Profile
              </Button>
            </Group>

            <Group gap="xs" mt="sm">
              <Button
                variant="light"
                color="violet"
                size="xs"
                leftSection={<FontAwesomeIcon icon={faDownload} />}
                onClick={() => {
                  batchDownload({ albumIds: [Number(albumId)], type: 'lyrics' }).then(() => {
                    notifications.show({ title: 'Started', message: 'Downloading missing lyrics for this album...', color: 'violet' });
                  }).catch((err: any) => {
                    notifications.show({ title: 'Not Started', message: err?.response?.data?.message || 'Failed to start lyrics download', color: 'orange' });
                  });
                }}
              >
                Download Missing Lyrics
              </Button>
              <Button
                variant="light"
                color="cyan"
                size="xs"
                leftSection={<FontAwesomeIcon icon={faLanguage} />}
                onClick={() => {
                  batchTranslate({ albumIds: [Number(albumId)] }).then((r: any) => {
                    notifications.show({ title: 'Started', message: r.message || 'Translating lyrics...', color: 'cyan' });
                  }).catch((err) => notifyError('Failed to start translation', err));
                }}
              >
                Translate Lyrics
              </Button>
              <Button
                variant="light"
                color="teal"
                size="xs"
                leftSection={<FontAwesomeIcon icon={faClock} />}
                onClick={() => {
                  batchSyncGenerate({ albumIds: [Number(albumId)] }).then((r: any) => {
                    notifications.show({ title: 'Started', message: r.message || 'Generating synced lyrics...', color: 'teal' });
                  }).catch((err) => notifyError('Failed to start timing generation', err));
                }}
              >
                Generate Timing
              </Button>
            </Group>

            {album.overview && (
              <p style={{ marginTop: 12, fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                {album.overview}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Tracks */}
      <h3 style={{ marginBottom: 12, color: 'var(--text-primary)' }}>
        Tracks ({tracks.length})
      </h3>

      {tracks.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">🎵</div>
          <div className="empty-state-title">No Tracks</div>
          <div className="empty-state-message">No track files synced for this album yet.</div>
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 60 }}>#</th>
              <th>Title</th>
              <th>Lyrics</th>
              <th style={{ width: 180 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {tracks.map((track: any, idx: number) => (
              <tr key={track.lidarrTrackId || idx}>
                <td style={{ color: 'var(--text-secondary)' }}>
                  {track.discNumber && track.discNumber > 1 ? `${track.discNumber}.` : ''}
                  {track.trackNumber || idx + 1}
                </td>
                <td>
                  <div style={{ fontWeight: 500 }}>{track.title}</div>
                  {track.path && (
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)', opacity: 0.6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 500 }}>
                      {track.path.split('/').pop()}
                    </div>
                  )}
                </td>
                <td>
                  <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span className={`status-badge ${track.lyrics_status || 'missing'}`}>
                      {track.lyrics_status || 'missing'}
                    </span>
                    {track.is_synced && (
                      <span className="status-badge available" style={{ fontSize: 10, padding: '1px 5px' }}>LRC</span>
                    )}
                    {track.detected_language && (
                      <span style={{
                        fontSize: 10, padding: '1px 5px', borderRadius: 4,
                        background: 'rgba(139,61,255,0.15)', color: 'var(--accent-primary)',
                        fontWeight: 600, textTransform: 'uppercase',
                      }}>
                        {track.detected_language}
                      </span>
                    )}
                  </div>
                </td>
                <td>
                  <Group gap="xs">
                    <Button
                      variant="subtle"
                      color="violet"
                      size="xs"
                      leftSection={<FontAwesomeIcon icon={faMagnifyingGlass} />}
                      onClick={() => setLyricsTrack(track)}
                      disabled={track.lyrics_status === 'blacklisted'}
                    >
                      Search
                    </Button>
                    <Button
                      variant="subtle"
                      color="grape"
                      size="xs"
                      leftSection={<FontAwesomeIcon icon={faPenToSquare} />}
                      onClick={() => setEditorTrack(track)}
                      disabled={track.lyrics_status === 'blacklisted'}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="subtle"
                      color={track.lyrics_status === 'blacklisted' ? 'green' : 'red'}
                      size="xs"
                      leftSection={<FontAwesomeIcon icon={track.lyrics_status === 'blacklisted' ? faRotateLeft : faBan} />}
                      onClick={() => {
                        // Skipping deletes the track's lyrics file (archived to
                        // version history first) — confirm. Unblocking is harmless.
                        if (track.lyrics_status === 'blacklisted') {
                          trackStatusMutation.mutate({ track, status: 'missing' });
                        } else {
                          setSkipTrack(track);
                        }
                      }}
                      title={track.lyrics_status === 'blacklisted' ? 'Un-blacklist' : 'Blacklist (instrumental/no lyrics)'}
                    >
                      {track.lyrics_status === 'blacklisted' ? 'Unblock' : 'Skip'}
                    </Button>
                  </Group>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Modals */}
      <Modal
        opened={!!skipTrack}
        onClose={() => setSkipTrack(null)}
        title="Skip lyrics for this track?"
        size="sm"
      >
        <p style={{ fontSize: 13, marginTop: 0 }}>
          <strong>{skipTrack?.title}</strong> will be marked as having no lyrics (e.g. instrumental)
          and skipped by the downloader.
        </p>
        {skipTrack?.lyrics_status && skipTrack.lyrics_status !== 'missing' && (
          <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            Its current lyrics file will be removed. A copy is archived to the track's version
            history, so it can be restored from the lyrics editor.
          </p>
        )}
        <Group justify="flex-end" mt="md">
          <Button variant="subtle" color="gray" size="xs" onClick={() => setSkipTrack(null)}>Cancel</Button>
          <Button
            color="red"
            size="xs"
            loading={trackStatusMutation.isPending}
            onClick={() => trackStatusMutation.mutate({ track: skipTrack, status: 'blacklisted' })}
          >
            Skip Track
          </Button>
        </Group>
      </Modal>

      <CoverSearchModal
        albumId={Number(albumId)}
        opened={coverSearchOpen}
        onClose={() => setCoverSearchOpen(false)}
      />

      {lyricsTrack && (
        <LyricsSearchModal
          trackId={lyricsTrack.lidarrTrackId}
          trackTitle={lyricsTrack.title}
          albumId={Number(albumId)}
          opened={!!lyricsTrack}
          onClose={() => setLyricsTrack(null)}
        />
      )}

      {editorTrack && (
        <LyricsEditorModal
          trackId={editorTrack.lidarrTrackId}
          trackTitle={editorTrack.title}
          albumId={Number(albumId)}
          opened={!!editorTrack}
          onClose={() => setEditorTrack(null)}
        />
      )}
      {lightboxOpen && album.cover && (
        <ImageLightbox src={album.cover} alt={album.title} onClose={() => setLightboxOpen(false)} />
      )}
    </div>
  );
}
