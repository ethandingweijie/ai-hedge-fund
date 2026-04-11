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
import { currencySymbol } from '@/lib/utils';
import type { AgentSignals } from '@/lib/reportTypes';
import { getIntelligence, type IntelligenceData } from '@/lib/api';

interface IntelligenceGridProps {
  agentSignals?: AgentSignals;
  /** Full pipeline data — needed to read dedicated intel keys for web runs */
  pipelineData?: Record<string, unknown>;
  ticker: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function badge(text: string, color: string) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${color}`}>
      {text}
    </span>
  );
}

function signalColor(s: string): string {
  const u = (s ?? '').toUpperCase();
  if (['BULLISH', 'BUY', 'POSITIVE', 'HIGH_QUALITY', 'HIGH', 'ACCELERATING_UP', 'BEAT'].includes(u))
    return 'bg-green-600 text-white';
  if (['BEARISH', 'SELL', 'NEGATIVE', 'LOW_QUALITY', 'HEAVILY_SHORTED', 'ACCELERATING_DOWN', 'MISS'].includes(u))
    return 'bg-red-600 text-white';
  if (['NEUTRAL', 'HOLD', 'STABLE', 'MEDIUM', 'MODERATELY_SHORTED', 'DECELERATING'].includes(u))
    return 'bg-yellow-500 text-white';
  if (['LOW_SHORT_INTEREST', 'LOW'].includes(u))
    return 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300';
  return 'bg-muted text-muted-foreground';
}

function flagColor(f: string): string {
  const u = (f ?? '').toUpperCase();
  if (['RED', 'HIGH'].includes(u)) return 'text-red-500 dark:text-red-400';
  if (['AMBER', 'MEDIUM', 'MODERATE'].includes(u)) return 'text-amber-500 dark:text-amber-400';
  if (['GREEN', 'LOW'].includes(u)) return 'text-green-600 dark:text-green-400';
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
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] text-muted-foreground truncate">{label}</span>
      <span className={`text-[11px] font-semibold tabular-nums shrink-0 ${valueClass ?? ''}`}>{value}</span>
    </div>
  );
}

// ── Agent card renderers ───────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type D = Record<string, any>;

function InsiderCard({ d }: { d: D }) {
  const sig = d.signal ?? d.insider_signal ?? '';
  return (
    <AgentCard title="Insider Activity" signal={sig}>
      <Row label="Net Buying (30d)"  value={fmtMoney(d.net_buying_30d_usd)} valueClass={d.net_buying_30d_usd > 0 ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'} />
      <Row label="Net Buying (90d)"  value={fmtMoney(d.net_buying_90d_usd)} />
      <Row label="Buy/Sell Ratio 12m" value={d.buy_sell_ratio_12m != null ? fmtNum(d.buy_sell_ratio_12m, 2) : '—'} />
      <Row label="Cluster Buy"       value={d.cluster_buy != null ? (d.cluster_buy ? 'Yes' : 'No') : '—'} valueClass={d.cluster_buy ? 'text-green-600 dark:text-green-400' : ''} />
      <Row label="Conviction Sell"   value={d.conviction_sell_flag != null ? (d.conviction_sell_flag ? '⚠ Yes' : 'No') : '—'} valueClass={d.conviction_sell_flag ? 'text-red-500' : ''} />
      {d.data_source && <Row label="Source" value={d.data_source} />}
      {d.analysis_note && <p className="text-[10px] text-muted-foreground/80 mt-1 line-clamp-2 italic">{d.analysis_note}</p>}
    </AgentCard>
  );
}

function RevisionCard({ d }: { d: D }) {
  const sig = d.revision_direction ?? d.signal ?? '';
  return (
    <AgentCard title="Analyst Revisions" signal={sig}>
      <Row label="Surprise Direction" value={d.surprise_direction ?? '—'} />
      <Row label="Surprise Streak"    value={d.surprise_streak != null ? (d.surprise_streak > 0 ? `+${d.surprise_streak} beats` : `${d.surprise_streak} misses`) : '—'} valueClass={d.surprise_streak > 0 ? 'text-green-600 dark:text-green-400' : d.surprise_streak < 0 ? 'text-red-500 dark:text-red-400' : ''} />
      <Row label="EPS Dispersion"     value={d.eps_dispersion_pct != null ? fmtPct(d.eps_dispersion_pct) : '—'} />
      <Row label="Rev Dispersion"     value={d.revenue_dispersion_pct != null ? fmtPct(d.revenue_dispersion_pct) : '—'} />
      <Row label="Estimate Spread"    value={d.estimate_dispersion ?? '—'} />
      <Row label="Analyst Count"      value={d.analyst_count != null ? String(d.analyst_count) : '—'} />
      {d.analysis_note && <p className="text-[10px] text-muted-foreground/80 mt-1 line-clamp-2 italic">{d.analysis_note}</p>}
    </AgentCard>
  );
}

function SentimentCard({ d }: { d: D }) {
  const sig = d.signal ?? '';
  const score = d.composite_score;
  return (
    <AgentCard title="News Sentiment" signal={sig}>
      <Row label="Composite Score"   value={score != null ? fmtNum(score, 3) : '—'} valueClass={score != null ? (score > 0 ? 'text-green-600 dark:text-green-400' : score < 0 ? 'text-red-500 dark:text-red-400' : '') : ''} />
      <Row label="Articles Analysed" value={d.article_count != null ? String(d.article_count) : '—'} />
      <Row label="Bullish / Bearish" value={(d.bullish_count != null && d.bearish_count != null) ? `${d.bullish_count} / ${d.bearish_count}` : '—'} />
      <Row label="Press Releases"    value={d.press_release_signal ?? (d.press_release_count != null ? String(d.press_release_count) : '—')} />
      <Row label="Volume Spike"      value={d.volume_spike != null ? (d.volume_spike ? '⚑ Yes' : 'No') : '—'} valueClass={d.volume_spike ? 'text-amber-500' : ''} />
      {d.top_headlines?.[0] && <p className="text-[10px] text-muted-foreground/80 mt-1 line-clamp-2 italic">"{d.top_headlines[0]}"</p>}
    </AgentCard>
  );
}

function EarningsQualityCard({ d }: { d: D }) {
  const sig = d.quality_verdict ?? d.signal ?? '';
  const score = d.overall_quality_score ?? d.overall_quality_score;
  return (
    <AgentCard title="Earnings Quality" signal={sig}>
      {score != null && <Row label="Quality Score" value={`${fmtNum(score, 1)} / 10`} valueClass={score >= 7 ? 'text-green-600 dark:text-green-400' : score >= 4 ? 'text-amber-500' : 'text-red-500 dark:text-red-400'} />}
      <Row label="Pre-Earnings Risk"  value={d.pre_earnings_risk ?? '—'} valueClass={d.pre_earnings_risk ? flagColor(d.pre_earnings_risk) : ''} />
      <Row label="Accrual Flag"       value={d.accrual_flag ?? '—'} valueClass={d.accrual_flag ? flagColor(d.accrual_flag) : ''} />
      <Row label="Accrual Trend"      value={d.accrual_trend ?? '—'} />
      <Row label="Cash Conversion"    value={d.cash_conversion_flag ?? '—'} valueClass={d.cash_conversion_flag ? flagColor(d.cash_conversion_flag) : ''} />
      <Row label="FCF vs NI"         value={d.fcf_ni_divergence ?? '—'} valueClass={d.fcf_ni_divergence ? flagColor(d.fcf_ni_divergence) : ''} />
      <Row label="SBC Drag"          value={d.sbc_drag_flag ?? (d.sbc_drag_pct != null ? fmtPct(d.sbc_drag_pct) : '—')} valueClass={d.sbc_drag_flag ? flagColor(d.sbc_drag_flag) : ''} />
      {d.flags?.length > 0 && <p className="text-[10px] text-red-500/80 mt-1 line-clamp-2">⚠ {d.flags[0]}</p>}
    </AgentCard>
  );
}

function ShortInterestCard({ d }: { d: D }) {
  const sig = d.signal ?? d.si_signal ?? '';
  return (
    <AgentCard title="Short Interest" signal={sig}>
      <Row label="Short Float %"    value={fmtPct(d.short_float_pct ?? d.si_short_float_pct)} valueClass={d.short_float_pct > 20 ? 'text-red-500 dark:text-red-400' : d.short_float_pct > 10 ? 'text-amber-500' : 'text-green-600 dark:text-green-400'} />
      <Row label="Days to Cover"    value={d.days_to_cover != null ? fmtNum(d.days_to_cover, 1) : '—'} />
      <Row label="Borrow Rate"      value={d.borrow_rate_pct != null ? fmtPct(d.borrow_rate_pct) : '—'} />
      <Row label="SI Trend"         value={d.short_interest_trend ?? '—'} valueClass={d.short_interest_trend === 'INCREASING' ? 'text-red-500 dark:text-red-400' : d.short_interest_trend === 'DECREASING' ? 'text-green-600 dark:text-green-400' : ''} />
      <Row label="Squeeze Risk"     value={d.squeeze_risk != null ? (d.squeeze_risk ? '⚠ Yes' : 'No') : (d.si_squeeze_risk ? '⚠ Yes' : '—')} valueClass={(d.squeeze_risk || d.si_squeeze_risk) ? 'text-red-500' : ''} />
      <Row label="Crowded Trade"    value={d.crowded_trade != null ? (d.crowded_trade ? 'Yes' : 'No') : (d.si_crowded_trade ? 'Yes' : '—')} valueClass={(d.crowded_trade || d.si_crowded_trade) ? 'text-amber-500' : ''} />
    </AgentCard>
  );
}

// ── Generic card shell ────────────────────────────────────────────────────────

function AgentCard({ title, signal, children }: { title: string; signal: string; children: React.ReactNode }) {
  return (
    <div className="border border-border rounded-lg p-3 space-y-1.5">
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-xs font-semibold">{title}</span>
        {signal ? badge(signal.replace(/_/g, ' '), signalColor(signal)) : (
          <span className="text-[10px] text-muted-foreground">No signal</span>
        )}
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
  const fmtMoney = makeFmtMoney(currencySymbol(ticker));
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

  const hasAny = insider || revision || sentiment || earnings || shortInt;

  if (fetching && !hasAny) {
    return (
      <Card className="p-4 flex items-center justify-center min-h-[120px]">
        <p className="text-xs text-muted-foreground">Loading intelligence signals…</p>
      </Card>
    );
  }

  if (!hasAny) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">No intelligence signals for {ticker}.</p>
      </Card>
    );
  }

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Intelligence Signals — {ticker}</h3>
        {fetching && (
          <span className="text-[10px] text-muted-foreground animate-pulse">Refreshing…</span>
        )}
        {!fetching && liveData && (
          <span className="text-[10px] text-muted-foreground">Live · FMP + yfinance</span>
        )}
      </div>
      <div className="mobile-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {insider   && <InsiderCard         d={insider}   />}
        {revision  && <RevisionCard        d={revision}  />}
        {sentiment && <SentimentCard       d={sentiment} />}
        {earnings  && <EarningsQualityCard d={earnings}  />}
        {shortInt  && <ShortInterestCard   d={shortInt}  />}
      </div>
    </Card>
  );
}
