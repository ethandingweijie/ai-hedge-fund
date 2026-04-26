import { useEffect, useState, useCallback } from 'react';
import type { RunResult, VgpmResult, AgentSignals, DebateResult, ScenarioAnalysis, PowerLawAnalysis, ValueTrapAnalysis, DcfRange, CitationRegistryEntry, SectorCardPayload } from '@/lib/reportTypes';
import { getStockData } from '@/lib/api';
import { gradeColorClass } from '@/lib/gradeColors';

import { MobileTickerHeader } from './MobileTickerHeader';
import { MobileChartStrip } from './MobileChartStrip';
import { MobileKeyStats } from './MobileKeyStats';
import { MobileSectionTabs } from './MobileSectionTabs';
import { MobileContentCard } from './MobileContentCard';
import { MobileMoreSheet } from './MobileMoreSheet';

// Report panel components
import { ScenarioChart } from '@/components/report/ScenarioChart';
import { PowerLawRadar } from '@/components/report/PowerLawRadar';
import { ValueTrapChecklist } from '@/components/report/ValueTrapChecklist';
import { AgentSignalsPanel } from '@/components/report/AgentSignalsPanel';
import { IntelligenceGrid } from '@/components/report/IntelligenceGrid';
import { FinancialsChart } from '@/components/report/FinancialsChart';
import { ValuationLadder } from '@/components/report/ValuationLadder';
import { SectorValuationCard } from '@/components/report/SectorValuationCard';
import { DebatePanel } from '@/components/report/DebatePanel';
import { CitationPanel } from '@/components/report/CitationPanel';
import { ResearchSummaryPanel } from '@/components/report/ResearchSummaryPanel';
import { IndustryBriefPanel } from '@/components/report/IndustryBriefPanel';
import { DeepResearchPanel } from '@/components/report/DeepResearchPanel';
import { MobilePriceTarget } from './MobilePriceTarget';
import { NewsPanel } from '@/components/report/NewsPanel';

const SECTIONS = [
  { id: 'summary',    label: 'Summary' },
  { id: 'analysis',   label: 'Analysis' },
  { id: 'valuation',  label: 'Valuation' },
  { id: 'financials', label: 'Financials' },
] as const;

interface MobileReportViewProps {
  result: RunResult;
  runId: string;
}

export function MobileReportView({ result, runId }: MobileReportViewProps) {
  const [activeSection, setActiveSection] = useState('summary');
  const [metrics, setMetrics] = useState<Record<string, number | undefined> | null>(null);
  const [livePrice, setLivePrice] = useState<number | undefined>();
  const [priceChange, setPriceChange] = useState<number | undefined>();

  const ticker = result.ticker;
  const data = result.data ?? {};
  const decisions = result.decisions ?? {};
  const decision = decisions[ticker];
  const vgpmMap = (result.vgpm ?? (data.vgpm as Record<string, VgpmResult> | undefined));
  const vgpm = vgpmMap?.[ticker];

  const regime = data.macro_regime as import('@/lib/reportTypes').MacroRegime | undefined;
  const routingDecision = data.routing_decision as Record<string, unknown> | undefined;
  const routing = (routingDecision as Record<string, { sector?: string }> | undefined)?.[ticker];
  const sector = routing?.sector ?? (data.sector as string | undefined);
  const subSector = (routingDecision as { specialist_block?: string } | undefined)?.specialist_block;

  const agentSignals = data.analyst_signals as AgentSignals | undefined;
  const debateResult = data.debate_result as DebateResult | undefined;
  const scenarioAnalysis = (data.scenario_analysis as Record<string, ScenarioAnalysis> | undefined)?.[ticker];
  const powerLaw = (data.power_law_analysis as Record<string, PowerLawAnalysis> | undefined)?.[ticker];
  const valueTrap = (data.value_trap_analysis as Record<string, ValueTrapAnalysis> | undefined)?.[ticker];
  const dcfRange = (data.dcf_range as Record<string, DcfRange> | undefined)?.[ticker];
  // Sector-specific valuation card payload (Option B). Absent for legacy
  // sub-profiles (SaaS / REIT / Biopharma) — those keep their bespoke cards.
  const sectorCard = (data.sector_card as Record<string, SectorCardPayload> | undefined)?.[ticker];
  const industryBrief = data.industry_brief as string | undefined;
  const deepResearchReport = (data.deep_research ?? data.deep_research_report) as string | undefined;
  const deepResearchAnnotated = data.deep_research_annotated as string | undefined;
  const citationRegistry = data.citation_registry as CitationRegistryEntry[] | undefined;
  const currentPrice = scenarioAnalysis?.current_price;

  // Load stock metrics + live price for key stats and header
  const loadMetrics = useCallback(() => {
    getStockData(ticker, '1y')
      .then((d) => {
        setMetrics(d?.metrics ?? null);
        const history = d?.history ?? [];
        if (history.length > 0) {
          // Last close = live price
          setLivePrice(history[history.length - 1].close);
        }
        if (history.length >= 2) {
          const first = history[0].close;
          const last = history[history.length - 1].close;
          setPriceChange(first > 0 ? ((last - first) / first) * 100 : undefined);
        }
      })
      .catch(() => {});
  }, [ticker]);

  useEffect(() => { loadMetrics(); }, [loadMetrics]);

  const scrollToSection = (id: string) => {
    setActiveSection(id);
    const el = document.getElementById(`mobile-${id}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="min-h-screen bg-background pb-20">
      {/* Layer 2: Sticky ticker hero card */}
      <MobileTickerHeader
        ticker={ticker}
        currentPrice={currentPrice ?? livePrice}
        priceChange={priceChange}
        regime={regime}
      />

      {/* Layer 3: Mini chart strip */}
      <MobileChartStrip ticker={ticker} />

      {/* Layer 4: Key stats horizontal scroll */}
      <MobileKeyStats ticker={ticker} metrics={metrics as MobileKeyStatsMetrics} />

      {/* VGPM grades row — full labels */}
      {vgpm && (
        <div className="grid grid-cols-4 gap-2 px-4 py-2 border-b border-border">
          {([
            { key: 'valuation',     label: 'Valuation' },
            { key: 'growth',        label: 'Growth' },
            { key: 'profitability', label: 'Profit.' },
            { key: 'momentum',      label: 'Momentum' },
          ] as const).map(({ key, label }) => {
            const dim = vgpm[key];
            if (!dim) return null;
            return (
              <div key={key} className="flex flex-col items-center gap-0.5">
                <span className="text-[8px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {label}
                </span>
                <span className={`text-base font-bold px-2 py-0.5 rounded-md ${gradeColorClass(dim.grade)}`}>
                  {dim.grade}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* Layer 5: Section tabs + More button */}
      <MobileSectionTabs
        sections={SECTIONS}
        activeSection={activeSection}
        onSectionChange={scrollToSection}
        moreButton={
          <MobileMoreSheet>
            {/* ── Secondary cards inside bottom sheet ── */}
            <MobileContentCard title="Scenarios">
              <div className="pt-3">
                <ScenarioChart scenario={scenarioAnalysis} ticker={ticker} />
              </div>
            </MobileContentCard>

            <MobileContentCard title="Valuation Ladder">
              <div className="pt-3">
                <ValuationLadder dcfRange={dcfRange} currentPrice={currentPrice} ticker={ticker} />
              </div>
            </MobileContentCard>

            <MobileContentCard title="Power Law">
              <div className="pt-3">
                <PowerLawRadar powerLaw={powerLaw} ticker={ticker} />
              </div>
            </MobileContentCard>

            <MobileContentCard title="Value Trap">
              <div className="pt-3">
                <ValueTrapChecklist analysis={valueTrap} ticker={ticker} />
              </div>
            </MobileContentCard>

            <MobileContentCard title="Agent Signals">
              <div className="pt-3">
                <AgentSignalsPanel agentSignals={agentSignals} ticker={ticker} />
              </div>
            </MobileContentCard>

            {debateResult && (
              <MobileContentCard title="Debate">
                <div className="pt-3">
                  <DebatePanel debateResult={debateResult} ticker={ticker} />
                </div>
              </MobileContentCard>
            )}

            <MobileContentCard title="Citations">
              <div className="pt-3">
                <CitationPanel data={data as Record<string, unknown>} ticker={ticker} />
              </div>
            </MobileContentCard>

            <MobileContentCard title="Latest News">
              <div className="pt-3">
                <NewsPanel ticker={ticker} />
              </div>
            </MobileContentCard>
          </MobileMoreSheet>
        }
      />

      {/* Layer 6: Primary content cards only */}
      <div className="px-4 py-3 space-y-3">

        {/* ── Summary ──────────────────────────────────── */}
        <div id="mobile-summary" className="scroll-mt-[120px] space-y-3">
          {decision && (
            <MobileContentCard title="Decision" defaultExpanded priority="high">
              <div className="pt-3 space-y-3">
                <div className="flex items-center gap-3">
                  <span className="text-2xl font-bold">{ticker}</span>
                  <span className={`px-3 py-1 rounded-full text-sm font-semibold ${
                    ({ BUY: 'bg-green-600 text-white', SELL: 'bg-red-600 text-white', SHORT: 'bg-orange-600 text-white', COVER: 'bg-blue-600 text-white', HOLD: 'bg-yellow-600 text-white' } as Record<string, string>)[decision.action] ?? 'bg-muted text-muted-foreground'
                  }`}>{decision.action}</span>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  {decision.position_size_pct != null && (
                    <div>
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Position</p>
                      <p className="text-lg font-bold">{(decision.position_size_pct * 100).toFixed(2)}%</p>
                    </div>
                  )}
                  {decision.price_target != null && decision.price_target > 0 && (
                    <div>
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Target</p>
                      <p className="text-lg font-bold">${decision.price_target.toFixed(2)}</p>
                    </div>
                  )}
                  {currentPrice != null && (
                    <div>
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Current</p>
                      <p className="text-lg font-bold">${currentPrice.toFixed(2)}</p>
                    </div>
                  )}
                </div>

                {decision.price_target != null && currentPrice != null && currentPrice > 0 && (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-muted-foreground uppercase tracking-wider">Upside</span>
                    <span className={`text-sm font-bold ${((decision.price_target - currentPrice) / currentPrice) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                      {(((decision.price_target - currentPrice) / currentPrice) * 100).toFixed(1)}%
                    </span>
                  </div>
                )}

                <p className="text-[10px] text-muted-foreground">
                  Run {result.run_at && !isNaN(new Date(result.run_at).getTime()) ? new Date(result.run_at).toLocaleString() : ''} · {result.model_name ?? 'N/A'}
                </p>

                {decision.rationale && (
                  <p className="text-sm text-muted-foreground leading-relaxed border-t pt-3">
                    {decision.rationale}
                  </p>
                )}

                {(sector || subSector) && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Sector</span>
                    {sector && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-primary/10 text-primary border border-primary/20">{sector}</span>
                    )}
                    {subSector && subSector !== sector && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-muted text-foreground/80 border border-border">{subSector}</span>
                    )}
                  </div>
                )}
              </div>
            </MobileContentCard>
          )}
        </div>

        {/* ── Analysis ─────────────────────────────────── */}
        <div id="mobile-analysis" className="scroll-mt-[120px] space-y-3">
          {/* Industry Intelligence first — auto-expanded (LLM summary) */}
          {(industryBrief || deepResearchReport) && runId && (
            <MobileContentCard title="Industry Intelligence" priority="medium" defaultExpanded>
              <div className="pt-3">
                <ResearchSummaryPanel
                  runId={runId}
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
              </div>
            </MobileContentCard>
          )}

          {/* Intelligence Signals below */}
          <MobileContentCard
            title="Intelligence Signals"
            priority="medium"
            badge={
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-500 font-medium">
                {agentSignals ? Object.keys(agentSignals).length : 0} agents
              </span>
            }
          >
            <div className="pt-3">
              <IntelligenceGrid
                agentSignals={agentSignals}
                pipelineData={data as Record<string, unknown>}
                ticker={ticker}
              />
            </div>
          </MobileContentCard>
        </div>

        {/* ── Valuation ────────────────────────────────── */}
        <div id="mobile-valuation" className="scroll-mt-[120px] space-y-3">
          <MobileContentCard title="Price Target" defaultExpanded priority="high">
            <div className="pt-3">
              <MobilePriceTarget
                dcfRange={dcfRange}
                scenario={scenarioAnalysis}
                decision={decision}
                ticker={ticker}
              />
            </div>
          </MobileContentCard>
          {sectorCard && (
            <MobileContentCard title="Sector Valuation" defaultExpanded priority="high">
              <div className="pt-3">
                <SectorValuationCard payload={sectorCard} />
              </div>
            </MobileContentCard>
          )}
        </div>

        {/* ── Financials ───────────────────────────────── */}
        <div id="mobile-financials" className="scroll-mt-[120px] space-y-3">
          <MobileContentCard title="Financials" priority="high" defaultExpanded>
            <div className="pt-3">
              <FinancialsChart ticker={ticker} />
            </div>
          </MobileContentCard>
        </div>
      </div>
    </div>
  );
}

// Type helper
type MobileKeyStatsMetrics = {
  market_cap?: number;
  revenue?: number;
  free_cash_flow?: number;
  net_margin?: number;
  pe_ratio?: number;
  revenue_growth?: number;
  ev_to_ebitda?: number;
  return_on_equity?: number;
};
