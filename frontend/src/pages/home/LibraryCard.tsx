import { Link } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faChevronRight, faClock, faIdBadge, faLanguage, type IconDefinition } from '@fortawesome/free-solid-svg-icons';
import { Panel } from '../../components/ui';
import { num } from '../../format';
import c from './Home.module.css';

export interface LibraryCardProps {
  artists: number | undefined;
  albums: number | undefined;
  tracks: number | undefined;
  profiles: number | undefined;
  untimed: number | undefined;
  undetected: number | undefined;
}

function Stat({ value, label }: { value: number | undefined; label: string }) {
  return (
    <div className={c.libraryStat}>
      <dt className={c.libraryLabel}>{label}</dt>
      <dd className={c.libraryValue}>{num(value)}</dd>
    </div>
  );
}

function Row({ to, icon, label, count }: { to: string; icon: IconDefinition; label: string; count: number | undefined }) {
  return (
    <li>
      <Link to={to} className={c.libraryLink}>
        <FontAwesomeIcon icon={icon} className={c.libraryLinkIcon} />
        <span className={c.libraryLinkLabel}>{label}</span>
        <span className={c.libraryLinkCount} data-zero={!count || undefined}>{num(count)}</span>
        <FontAwesomeIcon icon={faChevronRight} className={c.libraryLinkArrow} />
      </Link>
    </li>
  );
}

/** Library size and the smaller lyric to-dos (DESIGN.md 8.1). */
export function LibraryCard({ artists, albums, tracks, profiles, untimed, undetected }: LibraryCardProps) {
  return (
    <Panel title="Library" className={c.libraryPanel}>
      <div className={c.library}>
        <dl className={c.libraryStats}>
          <Stat value={artists} label={artists === 1 ? 'artist' : 'artists'} />
          <Stat value={albums} label={albums === 1 ? 'album' : 'albums'} />
          <Stat value={tracks} label={tracks === 1 ? 'track' : 'tracks'} />
        </dl>
        <ul className={c.libraryLinks}>
          <Row to="/profiles" icon={faIdBadge} label={profiles === 1 ? 'Profile' : 'Profiles'} count={profiles} />
          <Row to="/wanted?tab=untimed" icon={faClock} label="Untimed lyrics" count={untimed} />
          <Row to="/wanted?tab=language" icon={faLanguage} label="Undetected language" count={undetected} />
        </ul>
      </div>
    </Panel>
  );
}
