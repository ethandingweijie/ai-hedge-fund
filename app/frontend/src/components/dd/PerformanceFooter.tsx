/**
 * PerformanceFooter.tsx — Phase 3 attribution surface.
 *
 * Compact footer at the bottom of /dd-alerts showing the agent's measured
 * performance: hit rate, mean 5-day forward return, alpha vs. a naive
 * "do nothing" baseline. Expandable for breakdown by action / direction
 * / reason.
 *
 * Data source: GET /api/dd-alerts/performance.
 *
 * Hit rate semantics:
 *   - "correct" / "incorrect" only counted for ADD / TRIM / EXIT / HOLD
 *   - WATCH / UNCLEAR are deliberately excluded (no commitment, no grade)
 *   - hit_rate = correct / (correct + incorrect)
 *
 * Alpha semantics:
 *   - Naive baseline = mean fwd_5d_return across ALL graded alerts
 *     (i.e. "what would you have gotten just buying and holding the
 *     stocks that breached")
 *   - Agent alpha = mean signed return weighted by action direction:
 *       ADD       → +ret  (long capture)
 *       TRIM/EXIT → −ret  (short / no-position avoidance)
 *       HOLD      →  0    (held position; alpha contribution = 0)
 *   - Positive alpha_vs_naive = agent recommendations beat just buying
 *     every breaching ticker
 */

import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, Target, TrendingUp, TrendingDown } from 'lucide-react';
import { getDdPerformance } from '@/lib/api';
import type { DdPerformance, DdPerformanceBucket } from '@/lib/reportTypes';


interface PerformanceFooterProps {
  /** Optional ISO date filter — defaults to last 30 days. */
  sinceDays?: number;
}

function fmtPct(v: number | null | undefined, opts: { plus?: boolean } = {}): string {
  if (v == null) return '—';
  const sign = (opts.plus && v >= 0) ? '+' : '';
  return `${sign}${(v * 100).toFixed(1)}%`;
}

function fmtRate(v: number | null | undefined): string {
  if (v == null) return 'n/a';
  return `${(v * 100).toFixed(0)}%`;
}

function BucketRow({ label, bucket }: { label: string; bucket: DdPerformanceBucket }) {
  const colorCls = (bucket.mean_5d_return ?? 0) >= 0
    ? 'text-emerald-600 dark:text-emerald-400'
    : 'text-red-600 dark:text-red-400';
  return (
    <div className="grid grid-cols-12 gap-2 text-xs items-baseline px-1 py-1 rounded hover:bg-muted/40">
      <div className="col-span-3 font-mono font-semibold">{label}</div>
      <div className="col-span-2 text-muted-foreground">n={bucket.n}</div>
      <div className="col-span-3">
        <span className="text-muted-foreground">hit </span>
        <span className="font-semibold">{fmtRate(bucket.hit_rate)}</span>
        <span className="text-muted-foreground/70 ml-1">
          ({bucket.n_correct}/{bucket.n_correct + bucket.n_incorrect})
        </span>
      </div>
      <div className={`col-span-2 tabular-nums ${colorCls}`}>5d {fmtPct(bucket.mean_5d_return, { plus: true })}</div>
      <div className="col-span-2 tabular-nums text-muted-foreground/80">22d {fmtPct(bucket.mean_22d_return, { plus: true })}</div>
    </div>
  );
}

export function PerformanceFooter({ sinceDays = 30 }: PerformanceFooterProps) {
  const [data, setData]   = useState<DdPerformance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const since = new Date(Date.now() - sinceDays * 24 * 60 * 60 * 1000)
      .toISOString().slice(0, 10);
    getDdPerformance({ since })
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, [sinceDays]);

  if (error) {
    // Silent: don't break the page if the endpoint is missing or 500s
    return null;
  }
  if (!data) return null;
  if (data.n_alerts_graded === 0) {
    return (
      <section className="mt-4 rounded-md border border-border bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
        <span className="font-semibold">Performance:</span> no graded alerts in the last {sinceDays} days yet.
        Forward returns are computed 7+ calendar days after each trigger fires.
      </section>
    );
  }

  const alphaPositive = data.alpha_vs_naive >= 0;
  const alphaColor = alphaPositive
    ? 'text-emerald-600 dark:text-emerald-400'
    : 'text-red-600 dark:text-red-400';

  return (
    <section className="mt-4 rounded-md border border-border bg-muted/20 px-3 py-2.5 space-y-2">
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <Target size={12} className="text-primary" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Performance</span>
          <span className="text-[11px] text-muted-foreground/80">last {sinceDays}d:</span>
          <span className="text-xs font-semibold">{data.n_alerts_graded} graded</span>
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs">
            agent <span className={`font-semibold tabular-nums ${alphaColor}`}>
              {fmtPct(data.agent_mean_5d_alpha, { plus: true })}
            </span>
          </span>
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs">
            naive {' '}
            <span className="font-semibold tabular-nums text-muted-foreground">
              {fmtPct(data.naive_mean_5d_return, { plus: true })}
            </span>
          </span>
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs">
            <span className="text-muted-foreground">α </span>
            <span className={`font-bold tabular-nums ${alphaColor}`}>
              {fmtPct(data.alpha_vs_naive, { plus: true })}
              {alphaPositive
                ? <TrendingUp className="inline ml-0.5" size={11} />
                : <TrendingDown className="inline ml-0.5" size={11} />}
            </span>
          </span>
        </div>
        {expanded
          ? <ChevronUp size={14} className="text-muted-foreground shrink-0" />
          : <ChevronDown size={14} className="text-muted-foreground shrink-0" />}
      </button>

      {expanded && (
        <div className="border-t border-border/60 pt-2 space-y-3">
          {Object.keys(data.by_action).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground/70 mb-1">By action</div>
              {Object.entries(data.by_action).map(([cat, bkt]) => (
                <BucketRow key={cat} label={cat} bucket={bkt} />
              ))}
            </div>
          )}
          {Object.keys(data.by_direction).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground/70 mb-1">By direction</div>
              {Object.entries(data.by_direction).map(([dir, bkt]) => (
                <BucketRow key={dir} label={dir} bucket={bkt} />
              ))}
            </div>
          )}
          {Object.keys(data.by_reason).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground/70 mb-1">By trigger reason</div>
              {Object.entries(data.by_reason).map(([r, bkt]) => (
                <BucketRow key={r} label={r} bucket={bkt} />
              ))}
            </div>
          )}
          <div className="text-[10px] text-muted-foreground/60 leading-relaxed pt-1 border-t border-border/40">
            <span className="font-semibold">Hit rate</span> = correct / (correct + incorrect); WATCH/UNCLEAR not graded.
            <span className="mx-1">·</span>
            <span className="font-semibold">Alpha</span> = signed forward return weighted by action (ADD = +ret, TRIM/EXIT = −ret, HOLD = 0, WATCH/UNCLEAR excluded).
          </div>
        </div>
      )}
    </section>
  );
}
