/**
 * REITValuationPanel
 * -------------------
 * REIT-specific supplement to the Valuation tab. Renders BELOW the existing
 * PriceTargetPanel + ScenarioChart (which already work well for REITs) and
 * replaces ONLY the generic DCF ladder at the bottom.
 *
 * Design DNA matches the live deployed app exactly:
 *   - Uppercase `tracking-[0.2em]` section headings in muted-foreground
 *   - Big centered hero values (text-5xl) with delta beneath
 *   - Quad KPI grid with bull/bear tinted tiles (green/red-50 / -950)
 *   - Underline-style tab parent (rendered by ReportPage, not here)
 *   - Functional colors only: green-600 for positive, red-500 for negative,
 *     blue-500 for base scenarios. No purple accent.
 *   - Works in both light and dark modes via neutral/zinc scale
 *   - All cards use `rounded-2xl border bg-card p-5`
 *
 * Every derived metric prints its formula inline so analysts can audit.
 *
 * Sections, in order:
 *   1. NAV per share hero — mirrors "12-Month Price Target" card pattern
 *   2. REIT Key Stats — compact 2-col label/value grid
 *   3. Method Breakdown — mirrors "Scenario Probabilities" row pattern
 *   4. NPI History — mirrors "Scenario Analysis" chart pattern
 *   5. DPU History — same
 *   6. Portfolio Composition — 2 pies (asset class + geography)
 *   7. Cap-Rate Sensitivity — 3×3 matrix with peer cell highlighted
 */

import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { DcfRange, ReitBreakdown, DcfCase } from '@/lib/reportTypes';
import { currencySymbol } from '@/lib/utils';

// ── Formatters ─────────────────────────────────────────────────────────────

const fmtMoney = (v: number | null | undefined, sym: string, decimals = 2): string => {
  if (v == null || isNaN(v)) return '—';
  return `${sym}${v.toFixed(decimals)}`;
};
const fmtBn = (v: number | null | undefined, sym: string): string => {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${sym}${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sym}${(v / 1e6).toFixed(0)}M`;
  return `${sym}${v.toFixed(0)}`;
};
const fmtPct = (v: number | null | undefined, decimals = 1): string => {
  if (v == null || isNaN(v)) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
};
const fmtMult = (v: number | null | undefined): string => {
  if (v == null || isNaN(v)) return '—';
  return `${v.toFixed(1)}x`;
};

// Section heading — matches live UI "SCENARIO PROBABILITIES" style
const SECTION_HEADING_CLS =
  'text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground';

// Blue tints for pie slices — matches live app's scenario bar colors
const PIE_COLORS_BLUE = ['#1e40af', '#3b82f6', '#60a5fa', '#93c5fd', '#bfdbfe'];

// ── 1. NAV per share hero — mirrors "12-Month Price Target" card ────────

function NAVHeroCard({ rb, price, sym }: {
  rb: ReitBreakdown; price: number | undefined; sym: string;
}) {
  const navPs = rb.nav_per_share;
  const upside = (navPs && price) ? (navPs - price) / price : null;
  const gav = rb.gross_asset_value ?? (rb.noi && rb.cap_rate_used ? rb.noi / rb.cap_rate_used : null);
  const shares = rb.shares;

  return (
    <div className="rounded-2xl border border-border bg-card p-5 text-center">
      <p className={`${SECTION_HEADING_CLS} mb-3`}>Net Asset Value / Share</p>
      <p className="text-5xl font-bold tabular-nums text-foreground">
        {fmtMoney(navPs, sym)}
      </p>
      {upside != null && (
        <p className={`text-base font-semibold mt-2 ${upside >= 0 ? 'text-green-600' : 'text-red-500'}`}>
          {upside >= 0 ? '+' : ''}{(upside * 100).toFixed(1)}% vs price
        </p>
      )}
      <p className="text-xs text-muted-foreground mt-0.5">
        {rb.subtype?.replace('_', ' ') ?? 'REIT'} · peer cap {fmtPct(rb.cap_rate_peer, 2)}
      </p>

      {/* Quad: NOI / GAV / Debt / Cash — mirrors Current/Long-term/Bull/Bear */}
      <div className="grid grid-cols-2 gap-3 mt-5 text-left">
        <div className="rounded-xl border border-border bg-card p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1`}>NOI (EBITDA)</p>
          <p className="text-xl font-bold tabular-nums text-foreground">{fmtBn(rb.noi, sym)}</p>
        </div>
        <div className="rounded-xl border border-border bg-card p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1`}>Gross Asset Value</p>
          <p className="text-xl font-bold tabular-nums text-foreground">{fmtBn(gav, sym)}</p>
          <p className="text-xs text-muted-foreground mt-0.5 font-mono">
            NOI ÷ {fmtPct(rb.cap_rate_used, 2)}
          </p>
        </div>
        <div className="rounded-xl border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/40 p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1 text-red-600`}>Total Debt</p>
          <p className="text-xl font-bold tabular-nums text-red-700 dark:text-red-400">
            {fmtBn(rb.total_debt, sym)}
          </p>
        </div>
        <div className="rounded-xl border border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/40 p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1 text-green-600`}>Cash</p>
          <p className="text-xl font-bold tabular-nums text-green-700 dark:text-green-400">
            {fmtBn(rb.cash, sym)}
          </p>
        </div>
      </div>

      {/* Derivation formula footer */}
      {shares && shares > 0 && (
        <p className="text-[11px] text-muted-foreground mt-4 font-mono">
          NAV = NOI / cap − debt + cash ÷ {(shares / 1e6).toFixed(1)}M sh
        </p>
      )}
    </div>
  );
}

// ── 2. REIT Key Stats — compact 2-col grid ─────────────────────────────

function REITKeyStats({ rb, price, sym, ticker: _ticker }: {
  rb: ReitBreakdown; price: number | undefined; sym: string; ticker: string;
}) {
  const mcap = (price && rb.shares) ? price * rb.shares : null;
  const ev = (mcap && rb.total_debt != null && rb.cash != null)
    ? mcap + rb.total_debt - rb.cash : null;
  const impliedCap = (rb.noi && ev && ev > 0) ? rb.noi / ev : null;
  const distYield = (rb.dps && price) ? rb.dps / price : null;
  const affoCov = (rb.dps && rb.affo_per_share) ? rb.dps / rb.affo_per_share : null;
  const leverage = (rb.total_debt && mcap && rb.cash != null)
    ? rb.total_debt / (mcap + rb.total_debt - rb.cash) : (rb.leverage_ratio_research ?? null);

  const stats: Array<{ label: string; value: string; color?: 'green' | 'red' }> = [
    { label: 'Implied cap',    value: fmtPct(impliedCap, 2) },
    { label: 'Dist. yield',    value: fmtPct(distYield, 2) },
    { label: 'AFFO coverage',  value: fmtPct(affoCov, 0),
      color: affoCov != null ? (affoCov <= 1.0 ? 'green' : 'red') : undefined },
    { label: 'Leverage',       value: fmtPct(leverage, 0) },
    ...(rb.occupancy_rate != null
        ? [{ label: 'Occupancy', value: fmtPct(rb.occupancy_rate, 1) }]
        : []),
    ...(rb.wale_years != null
        ? [{ label: 'WALE',      value: `${rb.wale_years.toFixed(1)}y` }]
        : []),
    { label: 'FFO / sh',       value: fmtMoney(rb.ffo_per_share, sym) },
    { label: 'AFFO / sh',      value: fmtMoney(rb.affo_per_share, sym) },
  ];

  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center justify-between mb-4">
        <p className={SECTION_HEADING_CLS}>REIT Key Stats</p>
        <span className="text-xs font-medium text-muted-foreground">
          Peer cap {fmtPct(rb.cap_rate_peer, 2)}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
        {stats.map(s => {
          const valCls =
            s.color === 'green' ? 'text-green-600' :
            s.color === 'red'   ? 'text-red-500'   : 'text-foreground';
          return (
            <div key={s.label} className="flex items-center justify-between">
              <span className="text-muted-foreground">{s.label}</span>
              <span className={`font-semibold tabular-nums ${valCls}`}>{s.value}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 3. Method Breakdown — mirrors "Scenario Probabilities" row pattern ──

function MethodBreakdown({ base, rb, sym, price }: {
  base: DcfCase | undefined; rb: ReitBreakdown; sym: string; price: number | undefined;
}) {
  const iv = base?.method_iv_table ?? {};
  const weights = base?.profile_weights ?? [];
  const rowSource = weights.length > 0
    ? weights.map(w => ({ name: w.name, weight: w.weight, value: iv[w.name] ?? null }))
    : Object.entries(iv).map(([k, v]) => ({ name: k, weight: null, value: v }));

  const derivation = (name: string): string => {
    if (name.includes('NAV')) return 'NOI/cap − debt + cash';
    if (name === 'P/FFO')     return `FFO/sh × ${fmtMult(rb.p_ffo_peer)}`;
    if (name === 'P/AFFO')    return `AFFO/sh × ${fmtMult(rb.p_affo_peer)}`;
    if (name === 'DDM')       return 'DPS×(1+g)/(WACC−g)';
    if (name === 'Residual Income') return 'BVPS + PV excess ROE';
    if (name === 'DCF')       return 'PV(FCF) + PV(TV)';
    return '';
  };

  const totalWeight = weights.reduce((s, w) => s + (w.weight || 0), 0);
  const blended = rowSource.reduce((sum, r) => {
    if (r.value == null || r.weight == null) return sum;
    return sum + r.value * r.weight;
  }, 0);

  // Weight-bar color tints (blue shades, matching scenario probs)
  const barColor = (w: number | null): string => {
    if (w == null) return 'bg-neutral-300 dark:bg-neutral-600';
    if (w >= 0.40) return 'bg-blue-500';
    if (w >= 0.25) return 'bg-blue-400';
    if (w >= 0.10) return 'bg-blue-300';
    return 'bg-blue-200';
  };

  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center justify-between mb-4">
        <p className={SECTION_HEADING_CLS}>REIT Method Breakdown</p>
        {totalWeight > 0 && (
          <span className="text-xs font-medium text-muted-foreground">
            Σ = {(totalWeight * 100).toFixed(0)}%
          </span>
        )}
      </div>

      <div className="grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-3 items-center text-sm">
        <div></div><div></div>
        <span className="text-[10px] font-semibold tracking-widest uppercase text-muted-foreground text-right">
          IV / sh
        </span>

        {rowSource.map(r => (
          <div key={r.name} className="contents">
            <span className="text-xs font-medium tabular-nums text-muted-foreground">
              {r.weight != null ? `${(r.weight * 100).toFixed(0)}%` : '—'}
            </span>
            <div className="flex items-center gap-2 min-w-0">
              <div className="h-2 rounded-full bg-muted flex-1 overflow-hidden">
                <div
                  className={`h-full ${barColor(r.weight)}`}
                  style={{ width: `${(r.weight ?? 0) * 100}%` }}
                />
              </div>
              <div className="flex flex-col min-w-0">
                <span className="text-sm font-semibold text-foreground truncate">{r.name}</span>
                <span className="text-[10px] text-muted-foreground font-mono truncate">
                  {derivation(r.name)}
                </span>
              </div>
            </div>
            <span className="text-sm font-semibold tabular-nums text-foreground">
              {fmtMoney(r.value, sym)}
            </span>
          </div>
        ))}
      </div>

      {/* Blended footer */}
      <div className="flex items-baseline justify-between mt-4 pt-4 border-t border-border">
        <span className="text-sm font-semibold text-foreground">Blended IV</span>
        <div className="text-right">
          <span className="text-xl font-bold tabular-nums text-foreground">
            {fmtMoney(base?.intrinsic_value ?? blended, sym)}
          </span>
          {price && base?.intrinsic_value && (
            <span className={`ml-2 text-xs font-semibold ${
              base.intrinsic_value >= price ? 'text-green-600' : 'text-red-500'
            }`}>
              {base.intrinsic_value >= price ? '+' : ''}
              {((base.intrinsic_value - price) / price * 100).toFixed(1)}%
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── 4. History bar chart — mirrors "Scenario Analysis" pattern ──────────

function HistoryChart({ title, unit, data, color, caption }: {
  title: string;
  unit: string;
  data: Array<{ period: string; value: number | null }>;
  color: string;
  caption?: string;
}) {
  const rows = data
    .filter(d => d.value != null)
    .map(d => ({ period: d.period, value: d.value as number }));
  if (rows.length === 0) return null;

  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center justify-between mb-2">
        <p className={SECTION_HEADING_CLS}>{title}</p>
        <span className="text-xs font-medium text-muted-foreground">{unit}</span>
      </div>
      {caption && <p className="text-xs text-muted-foreground mb-3">{caption}</p>}
      <div className="h-32 w-full">
        <ResponsiveContainer>
          <BarChart data={rows} margin={{ top: 18, right: 4, left: 4, bottom: 2 }}>
            <XAxis
              dataKey="period"
              tick={{ fontSize: 10, fill: 'currentColor', opacity: 0.55 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis hide />
            <Tooltip
              cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
              contentStyle={{
                background: 'hsl(var(--card))',
                border: '1px solid hsl(var(--border))',
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={((value: number) => {
                const v = typeof value === 'number' ? value : Number(value);
                if (unit.includes('USD m'))  return [`$${(v / 1e6).toFixed(0)}M`, title] as [string, string];
                if (unit.includes('USD/sh')) return [`$${v.toFixed(2)}`, title]          as [string, string];
                if (unit.includes('USD'))    return [`$${v.toFixed(2)}`, title]          as [string, string];
                return [v.toLocaleString(), title]                                       as [string, string];
              }) as never}
            />
            <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]}>
              {rows.map((_, i) => <Cell key={i} fill={color} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ── 5. Portfolio Composition — 2 pies ──────────────────────────────────

function PortfolioComposition({ rb }: { rb: ReitBreakdown }) {
  const hasSubtype = rb.subtype_mix && Object.keys(rb.subtype_mix).length > 0;
  const hasGeo     = rb.geographic_mix && Object.keys(rb.geographic_mix).length > 0;
  if (!hasSubtype && !hasGeo) return null;

  const toPieData = (obj: Record<string, number>) =>
    Object.entries(obj)
      .sort((a, b) => b[1] - a[1])
      .map(([name, value]) => ({ name, value: value * 100 }));

  const PieRow = ({ data }: { data: { name: string; value: number }[] }) => (
    <div className="flex items-center gap-4">
      <div className="w-24 h-24 shrink-0">
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={data}
              innerRadius={22}
              outerRadius={46}
              dataKey="value"
              stroke="hsl(var(--card))"
              strokeWidth={2}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={PIE_COLORS_BLUE[i % PIE_COLORS_BLUE.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                background: 'hsl(var(--card))',
                border: '1px solid hsl(var(--border))',
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={((value: number) => {
                const v = typeof value === 'number' ? value : Number(value);
                return [`${v.toFixed(0)}%`, ''] as [string, string];
              }) as never}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="flex flex-col gap-1.5 text-sm flex-1 min-w-0">
        {data.map((d, i) => (
          <li key={d.name} className="flex items-center gap-2">
            <span
              className="w-2.5 h-2.5 rounded-full shrink-0"
              style={{ background: PIE_COLORS_BLUE[i % PIE_COLORS_BLUE.length] }}
            />
            <span className="text-muted-foreground flex-1 truncate">{d.name}</span>
            <span className="font-semibold tabular-nums text-foreground">
              {d.value.toFixed(0)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <p className={`${SECTION_HEADING_CLS} mb-4`}>Portfolio Composition</p>
      {hasSubtype && (
        <div className="mb-5">
          <p className="text-xs font-medium text-muted-foreground mb-2">By asset class</p>
          <PieRow data={toPieData(rb.subtype_mix!)} />
        </div>
      )}
      {hasGeo && (
        <div className={hasSubtype ? 'pt-4 border-t border-border' : ''}>
          <p className="text-xs font-medium text-muted-foreground mb-2">By geography</p>
          <PieRow data={toPieData(rb.geographic_mix!)} />
        </div>
      )}
      {rb.research_evidence && (
        <p className="text-[11px] text-muted-foreground mt-4 italic">
          Source: {rb.research_evidence.slice(0, 200)}
        </p>
      )}
    </div>
  );
}

// ── 6. Cap-Rate Sensitivity — 3×3 matrix ───────────────────────────────

function CapRateScenarios({ rb, sym, price }: {
  rb: ReitBreakdown; sym: string; price: number | undefined;
}) {
  if (!rb.noi || !rb.cap_rate_used || !rb.shares) return null;
  const baseCap = rb.cap_rate_used;
  const debt = rb.total_debt ?? 0;
  const cash = rb.cash ?? 0;
  const shares = rb.shares;

  const capRates  = [baseCap - 0.005, baseCap, baseCap + 0.005];
  const noiDeltas = [-0.05, 0.0, 0.05];
  const noiLabels = ['Bear', 'Base', 'Bull'] as const;

  const priceFor = (noi: number, cap: number) => {
    const gav = noi / cap;
    return (gav - debt + cash) / shares;
  };

  const upsideColor = (pct: number) => {
    if (pct >  10) return 'text-green-600';
    if (pct < -10) return 'text-red-500';
    return 'text-muted-foreground';
  };
  const rowColor = (label: typeof noiLabels[number]) =>
    label === 'Bull' ? 'text-green-600' :
    label === 'Base' ? 'text-blue-500'  : 'text-red-500';

  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center justify-between mb-1">
        <p className={SECTION_HEADING_CLS}>Cap-Rate Sensitivity</p>
        <span className="text-xs font-medium text-muted-foreground">NAV/sh</span>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        Base NOI {fmtBn(rb.noi, sym)} · peer cap {fmtPct(baseCap, 2)} highlighted
      </p>
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="text-[10px] font-semibold tracking-widest uppercase text-muted-foreground">
            <th className="text-left py-2">NOI Δ</th>
            {capRates.map((c, i) => (
              <th key={i} className="text-right py-2 px-1.5">
                {fmtPct(c, 1)}
                {i === 1 && (
                  <span className="block text-[9px] font-normal normal-case tracking-normal text-muted-foreground/70">
                    (peer)
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {noiDeltas.map((d, rowIdx) => {
            const label = noiLabels[rowIdx];
            return (
              <tr key={d} className="border-t border-border">
                <td className={`py-3 pr-1.5 font-semibold ${rowColor(label)}`}>
                  {label}<br/>
                  <span className="text-[10px] font-normal text-muted-foreground">
                    {d === 0 ? 'flat' : `${d > 0 ? '+' : ''}${(d * 100).toFixed(0)}%`}
                  </span>
                </td>
                {capRates.map((c, colIdx) => {
                  const noi = rb.noi! * (1 + d);
                  const p = priceFor(noi, c);
                  const up = price ? ((p - price) / price) * 100 : 0;
                  const isBase = rowIdx === 1 && colIdx === 1;
                  return (
                    <td
                      key={colIdx}
                      className={`py-3 px-1.5 text-right ${
                        isBase ? 'bg-blue-50 dark:bg-blue-950/30 rounded' : ''
                      }`}
                    >
                      <div className={`font-${isBase ? 'bold' : 'semibold'} tabular-nums ${
                        isBase ? 'text-blue-700 dark:text-blue-400' : 'text-foreground'
                      }`}>
                        {sym}{p.toFixed(0)}
                      </div>
                      {price != null && (
                        <div className={`text-[10px] ${upsideColor(up)}`}>
                          {up >= 0 ? '+' : ''}{up.toFixed(0)}%
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-[11px] text-muted-foreground mt-3 font-mono leading-tight">
        NAV = NOI × (1+Δ) / cap − {fmtBn(debt, sym)} + {fmtBn(cash, sym)} ÷ {(shares / 1e6).toFixed(1)}M sh
      </p>
    </div>
  );
}

// ── Top-level composite ────────────────────────────────────────────────

export interface REITValuationPanelProps {
  dcfRange?: DcfRange;
  currentPrice?: number;
  ticker: string;
}

export function REITValuationPanel({ dcfRange, currentPrice, ticker }: REITValuationPanelProps) {
  const rb = dcfRange?.reit_breakdown;
  const sym = currencySymbol(ticker);
  if (!dcfRange || !rb) return null;

  return (
    <div className="flex flex-col gap-4">
      <NAVHeroCard rb={rb} price={currentPrice} sym={sym} />
      <REITKeyStats rb={rb} price={currentPrice} sym={sym} ticker={ticker} />
      <MethodBreakdown base={dcfRange.base} rb={rb} sym={sym} price={currentPrice} />
      {rb.npi_history && rb.npi_history.length > 0 && (
        <HistoryChart
          title="Net Property Income"
          unit="USD m · 5Y"
          data={rb.npi_history}
          color="#3b82f6"
          caption="Annual EBITDA proxy — NAV sensitivity anchor"
        />
      )}
      {rb.dpu_history && rb.dpu_history.length > 0 && (
        <HistoryChart
          title="Distribution per Share"
          unit="USD · 5Y"
          data={rb.dpu_history}
          color="#22c55e"
          caption="AFFO-funded distribution trend"
        />
      )}
      {(rb.subtype_mix || rb.geographic_mix) && <PortfolioComposition rb={rb} />}
      <CapRateScenarios rb={rb} sym={sym} price={currentPrice} />
    </div>
  );
}
