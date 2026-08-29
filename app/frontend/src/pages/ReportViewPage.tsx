import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { getRunResult } from '@/lib/api';
import { extractLatestFinancials, isBiopharmaSector, isTechSector, classifyTechSubtype } from '@/lib/utils';
import type { RunResult } from '@/lib/reportTypes';
import { useLayoutMode } from '@/contexts/layout-mode-context';
// MobileBottomNav removed — hamburger menu in MobileTopBar replaces bottom tabs
import { V2ReportView } from '@/components/v2/V2ReportView';

import { ReportHeader } from '@/components/report/ReportHeader';
import { CardAuditBanner } from '@/components/report/CardAuditBanner';
import { PowerLawRadar } from '@/components/report/PowerLawRadar';
import { ValueTrapChecklist } from '@/components/report/ValueTrapChecklist';
import { DecisionInputsCard } from '@/components/report/DecisionInputsCard';
import { AssumptionWatchCard } from '@/components/report/AssumptionWatchCard';
import { IntelligenceGrid } from '@/components/report/IntelligenceGrid';
import { FinancialsChart } from '@/components/report/FinancialsChart';
import { SectorValuationCard } from '@/components/report/SectorValuationCard';
import type { SectorCardPayload } from '@/lib/reportTypes';
import { FinancialStatements } from '@/components/report/FinancialStatements';
import type { FinancialStatementsPayload } from '@/components/report/FinancialStatements';
import { ValuationLadder } from '@/components/report/ValuationLadder';
import { DcfMethodologyPanel } from '@/components/report/DcfMethodologyPanel';
import { REITValuationPanel } from '@/components/report/reit/REITValuationPanel';
import { BankValuationPanel } from '@/components/report/bank/BankValuationPanel';
import { BiopharmaValuationPanel } from '@/components/report/biopharma/BiopharmaValuationPanel';
import { TechValuationPanel } from '@/components/report/tech/TechValuationPanel';
import { SotpAnalystPanel } from '@/components/report/SotpAnalystPanel';
import { CitationPanel } from '@/components/report/CitationPanel';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { IndustryBriefPanel } from '@/components/report/IndustryBriefPanel';
import { DeepResearchPanel } from '@/components/report/DeepResearchPanel';
import { StockPanel } from '@/components/report/StockPanel';
import { PriceTargetPanel } from '@/components/report/PriceTargetPanel';
import { NewsPanel } from '@/components/report/NewsPanel';
import { PriorReportCard } from '@/components/report/PriorReportCard';

// Mirrors the mobile tab order in V2ReportView so the two render paths present
// the same information architecture.
const SECTIONS = [
  { id: 'summary',    label: 'Summary'    },
  { id: 'valuation',  label: 'Valuation'  },
  { id: 'decision',   label: 'Decision'   },
  { id: 'risk',       label: 'Risk'       },
  { id: 'research',   label: 'Research'   },
  { id: 'financials', label: 'Financials' },
] as const;

function scrollTo(id: string) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * Desktop card grid.
 *
 * `auto-fit` + `minmax` rather than a fixed `grid-cols-2`: several cards on
 * this page render null depending on the run (AssumptionWatchCard when the
 * steward is unflagged, SOTP without an analyst breakdown), and a fixed track
 * count leaves an empty column behind them. auto-fit drops the empty track, so
 * a lone survivor goes full width on its own.
 *
 * Cards keep their natural height (`items-start`). Stretching them to a common
 * row height was tried and measured worse: Value Trap Audit has three collapsed
 * rows and stretching it to match Power Law's radar produced a 511px card whose
 * content ended at 161px -- 349px of empty bordered box, which reads as broken
 * rather than tidy. A gap between cards is page background; a gap inside a card
 * is a defect. Only stretch a pair whose content volumes genuinely match.
 */
const CARD_GRID =
  'grid gap-5 items-start [grid-template-columns:repeat(auto-fit,minmax(420px,1fr))]';

function SectionAnchor({ id, label, badge }: { id: string; label: string; badge?: React.ReactNode }) {
  return (
    <div id={id} className="scroll-mt-28">
      <div className="flex items-center gap-3 mb-6 pt-12">
        <div className="h-px w-6 bg-border shrink-0" />
        <span className="text-xs font-bold uppercase tracking-[0.14em] text-foreground/40 whitespace-nowrap flex items-center gap-1.5">
          {label}
          {badge}
        </span>
        <div className="h-px flex-1 bg-border" />
      </div>
    </div>
  );
}

export function ReportViewPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [result, setResult] = useState<RunResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<string>('valuation');
  const observerRef = useRef<IntersectionObserver | null>(null);
  const { mode } = useLayoutMode();

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    getRunResult(runId)
      .then(setResult)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runId]);

  // Highlight the nav item for the section currently in view
  useEffect(() => {
    if (loading || !result) return;
    observerRef.current?.disconnect();
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) setActiveSection(visible[0].target.id);
      },
      { rootMargin: '-10% 0px -70% 0px', threshold: 0 },
    );
    SECTIONS.forEach(s => {
      const el = document.getElementById(s.id);
      if (el) obs.observe(el);
    });
    observerRef.current = obs;
    return () => obs.disconnect();
  }, [loading, result]);

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <p className="text-muted-foreground">Loading report…</p>
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="min-h-screen bg-background">
        <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
          <p className="text-content-high">{error ?? 'Run not found.'}</p>
          <Button onClick={() => navigate('/report')}>New Analysis</Button>
        </div>
      </div>
    );
  }

  const ticker = result.ticker;
  const data = result.data ?? {};
  const decisions = result.decisions ?? {};
  const decision = decisions[ticker];
  // VGPM may also be embedded in data.vgpm (pipeline emits it to partial_data after Phase 7)
  const vgpmMap = (result.vgpm ?? (data.vgpm as Record<string, import('@/lib/reportTypes').VgpmResult> | undefined));
  const vgpm = vgpmMap?.[ticker];

  const regime = data.macro_regime;
  const routingDecision = data.routing_decision as Record<string, unknown> | undefined;
  const routing = (routingDecision as Record<string, { sector?: string; raw_financials?: Record<string, unknown> }> | undefined)?.[ticker];
  const sector = routing?.sector ?? (data.sector as string | undefined);
  const subSector = (routingDecision as { specialist_block?: string } | undefined)?.specialist_block;

  const agentSignals = data.analyst_signals as import('@/lib/reportTypes').AgentSignals | undefined;
  const scenarioAnalysis = (data.scenario_analysis as Record<string, import('@/lib/reportTypes').ScenarioAnalysis> | undefined)?.[ticker];
  const powerLaw = (data.power_law_analysis as Record<string, import('@/lib/reportTypes').PowerLawAnalysis> | undefined)?.[ticker];
  const valueTrap = (data.value_trap_analysis as Record<string, import('@/lib/reportTypes').ValueTrapAnalysis> | undefined)?.[ticker];
  const dcfRange = (data.dcf_range as Record<string, import('@/lib/reportTypes').DcfRange> | undefined)?.[ticker];
  const dcfSkipReason = (data.dcf_skip_reasons as Record<string, string> | undefined)?.[ticker];
  const industryBrief = data.industry_brief as string | undefined;

  // Deep research + citations
  // Pipeline writes state["data"]["deep_research"] (not "deep_research_report")
  const deepResearchReport    = (data.deep_research ?? data.deep_research_report) as string | undefined;
  const deepResearchAnnotated = data.deep_research_annotated as string | undefined;
  const citationRegistry      = data.citation_registry as import('@/lib/reportTypes').CitationRegistryEntry[] | undefined;

  const currentPrice = scenarioAnalysis?.current_price;

  // Mobile layout — reimagined v2 tab view (Summary/Valuation/Decision/Risk/Research/Financials)
  if (mode === 'mobile') {
    return (
      <V2ReportView
        result={result}
        runId={runId!}
        isRunning={false}
        isComplete={true}
        phaseMap={{}}
        progressPct={100}
        events={[]}
        liveData={{}}
      />
    );
  }

  // Sector-specific valuation panel (REIT / bank / biopharma / tech), or
  // null when no profile matched. Hoisted out of the JSX so the layout
  // below can tell whether a dense full-width panel exists -- when it does
  // not, the generic ladder pairs with the price target instead.
  const sectorValuationPanel = dcfRange?.reit_breakdown ? (
              <REITValuationPanel
                dcfRange={dcfRange}
                currentPrice={currentPrice}
                ticker={ticker}
              />
            ) : dcfRange?.bank_breakdown ? (
              <BankValuationPanel
                dcfRange={dcfRange}
                currentPrice={currentPrice}
                ticker={ticker}
              />
            ) : isBiopharmaSector(sector) ? (() => {
              const _fin = extractLatestFinancials(data.raw_financials as Record<string, unknown> | undefined);
              return (
                <BiopharmaValuationPanel
                  dcfRange={dcfRange}
                  currentPrice={currentPrice}
                  ticker={ticker}
                  pipelineAssets={(data.pipeline_assets as Record<string, import('@/lib/reportTypes').BiopharmaPipelineAsset[]> | undefined)?.[ticker]}
                  sections={data.deep_research_sections as Record<string, string> | undefined}
                  rd_spend={_fin.rd_spend}
                  revenue={_fin.revenue}
                  fcf={_fin.fcf}
                />
              );
            })()
            /* Tech sub-type routing — uses classifyTechSubtype so historical    */
            /* runs missing profile_name in stored data still render the correct */
            /* panel via a ticker-table fallback (e.g. SNOW → growth_saas).      */
            : (isTechSector(sector) && classifyTechSubtype(
                 (data.profile_names as Record<string, string> | undefined)?.[ticker]
                 ?? (data.profile_name as string | undefined),
                 ticker
               ) !== null) ? (
              <TechValuationPanel
                dcfRange={dcfRange}
                currentPrice={currentPrice}
                ticker={ticker}
                profile={
                  (data.profile_names as Record<string, string> | undefined)?.[ticker]
                  ?? (data.profile_name as string | undefined)
                }
                sections={data.deep_research_sections as Record<string, string> | undefined}
                rawFinancials={data.raw_financials as Record<string, unknown> | undefined}
                saasMetrics={
                  (data.saas_metrics as Record<string, import('@/lib/reportTypes').SaasMetrics> | undefined)?.[ticker]
                }
              />
  ) : null;

  return (
    <div className="min-h-screen bg-background">
      {/* ── Sticky section nav ─────────────────────────────────────────────── */}
      <div className="sticky top-0 z-20 bg-background/95 backdrop-blur border-b">
        <div className="max-w-6xl mx-auto px-4 md:px-8">
          <div className="flex items-center justify-center gap-2 py-2">
            {SECTIONS.map(s => (
              <button
                key={s.id}
                onClick={() => scrollTo(s.id)}
                className={`text-[15px] px-4 h-8 rounded-md shrink-0 transition-colors font-medium
                  ${activeSection === s.id
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Page content ───────────────────────────────────────────────────── */}
      <div className="max-w-6xl mx-auto px-4 md:px-8 pt-4 md:pt-8 pb-12 md:pb-16 space-y-6">

        {/* ── Summary ────────────────────────────────────────────────────── */}
        <div id="summary" className="scroll-mt-28" />
        {/* Card QA Banner — surfaces self-healing audit findings */}
        <CardAuditBanner
          audit={(data as { card_qa_audit?: Record<string, import('@/lib/reportTypes').DdCardAudit> }).card_qa_audit?.[ticker]}
          ticker={ticker}
        />
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-5 items-stretch">
          <ReportHeader
            ticker={ticker}
            runAt={result.run_at}
            modelName={result.model_name}
            decision={decision}
            regime={regime}
            currentPrice={currentPrice}
            sector={sector}
            subSector={subSector}
            vgpm={vgpm}
          />
          <StockPanel ticker={ticker} />
        </div>

        {/* ── M1 recency — what the last report said + what changed since ── */}
        <PriorReportCard
          prior={data.prior_recap?.[ticker]}
          delta={data.freshness_delta?.[ticker]}
          ticker={ticker}
        />

        {/* ── Valuation ──────────────────────────────────────────────────── */}
        {/* REIT branch: when dcfRange.reit_breakdown is populated (backend    */}
        {/* emits for RealEstate / REIT sectors), render REITValuationPanel    */}
        {/* in place of the generic DCF ladder. Price Target + Scenario Chart */}
        {/* work for REITs too, so they render unconditionally.                */}
        <SectionAnchor id="valuation" label="Valuation" />
        {/* Sparse hero cards share a split row; the ladder only exists
            when no sector panel did, so auto-fit gives the price target
            the full width on tickers that have one. */}
        <div className={CARD_GRID}>
          <PriceTargetPanel
            dcfRange={dcfRange}
            scenario={scenarioAnalysis}
            decision={decision}
            ticker={ticker}
          />
          {!sectorValuationPanel && (
            <ValuationLadder dcfRange={dcfRange} currentPrice={currentPrice} ticker={ticker} />
          )}
        </div>

        {/* Dense, table-bearing panels take the full row: a 6-column
            scenario table was being squeezed into a 438px column, which is
            why its headers wrapped to three lines. */}
        {sectorValuationPanel}

        {/* GS-style SOTP report card (task #28) - present only when the DCF
            engine ran with SOTP (analyst) assumptions: business-unit
            breakdown, NAV bridge, multiple basis, scenario TPs. */}
        {dcfRange?.sotp_breakdown && (
          <SotpAnalystPanel breakdown={dcfRange.sotp_breakdown} />
        )}

        {/* Full width: this is the 6-column scenario table that was being
            squeezed into a 403px column, wrapping its headers onto three
            lines. */}
        <DcfMethodologyPanel dcfRange={dcfRange} ticker={ticker} skipReason={dcfSkipReason} />

        {/* Sector Valuation Card. Mounted here as well as in V2ReportView:
            the mobile view bypasses this JSX entirely, so a card added only
            there is invisible on desktop. Full width so its KPI tiles and
            adjustment bridge stop being crushed into a narrow column. */}
        {(data.sector_card as Record<string, SectorCardPayload> | undefined)?.[ticker]
          && <SectorValuationCard
               payload={(data.sector_card as Record<string, SectorCardPayload>)[ticker]} />}

        {/* ── Decision ───────────────────────────────────────────────────── */}
        <SectionAnchor id="decision" label="Decision" />
        <div className={CARD_GRID}>
          {/* Decision-inputs card (M2 D3) — replaces the investor persona
              panel retired with the committee; shows what the PM decided
              from. Historical runs without decision_inputs render the
              card's "not available" state. */}
          <DecisionInputsCard decisionInputs={decision?.decision_inputs} ticker={ticker} />
          {/* R3 Assumption Watch — the steward's live view of open
              challenges / variant drivers for this ticker. Fetches its own
              endpoint; renders nothing when disabled or unflagged. */}
          <AssumptionWatchCard ticker={ticker} />
        </div>

        {/* ── Risk ───────────────────────────────────────────────────────── */}
        <SectionAnchor id="risk" label="Risk" />
        {/* Natural heights: see the CARD_GRID note -- stretching this pair
            fills Value Trap with ~349px of empty card. */}
        <div className={CARD_GRID}>
          <PowerLawRadar powerLaw={powerLaw} ticker={ticker} />
          <ValueTrapChecklist analysis={valueTrap} ticker={ticker} />
        </div>

        {/* ── Research ─────────────────────────────────────── */}
        <SectionAnchor id="research" label="Research" />
        <ResearchSummaryPanel
          runId={runId!}
          ticker={ticker}
          industryBrief={industryBrief}
          deepResearch={deepResearchReport}
          industryBriefContent={industryBrief
            ? <IndustryBriefPanel industryBrief={industryBrief} sector={sector} />
            : undefined}
          deepResearchContent={deepResearchReport
            ? <DeepResearchPanel
                reportText={deepResearchReport}
                annotatedText={deepResearchAnnotated}
                registry={citationRegistry}
                ticker={ticker}
              />
            : undefined}
        />
        <IntelligenceGrid
          agentSignals={agentSignals}
          pipelineData={data as Record<string, unknown>}
          ticker={ticker}
        />
        <NewsPanel ticker={ticker} />

        {/* ── Financials ─────────────────────────────────────────────────── */}
        <SectionAnchor id="financials" label="Financials" />
        <FinancialStatements
          statements={data.financial_statements as FinancialStatementsPayload | undefined}
          ticker={ticker}
        />
        <FinancialsChart ticker={ticker} />
        <CitationPanel data={data as Record<string, unknown>} ticker={ticker} />

      </div>
    </div>
  );
}
