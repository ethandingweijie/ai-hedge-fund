/**
 * PriorReportCard — M1 recency loop "Since last report" card.
 *
 * Renders data.prior_recap[ticker] + data.freshness_delta[ticker]: what the
 * PREVIOUS report concluded (action, price target, thesis) and what has
 * materially changed since it. Absent on first-ever runs for a ticker —
 * the card simply renders nothing then.
 */

import { Card } from '@/components/ui/card';
import { currencySymbol } from '@/lib/utils';
import type { FreshnessDelta, PriorRecap } from '@/lib/reportTypes';
import { actionTone } from '@/lib/semanticColors';

interface Props {
  prior: PriorRecap | undefined;
  delta: FreshnessDelta | undefined;
  ticker: string;
}

function fmtNum(v: number | null | undefined, sym: string): string {
  if (v == null || !isFinite(v)) return '—';
  return `${sym}${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}


export function PriorReportCard({ prior, delta, ticker }: Props) {
  if (!prior) return null;

  const sym = currencySymbol(ticker);
  const rj = prior.recap_json ?? {};
  const ageDays = prior.age_days != null ? `${prior.age_days.toFixed(1)}d old` : '';
  const runDate = prior.run_at ? prior.run_at.slice(0, 10) : '';
  const actionColor = actionTone(prior.final_action, 'text');

  const events = delta?.events ?? [];
  const material = delta?.material;

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
          Since last report
        </div>
        <div className="text-[10px] text-muted-foreground">
          {runDate}{runDate && ageDays ? ' · ' : ''}{ageDays}
        </div>
      </div>

      {/* Prior decision summary */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm mb-2">
        <span className={`font-bold ${actionColor}`}>
          {prior.final_action ?? 'N/A'}
        </span>
        {rj.price_target != null && (
          <span className="text-muted-foreground">
            PT {fmtNum(rj.price_target, sym)}
          </span>
        )}
        {prior.price_at_run != null && (
          <span className="text-muted-foreground">
            @ {fmtNum(prior.price_at_run, sym)}
          </span>
        )}
        {rj.dcf_base_iv != null && (
          <span className="text-muted-foreground">
            DCF base {fmtNum(rj.dcf_base_iv, sym)}
          </span>
        )}
        {rj.time_horizon && (
          <span className="text-muted-foreground">· {rj.time_horizon}</span>
        )}
      </div>

      {/* Prior thesis recap */}
      {prior.recap_text && (
        <p className="text-[13px] leading-relaxed text-foreground/80 mb-3">
          {prior.recap_text}
        </p>
      )}

      {/* Freshness delta verdict */}
      <div className="border-t pt-3">
        {material === true && (
          <>
            <div className="text-[11px] font-semibold text-amber-500 mb-1.5">
              Material developments since then
            </div>
            {events.length > 0 && (
              <ul className="space-y-1 mb-1.5">
                {events.map((e, i) => (
                  <li key={i} className="text-[12px] text-foreground/80 flex gap-2">
                    <span className="text-amber-500 shrink-0">•</span>
                    <span>
                      {e.headline || '(untitled event)'}
                      {e.date ? <span className="text-muted-foreground"> ({e.date})</span> : null}
                      {e.relevance ? (
                        <span className="text-muted-foreground"> — {e.relevance}</span>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {material === false && (
          <div className="text-[11px] font-semibold text-content-high mb-1.5">
            No material change — prior report still current
          </div>
        )}
        {material == null && delta && (
          <div className="text-[11px] font-semibold text-muted-foreground mb-1.5">
            Freshness check unavailable
          </div>
        )}
        {delta?.verdict && (
          <p className="text-[12px] text-muted-foreground">{delta.verdict}</p>
        )}
      </div>
    </Card>
  );
}
