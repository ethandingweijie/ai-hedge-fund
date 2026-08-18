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
  // Per-scenario terminal-value inputs (src/agents/analysis/dcf_agent.py,
  // `scenario_results[scenario]`) — already sent by the backend, previously
  // untyped/unused by the frontend.
  tgr?: number;                    // terminal growth rate used for this scenario
  fcf_margin_start?: number;       // Year-1 FCF margin assumption
  margin_delta_per_year?: number;  // annual FCF-margin drift assumed over the projection
  tv_pct?: number;                 // terminal value as a fraction of total intrinsic value
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
  peak_sales_usd?: number | null;    // raw $ (backend `_extract_pipeline_assets` field)
  peak_sales_bn?: number | null;     // $ billions (legacy field; peakBn() falls back to peak_sales_usd/1e9)
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

// ── GS-style SOTP breakdown (Tier 1 report package) ─────────────────────────
// Emitted by src/agents/analysis/sotp_report_extras.build_sotp_breakdown at
// dcf_range[ticker].sotp_breakdown; null/absent for tickers without SOTP
// assumptions. Frontend gates on `dcfRange?.sotp_breakdown`.
export interface SotpRow {
  name: string;
  revenue_fwd?: number | null;
  ebit?: number | null;
  method?: string;
  multiple?: number | null;
  value?: number | null;
  implied_evrev?: number | null;
  rationale?: string;
  value_split_pct?: number | null;
}

export interface SotpFwdEstimate {
  period_end?: string;
  revenue?: number | null;
  ebit?: number | null;
  ebitda?: number | null;
  net_income?: number | null;
  source?: string;
}

export interface SotpElasticity {
  label: string;
  segment?: string | null;
  parameter?: string;
  base_value?: number | null;
  impact_per_share?: number | null;
  impact_pct?: number | null;
  elasticity?: number | null;
}

export interface SotpRevision {
  item: string;
  section?: string;
  old?: number | string | null;
  new?: number | string | null;
  delta_pct?: number | null;
}

export interface SotpScenario {
  per_share?: number | null;
  per_share_reporting?: number | null;
  applied?: string[];
}

export interface SotpBreakdown {
  method?: string;
  sentence?: string;
  reporting_currency?: string;
  rows?: SotpRow[];
  segment_value?: number | null;
  associates?: number | null;
  net_cash?: number | null;
  nav?: number | null;
  holdco_discount_pct?: number | null;
  holdco_discount?: number | null;
  final?: number | null;
  per_share?: number | null;
  per_share_reporting?: number | null;
  shares?: number | null;
  fx_to_reporting?: number | null;
  forward_estimates?: SotpFwdEstimate[];
  elasticities?: SotpElasticity[];
  scenarios?: Partial<Record<'bear' | 'bull', SotpScenario>>;
  snapshot?: Record<string, unknown> | null;
  revisions?: SotpRevision[];
  revisions_prev_run_at?: string;
  multiple_basis?: {
    summary?: string | null;
    jurisdiction?: { value?: number | null; basis?: string | null } | null;
    divergence_flags?: string[];
    segments?: Record<string, Record<string, unknown>>;
  } | null;
  sources?: Record<string, unknown>;
  confidence?: Record<string, unknown>;
  data_limitations?: string;
}

// ── Tier 2.6: past-run PT track record (GS-style accountability strip) ──────
export interface PtHistoryPoint {
  run_at?: string;
  intrinsic_value?: number | null;
  price_target?: number | null;
  decision_pt?: number | null;
  price_at_run?: number | null;
  action?: string | null;
}

// ── M1 recency loop: prior report recap + freshness delta ────────────────────
// Emitted by src/pipeline.py phases 2.8/2.9 (data.prior_recap / data.freshness_delta,
// per-ticker dicts). Absent on first-ever runs for a ticker.
export interface PriorRecapJson {
  final_action?: string | null;
  price_target?: number | null;
  stop_loss?: number | null;
  entry_range?: Array<number | null>;
  position_size_pct?: number | null;
  time_horizon?: string | null;
  rationale?: string | null;
  price_at_run?: number | null;
  dcf_base_iv?: number | null;
  dcf_bear_iv?: number | null;
  dcf_bull_iv?: number | null;
  dcf_wacc?: number | null;
  power_law_score?: number | null;
  value_trap_verdict?: string | null;
  ev_upside_pct?: number | null;
  assumptions?: string[];
  catalysts?: string[];
  risks?: string[];
  llm_used?: boolean;
}

export interface PriorRecap {
  run_id?: string;
  run_at?: string;
  age_days?: number;
  price_at_run?: number | null;
  final_action?: string | null;
  signal_score?: number | null;
  recap_text?: string;
  recap_json?: PriorRecapJson;
}

export interface FreshnessDeltaEvent {
  headline?: string;
  date?: string;
  relevance?: string;
}

export interface FreshnessDelta {
  material?: boolean | null;      // null = check unavailable
  events?: FreshnessDeltaEvent[];
  verdict?: string;
  based_on_run?: string | null;
  prior_run_at?: string | null;
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
  sotp_breakdown?: SotpBreakdown | null;
  // Methodology-transparency fields — already emitted by
  // src/agents/analysis/dcf_agent.py (dcf_range[ticker] dict) but previously
  // untyped/unused on the frontend. See DcfMethodologyPanel.
  profile_rationale?: string;         // why this profile/method mix was chosen
  data_source?: 'guided' | 'analyst' | 'historical' | string;  // growth-rate provenance
  c_macro?: number;                   // macro-regime WACC modifier applied
  calibration_error?: boolean;        // true when the DCF failed an internal sanity check
  calibration_note?: string;          // human-readable explanation when calibration_error is set
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
export type SectorKpiFormat =
  | 'pct'      // value is a 0–1 ratio, rendered × 100 (0.12 → "12.0%")
  | 'pct100'   // value already 0–100, rendered as-is ("12.0%")
  | 'bps'      // basis points, rendered "166 bps"
  | 'usd'      // absolute dollars, rendered "$N"
  | 'usd_b'    // value already in $billions, rendered "$N.NB"
  | 'x'        // multiple / ratio / score, rendered "N×"
  | 'int'      // count / duration, rendered as integer
  | 'string';  // opaque text rendered verbatim

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
  // Tier 2.6 — past-run PT track record per ticker (built at save time by
  // analysis_service._build_pt_history; oldest-first). Absent on the first
  // run for a ticker.
  price_target_history?: Record<string, PtHistoryPoint[]>;
  // M1 recency loop — recap of the previous report per ticker + freshness
  // delta (what materially changed since it). Absent on first-ever runs.
  prior_recap?: Record<string, PriorRecap>;
  freshness_delta?: Record<string, FreshnessDelta>;
  risk_manager_output?: RiskManagerOutput;
  // Deep research + citations
  deep_research_report?: string;
  deep_research_annotated?: string;   // report text with [n] markers inserted
  citation_registry?: CitationRegistryEntry[];
  // Sector-specific valuation card (Option B). One entry per ticker; absent
  // for legacy sub-profiles (frontend gates on `sector_card?.[ticker]`).
  sector_card?: Record<string, SectorCardPayload>;
  // Card QA Agent audit (Phase 10.5 self-healing). One entry per ticker.
  // Surfaces in CardAuditBanner when severity >= warning. Absent on runs
  // that pre-date the QA agent landing.
  card_qa_audit?: Record<string, DdCardAudit>;
  card_qa_engine_error?: {
    exception_type: string;
    message: string;
    traceback?: string;
  } | null;
  // Per-ticker reason a dcf_range entry came back {} (e.g. "insufficient_history:
  // only_1_years_min_2") + the exception when the whole DCF engine crashed.
  // See DcfMethodologyPanel — surfaced so an empty valuation panel is
  // diagnosable instead of silently blank.
  dcf_skip_reasons?: Record<string, string>;
  dcf_engine_error?: {
    exception_type: string;
    message: string;
    traceback?: string;
  } | null;
  [key: string]: unknown;
}

// ── Card QA Agent (Phase 10.5 / src/agents/audit/) ────────────────────────
//
// Mirrors the Python persistence schema in src/agents/audit/card_qa_agent.py.
// Bump only when the Python schema's TOP-LEVEL shape changes — per-card
// schema versions are tracked separately in `qa_schema_versions`.

export interface DdCardAuditMetaCheck {
  passed: boolean;
  checks_run: string[];
  issues: string[];
  suggested_profile: string | null;
}

export interface DdCardAuditCardInspection {
  card: string;
  applies_when_passed: boolean;
  missing_mandatory: string[];
  missing_opportunistic: string[];
  judge_verdict: 'EXTRACTOR_DROPPED' | 'WRONG_PROFILE' | 'GENUINELY_ABSENT' | 'BUDGET_EXHAUSTED' | null;
  judge_reasoning: string | null;
  judge_evidence_quote: string | null;
  remediation_attempted: boolean;
  remediation_success: boolean | null;
}

export interface DdCardAuditRemediation {
  card: string;
  field: string;
  method: 'hinted_reextract';
  value_set: unknown;
}

export interface DdCardAuditFlag {
  card: string | null;     // null for meta_check flags
  field: string | null;
  reason: string;          // 'classification_likely_wrong' | 'wrong_profile_per_judge' |
                           // 'genuinely_absent_per_judge' | 'reextract_returned_not_found' |
                           // 'budget_exhausted_mid_run' | etc.
  context: string;
  evidence_quote: string;
  suggested_profile?: string | null;
}

export interface DdCardAudit {
  qa_version: string;
  qa_ran_at: string;
  qa_model: string;
  qa_schema_versions: Record<string, number>;
  meta_check: DdCardAuditMetaCheck | null;
  cards_inspected: DdCardAuditCardInspection[];
  auto_remediations: DdCardAuditRemediation[];
  human_review_flags: DdCardAuditFlag[];
  qa_cost_estimate_usd: number;
  qa_budget_hit: boolean;
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
