import { Routes, Route, useLocation } from 'react-router-dom';
import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import { Button, Loader } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { getAuthStatus, authLogout, AUTH_EXPIRED_EVENT } from './api';
import { EventsProvider } from './events';
import { JobsProvider } from './jobs';
import ErrorBoundary from './components/ErrorBoundary';
import { AppShell } from './components/shell/AppShell';
import boot from './components/shell/Boot.module.css';

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
// Dev-only component gallery (DESIGN.md 7); tree-shaken from production builds.
const UiGalleryPage = import.meta.env.DEV ? lazy(() => import('./pages/UiGallery')) : null;

// Backoff between /auth/status attempts while the server is unreachable
// (e.g. a reverse proxy answering 502 during a container restart).
const STATUS_RETRY_DELAYS = [1000, 2000, 4000, 8000, 15000];

export default function App() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authType, setAuthType] = useState<string | null>(null);
  const [username, setUsername] = useState<string | undefined>(undefined);
  const [statusAttempts, setStatusAttempts] = useState(0);
  // Why the last /auth/status attempt failed: the server answered 429 (its shared
  // rate limiter) or it couldn't be reached at all.
  const [statusBusy, setStatusBusy] = useState(false);
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
      setUsername(data.username || undefined);
      setAuthChecked(true);
      setStatusAttempts(0);
      checking.current = false;
    } catch (err) {
      setStatusBusy((err as { response?: { status?: number } })?.response?.status === 429);
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

  const handleLogin = () => {
    setAuthenticated(true);
    // Drop anything cached (or failed) while signed out.
    queryClient.resetQueries();
    // Pick up the signed-in username for the account menu.
    getAuthStatus().then(data => setUsername(data.username || undefined)).catch(() => {});
  };

  const handleLogout = useCallback(async () => {
    try {
      await authLogout();
    } catch {
      // Signing out locally is still the right outcome.
    }
    setAuthenticated(false);
    setUsername(undefined);
    queryClient.clear();
  }, [queryClient]);

  // Show loading while checking auth (and while the server is unreachable)
  if (!authChecked) {
    return (
      <div className={boot.screen}>
        <Loader size="lg" aria-label="Connecting to Lyrarr" />
        {statusAttempts > 0 && (
          <>
            <div className={boot.message} role="status">
              {statusBusy
                ? 'The Lyrarr server is busy (too many requests) — retrying…'
                : "Can't reach the Lyrarr server — retrying…"}
            </div>
            <Button variant="subtle" color="gray" size="xs" onClick={() => { checking.current = false; checkAuth(); }}>
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
      <Suspense fallback={<div className={boot.screen}><Loader /></div>}>
        <LoginPage onLogin={handleLogin} />
      </Suspense>
    );
  }

  return (
    <EventsProvider>
      <JobsProvider>
        <AppShell authType={authType} username={username} onLogout={handleLogout}>
          <ErrorBoundary resetKey={location.pathname}>
            <Suspense fallback={<div className={boot.route}><Loader /></div>}>
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
                {UiGalleryPage && <Route path="/__ui" element={<UiGalleryPage />} />}
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </AppShell>
      </JobsProvider>
    </EventsProvider>
  );
}
