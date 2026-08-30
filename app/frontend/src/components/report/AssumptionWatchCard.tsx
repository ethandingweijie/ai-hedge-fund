/**
 * AssumptionWatchCard.tsx — R3 Assumption Steward.
 *
 * Shows the steward's live view for the report's ticker: OPEN challenges on
 * tracked assumption fields (guidance / margin / one-off / theme), the
 * variant drivers where our view diverges most from the street, and the
 * source track-record ledger (hit rates). Data is fetched directly from
 * GET /research/ideas/analyst-docs/watch/{ticker} — the card is NOT part of
 * the run payload, so the pipeline serialization contract is untouched.
 *
 * Renders nothing when the steward is disabled, the fetch fails, or there
 * is nothing flagged — the card never occupies space on a quiet ticker.
 * Mounted in BOTH render paths (ReportViewPage desktop JSX + V2ReportView
 * decision tab), so it uses plain Tailwind markup only.
 */
import { useEffect, useState } from 'react';
import { getAssumptionWatch, type AssumptionWatchPayload } from '@/lib/api';

const ANOMALY_CHIP: Record<string, string> = {
  divergence:         'bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/25',
  direction_reversal: 'bg-surface-2 text-content-high border-[var(--hairline)]',
  theme_divergence:   'bg-surface-2 text-content-high border-[var(--hairline)]',
  earnings_quality:   'bg-surface-2 text-content-high border-[var(--hairline)]',
  margin_compression: 'bg-surface-2 text-content-high border-[var(--hairline)]',
};

const ANOMALY_LABEL: Record<string, string> = {
  divergence: 'divergence',
  direction_reversal: 'reversal',
  theme_divergence: 'theme',
  earnings_quality: 'one-off',
  margin_compression: 'margin',
};

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n).trimEnd()}…` : s;
}

/** Pull the steward's LLM reading out of the accumulated outcome_note. */
function readingNote(note?: string | null): string | null {
  if (!note) return null;
  const idx = note.lastIndexOf('[reading]');
  if (idx < 0) return null;
  return note.slice(idx + '[reading]'.length).trim() || null;
}

interface Props {
  ticker: string;
}

export function AssumptionWatchCard({ ticker }: Props) {
  const [watch, setWatch] = useState<AssumptionWatchPayload | null>(null);

  useEffect(() => {
    let alive = true;
    setWatch(null);
    getAssumptionWatch(ticker)
      .then((payload) => { if (alive) setWatch(payload); })
      .catch(() => { /* steward unavailable — card stays hidden */ });
    return () => { alive = false; };
  }, [ticker]);

  if (!watch || watch.enabled === false) return null;

  const challenges = watch.open_challenges ?? [];
  const drivers = watch.variant_drivers ?? [];
  const records = Object.entries(watch.track_record ?? {})
    .filter(([, r]) => (r?.n ?? 0) > 0);

  if (challenges.length === 0 && drivers.length === 0 && records.length === 0) {
    return null;
  }

  return (
    <div className="rounded-xl border border-border/70 bg-card shadow-sm p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
          Assumption Watch
        </div>
        <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground whitespace-nowrap">
          recursive steward
        </span>
      </div>

      {/* Open challenges */}
      {challenges.length > 0 && (
        <div className="space-y-2">
          {challenges.map((c) => {
            const reading = readingNote(c.outcome_note);
            return (
              <div key={c.id} className="rounded-lg border border-border/60 bg-muted/30 p-2.5">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-full border ${
                    ANOMALY_CHIP[c.anomaly_type] ?? 'bg-muted text-muted-foreground border-border/60'
                  }`}
                  >
                    {ANOMALY_LABEL[c.anomaly_type] ?? c.anomaly_type}
                  </span>
                  <span className="text-[10px] font-medium font-mono text-foreground/85">
                    {c.field_key}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                  {truncate(c.evidence, 200)}
                </p>
                {reading && (
                  <p className="text-[11px] text-foreground/80 mt-1 leading-relaxed border-l-2 border-brand/40 pl-2">
                    {truncate(reading, 260)}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Variant drivers — where our view diverges most from the street */}
      {drivers.length > 0 && (
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/60 mb-1">
            Variant drivers
          </div>
          <div className="space-y-1">
            {drivers.map((d, i) => (
              <div key={i} className="flex items-baseline gap-2 text-[11px]">
                <span className="font-mono text-foreground/85 shrink-0">{d.field_key}</span>
                {Number.isFinite(d.gap_pct) && d.gap_pct !== 0 && (
                  <span className="text-amber-600 dark:text-amber-400 font-semibold tabular-nums shrink-0">
                    ~{Math.abs(d.gap_pct).toFixed(1)}{d.field_key.includes('growth') ? 'pp' : '%'}
                  </span>
                )}
                <span className="text-muted-foreground truncate">
                  {[d.house_view, d.street_view].filter(Boolean).join(' vs ') || d.source}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Source track record — hit-rate ledger */}
      {records.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap pt-1 border-t border-border/50">
          {records.map(([source, r]) => (
            <span
              key={source}
              className="inline-flex items-center gap-1 text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-muted/70 text-muted-foreground"
              title={`${source}: hit rate ${
                r.hit_rate != null ? `${Math.round(r.hit_rate * 100)}%` : 'n/a'
              } across ${r.n} scored call${r.n === 1 ? '' : 's'}`}
            >
              {source}
              <span className="tabular-nums font-semibold">
                {r.hit_rate != null ? `${Math.round(r.hit_rate * 100)}%` : '—'}
              </span>
              {r.low_track_record && (
                <span className="text-content-high font-bold">LOW-TRACK</span>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
