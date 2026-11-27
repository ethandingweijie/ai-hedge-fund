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
import { ratingWords, wordsFromAction } from '@/lib/ratingVocab';
import { glossaryFor, plainKey } from '@/lib/glossary';
import { InfoTip } from '@/components/report/shared/InfoTip';
import { useI18n } from '@/i18n';

const GRADE_LABEL: Record<string, string> = {
  valuation: 'Valuation', growth: 'Growth', profitability: 'Profitability', momentum: 'Momentum',
};

/** "TRAP RISK MEDIUM" -> "Medium". */
function trapLevel(v: string): string {
  const m = v.match(/(LOW|MEDIUM|HIGH)/i);
  return m ? m[1].charAt(0).toUpperCase() + m[1].slice(1).toLowerCase() : v;
}

function money(v: number | null | undefined, ticker: string): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${currencySymbol(ticker)}${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pct(v: number | null | undefined, signed = false): string {
  if (v == null || Number.isNaN(v)) return '—';
  const s = v > 0 ? (signed ? '+' : '') : v < 0 ? '−' : '';
  return `${s}${Math.abs(v).toFixed(1)}%`;
}

interface Props {
  decisionInputs?: DecisionInputs;
  ticker: string;
  /** True while the pipeline is still running (shows a waiting state). */
  isRunning?: boolean;
}

export function DecisionInputsCard({ decisionInputs, ticker, isRunning = false }: Props) {
  const { t } = useI18n();
  if (!decisionInputs) {
    return (
      <div className="rounded-xl border border-border/70 bg-card shadow-sm p-4">
        <div className="text-[15px] font-semibold text-foreground mb-2">
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
  // The chip is the DECISION. Runs before the rating layer carry no
  // trade_action and keep the band as before.
  const decisionWords = q.trade_action
    ? ratingWords({ rating_label: q.research_rating, action: q.trade_action })
    : wordsFromAction(q.band_action);
  const bandWords = wordsFromAction(q.band_action);

  return (
    <div className="rounded-xl border border-border/70 bg-card shadow-sm p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="text-[15px] font-semibold text-foreground">
          Decision inputs
        </div>
        {conviction?.value != null && (
          <span
            className="text-xs font-semibold tabular-nums px-1.5 py-0.5 rounded-full bg-brand/10 text-brand border border-brand/20"
            title={(conviction.notes ?? []).join(' · ') || t(glossaryFor('Conviction')![0])}
          >
            {t('conviction')} ×{conviction.value}
          </span>
        )}
      </div>

      {/* Quantitative block */}
      <div>
        <div className="text-xs font-semibold text-muted-foreground/70 mb-1.5">Quantitative</div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* The chip is the DECISION. Runs before the rating layer carry no
              trade_action and keep the band as before. */}
          {/* Owner, 2026-11-27: Buy / Hold / Sell, research word secondary. */}
          {decisionWords && (
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${actionTone(decisionWords.toneKey)}`}>
              {t(decisionWords.primary)}
            </span>
          )}
          {decisionWords?.secondary && (
            <span className="text-xs font-medium text-foreground">{t(decisionWords.secondary)} · {t('12-month rating')}</span>
          )}
        </div>
        {/* The valuation band is the long-run value check; it can differ from the rating. */}
        {q.band_action && (
          <div className="mt-1.5 text-xs text-muted-foreground leading-relaxed">
            <span className="inline-flex items-center gap-1">
              {t('Long-run value view')}: <span className="font-semibold text-foreground">{t(bandWords?.primary ?? q.band_action)}</span>
              <InfoTip term="Valuation band" />
            </span>
            {' · '}{t("our intrinsic value vs today's price")} <span className="text-foreground font-medium tabular-nums">{pct(q.upside_to_iv_pct, true)}</span>
            {q.trade_action && bandWords && decisionWords && bandWords.primary !== decisionWords.primary && (
              <p className="mt-1">
                {t('The rating ({r}) is the 12-month call: it adds the expected price path, dividends and rating rules to this long-run value check ({b}), so the two can differ.', { r: t(decisionWords.primary), b: t(bandWords.primary) })}
              </p>
            )}
          </div>
        )}
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 mt-2 text-xs">
          <div className="text-muted-foreground inline-flex items-center gap-1 flex-wrap">{t('Intrinsic value')} <InfoTip term="Intrinsic value" /> <span className="text-foreground font-medium tabular-nums">{money(q.blended_iv, ticker)}</span></div>
          <div className="text-muted-foreground inline-flex items-center gap-1 flex-wrap">{t('12-month target')} <InfoTip term="Target" /> <span className="text-foreground font-medium tabular-nums">{money(q.price_target_12m, ticker)}</span></div>
          <div className="text-muted-foreground inline-flex items-center gap-1 flex-wrap">{t('Expected value')} <InfoTip term="Expected value" /> <span className="text-foreground font-medium tabular-nums">{money(q.expected_value, ticker)}</span> <span className="tabular-nums">({pct(q.ev_upside_pct, true)})</span></div>
          <div className="text-muted-foreground inline-flex items-center gap-1 flex-wrap">{t('Power-law')} <InfoTip term="Power-law" /> <span><span className="text-foreground font-medium tabular-nums">{q.power_law_score != null ? q.power_law_score.toFixed(1) : '—'}</span>/10</span></div>
        </div>
        {/* VGPM grades inline */}
        {Object.keys(grades).length > 0 && (
          <div className="flex items-center gap-1.5 mt-2 flex-wrap">
            {(['valuation', 'growth', 'profitability', 'momentum'] as const).map(dim => {
              const g = grades[dim];
              if (!g) return null;
              return (
                <span key={dim} className={`text-xs font-semibold px-1.5 py-0.5 rounded ${gradeTone(g)}`}
                  title={`${t(GRADE_LABEL[dim])} ${g}: ${t(glossaryFor(GRADE_LABEL[dim])?.[0] ?? '')}`}>
                  {t(GRADE_LABEL[dim])} {g}
                </span>
              );
            })}
            {q.trap_verdict && (
              <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${
                q.trap_verdict.toUpperCase() === 'HIGH'
                  ? 'bg-surface-2 text-content-high'
                  : 'bg-muted text-muted-foreground'
              }`}
              >
                {t('Value-trap risk')}: {t(trapLevel(q.trap_verdict))}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Qualitative block */}
      <div>
        <div className="text-xs font-semibold text-muted-foreground/70 mb-1.5">Qualitative</div>
        <div className="flex items-center gap-2 flex-wrap text-xs">
          {ql.research_tier && (
            <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
              {t('Research')}: {t(plainKey(ql.research_tier))}
            </span>
          )}
          {ql.delta_material != null && (
            <span className={`px-1.5 py-0.5 rounded font-medium ${
              ql.delta_material
                ? 'bg-amber-500/15 text-warning'
                : 'bg-surface-2 text-content-high'
            }`}
            >
              {ql.delta_material ? t('Recent news: important') : t('Recent news: nothing important')}
            </span>
          )}
        </div>
        {(ql.regulatory_watch?.length ?? 0) > 0 && (
          <div className="mt-1.5 text-xs text-content-high">
            ⚠ regulatory watch: {(ql.regulatory_watch ?? []).join(', ')}
          </div>
        )}
        {(ql.prior_catalysts?.length ?? 0) > 0 && (
          <div className="mt-1.5">
            <div className="text-xs text-muted-foreground/70 mb-0.5">Watched catalysts (prior report)</div>
            <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
              {(ql.prior_catalysts ?? []).slice(0, 4).map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </div>
        )}
      </div>

      {/* Gates fired */}
      {gates.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-muted-foreground/70 mb-1">Gates</div>
          <ul className="space-y-0.5">
            {gates.map((g, i) => (
              <li key={i} className="text-xs text-muted-foreground flex gap-1.5">
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
