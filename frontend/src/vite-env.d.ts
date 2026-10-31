/// <reference types="vite/client" />

interface Window {
  /**
   * URL prefix the app is served under (e.g. "/lyrarr"), injected into
   * index.html by the server from `settings.general.base_url`. Empty string
   * (or absent, e.g. under the Vite dev server) when served from the root.
   */
  __LYRARR_BASE_URL__?: string;
}
