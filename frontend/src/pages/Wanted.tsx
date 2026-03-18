import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader, Tabs, Pagination, Group, TextInput, Button, Progress, Checkbox, Select } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getWantedCovers, getWantedLyrics, getWantedUntimed, getWantedUndetected, getWantedStats, searchCovers, searchLyrics, batchSyncGenerate, batchRedetectLanguages, updateTrack } from '../api';

const LANG_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ru', label: 'Russian' },
  { value: 'ar', label: 'Arabic' },
  { value: 'hi', label: 'Hindi' },
  { value: 'nl', label: 'Dutch' },
  { value: 'sv', label: 'Swedish' },
  { value: 'pl', label: 'Polish' },
  { value: 'tr', label: 'Turkish' },
  { value: 'da', label: 'Danish' },
  { value: 'fi', label: 'Finnish' },
  { value: 'no', label: 'Norwegian' },
  { value: 'el', label: 'Greek' },
  { value: 'he', label: 'Hebrew' },
  { value: 'th', label: 'Thai' },
  { value: 'vi', label: 'Vietnamese' },
  { value: 'id', label: 'Indonesian' },
  { value: 'uk', label: 'Ukrainian' },
  { value: 'cs', label: 'Czech' },
  { value: 'ro', label: 'Romanian' },
  { value: 'hu', label: 'Hungarian' },
];

export default function WantedPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [coversPage, setCoversPage] = useState(1);
  const [lyricsPage, setLyricsPage] = useState(1);
  const [untimedPage, setUntimedPage] = useState(1);
  const [undetectedPage, setUndetectedPage] = useState(1);
  const [coversSearch, setCoversSearch] = useState('');
  const [lyricsSearch, setLyricsSearch] = useState('');
  const [untimedSearch, setUntimedSearch] = useState('');
  const [undetectedSearch, setUndetectedSearch] = useState('');
  const [selectedTracks, setSelectedTracks] = useState<Set<number>>(new Set());
  const [selectedLangTracks, setSelectedLangTracks] = useState<Set<number>>(new Set());
  const [syncingTrackId, setSyncingTrackId] = useState<number | null>(null);
  const [detectingTrackId, setDetectingTrackId] = useState<number | null>(null);
  const pageSize = 25;

  const { data: stats } = useQuery({ queryKey: ['wanted-stats'], queryFn: getWantedStats });

  const { data: coversResult, isLoading: loadingCovers } = useQuery({
    queryKey: ['wanted-covers', coversPage, coversSearch],
    queryFn: () => getWantedCovers({ page: coversPage, pageSize, search: coversSearch || undefined }),
  });
  const { data: lyricsResult, isLoading: loadingLyrics } = useQuery({
    queryKey: ['wanted-lyrics', lyricsPage, lyricsSearch],
    queryFn: () => getWantedLyrics({ page: lyricsPage, pageSize, search: lyricsSearch || undefined }),
  });
  const { data: untimedResult, isLoading: loadingUntimed } = useQuery({
    queryKey: ['wanted-untimed', untimedPage, untimedSearch],
    queryFn: () => getWantedUntimed({ page: untimedPage, pageSize, search: untimedSearch || undefined }),
  });
  const { data: undetectedResult, isLoading: loadingUndetected } = useQuery({
    queryKey: ['wanted-undetected', undetectedPage, undetectedSearch],
    queryFn: () => getWantedUndetected({ page: undetectedPage, pageSize, search: undetectedSearch || undefined }),
  });

  const wantedCovers = coversResult?.data || [];
  const coversTotal = coversResult?.total || 0;
  const coversTotalPages = Math.ceil(coversTotal / pageSize);

  const wantedLyrics = lyricsResult?.data || [];
  const lyricsTotal = lyricsResult?.total || 0;
  const lyricsTotalPages = Math.ceil(lyricsTotal / pageSize);

  const untimedTracks = untimedResult?.data || [];
  const untimedTotal = untimedResult?.total || 0;
  const untimedTotalPages = Math.ceil(untimedTotal / pageSize);

  const undetectedTracks = undetectedResult?.data || [];
  const undetectedTotal = undetectedResult?.total || 0;
  const undetectedTotalPages = Math.ceil(undetectedTotal / pageSize);

  // Re-search mutations
  const [searchingCoverId, setSearchingCoverId] = useState<number | null>(null);
  const [searchingLyricsId, setSearchingLyricsId] = useState<number | null>(null);

  const coverSearchMutation = useMutation({
    mutationFn: (albumId: number) => {
      setSearchingCoverId(albumId);
      return searchCovers(albumId);
    },
    onSuccess: (data: any, albumId) => {
      const count = data?.results?.length || 0;
      notifications.show({
        title: 'Cover Search',
        message: count > 0 ? `Found ${count} cover(s). Go to album page to select.` : 'No covers found.',
        color: count > 0 ? 'green' : 'yellow',
      });
      setSearchingCoverId(null);
    },
    onError: () => { setSearchingCoverId(null); },
  });

  const lyricsSearchMutation = useMutation({
    mutationFn: (trackId: number) => {
      setSearchingLyricsId(trackId);
      return searchLyrics(trackId);
    },
    onSuccess: (data: any, trackId) => {
      const count = data?.results?.length || 0;
      notifications.show({
        title: 'Lyrics Search',
        message: count > 0 ? `Found ${count} result(s). Go to album page to select.` : 'No lyrics found.',
        color: count > 0 ? 'green' : 'yellow',
      });
      setSearchingLyricsId(null);
    },
    onError: () => { setSearchingLyricsId(null); },
  });

  const coversPct = stats ? Math.round(((stats.covers_complete || 0) / Math.max(stats.total_albums, 1)) * 100) : 0;
  const lyricsPct = stats ? Math.round(((stats.lyrics_complete || 0) / Math.max(stats.total_tracks, 1)) * 100) : 0;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h1 className="page-title">Wanted</h1>
        <p className="page-subtitle">Missing metadata that needs to be downloaded</p>
      </div>

      {/* Summary Stats */}
      {stats && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginBottom: 24 }}>
          <div className="settings-section" style={{ margin: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>🎨 Cover Art</span>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {stats.covers_complete}/{stats.total_albums} complete
              </span>
            </div>
            <Progress value={coversPct} color={coversPct > 80 ? 'green' : coversPct > 50 ? 'yellow' : 'red'} size="lg" />
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
              {stats.missing_covers} missing
            </div>
          </div>
          <div className="settings-section" style={{ margin: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>📝 Lyrics</span>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {stats.lyrics_complete}/{stats.total_tracks} complete
              </span>
            </div>
            <Progress value={lyricsPct} color={lyricsPct > 80 ? 'green' : lyricsPct > 50 ? 'yellow' : 'red'} size="lg" />
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
              {stats.missing_lyrics} missing
            </div>
          </div>
        </div>
      )}

      <Tabs defaultValue="covers" styles={{
        tab: { color: 'var(--text-secondary)', '&[data-active]': { color: 'var(--text-primary)' } }
      }}>
        <Tabs.List mb="lg">
        <Tabs.Tab value="covers">Missing Covers ({coversTotal})</Tabs.Tab>
          <Tabs.Tab value="lyrics">Missing Lyrics ({lyricsTotal})</Tabs.Tab>
          <Tabs.Tab value="untimed">Untimed Lyrics ({untimedTotal})</Tabs.Tab>
          <Tabs.Tab value="undetected">Undetected Language ({undetectedTotal})</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="covers">
          <Group mb="md">
            <TextInput
              placeholder="Search albums..."
              value={coversSearch}
              onChange={(e) => { setCoversSearch(e.currentTarget.value); setCoversPage(1); }}
              style={{ flex: 1 }}
              styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
            />
          </Group>

          {loadingCovers ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : wantedCovers.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🎨</div>
              <div className="empty-state-title">No Missing Covers</div>
              <div className="empty-state-message">
                {coversSearch ? 'No albums match your search.' : 'All albums have cover art.'}
              </div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr><th>Album</th><th>Artist</th><th>Year</th><th>Type</th><th>Action</th></tr>
                </thead>
                <tbody>
                  {wantedCovers.map((album: any) => (
                    <tr key={album.lidarrAlbumId} style={{ cursor: 'pointer' }}>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                          <div style={{
                            width: 36, height: 36, borderRadius: 6, background: 'var(--card-border)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, opacity: 0.5
                          }}>💿</div>
                          <span style={{ fontWeight: 600 }}>{album.title}</span>
                        </div>
                      </td>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)} style={{ color: 'var(--text-secondary)' }}>
                        {album.artistName || '—'}
                      </td>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>{album.year || '—'}</td>
                      <td onClick={() => navigate(`/albums/${album.lidarrAlbumId}`)}>{album.albumType || '—'}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <Button
                          size="xs" variant="light" color="violet"
                          loading={searchingCoverId === album.lidarrAlbumId}
                          onClick={() => coverSearchMutation.mutate(album.lidarrAlbumId)}
                        >
                          🔍 Search
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {coversTotalPages > 1 && (
                <Group justify="center" mt="xl">
                  <Pagination total={coversTotalPages} value={coversPage} onChange={setCoversPage} color="violet" />
                </Group>
              )}
            </>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="lyrics">
          <Group mb="md">
            <TextInput
              placeholder="Search tracks..."
              value={lyricsSearch}
              onChange={(e) => { setLyricsSearch(e.currentTarget.value); setLyricsPage(1); }}
              style={{ flex: 1 }}
              styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
            />
          </Group>

          {loadingLyrics ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : wantedLyrics.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📝</div>
              <div className="empty-state-title">No Missing Lyrics</div>
              <div className="empty-state-message">
                {lyricsSearch ? 'No tracks match your search.' : 'All tracks have lyrics.'}
              </div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr><th>Track</th><th>Artist</th><th>File</th><th>Action</th></tr>
                </thead>
                <tbody>
                  {wantedLyrics.map((track: any) => (
                    <tr key={track.lidarrTrackId}>
                      <td style={{ fontWeight: 600 }}>{track.title}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{track.artistName || '—'}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {track.path ? track.path.split('/').pop() : '—'}
                      </td>
                      <td>
                        <Button
                          size="xs" variant="light" color="violet"
                          loading={searchingLyricsId === track.lidarrTrackId}
                          onClick={() => lyricsSearchMutation.mutate(track.lidarrTrackId)}
                        >
                          🔍 Search
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {lyricsTotalPages > 1 && (
                <Group justify="center" mt="xl">
                  <Pagination total={lyricsTotalPages} value={lyricsPage} onChange={setLyricsPage} color="violet" />
                </Group>
              )}
            </>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="untimed">
          <Group mb="md" justify="space-between">
            <TextInput
              placeholder="Search tracks..."
              value={untimedSearch}
              onChange={(e) => { setUntimedSearch(e.currentTarget.value); setUntimedPage(1); setSelectedTracks(new Set()); }}
              style={{ flex: 1 }}
              styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
            />
            <Group gap="xs">
              <Button
                size="xs" variant="light" color="teal"
                disabled={selectedTracks.size === 0}
                onClick={() => {
                  batchSyncGenerate({ trackIds: Array.from(selectedTracks) }).then((r: any) => {
                    notifications.show({ title: 'Started', message: r.message || `Syncing ${selectedTracks.size} track(s)...`, color: 'teal' });
                    setSelectedTracks(new Set());
                  });
                }}
              >
                ⏱ Sync Selected ({selectedTracks.size})
              </Button>
              <Button
                size="xs" variant="filled" color="teal"
                disabled={untimedTracks.length === 0}
                onClick={() => {
                  const allIds = untimedTracks.map((t: any) => t.lidarrTrackId);
                  batchSyncGenerate({ trackIds: allIds }).then((r: any) => {
                    notifications.show({ title: 'Started', message: r.message || `Syncing all ${allIds.length} track(s)...`, color: 'teal' });
                  });
                }}
              >
                ⏱ Sync All on Page
              </Button>
            </Group>
          </Group>

          {loadingUntimed ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : untimedTracks.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">⏱</div>
              <div className="empty-state-title">No Untimed Lyrics</div>
              <div className="empty-state-message">
                {untimedSearch ? 'No tracks match your search.' : 'All available lyrics are synced.'}
              </div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>
                      <Checkbox
                        checked={selectedTracks.size === untimedTracks.length && untimedTracks.length > 0}
                        indeterminate={selectedTracks.size > 0 && selectedTracks.size < untimedTracks.length}
                        onChange={(e) => {
                          if (e.currentTarget.checked) {
                            setSelectedTracks(new Set(untimedTracks.map((t: any) => t.lidarrTrackId)));
                          } else {
                            setSelectedTracks(new Set());
                          }
                        }}
                        color="teal"
                      />
                    </th>
                    <th>Track</th>
                    <th>Artist</th>
                    <th>Album</th>
                    <th>Language</th>
                    <th style={{ width: 100 }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {untimedTracks.map((track: any) => (
                    <tr key={track.lidarrTrackId}>
                      <td>
                        <Checkbox
                          checked={selectedTracks.has(track.lidarrTrackId)}
                          onChange={(e) => {
                            const next = new Set(selectedTracks);
                            if (e.currentTarget.checked) {
                              next.add(track.lidarrTrackId);
                            } else {
                              next.delete(track.lidarrTrackId);
                            }
                            setSelectedTracks(next);
                          }}
                          color="teal"
                        />
                      </td>
                      <td style={{ fontWeight: 600 }}>{track.title}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{track.artistName || '—'}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{track.albumTitle || '—'}</td>
                      <td>
                        {track.detected_language ? (
                          <span style={{
                            fontSize: 10, padding: '1px 5px', borderRadius: 4,
                            background: 'rgba(139,61,255,0.15)', color: 'var(--accent-primary)',
                            fontWeight: 600, textTransform: 'uppercase',
                          }}>
                            {track.detected_language}
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>—</span>
                        )}
                      </td>
                      <td>
                        <Button
                          size="xs" variant="light" color="teal"
                          loading={syncingTrackId === track.lidarrTrackId}
                          onClick={() => {
                            setSyncingTrackId(track.lidarrTrackId);
                            batchSyncGenerate({ trackIds: [track.lidarrTrackId] }).then((r: any) => {
                              notifications.show({ title: 'Started', message: r.message || 'Generating timing...', color: 'teal' });
                              setSyncingTrackId(null);
                            }).catch(() => setSyncingTrackId(null));
                          }}
                        >
                          ⏱ Sync
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {untimedTotalPages > 1 && (
                <Group justify="center" mt="xl">
                  <Pagination total={untimedTotalPages} value={untimedPage} onChange={(p) => { setUntimedPage(p); setSelectedTracks(new Set()); }} color="violet" />
                </Group>
              )}
            </>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="undetected">
          <Group mb="md" justify="space-between">
            <TextInput
              placeholder="Search tracks..."
              value={undetectedSearch}
              onChange={(e) => { setUndetectedSearch(e.currentTarget.value); setUndetectedPage(1); setSelectedLangTracks(new Set()); }}
              style={{ flex: 1 }}
              styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' } }}
            />
            <Group gap="xs">
              <Button
                size="xs" variant="light" color="cyan"
                disabled={selectedLangTracks.size === 0}
                onClick={() => {
                  batchRedetectLanguages({ trackIds: Array.from(selectedLangTracks) }).then(() => {
                    notifications.show({ title: 'Started', message: `Detecting language for ${selectedLangTracks.size} track(s)...`, color: 'cyan' });
                    setSelectedLangTracks(new Set());
                    queryClient.invalidateQueries({ queryKey: ['wanted-undetected'] });
                  });
                }}
              >
                🌐 Detect Selected ({selectedLangTracks.size})
              </Button>
              <Button
                size="xs" variant="filled" color="cyan"
                disabled={undetectedTracks.length === 0}
                onClick={() => {
                  const allIds = undetectedTracks.map((t: any) => t.lidarrTrackId);
                  batchRedetectLanguages({ trackIds: allIds }).then(() => {
                    notifications.show({ title: 'Started', message: `Detecting language for all ${allIds.length} track(s)...`, color: 'cyan' });
                    queryClient.invalidateQueries({ queryKey: ['wanted-undetected'] });
                  });
                }}
              >
                🌐 Detect All on Page
              </Button>
            </Group>
          </Group>

          {loadingUndetected ? (
            <div className="empty-state"><Loader color="violet" /></div>
          ) : undetectedTracks.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🌐</div>
              <div className="empty-state-title">No Undetected Languages</div>
              <div className="empty-state-message">
                {undetectedSearch ? 'No tracks match your search.' : 'All available lyrics have a detected language.'}
              </div>
            </div>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>
                      <Checkbox
                        checked={selectedLangTracks.size === undetectedTracks.length && undetectedTracks.length > 0}
                        indeterminate={selectedLangTracks.size > 0 && selectedLangTracks.size < undetectedTracks.length}
                        onChange={(e) => {
                          if (e.currentTarget.checked) {
                            setSelectedLangTracks(new Set(undetectedTracks.map((t: any) => t.lidarrTrackId)));
                          } else {
                            setSelectedLangTracks(new Set());
                          }
                        }}
                        color="cyan"
                      />
                    </th>
                    <th>Track</th>
                    <th>Artist</th>
                    <th>Album</th>
                    <th>Synced</th>
                    <th style={{ width: 160 }}>Set Language</th>
                    <th style={{ width: 100 }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {undetectedTracks.map((track: any) => (
                    <tr key={track.lidarrTrackId}>
                      <td>
                        <Checkbox
                          checked={selectedLangTracks.has(track.lidarrTrackId)}
                          onChange={(e) => {
                            const next = new Set(selectedLangTracks);
                            if (e.currentTarget.checked) {
                              next.add(track.lidarrTrackId);
                            } else {
                              next.delete(track.lidarrTrackId);
                            }
                            setSelectedLangTracks(next);
                          }}
                          color="cyan"
                        />
                      </td>
                      <td style={{ fontWeight: 600 }}>{track.title}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{track.artistName || '—'}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{track.albumTitle || '—'}</td>
                      <td>
                        {track.is_synced ? (
                          <span className="status-badge available" style={{ fontSize: 10, padding: '1px 5px' }}>LRC</span>
                        ) : (
                          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>Plain</span>
                        )}
                      </td>
                      <td>
                        <Select
                          placeholder="Set..."
                          data={LANG_OPTIONS}
                          size="xs"
                          w={130}
                          searchable
                          clearable
                          onChange={(val) => {
                            if (val) {
                              updateTrack(track.lidarrTrackId, { detected_language: val }).then(() => {
                                notifications.show({ title: 'Updated', message: `Set language to ${val.toUpperCase()}`, color: 'green' });
                                queryClient.invalidateQueries({ queryKey: ['wanted-undetected'] });
                                queryClient.invalidateQueries({ queryKey: ['wanted-stats'] });
                              });
                            }
                          }}
                          styles={{ input: { background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: 'var(--text-primary)', minHeight: 28, height: 28 } }}
                        />
                      </td>
                      <td>
                        <Button
                          size="xs" variant="light" color="cyan"
                          loading={detectingTrackId === track.lidarrTrackId}
                          onClick={() => {
                            setDetectingTrackId(track.lidarrTrackId);
                            batchRedetectLanguages({ trackIds: [track.lidarrTrackId] }).then(() => {
                              notifications.show({ title: 'Started', message: 'Detecting language...', color: 'cyan' });
                              setDetectingTrackId(null);
                              queryClient.invalidateQueries({ queryKey: ['wanted-undetected'] });
                              queryClient.invalidateQueries({ queryKey: ['wanted-stats'] });
                            }).catch(() => setDetectingTrackId(null));
                          }}
                        >
                          🌐 Detect
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {undetectedTotalPages > 1 && (
                <Group justify="center" mt="xl">
                  <Pagination total={undetectedTotalPages} value={undetectedPage} onChange={(p) => { setUndetectedPage(p); setSelectedLangTracks(new Set()); }} color="violet" />
                </Group>
              )}
            </>
          )}
        </Tabs.Panel>
      </Tabs>
    </div>
  );
}
