/**
 * useNewsStream — keep an open page current without polling.
 *
 * Subscribes to GET /analysis/news/stream and prepends items as they arrive.
 * The caller loads its snapshot from the store first; this only carries what
 * is published afterwards, which is why the endpoint replays nothing.
 *
 * Two constraints come from the codebase rather than from preference:
 *
 *   1. `fetch` + body.getReader(), NOT EventSource. EventSource cannot send
 *      an Authorization header (lib/auth-fetch.ts), and every route here is
 *      behind a bearer token. Frame parsing follows useRunStream.
 *
 *   2. iOS Safari kills a stream on screen lock or tab switch. The pipeline
 *      run needed four mechanisms to survive that (see active-run-context);
 *      news needs far less, because there is no progress state to rebuild —
 *      a phone returning from sleep just re-reads the snapshot, which is a
 *      local read. So: reconnect on visibilitychange, and tell the caller to
 *      re-fetch rather than trusting that nothing was missed while asleep.
 */

import { useEffect, useRef } from 'react';
import { API_BASE_URL } from '@/config';
import { getStoredToken } from '@/contexts/auth-context';
import type { NewsArticle } from '@/lib/api';

interface UseNewsStreamOptions {
  /** Tickers to subscribe to. Empty means "skip" — not "everything". */
  tickers: string[];
  /** Called for each item pushed onto the open connection. */
  onItem: (article: NewsArticle) => void;
  /**
   * Called after a reconnect, because anything published while the connection
   * was dead was never delivered. Re-read the snapshot rather than assume.
   */
  onResync?: () => void;
  enabled?: boolean;
}

export function useNewsStream({ tickers, onItem, onResync, enabled = true }: UseNewsStreamOptions) {
  // Held in refs so a changing callback identity does not tear the stream
  // down and rebuild it on every parent render.
  const onItemRef = useRef(onItem);
  const onResyncRef = useRef(onResync);
  useEffect(() => { onItemRef.current = onItem; }, [onItem]);
  useEffect(() => { onResyncRef.current = onResync; }, [onResync]);

  const key = tickers.slice().sort().join(',');

  useEffect(() => {
    if (!enabled || !key) return;

    let cancelled = false;
    let controller: AbortController | null = null;
    let everConnected = false;

    const connect = async () => {
      if (cancelled) return;
      controller?.abort();
      controller = new AbortController();

      // A reconnect means there is a gap: items published while the stream
      // was down were not buffered for us (the endpoint does not replay).
      if (everConnected) onResyncRef.current?.();
      everConnected = true;

      try {
        const token = getStoredToken();
        const res = await fetch(
          `${API_BASE_URL}/analysis/news/stream?tickers=${encodeURIComponent(key)}`,
          {
            signal: controller.signal,
            headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          },
        );
        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        for (;;) {
          const { done, value } = await reader.read();
          if (done || cancelled || controller.signal.aborted) break;

          buffer += decoder.decode(value, { stream: true });
          const messages = buffer.split('\n\n');
          buffer = messages.pop() ?? '';

          for (const raw of messages) {
            if (!raw.trim()) continue;
            // Comment frames (": keep-alive") carry no event:/data: lines and
            // fall through this parser untouched, which is the point of them.
            let eventType = 'message';
            let dataStr = '';
            for (const line of raw.split('\n')) {
              if (line.startsWith('event:')) eventType = line.slice(6).trim();
              else if (line.startsWith('data:')) dataStr = line.slice(5).trim();
            }
            if (eventType !== 'news_item' || !dataStr) continue;
            try {
              onItemRef.current(JSON.parse(dataStr) as NewsArticle);
            } catch {
              /* a malformed frame is not worth tearing the stream down for */
            }
          }
        }
      } catch {
        /* aborted, offline, or the server closed — visibility/retry handles it */
      }
    };

    connect();

    // iOS drops the connection when the screen locks; reconnect on return.
    const onVisible = () => {
      if (document.visibilityState === 'visible' && !cancelled) connect();
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      cancelled = true;
      controller?.abort();
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [key, enabled]);
}
