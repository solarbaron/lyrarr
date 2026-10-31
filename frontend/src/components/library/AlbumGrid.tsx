import { useState, type ReactNode } from 'react';
import { faDownload, faImage } from '@fortawesome/free-solid-svg-icons';
import { CoverTile, GridSkeleton, IconButton } from '../ui';
import { useAlbumCounts } from '../../hooks/useAlbumCounts';
import CoverSearchModal from '../CoverSearchModal';
import { useDownloadMutation } from './actions';
import { GridLoadError, type LoadError } from './LibraryControls';
import type { AlbumRow } from './AlbumTable';
import c from './library.module.css';

export interface AlbumGridProps {
  rows: AlbumRow[];
  loading?: boolean;
  refetching?: boolean;
  /** Load error: "Couldn't load albums" + Retry replaces the grid (not the empty state). */
  error?: LoadError | null;
  /** Rendered instead of the grid when there are no rows. */
  empty?: ReactNode;
  /** Usually <Pager />, in a footer card under the grid. */
  footer?: ReactNode;
  selection?: { selected: Set<number>; onChange: (next: Set<number>) => void };
  /** Artist detail: subtitle "1990 · Album" instead of "Pixies · 1990". */
  hideArtist?: boolean;
  'aria-label': string;
}

/**
 * Albums as cover tiles (DESIGN.md 8.4): status badges, a selection checkbox on
 * hover/focus, and hover actions (Search covers, Download missing).
 */
export function AlbumGrid({ rows, loading, refetching, error, empty, footer, selection, hideArtist, 'aria-label': ariaLabel }: AlbumGridProps) {
  const { albums: albumCounts } = useAlbumCounts();
  const download = useDownloadMutation();
  const [coverAlbum, setCoverAlbum] = useState<AlbumRow | null>(null);

  if (error) return <GridLoadError error={error} noun="albums" />;
  if (loading) return <GridSkeleton count={12} />;

  return (
    <>
      {rows.length === 0 ? (
        empty && <div className={c.gridEmpty}>{empty}</div>
      ) : (
        <ul className={c.grid} aria-label={ariaLabel} data-dim={refetching || undefined}>
          {rows.map(a => {
            const id = a.lidarrAlbumId;
            const counts = albumCounts.get(id);
            const subtitle = (hideArtist ? [a.year, a.albumType] : [a.artistName, a.year]).filter(Boolean).join(' · ');
            return (
              <li key={id}>
                <CoverTile
                  href={`/albums/${id}`}
                  art={{ src: a.cover, alt: '', kind: 'album', name: a.title }}
                  title={a.title}
                  subtitle={subtitle || undefined}
                  cover={a.cover_status}
                  lyrics={a.lyrics_status}
                  lyricsFraction={counts && counts.total > 0 ? [counts.done, counts.total] : undefined}
                  selected={selection?.selected.has(id)}
                  onSelectChange={selection ? v => {
                    const next = new Set(selection.selected);
                    if (v) next.add(id);
                    else next.delete(id);
                    selection.onChange(next);
                  } : undefined}
                  actions={(
                    <>
                      <IconButton icon={faImage} label="Search covers" variant="default" size="sm" onClick={() => setCoverAlbum(a)} />
                      <IconButton
                        icon={faDownload}
                        label="Download missing"
                        variant="default"
                        size="sm"
                        onClick={() => download.mutate({ target: { albumIds: [id] }, type: 'all', message: `Downloading missing covers and lyrics for ${a.title}...` })}
                      />
                    </>
                  )}
                />
              </li>
            );
          })}
        </ul>
      )}
      {footer && rows.length > 0 && <div className={c.gridFooter}>{footer}</div>}
      <CoverSearchModal
        albumId={coverAlbum?.lidarrAlbumId ?? 0}
        album={coverAlbum ?? undefined}
        opened={!!coverAlbum}
        onClose={() => setCoverAlbum(null)}
      />
    </>
  );
}
