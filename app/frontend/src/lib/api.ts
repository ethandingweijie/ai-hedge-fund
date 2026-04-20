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
