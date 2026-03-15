import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
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
  const navigate = useNavigate();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (query.length < 2) {
      setResults(null);
      setOpen(false);
      return;
    }

    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await globalSearch(query);
        setResults(data);
        setOpen(true);
      } catch {
        setResults(null);
      }
    }, 300);

    return () => clearTimeout(debounceRef.current);
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

  const go = (path: string) => {
    navigate(path);
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
                <div key={`a-${a.id}`} onClick={() => go(`/artists/${a.id}`)} className="search-result-item">
                  {a.name}
                </div>
              ))}
            </div>
          ) : null}

          {results?.albums?.length ? (
            <div>
              <div style={{ padding: '10px 14px 4px', fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 1 }}>
                Albums
              </div>
              {results.albums.map(a => (
                <div key={`al-${a.id}`} onClick={() => go(`/albums/${a.id}`)} className="search-result-item">
                  <span>{a.title}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8 }}>{a.artistName}</span>
                </div>
              ))}
            </div>
          ) : null}

          {results?.tracks?.length ? (
            <div>
              <div style={{ padding: '10px 14px 4px', fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 1 }}>
                Tracks
              </div>
              {results.tracks.map(t => (
                <div key={`t-${t.id}`} onClick={() => go(`/albums/${t.albumId}`)} className="search-result-item">
                  <span>{t.title}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8 }}>{t.artistName}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
