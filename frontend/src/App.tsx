import { Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faMusic, faCompactDisc, faUser, faClockRotateLeft,
  faMagnifyingGlass, faGear, faServer, faHouse, faIdBadge, faBars, faXmark
} from '@fortawesome/free-solid-svg-icons';
import { useState, useEffect, lazy, Suspense } from 'react';
import { Loader } from '@mantine/core';

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

// Always-loaded components
import ActivityFeed from './components/ActivityFeed';
import SearchBar from './components/SearchBar';
import ThemeToggle from './components/ThemeToggle';

export default function App() {
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Close sidebar on navigation (mobile)
  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

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
