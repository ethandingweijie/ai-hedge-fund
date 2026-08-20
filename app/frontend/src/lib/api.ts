// ── API client for the analysis service ────────────────────────────────────
import type {
  ArchiveSummary,
  HistoryResponse,
  RunResult,
  ScreenerResponse,
  ScreenerStock,
  WatchlistItem,
} from './reportTypes';
import { API_BASE_URL } from '@/config';
import { getStoredToken } from '@/contexts/auth-context';

const BASE = API_BASE_URL;

// ── Helper ─────────────────────────────────────────────────────────────────

function _authHeaders(): HeadersInit {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Analysis endpoints ─────────────────────────────────────────────────────

/** Start a pipeline run — returns the raw Response so the caller can stream SSE. */
export function startAnalysisRun(
  ticker: string,
  model = 'claude-sonnet-4-6',
): Promise<Response> {
  // M2 Track E: the investor committee is decommissioned — the run body
  // carries ticker + model only. Old servers tolerated the extra key;
  // new servers ignore it, so there is nothing to send.
  return fetch(`${BASE}/analysis/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify({ ticker, model }),
  });
}

/** Pulse — Gemini-Flash-style instant recall (SSE). Read-only: never starts
 * a pipeline run. Returns the raw Response so the caller can stream beats. */
export function pulseTicker(ticker: string, signal?: AbortSignal): Promise<Response> {
  return fetch(`${BASE}/analysis/pulse?ticker=${encodeURIComponent(ticker)}`, { signal });
}

/** Fetch the full result for a completed run. */
export function getRunResult(runId: string): Promise<RunResult> {
  return fetchJson<RunResult>(`${BASE}/analysis/runs/${runId}`);
}

/** Permanently delete a run from the archive. */
export function deleteRun(runId: string): Promise<{ deleted: string }> {
  return fetchJson(`${BASE}/analysis/runs/${encodeURIComponent(runId)}`, { method: 'DELETE' });
}

/** Fetch paginated history with optional filters. */
export function getHistory(params: {
  ticker?: string;
  sector?: string;
  regime?: string;
  action?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}): Promise<HistoryResponse> {
  const qs = new URLSearchParams();
  if (params.ticker) qs.set('ticker', params.ticker);
  if (params.sector) qs.set('sector', params.sector);
  if (params.regime) qs.set('regime', params.regime);
  if (params.action) qs.set('action', params.action);
  if (params.date_from) qs.set('date_from', params.date_from);
  if (params.date_to) qs.set('date_to', params.date_to);
  if (params.page != null) qs.set('page', String(params.page));
  if (params.page_size != null) qs.set('page_size', String(params.page_size));
  return fetchJson<HistoryResponse>(`${BASE}/analysis/runs?${qs}`, { headers: _authHeaders() });
}

/** Fetch archive summary (counts by sector and action). */
export function getArchiveSummary(): Promise<ArchiveSummary> {
  return fetchJson<ArchiveSummary>(`${BASE}/analysis/summary`);
}

export interface CompanySearchResult {
  ticker:   string;
  name:     string;
  exchange: string;
  type:     string;
}

/** Search companies by name or ticker (FMP + yfinance fallback). */
export function searchCompanies(q: string, limit = 8): Promise<CompanySearchResult[]> {
  return fetchJson(`${BASE}/analysis/search?q=${encodeURIComponent(q)}&limit=${limit}`);
}

/** Resolve a ticker symbol to its company profile (name, sector, industry). */
export function getCompanyName(ticker: string): Promise<{ ticker: string; name: string; sector?: string | null; industry?: string | null }> {
  return fetchJson(`${BASE}/analysis/company/${encodeURIComponent(ticker.toUpperCase())}`);
}

/** Batch-resolve company names for multiple tickers in a single request. */
export function getCompanyNames(tickers: string[]): Promise<Record<string, { name: string; sector?: string | null; industry?: string | null }>> {
  if (!tickers.length) return Promise.resolve({});
  return fetchJson(`${BASE}/analysis/companies?tickers=${encodeURIComponent(tickers.join(','))}`);
}

export interface PopularTicker {
  ticker:     string;
  price:      number | null;
  change:     number | null;
  change_pct: number | null;
}

/** Return the most-searched tickers with their day-over-day price change. */
export function getPopularTickers(limit = 15): Promise<PopularTicker[]> {
  return fetchJson(`${BASE}/analysis/popular-tickers?limit=${limit}`);
}

// ── Intelligence types ──────────────────────────────────────────────────────

export interface IntelligenceData {
  ticker: string;
  insider_activity:  Record<string, unknown>;
  analyst_revisions: Record<string, unknown>;
  news_sentiment:    Record<string, unknown>;
  earnings_quality:  Record<string, unknown>;
  short_interest:    Record<string, unknown>;
}

/** Fetch all 5 intelligence signals live from FMP + yfinance. */
export function getIntelligence(ticker: string): Promise<IntelligenceData> {
  return fetchJson(
    `${BASE}/analysis/intelligence/${encodeURIComponent(ticker.toUpperCase())}`,
  );
}

// ── News types ──────────────────────────────────────────────────────────────

export interface NewsArticle {
  title: string;
  text: string;
  url: string;
  publishedDate: string;
  site: string;
  image: string;
  symbol: string;
}

/** Fetch latest news for a ticker via FMP (proxied through backend). */
export function getCompanyNews(ticker: string, limit = 10): Promise<{ ticker: string; articles: NewsArticle[] }> {
  return fetchJson(
    `${BASE}/analysis/news/${encodeURIComponent(ticker.toUpperCase())}?limit=${limit}`,
  );
}

// ── Financials types ────────────────────────────────────────────────────────

export interface FinancialsItem {
  date: string;
  period_label: string;
  revenue: number | null;
  net_income: number | null;
  operating_income: number | null;
}

export interface FinancialsResponse {
  ticker: string;
  period_type: string;
  items: FinancialsItem[];
}

/** Fetch income-statement time-series from FMP via the backend proxy. */
export function getFinancials(
  ticker: string,
  period: 'annual' | 'quarter' = 'annual',
): Promise<FinancialsResponse> {
  return fetchJson(
    `${BASE}/analysis/financials/${encodeURIComponent(ticker.toUpperCase())}?period=${period}`,
  );
}

// ── Screener ────────────────────────────────────────────────────────────────

/** Fetch FMP screener results merged with internal VGPM grades. */
export function getScreenerStocks(params: {
  sector?: string;
  exchange?: string;
  country?: string;
  marketCapMin?: number;
  marketCapMax?: number;
  limit?: number;
  refresh?: boolean;
} = {}): Promise<ScreenerResponse> {
  const q = new URLSearchParams();
  if (params.sector)                  q.set('sector', params.sector);
  if (params.exchange)                q.set('exchange', params.exchange);
  if (params.country)                 q.set('country', params.country);
  if (params.marketCapMin != null)    q.set('marketCapMin', String(params.marketCapMin));
  if (params.marketCapMax != null)    q.set('marketCapMax', String(params.marketCapMax));
  if (params.limit != null)           q.set('limit', String(params.limit));
  if (params.refresh)                 q.set('refresh', 'true');
  return fetchJson<ScreenerResponse>(`${BASE}/screener/stocks?${q}`);
}

/** Direct FMP profile lookup for a single ticker — fallback when not in screener batch. */
export function lookupScreenerTicker(symbol: string): Promise<ScreenerStock> {
  return fetchJson<ScreenerStock>(`${BASE}/screener/lookup?symbol=${encodeURIComponent(symbol.toUpperCase())}`);
}

/** ~118 well-known HKEX stocks with VGPM scores (peer-relative within HK universe). */
export function getHkScreenerStocks(refresh = false): Promise<ScreenerResponse> {
  return fetchJson<ScreenerResponse>(`${BASE}/screener/hk-stocks${refresh ? '?refresh=true' : ''}`);
}

export function getSgScreenerStocks(refresh = false): Promise<ScreenerResponse> {
  return fetchJson<ScreenerResponse>(`${BASE}/screener/sg-stocks${refresh ? '?refresh=true' : ''}`);
}

/** Lightweight live quote fetch — price, marketCap, volume, beta, change_pct. No VGPM recompute. */
export function getScreenerPrices(
  symbols: string[],
): Promise<Record<string, { price?: number; marketCap?: number; volume?: number; beta?: number; change_pct?: number | null }>> {
  return fetchJson(`${BASE}/screener/prices?symbols=${symbols.join(',')}`);
}

// ── Watchlist ────────────────────────────────────────────────────────────────

export function getWatchlist(): Promise<WatchlistItem[]> {
  return fetchJson<WatchlistItem[]>(`${BASE}/watchlist`);
}

export function addToWatchlist(ticker: string): Promise<WatchlistItem> {
  return fetchJson<WatchlistItem>(`${BASE}/watchlist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker }),
  });
}

export function removeFromWatchlist(ticker: string): Promise<{ removed: string }> {
  return fetchJson<{ removed: string }>(`${BASE}/watchlist/${encodeURIComponent(ticker)}`, {
    method: 'DELETE',
  });
}

/** Fetch price history and key financial metrics for a ticker.
 *  Matches app/backend/routes/analysis.py `get_stock_data`'s `metrics` dict. */
export function getStockData(ticker: string, period = '1y'): Promise<{
  ticker: string;
  history: { date: string; close: number }[];
  metrics: {
    market_cap?: number;
    revenue?: number;
    free_cash_flow?: number;
    net_margin?: number;
    pe_ratio?: number;
    price_to_sales?: number;
    revenue_growth?: number;
    ev_to_ebitda?: number;
    return_on_equity?: number;
    return_on_assets?: number;
    return_on_invested_capital?: number;
    free_cash_flow_yield?: number;
    total_cash?: number;
    total_debt?: number;
    net_cash?: number;
    fifty_two_week_high?: number;
    fifty_two_week_low?: number;
    price_to_book?: number;
    eps_ttm?: number;
    dividend_yield?: number;
    day_high?: number;
    day_low?: number;
    prev_close?: number;
    volume?: number;
  };
}> {
  return fetchJson(`${BASE}/analysis/stock/${encodeURIComponent(ticker.toUpperCase())}?period=${period}`);
}

// ── Revenue segmentation (FMP product + geographic) ─────────────────────────

export interface RevenueSegment {
  name: string;
  revenue: number;
  pct: number | null;
  yoy_pct: number | null;
}

export interface RevenueSegmentation {
  ticker: string;
  fiscal_year: number | null;
  period: string | null;
  currency: string | null;
  total_revenue: number | null;
  segments: RevenueSegment[];
}

/** Product-level revenue breakdown for a ticker. FMP-backed; US tickers
 *  get the best coverage. Empty `segments` = company doesn't report. */
export function getRevenueProductSegmentation(ticker: string, period: 'annual' | 'quarter' = 'annual'): Promise<RevenueSegmentation> {
  return fetchJson(`${BASE}/analysis/revenue-segmentation/${encodeURIComponent(ticker.toUpperCase())}?period=${period}`);
}

/** Geographic revenue breakdown for a ticker. Same shape as product
 *  segmentation — segment names are regions instead of product lines. */
export function getRevenueGeoSegmentation(ticker: string, period: 'annual' | 'quarter' = 'annual'): Promise<RevenueSegmentation> {
  return fetchJson(`${BASE}/analysis/revenue-geo-segmentation/${encodeURIComponent(ticker.toUpperCase())}?period=${period}`);
}

// ── Research Ideas ──────────────────────────────────────────────────────────
//
// Endpoints expose research artifacts that live outside the main pipeline.
// v1 surfaces SW46 — software-46 cohort using the Cassandra Unchained /
// Scion methodology (Tragic Algebra owner earnings, AICT tiering, IV15).

export type AICTTier = 'Fortress' | 'Castle' | 'Chapel' | 'Stone' | 'Wood';
export type TATier   = 'Not-TT' | 'Near-TT' | 'TT*' | 'N/A';

export interface SW46IdeaMeta {
  id: string;
  name: string;
  blurb: string;
  ticker_count: number;
  last_run_at: string | null;
  headline_metric_label?: string;
  // SW46-specific:
  last_pooled_delta_e?: number | null;
  // Complacency-specific:
  last_gate_passers?: number | null;
  // Idea-of-the-Day specific:
  is_ai_generated?: boolean;
  latest_idea_ticker?: string | null;
  latest_idea_id?: string | null;
  latest_idea_hypothesis?: string | null;
  latest_idea_conviction?: number | null;
  // Richer hero-card fields (thematic context)
  latest_idea_mode?: ContrarianIdeaMode | string | null;
  latest_idea_region?: string | null;
  latest_idea_sector?: string | null;
  latest_idea_company?: string | null;
  latest_idea_theme?: string | null;
  latest_idea_catalyst?: string | null;
  latest_idea_vehicle?: string | null;
  // HK50-specific:
  last_avg_growth?: number | null;
  last_avg_dividend?: number | null;
  top5_growth?: Array<{ ticker: string; name: string; score: number | null }>;
  top5_dividend?: Array<{ ticker: string; name: string; score: number | null }>;
  // Momentum-specific (dual-direction long + short):
  as_of?: string | null;
  long_count?: number | null;
  short_count?: number | null;
  is_dual_direction?: boolean;
  top_long_sectors?: MomentumSectorPreview[];
  top_short_sectors?: MomentumSectorPreview[];
  lead_long_tickers?: MomentumTickerPreview[];
  lead_short_tickers?: MomentumTickerPreview[];
  // Fund Flow-specific (geographic rotation). Reuses long_count/short_count
  // for inflow/outflow counts so the shared card chrome needs no special case.
  flow_headline?: string | null;
  flow_regime?: string | null;
  flow_summary_source?: string | null;
  top_inflow_regions?: FundFlowRegionPreview[];
  top_outflow_regions?: FundFlowRegionPreview[];
  // 100-Question screener-specific:
  active_pass_count?: number | null;
  on_deck_count?: number | null;
  cooloff_count?: number | null;
}

export interface FundFlowRegionPreview {
  region: string;
  label: string | null;
  emoji: string | null;
  composite: number | null;
  verdict: string | null;
  rel_flow_z: number | null;
  implied_flow_21d: number | null;
}

export interface MomentumSectorPreview {
  etf: string;
  label: string | null;
  composite: number | null;
  verdict: string | null;
}

export interface MomentumTickerPreview {
  ticker: string;
  name: string | null;
  verdict: string | null;
  composite: number | null;
  sector_aligned: boolean | null;
  sector: string | null;
}

export interface SW46TragicAlgebraYear {
  fiscal_year: number;
  net_income: number | null;
  sbc_expense: number | null;
  cash_tax_withholding: number | null;
  cash_tax_withholding_estimated: boolean;
  buybacks: number | null;
  share_change: number | null;
  avg_share_price: number | null;
  unfunded_comp: number | null;
  genuine_buyback_return: number | null;
  is_net_diluter: boolean | null;
  omega: number | null;
  owner_earnings: number | null;
  delta_e: number | null;
}

export type IV15Block = {
  iv15_total: number | null;
  iv15_per_share: number | null;
  iv15_ddm_total: number | null;
  iv15_buffett_total: number | null;
  base_oe: number | null;
  base_oe_source: 'latest' | 'median' | 'forward_ni' | 'forward_margin' | 'none';
  required_return_used: number;
  growth_year1_5: number | null;
  growth_year6_10: number | null;
  growth_year11_15: number | null;
  terminal_multiple_used: number | null;
  shares_outstanding: number | null;
};

export interface SW46TickerResult {
  ticker: string;
  name: string;
  price: number | null;
  market_cap: number | null;
  fwd_revenue_growth: number | null;
  rank: number | null;
  aict: {
    tier: AICTTier;
    growth_haircut: number;
    terminal_multiple: number;
    blend_buffett_weight: number;
  };
  tragic_algebra: {
    years: SW46TragicAlgebraYear[];
    pooled_delta_e: number | null;
    avg_owner_earnings: number | null;
    latest_owner_earnings: number | null;
    median_owner_earnings: number | null;
    positive_oe_years: number;
    sum_net_income: number | null;
    sbc_trend: number | null;
    ta_tier: TATier;
    estimated_c_years: number;
  };
  roic: {
    owner_earnings: number | null;
    interest_income: number | null;
    capital_lease_payments: number | null;
    other_expense_adjustments: number;
    numerator: number | null;
    total_capital: number | null;
    lt_operating_leases: number | null;
    net_cash: number | null;
    purchase_obligations: number;
    other_capital: number;
    denominator: number | null;
    roic: number | null;
  };
  iv15: IV15Block;
  iv12: IV15Block | null;
  iv18: IV15Block | null;
  ivb_pct: number | null;
  p_iv12: number | null;
  p_iv18: number | null;
  justification: string | null;
  composite: {
    shareholder_bucket: number;
    quality_bucket: number;
    valuation_bucket: number;
    total: number;
    pts_ta_tier: number;
    pts_delta_e: number;
    pts_sbc_trend: number;
    pts_aict_tier: number;
    pts_roic: number;
    pts_growth: number;
    pts_p_iv15: number;
    p_iv15: number | null;
  };
  error?: string | null;
}

export interface SW46Cohort {
  run_id: string | null;
  created_at: string | null;
  cohort_pooled_delta_e: number | null;
  ticker_count: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
  results: SW46TickerResult[];
}

export interface SW46RunHeader {
  run_id: string;
  created_at: string;
  cohort_pooled_delta_e: number | null;
  ticker_count: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}

export function listResearchIdeas(): Promise<{ ideas: SW46IdeaMeta[] }> {
  return fetchJson(`${BASE}/research/ideas`);
}

export function getSW46Cohort(): Promise<SW46Cohort> {
  return fetchJson(`${BASE}/research/ideas/sw46`);
}

export function getSW46Ticker(ticker: string): Promise<SW46TickerResult> {
  return fetchJson(`${BASE}/research/ideas/sw46/${encodeURIComponent(ticker.toUpperCase())}`);
}

export function refreshSW46(opts: { historyYears?: number; maxWorkers?: number } = {}): Promise<{
  run_id: string;
  created_at: string;
  cohort_pooled_delta_e: number | null;
  ticker_count: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}> {
  const q = new URLSearchParams();
  if (opts.historyYears != null) q.set('history_years', String(opts.historyYears));
  if (opts.maxWorkers  != null) q.set('max_workers',   String(opts.maxWorkers));
  return fetchJson(`${BASE}/research/ideas/sw46/refresh?${q}`, { method: 'POST' });
}

export function listSW46Runs(limit = 20): Promise<{ runs: SW46RunHeader[] }> {
  return fetchJson(`${BASE}/research/ideas/sw46/runs?limit=${limit}`);
}

// ── HK50 ("Long China / HK") two-screener cohort ────────────────────────────
//
// One 50-name China/HK universe scored by TWO independent 0-100 screens
// (High Growth + High Dividend). A single payload carries both scores per row;
// the UI derives the Growth ranking, the Dividend ranking, and the top-5 of
// each client-side. IV15 is AICT-modulated only for software / internet names.

export interface HK50MetricResult {
  value: number | null;
  source: string;   // primary | yfinance | fmp_growth | compute | structural | missing
}

export interface HK50IV15Detail {
  aict: string;                     // "—" for non-software/internet names
  currency: string;
  haircut: number | null;
  terminal_multiple: number | null;
  blend_weight_a: number | null;
  base_eps: number | null;          // E0 fed to IV15 (Method-E adjusted for ADR/FMP names)
  e0_raw: number | null;            // E0 before the SBC retention haircut
  oe_retention: number | null;      // (N - C - max(0, SBC - B)) / N, clamped [0.30, 1.00]
  unfunded_comp: number | null;     // max(0, SBC - B), reporting currency
  sbc_pct_ni: number | null;        // SBC / NI
  is_net_diluter: boolean | null;   // buybacks < SBC
  oe_cash_tax_estimated: boolean | null;
  stage1_growth: number | null;
  iv_gordon: number | null;
  iv_buffett: number | null;
  iv15: number | null;
  price: number | null;
  p_iv15: number | null;
  valuation_points: number | null;
}

// Qualitative overlay (dimensions 1 Policy + 5 Moat) — PARALLEL to the two
// pure quant screens; never summed into growth_score / dividend_score. Filled
// by a curated seed at cohort-build time, then upgraded in place by the
// Phase-2 LLM deep-research background job (source flips curated → hybrid/llm).
export type HK50PolicyTier = 'Tailwind' | 'Favorable' | 'Neutral' | 'Headwind' | 'Crackdown' | '—';
export type HK50MoatTier   = 'Fortress' | 'Castle' | 'Chapel' | 'Stone' | 'Wood' | '—';
export type HK50Conviction =
  | 'HIGH-CONVICTION'
  | 'SOLID'
  | 'QUANT-RICH'      // great screen masking a policy/moat trap
  | 'QUAL-SUPPORT'
  | 'WATCH'
  | 'POLICY-RISK'     // active crackdown / Wood moat hard-override
  | 'UNSCORED';

export interface HK50QualDimension {
  dimension: 'policy' | 'moat';
  score_0_100: number | null;
  tier: string;                     // PolicyTier or MoatTier label
  n_scored: number;
  mean_confidence: number;
  summary: string;
  indicators: Record<string, QualIndicatorScore>;
}

export interface HK50Qualitative {
  hk_ticker: string;
  sector: string;
  policy: HK50QualDimension | null;
  moat: HK50QualDimension | null;
  conviction: HK50Conviction;
  source: 'unscored' | 'curated' | 'llm' | 'hybrid';
  flags: string[];
  assessed_at: string | null;
  cost_usd: number;
  incomplete: boolean;              // true while the LLM pass is mid-stream
}

export interface HK50TickerResult {
  ticker: string;                   // reported ticker (ADR or HK)
  hk_ticker: string;                // canonical HK ticker (or native ADR)
  name: string;
  route_label: string;              // "ADR/FMP" | "HK/AKShare+yf"
  currency: string;
  growth_score: number;
  dividend_score: number;
  lead: 'Growth' | 'Dividend';
  lead_score: number;               // max(growth_score, dividend_score) — ranks the pool
  aict_tier: string;
  price: number | null;
  iv15: number | null;
  p_iv15: number | null;
  metrics: Record<string, HK50MetricResult>;
  iv15_detail: HK50IV15Detail;
  growth_rank: number | null;
  dividend_rank: number | null;
  // Dynamic-universe membership (output of the screen, not hand-curated):
  in_cohort: boolean;               // true = inside the displayed top-50
  cohort_rank: number | null;       // 1..N within the displayed cohort (null on bench)
  membership: 'member' | 'promoted' | 'relegated' | 'bench';
  qualitative?: HK50Qualitative | null;
  error?: string | null;
}

export interface HK50CohortDelta {
  ticker: string;
  name: string;
  lead_score: number;
  reason?: string;                  // present on relegated entries
}

export interface HK50Cohort {
  run_id: string | null;
  created_at: string | null;
  ticker_count: number;             // = displayed_count (back-compat)
  avg_growth: number | null;
  avg_dividend: number | null;
  median_p_iv15: number | null;
  lead_growth_count: number;
  // Dynamic-universe membership summary:
  eligible_count: number;           // names scored this run (the full pool, ~100)
  displayed_count: number;          // names in the cohort (<= 50)
  enter_threshold: number;
  stay_threshold: number;
  promoted: HK50CohortDelta[];
  relegated: HK50CohortDelta[];
  failed_tickers: Array<{ ticker: string; reason: string }>;
  results: HK50TickerResult[];      // ALL scored names, ranked by lead_score desc
}

export interface HK50RunHeader {
  run_id: string;
  created_at: string;
  ticker_count: number;
  avg_growth: number | null;
  avg_dividend: number | null;
  median_p_iv15: number | null;
  lead_growth_count: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}

export function getHK50Cohort(): Promise<HK50Cohort> {
  return fetchJson(`${BASE}/research/ideas/hk50`);
}

export function getHK50Ticker(ticker: string): Promise<HK50TickerResult> {
  return fetchJson(`${BASE}/research/ideas/hk50/${encodeURIComponent(ticker.toUpperCase())}`);
}

export function refreshHK50(opts: { maxWorkers?: number } = {}): Promise<{
  run_id: string;
  created_at: string;
  ticker_count: number;
  avg_growth: number | null;
  avg_dividend: number | null;
  median_p_iv15: number | null;
  lead_growth_count: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}> {
  const q = new URLSearchParams();
  if (opts.maxWorkers != null) q.set('max_workers', String(opts.maxWorkers));
  return fetchJson(`${BASE}/research/ideas/hk50/refresh?${q}`, { method: 'POST' });
}

export function listHK50Runs(limit = 20): Promise<{ runs: HK50RunHeader[] }> {
  return fetchJson(`${BASE}/research/ideas/hk50/runs?limit=${limit}`);
}

// ── HK50 Phase-2 qualitative deep-research (background job) ──────────────────
//
// Kicks off the LLM Policy+Moat pass over the top-N names by lead screen score.
// The quant cards must already exist (run refreshHK50 first). Returns a job id;
// poll getHK50Job() until status is 'completed' | 'failed'. Rows are patched
// live in the cohort payload as each name's sub-metrics stream in, so a plain
// getHK50Cohort() re-fetch mid-job shows the overlay filling in.

export interface HK50QualJob {
  job_id: string;
  kind: string;
  ticker: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed';
  started_at: string | null;
  finished_at: string | null;
  progress_msg: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export function runHK50DeepResearch(
  opts: { topN?: number; forceRefresh?: boolean } = {},
): Promise<{ job_id: string; status: string; started_at: string | null; deduped: boolean }> {
  const q = new URLSearchParams();
  if (opts.topN != null) q.set('top_n', String(opts.topN));
  if (opts.forceRefresh != null) q.set('force_refresh', String(opts.forceRefresh));
  return fetchJson(`${BASE}/research/ideas/hk50/qual-deep-research?${q}`, { method: 'POST' });
}

export function getHK50Job(jobId: string): Promise<HK50QualJob> {
  return fetchJson(`${BASE}/research/ideas/hk50/jobs/${encodeURIComponent(jobId)}`);
}

// Manual Policy+Moat deep-research for ONE cohort name (drill-in drawer). Runs
// the same LLM pass as the batch, scoped to a single ticker, as a background
// job. Polls via getHK50Job(); the cohort row is patched live.
export function runHK50TickerQual(
  ticker: string,
  opts: { forceRefresh?: boolean } = {},
): Promise<{ job_id: string; status: string; started_at: string | null; deduped: boolean }> {
  const q = new URLSearchParams();
  if (opts.forceRefresh != null) q.set('force_refresh', String(opts.forceRefresh));
  const qs = q.toString();
  return fetchJson(
    `${BASE}/research/ideas/hk50/qual/${encodeURIComponent(ticker)}${qs ? `?${qs}` : ''}`,
    { method: 'POST' },
  );
}

// ── Complacency Detector (Ackman 4-pillar equity screener) ──────────────────

export type ComplacencyVerdict =
  | 'Strong-Short'
  | 'Watch'
  | 'Borderline'
  | 'Pass'
  | 'N/A';

export type QualConvictionLabel = 'EXCEPTIONAL' | 'BOTH' | 'QUANT-ONLY' | 'QUAL-ONLY' | 'PASS';

export interface QualEvidence {
  source: string;
  quote: string;
  date: string | null;
  url: string | null;
}

export interface QualIndicatorScore {
  indicator: string;
  score: number;            // 0-5
  confidence: number;       // 0-1
  summary: string;
  evidence: QualEvidence[];
  scored_at: string | null;
  model_used: string | null;
}

export interface QualitativeAssessment {
  indicators: Record<string, QualIndicatorScore>;
  composite: number;
  max_possible: number;
  composite_normalized: number;    // 0-1
  conviction_label: QualConvictionLabel;
  assessed_at: string | null;
  cost_usd: number;
  incomplete: boolean;
}

export interface PutRecommendation {
  strike: number;
  strike_pct_otm: number;       // negative; -0.12 == 12% OTM
  expiry: string;               // ISO yyyy-mm-dd
  days_to_expiry: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  implied_volatility: number | null;
  open_interest: number | null;
  volume: number | null;
  rationale: string;
  contract_symbol: string | null;
}

export interface ComplacencyTickerResult {
  ticker: string;
  name: string;
  sector: string | null;
  industry: string | null;
  price: number | null;
  market_cap: number | null;
  rank: number | null;
  // Pillar inputs
  ev_sales: number | null;
  ev_sales_sector_median: number | null;
  ev_sales_relative: number | null;
  fcf_yield_ttm: number | null;
  altman_z: number | null;
  piotroski: number | null;
  ad_ratio_4q_avg: number | null;
  eps_revision_yoy: number | null;
  sma200_extension: number | null;
  rsi_weekly: number | null;
  range_position: number | null;
  // Pillar scores (0-2)
  val_score: number;
  beh_score: number;
  tech_score: number;
  qual_score: number;
  composite: number;
  passes_gate: boolean;
  verdict: ComplacencyVerdict;
  flag_notes: string[];
  justification: string | null;
  put_recommendation: PutRecommendation | null;
  options_data_freshness: string | null;
  qualitative: QualitativeAssessment | null;
  aggregate_score: number | null;
  aggregate_quant_pts: number | null;
  aggregate_qual_pts: number | null;
  error?: string | null;
}

export interface ComplacencyCohort {
  run_id: string | null;
  created_at: string | null;
  universe: string;
  ticker_count: number;
  gate_passers: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
  results: ComplacencyTickerResult[];
}

export interface ComplacencyRunHeader {
  run_id: string;
  created_at: string;
  universe: string;
  ticker_count: number;
  gate_passers: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}

export function getComplacencyCohort(): Promise<ComplacencyCohort> {
  return fetchJson(`${BASE}/research/ideas/complacency`);
}

export function getComplacencyTicker(ticker: string): Promise<ComplacencyTickerResult> {
  return fetchJson(`${BASE}/research/ideas/complacency/${encodeURIComponent(ticker.toUpperCase())}`);
}

// ─── Async-job pattern for long-running Complacency ops ─────────────────
// Cohort refresh and force-qual re-scoring can take 5-10 minutes. iOS
// Safari kills synchronous fetches that long. So the backend now returns
// a job_id immediately; we poll for completion.

export interface ComplacencyJobHandle {
  job_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  started_at: string | null;
  deduped?: boolean;   // true if existing in-flight job was returned
}

export interface ComplacencyJobStatus {
  job_id: string;
  kind: 'refresh' | 'score_adhoc' | 'qual_sweep';
  ticker: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed';
  started_at: string;
  finished_at: string | null;
  progress_msg: string | null;
  result: unknown;       // refresh: cohort summary; score_adhoc: ComplacencyTickerResult
  error: string | null;
}

/**
 * Kick off ad-hoc scoring (with optional forceQual) as a background job.
 * Returns immediately with {job_id}; poll getComplacencyJob() to await result.
 *
 * Used for:
 *   1. Brand-new ticker not in the curated universe (full fresh score)
 *   2. Existing-cohort ticker with forceQual=true (re-runs qualitative,
 *      patches cohort row, recomputes aggregate)
 *
 * Takes ~10-15 sec quant-only; ~4-6 min with forceQual + deep research.
 */
export function startComplacencyAdhocScore(
  ticker: string,
  opts: { forceQual?: boolean } = {},
): Promise<ComplacencyJobHandle> {
  const q = new URLSearchParams();
  if (opts.forceQual) q.set('force_qual', 'true');
  const qs = q.toString() ? `?${q.toString()}` : '';
  return fetchJson(
    `${BASE}/research/ideas/complacency/score/${encodeURIComponent(ticker.toUpperCase())}${qs}`,
    { method: 'POST' },
  );
}

/** Kick off a full cohort refresh as a background job. */
export function startComplacencyRefresh(opts: { maxWorkers?: number } = {}): Promise<ComplacencyJobHandle> {
  const q = new URLSearchParams();
  if (opts.maxWorkers != null) q.set('max_workers', String(opts.maxWorkers));
  return fetchJson(
    `${BASE}/research/ideas/complacency/refresh?${q}`,
    { method: 'POST' },
  );
}

/**
 * Kick off an on-demand qualitative sweep of the FULL 50-ticker universe
 * (gate-passers first). Cache-first: tickers whose 10 indicators are all
 * fresh + high-confidence are skipped unless force=true.
 */
export function startComplacencyQualSweep(opts: { force?: boolean } = {}): Promise<ComplacencyJobHandle> {
  const q = new URLSearchParams();
  if (opts.force) q.set('force', 'true');
  return fetchJson(
    `${BASE}/research/ideas/complacency/qual-sweep?${q}`,
    { method: 'POST' },
  );
}

/** Poll status for a background job (one shot). */
export function getComplacencyJob(jobId: string): Promise<ComplacencyJobStatus> {
  return fetchJson(`${BASE}/research/ideas/complacency/jobs/${encodeURIComponent(jobId)}`);
}

/**
 * Poll the job until it reaches a terminal state (completed | failed).
 *
 *   • Polls every `pollIntervalMs` (default 5s).
 *   • Calls `onProgress(status)` on every poll so the UI can update.
 *   • Tolerates transient network errors (logs but keeps polling) up to
 *     `maxFailures` consecutive (default 6 = ~30s downtime tolerance).
 *   • Hard-fails after `timeoutMs` total (default 15 min).
 *
 * iOS-friendly: when Safari kills the polling fetch on backgrounding,
 * the next successful poll picks up wherever the job actually is.
 */
export async function pollComplacencyJob(
  jobId: string,
  opts: {
    pollIntervalMs?: number;
    timeoutMs?: number;
    maxFailures?: number;
    onProgress?: (status: ComplacencyJobStatus) => void;
  } = {},
): Promise<ComplacencyJobStatus> {
  const pollIntervalMs = opts.pollIntervalMs ?? 5000;
  const timeoutMs = opts.timeoutMs ?? 15 * 60 * 1000;
  const maxFailures = opts.maxFailures ?? 6;
  const start = Date.now();
  let consecutiveFailures = 0;

  while (true) {
    if (Date.now() - start > timeoutMs) {
      throw new Error(`Job ${jobId} timed out after ${Math.round(timeoutMs / 1000)}s of polling`);
    }

    // ── Fetch the current status (network errors retry, job-result errors propagate) ──
    let status: ComplacencyJobStatus;
    try {
      status = await getComplacencyJob(jobId);
      consecutiveFailures = 0;
    } catch (e) {
      consecutiveFailures += 1;
      if (consecutiveFailures > maxFailures) {
        throw new Error(
          `Job ${jobId} polling failed ${consecutiveFailures} times in a row — giving up. Last error: ${(e as Error).message}`,
        );
      }
      // Transient network error — wait and retry on next tick
      await new Promise((r) => setTimeout(r, pollIntervalMs));
      continue;
    }

    // Report progress on every successful poll
    opts.onProgress?.(status);

    // Terminal states — propagate failures DIRECTLY (don't bury them in the
    // network-error retry loop, which the original implementation did and
    // surfaced legitimate job failures as misleading "polling failed N times".)
    if (status.status === 'completed') return status;
    if (status.status === 'failed') {
      throw new Error(status.error || `Job ${jobId} failed (no error message)`);
    }

    // Still pending / running — wait then poll again
    await new Promise((r) => setTimeout(r, pollIntervalMs));
  }
}

// ─── Legacy compatibility shims ─────────────────────────────────────────
// Kept so existing callers that expect a promise-of-result still work; they
// now go through the async-job pattern internally.

export async function scoreComplacencyTickerAdhoc(
  ticker: string,
  opts: { forceQual?: boolean; onProgress?: (s: ComplacencyJobStatus) => void } = {},
): Promise<ComplacencyTickerResult & { _persisted_to_cohort?: boolean }> {
  const handle = await startComplacencyAdhocScore(ticker, { forceQual: opts.forceQual });
  // Force-qual with deep-research escalation can take 10-15+ min in the worst
  // case (4-5 indicators each running 60-90s of Qwen web search). Bump the
  // poll timeout from the 15-min default to 25 min when forceQual is set.
  const timeoutMs = opts.forceQual ? 25 * 60 * 1000 : 15 * 60 * 1000;
  const final = await pollComplacencyJob(handle.job_id, {
    onProgress: opts.onProgress,
    timeoutMs,
  });
  return final.result as ComplacencyTickerResult & { _persisted_to_cohort?: boolean };
}

export async function refreshComplacency(opts: {
  maxWorkers?: number;
  onProgress?: (s: ComplacencyJobStatus) => void;
} = {}): Promise<{
  run_id: string;
  created_at: string;
  universe: string;
  ticker_count: number;
  gate_passers: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}> {
  const handle = await startComplacencyRefresh({ maxWorkers: opts.maxWorkers });
  const final = await pollComplacencyJob(handle.job_id, { onProgress: opts.onProgress });
  return final.result as {
    run_id: string;
    created_at: string;
    universe: string;
    ticker_count: number;
    gate_passers: number;
    failed_tickers: Array<{ ticker: string; reason: string }>;
  };
}

/**
 * On-demand qualitative sweep of the full universe (gate-passers first).
 * The 50-ticker sweep can run 30-60+ min (3-ticker parallelism, ~1-3 min
 * per scored ticker), so the poll timeout is 90 min — well above the 15-min
 * default. Heartbeats arrive via onProgress from the job's progress_msg.
 */
export async function sweepComplacency(opts: {
  force?: boolean;
  onProgress?: (s: ComplacencyJobStatus) => void;
} = {}): Promise<{
  universe: number;
  scored: number;
  fresh_skipped: number;
  failed: Array<{ ticker: string; reason: string }>;
  force: boolean;
  gate_passers_first: string[];
}> {
  const handle = await startComplacencyQualSweep({ force: opts.force });
  const final = await pollComplacencyJob(handle.job_id, {
    onProgress: opts.onProgress,
    timeoutMs: 90 * 60 * 1000,
  });
  return final.result as {
    universe: number;
    scored: number;
    fresh_skipped: number;
    failed: Array<{ ticker: string; reason: string }>;
    force: boolean;
    gate_passers_first: string[];
  };
}

export function listComplacencyRuns(limit = 20): Promise<{ runs: ComplacencyRunHeader[] }> {
  return fetchJson(`${BASE}/research/ideas/complacency/runs?limit=${limit}`);
}


// ── Momentum (turning & accelerating, long + short) ─────────────────────────
//
// Two-layer signed-momentum screen. The SAME scorer runs on sector ETFs and
// tickers, producing three signed pillars (STATE / TURN / ACCELERATION) that
// sum to a -6..+6 composite. Positive => long, negative => short. The ticker
// layer carries a sector-alignment overlay flagging the highest-conviction
// names (direction agrees with its sector ETF).

export type MomentumVerdict =
  | 'Accelerating-Long'
  | 'Turning-Long'
  | 'Neutral'
  | 'Turning-Short'
  | 'Accelerating-Short';

export type MomentumDirection = 'LONG' | 'SHORT' | 'NEUTRAL';

// Shared scored shape (returned by score_series for both sectors and tickers).
export interface MomentumScore {
  r_5d: number | null;
  r_21d: number | null;
  r_63d: number | null;
  r_126d: number | null;
  r_252d: number | null;
  r_12_1: number | null;
  ma_stack: number;                 // +1 / 0 / -1
  macd_hist: number | null;
  rsi: number | null;
  adx: number | null;
  accel_roc: number | null;
  return_slope: number | null;
  volume_ratio: number | null;
  days_since_turn: number | null;
  state_score: number;
  turn_score: number;
  accel_score: number;
  composite: number;                // -6..+6
  signal_strength: number;          // 0-100 = |composite|/6
  verdict: MomentumVerdict;
  direction: MomentumDirection;
  passes_gate: boolean;
  flag_notes: string[];
  justification: string | null;
}

export interface MomentumSectorResult extends MomentumScore {
  etf: string;
  sector: string | null;
  label: string | null;
  group: string | null;             // "macro" | "thematic"
  bars: number;
  composite_1m: number | null;      // composite ~1 month ago (rank a month ago)
  composite_3m: number | null;      // composite ~3 months ago (rank a quarter ago)
}

export interface MomentumTickerResult extends MomentumScore {
  ticker: string;
  name: string;
  sector: string | null;
  sector_etf: string | null;
  price: number | null;
  rank: number | null;
  bars: number;
  sector_aligned: boolean | null;
  sector_direction: MomentumDirection | null;
  sector_composite: number | null;
  error?: string | null;
}

export interface MomentumCohort {
  run_id: string | null;
  created_at: string | null;
  as_of: string | null;
  universe: string;
  ticker_count: number;
  long_count: number;
  short_count: number;
  sectors: MomentumSectorResult[];
  failed_tickers: Array<{ ticker: string; reason: string }>;
  results: MomentumTickerResult[];
}

export interface MomentumRunHeader {
  run_id: string;
  created_at: string;
  as_of: string | null;
  universe: string;
  ticker_count: number;
  long_count: number;
  short_count: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}

export function getMomentumCohort(): Promise<MomentumCohort> {
  return fetchJson(`${BASE}/research/ideas/momentum`);
}

export function getMomentumTicker(ticker: string): Promise<MomentumTickerResult> {
  return fetchJson(`${BASE}/research/ideas/momentum/${encodeURIComponent(ticker.toUpperCase())}`);
}

export function getMomentumSectors(): Promise<{
  as_of: string | null;
  created_at: string | null;
  sectors: MomentumSectorResult[];
}> {
  return fetchJson(`${BASE}/research/ideas/momentum/sectors`);
}

/**
 * Trigger a fresh momentum cohort run. Synchronous (returns once persisted) —
 * no LLM calls, so a full ~75-ticker + sector run completes in well under a
 * minute. `asOf` (ISO yyyy-mm-dd) points the screen at a historical date for
 * validation (e.g. inside the software sell-down window).
 */
export function refreshMomentum(opts: { asOf?: string; maxWorkers?: number } = {}): Promise<{
  run_id: string;
  created_at: string;
  as_of: string | null;
  ticker_count: number;
  long_count: number;
  short_count: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}> {
  const q = new URLSearchParams();
  if (opts.asOf) q.set('as_of', opts.asOf);
  if (opts.maxWorkers != null) q.set('max_workers', String(opts.maxWorkers));
  const qs = q.toString();
  return fetchJson(`${BASE}/research/ideas/momentum/refresh${qs ? `?${qs}` : ''}`, { method: 'POST' });
}

export function listMomentumRuns(limit = 20): Promise<{ runs: MomentumRunHeader[] }> {
  return fetchJson(`${BASE}/research/ideas/momentum/runs?limit=${limit}`);
}


// ── Fund Flow (geographic rotation) ─────────────────────────────────────────
//
// The same three-pillar signed engine as the sector momentum screen, applied
// to MONEY instead of price. Two quantities travel together and must not be
// confused in the UI:
//
//   cmf_z_21 / rel_flow_z — tape-derived flow pressure, in SIGMA off a
//     region's own baseline. Present for every region every day. Never render
//     these as dollars or as a percentage of assets.
//   implied_flow_* — measured issuer creation/redemption, in DOLLARS. Real
//     money, but null wherever the share-count feed is stale, which is why it
//     corroborates the composite rather than driving it.

export type FlowVerdict =
  | 'Accelerating-Inflow'
  | 'Turning-Inflow'
  | 'Neutral'
  | 'Turning-Outflow'
  | 'Accelerating-Outflow';

export type FlowDirection = 'INFLOW' | 'OUTFLOW' | 'NEUTRAL';

export type ImpliedQuality = 'good' | 'partial' | 'stale' | 'none';

export interface FlowSparkPoint {
  d: string;                          // ISO date
  v: number;                          // flow pressure, sigma
}

export interface FundFlowRegionResult {
  region: string;                     // "JP"
  label: string;                      // "Japan"
  emoji: string | null;
  bloc: string | null;                // "Developed" | "Asia EM" | "Benchmark"
  etf: string;
  basket: string[];
  is_benchmark: boolean;
  bars: number;

  aum: number | null;
  tape_flow_5d: number | null;        // signed accumulation dollars (NOT flow)
  tape_flow_21d: number | null;
  tape_flow_63d: number | null;
  tape_flow_126d: number | null;
  tape_flow_252d: number | null;
  avg_daily_turnover: number | null;

  // The composite is scored on the 1-month window; 3/6/12-month reads sit
  // alongside so a month can be judged against the longer trend.
  cmf_21: number | null;              // -1..+1 raw
  cmf_5: number | null;
  cmf_63: number | null;
  cmf_126: number | null;
  cmf_252: number | null;
  cmf_z_21: number | null;            // sigma off own baseline
  cmf_z_63: number | null;
  cmf_z_126: number | null;
  cmf_z_252: number | null;
  cmf_z_delta_21: number | null;
  mfi_14: number | null;              // 0..100
  flow_breadth_21: number | null;     // 0..1
  turnover_surge: number | null;      // 21d / 63d turnover
  flow_z_21: number | null;
  flow_slope_21: number | null;
  days_since_turn: number | null;

  pressure_score: number;             // -2..+2
  turn_score: number;
  accel_score: number;
  composite: number;                  // -6..+6
  signal_strength: number;            // 0-100
  composite_1m: number | null;        // where the composite stood N ago
  composite_3m: number | null;
  composite_6m: number | null;
  composite_12m: number | null;

  verdict: FlowVerdict;
  direction: FlowDirection;
  passes_gate: boolean;

  rel_flow_z: number | null;          // own pressure - world pressure, sigma
  rel_flow_z_1m: number | null;
  rel_flow_z_delta: number | null;    // the rotation axis

  price_composite: number | null;     // -6..+6 from the momentum engine
  price_verdict: string | null;
  r_21d: number | null;
  r_63d: number | null;
  r_126d: number | null;
  r_252d: number | null;
  fx_drag_21d: number | null;         // unhedged 1m return - hedged 1m return
  divergence: string | null;          // confirming | flow-leads | price-leads

  implied_flow_21d: number | null;    // measured USD
  implied_flow_63d: number | null;
  implied_flow_126d: number | null;
  implied_flow_252d: number | null;
  implied_flow_21d_pct_aum: number | null;
  implied_flow_63d_pct_aum: number | null;
  implied_flow_126d_pct_aum: number | null;
  implied_flow_252d_pct_aum: number | null;
  implied_quality: ImpliedQuality;

  spark: FlowSparkPoint[];
  flag_notes: string[];
  justification: string | null;
  data_notes: string[];
}

export interface FundFlowSummary {
  headline: string;
  regime: string;
  net_implied_flow_21d: number | null;
  implied_coverage: number;
  inflow_count: number;
  outflow_count: number;
  key_flows: string[];
  key_changes: string[];
  implications: string[];
  watch_items: string[];
  summary_source: 'deepseek' | 'deterministic';
  model_used: string | null;
}

export interface FundFlowCohort {
  run_id: string | null;
  created_at: string | null;
  as_of: string | null;
  universe: string;
  region_count: number;
  inflow_count: number;
  outflow_count: number;
  summary: FundFlowSummary | null;
  regions: FundFlowRegionResult[];
  benchmarks: FundFlowRegionResult[];
  failed_regions: Array<{ region: string; reason: string }>;
}

export interface FundFlowRunHeader {
  run_id: string;
  created_at: string;
  as_of: string | null;
  universe: string;
  region_count: number;
  inflow_count: number;
  outflow_count: number;
  failed_regions: Array<{ region: string; reason: string }>;
}

export function getFundFlowCohort(): Promise<FundFlowCohort> {
  return fetchJson(`${BASE}/research/ideas/fundflow`);
}

export function getFundFlowRegion(region: string): Promise<FundFlowRegionResult> {
  return fetchJson(`${BASE}/research/ideas/fundflow/${encodeURIComponent(region.toUpperCase())}`);
}

export function getFundFlowSummary(): Promise<{
  as_of: string | null;
  created_at: string | null;
  summary: FundFlowSummary | null;
}> {
  return fetchJson(`${BASE}/research/ideas/fundflow/summary`);
}

/**
 * Trigger a fresh geographic fund-flow run. Synchronous — ~27 ETFs plus one
 * DeepSeek summary call, around a minute end to end. `narrate: false` skips
 * the model and keeps the deterministic summary.
 */
export function refreshFundFlow(opts: {
  asOf?: string; maxWorkers?: number; narrate?: boolean;
} = {}): Promise<{
  run_id: string;
  created_at: string;
  as_of: string | null;
  region_count: number;
  inflow_count: number;
  outflow_count: number;
  summary_source: string | null;
  failed_regions: Array<{ region: string; reason: string }>;
}> {
  const q = new URLSearchParams();
  if (opts.asOf) q.set('as_of', opts.asOf);
  if (opts.maxWorkers != null) q.set('max_workers', String(opts.maxWorkers));
  if (opts.narrate === false) q.set('narrate', 'false');
  const qs = q.toString();
  return fetchJson(`${BASE}/research/ideas/fundflow/refresh${qs ? `?${qs}` : ''}`, { method: 'POST' });
}

export function listFundFlowRuns(limit = 20): Promise<{ runs: FundFlowRunHeader[] }> {
  return fetchJson(`${BASE}/research/ideas/fundflow/runs?limit=${limit}`);
}


// ── Research Idea of the Day (contrarian deep-value) ─────────────────────

export interface ContrarianSource {
  title: string;
  url: string | null;
  date: string | null;
}

export type ContrarianIdeaMode =
  | 'deep_value'
  | 'thematic_geographic'
  | 'thematic_sector'
  | 'special_situation';

export interface ContrarianIdea {
  idea_id: string;
  ticker: string;
  company_name: string;
  sector: string | null;
  industry: string | null;
  market_cap_usd: number | null;
  // Thematic / methodology fields (new — backwards-compatible)
  idea_mode?: ContrarianIdeaMode | null;
  theme?: string | null;
  region?: string | null;
  industry_theme?: string | null;
  expression_vehicle?: 'stock' | 'adr' | 'etf' | string | null;
  // Core thesis fields
  hypothesis: string;
  deep_value_angle: string;
  asymmetric_angle: string;
  contrarian_angle: string;
  primary_catalyst: string;
  catalyst_timeline: string | null;
  key_risks: string[];
  conviction_score: number;
  deep_value_score: number;
  asymmetry_score: number;
  contrarian_score: number;
  sources: ContrarianSource[];
  generated_at: string;
  model_used: string;
  cost_usd: number | null;
  _shortlisted?: boolean;
  _deleted_at?: string | null;
}

export interface ContrarianChatMessage {
  message_id: string;
  idea_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  cost_usd: number | null;
}

export interface ContrarianShortlistEntry {
  idea_id: string;
  shortlisted_at: string;
  user_note: string | null;
  idea_snapshot: ContrarianIdea;
}

export function getIdeaOfTheDay(): Promise<{ idea: ContrarianIdea | null }> {
  return fetchJson(`${BASE}/research/ideas/idea-of-the-day`);
}

export function getContrarianIdea(ideaId: string): Promise<ContrarianIdea> {
  return fetchJson(`${BASE}/research/ideas/idea-of-the-day/${encodeURIComponent(ideaId)}`);
}

export function listRecentContrarianIdeas(limit = 10): Promise<{ ideas: ContrarianIdea[] }> {
  return fetchJson(`${BASE}/research/ideas/idea-of-the-day/list?limit=${limit}`);
}

/**
 * Kick off a fresh idea generation as a background job. Returns immediately
 * with {job_id}; reuses the existing complacency_job_store + poll helper.
 */
export function startContrarianGeneration(
  opts: { mode?: ContrarianIdeaMode } = {},
): Promise<ComplacencyJobHandle> {
  const q = opts.mode ? `?mode=${encodeURIComponent(opts.mode)}` : '';
  return fetchJson(
    `${BASE}/research/ideas/idea-of-the-day/generate${q}`,
    { method: 'POST' },
  );
}

export async function generateContrarianIdea(opts: {
  onProgress?: (s: ComplacencyJobStatus) => void;
  mode?: ContrarianIdeaMode;
} = {}): Promise<ContrarianIdea> {
  const handle = await startContrarianGeneration({ mode: opts.mode });
  const final = await pollComplacencyJob(handle.job_id, { onProgress: opts.onProgress });
  return final.result as ContrarianIdea;
}

export function deleteContrarianIdea(ideaId: string): Promise<{ deleted: boolean; idea_id: string }> {
  return fetchJson(`${BASE}/research/ideas/idea-of-the-day/${encodeURIComponent(ideaId)}`, {
    method: 'DELETE',
  });
}

export function getContrarianChat(ideaId: string): Promise<{ idea_id: string; messages: ContrarianChatMessage[] }> {
  return fetchJson(`${BASE}/research/ideas/idea-of-the-day/${encodeURIComponent(ideaId)}/chat`);
}

export function postContrarianChat(
  ideaId: string,
  content: string,
): Promise<{ user_message: ContrarianChatMessage; assistant_message: ContrarianChatMessage }> {
  return fetchJson(
    `${BASE}/research/ideas/idea-of-the-day/${encodeURIComponent(ideaId)}/chat`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    },
  );
}

export function addContrarianToShortlist(
  ideaId: string,
  userNote?: string,
): Promise<ContrarianShortlistEntry> {
  return fetchJson(
    `${BASE}/research/ideas/idea-of-the-day/${encodeURIComponent(ideaId)}/shortlist`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_note: userNote ?? null }),
    },
  );
}

export function listContrarianShortlist(limit = 50): Promise<{ shortlist: ContrarianShortlistEntry[] }> {
  return fetchJson(`${BASE}/research/ideas/idea-of-the-day/shortlist/all?limit=${limit}`);
}

export function removeContrarianFromShortlist(ideaId: string): Promise<{ removed: boolean; idea_id: string }> {
  return fetchJson(
    `${BASE}/research/ideas/idea-of-the-day/shortlist/${encodeURIComponent(ideaId)}`,
    { method: 'DELETE' },
  );
}

// ── Robo Strategy (deterministic asset-allocation recommender) ──────────────
// Field naming mirrors the backend exactly: snake_case at the response
// envelope (etf_portfolio, total_investment, ...) and camelCase on each
// holding item (allocationPercent, expenseRatio, ...) — the backend's own
// mixed casing, ported straight from the "Robo Strategy" prototype's TS
// engine field names, not a frontend-side translation layer.

export type RoboRiskTolerance = 'conservative' | 'moderate' | 'aggressive';
export type RoboTimeHorizon = '<3 years' | '3-7 years' | '7-15 years' | '15+ years';

/** Canonical 11-sector taxonomy, matching the Screener's US sector field
 * values exactly so sector weights apply consistently to live candidates. */
export const ROBO_SECTORS = [
  'Technology', 'Healthcare', 'Consumer Cyclical', 'Financial Services',
  'Communication Services', 'Consumer Defensive', 'Energy', 'Industrials',
  'Basic Materials', 'Real Estate', 'Utilities',
] as const;

// 4-way geography taxonomy: US / Europe / Asia-Pacific / Emerging Markets.
// Hong Kong is deliberately grouped under Emerging Markets (with China),
// not Asia-Pacific — in this app HK exposure is a Greater-China proxy, so
// it shares the lever a user actually reaches for to dial up China/HK.
export const ROBO_REGIONS = ['US', 'Europe', 'Asia-Pacific', 'Emerging Markets'] as const;

export interface RoboQuestionnaire {
  risk_tolerance: RoboRiskTolerance;
  time_horizon: RoboTimeHorizon;
  sector_preferences: Record<string, number>;
  geography_preferences: Record<string, number>;
  investment_amount: number;
}

export interface RoboTopHolding {
  asset: string | null;
  name: string | null;
  weightPercentage: number;
}

export interface RoboHolding {
  ticker: string;
  name: string;
  category?: string | null;
  sector: string | null;
  region: string | null;
  allocationPercent: number;
  amount: number;
  shares: number;
  price: number;
  expenseRatio?: number | null;
  riskLevel?: string | null;
  topHoldings?: RoboTopHolding[];
}

export interface RoboBreakdowns {
  sector: Record<string, number>;
  geography: Record<string, number>;
  risk: Record<string, number>;
}

export interface RoboPortfolio {
  etf_portfolio: RoboHolding[];
  stock_portfolio: RoboHolding[];
  etf_breakdowns: RoboBreakdowns;
  stock_breakdowns: RoboBreakdowns;
  total_investment: number;
  generated_at: string;
}

export interface RoboProfileResponse {
  answers: RoboQuestionnaire | null;
  portfolio: RoboPortfolio | null;
}

/** Load the logged-in user's saved questionnaire draft + last-generated
 * portfolio, if any (both null for a first-time user). */
export function getRoboProfile(): Promise<RoboProfileResponse> {
  return fetchJson(`${BASE}/robo-strategy/profile`, { headers: _authHeaders() });
}

/** Generate (or regenerate) a recommended portfolio from questionnaire
 * answers. Synchronous — no job/poll needed, this is arithmetic over an
 * already-cached/live-fetched universe, not a multi-minute LLM job. Also
 * persists the answers + result server-side for the logged-in user. */
export function generateRoboPortfolio(answers: RoboQuestionnaire): Promise<RoboPortfolio> {
  return fetchJson(`${BASE}/robo-strategy/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify(answers),
  });
}

// ── Chat (per-ticker discussion) endpoints ──────────────────────────────────

export interface ChatMessage {
  id: number;
  ticker: string;
  user_id: number;
  author_name: string;
  content: string;
  created_at: string;
  edited_at: string | null;
  is_deleted: boolean;
  parent_message_id: number | null;
  reply_count: number;
  like_count: number;
  liked_by_me: boolean;
}

export interface ChatMessageListResponse {
  messages: ChatMessage[];
  has_more: boolean;
}

/** Fetch a page of top-level messages for a ticker's thread, newest-first
 * page (returned oldest-first for display) via `beforeId`, or everything
 * newer than `sinceId` for polling. */
export function getChatMessages(
  ticker: string,
  params?: { limit?: number; beforeId?: number; sinceId?: number },
): Promise<ChatMessageListResponse> {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set('limit', String(params.limit));
  if (params?.beforeId != null) qs.set('before_id', String(params.beforeId));
  if (params?.sinceId != null) qs.set('since_id', String(params.sinceId));
  const query = qs.toString();
  return fetchJson(
    `${BASE}/chat/${encodeURIComponent(ticker)}/messages${query ? `?${query}` : ''}`,
    { headers: _authHeaders() },
  );
}

/** Fetch all replies (one level) to a top-level message. */
export function getChatReplies(ticker: string, messageId: number): Promise<ChatMessage[]> {
  return fetchJson(
    `${BASE}/chat/${encodeURIComponent(ticker)}/messages/${messageId}/replies`,
    { headers: _authHeaders() },
  );
}

/** Post a new top-level message to a ticker's thread. */
export function postChatMessage(ticker: string, content: string): Promise<ChatMessage> {
  return fetchJson(`${BASE}/chat/${encodeURIComponent(ticker)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify({ content }),
  });
}

/** Reply to a top-level message (server rejects replying to a reply). */
export function postChatReply(ticker: string, messageId: number, content: string): Promise<ChatMessage> {
  return fetchJson(`${BASE}/chat/${encodeURIComponent(ticker)}/messages/${messageId}/replies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify({ content }),
  });
}

/** Soft-delete a message (author-only; server enforces). */
export function deleteChatMessage(ticker: string, messageId: number): Promise<{ deleted: boolean }> {
  return fetchJson(`${BASE}/chat/${encodeURIComponent(ticker)}/messages/${messageId}`, {
    method: 'DELETE',
    headers: _authHeaders(),
  });
}

/** Toggle the current user's like on a message. */
export function toggleChatLike(ticker: string, messageId: number): Promise<{ liked: boolean; like_count: number }> {
  return fetchJson(`${BASE}/chat/${encodeURIComponent(ticker)}/messages/${messageId}/like`, {
    method: 'POST',
    headers: _authHeaders(),
  });
}

export interface ChatActiveTicker {
  ticker: string;
  message_count: number;
  last_activity_at: string;
}

/** Tickers with at least one message, ranked by most recent activity —
 * powers the Discuss landing page's active-discussions list. */
export function getActiveChatTickers(limit = 12): Promise<ChatActiveTicker[]> {
  return fetchJson(`${BASE}/chat/active-tickers?limit=${limit}`, { headers: _authHeaders() });
}


// ── 100-Question Screener (FengHe-style quant+qual funnel) ─────────────────
//
// Composite is a flat aggregation (yes / answered) across ALL answered
// questions, quant + qual combined. Tiers: Active Pass >=65%, On-Deck
// 55-64%, Cool-off <55% (180-day lockout in a later phase).

export type HundredQTier = 'active_pass' | 'on_deck' | 'cooloff' | 'not_evaluated';

export interface HundredQEvidence {
  source: string;
  quote: string;
  date: string | null;
  url: string | null;
}

export interface HundredQQuestionAnswer {
  question_id: string;
  pillar: string;
  label: string;
  q_type: 'quant' | 'qual';
  answer: boolean | null;      // null = data_unavailable, excluded from denominator
  raw_value: string | null;
  threshold_desc: string | null;
  source: string | null;
  evaluated_at: string | null;
  confidence: number | null;
  evidence: HundredQEvidence[];
}

export interface HundredQPillarScore {
  pillar: string;
  label: string;
  questions_answered: number;
  questions_yes: number;
  pillar_pct: number | null;
}

export interface HundredQTickerResult {
  ticker: string;
  name: string;
  sector: string | null;
  industry: string | null;
  price: number | null;
  market_cap: number | null;
  question_ledger: HundredQQuestionAnswer[];
  pillar_scores: HundredQPillarScore[];
  quant_composite_pct: number | null;
  qual_composite_pct: number | null;
  composite_pct: number | null;
  tier: HundredQTier;
  rank: number | null;
  evaluated_at: string | null;
  error: string | null;
}

export interface HundredQWatchlistRow {
  ticker: string;
  company_name: string | null;
  sector: string | null;
  industry: string | null;
  tier: HundredQTier;
  composite_pct: number | null;
  quant_composite_pct: number | null;
  qual_composite_pct: number | null;
  entered_tier_at: string | null;
  cooloff_until: string | null;
  last_quant_run_at: string | null;
  last_qual_run_at: string | null;
  last_full_eval_at: string | null;
  run_id: string | null;
}

export interface HundredQCohortSummary {
  ticker_count: number;
  tier_counts: Partial<Record<HundredQTier, number>>;
  latest_run: {
    run_id: string;
    created_at: string;
    run_type: string;
    trigger_ticker: string | null;
    trigger_type: string | null;
    ticker_count: number;
    finished_at: string | null;
  } | null;
}

export interface HundredQTierHistoryRow {
  id: number;
  ticker: string;
  from_tier: string | null;
  to_tier: string;
  composite_pct: number | null;
  changed_at: string;
  run_id: string;
  reason: string | null;
}

export interface HundredQJobStatus {
  job_id: string;
  kind: string;
  ticker: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed';
  started_at: string;
  finished_at: string | null;
  progress_msg: string | null;
  result: { run_id: string; ticker_count: number; tier_counts: Record<string, number>; failed_tickers: unknown[] } | null;
  error: string | null;
}

export function getHundredQCohort(): Promise<HundredQCohortSummary> {
  return fetchJson(`${BASE}/research/ideas/hundred-q`);
}

export function getHundredQTier(tier: HundredQTier): Promise<{ tier: HundredQTier; tickers: HundredQWatchlistRow[] }> {
  return fetchJson(`${BASE}/research/ideas/hundred-q/tiers/${tier}`);
}

export function getHundredQTicker(ticker: string): Promise<HundredQTickerResult> {
  return fetchJson(`${BASE}/research/ideas/hundred-q/${encodeURIComponent(ticker.toUpperCase())}`);
}

export function getHundredQTickerHistory(ticker: string): Promise<{ ticker: string; history: HundredQTierHistoryRow[] }> {
  return fetchJson(`${BASE}/research/ideas/hundred-q/${encodeURIComponent(ticker.toUpperCase())}/history`);
}

/** Kick off a full quant-batch refresh as a background job (returns immediately). */
export function refreshHundredQ(): Promise<{ job_id: string; status: string; resumed?: boolean }> {
  return fetchJson(`${BASE}/research/ideas/hundred-q/refresh`, { method: 'POST' });
}

export function getHundredQJob(jobId: string): Promise<HundredQJobStatus> {
  return fetchJson(`${BASE}/research/ideas/hundred-q/jobs/${encodeURIComponent(jobId)}`);
}

/** Poll a hundred_q job until it reaches a terminal state. Mirrors pollComplacencyJob. */
export async function pollHundredQJob(
  jobId: string,
  opts: { pollIntervalMs?: number; timeoutMs?: number; onProgress?: (s: HundredQJobStatus) => void } = {},
): Promise<HundredQJobStatus> {
  const pollIntervalMs = opts.pollIntervalMs ?? 5000;
  const timeoutMs = opts.timeoutMs ?? 15 * 60 * 1000;
  const start = Date.now();
  while (true) {
    if (Date.now() - start > timeoutMs) {
      throw new Error(`Job ${jobId} timed out after ${Math.round(timeoutMs / 1000)}s of polling`);
    }
    const status = await getHundredQJob(jobId);
    opts.onProgress?.(status);
    if (status.status === 'completed' || status.status === 'failed') return status;
    await new Promise((r) => setTimeout(r, pollIntervalMs));
  }
}

/** Ad-hoc single-ticker rescore (UI "Force rescore" button). forceQual=true
 * re-scores every registered qualitative question, not just cache-stale ones. */
export function rescoreHundredQTicker(ticker: string, opts: { forceQual?: boolean } = {}): Promise<HundredQTickerResult> {
  const q = new URLSearchParams();
  if (opts.forceQual) q.set('force_qual', 'true');
  const qs = q.toString() ? `?${q.toString()}` : '';
  return fetchJson(`${BASE}/research/ideas/hundred-q/${encodeURIComponent(ticker.toUpperCase())}/rescore${qs}`, { method: 'POST' });
}
