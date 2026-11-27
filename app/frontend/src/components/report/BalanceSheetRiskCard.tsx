/**
 * BalanceSheetRiskCard.tsx — owner, 2026-11-27 (contrarian QA items 4 and 10).
 *
 * Shows the risk metrics a run already computes and used to hide: leverage (banks: CET1 and
 * loan-to-deposit), interest cover, the credit rating the cost of debt priced on, 1-year
 * volatility, liquidity, short interest, SBC / operating cash flow and the 3-year net share
 * count change. Payload: data.risk_metrics[ticker] (src/agents/analysis/risk_metrics.py).
 *
 * RisksToRatingCard renders decision.risks_to_rating (previously PDF-only); when a run has none,
 * it falls back to the balance-sheet line from the risk metrics.
 *
 * Mounted in BOTH render paths (desktop ReportPage / ReportViewPage and mobile V2ReportView), so
 * plain Tailwind markup only. Monochrome: green and red are reserved for price change.
 */
import type { RiskMetric, RiskMetricsBlock } from '@/lib/reportTypes';

const SHELL = 'rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)]';

function valueTone(status?: string | null): string {
  if (status === 'RED') return 'font-semibold text-content-high';
  if (status === 'AMBER') return 'font-semibold text-warning';
  return 'font-semibold text-foreground';
}

function MetricRow({ m }: { m: RiskMetric }) {
  const missing = !m.display;
  return (
    <div className="py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[12.5px] font-medium text-foreground">{m.label}</span>
        {missing ? (
          <span className="text-[12px] text-muted-foreground text-right">n/a — {m.reason ?? 'not in this run'}</span>
        ) : (
          <span className={`text-[12.5px] tabular-nums text-right ${valueTone(m.status)}`}>
            {m.display}
            {(m.status === 'RED' || m.status === 'AMBER') && (
              <span className="ml-1.5 text-[10.5px] font-medium tracking-wide text-muted-foreground">{m.status}</span>
            )}
          </span>
        )}
      </div>
      {m.explain && <p className="text-[11.5px] text-muted-foreground mt-0.5 leading-snug">{m.explain}</p>}
    </div>
  );
}

export function BalanceSheetRiskCard({ block, className = '' }: { block?: RiskMetricsBlock; className?: string }) {
  const metrics = block?.metrics ?? [];
  if (!metrics.length) return null;
  const chk = block?.balance_sheet_check;
  return (
    <div className={`${SHELL} ${className || 'p-4'}`}>
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[15px] font-semibold text-foreground">Balance sheet &amp; risk</h3>
        {block?.latest_period && (
          <span className="text-[11px] text-muted-foreground">Statements to {block.latest_period}</span>
        )}
      </div>
      <div className="divide-y divide-border/50">
        {metrics.map((m) => <MetricRow key={m.key} m={m} />)}
      </div>
      {chk?.evidence && (
        <p className="mt-3 pt-3 border-t border-border/60 text-[11.5px] text-muted-foreground leading-relaxed">
          <span className="font-semibold text-foreground">
            Value-trap balance-sheet check: {chk.status === 'NOT ASSESSED' ? 'not assessed' : chk.status}.
          </span>{' '}
          {chk.evidence}
        </p>
      )}
    </div>
  );
}

export function RisksToRatingCard({ risks, fallbackLine, className = '' }: {
  risks?: string[] | null;
  fallbackLine?: string | null;
  className?: string;
}) {
  const items = (risks ?? []).filter(Boolean);
  if (!items.length && fallbackLine) items.push(fallbackLine);
  if (!items.length) return null;
  return (
    <div className={`${SHELL} ${className || 'p-4'}`}>
      <h3 className="text-[15px] font-semibold text-foreground mb-2">Risks to rating</h3>
      <ul className="space-y-2">
        {items.map((r, i) => (
          <li key={i} className="flex gap-2 text-[12.5px] leading-relaxed text-foreground">
            <span className="shrink-0 text-muted-foreground">•</span>
            <span>{r}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
