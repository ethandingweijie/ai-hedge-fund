/**
 * ReportHero -- the top of a desktop report.
 *
 * Replaces the old ReportHeader card + StockPanel side card with one flat,
 * price-first layout (owner, 2026-11-24: "Robinhood-style", chart above the
 * verdict):
 *
 *   company line -> big price + period change -> full-width chart -> range tabs
 *   -> Our verdict (rating, target, upside, position, regime, headline, thesis)
 *   -> Key statistics -> Grades
 *
 * Shared by ReportViewPage (saved run) and ReportPage (live run). The mobile
 * path (V2ReportView) has its own hero; keep the two in step.
 *
 * Colour: green/red only on price change and upside; the rating pill takes the
 * cobalt accent through actionTone. Empty values are omitted, not shown as "—".
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { MessageSquare } from 'lucide-react';
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from 'recharts';
import { getStockData } from '@/lib/api';
import { currencySymbol } from '@/lib/utils';
import { formatSector } from '@/lib/gradeColors';
import { actionTone, gradeTone } from '@/lib/semanticColors';
import { useCompanyProfile } from '@/hooks/use-company-name';
import type { MacroRegime, PortfolioDecision, VgpmResult } from '@/lib/reportTypes';
import { RationaleBlock } from '@/components/report/shared/RationaleBlock';
import { ResearchRatingBlock, TargetPill } from '@/components/report/shared/ResearchRatingBlock';
import { HeroSectionTitle } from '@/components/report/SectionAnchor';
import { METRICS, TIMEFRAMES, makeFmtLarge, makeFmtPrice, type StockData } from '@/lib/stockMetrics';
import { useI18n } from '@/i18n';

const PERIOD_LABEL: Record<string, string> = {
  '1D': 'Today', '1W': 'Past week', '1M': 'Past month', '3M': 'Past 3 months',
  '1Y': 'Past year', '3Y': 'Past 3 years', '5Y': 'Past 5 years',
};

/** Sentence-case labels for the key-statistics grid (StockPanel keeps its own). */
const STAT_LABEL: Record<string, string> = {
  market_cap: 'Market cap', revenue: 'Revenue (TTM)', free_cash_flow: 'Free cash flow',
  net_margin: 'Net margin', pe_ratio: 'P/E (TTM)', price_to_sales: 'P/S (TTM)', price_to_book: 'P/B',
  ev_to_ebitda: 'EV / EBITDA', revenue_growth: 'Revenue growth', eps_ttm: 'EPS (TTM)',
  return_on_equity: 'Return on equity', return_on_invested_capital: 'ROIC',
  free_cash_flow_yield: 'FCF yield', dividend_yield: 'Dividend yield', net_cash: 'Net cash',
  fifty_two_week_high: '52-week high', fifty_two_week_low: '52-week low',
  day_high: 'Day high', day_low: 'Day low', volume: 'Volume',
};

const VGPM_DIMS = [
  { key: 'valuation', label: 'Valuation' },
  { key: 'growth', label: 'Growth' },
  { key: 'profitability', label: 'Profitability' },
  { key: 'momentum', label: 'Momentum' },
] as const;

/** Resolve a theme token (an HSL triple in index.css) to a colour recharts can paint. */
function tokenColor(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v ? `hsl(${v})` : fallback;
}

function cap(s?: string | null) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

interface ReportHeroProps {
  ticker: string;
  runAt?: string;
  modelName?: string;
  decision?: PortfolioDecision;
  regime?: MacroRegime;
  /** Price at run time (scenario analysis); upside is measured from it. */
  currentPrice?: number;
  sector?: string;
  subSector?: string;
  vgpm?: VgpmResult;
}

export function ReportHero({
  ticker, runAt, modelName, decision, regime, currentPrice, sector, subSector, vgpm,
}: ReportHeroProps) {
  const { t } = useI18n();
  const sym = currencySymbol(ticker);
  const fmtLarge = makeFmtLarge(sym);
  const fmtPrice = makeFmtPrice(sym);
  const profile = useCompanyProfile(ticker);
  const companyName = profile?.name ?? null;
  const displaySector = formatSector(sector || profile?.sector || null);
  const displaySub = formatSector(subSector || profile?.industry || null);

  const [tfIdx, setTfIdx] = useState(4); // 1Y
  const [stock, setStock] = useState<StockData | null>(null);
  const [loading, setLoading] = useState(true);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const tf = TIMEFRAMES[tfIdx];

  const load = useCallback((period: string) => {
    setLoading(true);
    getStockData(ticker, period)
      .then(setStock)
      .catch(() => setStock(null))
      .finally(() => setLoading(false));
  }, [ticker]);
  useEffect(() => { if (ticker) load(tf.period); }, [tf.period, load, ticker]);

  const history = useMemo(() => stock?.history ?? [], [stock]);
  const first = history[0]?.close;
  const lastIdx = history.length - 1;
  const shownIdx = hoverIdx ?? lastIdx;
  const shownPrice = history[shownIdx]?.close ?? currentPrice;
  const change = first != null && shownPrice != null ? shownPrice - first : null;
  const changePct = change != null && first ? (change / first) * 100 : null;
  const up = (change ?? 0) >= 0;
  const lineColor = up ? tokenColor('--gain', '#10B981') : tokenColor('--loss', '#EF4444');
  const mutedColor = tokenColor('--muted-foreground', '#6B7280');
  const shownLabel = hoverIdx != null && history[hoverIdx]
    ? new Date(history[hoverIdx].date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : t(PERIOD_LABEL[tf.label] ?? tf.label);

  const domain = useMemo<[number, number]>(() => {
    if (!history.length) return [0, 1];
    const lo = Math.min(...history.map(h => h.close));
    const hi = Math.max(...history.map(h => h.close));
    const pad = (hi - lo) * 0.08 || hi * 0.02;
    return [lo - pad, hi + pad];
  }, [history]);

  // ── Verdict figures ──────────────────────────────────────────────────────
  const target = decision?.price_target != null && decision.price_target > 0 ? decision.price_target : null;
  const upside = target != null && currentPrice ? (target / currentPrice - 1) * 100 : null;
  const position = decision?.position_size_pct != null ? decision.position_size_pct * 100 : null;
  const rated = !!decision?.research_view;

  const stats = stock
    ? METRICS
        .map(({ key, currency, price, fmt }) => {
          const v = stock.metrics[key];
          if (v == null) return null;
          const f = currency ? fmtLarge : price ? fmtPrice : (fmt ?? (() => '—'));
          return { key, label: t(STAT_LABEL[key] ?? key), value: f(v) };
        })
        .filter((s): s is { key: keyof StockData['metrics']; label: string; value: string } => s != null)
    : [];

  const runDate = runAt && !isNaN(new Date(runAt).getTime())
    ? new Date(runAt).toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' })
    : null;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_260px] gap-x-14 gap-y-10 items-start">
      <div className="min-w-0 flex flex-col gap-14">

        {/* ── Price + chart ─────────────────────────────────────────────── */}
        <section className="flex flex-col">
          <p className="text-sm text-muted-foreground">
            {[ticker, displaySector ? t(displaySector) : null, displaySub && displaySub !== displaySector ? displaySub : null]
              .filter(Boolean).join(' · ')}
          </p>
          <h1 className="text-[32px] font-semibold tracking-tight leading-tight text-foreground">
            {companyName ?? ticker}
          </h1>
          <p className="mt-2 text-[52px] font-bold tracking-tight leading-none tabular-nums text-foreground">
            {shownPrice != null ? `${sym}${shownPrice.toFixed(2)}` : '—'}
          </p>
          {change != null && changePct != null && (
            <p className={`mt-2 text-[15px] font-medium tabular-nums ${up ? 'text-gain' : 'text-loss'}`}>
              {up ? '+' : '−'}{sym}{Math.abs(change).toFixed(2)} ({up ? '+' : '−'}{Math.abs(changePct).toFixed(2)}%)
              <span className="ml-1.5 font-normal text-muted-foreground">{shownLabel}</span>
            </p>
          )}

          <div className="mt-6 h-[280px]">
            {loading ? (
              <div className="h-full flex items-center justify-center text-sm text-muted-foreground">{t('Loading chart…')}</div>
            ) : history.length === 0 ? (
              <div className="h-full flex items-center justify-center text-sm text-muted-foreground">{t('No price data.')}</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={history}
                  margin={{ top: 8, right: 0, left: 0, bottom: 8 }}
                  onMouseMove={(s) => {
                    const i = (s as { activeTooltipIndex?: number | string } | undefined)?.activeTooltipIndex;
                    setHoverIdx(i == null || i === '' ? null : Number(i));
                  }}
                  onMouseLeave={() => setHoverIdx(null)}
                >
                  <defs>
                    <linearGradient id="heroGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={lineColor} stopOpacity={0.14} />
                      <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <YAxis hide domain={domain} />
                  {first != null && (
                    <ReferenceLine y={first} stroke={mutedColor} strokeDasharray="2 5" strokeOpacity={0.7} />
                  )}
                  <Tooltip content={() => null} cursor={{ stroke: mutedColor, strokeWidth: 1 }} />
                  <Area
                    type="monotone"
                    dataKey="close"
                    stroke={lineColor}
                    strokeWidth={2}
                    fill="url(#heroGrad)"
                    dot={false}
                    activeDot={{ r: 4, fill: lineColor, strokeWidth: 0 }}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="mt-2 flex gap-1 border-b border-border" role="tablist" aria-label={t('Chart range')}>
            {TIMEFRAMES.map((t, i) => (
              <button
                key={t.label}
                role="tab"
                aria-selected={tfIdx === i}
                onClick={() => { setTfIdx(i); setHoverIdx(null); }}
                className={`h-11 px-3 -mb-px text-[13px] font-semibold border-b-2 transition-colors ${
                  tfIdx === i
                    ? `${up ? 'text-gain border-gain' : 'text-loss border-loss'}`
                    : 'text-muted-foreground border-transparent hover:text-foreground'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </section>

        {/* ── Our verdict ───────────────────────────────────────────────── */}
        {decision && (
          <section>
            <HeroSectionTitle>Our verdict</HeroSectionTitle>
            {rated ? (
              <ResearchRatingBlock view={decision.research_view!} ticker={ticker} />
            ) : (
              <div className="flex flex-wrap items-center gap-x-10 gap-y-5">
                <span className={`inline-flex items-center h-10 px-5 rounded-full text-lg font-bold ${actionTone(decision.action)}`}>
                  {t(cap(decision.action?.toLowerCase()))}
                </span>
                {target != null && <TargetPill value={target} ticker={ticker} className="h-10 px-5 py-0 text-lg" />}
                {upside != null && (
                  <Figure
                    label="Upside"
                    value={`${upside >= 0 ? '+' : '−'}${Math.abs(upside).toFixed(0)}%`}
                    className={upside >= 0 ? 'text-gain' : 'text-loss'}
                  />
                )}
                {position != null && <Figure label="Position" value={`${position.toFixed(1)}%`} />}
                {regime?.risk_appetite && (
                  <Figure
                    label="Regime"
                    value={t(cap(regime.risk_appetite))}
                    sub={regime.volatility_regime ? t('{v} vol', { v: t(regime.volatility_regime) }) : undefined}
                  />
                )}
              </div>
            )}
            {rated && (position != null || regime?.risk_appetite) && (
              <div className="mt-6 flex flex-wrap gap-x-10 gap-y-4">
                {position != null && <Figure label="Position" value={`${position.toFixed(1)}%`} />}
                {regime?.risk_appetite && (
                  <Figure label="Regime" value={t(cap(regime.risk_appetite))}
                    sub={regime.volatility_regime ? t('{v} vol', { v: t(regime.volatility_regime) }) : undefined} />
                )}
              </div>
            )}

            {decision.headline && (
              <p className="mt-8 max-w-[40ch] text-2xl font-semibold leading-snug tracking-tight text-foreground">
                {decision.headline}
              </p>
            )}
            {decision.rationale && (
              <RationaleBlock
                text={decision.rationale}
                className="mt-4 max-w-[68ch]"
                itemClassName="text-[17px] text-foreground/80 leading-relaxed"
              />
            )}
            {decision.price_target_override && (
              <p className="mt-3 text-xs text-muted-foreground">
                {decision.pm_regenerated_at
                  ? t('Thesis and headline were rewritten on your estimate override.')
                  : t('Target reflects your estimate override; the thesis was written before it. Rewrite it from the estimates panel.')}
              </p>
            )}
          </section>
        )}

        {/* ── Key statistics ────────────────────────────────────────────── */}
        {stats.length > 0 && (
          <section>
            <HeroSectionTitle>Key statistics</HeroSectionTitle>
            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-6">
              {stats.map(s => (
                <div key={s.key} className="min-w-0">
                  <dt className="text-[13px] text-muted-foreground">{s.label}</dt>
                  <dd className="mt-1 text-[17px] font-semibold tabular-nums text-foreground">{s.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {/* ── Grades ────────────────────────────────────────────────────── */}
        {vgpm && VGPM_DIMS.some(d => vgpm[d.key]?.grade) && (
          <section>
            <HeroSectionTitle>Grades</HeroSectionTitle>
            <div className="grid grid-cols-4 gap-6 max-w-xl">
              {VGPM_DIMS.map(({ key, label }) => {
                const dim = vgpm[key];
                if (!dim?.grade) return <div key={key} />;
                const tip = [`${label}: ${dim.score}/100`, ...(dim.subs ?? [])].join('\n');
                return (
                  <div key={key} title={tip} className="cursor-help">
                    <p className={`text-[32px] font-bold leading-none ${gradeTone(dim.grade, 'text')}`}>{dim.grade}</p>
                    <p className="mt-2 text-[13px] text-muted-foreground">{t(label)}</p>
                  </div>
                );
              })}
            </div>
          </section>
        )}
      </div>

      {/* ── Action rail ─────────────────────────────────────────────────── */}
      <aside className="lg:sticky lg:top-20 flex flex-col gap-3 rounded-2xl border border-border p-6">
        <p className="text-[17px] font-semibold text-foreground">{ticker}</p>
        {runDate && (
          <p className="-mt-2 text-[13px] text-muted-foreground" title={modelName ? `${t('Model')}: ${modelName}` : undefined}>
            {t('Report run {date}', { date: runDate })}
          </p>
        )}
        <Link
          to={`/discuss/${ticker}`}
          className="mt-2 inline-flex items-center justify-center gap-2 h-12 rounded-full bg-brand text-brand-foreground text-[15px] font-semibold hover:bg-brand/90 transition-colors"
        >
          <MessageSquare size={16} />
          {t('Discuss this report')}
        </Link>
      </aside>
    </div>
  );
}

function Figure({ label, value, sub, className = 'text-foreground' }: {
  label: string; value: string; sub?: string; className?: string;
}) {
  const { t } = useI18n();
  return (
    <div>
      <p className="text-[13px] text-muted-foreground">{t(label)}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${className}`}>{value}</p>
      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </div>
  );
}
