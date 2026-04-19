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
import { getStockData } from '@/lib/api';

// Existing panel components (reused as-is)
import { ScenarioChart } from '@/components/report/ScenarioChart';
import { PowerLawRadar } from '@/components/report/PowerLawRadar';
import { ValueTrapChecklist } from '@/components/report/ValueTrapChecklist';
import { AgentSignalsPanel } from '@/components/report/AgentSignalsPanel';
import { FinancialsChart } from '@/components/report/FinancialsChart';
import { ValuationLadder } from '@/components/report/ValuationLadder';
import { DebatePanel } from '@/components/report/DebatePanel';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { DeepResearchPanel } from '@/components/report/DeepResearchPanel';
import { LiveSearchPanel } from '@/components/report/LiveSearchPanel';
import { MobileChartStrip } from '@/components/mobile/MobileChartStrip';
import { MobileKeyStats } from '@/components/mobile/MobileKeyStats';

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
      {/* Ticker header */}
      <div
        className="sticky z-20 bg-white/90 dark:bg-zinc-900/90 backdrop-blur border-b border-zinc-100 dark:border-zinc-800 px-4 pt-3 pb-3"
        style={{ top: 'env(safe-area-inset-top, 0px)' }}
      >
        <div className="flex items-baseline justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-[20px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50 tabular-nums">
                {ticker || '—'}
              </span>
              {decision?.action && <ActionPill action={decision.action} size="lg" />}
            </div>
            <div className="mt-1 flex items-center gap-1.5 flex-wrap">
              {sector && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400">
                  {sector}
                </span>
              )}
              {regime?.risk_appetite && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400">
                  {regime.risk_appetite} · {regime.volatility_regime ?? ''} vol
                </span>
              )}
            </div>
          </div>
          {livePrice != null && (
            <div className="text-right shrink-0">
              <div className="text-[18px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50 tabular-nums leading-none">
                ${livePrice.toFixed(2)}
              </div>
              {priceChangePct != null && (
                <div className="mt-1 text-[11px]">
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
           style={{ top: 'calc(env(safe-area-inset-top, 0px) + 72px)' }}>
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
        {tab === 'risk'       && <RiskBody       powerLaw={powerLaw} valueTrap={valueTrap} ticker={ticker} isRunning={isRunning} />}
        {tab === 'research'   && <ResearchBody   runId={runId} ticker={ticker} industryBrief={industryBrief} deepResearch={deepResearch} deepAnnotated={deepAnnotated} citations={citations} events={events} liveData={liveData} isResearchPhase={isResearchPhase} isComplete={isComplete} />}
        {tab === 'financials' && <FinancialsBody ticker={ticker} />}
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
      {/* Stock chart — always rendered (fetches independently) */}
      {ticker && <MobileChartStrip ticker={ticker} />}

      {/* Key financial metrics — fetched via getStockData in parent */}
      {ticker && (
        stockMetrics ? (
          <MobileKeyStats ticker={ticker} metrics={stockMetrics} />
        ) : (
          <LoadingCard label="Key Stats" minH={100} />
        )
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
  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* DCF Valuation Ladder */}
      {dcfRange ? (
        <ValuationLadder dcfRange={dcfRange} currentPrice={currentPrice ?? undefined} ticker={ticker} />
      ) : (
        <LoadingCard label="DCF Valuation Ladder" minH={120} />
      )}

      {/* Scenario analysis */}
      {scenarioAnalysis ? (
        <ScenarioChart scenario={scenarioAnalysis} ticker={ticker} />
      ) : (
        <LoadingCard label="Scenario Analysis" minH={100} />
      )}

      {/* Price target */}
      {decision?.price_target != null ? (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-sm p-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400 dark:text-zinc-500 mb-2">
            Price target
          </div>
          <div className="text-[22px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
            ${decision.price_target.toFixed(2)}
          </div>
        </div>
      ) : (
        <LoadingCard label="Price Target" minH={60} />
      )}

      {!isRunning && !dcfRange && !scenarioAnalysis && (
        <EmptyTab label="Valuation data not available for this run." />
      )}
    </div>
  );
}

/* ───────── Investors Tab ───────── */
function InvestorsBody({
  agentSignals, debateResult, ticker, isRunning,
}: {
  agentSignals: AgentSignals | undefined;
  debateResult: DebateResult | undefined;
  ticker: string;
  isRunning: boolean;
}) {
  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {agentSignals ? (
        <AgentSignalsPanel agentSignals={agentSignals} ticker={ticker} />
      ) : (
        <LoadingCard
          label={isRunning ? 'Investor Agents (12 running in parallel)' : 'Investor Signals'}
          minH={120}
        />
      )}
      {debateResult ? (
        <DebatePanel debateResult={debateResult} ticker={ticker} />
      ) : (
        <LoadingCard
          label={isRunning ? 'Agent Debate (if triggered)' : 'Agent Debate'}
          minH={80}
        />
      )}
    </div>
  );
}

/* ───────── Risk Tab ───────── */
function RiskBody({
  powerLaw, valueTrap, ticker, isRunning,
}: {
  powerLaw: PowerLawAnalysis | undefined;
  valueTrap: ValueTrapAnalysis | undefined;
  ticker: string;
  isRunning: boolean;
}) {
  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {powerLaw ? (
        <PowerLawRadar powerLaw={powerLaw} ticker={ticker} />
      ) : (
        <LoadingCard
          label={isRunning ? 'Power Law Moat Analysis' : 'Power Law Moat'}
          minH={140}
        />
      )}
      {valueTrap ? (
        <ValueTrapChecklist analysis={valueTrap} ticker={ticker} />
      ) : (
        <LoadingCard
          label={isRunning ? 'Value Trap Audit' : 'Value Trap Checks'}
          minH={120}
        />
      )}
    </div>
  );
}

/* ───────── Research Tab ───────── */
function ResearchBody({
  runId, ticker, industryBrief, deepResearch, deepAnnotated, citations,
  events, liveData, isResearchPhase, isComplete,
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
  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      {/* Live thinking panel is rendered globally below the progress bar in
          V2ReportView so it shows on every tab. Do not duplicate here. */}

      {/* Research summary (4-category bullets from LLM) */}
      {industryBrief && runId && (
        <ResearchSummaryPanel
          runId={runId}
          ticker={ticker}
          industryBrief={industryBrief}
          deepResearch={deepResearch}
          industryBriefContent={industryBrief ? (
            <DeepResearchPanel
              reportText={deepResearch ?? ''}
              annotatedText={deepAnnotated}
              registry={citations}
              ticker={ticker}
            />
          ) : undefined}
        />
      )}

      {!industryBrief && !deepResearch && !isResearchPhase && (
        <EmptyTab label="Research data will appear as the pipeline streams." />
      )}
    </div>
  );
}

/* ───────── Financials Tab ───────── */
function FinancialsBody({ ticker }: { ticker: string }) {
  return (
    <div className="px-4 pt-4 pb-8 space-y-4">
      <FinancialsChart ticker={ticker} />
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
