import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Menu } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { faAlignLeft, faDownload, faEllipsis, faImage, faUser } from '@fortawesome/free-solid-svg-icons';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  DataTable, IconButton, LyricsMeter, StatusDot, TitleCell,
  type Column, type DataTableProps,
} from '../ui';
import { useAlbumCounts, type AlbumCounts } from '../../hooks/useAlbumCounts';
import { parseGenres } from '../../format';
import type { Album, Profile } from '../../types';
import CoverSearchModal from '../CoverSearchModal';
import { useAssignProfileMutation, useDownloadMutation } from './actions';
import { SetProfileSubmenu, profileLabel } from './LibraryControls';
import c from './library.module.css';

/** An album row from GET /albums (artist and profile names joined). */
export type AlbumRow = Album & { profileName?: string };

export interface AlbumTableProps {
  'aria-label': string;
  rows: AlbumRow[];
  profiles: Profile[];
  loading?: boolean;
  refetching?: boolean;
  error?: DataTableProps<AlbumRow>['error'];
  empty?: ReactNode;
  footer?: ReactNode;
  selection?: DataTableProps<AlbumRow>['selection'];
  selectAllMatching?: DataTableProps<AlbumRow>['selectAllMatching'];
  sort?: DataTableProps<AlbumRow>['sort'];
  /** Artist detail: no Artist or Profile column, no "Open artist" action; shows Type instead. */
  hideArtist?: boolean;
  /** false inside an existing Panel padding="none". */
  framed?: boolean;
  breakpoints?: DataTableProps<AlbumRow>['breakpoints'];
}

/**
 * "Album · Alternative Rock, Indie Rock", plus the artist (and year) when their
 * columns are hidden: mobile cards read "Cities Aviv · 2018 · Album · Experimental".
 */
function secondLine(album: AlbumRow, { artist, year, type }: { artist: boolean; year: boolean; type: boolean }): string {
  const genres = parseGenres(album.genres).slice(0, 3).join(', ');
  return [
    artist ? album.artistName : null,
    year && album.year ? String(album.year) : null,
    type ? album.albumType : null,
    genres,
  ].filter(Boolean).join(' · ');
}

/** Lyrics cell: StatusDot + LyricsMeter "n/m" when library counts are known, else the dot alone (8.0). */
export function AlbumLyricsCell({ status, counts }: { status: string | null | undefined; counts?: AlbumCounts }) {
  if (!counts || counts.total === 0) return <StatusDot domain="albumLyrics" status={status} />;
  return (
    <span className={c.lyricsCell}>
      <StatusDot className={c.lyricsDot} domain="albumLyrics" status={status} />
      <LyricsMeter
        synced={counts.synced}
        plain={counts.plain}
        instrumental={counts.instrumental}
        total={counts.total}
        aria-label="Tracks with lyrics"
      />
    </span>
  );
}

/**
 * The shared album list (DESIGN.md 8.4), used by Albums and Artist detail:
 * thumb + title + "Album · genres", artist, year, profile, cover and lyrics
 * status, and row actions (Search covers, Download missing lyrics, ⋯).
 */
export function AlbumTable({
  rows, profiles, loading, refetching, error, empty, footer, selection, selectAllMatching, sort,
  hideArtist, framed, breakpoints, 'aria-label': ariaLabel,
}: AlbumTableProps) {
  const { albums: albumCounts } = useAlbumCounts();
  const download = useDownloadMutation();
  const assign = useAssignProfileMutation();
  const [coverAlbum, setCoverAlbum] = useState<AlbumRow | null>(null);
  // Mobile cards have no column headers: the status items carry their own label.
  const isMobile = useMediaQuery('(max-width: 767px)') ?? false;

  // Mobile cards have no column headers: name each status ("Cover ○ Missing ·
  // Lyrics ○ Missing 0/16") so the two can be told apart without a tooltip.
  const withCardLabel = useCallback((label: string, node: ReactNode) => (isMobile ? (
    <span className={c.cardStatusItem}>
      <span className={c.cardStatusLabel}>{label}</span>
      {node}
    </span>
  ) : node), [isMobile]);

  const columns = useMemo<Column<AlbumRow>[]>(() => {
    const cols: Column<AlbumRow>[] = [
      {
        key: 'album',
        header: 'Album',
        sortKey: sort ? 'title' : undefined,
        priority: 1,
        mobile: 'title',
        // When the Artist column (priority 2) or, on Artist detail, the Type column
        // (priority 3) hides, its value joins the second line under the title. Mobile
        // cards have no meta line, so artist and year join it there too.
        cell: (a, { hidden, mobile }) => (
          <TitleCell
            title={a.title}
            href={`/albums/${a.lidarrAlbumId}`}
            art={{ src: a.cover, kind: 'album', name: a.title }}
            secondary={secondLine(a, {
              artist: !hideArtist && (hidden.has('artist') || mobile),
              year: mobile,
              type: !hideArtist || hidden.has('type') || mobile,
            }) || undefined}
          />
        ),
      },
    ];
    if (!hideArtist) {
      cols.push({
        key: 'artist',
        header: 'Artist',
        width: '16%',
        priority: 2,
        mobile: 'hidden',
        cell: a => (a.artistName
          ? <Link to={`/artists/${a.artistId}`} className={c.artistLink} title={a.artistName}>{a.artistName}</Link>
          : <span className={c.mutedText}>—</span>),
      });
    }
    cols.push({
      key: 'year',
      header: 'Year',
      width: 72,
      align: 'right',
      // Artist detail keeps the year longer than the Type column.
      priority: hideArtist ? 2 : 3,
      sortKey: sort ? 'year' : undefined,
      mobile: 'hidden',
      skeleton: 'num',
      cell: a => (a.year ? <span className={c.num}>{a.year}</span> : <span className={c.mutedText}>—</span>),
    });
    if (hideArtist) {
      cols.push({
        key: 'type',
        header: 'Type',
        width: 84,
        priority: 3,
        mobile: 'hidden',
        cell: a => (a.albumType ? <span className={c.secondaryText}>{a.albumType}</span> : <span className={c.mutedText}>—</span>),
      });
    }
    if (!hideArtist) cols.push({
        key: 'profile',
        header: 'Profile',
        width: 112,
        priority: 3,
        mobile: 'hidden',
        cell: a => {
          const name = profileLabel(a.profileName);
          return name ? <span className={c.secondaryText} title={name}>{name}</span> : <span className={c.mutedText}>—</span>;
        },
    });
    cols.push(
      {
        key: 'cover',
        header: 'Cover',
        width: 112,
        priority: 1,
        mobile: 'status',
        skeleton: 'chip',
        cell: a => withCardLabel('Cover', <span className={c.lyricsCell}><StatusDot domain="cover" status={a.cover_status} /></span>),
      },
      {
        key: 'lyrics',
        header: 'Lyrics',
        // Dot label (76: Complete / Partial / Missing) + gap + meter (64) + padding.
        width: 176,
        priority: 1,
        sortKey: sort ? 'lyricsStatus' : undefined,
        mobile: 'status',
        skeleton: 'meter',
        cell: a => withCardLabel('Lyrics', <AlbumLyricsCell status={a.lyrics_status} counts={albumCounts.get(a.lidarrAlbumId)} />),
      },
    );
    return cols;
  }, [hideArtist, sort, albumCounts, withCardLabel]);

  const rowActions = useCallback((a: AlbumRow) => (
    <>
      <IconButton icon={faImage} label="Search covers" onClick={() => setCoverAlbum(a)} />
      <IconButton
        icon={faAlignLeft}
        label="Download missing lyrics"
        onClick={() => download.mutate({ target: { albumIds: [a.lidarrAlbumId] }, type: 'lyrics', message: `Downloading missing lyrics for ${a.title}...` })}
      />
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <IconButton icon={faEllipsis} label={`More actions for ${a.title}`} />
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item
            leftSection={<FontAwesomeIcon icon={faDownload} />}
            onClick={() => download.mutate({ target: { albumIds: [a.lidarrAlbumId] }, type: 'all', message: `Downloading missing covers and lyrics for ${a.title}...` })}
          >
            Download all missing
          </Menu.Item>
          <SetProfileSubmenu
            profiles={profiles}
            currentId={a.profileId}
            onPick={p => assign.mutate({ target: { albumIds: [a.lidarrAlbumId] }, profileId: p.id, profileName: p.name })}
          />
          {!hideArtist && (
            <Menu.Item component={Link} to={`/artists/${a.artistId}`} leftSection={<FontAwesomeIcon icon={faUser} />}>
              Open artist
            </Menu.Item>
          )}
        </Menu.Dropdown>
      </Menu>
    </>
  ), [download, assign, profiles, hideArtist]);

  return (
    <>
      <DataTable<AlbumRow>
        aria-label={ariaLabel}
        columns={columns}
        rows={rows}
        getRowId={getAlbumId}
        getRowLabel={a => a.title}
        noun="album"
        loading={loading}
        refetching={refetching}
        error={error}
        empty={empty}
        footer={footer}
        rowHref={a => `/albums/${a.lidarrAlbumId}`}
        selection={selection}
        selectAllMatching={selectAllMatching}
        sort={sort}
        rowActions={rowActions}
        rowActionsWidth={120}
        framed={framed}
        breakpoints={breakpoints}
      />
      <CoverSearchModal
        albumId={coverAlbum?.lidarrAlbumId ?? 0}
        album={coverAlbum ?? undefined}
        opened={!!coverAlbum}
        onClose={() => setCoverAlbum(null)}
      />
    </>
  );
}

export function getAlbumId(a: Album): number {
  return a.lidarrAlbumId;
}
