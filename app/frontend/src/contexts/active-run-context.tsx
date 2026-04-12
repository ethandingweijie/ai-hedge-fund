/**
 * ActiveRunContext — global state tracking in-flight and recently-completed
 * pipeline analysis runs. Handles iOS Safari SSE disconnects gracefully:
 *
 * 1. SSE stream runs while the page is active
 * 2. When iOS kills the connection (screen lock / tab switch), state is
 *    "reconnecting" — NOT "error"
 * 3. On visibility change (user returns), polls the backend to check
 *    if the run completed while the phone was asleep
 * 4. Progress (phaseMap) is persisted to sessionStorage so it survives
 */
import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { startAnalysisRun, getRunResult } from '@/lib/api';
import { API_BASE_URL } from '@/config';
import type { ProgressEvent, RunResult } from '@/lib/reportTypes';

export type RunState = 'idle' | 'running' | 'reconnecting' | 'complete' | 'error';

export interface ActiveRunInfo {
  ticker: string;
  startedAt: string;
}

export interface CompletedRunInfo {
  ticker: string;
  runId: string;
  completedAt: string;
}

interface ActiveRunContextValue {
  activeRun: ActiveRunInfo | null;
  recentlyCompleted: CompletedRunInfo | null;
  startRun: (ticker: string) => void;
  completeRun: (ticker: string, runId: string) => void;
  clearCompleted: () => void;
  clearActive: () => void;
  streamState: RunState;
  streamEvents: ProgressEvent[];
  phaseMap: Record<string, ProgressEvent>;
  liveData: Record<string, unknown>;
  streamRunId: string | null;
  streamError: string | null;
  streamTotalPhases: number;
  streamExpectedPhases: string[];
  liveResult: RunResult | null;
  setLiveResult: (r: RunResult | null) => void;
  startStream: (ticker: string, model?: string, agents?: string[]) => void;
  resetStream: () => void;
}

const ActiveRunContext = createContext<ActiveRunContextValue>({
  activeRun: null,
  recentlyCompleted: null,
  startRun: () => {},
  completeRun: () => {},
  clearCompleted: () => {},
  clearActive: () => {},
  streamState: 'idle',
  streamEvents: [],
  phaseMap: {},
  liveData: {},
  streamRunId: null,
  streamError: null,
  streamTotalPhases: 12,
  streamExpectedPhases: [],
  liveResult: null,
  setLiveResult: () => {},
  startStream: () => {},
  resetStream: () => {},
});

export function ActiveRunProvider({ children }: { children: React.ReactNode }) {
  // ── Run-coordination state ────────────────────────────────────────────────
  const [activeRun, setActiveRun] = useState<ActiveRunInfo | null>(() => {
    try {
      const stored = sessionStorage.getItem('activeRun');
      if (stored) {
        const parsed = JSON.parse(stored) as ActiveRunInfo;
        const age = Date.now() - new Date(parsed.startedAt).getTime();
        if (age < 30 * 60 * 1000) return parsed;
        sessionStorage.removeItem('activeRun');
      }
    } catch { /* ignore */ }
    return null;
  });
  const activeRunRef = useRef(activeRun);
  activeRunRef.current = activeRun;

  const [recentlyCompleted, setRecentlyCompleted] = useState<CompletedRunInfo | null>(null);

  const startRun = useCallback((ticker: string) => {
    const run = { ticker, startedAt: new Date().toISOString() };
    setActiveRun(run);
    sessionStorage.setItem('activeRun', JSON.stringify(run));
    setRecentlyCompleted(null);
  }, []);

  const completeRun = useCallback((ticker: string, runId: string) => {
    setActiveRun(null);
    sessionStorage.removeItem('activeRun');
    setRecentlyCompleted({ ticker, runId, completedAt: new Date().toISOString() });
  }, []);

  const clearCompleted = useCallback(() => setRecentlyCompleted(null), []);
  const clearActive = useCallback(() => {
    setActiveRun(null);
    sessionStorage.removeItem('activeRun');
  }, []);

  // ── SSE stream state ─────────────────────────────────────────────────────
  const [streamState, setStreamState] = useState<RunState>('idle');
  const [streamEvents, setStreamEvents] = useState<ProgressEvent[]>([]);
  const [phaseMap, _setPhaseMap] = useState<Record<string, ProgressEvent>>(() => {
    try {
      const stored = sessionStorage.getItem('phaseMap');
      if (stored) return JSON.parse(stored);
    } catch { /* ignore */ }
    return {};
  });
  const setPhaseMap = useCallback((updater: Record<string, ProgressEvent> | ((prev: Record<string, ProgressEvent>) => Record<string, ProgressEvent>)) => {
    _setPhaseMap(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      try { sessionStorage.setItem('phaseMap', JSON.stringify(next)); } catch { /* quota */ }
      return next;
    });
  }, []);
  const [liveData, setLiveData] = useState<Record<string, unknown>>({});
  const [streamRunId, setStreamRunId] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamTotalPhases, setStreamTotalPhases] = useState<number>(() => {
    try {
      const stored = sessionStorage.getItem('streamTotalPhases');
      if (stored) return parseInt(stored, 10) || 12;
    } catch { /* ignore */ }
    return 12;
  });
  const [liveResult, setLiveResult] = useState<RunResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastStreamTickerRef = useRef<string | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Cleanup polling on unmount ────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, []);

  // ── Poll backend for completion (used when SSE disconnects) ───────────────
  const startPolling = useCallback((ticker: string) => {
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);

    setStreamState('reconnecting');
    setStreamError(null);

    let attempts = 0;
    const maxAttempts = 60; // 10 minutes at 10s intervals

    pollIntervalRef.current = setInterval(async () => {
      attempts++;
      const currentRun = activeRunRef.current;
      if (!currentRun) {
        if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        return;
      }

      try {
        const res = await fetch(
          `${API_BASE_URL}/analysis/runs?page=1&page_size=1&ticker=${encodeURIComponent(ticker)}`,
          { headers: { 'Content-Type': 'application/json' } }
        );
        if (!res.ok) return;
        const data = await res.json();
        const runs = data.items || data.runs || [];

        if (runs.length > 0) {
          const latest = runs[0];
          const runTime = new Date(latest.run_at).getTime();
          const startTime = new Date(currentRun.startedAt).getTime();

          // Run completed after we started — success
          if (runTime >= startTime - 60000 && latest.final_action) {
            if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
            setStreamRunId(latest.run_id);
            setStreamState('complete');
            setActiveRun(null);
            sessionStorage.removeItem('activeRun');
            setRecentlyCompleted({
              ticker: ticker.toUpperCase(),
              runId: latest.run_id,
              completedAt: new Date().toISOString(),
            });
            try {
              const result = await getRunResult(latest.run_id);
              setLiveResult(result);
            } catch { /* ignore */ }
            return;
          }
        }
      } catch { /* network error, keep trying */ }

      if (attempts >= maxAttempts) {
        if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        setStreamError('Analysis is taking longer than expected — check History for results');
        setStreamState('error');
      }
    }, 10000);
  }, []);

  // ── Visibility change handler — resume when user returns ──────────────────
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState !== 'visible') return;
      const current = activeRunRef.current;
      if (!current) return;

      // User returned — if we're in reconnecting state or the stream died,
      // poll the backend immediately
      const ticker = current.ticker;
      const checkNow = async () => {
        try {
          const res = await fetch(
            `${API_BASE_URL}/analysis/runs?page=1&page_size=1&ticker=${encodeURIComponent(ticker)}`,
            { headers: { 'Content-Type': 'application/json' } }
          );
          if (!res.ok) return;
          const data = await res.json();
          const runs = data.items || data.runs || [];

          if (runs.length > 0) {
            const latest = runs[0];
            const runTime = new Date(latest.run_at).getTime();
            const startTime = new Date(current.startedAt).getTime();

            if (runTime >= startTime - 60000 && latest.final_action) {
              // Run completed while phone was asleep!
              if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
              setStreamRunId(latest.run_id);
              setStreamState('complete');
              setActiveRun(null);
              sessionStorage.removeItem('activeRun');
              setRecentlyCompleted({
                ticker: ticker.toUpperCase(),
                runId: latest.run_id,
                completedAt: new Date().toISOString(),
              });
              try {
                const result = await getRunResult(latest.run_id);
                setLiveResult(result);
              } catch { /* ignore */ }
            }
          }
        } catch { /* ignore */ }
      };
      checkNow();
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  const resetStream = useCallback(() => {
    abortRef.current?.abort();
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    setStreamState('idle');
    setStreamEvents([]);
    setPhaseMap({});
    setLiveData({});
    setStreamRunId(null);
    setStreamError(null);
    setLiveResult(null);
    try {
      sessionStorage.removeItem('phaseMap');
      sessionStorage.removeItem('streamTotalPhases');
    } catch { /* ignore */ }
  }, []);

  const startStream = useCallback(async (ticker: string, model = 'claude-sonnet-4-6', agents?: string[]) => {
    abortRef.current?.abort();
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    const controller = new AbortController();
    abortRef.current = controller;

    setStreamState('running');
    setStreamEvents([]);
    const prevTicker = lastStreamTickerRef.current;
    if (prevTicker && prevTicker !== ticker.toUpperCase()) {
      setPhaseMap({});
      try { sessionStorage.removeItem('phaseMap'); sessionStorage.removeItem('streamTotalPhases'); } catch { /* ignore */ }
    }
    lastStreamTickerRef.current = ticker.toUpperCase();
    setLiveData({});
    setStreamRunId(null);
    setStreamError(null);
    setLiveResult(null);

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

            if (eventType === 'start') {
              if (payload.total_done_phases) {
                setStreamTotalPhases(payload.total_done_phases);
                try { sessionStorage.setItem('streamTotalPhases', String(payload.total_done_phases)); } catch { /* ignore */ }
              }
            } else if (eventType === 'progress') {
              const ev = payload as ProgressEvent;
              setStreamEvents((prev) => [...prev, ev]);
              setPhaseMap((prev) => ({ ...prev, [ev.phase]: ev }));
              if (ev.partial_data) {
                setLiveData((prev) => ({ ...prev, ...ev.partial_data }));
              }
            } else if (eventType === 'cached') {
              const cachedRunId: string = payload.run_id ?? null;
              if (cachedRunId) {
                setStreamRunId(cachedRunId);
                setStreamState('complete');
                setActiveRun(null);
                sessionStorage.removeItem('activeRun');
                setRecentlyCompleted({
                  ticker: ticker.toUpperCase(),
                  runId: cachedRunId,
                  completedAt: new Date().toISOString(),
                });
                getRunResult(cachedRunId)
                  .then((r) => setLiveResult(r))
                  .catch(() => {});
              }
            } else if (eventType === 'complete') {
              const completedRunId: string = payload.run_id ?? null;
              setStreamRunId(completedRunId);
              setStreamState('complete');
              if (completedRunId) {
                setActiveRun(null);
                sessionStorage.removeItem('activeRun');
                setRecentlyCompleted({
                  ticker: ticker.toUpperCase(),
                  runId: completedRunId,
                  completedAt: new Date().toISOString(),
                });
                getRunResult(completedRunId)
                  .then((r) => setLiveResult(r))
                  .catch(() => {});
              }
            } else if (eventType === 'error') {
              setStreamError(payload.error ?? 'Unknown error');
              setStreamState('error');
            }
          } catch {
            // malformed JSON — skip
          }
        }
      }
    } catch (err: unknown) {
      if (controller.signal.aborted) return;

      // ── SSE disconnected (iOS screen lock, network switch, etc.) ───
      // Don't show "failed" — switch to polling mode instead.
      // The pipeline keeps running on the backend regardless.
      startPolling(ticker);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <ActiveRunContext.Provider value={{
      activeRun, recentlyCompleted,
      startRun, completeRun, clearCompleted, clearActive,
      streamState, streamEvents, phaseMap, liveData,
      streamRunId, streamError, streamTotalPhases, streamExpectedPhases: [], liveResult, setLiveResult,
      startStream, resetStream,
    }}>
      {children}
    </ActiveRunContext.Provider>
  );
}

export function useActiveRun() {
  return useContext(ActiveRunContext);
}
