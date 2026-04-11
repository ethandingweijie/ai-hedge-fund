// ── SSE hook for streaming a pipeline run ──────────────────────────────────
import { useState, useCallback, useRef } from 'react';
import { startAnalysisRun } from '@/lib/api';
import type { ProgressEvent } from '@/lib/reportTypes';

export type RunState = 'idle' | 'running' | 'complete' | 'error';

export interface UseRunStreamResult {
  state: RunState;
  events: ProgressEvent[];
  /** Latest ProgressEvent keyed by phase name — used for per-section live status */
  phaseMap: Record<string, ProgressEvent>;
  /** Accumulated partial_data from all progress events — enables progressive section rendering */
  liveData: Record<string, unknown>;
  runId: string | null;
  error: string | null;
  start: (ticker: string, model?: string, agents?: string[]) => void;
  reset: () => void;
}

export function useRunStream(): UseRunStreamResult {
  const [state, setState] = useState<RunState>('idle');
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [phaseMap, setPhaseMap] = useState<Record<string, ProgressEvent>>({});
  const [liveData, setLiveData] = useState<Record<string, unknown>>({});
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setState('idle');
    setEvents([]);
    setPhaseMap({});
    setLiveData({});
    setRunId(null);
    setError(null);
  }, []);

  const start = useCallback(async (ticker: string, model = 'claude-sonnet-4-6', agents?: string[]) => {
    // Cancel any in-progress run
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState('running');
    setEvents([]);
    setRunId(null);
    setError(null);

    try {
      const res = await startAnalysisRun(ticker, model, agents);

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (controller.signal.aborted) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE messages — each message ends with \n\n
        const messages = buffer.split('\n\n');
        buffer = messages.pop() ?? '';

        for (const raw of messages) {
          if (!raw.trim()) continue;

          let eventType = 'message';
          let dataStr = '';

          for (const line of raw.split('\n')) {
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              dataStr = line.slice(5).trim();
            }
          }

          if (!dataStr) continue;

          try {
            const payload = JSON.parse(dataStr);

            if (eventType === 'progress') {
              const ev = payload as ProgressEvent;
              setEvents((prev) => [...prev, ev]);
              setPhaseMap((prev) => ({ ...prev, [ev.phase]: ev }));
              if (ev.partial_data) {
                setLiveData((prev) => ({ ...prev, ...ev.partial_data }));
              }
            } else if (eventType === 'complete') {
              setRunId(payload.run_id ?? null);
              setState('complete');
            } else if (eventType === 'error') {
              setError(payload.error ?? 'Unknown error');
              setState('error');
            }
            // heartbeat and start events are silently ignored
          } catch {
            // malformed JSON — skip
          }
        }
      }
    } catch (err: unknown) {
      if (controller.signal.aborted) return; // intentional abort
      setError(err instanceof Error ? err.message : String(err));
      setState('error');
    }
  }, []);

  return { state, events, phaseMap, liveData, runId, error, start, reset };
}
