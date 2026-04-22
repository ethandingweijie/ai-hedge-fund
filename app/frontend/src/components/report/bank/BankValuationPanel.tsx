/**
 * BankValuationPanel
 * -------------------
 * Bank-specific supplement to the Valuation tab, rendered below the existing
 * PriceTargetPanel + ScenarioChart when `dcfRange.bank_breakdown` is present
 * (backend emits it only for Financials / Bank / Mortgage profiles).
 *
 * Design DNA exactly matches REITValuationPanel:
 *   - uppercase `tracking-[0.2em]` section headings in zinc-500/400
 *   - `rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5`
 *   - `text-5xl font-bold tabular-nums` hero values centered
 *   - Bull / bear tinted tiles: bg-green-50 dark:bg-green-950/40 /
 *     bg-red-50 dark:bg-red-950/40
 *   - Functional colors only — green-600 / red-500 / blue-500
 *
 * 8 panels in order (mirrors the OCBC / DBS research driver hierarchy):
 *   1. P/TBV Fair Value Hero   — "Does this bank earn above its CoC?"
 *   2. Bank Key Stats grid     — ROE, ROA, NIM, CIR, credit cost, BVPS, NPL, CET1
 *   3. ROE vs CoE Spread gauge — value-creation signal
 *   4. Capital Return card     — div yield + buyback yield + payout + CET1 surplus
 *   5. PPOP Growth             — 5y bar chart (OCBC driver #2: pre-provision profit)
 *   6. NIM History             — 5y bar chart + optional NIM rate sensitivity tile
 *   7. Loan Growth             — 5y if FMP has it, else single YoY tile from research
 *   8. Book Quality card       — NPL + coverage + overlays (research-only, gated)
 *
 * Every panel degrades gracefully when data is missing (common for SGX/HK
 * banks or archived runs without research extraction).
 */

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { DcfRange, BankBreakdown } from '@/lib/reportTypes';
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
const fmtBps = (v: number | null | undefined, decimals = 0): string => {
  if (v == null || isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)} bps`;
};

const SECTION_HEADING_CLS =
  'text-[11px] font-semibold uppercase tracking-[0.2em] text-zinc-500 dark:text-zinc-400';

// ── 1. P/TBV Fair Value Hero ──────────────────────────────────────────────

function PTBVHeroCard({ bb, price, sym, ticker }: {
  bb: BankBreakdown; price: number | undefined; sym: string; ticker: string;
}) {
  const fairValue = bb.fair_value_per_share;
  const upside = (fairValue && price) ? (fairValue - price) / price : null;

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 text-center">
      <p className={`${SECTION_HEADING_CLS} mb-3`}>P/TBV Fair Value — {ticker}</p>
      <p className="text-5xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
        {fmtMoney(fairValue, sym)}
      </p>
      {upside != null && (
        <p className={`text-base font-semibold mt-2 ${upside >= 0 ? 'text-green-600' : 'text-red-500'}`}>
          {upside >= 0 ? '+' : ''}{(upside * 100).toFixed(1)}% vs price
        </p>
      )}
      <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
        {bb.profile ?? 'Bank'} · Target ROE {fmtPct(bb.target_roe, 1)} · CoE {fmtPct(bb.coe, 1)}
      </p>

      {/* Quad: TBV/sh · BVPS · ROE · CET1 buffer */}
      <div className="grid grid-cols-2 gap-3 mt-5 text-left">
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1`}>TBV / share</p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
            {fmtMoney(bb.tbv_per_share, sym)}
          </p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">Equity − goodwill − intangibles</p>
        </div>
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1`}>BVPS</p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
            {fmtMoney(bb.bvps, sym)}
          </p>
        </div>
        {/* ROE — green if ≥ target */}
        <div className={`rounded-xl border p-3.5 ${
          bb.roe != null && bb.target_roe != null && bb.roe >= bb.target_roe
            ? 'border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/40'
            : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900'
        }`}>
          <p className={`${SECTION_HEADING_CLS} mb-1 ${
            bb.roe != null && bb.target_roe != null && bb.roe >= bb.target_roe ? 'text-green-600' : ''
          }`}>ROE</p>
          <p className={`text-xl font-bold tabular-nums ${
            bb.roe != null && bb.target_roe != null && bb.roe >= bb.target_roe
              ? 'text-green-700 dark:text-green-400' : 'text-zinc-900 dark:text-zinc-50'
          }`}>
            {fmtPct(bb.roe, 1)}
          </p>
          {bb.target_roe != null && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">
              vs target {fmtPct(bb.target_roe, 0)}
            </p>
          )}
        </div>
        {/* CET1 buffer — green if positive, red if deficit */}
        <div className={`rounded-xl border p-3.5 ${
          bb.cet1_buffer_bps != null && bb.cet1_buffer_bps > 0
            ? 'border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/40'
            : bb.cet1_buffer_bps != null && bb.cet1_buffer_bps < 0
              ? 'border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/40'
              : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900'
        }`}>
          <p className={`${SECTION_HEADING_CLS} mb-1 ${
            bb.cet1_buffer_bps != null && bb.cet1_buffer_bps >= 0 ? 'text-green-600' :
            bb.cet1_buffer_bps != null && bb.cet1_buffer_bps < 0  ? 'text-red-600'   : ''
          }`}>CET1 Buffer</p>
          <p className={`text-xl font-bold tabular-nums ${
            bb.cet1_buffer_bps != null && bb.cet1_buffer_bps >= 0
              ? 'text-green-700 dark:text-green-400'
              : bb.cet1_buffer_bps != null && bb.cet1_buffer_bps < 0
                ? 'text-red-700 dark:text-red-400'
                : 'text-zinc-900 dark:text-zinc-50'
          }`}>
            {bb.cet1_buffer_bps != null ? fmtBps(bb.cet1_buffer_bps) : '—'}
          </p>
          {bb.cet1_ratio != null && bb.target_cet1 != null && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">
              {fmtPct(bb.cet1_ratio, 1)} vs {fmtPct(bb.target_cet1, 0)} target
            </p>
          )}
        </div>
      </div>

      {bb.fair_p_tbv != null && bb.tbv_per_share != null && (
        <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-4 font-mono">
          Fair = TBV × (1 + (ROE−CoE) / CoE) = {fmtMoney(bb.tbv_per_share, sym)} × {bb.fair_p_tbv.toFixed(2)}x
        </p>
      )}
    </div>
  );
}

// ── 2. Bank Key Stats grid ────────────────────────────────────────────────

function BankKeyStats({ bb, sym }: { bb: BankBreakdown; sym: string }) {
  // Credit cost tile — prefer raw FMP ratio; fall back to research-extracted NCO
  const creditCost = bb.credit_cost_ratio ?? bb.net_charge_offs_pct ?? null;
  const creditCostLabel = bb.credit_cost_ratio != null
    ? 'Credit cost' : bb.net_charge_offs_pct != null ? 'Net charge-offs' : 'Credit cost';

  // Color coding per threshold (from profile + institutional convention)
  const creditCostColor = (v: number | null): 'green' | 'red' | 'muted' => {
    if (v == null) return 'muted';
    if (v <= 0.005) return 'green';       // ≤ 50 bps = normalized
    if (v <= 0.015) return 'muted';       // 50-150 bps = mid-cycle
    return 'red';                         // > 150 bps = stressed
  };
  const cirColor = (v: number | null): 'green' | 'red' | 'muted' =>
    v == null ? 'muted' : v <= 0.50 ? 'green' : v >= 0.65 ? 'red' : 'muted';
  const nplColor = (v: number | null): 'green' | 'red' | 'muted' =>
    v == null ? 'muted' : v <= 0.01 ? 'green' : v >= 0.03 ? 'red' : 'muted';

  const stats: Array<{ label: string; value: string; color?: 'green' | 'red' | 'muted' }> = [
    { label: 'ROE',          value: fmtPct(bb.roe, 1),
      color: bb.roe != null && bb.target_roe != null
        ? (bb.roe >= bb.target_roe ? 'green' : 'red') : 'muted' },
    { label: 'ROA',          value: fmtPct(bb.roa, 2) },
    { label: 'NIM',          value: fmtPct(bb.nim, 2) },
    { label: 'CIR',          value: fmtPct(bb.efficiency_ratio, 1), color: cirColor(bb.efficiency_ratio ?? null) },
    { label: creditCostLabel, value: fmtPct(creditCost, 2), color: creditCostColor(creditCost) },
    { label: 'BVPS',         value: fmtMoney(bb.bvps ?? bb.tbv_per_share, sym) },
    { label: 'NPL',          value: fmtPct(bb.npl_ratio, 2), color: nplColor(bb.npl_ratio ?? null) },
    { label: 'CET1',         value: fmtPct(bb.cet1_ratio, 1),
      color: bb.cet1_ratio != null && bb.target_cet1 != null
        ? (bb.cet1_ratio >= bb.target_cet1 ? 'green' : 'red') : 'muted' },
  ];

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5">
      <div className="flex items-center justify-between mb-4">
        <p className={SECTION_HEADING_CLS}>Bank Key Stats</p>
        {bb.profile && (
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{bb.profile}</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
        {stats.map(s => {
          const valCls =
            s.color === 'green' ? 'text-green-600' :
            s.color === 'red'   ? 'text-red-500'   : 'text-zinc-900 dark:text-zinc-50';
          return (
            <div key={s.label} className="flex items-center justify-between">
              <span className="text-zinc-500 dark:text-zinc-400">{s.label}</span>
              <span className={`font-semibold tabular-nums ${valCls}`}>{s.value}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 3. ROE vs CoE Spread gauge ────────────────────────────────────────────

function ROEGauge({ bb }: { bb: BankBreakdown }) {
  if (bb.roe == null || bb.coe == null) return null;
  const spread = bb.roe - bb.coe;
  const spreadBps = spread * 10000;
  const isPositive = spread >= 0;
  // Map spread to a 0-100 scale for the bar: 0 = −500bps, 50 = 0, 100 = +1000bps
  const barPosition = Math.max(0, Math.min(100, ((spreadBps + 500) / 1500) * 100));
  const label = isPositive
    ? (spread >= 0.04 ? 'strong value creator' : spread >= 0.01 ? 'value creator' : 'marginal creator')
    : (spread <= -0.03 ? 'capital destroyer' : 'below-cost-of-capital');

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5">
      <p className={`${SECTION_HEADING_CLS} mb-3`}>ROE vs CoE Spread</p>
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-xs text-zinc-500 dark:text-zinc-400">Value creation signal</span>
        <span className={`text-lg font-bold tabular-nums ${
          isPositive ? 'text-green-600' : 'text-red-500'
        }`}>
          {fmtBps(spreadBps)}
        </span>
      </div>
      {/* Gauge bar */}
      <div className="relative w-full h-2.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
        {/* Zero-line marker at position 33.3% (since range is [-500, +1000]) */}
        <div
          className="absolute top-0 h-full w-px bg-zinc-400 dark:bg-zinc-600"
          style={{ left: '33.3%' }}
        />
        {/* Fill: green for positive spread, red for negative */}
        {isPositive ? (
          <div
            className="absolute top-0 h-full bg-green-500"
            style={{ left: '33.3%', width: `${barPosition - 33.3}%` }}
          />
        ) : (
          <div
            className="absolute top-0 h-full bg-red-500"
            style={{ left: `${barPosition}%`, width: `${33.3 - barPosition}%` }}
          />
        )}
      </div>
      <p className={`text-[11px] mt-2 font-mono ${
        isPositive ? 'text-green-600' : 'text-red-500'
      }`}>
        ROE {fmtPct(bb.roe, 1)} − CoE {fmtPct(bb.coe, 1)} = {fmtBps(spreadBps)} · {label}
      </p>
    </div>
  );
}

// ── 4. Capital Return card ────────────────────────────────────────────────

function CapitalReturnCard({ bb, sym }: { bb: BankBreakdown; sym: string }) {
  const totalYield = (bb.dividend_yield ?? 0) + (bb.buyback_yield ?? 0);
  const hasAnyYield = bb.dividend_yield != null || bb.buyback_yield != null;
  if (!hasAnyYield) return null;

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 text-center">
      <p className={`${SECTION_HEADING_CLS} mb-3`}>Capital Return</p>
      <p className="text-5xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
        {fmtPct(totalYield, 1)}
      </p>
      <p className="text-base font-semibold text-green-600 mt-2">total yield</p>
      <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">div + buyback, TTM</p>

      {/* 4-metric row */}
      <div className="grid grid-cols-2 gap-3 mt-5 text-left">
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1`}>Div yield</p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
            {fmtPct(bb.dividend_yield, 2)}
          </p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">DPS {fmtMoney(bb.dps, sym)}</p>
        </div>
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1`}>Buyback yield</p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
            {fmtPct(bb.buyback_yield, 2)}
          </p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">{fmtBn(bb.buybacks_usd, sym)}</p>
        </div>
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-3.5">
          <p className={`${SECTION_HEADING_CLS} mb-1`}>Payout ratio</p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
            {fmtPct(bb.total_payout_ratio, 0)}
          </p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">of net income</p>
        </div>
        <div className={`rounded-xl border p-3.5 ${
          bb.cet1_surplus_usd != null && bb.cet1_surplus_usd > 0
            ? 'border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/40'
            : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900'
        }`}>
          <p className={`${SECTION_HEADING_CLS} mb-1 ${
            bb.cet1_surplus_usd != null && bb.cet1_surplus_usd > 0 ? 'text-green-600' : ''
          }`}>Distributable</p>
          <p className={`text-xl font-bold tabular-nums ${
            bb.cet1_surplus_usd != null && bb.cet1_surplus_usd > 0
              ? 'text-green-700 dark:text-green-400' : 'text-zinc-900 dark:text-zinc-50'
          }`}>
            {fmtBn(bb.cet1_surplus_usd, sym)}
          </p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">CET1 surplus vs target</p>
        </div>
      </div>
    </div>
  );
}

// ── 5-6. Generic history bar chart (PPOP / NIM / BVPS / CIR / ROE) ───────

function HistoryChart({ title, unit, data, color, caption, yFormat }: {
  title: string;
  unit: string;
  data: Array<{ period: string; value: number | null }>;
  color: string;
  caption?: string;
  yFormat?: 'money' | 'pct';
}) {
  const rows = data.filter(d => d.value != null).map(d => ({ period: d.period, value: d.value as number }));
  if (rows.length < 2) {
    return (
      <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5">
        <div className="flex items-center justify-between mb-2">
          <p className={SECTION_HEADING_CLS}>{title}</p>
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{unit}</span>
        </div>
        <p className="text-[11px] text-zinc-400 dark:text-zinc-500 italic py-6 text-center">
          Insufficient data ({rows.length} of 5 years). Data vendor does not expose {title.toLowerCase()} for this ticker.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5">
      <div className="flex items-center justify-between mb-2">
        <p className={SECTION_HEADING_CLS}>{title}</p>
        <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{unit}</span>
      </div>
      {caption && <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-3">{caption}</p>}
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
              cursor={{ fill: 'rgba(113 113 122 / 0.2)' }}
              contentStyle={{
                background: 'rgb(24 24 27)',
                border: '1px solid rgb(63 63 70)',
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={((value: number) => {
                const v = typeof value === 'number' ? value : Number(value);
                if (yFormat === 'pct')   return [`${(v * 100).toFixed(2)}%`, title] as [string, string];
                if (unit.includes('USD m'))  return [`$${(v / 1e6).toFixed(0)}M`, title] as [string, string];
                if (unit.includes('USD'))    return [`$${v.toFixed(2)}`, title] as [string, string];
                return [v.toLocaleString(), title] as [string, string];
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

// ── NIM sensitivity tile (renders inside NIM History when research provided it) ──

function NIMSensitivityTile({ bb }: { bb: BankBreakdown }) {
  if (bb.nim_rate_sensitivity_bps == null) return null;
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950/50 p-3 mt-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500 dark:text-zinc-400">
          Rate sensitivity
        </span>
        <span className="text-sm font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
          {bb.nim_rate_sensitivity_bps.toFixed(0)} bps NIM per 1 bp rate
        </span>
      </div>
      {bb.forward_nim_guidance && (
        <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-1 italic">
          {bb.forward_nim_guidance}
        </p>
      )}
    </div>
  );
}

// ── 7. Loan Growth — 5y chart OR single-year tile fallback ───────────────

function LoanGrowthCard({ bb, sym }: { bb: BankBreakdown; sym: string }) {
  const hist = bb.loans_history ?? [];
  const historyRows = hist.filter(d => d.value != null && d.value > 0);

  // Tier 1 — FMP has ≥3 years of loan data → full bar chart
  if (historyRows.length >= 3) {
    return (
      <HistoryChart
        title="Loan Book"
        unit={`${sym} · 5Y`}
        data={hist}
        color="#0ea5e9"
        caption="Loan book growth is the earnings engine for banks"
      />
    );
  }

  // Tier 2 — research-extracted loan_growth_yoy → single-number tile
  if (bb.loan_growth_yoy != null) {
    const positive = bb.loan_growth_yoy >= 0;
    return (
      <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 text-center">
        <p className={`${SECTION_HEADING_CLS} mb-3`}>Loan Growth (YoY)</p>
        <p className={`text-5xl font-bold tabular-nums ${
          positive ? 'text-green-600' : 'text-red-500'
        }`}>
          {positive ? '+' : ''}{(bb.loan_growth_yoy * 100).toFixed(1)}%
        </p>
        {bb.deposit_growth_yoy != null && (
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-2">
            Deposits {bb.deposit_growth_yoy >= 0 ? '+' : ''}{(bb.deposit_growth_yoy * 100).toFixed(1)}% ·
            LDR {fmtPct(bb.loan_to_deposit_ratio, 0)}
          </p>
        )}
        {bb.forward_loan_growth_guidance && (
          <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-3 italic px-2">
            {bb.forward_loan_growth_guidance}
          </p>
        )}
      </div>
    );
  }

  // Tier 3 — no data; hide the card entirely
  return null;
}

// ── 8. Book Quality card ─────────────────────────────────────────────────

function BookQualityCard({ bb, sym }: { bb: BankBreakdown; sym: string }) {
  // Render only when at least one research-extracted field is populated
  const hasAny =
    bb.npl_ratio != null ||
    bb.npl_coverage_ratio != null ||
    bb.management_overlays_bn != null ||
    bb.net_charge_offs_pct != null;
  if (!hasAny) return null;

  // NPL coverage color: green ≥ 100%, amber 80-100%, red < 80%
  const covColor = bb.npl_coverage_ratio != null
    ? (bb.npl_coverage_ratio >= 1.0 ? 'green' : bb.npl_coverage_ratio >= 0.8 ? 'amber' : 'red')
    : 'muted';

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5">
      <p className={`${SECTION_HEADING_CLS} mb-4`}>Book Quality</p>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm mb-3">
        {bb.npl_ratio != null && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-500 dark:text-zinc-400">NPL ratio</span>
            <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">{fmtPct(bb.npl_ratio, 2)}</span>
          </div>
        )}
        {bb.npl_coverage_ratio != null && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-500 dark:text-zinc-400">NPL coverage</span>
            <span className={`font-semibold tabular-nums ${
              covColor === 'green' ? 'text-green-600' :
              covColor === 'red'   ? 'text-red-500'   :
              covColor === 'amber' ? 'text-amber-500' : 'text-zinc-900 dark:text-zinc-50'
            }`}>
              {(bb.npl_coverage_ratio * 100).toFixed(0)}%
            </span>
          </div>
        )}
        {bb.net_charge_offs_pct != null && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-500 dark:text-zinc-400">Net charge-offs</span>
            <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">{fmtPct(bb.net_charge_offs_pct, 2)}</span>
          </div>
        )}
        {bb.management_overlays_bn != null && (
          <div className="flex items-center justify-between">
            <span className="text-zinc-500 dark:text-zinc-400">Mgmt overlays</span>
            <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
              {sym}{bb.management_overlays_bn.toFixed(2)}B
            </span>
          </div>
        )}
      </div>

      {/* Coverage gauge */}
      {bb.npl_coverage_ratio != null && (
        <>
          <div className="relative w-full h-2 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
            <div
              className={`absolute top-0 left-0 h-full rounded-full ${
                covColor === 'green' ? 'bg-green-500' :
                covColor === 'red'   ? 'bg-red-500'   :
                covColor === 'amber' ? 'bg-amber-500' : 'bg-zinc-400'
              }`}
              style={{ width: `${Math.min(100, bb.npl_coverage_ratio * 50)}%` }}
            />
            {/* 100% safety line */}
            <div className="absolute top-0 h-full w-px bg-zinc-400 dark:bg-zinc-500" style={{ left: '50%' }} />
          </div>
          <p className="text-[10px] text-zinc-500 dark:text-zinc-400 mt-1 font-mono">
            coverage = provisions / NPLs · 100% = line
          </p>
        </>
      )}

      {bb.research_evidence && (
        <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-3 italic">
          Source: {bb.research_evidence.slice(0, 200)}
        </p>
      )}
    </div>
  );
}

// ── Top-level composite ──────────────────────────────────────────────────

export interface BankValuationPanelProps {
  dcfRange?: DcfRange;
  currentPrice?: number;
  ticker: string;
}

export function BankValuationPanel({ dcfRange, currentPrice, ticker }: BankValuationPanelProps) {
  const bb = dcfRange?.bank_breakdown;
  const sym = currencySymbol(ticker);
  if (!dcfRange || !bb) return null;

  // Panels 5, 6 are history-bar-chart driven. NIM panel appends
  // NIMSensitivityTile beneath the chart when research data is available.
  return (
    <div className="flex flex-col gap-4">
      <PTBVHeroCard bb={bb} price={currentPrice} sym={sym} ticker={ticker} />
      <BankKeyStats bb={bb} sym={sym} />
      <ROEGauge bb={bb} />
      <CapitalReturnCard bb={bb} sym={sym} />

      {/* PPOP history */}
      {bb.ppop_history && bb.ppop_history.length > 0 && (
        <HistoryChart
          title="Pre-Provision Operating Profit"
          unit="USD m · 5Y"
          data={bb.ppop_history}
          color="#8b0000"
          caption="Core operating quality — strips cyclical provisioning noise"
        />
      )}

      {/* NIM history (+ optional rate-sensitivity tile appended) */}
      {bb.nim_history && bb.nim_history.length > 0 && (
        <div>
          <HistoryChart
            title="Net Interest Margin"
            unit="% · 5Y"
            data={bb.nim_history}
            color="#dc2626"
            caption="Rate-cycle driven — key operating metric"
            yFormat="pct"
          />
          <NIMSensitivityTile bb={bb} />
        </div>
      )}

      {/* Loan Growth — full chart OR fallback tile */}
      <LoanGrowthCard bb={bb} sym={sym} />

      {/* Book Quality — research-sourced, only renders when at least one field present */}
      <BookQualityCard bb={bb} sym={sym} />
    </div>
  );
}
