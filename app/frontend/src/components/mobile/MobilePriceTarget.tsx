import { currencySymbol } from '@/lib/utils';
import type { DcfRange, ScenarioAnalysis, PortfolioDecision } from '@/lib/reportTypes';

interface Props {
  dcfRange?: DcfRange;
  scenario?: ScenarioAnalysis;
  decision?: PortfolioDecision;
  ticker: string;
}

function fmt(v: number | null | undefined, sym: string): string {
  if (v == null || isNaN(v)) return '—';
  return `${sym}${v.toFixed(2)}`;
}
function fmtPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}
function upside(target: number | undefined, current: number | undefined): number | null {
  if (!target || !current || current === 0) return null;
  return ((target - current) / current) * 100;
}
function upColor(pct: number | null): string {
  if (pct == null) return 'text-muted-foreground';
  return pct >= 0 ? 'text-green-500' : 'text-red-500';
}

export function MobilePriceTarget({ dcfRange, scenario, decision, ticker }: Props) {
  const sym = currencySymbol(ticker);
  const current = scenario?.current_price ?? 0;
  const pmTarget = decision?.price_target;
  const ev = scenario?.expected_value;
  const blended12m = scenario?.['12m_price_target'];
  const wacc = dcfRange?.wacc;

  const pmUp = upside(pmTarget ?? undefined, current || undefined);
  const blendedUp = upside(blended12m ?? undefined, current || undefined);
  const evUp = upside(ev ?? undefined, current || undefined);

  // Scenarios
  const targets12m = scenario?.['12m_targets_by_scenario'] ?? dcfRange?.['12m_targets'];
  const bearIV = dcfRange?.bear?.intrinsic_value;
  const baseIV = dcfRange?.base?.intrinsic_value;
  const bullIV = dcfRange?.bull?.intrinsic_value;
  const bearDown = upside(bearIV ?? undefined, current || undefined);

  // Detect if PM target differs from blended (the confusing case)
  const pmDiffersFromBlend = pmTarget != null && blended12m != null &&
    pmTarget > 0 && blended12m > 0 &&
    Math.abs(pmTarget - blended12m) > 0.5;

  if (!pmTarget && !ev && !blended12m) {
    return <p className="text-muted-foreground text-sm">Price target unavailable for {ticker}.</p>;
  }

  return (
    <div className="space-y-4">

      {/* ── Hero: THE number that matters ── */}
      <div className="text-center py-2">
        {pmTarget != null && pmTarget > 0 ? (
          <>
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider">12-Month Price Target</p>
            <p className="text-3xl font-bold tabular-nums mt-1">{fmt(pmTarget, sym)}</p>
            {pmUp != null && (
              <p className={`text-lg font-bold ${upColor(pmUp)}`}>{fmtPct(pmUp)} upside</p>
            )}
            {current > 0 && (
              <p className="text-xs text-muted-foreground mt-1">vs current {fmt(current, sym)}</p>
            )}
          </>
        ) : blended12m != null && blended12m > 0 ? (
          <>
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Blended 12-Month Target</p>
            <p className="text-3xl font-bold tabular-nums mt-1">{fmt(blended12m, sym)}</p>
            {blendedUp != null && (
              <p className={`text-lg font-bold ${upColor(blendedUp)}`}>{fmtPct(blendedUp)}</p>
            )}
          </>
        ) : null}
      </div>

      {/* ── How it was derived (one-liner) ── */}
      {pmDiffersFromBlend && (
        <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200/60 dark:border-amber-800/40 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-300 leading-snug">
          The Portfolio Manager set {fmt(pmTarget, sym)} as the target.
          The scenario-weighted blend is {fmt(blended12m, sym)} ({fmtPct(blendedUp)}).
          The PM may weight scenarios differently based on its thesis.
        </div>
      )}

      {/* ── At a glance: 4 key numbers ── */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-muted/40 rounded-lg px-3 py-2.5">
          <p className="text-[9px] uppercase tracking-wider text-muted-foreground">Current Price</p>
          <p className="text-base font-bold tabular-nums">{fmt(current || undefined, sym)}</p>
        </div>
        <div className="bg-muted/40 rounded-lg px-3 py-2.5">
          <p className="text-[9px] uppercase tracking-wider text-muted-foreground">Long-term Value</p>
          <p className="text-base font-bold tabular-nums">{fmt(ev, sym)}</p>
          {evUp != null && <p className={`text-[10px] font-semibold ${upColor(evUp)}`}>{fmtPct(evUp)}</p>}
        </div>
        <div className="bg-green-50 dark:bg-green-950/20 rounded-lg px-3 py-2.5">
          <p className="text-[9px] uppercase tracking-wider text-muted-foreground">Bull Case</p>
          <p className="text-base font-bold tabular-nums text-green-600">{fmt(bullIV, sym)}</p>
        </div>
        <div className="bg-red-50 dark:bg-red-950/20 rounded-lg px-3 py-2.5">
          <p className="text-[9px] uppercase tracking-wider text-muted-foreground">Bear Case</p>
          <p className="text-base font-bold tabular-nums text-red-500">{fmt(bearIV, sym)}</p>
          {bearDown != null && <p className={`text-[10px] font-semibold text-red-500`}>{fmtPct(bearDown)}</p>}
        </div>
      </div>

      {/* ── Scenario probabilities ── */}
      {(scenario?.bear || scenario?.base || scenario?.bull) && (
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">Scenario Probabilities</p>
          {[
            { label: 'Bear',  prob: scenario?.bear?.probability, target: targets12m?.bear, iv: bearIV, color: 'bg-red-500' },
            { label: 'Base',  prob: scenario?.base?.probability, target: targets12m?.base, iv: baseIV, color: 'bg-blue-500' },
            { label: 'Bull',  prob: scenario?.bull?.probability, target: targets12m?.bull, iv: bullIV, color: 'bg-green-500' },
          ].map(r => {
            const pct = r.prob != null ? Math.round(r.prob * 100) : null;
            return (
              <div key={r.label} className="flex items-center gap-2 py-1.5 border-b border-border/30 last:border-0">
                {/* Probability bar */}
                <div className="w-[42px] text-right">
                  <span className="text-xs font-bold tabular-nums">{pct != null ? `${pct}%` : '—'}</span>
                </div>
                <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                  <div className={`h-full ${r.color} rounded-full`} style={{ width: `${pct ?? 0}%` }} />
                </div>
                <span className="text-xs font-semibold w-[36px]">{r.label}</span>
                {/* 12m target + DCF IV */}
                <div className="text-right w-[60px]">
                  <p className="text-[10px] font-mono tabular-nums">{r.target != null && r.target > 0 ? fmt(r.target, sym) : '—'}</p>
                </div>
                <div className="text-right w-[60px]">
                  <p className="text-[10px] font-mono tabular-nums text-muted-foreground">{r.iv != null ? fmt(r.iv, sym) : '—'}</p>
                </div>
              </div>
            );
          })}
          {/* Column labels */}
          <div className="flex items-center gap-2 mt-1">
            <div className="w-[42px]" />
            <div className="flex-1" />
            <span className="w-[36px]" />
            <span className="text-[8px] text-muted-foreground text-right w-[60px]">12m Target</span>
            <span className="text-[8px] text-muted-foreground text-right w-[60px]">DCF IV</span>
          </div>
        </div>
      )}

      {/* ── WACC footnote ── */}
      {wacc != null && (
        <p className="text-[10px] text-muted-foreground">
          DCF discount rate (WACC): {(wacc * 100).toFixed(1)}%
        </p>
      )}
    </div>
  );
}
