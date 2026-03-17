import { Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faMusic, faCompactDisc, faUser, faClockRotateLeft,
  faMagnifyingGlass, faGear, faServer, faHouse, faIdBadge, faBars, faXmark, faRightFromBracket
} from '@fortawesome/free-solid-svg-icons';
import { useState, useEffect, lazy, Suspense } from 'react';
import { Button, Loader } from '@mantine/core';

// Lazy-loaded pages (code-split)
const ArtistsPage = lazy(() => import('./pages/Artists'));
const ArtistDetailPage = lazy(() => import('./pages/ArtistDetail'));
const AlbumsPage = lazy(() => import('./pages/Albums'));
const AlbumDetailPage = lazy(() => import('./pages/AlbumDetail'));
const WantedPage = lazy(() => import('./pages/Wanted'));
const HistoryPage = lazy(() => import('./pages/History'));
const SettingsPage = lazy(() => import('./pages/Settings'));
const SystemPage = lazy(() => import('./pages/System'));
const DashboardPage = lazy(() => import('./pages/Dashboard'));
const ProfilesPage = lazy(() => import('./pages/Profiles'));
const LoginPage = lazy(() => import('./pages/Login'));

// Always-loaded components
import ActivityFeed from './components/ActivityFeed';
import SearchBar from './components/SearchBar';
import ThemeToggle from './components/ThemeToggle';

export default function App() {
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authType, setAuthType] = useState<string | null>(null);

  // Check auth status on mount
  useEffect(() => {
    fetch('/api/auth/status')
      .then(r => r.json())
      .then(data => {
        setAuthType(data.authType);
        setAuthenticated(data.authenticated);
        setAuthChecked(true);
      })
      .catch(() => {
        // If we can't reach the server, assume no auth needed
        setAuthenticated(true);
        setAuthChecked(true);
      });
  }, []);

  // Listen for 401 auth expired events from axios interceptor
  useEffect(() => {
    const handler = () => {
      setAuthenticated(false);
    };
    window.addEventListener('lyrarr-auth-expired', handler);
    return () => window.removeEventListener('lyrarr-auth-expired', handler);
  }, []);

  // Close sidebar on navigation (mobile)
  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {}
    setAuthenticated(false);
  };

  // Show loading while checking auth
  if (!authChecked) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: 'var(--bg-primary)' }}>
        <Loader color="violet" size="lg" />
      </div>
    );
  }

  // Show login page if form auth is enabled and not authenticated
  if (authType === 'form' && !authenticated) {
    return (
      <Suspense fallback={<div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}><Loader color="violet" /></div>}>
        <LoginPage onLogin={() => setAuthenticated(true)} />
      </Suspense>
    );
  }

  const navItems = [
    { label: 'Dashboard', icon: faHouse, path: '/' },
    { label: 'Artists', icon: faUser, path: '/artists' },
    { label: 'Albums', icon: faCompactDisc, path: '/albums' },
  ];

  const metadataItems = [
    { label: 'Wanted', icon: faMagnifyingGlass, path: '/wanted' },
    { label: 'History', icon: faClockRotateLeft, path: '/history' },
  ];

  const systemItems = [
    { label: 'Profiles', icon: faIdBadge, path: '/profiles' },
    { label: 'Settings', icon: faGear, path: '/settings' },
    { label: 'System', icon: faServer, path: '/system' },
  ];

  return (
    <div className="app-layout">
      {/* Mobile hamburger */}
      <button className="mobile-hamburger" onClick={() => setSidebarOpen(!sidebarOpen)}>
        <FontAwesomeIcon icon={sidebarOpen ? faXmark : faBars} />
      </button>

      {/* Mobile backdrop */}
      {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}

      <nav className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-header">
          <div className="sidebar-logo">
            <div className="sidebar-logo-icon">
              <FontAwesomeIcon icon={faMusic} />
            </div>
            <h1>Lyrarr</h1>
          </div>
        </div>

        <SearchBar />

        <div className="sidebar-nav">
          <div className="sidebar-section-label">Library</div>
          {navItems.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
              end={item.path === '/'}
            >
              <span className="nav-link-icon">
                <FontAwesomeIcon icon={item.icon} />
              </span>
              <span>{item.label}</span>
            </NavLink>
          ))}

          <div className="sidebar-section-label">Metadata</div>
          {metadataItems.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              <span className="nav-link-icon">
                <FontAwesomeIcon icon={item.icon} />
              </span>
              <span>{item.label}</span>
            </NavLink>
          ))}

          <div className="sidebar-section-label">System</div>
          {systemItems.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              <span className="nav-link-icon">
                <FontAwesomeIcon icon={item.icon} />
              </span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </div>

        <div className="sidebar-footer">
          <ThemeToggle />
          {authType === 'form' && (
            <Button
              variant="subtle"
              color="gray"
              size="xs"
              leftSection={<FontAwesomeIcon icon={faRightFromBracket} />}
              onClick={handleLogout}
              style={{ marginTop: 8, width: '100%' }}
            >
              Sign Out
            </Button>
          )}
        </div>
      </nav>

      <main className="main-content">
        <Suspense fallback={<div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '50vh' }}><Loader color="var(--accent)" /></div>}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/artists" element={<ArtistsPage />} />
            <Route path="/artists/:artistId" element={<ArtistDetailPage />} />
            <Route path="/albums" element={<AlbumsPage />} />
            <Route path="/albums/:albumId" element={<AlbumDetailPage />} />
            <Route path="/wanted" element={<WantedPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/profiles" element={<ProfilesPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/system" element={<SystemPage />} />
          </Routes>
        </Suspense>
      </main>

      <ActivityFeed />
    </div>
  );
}
