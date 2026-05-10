/**
 * DigestPanel.tsx — EOD aggregate display.
 *
 * Top 10 drops + top 10 pumps + active sector clusters for "today" (UTC).
 * Compact list view (vs the AlertCard list above) — designed for retrospective
 * scan rather than active monitoring.
 *
 * Phase 2E: when an LLM-narrated digest exists for today, surface it at the
 * top of the panel as the "headline" — a 3-5 sentence senior-analyst note
 * answering "what was the dominant story today, was it macro or micro,
 * what to watch tomorrow." The raw aggregates remain below as the
 * supporting data.
 */
import { TrendingDown, TrendingUp, Layers, Sparkles } from 'lucide-react';
import type { DdDigest, DdDigestNarrative } from '@/lib/reportTypes';

interface DigestPanelProps {
  digest: DdDigest | null;
  loading?: boolean;
}

function NarrativeCard({ narrative }: { narrative: DdDigestNarrative }) {
  const isFallback = (narrative._model_name || '').includes('FALLBACK');
  const macroLabel =
    narrative.macro_or_micro === 'macro'  ? 'Macro-driven' :
    narrative.macro_or_micro === 'micro'  ? 'Ticker-driven' :
                                            'Mixed signals';
  return (
    <section
      className={`rounded-lg border ${isFallback ? 'border-amber-500/40' : 'border-primary/30'}
        ${isFallback ? 'bg-amber-50 dark:bg-amber-950/20' : 'bg-primary/5'}
        px-4 py-3 space-y-2`}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] uppercase tracking-widest text-muted-foreground flex items-center gap-1.5">
          <Sparkles size={12} className="text-primary" />
          EOD Narrative
        </h3>
        <span className="text-[10px] font-medium text-muted-foreground">{macroLabel}</span>
      </div>
      <p className="text-sm leading-relaxed text-foreground">{narrative.narrative}</p>
      {narrative.key_themes && narrative.key_themes.length > 0 && (
        <ul className="space-y-0.5 pt-1">
          {narrative.key_themes.map((t, i) => (
            <li key={i} className="text-xs text-foreground/80 flex gap-1.5">
              <span className="text-muted-foreground/60">·</span>
              <span>{t}</span>
            </li>
          ))}
        </ul>
      )}
      {narrative.tomorrow_watch && narrative.tomorrow_watch !== 'n/a' && (
        <div className="pt-1.5 border-t border-border/40">
          <span className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground">
            Tomorrow's watch:&nbsp;
          </span>
          <span className="text-xs text-foreground">{narrative.tomorrow_watch}</span>
        </div>
      )}
      {isFallback && (
        <div className="text-[10px] italic text-amber-700 dark:text-amber-400">
          (LLM agent fell back to synthetic — see logs)
        </div>
      )}
    </section>
  );
}

export function DigestPanel({ digest, loading }: DigestPanelProps) {
  if (loading && !digest) {
    return <div className="text-sm text-muted-foreground italic px-2 py-3">Loading digest…</div>;
  }
  if (!digest) {
    return <div className="text-sm text-muted-foreground italic px-2 py-3">No digest available.</div>;
  }
  const empty = digest.drops.length === 0 && digest.pumps.length === 0 && digest.clusters.length === 0;
  if (empty && !digest.narrative) {
    return (
      <div className="text-sm text-muted-foreground italic px-2 py-3">
        No alerts today. Cooldown gates are working — or no qualifying movements detected yet.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Phase 2E: LLM narrative leads the panel when present */}
      {digest.narrative && <NarrativeCard narrative={digest.narrative} />}

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
