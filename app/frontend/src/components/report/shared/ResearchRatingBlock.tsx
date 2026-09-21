/**
 * Research rating header — shared by the desktop ReportHeader and the mobile
 * V2ReportView hero card, so the two render paths cannot drift.
 *
 * Carries the disclosure checklist a published rating needs: price with its
 * as-of date, the 12-month target, implied upside, dividend yield and implied
 * total shareholder return, the benchmark the rating is relative to, the
 * structural vs tactical views, any divergence / SOTP disclosure, the rating
 * definition and the rating-to-action disclaimer.
 */
import type { ResearchView } from '@/lib/reportTypes';
import { actionTone, priceTone } from '@/lib/semanticColors';
import { currencySymbol } from '@/lib/utils';

const pct = (x: number | null | undefined) =>
  (x == null ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`);
const title = (s?: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase() : '—');

export function RatingPill({ view, className = '' }: { view: ResearchView; className?: string }) {
  const key = view.under_review ? 'UNDER_REVIEW' : view.research_rating;
  return (
    <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${actionTone(key)} ${className}`}>
      {view.rating_label}
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
  const small = compact ? 'text-[10.5px]' : 'text-xs';
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
        <p className={`${compact ? 'text-[10px]' : 'text-[11px]'} text-content-muted leading-relaxed`}>
          {view.rating_definition} {view.disclaimer}
        </p>
      </div>
    );
  }

  return (
    <div className={`flex flex-col gap-3 ${text}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <RatingPill view={view} />
        {view.benchmark && <span className="text-content-medium">vs {view.benchmark.name}</span>}
        <span className={`${small} text-content-muted`}>· trade action {view.trade_action}</span>
      </div>

      {/* Disclosure checklist */}
      <dl className={`grid grid-cols-2 sm:grid-cols-5 gap-3 ${small}`}>
        <div>
          <dt className="uppercase tracking-wider text-content-muted">Price</dt>
          <dd className="font-semibold tabular-nums text-content-high">{view.price != null ? `${sym}${view.price.toFixed(2)}` : '—'}</dd>
          {view.price_as_of && <dd className="text-content-muted">close {view.price_as_of.slice(0, 10)}</dd>}
        </div>
        <div>
          <dt className="uppercase tracking-wider text-content-muted">12M Target</dt>
          <dd className="font-semibold tabular-nums text-content-high">{view.target_12m != null ? `${sym}${view.target_12m.toFixed(2)}` : '—'}</dd>
        </div>
        <div>
          <dt className="uppercase tracking-wider text-content-muted">Implied upside</dt>
          <dd className={`font-semibold tabular-nums ${priceTone(view.capital_gain_12m)}`}>{pct(view.capital_gain_12m)}</dd>
        </div>
        <div>
          <dt className="uppercase tracking-wider text-content-muted">Dividend yield</dt>
          <dd className="font-semibold tabular-nums text-content-high">
            {view.dividend_known ? pct(view.dividend_yield) : 'n/a'}
          </dd>
        </div>
        <div>
          <dt className="uppercase tracking-wider text-content-muted">12M TSR</dt>
          <dd className={`font-semibold tabular-nums ${priceTone(view.tsr_12m)}`}>{pct(view.tsr_12m)}</dd>
          {view.excess_return_bps != null && view.benchmark && (
            <dd className="text-content-muted tabular-nums">
              {view.excess_return_bps >= 0 ? '+' : ''}{view.excess_return_bps} bps vs {view.benchmark.code}
            </dd>
          )}
        </div>
      </dl>

      {/* TSR callout */}
      <div className="rounded-lg border border-[var(--hairline)] bg-surface-2 px-3 py-2 text-content-high leading-relaxed">
        {view.callout}
      </div>

      {view.structural_rating && (
        <div className={`flex items-center gap-2 flex-wrap ${small}`}>
          <span className="rounded border border-[var(--hairline)] px-2 py-0.5 text-content-high">
            Structural: {title(view.structural_rating)}
          </span>
          <span className="rounded border border-[var(--hairline)] px-2 py-0.5 text-content-high">
            Tactical (12M): {title(view.tactical_rating)}
          </span>
          {diverges && <span className="text-content-muted">views differ, see disclosure</span>}
        </div>
      )}

      {view.compliance.notes.length > 0 && (
        <ul className={`${small} text-content-medium leading-relaxed list-none space-y-1`}>
          {view.compliance.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}

      <p className={`${compact ? 'text-[10px]' : 'text-[11px]'} text-content-muted leading-relaxed`}>
        {view.rating_definition}{view.benchmark
          ? ` Benchmark expected return ${(view.benchmark.expected_return * 100).toFixed(1)}% (${view.benchmark.basis}).`
          : ''} {view.disclaimer}
      </p>
    </div>
  );
}
