/**
 * PriceTargetHistoryStrip — GS-style PT accountability strip (Tier 2.6).
 *
 * Renders data.price_target_history[ticker] (built at save time by
 * analysis_service._build_pt_history): past runs' model IV and 12m PT vs the
 * price-at-run, oldest → newest left → right. Answers "has the model's
 * target been ahead of, behind, or chasing the tape?" without leaving the
 * report. Needs ≥2 past runs to draw; otherwise renders nothing.
 */

import { Card } from '@/components/ui/card';
import { currencySymbol } from '@/lib/utils';
import type { PtHistoryPoint } from '@/lib/reportTypes';

interface Props {
  history: PtHistoryPoint[];
  ticker: string;
}

const W = 640;
const H = 120;
const PAD_X = 8;
const PAD_TOP = 14;
const PAD_BOT = 18;

function fmtDate(runAt?: string): string {
  if (!runAt) return '';
  const d = new Date(runAt);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function PriceTargetHistoryStrip({ history, ticker }: Props) {
  const pts = (history ?? []).filter(
    (p) => p.intrinsic_value != null || p.price_target != null,
  );
  if (pts.length < 2) return null;

  const sym = currencySymbol(ticker);
  const vals: number[] = [];
  for (const p of pts) {
    for (const v of [p.intrinsic_value, p.price_target, p.price_at_run]) {
      if (v != null && isFinite(v)) vals.push(v);
    }
  }
  if (!vals.length) return null;
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;

  const x = (i: number) => PAD_X + (i * (W - 2 * PAD_X)) / Math.max(1, pts.length - 1);
  const y = (v: number) => PAD_TOP + (1 - (v - lo) / span) * (H - PAD_TOP - PAD_BOT);

  const path = (key: 'intrinsic_value' | 'price_target'): string =>
    pts
      .map((p, i) => ({ v: p[key], i }))
      .filter((d) => d.v != null && isFinite(d.v))
      .map((d, j) => `${j === 0 ? 'M' : 'L'}${x(d.i).toFixed(1)},${y(d.v as number).toFixed(1)}`)
      .join(' ');

  const ivPath = path('intrinsic_value');
  const ptPath = path('price_target');
  const last = pts[pts.length - 1];

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
          Model target track record
        </div>
        <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 rounded bg-brand" /> Intrinsic value
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 rounded border-t border-dashed border-foreground/60" /> 12m PT
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-zinc-400" /> Price at run
          </span>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full" role="img"
        aria-label={`Past model targets vs price for ${ticker}`}>
        {ivPath && <path d={ivPath} fill="none" className="stroke-brand" strokeWidth={2} />}
        {ptPath && (
          <path d={ptPath} fill="none" className="stroke-foreground/60" strokeWidth={1.5}
            strokeDasharray="4 3" />
        )}
        {pts.map((p, i) =>
          p.price_at_run != null && isFinite(p.price_at_run) ? (
            <circle key={`p-${i}`} cx={x(i)} cy={y(p.price_at_run)} r={2.5}
              className="fill-zinc-400 dark:fill-zinc-500" />
          ) : null,
        )}
        {/* Date labels: first and last */}
        <text x={PAD_X} y={H - 4} className="fill-muted-foreground" fontSize={9}>
          {fmtDate(pts[0].run_at)}
        </text>
        <text x={W - PAD_X} y={H - 4} textAnchor="end" className="fill-muted-foreground" fontSize={9}>
          {fmtDate(last.run_at)}
        </text>
      </svg>

      <div className="mt-1 flex items-baseline justify-between text-[10.5px] text-muted-foreground tabular-nums">
        <span>{pts.length} prior runs</span>
        {last.intrinsic_value != null && last.price_at_run != null && last.price_at_run > 0 && (
          <span>
            Latest IV {sym}{last.intrinsic_value.toFixed(2)} vs price {sym}{last.price_at_run.toFixed(2)}{' '}
            ({(((last.intrinsic_value - last.price_at_run) / last.price_at_run) * 100).toFixed(1)}%)
          </span>
        )}
      </div>
    </Card>
  );
}
