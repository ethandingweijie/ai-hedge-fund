/**
 * PriceTargetPanel — explains exactly how the 12-month price target was derived.
 *
 * Two distinct valuations are shown side-by-side:
 *
 *  ① 12-Month Price Target  (forward sector multiples on Year-1 projections)
 *     bear 12m × P(bear) + base 12m × P(base) + bull 12m × P(bull) = blended PT
 *     → This IS the decision.price_target (e.g. $19)
 *
 *  ② Long-term Intrinsic Value  (10-year DCF, probability-weighted)
 *     bear IV × 25% + base IV × 50% + bull IV × 25% = Expected Value
 *     → This is scenario.expected_value (e.g. $101.57) — NOT the price target
 *
 * Without this distinction the frontend is meaningless: a $19 target against
 * a $101 EV looks absurd unless you understand they answer different questions.
 */

import { Card } from '@/components/ui/card';
import { currencySymbol } from '@/lib/utils';
import type {
  DcfRange,
  ScenarioAnalysis,
  PortfolioDecision,
} from '@/lib/reportTypes';

interface PriceTargetPanelProps {
  dcfRange?: DcfRange;
  scenario?: ScenarioAnalysis;
  decision?: PortfolioDecision;
  ticker: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeFmt(sym: string) {
  return (v: number | null | undefined): string => {
    if (v == null || isNaN(v)) return '—';
    return `${sym}${v.toFixed(2)}`;
  };
}

// Legacy default — overridden inside the main component using ticker
function fmt(v: number | null | undefined, prefix = '$'): string {
  if (v == null || isNaN(v)) return '—';
  return `${prefix}${v.toFixed(2)}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}

function upsidePct(target: number | undefined, current: number | undefined): number | null {
  if (!target || !current || current === 0) return null;
  return ((target - current) / current) * 100;
}

function upsideColor(pct: number | null): string {
  if (pct == null) return 'text-muted-foreground';
  return pct >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400';
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface BlendTableProps {
  title: string;
  subtitle: string;
  badge: string;
  badgeColor: string;
  rows: { label: string; value?: number | null; weight?: number | null; contribution?: number | null; color: string }[];
  blendedLabel: string;
  blendedValue?: number | null;
  currentPrice?: number;
  note?: string;
}

function BlendTable({
  title, subtitle, badge, badgeColor, rows, blendedLabel, blendedValue, currentPrice, note,
}: BlendTableProps) {
  const up = upsidePct(blendedValue ?? undefined, currentPrice);
  return (
    <div className="flex flex-col gap-2">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${badgeColor}`}>
              {badge}
            </span>
            <span className="text-xs font-semibold">{title}</span>
          </div>
          <p className="text-[10px] text-muted-foreground mt-0.5">{subtitle}</p>
        </div>
        {blendedValue != null && (
          <div className="text-right shrink-0">
            <div className="text-lg font-bold tabular-nums">{fmt(blendedValue)}</div>
            {up != null && (
              <div className={`text-xs font-semibold ${upsideColor(up)}`}>{fmtPct(up)}</div>
            )}
          </div>
        )}
      </div>

      {/* Table */}
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-border/60">
            <th className="text-left py-1.5 pr-2 text-xs font-semibold text-muted-foreground w-20">Case</th>
            <th className="text-right py-1.5 pr-2 text-xs font-semibold text-muted-foreground">Value</th>
            <th className="text-right py-1.5 pr-2 text-xs font-semibold text-muted-foreground w-16">Weight</th>
            <th className="text-right py-1.5 text-xs font-semibold text-muted-foreground w-20">Contribution</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className="border-b border-border/30 hover:bg-muted/20 transition-colors">
              <td className={`py-1.5 pr-2 font-semibold ${row.color}`}>{row.label}</td>
              <td className="py-1.5 pr-2 text-right font-mono">{fmt(row.value)}</td>
              <td className="py-1.5 pr-2 text-right text-muted-foreground">
                {row.weight != null ? `${(row.weight * 100).toFixed(0)}%` : '—'}
              </td>
              <td className="py-1.5 text-right font-mono font-semibold">
                {row.contribution != null ? fmt(row.contribution) : '—'}
              </td>
            </tr>
          ))}
          {/* Blended total row */}
          <tr className="bg-muted/30 font-semibold">
            <td className="py-2 pr-2 text-xs" colSpan={3}>{blendedLabel}</td>
            <td className="py-2 text-right font-mono text-sm">{fmt(blendedValue)}</td>
          </tr>
        </tbody>
      </table>

      {note && (
        <p className="text-[10px] text-muted-foreground italic mt-0.5">{note}</p>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PriceTargetPanel({
  dcfRange,
  scenario,
  decision,
  ticker,
}: PriceTargetPanelProps) {
  // Override the module-level fmt with a ticker-aware version
  const fmt = makeFmt(currencySymbol(ticker));
  const current = scenario?.current_price ?? 0;
  const priceTarget = decision?.price_target;

  // ── ① 12m forward-multiple targets ──────────────────────────────────────
  // The pipeline computes 12m_price_target = Σ(per-scenario target × probability).
  // We display the per-scenario values + their weights for transparency, and show
  // the pipeline's own blended figure — NOT a frontend recomputation — to avoid
  // any rounding discrepancy between the table and the headline number.
  const _raw12mPipeline = scenario?.['12m_targets_by_scenario'] ?? dcfRange?.['12m_targets'];

  // If all pipeline 12m targets are zero/null (e.g. GSE conservatorship where EV-based
  // multiples collapse to 0), fall back to the scenario DCF fair values so the table
  // always shows meaningful numbers. The note is updated to reflect this substitution.
  const _all12mZero = !_raw12mPipeline ||
    ((_raw12mPipeline.bear == null || _raw12mPipeline.bear === 0) &&
     (_raw12mPipeline.base == null || _raw12mPipeline.base === 0) &&
     (_raw12mPipeline.bull == null || _raw12mPipeline.bull === 0));

  const raw12m = _all12mZero
    ? (scenario?.bear?.fair_value || scenario?.base?.fair_value || scenario?.bull?.fair_value
        ? { bear: scenario?.bear?.fair_value ?? null,
            base: scenario?.base?.fair_value ?? null,
            bull: scenario?.bull?.fair_value ?? null }
        : _raw12mPipeline)
    : _raw12mPipeline;

  const _usingFairValueFallback = _all12mZero && raw12m !== _raw12mPipeline;

  // Always use the pipeline-computed blended value as the headline; fall back to decision.
  // Exclude 0: a $0 price target means the pipeline couldn't compute one (e.g. GSE in
  // conservatorship), not that the target is literally zero — show nothing instead.
  const _pt12mRaw = scenario?.['12m_price_target'] ?? priceTarget;
  const pt12mBlended: number | null | undefined =
    (_pt12mRaw != null && _pt12mRaw > 0) ? _pt12mRaw : undefined;
  const pt12mMethod = _usingFairValueFallback
    ? 'DCF scenario fair values (forward multiple unavailable)'
    : (scenario?.['12m_pt_method'] ?? dcfRange?.anchor_method ?? '');

  const bearProb = scenario?.bear?.probability;
  const baseProb = scenario?.base?.probability;
  const bullProb = scenario?.bull?.probability;

  const rows12m = [
    {
      label: 'Bear',
      value: (raw12m?.bear != null && raw12m.bear > 0) ? raw12m.bear : null,
      weight: bearProb ?? null,
      contribution: (raw12m?.bear != null && raw12m.bear > 0 && bearProb != null) ? raw12m.bear * bearProb : null,
      color: 'text-red-500 dark:text-red-400',
    },
    {
      label: 'Base',
      value: (raw12m?.base != null && raw12m.base > 0) ? raw12m.base : null,
      weight: baseProb ?? null,
      contribution: (raw12m?.base != null && raw12m.base > 0 && baseProb != null) ? raw12m.base * baseProb : null,
      color: 'text-blue-600 dark:text-blue-400',
    },
    {
      label: 'Bull',
      value: (raw12m?.bull != null && raw12m.bull > 0) ? raw12m.bull : null,
      weight: bullProb ?? null,
      contribution: (raw12m?.bull != null && raw12m.bull > 0 && bullProb != null) ? raw12m.bull * bullProb : null,
      color: 'text-green-600 dark:text-green-400',
    },
  ];

  // Check if the frontend-computed sum matches the pipeline value (flag if not)
  const computed12mSum = rows12m.reduce((s, r) => s + (r.contribution ?? 0), 0);
  const has12mMismatch =
    pt12mBlended != null &&
    computed12mSum > 0 &&
    Math.abs(computed12mSum - pt12mBlended) > 0.05;

  // ── ② Long-term DCF intrinsic value ─────────────────────────────────────
  const bearIV = dcfRange?.bear?.intrinsic_value;
  const baseIV = dcfRange?.base?.intrinsic_value;
  const bullIV = dcfRange?.bull?.intrinsic_value;

  // FX metadata — stored in base case (same rate applied to all three scenarios)
  const reportedCurrency = dcfRange?.base?.reported_currency;
  const fxRate           = dcfRange?.base?.fx_rate;
  const fxNote           = dcfRange?.base?.fx_note;
  const isNonUSD         = reportedCurrency != null && reportedCurrency !== 'USD';

  // Only include cases that actually have data; use scenario probabilities where
  // available (scenario agent may override the 25/50/25 default weighting)
  const dcfAvailCases = [
    { label: 'Bear', value: bearIV ?? null, weight: bearProb ?? 0.25, color: 'text-red-500 dark:text-red-400' },
    { label: 'Base', value: baseIV ?? null, weight: baseProb ?? 0.50, color: 'text-blue-600 dark:text-blue-400' },
    { label: 'Bull', value: bullIV ?? null, weight: bullProb ?? 0.25, color: 'text-green-600 dark:text-green-400' },
  ];

  const rowsDCF = dcfAvailCases.map(c => ({
    ...c,
    contribution: c.value != null ? c.value * c.weight : null,
  }));

  // Pipeline's own EV value takes priority over frontend sum (same reason as above)
  const expectedValue = scenario?.expected_value;

  // Which DCF cases are missing data (for the transparency note)
  const missingDCFCases = rowsDCF.filter(r => r.value == null).map(r => r.label);

  // ── ③ Valuation chasm warning ────────────────────────────────────────────
  const chasmRatio = (expectedValue && priceTarget && priceTarget > 0)
    ? expectedValue / priceTarget
    : null;

  // ── Reconciliation data ─────────────────────────────────────────────────
  const recon = scenario?.reconciliation;
  const skewRatio = recon?.skew_ratio;

  const has12m = raw12m != null && (raw12m.bear != null || raw12m.base != null || raw12m.bull != null);
  const hasDCF = bearIV != null || baseIV != null || bullIV != null;

  if (!has12m && !hasDCF && !priceTarget) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Price target data unavailable for {ticker}.</p>
      </Card>
    );
  }

  return (
    <Card className="p-5 space-y-5">

      {/* ── Page title + final target hero ────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">12-Month Price Target — {ticker}</h3>
          {current > 0 && (
            <p className="text-xs text-muted-foreground mt-0.5">
              Current price: <span className="font-semibold text-foreground">{fmt(current)}</span>
            </p>
          )}
        </div>
        {priceTarget != null && priceTarget > 0 && (
          <div className="text-right">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              PM 12m Target
            </div>
            <div className="text-2xl font-bold tabular-nums">{fmt(priceTarget)}</div>
            {current > 0 && (() => {
              const up = upsidePct(priceTarget, current);
              return up != null ? (
                <div className={`text-sm font-bold ${upsideColor(up)}`}>{fmtPct(up)}</div>
              ) : null;
            })()}
          </div>
        )}
      </div>

      {/* ── Explanation banner ─────────────────────────────────────────── */}
      <div className="rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-300 leading-snug">
        <strong>Why two different numbers?</strong>{' '}
        The <strong>12m price target</strong> uses near-term sector multiples (e.g. EV/EBITDA) applied
        to Year-1 projections — it reflects what the <em>market</em> is likely to price in over 12 months.
        The <strong>long-term intrinsic value (EV)</strong> is a 10-year discounted cash flow — it reflects
        fundamental worth. Both are valid; they answer different questions.
        {pt12mMethod && (
          <> Method used: <span className="font-semibold">{pt12mMethod}</span>.</>
        )}
      </div>

      {/* ── FX conversion notice (non-USD reporting companies) ─────────── */}
      {isNonUSD && (
        <div className="rounded-md bg-sky-50 dark:bg-sky-950/30 border border-sky-200 dark:border-sky-800 px-3 py-2 text-[11px] text-sky-800 dark:text-sky-300 leading-snug">
          <strong>💱 FX conversion applied:</strong>{' '}
          {ticker} reports financials in{' '}
          <span className="font-semibold">{reportedCurrency}</span>.{' '}
          {fxNote ?? (
            fxRate
              ? `All monetary inputs converted to USD at ${fxRate.toFixed(4)} ${reportedCurrency}/USD before DCF. Intrinsic values above are in USD.`
              : `Values converted to USD before DCF computation. Intrinsic values above are in USD.`
          )}
        </div>
      )}

      {/* ── Two-column blend tables ────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">

        {/* Left: 12m forward multiples */}
        {has12m && (
          <BlendTable
            title="12-Month Forward Multiple"
            subtitle="Sector multiples on Year-1 projected financials"
            badge="12m PT"
            badgeColor="bg-indigo-100 text-indigo-800 dark:bg-indigo-900/50 dark:text-indigo-300"
            rows={rows12m}
            blendedLabel="Probability-weighted 12m Target"
            blendedValue={pt12mBlended}
            currentPrice={current}
            note={pt12mMethod ? `Method: ${pt12mMethod}` : undefined}
          />
        )}

        {/* Right: long-term DCF intrinsic value */}
        {hasDCF && (
          <BlendTable
            title="Long-term Intrinsic Value (DCF)"
            subtitle="10-year discounted cash flow — NOT the price target"
            badge="EV"
            badgeColor="bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-300"
            rows={rowsDCF}
            blendedLabel="Probability-weighted Expected Value"
            blendedValue={expectedValue}
            currentPrice={current}
            note={[
              dcfRange?.wacc != null ? `WACC: ${(dcfRange.wacc * 100).toFixed(1)}%` : null,
              isNonUSD && fxRate ? `FX: ${reportedCurrency}→USD @ ${fxRate.toFixed(4)} (IVs in USD)` : null,
            ].filter(Boolean).join(' · ') || undefined}
          />
        )}
      </div>

      {/* ── Math transparency notes ────────────────────────────────────── */}
      {(has12mMismatch || missingDCFCases.length > 0) && (
        <div className="space-y-1.5">
          {has12mMismatch && (
            <div className="flex items-start gap-1.5 rounded bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800 px-3 py-2 text-[10px] text-yellow-800 dark:text-yellow-300">
              <span className="mt-px shrink-0">⚠</span>
              <span>
                <strong>12m blend note:</strong> Pipeline blended value{' '}
                <span className="font-mono">{fmt(pt12mBlended)}</span> differs from the
                displayed row sum <span className="font-mono">{fmt(computed12mSum)}</span>{' '}
                by{' '}
                <span className="font-mono">
                  {fmt(Math.abs((computed12mSum ?? 0) - (pt12mBlended ?? 0)))}
                </span>
                . The headline uses the pipeline&apos;s full-precision figure; the table
                rounds weights to the nearest integer %.
              </span>
            </div>
          )}
          {missingDCFCases.length > 0 && (
            <div className="flex items-start gap-1.5 rounded bg-slate-50 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 px-3 py-2 text-[10px] text-slate-600 dark:text-slate-400">
              <span className="mt-px shrink-0">ℹ</span>
              <span>
                <strong>DCF note:</strong> Intrinsic values for the{' '}
                <span className="font-semibold">{missingDCFCases.join(' & ')}</span>{' '}
                case{missingDCFCases.length > 1 ? 's are' : ' is'} not stored for this
                run (archived runs may omit bear/bull DCF detail). The long-term EV
                shown uses available cases only.
              </span>
            </div>
          )}
        </div>
      )}

      {/* ── Valuation chasm warning ─────────────────────────────────────── */}
      {chasmRatio != null && chasmRatio >= 5 && (
        <div className="rounded-md bg-violet-50 dark:bg-violet-950/30 border border-violet-200 dark:border-violet-800 px-3 py-2.5 text-[11px] text-violet-800 dark:text-violet-300 leading-snug space-y-1">
          <p className="font-bold">
            📐 Valuation Chasm — {chasmRatio.toFixed(1)}× gap between 12m target and long-term EV
          </p>
          <p>
            A {chasmRatio.toFixed(1)}× difference between the{' '}
            <span className="font-semibold">12m price target ({fmt(priceTarget)})</span> and the{' '}
            <span className="font-semibold">long-term intrinsic value ({fmt(expectedValue)})</span>{' '}
            is not a contradiction — it reflects two fundamentally different time horizons and methods.
          </p>
          <p>
            The 12m target asks: <em>&ldquo;What will the market pay in one year?&rdquo;</em> —
            it is anchored to current sector multiples and near-term earnings. The long-term EV
            asks: <em>&ldquo;What are the future cash flows worth today?&rdquo;</em> — it
            compounds growth over a decade. When a company is early-stage, unprofitable, or
            deeply undervalued by the market, this chasm is expected and meaningful, not
            an error.
          </p>
        </div>
      )}

      {/* ── Risk/reward summary bar ────────────────────────────────────── */}
      {(recon || (current > 0 && (priceTarget != null || expectedValue != null))) && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-3 border-t border-border/50">
          <div>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Current</p>
            <p className="text-sm font-bold tabular-nums">{fmt(current || undefined)}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">12m Target</p>
            <p className="text-sm font-bold tabular-nums">{priceTarget && priceTarget > 0 ? fmt(priceTarget) : '—'}</p>
            {current > 0 && priceTarget != null && priceTarget > 0 && (
              <p className={`text-xs font-semibold ${upsideColor(upsidePct(priceTarget, current))}`}>
                {fmtPct(upsidePct(priceTarget, current))}
              </p>
            )}
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Long-term EV</p>
            <p className="text-sm font-bold tabular-nums">{fmt(expectedValue)}</p>
            {current > 0 && expectedValue != null && (
              <p className={`text-xs font-semibold ${upsideColor(upsidePct(expectedValue, current))}`}>
                {fmtPct(upsidePct(expectedValue, current))}
              </p>
            )}
          </div>
          {skewRatio != null && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Risk/Reward</p>
              <p className="text-sm font-bold tabular-nums">{skewRatio.toFixed(2)}×</p>
              <p className="text-[10px] text-muted-foreground">upside / downside</p>
            </div>
          )}
          {skewRatio == null && recon?.bear_iv != null && current > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Downside (Bear)</p>
              <p className="text-sm font-bold tabular-nums">{fmt(recon.bear_iv)}</p>
              {recon.downside_to_bear_pct != null && (
                <p className="text-xs font-semibold text-red-500 dark:text-red-400">
                  {fmtPct(recon.downside_to_bear_pct)}
                </p>
              )}
            </div>
          )}
        </div>
      )}

    </Card>
  );
}
