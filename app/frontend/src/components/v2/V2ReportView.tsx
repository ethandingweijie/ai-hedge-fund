/**
 * V2ReportView.tsx — Reimagined Report view (live + complete states)
 *
 * Tabs: Summary · Valuation · Investors · Risk · Research · Financials
 *
 * Wraps existing report panel components (ValuationLadder, PowerLawRadar,
 * ValueTrapChecklist, AgentSignalsPanel, DebatePanel, FinancialsChart,
 * ResearchSummaryPanel, IndustryBriefPanel, DeepResearchPanel) in the new
 * zinc-neutral tab shell. No translator layer needed — existing panels
 * already consume the RunResult shape.
 */

import { useEffect, useMemo, useState } from 'react';
import type {
  RunResult,
  VgpmResult,
  AgentSignals,
  DebateResult,
  ScenarioAnalysis,
  PowerLawAnalysis,
  ValueTrapAnalysis,
  DcfRange,
  CitationRegistryEntry,
  ProgressEvent,
  MacroRegime,
} from '@/lib/reportTypes';
import { getStockData, getCompanyName } from '@/lib/api';

// Existing panel components (reused as-is)
import { FinancialsChart } from '@/components/report/FinancialsChart';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { DeepResearchPanel } from '@/components/report/DeepResearchPanel';
import { LiveSearchPanel } from '@/components/report/LiveSearchPanel';
// MobileChartStrip / MobileKeyStats replaced with v2-native components below

import { ActionPill, GradeChip, Delta, BRAND } from '@/components/v2/shared';

type TabId = 'summary' | 'valuation' | 'investors' | 'risk' | 'research' | 'financials';

interface V2ReportViewProps {
  result: RunResult | null;
  runId: string;
  /** True while pipeline is actively streaming. */
  isRunning: boolean;
  /** True when pipeline has finished successfully. */
  isComplete: boolean;
  phaseMap: Record<string, ProgressEvent>;
  /** 0-100 progress percent (caller-computed, front-loaded). */
  progressPct: number;
  /** Text to show under the progress header ("Macro regime classifier...", etc.). */
  currentPhaseLabel?: string;
  /** Optional list of live events from SSE for the Research tab "Thinking" view. */
  events: ProgressEvent[];
  /** Optional partial liveData accumulated from SSE partial_data payloads. */
  liveData?: Record<string, unknown>;
  /** Called when user clicks Cancel on the progress header. */
  onCancel?: () => void;
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'summary',    label: 'Summary'    },
  { id: 'valuation',  label: 'Valuation'  },
  { id: 'investors',  label: 'Investors'  },
  { id: 'risk',       label: 'Risk'       },
  { id: 'research',   label: 'Research'   },
  { id: 'financials', label: 'Financials' },
];

export function V2ReportView({
  result,
  runId,
  isRunning,
  isComplete,
  phaseMap,
  progressPct,
  currentPhaseLabel,
  events,
  liveData = {},
  onCancel,
}: V2ReportViewProps) {
  const [tab, setTab] = useState<TabId>('summary');
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [priceChangePct, setPriceChangePct] = useState<number | null>(null);
  const [stockMetrics, setStockMetrics] = useState<Record<string, number | undefined> | null>(null);
  const [companyName, setCompanyName] = useState<string>('');
  const [companyExchange, setCompanyExchange] = useState<string>('');

  const ticker = result?.ticker ?? '';
  const data = result?.data ?? {};
  const decisions = result?.decisions ?? {};
  const decision = decisions[ticker] || null;

  // ── Data extractors ────────────────────────────────────────────────────
  const vgpmMap = (result?.vgpm ?? (data.vgpm as Record<string, VgpmResult> | undefined));
  const vgpm = vgpmMap?.[ticker];
  const regime = data.macro_regime as MacroRegime | undefined;
  const routing = (data.routing_decision as Record<string, { sector?: string }> | undefined)?.[ticker];
  const sector = routing?.sector ?? (data.sector as string | undefined);
  const agentSignals = data.analyst_signals as AgentSignals | undefined;
  const debateResult = data.debate_result as DebateResult | undefined;
  const scenarioAnalysis = (data.scenario_analysis as Record<string, ScenarioAnalysis> | undefined)?.[ticker];
  const powerLaw = (data.power_law_analysis as Record<string, PowerLawAnalysis> | undefined)?.[ticker];
  const valueTrap = (data.value_trap_analysis as Record<string, ValueTrapAnalysis> | undefined)?.[ticker];
  const dcfRange = (data.dcf_range as Record<string, DcfRange> | undefined)?.[ticker];
  const industryBrief = data.industry_brief as string | undefined;
  const deepResearch = (data.deep_research ?? data.deep_research_report) as string | undefined;
  const deepAnnotated = data.deep_research_annotated as string | undefined;
  const citations = data.citation_registry as CitationRegistryEntry[] | undefined;

  // Company name fetch (sets header "NVDA · NVIDIA Corporation" style)
  useEffect(() => {
    if (!ticker) return;
    getCompanyName(ticker)
      .then((d) => {
        setCompanyName(d?.name || '');
        // Derive exchange chip from industry/sector if present — best effort
      })
      .catch(() => { /* ignore */ });
  }, [ticker]);

  // Live price + financial metrics fetch — runs as soon as ticker is known,
  // even before the pipeline has produced decisions/VGPM. Lets us show the
  // chart + key stats on Summary during the ongoing-research phase.
  useEffect(() => {
    if (!ticker) return;
    getStockData(ticker, '1y')
      .then((d) => {
        const history = d?.history ?? [];
        if (history.length > 0) {
          const latest = history[history.length - 1].close;
          const first = history[0].close;
          setLivePrice(latest);
          if (first > 0) setPriceChangePct(((latest - first) / first) * 100);
        }
        setStockMetrics((d?.metrics as Record<string, number | undefined>) ?? null);
      })
      .catch(() => { /* ignore */ });
  }, [ticker]);

  const isResearchPhase = useMemo(
    () => Object.values(phaseMap).some(p =>
      (p.phase === 'deep_research_agent' || p.phase === 'deep_research') && !p.status?.toLowerCase().match(/done|complete/)
    ),
    [phaseMap]
  );

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="min-h-full flex flex-col bg-white dark:bg-zinc-900">
      {/* Ticker header — offset from top so it clears the iOS status bar.
          The hamburger is a fixed top-left button (in MobileTopBar); we leave
          a 48px top gutter so the ticker row sits just below that button. */}
      <div
        className="sticky z-20 bg-white/95 dark:bg-zinc-900/95 backdrop-blur border-b border-zinc-100 dark:border-zinc-800 px-5 pb-3"
        style={{ top: 0, paddingTop: 'calc(env(safe-area-inset-top, 0px) + 56px)' }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span
                className="text-[22px] font-bold tracking-tight text-zinc-900 dark:text-zinc-50 tabular-nums leading-none"
                style={{ fontFamily: "'Inter', system-ui, sans-serif", letterSpacing: '-0.02em' }}
              >
                {ticker || '—'}
              </span>
              {companyName && (
                <span className="text-[13px] text-zinc-500 dark:text-zinc-400 truncate leading-none">
                  {companyName}
                </span>
              )}
            </div>
            <div className="mt-2 flex items-center gap-1.5 flex-wrap">
              {sector && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400">
                  {sector}
                </span>
              )}
              {regime?.risk_appetite && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400">
                  {regime.risk_appetite}{regime.volatility_regime ? ` · ${regime.volatility_regime} vol` : ''}
                </span>
              )}
            </div>
          </div>
          {livePrice != null && (
            <div className="text-right shrink-0">
              <div
                className="text-[22px] font-bold tracking-tight text-zinc-900 dark:text-zinc-50 tabular-nums leading-none"
                style={{ fontFamily: "'Inter', system-ui, sans-serif", letterSpacing: '-0.02em' }}
              >
                ${livePrice.toFixed(2)}
              </div>
              {priceChangePct != null && (
                <div className="mt-1.5 text-[12px]">
                  <Delta v={priceChangePct} />
                  <span className="text-zinc-400 dark:text-zinc-500 font-normal ml-1">1Y</span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Tab strip */}
      <div className="sticky z-10 bg-white dark:bg-zinc-900 border-b border-zinc-100 dark:border-zinc-800"
           style={{ top: 'calc(env(safe-area-inset-top, 0px) + 120px)' }}>
        <div className="px-3 flex items-center gap-1 overflow-x-auto phone-scroll">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`h-10 px-2.5 text-[12px] font-medium border-b-[2px] -mb-px transition-colors shrink-0 flex items-center gap-1
                ${tab === t.id
                  ? 'text-zinc-900 dark:text-zinc-50 border-[#2e7d32]'
                  : 'text-zinc-500 dark:text-zinc-400 border-transparent active:text-zinc-800'}`}
            >
              {t.label}
              {t.id === 'summary' && isRunning && (
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-[#2e7d32] dark:bg-[#4ea354] animate-pulse" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Progress header (always visible when running) */}
      {isRunning && (
        <div className="px-4 pt-3">
          <ProgressHeader
            progressPct={progressPct}
            currentPhaseLabel={currentPhaseLabel}
            onCancel={onCancel}
          />
        </div>
      )}

      {/* Live Qwen thinking stream — visible on ALL tabs while streaming.
          User asked for this to sit directly below the progress bar so the
          reasoning output is always visible regardless of which tab is active. */}
      {isRunning && !isComplete && (isResearchPhase || !!(liveData.deep_research_thinking as string)) && (
        <div className="px-4 pt-3">
          <LiveSearchPanel
            streamEvents={events}
            liveData={liveData}
            thinking={(liveData.deep_research_thinking as string) || ''}
            isResearchPhase={isResearchPhase}
            isComplete={isComplete}
          />
        </div>
      )}

      {/* Tab bodies */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'summary'    && <SummaryBody    ticker={ticker} stockMetrics={stockMetrics} decision={decision} vgpm={vgpm} isRunning={isRunning} />}
        {tab === 'valuation'  && <ValuationBody  dcfRange={dcfRange} scenarioAnalysis={scenarioAnalysis} decision={decision} ticker={ticker} currentPrice={livePrice} isRunning={isRunning} />}
        {tab === 'investors'  && <InvestorsBody  agentSignals={agentSignals} debateResult={debateResult} ticker={ticker} isRunning={isRunning} />}
        {tab === 'risk'       && <RiskBody       powerLaw={powerLaw} valueTrap={valueTrap} scenarioAnalysis={scenarioAnalysis} isRunning={isRunning} />}
        {tab === 'research'   && <ResearchBody   runId={runId} ticker={ticker} industryBrief={industryBrief} deepResearch={deepResearch} deepAnnotated={deepAnnotated} citations={citations} events={events} liveData={liveData} isResearchPhase={isResearchPhase} isComplete={isComplete} />}
        {tab === 'financials' && <FinancialsBody ticker={ticker} stockMetrics={stockMetrics} />}
      </div>
    </div>
  );
}

/* ───────── Progress Header ───────── */
function ProgressHeader({
  progressPct,
  currentPhaseLabel,
  onCancel,
}: {
  progressPct: number;
  currentPhaseLabel?: string;
  onCancel?: () => void;
}) {
  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm overflow-hidden">
      <div className="px-4 pt-3 pb-3 flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold text-zinc-900 dark:text-zinc-50 truncate tracking-tight">
            {currentPhaseLabel ?? 'Running analysis…'}
          </div>
          <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
            Hold tight — research streams in over 4–6 minutes.
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <span className="text-[15px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-50 tracking-tight">
            {Math.round(progressPct)}%
          </span>
          {onCancel && (
            <button
              onClick={onCancel}
              className="text-[11.5px] font-medium text-zinc-500 dark:text-zinc-400 hover:text-rose-600 dark:hover:text-rose-400 transition-colors"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
      <div className="h-1 bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
        <div
          className="h-full transition-[width] duration-200 ease-out"
          style={{
            width: `${Math.max(0, Math.min(100, progressPct))}%`,
            background: `linear-gradient(90deg, ${BRAND} 0%, ${BRAND} 80%, #4ea354 100%)`,
            boxShadow: `0 0 8px ${BRAND}80`,
          }}
        />
      </div>
    </div>
  );
}

/* ───────── Summary Tab ───────── */
function LoadingSpinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="inline-block rounded-full border-2 border-[#2e7d32] dark:border-[#4ea354] border-t-transparent animate-spin"
      style={{ width: size, height: size }}
    />
  );
}

function LoadingCard({ label, minH = 80 }: { label: string; minH?: number }) {
  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-3">
        {label}
      </div>
      <div className="flex items-center justify-center gap-2.5 text-zinc-400 dark:text-zinc-500" style={{ minHeight: minH }}>
        <LoadingSpinner size={14} />
        <span className="text-[12px]">Computing…</span>
      </div>
    </div>
  );
}

function LoadingGradeChip({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-1 min-w-[28px]">
      <span className="text-[9px] font-medium uppercase tracking-[0.08em] text-zinc-400 dark:text-zinc-500">{label}</span>
      <span className="inline-flex items-center justify-center min-w-[22px] h-[20px] px-1.5 rounded-md bg-zinc-50 dark:bg-zinc-800/60">
        <LoadingSpinner size={10} />
      </span>
    </div>
  );
}

function SummaryBody({
  ticker, stockMetrics, decision, vgpm, isRunning,
}: {
  ticker: string;
  stockMetrics: Record<string, number | undefined> | null;
  decision: any;
  vgpm: VgpmResult | undefined;
  isRunning: boolean;
}) {
  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* Stock chart card */}
      {ticker && <V2StockChart ticker={ticker} />}

      {/* Key financial metrics card */}
      {ticker && (
        stockMetrics ? <V2KeyStats metrics={stockMetrics} /> : <LoadingCard label="Key Stats" minH={140} />
      )}

      {/* Portfolio Manager hero card — loading skeleton while pipeline runs */}
      {decision ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-2">
            Portfolio Manager
          </div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <ActionPill action={decision.action} size="lg" />
            {typeof decision.position_size_pct === 'number' && (
              <span className="text-[15px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                {(decision.position_size_pct * 100).toFixed(1)}%
              </span>
            )}
            {typeof decision.confidence === 'number' && (
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Confidence {Math.round(decision.confidence * 100)}%
              </span>
            )}
          </div>
          {typeof decision.price_target === 'number' && (
            <div className="mt-3 flex items-baseline gap-2">
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">Target</span>
              <span className="text-[15px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                ${decision.price_target.toFixed(2)}
              </span>
            </div>
          )}
          {decision.rationale && (
            <p className="mt-3 text-[12.5px] text-zinc-700 dark:text-zinc-300 leading-relaxed">
              {decision.rationale}
            </p>
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-2">
            Portfolio Manager
          </div>
          <div className="flex items-center gap-2.5 py-2 text-zinc-400 dark:text-zinc-500">
            <LoadingSpinner size={16} />
            <span className="text-[12px]">
              {isRunning ? 'Investor agents running...' : 'Waiting for decision'}
            </span>
          </div>
        </div>
      )}

      {/* VGPM scorecard — always rendered, with spinners per grade until ready */}
      <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-3">
          VGPM Scorecard
          {!vgpm && isRunning && <span className="ml-2 text-zinc-400 dark:text-zinc-500 normal-case font-normal tracking-normal">· computing…</span>}
        </div>
        <div className="grid grid-cols-4 gap-3">
          {vgpm?.valuation?.grade
            ? <GradeChip grade={vgpm.valuation.grade} label="Valuation" />
            : <LoadingGradeChip label="Valuation" />}
          {vgpm?.growth?.grade
            ? <GradeChip grade={vgpm.growth.grade} label="Growth" />
            : <LoadingGradeChip label="Growth" />}
          {vgpm?.profitability?.grade
            ? <GradeChip grade={vgpm.profitability.grade} label="Profit." />
            : <LoadingGradeChip label="Profit." />}
          {vgpm?.momentum?.grade
            ? <GradeChip grade={vgpm.momentum.grade} label="Momentum" />
            : <LoadingGradeChip label="Momentum" />}
        </div>
      </div>
    </div>
  );
}

/* ───────── Valuation Tab ───────── */
function ValuationBody({
  dcfRange, scenarioAnalysis, decision, ticker, currentPrice, isRunning,
}: {
  dcfRange: DcfRange | undefined;
  scenarioAnalysis: ScenarioAnalysis | undefined;
  decision: any;
  ticker: string;
  currentPrice: number | null;
  isRunning: boolean;
}) {
  // Extract all the numbers we need from real data
  const target = (scenarioAnalysis?.['12m_price_target'] ?? decision?.price_target ?? null);
  const current = currentPrice ?? scenarioAnalysis?.current_price ?? null;
  const upside = (target != null && current != null && current > 0)
    ? ((target - current) / current) * 100
    : (scenarioAnalysis?.upside_pct ?? null);
  const longTerm = dcfRange?.base?.intrinsic_value ?? scenarioAnalysis?.base?.fair_value ?? null;
  const longTermDelta = (longTerm != null && current != null && current > 0)
    ? ((longTerm - current) / current) * 100 : null;
  const bullIV = dcfRange?.bull?.intrinsic_value ?? scenarioAnalysis?.bull?.fair_value ?? null;
  const baseIV = dcfRange?.base?.intrinsic_value ?? scenarioAnalysis?.base?.fair_value ?? null;
  const bearIV = dcfRange?.bear?.intrinsic_value ?? scenarioAnalysis?.bear?.fair_value ?? null;
  const bullDelta = (bullIV != null && current != null && current > 0) ? ((bullIV - current) / current) * 100 : null;
  const bearDelta = (bearIV != null && current != null && current > 0) ? ((bearIV - current) / current) * 100 : null;
  const baseDelta = (baseIV != null && current != null && current > 0) ? ((baseIV - current) / current) * 100 : null;
  const wacc = dcfRange?.wacc ?? null;

  const has12mTargets = dcfRange?.['12m_targets'] ?? {};
  const bull12m = has12mTargets.bull ?? bullIV;
  const base12m = has12mTargets.base ?? target ?? baseIV;
  const bear12m = has12mTargets.bear ?? bearIV;
  const probBull = scenarioAnalysis?.bull?.probability ?? 0.25;
  const probBase = scenarioAnalysis?.base?.probability ?? 0.50;
  const probBear = scenarioAnalysis?.bear?.probability ?? 0.25;
  const evValue = scenarioAnalysis?.expected_value ?? target ?? null;

  const haveAny = dcfRange || scenarioAnalysis || decision;
  if (!haveAny) {
    return (
      <div className="px-4 pt-4 pb-8 space-y-4">
        <LoadingCard label="12-Month Price Target" minH={180} />
        <LoadingCard label="Scenario Probabilities" minH={140} />
        <LoadingCard label="Scenario Analysis" minH={220} />
        <LoadingCard label="DCF Valuation Ladder" minH={160} />
      </div>
    );
  }

  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* ── 12-Month Price Target hero ──────────────────────────────── */}
      {target != null ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-5">
          <div className="text-center">
            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
              12-Month Price Target
            </div>
            <div className="text-[34px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50 tabular-nums mt-1 leading-none">
              ${target.toFixed(2)}
            </div>
            {upside != null && (
              <div className={`mt-2 text-[14px] font-medium tabular-nums ${upside >= 0 ? 'text-[#2e7d32] dark:text-[#4ea354]' : 'text-rose-600 dark:text-rose-400'}`}>
                {upside >= 0 ? '+' : ''}{upside.toFixed(1)}% upside
              </div>
            )}
            {current != null && (
              <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
                vs current ${current.toFixed(2)}
              </div>
            )}
          </div>

          {/* 2×2 metric grid */}
          <div className="grid grid-cols-2 gap-2 mt-5">
            <MetricBox label="Current price"   value={current != null ? `$${current.toFixed(2)}` : '—'} tone="neutral" />
            <MetricBox label="Long-term value" value={longTerm != null ? `$${longTerm.toFixed(2)}` : '—'} delta={longTermDelta ?? undefined} tone="neutral" />
            <MetricBox label="Bull case"       value={bullIV   != null ? `$${bullIV.toFixed(2)}`   : '—'} delta={bullDelta ?? undefined} tone="bull" />
            <MetricBox label="Bear case"       value={bearIV   != null ? `$${bearIV.toFixed(2)}`   : '—'} delta={bearDelta ?? undefined} tone="bear" />
          </div>
        </div>
      ) : (
        <LoadingCard label="12-Month Price Target" minH={200} />
      )}

      {/* ── Scenario probabilities ──────────────────────────────────── */}
      {(scenarioAnalysis || dcfRange) ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
              Scenario Probabilities
            </span>
            {wacc != null && <span className="text-[10px] tabular-nums text-zinc-400 dark:text-zinc-500">WACC {(wacc * 100).toFixed(1)}%</span>}
          </div>
          <div className="flex items-center justify-end gap-2 px-1 pb-1.5 text-[9px] uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
            <span className="w-[60px] text-right">12M Target</span>
            <span className="w-[56px] text-right">DCF IV</span>
          </div>
          {[
            { prob: probBear, name: 'Bear', target12m: bear12m, iv: bearIV, color: 'rose' },
            { prob: probBase, name: 'Base', target12m: base12m, iv: baseIV, color: 'blue' },
            { prob: probBull, name: 'Bull', target12m: bull12m, iv: bullIV, color: 'brand' },
          ].map((r, i) => (
            <div key={r.name} className={`flex items-center gap-2 py-2 ${i > 0 ? 'border-t border-zinc-100 dark:border-zinc-800' : ''}`}>
              <span className="w-[34px] text-[11.5px] font-semibold text-zinc-700 dark:text-zinc-300 tabular-nums">
                {Math.round((r.prob ?? 0) * 100)}%
              </span>
              <div className="w-[60px] h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
                <div
                  style={{ width: `${Math.min(100, (r.prob ?? 0) * 200)}%` }}
                  className={`h-full ${r.color === 'rose' ? 'bg-rose-500 dark:bg-rose-400' : r.color === 'blue' ? 'bg-blue-500 dark:bg-blue-400' : 'bg-[#2e7d32] dark:bg-[#4ea354]'}`}
                />
              </div>
              <span className="text-[12.5px] font-semibold text-zinc-900 dark:text-zinc-50 min-w-[40px]">{r.name}</span>
              <span className="ml-auto w-[60px] text-right text-[12px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                {r.target12m != null ? `$${r.target12m.toFixed(2)}` : '—'}
              </span>
              <span className="w-[56px] text-right text-[11px] tabular-nums text-zinc-500 dark:text-zinc-400">
                {r.iv != null ? `$${r.iv.toFixed(2)}` : '—'}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <LoadingCard label="Scenario Probabilities" minH={140} />
      )}

      {/* ── Scenario analysis (v2 native bar chart) ─────────────────── */}
      {(bullIV != null || baseIV != null || bearIV != null) ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="flex items-start justify-between mb-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
              Scenario Analysis
            </span>
            {upside != null && (
              <span className={`inline-flex items-center gap-1 text-[10.5px] font-medium ${upside >= 0 ? 'text-[#2e7d32] dark:text-[#4ea354]' : 'text-rose-600 dark:text-rose-400'}`}>
                EV upside {upside >= 0 ? '+' : ''}{upside.toFixed(1)}%
              </span>
            )}
          </div>
          {baseDelta != null && bearDelta != null && (
            <p className="text-[11.5px] text-zinc-500 dark:text-zinc-400 leading-relaxed mb-3">
              Base case implies {baseDelta >= 0 ? '+' : ''}{baseDelta.toFixed(0)}% upside; bear-case downside is {Math.abs(bearDelta).toFixed(0)}%.
            </p>
          )}
          <V2ScenarioBars bear={bearIV} base={baseIV} bull={bullIV} ev={evValue} current={current ?? undefined} />
        </div>
      ) : (
        <LoadingCard label="Scenario Analysis" minH={220} />
      )}

      {/* ── DCF Valuation Ladder (v2 native) ────────────────────────── */}
      {dcfRange ? (
        <V2ValuationLadder dcfRange={dcfRange} current={current ?? undefined} wacc={wacc} />
      ) : (
        <LoadingCard label="DCF Valuation Ladder" minH={160} />
      )}

      {isRunning && !haveAny && (
        <p className="text-center text-[11px] text-zinc-400 dark:text-zinc-500 pt-2">
          Valuation renders once the pipeline reaches Phase 4.5 (DCF Engine).
        </p>
      )}
    </div>
  );
}

function MetricBox({
  label, value, delta, tone = 'neutral',
}: {
  label: string;
  value: string;
  delta?: number;
  tone?: 'neutral' | 'bull' | 'bear';
}) {
  const bg =
    tone === 'bull' ? 'bg-[#ecf5ed]/70 dark:bg-[#2e7d32]/10 border-[#d0e7d2]/70 dark:border-[#2e7d32]/20' :
    tone === 'bear' ? 'bg-rose-50/70 dark:bg-rose-500/10 border-rose-100 dark:border-rose-500/20' :
    'bg-zinc-50/80 dark:bg-zinc-800/40 border-zinc-100 dark:border-zinc-800';
  const labelCls =
    tone === 'bull' ? 'text-[#2e7d32]/80 dark:text-[#4ea354]/80' :
    tone === 'bear' ? 'text-rose-700/80 dark:text-rose-400/80' :
    'text-zinc-500 dark:text-zinc-400';
  const valCls =
    tone === 'bull' ? 'text-[#2e7d32] dark:text-[#4ea354]' :
    tone === 'bear' ? 'text-rose-700 dark:text-rose-400' :
    'text-zinc-900 dark:text-zinc-50';
  return (
    <div className={`p-3 rounded-xl border ${bg}`}>
      <div className={`text-[10px] uppercase tracking-[0.08em] font-semibold ${labelCls}`}>{label}</div>
      <div className={`text-[18px] font-semibold tabular-nums mt-1 tracking-tight ${valCls}`}>{value}</div>
      {delta != null && (
        <div className={`text-[10.5px] font-medium tabular-nums mt-0.5 ${delta >= 0 ? 'text-[#2e7d32] dark:text-[#4ea354]' : 'text-rose-600 dark:text-rose-400'}`}>
          {delta >= 0 ? '+' : ''}{delta.toFixed(1)}%
        </div>
      )}
    </div>
  );
}

/* ───────── V2 Scenario Bars (Bear/Base/Bull/EV) ───────── */
function V2ScenarioBars({
  bear, base, bull, ev, current,
}: {
  bear?: number | null;
  base?: number | null;
  bull?: number | null;
  ev?: number | null;
  current?: number;
}) {
  const bars = [
    { label: 'Bear', value: bear,  fill: '#f43f5e' },
    { label: 'Base', value: base,  fill: '#3b82f6' },
    { label: 'Bull', value: bull,  fill: '#2e7d32' },
    { label: 'EV',   value: ev,    fill: '#a855f7' },
  ].filter(b => typeof b.value === 'number' && b.value > 0) as { label: string; value: number; fill: string }[];

  if (bars.length === 0) return null;

  const values = bars.map(b => b.value).concat(current ? [current] : []);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  // Pad 10% above/below
  const yMin = Math.max(0, rawMin * 0.85);
  const yMax = rawMax * 1.1;

  const w = 340, h = 200;
  const padT = 14, padB = 28, padL = 42, padR = 12;
  const chartW = w - padL - padR;
  const chartH = h - padT - padB;
  const yFor = (v: number) => padT + chartH * (1 - (v - yMin) / Math.max(0.001, yMax - yMin));
  const barW = Math.min(38, (chartW / bars.length) * 0.6);
  const step = chartW / bars.length;

  // 5 Y ticks
  const ticks: number[] = [];
  for (let i = 0; i <= 4; i++) ticks.push(yMin + (yMax - yMin) * (i / 4));

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="xMidYMid meet" style={{ height: 200 }}>
      {/* Grid */}
      <g className="text-zinc-200 dark:text-zinc-800">
        {ticks.map(t => (
          <line key={t} x1={padL} y1={yFor(t)} x2={w - padR} y2={yFor(t)}
                stroke="currentColor" strokeWidth={0.5} strokeDasharray="2,3" />
        ))}
      </g>
      <g className="fill-zinc-400 dark:fill-zinc-500">
        {ticks.map(t => (
          <text key={t} x={padL - 4} y={yFor(t) + 3} textAnchor="end" fontSize={9}>${Math.round(t)}</text>
        ))}
      </g>

      {/* Current line */}
      {current && current >= yMin && current <= yMax && (
        <g>
          <line x1={padL} y1={yFor(current)} x2={w - padR} y2={yFor(current)}
                className="text-zinc-400 dark:text-zinc-500" stroke="currentColor" strokeWidth={1} strokeDasharray="4,4"/>
          <text x={w - padR - 2} y={yFor(current) - 3} textAnchor="end" fontSize={9}
                className="fill-zinc-500 dark:fill-zinc-400">Current ${current.toFixed(2)}</text>
        </g>
      )}

      {/* Bars */}
      {bars.map((b, i) => {
        const cx = padL + step * (i + 0.5);
        const x = cx - barW / 2;
        const y = yFor(b.value);
        const bh = yFor(yMin) - y;
        return (
          <g key={b.label}>
            <rect x={x} y={y} width={barW} height={Math.max(2, bh)} rx={2.5} fill={b.fill} />
            <text x={cx} y={y - 4} textAnchor="middle" fontSize={9}
                  className="fill-zinc-700 dark:fill-zinc-200" fontWeight={600}>
              ${Math.round(b.value)}
            </text>
            <text x={cx} y={h - padB + 14} textAnchor="middle" fontSize={10}
                  className="fill-zinc-500 dark:fill-zinc-400">{b.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ───────── V2 DCF Valuation Ladder ───────── */
function V2ValuationLadder({
  dcfRange, current, wacc,
}: {
  dcfRange: DcfRange;
  current?: number;
  wacc?: number | null;
}) {
  const bullIV = dcfRange.bull?.intrinsic_value;
  const baseIV = dcfRange.base?.intrinsic_value;
  const bearIV = dcfRange.bear?.intrinsic_value;
  const bullG  = dcfRange.bull?.growth_rate;
  const baseG  = dcfRange.base?.growth_rate;
  const bearG  = dcfRange.bear?.growth_rate;

  const maxIV = Math.max(current ?? 0, bullIV ?? 0, baseIV ?? 0, bearIV ?? 0, 1);
  const pct = (iv?: number) => {
    if (iv == null || current == null || current <= 0) return null;
    return ((iv - current) / current) * 100;
  };

  const rows = [
    { name: 'Current',   value: current,  color: 'bg-zinc-400 dark:bg-zinc-500', delta: null as number | null, growth: null as number | null | undefined },
    { name: 'Bull case', value: bullIV,   color: 'bg-[#2e7d32] dark:bg-[#4ea354]', delta: pct(bullIV), growth: bullG },
    { name: 'Base case', value: baseIV,   color: 'bg-blue-500 dark:bg-blue-400',   delta: pct(baseIV), growth: baseG },
    { name: 'Bear case', value: bearIV,   color: 'bg-rose-500 dark:bg-rose-400',   delta: pct(bearIV), growth: bearG },
  ];

  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
          DCF Valuation Ladder
        </span>
        {wacc != null && (
          <span className="text-[10px] tabular-nums text-zinc-400 dark:text-zinc-500">
            WACC: {(wacc * 100).toFixed(1)}%
          </span>
        )}
      </div>
      <div className="space-y-2.5">
        {rows.map(r => r.value != null && (
          <div key={r.name} className="flex items-center gap-3">
            <span className="text-[11.5px] text-zinc-500 dark:text-zinc-400 w-[62px] shrink-0">{r.name}</span>
            <div className="flex-1 h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
              <div
                className={`h-full rounded-full ${r.color}`}
                style={{ width: `${Math.max(4, Math.min(100, (r.value / maxIV) * 100))}%` }}
              />
            </div>
            <div className="flex items-baseline gap-1.5 min-w-[100px] justify-end">
              <span className="text-[12.5px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums">
                ${r.value.toFixed(2)}
              </span>
              {r.delta != null && (
                <span className={`text-[11px] font-medium tabular-nums ${r.delta >= 0 ? 'text-[#2e7d32] dark:text-[#4ea354]' : 'text-rose-600 dark:text-rose-400'}`}>
                  {r.delta >= 0 ? '+' : ''}{r.delta.toFixed(1)}%
                </span>
              )}
            </div>
            {r.growth != null && (
              <span className="text-[10px] text-zinc-400 dark:text-zinc-500 tabular-nums w-[38px] text-right">
                @ {(r.growth * 100).toFixed(0)}% g
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ───────── Investors Tab — v2 native (Panel Verdicts + Agent list + Debate) ─── */
const AGENT_LABELS_V2: Record<string, string> = {
  buffett:      'Buffett',
  graham:       'Graham',
  munger:       'Munger',
  burry:        'Burry',
  cathie_wood:  'Wood',
  wood:         'Wood',
  ackman:       'Ackman',
  pabrai:       'Pabrai',
  lynch:        'Lynch',
  fisher:       'Fisher',
  jhunjhunwala: 'Jhunjhunwala',
  druckenmiller:'Druckenmiller',
  damodaran:    'Damodaran',
};

function InvestorsBody({
  agentSignals, debateResult, ticker, isRunning,
}: {
  agentSignals: AgentSignals | undefined;
  debateResult: DebateResult | undefined;
  ticker: string;
  isRunning: boolean;
}) {
  // Extract per-ticker agent signals into a flat list
  const agentList: { name: string; verdict: string; conviction: number; thesis: string; priceTarget?: number }[] =
    agentSignals
      ? Object.entries(agentSignals)
          .map(([key, tmap]) => {
            const sig = (tmap as any)?.[ticker];
            if (!sig) return null;
            return {
              name: AGENT_LABELS_V2[key] || key.charAt(0).toUpperCase() + key.slice(1),
              verdict: (sig.signal || 'HOLD').toUpperCase(),
              conviction: Number(sig.conviction ?? 0),
              thesis: sig.thesis_summary || '',
              priceTarget: typeof sig.price_target === 'number' ? sig.price_target : undefined,
            };
          })
          .filter(Boolean) as any
      : [];

  // Count agents by verdict
  const counts: Record<string, number> = { BUY: 0, HOLD: 0, SELL: 0, SHORT: 0 };
  agentList.forEach(a => { counts[a.verdict] = (counts[a.verdict] || 0) + 1; });

  // Debate (structured)
  const tDebate = debateResult?.[ticker];
  const debateTriggered = tDebate?.triggered === true;

  if (agentList.length === 0) {
    return (
      <div className="px-4 pt-4 pb-8 space-y-4">
        <LoadingCard
          label={isRunning ? 'Panel Verdicts — 12 investor agents running' : 'Panel Verdicts'}
          minH={90}
        />
        <LoadingCard
          label={isRunning ? 'Investor Thesis List' : 'Investor Signals'}
          minH={240}
        />
        <LoadingCard
          label="Points of Disagreement (debate if triggered)"
          minH={80}
        />
      </div>
    );
  }

  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* Panel Verdicts card */}
      <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-3">
          Panel Verdicts
        </div>
        <div className="grid grid-cols-4 gap-2">
          {(['BUY', 'HOLD', 'SELL', 'SHORT'] as const).map(v => (
            <div key={v} className="flex flex-col items-center gap-1">
              <span className="text-[18px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums">
                {counts[v] || 0}
              </span>
              <ActionPill action={v} />
            </div>
          ))}
        </div>
      </div>

      {/* Agent thesis list */}
      <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm overflow-hidden">
        {agentList.map((p, i) => (
          <div
            key={p.name + i}
            className={`px-4 py-3 flex items-start gap-3 ${i > 0 ? 'border-t border-zinc-100 dark:border-zinc-800' : ''}`}
          >
            <div className="w-8 h-8 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 flex items-center justify-center text-[11px] font-semibold shrink-0">
              {p.name[0]}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-50">{p.name}</span>
                <ActionPill action={p.verdict} />
                {p.conviction > 0 && (
                  <span className="text-[10px] text-zinc-400 dark:text-zinc-500 tabular-nums">
                    {p.conviction}/10
                  </span>
                )}
                {p.priceTarget != null && (
                  <span className="text-[10px] text-zinc-400 dark:text-zinc-500 tabular-nums">
                    · ${p.priceTarget.toFixed(2)}
                  </span>
                )}
              </div>
              {p.thesis && (
                <p className="text-[12px] text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed">
                  {p.thesis}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Points of Disagreement (Debate) */}
      <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
            Points of Disagreement
          </span>
          <span className="text-[10px] text-zinc-400 dark:text-zinc-500">debate</span>
        </div>
        {debateTriggered && tDebate ? (
          <div className="space-y-2">
            {tDebate.disagreement_core && (
              <DebateRow side="bull" who="Core disagreement" point={tDebate.disagreement_core} />
            )}
            {tDebate.agent_a_rebuttal && (
              <DebateRow side="bull" who="Bull rebuttal" point={tDebate.agent_a_rebuttal} />
            )}
            {tDebate.agent_b_rebuttal && (
              <DebateRow side="bear" who="Bear rebuttal" point={tDebate.agent_b_rebuttal} />
            )}
            {tDebate.adjudication && (
              <DebateRow
                side={(tDebate.adjudicated_signal || '').toUpperCase() === 'BUY' ? 'bull' : 'bear'}
                who={`Adjudication → ${tDebate.adjudicated_signal || '?'}`}
                point={tDebate.adjudication}
              />
            )}
          </div>
        ) : (
          <p className="text-[12px] text-zinc-500 dark:text-zinc-400">
            Debate round not triggered for {ticker} (requires ≥3 BUY and ≥3 SELL signals).
          </p>
        )}
      </div>
    </div>
  );
}

function DebateRow({ side, who, point }: { side: 'bull' | 'bear'; who: string; point: string }) {
  const accent = side === 'bull'
    ? 'border-l-[#2e7d32] dark:border-l-[#4ea354]'
    : 'border-l-rose-500 dark:border-l-rose-400';
  const labelCls = side === 'bull'
    ? 'text-[#2e7d32] dark:text-[#4ea354]'
    : 'text-rose-600 dark:text-rose-400';
  return (
    <div className={`pl-3 border-l-2 ${accent}`}>
      <div className={`text-[10px] font-semibold uppercase tracking-[0.08em] ${labelCls}`}>{who}</div>
      <p className="text-[12px] text-zinc-700 dark:text-zinc-300 mt-0.5 leading-relaxed">{point}</p>
    </div>
  );
}

/* ───────── Risk Tab — v2 native (Power Law + Value Trap + Scenario Mix) ─── */
function RiskBody({
  powerLaw, valueTrap, scenarioAnalysis, isRunning,
}: {
  powerLaw: PowerLawAnalysis | undefined;
  valueTrap: ValueTrapAnalysis | undefined;
  scenarioAnalysis?: ScenarioAnalysis | undefined;
  isRunning: boolean;
}) {
  const powerLawOverall = powerLaw?.score ?? powerLaw?.total_score ?? null;

  // Legacy rescale: pre-v1.7.1 runs stored dimensions on the 0-2 scale.
  // Detect by max ≤ 2 and multiply by 5 so the pentagon + dimension bars
  // render on the same 0-10 axis used by new runs.
  const _rawPowerLawDims = powerLaw ? [
    powerLaw.scale_economies ?? 0,
    powerLaw.network_effects ?? 0,
    powerLaw.winner_take_most ?? 0,
    powerLaw.switching_costs ?? 0,
    powerLaw.data_ip_moat ?? 0,
  ] : [];
  const _isLegacyPowerLaw = _rawPowerLawDims.length === 5
    && _rawPowerLawDims.every(v => v >= 0 && v <= 2)
    && Math.max(..._rawPowerLawDims) > 0;
  const _plScale = _isLegacyPowerLaw ? 5 : 1;

  const dims = powerLaw ? [
    { label: 'Scale economies',  score: (powerLaw.scale_economies  ?? 0) * _plScale, note: powerLaw.scale_economies_note,  concern: powerLaw.scale_economies_concern },
    { label: 'Network effects',  score: (powerLaw.network_effects  ?? 0) * _plScale, note: powerLaw.network_effects_note,  concern: powerLaw.network_effects_concern },
    { label: 'Winner-take-most', score: (powerLaw.winner_take_most ?? 0) * _plScale, note: powerLaw.winner_take_most_note, concern: powerLaw.winner_take_most_concern },
    { label: 'Switching costs',  score: (powerLaw.switching_costs  ?? 0) * _plScale, note: powerLaw.switching_costs_note,  concern: powerLaw.switching_costs_concern },
    { label: 'Data / IP moat',   score: (powerLaw.data_ip_moat     ?? 0) * _plScale, note: powerLaw.data_ip_moat_note,     concern: powerLaw.data_ip_moat_concern },
  ] : [];

  // Backend emits `status` on each check; type says `rating`. Read both.
  const checkRating = (c: any): string | undefined => c?.rating || c?.status;
  const checkEv     = (c: any): string | undefined => c?.evidence || c?.detail;
  const trapChecks = valueTrap ? [
    { k: 'Dividend sustainability', rating: checkRating(valueTrap.dividend_sustainability), ev: checkEv(valueTrap.dividend_sustainability) },
    { k: 'Structural decline',      rating: checkRating(valueTrap.structural_decline),      ev: checkEv(valueTrap.structural_decline) },
    { k: 'Earnings / cash mismatch',rating: checkRating(valueTrap.earnings_cash_mismatch),  ev: checkEv(valueTrap.earnings_cash_mismatch) },
    { k: 'Insider behaviour',       rating: checkRating(valueTrap.insider_behaviour),       ev: checkEv(valueTrap.insider_behaviour) },
    { k: 'Balance sheet',           rating: checkRating(valueTrap.balance_sheet),           ev: checkEv(valueTrap.balance_sheet) },
  ] : [];
  const trapVerdict = valueTrap?.verdict || valueTrap?.overall_verdict || '';

  const bullProb = scenarioAnalysis?.bull?.probability;
  const bearProb = scenarioAnalysis?.bear?.probability;
  const showScenario = bullProb != null || bearProb != null;

  if (!powerLaw && !valueTrap) {
    return (
      <div className="px-4 pt-4 pb-8 space-y-4">
        <LoadingCard label="Power Law — 5-dimension moat audit" minH={240} />
        <LoadingCard label="Value Trap Check" minH={200} />
      </div>
    );
  }

  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* Power Law card */}
      {powerLaw ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="flex items-center justify-between mb-4">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
              Power Law
            </span>
            {powerLawOverall != null && (
              <span className="text-[15px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                {powerLawOverall.toFixed(1)} <span className="text-[11px] text-zinc-400 dark:text-zinc-500 font-normal">/ 10</span>
              </span>
            )}
          </div>

          {/* Radar chart (simple SVG pentagon) */}
          <PowerLawPentagon dims={dims} />

          {/* Dimension list */}
          <div className="space-y-3 mt-4">
            {dims.map(d => d.score != null && (
              <div key={d.label}>
                <div className="flex items-baseline justify-between mb-1">
                  <span className="text-[12.5px] font-semibold text-zinc-900 dark:text-zinc-50">{d.label}</span>
                  <span className="text-[12.5px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums">
                    {d.score.toFixed(1)}
                  </span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[#2e7d32] dark:bg-[#4ea354]"
                    style={{ width: `${Math.max(0, Math.min(100, (d.score / 10) * 100))}%` }}
                  />
                </div>
                {d.note && (
                  <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-1 leading-relaxed">
                    {d.note}
                  </p>
                )}
                {d.concern && (
                  <p className="text-[11px] text-rose-600 dark:text-rose-400 mt-0.5 leading-relaxed">
                    Watch: {d.concern}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <LoadingCard label="Power Law Moat Analysis" minH={280} />
      )}

      {/* Value Trap card */}
      {valueTrap ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
              Value Trap Check
            </span>
            {trapVerdict && (
              <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${
                trapVerdict.includes('HIGH')
                  ? 'text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-500/10 border-rose-200 dark:border-rose-500/30'
                  : trapVerdict.includes('MEDIUM')
                  ? 'text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10 border-amber-200 dark:border-amber-500/30'
                  : 'text-[#2e7d32] dark:text-[#4ea354] bg-[#ecf5ed] dark:bg-[#2e7d32]/10 border-[#d0e7d2] dark:border-[#2e7d32]/30'
              }`}>
                {trapVerdict}
              </span>
            )}
          </div>
          <div className="space-y-3">
            {trapChecks.map((c, i) => c.rating && (
              <div key={c.k} className={`flex items-start gap-2.5 ${i > 0 ? 'pt-3 border-t border-zinc-100 dark:border-zinc-800' : ''}`}>
                <span
                  className={`w-2 h-2 rounded-full shrink-0 mt-1.5 ${
                    c.rating === 'GREEN' ? 'bg-[#2e7d32] dark:bg-[#4ea354]'
                    : c.rating === 'AMBER' ? 'bg-amber-500 dark:bg-amber-400'
                    : 'bg-rose-500 dark:bg-rose-400'
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[12.5px] font-semibold text-zinc-900 dark:text-zinc-50">{c.k}</span>
                    <span className={`text-[10px] font-semibold tracking-wide shrink-0 ${
                      c.rating === 'GREEN' ? 'text-[#2e7d32] dark:text-[#4ea354]'
                      : c.rating === 'AMBER' ? 'text-amber-600 dark:text-amber-400'
                      : 'text-rose-600 dark:text-rose-400'
                    }`}>
                      {c.rating}
                    </span>
                  </div>
                  {c.ev && (
                    <p className="text-[11.5px] text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed">
                      {c.ev}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <LoadingCard label="Value Trap Check" minH={200} />
      )}

      {/* Scenario Mix (bull/bear split bar) */}
      {showScenario && (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
              Scenario Mix
            </span>
            <span className="text-[10px] text-zinc-400 dark:text-zinc-500">12-mo</span>
          </div>
          <div className="w-full h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden flex">
            <div
              className="h-full bg-[#2e7d32] dark:bg-[#4ea354]"
              style={{ width: `${Math.round(((bullProb ?? 0) / ((bullProb ?? 0) + (bearProb ?? 0) || 1)) * 100)}%` }}
            />
            <div
              className="h-full bg-rose-500 dark:bg-rose-400"
              style={{ width: `${Math.round(((bearProb ?? 0) / ((bullProb ?? 0) + (bearProb ?? 0) || 1)) * 100)}%` }}
            />
          </div>
          <div className="flex items-center justify-between mt-2 text-[11px] tabular-nums">
            <span className="text-[#2e7d32] dark:text-[#4ea354]">
              {Math.round(((bullProb ?? 0) / ((bullProb ?? 0) + (bearProb ?? 0) || 1)) * 100)}% bull
            </span>
            <span className="text-rose-600 dark:text-rose-400">
              {Math.round(((bearProb ?? 0) / ((bullProb ?? 0) + (bearProb ?? 0) || 1)) * 100)}% bear
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/* Pentagon radar chart — 5 axes */
function PowerLawPentagon({ dims }: { dims: { label: string; score?: number }[] }) {
  const w = 300, h = 180, cx = w / 2, cy = h / 2 + 6;
  const r = 70;
  const angles = dims.map((_, i) => (-Math.PI / 2) + (i * 2 * Math.PI) / 5);
  const pts = dims.map((d, i) => {
    const rr = ((d.score ?? 0) / 10) * r;
    return [cx + rr * Math.cos(angles[i]), cy + rr * Math.sin(angles[i])];
  });
  const outer = angles.map(a => [cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  const gridRings = [0.25, 0.5, 0.75, 1.0];
  const poly = (p: number[][]) => p.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="xMidYMid meet" style={{ height: 180 }}>
      {/* Grid rings */}
      {gridRings.map(ring => (
        <polygon key={ring}
          points={poly(angles.map(a => [cx + r * ring * Math.cos(a), cy + r * ring * Math.sin(a)]))}
          fill="none"
          className="text-zinc-200 dark:text-zinc-700"
          stroke="currentColor"
          strokeWidth={0.5}
        />
      ))}
      {/* Axes */}
      {outer.map(([x, y], i) => (
        <line key={i} x1={cx} y1={cy} x2={x} y2={y}
          className="text-zinc-200 dark:text-zinc-700" stroke="currentColor" strokeWidth={0.5} />
      ))}
      {/* Data polygon */}
      <polygon
        points={poly(pts)}
        fill={BRAND}
        fillOpacity={0.18}
        stroke={BRAND}
        strokeWidth={1.4}
        strokeLinejoin="round"
      />
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={2.5} fill={BRAND} />
      ))}
      {/* Labels */}
      {outer.map(([x, y], i) => {
        const label = dims[i].label.length > 14 ? dims[i].label.slice(0, 14) + '…' : dims[i].label;
        const lx = cx + (r + 20) * Math.cos(angles[i]);
        const ly = cy + (r + 12) * Math.sin(angles[i]);
        return (
          <text key={`l${i}`} x={lx} y={ly}
            textAnchor={Math.abs(Math.cos(angles[i])) < 0.1 ? 'middle' : Math.cos(angles[i]) > 0 ? 'start' : 'end'}
            fontSize={9}
            className="fill-zinc-600 dark:fill-zinc-400"
          >
            {label}
          </text>
        );
      })}
    </svg>
  );
}

/* ───────── Research Tab — v2 native (status card + sub-tabs) ───────── */
function ResearchBody({
  runId, ticker, industryBrief, deepResearch, deepAnnotated, citations,
  isResearchPhase, isComplete,
}: {
  runId: string;
  ticker: string;
  industryBrief: string | undefined;
  deepResearch: string | undefined;
  deepAnnotated: string | undefined;
  citations: CitationRegistryEntry[] | undefined;
  events: ProgressEvent[];
  liveData: Record<string, unknown>;
  isResearchPhase: boolean;
  isComplete: boolean;
}) {
  type SubTab = 'summary' | 'brief' | 'deep';
  const [sub, setSub] = useState<SubTab>('summary');
  const hasData = !!(industryBrief || deepResearch);
  const sourceCount = citations?.length ?? 0;

  if (!hasData && !isResearchPhase) {
    return (
      <div className="px-4 pt-4 pb-8 space-y-4">
        <LoadingCard label="Research streaming — 14+ source synthesis" minH={200} />
      </div>
    );
  }

  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* Research complete status card */}
      {hasData && (
        <div className="rounded-xl border border-[#d0e7d2] dark:border-[#2e7d32]/40 bg-[#ecf5ed]/60 dark:bg-[#2e7d32]/10 shadow-sm p-4 flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-white dark:bg-zinc-900 border border-[#d0e7d2] dark:border-[#2e7d32]/40 flex items-center justify-center shrink-0">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#2e7d32" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-50">Research complete</div>
            <div className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
              {sourceCount > 0 && <>{sourceCount} source{sourceCount === 1 ? '' : 's'} · </>}
              Qwen 3.6-plus + Claude Sonnet
            </div>
          </div>
        </div>
      )}

      {/* Sub-tab switcher */}
      {hasData && (
        <div className="flex items-center gap-1 p-1 bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800 rounded-lg">
          {([
            { id: 'summary' as const, label: 'Research summary' },
            { id: 'brief'   as const, label: 'Industry brief' },
            { id: 'deep'    as const, label: 'Deep research' },
          ]).map(t => (
            <button
              key={t.id}
              onClick={() => setSub(t.id)}
              className={`flex-1 h-8 rounded-md text-[11.5px] font-medium transition-colors
                ${sub === t.id
                  ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-50 shadow-sm border border-zinc-200 dark:border-zinc-800'
                  : 'text-zinc-500 dark:text-zinc-400 active:text-zinc-800'}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Sub-tab body */}
      {hasData && sub === 'summary' && (
        runId ? (
          <ResearchSummaryPanel
            runId={runId}
            ticker={ticker}
            industryBrief={industryBrief}
            deepResearch={deepResearch}
          />
        ) : (
          <StreamingResearchSummary
            ticker={ticker}
            industryBrief={industryBrief}
            deepResearch={deepResearch}
          />
        )
      )}
      {hasData && sub === 'brief' && industryBrief && (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-3">
            Industry Intelligence Brief
          </div>
          <div className="text-[12.5px] text-zinc-700 dark:text-zinc-300 leading-relaxed whitespace-pre-wrap">
            {industryBrief}
          </div>
        </div>
      )}
      {hasData && sub === 'deep' && deepResearch && (
        <DeepResearchPanel
          reportText={deepResearch}
          annotatedText={deepAnnotated}
          registry={citations}
          ticker={ticker}
        />
      )}

      {!isComplete && isResearchPhase && (
        <p className="text-[11px] text-zinc-400 dark:text-zinc-500 text-center">
          Research streaming — thinking stream shown above. Sections fill in as synthesis completes.
        </p>
      )}
    </div>
  );
}

/* ───────── Streaming Research Summary (no runId available yet) ───────────── */
/**
 * Rendered during ongoing research when runId is not yet persisted.
 * Shows whatever industry brief / deep research text has streamed in so far,
 * using the same collapsible accordion layout as the completed view — but
 * without the backend Qwen /analysis/research-summary call (no cache key yet).
 */
function StreamingResearchSummary({
  ticker, industryBrief, deepResearch,
}: {
  ticker: string;
  industryBrief: string | undefined;
  deepResearch: string | undefined;
}) {
  const [briefOpen, setBriefOpen] = useState(true);
  const [deepOpen, setDeepOpen]   = useState(!industryBrief); // expand deep if brief absent

  return (
    <div className="flex flex-col gap-3">
      {/* Streaming banner — explains why Qwen summary is absent vs. completed view */}
      <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-[11px] font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400">
            Research streaming · {ticker}
          </span>
        </div>
        <p className="text-[12px] text-zinc-600 dark:text-zinc-400 leading-relaxed">
          Industry brief and deep research are populating live. The AI summary card appears once synthesis completes.
        </p>
      </div>

      {industryBrief && (
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-xl overflow-hidden bg-white dark:bg-zinc-900">
          <button
            onClick={() => setBriefOpen(o => !o)}
            className="w-full flex items-center justify-between px-4 py-3 text-[13px] font-medium text-zinc-800 dark:text-zinc-100 active:bg-zinc-50 dark:active:bg-zinc-800/60"
          >
            <span>Industry Intelligence Brief</span>
            <span className="text-[11px] text-zinc-400">{briefOpen ? '▲' : '▼'}</span>
          </button>
          {briefOpen && (
            <div className="border-t border-zinc-200 dark:border-zinc-800 px-4 py-3 text-[12.5px] text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap leading-relaxed">
              {industryBrief}
            </div>
          )}
        </div>
      )}

      {deepResearch && (
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-xl overflow-hidden bg-white dark:bg-zinc-900">
          <button
            onClick={() => setDeepOpen(o => !o)}
            className="w-full flex items-center justify-between px-4 py-3 text-[13px] font-medium text-zinc-800 dark:text-zinc-100 active:bg-zinc-50 dark:active:bg-zinc-800/60"
          >
            <span>Deep Research</span>
            <span className="text-[11px] text-zinc-400">{deepOpen ? '▲' : '▼'}</span>
          </button>
          {deepOpen && (
            <div className="border-t border-zinc-200 dark:border-zinc-800 px-4 py-3 text-[12.5px] text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap leading-relaxed">
              {deepResearch}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ───────── Financials Tab — Revenue Build + Income Statement + Key Stats ─── */
function FinancialsBody({
  ticker, stockMetrics,
}: {
  ticker: string;
  stockMetrics: Record<string, number | undefined> | null;
}) {
  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* Revenue Build — placeholder UI until backend emits segment data.
          Matches the NVDA reference card structure (4 segment tiles). */}
      <V2RevenueBuildPlaceholder />

      {/* Income Statement — wrap FinancialsChart in zinc-900 dark card shell
          matching Key Stats / Valuation cards. The inner FinancialsChart
          has its own surface; the `v2-dark-card` wrapper overrides it. */}
      <div className="v2-dark-card rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm overflow-hidden">
        <style>{`
          .v2-dark-card > * {
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
            border-radius: 0 !important;
          }
        `}</style>
        <FinancialsChart ticker={ticker} />
      </div>

      {/* Financial Metric / Key Stats card — same as Summary tab */}
      {stockMetrics ? (
        <V2KeyStats metrics={stockMetrics} />
      ) : (
        <LoadingCard label="Financial Metrics" minH={200} />
      )}
    </div>
  );
}

function V2RevenueBuildPlaceholder() {
  // Static placeholder until backend emits segment breakdown
  const segments = [
    { name: 'Segment A', value: '—', delta: null },
    { name: 'Segment B', value: '—', delta: null },
    { name: 'Segment C', value: '—', delta: null },
    { name: 'Segment D', value: '—', delta: null },
  ];
  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
          Revenue Build
        </span>
        <span className="text-[10px] text-zinc-400 dark:text-zinc-500">LTM · coming soon</span>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {segments.map(s => (
          <div key={s.name} className="p-2.5 rounded-lg border border-zinc-100 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-800/40">
            <div className="text-[9.5px] uppercase tracking-wider font-semibold text-zinc-500 dark:text-zinc-400">
              {s.name}
            </div>
            <div className="text-[14px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-50 mt-1">
              {s.value}
            </div>
            {s.delta != null && (
              <div className="text-[10px] font-medium tabular-nums mt-0.5 text-[#2e7d32] dark:text-[#4ea354]">
                +{s.delta}%
              </div>
            )}
          </div>
        ))}
      </div>
      <p className="text-[10.5px] text-zinc-400 dark:text-zinc-500 mt-2.5 leading-relaxed">
        Segment breakdown will populate once the backend emits product-level revenue data.
      </p>
    </div>
  );
}

/* ───────── V2 Stock Chart (SVG-based, zinc aesthetic) ───────── */
const V2_TIMEFRAMES: { label: string; period: '1d' | '5d' | '1mo' | '3mo' | '1y' | '3y' | '5y' }[] = [
  { label: '1D', period: '1d' },
  { label: '1W', period: '5d' },
  { label: '1M', period: '1mo' },
  { label: '3M', period: '3mo' },
  { label: '1Y', period: '1y' },
  { label: '3Y', period: '3y' },
  { label: '5Y', period: '5y' },
];

function V2StockChart({ ticker }: { ticker: string }) {
  const [tfIdx, setTfIdx] = useState(4); // default 1Y
  const [history, setHistory] = useState<{ date: string; close: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const tf = V2_TIMEFRAMES[tfIdx];

  useEffect(() => {
    setLoading(true);
    getStockData(ticker, tf.period)
      .then((d) => setHistory(d?.history ?? []))
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, [ticker, tf.period]);

  const points = history.map(h => h.close);
  const min = points.length ? Math.min(...points) : 0;
  const max = points.length ? Math.max(...points) : 1;
  const w = 400, h = 180;
  const padT = 14, padB = 22, padL = 38, padR = 12;
  const chartW = w - padL - padR;
  const chartH = h - padT - padB;
  const xFor = (i: number) => padL + (i / Math.max(1, points.length - 1)) * chartW;
  const yFor = (p: number) => padT + chartH * (1 - (p - min) / Math.max(0.0001, max - min));
  const pathD = points.map((p, i) => (i === 0 ? 'M' : 'L') + `${xFor(i).toFixed(2)},${yFor(p).toFixed(2)}`).join(' ');
  const areaD = points.length
    ? `${pathD} L ${xFor(points.length - 1).toFixed(2)},${padT + chartH} L ${xFor(0).toFixed(2)},${padT + chartH} Z`
    : '';

  const yTicks = 4;
  const yVals = Array.from({ length: yTicks + 1 }, (_, i) => min + (max - min) * (i / yTicks));

  const periodDelta = points.length > 1 ? ((points[points.length - 1] - points[0]) / points[0]) * 100 : 0;
  const displayIdx = hoverIdx ?? Math.max(0, points.length - 1);
  const displayPrice = points[displayIdx] ?? 0;
  const displayDateLabel = (() => {
    const d = history[displayIdx]?.date;
    if (!d) return tf.label;
    try {
      return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch { return tf.label; }
  })();

  const handleMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const xInSvg = ((e.clientX - rect.left) / rect.width) * w;
    const ratio = Math.max(0, Math.min(1, (xInSvg - padL) / chartW));
    setHoverIdx(Math.round(ratio * Math.max(0, points.length - 1)));
  };

  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500">
            {hoverIdx != null ? displayDateLabel : `Price · ${tf.label}`}
          </div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-[22px] font-semibold tracking-tight tabular-nums text-zinc-900 dark:text-zinc-50 leading-none">
              ${displayPrice.toFixed(2)}
            </span>
            <span className={`text-[12px] font-medium tabular-nums ${periodDelta >= 0 ? 'text-[#2e7d32] dark:text-[#4ea354]' : 'text-rose-600 dark:text-rose-400'}`}>
              {periodDelta >= 0 ? '+' : ''}{periodDelta.toFixed(2)}%
            </span>
          </div>
        </div>
      </div>

      {/* Timeframe pills */}
      <div className="flex items-center gap-1 mb-3 overflow-x-auto phone-scroll">
        {V2_TIMEFRAMES.map((t, i) => (
          <button
            key={t.label}
            onClick={() => { setTfIdx(i); setHoverIdx(null); }}
            className={`h-7 px-3 text-[11px] font-semibold rounded-full transition-colors shrink-0
              ${tfIdx === i
                ? 'bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900'
                : 'bg-zinc-50 dark:bg-zinc-800/60 text-zinc-600 dark:text-zinc-400 active:bg-zinc-100 dark:active:bg-zinc-800'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="h-[180px] flex items-center justify-center gap-2 text-zinc-400 dark:text-zinc-500">
          <LoadingSpinner size={14} /><span className="text-[12px]">Loading chart…</span>
        </div>
      ) : points.length === 0 ? (
        <div className="h-[180px] flex items-center justify-center text-[12px] text-zinc-400 dark:text-zinc-500">
          No price data available
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${w} ${h}`}
          className="w-full touch-none select-none"
          onPointerMove={handleMove}
          onPointerLeave={() => setHoverIdx(null)}
          preserveAspectRatio="none"
          style={{ height: 180 }}
        >
          <defs>
            <linearGradient id="v2-stock-g" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%"   stopColor={BRAND} stopOpacity="0.22"/>
              <stop offset="100%" stopColor={BRAND} stopOpacity="0"/>
            </linearGradient>
          </defs>
          <g className="text-zinc-200 dark:text-zinc-800">
            {yVals.map((v, i) => (
              <line key={i} x1={padL} y1={yFor(v)} x2={w - padR} y2={yFor(v)}
                    stroke="currentColor" strokeWidth={0.6} strokeDasharray="2,3"/>
            ))}
          </g>
          <g className="fill-zinc-400 dark:fill-zinc-500">
            {yVals.map((v, i) => (
              <text key={i} x={padL - 4} y={yFor(v) + 3} textAnchor="end" fontSize={9}>${v.toFixed(0)}</text>
            ))}
          </g>
          <path d={areaD} fill="url(#v2-stock-g)"/>
          <path d={pathD} fill="none" stroke={BRAND} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round"/>
          {hoverIdx != null && points[hoverIdx] != null && (
            <g>
              <line x1={xFor(hoverIdx)} y1={padT} x2={xFor(hoverIdx)} y2={padT + chartH}
                    className="text-zinc-400 dark:text-zinc-500" stroke="currentColor" strokeWidth={0.8}/>
              <circle cx={xFor(hoverIdx)} cy={yFor(points[hoverIdx])} r={3.5} fill={BRAND}
                      className="stroke-white dark:stroke-zinc-900" strokeWidth={1.5}/>
            </g>
          )}
        </svg>
      )}
    </div>
  );
}

/* ───────── V2 Key Stats (zinc card, 2-col grid) ───────── */
function V2KeyStats({ metrics }: { metrics: Record<string, number | undefined> }) {
  // Signed money formatter — used for Net Cash where negative (net debt) is
  // meaningful. Market cap / revenue / FCF are non-signed and render via
  // `fmtMoney` (absolute value, with a minus prefix when negative).
  const fmtMoney = (v: number | undefined) => {
    if (v == null) return '—';
    const sign = v < 0 ? '-' : '';
    const abs = Math.abs(v);
    if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(1)}T`;
    if (abs >= 1e9)  return `${sign}$${(abs / 1e9).toFixed(1)}B`;
    if (abs >= 1e6)  return `${sign}$${(abs / 1e6).toFixed(0)}M`;
    return `${sign}$${abs.toLocaleString()}`;
  };
  const fmtPct = (v: number | undefined) => {
    if (v == null) return '—';
    const pct = v * 100;
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
  };
  const fmtMult = (v: number | undefined) => v == null ? '—' : `${v.toFixed(1)}×`;
  // Per-share price — 52wk high/low are raw dollar levels, not aggregates
  const fmtPrice = (v: number | undefined) => {
    if (v == null) return '—';
    return `$${v.toLocaleString(undefined, {
      minimumFractionDigits: v < 10 ? 2 : 0,
      maximumFractionDigits: v < 10 ? 2 : 0,
    })}`;
  };

  // 12-cell grid in request order. FMP-sourced fields (roic) fall back to
  // yfinance ROA when FMP is unavailable (handled backend-side).
  const rows: { k: string; v: string }[] = [
    { k: 'Market cap', v: fmtMoney(metrics.market_cap) },
    { k: 'Rev TTM',    v: fmtMoney(metrics.revenue) },
    { k: 'FCF',        v: fmtMoney(metrics.free_cash_flow) },
    { k: 'Net margin', v: fmtPct(metrics.net_margin) },
    { k: 'P/E',        v: fmtMult(metrics.pe_ratio) },
    { k: 'Rev growth', v: fmtPct(metrics.revenue_growth) },
    { k: 'EV/EBITDA',  v: fmtMult(metrics.ev_to_ebitda) },
    { k: 'ROE',        v: fmtPct(metrics.return_on_equity) },
    { k: 'ROIC',       v: fmtPct(metrics.return_on_invested_capital ?? metrics.return_on_assets) },
    { k: 'Net cash',   v: fmtMoney(metrics.net_cash) },
    { k: '52wk high',  v: fmtPrice(metrics.fifty_two_week_high) },
    { k: '52wk low',   v: fmtPrice(metrics.fifty_two_week_low) },
  ];

  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-3">
        Key Stats
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-2.5">
        {rows.map((r) => (
          <div key={r.k} className="flex items-baseline justify-between">
            <span className="text-[11.5px] text-zinc-500 dark:text-zinc-400">{r.k}</span>
            <span className={`text-[13px] font-semibold tabular-nums ${
              r.v.startsWith('+') ? 'text-[#2e7d32] dark:text-[#4ea354]'
              : r.v.startsWith('-') && r.v !== '—' ? 'text-rose-600 dark:text-rose-400'
              : 'text-zinc-900 dark:text-zinc-50'
            }`}>
              {r.v}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ───────── Empty state ───────── */
function EmptyTab({ label = 'Not yet available' }: { label?: string }) {
  return (
    <div className="px-4 py-12 text-center text-[12.5px] text-zinc-400 dark:text-zinc-500">
      {label}
    </div>
  );
}
