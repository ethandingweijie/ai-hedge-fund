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
  // FX conversion metadata (populated when financials are not in USD)
  reported_currency?: string;
  fx_rate?: number;
  fx_note?: string;
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
  net_debt?: number;
  anchor_method?: string;
  profile?: string;
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
