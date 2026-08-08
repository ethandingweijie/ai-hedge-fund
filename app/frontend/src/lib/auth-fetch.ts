/**
 * auth-fetch.ts
 * ─────────────
 * Attaches the session token to every request aimed at our own API.
 *
 * Why an interceptor rather than per-call headers: the backend now denies
 * unauthenticated requests on all non-public routes (app/backend/auth_gate.py),
 * but only ~18 of the many call sites in lib/api.ts and services/*.ts were
 * passing an Authorization header. Patching each one leaves the same failure
 * mode in place — the next call site added forgets the header and 401s. This
 * wraps window.fetch once, so any current or future caller is covered,
 * including the SSE streams (they use fetch streaming, not EventSource, which
 * cannot send headers at all).
 *
 * Requests to third-party origins are passed through untouched — the token must
 * never leak off our own API.
 *
 * Install once, before anything issues a request. See main.tsx.
 */
import { API_BASE_URL } from '@/config';
import { getStoredToken } from '@/contexts/auth-context';

let installed = false;

/** True when `url` targets our own backend. */
function isApiRequest(url: string): boolean {
  if (!API_BASE_URL) return url.startsWith('/');
  if (url.startsWith(API_BASE_URL)) return true;
  // Relative URL in a dev setup where API_BASE_URL is an absolute origin.
  return url.startsWith('/') && !url.startsWith('//');
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

export function installAuthFetch(): void {
  if (installed) return;
  installed = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = requestUrl(input);
    const token = getStoredToken();

    if (!token || !isApiRequest(url)) {
      return originalFetch(input, init);
    }

    // Respect an Authorization header the caller set explicitly.
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    if (!headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    return originalFetch(input, { ...init, headers });
  };
}
