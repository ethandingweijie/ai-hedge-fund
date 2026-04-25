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
 *
 * Phase A/3 — per-ticker live-streaming state
 * ------------------------------------------
 * Live SSE state (streamState, streamEvents, phaseMap, liveData,
 * streamRunId, streamError) is now keyed by ticker in `byTicker`. Legacy
 * singleton exports (streamState, phaseMap, …) remain for backward compat
 * — they resolve to the most-recently-updated ticker's slice. New callers
 * should use `getTickerState(ticker)` to read per-ticker state directly.
 */
import { createContext, useContext, useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { startAnalysisRun, getRunResult } from '@/lib/api';
import { API_BASE_URL } from '@/config';
import { parseBackendIso } from '@/lib/utils';
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

// ── Per-ticker live SSE state ──────────────────────────────────────────────
// Keyed by ticker (UPPERCASE). Each entry holds the full SSE slice for one
// ticker so concurrent runs do NOT clobber each other's progress bars.
export interface PerTickerLiveState {
  streamState:  RunState;
  streamRunId:  string | null;
  streamEvents: ProgressEvent[];
  phaseMap:     Record<string, ProgressEvent>;
  liveData:     Record<string, unknown>;
  streamError:  string | null;
}

const EMPTY_TICKER_STATE: PerTickerLiveState = {
  streamState: 'idle',
  streamRunId: null,
  streamEvents: [],
  phaseMap: {},
  liveData: {},
  streamError: null,
};

interface ActiveRunContextValue {
  activeRun: ActiveRunInfo | null;
  activeRuns: ActiveRunInfo[];
  recentlyCompleted: CompletedRunInfo | null;
  startRun: (ticker: string) => void;
  completeRun: (ticker: string, runId: string) => void;
  clearCompleted: () => void;
  clearActive: () => void;
  // ── Legacy singleton SSE state (backward-compat shims) ─────────────────
  // Resolve to the most-recently-updated ticker's slice so consumers that
  // don't know which ticker they're reading about keep working.
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
  startPolling: (ticker: string) => void;
  // ── Per-ticker API ──────────────────────────────────────────────────────
  /** Returns live SSE state for one ticker; empty/idle slice if unknown. */
  getTickerState: (ticker: string) => PerTickerLiveState;
  /** Full map — consumers can iterate for multi-ticker views. */
  byTicker: Record<string, PerTickerLiveState>;
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
  startPolling: () => {},
  getTickerState: () => EMPTY_TICKER_STATE,
  byTicker: {},
});

// ── Known ticker-keyed fields in partial_data ───────────────────────────────
// Some backend phases emit partial_data already keyed by ticker (e.g.
// `{ "dcf_range": { "NVDA": {...} } }`); others emit a flat shape and the
// frontend has to wrap. normalisePartialData() handles both.
const TICKER_KEYED_FIELDS = new Set<string>([
  'dcf_range',
  'scenario_analysis',
  'power_law_analysis',
  'value_trap_analysis',
  'pipeline_assets',
  'saas_metrics',
  'bank_metrics',
  'reit_metrics',
  'profile_names',
  'sectors',
  'vgpm',
  'decisions',
  'analyst_signals',
]);

/**
 * Normalise partial_data so ticker-keyed fields always use the canonical
 * ticker key. When a backend phase emits a flat object (no ticker key),
 * wrap it under the ticker so downstream panels that do
 * `dcf_range[ticker]` keep working.
 */
export function normalisePartialData(
  ticker: string,
  partial: Record<string, unknown>,
): Record<string, unknown> {
  const T = (ticker ?? '').toUpperCase();
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(partial)) {
    if (
      TICKER_KEYED_FIELDS.has(k) &&
      v &&
      typeof v === 'object' &&
      !Array.isArray(v)
    ) {
      const obj = v as Record<string, unknown>;
      // Empty object → pass through as-is. Wrapping `{}` under `{[ticker]: {}}`
      // would actively clobber populated inner data via mergeDataPreserve's
      // inner merge. Keeping it as bare `{}` lets the outer mergeDataPreserve
      // skip it via its empty-clobber guard. Bug fixed 2026-04-25.
      if (Object.keys(obj).length === 0) {
        out[k] = obj;
        continue;
      }
      // Already keyed by ticker? pass through.
      if (T in obj || ticker in obj || ticker.toLowerCase() in obj) {
        out[k] = obj;
      } else {
        // Flat shape — wrap under the ticker key.
        out[k] = { [ticker]: obj };
      }
    } else {
      out[k] = v;
    }
  }
  return out;
}

/**
 * mergeDataPreserve — deep merge that does NOT overwrite populated values
 * with empty / nullish ones from the source. Per-key rules:
 *
 *   1. If source value is `null` or `undefined`, keep target value.
 *   2. If both are plain objects, recurse one level so per-ticker inner
 *      dicts (e.g. `dcf_range[INTU] = {bear: ..., base: ...}`) are NOT
 *      clobbered by a later emit carrying `dcf_range[INTU] = {}` (which
 *      happens when normalisePartialData wraps an empty outer object).
 *   3. At any depth: an EMPTY object { } in source never overwrites a
 *      POPULATED object in target — but a populated source DOES win.
 *   4. Otherwise (primitives, arrays, type mismatches), source wins.
 *
 * Bug fixed 2026-04-25 (rev 2):
 * The shallow-merge version still got clobbered when a later SSE partial_data
 * emit carried `dcf_range: {}`. normalisePartialData wraps that as
 * `{INTU: {}}`, which the shallow merge then merged `{...{INTU: populated},
 *  ...{INTU: {}}}` = `{INTU: {}}` — the populated inner dict was overwritten
 * by the empty one. Now we recurse one level and skip empty-overrides.
 */
function _isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

export function mergeDataPreserve<T extends Record<string, unknown>>(
  target: T,
  src: Partial<T> | null | undefined,
): T {
  if (!src) return target;
  const out: Record<string, unknown> = { ...target };
  for (const k of Object.keys(src)) {
    const newV = (src as Record<string, unknown>)[k];
    if (newV === null || newV === undefined) continue;
    const oldV = out[k];

    if (_isPlainObject(oldV) && _isPlainObject(newV)) {
      // Empty source object would clobber populated target — skip
      if (Object.keys(newV).length === 0 && Object.keys(oldV).length > 0) {
        continue;  // keep oldV intact
      }
      // Inner-merge one level deeper: per-ticker dicts inside per-key dicts.
      // Without this, oldV.INTU = {populated} merged with newV.INTU = {} would
      // wipe INTU's data because the OUTER spread treats them as objects to
      // shallow-merge but the INNER value is the one that needs preservation.
      const merged: Record<string, unknown> = { ...oldV };
      for (const innerK of Object.keys(newV)) {
        const innerNewV = newV[innerK];
        if (innerNewV === null || innerNewV === undefined) continue;
        const innerOldV = merged[innerK];
        // Empty inner object would clobber populated inner — skip
        if (
          _isPlainObject(innerNewV) &&
          Object.keys(innerNewV).length === 0 &&
          _isPlainObject(innerOldV) &&
          Object.keys(innerOldV).length > 0
        ) {
          continue;
        }
        merged[innerK] = innerNewV;
      }
      out[k] = merged;
    } else {
      out[k] = newV;
    }
  }
  return out as T;
}

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
        if (age < 45 * 60 * 1000) {
          sessionStorage.setItem('activeRuns', JSON.stringify([parsed]));
          return [parsed];
        }
      }
      const stored = sessionStorage.getItem('activeRuns');
      if (stored) {
        const parsed = JSON.parse(stored) as ActiveRunInfo[];
        // Filter out stale runs (>30 min)
        const fresh = parsed.filter(r => Date.now() - new Date(r.startedAt).getTime() < 45 * 60 * 1000);
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

  // ── Per-ticker live SSE state ────────────────────────────────────────────
  // Restored from sessionStorage on mount (capped at last 5 tickers — Phase C).
  // Tickers that were 'running' when the page unloaded are downgraded to
  // 'idle' here AND captured in `downgradedOnMountRef` so the mount effect
  // below can poll the backend to verify and restore proper state. Without
  // that re-poll the user sees stale 'idle' progress UI on reload — the
  // 2026-04-25 regression where ongoing research vanished after refresh.
  const downgradedOnMountRef = useRef<string[]>([]);
  const [byTicker, setByTicker] = useState<Record<string, PerTickerLiveState>>(() => {
    try {
      const raw = sessionStorage.getItem('tickerLiveState');
      if (raw) {
        const parsed = JSON.parse(raw) as Record<string, PerTickerLiveState>;
        const downgraded: string[] = [];
        for (const k of Object.keys(parsed)) {
          if (parsed[k]?.streamState === 'running') {
            parsed[k] = { ...parsed[k], streamState: 'idle' };
            downgraded.push(k);
          }
        }
        downgradedOnMountRef.current = downgraded;
        return parsed;
      }
    } catch { /* ignore */ }
    return {};
  });

  // ── SSE stream state — LEGACY singletons (kept writable for existing logic).
  // These mirror the "primary ticker" slice during transition; writing them
  // is still wired up in the SSE handler below, but readers should eventually
  // switch to getTickerState().
  const [streamState, setStreamState] = useState<RunState>('idle');
  const [streamEvents, _setStreamEvents] = useState<ProgressEvent[]>(() => {
    try {
      const stored = sessionStorage.getItem('streamEvents');
      if (stored) return JSON.parse(stored);
    } catch { /* ignore */ }
    return [];
  });
  const setStreamEvents = useCallback((updater: ProgressEvent[] | ((prev: ProgressEvent[]) => ProgressEvent[])) => {
    _setStreamEvents(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      // Only persist the last 50 events to avoid quota issues
      try { sessionStorage.setItem('streamEvents', JSON.stringify(next.slice(-50))); } catch { /* quota */ }
      return next;
    });
  }, []);
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
  const [liveData, _setLiveData] = useState<Record<string, unknown>>(() => {
    try {
      const stored = sessionStorage.getItem('liveData');
      if (stored) return JSON.parse(stored);
    } catch { /* ignore */ }
    return {};
  });
  const setLiveData = useCallback((updater: Record<string, unknown> | ((prev: Record<string, unknown>) => Record<string, unknown>)) => {
    _setLiveData(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      try { sessionStorage.setItem('liveData', JSON.stringify(next)); } catch { /* quota */ }
      return next;
    });
  }, []);
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
  // Per-ticker AbortController map (2026-04-25) so parallel tickers don't
  // abort each other's SSE streams. Previously a single AbortController meant
  // starting ticker B killed ticker A's stream. Map lets each ticker maintain
  // independent lifecycle — triggering a new run only aborts THAT ticker's
  // previous stream (if any), others continue uninterrupted.
  const abortRefs = useRef<Map<string, AbortController>>(new Map());
  const lastStreamTickerRef = useRef<string | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Per-ticker update helper ─────────────────────────────────────────────
  const updateTicker = useCallback(
    (
      ticker: string,
      patch:
        | Partial<PerTickerLiveState>
        | ((prev: PerTickerLiveState) => Partial<PerTickerLiveState>),
    ) => {
      const T = (ticker ?? '').toUpperCase();
      if (!T) return;
      setByTicker(prev => {
        const cur = prev[T] ?? EMPTY_TICKER_STATE;
        const resolved = typeof patch === 'function' ? patch(cur) : patch;
        return { ...prev, [T]: { ...cur, ...resolved } };
      });
    },
    [],
  );

  // ── Self-healing activeRuns: ensure a ticker that's currently streaming
  //    or polling is reflected in activeRuns. Two paths populate byTicker
  //    (SSE progress events + status polling) but only `markRunStarted` /
  //    `startRun` populates activeRuns. When users click an ongoing ticker
  //    in History (which calls `poll()` not `start()`), or when
  //    sessionStorage is cleared mid-run on iOS, activeRuns can lose its
  //    entry — and HistoryPage gates the ongoing card on activeRuns. Auto-
  //    syncing here is idempotent (filter+append) and prevents the symptom
  //    where the run is alive on the server but invisible in the UI.
  const ensureInActiveRuns = useCallback((ticker: string) => {
    const T = (ticker ?? '').toUpperCase();
    if (!T) return;
    setActiveRuns(prev => {
      if (prev.some(r => r.ticker === T)) return prev;  // already there — no-op
      const run: ActiveRunInfo = { ticker: T, startedAt: new Date().toISOString() };
      const next = [...prev, run];
      try { sessionStorage.setItem('activeRuns', JSON.stringify(next)); } catch { /* quota */ }
      return next;
    });
  }, []);

  const getTickerState = useCallback(
    (ticker: string): PerTickerLiveState => {
      const T = (ticker ?? '').toUpperCase();
      if (!T) return EMPTY_TICKER_STATE;
      return byTicker[T] ?? EMPTY_TICKER_STATE;
    },
    [byTicker],
  );

  // ── Persist byTicker to sessionStorage (Phase C — cap at last 5) ─────────
  useEffect(() => {
    try {
      const entries = Object.entries(byTicker)
        .sort(([, a], [, b]) => {
          const tsA = a.streamEvents?.[a.streamEvents.length - 1]?.timestamp ?? '0';
          const tsB = b.streamEvents?.[b.streamEvents.length - 1]?.timestamp ?? '0';
          return tsB.localeCompare(tsA);
        })
        .slice(0, 5);
      const trimmed = Object.fromEntries(entries);
      sessionStorage.setItem('tickerLiveState', JSON.stringify(trimmed));
    } catch { /* quota exceeded — skip */ }
  }, [byTicker]);

  // ── Cleanup polling on unmount ────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, []);

  // ── Check if a run already completed (prevents re-triggering pipeline) ─────
  const checkCompleted = useCallback(async (ticker: string): Promise<boolean> => {
    const current = activeRunRef.current;
    try {
      const res = await fetch(
        `${API_BASE_URL}/analysis/runs?page=1&page_size=1&ticker=${encodeURIComponent(ticker)}`,
        { headers: { 'Content-Type': 'application/json' } }
      );
      if (!res.ok) return false;
      const data = await res.json();
      const runs = data.items || data.runs || [];
      if (runs.length > 0) {
        const latest = runs[0];
        // parseBackendIso: backend run_at is naive ISO (UTC server) but JS
        // would parse it as local browser time → mixing with the UTC-Z
        // startedAt produced false-positive completion detections in
        // non-UTC browsers (Singapore: 8h skew). Treating as UTC fixes it.
        const runTime = parseBackendIso(latest.run_at).getTime();
        // If we have a startedAt reference, check timing. Otherwise accept any
        // recent run (within 60 min) — handles case where activeRun was cleaned up.
        // Relaxed to 5-min window tolerance (was 60s) to handle DB write lag.
        const startTime = current
          ? new Date(current.startedAt).getTime()
          : Date.now() - 60 * 60 * 1000;
        // Accept if: (a) run completed AFTER we started (within 5 min tolerance) OR
        //            (b) no activeRun reference AND run is within last 60 min
        const timingOK = current
          ? runTime >= startTime - 5 * 60 * 1000
          : runTime >= Date.now() - 60 * 60 * 1000;
        if (timingOK && latest.final_action) {
          // Run completed — mark as done
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
          setStreamRunId(latest.run_id);
          setStreamState('complete');
          updateTicker(ticker, { streamRunId: latest.run_id, streamState: 'complete' });
          completeRun(ticker.toUpperCase(), latest.run_id);
          try {
            const result = await getRunResult(latest.run_id);
            setLiveResult(result);
          } catch { /* ignore */ }
          return true;
        }
      }
    } catch { /* ignore */ }
    return false;
  }, [updateTicker, completeRun]);

  // ── Poll backend for completion (used when SSE disconnects) ───────────────
  const startPolling = useCallback((ticker: string) => {
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);

    setStreamState('reconnecting');
    setStreamError(null);
    updateTicker(ticker, { streamState: 'reconnecting', streamError: null });

    let attempts = 0;
    const maxAttempts = 180; // 30 minutes at 10s intervals
    let consecutiveNotRunning = 0; // require multiple confirmations before declaring crash

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
          // Same UTC-vs-local mismatch fix as in checkCompleted above
          const runTime = parseBackendIso(latest.run_at).getTime();
          const startTime = new Date(currentRun.startedAt).getTime();

          // Run completed after we started — success
          if (runTime >= startTime - 60000 && latest.final_action) {
            if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
            setStreamRunId(latest.run_id);
            setStreamState('complete');
            updateTicker(ticker, { streamRunId: latest.run_id, streamState: 'complete' });
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

      // Also poll live phase status so progress bar shows current phase
      try {
        const statusRes = await fetch(
          `${API_BASE_URL}/analysis/status/${encodeURIComponent(ticker)}`
        );
        if (statusRes.ok) {
          const status = await statusRes.json();

          // ── NEW: Detect pipeline_complete marker from backend ─────────────
          // Backend sets this in the finally block after the pipeline finishes,
          // BEFORE the DB commit may have fully landed. We can mark complete
          // immediately and fetch the result from the run_id in the marker.
          if (status.phase === 'pipeline_complete' || status.completed === true) {
            if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
            const completedRunId = status.run_id || '';
            setStreamRunId(completedRunId);
            setStreamState('complete');
            updateTicker(ticker, { streamRunId: completedRunId, streamState: 'complete' });
            setActiveRuns(prev => {
              const next = prev.filter(r => r.ticker !== ticker.toUpperCase());
              next.length > 0 ? sessionStorage.setItem('activeRuns', JSON.stringify(next)) : sessionStorage.removeItem('activeRuns');
              return next;
            });
            setRecentlyCompleted({
              ticker: ticker.toUpperCase(),
              runId: completedRunId,
              completedAt: new Date().toISOString(),
            });
            // Fetch the result — retry a few times if DB write is still landing
            if (completedRunId) {
              const fetchResult = async (attempts = 0): Promise<void> => {
                try {
                  const result = await getRunResult(completedRunId);
                  setLiveResult(result);
                } catch {
                  if (attempts < 5) {
                    await new Promise(r => setTimeout(r, 2000));
                    return fetchResult(attempts + 1);
                  }
                }
              };
              fetchResult();
            }
            return;
          }

          if (status.in_progress && status.phase) {
            // Pipeline still running — update phaseMap with ALL phases from server
            consecutiveNotRunning = 0; // reset crash counter
            // Self-heal: backend confirms this ticker is in flight, so it
            // belongs in activeRuns regardless of how we reached the polling
            // state (mount-time poll, switchTicker from History, iOS resume,
            // etc.). Idempotent — filtered out if already present.
            ensureInActiveRuns(ticker);
            setStreamState('running');
            const liveEvent: ProgressEvent = {
              phase: status.phase,
              status: status.status || '',
              summary: status.summary || '',
              timestamp: status.timestamp || '',
            };
            setStreamEvents((prev) => [...prev, liveEvent]);
            // Rebuild full phaseMap from server's all_phases (preserves completed phases
            // that were lost when SSE disconnected — fixes progress bar regression)
            if (status.all_phases && typeof status.all_phases === 'object') {
              const rebuilt: Record<string, ProgressEvent> = {};
              for (const [phaseName, phaseData] of Object.entries(status.all_phases)) {
                const pd = phaseData as Record<string, string>;
                rebuilt[phaseName] = {
                  phase: pd.phase || phaseName,
                  status: pd.status || '',
                  summary: pd.summary || '',
                  timestamp: pd.timestamp || '',
                };
              }
              setPhaseMap(rebuilt);
              updateTicker(ticker, prev => ({
                streamState: 'running',
                phaseMap: rebuilt,
                streamEvents: [...prev.streamEvents, liveEvent],
              }));
            } else {
              setPhaseMap((prev) => ({ ...prev, [status.phase]: liveEvent }));
              updateTicker(ticker, prev => ({
                streamState: 'running',
                phaseMap: { ...prev.phaseMap, [status.phase]: liveEvent },
                streamEvents: [...prev.streamEvents, liveEvent],
              }));
            }
          } else if (!status.in_progress) {
            // Pipeline reports not running — could be:
            //   a) Just completed — DB write may lag a few seconds
            //   b) Brief gap between phases
            //   c) Actual crash
            // Check if it completed successfully first.
            const completed = await checkCompleted(ticker);
            if (completed) {
              if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
              return;
            }
            // Not found yet — increment counter but keep polling.
            // DB write can lag a few seconds after pipeline finishes.
            consecutiveNotRunning++;
            if (consecutiveNotRunning >= 6) {
              // Wait 3s for DB write to land, then final check
              await new Promise(r => setTimeout(r, 3000));
              const lastCheck = await checkCompleted(ticker);
              if (lastCheck) {
                if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
                return;
              }
              // Still not found after 60s of "not running" — pipeline finished
              // but result may have been deleted or never saved.
              // Give one more round (reset to 3 so 3 more polls = 30s), then stop.
              if (consecutiveNotRunning >= 12) {
                // 2 minutes of "not running" + no result = pipeline is truly done
                if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
                // Clear the stuck active run so UI can show search form
                setActiveRuns(prev => {
                  const next = prev.filter(r => r.ticker !== ticker.toUpperCase());
                  next.length > 0 ? sessionStorage.setItem('activeRuns', JSON.stringify(next)) : sessionStorage.removeItem('activeRuns');
                  return next;
                });
                setStreamState('idle');
                setStreamError(null);
                updateTicker(ticker, { streamState: 'idle', streamError: null });
                return;
              }
            }
          }
        }
      } catch { /* ignore */ }

      if (attempts >= maxAttempts) {
        // Before giving up, do one final completion check
        const lastCheck = await checkCompleted(ticker);
        if (lastCheck) {
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
          return;
        }
        if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        setStreamError('Analysis may still be running on the server — check History shortly');
        setStreamState('reconnecting');
        updateTicker(ticker, {
          streamError: 'Analysis may still be running on the server — check History shortly',
          streamState: 'reconnecting',
        });
      }
    }, 10000);
  }, [checkCompleted, setStreamEvents, setPhaseMap, updateTicker, ensureInActiveRuns]);

  // ── Mount-time resume for tickers that were running when the page unloaded.
  //    The byTicker initializer downgraded them to 'idle' (JS can't carry an
  //    SSE connection across reloads), but the backend pipeline may still be
  //    in flight. Poll one of them — if backend says in_progress, startPolling
  //    will restore streamState='running' AND ensureInActiveRuns will repair
  //    the activeRuns array (so HistoryPage shows the ongoing card again).
  //    If backend says completed, checkCompleted picks up the final result.
  //
  //    For multiple downgraded tickers (parallel runs), we also issue a
  //    one-shot status fetch per ticker — that single fetch is enough for
  //    ensureInActiveRuns to repair activeRuns. Continuous polling stays on
  //    the primary ticker only because pollIntervalRef is single-ticker.
  useEffect(() => {
    const downgraded = downgradedOnMountRef.current;
    if (!downgraded || downgraded.length === 0) return;
    // Continuous poll on the first (most recent) downgraded ticker.
    const primary = downgraded[0];
    try { startPolling(primary); } catch { /* best-effort */ }
    // One-shot status check for the rest — enough to repair activeRuns
    // and seed phaseMap so HistoryPage renders all ongoing cards.
    for (const t of downgraded.slice(1)) {
      const T = t.toUpperCase();
      fetch(`${API_BASE_URL}/analysis/status/${encodeURIComponent(T)}`)
        .then(r => r.ok ? r.json() : null)
        .then(status => {
          if (!status) return;
          if (status.in_progress || status.phase === 'pipeline_complete') {
            ensureInActiveRuns(T);
          }
          if (status.in_progress) {
            updateTicker(T, { streamState: 'running' });
          }
        })
        .catch(() => { /* ignore */ });
    }
    downgradedOnMountRef.current = []; // run-once
  }, [startPolling, ensureInActiveRuns, updateTicker]);

  // ── Visibility change handler — resume when user returns ──────────────────
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState !== 'visible') return;
      const current = activeRunRef.current;
      if (!current) return;

      const ticker = current.ticker;
      const checkAndResume = async () => {
        // CRITICAL: Check if run completed FIRST — never call startAnalysisRun
        // directly as it would trigger a new pipeline run if the dedup lock cleared.
        const completed = await checkCompleted(ticker);
        if (completed) return;

        // Check live status — pipeline may still be running on the server
        // even though iOS killed our SSE / suspended our timers.
        try {
          const statusRes = await fetch(
            `${API_BASE_URL}/analysis/status/${encodeURIComponent(ticker)}`
          );
          if (statusRes.ok) {
            const status = await statusRes.json();
            // Check for completion marker first
            if (status.phase === 'pipeline_complete' || status.completed === true) {
              const completedRunId = status.run_id || '';
              setStreamRunId(completedRunId);
              setStreamState('complete');
              updateTicker(ticker, { streamRunId: completedRunId, streamState: 'complete' });
              setActiveRuns(prev => {
                const next = prev.filter(r => r.ticker !== ticker.toUpperCase());
                next.length > 0 ? sessionStorage.setItem('activeRuns', JSON.stringify(next)) : sessionStorage.removeItem('activeRuns');
                return next;
              });
              setRecentlyCompleted({
                ticker: ticker.toUpperCase(),
                runId: completedRunId,
                completedAt: new Date().toISOString(),
              });
              if (completedRunId) {
                try {
                  const result = await getRunResult(completedRunId);
                  setLiveResult(result);
                } catch { /* ignore */ }
              }
              return;
            }
            if (status.in_progress) {
              // Pipeline confirmed alive — restart fresh polling (resets crash counter)
              startPolling(ticker);
              return;
            }
          }
        } catch { /* ignore */ }

        // Status says not running and not completed — start polling anyway
        // (gives it more time to confirm before declaring anything)
        startPolling(ticker);
      };
      checkAndResume();
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, [checkCompleted, startPolling, updateTicker]);

  const resetStream = useCallback(() => {
    // Abort ALL in-flight streams — resetStream is a full-system reset.
    // Individual per-ticker aborts happen in startStream for new runs.
    for (const ctrl of abortRefs.current.values()) {
      try { ctrl.abort(); } catch { /* ignore */ }
    }
    abortRefs.current.clear();
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    setStreamState('idle');
    setStreamEvents([]);
    setPhaseMap({});
    setLiveData({});
    setStreamRunId(null);
    setStreamError(null);
    setLiveResult(null);
    // Reset the currently-focused ticker's slice too (others untouched).
    const current = lastStreamTickerRef.current;
    if (current) {
      updateTicker(current, { ...EMPTY_TICKER_STATE });
    }
    try {
      sessionStorage.removeItem('phaseMap');
      sessionStorage.removeItem('streamTotalPhases');
      sessionStorage.removeItem('streamEvents');
      sessionStorage.removeItem('liveData');
    } catch { /* ignore */ }
  }, [setStreamEvents, setPhaseMap, setLiveData, updateTicker]);

  const startStream = useCallback(async (ticker: string, model = 'claude-sonnet-4-6', agents?: string[]) => {
    const T = ticker.toUpperCase();
    // Per-ticker abort: only kill THIS ticker's previous stream (if any).
    // Other tickers' streams continue uninterrupted. Before 2026-04-25 this
    // used a single abortRef that aborted whatever was running, breaking
    // parallel multi-ticker runs.
    const existingCtrl = abortRefs.current.get(T);
    if (existingCtrl) {
      try { existingCtrl.abort(); } catch { /* ignore */ }
    }
    const controller = new AbortController();
    abortRefs.current.set(T, controller);

    // NOTE: legacy singleton resets intentionally gated — only reset globals
    // when switching to a NEW focus ticker, otherwise they clobber other
    // active tickers' state (e.g. starting ticker B while ticker A runs
    // shouldn't wipe A's liveData singleton which is fallback for A's report).
    const prevTicker = lastStreamTickerRef.current;
    const isFocusChange = prevTicker !== T;

    if (isFocusChange) {
      // User switched focus to a new ticker — safe to reset legacy singletons.
      setStreamState('running');
      setStreamEvents([]);
      setPhaseMap({});
      setLiveData({});
      setStreamRunId(null);
      setStreamError(null);
      setLiveResult(null);
      try {
        sessionStorage.removeItem('phaseMap');
        sessionStorage.removeItem('streamTotalPhases');
        sessionStorage.removeItem('streamEvents');
        sessionStorage.removeItem('liveData');
      } catch { /* ignore */ }
    }
    // Always update focused ticker pointer
    lastStreamTickerRef.current = T;

    // Reset the per-ticker slice for THIS ticker — its new run starts clean.
    // Other tickers' slices untouched → parallel runs isolated.
    updateTicker(T, {
      streamState: 'running',
      streamEvents: [],
      phaseMap: {},
      liveData: {},
      streamRunId: null,
      streamError: null,
    });

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
              // Self-heal: ensure activeRuns reflects this running ticker
              // (no-op if already there). Closes a race where the SSE
              // started but activeRuns lost its entry (sessionStorage
              // cleared, parallel-ticker context migration, etc.)
              ensureInActiveRuns(T);
              setStreamEvents((prev) => [...prev, ev]);
              setPhaseMap((prev) => ({ ...prev, [ev.phase]: ev }));
              // Phase B diagnostic: log shape of partial_data per ticker so
              // shape mismatches are easy to spot in the browser console.
              if (ev.partial_data && typeof ev.partial_data === 'object') {
                // eslint-disable-next-line no-console
                console.log(
                  `[SSE-${T}] phase=${ev.phase} status=${ev.status}`,
                  { partial_keys: Object.keys(ev.partial_data as Record<string, unknown>) },
                );
              }
              const normalisedPartial = ev.partial_data && typeof ev.partial_data === 'object'
                ? normalisePartialData(T, ev.partial_data as Record<string, unknown>)
                : null;
              if (normalisedPartial) {
                // Use mergeDataPreserve so a later phase emitting
                // {dcf_range: {}} doesn't clobber an earlier-populated
                // {dcf_range: {INTU: {...}}}. Same protection at per-ticker
                // level just below.
                setLiveData((prev) => mergeDataPreserve(prev, normalisedPartial));
              }
              // Route event per-ticker too
              updateTicker(T, (prev) => ({
                streamState: 'running',
                streamEvents: [...prev.streamEvents, ev],
                phaseMap: { ...prev.phaseMap, [ev.phase]: ev },
                liveData: normalisedPartial
                  ? mergeDataPreserve(prev.liveData, normalisedPartial)
                  : prev.liveData,
              }));
            } else if (eventType === 'cached') {
              const cachedRunId: string = payload.run_id ?? null;
              if (cachedRunId) {
                setStreamRunId(cachedRunId);
                setStreamState('complete');
                updateTicker(T, { streamRunId: cachedRunId, streamState: 'complete' });
                setActiveRuns(prev => {
                  const next = prev.filter(r => r.ticker !== T);
                  next.length > 0
                    ? sessionStorage.setItem('activeRuns', JSON.stringify(next))
                    : sessionStorage.removeItem('activeRuns');
                  return next;
                });
                setRecentlyCompleted({
                  ticker: T,
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
              updateTicker(T, { streamRunId: completedRunId, streamState: 'complete' });
              if (completedRunId) {
                setActiveRuns(prev => {
                  const next = prev.filter(r => r.ticker !== T);
                  next.length > 0
                    ? sessionStorage.setItem('activeRuns', JSON.stringify(next))
                    : sessionStorage.removeItem('activeRuns');
                  return next;
                });
                setRecentlyCompleted({
                  ticker: T,
                  runId: completedRunId,
                  completedAt: new Date().toISOString(),
                });
                getRunResult(completedRunId)
                  .then((r) => setLiveResult(r))
                  .catch(() => {});
              }
            } else if (eventType === 'error') {
              const errMsg = payload.error ?? 'Unknown error';
              setStreamError(errMsg);
              setStreamState('error');
              updateTicker(T, { streamError: errMsg, streamState: 'error' });
            }
          } catch {
            // malformed JSON — skip
          }
        }
      }
    } catch (err: unknown) {
      if (controller.signal.aborted) return;

      // ── SSE disconnected (iOS screen lock, network switch, etc.) ───
      // CRITICAL: Do NOT call startAnalysisRun() here — it would trigger
      // a new pipeline run if the previous one already completed.
      // Instead: check if completed, then fall back to polling.
      setStreamState('reconnecting');
      setStreamError(null);
      updateTicker(T, { streamState: 'reconnecting', streamError: null });

      // Wait briefly for network to stabilize
      await new Promise(r => setTimeout(r, 2000));

      // Check if run already completed while we were disconnected
      if (!activeRunRef.current || activeRunRef.current.ticker !== T) return;
      const completed = await checkCompleted(ticker);
      if (completed) return;

      // Run still in progress — poll for completion (safe, no new runs triggered)
      startPolling(ticker);
    }
  }, [setStreamEvents, setPhaseMap, setLiveData, updateTicker, checkCompleted, startPolling, ensureInActiveRuns]);

  // ── Mount-time rehydration (Phase C): on ticker focus, seed phaseMap
  // from /analysis/status/{ticker} when this ticker's slice is empty.
  // This is triggered at the context level so every ReportPage remount
  // benefits, not just brand-new runs. See ReportPage.tsx for the focus
  // effect that invokes rehydrateTicker on liveTicker changes.
  const rehydrateTicker = useCallback(async (ticker: string) => {
    if (!ticker) return;
    const T = ticker.toUpperCase();
    try {
      const r = await fetch(`${API_BASE_URL}/analysis/status/${encodeURIComponent(ticker)}`);
      if (!r.ok) return;
      const status = await r.json();
      if (!status?.all_phases || typeof status.all_phases !== 'object') return;
      const cur = byTicker[T];
      if (cur && Object.keys(cur.phaseMap).length > 0) return; // don't overwrite live
      const rebuilt: Record<string, ProgressEvent> = {};
      for (const [phaseName, phaseData] of Object.entries(status.all_phases)) {
        const pd = phaseData as Record<string, string>;
        rebuilt[phaseName] = {
          phase: pd.phase || phaseName,
          status: pd.status || '',
          summary: pd.summary || '',
          timestamp: pd.timestamp || '',
        };
      }
      updateTicker(T, { phaseMap: rebuilt });
    } catch { /* ignore */ }
    // ^ intentionally not memoising on byTicker to keep the fn stable — we
    // read the latest byTicker through closure above; staleness is fine
    // because the guard just prevents overwriting non-empty maps.
  }, [byTicker, updateTicker]);

  // Expose rehydrateTicker via byTicker shape is not ideal; ReportPage reaches
  // it through useActiveRun() below. Keep as unreferenced guard — wired through
  // the context value so consumers can call it on liveTicker change.
  // (Intentionally untyped in the public surface to avoid churn; ReportPage
  // uses it directly.)
  const rehydrateRef = useRef(rehydrateTicker);
  rehydrateRef.current = rehydrateTicker;

  // ── Primary ticker (most recently touched) for legacy shim readers ─────
  const primaryTicker = useMemo(() => {
    const keys = Object.keys(byTicker);
    if (keys.length === 0) return null;
    return keys.reduce((a, b) => {
      const evA = byTicker[a]?.streamEvents?.[byTicker[a].streamEvents.length - 1]?.timestamp ?? '0';
      const evB = byTicker[b]?.streamEvents?.[byTicker[b].streamEvents.length - 1]?.timestamp ?? '0';
      return evA.localeCompare(evB) >= 0 ? a : b;
    });
  }, [byTicker]);

  // Primary slice drives the legacy singleton readers WHEN the legacy
  // writable state is still on its default. In practice the SSE handler
  // still writes the legacy state too, so both paths stay in sync — the
  // shim is a safety net for paths where only updateTicker() fires.
  const primarySlice = primaryTicker ? byTicker[primaryTicker] ?? null : null;

  // Legacy-exposed values: prefer the legacy writable state when it's
  // non-empty (preserves exact current behaviour), otherwise fall through
  // to the primary ticker's slice.
  const shimStreamState = streamState !== 'idle'
    ? streamState
    : (primarySlice?.streamState ?? 'idle');
  const shimStreamEvents = streamEvents.length > 0
    ? streamEvents
    : (primarySlice?.streamEvents ?? []);
  const shimPhaseMap = Object.keys(phaseMap).length > 0
    ? phaseMap
    : (primarySlice?.phaseMap ?? {});
  const shimLiveData = Object.keys(liveData).length > 0
    ? liveData
    : (primarySlice?.liveData ?? {});
  const shimStreamRunId = streamRunId ?? primarySlice?.streamRunId ?? null;
  const shimStreamError = streamError ?? primarySlice?.streamError ?? null;

  return (
    <ActiveRunContext.Provider value={{
      activeRun, activeRuns, recentlyCompleted,
      startRun, completeRun, clearCompleted, clearActive,
      streamState: shimStreamState,
      streamEvents: shimStreamEvents,
      phaseMap: shimPhaseMap,
      liveData: shimLiveData,
      streamRunId: shimStreamRunId,
      streamError: shimStreamError,
      streamTotalPhases, streamExpectedPhases: [], liveResult, setLiveResult,
      startStream, resetStream, startPolling,
      getTickerState, byTicker,
    }}>
      {children}
    </ActiveRunContext.Provider>
  );
}

export function useActiveRun() {
  return useContext(ActiveRunContext);
}

/**
 * Fire-and-forget rehydration: call from a component's useEffect when the
 * focused ticker changes. Reads the latest phase map from
 * /analysis/status/{ticker} and seeds it into byTicker if that ticker's
 * slice is empty. Safe to call any time — no-op if slice already has data.
 */
export async function rehydrateTickerFromBackend(
  ticker: string,
  getTickerState: (t: string) => PerTickerLiveState,
  updateFn: (t: string, phaseMap: Record<string, ProgressEvent>) => void,
): Promise<void> {
  if (!ticker) return;
  try {
    const r = await fetch(`${API_BASE_URL}/analysis/status/${encodeURIComponent(ticker)}`);
    if (!r.ok) return;
    const status = await r.json();
    if (!status?.all_phases || typeof status.all_phases !== 'object') return;
    const cur = getTickerState(ticker);
    if (Object.keys(cur.phaseMap).length > 0) return;
    const rebuilt: Record<string, ProgressEvent> = {};
    for (const [phaseName, phaseData] of Object.entries(status.all_phases)) {
      const pd = phaseData as Record<string, string>;
      rebuilt[phaseName] = {
        phase: pd.phase || phaseName,
        status: pd.status || '',
        summary: pd.summary || '',
        timestamp: pd.timestamp || '',
      };
    }
    updateFn(ticker, rebuilt);
  } catch { /* ignore */ }
}
