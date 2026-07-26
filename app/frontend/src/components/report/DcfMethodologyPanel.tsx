/**
 * DcfMethodologyPanel — "how was this valuation calculated" explainer.
 *
 * Every field here is already computed and sent by the backend
 * (src/agents/analysis/dcf_agent.py, dcf_range[ticker]) — this panel adds
 * no new computation, it just surfaces profile/rationale/assumption fields
 * that previously reached the frontend untyped and unrendered.
 */
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { DcfRange } from '@/lib/reportTypes';

interface DcfMethodologyPanelProps {
  dcfRange?: DcfRange | null;
  ticker: string;
  /** Why dcfRange came back {} for this ticker (dcf_agent.py early-exit —
   *  insufficient history, no growth rate, etc.), if known. */
  skipReason?: string | null;
}

const DATA_SOURCE_INFO: Record<string, { label: string; detail: string; variant: 'success' | 'outline' | 'warning' }> = {
  guided: {
    label: 'Management guidance',
    detail: 'Growth assumption sourced from company-issued guidance — highest confidence.',
    variant: 'success',
  },
  analyst: {
    label: 'Analyst consensus',
    detail: 'Growth assumption sourced from Wall Street analyst estimates.',
    variant: 'outline',
  },
  historical: {
    label: 'Historical CAGR',
    detail: 'No management guidance or analyst consensus available — growth assumption extrapolated from historical revenue trend. Lower confidence than guided or analyst-sourced growth.',
    variant: 'warning',
  },
  sector_default: {
    label: 'Sector average (fallback)',
    detail: 'No guidance, analyst estimates, or usable historical revenue trend were available — growth assumption falls back to a sector-average rate. Lowest-confidence tier; treat this valuation as a rough estimate.',
    variant: 'warning',
  },
};

function pct(v?: number | null, decimals = 1): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
}

const SCENARIOS = [
  { key: 'bear', label: 'Bear' },
  { key: 'base', label: 'Base' },
  { key: 'bull', label: 'Bull' },
] as const;

const SKIP_REASON_LABELS: Record<string, string> = {
  line_items_fetch_failed: 'Financial data provider request failed',
  insufficient_history: 'Not enough annual financial history available',
  no_shares_data: 'Shares outstanding data unavailable',
  no_growth_rate: 'No management guidance, analyst estimates, or historical trend to derive a growth rate',
};

function friendlySkipReason(reason: string): string {
  const [code] = reason.split(':', 1);
  return SKIP_REASON_LABELS[code] ?? reason;
}

export function DcfMethodologyPanel({ dcfRange, ticker, skipReason }: DcfMethodologyPanelProps) {
  // dcf_agent.py's early-exit branches leave dcf_range[ticker] = {} rather
  // than omitting the key entirely — Object.keys check catches that exact
  // shape (a populated result always has at least `profile` + scenario keys).
  // Always render a card here rather than returning null — hiding the panel
  // entirely reads as "did this even run?" to a user browsing history;
  // an explicit "not available" is more honest than silence. skipReason is
  // only known for runs made after the diagnostic was added, hence the
  // generic fallback for older archived runs.
  if (!dcfRange || Object.keys(dcfRange).length === 0) {
    return (
      <Card className="p-4">
        <h3 className="text-sm font-semibold mb-1.5">Valuation Methodology — {ticker}</h3>
        <p className="text-xs text-muted-foreground">
          {skipReason
            ? `Not available for this run — ${friendlySkipReason(skipReason)}.`
            : 'Not available for this run — insufficient data at run time.'}
        </p>
      </Card>
    );
  }

  const src = dcfRange.data_source ? DATA_SOURCE_INFO[dcfRange.data_source] : undefined;
  const methodBadges: Array<{ name: string; weight?: number }> =
    dcfRange.base?.profile_weights
    ?? (dcfRange.base?.methods_used ?? []).map((name) => ({ name }));

  const hasScenarioData = SCENARIOS.some(({ key }) => dcfRange[key]);

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-1 gap-2">
        <h3 className="text-sm font-semibold">Valuation Methodology — {ticker}</h3>
        {dcfRange.calibration_error && (
          <Badge variant="warning" className="h-5 px-2 text-[10px] shrink-0">
            CALIBRATION WARNING
          </Badge>
        )}
      </div>

      <div className="mt-2 space-y-1">
        <p className="text-xs">
          <span className="text-muted-foreground">Profile:</span>{' '}
          <span className="font-medium">{dcfRange.profile ?? '—'}</span>
          {dcfRange.anchor_method && (
            <>
              <span className="text-muted-foreground mx-1.5">·</span>
              <span className="text-muted-foreground">Primary anchor:</span>{' '}
              <span className="font-medium">{dcfRange.anchor_method}</span>
            </>
          )}
        </p>
        {dcfRange.profile_rationale && (
          <p className="text-xs text-muted-foreground italic">{dcfRange.profile_rationale}</p>
        )}
        {dcfRange.calibration_note && (
          <p className="text-xs text-amber-600 dark:text-amber-400">{dcfRange.calibration_note}</p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        {dcfRange.wacc != null && (
          <span>
            <span className="text-muted-foreground">WACC:</span>{' '}
            <span className="font-medium">{pct(dcfRange.wacc)}</span>
          </span>
        )}
        {dcfRange.c_macro != null && dcfRange.c_macro !== 0 && (
          <span title="Macro-regime adjustment applied to the base discount rate">
            <span className="text-muted-foreground">Macro modifier:</span>{' '}
            <span className="font-medium">
              {dcfRange.c_macro > 0 ? '+' : ''}{(dcfRange.c_macro * 100).toFixed(2)}pp
            </span>
          </span>
        )}
        {src && (
          <span className="inline-flex items-center gap-1.5">
            <span className="text-muted-foreground">Growth source:</span>
            <Badge variant={src.variant} className="h-5 px-2 text-[10px]" title={src.detail}>
              {src.label}
            </Badge>
          </span>
        )}
      </div>

      {hasScenarioData && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left font-medium py-1 pr-3">Scenario</th>
                <th className="text-right font-medium py-1 px-3">Growth</th>
                <th className="text-right font-medium py-1 px-3">FCF Margin (Yr 1)</th>
                <th className="text-right font-medium py-1 px-3">Margin Δ/yr</th>
                <th className="text-right font-medium py-1 px-3">Terminal Growth</th>
                <th className="text-right font-medium py-1 pl-3">TV % of IV</th>
              </tr>
            </thead>
            <tbody>
              {SCENARIOS.map(({ key, label }) => {
                const c = dcfRange[key];
                if (!c) return null;
                return (
                  <tr key={key} className="border-b border-border/50 last:border-0">
                    <td className="py-1.5 pr-3 font-medium">{label}</td>
                    <td className="text-right py-1.5 px-3 tabular-nums">{pct(c.growth_rate)}</td>
                    <td className="text-right py-1.5 px-3 tabular-nums">{pct(c.fcf_margin_start)}</td>
                    <td className="text-right py-1.5 px-3 tabular-nums">
                      {c.margin_delta_per_year != null
                        ? `${c.margin_delta_per_year >= 0 ? '+' : ''}${(c.margin_delta_per_year * 100).toFixed(2)}pp`
                        : '—'}
                    </td>
                    <td className="text-right py-1.5 px-3 tabular-nums">{pct(c.tgr)}</td>
                    <td className="text-right py-1.5 pl-3 tabular-nums">{pct(c.tv_pct, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {methodBadges.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-widest text-muted-foreground mr-1">
            Methods (base case)
          </span>
          {methodBadges.map((m) => (
            <Badge key={m.name} variant="outline" className="h-5 px-2 text-[10px]">
              {m.name}{m.weight != null ? ` · ${(m.weight * 100).toFixed(0)}%` : ''}
            </Badge>
          ))}
        </div>
      )}

      <p className="mt-3 text-[10px] text-muted-foreground">
        Model estimate, not investment advice. Assumptions reflect data available at run time.
      </p>
    </Card>
  );
}
