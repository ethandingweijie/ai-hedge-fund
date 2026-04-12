/**
 * ActiveRunContext — global state tracking in-flight and recently-completed
 * pipeline analysis runs, used to coordinate the "We are cooking!" indicator
 * in HistoryPage, the completed-row highlight, AND the live SSE stream state
 * so that navigating away from /report and back does not lose the in-progress run.
 */
import { createContext, useContext, useState, useCallback, useRef } from 'react';
import { startAnalysisRun, getRunResult } from '@/lib/api';
import { API_BASE_URL } from '@/config';
import type { ProgressEvent, RunResult } from '@/lib/reportTypes';

// ── Stream types (mirrors useRunStream) ──────────────────────────────────────
export type RunState = 'idle' | 'running' | 'complete' | 'error';

export interface ActiveRunInfo {
  ticker: string;
  startedAt: string;   // ISO timestamp
}

export interface CompletedRunInfo {
  ticker: string;
  runId: string;
  completedAt: string; // ISO timestamp
}

interface ActiveRunContextValue {
  // ── Run-coordination ────────────────────────────────────────────────────────
  activeRun: ActiveRunInfo | null;
  recentlyCompleted: CompletedRunInfo | null;
  startRun: (ticker: string) => void;
  completeRun: (ticker: string, runId: string) => void;
  clearCompleted: () => void;
  clearActive: () => void;

  // ── Live SSE stream state (persists across navigation) ─────────────────────
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
  // Persist activeRun to sessionStorage so it survives page refresh / screensave.
  // On mount, restore from sessionStorage if an active run was in progress.
  const [activeRun, setActiveRun] = useState<ActiveRunInfo | null>(() => {
    try {
      const stored = sessionStorage.getItem('activeRun');
      if (stored) {
        const parsed = JSON.parse(stored) as ActiveRunInfo;
        // Only restore if started within the last 30 minutes (stale guard)
        const age = Date.now() - new Date(parsed.startedAt).getTime();
        if (age < 30 * 60 * 1000) return parsed;
        sessionStorage.removeItem('activeRun');
      }
    } catch { /* ignore parse errors */ }
    return null;
  });
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

  const clearCompleted = useCallback(() => {
    setRecentlyCompleted(null);
  }, []);

  const clearActive = useCallback(() => {
    setActiveRun(null);
    sessionStorage.removeItem('activeRun');
  }, []);

  // ── SSE stream state (lifted so it survives /report → /history navigation) ─
  // Restore phaseMap from sessionStorage so progress survives page refresh.
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

  const resetStream = useCallback(() => {
    abortRef.current?.abort();
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
    const controller = new AbortController();
    abortRef.current = controller;

    setStreamState('running');
    setStreamEvents([]);
    // Clear phaseMap when switching to a different ticker — stale "Done" entries
    // from the previous run inflate the progress bar.  Preserve on reconnect
    // (same ticker) so progress survives page refresh.
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
              // Server found a run within 30 min — skip the pipeline and reuse it
              const cachedRunId: string = payload.run_id ?? null;
              if (cachedRunId) {
                setStreamRunId(cachedRunId);
                setStreamState('complete');
                setActiveRun(null);
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
              // Fetch the full result and mark the run complete — runs even if
              // ReportPage has navigated away (stream lives in context).
              if (completedRunId) {
                setActiveRun(null);
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

      // ── SSE disconnect recovery ────────────────────────────────────
      // iOS Safari kills background SSE connections. Instead of showing
      // "failed", poll the backend to check if the run completed or is
      // still in progress. Only show error if the run truly failed.
      if (activeRun) {
        const pollForResult = async () => {
          const maxAttempts = 30;  // poll for up to 5 minutes
          const interval = 10000; // every 10 seconds
          for (let i = 0; i < maxAttempts; i++) {
            await new Promise(r => setTimeout(r, interval));
            try {
              // Check if a completed run exists for this ticker
              const res = await fetch(
                `${API_BASE_URL}/analysis/runs?page=1&page_size=1&ticker=${encodeURIComponent(ticker)}`,
                { headers: { 'Content-Type': 'application/json' } }
              );
              if (!res.ok) continue;
              const data = await res.json();
              const runs = data.items || data.runs || [];
              if (runs.length > 0) {
                const latest = runs[0];
                // Check if this run was created after we started
                const runTime = new Date(latest.run_at).getTime();
                const startTime = new Date(activeRun.startedAt).getTime();
                if (runTime >= startTime - 60000 && latest.final_action) {
                  // Run completed on the backend — show success
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
            } catch { /* network error, retry */ }
          }
          // After 5 minutes of polling, truly mark as error
          setStreamError('Connection lost — check History for results');
          setStreamState('error');
        };
        pollForResult();
      } else {
        setStreamError(err instanceof Error ? err.message : String(err));
        setStreamState('error');
      }
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
