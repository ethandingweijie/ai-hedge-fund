/**
 * IntelligenceGrid — displays the five intelligence/risk agents:
 *   Insider Activity · Analyst Revisions · News Sentiment
 *   Earnings Quality · Short Interest
 *
 * Data source priority:
 *   1. Dedicated state keys (web runs): data.insider_activity, data.analyst_revisions, etc.
 *   2. Merged analyst_signals (CLI archive runs): agentSignals.insider_activity_agent, etc.
 *
 * Each agent has its own field structure — this component maps them correctly
 * instead of blindly reading "signal" + "conviction" like investor agents.
 */

import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import type { AgentSignals } from '@/lib/reportTypes';
import { getIntelligence, type IntelligenceData } from '@/lib/api';
import { rankTextTone } from '@/lib/semanticColors';

interface IntelligenceGridProps {
  agentSignals?: AgentSignals;
  /** Full pipeline data — needed to read dedicated intel keys for web runs */
  pipelineData?: Record<string, unknown>;
  ticker: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** "HEAVILY_SHORTED" -> "Heavily shorted". */
function sentence(s: string): string {
  const t = s.replace(/_/g, ' ').toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function badge(text: string, color: string) {
  return (
    <span className={`inline-flex items-center h-6 px-2.5 rounded-full text-[12px] font-semibold ${color}`}>
      {sentence(text)}
    </span>
  );
}

/**
 * Signal pills: one accent for constructive reads, inverted monochrome for
 * negative ones, an outline for neutral. Green/red stay reserved for price
 * change; the label carries the direction.
 */
function signalColor(s: string): string {
  const u = (s ?? '').toUpperCase();
  if (['BULLISH', 'BUY', 'POSITIVE', 'HIGH_QUALITY', 'HIGH', 'ACCELERATING_UP', 'BEAT'].includes(u))
    return 'bg-brand text-white';
  if (['BEARISH', 'SELL', 'NEGATIVE', 'LOW_QUALITY', 'HEAVILY_SHORTED', 'ACCELERATING_DOWN', 'MISS'].includes(u))
    return 'bg-foreground text-background';
  return 'border border-border text-foreground';
}

/** Signals with no information: the card is hidden when one of these has no data either. */
const EMPTY_SIGNALS = new Set(['', 'UNKNOWN', 'N/A', 'NONE', 'NO_DATA', 'INSUFFICIENT_DATA']);

/** True when at least one of the given fields carries a real value (0 counts and empty strings do not). */
function hasAnyValue(...vals: unknown[]): boolean {
  return vals.some(v => v != null && v !== '' && v !== 0 && v !== '—' && !(typeof v === 'string' && EMPTY_SIGNALS.has(v.toUpperCase())));
}

function flagColor(f: string): string {
  const u = (f ?? '').toUpperCase();
  // Risk flags invert the usual ramp: a HIGH flag must stand out, while an
  // all-clear should recede rather than compete for attention.
  if (['RED', 'HIGH'].includes(u)) return rankTextTone(0);
  if (['AMBER', 'MEDIUM', 'MODERATE'].includes(u)) return 'text-warning';
  if (['GREEN', 'LOW'].includes(u)) return rankTextTone(3);
  return 'text-muted-foreground';
}

function makeFmtMoney(sym: string) {
  return (v: number | null | undefined): string => {
    if (v == null) return '—';
    const abs = Math.abs(v);
    const sign = v < 0 ? '-' : '+';
    if (abs >= 1e9) return `${sign}${sym}${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${sign}${sym}${(abs / 1e6).toFixed(1)}M`;
    if (abs >= 1e3) return `${sign}${sym}${(abs / 1e3).toFixed(0)}K`;
    return `${sign}${sym}${abs.toFixed(0)}`;
  };
}
// Module-level default — overridden inside the component
function fmtMoney(v: number | null | undefined): string { return makeFmtMoney('$')(v); }

function fmtNum(v: number | null | undefined, decimals = 1): string {
  if (v == null) return '—';
  return v.toFixed(decimals);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v.toFixed(1)}%`;
}

function Row({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  // Empty readings are left out rather than shown as "—".
  if (value === '—' || value === '') return null;
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-[13px] text-muted-foreground truncate">{label}</span>
      <span className={`text-[14px] font-semibold tabular-nums shrink-0 ${valueClass ?? ''}`}>{value}</span>
    </div>
  );
}

const yesNo = (v: unknown) => (v == null ? '—' : v ? 'Yes' : 'No');
const enumText = (v: unknown) => (v == null || v === '' ? '—' : sentence(String(v)));

// ── Agent card renderers ───────────────────────────────────────────────────────
// Each card has a matching *HasData check; the grid drops the cards with
// nothing to say (an "Unknown" pill over a column of dashes reads as broken).

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type D = Record<string, any>;

function insiderHasData(d: D) {
  return hasAnyValue(d.net_buying_30d_usd, d.net_buying_90d_usd, d.buy_sell_ratio_12m, d.cluster_buy, d.conviction_sell_flag);
}
function InsiderCard({ d }: { d: D }) {
  const sig = d.signal ?? d.insider_signal ?? '';
  return (
    <AgentCard title="Insider activity" signal={sig}>
      <Row label="Net buying (30d)"     value={d.net_buying_30d_usd ? fmtMoney(d.net_buying_30d_usd) : '—'} />
      <Row label="Net buying (90d)"     value={d.net_buying_90d_usd ? fmtMoney(d.net_buying_90d_usd) : '—'} />
      <Row label="Buy/sell ratio (12m)" value={d.buy_sell_ratio_12m != null ? fmtNum(d.buy_sell_ratio_12m, 2) : '—'} />
      <Row label="Cluster buying"       value={yesNo(d.cluster_buy)} />
      <Row label="Conviction selling"   value={yesNo(d.conviction_sell_flag)} />
      {d.analysis_note && <p className="text-[13px] text-muted-foreground mt-1 line-clamp-2">{d.analysis_note}</p>}
    </AgentCard>
  );
}

function revisionHasData(d: D) {
  return hasAnyValue(d.surprise_streak, d.eps_dispersion_pct, d.revenue_dispersion_pct, d.analyst_count, d.surprise_direction);
}
function RevisionCard({ d }: { d: D }) {
  const sig = d.revision_direction ?? d.signal ?? '';
  const streak = d.surprise_streak;
  return (
    <AgentCard title="Analyst revisions" signal={sig}>
      <Row label="Last surprise"      value={hasAnyValue(d.surprise_direction) ? enumText(d.surprise_direction) : '—'} />
      <Row label="Surprise streak"    value={streak ? (streak > 0 ? `${streak} beats` : `${Math.abs(streak)} misses`) : '—'} />
      <Row label="EPS dispersion"     value={d.eps_dispersion_pct != null ? fmtPct(d.eps_dispersion_pct) : '—'} />
      <Row label="Revenue dispersion" value={d.revenue_dispersion_pct != null ? fmtPct(d.revenue_dispersion_pct) : '—'} />
      <Row label="Analysts"           value={d.analyst_count ? String(d.analyst_count) : '—'} />
      {d.analysis_note && <p className="text-[13px] text-muted-foreground mt-1 line-clamp-2">{d.analysis_note}</p>}
    </AgentCard>
  );
}

function sentimentHasData(d: D) {
  return hasAnyValue(d.article_count, d.bullish_count, d.bearish_count, d.press_release_count);
}
function SentimentCard({ d }: { d: D }) {
  const sig = d.signal ?? '';
  const score = d.composite_score;
  return (
    <AgentCard title="News sentiment" signal={sig}>
      <Row label="Sentiment score"     value={score != null && d.article_count ? fmtNum(score, 2) : '—'} />
      <Row label="Articles read"       value={d.article_count ? String(d.article_count) : '—'} />
      <Row label="Positive / negative" value={(d.bullish_count || d.bearish_count) ? `${d.bullish_count ?? 0} / ${d.bearish_count ?? 0}` : '—'} />
      <Row label="Volume spike"        value={d.volume_spike ? 'Yes' : '—'} />
      {d.top_headlines?.[0] && <p className="text-[13px] text-muted-foreground mt-1 line-clamp-2">“{d.top_headlines[0]}”</p>}
    </AgentCard>
  );
}

function earningsHasData(d: D) {
  return hasAnyValue(d.overall_quality_score, d.pre_earnings_risk, d.accrual_flag, d.cash_conversion_flag, d.fcf_ni_divergence);
}
function EarningsQualityCard({ d }: { d: D }) {
  const sig = d.quality_verdict ?? d.signal ?? '';
  const score = d.overall_quality_score;
  return (
    <AgentCard title="Earnings quality" signal={sig}>
      {score != null && <Row label="Quality score" value={`${fmtNum(score, 1)} / 10`} />}
      <Row label="Pre-earnings risk" value={enumText(d.pre_earnings_risk)}    valueClass={d.pre_earnings_risk ? flagColor(d.pre_earnings_risk) : ''} />
      <Row label="Accruals"          value={enumText(d.accrual_flag)}         valueClass={d.accrual_flag ? flagColor(d.accrual_flag) : ''} />
      <Row label="Accrual trend"     value={enumText(d.accrual_trend)} />
      <Row label="Cash conversion"   value={enumText(d.cash_conversion_flag)} valueClass={d.cash_conversion_flag ? flagColor(d.cash_conversion_flag) : ''} />
      <Row label="FCF vs net income" value={enumText(d.fcf_ni_divergence)}    valueClass={d.fcf_ni_divergence ? flagColor(d.fcf_ni_divergence) : ''} />
      <Row label="SBC drag"          value={d.sbc_drag_flag ? enumText(d.sbc_drag_flag) : (d.sbc_drag_pct != null ? fmtPct(d.sbc_drag_pct) : '—')} valueClass={d.sbc_drag_flag ? flagColor(d.sbc_drag_flag) : ''} />
      {d.flags?.length > 0 && <p className="text-[13px] text-foreground mt-1 line-clamp-2">{d.flags[0]}</p>}
    </AgentCard>
  );
}

function shortHasData(d: D) {
  return hasAnyValue(d.short_float_pct, d.si_short_float_pct, d.days_to_cover, d.borrow_rate_pct, d.short_interest_trend);
}
function ShortInterestCard({ d }: { d: D }) {
  const sig = d.signal ?? d.si_signal ?? '';
  const float = d.short_float_pct ?? d.si_short_float_pct;
  return (
    <AgentCard title="Short interest" signal={sig}>
      <Row label="Short float"   value={float != null ? fmtPct(float) : '—'} />
      <Row label="Days to cover" value={d.days_to_cover != null ? fmtNum(d.days_to_cover, 1) : '—'} />
      <Row label="Borrow rate"   value={d.borrow_rate_pct != null ? fmtPct(d.borrow_rate_pct) : '—'} />
      <Row label="Trend"         value={enumText(d.short_interest_trend)} />
      <Row label="Squeeze risk"  value={(d.squeeze_risk || d.si_squeeze_risk) ? 'Yes' : '—'} />
      <Row label="Crowded trade" value={(d.crowded_trade || d.si_crowded_trade) ? 'Yes' : '—'} />
    </AgentCard>
  );
}

// ── Generic card shell ────────────────────────────────────────────────────────

function AgentCard({ title, signal, children }: { title: string; signal: string; children: React.ReactNode }) {
  const showSignal = !!signal && !EMPTY_SIGNALS.has(signal.toUpperCase());
  return (
    <div className="border-t border-border pt-3">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="text-[15px] font-semibold text-foreground">{title}</span>
        {showSignal && badge(signal, signalColor(signal))}
      </div>
      {children}
    </div>
  );
}

// ── Data resolution ───────────────────────────────────────────────────────────
// Try dedicated state key first (web runs), then merged agentSignals (CLI runs)

function resolve(
  ticker: string,
  agentSignals: AgentSignals | undefined,
  pipelineData: Record<string, unknown> | undefined,
  agentKey: string,   // e.g. "insider_activity_agent"
  dataKey: string,    // e.g. "insider_activity" or "insider_activity_agent"
): D | null {
  // 1. Direct pipeline data key (web runs)
  if (pipelineData) {
    const byTicker = pipelineData[dataKey] as Record<string, D> | undefined;
    const d = byTicker?.[ticker] ?? (byTicker ? Object.values(byTicker)[0] : undefined);
    if (d && typeof d === 'object') return d as D;

    // also try with _agent suffix
    const byTicker2 = pipelineData[agentKey] as Record<string, D> | undefined;
    const d2 = byTicker2?.[ticker] ?? (byTicker2 ? Object.values(byTicker2)[0] : undefined);
    if (d2 && typeof d2 === 'object') return d2 as D;
  }

  // 2. Merged agentSignals (CLI archive runs)
  if (agentSignals) {
    const byTicker = agentSignals[agentKey];
    const d = byTicker?.[ticker] ?? Object.values(byTicker ?? {})[0];
    if (d) return d as unknown as D;
  }

  return null;
}

// ── Main component ─────────────────────────────────────────────────────────────

export function IntelligenceGrid({ agentSignals, pipelineData, ticker }: IntelligenceGridProps) {
  // NB: the module-level `fmtMoney` (line 70, hard-coded '$') is what the Row
  // helper components consume. A component-scoped `fmtMoney` using
  // `currencySymbol(ticker)` was previously declared here but never referenced
  // — removed to silence TS6133 and avoid misleading future readers into
  // thinking the grid is currency-aware when it isn't.
  const [liveData, setLiveData] = useState<IntelligenceData | null>(null);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    setFetching(true);
    getIntelligence(ticker)
      .then(setLiveData)
      .catch(() => setLiveData(null))
      .finally(() => setFetching(false));
  }, [ticker]);

  // Live FMP data takes priority; fall back to stored pipeline data for any agent
  // that the live fetch failed to return (e.g. FMP_API_KEY not configured).
  const insider   = liveData?.insider_activity  as D | undefined
                    ?? resolve(ticker, agentSignals, pipelineData, 'insider_activity_agent',  'insider_activity');
  const revision  = liveData?.analyst_revisions as D | undefined
                    ?? resolve(ticker, agentSignals, pipelineData, 'analyst_revision_agent',  'analyst_revisions');
  const sentiment = liveData?.news_sentiment    as D | undefined
                    ?? resolve(ticker, agentSignals, pipelineData, 'news_sentiment_agent',    'news_sentiment');
  const earnings  = liveData?.earnings_quality  as D | undefined
                    ?? resolve(ticker, agentSignals, pipelineData, 'earnings_quality_agent',  'earnings_quality');
  const shortInt  = liveData?.short_interest    as D | undefined
                    ?? resolve(ticker, agentSignals, pipelineData, 'short_interest_agent',    'short_interest');

  // Only agents with something to report get a card.
  const showInsider   = !!insider   && insiderHasData(insider);
  const showRevision  = !!revision  && revisionHasData(revision);
  const showSentiment = !!sentiment && sentimentHasData(sentiment);
  const showEarnings  = !!earnings  && earningsHasData(earnings);
  const showShort     = !!shortInt  && shortHasData(shortInt);
  const hasAny = showInsider || showRevision || showSentiment || showEarnings || showShort;

  if (fetching && !hasAny) {
    return (
      <Card className="p-5 flex items-center justify-center min-h-[120px]">
        <p className="text-[13px] text-muted-foreground">Loading signals…</p>
      </Card>
    );
  }

  // Nothing to say: no card at all, rather than an empty-state box.
  if (!hasAny) return null;

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[15px] font-semibold">Signals</h3>
        {fetching
          ? <span className="text-[12px] text-muted-foreground animate-pulse">Refreshing…</span>
          : liveData && <span className="text-[12px] text-muted-foreground">Live</span>}
      </div>
      <div className="mobile-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4">
        {showInsider   && <InsiderCard         d={insider!}   />}
        {showRevision  && <RevisionCard        d={revision!}  />}
        {showSentiment && <SentimentCard       d={sentiment!} />}
        {showEarnings  && <EarningsQualityCard d={earnings!}  />}
        {showShort     && <ShortInterestCard   d={shortInt!}  />}
      </div>
    </Card>
  );
}
