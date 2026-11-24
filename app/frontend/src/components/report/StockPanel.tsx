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
import { METRICS, TIMEFRAMES, makeFmtLarge, makeFmtPrice, type StockData } from '@/lib/stockMetrics';

interface StockPanelProps {
  ticker: string;
}

export function StockPanel({ ticker }: StockPanelProps) {
  const sym = currencySymbol(ticker);
  const fmtLarge = makeFmtLarge(sym);
  const fmtPrice = makeFmtPrice(sym);
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
          <span className={`text-xs font-bold ${isPositive ? 'text-gain' : 'text-loss'}`}>
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
            className={`text-xs font-semibold px-2 py-0.5 rounded transition-colors
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
          {METRICS.map(({ key, label, currency, price, fmt, signed }) => {
            const val       = data.metrics[key];
            // Currency metrics use the ticker-aware fmtLarge (HK$ vs $, K/M/B/T
            // scaled); price metrics use fmtPrice (same currency symbol, no scaling).
            const formatter = currency ? fmtLarge : price ? fmtPrice : (fmt ?? (() => '—'));
            const formatted = formatter(val);
            // Monochrome: these `signed` metrics are net margin, revenue
            // growth, ROE, ROIC and net cash -- financial figures, not price
            // moves, so they do not earn the reserved gain/loss palette. The
            // leading sign in the formatted value carries the direction.
            const valueColor = !signed || val == null
              ? 'text-foreground'
              : val >= 0 ? 'text-foreground' : 'text-content-medium';
            return (
              <div key={key}>
                <p className="text-xs uppercase tracking-wider text-muted-foreground leading-none mb-0.5">
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
