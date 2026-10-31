// URL prefix for reverse-proxy subpath deployments (settings.general.base_url).
// The server injects it into index.html; normalise to "" or "/prefix" (no
// trailing slash) so it can be prepended to absolute paths.
export const BASE_URL = (window.__LYRARR_BASE_URL__ ?? '').replace(/\/+$/, '');

/** Prefix an absolute app path ("/api/...") with the configured base URL. */
export const withBase = (path: string) => `${BASE_URL}${path}`;
