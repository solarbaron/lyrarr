import { Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faMusic, faCompactDisc, faUser, faClockRotateLeft,
  faMagnifyingGlass, faGear, faServer, faHouse, faIdBadge, faBars, faXmark, faRightFromBracket,
  faLayerGroup
} from '@fortawesome/free-solid-svg-icons';
import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import { Button, Loader } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { getAuthStatus, authLogout, AUTH_EXPIRED_EVENT } from './api';
import { EventsProvider } from './events';
import ErrorBoundary from './components/ErrorBoundary';

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
const MassEditPage = lazy(() => import('./pages/MassEdit'));
const LoginPage = lazy(() => import('./pages/Login'));

// Always-loaded components
import ActivityFeed from './components/ActivityFeed';
import SearchBar from './components/SearchBar';
import ThemeToggle from './components/ThemeToggle';

// Backoff between /auth/status attempts while the server is unreachable
// (e.g. a reverse proxy answering 502 during a container restart).
const STATUS_RETRY_DELAYS = [1000, 2000, 4000, 8000, 15000];

export default function App() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authType, setAuthType] = useState<string | null>(null);
  const [statusAttempts, setStatusAttempts] = useState(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const checking = useRef(false);

  // Ask the server whether auth is required and whether this session has it.
  // Never guess on failure: assuming "no auth" leaves a blank, broken UI when
  // auth is actually enabled, so keep retrying with backoff instead.
  const checkAuth = useCallback(async (attempt = 0) => {
    clearTimeout(retryTimer.current);
    if (checking.current && attempt === 0) return;
    checking.current = true;
    try {
      const data = await getAuthStatus();
      setAuthType(data.authType);
      setAuthenticated(!!data.authenticated);
      setAuthChecked(true);
      setStatusAttempts(0);
      checking.current = false;
    } catch {
      setStatusAttempts(attempt + 1);
      const delay = STATUS_RETRY_DELAYS[Math.min(attempt, STATUS_RETRY_DELAYS.length - 1)];
      retryTimer.current = setTimeout(() => checkAuth(attempt + 1), delay);
    }
  }, []);

  useEffect(() => {
    checkAuth();
    return () => clearTimeout(retryTimer.current);
  }, [checkAuth]);

  // Any 401 from the API means the session (or the auth config) changed:
  // re-check status, which shows the login page if needed.
  useEffect(() => {
    const handler = () => { checkAuth(); };
    window.addEventListener(AUTH_EXPIRED_EVENT, handler);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handler);
  }, [checkAuth]);

  // Close sidebar on navigation (mobile)
  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  const handleLogin = () => {
    setAuthenticated(true);
    // Drop anything cached (or failed) while signed out.
    queryClient.resetQueries();
  };

  const handleLogout = async () => {
    try {
      await authLogout();
    } catch {
      // Signing out locally is still the right outcome.
    }
    setAuthenticated(false);
    queryClient.clear();
  };

  // Show loading while checking auth (and while the server is unreachable)
  if (!authChecked) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, justifyContent: 'center', alignItems: 'center', height: '100vh', background: 'var(--bg-primary)' }}>
        <Loader color="violet" size="lg" />
        {statusAttempts > 0 && (
          <>
            <div style={{ color: 'var(--text-secondary)', fontSize: 14 }} role="status">
              Can't reach the Lyrarr server — retrying…
            </div>
            <Button variant="subtle" color="violet" size="xs" onClick={() => { checking.current = false; checkAuth(); }}>
              Retry now
            </Button>
          </>
        )}
      </div>
    );
  }

  // Show login page if form auth is enabled and not authenticated
  if (authType === 'form' && !authenticated) {
    return (
      <Suspense fallback={<div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}><Loader color="violet" /></div>}>
        <LoginPage onLogin={handleLogin} />
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

  const bulkItems = [
    { label: 'Mass Edit', icon: faLayerGroup, path: '/bulk' },
  ];

  const systemItems = [
    { label: 'Profiles', icon: faIdBadge, path: '/profiles' },
    { label: 'Settings', icon: faGear, path: '/settings' },
    { label: 'System', icon: faServer, path: '/system' },
  ];

  return (
    <EventsProvider>
    <div className="app-layout">
      {/* Mobile hamburger */}
      <button
        className="mobile-hamburger"
        onClick={() => setSidebarOpen(!sidebarOpen)}
        aria-label={sidebarOpen ? 'Close navigation menu' : 'Open navigation menu'}
        aria-expanded={sidebarOpen}
      >
        <FontAwesomeIcon icon={sidebarOpen ? faXmark : faBars} />
      </button>

      {/* Mobile backdrop */}
      {sidebarOpen && <div className="sidebar-backdrop" aria-hidden="true" onClick={() => setSidebarOpen(false)} />}

      <nav className={`sidebar ${sidebarOpen ? 'open' : ''}`} aria-label="Main navigation">
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

          <div className="sidebar-section-label">Bulk Actions</div>
          {bulkItems.map(item => (
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
        <ErrorBoundary resetKey={location.pathname}>
        <Suspense fallback={<div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '50vh' }}><Loader color="var(--accent)" /></div>}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/artists" element={<ArtistsPage />} />
            <Route path="/artists/:artistId" element={<ArtistDetailPage />} />
            <Route path="/albums" element={<AlbumsPage />} />
            <Route path="/albums/:albumId" element={<AlbumDetailPage />} />
            <Route path="/wanted" element={<WantedPage />} />
            <Route path="/bulk" element={<MassEditPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/profiles" element={<ProfilesPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/system" element={<SystemPage />} />
          </Routes>
        </Suspense>
        </ErrorBoundary>
      </main>

      <ActivityFeed />
    </div>
    </EventsProvider>
  );
}
