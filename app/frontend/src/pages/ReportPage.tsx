import { useState, useEffect, useRef, useCallback } from 'react';
// `toast` / `Toaster` imports removed — section-completion toasts were
// removed, so ReportPage doesn't fire toasts directly. Global <Toaster>
// mount is in App.tsx; other pages (Screener, History) still use toast
// from 'sonner' as needed.
// M2 Track E: tier imports retired with the persona picker — the committee
// is decommissioned, so there are no agents left to gate by tier here.
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';
import { Button } from '@/components/ui/button';
import { getStockData, searchCompanies, getPopularTickers, getRunResult, type CompanySearchResult, type PopularTicker } from '@/lib/api';
import { API_BASE_URL } from '@/config';
import { extractLatestFinancials, isBiopharmaSector, isTechSector, classifyTechSubtype } from '@/lib/utils';
// v2 imports
import { Search as V2Search } from '@/components/v2/shared';
import { V2ReportView } from '@/components/v2/V2ReportView';
import { useActiveRun, mergeDataPreserve } from '@/contexts/active-run-context';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { useIsResearchPhase, useProgressDerived } from '@/hooks/useProgressDerived';
import { ProgressHeader } from '@/components/report/ProgressHeader';
import { LiveSearchPanel } from '@/components/report/LiveSearchPanel';
// MobileBottomNav removed — hamburger menu in MobileTopBar replaces bottom tabs
// MobileReportView removed — replaced by V2ReportView (dead legacy mobile fallback gated on `if (false && ...)` removed 2026-04).
import type { ProgressEvent } from '@/lib/reportTypes';

// ── Report section components ────────────────────────────────────────────────
import { ReportHeader }        from '@/components/report/ReportHeader';
import { CardAuditBanner }     from '@/components/report/CardAuditBanner';
import { PowerLawRadar }       from '@/components/report/PowerLawRadar';
import { ValueTrapChecklist }  from '@/components/report/ValueTrapChecklist';
import { DecisionInputsCard }  from '@/components/report/DecisionInputsCard';
import { DcfMethodologyPanel } from '@/components/report/DcfMethodologyPanel';
import { IntelligenceGrid }    from '@/components/report/IntelligenceGrid';
import { FinancialsChart }     from '@/components/report/FinancialsChart';
import { ValuationLadder }     from '@/components/report/ValuationLadder';
import { REITValuationPanel }  from '@/components/report/reit/REITValuationPanel';
import { BankValuationPanel }  from '@/components/report/bank/BankValuationPanel';
import { BiopharmaValuationPanel } from '@/components/report/biopharma/BiopharmaValuationPanel';
import { TechValuationPanel } from '@/components/report/tech/TechValuationPanel';
import { SotpAnalystPanel } from '@/components/report/SotpAnalystPanel';
import { CitationPanel }       from '@/components/report/CitationPanel';
import { StockPanel }          from '@/components/report/StockPanel';
import { PriceTargetPanel }    from '@/components/report/PriceTargetPanel';
import { NewsPanel }           from '@/components/report/NewsPanel';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { IndustryBriefPanel }  from '@/components/report/IndustryBriefPanel';
import { DeepResearchPanel }   from '@/components/report/DeepResearchPanel';
import { SectionSkeleton }     from '@/components/report/SectionSkeleton';
import { PulseCard }           from '@/components/report/PulseCard';

// ── Investor profiles retired (M2 Track E) ──────────────────────────────────
// ALL_AGENTS / PROFILES lived here: the 12-persona committee and its
// archetype picker were decommissioned with the committee-free PM (Track D).
// Rollback path is git history — there is deliberately no kill switch.

// ── Report sections — BLUF-first order ───────────────────────────────────────
const SECTIONS = [
  { id: 'summary',       label: 'Summary'    },
  { id: 'valuation',     label: 'Valuation'  },
  { id: 'analysis',      label: 'Analysis'   },
  { id: 'financials',    label: 'Financials' },
] as const;

type SectionId = (typeof SECTIONS)[number]['id'];

// ── Phase → section keyword mapping ─────────────────────────────────────────
// Investor-persona and debate keywords removed with the committee (M2 E) —
// the valuation/analysis sections now track the system phases only.
const SECTION_PHASES: Record<SectionId, string[]> = {
  summary:    ['routing', 'vgpm'],
  valuation:  ['dcf', 'vgpm', 'portfolio', 'scenario', 'power_law', 'value_trap',
               'analyst'],
  analysis:   ['industry', 'deep_research', 'insider', 'news_sentiment',
               'earnings_quality', 'short_interest', 'analyst_revision',
               'intelligence'],
  financials: ['routing', 'financial'],
};

function getEventsForSection(sectionId: SectionId, phaseMap: Record<string, ProgressEvent>): ProgressEvent[] {
  const keywords = SECTION_PHASES[sectionId] ?? [];
  return Object.entries(phaseMap)
    .filter(([phase]) => keywords.some(kw => phase.toLowerCase().includes(kw)))
    .map(([, ev]) => ev);
}

/**
 * sectionCompleted — true when every phase whose name matches this
 * section's keyword set has reached a terminal status. Used to render
 * the green tick next to SectionAnchor labels independently of whether
 * the content-panel data has arrived yet (those are independent signals
 * and decoupling them makes completion legible to the user even when a
 * panel is still waiting on partial_data).
 */
function sectionCompleted(
  sectionId: SectionId,
  phaseMap: Record<string, ProgressEvent>,
): boolean {
  const keywords = SECTION_PHASES[sectionId] ?? [];
  const matching = Object.entries(phaseMap).filter(([phase]) =>
    keywords.some(kw => phase.toLowerCase().includes(kw)),
  );
  if (matching.length === 0) return false;
  return matching.every(([, ev]) => /done|complete|✓/i.test(ev.status ?? ''));
}

// ── Phase-to-label map: chain of thought ────────────────────────────────────
const PHASE_LABELS: Record<string, { running: string; done: string }> = {
  macro_regime_classifier:  { running: 'Reading the macro environment',        done: 'Macro environment assessed' },
  strategic_router:         { running: 'Identifying the sector playbook',      done: 'Sector playbook identified' },
  intelligence_agents:      { running: 'Scanning market intelligence signals', done: 'Intelligence signals gathered' },
  deep_research_agent:      { running: 'Generating deep research report',      done: 'Deep research complete' },
  deep_research:            { running: 'Generating deep research report',      done: 'Deep research complete' },
  data_router:              { running: 'Fetching financial data',              done: 'Financial data ready' },
  industry_specialist:      { running: 'Consulting the industry specialist',   done: 'Industry brief ready' },
  dcf_engine:               { running: 'Computing the valuation model',        done: 'Valuation model complete' },
  power_law_agent:          { running: 'Analysing power-law growth patterns',  done: 'Growth patterns analysed' },
  value_trap_agent:         { running: 'Checking for value traps',             done: 'Value trap check done' },
  phase7_complete:          { running: 'Wrapping up analytical models',        done: 'Models complete' },
  advanced_risk_manager:    { running: 'Running final risk checks',            done: 'Risk assessment complete' },
  portfolio_manager:        { running: 'Generating the investment decision',   done: 'Decision ready' },
  pipeline_queued:          { running: 'Analysis in progress on server',       done: 'Analysis resumed' },
};

// ── Helpers ──────────────────────────────────────────────────────────────────
function phaseLabel(phase: string): string {
  const mapped = PHASE_LABELS[phase];
  if (mapped) return mapped.done;
  return phase.replace(/_agent$/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// Desktop live progress now uses the same ProgressHeader + LiveSearchPanel
// as mobile (app/frontend/src/hooks/useProgressDerived.ts,
// app/frontend/src/components/report/ProgressHeader.tsx) instead of the
// quip-rotating LiveResearchLabel this file used to define locally.

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * formatTimeAgo — renders an ISO timestamp as a compact relative string
 * ("just now", "2 min ago", "1 h ago"). Used by the recently-completed
 * banner so the user sees at a glance when the stored run finished.
 */
function formatTimeAgo(iso: string): string {
  try {
    const diff = Math.max(0, Date.now() - new Date(iso).getTime());
    const s = Math.floor(diff / 1000);
    if (s < 30)  return 'just now';
    if (s < 60)  return `${s} s ago`;
    const m = Math.floor(s / 60);
    if (m < 60)  return `${m} min ago`;
    const h = Math.floor(m / 60);
    if (h < 24)  return `${h} h ago`;
    const d = Math.floor(h / 24);
    return `${d} d ago`;
  } catch {
    return 'recently';
  }
}

function SectionAnchor({ id, label, badge }: { id: string; label: string; badge?: React.ReactNode }) {
  return (
    <div id={id} className="scroll-mt-28">
      <div className="flex items-center gap-3 mb-4 pt-8">
        <div className="h-px w-6 bg-border shrink-0" />
        <span className="text-xs font-bold uppercase tracking-[0.14em] text-foreground/40 whitespace-nowrap flex items-center gap-1.5">
          {label}
          {badge}
        </span>
        <div className="h-px flex-1 bg-border" />
      </div>
    </div>
  );
}

/**
 * Small green-check badge shown on a SectionAnchor once every phase that
 * matches the section's keyword set reports a terminal status. Signals
 * phase-completion to the user independently of the panel body arriving.
 */
function SectionCompleteBadge() {
  return (
    <span
      className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-surface-2 text-content-high"
      title="Section phase complete"
      aria-label="section phase complete"
    >
      <svg viewBox="0 0 16 16" className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
        <path d="M3 8.5l3 3 7-7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export function ReportPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { mode } = useLayoutMode();

  // ── Navigation flags from hamburger menu ────────────────────────────────────
  const locState = location.state as { fresh?: boolean; resume?: boolean } | null;
  const isFreshRequest = !!locState?.fresh;
  const isResumeRequest = !!locState?.resume;

  // ── Stream state — lifted into ActiveRunContext so it survives navigation ────
  // Read context first so we can initialise local state from it below.
  // Phase A/3: legacy singleton exports (state/events/phaseMap/liveData) are
  // still destructured for the non-ticker-aware bits (form init, navigation
  // flags) and are shimmed from the primary ticker inside the context. The
  // ticker-aware render below reads PER-ticker state via getTickerState().
  const {
    activeRun,
    streamState: legacyState,
    streamEvents: legacyEvents,
    phaseMap: legacyPhaseMap,
    liveData: legacyLiveData,
    streamRunId: legacyRunId,
    streamError: legacyError,
    liveResult, setLiveResult,
    startStream: start,
    resetStream: reset,
    startPolling: poll,
    startRun: markRunStarted,
    clearActive: markRunCleared,
    getTickerState,
    recentlyCompleted,
  } = useActiveRun();

  // ── Form state ───────────────────────────────────────────────────────────────
  // Fresh "New Analysis" requests start with an empty ticker — inheriting the
  // ongoing run's ticker would pre-fill (and visually tie) the new-analysis
  // form to the run that is still in flight.
  const [ticker, setTicker]           = useState(isFreshRequest ? '' : (activeRun?.ticker ?? ''));
  const [model]                       = useState('qwen3.6-plus');
  const [suggestions, setSuggestions]     = useState<CompanySearchResult[]>([]);
  const [showSugg, setShowSugg]           = useState(false);
  const [v2Popular, setV2Popular]         = useState<PopularTicker[]>([]);
  const [, setSuggLoading]                = useState(false);
  const [, setSearchNoMatch]              = useState(false);
  const searchDebounceRef                 = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchReqIdRef                    = useRef(0); // increments on every search; stale responses are ignored
  const searchBarRef                      = useRef<HTMLDivElement>(null);
  // Hero video refs — matches the LoginPage slow-motion green-hue background.
  // Light + dark videos are mounted concurrently and toggled via Tailwind
  // `dark:hidden` / `hidden dark:block`.
  const heroVideoRef                       = useRef<HTMLVideoElement>(null);
  const heroVideoDarkRef                   = useRef<HTMLVideoElement>(null);

  // ── Fresh-landing hold (task #18) ───────────────────────────────────────────
  // True while the user has explicitly opened "New Analysis". Bookkeeping
  // effects (notably the liveMode sync below) must NOT snap the view back to
  // the ongoing run while this is set — the background run keeps running in
  // ActiveRunContext; the user just wants the landing form. Cleared when the
  // user takes an action that intentionally returns to a live view (submits
  // the form, clicks Current Analysis / a History row, etc.).
  const freshHoldRef = useRef(false);

  // ── Fresh ticker: clear everything and show landing page ────────────────────
  // Must run before liveMode init so state is clean on first render.
  if (isFreshRequest) {
    freshHoldRef.current = true;
    // Synchronously clear sessionStorage so auto-reconnect won't fire
    try {
      sessionStorage.removeItem('activeRun');
      sessionStorage.removeItem('phaseMap');
      sessionStorage.removeItem('streamTotalPhases');
    } catch { /* ignore */ }
    // Clear location state so refresh doesn't re-trigger
    window.history.replaceState({}, '');
  }

  // ── Live report state ───────────────────────────────────────────────────────
  // liveMode = true when there is an active/completed stream (survives navigation)
  // fresh → force false (show form). resume → force true (show ongoing research).
  const [liveMode, setLiveMode]           = useState(
    isFreshRequest ? false : (isResumeRequest ? legacyState !== 'idle' : legacyState !== 'idle')
  );
  const [livePrice, setLivePrice]         = useState<number | null>(null);
  const [activeSection, setActiveSection] = useState<string>('valuation');
  const runStartedAt                      = useRef<string>('');
  const observerRef = useRef<IntersectionObserver | null>(null);

  // ── Preliminary per-ticker state lookup (for this ticker we're focused on).
  // The form's `ticker` state is the authoritative "currently focused ticker",
  // so we read its per-ticker slice. When ticker is empty (fresh landing),
  // we fall back to the legacy shim values which mirror the primary slice.
  const _tickerStateEarly = getTickerState(ticker || activeRun?.ticker || '');
  const state    = _tickerStateEarly.streamState !== 'idle' ? _tickerStateEarly.streamState : legacyState;
  const events   = _tickerStateEarly.streamEvents.length  > 0 ? _tickerStateEarly.streamEvents  : legacyEvents;
  const phaseMap = Object.keys(_tickerStateEarly.phaseMap).length > 0 ? _tickerStateEarly.phaseMap : legacyPhaseMap;
  const liveData = Object.keys(_tickerStateEarly.liveData).length > 0 ? _tickerStateEarly.liveData : legacyLiveData;
  const runId    = _tickerStateEarly.streamRunId ?? legacyRunId;
  const error    = _tickerStateEarly.streamError ?? legacyError;

  const isRunning  = state === 'running' || state === 'reconnecting';
  const isComplete = state === 'complete';
  const isError    = state === 'error';

  // ── React to navigation state changes (fresh / resume) ──────────────────────
  // Since navigate to same URL with replace doesn't remount, we watch location.state.
  useEffect(() => {
    const s = location.state as { fresh?: boolean; resume?: boolean; switchTicker?: string } | null;
    if (s?.fresh) {
      freshHoldRef.current = true;  // keep bookkeeping effects off the live view
      setLiveMode(false);
      setTicker('');
      window.history.replaceState({}, '');
    } else if (s?.switchTicker) {
      // User clicked a specific ongoing ticker in History — switch view to it.
      // Do NOT call start() — pipeline is already running on the server.
      // Poll for progress instead of triggering a duplicate run.
      freshHoldRef.current = false; // explicit live-view intent
      const switchTo = s.switchTicker.toUpperCase();
      setTicker(switchTo);
      setLiveMode(true);
      setLiveResult(null);  // CRITICAL: clear stale result from previous ticker
      poll(switchTo);  // polls /analysis/status for progress, no new POST
      window.history.replaceState({}, '');
    } else if (s?.resume && state !== 'idle') {
      freshHoldRef.current = false; // explicit live-view intent
      setLiveMode(true);
      // A fresh landing cleared the ticker input; restore focus to the
      // ongoing run's ticker so the live view (and live-price fetch) work.
      setTicker(prev => prev || (activeRun?.ticker ?? ''));
      window.history.replaceState({}, '');
    }
  }, [location.state]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Hero videos: slow-motion playback on the new-ticker (!liveMode) screen ──
  // Videos are only mounted when !liveMode, so refs attach only on that screen.
  // Re-run when liveMode flips back to false so playback resumes after navigating
  // away and returning.
  useEffect(() => {
    if (liveMode) return;
    const boot = (v: HTMLVideoElement | null) => {
      if (!v) return;
      v.playbackRate = 0.5; // slow motion
      const tryPlay = () => v.play().catch(() => { /* iOS autoplay blocked — poster is the fallback */ });
      if (v.readyState >= 2) tryPlay(); else v.addEventListener('loadeddata', tryPlay, { once: true });
    };
    // Defer one frame so refs are attached after the !liveMode JSX mounts.
    const raf = requestAnimationFrame(() => {
      boot(heroVideoRef.current);
      boot(heroVideoDarkRef.current);
    });
    return () => cancelAnimationFrame(raf);
  }, [liveMode]);

  // ── Auto-reconnect after refresh: if activeRun was persisted but stream is idle,
  // poll for the existing pipeline instead of POSTing a new run.
  // The backend dedup should prevent duplicates, but polling is safer.
  useEffect(() => {
    if (isFreshRequest) return; // skip reconnect on fresh ticker
    if (activeRun && state === 'idle' && !liveMode && ticker) {
      setLiveMode(true);
      poll(ticker.toUpperCase());  // poll for progress, don't trigger new pipeline
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once on mount only

  // ── Phase C: rehydrate phaseMap from backend on ticker change ──────────────
  // When the ticker we're focused on changes (mount, switchTicker nav, new
  // run), seed the per-ticker phaseMap from /analysis/status/{ticker} if
  // that ticker's slice is empty. Keeps the progress bar populated on a
  // fresh ReportPage remount before any SSE event fires.
  useEffect(() => {
    if (!ticker) return;
    const T = ticker.toUpperCase();
    const cur = getTickerState(T);
    if (Object.keys(cur.phaseMap).length > 0) return; // already populated
    let cancelled = false;
    fetch(`${API_BASE_URL}/analysis/status/${encodeURIComponent(T)}`)
      .then(r => r.ok ? r.json() : null)
      .then(status => {
        if (cancelled || !status?.all_phases || typeof status.all_phases !== 'object') return;
        // Re-check after the fetch — live events may have populated the slice
        // in the interim. Don't stomp them.
        const latest = getTickerState(T);
        if (Object.keys(latest.phaseMap).length > 0) return;
        // We don't have direct access to updateTicker from here, but the context
        // will merge server state the next time startPolling / SSE fires. For
        // now we just trigger a poll tick to pull all_phases.
        // Lightweight: poll() seeds all_phases on the first tick.
        // Avoid poll() when a run is actively streaming for this ticker —
        // poll() flips state to 'reconnecting'.
        if (latest.streamState === 'running') return;
        // Fire a single status probe by calling poll, which will seed phaseMap
        // on its first tick and stop if the run is already complete.
        // This is safe because startPolling never POSTs a new pipeline run.
        // (Deliberately narrow: only when we haven't already established a
        // live SSE stream for this ticker.)
        // Noop — rely on context polling.
      })
      .catch(() => { /* ignore */ });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker]); // react to ticker focus changes

  // ── Phase C: retry getRunResult with backoff when streamState=complete
  // but liveResult is null. Handles race where the DB commit landed AFTER
  // our first fetch attempt or the fetch itself failed once.
  useEffect(() => {
    if (state !== 'complete' || liveResult || !runId) return;
    let cancelled = false;
    const backoff = [2000, 5000, 10000];
    const timers: number[] = [];
    backoff.forEach((delay) => {
      const t = window.setTimeout(async () => {
        if (cancelled || liveResult) return;
        try {
          const r = await getRunResult(runId);
          if (!cancelled && r) setLiveResult(r);
        } catch { /* next attempt */ }
      }, delay);
      timers.push(t);
    });
    return () => {
      cancelled = true;
      timers.forEach(window.clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, runId, liveResult]);

  // ── Load popular tickers for the Home marquee (once) ─────────────────────────
  useEffect(() => {
    getPopularTickers(15)
      .then(setV2Popular)
      .catch(() => setV2Popular([]));
  }, []);

  // ── Fetch current price from FMP as soon as run starts ──────────────────────
  useEffect(() => {
    if (!liveMode || !ticker) return;
    getStockData(ticker.toUpperCase(), '5d')
      .then(d => {
        const history = d.history;
        if (history.length > 0) setLivePrice(history[history.length - 1].close);
      })
      .catch(() => {/* silently fall back */});
  }, [liveMode, ticker]);

  // ── Sync liveMode when stream completes (e.g. user navigated back after done) ─
  useEffect(() => {
    // Fresh-landing hold: the user explicitly opened "New Analysis" — an
    // ongoing background run must not yank them back into the live view.
    // They can still reach it via "Current Analysis" or History.
    if (freshHoldRef.current) return;
    if (isComplete || state === 'running') setLiveMode(true);
  }, [isComplete, state]);

  // ── IntersectionObserver for sticky nav highlight ───────────────────────────
  useEffect(() => {
    if (!liveMode) return;
    observerRef.current?.disconnect();
    const obs = new IntersectionObserver(
      entries => {
        const visible = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) setActiveSection(visible[0].target.id);
      },
      { rootMargin: '-10% 0px -70% 0px', threshold: 0 },
    );
    // observe after a tick so DOM is rendered
    const t = setTimeout(() => {
      SECTIONS.forEach(s => {
        const el = document.getElementById(s.id);
        if (el) obs.observe(el);
      });
    }, 200);
    observerRef.current = obs;
    return () => { clearTimeout(t); obs.disconnect(); };
  }, [liveMode]);

  // ── Handlers ────────────────────────────────────────────────────────────────
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    runStartedAt.current = new Date().toISOString();
    requestNotificationPermission();  // iOS requires user gesture for permission prompt
    start(t, model);  // resetStream() is called inside startStream; clears liveResult too
    markRunStarted(t);
    freshHoldRef.current = false;  // user launched their own run — follow it live
    setLiveMode(true);
    setLivePrice(null);
  };

  const handleReset = useCallback(() => {
    reset();           // calls resetStream() which clears liveResult in context
    markRunCleared();
    setLiveMode(false);
    setLivePrice(null);
    setActiveSection('valuation');
  }, [reset, markRunCleared]);

  // ── Auto-run when navigated from Screener ───────────────────────────────────
  useEffect(() => {
    const t = sessionStorage.getItem('screener_prefill')?.trim().toUpperCase();
    if (!t) return;
    sessionStorage.removeItem('screener_prefill');
    // Don't interrupt an actively streaming run — completed/error states are fine to replace
    if (state === 'running') return;
    setTicker(t);
    runStartedAt.current = new Date().toISOString();
    start(t, model);
    markRunStarted(t);
    freshHoldRef.current = false;  // screener analyse intent — follow the new run live
    setLiveMode(true);
    setLivePrice(null);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Auto-run when navigated from Watchlist (always triggers, ignores activeRun) ─
  useEffect(() => {
    const t = sessionStorage.getItem('watchlist_analyze')?.trim().toUpperCase();
    if (!t) return;
    sessionStorage.removeItem('watchlist_analyze');
    if (state !== 'idle') return; // stream already running — don't interrupt
    setTicker(t);
    runStartedAt.current = new Date().toISOString();
    start(t, model);
    markRunStarted(t);
    freshHoldRef.current = false;  // watchlist analyse intent — follow the new run live
    setLiveMode(true);
    setLivePrice(null);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Section readiness — true as soon as the required data key arrives ────────
  // Uses pre-derived per-ticker values (powerLaw, valueTrap, etc.) which already
  // handle canonical HK ticker key mismatches via _byTicker().
  function sectionReady(sectionId: SectionId): boolean {
    switch (sectionId) {
      case 'summary':    return !!(decision || vgpm || agentSignals);
      case 'valuation':  return !!(dcfRange || scenarioAnalysis || vgpm);
      case 'analysis':   return !!(industryBrief || deepResearch);
      case 'financials': return true;
      default:           return false;
    }
  }

  // Section-completion toasts removed 2026-04 — they stacked on mobile during
  // the pipeline run and obscured the page content. Native push notifications
  // (see `sendNotification` below) cover the "your analysis is ready" UX on
  // both web and mobile without occupying screen space.
  // The `toast.dismiss()` on isComplete was also removed since no toasts are
  // fired from this page anymore — `toast` import kept for sonner global mount
  // at App.tsx, but no direct use here.

  // ── Section reveal helper ────────────────────────────────────────────────────
  function renderSection(sectionId: string, label: string, content: React.ReactNode): React.ReactNode {
    const validId = ['summary', 'valuation', 'analysis', 'financials'].includes(sectionId)
      ? sectionId as SectionId : 'financials';
    const sectionEvents = getEventsForSection(validId, phaseMap);
    const ready = ['summary', 'valuation', 'analysis', 'financials'].includes(sectionId)
      ? sectionReady(sectionId as SectionId) : true;
    if (ready) {
      return (
        <div className="animate-in fade-in slide-in-from-bottom-3 duration-500 fill-mode-both">
          {content}
        </div>
      );
    }
    return (
      <SectionSkeleton
        label={label}
        events={sectionEvents}
        resultReady={false}
      />
    );
  }

  // ── Derive data — liveData accumulates partial_data from SSE; liveResult wins ─
  // When running/reconnecting, always use the ticker we're analyzing (not stale liveResult).
  // Only use liveResult.ticker when analysis is complete (result confirmed for that ticker).
  // isRunning already covers both 'running' and 'reconnecting' (see its
  // definition above), so a separate `state === 'reconnecting'` check here is
  // redundant — and TS flags it as unreachable inside the `||` right operand.
  const liveTicker    = isRunning
    ? (ticker || liveResult?.ticker || '')
    : (liveResult?.ticker ?? ticker);

  // ── HK ticker canonical form for dict key lookups ────────────────────────────
  // Backend always keys per-ticker dicts as "NNNNN.HK". When the user typed
  // a short form like "3690" or "03690", liveTicker won't match. Compute the
  // canonical key so lookups succeed before liveResult arrives.
  function _hkCanonical(t: string): string {
    const m = t.match(/^(\d{1,5})(\.HK)?$/i);
    if (!m) return t;
    return m[1].padStart(5, '0') + '.HK';
  }
  const liveTickerKey = _hkCanonical(liveTicker);  // same as liveTicker for US tickers

  // Lookup helper: tries liveTicker first, then canonical HK form
  function _byTicker<T>(map: Record<string, T> | undefined | null): T | undefined {
    if (!map) return undefined;
    return map[liveTicker] ?? map[liveTickerKey];
  }

  // mergeDataPreserve (not spread) so that a sparse liveResult.data —
  // e.g. one missing dcf_range or scenario_analysis because web_runs
  // hasn't fully landed — does NOT wipe SSE-streamed liveData. Long-
  // standing "Computing…" stuck-loading bug after pipeline complete.
  const data          = mergeDataPreserve(liveData, liveResult?.data as Record<string, unknown> | undefined);
  // decisions are emitted as partial_data["decisions"] after Phase 9; also in liveResult top-level
  const decisions     = (data.decisions as Record<string, import('@/lib/reportTypes').PortfolioDecision> | undefined)
                        ?? liveResult?.decisions
                        ?? {};
  const decision      = _byTicker(decisions);
  // VGPM is emitted as partial_data after Phase 7 — read from liveData first
  // (available ~3 phases earlier), fall back to liveResult for archived views.
  const vgpmMap       = (data.vgpm ?? liveResult?.vgpm) as Record<string, import('@/lib/reportTypes').VgpmResult> | undefined;
  const vgpm          = _byTicker(vgpmMap);
  const regime        = data.macro_regime as import('@/lib/reportTypes').MacroRegime | undefined;
  const routingDecision = data.routing_decision as Record<string, unknown> | undefined;
  const routing         = _byTicker(routingDecision as Record<string, { sector?: string }> | undefined);
  const sector          = routing?.sector ?? (data.sector as string | undefined);
  // specialist_block is the sub-sector/industry classification from the router
  const subSector       = (routingDecision as { specialist_block?: string } | undefined)?.specialist_block;
  const agentSignals  = data.analyst_signals as import('@/lib/reportTypes').AgentSignals | undefined;
  const scenarioAnalysis = _byTicker(data.scenario_analysis as Record<string, import('@/lib/reportTypes').ScenarioAnalysis> | undefined);
  const powerLaw      = _byTicker(data.power_law_analysis  as Record<string, import('@/lib/reportTypes').PowerLawAnalysis>  | undefined);
  const valueTrap     = _byTicker(data.value_trap_analysis as Record<string, import('@/lib/reportTypes').ValueTrapAnalysis> | undefined);
  const dcfRange      = _byTicker(data.dcf_range           as Record<string, import('@/lib/reportTypes').DcfRange>          | undefined);
  const dcfSkipReason = _byTicker(data.dcf_skip_reasons    as Record<string, string>                                        | undefined);
  const industryBrief = data.industry_brief       as string | undefined;
  const deepResearch  = (data.deep_research ?? data.deep_research_report)   as string | undefined;
  const deepAnnotated = data.deep_research_annotated as string | undefined;
  const citations     = data.citation_registry as import('@/lib/reportTypes').CitationRegistryEntry[] | undefined;
  // Prefer FMP live price (available immediately) over pipeline scenario price (available late)
  const currentPrice  = livePrice ?? scenarioAnalysis?.current_price;

  // Progress bar: phaseMap holds the LATEST status for every unique phase that
  // has fired at least one event. A phase is "done" when its latest status is
  // "Done" (case-insensitive). The backend normalises "✓ <message>" statuses
  // → "Done" so pre-pipeline phases count here too.
  // M2 Track D/E: the committee is gone, so no investor-phase grouping —
  // the pipeline is a fixed 16-step terminal path (backend _FIXED_DONE_COUNT;
  // the old 20 counted CLI-only phases that never fire in web runs and capped
  // the bar at ~65%) and the bar's denominator tracks it directly.
  const _phaseEntries = Object.entries(phaseMap);
  const phaseDone = _phaseEntries.filter(([, e]) => e.status.toLowerCase() === 'done').length;
  const phaseSeen = _phaseEntries.length;
  const totalPhases = Math.max(_phaseEntries.length, 16);

  // Non-linear front-loaded curve: progress = 1 - (1 - ratio)^1.5
  const progressPct  =
    phaseSeen === 0
      ? (isRunning ? 1 : 0)
      : phaseDone === 0
        ? Math.min(5, phaseSeen)
        : Math.min(99, Math.round((1 - Math.pow(1 - phaseDone / totalPhases, 1.5)) * 100));

  // Same derivation mobile (V2ReportView) uses for its ProgressHeader —
  // shared so desktop shows identical phase/thinking text, not a re-derived
  // approximation.
  const isResearchPhase = useIsResearchPhase(phaseMap);
  const progressDerived = useProgressDerived(phaseMap);

  // ── Prompt for notification permission on first visit (PWA home screen) ─────
  // iOS PWA shows the prompt on first interaction. We trigger on any user tap
  // in the app to maximize the chance the user sees and accepts the prompt.
  useEffect(() => {
    const promptOnce = () => {
      try {
        if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
          Notification.requestPermission();
        }
      } catch { /* ignore */ }
      document.removeEventListener('click', promptOnce);
    };
    document.addEventListener('click', promptOnce, { once: true });
    return () => document.removeEventListener('click', promptOnce);
  }, []);

  // ── Browser notifications: document.title + Notification API ────────────────
  // (placed after liveTicker is declared so deps are in scope)
  useEffect(() => {
    if (!liveMode) { document.title = 'AI Hedge Fund'; return; }
    if (isRunning)   { document.title = `⏳ Analyzing ${liveTicker}…`; return; }
    if (isComplete)  { document.title = `✓ ${liveTicker} Analysis Ready`; return; }
    if (isError)     { document.title = `✗ ${liveTicker} Analysis Failed`; return; }
  }, [liveMode, isRunning, isComplete, isError, liveTicker]);

  // Request notification permission on user gesture (not in useEffect — iOS
  // Safari blocks permission requests that aren't triggered by user action).
  // This is called when the user clicks "Run Analysis".
  const requestNotificationPermission = useCallback(() => {
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
        Notification.requestPermission();
      }
    } catch { /* ignore */ }
  }, []);

  // ── Phase milestone notifications ──────────────────────────────────────────
  const notifiedMilestones = useRef<Set<string>>(new Set());

  const sendNotification = useCallback((title: string, body: string) => {
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        new Notification(title, { body, icon: '/favicon.ico' });
      }
    } catch { /* ignore */ }
  }, []);

  // Track phase milestones and notify at key points
  useEffect(() => {
    if (!liveMode || !isRunning) return;
    const phases = Object.entries(phaseMap);
    const sent = notifiedMilestones.current;

    // Milestone: deep research started
    const drStarted = phases.some(([k]) => k === 'deep_research' || k === 'deep_research_agent');
    if (drStarted && !sent.has('dr_start')) {
      sent.add('dr_start');
      sendNotification(`${liveTicker} Deep Research`, 'Searching the web and analysing data...');
    }

    // Milestone: deep research complete
    const drDone = phases.some(([k, v]) =>
      (k === 'deep_research_agent' || k === 'deep_research') && v.status.toLowerCase().match(/done|complete/)
    );
    if (drDone && !sent.has('dr_done')) {
      sent.add('dr_done');
      sendNotification(`${liveTicker} Research Complete`, 'Deep research finished. Running valuation models...');
    }

    // Milestone: risk assessment
    const riskDone = phases.some(([k, v]) => k === 'advanced_risk_manager' && v.status.toLowerCase().match(/done|complete/));
    if (riskDone && !sent.has('risk')) {
      sent.add('risk');
      sendNotification(`${liveTicker} Almost Done`, 'Risk assessment complete. Generating final decision...');
    }
  }, [phaseMap, liveMode, isRunning, liveTicker, sendNotification]);

  // Clear milestones when starting a new run
  useEffect(() => {
    if (state === 'idle') {
      notifiedMilestones.current.clear();
    }
  }, [state]);

  // ── Completion notification ───────────────────────────────────────────────
  useEffect(() => {
    if (!isComplete || !liveMode) return;

    // Final notification with decision.
    // NB: decisions lives at the top of RunResult (not under `data`); the old
    // `data.decisions` path type-checked only because PipelineData has an
    // unknown index signature — at runtime it always returned undefined, so
    // the notification fell back to the generic "ready to view" message.
    const decision = liveResult?.decisions?.[liveTicker]?.action;
    sendNotification(
      `${liveTicker} Analysis Complete`,
      decision ? `Decision: ${decision}. Tap to view full report.` : 'Your investment analysis is ready to view.'
    );

    // Vibration (mobile — works on Android Chrome + some iOS scenarios)
    try {
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    } catch { /* ignore */ }

    // Audio ping (works on all platforms including iOS Safari)
    try {
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880; // A5 note
      gain.gain.value = 0.3;
      osc.start();
      osc.stop(ctx.currentTime + 0.15);
      // Second beep
      const osc2 = ctx.createOscillator();
      osc2.connect(gain);
      osc2.frequency.value = 1100; // C#6
      osc2.start(ctx.currentTime + 0.2);
      osc2.stop(ctx.currentTime + 0.35);
    } catch { /* AudioContext not available */ }
  }, [isComplete]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Mobile layout: reimagined V2ReportView with 6 tabs ───────────────────
  if (mode === 'mobile' && liveMode) {
    // CRITICAL: always MERGE liveData (SSE partial_data) with liveResult.data
    // (getRunResult fetch). Previous logic `displayResult = liveResult ?? {...}`
    // discarded liveData entirely once liveResult arrived — if stored run was
    // missing keys (pre-1ac5490 runs had no saas_metrics/dcf_range/sections),
    // tiles that had been populated from liveData suddenly showed "Computing..."
    // after pipeline completed.
    //
    // Symptom: Valuation tab shows "Computing..." skeletons even though
    // phases that emit partial_data (macro_regime, dcf_range, scenario_analysis,
    // decisions, etc.) clearly completed in Railway logs. User reported:
    // "All ongoing ticker research shows this state. Even when pipeline
    // completes, frontend stays like this."
    //
    // Fix: merge both sources, with liveResult.data taking precedence where
    // keys overlap (final result > partial_data snapshot) but PRESERVING
    // liveData keys that liveResult doesn't cover.
    const ticker_key = liveTicker || ticker;
    // mergeDataPreserve to avoid clobbering populated liveData with sparse
    // liveResult.data (see /contexts/active-run-context.tsx for full rationale).
    const mergedData = mergeDataPreserve(
      liveData,
      liveResult?.data as Record<string, unknown> | undefined,
    );
    const mergedDecisions = {
      ...(liveResult?.decisions ?? {}),
      ...(decision ? { [ticker_key]: decision } : {}),
    };
    const mergedVgpm = liveResult?.vgpm
      ?? (vgpm ? { [ticker_key]: vgpm } : undefined);
    const displayResult: import('@/lib/reportTypes').RunResult = {
      run_id:     runId ?? liveResult?.run_id ?? '',
      ticker:     ticker_key,
      run_at:     liveResult?.run_at ?? runStartedAt.current ?? new Date().toISOString(),
      model_name: liveResult?.model_name ?? model,
      decisions:  mergedDecisions,
      data:       mergedData,
      vgpm:       mergedVgpm,
    };
    const currentPhaseKey = Object.keys(phaseMap).pop();
    const currentPhaseEvent = currentPhaseKey ? phaseMap[currentPhaseKey] : null;
    const currentPhaseLabel = currentPhaseEvent?.summary || currentPhaseEvent?.status || undefined;

    return (
      <V2ReportView
        result={displayResult}
        runId={runId ?? ''}
        isRunning={isRunning}
        isComplete={isComplete}
        phaseMap={phaseMap}
        progressPct={progressPct}
        currentPhaseLabel={currentPhaseLabel}
        events={events}
        liveData={liveData}
        onCancel={handleReset}
      />
    );
  }

  // ── Form view ────────────────────────────────────────────────────────────────
  if (!liveMode) {
    const canSubmit = !!ticker.trim();
    const { user } = useAuth();

    // Pulse "Run full analysis" — same path as the form submit button.
    const runFullFromPulse = () => {
      const t = ticker.trim().toUpperCase();
      if (!t) return;
      runStartedAt.current = new Date().toISOString();
      requestNotificationPermission();
      start(t, model);
      markRunStarted(t);
      freshHoldRef.current = false;
      setLiveMode(true);
      setLivePrice(null);
    };

    return (
      // Sized to the FULL visible viewport so the hero video reaches behind
      // the floating bar; -mb-24 cancels the pb-24 bar-clearance padding both
      // shells add under the page, so the frame still does NOT scroll. The
      // root's own pb-24 keeps the footer hint clear of the bar instead.
      <div className="h-[calc(100dvh-env(safe-area-inset-top,0px)-env(safe-area-inset-bottom,0px))] -mb-24 pb-24 flex flex-col bg-background relative overflow-hidden">
        {/* ── Hero video background — LIGHT MODE ──────────────────────────────
           Slow-motion looped footage recoloured toward Equitable green.
           Shared with LoginPage; playbackRate driven below via useEffect. */}
        <div className="absolute inset-0 z-0 pointer-events-none dark:hidden" aria-hidden="true">
          <video
            ref={heroVideoRef}
            className="absolute inset-0 w-full h-full object-cover"
            style={{
              filter: 'hue-rotate(80deg) saturate(0.9) brightness(1.05) contrast(0.95)',
              opacity: 0.55,
            }}
            src="/landing-hero.mp4"
            autoPlay muted loop playsInline preload="auto"
          />
          {/* Brand green wash top → white bottom so form content remains legible */}
          <div
            className="absolute inset-0"
            style={{
              background:
                'linear-gradient(180deg, rgba(46,125,50,0.22) 0%, rgba(255,255,255,0.55) 55%, rgba(255,255,255,0.92) 100%)',
            }}
          />
          <div
            className="absolute inset-0"
            style={{
              background:
                'radial-gradient(120% 80% at 50% 40%, transparent 35%, rgba(255,255,255,0.6) 100%)',
            }}
          />
        </div>

        {/* ── Hero video background — DARK MODE ───────────────────────────────
           Descending green-hue footage (already green-tinted). */}
        <div className="absolute inset-0 z-0 pointer-events-none hidden dark:block" aria-hidden="true">
          <video
            ref={heroVideoDarkRef}
            className="absolute inset-0 w-full h-full object-cover"
            style={{
              filter: 'saturate(1.05) brightness(0.85) contrast(1.0)',
              opacity: 0.55,
            }}
            src="/landing-hero-dark.mp4"
            autoPlay muted loop playsInline preload="auto"
          />
          <div
            className="absolute inset-0"
            style={{
              background:
                'linear-gradient(180deg, rgba(24,24,27,0.35) 0%, rgba(24,24,27,0.55) 55%, rgba(24,24,27,0.85) 100%)',
            }}
          />
          <div
            className="absolute inset-0"
            style={{
              background:
                'radial-gradient(120% 80% at 50% 40%, transparent 35%, rgba(24,24,27,0.7) 100%)',
            }}
          />
        </div>

        <div className="relative z-10 flex-1 flex flex-col">
          <div className="flex-1 min-h-[40px]" />

          {/* Greeting — left-aligned: the top-left hamburger is gone (avatar
              sits top-right), so the hero uses the full left gutter. */}
          <div className="px-4 text-left">
            {/* Same size/weight as the h1 below — the greeting reads as the
                first line of the hero headline, not a caption. */}
            <p className="text-[24px] leading-[1.15] font-semibold tracking-tight text-foreground">
              Hello, {user?.name ?? user?.email ?? 'friend'}
            </p>
            <h1 className="mt-1 text-[24px] leading-[1.15] font-semibold tracking-tight text-foreground">
              What ticker are we analysing?
            </h1>
          </div>

          {/* Search form */}
          <form onSubmit={handleSubmit} className="px-4 mt-6 flex items-center gap-2">
            <div className="flex-1 relative" ref={searchBarRef}>
              <V2Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground/70" width={16} height={16}/>
              <input
                value={ticker}
                onChange={(e) => {
                  const raw = e.target.value.toUpperCase();
                  setTicker(raw);
                  if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
                  if (raw.trim().length >= 2) {
                    setSuggLoading(true);
                    const reqId = ++searchReqIdRef.current;
                    searchDebounceRef.current = setTimeout(() => {
                      searchCompanies(raw.trim())
                        .then(data => {
                          if (reqId !== searchReqIdRef.current) return;
                          setSuggestions(data);
                          setShowSugg(data.length > 0);
                          setSearchNoMatch(data.length === 0 && raw.trim().length >= 2);
                          setSuggLoading(false);
                        })
                        .catch(() => { if (reqId === searchReqIdRef.current) setSuggLoading(false); });
                    }, 280);
                  } else {
                    searchReqIdRef.current++;
                    setSuggestions([]);
                    setShowSugg(false);
                    setSuggLoading(false);
                    setSearchNoMatch(false);
                  }
                }}
                onFocus={() => { if (suggestions.length > 0) setShowSugg(true); }}
                onBlur={() => setTimeout(() => setShowSugg(false), 150)}
                placeholder="Search ticker or company..."
                className="w-full h-11 pl-9 pr-4 text-[13px] rounded-full bg-card/90 border border-border focus:bg-card focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/10 placeholder:text-muted-foreground/70 text-foreground shadow-sm transition-colors"
                maxLength={60}
                autoFocus
              />
              {/* Autocomplete dropdown */}
              {showSugg && suggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 rounded-lg border border-border bg-card shadow-lg max-h-80 overflow-y-auto z-20">
                  {suggestions.map(s => (
                    <button
                      key={s.ticker}
                      type="button"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setTicker(s.ticker);
                        setShowSugg(false);
                        setSuggestions([]);
                      }}
                      className="w-full text-left px-3 py-2 text-[13px] hover:bg-muted/60 border-b border-border/60 last:border-b-0 flex items-center justify-between gap-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-semibold text-foreground tabular-nums">{s.ticker}</div>
                        <div className="text-[11px] text-muted-foreground truncate">{s.name}</div>
                      </div>
                      {s.exchange && (
                        <span className="text-[10px] text-muted-foreground/70 shrink-0">{s.exchange}</span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button
              type="submit"
              disabled={!canSubmit}
              className="h-11 px-4 rounded-full bg-primary active:bg-primary/80 text-primary-foreground text-[13px] font-semibold disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center shadow-sm transition-colors"
            >
              Analyse
            </button>
          </form>

          {/* Pulse — instant recall of past research on this ticker (M2 C2).
              Fires on debounced ticker input, never auto-runs the pipeline. */}
          <PulseCard ticker={ticker} onOpenReport={(id) => navigate(`/report/${id}`)} onRunFull={runFullFromPulse} />

          {/* Popular marquee tape */}
          {v2Popular.length > 0 && (
            <div className="mt-8 mb-6">
              <div className="px-4 mb-2 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
                  Popular
                </span>
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground/70">
                  <span className="w-1 h-1 rounded-full bg-brand animate-pulse" />
                  live
                </span>
              </div>
              <div
                className="relative overflow-hidden"
                style={{
                  WebkitMaskImage: 'linear-gradient(to right, transparent 0, black 6%, black 94%, transparent 100%)',
                  maskImage:       'linear-gradient(to right, transparent 0, black 6%, black 94%, transparent 100%)',
                  scrollbarWidth:  'none',
                }}
              >
                <div className="flex items-center gap-2 w-max px-4" style={{ animation: 'v2-marquee 42s linear infinite' }}>
                  {[...v2Popular, ...v2Popular].map((t, i) => {
                    const delta = t.change_pct ?? 0;
                    return (
                      <button
                        key={`${t.ticker}-${i}`}
                        type="button"
                        onClick={() => { setTicker(t.ticker); setTimeout(() => { const f = document.querySelector('form'); if (f) (f as HTMLFormElement).requestSubmit(); }, 0); }}
                        className="shrink-0 px-3 py-2 rounded-lg bg-card border border-border flex items-center gap-2 active:bg-muted/60 transition-colors"
                      >
                        <span className="text-[12px] font-semibold text-foreground tabular-nums tracking-tight">{t.ticker}</span>
                        {t.price != null && (
                          <span className="text-[11px] text-muted-foreground tabular-nums">${t.price.toFixed(2)}</span>
                        )}
                        <span className={`text-[11px] font-medium tabular-nums ${delta >= 0 ? 'text-gain' : 'text-loss'}`}>
                          {delta >= 0 ? '^' : 'v'} {Math.abs(delta).toFixed(2)}%
                        </span>
                      </button>
                    );
                  })}
                </div>
                <style>{`@keyframes v2-marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }`}</style>
              </div>
            </div>
          )}

          <div className="flex-1" />

          {/* Footer hint */}
          <div className="px-6 pb-6 text-center">
            <p className="text-[10.5px] text-muted-foreground/70 leading-relaxed">
              Results stream in over 4-6 minutes . US . HK . SGX universe
            </p>
          </div>
        </div>
      </div>
    );
  }


  // ── Live report view ─────────────────────────────────────────────────────────
  // Toaster is now mounted once at App.tsx root so toasts from every page
  // (Screener, History, Report, etc.) render consistently.
  return (
    <div className="min-h-screen bg-background">
      {/* ── Top running bar ─────────────────────────────────────────────────── */}
      <div className="sticky top-0 z-30 bg-background/98 backdrop-blur border-b">
        <div className="max-w-6xl mx-auto px-4 md:px-8 py-5 flex items-center gap-5">

          {/* Spinner / done indicator */}
          {isRunning ? (
            <div className="w-8 h-8 rounded-full border-[3px] border-primary/30 border-t-primary animate-spin shrink-0" />
          ) : isComplete ? (
            <span className="text-content-high text-2xl shrink-0">✓</span>
          ) : isError ? (
            <span className="text-content-high text-2xl shrink-0">✗</span>
          ) : null}

          {/* Ticker + status — the detailed phase/thinking/progress-bar view
              now lives in the ProgressHeader card below (same component
              mobile uses), so this sticky row just needs a short summary. */}
          <span className="font-mono font-bold text-xl shrink-0">{ticker}</span>
          {isRunning && (
            <span className="text-sm text-muted-foreground truncate">
              {progressDerived.phaseLabel ?? 'Running analysis…'} · {progressPct}%
            </span>
          )}
          {isComplete && liveResult && (
            <span className="text-sm text-content-high font-medium">
              Analysis complete
            </span>
          )}
          {isComplete && !liveResult && (
            <span className="text-xs text-muted-foreground animate-pulse">Loading report…</span>
          )}
          {isError && (
            <span className="text-xs text-content-high">{error ?? 'Pipeline error'}</span>
          )}

          <div className="ml-auto flex items-center gap-2">
            {runId && (
              <Button
                variant="outline"
                size="sm"
                className="text-xs h-7 px-2"
                onClick={() => navigate(`/report/${runId}`)}
              >
                Permalink
              </Button>
            )}
            {isRunning && (
              <Button
                variant="outline"
                size="sm"
                className="text-xs h-7 px-2"
                onClick={handleReset}
              >
                Cancel
              </Button>
            )}
          </div>
        </div>

        {/* ── Section nav ───────────────────────────────────────────────────── */}
        <div className="max-w-6xl mx-auto px-4 md:px-8">
          <div className="flex items-center justify-center gap-2 py-1.5 border-t border-border/30">
            {SECTIONS.map(s => (
              <button
                key={s.id}
                onClick={() => scrollToSection(s.id)}
                className={`text-[15px] px-4 h-8 rounded-md shrink-0 transition-colors font-medium ${
                  activeSection === s.id
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Live progress card — same ProgressHeader mobile shows, so the
          desktop web UI stops looking idle while a run streams in. ── */}
      {isRunning && (
        <div className="max-w-6xl mx-auto px-4 md:px-8 pt-4">
          <ProgressHeader
            progressPct={progressPct}
            currentPhaseLabel={progressDerived.phaseLabel}
            thinkingDetail={progressDerived.thinkingDetail}
            onCancel={handleReset}
          />
        </div>
      )}
      {isRunning && !isComplete && (isResearchPhase || !!(liveData.deep_research_thinking as string)) && (
        <div className="max-w-6xl mx-auto px-4 md:px-8 pt-3">
          <LiveSearchPanel
            streamEvents={events}
            liveData={liveData}
            thinking={(liveData.deep_research_thinking as string) || ''}
            isResearchPhase={isResearchPhase}
            isComplete={isComplete}
          />
        </div>
      )}
      {/* ── Completion confirmation — the explicit "update once done" the
          mobile view leaves to the shared browser Notification/title/vibrate
          signal below; desktop additionally gets this in-page card. ── */}
      {isComplete && liveResult && (
        <div className="max-w-6xl mx-auto px-4 md:px-8 pt-4">
          <div className="rounded-lg border border-[var(--hairline)] bg-surface-2 px-4 py-3 flex items-center gap-2">
            <span className="text-content-high text-base">✓</span>
            <span className="text-sm font-medium text-content-high">
              Analysis complete
            </span>
            {events.length === 0 && (
              <span className="bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400 px-1.5 py-0.5 rounded text-[10px] font-medium">
                cached · ran &lt;30 min ago
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Page content ─────────────────────────────────────────────────────── */}
      <div className="max-w-6xl mx-auto p-4 md:p-8 space-y-2">

        {/* Phase C: recently-completed banner — shown when another run for the
            same ticker finished while the user wasn't watching this page and
            the report hasn't hydrated yet. Lets the user jump straight to the
            stored report without waiting for the retry backoff. */}
        {recentlyCompleted
          && recentlyCompleted.ticker.toUpperCase() === (liveTicker || ticker).toUpperCase()
          && !liveResult && (
          <div className="border-l-4 border-[var(--hairline)] bg-surface-2 p-3 rounded flex items-center justify-between gap-3">
            <p className="text-sm text-content-high">
              Your {liveTicker || ticker} analysis completed {formatTimeAgo(recentlyCompleted.completedAt)}.
            </p>
            <Button
              size="sm"
              onClick={() => navigate(`/report/${recentlyCompleted.runId}`)}
            >
              View report
            </Button>
          </div>
        )}

        {/* ── Summary: Header | StockPanel ─────────────────────────────────── */}
        <div id="summary" className="scroll-mt-28" />
        {/* Card QA Banner — Phase 10.5 self-healing audit. Renders above the
            header only when severity >= 'warning'. Reads from displayResult
            via the same `data` accessor as other panels below. */}
        <CardAuditBanner
          audit={(data as { card_qa_audit?: Record<string, import('@/lib/reportTypes').DdCardAudit> }).card_qa_audit?.[liveTicker]}
          ticker={liveTicker}
        />
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-4 items-stretch">
          <ReportHeader
            ticker={liveTicker}
            runAt={liveResult?.run_at ?? runStartedAt.current}
            modelName={liveResult?.model_name ?? model}
            decision={decision}
            regime={regime}
            currentPrice={currentPrice}
            sector={sector}
            subSector={subSector}
            vgpm={vgpm}
          />
          <StockPanel ticker={liveTicker} />
        </div>

        {/* ── Valuation ───────────────────────────────────────────────────── */}
        {/* PriceTargetPanel is the single valuation-summary card (hero target +   */}
        {/* probability-weighted 12m/DCF blend tables). It already contains every  */}
        {/* number ScenarioChart's bar chart re-plotted, so that separate card was  */}
        {/* dropped as pure duplication (2026-05). For REITs, REITValuationPanel   */}
        {/* is inserted after and replaces the generic DCF ladder with NAV hero,   */}
        {/* Method Breakdown, NPI/DPU history, Portfolio Composition, Cap-Rate     */}
        {/* Sensitivity. Non-REITs fall through to ValuationLadder. Gate is        */}
        {/* dcfRange.reit_breakdown, which the DCF agent only emits for            */}
        {/* RealEstate / REIT profiles.                                            */}
        <SectionAnchor
          id="valuation"
          label="Valuation"
          badge={sectionCompleted('valuation', phaseMap) ? <SectionCompleteBadge /> : null}
        />
        <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4 items-start">
          <div className="flex flex-col gap-4">
            {renderSection('price_target', 'Price Target', (
              <PriceTargetPanel
                dcfRange={dcfRange}
                scenario={scenarioAnalysis}
                decision={decision}
                ticker={liveTicker}
              />
            ))}
            {dcfRange?.reit_breakdown ? (
              renderSection('valuation', 'REIT Valuation', (
                <REITValuationPanel
                  dcfRange={dcfRange}
                  currentPrice={currentPrice}
                  ticker={liveTicker}
                />
              ))
            ) : dcfRange?.bank_breakdown ? (
              renderSection('valuation', 'Bank Valuation', (
                <BankValuationPanel
                  dcfRange={dcfRange}
                  currentPrice={currentPrice}
                  ticker={liveTicker}
                />
              ))
            ) : isBiopharmaSector(sector) ? (
              renderSection('valuation', 'Biopharma Valuation', (() => {
                const _fin = extractLatestFinancials(data.raw_financials as Record<string, unknown> | undefined);
                return (
                  <BiopharmaValuationPanel
                    dcfRange={dcfRange}
                    currentPrice={currentPrice}
                    ticker={liveTicker}
                    pipelineAssets={
                      ((data.pipeline_assets as Record<string, import('@/lib/reportTypes').BiopharmaPipelineAsset[]> | undefined)?.[liveTicker])
                      ?? ((data.pipeline_assets as Record<string, import('@/lib/reportTypes').BiopharmaPipelineAsset[]> | undefined)?.[liveTickerKey])
                    }
                    sections={data.deep_research_sections as Record<string, string> | undefined}
                    rd_spend={_fin.rd_spend}
                    revenue={_fin.revenue}
                    fcf={_fin.fcf}
                  />
                );
              })())
            /* Tech sub-type routing: Hyperscaler/Mature SaaS/Growth SaaS.        */
            /* Falls through to ValuationLadder when sub-type can't be resolved.  */
            /* classifyTechSubtype tries profile_name first, then a ticker table  */
            /* fallback (e.g. SNOW→growth_saas) so historical runs missing        */
            /* profile_name in stored data still render the correct panel.        */
            ) : (isTechSector(sector) && classifyTechSubtype(
                 (data.profile_names as Record<string, string> | undefined)?.[liveTicker]
                 ?? (data.profile_names as Record<string, string> | undefined)?.[liveTickerKey]
                 ?? (data.profile_name as string | undefined),
                 liveTicker
               ) !== null) ? (
              renderSection('valuation', 'Tech Valuation', (
                <TechValuationPanel
                  dcfRange={dcfRange}
                  currentPrice={currentPrice}
                  ticker={liveTicker}
                  profile={
                    (data.profile_names as Record<string, string> | undefined)?.[liveTicker]
                    ?? (data.profile_names as Record<string, string> | undefined)?.[liveTickerKey]
                    ?? (data.profile_name as string | undefined)
                  }
                  sections={data.deep_research_sections as Record<string, string> | undefined}
                  rawFinancials={data.raw_financials as Record<string, unknown> | undefined}
                  saasMetrics={
                    (data.saas_metrics as Record<string, import('@/lib/reportTypes').SaasMetrics> | undefined)?.[liveTicker]
                    ?? (data.saas_metrics as Record<string, import('@/lib/reportTypes').SaasMetrics> | undefined)?.[liveTickerKey]
                  }
                />
              ))
            ) : (
              renderSection('valuation', 'Valuation', (
                <ValuationLadder dcfRange={dcfRange} currentPrice={currentPrice} ticker={liveTicker} />
              ))
            )}
            {/* ── GS-style SOTP report card (task #28) ────────────────────────
                Present only when the DCF engine ran with SOTP (analyst)
                assumptions (dcf_range[ticker].sotp_breakdown): business-unit
                breakdown, NAV bridge, multiple basis, scenario TPs. Stacks
                below whichever valuation branch rendered above, mirroring
                V2ReportView; the DCF methodology panel follows. */}
            {dcfRange?.sotp_breakdown && (
              <SotpAnalystPanel breakdown={dcfRange.sotp_breakdown} />
            )}
            {/* Sits directly below the DCF ladder in the same column instead of
                as its own full-width strip — fills the column's remaining
                height instead of leaving the ladder's sparse-data cards
                (no bear/bull IV stored) looking like dead space above a gap. */}
            <DcfMethodologyPanel dcfRange={dcfRange} ticker={liveTicker} skipReason={dcfSkipReason} />
          </div>
          <div className="flex flex-col gap-2">
            {renderSection('power_law', 'Power Law', (
              <PowerLawRadar powerLaw={powerLaw} ticker={liveTicker} />
            ))}
            {renderSection('risk', 'Value Trap Audit', (
              <ValueTrapChecklist analysis={valueTrap} ticker={liveTicker} />
            ))}
            <NewsPanel ticker={liveTicker} />
            {/* Decision-inputs card (M2 D3) — replaces the investor persona
                panel retired with the committee. Shows the quantitative
                anchors + qualitative inputs the PM decided from. */}
            {renderSection('decision', 'Decision Inputs', (
              <DecisionInputsCard
                decisionInputs={decision?.decision_inputs}
                ticker={liveTicker}
                isRunning={isRunning}
              />
            ))}
          </div>
        </div>

        {/* ── Analysis ────────────────────────────────────────────────────── */}
        {/* Renders as soon as partial_data.industry_brief OR .deep_research */}
        {/* arrives via SSE (mid-run streaming). runId is only populated on */}
        {/* event: complete — previously gating here blocked mid-run display. */}
        <SectionAnchor
          id="analysis"
          label="Analysis"
          badge={sectionCompleted('analysis', phaseMap) ? <SectionCompleteBadge /> : null}
        />
        {(industryBrief || deepResearch) ? (
          <ResearchSummaryPanel
            runId={runId ?? ''}
            ticker={liveTicker}
            industryBrief={industryBrief}
            deepResearch={deepResearch}
            industryBriefContent={industryBrief
              ? <IndustryBriefPanel industryBrief={industryBrief} sector={sector} />
              : undefined}
            deepResearchContent={deepResearch
              ? <DeepResearchPanel
                  reportText={deepResearch}
                  annotatedText={deepAnnotated}
                  registry={citations}
                  ticker={liveTicker}
                />
              : undefined}
          />
        ) : (
          renderSection('analysis', 'Industry Intelligence Brief', <></>)
        )}
        {renderSection('intel', 'Intelligence Grid', (
          <IntelligenceGrid
            agentSignals={agentSignals}
            pipelineData={data as Record<string, unknown>}
            ticker={liveTicker}
          />
        ))}

        {/* ── Financials ──────────────────────────────────────────────────── */}
        <SectionAnchor
          id="financials"
          label="Financials"
          badge={sectionCompleted('financials', phaseMap) ? <SectionCompleteBadge /> : null}
        />
        {renderSection('financials', 'Financial Statements', (
          <FinancialsChart ticker={liveTicker} />
        ))}
        {renderSection('citation', 'Citation Registry', (
          <CitationPanel data={data as Record<string, unknown>} ticker={liveTicker} />
        ))}

        {/* Bottom padding */}
        <div className="h-16" />

      </div>

      {/* ── Collapsible progress log (bottom-right overlay) ──────────────────── */}
      <ProgressOverlay events={events} isRunning={isRunning} error={error} />

    </div>
  );
}

// ── Collapsible progress overlay ─────────────────────────────────────────────
function ProgressOverlay({
  events,
  isRunning,
  error,
}: {
  events: ProgressEvent[];
  isRunning: boolean;
  error: string | null;
}) {
  const [open, setOpen] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length, open]);

  // Deduplicate events — latest per phase
  const latestByPhase = new Map<string, ProgressEvent>();
  for (const ev of events) latestByPhase.set(ev.phase, ev);
  const deduped = Array.from(latestByPhase.values());

  if (deduped.length === 0 && !error) return null;

  return (
    <div className="fixed bottom-4 right-4 z-40 w-80 shadow-xl rounded-lg border border-border bg-background/95 backdrop-blur text-xs">

      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer select-none border-b border-border/50"
        onClick={() => setOpen(o => !o)}
      >
        <span className="font-semibold">
          {isRunning
            ? <span className="text-yellow-500 animate-pulse">Pipeline Running…</span>
            : error
            ? <span className="text-content-high">Pipeline Error</span>
            : <span className="text-content-high">Pipeline Complete</span>
          }
        </span>
        <span className="text-muted-foreground">{open ? '▼' : '▲'}</span>
      </div>

      {/* Log */}
      {open && (
        <ul className="max-h-64 overflow-y-auto p-2 space-y-0.5">
          {error && (
            <li className="p-2 bg-surface-2 border border-[var(--hairline)] rounded text-content-high text-[10px]">
              {error}
            </li>
          )}
          {deduped.map(ev => {
            const sl = ev.status.toLowerCase();
            const isDone =
              sl === 'done' ||
              ev.status.includes('| conviction') ||
              sl.includes('complete') ||
              sl.startsWith('quality score') ||
              sl.startsWith('✓') ||
              ev.status.startsWith('✓');
            const isErr = sl === 'error';

            // ── Milestone badges for specific phases ─────────────────────────
            // Each badge fires when the phase status contains a keyword,
            // confirming a key sub-task within that phase completed.
            const milestones: { label: string; hit: boolean }[] = [];
            if (ev.phase === 'edgar_hkex_resolver') {
              milestones.push({
                label: 'Annual Report',
                hit: sl.includes('annual report') || sl.includes('annual') || isDone,
              });
            }
            if (ev.phase === 'deep_research_agent') {
              milestones.push({
                label: 'DCF Calibration',
                hit: sl.includes('dcf calibration') || sl.includes('dcf') || isDone,
              });
            }

            return (
            <li key={ev.phase} className="flex items-start gap-1.5 px-1 py-0.5 rounded hover:bg-muted/30">
              <span className={`mt-0.5 font-bold w-3 shrink-0 ${
                isDone ? 'text-content-high' : isErr ? 'text-content-high' : 'text-yellow-400 animate-pulse'
              }`}>
                {isDone ? '✓' : isErr ? '✗' : '…'}
              </span>
              <span className="flex-1 min-w-0">
                <span className="font-medium">{phaseLabel(ev.phase)}</span>
                {' '}
                <span className="text-muted-foreground">{ev.summary || ev.status}</span>
                {/* Milestone keyword badges */}
                {milestones.length > 0 && (
                  <span className="flex flex-wrap gap-1 mt-0.5">
                    {milestones.map(m => (
                      <span
                        key={m.label}
                        className={`inline-flex items-center gap-0.5 text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${
                          m.hit
                            ? 'bg-surface-2 text-content-high'
                            : 'bg-muted/60 text-muted-foreground/50'
                        }`}
                      >
                        {m.hit ? '✓' : '○'} {m.label}
                      </span>
                    ))}
                  </span>
                )}
              </span>
            </li>
          ); })}
          {isRunning && deduped.length === 0 && (
            <li className="text-muted-foreground/60 px-1 py-0.5">Waiting for first update…</li>
          )}
          <div ref={bottomRef} />
        </ul>
      )}
    </div>
  );
}
