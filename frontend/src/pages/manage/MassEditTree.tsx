import { memo, useCallback, useMemo, useRef, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Checkbox } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowUpRightFromSquare, faChevronRight } from '@fortawesome/free-solid-svg-icons';
import type { LibraryTreeArtist } from '../../api';
import { CoverArt, IconButton, LyricsMeter, StatusDot } from '../../components/ui';
import { countsFromTreeAlbum } from '../../hooks/useAlbumCounts';
import { num } from '../../format';
import c from './MassEdit.module.css';

/** A focusable tree node: `a:<artistId>` or `b:<albumId>`. */
export type NodeKey = string;
const artistKey = (id: number): NodeKey => `a:${id}`;
const albumKey = (id: number): NodeKey => `b:${id}`;

interface VisibleNode {
  key: NodeKey;
  artist: LibraryTreeArtist;
  albumId?: number;
}

/** Ghost icon link with a tooltip ("Open Westside Gunn"); not a tab stop inside the tree. */
function OpenLink({ to, label }: { to: string; label: string }) {
  return (
    <IconButton
      icon={faArrowUpRightFromSquare}
      to={to}
      label={label}
      tabIndex={-1}
      size="sm"
      className={c.openLink}
      onClick={e => e.stopPropagation()}
    />
  );
}

interface ArtistNodeProps {
  artist: LibraryTreeArtist;
  selected: Set<number>;
  isExpanded: boolean;
  /** The cursor key when it is inside this artist, else null (keeps memo cheap). */
  cursor: NodeKey | null;
  /** True when this artist holds the tree's single tab stop. */
  tabStop: boolean;
  onToggleArtist: (artist: LibraryTreeArtist) => void;
  onToggleAlbum: (id: number) => void;
  onToggleExpanded: (id: number) => void;
  onCursor: (key: NodeKey) => void;
}

/**
 * One artist (and, when expanded, its albums). Memoized so toggling one album
 * or typing in the filter doesn't re-render every row of a large library: a row
 * only re-renders when its own albums' selection, expansion or cursor changes.
 */
const ArtistNode = memo(function ArtistNode({
  artist, selected, isExpanded, cursor, tabStop, onToggleArtist, onToggleAlbum, onToggleExpanded, onCursor,
}: ArtistNodeProps) {
  const selectedCount = artist.albums.filter(a => selected.has(a.id)).length;
  const allSelected = selectedCount === artist.albums.length;
  const someSelected = selectedCount > 0 && !allSelected;
  const groupId = `mass-edit-artist-${artist.id}`;
  const key = artistKey(artist.id);
  const albumsLabel = `${num(artist.albums.length)} album${artist.albums.length === 1 ? '' : 's'}`;
  // Artist rows summarise their albums in the Lyrics and Cover columns.
  const totals = artist.albums.reduce((t, a) => {
    const n = countsFromTreeAlbum(a);
    t.synced += n.synced; t.plain += n.plain; t.instrumental += n.instrumental; t.total += n.total;
    const cs = (a.coverStatus || '').toLowerCase();
    if (cs && cs !== 'missing' && cs !== 'failed' && cs !== 'error') t.covers += 1;
    return t;
  }, { synced: 0, plain: 0, instrumental: 0, total: 0, covers: 0 });

  return (
    <div
      role="treeitem"
      aria-level={1}
      aria-expanded={isExpanded}
      aria-checked={allSelected ? true : someSelected ? 'mixed' : false}
      aria-label={`${artist.name}, ${albumsLabel}${selectedCount ? `, ${num(selectedCount)} selected` : ''}`}
      tabIndex={cursor === key || (tabStop && cursor === null) ? 0 : -1}
      data-key={key}
      className={c.artistItem}
      onFocus={e => { if (e.target === e.currentTarget) onCursor(key); }}
    >
      <div
        className={c.row}
        data-level="1"
        data-selected={allSelected || undefined}
        data-partial={someSelected || undefined}
        onClick={() => { onCursor(key); onToggleExpanded(artist.id); }}
      >
        <span className={c.check} onClick={e => e.stopPropagation()}>
          <Checkbox
            size="xs"
            tabIndex={-1}
            checked={allSelected}
            indeterminate={someSelected}
            onChange={() => { onCursor(key); onToggleArtist(artist); }}
            aria-label={`Select all albums by ${artist.name}`}
          />
        </span>
        <span className={c.chevron} data-open={isExpanded || undefined} aria-hidden="true">
          <FontAwesomeIcon icon={faChevronRight} />
        </span>
        <CoverArt kind="artist" alt="" name={artist.name} size={24} />
        <span className={c.name} title={artist.name}>{artist.name}</span>
        <span className={c.meta}>
          {selectedCount > 0 ? `${num(selectedCount)} of ${albumsLabel} selected` : albumsLabel}
        </span>
        <span className={c.artistCols} aria-hidden="true">
          <span className={c.year} />
          <span className={c.meter}>
            <LyricsMeter
              synced={totals.synced}
              plain={totals.plain}
              instrumental={totals.instrumental}
              total={totals.total}
              withTooltip={false}
            />
          </span>
          <span className={`${c.cover} ${c.coverCount}`}>{num(totals.covers)}/{num(artist.albums.length)}</span>
        </span>
        <OpenLink to={`/artists/${artist.id}`} label={`Open ${artist.name}`} />
      </div>

      {isExpanded && (
        <div role="group" id={groupId}>
          {artist.albums.map(album => {
            const counts = countsFromTreeAlbum(album);
            const isSelected = selected.has(album.id);
            const aKey = albumKey(album.id);
            return (
              <div
                key={album.id}
                role="treeitem"
                aria-level={2}
                aria-checked={isSelected}
                aria-label={`${album.title}${album.year ? ` (${album.year})` : ''}`}
                tabIndex={cursor === aKey ? 0 : -1}
                data-key={aKey}
                className={`${c.row} ${c.albumRow}`}
                data-level="2"
                data-selected={isSelected || undefined}
                onFocus={e => { if (e.target === e.currentTarget) onCursor(aKey); }}
                onClick={() => { onCursor(aKey); onToggleAlbum(album.id); }}
              >
                <span className={c.check} onClick={e => e.stopPropagation()}>
                  <Checkbox
                    size="xs"
                    tabIndex={-1}
                    checked={isSelected}
                    onChange={() => { onCursor(aKey); onToggleAlbum(album.id); }}
                    aria-label={`Select ${album.title}`}
                  />
                </span>
                <span className={c.albumTitle} title={album.title}>{album.title}</span>
                <span className={c.year}>{album.year ?? '—'}</span>
                <span className={c.meter}>
                  <LyricsMeter
                    synced={counts.synced}
                    plain={counts.plain}
                    instrumental={counts.instrumental}
                    total={counts.total}
                    withTooltip={false}
                    aria-label={`Lyrics for ${album.title}`}
                  />
                </span>
                <span className={c.cover}>
                  <StatusDot domain="cover" status={album.coverStatus} />
                </span>
                <OpenLink to={`/albums/${album.id}`} label={`Open ${album.title}`} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}, (prev, next) => {
  if (prev.artist !== next.artist || prev.isExpanded !== next.isExpanded
    || prev.cursor !== next.cursor || prev.tabStop !== next.tabStop) return false;
  if (prev.selected === next.selected) return true;
  // Callbacks are stable; only this artist's slice of the selection matters.
  return prev.artist.albums.every(a => prev.selected.has(a.id) === next.selected.has(a.id));
});

export interface MassEditTreeProps {
  artists: LibraryTreeArtist[];
  selected: Set<number>;
  isExpanded: (id: number) => boolean;
  /** Changes identity whenever the expansion state changes (drives memo). */
  expansionKey: unknown;
  cursor: NodeKey | null;
  onCursor: (key: NodeKey | null) => void;
  onToggleArtist: (artist: LibraryTreeArtist) => void;
  onToggleAlbum: (id: number) => void;
  onToggleExpanded: (id: number) => void;
  onSetExpanded: (id: number, open: boolean) => void;
}

/**
 * The selection tree (DESIGN.md 8.8): artists with tri-state checkboxes, albums
 * indented below. One tab stop; ↑/↓ move, →/← expand/collapse (or step in/out),
 * Space or x toggles, Enter opens, Home/End jump.
 */
export function MassEditTree({
  artists, selected, isExpanded, expansionKey, cursor, onCursor,
  onToggleArtist, onToggleAlbum, onToggleExpanded, onSetExpanded,
}: MassEditTreeProps) {
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const nodes = useMemo<VisibleNode[]>(() => {
    const out: VisibleNode[] = [];
    for (const artist of artists) {
      out.push({ key: artistKey(artist.id), artist });
      if (isExpanded(artist.id)) {
        for (const album of artist.albums) out.push({ key: albumKey(album.id), artist, albumId: album.id });
      }
    }
    return out;
    // expansionKey stands in for isExpanded's state
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artists, expansionKey]);

  const indexOf = useMemo(() => {
    const m = new Map<NodeKey, number>();
    nodes.forEach((n, i) => m.set(n.key, i));
    return m;
  }, [nodes]);

  // A cursor that was filtered away or collapsed falls back to the first row.
  const liveCursor = cursor !== null && indexOf.has(cursor) ? cursor : null;
  const cursorArtist = liveCursor ? nodes[indexOf.get(liveCursor)!].artist.id : null;

  const focusKey = useCallback((key: NodeKey) => {
    onCursor(key);
    // After React commits the new tabIndex.
    requestAnimationFrame(() => {
      const el = ref.current?.querySelector<HTMLElement>(`[data-key="${key}"]`);
      el?.focus();
      el?.scrollIntoView({ block: 'nearest' });
    });
  }, [onCursor]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const target = e.target as HTMLElement;
    const key = target.getAttribute?.('data-key');
    if (!key || !indexOf.has(key)) return;
    const i = indexOf.get(key)!;
    const node = nodes[i];
    const isArtist = node.albumId === undefined;
    const open = isArtist && isExpanded(node.artist.id);

    switch (e.key) {
      case 'ArrowDown':
        if (i < nodes.length - 1) focusKey(nodes[i + 1].key);
        break;
      case 'ArrowUp':
        if (i > 0) focusKey(nodes[i - 1].key);
        break;
      case 'Home':
        focusKey(nodes[0].key);
        break;
      case 'End':
        focusKey(nodes[nodes.length - 1].key);
        break;
      case 'ArrowRight':
        if (isArtist && !open) onSetExpanded(node.artist.id, true);
        else if (isArtist && open && node.artist.albums.length) focusKey(albumKey(node.artist.albums[0].id));
        break;
      case 'ArrowLeft':
        if (isArtist && open) onSetExpanded(node.artist.id, false);
        else if (!isArtist) focusKey(artistKey(node.artist.id));
        break;
      case ' ':
      case 'x':
      case 'X':
        if (isArtist) onToggleArtist(node.artist);
        else onToggleAlbum(node.albumId!);
        break;
      case 'Enter':
        navigate(isArtist ? `/artists/${node.artist.id}` : `/albums/${node.albumId}`);
        break;
      default:
        return;
    }
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <div
      ref={ref}
      role="tree"
      aria-label="Library: artists and albums"
      aria-multiselectable="true"
      className={c.tree}
      onKeyDown={onKeyDown}
    >
      {artists.map((artist, i) => (
        <ArtistNode
          key={artist.id}
          artist={artist}
          selected={selected}
          isExpanded={isExpanded(artist.id)}
          cursor={cursorArtist === artist.id ? liveCursor : null}
          tabStop={liveCursor === null && i === 0}
          onToggleArtist={onToggleArtist}
          onToggleAlbum={onToggleAlbum}
          onToggleExpanded={onToggleExpanded}
          onCursor={onCursor}
        />
      ))}
    </div>
  );
}
