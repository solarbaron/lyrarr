import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:6868',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../lyrarr/frontend',
    emptyOutDir: true,
  },
  experimental: {
    // Subpath deployments (settings.general.base_url): index.html keeps
    // absolute /assets/ URLs, which the server rewrites to <base>/assets/ when
    // serving it (a relative "./assets" would break on deep routes like
    // /albums/12). Everything referenced from JS (lazy page chunks, preloads)
    // and CSS (fonts) is emitted relative to the referencing file, so it
    // follows wherever index.html loaded the entry from — no rewrite needed.
    renderBuiltUrl(_filename, { hostType }) {
      return hostType === 'html' ? undefined : { relative: true };
    },
  },
});
