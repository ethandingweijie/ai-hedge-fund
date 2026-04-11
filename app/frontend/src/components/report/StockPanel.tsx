import { useEffect, useState, useCallback } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card } from '@/components/ui/card';
import { getStockData } from '@/lib/api';
import { currencySymbol } from '@/lib/utils';

interface StockPanelProps {
  ticker: string;
}

interface StockData {
  history: { date: string; close: number }[];
  metrics: {
    market_cap?:       number;
    revenue?:          number;
    free_cash_flow?:   number;
    net_margin?:       number;
    pe_ratio?:         number;
    revenue_growth?:   number;
    ev_to_ebitda?:     number;
    return_on_equity?: number;
  };
}

// ── Timeframe config ──────────────────────────────────────────────────────────
const TIMEFRAMES: { label: string; period: string; dateFormat: Intl.DateTimeFormatOptions; interval: (n: number) => number }[] = [
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

function makeFmtLarge(sym: string) {
  return (v: number | undefined): string => {
    if (v == null) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e12) return `${sym}${(v / 1e12).toFixed(2)}T`;
    if (abs >= 1e9)  return `${sym}${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6)  return `${sym}${(v / 1e6).toFixed(2)}M`;
    return `${sym}${v.toLocaleString()}`;
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

// ── Metric definitions ────────────────────────────────────────────────────────
// `currency: true` means the value needs the ticker-aware currency formatter
// (injected at render time inside the component via makeFmtLarge).
// All others use a static module-level formatter.
type MetricDef = {
  key:       keyof StockData['metrics'];
  label:     string;
  currency?: true;
  fmt?:      (v: number | undefined) => string;
  signed?:   true;   // colour green/red based on sign
};

const METRICS: MetricDef[] = [
  { key: 'market_cap',       label: 'Market Cap',       currency: true              },
  { key: 'revenue',          label: 'Revenue (TTM)',     currency: true              },
  { key: 'free_cash_flow',   label: 'Free Cash Flow',   currency: true              },
  { key: 'net_margin',       label: 'Net Margin',        fmt: fmtPct,   signed: true },
  { key: 'pe_ratio',         label: 'P/E (TTM)',         fmt: fmtMultiple            },
  { key: 'revenue_growth',   label: 'Rev Growth YoY',   fmt: fmtPct,   signed: true },
  { key: 'ev_to_ebitda',     label: 'EV / EBITDA',      fmt: fmtMultiple            },
  { key: 'return_on_equity', label: 'Return on Equity', fmt: fmtPct,   signed: true },
];

export function StockPanel({ ticker }: StockPanelProps) {
  const sym = currencySymbol(ticker);
  const fmtLarge = makeFmtLarge(sym);
  const [tfIdx, setTfIdx] = useState(4);           // default: 1Y
  const [data, setData] = useState<StockData | null>(null);
  const [loading, setLoading] = useState(true);

  const tf = TIMEFRAMES[tfIdx];

  const load = useCallback((period: string) => {
    setLoading(true);
    getStockData(ticker, period)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [ticker]);

  useEffect(() => { load(tf.period); }, [tf.period, load]);

  // ── date formatter for axis labels ────────────────────────────────────────
  const fmtAxisDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString('en-US', tf.dateFormat);

  // ── tooltip date formatter ────────────────────────────────────────────────
  const fmtTooltipDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString('en-US', {
      weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
    });

  const history = data?.history ?? [];
  const minClose = history.length ? Math.min(...history.map(d => d.close)) : 0;
  const maxClose = history.length ? Math.max(...history.map(d => d.close)) : 0;
  const domain: [number, number] = [minClose * 0.97, maxClose * 1.02];

  const first = history[0]?.close ?? 0;
  const last  = history[history.length - 1]?.close ?? 0;
  const pctChange = first > 0 ? ((last - first) / first) * 100 : 0;
  const isPositive = pctChange >= 0;

  return (
    <Card className="p-4 flex flex-col gap-3 h-full">
      {/* ── Header row ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          {ticker}
        </span>
        {!loading && history.length > 0 && (
          <span className={`text-xs font-bold ${isPositive ? 'text-green-500' : 'text-red-500'}`}>
            {isPositive ? '+' : ''}{pctChange.toFixed(2)}%
          </span>
        )}
      </div>

      {/* ── Timeframe toggle ───────────────────────────────────────────── */}
      <div className="flex gap-1">
        {TIMEFRAMES.map((t, i) => (
          <button
            key={t.label}
            onClick={() => setTfIdx(i)}
            className={`text-[10px] font-semibold px-2 py-0.5 rounded transition-colors
              ${tfIdx === i
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Chart ──────────────────────────────────────────────────────── */}
      <div className="min-h-[120px] flex items-center justify-center">
        {loading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : history.length === 0 ? (
          <p className="text-xs text-muted-foreground">No price data.</p>
        ) : (
          <ResponsiveContainer width="100%" height={120}>
            <AreaChart data={history} margin={{ top: 2, right: 2, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="stockGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tickFormatter={fmtAxisDate}
                tick={{ fontSize: 9 }}
                tickLine={false}
                axisLine={false}
                interval={tf.interval(history.length)}
              />
              <YAxis
                domain={domain}
                tick={{ fontSize: 9 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `${sym}${v.toFixed(0)}`}
              />
              <Tooltip
                contentStyle={{ fontSize: 11, padding: '4px 8px' }}
                formatter={(v: unknown) => [`${sym}${Number(v).toFixed(2)}`, 'Close']}
                labelFormatter={(l: unknown) => fmtTooltipDate(String(l))}
              />
              <Area
                type="monotone"
                dataKey="close"
                stroke={isPositive ? '#22c55e' : '#ef4444'}
                strokeWidth={1.5}
                fill="url(#stockGrad)"
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── Key metrics (2 × 4 grid) ────────────────────────────────────── */}
      {data && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 border-t pt-3">
          {METRICS.map(({ key, label, currency, fmt, signed }) => {
            const val       = data.metrics[key];
            // Currency metrics use the ticker-aware fmtLarge (HK$ vs $)
            const formatter = currency ? fmtLarge : (fmt ?? (() => '—'));
            const formatted = formatter(val);
            const valueColor = !signed || val == null
              ? 'text-foreground'
              : val >= 0 ? 'text-green-500' : 'text-red-500';
            return (
              <div key={key}>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground leading-none mb-0.5">
                  {label}
                </p>
                <p className={`text-sm font-semibold tabular-nums ${valueColor}`}>
                  {formatted}
                </p>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
