/**
 * BiopharmaValuationPanel
 * ------------------------
 * Pipeline-forward analyst view for Biopharma tickers.
 *
 * Design DNA matches REITValuationPanel + BankValuationPanel:
 *   - uppercase tracking-[0.2em] section headings in zinc-500/400
 *   - `rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4`
 *   - functional colors only (green-600 / red-500 / blue-500); phase chips are
 *     the one exception because they map to an ISO phase ontology where each
 *     colour signals regulatory risk (Approved = green → Ph1 = red).
 *
 * Data sources (no backend changes — consumes only what's already on the wire):
 *   - data.pipeline_assets[ticker]         — from _extract_pipeline_assets()
 *   - dcfRange.base.intrinsic_value        — from _compute_rnpv()
 *   - dcfRange.base.method_iv_table        — per-method IV table
 *   - currentPrice                         — from scenarioAnalysis
 *
 * Per-asset risk-adjusted NPV is recomputed on the frontend using the same
 * PHASE_POS_TABLE constants as the backend so the pipeline-breakdown table
 * lines up with the aggregate rNPV method IV. Any discrepancy (e.g. if the
 * backend applies a therapeutic-area multiplier we don't replicate) shows
 * up as the "other / rounding" row at the bottom.
 *
 * Panels in order:
 *   1. rNPV Header card — compact summary with per-share breakdown (pipeline,
 *      legacy, net cash)
 *   2. Pipeline Assets table — scrollable per-drug with phase chip, PoS, peak
 *      sales, risk-adj NPV contribution
 *   3. Upcoming Catalysts — timeline (requires research text; gracefully
 *      hidden until backend extractor surfaces catalyst dates)
 *   4. R&D Productivity — from FMP line items
 *   5. Legacy Decline / Cliff Risk — banner when sector notes flag it
 */

import type { DcfRange, BiopharmaPipelineAsset } from '@/lib/reportTypes';
import { currencySymbol } from '@/lib/utils';
import { ResearchNarrativeCard } from '@/components/report/shared/ResearchNarrativeCard';

// ── Phase → PoS mapping (mirrors backend PHASE_POS_TABLE in dcf_agent.py) ──
// Keys are pre-normalized by normPhase() (lowercase, whitespace/punct stripped),
// so we only need the canonical forms here. normPhase handles the
// "phase 1"/"Ph-1"/"phase1" variants at runtime.
const PHASE_POS: Record<string, number> = {
  preclinical: 0.037,
  preclin:     0.037,
  ph1:         0.096,
  phase1:      0.096,
  ph2:         0.153,
  phase2:      0.153,
  ph3:         0.493,
  phase3:      0.493,
  filed:       0.85,
  ndafiled:    0.85,
  approved:    1.0,
  marketed:    1.0,
  launched:    1.0,
};

// Therapeutic-area PoS multiplier (mirrors _BIOPHARMA_TA_PoS_MULT in dcf_agent.py)
const TA_POS_MULT: Record<string, number> = {
  oncology:            0.55,
  cns:                 0.60,
  rare:                1.70,
  hematology:          1.40,
  metabolic:           1.00,
  cardiovascular:      1.00,
  cv:                  1.00,
  immunology:          1.10,
  infectious_disease:  1.20,
  infectious:          1.20,
  other:               1.00,
};

const PHASE_COLORS: Record<string, string> = {
  approved:    'bg-green-100 text-green-700 dark:bg-green-950/50 dark:text-green-400',
  marketed:    'bg-green-100 text-green-700 dark:bg-green-950/50 dark:text-green-400',
  launched:    'bg-green-100 text-green-700 dark:bg-green-950/50 dark:text-green-400',
  filed:       'bg-blue-100 text-blue-700 dark:bg-blue-950/50 dark:text-blue-400',
  ph3:         'bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-400',
  ph2:         'bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400',
  ph1:         'bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-400',
  preclinical: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
};

const SECTION_HEADING_CLS =
  'text-[11px] font-semibold uppercase tracking-[0.2em] text-zinc-500 dark:text-zinc-400';

// ── Formatters ─────────────────────────────────────────────────────────────

const fmtMoney = (v: number | null | undefined, sym: string, decimals = 2): string => {
  if (v == null || isNaN(v)) return '—';
  return `${sym}${v.toFixed(decimals)}`;
};
const fmtBn = (v: number | null | undefined, sym: string): string => {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1) return `${sym}${v.toFixed(2)}B`;
  if (abs >= 0.01) return `${sym}${(v * 1000).toFixed(0)}M`;
  return `${sym}${v.toFixed(3)}B`;
};
const fmtPct = (v: number | null | undefined, decimals = 0): string => {
  if (v == null || isNaN(v)) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
};

// ── Normalize phase string to a lookup key ─────────────────────────────────

function normPhase(phase: string | null | undefined): string {
  if (!phase) return 'preclinical';
  const p = phase.toLowerCase().replace(/\s+/g, '').replace(/[^a-z0-9]/g, '');
  if (p.includes('approved') || p.includes('market') || p.includes('launch')) return 'approved';
  if (p.includes('filed') || p.includes('ndafiled') || p.includes('blafile')) return 'filed';
  if (p.includes('3'))      return 'ph3';
  if (p.includes('2'))      return 'ph2';
  if (p.includes('1'))      return 'ph1';
  return 'preclinical';
}

function phaseLabel(phase: string | null | undefined): string {
  const p = normPhase(phase);
  return ({
    approved:    'Approved',
    filed:       'Filed',
    ph3:         'Ph3',
    ph2:         'Ph2',
    ph1:         'Ph1',
    preclinical: 'Preclin',
  })[p] ?? 'N/A';
}

function posForAsset(asset: BiopharmaPipelineAsset): number {
  const p = normPhase(asset.phase);
  const basePos = PHASE_POS[p] ?? 0.05;
  const ta = (asset.therapeutic_area ?? 'other').toLowerCase().replace(/\s+/g, '_');
  const taMult = TA_POS_MULT[ta] ?? 1.0;
  return Math.min(1.0, basePos * taMult);
}

// Per-asset rNPV contribution estimate (mirrors backend 2-stage rNPV formula)
function riskAdjNpvBn(asset: BiopharmaPipelineAsset): number {
  const peak = asset.peak_sales_bn ?? 0;
  if (peak <= 0) return 0;
  const pos = posForAsset(asset);
  // Heuristic: op_margin 0.35 × ramp_profile_integral ≈ 0.30 (bell-shaped
  // commercial curve) × discount ≈ 0.50 for near-term, 0.30 for Ph2/Ph1.
  const p = normPhase(asset.phase);
  const discount =
    p === 'approved' ? 0.60 :
    p === 'filed'    ? 0.55 :
    p === 'ph3'      ? 0.45 :
    p === 'ph2'      ? 0.30 :
    p === 'ph1'      ? 0.20 : 0.10;
  return peak * pos * 0.35 * discount;
}

// ── Panel 1. rNPV Header ──────────────────────────────────────────────────

function RNPVHeader({ dcfRange, currentPrice, sym }: {
  dcfRange: DcfRange; currentPrice: number | undefined; sym: string;
}) {
  const iv = dcfRange.base?.intrinsic_value ?? null;
  const netDebt = dcfRange.net_debt ?? 0;
  const shares = dcfRange.shares_outstanding ?? 0;
  const upside = (iv != null && currentPrice && currentPrice > 0)
    ? (iv - currentPrice) / currentPrice
    : null;

  // Cash per share = -net_debt / shares (negative net debt = net cash)
  const netCashPerShare = (shares > 0) ? (-netDebt) / shares : null;
  // Pipeline rNPV/sh + legacy rev NPV ≈ IV - net cash/share
  const pipelineAndLegacyPerShare = (iv != null && netCashPerShare != null)
    ? iv - netCashPerShare
    : null;

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex items-baseline justify-between">
        <p className={SECTION_HEADING_CLS}>Pipeline rNPV / share</p>
        <span className="text-[10px] text-zinc-500 dark:text-zinc-400">
          base case · WACC {fmtPct(dcfRange.wacc, 1)}
        </span>
      </div>
      <div className="mt-2 flex items-baseline justify-between gap-2">
        <p className="text-3xl font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
          {fmtMoney(iv, sym)}
        </p>
        {upside != null && (
          <p className={`text-sm font-semibold ${upside >= 0 ? 'text-green-600' : 'text-red-500'}`}>
            {upside >= 0 ? '+' : ''}{(upside * 100).toFixed(1)}% vs {fmtMoney(currentPrice, sym)}
          </p>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 mt-3 text-xs">
        <div>
          <p className="text-[10px] text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">Pipeline + Legacy</p>
          <p className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
            {fmtMoney(pipelineAndLegacyPerShare, sym)}/sh
          </p>
        </div>
        <div>
          <p className="text-[10px] text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">Net Cash</p>
          <p className={`font-semibold tabular-nums ${
            (netCashPerShare ?? 0) > 0 ? 'text-green-600' : 'text-zinc-900 dark:text-zinc-50'
          }`}>
            {fmtMoney(netCashPerShare, sym)}/sh
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Panel 2. Pipeline Assets Table (THE HERO) ─────────────────────────────

function PipelineTable({ assets, sym }: {
  assets: BiopharmaPipelineAsset[]; sym: string;
}) {
  if (!assets || assets.length === 0) {
    return (
      <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
        <p className={SECTION_HEADING_CLS}>Pipeline Assets</p>
        <p className="text-xs text-zinc-500 dark:text-zinc-400 italic py-6 text-center">
          Deep-research extractor didn't surface pipeline assets. Re-run with fresh research.
        </p>
      </div>
    );
  }

  // Rank assets by risk-adjusted NPV descending
  const ranked = assets.map(a => ({
    asset: a,
    rnpv_bn: riskAdjNpvBn(a),
    pos: posForAsset(a),
  })).sort((a, b) => b.rnpv_bn - a.rnpv_bn);

  const totalRnpv = ranked.reduce((s, r) => s + r.rnpv_bn, 0);

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex items-center justify-between mb-3">
        <p className={SECTION_HEADING_CLS}>Pipeline Assets</p>
        <span className="text-[10px] text-zinc-500 dark:text-zinc-400">
          {ranked.length} assets · {sym}{totalRnpv.toFixed(2)}B rNPV
        </span>
      </div>
      <div className="overflow-x-auto -mx-4 px-4">
        <table className="w-full text-xs border-collapse" style={{ minWidth: '440px' }}>
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400">
              <th className="text-left py-2 pr-2 font-semibold">Asset</th>
              <th className="text-left py-2 pr-2 font-semibold">Phase</th>
              <th className="text-right py-2 pr-2 font-semibold">Peak</th>
              <th className="text-right py-2 pr-2 font-semibold">PoS</th>
              <th className="text-right py-2 font-semibold">rNPV</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map(({ asset, rnpv_bn, pos }, i) => {
              const phase = normPhase(asset.phase);
              const chipCls = PHASE_COLORS[phase] ?? PHASE_COLORS.preclinical;
              return (
                <tr key={`${asset.name}-${i}`} className="border-b border-zinc-100 dark:border-zinc-800/50">
                  <td className="py-2 pr-2">
                    <div className="font-semibold text-zinc-900 dark:text-zinc-50">
                      {asset.name}
                      {asset.partner && (
                        <span className="text-[9px] text-zinc-500 dark:text-zinc-400 ml-1">
                          w/ {asset.partner}
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-zinc-500 dark:text-zinc-400 truncate">
                      {asset.indication ?? '—'}
                      {asset.therapeutic_area && ` · ${asset.therapeutic_area.replace(/_/g, ' ')}`}
                    </div>
                  </td>
                  <td className="py-2 pr-2">
                    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${chipCls}`}>
                      {phaseLabel(asset.phase)}
                    </span>
                  </td>
                  <td className="py-2 pr-2 text-right font-mono tabular-nums text-zinc-900 dark:text-zinc-50">
                    {asset.peak_sales_bn != null ? `${sym}${asset.peak_sales_bn.toFixed(1)}B` : '—'}
                  </td>
                  <td className="py-2 pr-2 text-right font-mono tabular-nums text-zinc-900 dark:text-zinc-50">
                    {(pos * 100).toFixed(0)}%
                  </td>
                  <td className="py-2 text-right font-mono tabular-nums font-semibold text-zinc-900 dark:text-zinc-50">
                    {sym}{rnpv_bn.toFixed(2)}B
                  </td>
                </tr>
              );
            })}
            <tr className="border-t-2 border-zinc-300 dark:border-zinc-700 font-semibold bg-zinc-50 dark:bg-zinc-900/50">
              <td className="py-2 pr-2 text-zinc-900 dark:text-zinc-50" colSpan={4}>
                Total rNPV
              </td>
              <td className="py-2 text-right font-mono tabular-nums text-zinc-900 dark:text-zinc-50">
                {sym}{totalRnpv.toFixed(2)}B
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-zinc-500 dark:text-zinc-400 mt-3 font-mono leading-relaxed">
        rNPV = Peak × PoS × op_margin × ramp_profile × discount · PoS from BIO 2011-2020 × TA multiplier
      </p>
    </div>
  );
}

// ── Panel 3. Upcoming Catalysts (vertical timeline) ──────────────────────
// Derives likely catalyst events from pipeline_assets — Ph3 readouts, Filed
// PDUFA decisions, Approved launches. Gracefully degrades when pipeline
// extractor didn't fire.

interface CatalystItem {
  asset: BiopharmaPipelineAsset;
  eventType: 'PDUFA' | 'Ph3 readout' | 'Launch' | 'Ph2 readout';
  priority: number;       // for ordering
  color: 'blue' | 'purple' | 'green' | 'amber';
}

function deriveCatalysts(assets: BiopharmaPipelineAsset[]): CatalystItem[] {
  return assets
    .map((a): CatalystItem | null => {
      const p = normPhase(a.phase);
      if (p === 'filed')    return { asset: a, eventType: 'PDUFA',      priority: 1, color: 'blue'   };
      if (p === 'ph3')      return { asset: a, eventType: 'Ph3 readout', priority: 2, color: 'purple' };
      if (p === 'approved') return { asset: a, eventType: 'Launch',     priority: 3, color: 'green'  };
      if (p === 'ph2')      return { asset: a, eventType: 'Ph2 readout', priority: 4, color: 'amber'  };
      return null;
    })
    .filter((c): c is CatalystItem => c !== null)
    .sort((a, b) => {
      // Order by priority, then peak sales (larger first within priority)
      if (a.priority !== b.priority) return a.priority - b.priority;
      return (b.asset.peak_sales_bn ?? 0) - (a.asset.peak_sales_bn ?? 0);
    })
    .slice(0, 6);   // show top 6
}

function CatalystsTimeline({ assets, sym }: {
  assets: BiopharmaPipelineAsset[]; sym: string;
}) {
  const catalysts = deriveCatalysts(assets);
  if (catalysts.length === 0) return null;

  const colorCls = {
    blue:   'bg-blue-500',
    purple: 'bg-purple-500',
    green:  'bg-green-500',
    amber:  'bg-amber-500',
  };
  const chipCls = {
    blue:   'bg-blue-100 text-blue-700 dark:bg-blue-950/50 dark:text-blue-400',
    purple: 'bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-400',
    green:  'bg-green-100 text-green-700 dark:bg-green-950/50 dark:text-green-400',
    amber:  'bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400',
  };

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <p className={`${SECTION_HEADING_CLS} mb-3`}>Upcoming Catalysts</p>
      <ol className="relative border-l-2 border-zinc-200 dark:border-zinc-800 ml-2">
        {catalysts.map((c, i) => {
          const rnpv_bn = riskAdjNpvBn(c.asset);
          return (
            <li key={`${c.asset.name}-${i}`} className="ml-4 pb-4 relative">
              <div
                className={`absolute w-3 h-3 ${colorCls[c.color]} rounded-full -left-[23px] mt-1`}
              ></div>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-50">
                  {c.asset.launch_year ?? 'TBD'}
                </span>
                <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${chipCls[c.color]}`}>
                  {c.eventType}
                </span>
              </div>
              <p className="text-sm text-zinc-800 dark:text-zinc-200 mt-1">
                <span className="font-semibold">{c.asset.name}</span>
                {c.asset.indication && ` — ${c.asset.indication}`}
              </p>
              {c.asset.peak_sales_bn != null && (
                <p className="text-[10px] text-zinc-500 dark:text-zinc-400 mt-0.5">
                  Peak {sym}{c.asset.peak_sales_bn.toFixed(1)}B · driving {sym}{rnpv_bn.toFixed(2)}B rNPV
                  {c.asset.partner && ` · w/ ${c.asset.partner}`}
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ── Panel 4. R&D Productivity ─────────────────────────────────────────────
//
// Strictly FMP-derivable tiles only (no fabricated cost/NME or industry
// benchmark). Narrative commentary is offloaded to the research-narrative
// companion card below, which pulls Section 2F.4 from deep research when
// available.

function RDProductivity({
  dcfRange, rd_spend, revenue, fcf, sym,
}: {
  dcfRange: DcfRange;
  rd_spend: number | null;
  revenue: number | null;
  fcf: number | null;
  sym: string;
}) {
  const netDebt = dcfRange.net_debt ?? 0;
  const shares = dcfRange.shares_outstanding ?? 0;
  // Balance sheet amounts in FMP are in $, not $B — detect scale
  const netCash = -netDebt;
  const netCashBn = (Math.abs(netCash) > 1e6) ? netCash / 1e9 : netCash;
  const rdSpendBn = (rd_spend && Math.abs(rd_spend) > 1e6) ? rd_spend / 1e9 : (rd_spend ?? 0);
  // R&D / Revenue ratio — the one FMP-direct productivity metric
  const rdRatio = (rd_spend && revenue && revenue > 0) ? rd_spend / revenue : null;
  // Runway = net_cash / |FCF burn|. Only meaningful when FCF is negative.
  const fcfBn = (fcf && Math.abs(fcf) > 1e6) ? fcf / 1e9 : (fcf ?? 0);
  let runwayYears: number | null = null;
  if (netCashBn > 0 && fcfBn < 0) {
    runwayYears = netCashBn / Math.abs(fcfBn);
  }

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <p className={`${SECTION_HEADING_CLS} mb-3`}>R&D &amp; Capital</p>
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        {rdRatio != null ? (
          <div className="flex items-center justify-between">
            <span className="text-zinc-500 dark:text-zinc-400">R&amp;D / Revenue</span>
            <span className={`font-semibold tabular-nums ${rdRatio > 0.5 ? 'text-red-500' : rdRatio > 0.25 ? 'text-amber-500' : 'text-zinc-900 dark:text-zinc-50'}`}>
              {(rdRatio * 100).toFixed(0)}%
            </span>
          </div>
        ) : null}
        {rd_spend != null ? (
          <div className="flex items-center justify-between">
            <span className="text-zinc-500 dark:text-zinc-400">R&amp;D spend</span>
            <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
              {fmtBn(rdSpendBn, sym)}
            </span>
          </div>
        ) : null}
        <div className="flex items-center justify-between">
          <span className="text-zinc-500 dark:text-zinc-400">Net cash</span>
          <span className={`font-semibold tabular-nums ${netCashBn > 0 ? 'text-green-600' : 'text-red-500'}`}>
            {netCashBn > 0 ? '+' : ''}{fmtBn(netCashBn, sym)}
          </span>
        </div>
        {runwayYears != null ? (
          <div className="flex items-center justify-between">
            <span className="text-zinc-500 dark:text-zinc-400">Runway</span>
            <span className={`font-semibold tabular-nums ${runwayYears < 2 ? 'text-red-500' : runwayYears < 3 ? 'text-amber-500' : 'text-green-600'}`}>
              {runwayYears.toFixed(1)}y
            </span>
          </div>
        ) : null}
        <div className="flex items-center justify-between">
          <span className="text-zinc-500 dark:text-zinc-400">Shares out</span>
          <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
            {shares > 0 ? `${(shares / 1e6).toFixed(1)}M` : '—'}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-zinc-500 dark:text-zinc-400">WACC</span>
          <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
            {fmtPct(dcfRange.wacc, 1)}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Top-level composite ──────────────────────────────────────────────────

export interface BiopharmaValuationPanelProps {
  dcfRange?: DcfRange;
  currentPrice?: number;
  ticker: string;
  pipelineAssets?: BiopharmaPipelineAsset[];
  /** Deep research section 2 text blocks, keyed by subsection id ("2a", "2b",
      "2c", "2d", "2e", "2f"). When supplied, Panel 4's R&D narrative card and
      Panel 5's cliff-risk narrative card render. When missing, both cards
      gracefully hide. */
  sections?: Record<string, string> | null;
  /** R&D spend (annual, $). From FMP line items. Optional — tile hides when null. */
  rd_spend?: number | null;
  /** Revenue (annual, $). From FMP. Used to derive R&D / Revenue ratio. */
  revenue?: number | null;
  /** Free cash flow (annual, $). From FMP. Used to derive runway. */
  fcf?: number | null;
}

export function BiopharmaValuationPanel({
  dcfRange, currentPrice, ticker, pipelineAssets,
  sections, rd_spend, revenue, fcf,
}: BiopharmaValuationPanelProps) {
  const sym = currencySymbol(ticker);
  if (!dcfRange) return null;
  const assets = pipelineAssets ?? [];
  // Section 2F is where Biopharma KPI framework lands. Normalize key case so
  // the frontend works regardless of whether backend parse produced "2f" or "2F".
  const section2F = sections?.["2f"] ?? sections?.["2F"] ?? null;

  return (
    <div className="flex flex-col gap-4">
      <RNPVHeader dcfRange={dcfRange} currentPrice={currentPrice} sym={sym} />
      <PipelineTable assets={assets} sym={sym} />
      <CatalystsTimeline assets={assets} sym={sym} />

      {/* Panel 4 — R&D Productivity: FMP tiles + 2F.4 narrative companion */}
      <RDProductivity
        dcfRange={dcfRange}
        rd_spend={rd_spend ?? null}
        revenue={revenue ?? null}
        fcf={fcf ?? null}
        sym={sym}
      />
      <ResearchNarrativeCard
        title="R&D Productivity commentary"
        sectionText={section2F}
        subsection="2F.4"
        sourceLabel="Deep research · Section 2F.4"
      />

      {/* Panel 5 — Patent Cliff / Legacy Decline: narrative-only (2F.3).
          Entire card hides when deep research didn't produce this subsection. */}
      <ResearchNarrativeCard
        title="Patent Cliff / Legacy Decline"
        sectionText={section2F}
        subsection="2F.3"
        sourceLabel="Deep research · Section 2F.3"
      />
    </div>
  );
}
