import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { getRunResult } from '@/lib/api';
import { extractLatestFinancials, isBiopharmaSector, isTechSector, classifyTechSubtype } from '@/lib/utils';
import type { RunResult } from '@/lib/reportTypes';
import { useIsMobile } from '@/hooks/use-mobile';
// MobileBottomNav removed — hamburger menu in MobileTopBar replaces bottom tabs
import { V2ReportView } from '@/components/v2/V2ReportView';

import { ResearchNav } from '@/components/layout/ResearchNav';
import { ReportHeader } from '@/components/report/ReportHeader';
import { ScenarioChart } from '@/components/report/ScenarioChart';
import { PowerLawRadar } from '@/components/report/PowerLawRadar';
import { ValueTrapChecklist } from '@/components/report/ValueTrapChecklist';
import { AgentSignalsPanel } from '@/components/report/AgentSignalsPanel';
import { IntelligenceGrid } from '@/components/report/IntelligenceGrid';
import { FinancialsChart } from '@/components/report/FinancialsChart';
import { ValuationLadder } from '@/components/report/ValuationLadder';
import { REITValuationPanel } from '@/components/report/reit/REITValuationPanel';
import { BankValuationPanel } from '@/components/report/bank/BankValuationPanel';
import { BiopharmaValuationPanel } from '@/components/report/biopharma/BiopharmaValuationPanel';
import { TechValuationPanel } from '@/components/report/tech/TechValuationPanel';
import { DebatePanel } from '@/components/report/DebatePanel';
import { CitationPanel } from '@/components/report/CitationPanel';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { IndustryBriefPanel } from '@/components/report/IndustryBriefPanel';
import { DeepResearchPanel } from '@/components/report/DeepResearchPanel';
import { StockPanel } from '@/components/report/StockPanel';
import { PriceTargetPanel } from '@/components/report/PriceTargetPanel';
import { NewsPanel } from '@/components/report/NewsPanel';

const SECTIONS = [
  { id: 'summary',       label: 'Summary'    },
  { id: 'valuation',     label: 'Valuation'  },
  { id: 'analysis',      label: 'Analysis'   },
  { id: 'financials',    label: 'Financials' },
] as const;

function scrollTo(id: string) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function SectionAnchor({ id, label, badge }: { id: string; label: string; badge?: React.ReactNode }) {
  return (
    <div id={id} className="scroll-mt-28">
      <div className="flex items-center gap-3 mb-4 pt-10">
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
  const isMobile = useIsMobile();

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
        <ResearchNav />
        <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
          <p className="text-red-500">{error ?? 'Run not found.'}</p>
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
  const debateResult = data.debate_result as import('@/lib/reportTypes').DebateResult | undefined;
  const scenarioAnalysis = (data.scenario_analysis as Record<string, import('@/lib/reportTypes').ScenarioAnalysis> | undefined)?.[ticker];
  const powerLaw = (data.power_law_analysis as Record<string, import('@/lib/reportTypes').PowerLawAnalysis> | undefined)?.[ticker];
  const valueTrap = (data.value_trap_analysis as Record<string, import('@/lib/reportTypes').ValueTrapAnalysis> | undefined)?.[ticker];
  const dcfRange = (data.dcf_range as Record<string, import('@/lib/reportTypes').DcfRange> | undefined)?.[ticker];
  const industryBrief = data.industry_brief as string | undefined;

  // Deep research + citations
  // Pipeline writes state["data"]["deep_research"] (not "deep_research_report")
  const deepResearchReport    = (data.deep_research ?? data.deep_research_report) as string | undefined;
  const deepResearchAnnotated = data.deep_research_annotated as string | undefined;
  const citationRegistry      = data.citation_registry as import('@/lib/reportTypes').CitationRegistryEntry[] | undefined;

  const currentPrice = scenarioAnalysis?.current_price;

  // Mobile layout — reimagined v2 tab view (Summary/Valuation/Investors/Risk/Research/Financials)
  if (isMobile) {
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

  return (
    <div className="min-h-screen bg-background">
      <ResearchNav />
      {/* ── Sticky section nav ─────────────────────────────────────────────── */}
      <div className="sticky top-[57px] z-20 bg-background/95 backdrop-blur border-b">
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
      <div className="max-w-6xl mx-auto p-4 md:p-8 space-y-2">

        {/* ── Summary ────────────────────────────────────────────────────── */}
        <div id="summary" className="scroll-mt-28" />
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4 items-stretch">
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

        {/* ── Valuation ──────────────────────────────────────────────────── */}
        {/* REIT branch: when dcfRange.reit_breakdown is populated (backend    */}
        {/* emits for RealEstate / REIT sectors), render REITValuationPanel    */}
        {/* in place of the generic DCF ladder. Price Target + Scenario Chart */}
        {/* work for REITs too, so they render unconditionally.                */}
        <SectionAnchor id="valuation" label="Valuation" />
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4 items-start">
          <div className="flex flex-col gap-4">
            <PriceTargetPanel
              dcfRange={dcfRange}
              scenario={scenarioAnalysis}
              decision={decision}
              ticker={ticker}
            />
            <ScenarioChart scenario={scenarioAnalysis} ticker={ticker} />
            {dcfRange?.reit_breakdown ? (
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
            ) : (
              <ValuationLadder dcfRange={dcfRange} currentPrice={currentPrice} ticker={ticker} />
            )}
          </div>
          <div className="flex flex-col gap-2">
            <PowerLawRadar powerLaw={powerLaw} ticker={ticker} />
            <ValueTrapChecklist analysis={valueTrap} ticker={ticker} />
            <NewsPanel ticker={ticker} />
          </div>
        </div>

        <AgentSignalsPanel agentSignals={agentSignals} ticker={ticker} />

        {/* ── Analysis (anchored to Industry Brief) ──────────────────────── */}
        <SectionAnchor id="analysis" label="Analysis" />
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
        <DebatePanel debateResult={debateResult} ticker={ticker} />

        {/* ── Financials ─────────────────────────────────────────────────── */}
        <SectionAnchor id="financials" label="Financials" />
        <FinancialsChart ticker={ticker} />
        <CitationPanel data={data as Record<string, unknown>} ticker={ticker} />

      </div>
    </div>
  );
}
