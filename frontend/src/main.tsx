import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider, createTheme } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { BASE_URL } from './baseUrl';
import { notifyError } from './notify';

import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import './index.css';

const theme = createTheme({
  fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
  primaryColor: 'violet',
  defaultRadius: 'md',
  colors: {
    violet: [
      '#f3edff', '#e0d0ff', '#c9acff', '#b185ff', '#9a5eff',
      '#8b3dff', '#7c2dff', '#6a1bfa', '#5a11e0', '#4a0bc6'
    ],
  },
});

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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={theme} defaultColorScheme="dark">
        <Notifications position="top-right" />
        <BrowserRouter basename={BASE_URL || undefined}>
          <App />
        </BrowserRouter>
      </MantineProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
