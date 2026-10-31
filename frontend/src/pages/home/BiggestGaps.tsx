import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faChevronDown, faChevronUp, faDownload } from '@fortawesome/free-solid-svg-icons';
import { batchDownload } from '../../api';
import { notifyError } from '../../notify';
import { CoverArt, IconButton, Meter, Panel } from '../../components/ui';
import { num, plural } from '../../format';
import type { ArtistCompletion } from './types';
import c from './Home.module.css';

const COLLAPSED = 10;

function GapRow({ artist, busy, onDownload }: { artist: ArtistCompletion; busy: boolean; onDownload: () => void }) {
  const href = `/artists/${artist.id}`;
  return (
    <li>
      <div className={c.gapRow}>
        <CoverArt kind="artist" name={artist.name} alt="" size={32} />
        <div className={c.gapName}>
          <Link to={href} className={c.gapLink} title={artist.name}>{artist.name}</Link>
          <span className={c.gapMeta}>
            {plural(artist.totalAlbums, 'album')} · {plural(artist.totalTracks, 'track')}
          </span>
        </div>
        <div className={c.gapMeters}>
          <span className={c.gapMeterLabel}>Covers</span>
          <Meter
            size="xs"
            width="100%"
            total={artist.totalAlbums}
            segments={[{ value: artist.coversDone, tone: 'success', label: 'Saved' }]}
            remainderLabel="Missing"
            withTooltip={false}
            aria-label={`${artist.name}: covers`}
          />
          <span className={c.gapPct}>{artist.coverPct}%</span>
          <span className={c.gapMeterLabel}>Lyrics</span>
          <Meter
            size="xs"
            width="100%"
            total={artist.totalTracks}
            segments={[{ value: artist.lyricsDone, tone: 'success', label: 'With lyrics' }]}
            remainderLabel="Missing"
            withTooltip={false}
            aria-label={`${artist.name}: lyrics`}
          />
          <span className={c.gapPct}>{artist.lyricsPct}%</span>
        </div>
        <div className={c.gapMissing}>
          <span className={c.gapMissingValue}>{num(artist.missing)}</span>
          <span className={c.gapMissingLabel}>missing</span>
        </div>
        <div className={c.gapAction} data-busy={busy || undefined}>
          <IconButton
            icon={faDownload}
            label={`Download missing for ${artist.name}`}
            variant="subtle"
            loading={busy}
            onClick={onDownload}
          />
        </div>
      </div>
    </li>
  );
}

/** Artists with the most missing items (DESIGN.md 8.1), from dashboard stats. */
export function BiggestGaps({ artists }: { artists: ArtistCompletion[] }) {
  const [expanded, setExpanded] = useState(false);
  const download = useMutation({
    mutationFn: (artist: ArtistCompletion) => batchDownload({ artistIds: [artist.id], type: 'all' }),
    onSuccess: (_, artist) => {
      notifications.show({ title: 'Download started', message: `Fetching missing covers and lyrics for ${artist.name}.` });
    },
    onError: (err, artist) => notifyError(`Couldn’t start the download for ${artist.name}`, err),
  });

  const shown = expanded ? artists : artists.slice(0, COLLAPSED);
  return (
    <Panel
      padding="none"
      title="Biggest gaps"
      subtitle="Artists with the most missing items"
      actions={(
        <Link to="/wanted" className={`ly-link ${c.recentAll}`}>Open Wanted</Link>
      )}
    >
      <ul className={c.gaps}>
        {shown.map(artist => (
          <GapRow
            key={artist.id}
            artist={artist}
            busy={download.isPending && download.variables?.id === artist.id}
            onDownload={() => download.mutate(artist)}
          />
        ))}
      </ul>
      {artists.length > COLLAPSED && (
        <div className={c.gapsFoot}>
          <Button
            variant="subtle"
            color="gray"
            size="xs"
            rightSection={<FontAwesomeIcon icon={expanded ? faChevronUp : faChevronDown} />}
            onClick={() => setExpanded(v => !v)}
            aria-expanded={expanded}
          >
            {expanded ? 'Show fewer' : `Show all ${artists.length}`}
          </Button>
        </div>
      )}
    </Panel>
  );
}
