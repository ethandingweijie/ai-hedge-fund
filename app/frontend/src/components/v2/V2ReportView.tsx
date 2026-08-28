/**
 * V2ReportView.tsx — Reimagined Report view (live + complete states)
 *
 * Tabs: Summary · Valuation · Decision · Risk · Research · Financials
 *
 * Wraps existing report panel components (ValuationLadder, PowerLawRadar,
 * ValueTrapChecklist, DecisionInputsCard, FinancialsChart,
 * ResearchSummaryPanel, IndustryBriefPanel, DeepResearchPanel) in the new
 * zinc-neutral tab shell. No translator layer needed — existing panels
 * already consume the RunResult shape.
 *
 * M2 Track E: the Investors tab (12-persona committee + debate round) was
 * decommissioned with the committee-free PM; the tab is now "Decision" and
 * renders the PM's decision-inputs card instead.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { MessageSquare } from 'lucide-react';
import type {
  RunResult,
  VgpmResult,
  ScenarioAnalysis,
  PowerLawAnalysis,
  ValueTrapAnalysis,
  DcfRange,
  CitationRegistryEntry,
  ProgressEvent,
  MacroRegime,
  BiopharmaPipelineAsset,
  SaasMetrics,
  SectorCardPayload,
  PtHistoryPoint,
  PriorRecap,
  FreshnessDelta,
} from '@/lib/reportTypes';
import {
  getStockData, getCompanyName,
  getRevenueProductSegmentation, getRevenueGeoSegmentation,
  type RevenueSegmentation,
} from '@/lib/api';
import { extractLatestFinancials, isBiopharmaSector, isTechSector, classifyTechSubtype } from '@/lib/utils';

// Existing panel components (reused as-is)
import { FinancialsChart } from '@/components/report/FinancialsChart';
import { FinancialStatements } from '@/components/report/FinancialStatements';
import type { FinancialStatementsPayload } from '@/components/report/FinancialStatements';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { CardAuditBanner } from '@/components/report/CardAuditBanner';
import type { DdCardAudit } from '@/lib/reportTypes';
import { DeepResearchPanel } from '@/components/report/DeepResearchPanel';
import { LiveSearchPanel } from '@/components/report/LiveSearchPanel';
import { REITValuationPanel } from '@/components/report/reit/REITValuationPanel';
import { BankValuationPanel } from '@/components/report/bank/BankValuationPanel';
import { BiopharmaValuationPanel } from '@/components/report/biopharma/BiopharmaValuationPanel';
import { TechValuationPanel } from '@/components/report/tech/TechValuationPanel';
import { SectorValuationCard } from '@/components/report/SectorValuationCard';
import { DcfMethodologyPanel } from '@/components/report/DcfMethodologyPanel';
import { PriceTargetPanel } from '@/components/report/PriceTargetPanel';
import { SotpAnalystPanel } from '@/components/report/SotpAnalystPanel';
import { PriceTargetHistoryStrip } from '@/components/report/PriceTargetHistoryStrip';
import { PriorReportCard } from '@/components/report/PriorReportCard';
import { ProgressHeader } from '@/components/report/ProgressHeader';
import { DecisionInputsCard } from '@/components/report/DecisionInputsCard';
import { AssumptionWatchCard } from '@/components/report/AssumptionWatchCard';
import { useIsResearchPhase, useProgressDerived } from '@/hooks/useProgressDerived';
// MobileChartStrip / MobileKeyStats replaced with v2-native components below

import { ActionPill, GradeChip, Delta, BRAND } from '@/components/v2/shared';
import { RationaleBlock } from '@/components/report/shared/RationaleBlock';
import { Markdown } from '@/components/report/shared/Markdown';

type TabId = 'summary' | 'valuation' | 'decision' | 'risk' | 'research' | 'financials';

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
  { id: 'decision',   label: 'Decision'   },
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
  // M2 Track E: analyst_signals still feeds the desktop IntelligenceGrid, but
  // V2's mobile surface shows the PM's decision inputs instead of the retired
  // investor panel — no investor/debate derivations remain here.
  const scenarioAnalysis = (data.scenario_analysis as Record<string, ScenarioAnalysis> | undefined)?.[ticker];
  const powerLaw = (data.power_law_analysis as Record<string, PowerLawAnalysis> | undefined)?.[ticker];
  const valueTrap = (data.value_trap_analysis as Record<string, ValueTrapAnalysis> | undefined)?.[ticker];
  const dcfRange = (data.dcf_range as Record<string, DcfRange> | undefined)?.[ticker];
  const dcfSkipReason = (data.dcf_skip_reasons as Record<string, string> | undefined)?.[ticker];
  // Sector-specific valuation card (Option B). Absent for legacy sub-profiles.
  const sectorCard = (data.sector_card as Record<string, SectorCardPayload> | undefined)?.[ticker];
  const industryBrief = data.industry_brief as string | undefined;
  // M1 recency loop — prior report recap + freshness delta (absent on
  // first-ever runs; PriorReportCard renders nothing then).
  const priorRecap = (data.prior_recap as Record<string, PriorRecap> | undefined)?.[ticker];
  const freshnessDelta = (data.freshness_delta as Record<string, FreshnessDelta> | undefined)?.[ticker];
  const deepResearch = (data.deep_research ?? data.deep_research_report) as string | undefined;
  const deepAnnotated = data.deep_research_annotated as string | undefined;
  const citations = data.citation_registry as CitationRegistryEntry[] | undefined;

  // Company name fetch (sets header "NVDA · NVIDIA Corporation" style)
  useEffect(() => {
    if (!ticker) return;
    getCompanyName(ticker)
      .then((d) => {
        setCompanyName(d?.name || '');
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

  const isResearchPhase = useIsResearchPhase(phaseMap);
  const progressDerived = useProgressDerived(phaseMap);

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="min-h-full flex flex-col bg-background">
      {/* Ticker header — offset from top so it clears the iOS status bar.
          The avatar menu button is a fixed top-right button (in MobileTopBar);
          we leave a top gutter so the ticker row sits just below it. */}
      <div
        className="sticky z-20 bg-background/95 backdrop-blur border-b border-border/60 px-5 pb-3"
        style={{ top: 0, paddingTop: 'calc(env(safe-area-inset-top, 0px) + 56px)' }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span
                className="text-[22px] font-bold tracking-tight text-foreground tabular-nums leading-none"
                style={{ letterSpacing: '-0.02em' }}
              >
                {ticker || '—'}
              </span>
              {companyName && (
                <span className="text-[13px] text-muted-foreground truncate leading-none">
                  {companyName}
                </span>
              )}
            </div>
            <div className="mt-2 flex items-center gap-1.5 flex-wrap">
              {sector && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-border text-muted-foreground">
                  {sector}
                </span>
              )}
              {regime?.risk_appetite && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-border text-muted-foreground">
                  {regime.risk_appetite}{regime.volatility_regime ? ` · ${regime.volatility_regime} vol` : ''}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-start gap-2 shrink-0">
            {ticker && (
              <Link
                to={`/discuss/${ticker}`}
                title={`Discuss ${ticker}`}
                className="flex items-center justify-center w-8 h-8 rounded-full bg-brand text-white active:bg-brand/90 shrink-0"
              >
                <MessageSquare size={15} />
              </Link>
            )}
            {livePrice != null && (
              <div className="text-right shrink-0">
                <div
                  className="text-[22px] font-bold tracking-tight text-foreground tabular-nums leading-none"
                  style={{ letterSpacing: '-0.02em' }}
                >
                  ${livePrice.toFixed(2)}
                </div>
                {priceChangePct != null && (
                  <div className="mt-1.5 text-[12px]">
                    <Delta v={priceChangePct} />
                    <span className="text-muted-foreground/70 font-normal ml-1">1Y</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Tab strip */}
      <div className="sticky z-10 bg-background border-b border-border/60"
           style={{ top: 'calc(env(safe-area-inset-top, 0px) + 120px)' }}>
        <div className="px-3 flex items-center gap-1 overflow-x-auto phone-scroll">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`h-10 px-2.5 text-[12px] font-medium border-b-[2px] -mb-px transition-colors shrink-0 flex items-center gap-1
                ${tab === t.id
                  ? 'text-foreground border-brand'
                  : 'text-muted-foreground border-transparent active:text-foreground'}`}
            >
              {t.label}
              {t.id === 'summary' && isRunning && (
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-brand animate-pulse" />
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
            currentPhaseLabel={progressDerived.phaseLabel ?? currentPhaseLabel}
            thinkingDetail={progressDerived.thinkingDetail}
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

      {/* Card QA banner (Phase 10.5 self-healing audit). Renders above
          tab content so users see data-quality warnings on every tab. */}
      <CardAuditBanner
        audit={(data.card_qa_audit as Record<string, DdCardAudit> | undefined)?.[ticker]}
        ticker={ticker}
      />

      {/* Tab bodies */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'summary'    && <SummaryBody    ticker={ticker} stockMetrics={stockMetrics} decision={decision} vgpm={vgpm} isRunning={isRunning} prior={priorRecap} delta={freshnessDelta} />}
        {tab === 'valuation'  && <ValuationBody
          dcfRange={dcfRange}
          dcfSkipReason={dcfSkipReason}
          scenarioAnalysis={scenarioAnalysis}
          decision={decision}
          ticker={ticker}
          currentPrice={livePrice}
          isRunning={isRunning}
          sector={sector}
          pipelineAssets={(data.pipeline_assets as Record<string, BiopharmaPipelineAsset[]> | undefined)?.[ticker]}
          sections={data.deep_research_sections as Record<string, string> | undefined}
          rawFinancials={data.raw_financials as Record<string, unknown> | undefined}
          profile={
            (data.profile_names as Record<string, string> | undefined)?.[ticker]
            ?? (data.profile_name as string | undefined)
          }
          saasMetrics={(data.saas_metrics as Record<string, SaasMetrics> | undefined)?.[ticker]}
          sectorCard={sectorCard}
          ptHistory={(data.price_target_history as Record<string, PtHistoryPoint[]> | undefined)?.[ticker]}
        />}
        {tab === 'decision'   && (isRunning && !decision?.decision_inputs
          ? <LoadingCard label="Decision Inputs" minH={120} />
          : (
            <>
              <DecisionInputsCard decisionInputs={decision?.decision_inputs} ticker={ticker} isRunning={isRunning} />
              {/* R3 Assumption Watch — mounts in BOTH render paths; fetches
                  its own endpoint and renders nothing when unflagged. */}
              <div className="mt-3">
                <AssumptionWatchCard ticker={ticker} />
              </div>
            </>
          ))}
        {tab === 'risk'       && <RiskBody       powerLaw={powerLaw} valueTrap={valueTrap} scenarioAnalysis={scenarioAnalysis} isRunning={isRunning} />}
        {tab === 'research'   && <ResearchBody   runId={runId} ticker={ticker} industryBrief={industryBrief} deepResearch={deepResearch} deepAnnotated={deepAnnotated} citations={citations} events={events} liveData={liveData} isResearchPhase={isResearchPhase} isComplete={isComplete} />}
        {tab === 'financials' && (
          <FinancialsBody
            ticker={ticker}
            stockMetrics={stockMetrics}
            statements={data.financial_statements as FinancialStatementsPayload | undefined}
          />
        )}
      </div>
    </div>
  );
}

/* ───────── Progress Header ─────────
 *
 * Prior layout truncated the "Thinking: ..." message to one line (dropping
 * the middle/end of each update) AND wasted a second line on a static
 * "Hold tight — research streams in over 4-6 minutes" that never changed
 * across the run.
 *
 * New layout keeps two rows, just better used:
 *   Row 1: Phase label (short human-readable, e.g. "Deep Research") · % · Cancel
 *   Row 2: Thinking / status detail — full width, wraps up to 3 lines
 *          (replaces the static "Hold tight" — live detail flows into that
 *          space so the user can actually read what's happening)
 *   Row 3: Progress bar
 */
/* ───────── Summary Tab ───────── */
function LoadingSpinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="inline-block rounded-full border-2 border-brand border-t-transparent animate-spin"
      style={{ width: size, height: size }}
    />
  );
}

function LoadingCard({ label, minH = 80 }: { label: string; minH?: number }) {
  return (
    <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70 mb-3">
        {label}
      </div>
      <div className="flex items-center justify-center gap-2.5 text-muted-foreground/70" style={{ minHeight: minH }}>
        <LoadingSpinner size={14} />
        <span className="text-[12px]">Computing…</span>
      </div>
    </div>
  );
}

function vgpmTooltip(label: string, dim?: { score?: number; subs?: string[] }): string | undefined {
  if (!dim) return undefined;
  return [`${label}: ${dim.score}/100`, ...(dim.subs ?? [])].join('\n');
}

function LoadingGradeChip({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-1 min-w-[28px]">
      <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">{label}</span>
      <span className="inline-flex items-center justify-center min-w-[22px] h-[20px] px-1.5 rounded-md bg-muted/60">
        <LoadingSpinner size={10} />
      </span>
    </div>
  );
}

function SummaryBody({
  ticker, stockMetrics, decision, vgpm, isRunning, prior, delta,
}: {
  ticker: string;
  stockMetrics: Record<string, number | undefined> | null;
  decision: any;
  vgpm: VgpmResult | undefined;
  isRunning: boolean;
  prior: PriorRecap | undefined;
  delta: FreshnessDelta | undefined;
}) {
  return (
    <div className="px-4 pt-5 pb-10 space-y-5">
      {/* Stock chart card */}
      {ticker && <V2StockChart ticker={ticker} />}

      {/* Key financial metrics card */}
      {ticker && (
        stockMetrics ? <V2KeyStats metrics={stockMetrics} /> : <LoadingCard label="Key Stats" minH={140} />
      )}

      {/* Portfolio Manager hero card — loading skeleton while pipeline runs */}
      {decision ? (
        <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70 mb-2">
            Portfolio Manager
          </div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <ActionPill action={decision.action} size="lg" />
            {/* Portfolio weight (position size). The PM's `confidence` field is
                deliberately NOT shown — it was misread as a probability/quality
                signal when the meaningful number here is the recommended
                portfolio weight. Labelled "weight" to remove that ambiguity. */}
            {typeof decision.position_size_pct === 'number' && (
              <span className="text-[15px] font-semibold tabular-nums text-foreground">
                {(decision.position_size_pct * 100).toFixed(1)}%
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">weight</span>
              </span>
            )}
          </div>
          {typeof decision.price_target === 'number' && (
            <div className="mt-3 flex items-baseline gap-2">
              <span className="text-[11px] text-muted-foreground">Target</span>
              <span className="text-[15px] font-semibold tabular-nums text-foreground">
                ${decision.price_target.toFixed(2)}
              </span>
            </div>
          )}
          {decision.rationale && (
            /* Shared with the desktop path via RationaleBlock so the two
               render paths cannot drift apart again. */
            <RationaleBlock
              text={String(decision.rationale)}
              className="mt-3"
              itemClassName="text-[12.5px] text-foreground/80 leading-relaxed"
            />
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70 mb-2">
            Portfolio Manager
          </div>
          <div className="flex items-center gap-2.5 py-2 text-muted-foreground/70">
            <LoadingSpinner size={16} />
            <span className="text-[12px]">
              {isRunning ? 'Research & valuation running…' : 'Waiting for decision'}
            </span>
          </div>
        </div>
      )}

      {/* M1 recency — what the last report said + what changed since.
          Renders nothing on first-ever runs for this ticker. */}
      <PriorReportCard prior={prior} delta={delta} ticker={ticker} />

      {/* VGPM scorecard — always rendered, with spinners per grade until ready */}
      <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70 mb-3">
          VGPM Scorecard
          {!vgpm && isRunning && <span className="ml-2 text-muted-foreground/70 normal-case font-normal tracking-normal">· computing…</span>}
        </div>
        <div className="grid grid-cols-4 gap-3">
          {vgpm?.valuation?.grade
            ? <GradeChip grade={vgpm.valuation.grade} label="Valuation" tooltip={vgpmTooltip('Valuation', vgpm.valuation)} />
            : <LoadingGradeChip label="Valuation" />}
          {vgpm?.growth?.grade
            ? <GradeChip grade={vgpm.growth.grade} label="Growth" tooltip={vgpmTooltip('Growth', vgpm.growth)} />
            : <LoadingGradeChip label="Growth" />}
          {vgpm?.profitability?.grade
            ? <GradeChip grade={vgpm.profitability.grade} label="Profit." tooltip={vgpmTooltip('Profitability', vgpm.profitability)} />
            : <LoadingGradeChip label="Profit." />}
          {vgpm?.momentum?.grade
            ? <GradeChip grade={vgpm.momentum.grade} label="Momentum" tooltip={vgpmTooltip('Momentum', vgpm.momentum)} />
            : <LoadingGradeChip label="Momentum" />}
        </div>
      </div>
    </div>
  );
}

/* ───────── Valuation Tab ───────── */
function ValuationBody({
  dcfRange, dcfSkipReason, scenarioAnalysis, decision, ticker, currentPrice, isRunning,
  sector, pipelineAssets, sections, rawFinancials, profile, saasMetrics, sectorCard, ptHistory,
}: {
  dcfRange: DcfRange | undefined;
  /** Why dcfRange came back {} for this ticker, if known (see DcfMethodologyPanel). */
  dcfSkipReason?: string;
  scenarioAnalysis: ScenarioAnalysis | undefined;
  decision: any;
  ticker: string;
  currentPrice: number | null;
  isRunning: boolean;
  /** Sector classification — gates Biopharma / Tech-specific panels. */
  sector?: string;
  /** Pipeline assets for Biopharma tickers (extracted from deep research). */
  pipelineAssets?: BiopharmaPipelineAsset[];
  /** Deep research section 2 text blocks for narrative cards. */
  sections?: Record<string, string>;
  /** FY-keyed raw financials dict — used to derive R&D / revenue / FCF. */
  rawFinancials?: Record<string, unknown>;
  /** Profile name (e.g. "Hyperscaler / Tech Conglomerate") — Tech sub-type routing. */
  profile?: string;
  /** SaaS metrics extractor output — Tech sub-type NRR / Rule-of-40 / CAC tiles. */
  saasMetrics?: SaasMetrics;
  /** Sector-specific valuation card payload (V3 audit bridge UI). */
  sectorCard?: SectorCardPayload;
  /** Tier 2.6 — past-run PT track record (built at save time; oldest-first). */
  ptHistory?: PtHistoryPoint[];
}) {
  // Extract the numbers still needed by sibling panels below (PriceTargetPanel
  // now owns its own derivation of target/upside/bear-base-bull/consensus).
  const current = currentPrice ?? scenarioAnalysis?.current_price ?? null;
  const wacc = dcfRange?.wacc ?? null;

  const haveAny = dcfRange || scenarioAnalysis || decision;
  if (!haveAny) {
    return (
      <div className="px-4 pt-5 pb-10 space-y-5">
        <LoadingCard label="12-Month Price Target" minH={320} />
        <LoadingCard label="DCF Valuation Ladder" minH={160} />
      </div>
    );
  }

  return (
    <div className="px-4 pt-5 pb-10 space-y-5">
      {/* ── 12-Month Price Target ──────────────────────────────────────
          Standardised 2026-07: same PriceTargetPanel component desktop
          uses (components/report/PriceTargetPanel.tsx) — one
          implementation instead of mobile carrying its own duplicate. */}
      {haveAny ? (
        <PriceTargetPanel dcfRange={dcfRange} scenario={scenarioAnalysis} decision={decision} ticker={ticker} />
      ) : (
        <LoadingCard label="12-Month Price Target" minH={320} />
      )}

      {/* ── Tier 2.6: model target track record (past IV/PT vs price-at-run).
          Renders nothing until ≥2 prior runs exist for the ticker. ────── */}
      {ptHistory && ptHistory.length > 0 && (
        <PriceTargetHistoryStrip history={ptHistory} ticker={ticker} />
      )}

      {/* ── REIT branch OR DCF Valuation Ladder ──────────────────────────── */}
      {/* When dcfRange.reit_breakdown is populated (backend emits it for     */}
      {/* RealEstate / REIT sectors), render the full REIT-specific panel    */}
      {/* stack (NAV hero, Method Breakdown, NPI/DPU history, Cap-Rate grid).*/}
      {/* Otherwise fall through to the generic v2 DCF ladder.                */}
      {dcfRange ? (
        dcfRange.reit_breakdown ? (
          <REITValuationPanel
            dcfRange={dcfRange}
            currentPrice={current ?? undefined}
            ticker={ticker}
          />
        ) : dcfRange.bank_breakdown ? (
          <BankValuationPanel
            dcfRange={dcfRange}
            currentPrice={current ?? undefined}
            ticker={ticker}
          />
        ) : isBiopharmaSector(sector) ? (() => {
          const _fin = extractLatestFinancials(rawFinancials);
          return (
            <BiopharmaValuationPanel
              dcfRange={dcfRange}
              currentPrice={current ?? undefined}
              ticker={ticker}
              pipelineAssets={pipelineAssets}
              sections={sections}
              rd_spend={_fin.rd_spend}
              revenue={_fin.revenue}
              fcf={_fin.fcf}
            />
          );
        })()
        /* Tech sub-type routing — classifyTechSubtype tries profile_name,    */
        /* then a ticker-table fallback (SNOW→growth_saas etc.) so historical  */
        /* runs missing profile_name still render the correct panel.          */
        : (isTechSector(sector) && classifyTechSubtype(profile, ticker) !== null) ? (
          <TechValuationPanel
            dcfRange={dcfRange}
            currentPrice={current ?? undefined}
            ticker={ticker}
            profile={profile}
            sections={sections}
            rawFinancials={rawFinancials}
            saasMetrics={saasMetrics}
          />
        ) : (
          <V2ValuationLadder dcfRange={dcfRange} current={current ?? undefined} wacc={wacc} />
        )
      ) : (
        <LoadingCard label="DCF Valuation Ladder" minH={160} />
      )}

      {/* ── Tier 1: GS-style SOTP report card ──────────────────────────────
          Present only when the SOTP extractor produced assumptions for this
          ticker (dcf_range[ticker].sotp_breakdown). Stacks below whichever
          sector branch rendered above; the DCF methodology panel follows. ── */}
      {dcfRange?.sotp_breakdown && <SotpAnalystPanel breakdown={dcfRange.sotp_breakdown} />}

      <DcfMethodologyPanel dcfRange={dcfRange} ticker={ticker} skipReason={dcfSkipReason} />

      {/* ── Sector Valuation Card (Option B render) ─────────────────── */}
      {sectorCard && <SectorValuationCard payload={sectorCard} />}

      {isRunning && !haveAny && (
        <p className="text-center text-[11px] text-muted-foreground/70 pt-2">
          Valuation renders once the pipeline reaches Phase 4.5 (DCF Engine).
        </p>
      )}
    </div>
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
    { name: 'Current',   value: current,  color: 'bg-muted-foreground/50', delta: null as number | null, growth: null as number | null | undefined },
    { name: 'Bull case', value: bullIV,   color: 'bg-brand', delta: pct(bullIV), growth: bullG },
    { name: 'Base case', value: baseIV,   color: 'bg-blue-500 dark:bg-blue-400',   delta: pct(baseIV), growth: baseG },
    { name: 'Bear case', value: bearIV,   color: 'bg-surface-2',   delta: pct(bearIV), growth: bearG },
  ];

  return (
    <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
          DCF Valuation Ladder
        </span>
        {wacc != null && (
          <span className="text-[10px] tabular-nums text-muted-foreground/70">
            WACC: {(wacc * 100).toFixed(1)}%
          </span>
        )}
      </div>
      <div className="space-y-2.5">
        {rows.map(r => r.value != null && (
          <div key={r.name} className="flex items-center gap-3">
            <span className="text-[11.5px] text-muted-foreground w-[62px] shrink-0">{r.name}</span>
            <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full ${r.color}`}
                style={{ width: `${Math.max(4, Math.min(100, (r.value / maxIV) * 100))}%` }}
              />
            </div>
            <div className="flex items-baseline gap-1.5 min-w-[100px] justify-end">
              <span className="text-[12.5px] font-semibold text-foreground tabular-nums">
                ${r.value.toFixed(2)}
              </span>
              {r.delta != null && (
                <span className={`text-[11px] font-medium tabular-nums ${r.delta >= 0 ? 'text-gain' : 'text-loss'}`}>
                  {r.delta >= 0 ? '+' : ''}{r.delta.toFixed(1)}%
                </span>
              )}
            </div>
            {r.growth != null && (
              <span className="text-[10px] text-muted-foreground/70 tabular-nums w-[38px] text-right">
                @ {(r.growth * 100).toFixed(0)}% g
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ───────── Decision Tab ───────────────────────────────────────────────────
 * M2 Track E: AGENT_LABELS_V2, InvestorsBody (12 persona verdict cards +
 * thesis list) and DebateRow lived here. The committee was decommissioned;
 * the tab now renders the shared DecisionInputsCard (mounted here AND in
 * ReportPage/ReportViewPage desktop JSX — both render paths). */

/* ───────── Risk Tab — v2 native (Power Law + Value Trap + Scenario Mix) ─── */
function RiskBody({
  powerLaw, valueTrap, scenarioAnalysis,
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
      <div className="px-4 pt-5 pb-10 space-y-5">
        <LoadingCard label="Power Law — 5-dimension moat audit" minH={240} />
        <LoadingCard label="Value Trap Check" minH={200} />
      </div>
    );
  }

  return (
    <div className="px-4 pt-5 pb-10 space-y-5">
      {/* Power Law card */}
      {powerLaw ? (
        <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
          <div className="flex items-center justify-between mb-4">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
              Power Law
            </span>
            {powerLawOverall != null && (
              <span className="text-[15px] font-semibold tabular-nums text-foreground">
                {powerLawOverall.toFixed(1)} <span className="text-[11px] text-muted-foreground/70 font-normal">/ 10</span>
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
                  <span className="text-[12.5px] font-semibold text-foreground">{d.label}</span>
                  <span className="text-[12.5px] font-semibold text-foreground tabular-nums">
                    {d.score.toFixed(1)}
                  </span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full bg-brand"
                    style={{ width: `${Math.max(0, Math.min(100, (d.score / 10) * 100))}%` }}
                  />
                </div>
                {d.note && (
                  <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                    {d.note}
                  </p>
                )}
                {d.concern && (
                  <p className="text-[11px] text-content-high mt-0.5 leading-relaxed">
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
        <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
              Value Trap Check
            </span>
            {trapVerdict && (
              <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${
                trapVerdict.includes('HIGH')
                  ? 'text-content-high bg-surface-2 border-[var(--hairline)]'
                  : trapVerdict.includes('MEDIUM')
                  ? 'text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10 border-amber-200 dark:border-amber-500/30'
                  : 'text-brand bg-brand/10 border-brand/25'
              }`}>
                {trapVerdict}
              </span>
            )}
          </div>
          <div className="space-y-3">
            {trapChecks.map((c, i) => c.rating && (
              <div key={c.k} className={`flex items-start gap-2.5 ${i > 0 ? 'pt-3 border-t border-border/60' : ''}`}>
                <span
                  className={`w-2 h-2 rounded-full shrink-0 mt-1.5 ${
                    c.rating === 'GREEN' ? 'bg-brand'
                    : c.rating === 'AMBER' ? 'bg-amber-500 dark:bg-amber-400'
                    : 'bg-surface-2'
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[12.5px] font-semibold text-foreground">{c.k}</span>
                    <span className={`text-[10px] font-semibold tracking-wide shrink-0 ${
                      c.rating === 'GREEN' ? 'text-brand'
                      : c.rating === 'AMBER' ? 'text-amber-600 dark:text-amber-400'
                      : 'text-content-high'
                    }`}>
                      {c.rating}
                    </span>
                  </div>
                  {c.ev && (
                    <p className="text-[11.5px] text-muted-foreground mt-0.5 leading-relaxed">
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
        <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
              Scenario Mix
            </span>
            <span className="text-[10px] text-muted-foreground/70">12-mo</span>
          </div>
          <div className="w-full h-2 rounded-full bg-muted overflow-hidden flex">
            <div
              className="h-full bg-brand"
              style={{ width: `${Math.round(((bullProb ?? 0) / ((bullProb ?? 0) + (bearProb ?? 0) || 1)) * 100)}%` }}
            />
            <div
              className="h-full bg-surface-2"
              style={{ width: `${Math.round(((bearProb ?? 0) / ((bullProb ?? 0) + (bearProb ?? 0) || 1)) * 100)}%` }}
            />
          </div>
          <div className="flex items-center justify-between mt-2 text-[11px] tabular-nums">
            <span className="text-brand">
              {Math.round(((bullProb ?? 0) / ((bullProb ?? 0) + (bearProb ?? 0) || 1)) * 100)}% bull
            </span>
            <span className="text-content-high">
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
          className="text-border"
          stroke="currentColor"
          strokeWidth={0.5}
        />
      ))}
      {/* Axes */}
      {outer.map(([x, y], i) => (
        <line key={i} x1={cx} y1={cy} x2={x} y2={y}
          className="text-border" stroke="currentColor" strokeWidth={0.5} />
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
      {outer.map((_, i) => {
        const label = dims[i].label.length > 14 ? dims[i].label.slice(0, 14) + '…' : dims[i].label;
        const lx = cx + (r + 20) * Math.cos(angles[i]);
        const ly = cy + (r + 12) * Math.sin(angles[i]);
        return (
          <text key={`l${i}`} x={lx} y={ly}
            textAnchor={Math.abs(Math.cos(angles[i])) < 0.1 ? 'middle' : Math.cos(angles[i]) > 0 ? 'start' : 'end'}
            fontSize={9}
            className="fill-muted-foreground"
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
      <div className="px-4 pt-5 pb-10 space-y-5">
        <LoadingCard label="Research streaming — 14+ source synthesis" minH={200} />
      </div>
    );
  }

  return (
    <div className="px-4 pt-5 pb-10 space-y-5">
      {/* Research complete status card */}
      {hasData && (
        <div className="rounded-lg border border-brand/25 bg-brand/10 shadow-sm p-4 flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-card border border-brand/25 flex items-center justify-center shrink-0">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="hsl(var(--brand))" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold text-foreground">Research complete</div>
            <div className="text-[11px] text-muted-foreground truncate">
              {sourceCount > 0 && <>{sourceCount} source{sourceCount === 1 ? '' : 's'} · </>}
              Qwen 3.6-plus + Claude Sonnet
            </div>
          </div>
        </div>
      )}

      {/* Sub-tab switcher */}
      {hasData && (
        <div className="flex items-center gap-1 p-1 bg-muted/60 border border-border/60 rounded-lg">
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
                  ? 'bg-card text-foreground shadow-sm border border-border'
                  : 'text-muted-foreground active:text-foreground'}`}
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
        <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70 mb-3">
            Industry Intelligence Brief
          </div>
          <Markdown>{industryBrief}</Markdown>
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
        <p className="text-[11px] text-muted-foreground/70 text-center">
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
      <div className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-surface-2 animate-pulse" />
          <span className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground">
            Research streaming · {ticker}
          </span>
        </div>
        <p className="text-[12px] text-muted-foreground leading-relaxed">
          Industry brief and deep research are populating live. The AI summary card appears once synthesis completes.
        </p>
      </div>

      {industryBrief && (
        <div className="border border-border rounded-lg overflow-hidden bg-card">
          <button
            onClick={() => setBriefOpen(o => !o)}
            className="w-full flex items-center justify-between px-4 py-3 text-[13px] font-medium text-foreground active:bg-muted/60"
          >
            <span>Industry Intelligence Brief</span>
            <span className="text-[11px] text-muted-foreground/70">{briefOpen ? '▲' : '▼'}</span>
          </button>
          {briefOpen && (
            <Markdown>{industryBrief}</Markdown>
          )}
        </div>
      )}

      {deepResearch && (
        <div className="border border-border rounded-lg overflow-hidden bg-card">
          <button
            onClick={() => setDeepOpen(o => !o)}
            className="w-full flex items-center justify-between px-4 py-3 text-[13px] font-medium text-foreground active:bg-muted/60"
          >
            <span>Deep Research</span>
            <span className="text-[11px] text-muted-foreground/70">{deepOpen ? '▲' : '▼'}</span>
          </button>
          {deepOpen && (
            <Markdown>{deepResearch}</Markdown>
          )}
        </div>
      )}
    </div>
  );
}

/* ───────── Financials Tab — Revenue Build + Income Statement + Key Stats ─── */
function FinancialsBody({
  ticker, stockMetrics, statements,
}: {
  ticker: string;
  stockMetrics: Record<string, number | undefined> | null;
  statements?: FinancialStatementsPayload;
}) {
  return (
    <div className="px-4 pt-5 pb-10 space-y-5">
      {/* Three statements with derived YoY growth, profile-aware layout */}
      <FinancialStatements statements={statements} ticker={ticker} />

      {/* Revenue Build — FMP product segmentation (LTM fiscal year) */}
      <V2RevenueBuild ticker={ticker} kind="product" />

      {/* Revenue Build — FMP geographic segmentation (LTM fiscal year) */}
      <V2RevenueBuild ticker={ticker} kind="geo" />

      {/* Income Statement — wrap FinancialsChart in a token-driven card shell
          matching Key Stats / Valuation cards. The inner FinancialsChart
          has its own surface; the `v2-dark-card` wrapper overrides it. */}
      <div className="v2-dark-card rounded-lg border border-border bg-card shadow-sm overflow-hidden">
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

/* ───────── Revenue Build (product or geographic) ─────────────────────────
   Fetches FMP /stable/revenue-{product|geographic}-segmentation via the
   backend, shows each segment with its share of total and YoY delta plus a
   share bar. Falls back to an empty-state card when FMP returns no
   segmentation (common for pure service cos, Asian tickers, REITs).
*/
function V2RevenueBuild({ ticker, kind }: { ticker: string; kind: 'product' | 'geo' }) {
  const [data, setData]       = useState<RevenueSegmentation | null>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);
  const [period, setPeriod]   = useState<'annual' | 'quarter'>('annual');

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    setLoading(true);
    setErrored(false);
    const fn = kind === 'product' ? getRevenueProductSegmentation : getRevenueGeoSegmentation;
    fn(ticker, period)
      .then(r => { if (!cancelled) setData(r); })
      .catch(() => { if (!cancelled) setErrored(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker, kind, period]);

  const title    = kind === 'product' ? 'Revenue Build — Product' : 'Revenue Build — Geography';
  const emptyLbl = kind === 'product'
    ? 'Company does not report product-level revenue.'
    : 'Company does not report geographic revenue.';

  const fmtMoney = (v: number) => {
    const abs = Math.abs(v);
    if (abs >= 1e12) return `$${(abs / 1e12).toFixed(2)}T`;
    if (abs >= 1e9)  return `$${(abs / 1e9).toFixed(1)}B`;
    if (abs >= 1e6)  return `$${(abs / 1e6).toFixed(0)}M`;
    return `$${abs.toLocaleString()}`;
  };
  // Truncate long FMP segment names (e.g. "Wearables, Home and Accessories",
  // "Greater China Segment") for the tight 2-col grid on mobile.
  const shortName = (n: string) => {
    const cleaned = n.replace(/\s+Segment$/i, '').trim();
    return cleaned.length > 28 ? cleaned.slice(0, 26) + '…' : cleaned;
  };

  // Segmented toggle — Annual | Quarter. Sits in the header regardless of
  // loading / empty state so the user can always switch.
  const toggle = (
    <div className="flex items-center p-0.5 rounded-md bg-muted/60 border border-border">
      {(['annual', 'quarter'] as const).map(p => (
        <button
          key={p}
          type="button"
          onClick={() => setPeriod(p)}
          className={`h-5 px-2 text-[10px] font-medium rounded uppercase tracking-wider transition-colors ${
            period === p
              ? 'bg-card text-foreground shadow-sm'
              : 'text-muted-foreground active:text-foreground'
          }`}
        >
          {p === 'annual' ? 'Annual' : 'Quarter'}
        </button>
      ))}
    </div>
  );

  const card = (body: React.ReactNode) => (
    <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
          {title}
        </span>
        {toggle}
      </div>
      {body}
    </div>
  );

  if (loading) {
    return card(
      <div className="flex items-center gap-2 text-[12px] text-muted-foreground/70">
        <div className="w-3 h-3 rounded-full border-2 border-border border-t-brand animate-spin" />
        Loading segmentation…
      </div>
    );
  }
  if (errored || !data) {
    return card(
      <p className="text-[12px] text-muted-foreground/70 leading-relaxed">
        Segment data unavailable.
      </p>
    );
  }
  if (!data.segments.length) {
    return card(
      <p className="text-[12px] text-muted-foreground/70 leading-relaxed">
        {emptyLbl}
      </p>
    );
  }

  const periodLabel = data.fiscal_year
    ? `FY${data.fiscal_year}${data.period && data.period !== 'FY' ? ` · ${data.period}` : ''}`
    : '';
  const currency = data.currency || 'USD';

  return card(
    <>
      <div className="grid grid-cols-2 gap-2 mb-3">
        {data.segments.slice(0, 6).map(s => {
          const pctLabel = s.pct != null ? `${s.pct.toFixed(1)}%` : '—';
          const yoy = s.yoy_pct;
          return (
            <div key={s.name} className="p-2.5 rounded-lg border border-border/60 bg-muted/40">
              <div className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground truncate" title={s.name}>
                {shortName(s.name)}
              </div>
              <div className="text-[14px] font-semibold tabular-nums text-foreground mt-1">
                {fmtMoney(s.revenue)}
              </div>
              <div className="flex items-baseline gap-2 mt-0.5">
                <span className="text-[10px] text-muted-foreground tabular-nums">{pctLabel}</span>
                {yoy != null && (
                  <span className={`text-[10px] font-medium tabular-nums ${yoy >= 0 ? 'text-brand' : 'text-content-high'}`}>
                    {yoy >= 0 ? '+' : ''}{yoy.toFixed(1)}%
                  </span>
                )}
              </div>
              {/* Share bar */}
              <div className="mt-1.5 h-1 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full bg-brand"
                  style={{ width: `${Math.max(2, Math.min(100, s.pct ?? 0))}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex items-center justify-between text-[10.5px] text-muted-foreground/70">
        <span>{periodLabel}</span>
        <span className="tabular-nums">
          Total {data.total_revenue != null ? `${fmtMoney(data.total_revenue)} ${currency}` : '—'}
        </span>
      </div>
    </>
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
    <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
            {hoverIdx != null ? displayDateLabel : `Price · ${tf.label}`}
          </div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-[22px] font-semibold tracking-tight tabular-nums text-foreground leading-none">
              ${displayPrice.toFixed(2)}
            </span>
            <span className={`text-[12px] font-medium tabular-nums ${periodDelta >= 0 ? 'text-gain' : 'text-loss'}`}>
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
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted/60 text-muted-foreground active:bg-muted'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="h-[180px] flex items-center justify-center gap-2 text-muted-foreground/70">
          <LoadingSpinner size={14} /><span className="text-[12px]">Loading chart…</span>
        </div>
      ) : points.length === 0 ? (
        <div className="h-[180px] flex items-center justify-center text-[12px] text-muted-foreground/70">
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
          <g className="text-border/70">
            {yVals.map((v, i) => (
              <line key={i} x1={padL} y1={yFor(v)} x2={w - padR} y2={yFor(v)}
                    stroke="currentColor" strokeWidth={0.6} strokeDasharray="2,3"/>
            ))}
          </g>
          <g className="fill-muted-foreground/70">
            {yVals.map((v, i) => (
              <text key={i} x={padL - 4} y={yFor(v) + 3} textAnchor="end" fontSize={9}>${v.toFixed(0)}</text>
            ))}
          </g>
          <path d={areaD} fill="url(#v2-stock-g)"/>
          <path d={pathD} fill="none" stroke={BRAND} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round"/>
          {hoverIdx != null && points[hoverIdx] != null && (
            <g>
              <line x1={xFor(hoverIdx)} y1={padT} x2={xFor(hoverIdx)} y2={padT + chartH}
                    className="text-muted-foreground/70" stroke="currentColor" strokeWidth={0.8}/>
              <circle cx={xFor(hoverIdx)} cy={yFor(points[hoverIdx])} r={3.5} fill={BRAND}
                      className="stroke-card" strokeWidth={1.5}/>
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
  // Per-share price — 52wk/day high/low are raw dollar levels, not aggregates
  const fmtPrice = (v: number | undefined) => {
    if (v == null) return '—';
    return `$${v.toLocaleString(undefined, {
      minimumFractionDigits: v < 10 ? 2 : 0,
      maximumFractionDigits: v < 10 ? 2 : 0,
    })}`;
  };
  const fmtVolume = (v: number | undefined) => {
    if (v == null) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
    return v.toLocaleString();
  };

  // 20-cell grid (extended from 14 — see app/backend/routes/analysis.py
  // get_stock_data for the new P/B, EPS, Div Yield, Day High/Low, Volume
  // fields). Same field set/order as desktop's StockPanel for consistency.
  // For US tickers, valuation + profitability rows prefer FMP TTM values
  // (/key-metrics-ttm + /ratios-ttm) over yfinance info which can lag by a
  // quarter. ROIC falls back to ROA if neither FMP nor HK path provided a
  // value (handled backend-side).
  const rows: { k: string; v: string }[] = [
    { k: 'Market cap', v: fmtMoney(metrics.market_cap) },
    { k: 'Rev TTM',    v: fmtMoney(metrics.revenue) },
    { k: 'FCF',        v: fmtMoney(metrics.free_cash_flow) },
    { k: 'Net margin', v: fmtPct(metrics.net_margin) },
    { k: 'P/E',        v: fmtMult(metrics.pe_ratio) },
    { k: 'P/S',        v: fmtMult(metrics.price_to_sales) },
    { k: 'P/B',        v: fmtMult(metrics.price_to_book) },
    { k: 'EV/EBITDA',  v: fmtMult(metrics.ev_to_ebitda) },
    { k: 'Rev growth', v: fmtPct(metrics.revenue_growth) },
    { k: 'EPS ttm',    v: fmtPrice(metrics.eps_ttm) },
    { k: 'ROE',        v: fmtPct(metrics.return_on_equity) },
    { k: 'ROIC',       v: fmtPct(metrics.return_on_invested_capital ?? metrics.return_on_assets) },
    { k: 'FCF yield',  v: fmtPct(metrics.free_cash_flow_yield) },
    { k: 'Div yield',  v: fmtPct(metrics.dividend_yield) },
    { k: 'Net cash',   v: fmtMoney(metrics.net_cash) },
    { k: '52wk high',  v: fmtPrice(metrics.fifty_two_week_high) },
    { k: '52wk low',   v: fmtPrice(metrics.fifty_two_week_low) },
    { k: 'Day high',   v: fmtPrice(metrics.day_high) },
    { k: 'Day low',    v: fmtPrice(metrics.day_low) },
    { k: 'Volume',     v: fmtVolume(metrics.volume) },
  ];

  return (
    <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] p-5">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70 mb-3">
        Key Stats
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-2.5">
        {rows.map((r) => (
          <div key={r.k} className="flex items-baseline justify-between">
            <span className="text-[11.5px] text-muted-foreground">{r.k}</span>
            <span className={`text-[13px] font-semibold tabular-nums ${
              r.v.startsWith('+') ? 'text-brand'
              : r.v.startsWith('-') && r.v !== '—' ? 'text-content-high'
              : 'text-foreground'
            }`}>
              {r.v}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

