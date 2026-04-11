import { useEffect, useState, useCallback } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { getStockData } from '@/lib/api';
import { currencySymbol } from '@/lib/utils';

interface MobileChartStripProps {
  ticker: string;
}

const TIMEFRAMES = [
  { label: '1D',  period: '1d',   dateFormat: { hour: '2-digit', minute: '2-digit' } as Intl.DateTimeFormatOptions },
  { label: '1W',  period: '5d',   dateFormat: { weekday: 'short', month: 'short', day: 'numeric' } as Intl.DateTimeFormatOptions },
  { label: '1M',  period: '1mo',  dateFormat: { month: 'short', day: 'numeric' } as Intl.DateTimeFormatOptions },
  { label: '3M',  period: '3mo',  dateFormat: { month: 'short', day: 'numeric' } as Intl.DateTimeFormatOptions },
  { label: '1Y',  period: '1y',   dateFormat: { month: 'short', year: '2-digit' } as Intl.DateTimeFormatOptions },
  { label: '3Y',  period: '3y',   dateFormat: { month: 'short', year: '2-digit' } as Intl.DateTimeFormatOptions },
  { label: '5Y',  period: '5y',   dateFormat: { month: 'short', year: '2-digit' } as Intl.DateTimeFormatOptions },
];

export function MobileChartStrip({ ticker }: MobileChartStripProps) {
  const sym = currencySymbol(ticker);
  const [tfIdx, setTfIdx] = useState(4); // default: 1Y
  const [history, setHistory] = useState<{ date: string; close: number }[]>([]);
  const [loading, setLoading] = useState(true);

  const tf = TIMEFRAMES[tfIdx];

  const load = useCallback((period: string) => {
    setLoading(true);
    getStockData(ticker, period)
      .then((d) => setHistory(d?.history ?? []))
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, [ticker]);

  useEffect(() => { load(tf.period); }, [tf.period, load]);

  const first = history[0]?.close ?? 0;
  const last  = history[history.length - 1]?.close ?? 0;
  const pctChange = first > 0 ? ((last - first) / first) * 100 : 0;
  const isPositive = pctChange >= 0;

  const minClose = history.length ? Math.min(...history.map(d => d.close)) : 0;
  const maxClose = history.length ? Math.max(...history.map(d => d.close)) : 0;
  const domain: [number, number] = [minClose * 0.97, maxClose * 1.02];

  return (
    <div className="px-4 py-2">
      {/* Timeframe pill selector */}
      <div className="flex gap-1 mb-2 overflow-x-auto scrollbar-hide">
        {TIMEFRAMES.map((t, i) => (
          <button
            key={t.label}
            onClick={() => setTfIdx(i)}
            className={`text-[11px] font-semibold px-3 py-1 rounded-full shrink-0 transition-colors
              ${tfIdx === i
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground'
              }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Chart */}
      <div className="h-[160px] w-full touch-pan-x">
        {loading ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-xs text-muted-foreground">Loading chart...</p>
          </div>
        ) : history.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-xs text-muted-foreground">No price data.</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={history} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="mobileStockGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={isPositive ? '#22c55e' : '#ef4444'} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tickFormatter={(d: string) => new Date(d).toLocaleDateString('en-US', tf.dateFormat)}
                tick={{ fontSize: 9 }}
                tickLine={false}
                axisLine={false}
                interval={Math.floor(history.length / 4)}
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
                labelFormatter={(l: unknown) => new Date(String(l)).toLocaleDateString('en-US', {
                  weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
                })}
              />
              <Area
                type="monotone"
                dataKey="close"
                stroke={isPositive ? '#22c55e' : '#ef4444'}
                strokeWidth={1.5}
                fill="url(#mobileStockGrad)"
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
