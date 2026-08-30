/**
 * Phase vocabulary for the live run — shared by the desktop and mobile paths.
 *
 * Moved out of pages/ReportPage.tsx so ChainOfThought, ProgressHeader and both
 * render paths agree on what a phase is called. `ReportViewPage` mobile bypasses
 * the desktop JSX entirely, so anything phase-related that lives in one file
 * silently diverges on the other.
 *
 * The keys are the literal `agent_id` values the pipeline passes to
 * `progress.update_status()`. They were verified against the emitting modules
 * rather than assumed — several were missing from the original map and had been
 * falling through to the humanised fallback:
 *
 *   edgar_hkex_resolver · sotp_extractor · scenario_agent · post_trade_review
 *   archive_cache · sector_card
 *
 * and, most visibly, the final decision phase emits as
 * `advanced_portfolio_manager` (src/agents/portfolio_manager.py) while the map
 * only carried `portfolio_manager` — so the last step of every run rendered as
 * "Advanced Portfolio Manager" instead of "Generating the investment decision".
 * Both keys are kept.
 */

export interface PhaseCopy {
  running: string;
  done: string;
}

export const PHASE_LABELS: Record<string, PhaseCopy> = {
  pipeline_queued:            { running: 'Analysis in progress on server',       done: 'Analysis resumed' },
  archive_cache:              { running: 'Checking the research archive',        done: 'Archive checked' },
  macro_regime_classifier:    { running: 'Reading the macro environment',        done: 'Macro environment assessed' },
  strategic_router:           { running: 'Identifying the sector playbook',      done: 'Sector playbook identified' },
  intelligence_agents:        { running: 'Scanning market intelligence signals', done: 'Intelligence signals gathered' },
  edgar_hkex_resolver:        { running: 'Resolving regulatory filings',         done: 'Filings resolved' },
  deep_research_agent:        { running: 'Generating deep research report',      done: 'Deep research complete' },
  deep_research:              { running: 'Generating deep research report',      done: 'Deep research complete' },
  industry_specialist:        { running: 'Consulting the industry specialist',   done: 'Industry brief ready' },
  data_router:                { running: 'Fetching financial data',              done: 'Financial data ready' },
  dcf_engine:                 { running: 'Computing the valuation model',        done: 'Valuation model complete' },
  sotp_extractor:             { running: 'Breaking out business segments',       done: 'Segments broken out' },
  scenario_agent:             { running: 'Building bull / base / bear cases',    done: 'Scenarios built' },
  sector_card:                { running: 'Scoring against sector peers',         done: 'Peer scoring complete' },
  power_law_agent:            { running: 'Analysing power-law growth patterns',  done: 'Growth patterns analysed' },
  value_trap_agent:           { running: 'Checking for value traps',             done: 'Value trap check done' },
  phase7_complete:            { running: 'Wrapping up analytical models',        done: 'Models complete' },
  advanced_risk_manager:      { running: 'Running final risk checks',            done: 'Risk assessment complete' },
  portfolio_manager:          { running: 'Generating the investment decision',   done: 'Decision ready' },
  advanced_portfolio_manager: { running: 'Generating the investment decision',   done: 'Decision ready' },
  post_trade_review:          { running: 'Filing the post-trade review',         done: 'Post-trade review filed' },
};

/**
 * Execution order, taken from src/pipeline.py. Used to render phases that have
 * not emitted yet as pending rows, so the timeline shows the shape of the whole
 * run rather than only what has happened so far.
 *
 * Phases 2-6 run concurrently in the pipeline's "front block"; they are listed
 * in dispatch order since the UI shows them as a list, not a dependency graph.
 */
export const PHASE_ORDER: string[] = [
  'archive_cache',
  'macro_regime_classifier',
  'strategic_router',
  'intelligence_agents',
  'edgar_hkex_resolver',
  'deep_research',
  'industry_specialist',
  'data_router',
  'dcf_engine',
  'sotp_extractor',
  'scenario_agent',
  'sector_card',
  'power_law_agent',
  'value_trap_agent',
  'phase7_complete',
  'advanced_risk_manager',
  'advanced_portfolio_manager',
];

/** `deep_research_agent` and `deep_research` are the same step to a reader. */
const PHASE_ALIASES: Record<string, string> = {
  deep_research_agent: 'deep_research',
  portfolio_manager: 'advanced_portfolio_manager',
};

export function canonicalPhase(phase: string): string {
  return PHASE_ALIASES[phase] ?? phase;
}

/** Humanise an unknown phase key: `power_law_agent` -> `Power Law`. */
export function humanisePhase(phase: string): string {
  return phase
    .replace(/_agent$/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Completed-state label (the original `phaseLabel` behaviour). */
export function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase]?.done ?? humanisePhase(phase);
}

/** Label for a phase given whether it is still running. */
export function phaseCopy(phase: string, running: boolean): string {
  const mapped = PHASE_LABELS[phase] ?? PHASE_LABELS[canonicalPhase(phase)];
  if (!mapped) return humanisePhase(phase);
  return running ? mapped.running : mapped.done;
}

/* ── Market-aware phase labels ───────────────────────────────────────────────
   Some phases serve different venues depending on the ticker's market, so a
   fixed label is wrong for most runs. `edgar_hkex_resolver` is the clear case:
   one phase that hits SEC EDGAR for US/ADR tickers and HKEXnews for HK ones,
   with SGX and further venues to come. Naming it after its implementation
   ("edgar_hkex_resolver") leaks internals and is actively misleading on a
   Singapore ticker.

   Rather than branch on the ticker — which the UI would then have to keep in
   sync with backend routing — the label is derived from the phase's OWN status
   text, which already names the registry it actually reached:

       "Resolving SEC EDGAR annual filing..."
       "Resolving HKEXnews Annual Report (年報)..."
       "HKEX OK: {filing_type} | ..."

   So when a new venue is wired up and emits its own status, the label follows
   with no frontend change. */
const VENUE_PATTERNS: { test: RegExp; venue: string }[] = [
  { test: /hkex|年報/i, venue: 'HKEX' },
  { test: /edgar|\bsec\b|10-K|20-F/i, venue: 'SEC' },
  { test: /\bsgx\b|catalist/i, venue: 'SGX' },
  { test: /\basx\b/i, venue: 'ASX' },
  { test: /\btse\b|edinet/i, venue: 'TSE' },
];

const VENUE_AWARE_PHASES = new Set(['edgar_hkex_resolver']);

/**
 * Label for a phase, specialised to the venue it actually reached when the
 * phase is one that varies by market. Falls back to {@link phaseCopy}.
 *
 * @param steps the phase's own status strings, in arrival order
 */
export function refinePhaseLabel(phase: string, steps: string[], running: boolean): string {
  if (VENUE_AWARE_PHASES.has(canonicalPhase(phase))) {
    const hit = VENUE_PATTERNS.find((p) => steps.some((s) => p.test.test(s)));
    if (hit) {
      return running ? `Resolving ${hit.venue} filings` : `${hit.venue} filings resolved`;
    }
  }
  return phaseCopy(phase, running);
}
