// ── API client for the analysis service ────────────────────────────────────
import type {
  ArchiveSummary,
  DdAlert,
  DdDigest,
  DdDirection,
  DdPerformance,
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
  agents?: string[],
): Promise<Response> {
  return fetch(`${BASE}/analysis/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify({ ticker, model, agents: agents && agents.length > 0 ? agents : undefined }),
  });
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

/** Fetch 1-year price history and key financial metrics for a ticker. */
export function getStockData(ticker: string, period = '1y'): Promise<{
  ticker: string;
  history: { date: string; close: number }[];
  metrics: {
    market_cap?: number;
    revenue?: number;
    net_income?: number;
    profit_margin?: number;
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

// ── DD Alerts (Auto Due-D dashboard) ───────────────────────────────────────

/** List recent DD alerts. All filters optional. */
export function listDdAlerts(params: {
  since?:     string;
  until?:     string;
  direction?: DdDirection;
  tier?:      string;
  ticker?:    string;
  limit?:     number;
} = {}): Promise<DdAlert[]> {
  const qs = new URLSearchParams();
  if (params.since)     qs.set('since', params.since);
  if (params.until)     qs.set('until', params.until);
  if (params.direction) qs.set('direction', params.direction);
  if (params.tier)      qs.set('tier', params.tier);
  if (params.ticker)    qs.set('ticker', params.ticker);
  if (params.limit)     qs.set('limit', String(params.limit));
  const q = qs.toString();
  return fetchJson<DdAlert[]>(`${BASE}/api/dd-alerts${q ? `?${q}` : ''}`, { headers: _authHeaders() });
}

/** Today's aggregate digest — top drops, top pumps, active sector clusters. */
export function getDdDigestToday(): Promise<DdDigest> {
  return fetchJson<DdDigest>(`${BASE}/api/dd-alerts/digest/today`, { headers: _authHeaders() });
}

/** Single full DD report (hydrated from web_runs). */
export function getDdAlertDetail(runId: string): Promise<DdAlert> {
  return fetchJson<DdAlert>(`${BASE}/api/dd-alerts/${encodeURIComponent(runId)}`, { headers: _authHeaders() });
}

/** Phase 3 attribution: aggregate hit rates + alpha-vs-naive. */
export function getDdPerformance(params: { since?: string; until?: string } = {}): Promise<DdPerformance> {
  const qs = new URLSearchParams();
  if (params.since) qs.set('since', params.since);
  if (params.until) qs.set('until', params.until);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return fetchJson<DdPerformance>(`${BASE}/api/dd-alerts/performance${suffix}`, { headers: _authHeaders() });
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

// ── Complacency Detector (Ackman 4-pillar equity screener) ──────────────────

export type ComplacencyVerdict =
  | 'Strong-Short'
  | 'Watch'
  | 'Borderline'
  | 'Pass'
  | 'N/A';

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

export function refreshComplacency(opts: { maxWorkers?: number } = {}): Promise<{
  run_id: string;
  created_at: string;
  universe: string;
  ticker_count: number;
  gate_passers: number;
  failed_tickers: Array<{ ticker: string; reason: string }>;
}> {
  const q = new URLSearchParams();
  if (opts.maxWorkers != null) q.set('max_workers', String(opts.maxWorkers));
  return fetchJson(`${BASE}/research/ideas/complacency/refresh?${q}`, { method: 'POST' });
}

export function listComplacencyRuns(limit = 20): Promise<{ runs: ComplacencyRunHeader[] }> {
  return fetchJson(`${BASE}/research/ideas/complacency/runs?limit=${limit}`);
}
