/**
 * FinancialsChart
 *
 * Income-statement bar chart with:
 *  - Metric toggle: Total Revenue | Operating Income | Net Income
 *  - Period toggle: Annual (last 5 FY) | Quarterly (last 20 quarters)
 *  - Aligned data table below: absolute values (M/B) + YoY change row
 *    (green = positive, red = negative)
 *
 * Data sourced from FMP via GET /api/analysis/financials/{ticker}
 */

import { useEffect, useState, useCallback } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts';
import { Card } from '@/components/ui/card';
import { getFinancials, type FinancialsItem } from '@/lib/api';
import { currencySymbol } from '@/lib/utils';

// ── Types ─────────────────────────────────────────────────────────────────────

type MetricKey = 'revenue' | 'operating_income' | 'net_income';
type PeriodType = 'annual' | 'quarter';

interface MetricConfig {
  key:   MetricKey;
  label: string;
  color: string;           // base bar color (used when value is positive)
}

const METRICS: MetricConfig[] = [
  { key: 'revenue',          label: 'Total Revenue',    color: '#3b82f6' },
  { key: 'operating_income', label: 'Operating Income', color: '#8b5cf6' },
  { key: 'net_income',       label: 'Net Income',       color: '#22c55e' },
];

// ── Formatters (sym injected at component level via makeFmtFull/makeFmtAxis) ──

function makeFmtFull(sym: string) {
  return (v: number | null | undefined): string => {
    if (v == null) return '—';
    const abs = Math.abs(v);
    const sign = v < 0 ? '-' : '';
    if (abs >= 1e12) return `${sign}${sym}${(abs / 1e12).toFixed(2)}T`;
    if (abs >= 1e9)  return `${sign}${sym}${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6)  return `${sign}${sym}${(abs / 1e6).toFixed(2)}M`;
    return `${sign}${sym}${abs.toLocaleString()}`;
  };
}

function makeFmtAxis(sym: string) {
  return (v: number): string => {
    const abs = Math.abs(v);
    const sign = v < 0 ? '-' : '';
    if (abs >= 1e12) return `${sign}${sym}${(abs / 1e12).toFixed(1)}T`;
    if (abs >= 1e9)  return `${sign}${sym}${(abs / 1e9).toFixed(1)}B`;
    if (abs >= 1e6)  return `${sign}${sym}${(abs / 1e6).toFixed(0)}M`;
    return `${sign}${sym}${abs}`;
  };
}

// Module-level defaults — overridden inside the component
function fmtFull(v: number | null | undefined): string { return makeFmtFull('$')(v); }
function fmtAxis(v: number): string { return makeFmtAxis('$')(v); }

// ── Change calculation ────────────────────────────────────────────────────────

interface ChangeResult {
  text:     string;
  positive: boolean | null;   // null → no comparison available
}

function calcChange(current: number | null, prev: number | null): ChangeResult {
  if (current == null || prev == null || prev === 0) {
    return { text: '—', positive: null };
  }
  const pct = ((current - prev) / Math.abs(prev)) * 100;
  return {
    text:     `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`,
    positive: pct >= 0,
  };
}

// ── Bar color helper ──────────────────────────────────────────────────────────
// Net Income bars go green/red depending on value sign.
// Revenue & OpIncome keep their brand color always.

function barColor(cfg: MetricConfig, value: number | null): string {
  if (cfg.key === 'net_income') {
    return (value ?? 0) >= 0 ? '#22c55e' : '#ef4444';
  }
  return cfg.color;
}

// ── Custom tooltip ────────────────────────────────────────────────────────────

interface CustomTooltipProps {
  active?:  boolean;
  payload?: { value: number; payload: { periodLabel: string; rawValue: number | null } }[];
  label?:   string;
  metricLabel: string;
}

function CustomTooltip({ active, payload, metricLabel }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  return (
    <div className="bg-popover border border-border rounded px-3 py-2 shadow text-xs">
      <p className="font-semibold mb-1">{entry.payload.periodLabel}</p>
      <p className="text-muted-foreground">
        {metricLabel}:{' '}
        <span className="font-mono text-foreground">{fmtFull(entry.payload.rawValue)}</span>
      </p>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface FinancialsChartProps {
  ticker: string;
}

export function FinancialsChart({ ticker }: FinancialsChartProps) {
  const sym = currencySymbol(ticker);
  const fmtFull = makeFmtFull(sym);
  const fmtAxis = makeFmtAxis(sym);
  const [metricIdx, setMetricIdx] = useState<number>(0);
  const [periodType, setPeriodType] = useState<PeriodType>('annual');
  const [items, setItems] = useState<FinancialsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const metric = METRICS[metricIdx];

  const load = useCallback((pt: PeriodType) => {
    setLoading(true);
    setError(null);
    getFinancials(ticker, pt)
      .then(d => setItems(d.items))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [ticker]);

  useEffect(() => { load(periodType); }, [periodType, load]);

  // ── Build chart data ───────────────────────────────────────────────────────
  const chartData = items.map(item => ({
    periodLabel: item.period_label,
    rawValue:    item[metric.key] as number | null,
    // recharts needs a numeric value; use 0 for null so bar renders at baseline
    value:       item[metric.key] as number ?? 0,
  }));

  const hasNegative = chartData.some(d => (d.rawValue ?? 0) < 0);

  // ── Build change rows (YoY for both annual and quarterly)
  // For annual: compare to previous year (i-1).
  // For quarterly: compare to same quarter previous year (i-4).
  const changeStep = periodType === 'quarter' ? 4 : 1;

  const changeRow: ChangeResult[] = items.map((item, i) => {
    const prev = i >= changeStep ? items[i - changeStep][metric.key] as number | null : null;
    return calcChange(item[metric.key] as number | null, prev);
  });

  const changeLabel = periodType === 'quarter' ? 'YoY Δ' : 'YoY Δ';

  return (
    <Card className="p-4">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h3 className="text-sm font-semibold">Income Statement — {ticker}</h3>

        <div className="flex items-center gap-2">
          {/* Period toggle */}
          <div className="flex rounded-md overflow-hidden border border-border">
            {(['annual', 'quarter'] as PeriodType[]).map(pt => (
              <button
                key={pt}
                onClick={() => setPeriodType(pt)}
                className={`text-[11px] font-semibold px-3 py-1 transition-colors
                  ${periodType === pt
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
              >
                {pt === 'annual' ? 'Annual' : 'Quarterly'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Metric toggle ──────────────────────────────────────────────── */}
      <div className="flex gap-1.5 mb-4">
        {METRICS.map((m, i) => (
          <button
            key={m.key}
            onClick={() => setMetricIdx(i)}
            className={`text-[11px] font-semibold px-3 py-1 rounded-full border transition-colors
              ${metricIdx === i
                ? 'border-transparent text-white'
                : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/30 bg-transparent'
              }`}
            style={metricIdx === i ? { backgroundColor: m.color } : undefined}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* ── Chart body ─────────────────────────────────────────────────── */}
      {loading ? (
        <div className="h-52 flex items-center justify-center">
          <p className="text-xs text-muted-foreground">Loading financials…</p>
        </div>
      ) : error ? (
        <div className="h-52 flex items-center justify-center">
          <p className="text-xs text-red-500">{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="h-52 flex items-center justify-center">
          <p className="text-xs text-muted-foreground">No data available.</p>
        </div>
      ) : (
        <>
          {/*
            Quarterly view can hold up to 20 data points — squeezing them into
            a mobile-width card collapses the bar x-axis labels and overlaps
            the data-table cells beneath. We scroll horizontally for quarter
            mode by wrapping BOTH the chart and the table in one scroll
            container with a shared minWidth. Annual (≤5 points) fits in the
            viewport, so keep its 100%-width layout.
          */}
          {(() => {
            // Label col 88px + 72px per data column → readable "$260.12B" and
            // "Q4 2024" without truncation. Pulled out so chart + table share
            // the same minWidth when scrolling.
            const LABEL_COL = 88;
            const DATA_COL  = 72;
            const minInner  = LABEL_COL + items.length * DATA_COL;
            const isQuarter = periodType === 'quarter';
            const outerClass = isQuarter
              ? 'overflow-x-auto -mx-2 px-2 pb-1'
              : '';
            const innerStyle: React.CSSProperties = isQuarter
              ? { minWidth: minInner }
              : {};

            return (
              <div className={outerClass}>
                <div style={innerStyle}>
                  {/* Bar chart */}
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart
                      data={chartData}
                      margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
                      barCategoryGap="28%"
                    >
                      <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="hsl(var(--border))" strokeOpacity={0.5} />
                      <XAxis
                        dataKey="periodLabel"
                        tick={{ fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                        interval={0}
                      />
                      <YAxis
                        tickFormatter={fmtAxis}
                        tick={{ fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                        width={68}
                      />
                      <Tooltip
                        content={<CustomTooltip metricLabel={metric.label} />}
                        cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
                      />
                      {hasNegative && (
                        <ReferenceLine
                          y={0}
                          stroke="hsl(var(--border))"
                          strokeWidth={1.5}
                        />
                      )}
                      <Bar dataKey="value" radius={[3, 3, 0, 0]} maxBarSize={60}>
                        {chartData.map((entry, i) => (
                          <Cell
                            key={`cell-${i}`}
                            fill={barColor(metric, entry.rawValue)}
                            fillOpacity={0.9}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>

                  {/* ── Aligned data table ─────────────────────────────── */}
                  <div className="mt-1">
                    <table className="w-full border-collapse" style={{ tableLayout: 'fixed' }}>
              <colgroup>
                {/* Label column */}
                <col style={{ width: '88px' }} />
                {/* One column per data point */}
                {items.map((_, i) => (
                  <col key={i} />
                ))}
              </colgroup>

              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-1.5 pr-2 text-xs font-semibold text-muted-foreground">
                    {periodType === 'annual' ? 'Fiscal Year' : 'Quarter'}
                  </th>
                  {items.map(item => (
                    <th
                      key={item.period_label}
                      className="text-center py-1.5 px-1 text-xs font-semibold text-muted-foreground whitespace-nowrap"
                    >
                      {item.period_label}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {/* Row 1: absolute values */}
                <tr className="border-b border-border/40">
                  <td className="py-1.5 pr-2 text-xs font-semibold text-muted-foreground align-middle">
                    {metric.label}
                  </td>
                  {items.map((item, i) => {
                    const v = item[metric.key] as number | null;
                    return (
                      <td
                        key={i}
                        className="text-center py-1.5 px-1 text-[11px] font-mono font-semibold align-middle"
                        style={{ color: metric.key === 'net_income' ? barColor(metric, v) : undefined }}
                      >
                        {fmtFull(v)}
                      </td>
                    );
                  })}
                </tr>

                {/* Row 2: YoY change */}
                <tr>
                  <td className="py-1.5 pr-2 text-xs font-semibold text-muted-foreground align-middle">
                    {changeLabel}
                  </td>
                  {changeRow.map((cr, i) => (
                    <td
                      key={i}
                      className={`text-center py-1.5 px-1 text-[11px] font-semibold font-mono align-middle
                        ${cr.positive === true
                          ? 'text-green-600 dark:text-green-400'
                          : cr.positive === false
                            ? 'text-red-500 dark:text-red-400'
                            : 'text-muted-foreground'
                        }`}
                    >
                      {cr.text}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
                  </div>
                </div>
              </div>
            );
          })()}
        </>
      )}
    </Card>
  );
}
