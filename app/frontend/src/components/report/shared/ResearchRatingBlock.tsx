/**
 * Research rating header — shared by the desktop ReportHeader and the mobile
 * V2ReportView hero card, so the two render paths cannot drift.
 *
 * Owner, 2026-10-03: the summary shows the rating pin (with the benchmark and the trade action)
 * only. The disclosed figures (price, 12-month target, upside, dividend yield, TSR), the TSR arithmetic,
 * the structural vs tactical views, the compliance notes, the rating definition and the
 * rating-to-action disclaimer are on hover and in the PDF.
 */
import type { FreshnessDelta, ResearchView } from '@/lib/reportTypes';
import { ratingOneLiner, ratingWords, wordsFromResearch } from '@/lib/ratingVocab';
import { MaterialNewsLine } from '@/components/report/shared/MaterialNewsLine';
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

/**
 * Owner, 2026-11-27: one rating vocabulary. The pill says Buy / Hold / Sell;
 * the research word (Overweight / Neutral / Underweight) follows as a
 * secondary label. Under Review and Unrated keep their own words.
 */
export function RatingPill({ view, className = '', showSecondary = true }: { view: ResearchView; className?: string; showSecondary?: boolean }) {
  const { t } = useI18n();
  const w = ratingWords({ research_rating: view.research_rating, rating_label: view.rating_label, under_review: view.under_review, action: view.trade_action });
  const key = view.under_review ? 'UNDER_REVIEW' : (w?.toneKey ?? view.research_rating);
  const label = w?.primary === 'Unrated' ? view.rating_label : (w?.primary ?? view.rating_label);
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${actionTone(key)} ${className}`}>
        {t(label)}
      </span>
      {showSecondary && w?.secondary && (
        <span className="text-[13px] text-muted-foreground">{t(w.secondary)}</span>
      )}
    </span>
  );
}

/** "Hold = expected to roughly match the Straits Times Index over 12 months." */
export function RatingOneLiner({ view, className = '' }: { view: ResearchView; className?: string }) {
  const { t } = useI18n();
  const w = ratingWords({ research_rating: view.research_rating, rating_label: view.rating_label, under_review: view.under_review, action: view.trade_action });
  if (!w) return null;
  return <p className={`text-[13px] leading-relaxed text-muted-foreground ${className}`}>{ratingOneLiner(w.primary, view.benchmark?.name, t)}</p>;
}

function fmtDay(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return isNaN(d.getTime()) ? null : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

/** "Research dated 12 Oct 2026 · news checked 14 Nov – 28 Nov 2026" */
export function ProvenanceLine({ p, compact = false }: { p?: { research_as_of?: string | null; news_from?: string | null; news_to?: string | null } | null; compact?: boolean }) {
  const { t } = useI18n();
  const r = fmtDay(p?.research_as_of);
  const f = fmtDay(p?.news_from);
  const to = fmtDay(p?.news_to);
  if (!r && !(f && to)) return null;
  const parts = [
    r ? t('Research dated {d}', { d: r }) : null,
    f && to ? t('news checked {a} – {b}', { a: f, b: to }) : null,
  ].filter(Boolean);
  return <p className={`${compact ? 'text-xs' : 'text-[12.5px]'} text-muted-foreground -mt-1`}>{parts.join(' · ')}</p>;
}

/** The reasons behind the rating, visible on request (they used to live in a 900-character hover title). */
function WhyThisRating({ view, compact }: { view: ResearchView; compact: boolean }) {
  const { t } = useI18n();
  const notes = (view.compliance?.notes ?? []).filter((n) => !/incorporates material news|not yet reflected/i.test(n));
  const diverges = view.structural_rating && view.structural_rating !== view.tactical_rating;
  const items = [
    view.callout,
    ...notes,
    view.structural_rating
      ? `${t('Long-run view')}: ${t(wordsFromResearch(view.structural_rating)?.primary ?? title(view.structural_rating))}; ${t('12-month view')}: ${t(wordsFromResearch(view.tactical_rating)?.primary ?? title(view.tactical_rating))}${diverges ? ` (${t('the views differ')})` : ''}.`
      : '',
    view.benchmark ? `${t('Benchmark expected return')} ${(view.benchmark.expected_return * 100).toFixed(1)}% (${view.benchmark.basis}).` : '',
    view.rating_definition,
    view.disclaimer,
  ].filter(Boolean);
  if (items.length === 0) return null;
  return (
    <details className={`${compact ? 'text-[12.5px]' : 'text-[13px]'} group`}>
      <summary className="cursor-pointer select-none text-foreground underline underline-offset-2 w-fit">{t('Why this rating')}</summary>
      <ul className="mt-2 space-y-1.5 border-l border-border pl-3 text-foreground/80 leading-relaxed">
        {items.map((s, i) => <li key={i}>{s}</li>)}
      </ul>
    </details>
  );
}

export function ResearchRatingBlock({ view, ticker, compact = false, delta, priorRunAt }: {
  view: ResearchView;
  ticker: string;
  compact?: boolean;
  /** Freshness check since the prior report (its events feed the "what's new" line). */
  delta?: FreshnessDelta | null;
  priorRunAt?: string | null;
}) {
  const sym = currencySymbol(ticker);
  const text = compact ? 'text-[12px]' : 'text-sm';
  const small = compact ? 'text-xs' : 'text-xs';
  const { t } = useI18n();

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
      <div className="flex items-center gap-2 flex-wrap">
        <RatingPill view={view} />
        {view.target_12m != null && <TargetPill value={view.target_12m} ticker={ticker} />}
        {view.benchmark && <span className="text-content-medium">{t('vs {b}', { b: view.benchmark.name })}</span>}
      </div>
      {/* Owner, 2026-11-27: the rating word explained in one visible line. */}
      <RatingOneLiner view={view} className="-mt-1" />

      {/* Owner, 2026-11-27: what this report did with material news since the prior one, in plain words. */}
      <MaterialNewsLine view={view} delta={delta} priorRunAt={priorRunAt} compact={compact} />

      {/* D2 (owner, 2026-11-28): research age and the span the news check covered, always visible. */}
      <ProvenanceLine p={view.provenance} compact={compact} />

      <WhyThisRating view={view} compact={compact} />

      {/* Owner, 2026-10-03: the price / target / upside / dividend / TSR row is removed on web and mobile;
          those figures remain in the price-target panel, on the pin's hover title and in the PDF. */}
    </div>
  );
}
