/**
 * TechValuationPanel
 * -------------------
 * Sub-type-aware analyst view for Tech tickers. Routes to one of three
 * sub-views based on the backend-resolved profile name:
 *   - 'hyperscaler'  → big-cap cloud / tech conglomerate (MSFT, GOOGL, AMZN)
 *   - 'mature_saas'  → durable enterprise SaaS (CRM, ADBE, INTU)
 *   - 'growth_saas'  → hyper-growth consumption / cybersecurity (SNOW, CRWD,
 *                      DDOG)
 *
 * Design DNA matches REIT / Bank / Biopharma panels:
 *   - uppercase tracking-[0.2em] section headings in zinc-500/400
 *   - `rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white
 *      dark:bg-zinc-900 p-4`
 *   - functional colors only (green-600 / red-500 / amber-600 / blue-600)
 *
 * Data sources — NO FABRICATION:
 *   - `dcfRange.{bear|base|bull}.intrinsic_value` for the ScenarioStrip
 *   - `rawFinancials` FY-keyed dict → latest FY for Revenue / Capex / FCF /
 *     Op Margin / SBC / Gross Margin
 *   - `saasMetrics` extractor (`src/agents/industry/deep_research.py::
 *     _extract_saas_metrics`) for NRR / Rule-of-40 / CAC payback /
 *     Magic Number / Gross Retention
 *   - `sections` dict keyed by Section 2 subsection id for narrative cards
 *
 * Graceful degradation:
 *   - Top-level returns null if `dcfRange` is absent OR if the profile can't
 *     be classified into a sub-type (`classifyTechProfile` returns null).
 *     That lets the caller's generic ValuationLadder fall through — we NEVER
 *     render a generic Tech panel for an unknown profile.
 *   - Individual tiles / rows hide when their source field is null; there's
 *     no filler zero or "TBD" placeholder.
 */

import type { DcfRange, SaasMetrics } from '@/lib/reportTypes';
import { classifyTechSubtype, currencySymbol, type TechSubtype } from '@/lib/utils';
import { ResearchNarrativeCard } from '@/components/report/shared/ResearchNarrativeCard';

const SECTION_HEADING_CLS =
  'text-[11px] font-semibold uppercase tracking-[0.2em] text-zinc-500 dark:text-zinc-400';

// ── Formatters ─────────────────────────────────────────────────────────────

const fmtMoney = (v: number | null | undefined, sym: string, decimals = 2): string => {
  if (v == null || isNaN(v)) return '—';
  return `${sym}${v.toFixed(decimals)}`;
};

// Format a raw USD revenue / capex number (scale-detected) as $XB / $XM.
// Mirrors BiopharmaValuationPanel's fmtBn. Callers pass raw FMP dollars.
const fmtBn = (v: number | null | undefined, sym: string): string => {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  // FMP values are in $ (not $B). Detect scale.
  const bn = (abs > 1e6) ? v / 1e9 : v;
  const absBn = Math.abs(bn);
  if (absBn >= 10)   return `${sym}${bn.toFixed(0)}B`;
  if (absBn >= 1)    return `${sym}${bn.toFixed(1)}B`;
  if (absBn >= 0.01) return `${sym}${(bn * 1000).toFixed(0)}M`;
  return `${sym}${bn.toFixed(3)}B`;
};

const fmtPct = (v: number | null | undefined, decimals = 0, plus = false): string => {
  if (v == null || isNaN(v)) return '—';
  const s = (v * 100).toFixed(decimals);
  const isPos = v >= 0;
  return (plus && isPos ? '+' : '') + s + '%';
};

// ── FY helpers ─────────────────────────────────────────────────────────────

/** Get the keys of rawFinancials sorted ascending so `[length-1]` is newest. */
function fyKeys(rawFinancials: Record<string, unknown> | undefined): string[] {
  if (!rawFinancials || typeof rawFinancials !== 'object') return [];
  return Object.keys(rawFinancials)
    .filter(k => rawFinancials[k] && typeof rawFinancials[k] === 'object')
    .sort();
}

function asNum(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === 'number' ? v : parseFloat(String(v));
  return isNaN(n) ? null : n;
}

interface FYRow {
  revenue: number | null;
  free_cash_flow: number | null;
  capital_expenditure: number | null;
  operating_income: number | null;
  gross_profit: number | null;
  gross_margin: number | null;
  stock_based_compensation: number | null;
}

function getFY(
  rawFinancials: Record<string, unknown> | undefined,
  key: string | undefined,
): FYRow | null {
  if (!rawFinancials || !key) return null;
  const row = rawFinancials[key] as Record<string, unknown> | undefined;
  if (!row || typeof row !== 'object') return null;
  return {
    revenue:                  asNum(row.revenue),
    free_cash_flow:           asNum(row.free_cash_flow),
    capital_expenditure:      asNum(row.capital_expenditure),
    operating_income:         asNum(row.operating_income),
    gross_profit:             asNum(row.gross_profit),
    gross_margin:             asNum(row.gross_margin),
    stock_based_compensation: asNum(row.stock_based_compensation),
  };
}

// ── ScenarioStrip (shared across all 3 sub-types) ─────────────────────────

function ScenarioStrip({
  dcfRange, currentPrice, sym,
}: {
  dcfRange: DcfRange;
  currentPrice: number | undefined;
  sym: string;
}) {
  const bear = dcfRange.bear?.intrinsic_value ?? null;
  const base = dcfRange.base?.intrinsic_value ?? null;
  const bull = dcfRange.bull?.intrinsic_value ?? null;

  const delta = (iv: number | null): number | null => {
    if (iv == null || !currentPrice || currentPrice <= 0) return null;
    return (iv - currentPrice) / currentPrice;
  };

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex items-center justify-between mb-3">
        <p className={SECTION_HEADING_CLS}>Scenario Fair Value</p>
        {currentPrice != null && (
          <span className="text-[10px] text-zinc-500 dark:text-zinc-400 tabular-nums">
            current {fmtMoney(currentPrice, sym)}
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-3">
        {/* BEAR */}
        <div className="text-center">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-rose-500 dark:text-rose-400">
            Bear
          </p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50 mt-0.5">
            {bear != null ? fmtMoney(bear, sym, bear >= 100 ? 0 : 2) : '—'}
          </p>
          <p className="text-[11px] font-semibold tabular-nums text-rose-600 dark:text-rose-400 mt-0.5">
            {delta(bear) != null ? fmtPct(delta(bear), 1, true) : '—'}
          </p>
        </div>
        {/* BASE */}
        <div className="text-center border-x border-zinc-200 dark:border-zinc-800">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-blue-500 dark:text-blue-400">
            Base
          </p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50 mt-0.5">
            {base != null ? fmtMoney(base, sym, base >= 100 ? 0 : 2) : '—'}
          </p>
          <p className="text-[11px] font-semibold tabular-nums text-blue-600 dark:text-blue-400 mt-0.5">
            {delta(base) != null ? fmtPct(delta(base), 1, true) : '—'}
          </p>
        </div>
        {/* BULL */}
        <div className="text-center">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-emerald-500 dark:text-emerald-400">
            Bull
          </p>
          <p className="text-xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50 mt-0.5">
            {bull != null ? fmtMoney(bull, sym, bull >= 100 ? 0 : 2) : '—'}
          </p>
          <p className="text-[11px] font-semibold tabular-nums text-emerald-600 dark:text-emerald-400 mt-0.5">
            {delta(bull) != null ? fmtPct(delta(bull), 1, true) : '—'}
          </p>
        </div>
      </div>
    </div>
  );
}

// ── KPITile: shared 6-up grid cell ────────────────────────────────────────

function KPITile({
  label, value, sub, tone = 'neutral',
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'green' | 'amber' | 'red' | 'neutral';
}) {
  const toneCls =
    tone === 'green' ? 'text-green-600' :
    tone === 'amber' ? 'text-amber-600' :
    tone === 'red'   ? 'text-red-500'   :
                       'text-zinc-900 dark:text-zinc-50';
  return (
    <div>
      <p className="text-[10px] text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
        {label}
      </p>
      <p className={`text-xl font-bold tabular-nums ${toneCls} mt-0.5`}>
        {value}
      </p>
      {sub && (
        <p className="text-[9px] text-zinc-400 mt-0.5">{sub}</p>
      )}
    </div>
  );
}

// ── Sub-type banner strip (visual marker for the sub-type view) ───────────

function SubtypeBanner({
  subtype, ticker,
}: {
  subtype: TechSubtype;
  ticker: string;
}) {
  if (subtype === 'hyperscaler') {
    return (
      <div className="rounded-xl bg-gradient-to-r from-indigo-100 to-indigo-50 dark:from-indigo-950/60 dark:to-indigo-950/20 border border-indigo-200 dark:border-indigo-900 px-4 py-2.5">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-indigo-700 dark:text-indigo-300">
          Hyperscaler · AI Capex ROI
        </p>
        <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-50 mt-0.5">{ticker}</p>
      </div>
    );
  }
  if (subtype === 'mature_saas') {
    return (
      <div className="rounded-xl bg-gradient-to-r from-sky-100 to-sky-50 dark:from-sky-950/60 dark:to-sky-950/20 border border-sky-200 dark:border-sky-900 px-4 py-2.5">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-sky-700 dark:text-sky-300">
          Mature SaaS · Durability
        </p>
        <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-50 mt-0.5">{ticker}</p>
      </div>
    );
  }
  // growth_saas
  return (
    <div className="rounded-xl bg-gradient-to-r from-emerald-100 to-emerald-50 dark:from-emerald-950/60 dark:to-emerald-950/20 border border-emerald-200 dark:border-emerald-900 px-4 py-2.5">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-emerald-700 dark:text-emerald-300">
        Growth SaaS · Unit Economics
      </p>
      <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-50 mt-0.5">{ticker}</p>
    </div>
  );
}

// ── Hyperscaler view ──────────────────────────────────────────────────────

function HyperscalerView({
  ticker, sections, rawFinancials, sym,
}: {
  ticker: string;
  sections?: Record<string, string>;
  rawFinancials?: Record<string, unknown>;
  sym: string;
}) {
  const keys = fyKeys(rawFinancials);
  const latestKey = keys.length > 0 ? keys[keys.length - 1] : undefined;
  const prevKey   = keys.length > 1 ? keys[keys.length - 2] : undefined;
  const latest = getFY(rawFinancials, latestKey);
  const prev   = getFY(rawFinancials, prevKey);
  const section2F = sections?.["2f"] ?? sections?.["2F"] ?? null;

  // Tile values — only computed when source fields are present
  const revenue = latest?.revenue ?? null;
  const revenueYoY = (latest?.revenue && prev?.revenue && prev.revenue > 0)
    ? (latest.revenue / prev.revenue) - 1
    : null;

  const capexIntensity = (latest?.capital_expenditure != null && latest?.revenue && latest.revenue > 0)
    ? Math.abs(latest.capital_expenditure) / latest.revenue
    : null;

  const fcfMargin = (latest?.free_cash_flow != null && latest?.revenue && latest.revenue > 0)
    ? latest.free_cash_flow / latest.revenue
    : null;

  // Op margin: prefer operating_income / revenue; fall back to gross_margin
  let opMargin: number | null = null;
  if (latest?.operating_income != null && latest?.revenue && latest.revenue > 0) {
    opMargin = latest.operating_income / latest.revenue;
  } else if (latest?.gross_margin != null) {
    opMargin = latest.gross_margin;
  }

  const sbcPctRev = (latest?.stock_based_compensation != null && latest?.revenue && latest.revenue > 0)
    ? latest.stock_based_compensation / latest.revenue
    : null;

  // Tone helpers
  const fcfTone: 'green' | 'amber' | 'red' | 'neutral' = fcfMargin == null ? 'neutral'
    : fcfMargin > 0.20 ? 'green' : fcfMargin > 0.10 ? 'amber' : 'red';
  const capexTone: 'green' | 'amber' | 'red' | 'neutral' = capexIntensity == null ? 'neutral'
    : capexIntensity > 0.20 ? 'amber' : 'neutral';
  const sbcTone: 'green' | 'amber' | 'red' | 'neutral' = sbcPctRev == null ? 'neutral'
    : sbcPctRev > 0.10 ? 'red' : 'neutral';
  const revYoYTone: 'green' | 'amber' | 'red' | 'neutral' = revenueYoY == null ? 'neutral'
    : revenueYoY > 0.15 ? 'green' : revenueYoY > 0.05 ? 'neutral' : 'red';
  const opMarginTone: 'green' | 'amber' | 'red' | 'neutral' = opMargin == null ? 'neutral'
    : opMargin > 0.30 ? 'green' : opMargin > 0.15 ? 'neutral' : 'red';

  // Build tile list (skip individual tiles whose source is missing)
  const tiles: Array<React.ReactNode> = [];
  if (revenue != null) {
    tiles.push(
      <KPITile key="revenue" label="Revenue" value={fmtBn(revenue, sym)} sub={latestKey} />
    );
  }
  if (revenueYoY != null) {
    tiles.push(
      <KPITile key="rev-yoy" label="Revenue YoY" value={fmtPct(revenueYoY, 0, true)} sub="latest FY" tone={revYoYTone} />
    );
  }
  if (capexIntensity != null) {
    tiles.push(
      <KPITile key="capex" label="Capex Intensity" value={fmtPct(capexIntensity, 0)} sub="of revenue" tone={capexTone} />
    );
  }
  if (fcfMargin != null) {
    tiles.push(
      <KPITile key="fcf" label="FCF Margin" value={fmtPct(fcfMargin, 0)} sub="FCF / revenue" tone={fcfTone} />
    );
  }
  if (opMargin != null) {
    tiles.push(
      <KPITile key="op" label="Op Margin" value={fmtPct(opMargin, 0)}
        sub={latest?.operating_income != null ? 'GAAP' : 'gross margin'} tone={opMarginTone} />
    );
  }
  if (sbcPctRev != null) {
    tiles.push(
      <KPITile key="sbc" label="SBC % Rev" value={fmtPct(sbcPctRev, 1)}
        sub={sbcPctRev > 0.10 ? 'elevated' : 'disciplined'} tone={sbcTone} />
    );
  }

  // Revenue trend mini-bar — last 3 FYs, only when we have >=2 years
  const last3Keys = keys.slice(-3);
  const trendRows = last3Keys.map(k => {
    const row = getFY(rawFinancials, k);
    return {
      period: k,
      revenue: row?.revenue ?? null,
      capex: row?.capital_expenditure != null ? Math.abs(row.capital_expenditure) : null,
    };
  }).filter(r => r.revenue != null) as Array<{ period: string; revenue: number; capex: number | null }>;
  const maxRevenue = trendRows.reduce((m, r) => Math.max(m, r.revenue), 0);
  const showTrend = trendRows.length >= 2 && maxRevenue > 0;

  return (
    <>
      <SubtypeBanner subtype="hyperscaler" ticker={ticker} />

      {tiles.length > 0 && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
          <p className={`${SECTION_HEADING_CLS} mb-3`}>Key Metrics</p>
          <div className="grid grid-cols-3 gap-3">
            {tiles}
          </div>
        </div>
      )}

      {showTrend && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <p className={SECTION_HEADING_CLS}>Revenue Trend · last {trendRows.length} FY</p>
            <span className="text-[10px] text-zinc-500 dark:text-zinc-400">
              bar = revenue · overlay = capex
            </span>
          </div>
          <div className="flex flex-col gap-2">
            {trendRows.map((r) => {
              const revWidth = (r.revenue / maxRevenue) * 100;
              const capexWidth = (r.capex != null && r.revenue > 0)
                ? Math.min(100, (r.capex / r.revenue) * 100) * (revWidth / 100)
                : null;
              return (
                <div key={r.period} className="flex items-center gap-2">
                  <span className="w-14 text-[10px] font-mono text-zinc-500 dark:text-zinc-400 shrink-0">
                    {r.period}
                  </span>
                  <div className="flex-1 h-6 rounded bg-zinc-100 dark:bg-zinc-800 relative overflow-hidden">
                    <div className="absolute inset-y-0 left-0 bg-blue-500 dark:bg-blue-600"
                         style={{ width: `${revWidth}%` }}></div>
                    {capexWidth != null && (
                      <div className="absolute inset-y-0 left-0 bg-amber-500/60 dark:bg-amber-500/40"
                           style={{ width: `${capexWidth}%` }}
                           title={`capex ${((r.capex! / r.revenue) * 100).toFixed(0)}%`}></div>
                    )}
                  </div>
                  <span className="w-14 text-right text-[11px] font-mono tabular-nums text-zinc-900 dark:text-zinc-50 shrink-0">
                    {fmtBn(r.revenue, sym)}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-zinc-500 dark:text-zinc-400 mt-3 font-mono leading-relaxed">
            Revenue from FMP · capex overlay = |capital_expenditure| / revenue
          </p>
        </div>
      )}

      <ResearchNarrativeCard
        title="AI Capex ROI Commentary"
        sectionText={section2F}
        subsection="2F.2"
        sourceLabel="Deep research · Section 2F.2"
      />
      <ResearchNarrativeCard
        title="Regulatory Overhang"
        sectionText={section2F}
        subsection="2F.6"
        sourceLabel="Deep research · Section 2F.6"
      />
    </>
  );
}

// ── Mature SaaS view ──────────────────────────────────────────────────────

function MatureSaasView({
  ticker, sections, rawFinancials, saasMetrics, sym,
}: {
  ticker: string;
  sections?: Record<string, string>;
  rawFinancials?: Record<string, unknown>;
  saasMetrics?: SaasMetrics;
  sym: string;
}) {
  const keys = fyKeys(rawFinancials);
  const latestKey = keys.length > 0 ? keys[keys.length - 1] : undefined;
  const prevKey   = keys.length > 1 ? keys[keys.length - 2] : undefined;
  const latest = getFY(rawFinancials, latestKey);
  const prev   = getFY(rawFinancials, prevKey);
  const section2F = sections?.["2f"] ?? sections?.["2F"] ?? null;

  const revenue = latest?.revenue ?? null;
  const revenueYoY = (latest?.revenue && prev?.revenue && prev.revenue > 0)
    ? (latest.revenue / prev.revenue) - 1
    : null;

  // Post-SBC FCF %: (FCF - SBC) / revenue
  const postSbcFcfPct = (
    latest?.free_cash_flow != null
    && latest?.stock_based_compensation != null
    && latest?.revenue && latest.revenue > 0
  )
    ? (latest.free_cash_flow - latest.stock_based_compensation) / latest.revenue
    : null;

  // Gross margin: prefer field direct, fall back to gross_profit / revenue
  let grossMargin: number | null = null;
  if (latest?.gross_margin != null) {
    grossMargin = latest.gross_margin;
  } else if (latest?.gross_profit != null && latest?.revenue && latest.revenue > 0) {
    grossMargin = latest.gross_profit / latest.revenue;
  }

  const sbcPctRev = (latest?.stock_based_compensation != null && latest?.revenue && latest.revenue > 0)
    ? latest.stock_based_compensation / latest.revenue
    : null;

  const nrr = saasMetrics?.nrr_pct ?? null;
  const ruleOf40 = saasMetrics?.rule_of_40_score ?? null;

  // Tones
  const nrrTone: 'green' | 'amber' | 'red' | 'neutral' = nrr == null ? 'neutral'
    : nrr > 1.15 ? 'green' : nrr >= 1.0 ? 'amber' : 'red';
  const ruleTone: 'green' | 'amber' | 'red' | 'neutral' = ruleOf40 == null ? 'neutral'
    : ruleOf40 > 40 ? 'green' : ruleOf40 >= 20 ? 'amber' : 'red';
  const postSbcTone: 'green' | 'amber' | 'red' | 'neutral' = postSbcFcfPct == null ? 'neutral'
    : postSbcFcfPct > 0.15 ? 'green' : postSbcFcfPct >= 0.05 ? 'amber' : 'red';
  const sbcTone: 'green' | 'amber' | 'red' | 'neutral' = sbcPctRev == null ? 'neutral'
    : sbcPctRev > 0.12 ? 'red' : sbcPctRev > 0.08 ? 'amber' : 'neutral';

  const tiles: Array<React.ReactNode> = [];
  if (revenue != null) {
    tiles.push(
      <KPITile key="arr" label="Revenue (ARR proxy)" value={fmtBn(revenue, sym)} sub={latestKey} />
    );
  }
  if (nrr != null) {
    tiles.push(
      <KPITile key="nrr" label="NRR" value={`${(nrr * 100).toFixed(0)}%`} sub="net expansion" tone={nrrTone} />
    );
  }
  if (ruleOf40 != null) {
    tiles.push(
      <KPITile key="rule" label="Rule of 40" value={ruleOf40.toFixed(0)} sub="growth % + FCF %" tone={ruleTone} />
    );
  }
  if (postSbcFcfPct != null) {
    tiles.push(
      <KPITile key="postsbc" label="Post-SBC FCF %" value={fmtPct(postSbcFcfPct, 0)} sub="true FCF" tone={postSbcTone} />
    );
  }
  if (grossMargin != null) {
    tiles.push(
      <KPITile key="gm" label="Gross Margin" value={fmtPct(grossMargin, 0)} sub="gross profit / rev" />
    );
  }
  if (sbcPctRev != null) {
    tiles.push(
      <KPITile key="sbc" label="SBC % Rev" value={fmtPct(sbcPctRev, 1)}
        sub={sbcPctRev > 0.08 ? 'elevated' : 'disciplined'} tone={sbcTone} />
    );
  }

  // Rule-of-40 decomposition: revenue growth + FCF margin (FMP-derived)
  const fcfMargin = (latest?.free_cash_flow != null && latest?.revenue && latest.revenue > 0)
    ? latest.free_cash_flow / latest.revenue
    : null;
  const showDecomp = (revenueYoY != null && fcfMargin != null);
  const decompSum = showDecomp ? (revenueYoY! * 100) + (fcfMargin! * 100) : null;
  // Widths normalized to 100% of the bar — show relative proportions when both positive
  const growthPct = revenueYoY != null ? revenueYoY * 100 : 0;
  const fcfPct    = fcfMargin  != null ? fcfMargin  * 100 : 0;
  const total = Math.max(0.01, Math.abs(growthPct) + Math.abs(fcfPct));
  const growthW = (Math.abs(growthPct) / total) * 100;
  const fcfW    = (Math.abs(fcfPct)    / total) * 100;
  const decompTone: 'green' | 'amber' | 'red' | 'neutral' = decompSum == null ? 'neutral'
    : decompSum > 40 ? 'green' : decompSum >= 20 ? 'amber' : 'red';

  return (
    <>
      <SubtypeBanner subtype="mature_saas" ticker={ticker} />

      {tiles.length > 0 && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
          <p className={`${SECTION_HEADING_CLS} mb-3`}>Key Metrics</p>
          <div className="grid grid-cols-3 gap-3">
            {tiles}
          </div>
        </div>
      )}

      {showDecomp && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <p className={SECTION_HEADING_CLS}>Rule of 40 · Decomposition</p>
            <span className={`text-lg font-bold tabular-nums ${
              decompTone === 'green' ? 'text-green-600' :
              decompTone === 'amber' ? 'text-amber-600' :
              decompTone === 'red'   ? 'text-red-500'   :
                                       'text-zinc-900 dark:text-zinc-50'
            }`}>
              {decompSum != null ? decompSum.toFixed(0) : '—'}
            </span>
          </div>
          <div className="w-full h-8 rounded-lg overflow-hidden flex border border-zinc-200 dark:border-zinc-800">
            <div className="bg-blue-500 flex items-center justify-center text-white text-[11px] font-semibold"
                 style={{ width: `${growthW}%` }}
                 title={`Growth ${growthPct.toFixed(0)}%`}>
              {growthW > 15 ? `Growth ${growthPct.toFixed(0)}%` : ''}
            </div>
            <div className="bg-green-500 flex items-center justify-center text-white text-[11px] font-semibold"
                 style={{ width: `${fcfW}%` }}
                 title={`FCF ${fcfPct.toFixed(0)}%`}>
              {fcfW > 15 ? `FCF ${fcfPct.toFixed(0)}%` : ''}
            </div>
          </div>
          <ul className="flex flex-col gap-1 mt-3 text-xs">
            <li className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-blue-500"></span>
              <span className="text-zinc-700 dark:text-zinc-300 flex-1">Revenue Growth</span>
              <span className="tabular-nums font-semibold text-zinc-900 dark:text-zinc-50">
                {growthPct.toFixed(0)}%
              </span>
            </li>
            <li className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500"></span>
              <span className="text-zinc-700 dark:text-zinc-300 flex-1">FCF Margin</span>
              <span className="tabular-nums font-semibold text-zinc-900 dark:text-zinc-50">
                {fcfPct.toFixed(0)}%
              </span>
            </li>
          </ul>
          <p className="text-[10px] text-zinc-500 dark:text-zinc-400 mt-3 font-mono leading-relaxed">
            Target ≥40 healthy · ≥60 best-in-class
          </p>
        </div>
      )}

      <ResearchNarrativeCard
        title="NRR Trajectory"
        sectionText={section2F}
        subsection="2F.2"
        sourceLabel="Deep research · Section 2F.2"
      />
      <ResearchNarrativeCard
        title="AI Monetization Strategy"
        sectionText={section2F}
        subsection="2F.7"
        sourceLabel="Deep research · Section 2F.7"
      />
    </>
  );
}

// ── Growth SaaS view ──────────────────────────────────────────────────────

function GrowthSaasView({
  ticker, sections, rawFinancials, saasMetrics,
}: {
  ticker: string;
  sections?: Record<string, string>;
  rawFinancials?: Record<string, unknown>;
  saasMetrics?: SaasMetrics;
  sym: string;
}) {
  const keys = fyKeys(rawFinancials);
  const latestKey = keys.length > 0 ? keys[keys.length - 1] : undefined;
  const prevKey   = keys.length > 1 ? keys[keys.length - 2] : undefined;
  const latest = getFY(rawFinancials, latestKey);
  const prev   = getFY(rawFinancials, prevKey);
  const section2F = sections?.["2f"] ?? sections?.["2F"] ?? null;

  const revenueYoY = (latest?.revenue && prev?.revenue && prev.revenue > 0)
    ? (latest.revenue / prev.revenue) - 1
    : null;

  const nrr = saasMetrics?.nrr_pct ?? null;
  const gr  = saasMetrics?.gross_retention_pct ?? null;
  const cac = saasMetrics?.cac_payback_months ?? null;
  const magic = saasMetrics?.magic_number ?? null;
  const ruleOf40 = saasMetrics?.rule_of_40_score ?? null;

  // Post-SBC FCF % — used only in traffic-light table
  const postSbcFcfPct = (
    latest?.free_cash_flow != null
    && latest?.stock_based_compensation != null
    && latest?.revenue && latest.revenue > 0
  )
    ? (latest.free_cash_flow - latest.stock_based_compensation) / latest.revenue
    : null;

  // Tones
  const rYoYTone: 'green' | 'amber' | 'red' | 'neutral' = revenueYoY == null ? 'neutral'
    : revenueYoY > 0.30 ? 'green' : revenueYoY > 0.15 ? 'amber' : 'red';
  const nrrTone: 'green' | 'amber' | 'red' | 'neutral' = nrr == null ? 'neutral'
    : nrr > 1.15 ? 'green' : nrr >= 1.0 ? 'amber' : 'red';
  const grTone: 'green' | 'amber' | 'red' | 'neutral' = gr == null ? 'neutral'
    : gr > 0.95 ? 'green' : gr >= 0.90 ? 'amber' : 'red';
  const cacTone: 'green' | 'amber' | 'red' | 'neutral' = cac == null ? 'neutral'
    : cac < 18 ? 'green' : cac <= 30 ? 'amber' : 'red';
  const magicTone: 'green' | 'amber' | 'red' | 'neutral' = magic == null ? 'neutral'
    : magic > 1.0 ? 'green' : magic >= 0.5 ? 'amber' : 'red';
  const ruleTone: 'green' | 'amber' | 'red' | 'neutral' = ruleOf40 == null ? 'neutral'
    : ruleOf40 > 50 ? 'green' : ruleOf40 >= 30 ? 'amber' : 'red';

  const tiles: Array<React.ReactNode> = [];
  if (revenueYoY != null) {
    tiles.push(
      <KPITile key="growth" label="Revenue Growth" value={fmtPct(revenueYoY, 0, true)}
        sub="YoY FY" tone={rYoYTone} />
    );
  }
  if (nrr != null) {
    tiles.push(
      <KPITile key="nrr" label="NRR" value={`${(nrr * 100).toFixed(0)}%`}
        sub="net expansion" tone={nrrTone} />
    );
  }
  if (gr != null) {
    tiles.push(
      <KPITile key="gr" label="Gross Retention" value={fmtPct(gr, 0)}
        sub="low churn target" tone={grTone} />
    );
  }
  if (cac != null) {
    tiles.push(
      <KPITile key="cac" label="CAC Payback" value={`${cac.toFixed(0)}mo`}
        sub="<18mo healthy" tone={cacTone} />
    );
  }
  if (magic != null) {
    tiles.push(
      <KPITile key="magic" label="Magic Number" value={`${magic.toFixed(1)}x`}
        sub=">1.0 healthy" tone={magicTone} />
    );
  }
  if (ruleOf40 != null) {
    tiles.push(
      <KPITile key="rule" label="Rule of 40" value={ruleOf40.toFixed(0)}
        sub="growth + FCF margin" tone={ruleTone} />
    );
  }

  // Build traffic-light rows (hide rows whose input is null)
  type LightRow = {
    metric: string;
    value: string;
    tone: 'green' | 'amber' | 'red';
    label: string;
  };
  const lightRows: LightRow[] = [];
  if (nrr != null) {
    lightRows.push({
      metric: 'NRR',
      value: `${(nrr * 100).toFixed(0)}%`,
      tone: nrr > 1.15 ? 'green' : nrr >= 1.0 ? 'amber' : 'red',
      label: nrr > 1.15 ? 'Healthy' : nrr >= 1.0 ? 'Watch' : 'Weak',
    });
  }
  if (gr != null) {
    lightRows.push({
      metric: 'Gross Retention',
      value: fmtPct(gr, 0),
      tone: gr >= 0.90 ? (gr > 0.95 ? 'green' : 'amber') : 'red',
      label: gr > 0.95 ? 'Healthy' : gr >= 0.90 ? 'Watch' : 'Weak',
    });
  }
  if (cac != null) {
    lightRows.push({
      metric: 'CAC Payback',
      value: `${cac.toFixed(0)} mo`,
      tone: cac < 18 ? 'green' : cac <= 30 ? 'amber' : 'red',
      label: cac < 18 ? 'Healthy' : cac <= 30 ? 'Watch' : 'Weak',
    });
  }
  if (magic != null) {
    lightRows.push({
      metric: 'Magic Number',
      value: `${magic.toFixed(1)}x`,
      tone: magic > 1.0 ? 'green' : magic >= 0.5 ? 'amber' : 'red',
      label: magic > 1.0 ? 'Healthy' : magic >= 0.5 ? 'Watch' : 'Weak',
    });
  }
  if (postSbcFcfPct != null) {
    lightRows.push({
      metric: 'Post-SBC FCF',
      value: fmtPct(postSbcFcfPct, 0),
      tone: postSbcFcfPct > 0.10 ? 'green' : postSbcFcfPct >= 0 ? 'amber' : 'red',
      label: postSbcFcfPct > 0.10 ? 'Healthy' : postSbcFcfPct >= 0 ? 'Watch' : 'Weak',
    });
  }

  const dotCls = (tone: 'green' | 'amber' | 'red') =>
    tone === 'green' ? 'bg-green-500' : tone === 'amber' ? 'bg-amber-500' : 'bg-red-500';
  const textCls = (tone: 'green' | 'amber' | 'red') =>
    tone === 'green' ? 'text-green-600' : tone === 'amber' ? 'text-amber-600' : 'text-red-500';

  return (
    <>
      <SubtypeBanner subtype="growth_saas" ticker={ticker} />

      {tiles.length > 0 && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
          <p className={`${SECTION_HEADING_CLS} mb-3`}>Key Metrics</p>
          <div className="grid grid-cols-3 gap-3">
            {tiles}
          </div>
        </div>
      )}

      {lightRows.length > 0 && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
          <p className={`${SECTION_HEADING_CLS} mb-3`}>Unit Economics Traffic Light</p>
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="border-b border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400">
                <th className="text-left py-2 pr-2 font-semibold">Metric</th>
                <th className="text-right py-2 pr-2 font-semibold">Value</th>
                <th className="text-right py-2 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {lightRows.map((r, i) => (
                <tr key={r.metric} className={i < lightRows.length - 1
                  ? 'border-b border-zinc-100 dark:border-zinc-800/50' : ''}>
                  <td className="py-2 pr-2 text-zinc-900 dark:text-zinc-50 font-medium">
                    {r.metric}
                  </td>
                  <td className="py-2 pr-2 text-right font-mono tabular-nums text-zinc-900 dark:text-zinc-50">
                    {r.value}
                  </td>
                  <td className="py-2 text-right">
                    <span className="inline-flex items-center gap-1.5">
                      <span className={`w-2.5 h-2.5 rounded-full ${dotCls(r.tone)}`}></span>
                      <span className={`${textCls(r.tone)} font-semibold`}>{r.label}</span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[10px] text-zinc-500 dark:text-zinc-400 mt-3 font-mono leading-relaxed">
            Benchmarks: NRR ≥115 · GR ≥95 · CAC &lt;18mo · Magic &gt;1.0 · Post-SBC FCF &gt;10%
          </p>
        </div>
      )}

      <ResearchNarrativeCard
        title="Unit Economics Sustainability"
        sectionText={section2F}
        subsection="2F.4"
        sourceLabel="Deep research · Section 2F.4"
      />
      <ResearchNarrativeCard
        title="Path to Profitability"
        sectionText={section2F}
        subsection="2F.6"
        sourceLabel="Deep research · Section 2F.6"
      />
    </>
  );
}

// ── Top-level composite ──────────────────────────────────────────────────

export interface TechValuationPanelProps {
  dcfRange?: DcfRange;
  currentPrice?: number;
  ticker: string;
  /** Tech sub-type profile name, e.g. "Hyperscaler / Tech Conglomerate",
   *  "Mature SaaS", "Growth SaaS". Used to route to the correct sub-type view. */
  profile?: string;
  /** Deep research Section 2 blocks, keyed by subsection id ("2a"..."2f"). */
  sections?: Record<string, string>;
  /** FY-keyed raw financials dict for FMP-derived tiles. */
  rawFinancials?: Record<string, unknown>;
  /** SaaS metrics extractor output for the ticker. */
  saasMetrics?: SaasMetrics;
}

export function TechValuationPanel({
  dcfRange, currentPrice, ticker, profile,
  sections, rawFinancials, saasMetrics,
}: TechValuationPanelProps) {
  // Step 1: dcfRange is mandatory
  if (!dcfRange) return null;

  // Step 2: classify the sub-type. classifyTechSubtype uses profile FIRST,
  // then falls back to a ticker-table lookup for historical runs that don't
  // have profile_name in stored data. If we can't match a known sub-type,
  // return null — the caller falls through to the generic ValuationLadder.
  // This enforces the contract that sub-type screens render ONLY for their
  // sub-segment; unknown tech profiles don't get a generic Tech panel.
  const subtype = classifyTechSubtype(profile, ticker);
  if (subtype === null) return null;

  const sym = currencySymbol(ticker);

  return (
    <div className="flex flex-col gap-4">
      <ScenarioStrip dcfRange={dcfRange} currentPrice={currentPrice} sym={sym} />

      {subtype === 'hyperscaler' && (
        <HyperscalerView
          ticker={ticker}
          sections={sections}
          rawFinancials={rawFinancials}
          sym={sym}
        />
      )}
      {subtype === 'mature_saas' && (
        <MatureSaasView
          ticker={ticker}
          sections={sections}
          rawFinancials={rawFinancials}
          saasMetrics={saasMetrics}
          sym={sym}
        />
      )}
      {subtype === 'growth_saas' && (
        <GrowthSaasView
          ticker={ticker}
          sections={sections}
          rawFinancials={rawFinancials}
          saasMetrics={saasMetrics}
          sym={sym}
        />
      )}
    </div>
  );
}
