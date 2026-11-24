/**
 * Stock chart timeframes, key-statistic definitions and formatters.
 *
 * Shared by the old StockPanel card and the desktop ReportHero. Kept out of
 * the component files so React fast refresh keeps working.
 */

export interface StockData {
  history: { date: string; close: number }[];
  metrics: {
    market_cap?:                  number;
    revenue?:                     number;
    free_cash_flow?:              number;
    net_margin?:                  number;
    pe_ratio?:                    number;
    price_to_sales?:              number;
    price_to_book?:               number;
    ev_to_ebitda?:                number;
    revenue_growth?:              number;
    eps_ttm?:                     number;
    return_on_equity?:            number;
    return_on_invested_capital?:  number;
    return_on_assets?:            number;
    free_cash_flow_yield?:        number;
    dividend_yield?:              number;
    net_cash?:                    number;
    fifty_two_week_high?:         number;
    fifty_two_week_low?:          number;
    day_high?:                    number;
    day_low?:                     number;
    volume?:                      number;
  };
}

// ── Timeframe config ──────────────────────────────────────────────────────────
export const TIMEFRAMES: { label: string; period: string; dateFormat: Intl.DateTimeFormatOptions; interval: (n: number) => number }[] = [
  {
    label: '1D',
    period: '1d',
    dateFormat: { hour: '2-digit', minute: '2-digit' },
    interval: (n) => Math.max(1, Math.floor(n / 4)),
  },
  {
    label: '1W',
    period: '5d',
    dateFormat: { weekday: 'short', month: 'short', day: 'numeric' },
    interval: (n) => Math.max(1, Math.floor(n / 5)),
  },
  {
    label: '1M',
    period: '1mo',
    dateFormat: { month: 'short', day: 'numeric' },
    interval: (n) => Math.max(1, Math.floor(n / 4)),
  },
  {
    label: '3M',
    period: '3mo',
    dateFormat: { month: 'short', day: 'numeric' },
    interval: (n) => Math.max(1, Math.floor(n / 6)),
  },
  {
    label: '1Y',
    period: '1y',
    dateFormat: { month: 'short', year: '2-digit' },
    interval: (n) => Math.floor(n / 4),
  },
  {
    label: '3Y',
    period: '3y',
    dateFormat: { month: 'short', year: '2-digit' },
    interval: (n) => Math.floor(n / 6),
  },
  {
    label: '5Y',
    period: '5y',
    dateFormat: { month: 'short', year: '2-digit' },
    interval: (n) => Math.floor(n / 5),
  },
];

export function makeFmtLarge(sym: string) {
  return (v: number | undefined): string => {
    if (v == null) return '—';
    // Sign before the currency symbol: "−S$630.75M", never "S$-630.75M".
    const sign = v < 0 ? '−' : '';
    const abs = Math.abs(v);
    if (abs >= 1e12) return `${sign}${sym}${(abs / 1e12).toFixed(2)}T`;
    if (abs >= 1e9)  return `${sign}${sym}${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6)  return `${sign}${sym}${(abs / 1e6).toFixed(2)}M`;
    return `${sign}${sym}${abs.toLocaleString()}`;
  };
}
function fmtPct(v: number | undefined): string {
  if (v == null) return '—';
  const pct = v * 100;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
}

function fmtMultiple(v: number | undefined): string {
  if (v == null) return '—';
  return `${v.toFixed(1)}x`;
}

// Per-share price (52wk high/low, day high/low) — a raw dollar level, not an
// aggregate, so it needs the ticker-aware currency formatter but WITHOUT the
// K/M/B/T scaling makeFmtLarge applies.
export function makeFmtPrice(sym: string) {
  return (v: number | undefined): string => {
    if (v == null) return '—';
    return `${sym}${v.toFixed(2)}`;
  };
}

function fmtVolume(v: number | undefined): string {
  if (v == null) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toLocaleString();
}

// ── Metric definitions ────────────────────────────────────────────────────────
// `currency: true` means the value needs the ticker-aware currency formatter
// (injected at render time inside the component via makeFmtLarge).
// All others use a static module-level formatter.
export type MetricDef = {
  key:       keyof StockData['metrics'];
  label:     string;
  currency?: true;   // large $ value — K/M/B/T scaled (makeFmtLarge)
  price?:    true;   // per-share $ value — no scaling (makeFmtPrice)
  fmt?:      (v: number | undefined) => string;
  signed?:   true;   // colour green/red based on sign
};

// 20 fields (10 rows × 2 cols) — extended from the original 8 to reach parity
// with mobile's V2KeyStats (P/S, ROIC, Net Cash, FCF Yield, 52wk High/Low)
// plus new additions fetched from FMP for the first time (P/B, EPS, Div
// Yield, Day High/Low, Volume) — see app/backend/routes/analysis.py
// get_stock_data. DR Ratio / Turnover Rate / Amplitude / Flow of Equity from
// the reference design were skipped: they're China/HK-trading-terminal
// concepts (ADR conversion ratio, share turnover %) that don't fit this
// app's fundamentals/DCF focus and aren't in the FMP fields already fetched.
export const METRICS: MetricDef[] = [
  { key: 'market_cap',                  label: 'Market Cap',       currency: true              },
  { key: 'revenue',                     label: 'Revenue (TTM)',    currency: true              },
  { key: 'free_cash_flow',              label: 'Free Cash Flow',   currency: true              },
  { key: 'net_margin',                  label: 'Net Margin',       fmt: fmtPct,   signed: true },
  { key: 'pe_ratio',                    label: 'P/E (TTM)',        fmt: fmtMultiple            },
  { key: 'price_to_sales',              label: 'P/S (TTM)',        fmt: fmtMultiple            },
  { key: 'price_to_book',               label: 'P/B',              fmt: fmtMultiple            },
  { key: 'ev_to_ebitda',                label: 'EV / EBITDA',      fmt: fmtMultiple            },
  { key: 'revenue_growth',              label: 'Rev Growth YoY',   fmt: fmtPct,   signed: true },
  { key: 'eps_ttm',                     label: 'EPS (TTM)',        price: true                 },
  { key: 'return_on_equity',            label: 'Return on Equity', fmt: fmtPct,   signed: true },
  { key: 'return_on_invested_capital',  label: 'ROIC',             fmt: fmtPct,   signed: true },
  { key: 'free_cash_flow_yield',        label: 'FCF Yield',        fmt: fmtPct                 },
  { key: 'dividend_yield',              label: 'Div Yield',        fmt: fmtPct                 },
  { key: 'net_cash',                    label: 'Net Cash',         currency: true, signed: true },
  { key: 'fifty_two_week_high',         label: '52W High',         price: true                 },
  { key: 'fifty_two_week_low',          label: '52W Low',          price: true                 },
  { key: 'day_high',                    label: 'Day High',         price: true                 },
  { key: 'day_low',                     label: 'Day Low',          price: true                 },
  { key: 'volume',                      label: 'Volume',           fmt: fmtVolume              },
];
