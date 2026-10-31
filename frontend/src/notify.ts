import { notifications } from '@mantine/notifications';

interface ErrorShape {
  response?: { status?: number; data?: { message?: unknown; error?: unknown; code?: unknown } | string };
  message?: unknown;
}

function body(err: unknown): { message?: unknown; error?: unknown; code?: unknown } | undefined {
  const data = (err as ErrorShape | null | undefined)?.response?.data;
  return data && typeof data === 'object' ? data : undefined;
}

/**
 * Best-effort human-readable message from an axios/JS error. The server's own
 * `message` wins and is shown verbatim (PLAN §1.8 error contract: e.g. "The audio
 * file isn't reachable at …", "This album has no folder of its own …"); then
 * `error`, then `fallback`, then the raw error text.
 */
export function errorMessage(err: unknown, fallback?: string): string {
  const data = body(err);
  const raw = (err as ErrorShape | null | undefined)?.message;
  for (const msg of [data?.message, data?.error, fallback, raw]) {
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return 'Request failed';
}

/** Machine reason of an API error (`code`: 'no_audio', 'row_changed', 'user_lyrics', …), if any. */
export function errorCode(err: unknown): string | undefined {
  const code = body(err)?.code;
  return typeof code === 'string' && code ? code : undefined;
}

/** HTTP status of an axios error, if any. */
export function errorStatus(err: unknown): number | undefined {
  return (err as ErrorShape | null | undefined)?.response?.status;
}

/**
 * Error toast for a failed request: the server's message verbatim. 409s (the
 * server refused on purpose: folder not reachable, row changed, run in progress)
 * read as a warning; everything else as danger.
 */
export function notifyError(title: string, err?: unknown, fallback?: string) {
  const conflict = errorStatus(err) === 409;
  notifications.show({
    title,
    message: errorMessage(err, fallback),
    color: conflict ? 'warning' : 'danger',
    autoClose: conflict ? 10000 : undefined,
  });
}
