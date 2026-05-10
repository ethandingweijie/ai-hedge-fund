// ── Shared types for the analysis pipeline report ──────────────────────────

export interface MacroRegime {
  risk_appetite: string;
  rate_direction: string;
  dollar_trend: string;
  volatility_regime: string;
  recession_risk?: string;
}

export interface AgentSignal {
  signal: string;         // BUY | SELL | SHORT | HOLD
  conviction: number;     // 1-10
  time_horizon: string;
  price_target?: number;
  thesis_summary?: string;
  key_risks?: string[];
  cot_log?: string;
}

export interface AgentSignals {
  [agentKey: string]: {
    [ticker: string]: AgentSignal;
  };
}

export interface DebateResult {
  [ticker: string]: {
    triggered?: boolean;
    disagreement_core?: string;
    agent_a_rebuttal?: string;
    agent_b_rebuttal?: string;
    adjudication?: string;
    adjudicated_signal?: string;
    adjudicated_conviction?: number;
  };
}

export interface ScenarioCase {
  fair_value?: number;
  probability?: number;
  assumptions?: string;
  revenue_growth?: number;
  margin?: number;
  multiple?: number;
}

export interface ScenarioReconciliation {
  current_price?: number;
  blended_iv?: number;
  expected_value?: number;
  '12m_price_target'?: number;
  upside_to_pt_pct?: number;
  upside_to_iv_pct?: number;
  bear_iv?: number;
  downside_to_bear_pct?: number;
  skew_ratio?: number;
}

export interface ScenarioAnalysis {
  bull?: ScenarioCase;
  base?: ScenarioCase;
  bear?: ScenarioCase;
  expected_value?: number;
  current_price?: number;
  upside_pct?: number;
  // 12-month forward-multiple price target (different from long-term EV)
  '12m_price_target'?: number;
  '12m_targets_by_scenario'?: { bear?: number; base?: number; bull?: number };
  '12m_pt_method'?: string;
  reconciliation?: ScenarioReconciliation;
  ev_arithmetic_flag?: string;
}

export interface PowerLawAnalysis {
  score?: number;
  total_score?: number;
  scale_economies?: number;
  scale_economies_note?: string;
  scale_economies_concern?: string;
  network_effects?: number;
  network_effects_note?: string;
  network_effects_concern?: string;
  winner_take_most?: number;
  winner_take_most_note?: string;
  winner_take_most_concern?: string;
  switching_costs?: number;
  switching_costs_note?: string;
  switching_costs_concern?: string;
  data_ip_moat?: number;
  data_ip_moat_note?: string;
  data_ip_moat_concern?: string;
  interpretation?: string;
  multiple_implication?: string;
}

export interface ValueTrapCheck {
  rating: 'RED' | 'AMBER' | 'GREEN';
  evidence?: string;  // backend field name
  detail?: string;    // legacy alias
}

export interface ValueTrapAnalysis {
  dividend_sustainability?: ValueTrapCheck;
  structural_decline?: ValueTrapCheck;
  earnings_cash_mismatch?: ValueTrapCheck;
  insider_behaviour?: ValueTrapCheck;
  balance_sheet?: ValueTrapCheck;
  verdict?: string;          // "TRAP RISK HIGH" | "TRAP RISK MEDIUM" | "TRAP RISK LOW"
  overall_verdict?: string;  // legacy field name used by older pipeline runs
}

export interface DcfCase {
  intrinsic_value?: number;
  growth_rate?: number;
  margin_direction?: string;
  risk_flag?: string;
  terminal_value?: number;
  // Per-scenario per-method IV table (key = method name, value = $ per share)
  method_iv_table?: Record<string, number>;
  // Profile weights list — [{name, weight}] for method-weight columns
  profile_weights?: Array<{ name: string; weight: number }>;
  methods_used?: string[];
  forward_flags?: string[];
  // FX conversion metadata (populated when financials are not in USD)
  reported_currency?: string;
  fx_rate?: number;
  fx_note?: string;
}

// ── REIT-specific breakdown ────────────────────────────────────────────────
// Emitted by dcf_agent.py (src/agents/analysis/dcf_agent.py) for tickers in
// sector in {"RealEstate","REIT"} or profile_name contains "REIT".
// Every field is either a real number or null/undefined — the UI hides the
// sub-panel when the specific field is missing. Research-sourced fields
// (occupancy, WALE, subtype_mix, geographic_mix) are optional; derivable
// fields (nav_per_share, gross_asset_value) are always present when the
// underlying ingredients are.
export interface ReitBreakdown {
  subtype?: string;                 // e.g. "data_center", "retail", "industrial"
  // Absolute figures (for NAV Bridge + audit)
  ffo?: number | null;
  affo?: number | null;
  noi?: number | null;
  normalized_maintenance_capex?: number | null;
  maint_capex_pct?: number | null;
  total_debt?: number | null;
  cash?: number | null;
  shares?: number | null;
  // Per-share figures
  ffo_per_share?: number | null;
  affo_per_share?: number | null;
  dps?: number | null;
  // Multiples used
  cap_rate_used?: number | null;
  cap_rate_peer?: number | null;
  p_ffo_peer?: number | null;
  p_affo_peer?: number | null;
  // Research overrides (LLM-extracted from deep research)
  occupancy_rate?: number | null;
  wale_years?: number | null;
  leverage_ratio_research?: number | null;
  subtype_mix?: Record<string, number> | null;
  geographic_mix?: Record<string, number> | null;
  research_evidence?: string | null;
  // Pre-computed NAV bridge components (verification / display convenience;
  // the frontend also recomputes these from the raw inputs for transparency)
  gross_asset_value?: number | null;
  nav_total?: number | null;
  nav_per_share?: number | null;
  // Historical series for CLINT-style time-series bar charts
  npi_history?: Array<{ period: string; value: number | null }>;
  dpu_history?: Array<{ period: string; value: number | null }>;
}

// ── Bank-specific breakdown ────────────────────────────────────────────────
// Emitted by dcf_agent.py for any ticker where sector == "Financials" AND
// profile_name is in _BANK_PROFILE_CALIBRATION (Money Center Bank, Regional
// Bank, Investment Bank, Asset Manager, Mortgage/GSE, Insurance, FinTech,
// EM Bank, Money Center Bank (SG), etc.). Every field is either a real
// number or null — the UI gates tile-by-tile to gracefully degrade when a
// source is missing (FMP rolls bank line items into generic buckets;
// yfinance SGX coverage misses interest_income on some years).
export interface BankBreakdown {
  profile?: string;                 // e.g. "Money Center Bank"
  // Profile calibration constants (for threshold color-coding on the UI)
  coe?: number | null;
  target_roe?: number | null;
  target_cet1?: number | null;
  fade_years?: number | null;
  // Core latest-year ratios
  roe?: number | null;
  roa?: number | null;
  nim?: number | null;
  efficiency_ratio?: number | null;   // Cost / Income Ratio
  credit_cost_ratio?: number | null;
  tbv_per_share?: number | null;
  bvps?: number | null;
  total_equity?: number | null;
  total_assets?: number | null;
  // P/TBV-based Fair Value (Gordon-growth identity)
  fair_p_tbv?: number | null;
  fair_value_per_share?: number | null;
  // Capital adequacy
  cet1_ratio?: number | null;
  cet1_buffer_bps?: number | null;
  cet1_surplus_usd?: number | null;
  // Capital return
  dividend_yield?: number | null;
  buyback_yield?: number | null;
  total_payout_ratio?: number | null;
  dps?: number | null;
  buybacks_usd?: number | null;
  // Research-sourced (nullable — only present on fresh runs with deep research)
  npl_ratio?: number | null;
  npl_coverage_ratio?: number | null;
  net_charge_offs_pct?: number | null;
  management_overlays_bn?: number | null;
  nim_rate_sensitivity_bps?: number | null;
  loan_growth_yoy?: number | null;
  deposit_growth_yoy?: number | null;
  loan_to_deposit_ratio?: number | null;
  forward_loan_growth_guidance?: string | null;
  forward_nim_guidance?: string | null;
  research_evidence?: string | null;
  // 5y history arrays (CLINT-style bar charts)
  roe_history?: Array<{ period: string; value: number | null }>;
  nim_history?: Array<{ period: string; value: number | null }>;
  bvps_history?: Array<{ period: string; value: number | null }>;
  ppop_history?: Array<{ period: string; value: number | null }>;
  cir_history?: Array<{ period: string; value: number | null }>;
  loans_history?: Array<{ period: string; value: number | null }>;
}

// ── Biopharma — pipeline asset schema (emitted today by _extract_pipeline_assets) ──
// Source: src/agents/industry/deep_research.py  `_extract_pipeline_assets`
// Propagated via state["data"]["pipeline_assets"][ticker] → RunResult.data.pipeline_assets
export interface BiopharmaPipelineAsset {
  name: string;
  phase?: string;                    // "preclinical" | "Ph1" | "Ph2" | "Ph3" | "Filed" | "Approved"
  peak_sales_bn?: number | null;     // $ billions
  launch_year?: number | null;
  indication?: string | null;
  therapeutic_area?: string | null;  // oncology | cns | rare | metabolic | cv | immunology | infectious_disease | other
  partner?: string | null;           // e.g. "MRK" for mRNA-4157
  evidence?: string | null;          // ≤300 char source citation
}

// ── Tech / SaaS metrics extractor output ─────────────────────────────────────
// Source: src/agents/industry/deep_research.py  `_extract_saas_metrics`
// Propagated via state["data"]["saas_metrics"][ticker] → RunResult.data.saas_metrics
// All fields are decimals (0.80-1.50 for NRR = 80%-150%) except months / raw
// scores. Fields are individually optional — tiles gate on presence.
export interface SaasMetrics {
  nrr_pct?: number | null;                // 0.80–1.50 (e.g. 1.26 = 126% NRR)
  gross_retention_pct?: number | null;    // 0.80–1.00
  cac_payback_months?: number | null;
  ltv_cac_ratio?: number | null;
  rule_of_40_score?: number | null;       // numeric score (growth % + FCF margin %)
  magic_number?: number | null;
  rpo_growth_yoy?: number | null;         // −0.20 to 0.80
  billings_growth_yoy?: number | null;
  evidence?: string | null;
}

export interface DcfRange {
  bull?: DcfCase;
  base?: DcfCase;
  bear?: DcfCase;
  wacc?: number;
  shares_outstanding?: number;
  revenue_base?: number;
  fcf_margin_base?: number;
  // 12m forward-multiple targets per scenario (from DCF agent)
  '12m_targets'?: { bear?: number | null; base?: number | null; bull?: number | null };
  // Wall Street analyst consensus 12m PT (FMP /stable/price-target-consensus).
  // null when ticker is HK/SG (FMP n/a) or when fetch fails. Used by V2 hero
  // card to render "vs Wall St $XXX" sanity line below the model PT.
  consensus_pt?: {
    high?:      number | null;
    low?:       number | null;
    consensus?: number | null;
    median?:    number | null;
  } | null;
  net_debt?: number;
  anchor_method?: string;
  profile?: string;
  reit_breakdown?: ReitBreakdown | null;
  bank_breakdown?: BankBreakdown | null;
}

export interface RoutingDecision {
  sector?: string;
  raw_financials?: Record<string, unknown>;
  insider_summary?: string;
}

export interface RiskManagerOutput {
  [ticker: string]: {
    approved_position_size?: number;
    flags?: string[];
    notes?: string;
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// Sector valuation card (Option B render). Built backend-side by
// `src/data/sector_kpi_framework.render_card_payload(profile_name, state, ticker)`
// and persisted in three places (per the persistence fix chain — see commits
// 1ac5490, 10ed937, d748ad4):
//   1. Pipeline return dict → web_runs.full_result_json (fresh runs)
//   2. _save_checkpoint partial_data → SSE progressive UI
//   3. ticker_signals.sector_card_json → archive (historical runs)
// Legacy sub-profiles (SaaS / REIT / Biopharma) intentionally omit this
// payload — the existing bespoke cards remain authoritative for them.
// ═══════════════════════════════════════════════════════════════════════════
export type SectorKpiAccent = 'blue' | 'green' | 'amber' | 'rose' | 'violet';
export type SectorKpiFormat = 'pct' | 'usd' | 'x' | 'int' | 'string';

export interface SectorKpi {
  key: string;
  label: string;
  value: number | string | null;
  format: SectorKpiFormat;
  decimals?: number | null;
  unit?: string | null;
  mandatory?: boolean;
  clamp_low?: number | null;
  clamp_high?: number | null;
}

export interface SectorKpiGroup {
  title: string;
  accent: SectorKpiAccent;
  kpis: SectorKpi[];
}

// V3 — Composite adjustment audit bridge. Tells the user WHY the IV moved:
// Quality (operational) × Risk (balance sheet) × Commodity (forward leverage)
// → Final composite multiplier (capped at 1.85x or 1.70x for commodity sectors).
export interface AuditBridge {
  quality: number;
  quality_note: string;
  quality_weight?: number;           // V4-α profile-specific weight (0–1)
  quality_z?: number | null;         // V4-β peer-cohort z-score (when n≥3)
  quality_cohort?: number | null;    // V4-β peer cohort size used for z
  quality_extracted?: number;        // P2 — # of tier KPIs with non-null values
  quality_total?: number;            // P2 — total tier KPIs in schema
  risk: number;
  risk_note: string;
  risk_weight?: number;
  risk_z?: number | null;
  risk_cohort?: number | null;
  risk_extracted?: number;           // P2 — # extracted (0 or 1)
  risk_total?: number;               // P2 — 0 or 1
  risk_cap_gate_kpi?: string | null; // P2 — cap_when gate KPI name (if any)
  commodity: number;
  commodity_note: string;
  commodity_weight?: number;
  raw_composite: number;
  final_multiplier: number;
  cap_high: number;     // 1.70 for Resources/Energy/Materials, else 1.85
  was_capped: boolean;
  // P1 — extraction completeness signals from extract_via_framework
  completeness_score?: number | null;
  mandatory_missing?: string[];
  // v3.19 — Composite normalised to 0-100 score with tier label for UI display
  // (replaces the raw "1.14x" multiplier as the prominent number on the card).
  // tier_label ∈ {"premium" (≥80), "in-band" (40-79), "haircut" (<40)}.
  composite_score?: number | null;
  tier_label?: 'premium' | 'in-band' | 'haircut' | null;
}

export interface SectorCardPayload {
  ticker: string;
  sector: string;
  profile_name: string;
  sub_profile?: string | null;
  anchor_methods: string[];
  groups: SectorKpiGroup[];
  source_priority?: string[];
  audit_bridge?: AuditBridge;  // V3 composite adjustment breakdown
}

export interface PipelineData {
  tickers?: string[];
  macro_regime?: MacroRegime;
  agent_weights?: Record<string, number>;
  routing_decision?: Record<string, RoutingDecision>;
  analyst_signals?: AgentSignals;
  industry_brief?: string;
  debate_result?: DebateResult;
  scenario_analysis?: Record<string, ScenarioAnalysis>;
  power_law_analysis?: Record<string, PowerLawAnalysis>;
  value_trap_analysis?: Record<string, ValueTrapAnalysis>;
  dcf_range?: Record<string, DcfRange>;
  risk_manager_output?: RiskManagerOutput;
  // Deep research + citations
  deep_research_report?: string;
  deep_research_annotated?: string;   // report text with [n] markers inserted
  citation_registry?: CitationRegistryEntry[];
  // Sector-specific valuation card (Option B). One entry per ticker; absent
  // for legacy sub-profiles (frontend gates on `sector_card?.[ticker]`).
  sector_card?: Record<string, SectorCardPayload>;
  [key: string]: unknown;
}

export interface PortfolioDecision {
  action: string;        // BUY | SELL | SHORT | COVER | HOLD
  position_size_pct?: number;
  entry_range?: [number, number];
  stop_loss?: number;
  price_target?: number;
  time_horizon?: string;
  rationale?: string;
}

// ── VGPM Scorecard ─────────────────────────────────────────────────────────

export interface VgpmDimension {
  score: number;       // 0-100
  grade: string;       // A+ | A | A- | B+ | B | B- | C | D
  subs?: string[];     // sub-metric label strings
}

export interface VgpmResult {
  valuation?: VgpmDimension;
  growth?: VgpmDimension;
  profitability?: VgpmDimension;
  momentum?: VgpmDimension;
}

// ── Full run result ─────────────────────────────────────────────────────────

export interface RunResult {
  run_id: string;
  ticker: string;
  model_name?: string;
  run_at: string;
  data: PipelineData;
  decisions: Record<string, PortfolioDecision>;
  vgpm?: Record<string, VgpmResult>;
}

// ── History / list item ─────────────────────────────────────────────────────

export interface RunSummary {
  run_id: string;
  run_at: string;
  ticker: string;
  model_name?: string;
  regime?: string;
  sector?: string;
  final_action?: string;
  position_size_pct?: number;
  price_target?: number;
  stop_loss?: number;
  dcf_base_iv?: number;
  ev_upside_pct?: number;
  power_law_score?: number;
  value_trap_verdict?: string;
  vgpm_grades?: Record<string, string>;
}

export interface HistoryResponse {
  items: RunSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface ArchiveSummary {
  total_runs: number;
  sector_breakdown: Record<string, number>;
  action_breakdown: Record<string, number>;
}

// ── Screener ────────────────────────────────────────────────────────────────

export interface VgpmSummary {
  score: number;
  grade: string;
}

export interface ScreenerStock {
  symbol: string;
  companyName: string;
  sector: string;
  industry: string;
  marketCap: number | null;
  price: number | null;
  change_pct: number | null;
  volume: number | null;
  beta: number | null;
  exchange: string;
  country: string;
  vgpm: {
    valuation?:     VgpmSummary;
    growth?:        VgpmSummary;
    profitability?: VgpmSummary;
    momentum?:      VgpmSummary;
  } | null;
  vgpm_estimated: boolean;
  composite_score: number | null;
}

export interface ScreenerResponse {
  items: ScreenerStock[];
  total: number;
  cached: boolean;
}

// ── Watchlist ────────────────────────────────────────────────────────────────

export interface WatchlistItem {
  ticker:          string;
  companyName:     string;
  addedAt:         string;
  price:           number | null;
  change_pct:      number | null;
  vgpm: {
    valuation?:     VgpmSummary;
    growth?:        VgpmSummary;
    profitability?: VgpmSummary;
    momentum?:      VgpmSummary;
  } | null;
  composite_score: number | null;
}

// ── Citation registry ────────────────────────────────────────────────────────

export interface CitationRegistryEntry {
  ref_id: number;
  claim: string;
  source_name?: string;
  source_type?: string;   // "sec_filing" | "press_release" | "web" | "financial_data" | etc.
  date?: string;
  speaker?: string;
  quote?: string;         // verbatim cited text (used for inline [n] matching)
  url?: string;
  section?: string;
  verified?: boolean;
}

// ── DD Alerts (Auto Due-D dashboard) ───────────────────────────────────────

export type DdDirection = 'DROP' | 'PUMP';

export interface DdReport {
  cause_summary?:       string;
  thesis_impact?:       string;
  recommended_action?:  string;
  news_drivers?:        Array<{ title?: string; url?: string; publishedDate?: string; date?: string }>;
  filings?:             Array<{ form?: string; type?: string; filing_date?: string; date?: string; url?: string; summary?: string; title?: string }>;
  insider_signal?:      string;
}

export interface DdAlert {
  ticker:            string;
  last_direction:    DdDirection;
  trigger_pct:       number;
  trigger_price:     number;
  last_triggered_at: string;
  tier:              string;
  alert_reason:      string;          // 'first_breach' | 'direction_flip_*' | 'high_water_mark*' | 'cooldown_expired'
  cluster_id?:       string | null;
  dd_run_id?:        string | null;
  sent_status?:      string;
  /** Hydrated from web_runs.full_result_json when dd_run_id is present. */
  report?:           DdReport | null;
}

export interface DdCluster {
  cluster_id: string;
  direction:  DdDirection;
  n:          number;
  median_pct: number;
}

/** Phase 2E: LLM-narrated EOD digest payload (web-only). Present when
 *  the digest agent has run for the current UTC date; null otherwise. */
export interface DdDigestNarrative {
  narrative:        string;            // 3-5 sentence prose
  key_themes:       string[];          // up to ~5 short clauses
  macro_or_micro:   'macro' | 'micro' | 'mixed';
  tomorrow_watch:   string;            // 1-2 sentences
  generated_at?:    string;            // ISO timestamp
  _model_name?:     string;            // 'dd_digest_qwen' | 'dd_digest_qwen_FALLBACK'
  drops?:           Array<{ ticker: string; pct: number; price: number }>;
  pumps?:           Array<{ ticker: string; pct: number; price: number }>;
  clusters?:        Array<{ sector: string; direction: DdDirection; n: number; median_pct: number }>;
}

export interface DdDigest {
  date:       string;
  drops:      DdAlert[];
  pumps:      DdAlert[];
  clusters:   DdCluster[];
  narrative?: DdDigestNarrative | null;   // Phase 2E
}

/** Phase 3 attribution: aggregate hit rates by action / direction / reason
 *  + agent-vs-naive-baseline alpha. Returned by GET /api/dd-alerts/performance. */
export interface DdPerformanceBucket {
  n:                  number;
  n_correct:          number;
  n_incorrect:        number;
  hit_rate:           number | null;       // (correct / (correct + incorrect)); null if zero decisive grades
  mean_1d_return:     number | null;       // decimal
  mean_5d_return:     number | null;
  mean_22d_return:    number | null;
}

export interface DdPerformance {
  n_alerts_graded:        number;
  by_action:              Record<string, DdPerformanceBucket>;   // ADD/TRIM/EXIT/HOLD/WATCH/UNCLEAR
  by_direction:           Record<string, DdPerformanceBucket>;   // DROP/PUMP
  by_reason:              Record<string, DdPerformanceBucket>;   // first_breach/direction_flip/...
  naive_mean_5d_return:   number;
  agent_mean_5d_alpha:    number;
  alpha_vs_naive:         number;
  since:                  string | null;
  until:                  string | null;
}

// ── SSE progress event ──────────────────────────────────────────────────────

export interface ProgressEvent {
  phase: string;
  status: string;
  summary: string;
  reasoning?: string;
  ticker?: string;
  timestamp?: string;
  /** Structured pipeline data emitted as soon as a phase completes — accumulated into liveData */
  partial_data?: Record<string, unknown>;
}
