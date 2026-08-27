/**
 * DecisionInputsCard.tsx — M2 Track D3.
 *
 * The committee-free PM decides from a quantitative valuation band plus
 * qualitative research inputs; this card shows exactly what the decision
 * was made from (decision.decision_inputs payload). It replaces the
 * retired investor persona cards and is mounted in BOTH render paths
 * (desktop JSX + V2ReportView), so it uses only plain Tailwind markup —
 * no shadcn Card wrapper — and renders identically on both surfaces.
 */
import type { DecisionInputs } from '@/lib/reportTypes';
import { currencySymbol } from '@/lib/utils';
import { actionTone, gradeTone } from '@/lib/semanticColors';



function money(v: number | null | undefined, ticker: string): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${currencySymbol(ticker)}${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function pct(v: number | null | undefined, signed = false): string {
  if (v == null || Number.isNaN(v)) return '—';
  const s = signed && v > 0 ? '+' : '';
  return `${s}${v.toFixed(1)}%`;
}

interface Props {
  decisionInputs?: DecisionInputs;
  ticker: string;
  /** True while the pipeline is still running (shows a waiting state). */
  isRunning?: boolean;
}

export function DecisionInputsCard({ decisionInputs, ticker, isRunning = false }: Props) {
  if (!decisionInputs) {
    return (
      <div className="rounded-xl border border-border/70 bg-card shadow-sm p-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70 mb-2">
          Decision inputs
        </div>
        <p className="text-[12px] text-muted-foreground">
          {isRunning ? 'Computed when the portfolio manager decides.' : 'Not available for this run.'}
        </p>
      </div>
    );
  }

  const q = decisionInputs.quantitative ?? {};
  const ql = decisionInputs.qualitative ?? {};
  const gates = decisionInputs.gates ?? [];
  const conviction = decisionInputs.conviction;
  const grades = q.vgpm_grades ?? {};

  return (
    <div className="rounded-xl border border-border/70 bg-card shadow-sm p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
          Decision inputs
        </div>
        {conviction?.value != null && (
          <span
            className="text-[10px] font-semibold tabular-nums px-1.5 py-0.5 rounded-full bg-brand/10 text-brand border border-brand/20"
            title={(conviction.notes ?? []).join(' · ') || 'Qualitative conviction multiplier'}
          >
            conviction ×{conviction.value}
          </span>
        )}
      </div>

      {/* Quantitative block */}
      <div>
        <div className="text-[10px] font-semibold text-muted-foreground/70 mb-1.5">Quantitative</div>
        <div className="flex items-center gap-2 flex-wrap">
          {q.band_action && (
            <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${actionTone(q.band_action)}`}>
              {q.band_action}
            </span>
          )}
          <span className="text-[11px] text-muted-foreground">
            valuation band · IV upside <span className="text-foreground font-medium tabular-nums">{pct(q.upside_to_iv_pct, true)}</span>
          </span>
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 mt-2 text-[11px]">
          <div className="text-muted-foreground">Blended IV <span className="text-foreground font-medium tabular-nums">{money(q.blended_iv, ticker)}</span></div>
          <div className="text-muted-foreground">12m PT <span className="text-foreground font-medium tabular-nums">{money(q.price_target_12m, ticker)}</span></div>
          <div className="text-muted-foreground">Expected value <span className="text-foreground font-medium tabular-nums">{money(q.expected_value, ticker)}</span> <span className="tabular-nums">({pct(q.ev_upside_pct, true)})</span></div>
          <div className="text-muted-foreground">Power-law <span className="text-foreground font-medium tabular-nums">{q.power_law_score != null ? q.power_law_score.toFixed(1) : '—'}</span>/10</div>
        </div>
        {/* VGPM grades inline */}
        {Object.keys(grades).length > 0 && (
          <div className="flex items-center gap-1.5 mt-2 flex-wrap">
            {(['valuation', 'growth', 'profitability', 'momentum'] as const).map(dim => {
              const g = grades[dim];
              if (!g) return null;
              return (
                <span key={dim} className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${gradeTone(g)}`} title={dim}>
                  {dim[0].toUpperCase()} {g}
                </span>
              );
            })}
            {q.trap_verdict && (
              <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                q.trap_verdict.toUpperCase() === 'HIGH'
                  ? 'bg-surface-2 text-content-high'
                  : 'bg-muted text-muted-foreground'
              }`}
              >
                trap {q.trap_verdict}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Qualitative block */}
      <div>
        <div className="text-[10px] font-semibold text-muted-foreground/70 mb-1.5">Qualitative</div>
        <div className="flex items-center gap-2 flex-wrap text-[11px]">
          {ql.research_tier && (
            <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
              research: {ql.research_tier}
            </span>
          )}
          {ql.delta_material != null && (
            <span className={`px-1.5 py-0.5 rounded font-medium ${
              ql.delta_material
                ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                : 'bg-surface-2 text-content-high'
            }`}
            >
              {ql.delta_material ? 'fresh news: material' : 'fresh news: none material'}
            </span>
          )}
        </div>
        {(ql.regulatory_watch?.length ?? 0) > 0 && (
          <div className="mt-1.5 text-[11px] text-content-high">
            ⚠ regulatory watch: {(ql.regulatory_watch ?? []).join(', ')}
          </div>
        )}
        {(ql.prior_catalysts?.length ?? 0) > 0 && (
          <div className="mt-1.5">
            <div className="text-[10px] text-muted-foreground/70 mb-0.5">Watched catalysts (prior report)</div>
            <ul className="text-[11px] text-muted-foreground list-disc list-inside space-y-0.5">
              {(ql.prior_catalysts ?? []).slice(0, 4).map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </div>
        )}
      </div>

      {/* Gates fired */}
      {gates.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-muted-foreground/70 mb-1">Gates</div>
          <ul className="space-y-0.5">
            {gates.map((g, i) => (
              <li key={i} className="text-[11px] text-muted-foreground flex gap-1.5">
                <span className="text-foreground/50 shrink-0">·</span>
                <span>{g}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
