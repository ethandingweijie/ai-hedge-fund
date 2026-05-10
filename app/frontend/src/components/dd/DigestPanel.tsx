/**
 * DigestPanel.tsx — EOD aggregate display.
 *
 * Top 10 drops + top 10 pumps + active sector clusters for "today" (UTC).
 * Compact list view (vs the AlertCard list above) — designed for retrospective
 * scan rather than active monitoring.
 */
import { TrendingDown, TrendingUp, Layers } from 'lucide-react';
import type { DdDigest } from '@/lib/reportTypes';

interface DigestPanelProps {
  digest: DdDigest | null;
  loading?: boolean;
}

export function DigestPanel({ digest, loading }: DigestPanelProps) {
  if (loading && !digest) {
    return <div className="text-sm text-muted-foreground italic px-2 py-3">Loading digest…</div>;
  }
  if (!digest) {
    return <div className="text-sm text-muted-foreground italic px-2 py-3">No digest available.</div>;
  }
  const empty = digest.drops.length === 0 && digest.pumps.length === 0 && digest.clusters.length === 0;
  if (empty) {
    return (
      <div className="text-sm text-muted-foreground italic px-2 py-3">
        No alerts today. Cooldown gates are working — or no qualifying movements detected yet.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Sector clusters — surfaced first when active (sector signal trumps individual) */}
      {digest.clusters.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-widest text-muted-foreground mb-2 flex items-center gap-1.5">
            <Layers size={12} />
            Sector clusters ({digest.clusters.length})
          </h3>
          <ul className="space-y-1">
            {digest.clusters.map(c => {
              const sign = c.median_pct >= 0 ? '+' : '';
              const colorCls = c.direction === 'DROP'
                ? 'text-red-600 dark:text-red-400'
                : 'text-emerald-600 dark:text-emerald-400';
              return (
                <li key={c.cluster_id}
                    className="rounded-md border border-border bg-muted/30 px-3 py-2 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      {c.direction}
                    </span>
                    <span className="font-mono text-xs text-foreground">{c.n} tickers</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-bold tabular-nums ${colorCls}`}>
                      median {sign}{(c.median_pct * 100).toFixed(1)}%
                    </span>
                    <span className="font-mono text-[9px] text-muted-foreground/70 truncate max-w-[100px]">
                      {c.cluster_id}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* Top drops + top pumps side-by-side on wide screens, stacked on narrow */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <DigestList
          title="Top drops"
          icon={<TrendingDown size={12} />}
          colorCls="text-red-600 dark:text-red-400"
          items={digest.drops}
        />
        <DigestList
          title="Top pumps"
          icon={<TrendingUp size={12} />}
          colorCls="text-emerald-600 dark:text-emerald-400"
          items={digest.pumps}
        />
      </div>
    </div>
  );
}

interface DigestListProps {
  title: string;
  icon: React.ReactNode;
  colorCls: string;
  items: DdDigest['drops'];
}

function DigestList({ title, icon, colorCls, items }: DigestListProps) {
  return (
    <section>
      <h3 className="text-[10px] uppercase tracking-widest text-muted-foreground mb-2 flex items-center gap-1.5">
        {icon}
        {title} ({items.length})
      </h3>
      {items.length === 0 ? (
        <div className="text-xs text-muted-foreground/60 italic px-2 py-1">none</div>
      ) : (
        <ul className="space-y-0.5">
          {items.map(a => {
            const sign = a.trigger_pct >= 0 ? '+' : '';
            return (
              <li key={`${a.ticker}-${a.last_triggered_at}`}
                  className="flex items-center justify-between text-xs px-2 py-1 rounded hover:bg-muted/40">
                <span className="font-mono font-bold text-foreground">{a.ticker}</span>
                <span className={`font-bold tabular-nums ${colorCls}`}>
                  {sign}{(a.trigger_pct * 100).toFixed(1)}%
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
