import React from 'react';
import ReactDOM from 'react-dom/client';
import { Notifications } from '@mantine/notifications';
import { useMediaQuery } from '@mantine/hooks';
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { BASE_URL } from './baseUrl';
import { notifyError } from './notify';
import { ThemeProvider, applyInitialPreferences, applyTheme, resolveInitialTheme } from './theme/ThemeProvider';

// Layered Mantine styles: our unlayered CSS always wins without specificity fights.
import '@mantine/core/styles.layer.css';
import '@mantine/notifications/styles.layer.css';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import './theme/base.css';

// Same resolution as the pre-paint script in index.html; replaces its temporary background.
applyTheme(resolveInitialTheme());
applyInitialPreferences();

const queryClient = new QueryClient({
  // Safety net: a mutation without its own onError would otherwise fail
  // silently. Mutations that handle errors themselves are left alone.
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      if (!mutation.options.onError) notifyError('Error', error);
    },
  }),
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000,
    },
  },
});

/** Toasts bottom-right above any reserved bottom space; top-centre on phones (DESIGN.md 5.3). */
function Toasts() {
  const mobile = useMediaQuery('(max-width: 767px)');
  return (
    <Notifications
      position={mobile ? 'top-center' : 'bottom-right'}
      limit={4}
      zIndex={1000}
      containerWidth={380}
      autoClose={5000}
    />
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <Toasts />
        <BrowserRouter basename={BASE_URL || undefined}>
          <App />
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
