/**
 * Research rating header — shared by the desktop ReportHeader and the mobile
 * V2ReportView hero card, so the two render paths cannot drift.
 *
 * Owner, 2026-10-03: the summary shows the rating pin (with the benchmark and the trade action)
 * only. The disclosed figures (price, 12-month target, upside, dividend yield, TSR), the TSR arithmetic,
 * the structural vs tactical views, the compliance notes, the rating definition and the
 * rating-to-action disclaimer are on hover and in the PDF.
 */
import type { ResearchView } from '@/lib/reportTypes';
import { actionTone } from '@/lib/semanticColors';
import { cn, currencySymbol } from '@/lib/utils';
import { useI18n } from '@/i18n';

const title = (s?: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase() : '—');

/**
 * The 12-month target as a pill that sits beside the rating / action pill
 * (owner, 2026-11-24: "include a pill for the target price, clearly").
 * Outlined so it reads as a figure, not a second rating.
 */
export function TargetPill({ value, ticker, className = '' }: { value: number; ticker: string; className?: string }) {
  const { t } = useI18n();
  return (
    <span
      // cn() (tailwind-merge) so a caller's size classes replace the defaults
      // instead of racing them in the stylesheet.
      className={cn('inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-sm font-semibold tabular-nums text-foreground', className)}
      title={t('12-month price target')}
    >
      <span className="font-medium text-muted-foreground">{t('Target')}</span>
      {currencySymbol(ticker)}{value.toFixed(2)}
    </span>
  );
}

export function RatingPill({ view, className = '' }: { view: ResearchView; className?: string }) {
  const { t } = useI18n();
  const key = view.under_review ? 'UNDER_REVIEW' : view.research_rating;
  return (
    <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${actionTone(key)} ${className}`}>
      {t(view.rating_label)}
    </span>
  );
}

export function ResearchRatingBlock({ view, ticker, compact = false }: {
  view: ResearchView;
  ticker: string;
  compact?: boolean;
}) {
  const sym = currencySymbol(ticker);
  const text = compact ? 'text-[12px]' : 'text-sm';
  const small = compact ? 'text-xs' : 'text-xs';
  const diverges = view.structural_rating && view.structural_rating !== view.tactical_rating;

  // Unrated / Pre-Revenue: no target, no upside, no TSR, no benchmark. The
  // block shows the state and WHY, and nothing that reads like a valuation.
  if (view.research_rating === 'UNRATED') {
    return (
      <div className={`flex flex-col gap-3 ${text}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <RatingPill view={view} />
          <span className={`${small} text-content-muted`}>· no recommendation</span>
        </div>
        {view.price != null && (
          <dl className={`grid grid-cols-2 gap-3 ${small}`}>
            <div>
              <dt className="uppercase tracking-wider text-content-muted">Price</dt>
              <dd className="font-semibold tabular-nums text-content-high">{sym}{view.price.toFixed(2)}</dd>
              {view.price_as_of && <dd className="text-content-muted">close {view.price_as_of.slice(0, 10)}</dd>}
            </div>
            <div>
              <dt className="uppercase tracking-wider text-content-muted">12M Target</dt>
              <dd className="font-semibold text-content-medium">Not published</dd>
            </div>
          </dl>
        )}
        <div className="rounded-lg border border-dashed border-[var(--hairline)] bg-surface-2 px-3 py-2 text-content-high leading-relaxed">
          {view.callout}
        </div>
        <p className={`${compact ? 'text-xs' : 'text-xs'} text-content-muted leading-relaxed`}>
          {view.rating_definition} {view.disclaimer}
        </p>
      </div>
    );
  }

  return (
    <div className={`flex flex-col gap-3 ${text}`}>
      <div className="flex items-center gap-2 flex-wrap" title={[view.callout, view.structural_rating ? `Structural: ${title(view.structural_rating)}; tactical (12M): ${title(view.tactical_rating)}${diverges ? ' (views differ, see disclosure)' : ''}.` : '',
        ...(view.compliance?.notes ?? []), view.rating_definition, view.benchmark ? `Benchmark expected return ${(view.benchmark.expected_return * 100).toFixed(1)}% (${view.benchmark.basis}).` : '', view.disclaimer].filter(Boolean).join(' | ')}>
        <RatingPill view={view} />
        {view.target_12m != null && <TargetPill value={view.target_12m} ticker={ticker} />}
        {view.benchmark && <span className="text-content-medium">vs {view.benchmark.name}</span>}
        <span className={`${small} text-content-muted`}>· trade action {view.trade_action}</span>
      </div>

      {/* Owner, 2026-11-27: what this report did with material news since the prior one. */}
      {view.material_update?.sentence && (
        <p className={`${small} leading-relaxed ${view.material_update.status === 'not_reflected' ? 'text-content-high font-medium' : 'text-content-medium'}`}>
          {view.material_update.sentence}
        </p>
      )}

      {/* Owner, 2026-10-03: the price / target / upside / dividend / TSR row is removed on web and mobile;
          those figures remain in the price-target panel, on the pin's hover title and in the PDF. */}
    </div>
  );
}
