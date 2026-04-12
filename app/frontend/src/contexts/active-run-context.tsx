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
  activeRuns: ActiveRunInfo[];
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
  activeRuns: [],
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
  // ── Multiple concurrent runs support ──────────────────────────────────────
  // activeRuns is an array; activeRun returns the latest one for backward compat.
  const [activeRuns, setActiveRuns] = useState<ActiveRunInfo[]>(() => {
    try {
      // Migrate from old single-run format
      const oldSingle = sessionStorage.getItem('activeRun');
      if (oldSingle) {
        sessionStorage.removeItem('activeRun');
        const parsed = JSON.parse(oldSingle) as ActiveRunInfo;
        const age = Date.now() - new Date(parsed.startedAt).getTime();
        if (age < 30 * 60 * 1000) {
          sessionStorage.setItem('activeRuns', JSON.stringify([parsed]));
          return [parsed];
        }
      }
      const stored = sessionStorage.getItem('activeRuns');
      if (stored) {
        const parsed = JSON.parse(stored) as ActiveRunInfo[];
        // Filter out stale runs (>30 min)
        const fresh = parsed.filter(r => Date.now() - new Date(r.startedAt).getTime() < 30 * 60 * 1000);
        if (fresh.length !== parsed.length) {
          sessionStorage.setItem('activeRuns', JSON.stringify(fresh));
        }
        return fresh;
      }
    } catch { /* ignore */ }
    return [];
  });
  // Backward compat: activeRun = latest run (used by stream logic + ReportPage)
  const activeRun = activeRuns.length > 0 ? activeRuns[activeRuns.length - 1] : null;
  const activeRunRef = useRef(activeRun);
  activeRunRef.current = activeRun;

  const [recentlyCompleted, setRecentlyCompleted] = useState<CompletedRunInfo | null>(null);

  const startRun = useCallback((ticker: string) => {
    const run = { ticker: ticker.toUpperCase(), startedAt: new Date().toISOString() };
    setActiveRuns(prev => {
      // Don't duplicate — replace if same ticker already running
      const filtered = prev.filter(r => r.ticker !== run.ticker);
      const next = [...filtered, run];
      sessionStorage.setItem('activeRuns', JSON.stringify(next));
      return next;
    });
    setRecentlyCompleted(null);
  }, []);

  const completeRun = useCallback((ticker: string, runId: string) => {
    setActiveRuns(prev => {
      const next = prev.filter(r => r.ticker !== ticker.toUpperCase());
      if (next.length > 0) {
        sessionStorage.setItem('activeRuns', JSON.stringify(next));
      } else {
        sessionStorage.removeItem('activeRuns');
      }
      return next;
    });
    setRecentlyCompleted({ ticker, runId, completedAt: new Date().toISOString() });
  }, []);

  const clearCompleted = useCallback(() => setRecentlyCompleted(null), []);
  const clearActive = useCallback(() => {
    setActiveRuns([]);
    sessionStorage.removeItem('activeRuns');
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
            setActiveRuns(prev => {
              const next = prev.filter(r => r.ticker !== ticker.toUpperCase());
              next.length > 0 ? sessionStorage.setItem('activeRuns', JSON.stringify(next)) : sessionStorage.removeItem('activeRuns');
              return next;
            });
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

      const ticker = current.ticker;
      const checkAndReconnect = async () => {
        try {
          // First check if the run already completed
          const res = await fetch(
            `${API_BASE_URL}/analysis/runs?page=1&page_size=1&ticker=${encodeURIComponent(ticker)}`,
            { headers: { 'Content-Type': 'application/json' } }
          );
          if (res.ok) {
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
                completeRun(ticker.toUpperCase(), latest.run_id);
                try {
                  const result = await getRunResult(latest.run_id);
                  setLiveResult(result);
                } catch { /* ignore */ }
                return;
              }
            }
          }

          // Run still in progress — try SSE reconnect to resume live progress
          // (only if we're not already streaming)
          const reconnectController = new AbortController();
          abortRef.current = reconnectController;
          setStreamState('running');
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);

          const sseRes = await startAnalysisRun(ticker, '', []);
          if (!sseRes.ok) { startPolling(ticker); return; }
          const reader = sseRes.body?.getReader();
          if (!reader) { startPolling(ticker); return; }

          const decoder = new TextDecoder();
          let buffer = '';
          // eslint-disable-next-line no-constant-condition
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            if (reconnectController.signal.aborted) break;
            buffer += decoder.decode(value, { stream: true });
            const messages = buffer.split('\n\n');
            buffer = messages.pop() ?? '';
            for (const raw of messages) {
              if (!raw.trim()) continue;
              let eventType = 'message';
              let dataStr = '';
              for (const line of raw.split('\n')) {
                if (line.startsWith('event:')) eventType = line.slice(6).trim();
                else if (line.startsWith('data:')) dataStr = line.slice(5).trim();
              }
              if (!dataStr) continue;
              try {
                const payload = JSON.parse(dataStr);
                if (eventType === 'progress') {
                  const ev = payload as ProgressEvent;
                  setStreamEvents((prev) => [...prev, ev]);
                  setPhaseMap((prev) => ({ ...prev, [ev.phase]: ev }));
                  if (ev.partial_data) setLiveData((prev) => ({ ...prev, ...ev.partial_data }));
                } else if (eventType === 'cached' || eventType === 'complete') {
                  const rid: string = payload.run_id ?? null;
                  if (rid) {
                    setStreamRunId(rid);
                    setStreamState('complete');
                    completeRun(ticker.toUpperCase(), rid);
                    getRunResult(rid).then((r) => setLiveResult(r)).catch(() => {});
                  }
                  return;
                } else if (eventType === 'error') {
                  setStreamError(payload.error ?? 'Unknown error');
                  setStreamState('error');
                  return;
                }
              } catch { /* malformed JSON */ }
            }
          }
        } catch {
          // Reconnect failed — fall back to polling
          startPolling(ticker);
        }
      };
      checkAndReconnect();
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
      // Try to reconnect to the SSE stream first. The backend returns
      // the existing stream if the pipeline is still running, or a
      // 'cached' event if it already completed.
      // Fall back to polling if reconnect also fails.
      setStreamState('reconnecting');
      setStreamError(null);

      const maxReconnects = 3;
      let reconnected = false;

      for (let attempt = 0; attempt < maxReconnects; attempt++) {
        // Wait before reconnecting (2s, 4s, 8s exponential backoff)
        await new Promise(r => setTimeout(r, 2000 * Math.pow(2, attempt)));

        // Check if the run was cancelled or completed while we waited
        if (!activeRunRef.current || activeRunRef.current.ticker !== ticker.toUpperCase()) return;

        try {
          const reconnectController = new AbortController();
          abortRef.current = reconnectController;

          const res = await startAnalysisRun(ticker, model, agents);
          if (!res.ok) continue;

          const reader = res.body?.getReader();
          if (!reader) continue;

          // Successfully reconnected — switch back to running state
          setStreamState('running');
          reconnected = true;

          const decoder = new TextDecoder();
          let buffer = '';

          // eslint-disable-next-line no-constant-condition
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            if (reconnectController.signal.aborted) break;

            buffer += decoder.decode(value, { stream: true });
            const messages = buffer.split('\n\n');
            buffer = messages.pop() ?? '';

            for (const raw of messages) {
              if (!raw.trim()) continue;
              let eventType = 'message';
              let dataStr = '';
              for (const line of raw.split('\n')) {
                if (line.startsWith('event:')) eventType = line.slice(6).trim();
                else if (line.startsWith('data:')) dataStr = line.slice(5).trim();
              }
              if (!dataStr) continue;
              try {
                const payload = JSON.parse(dataStr);
                if (eventType === 'progress') {
                  const ev = payload as ProgressEvent;
                  setStreamEvents((prev) => [...prev, ev]);
                  setPhaseMap((prev) => ({ ...prev, [ev.phase]: ev }));
                  if (ev.partial_data) setLiveData((prev) => ({ ...prev, ...ev.partial_data }));
                } else if (eventType === 'cached') {
                  const cachedRunId: string = payload.run_id ?? null;
                  if (cachedRunId) {
                    setStreamRunId(cachedRunId);
                    setStreamState('complete');
                    completeRun(ticker.toUpperCase(), cachedRunId);
                    getRunResult(cachedRunId).then((r) => setLiveResult(r)).catch(() => {});
                  }
                  return; // done
                } else if (eventType === 'complete') {
                  const completedRunId: string = payload.run_id ?? null;
                  setStreamRunId(completedRunId);
                  setStreamState('complete');
                  if (completedRunId) {
                    completeRun(ticker.toUpperCase(), completedRunId);
                    getRunResult(completedRunId).then((r) => setLiveResult(r)).catch(() => {});
                  }
                  return; // done
                } else if (eventType === 'error') {
                  setStreamError(payload.error ?? 'Unknown error');
                  setStreamState('error');
                  return;
                }
              } catch { /* malformed JSON */ }
            }
          }
          // If we get here, the reconnected stream ended normally
          // (reader.read() returned done=true) — could be another disconnect
          break; // exit reconnect loop, fall through to polling
        } catch {
          // Reconnect attempt failed — try again
          continue;
        }
      }

      // If reconnect didn't resolve the run, fall back to polling
      if (!reconnected || streamState !== 'complete') {
        startPolling(ticker);
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <ActiveRunContext.Provider value={{
      activeRun, activeRuns, recentlyCompleted,
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
