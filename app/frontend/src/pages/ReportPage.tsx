import { useState, useEffect, useRef, useCallback } from 'react';
import { useTheme } from '@/contexts/theme-context';
import { CheckCircle } from 'lucide-react';
import { toast, Toaster } from 'sonner';
import { ResearchNav } from '@/components/layout/ResearchNav';
import { getActiveTier, STARTER_ALLOWED_AGENTS } from '@/lib/tier';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getStockData, searchCompanies, type CompanySearchResult } from '@/lib/api';
import { PopularTickerTape } from '@/components/report/PopularTickerTape';
import { AgentOrbIcon }     from '@/components/report/AgentOrbIcon';
import { useActiveRun } from '@/contexts/active-run-context';
import { useIsMobile } from '@/hooks/use-mobile';
import { MobileReportView } from '@/components/mobile/MobileReportView';
// MobileBottomNav removed — hamburger menu in MobileTopBar replaces bottom tabs
import type { ProgressEvent } from '@/lib/reportTypes';

// ── Report section components ────────────────────────────────────────────────
import { ReportHeader }        from '@/components/report/ReportHeader';
import { ScenarioChart }       from '@/components/report/ScenarioChart';
import { PowerLawRadar }       from '@/components/report/PowerLawRadar';
import { ValueTrapChecklist }  from '@/components/report/ValueTrapChecklist';
import { AgentSignalsPanel }   from '@/components/report/AgentSignalsPanel';
import { IntelligenceGrid }    from '@/components/report/IntelligenceGrid';
import { FinancialsChart }     from '@/components/report/FinancialsChart';
import { ValuationLadder }     from '@/components/report/ValuationLadder';
import { DebatePanel }         from '@/components/report/DebatePanel';
import { CitationPanel }       from '@/components/report/CitationPanel';
import { StockPanel }          from '@/components/report/StockPanel';
import { PriceTargetPanel }    from '@/components/report/PriceTargetPanel';
import { NewsPanel }           from '@/components/report/NewsPanel';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { IndustryBriefPanel }  from '@/components/report/IndustryBriefPanel';
import { DeepResearchPanel }   from '@/components/report/DeepResearchPanel';
import { LiveSearchPanel }    from '@/components/report/LiveSearchPanel';
import { SectionSkeleton }     from '@/components/report/SectionSkeleton';

// ── Investor profiles ────────────────────────────────────────────────────────
const ALL_AGENTS = [
  'damodaran', 'graham', 'ackman', 'cathie_wood', 'munger',
  'burry', 'pabrai', 'lynch', 'fisher', 'jhunjhunwala',
  'druckenmiller', 'buffett',
];

const AGENT_LABELS: Record<string, string> = {
  damodaran:     'Damodaran',
  graham:        'Graham',
  ackman:        'Ackman',
  cathie_wood:   'Cathie Wood',
  munger:        'Munger',
  burry:         'Burry',
  pabrai:        'Pabrai',
  lynch:         'Lynch',
  fisher:        'Fisher',
  jhunjhunwala:  'Jhunjhunwala',
  druckenmiller: 'Druckenmiller',
  buffett:       'Buffett',
};

interface Profile { label: string; description: string; agents: string[]; }

const PROFILES: Profile[] = [
  { label: 'Full Committee',   description: 'All 12 investors — comprehensive analysis',          agents: ALL_AGENTS },
  { label: 'Deep Value',       description: 'Graham · Burry · Pabrai — margin of safety focus',   agents: ['graham', 'burry', 'pabrai'] },
  { label: 'Quality Growth',   description: 'Buffett · Munger · Fisher — wonderful businesses',    agents: ['buffett', 'munger', 'fisher'] },
  { label: 'Disruptive Growth',description: 'Cathie Wood · Lynch · Ackman — high-growth thesis',  agents: ['cathie_wood', 'lynch', 'ackman'] },
  { label: 'Macro Overlay',    description: 'Druckenmiller · Damodaran · Munger — top-down view', agents: ['druckenmiller', 'damodaran', 'munger'] },
  { label: 'Valuation Focus',  description: 'Damodaran · Graham · Buffett — DCF and earnings power', agents: ['damodaran', 'graham', 'buffett'] },
  { label: 'Custom',           description: 'Pick individual investors',                           agents: [] },
];

// ── Report sections — BLUF-first order ───────────────────────────────────────
const SECTIONS = [
  { id: 'summary',       label: 'Summary'    },
  { id: 'valuation',     label: 'Valuation'  },
  { id: 'analysis',      label: 'Analysis'   },
  { id: 'financials',    label: 'Financials' },
] as const;

type SectionId = (typeof SECTIONS)[number]['id'];

// ── Phase → section keyword mapping ─────────────────────────────────────────
const SECTION_PHASES: Record<SectionId, string[]> = {
  summary:    ['routing', 'vgpm'],
  valuation:  ['dcf', 'vgpm', 'portfolio', 'scenario', 'power_law', 'value_trap',
               'buffett', 'graham', 'munger', 'fisher', 'lynch', 'ackman',
               'cathie', 'burry', 'pabrai', 'druckenmiller', 'jhunjhunwala',
               'damodaran', 'investor', 'analyst'],
  analysis:   ['industry', 'deep_research', 'insider', 'news_sentiment',
               'earnings_quality', 'short_interest', 'analyst_revision',
               'intelligence', 'debate'],
  financials: ['routing', 'financial'],
};

function getEventsForSection(sectionId: SectionId, phaseMap: Record<string, ProgressEvent>): ProgressEvent[] {
  const keywords = SECTION_PHASES[sectionId] ?? [];
  return Object.entries(phaseMap)
    .filter(([phase]) => keywords.some(kw => phase.toLowerCase().includes(kw)))
    .map(([, ev]) => ev);
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
  investor_agents:          { running: 'Consulting the investor agents',       done: 'Investor signals received' },
  debate_round:             { running: 'Bulls and bears debating',             done: 'Debate concluded' },
  power_law_agent:          { running: 'Analysing power-law growth patterns',  done: 'Growth patterns analysed' },
  value_trap_agent:         { running: 'Checking for value traps',             done: 'Value trap check done' },
  phase7_complete:          { running: 'Wrapping up analytical models',        done: 'Models complete' },
  citation_auditor:         { running: 'Verifying sources and citations',      done: 'Sources verified' },
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

// ── Live research quips — bucketed by progress ───────────────────────────────
const QUIPS_BY_STAGE: Record<'early' | 'mid' | 'late' | 'final', string[]> = {
  // 0–30 %: chaos, frustration, just getting started
  early: [
    "The GPUs are on fire! Putting them out while the remaining works!",
    "My wife is asking me to take care of the kids.. Hang on a bit!",
    "We are working our equity analyst backend hard! Shhh\u2026",
    "Yawns. This is taking a while because of budget..",
    "Trust me we are working on it.",
    "Someone spilled coffee on the server rack. Cleaning up\u2026",
    "Convincing the interns to stop arguing and start analyzing\u2026",
    "Deep Research is commencing really. Sending my kids to the library nearby to get the books.",
  ],
  // 30–60 %: mid-grind, still struggling but making progress
  mid: [
    "Flipping our CFA textbook behind the scenes!",
    "Asking our boss on holiday for approval\u2026",
    "Half-way there \u2014 the analysts are starting to sweat.",
    "Running the numbers twice. Then a third time just to be safe.",
    "Our quant team disagrees with our fundamental team. Mediating\u2026",
    "Checking whether this is a value trap or a genuine bargain\u2026",
    "The spreadsheet has 47 tabs. We're on tab 23.",
    "Somewhere a DCF model is being built. Slowly.",
  ],
  // 70–90 %: closing in, cautious optimism
  late: [
    "Checking with our school professors on the formatting.",
    "Almost there \u2014 peer-reviewing the thesis one more time.",
    "Crossing the t's and dotting the i's on the valuation.",
    "Running final sanity checks so we don't embarrass ourselves.",
    "The debate panel is reaching a consensus\u2026 almost.",
    "Polishing the report so it looks like we knew what we were doing.",
    "Senior analyst is reviewing. Waiting for the red pen\u2026",
    "Just formatting the footnotes. Very important footnotes.",
  ],
  // 90–100 %: nearly done, excited energy
  final: [
    "Wrapping up! The finish line is in sight.",
    "Final touches \u2014 adding the cherry on top of the analysis.",
    "Done with the hard part. Now making it look pretty.",
    "Sending the draft to compliance\u2026 just kidding, we ship fast here.",
    "Almost ready to present to the investment committee!",
    "Last spell-check. We promise.",
    "Signing off the report. Thank you for your patience!",
  ],
};

function getStage(pct: number): 'early' | 'mid' | 'late' | 'final' {
  if (pct < 30)  return 'early';
  if (pct < 70)  return 'mid';
  if (pct < 90)  return 'late';
  return 'final';
}

function LiveResearchLabel({ pct, phaseMap }: { pct: number; phaseMap: Record<string, ProgressEvent> }) {
  const stage = getStage(pct);

  const [quipIdx,  setQuipIdx]  = useState(0);
  const [fadeIn,   setFadeIn]   = useState(true);
  const [dot,      setDot]      = useState(0);
  const prevStageRef = useRef<'early' | 'mid' | 'late' | 'final'>(stage);

  // Determine current active phase from phaseMap
  const phases = Object.values(phaseMap);
  // Filter out pipeline_queued to find the real active pipeline phase
  const realPhases = phases.filter(p => p.phase !== 'pipeline_queued');
  const activePhase = realPhases.length > 0
    ? realPhases.filter(p => p.status.toLowerCase() !== 'done').pop()  // latest non-done
      ?? realPhases[realPhases.length - 1]  // fallback: last completed phase
    : (phases.length > 0 ? phases[phases.length - 1] : null);  // fall back to pipeline_queued if nothing else
  const currentPhaseLabel = activePhase
    ? (activePhase.status.toLowerCase() === 'done'
        ? (PHASE_LABELS[activePhase.phase]?.done ?? phaseLabel(activePhase.phase))
        : (PHASE_LABELS[activePhase.phase]?.running ?? phaseLabel(activePhase.phase)))
    : 'Starting analysis...';

  // Helper: crossfade to a new quip index
  const crossfadeTo = useCallback((nextIdx: number) => {
    setFadeIn(false);
    const t = window.setTimeout(() => {
      setQuipIdx(nextIdx);
      setFadeIn(true);
    }, 300);
    return t;
  }, []);

  useEffect(() => {
    if (prevStageRef.current === stage) return;
    prevStageRef.current = stage;
    const t = crossfadeTo(0);
    return () => window.clearTimeout(t);
  }, [stage, crossfadeTo]);

  useEffect(() => {
    let fadeTimer: number;
    const interval = window.setInterval(() => {
      const pool = QUIPS_BY_STAGE[prevStageRef.current];
      fadeTimer = crossfadeTo((quipIdx + 1) % pool.length);
    }, 5000);
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(fadeTimer);
    };
  }, [quipIdx, crossfadeTo]);

  useEffect(() => {
    const id = window.setInterval(() => setDot(d => (d + 1) % 5), 350);
    return () => window.clearInterval(id);
  }, []);

  const pool = QUIPS_BY_STAGE[stage];
  const quipText = pool[quipIdx] ?? pool[0];

  return (
    <span className="flex items-center gap-1.5 min-w-0">
      {/* Animated five dots */}
      <span className="flex items-center gap-[3px] shrink-0">
        {[0,1,2,3,4].map(i => (
          <span
            key={i}
            className="inline-block w-1.5 h-1.5 rounded-full bg-primary transition-opacity duration-200"
            style={{ opacity: dot === i ? 1 : 0.25 }}
          />
        ))}
      </span>
      {/* Phase label (primary) + quip (secondary) */}
      <span className="flex flex-col min-w-0">
        <span className="text-xs font-semibold text-foreground truncate">
          {currentPhaseLabel}
        </span>
        <span
          className="text-[10px] text-muted-foreground/60 truncate transition-opacity duration-300"
          style={{ opacity: fadeIn ? 1 : 0 }}
        >
          {quipText}
        </span>
      </span>
    </span>
  );
}

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

// ── Main component ────────────────────────────────────────────────────────────
export function ReportPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { theme } = useTheme();
  const isDark = theme === 'dark' || (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const isMobile = useIsMobile();

  // ── Navigation flags from hamburger menu ────────────────────────────────────
  const locState = location.state as { fresh?: boolean; resume?: boolean } | null;
  const isFreshRequest = !!locState?.fresh;
  const isResumeRequest = !!locState?.resume;

  // ── Stream state — lifted into ActiveRunContext so it survives navigation ────
  // Read context first so we can initialise local state from it below.
  const {
    activeRun,
    streamState: state,
    streamEvents: events,
    phaseMap,
    liveData,
    streamTotalPhases,
    streamRunId: runId,
    streamError: error,
    liveResult,
    startStream: start,
    resetStream: reset,
    startPolling: poll,
    startRun: markRunStarted,
    clearActive: markRunCleared,
  } = useActiveRun();

  // ── Form state ───────────────────────────────────────────────────────────────
  const [ticker, setTicker]           = useState(activeRun?.ticker ?? '');
  const [model, setModel]             = useState('qwen3.6-plus');
  const [profileIdx, setProfileIdx]   = useState(1); // default: Deep Value (skip Full Committee)
  const [customAgents, setCustomAgents] = useState<string[]>([]);
  const [agentSearch, setAgentSearch] = useState('');
  const [showAdvanced, setShowAdvanced]   = useState(false);
  const [showArchetype, setShowArchetype] = useState(false);
  const [expandCustom, setExpandCustom]   = useState(false);
  const [suggestions, setSuggestions]     = useState<CompanySearchResult[]>([]);
  const [showSugg, setShowSugg]           = useState(false);
  const [suggLoading, setSuggLoading]     = useState(false);
  const [searchNoMatch, setSearchNoMatch] = useState(false);
  const searchDebounceRef                 = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchReqIdRef                    = useRef(0); // increments on every search; stale responses are ignored
  const searchBarRef                      = useRef<HTMLDivElement>(null);

  // ── Fresh ticker: clear everything and show landing page ────────────────────
  // Must run before liveMode init so state is clean on first render.
  if (isFreshRequest) {
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
    isFreshRequest ? false : (isResumeRequest ? state !== 'idle' : state !== 'idle')
  );
  const [livePrice, setLivePrice]         = useState<number | null>(null);
  const [activeSection, setActiveSection] = useState<string>('valuation');
  const runStartedAt                      = useRef<string>('');
  const observerRef = useRef<IntersectionObserver | null>(null);

  const isRunning  = state === 'running' || state === 'reconnecting';
  const isComplete = state === 'complete';
  const isError    = state === 'error';

  // ── React to navigation state changes (fresh / resume) ──────────────────────
  // Since navigate to same URL with replace doesn't remount, we watch location.state.
  useEffect(() => {
    const s = location.state as { fresh?: boolean; resume?: boolean; switchTicker?: string } | null;
    if (s?.fresh) {
      setLiveMode(false);
      setTicker('');
      window.history.replaceState({}, '');
    } else if (s?.switchTicker) {
      // User clicked a specific ongoing ticker in History — switch view to it.
      // Do NOT call start() — pipeline is already running on the server.
      // Poll for progress instead of triggering a duplicate run.
      const switchTo = s.switchTicker.toUpperCase();
      setTicker(switchTo);
      setLiveMode(true);
      setLiveResult(null);  // CRITICAL: clear stale result from previous ticker
      poll(switchTo);  // polls /analysis/status for progress, no new POST
      window.history.replaceState({}, '');
    } else if (s?.resume && state !== 'idle') {
      setLiveMode(true);
      window.history.replaceState({}, '');
    }
  }, [location.state]); // eslint-disable-line react-hooks/exhaustive-deps

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
  const selectedProfile = PROFILES[profileIdx];
  const isCustom        = selectedProfile.label === 'Custom';
  const activeAgents    = isCustom ? customAgents : selectedProfile.agents;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    const agentsToSend = activeAgents.length === ALL_AGENTS.length ? undefined : activeAgents;
    runStartedAt.current = new Date().toISOString();
    requestNotificationPermission();  // iOS requires user gesture for permission prompt
    start(t, model, agentsToSend);  // resetStream() is called inside startStream; clears liveResult too
    markRunStarted(t);
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
    const agentsToSend = activeAgents.length === ALL_AGENTS.length ? undefined : activeAgents;
    runStartedAt.current = new Date().toISOString();
    start(t, model, agentsToSend);
    markRunStarted(t);
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
    const agentsToSend = activeAgents.length === ALL_AGENTS.length ? undefined : activeAgents;
    runStartedAt.current = new Date().toISOString();
    start(t, model, agentsToSend);
    markRunStarted(t);
    setLiveMode(true);
    setLivePrice(null);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleCustomAgent = (agent: string) => {
    setCustomAgents(prev => prev.includes(agent) ? prev.filter(a => a !== agent) : [...prev, agent]);
  };

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

  // ── Section completion toasts — fire once per section when it becomes ready ──
  const toastedSections = useRef(new Set<SectionId>());
  useEffect(() => {
    if (!isRunning) return;
    SECTIONS.forEach(({ id, label }) => {
      if (sectionReady(id) && !toastedSections.current.has(id)) {
        toastedSections.current.add(id);
        toast.success(`Yay! ${label} completed`, {
          description: 'Ready to view below',
          duration: 10000,
          action: {
            label: 'Jump ↓',
            onClick: () => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
          },
        });
      }
    });
  }); // no deps — runs every render, but Set prevents duplicate toasts

  // ── Dismiss all toasts when analysis completes ───────────────────────────────
  useEffect(() => {
    if (isComplete) toast.dismiss();
  }, [isComplete]);

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
  const liveTicker    = (isRunning || state === 'reconnecting')
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

  const data          = { ...liveData, ...(liveResult?.data ?? {}) };
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
  const debateResult  = data.debate_result   as import('@/lib/reportTypes').DebateResult | undefined;
  const scenarioAnalysis = _byTicker(data.scenario_analysis as Record<string, import('@/lib/reportTypes').ScenarioAnalysis> | undefined);
  const powerLaw      = _byTicker(data.power_law_analysis  as Record<string, import('@/lib/reportTypes').PowerLawAnalysis>  | undefined);
  const valueTrap     = _byTicker(data.value_trap_analysis as Record<string, import('@/lib/reportTypes').ValueTrapAnalysis> | undefined);
  const dcfRange      = _byTicker(data.dcf_range           as Record<string, import('@/lib/reportTypes').DcfRange>          | undefined);
  const industryBrief = data.industry_brief       as string | undefined;
  const deepResearch  = (data.deep_research ?? data.deep_research_report)   as string | undefined;
  const deepAnnotated = data.deep_research_annotated as string | undefined;
  const citations     = data.citation_registry as import('@/lib/reportTypes').CitationRegistryEntry[] | undefined;
  // Prefer FMP live price (available immediately) over pipeline scenario price (available late)
  const currentPrice  = livePrice ?? scenarioAnalysis?.current_price;

  // Progress bar: phaseMap holds the LATEST status for every unique phase that
  // has fired at least one event. This naturally covers all pipeline phases.
  // A phase is "done" when its latest status is "Done" (case-insensitive).
  // The backend normalises "✓ <message>" statuses → "Done" so pre-pipeline phases
  // (macro_regime, strategic_router, dcf_engine, etc.) count here too.
  // phaseDone  = all phases whose latest status is "Done"
  // totalPhases = backend-provided count: investor agents + 18 fixed terminal phases
  // Group individual investor agents as ONE logical phase for progress calculation.
  // Without grouping, 12 investor agents + 18 fixed phases = 34 total, making
  // progress feel very slow (investor phase = 25% instead of ~60%).
  // With grouping: ~12 logical phases, matching the 10-phase pipeline.
  const _phaseEntries = Object.entries(phaseMap);
  const _investorPhases = _phaseEntries.filter(([k]) => k.startsWith('investor_'));
  const _nonInvestorPhases = _phaseEntries.filter(([k]) => !k.startsWith('investor_'));
  const _investorAllDone = _investorPhases.length > 0 && _investorPhases.every(([, e]) => e.status.toLowerCase() === 'done');
  const _nonInvestorDone = _nonInvestorPhases.filter(([, e]) => e.status.toLowerCase() === 'done').length;

  // Count investors as 1 grouped phase (done only if ALL investor agents done)
  const phaseDone = _nonInvestorDone + (_investorAllDone ? 1 : 0);
  const phaseSeen = _nonInvestorPhases.length + (_investorPhases.length > 0 ? 1 : 0);
  // Use grouped total: non-investor unique phases + 1 for investors (if any seen)
  const totalPhases = Math.max(
    _nonInvestorPhases.length + (_investorPhases.length > 0 ? 1 : 0),
    12  // minimum 12 logical phases
  );

  // Non-linear front-loaded curve: progress = 1 - (1 - ratio)^1.5
  const progressPct  =
    phaseSeen === 0
      ? (isRunning ? 1 : 0)
      : phaseDone === 0
        ? Math.min(5, phaseSeen)
        : Math.min(99, Math.round((1 - Math.pow(1 - phaseDone / totalPhases, 1.5)) * 100));
  // Keep doneCount alias so any other references still compile
  const doneCount = phaseDone;

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
      sendNotification(`${liveTicker} Research Complete`, 'Deep research finished. Consulting investor agents...');
    }

    // Milestone: investor agents started
    const investorStarted = phases.some(([k]) => k.startsWith('investor_'));
    if (investorStarted && !sent.has('investors')) {
      sent.add('investors');
      sendNotification(`${liveTicker} Investor Analysis`, '12 investor agents are analysing the stock...');
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

    // Final notification with decision
    const decision = liveResult?.data?.decisions?.[liveTicker]?.action;
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

  // ── Mobile layout: progressive report view matching history layout ───────────
  // When complete with liveResult, use MobileReportView directly.
  // When still running, render the same mobile layout with live-streaming data
  // so sections fill in progressively as the pipeline completes each phase.
  if (isMobile && liveMode) {
    if (liveResult) {
      return (
        <>
          <MobileReportView result={liveResult} runId={runId ?? ''} />
          {/* Navigation via hamburger menu in MobileTopBar */}
        </>
      );
    }

    // Build a partial RunResult from streaming data so MobileReportView can render progressively
    const partialResult: import('@/lib/reportTypes').RunResult = {
      run_id: runId ?? '',
      ticker: liveTicker || ticker,
      run_at: runStartedAt.current || new Date().toISOString(),
      model_name: model,
      decisions: decision ? { [liveTicker || ticker]: decision } : {},
      data: data,
      vgpm: vgpm ? { [liveTicker || ticker]: vgpm } : undefined,
    };

    return (
      <div className="min-h-screen bg-background pb-20">
        {/* Progress bar at top while running — sits below hamburger/profile row */}
        {isRunning && (
          <div className="sticky top-0 z-50 bg-background/95 backdrop-blur border-b border-border">
            {/* Spacer for hamburger + iOS safe area */}
            <div style={{ height: 'calc(env(safe-area-inset-top, 0px) + 48px)' }} />
            {/* Quip + percentage on same row */}
            <div className="flex items-center gap-2 px-4">
              <div className="flex-1 min-w-0">
                {state === 'reconnecting'
                  ? <span className="text-xs text-amber-400 animate-pulse">Pipeline running on server — waiting for result...</span>
                  : <LiveResearchLabel pct={progressPct} phaseMap={phaseMap} />
                }
              </div>
              <span className="text-sm font-bold tabular-nums text-primary shrink-0">{state === 'reconnecting' ? '...' : `${progressPct}%`}</span>
              <Button variant="ghost" size="sm" className="text-[10px] h-5 px-1.5 text-muted-foreground/60" onClick={handleReset}>
                Cancel
              </Button>
            </div>
            {/* Segmented progress bar — blue done + shimmer in-progress + grey upcoming */}
            <style>{`
              @keyframes mobile-shimmer {
                0%   { background-position:  200% 0; }
                100% { background-position: -200% 0; }
              }
              .mobile-shimmer-seg {
                background: linear-gradient(90deg,
                  rgba(59,130,246,0.25) 25%,
                  rgba(59,130,246,0.7)  50%,
                  rgba(59,130,246,0.25) 75%
                );
                background-size: 200% 100%;
                animation: mobile-shimmer 1.4s ease-in-out infinite;
              }
            `}</style>
            <div className="w-full h-3 overflow-hidden flex bg-gray-200 dark:bg-gray-700 mt-2">
              {/* Completed — solid blue */}
              <div
                className="h-full bg-blue-500 transition-all duration-500 flex-none"
                style={{ width: `${progressPct}%` }}
              />
              {/* In-progress — shimmer blue */}
              {progressPct < 100 && (
                <div
                  className="mobile-shimmer-seg h-full flex-none transition-all duration-500"
                  style={{ width: '25%' }}
                />
              )}
            </div>
          </div>
        )}
        {/* Live thinking / web search panel */}
        {isRunning && (
          <LiveSearchPanel
            streamEvents={events}
            liveData={liveData}
            thinking={(liveData.deep_research_thinking as string) || ''}
            isResearchPhase={
              Object.values(phaseMap).some(p =>
                (p.phase === 'deep_research_agent' || p.phase === 'deep_research') && !p.status.includes('✓')
              ) || events.some(e =>
                (e.phase === 'deep_research_agent' || e.phase === 'deep_research') && !e.status.includes('✓')
              )
            }
            isComplete={
              !!(phaseMap['deep_research_agent']?.status?.toLowerCase().match(/done|✓|complete/)) ||
              !!(phaseMap['deep_research']?.status?.toLowerCase().match(/done|✓|complete/))
            }
          />
        )}
        {isError && (
          <div className="sticky top-0 z-50 bg-red-500/10 border-b border-red-500/30 px-4 py-2 flex items-center gap-2">
            <span className="text-red-500 text-sm">{error ?? 'Pipeline error'}</span>
            <Button variant="outline" size="sm" className="text-xs h-6 ml-auto" onClick={handleReset}>Retry</Button>
          </div>
        )}
        {/* Render MobileReportView with partial streaming data */}
        <MobileReportView result={partialResult} runId={runId ?? ''} />
      </div>
    );
  }

  // ── Form view ────────────────────────────────────────────────────────────────
  if (!liveMode) {
    // ── Tier enforcement ────────────────────────────────────────────────────
    const activeTier = getActiveTier();
    const isStarterTier = activeTier === 'starter';
    // A profile is locked for Starter if any of its agents are outside the allowed set
    const isProfileLocked = (agents: string[]) =>
      isStarterTier && agents.length > 0 &&
      agents.some(a => !(STARTER_ALLOWED_AGENTS as readonly string[]).includes(a));
    // An agent is locked for Starter if it's not in the allowed set
    const isAgentLocked = (agent: string) =>
      isStarterTier && !(STARTER_ALLOWED_AGENTS as readonly string[]).includes(agent);

    const filteredAgents = ALL_AGENTS.filter(a =>
      AGENT_LABELS[a].toLowerCase().includes(agentSearch.toLowerCase())
    );

    // Selected archetype label for the icon tooltip
    const archetypeLabel  = PROFILES[profileIdx].label;
    const hasCustomAgents = isCustom && customAgents.length > 0;
    const archetypeReady  = !isCustom || hasCustomAgents;
    // profileIdx starts at 1 (Deep Value default) — treat as "chosen" once user has opened the panel
    // or when it's non-default. We always allow submit since Deep Value is a sensible default.
    const canSubmit       = !!ticker.trim() && archetypeReady;
    const { user, logout } = useAuth();

    return (
      <div
        className="min-h-screen flex flex-col"
        style={{
          backgroundImage: 'url(/bg-wallpaper.jpg)',
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      >
        {/* Dark overlay for legibility */}
        <div className="absolute inset-0 bg-black/40 pointer-events-none" />

        <div className="relative z-10 flex flex-col min-h-screen">

        {/* Profile icon removed — user profile moved to hamburger menu */}
        {!liveMode && false && (
        <div className="flex items-center justify-end px-5 pt-4 pb-2">
          {user ? (
            <div className="relative group">
              {user.avatar_url ? (
                <img
                  src={user.avatar_url}
                  alt={user.name ?? user.email}
                  className="w-10 h-10 rounded-full object-cover ring-2 ring-white/40 cursor-pointer hover:ring-white/70 transition-all shadow-lg"
                />
              ) : (
                <div className="w-10 h-10 rounded-full bg-white/90 flex items-center justify-center text-sm font-bold text-gray-700 cursor-pointer ring-2 ring-white/40 hover:ring-white/70 transition-all shadow-lg">
                  {(user.name ?? user.email)[0].toUpperCase()}
                </div>
              )}
              {/* Dropdown on hover */}
              <div className="absolute right-0 top-12 w-48 bg-white/95 backdrop-blur-md rounded-2xl shadow-xl border border-white/20 py-2 opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-all duration-200 z-50">
                <div className="px-4 py-2 border-b border-gray-100">
                  <p className="text-sm font-semibold text-gray-800 truncate">{user.name ?? ''}</p>
                  <p className="text-xs text-gray-400 truncate">{user.email}</p>
                </div>
                <button
                  onClick={logout}
                  className="w-full text-left px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors"
                >
                  Sign out
                </button>
              </div>
            </div>
          ) : (
            <a
              href="#/login"
              className="w-10 h-10 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center ring-2 ring-white/30 hover:bg-white/30 hover:ring-white/50 transition-all shadow-lg"
              title="Sign in"
            >
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
              </svg>
            </a>
          )}
        </div>
        )}

        <div className="flex-1 flex flex-col items-center justify-center px-4 py-12">
        <div className="w-full max-w-2xl flex flex-col items-center gap-6">

          {/* Hero heading */}
          <div className="text-center mb-2">
            {user && (
              <p
                className="text-2xl font-bold tracking-tight text-white/70 drop-shadow-lg mb-0.5"
                style={{ fontFamily: "'Segoe UI', 'Google Sans', 'Nunito', sans-serif", fontWeight: 700 }}
              >
                Hello, {user.name ?? user.email}!
              </p>
            )}
            <h1
              className="text-2xl font-bold tracking-tight text-white drop-shadow-lg"
              style={{ fontFamily: "'Segoe UI', 'Google Sans', 'Nunito', sans-serif", fontWeight: 700 }}
            >
              What ticker are we analysing?
            </h1>
          </div>

          <form onSubmit={handleSubmit} className="w-full flex flex-col items-center gap-4">

            {/* ── Search pill ── */}
            <div className="w-full relative" ref={searchBarRef}>
              <div className={`flex items-center w-full h-16 rounded-full px-5 gap-3 border-2 transition-all duration-200 ${
                showSugg
                  ? 'border-white/60 bg-white/95 shadow-2xl rounded-b-none border-b-transparent'
                  : 'border-white/40 bg-white/85 hover:border-white/60 hover:bg-white/90 focus-within:border-white/70 focus-within:bg-white/95 focus-within:shadow-xl'
              }`}>

                {/* Search input */}
                <input
                  placeholder="Search ticker or company name…"
                  value={ticker}
                  onChange={(e) => {
                    const raw = e.target.value;
                    setTicker(raw);
                    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
                    if (raw.trim().length >= 2) {
                      setSuggLoading(true);
                      // Increment request ID — any response with a lower ID is stale and discarded
                      const reqId = ++searchReqIdRef.current;
                      searchDebounceRef.current = setTimeout(() => {
                        searchCompanies(raw.trim())
                          .then(data => {
                            if (reqId !== searchReqIdRef.current) return; // stale — ignore
                            setSuggestions(data);
                            setShowSugg(data.length > 0);
                            setSearchNoMatch(data.length === 0 && raw.trim().length >= 2);
                            setSuggLoading(false);
                          })
                          .catch(() => {
                            if (reqId !== searchReqIdRef.current) return;
                            setSuggLoading(false);
                          });
                      }, 280);
                    } else {
                      searchReqIdRef.current++;   // invalidate any in-flight request
                      setSuggestions([]);
                      setShowSugg(false);
                      setSuggLoading(false);
                      setSearchNoMatch(false);
                    }
                  }}
                  onFocus={() => { if (suggestions.length > 0) setShowSugg(true); }}
                  onBlur={() => setTimeout(() => setShowSugg(false), 150)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') { setShowSugg(false); setSuggestions([]); setShowArchetype(false); }
                  }}
                  className="flex-1 bg-transparent outline-none text-base font-normal text-left text-gray-800 placeholder:text-gray-400 placeholder:font-normal placeholder:text-base placeholder:tracking-normal"
                  style={{ fontFamily: "'Google Sans', 'Segoe UI', Arial, sans-serif" }}
                  maxLength={60}
                  autoFocus
                />

                {/* Clear × */}
                {ticker && (
                  <button type="button" tabIndex={-1}
                    onClick={() => { setTicker(''); setSuggestions([]); setShowSugg(false); }}
                    className="text-muted-foreground/40 hover:text-muted-foreground transition-colors shrink-0 text-xl leading-none"
                  >×</button>
                )}

                {/* Spinner */}
                {suggLoading && (
                  <div className="w-4 h-4 border-2 border-border border-t-primary rounded-full animate-spin shrink-0" />
                )}

                {/* Divider */}
                <div className="w-px h-6 bg-border/50 shrink-0" />

                {/* Agent orb icon — rightmost element, opens archetype panel */}
                <button
                  type="button"
                  title={`Investor archetype: ${archetypeLabel}`}
                  onClick={() => { setShowArchetype(v => !v); setShowSugg(false); }}
                  className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 transition-all duration-200 ${
                    showArchetype
                      ? 'bg-foreground text-background shadow-md'
                      : archetypeReady
                      ? 'bg-muted/60 text-foreground/30 hover:bg-muted hover:text-foreground/60'
                      : 'bg-amber-500/10 text-amber-500 hover:bg-amber-500/20 animate-pulse'
                  }`}
                >
                  <AgentOrbIcon size={22} />
                </button>
              </div>

              {/* ── Autocomplete suggestions ── */}
              {showSugg && suggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 bg-white border-2 border-t-0 border-white/60 rounded-b-3xl shadow-xl z-40 overflow-hidden">
                  {suggestions.map((s, i) => (
                    <button
                      key={`${s.ticker}-${i}`}
                      type="button"
                      onMouseDown={() => {
                        setTicker(s.ticker);
                        setSuggestions([]);
                        setShowSugg(false);
                        setSearchNoMatch(false);
                      }}
                      className="w-full flex items-center gap-3 px-6 py-3 hover:bg-muted/50 transition-colors text-left"
                    >
                      <svg className="w-4 h-4 text-muted-foreground/30 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                      </svg>
                      <span className="font-mono font-bold text-sm w-16 shrink-0">{s.ticker}</span>
                      <span className="text-sm text-muted-foreground flex-1 truncate">{s.name}</span>
                      {s.exchange && <span className="text-[10px] text-muted-foreground/40 font-mono shrink-0">{s.exchange}</span>}
                    </button>
                  ))}
                  <div className="px-6 py-2 border-t border-border/20">
                    <p className="text-[10px] text-muted-foreground/40">
                      <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">↵</kbd> to run · click to select
                    </p>
                  </div>
                </div>
              )}

              {/* ── No-match error ── */}
              {searchNoMatch && !showSugg && (
                <p className="mt-2 text-xs text-red-500 text-center">No matching ticker or company found.</p>
              )}

              {/* ── Archetype panel — floats below pill, full width ── */}
              {showArchetype && (
                <div className="absolute top-[calc(100%+6px)] left-0 right-0 bg-background border border-border rounded-2xl shadow-xl z-30 overflow-hidden">

                  {/* Header */}
                  <div className="px-5 py-3 border-b border-border/50 flex items-center justify-between">
                    <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Investor Archetype</span>
                    {archetypeLabel !== 'Custom' ? (
                      <span className="text-xs text-primary font-medium">{archetypeLabel}</span>
                    ) : hasCustomAgents ? (
                      <span className="text-xs text-primary font-medium">{customAgents.length} investors selected</span>
                    ) : (
                      <span className="text-xs text-amber-500/80">Select investors below</span>
                    )}
                  </div>

                  {/* Archetype rows */}
                  {PROFILES.filter((_, i) => i !== 0).map((p, displayIdx) => {
                    const realIdx = displayIdx + 1;
                    const selected = profileIdx === realIdx;
                    const isThisCustom = p.label === 'Custom';
                    const locked = isProfileLocked(p.agents);
                    const chips = p.agents.slice(0, 3).map(a => AGENT_LABELS[a]);

                    return (
                      <div key={p.label}>
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() => {
                            if (locked) return;
                            setProfileIdx(realIdx);
                            if (isThisCustom) {
                              setExpandCustom(v => !v);
                            } else {
                              setExpandCustom(false);
                              setShowArchetype(false);
                            }
                          }}
                          className={`w-full flex items-center gap-4 px-5 py-3.5 transition-colors text-left ${
                            locked
                              ? 'opacity-40 cursor-not-allowed'
                              : selected ? 'bg-primary/8' : 'hover:bg-muted/40'
                          }`}
                        >
                          {/* Selection dot or lock */}
                          {locked ? (
                            <svg className="w-3 h-3 text-muted-foreground/50 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                            </svg>
                          ) : (
                            <span className={`w-2 h-2 rounded-full shrink-0 transition-colors ${selected ? 'bg-primary' : 'bg-border'}`} />
                          )}

                          {/* Label + chips */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className={`font-semibold text-sm ${locked ? 'text-muted-foreground' : selected ? 'text-primary' : 'text-foreground'}`}>
                                {p.label}
                              </span>
                              {locked && (
                                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 leading-none">
                                  PRO
                                </span>
                              )}
                            </div>
                            {chips.length > 0 && (
                              <div className="flex flex-wrap gap-1 mt-1">
                                {chips.map(name => (
                                  <span key={name} className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                                    locked ? 'bg-muted/50 text-muted-foreground/50' :
                                    selected ? 'bg-primary/15 text-primary/80' : 'bg-muted text-muted-foreground'
                                  }`}>{name}</span>
                                ))}
                              </div>
                            )}
                          </div>

                          {/* Custom: chevron */}
                          {isThisCustom && (
                            <svg className={`w-4 h-4 text-muted-foreground/50 shrink-0 transition-transform ${expandCustom ? 'rotate-180' : ''}`}
                              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                            </svg>
                          )}
                        </button>

                        {/* Custom sub-dropdown */}
                        {isThisCustom && expandCustom && (
                          <div className="border-t border-border/30 bg-muted/20 px-4 py-3">
                            {/* Search */}
                            <div className="flex items-center gap-2 bg-background border border-border rounded-lg px-3 py-2 mb-2">
                              <svg className="w-3.5 h-3.5 text-muted-foreground/50 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                              </svg>
                              <input
                                type="text"
                                placeholder="Search investors…"
                                value={agentSearch}
                                onChange={e => setAgentSearch(e.target.value)}
                                className="bg-transparent flex-1 outline-none text-sm placeholder:text-muted-foreground/40"
                              />
                              {customAgents.length > 0 && (
                                <span className="text-xs font-semibold text-primary shrink-0">{customAgents.length}</span>
                              )}
                            </div>

                            {/* Investor list */}
                            <div className="max-h-44 overflow-y-auto space-y-0.5">
                              {filteredAgents.map(agent => {
                                const agentLocked = isAgentLocked(agent);
                                const checked = customAgents.includes(agent);
                                return (
                                  <button
                                    key={agent}
                                    type="button"
                                    disabled={agentLocked}
                                    onClick={() => { if (!agentLocked) toggleCustomAgent(agent); }}
                                    className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-sm transition-colors text-left ${
                                      agentLocked
                                        ? 'opacity-40 cursor-not-allowed text-muted-foreground'
                                        : checked ? 'bg-primary/10 text-primary' : 'hover:bg-background text-foreground'
                                    }`}
                                  >
                                    {agentLocked ? (
                                      <svg className="w-3.5 h-3.5 text-muted-foreground/50 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                                      </svg>
                                    ) : (
                                      <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 ${
                                        checked ? 'bg-primary border-primary' : 'border-border'
                                      }`}>
                                        {checked && (
                                          <svg className="w-2 h-2 text-primary-foreground" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M2 6l3 3 5-5" />
                                          </svg>
                                        )}
                                      </span>
                                    )}
                                    <span className="flex-1">{AGENT_LABELS[agent]}</span>
                                    {agentLocked && (
                                      <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400">
                                        PRO
                                      </span>
                                    )}
                                  </button>
                                );
                              })}
                            </div>

                            {/* Selected chips */}
                            {customAgents.length > 0 && (
                              <div className="mt-2 pt-2 border-t border-border/30 flex flex-wrap gap-1 items-center">
                                {customAgents.map(a => (
                                  <button key={a} type="button" onClick={() => toggleCustomAgent(a)}
                                    className="inline-flex items-center gap-0.5 text-[11px] bg-primary/10 text-primary px-2 py-0.5 rounded-full border border-primary/20 hover:bg-red-500/10 hover:text-red-400 hover:border-red-400/20 transition-colors">
                                    {AGENT_LABELS[a]}<span className="opacity-50 ml-0.5">×</span>
                                  </button>
                                ))}
                                <button type="button" onClick={() => setCustomAgents([])}
                                  className="text-[11px] text-muted-foreground/50 hover:text-muted-foreground ml-1">Clear</button>
                              </div>
                            )}

                            {customAgents.length === 0 && (
                              <p className="text-xs text-amber-500/70 mt-1.5">Select at least one investor.</p>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}

                  {/* Starter tier upgrade nudge */}
                  {isStarterTier && (
                    <div className="px-5 py-2.5 border-t border-border/30 bg-muted/20 flex items-center justify-between">
                      <p className="text-[11px] text-muted-foreground/60">
                        5 agents locked — deep value analysts included in Starter
                      </p>
                      <a href="#/pricing" className="text-[11px] font-semibold text-amber-600 dark:text-amber-400 underline underline-offset-2 hover:opacity-80">
                        Upgrade →
                      </a>
                    </div>
                  )}

                </div>
              )}
            </div>

            {/* Navigation pill bubbles */}
            <div className="flex items-center gap-3 flex-wrap justify-center mt-1">
              {[
                { label: 'Stock Screener', href: '#/screener' },
                { label: 'Watchlist',      href: '#/watchlist' },
                { label: 'History',        href: '#/history' },
              ].map(({ label, href }) => (
                <a
                  key={label}
                  href={href}
                  className="px-4 py-2 rounded-full text-sm font-medium text-white border border-white/40 bg-white/15 backdrop-blur-sm hover:bg-white/25 hover:border-white/60 transition-all duration-200 shadow-sm"
                >
                  {label}
                </a>
              ))}
            </div>

            {/* Archetype prompt — shown when ticker is filled but Custom has no agents */}
            {ticker.trim() && !archetypeReady && (
              <p className="text-xs text-amber-200/90 text-center -mt-1">
                Click the <span className="font-semibold">investor icon</span> to choose your archetype before running.
              </p>
            )}

            {/* Hidden submit — disabled when not ready */}
            <button type="submit" disabled={!canSubmit} className="sr-only" aria-hidden="true" tabIndex={-1} />

          </form>

          {/* Popular ticker tape */}
          <div className="w-full mt-2">
            <PopularTickerTape onSelect={(t) => { setTicker(t); setSuggestions([]); setShowSugg(false); }} />
          </div>

        </div>
        </div>
        </div>
      </div>
    );
  }

  // ── Live report view ─────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-background">
      <Toaster position="top-right" richColors closeButton expand visibleToasts={6} />
      <ResearchNav />

      {/* ── Top running bar ─────────────────────────────────────────────────── */}
      <div className="sticky top-[45px] z-30 bg-background/98 backdrop-blur border-b">
        <div className="max-w-6xl mx-auto px-4 md:px-8 py-5 flex items-center gap-5">

          {/* Spinner / done indicator */}
          {isRunning ? (
            <div className="w-8 h-8 rounded-full border-[3px] border-primary/30 border-t-primary animate-spin shrink-0" />
          ) : isComplete ? (
            <span className="text-green-500 text-2xl shrink-0">✓</span>
          ) : isError ? (
            <span className="text-red-500 text-2xl shrink-0">✗</span>
          ) : null}

          {/* Ticker + status */}
          <span className="font-mono font-bold text-xl shrink-0">{ticker}</span>
          {isRunning && (
            <div className="flex-1 flex flex-col gap-2.5 mx-2 min-w-0 hidden sm:flex">
              <div className="flex items-center justify-between gap-4">
                <LiveResearchLabel pct={progressPct} phaseMap={phaseMap} />
                <span className="text-base font-bold tabular-nums text-primary shrink-0">
                  {progressPct}%
                </span>
              </div>
              {/* Progress bar — blue (done) + blue shimmer (in-progress) + grey (upcoming) */}
              <style>{`
                @keyframes progress-shimmer {
                  0%   { background-position:  200% 0; }
                  100% { background-position: -200% 0; }
                }
                .progress-shimmer-seg {
                  background: linear-gradient(90deg,
                    rgba(59,130,246,0.25) 25%,
                    rgba(59,130,246,0.7)  50%,
                    rgba(59,130,246,0.25) 75%
                  );
                  background-size: 200% 100%;
                  animation: progress-shimmer 1.4s ease-in-out infinite;
                }
              `}</style>
              <div className="w-full h-3 rounded-full overflow-hidden flex bg-gray-200 dark:bg-gray-700">
                {/* Completed — blue */}
                <div
                  className="h-full bg-blue-500 transition-all duration-500 flex-none"
                  style={{ width: `${progressPct}%` }}
                />
                {/* In-progress phase — one segment wide, blue shimmer */}
                {progressPct < 100 && (
                  <div
                    className="progress-shimmer-seg h-full flex-none transition-all duration-500"
                    style={{ width: `${(1 / SECTIONS.length) * 100}%` }}
                  />
                )}
              </div>
            </div>
          )}
          {isComplete && liveResult && (
            <div className="flex-1 flex flex-col gap-1.5 mx-2 min-w-0 hidden sm:flex">
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-green-600 dark:text-green-400 font-medium">Analysis complete</span>
                {events.length === 0 && (
                  <span className="bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400 px-1.5 py-0.5 rounded text-[10px] font-medium">
                    cached · ran &lt;30 min ago
                  </span>
                )}
              </div>
              <div className="w-full h-3 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-700">
                <div className="h-full w-full bg-green-500 transition-all duration-500" />
              </div>
            </div>
          )}
          {isComplete && !liveResult && (
            <span className="text-xs text-muted-foreground animate-pulse">Loading report…</span>
          )}
          {isError && (
            <span className="text-xs text-red-500">{error ?? 'Pipeline error'}</span>
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

      {/* ── Page content ─────────────────────────────────────────────────────── */}
      <div className="max-w-6xl mx-auto p-4 md:p-8 space-y-2">

        {/* ── Summary: Header | StockPanel ─────────────────────────────────── */}
        <div id="summary" className="scroll-mt-28" />
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4 items-stretch">
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
        <SectionAnchor id="valuation" label="Valuation" />
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4 items-start">
          <div className="flex flex-col gap-4">
            {renderSection('valuation', 'Valuation', (
              <ValuationLadder dcfRange={dcfRange} currentPrice={currentPrice} ticker={liveTicker} />
            ))}
            {renderSection('price_target', 'Price Target', (
              <PriceTargetPanel
                dcfRange={dcfRange}
                scenario={scenarioAnalysis}
                decision={decision}
                ticker={liveTicker}
              />
            ))}
            {renderSection('scenario', 'Scenario Analysis', (
              <ScenarioChart scenario={scenarioAnalysis} ticker={liveTicker} />
            ))}
          </div>
          <div className="flex flex-col gap-2">
            {renderSection('power_law', 'Power Law', (
              <PowerLawRadar powerLaw={powerLaw} ticker={liveTicker} />
            ))}
            {renderSection('risk', 'Value Trap Audit', (
              <ValueTrapChecklist analysis={valueTrap} ticker={liveTicker} />
            ))}
            <NewsPanel ticker={liveTicker} />
          </div>
        </div>

        {renderSection('agents', 'Agent Signals', (
          <AgentSignalsPanel agentSignals={agentSignals} ticker={liveTicker} />
        ))}

        {/* ── Analysis ────────────────────────────────────────────────────── */}
        <SectionAnchor id="analysis" label="Analysis" />
        {(industryBrief || deepResearch) && runId ? (
          <ResearchSummaryPanel
            runId={runId}
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
        {renderSection('debate', 'Agent Debate', (
          <DebatePanel debateResult={debateResult} ticker={liveTicker} />
        ))}

        {/* ── Financials ──────────────────────────────────────────────────── */}
        <SectionAnchor id="financials" label="Financials" />
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
            ? <span className="text-red-500">Pipeline Error</span>
            : <span className="text-green-500">Pipeline Complete</span>
          }
        </span>
        <span className="text-muted-foreground">{open ? '▼' : '▲'}</span>
      </div>

      {/* Log */}
      {open && (
        <ul className="max-h-64 overflow-y-auto p-2 space-y-0.5">
          {error && (
            <li className="p-2 bg-red-100/10 border border-red-500/30 rounded text-red-400 text-[10px]">
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
                isDone ? 'text-green-500' : isErr ? 'text-red-500' : 'text-yellow-400 animate-pulse'
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
                        className={`inline-flex items-center gap-0.5 text-[9px] font-semibold px-1.5 py-0.5 rounded-full ${
                          m.hit
                            ? 'bg-green-500/15 text-green-500'
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
