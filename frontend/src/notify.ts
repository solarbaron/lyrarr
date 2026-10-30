import { notifications } from '@mantine/notifications';

/**
 * Best-effort human-readable message from an axios/JS error. The server's own
 * `message` (e.g. "Another batch translate is already running" on a 409) wins;
 * otherwise `fallback`, then the raw error text.
 */
export function errorMessage(err: unknown, fallback?: string): string {
  const e = err as { response?: { data?: { message?: unknown; error?: unknown } }; message?: unknown };
  const data = e?.response?.data;
  for (const msg of [data?.message, data?.error, fallback, e?.message]) {
    if (typeof msg === 'string' && msg) return msg;
  }
  return 'Request failed';
}

/** Show a red error toast for a failed request. */
export function notifyError(title: string, err?: unknown, fallback?: string) {
  notifications.show({ title, message: errorMessage(err, fallback), color: 'red' });
}
