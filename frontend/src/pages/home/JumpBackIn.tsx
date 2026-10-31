import { Link } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faArrowRight } from '@fortawesome/free-solid-svg-icons';
import { CoverTile } from '../../components/ui';
import { useRecentAlbums } from '../../hooks/useRecentAlbums';
import c from './Home.module.css';

const MIN_ITEMS = 4;
const MAX_ITEMS = 8;

/** Recently opened albums (DESIGN.md 8.1); shown once there are at least four. */
export function JumpBackIn() {
  const { items } = useRecentAlbums();
  if (items.length < MIN_ITEMS) return null;
  return (
    <section aria-labelledby="home-recent">
      <div className={c.recentHead}>
        <h2 id="home-recent" className={c.recentTitle}>Jump back in</h2>
        <span className={c.recentSub}>Recently opened</span>
        <Link to="/albums" className={`ly-link ${c.recentAll}`}>
          All albums
          <FontAwesomeIcon icon={faArrowRight} />
        </Link>
      </div>
      <div className={c.recentGrid}>
        {items.slice(0, MAX_ITEMS).map(album => (
          <CoverTile
            key={album.id}
            href={`/albums/${album.id}`}
            art={{ src: album.cover, kind: 'album', name: album.title, alt: '' }}
            title={album.title}
            subtitle={[album.artistName, album.year].filter(Boolean).join(' · ')}
          />
        ))}
      </div>
    </section>
  );
}
