/**
 * ProgressHeader — the live-run progress card.
 *
 * Originally mobile-only (V2ReportView). Extracted so the desktop live view
 * (ReportPage.tsx) can show the exact same progress UI instead of the
 * thinner phase-quip + bar it used to show.
 *
 *   Row 1: Phase label (short human-readable, e.g. "Deep Research") · % · Cancel
 *   Row 2: Thinking / status detail — full width, wraps up to 3 lines
 *   Row 3: Progress bar
 */
const BRAND = '#297A4B';

export function ProgressHeader({
  progressPct,
  currentPhaseLabel,
  thinkingDetail,
  onCancel,
}: {
  progressPct: number;
  currentPhaseLabel?: string;
  thinkingDetail?: string;
  onCancel?: () => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-card shadow-sm overflow-hidden">
      {/* Row 1 — phase label + % + Cancel (single line, tight) */}
      <div className="px-4 pt-3 pb-1 flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold text-foreground truncate tracking-tight">
            {currentPhaseLabel ?? 'Running analysis…'}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <span className="text-[15px] font-semibold tabular-nums text-foreground tracking-tight">
            {Math.round(progressPct)}%
          </span>
          {onCancel && (
            <button
              onClick={onCancel}
              className="text-[11.5px] font-medium text-muted-foreground hover:text-content-high dark:hover:text-content-high transition-colors"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
      {/* Row 2 — live thinking/status detail flows into the full width,
          wraps up to 3 lines. */}
      <div className="px-4 pb-2.5 text-[11.5px] text-muted-foreground leading-snug line-clamp-3 break-words min-h-[1.35em]">
        {thinkingDetail ?? 'Running analysis — research streams in over 4–6 minutes.'}
      </div>
      <div className="h-1 bg-muted overflow-hidden">
        <div
          className="h-full transition-[width] duration-200 ease-out"
          style={{
            width: `${Math.max(0, Math.min(100, progressPct))}%`,
            background: `linear-gradient(90deg, ${BRAND} 0%, ${BRAND} 80%, #9FE870 100%)`,
            boxShadow: `0 0 8px ${BRAND}80`,
          }}
        />
      </div>
    </div>
  );
}
