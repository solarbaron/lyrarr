import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { globalSearch } from '../api';

interface SearchResults {
  artists: { id: number; name: string; poster?: string }[];
  albums: { id: number; title: string; cover?: string; artistName: string; year?: number }[];
  tracks: { id: number; title: string; albumId: number; artistName: string }[];
}

export default function SearchBar() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResults | null>(null);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (query.length < 2) {
      setResults(null);
      setOpen(false);
      return;
    }

    // Each query change cancels the previous request, and a response is only
    // applied if its effect is still current — so a slow reply for an older
    // query (or one arriving after the box was cleared) can't overwrite the
    // results or reopen the dropdown.
    const controller = new AbortController();
    let current = true;
    const timer = setTimeout(async () => {
      try {
        const data = await globalSearch(query, controller.signal);
        if (!current) return;
        setResults(data);
        setOpen(true);
      } catch {
        if (current) setResults(null);
      }
    }, 300);

    return () => {
      current = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const close = () => {
    setOpen(false);
    setQuery('');
    setResults(null);
  };

  const hasResults = results && (results.artists.length || results.albums.length || results.tracks.length);

  return (
    <div ref={containerRef} style={{ position: 'relative', padding: '0 12px', marginBottom: 8 }}>
      <input
        type="text"
        placeholder="Search..."
        aria-label="Search library"
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{
          width: '100%',
          padding: '10px 14px',
          borderRadius: 10,
          border: '1px solid var(--card-border)',
          background: 'rgba(25,20,50,0.4)',
          color: 'var(--text-primary)',
          fontSize: 13,
          outline: 'none',
          transition: 'border-color 0.2s',
        }}
        onFocus={() => results && setOpen(true)}
      />

      {open && (
        <div style={{
          position: 'absolute',
          top: '100%',
          left: 12,
          right: 12,
          marginTop: 4,
          background: 'var(--surface-bg, rgba(15,10,35,0.98))',
          border: '1px solid var(--card-border)',
          borderRadius: 12,
          boxShadow: '0 8px 40px rgba(0,0,0,0.6)',
          zIndex: 200,
          maxHeight: 360,
          overflowY: 'auto',
          backdropFilter: 'blur(20px)',
        }}>
          {!hasResults && (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
              No results for "{query}"
            </div>
          )}

          {results?.artists?.length ? (
            <div>
              <div style={{ padding: '10px 14px 4px', fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 1 }}>
                Artists
              </div>
              {results.artists.map(a => (
                <Link key={`a-${a.id}`} to={`/artists/${a.id}`} onClick={close} className="search-result-item">
                  {a.name}
                </Link>
              ))}
            </div>
          ) : null}

          {results?.albums?.length ? (
            <div>
              <div style={{ padding: '10px 14px 4px', fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 1 }}>
                Albums
              </div>
              {results.albums.map(a => (
                <Link key={`al-${a.id}`} to={`/albums/${a.id}`} onClick={close} className="search-result-item">
                  <span>{a.title}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8 }}>{a.artistName}</span>
                </Link>
              ))}
            </div>
          ) : null}

          {results?.tracks?.length ? (
            <div>
              <div style={{ padding: '10px 14px 4px', fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 1 }}>
                Tracks
              </div>
              {results.tracks.map(t => (
                <Link key={`t-${t.id}`} to={`/albums/${t.albumId}`} onClick={close} className="search-result-item">
                  <span>{t.title}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8 }}>{t.artistName}</span>
                </Link>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
