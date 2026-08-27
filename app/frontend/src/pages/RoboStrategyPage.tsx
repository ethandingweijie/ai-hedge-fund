/**
 * RoboStrategyPage.tsx
 * ======================
 * Robo Strategy — deterministic portfolio recommendation. User answers a
 * risk/horizon/sector/geography questionnaire; the backend returns a
 * recommended "Diversified ETFs" mix and an "Individual Stocks" mix with
 * per-holding allocation, sector/geography/risk breakdown charts, and CSV
 * export. Single component branching mobile/desktop internally, matching
 * WatchlistPage.tsx's established shape (TabHero + PageContainer on
 * desktop, TabHero + padded div on mobile).
 */
import { useEffect, useState } from 'react';
import { ArrowLeft, Download, Loader2, RefreshCw, Sparkles, Wallet } from 'lucide-react';
import { toast } from 'sonner';
import {
  Accordion, AccordionContent, AccordionItem, AccordionTrigger,
} from '@/components/ui/accordion';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PageContainer } from '@/components/layout/PageContainer';
import { TabHero } from '@/components/layout/TabHero';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { RangeSlider } from '@/components/robo/RangeSlider';
import { HoldingsTable } from '@/components/robo/HoldingsTable';
import { AllocationCharts } from '@/components/robo/AllocationCharts';
import { TemplateCard } from '@/components/robo/TemplateCard';
import { adjustWeight, sumWeights } from '@/lib/roboSliderUtils';
import { STRATEGY_TEMPLATES, templateToQuestionnaire, type StrategyTemplate } from '@/data/roboStrategyTemplates';
import {
  ROBO_REGIONS, ROBO_SECTORS,
  getRoboProfile, generateRoboPortfolio,
  type RoboHolding, type RoboPortfolio, type RoboQuestionnaire,
  type RoboRiskTolerance, type RoboTimeHorizon,
} from '@/lib/api';

type RoboView = 'templates' | 'questionnaire' | 'results';

// ── Defaults ─────────────────────────────────────────────────────────────────

function evenSplit(keys: readonly string[]): Record<string, number> {
  const n = keys.length;
  const base = Math.floor(100 / n);
  const remainder = 100 - base * n;
  const result: Record<string, number> = {};
  keys.forEach((k, i) => { result[k] = base + (i < remainder ? 1 : 0); });
  return result;
}

const DEFAULT_ANSWERS: RoboQuestionnaire = {
  risk_tolerance: 'moderate',
  time_horizon: '7-15 years',
  sector_preferences: evenSplit(ROBO_SECTORS),
  geography_preferences: { US: 45, Europe: 15, 'Asia-Pacific': 15, 'Emerging Markets': 25 },
  investment_amount: 10000,
};

// Friendlier slider labels — states upfront which countries live in each
// bucket, since "Emerging Markets" alone doesn't tell a user this is their
// lever for China/Hong Kong/India exposure.
const REGION_LABELS: Record<string, string> = {
  US: 'US',
  Europe: 'Europe',
  'Asia-Pacific': 'Asia-Pacific (Japan, Australia, Korea)',
  'Emerging Markets': 'Emerging Markets (China, Hong Kong, India)',
};

const RISK_OPTIONS: { value: RoboRiskTolerance; label: string; desc: string }[] = [
  { value: 'conservative', label: 'Conservative', desc: 'Capital preservation, lower volatility' },
  { value: 'moderate', label: 'Moderate', desc: 'Balanced growth and stability' },
  { value: 'aggressive', label: 'Aggressive', desc: 'Maximum growth, higher volatility' },
];

const HORIZON_OPTIONS: { value: RoboTimeHorizon; label: string }[] = [
  { value: '<3 years', label: '< 3 years' },
  { value: '3-7 years', label: '3-7 years' },
  { value: '7-15 years', label: '7-15 years' },
  { value: '15+ years', label: '15+ years' },
];

// ── CSV export ───────────────────────────────────────────────────────────────

function exportCsv(items: RoboHolding[], mode: 'etf' | 'stocks') {
  const headers = mode === 'etf'
    ? ['Ticker', 'Name', 'Category', 'Sector', 'Region', 'Allocation %', 'Amount', 'Shares', 'Price', 'Expense Ratio']
    : ['Ticker', 'Name', 'Sector', 'Region', 'Allocation %', 'Amount', 'Shares', 'Price', 'Risk Level'];
  const rows = items.map((i) => mode === 'etf'
    ? [i.ticker, i.name, i.category ?? '', i.sector ?? '', i.region ?? '', i.allocationPercent, i.amount, i.shares, i.price, i.expenseRatio ?? '']
    : [i.ticker, i.name, i.sector ?? '', i.region ?? '', i.allocationPercent, i.amount, i.shares, i.price, i.riskLevel ?? '']);
  const csv = [headers, ...rows]
    .map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `robo-strategy-${mode}-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Chip button (single-select) ─────────────────────────────────────────────

function ChipButton({
  selected, onClick, children,
}: { selected: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3.5 py-2.5 text-left rounded-lg border transition-colors ${
        selected
          ? 'bg-brand/10 border-brand/25 text-brand'
          : 'bg-card border-border text-muted-foreground active:bg-muted/60'
      }`}
    >
      {children}
    </button>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function RoboStrategyPage() {
  const { mode: layoutMode } = useLayoutMode();

  const [answers, setAnswers] = useState<RoboQuestionnaire>(DEFAULT_ANSWERS);
  const [portfolio, setPortfolio] = useState<RoboPortfolio | null>(null);
  const [view, setView] = useState<RoboView>('templates');
  const [activeTemplateName, setActiveTemplateName] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'etf' | 'stocks'>('etf');
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    getRoboProfile()
      .then(({ answers: saved, portfolio: savedPortfolio }) => {
        if (saved) setAnswers(saved);
        if (savedPortfolio) {
          setPortfolio(savedPortfolio);
          setView('results');
        }
      })
      .catch(() => { /* first-time user — no saved profile, stay on templates */ })
      .finally(() => setLoading(false));
  }, []);

  const handleSelectTemplate = (template: StrategyTemplate) => {
    setAnswers(templateToQuestionnaire(template, answers.investment_amount));
    setActiveTemplateName(template.name);
    setView('questionnaire');
  };

  const handleBuildOwn = () => {
    setActiveTemplateName(null);
    setView('questionnaire');
  };

  const sectorTotal = Math.round(sumWeights(answers.sector_preferences));
  const geoTotal = Math.round(sumWeights(answers.geography_preferences));
  const canGenerate = sectorTotal === 100 && geoTotal === 100 && answers.investment_amount > 0;

  const handleSectorChange = (key: string, value: number) => {
    setAnswers((prev) => ({ ...prev, sector_preferences: adjustWeight(prev.sector_preferences, key, value) }));
  };
  const handleGeoChange = (key: string, value: number) => {
    setAnswers((prev) => ({ ...prev, geography_preferences: adjustWeight(prev.geography_preferences, key, value) }));
  };

  const handleGenerate = async () => {
    if (!canGenerate || generating) return;
    setGenerating(true);
    try {
      const result = await generateRoboPortfolio(answers);
      setPortfolio(result);
      setView('results');
      toast.success('Portfolio generated.');
    } catch (e) {
      toast.error(`Failed to generate portfolio: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setGenerating(false);
    }
  };

  const activeItems = portfolio ? (activeTab === 'etf' ? portfolio.etf_portfolio : portfolio.stock_portfolio) : [];
  const activeBreakdowns = portfolio ? (activeTab === 'etf' ? portfolio.etf_breakdowns : portfolio.stock_breakdowns) : null;
  const sectorCount = activeBreakdowns ? Object.keys(activeBreakdowns.sector).length : 0;

  // ── Templates view ("Browse Strategies") ────────────────────────────────────
  const templatesView = (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-[15px] font-semibold text-foreground">Popular Strategy Templates</h2>
          <p className="text-[12px] text-muted-foreground mt-0.5">
            Pick a preset allocation to start from, then customize it to your preferences.
          </p>
        </div>
        <button
          type="button"
          onClick={handleBuildOwn}
          className="h-9 px-4 rounded-full border border-border bg-card text-[13px] font-medium text-foreground hover:bg-muted flex items-center gap-1.5 shrink-0"
        >
          <Sparkles size={14} />
          Build Your Own
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {STRATEGY_TEMPLATES.map((template) => (
          <TemplateCard key={template.id} template={template} onSelect={() => handleSelectTemplate(template)} />
        ))}
      </div>
    </div>
  );

  // ── Questionnaire view ──────────────────────────────────────────────────────
  const questionnaireView = (
    <div className="space-y-4">
      <button
        type="button"
        onClick={() => setView('templates')}
        className="text-[12px] font-medium text-muted-foreground hover:text-foreground flex items-center gap-1"
      >
        <ArrowLeft size={13} />
        Back to Strategies
      </button>
      {activeTemplateName && (
        <p className="text-[12px] text-muted-foreground -mt-2">
          Starting from <span className="font-semibold text-foreground">{activeTemplateName}</span> — adjust below, then generate.
        </p>
      )}
      <Accordion type="multiple" defaultValue={['risk', 'horizon', 'sectors', 'geography', 'amount']}
        className="rounded-lg border border-border bg-card overflow-hidden">
        <AccordionItem value="risk" className="px-4">
          <AccordionTrigger className="text-[14px] font-semibold">Risk Tolerance</AccordionTrigger>
          <AccordionContent>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {RISK_OPTIONS.map((opt) => (
                <ChipButton
                  key={opt.value}
                  selected={answers.risk_tolerance === opt.value}
                  onClick={() => setAnswers((prev) => ({ ...prev, risk_tolerance: opt.value }))}
                >
                  <div className="text-[13px] font-semibold">{opt.label}</div>
                  <div className="text-[11px] opacity-80 mt-0.5">{opt.desc}</div>
                </ChipButton>
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="horizon" className="px-4">
          <AccordionTrigger className="text-[14px] font-semibold">Time Horizon</AccordionTrigger>
          <AccordionContent>
            <div className="flex flex-wrap gap-2">
              {HORIZON_OPTIONS.map((opt) => (
                <ChipButton
                  key={opt.value}
                  selected={answers.time_horizon === opt.value}
                  onClick={() => setAnswers((prev) => ({ ...prev, time_horizon: opt.value }))}
                >
                  <span className="text-[13px] font-semibold">{opt.label}</span>
                </ChipButton>
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="sectors" className="px-4">
          <AccordionTrigger className="text-[14px] font-semibold">
            <span className="flex items-center gap-2">
              Sector Preferences
              <span className={`text-[11px] font-mono font-normal ${sectorTotal === 100 ? 'text-brand' : 'text-content-high'}`}>
                {sectorTotal}%
              </span>
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <div>
              {ROBO_SECTORS.map((sector) => (
                <RangeSlider
                  key={sector}
                  label={sector}
                  value={answers.sector_preferences[sector] ?? 0}
                  onChange={(v) => handleSectorChange(sector, v)}
                />
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="geography" className="px-4">
          <AccordionTrigger className="text-[14px] font-semibold">
            <span className="flex items-center gap-2">
              Geography Preferences
              <span className={`text-[11px] font-mono font-normal ${geoTotal === 100 ? 'text-brand' : 'text-content-high'}`}>
                {geoTotal}%
              </span>
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <div>
              {ROBO_REGIONS.map((region) => (
                <RangeSlider
                  key={region}
                  label={REGION_LABELS[region] ?? region}
                  value={answers.geography_preferences[region] ?? 0}
                  onChange={(v) => handleGeoChange(region, v)}
                />
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground/80 italic mt-2 leading-relaxed">
              In Individual Stocks mode, Emerging Markets exposure comes from our Hong Kong screener
              universe and Asia-Pacific from Singapore; Europe has no individual-stock coverage yet.
              Diversified ETFs mode has full coverage across all four regions, including dedicated
              China and India funds.
            </p>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="amount" className="px-4 border-b-0">
          <AccordionTrigger className="text-[14px] font-semibold">Investment Amount</AccordionTrigger>
          <AccordionContent>
            <div className="relative max-w-xs">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">$</span>
              <input
                type="text"
                inputMode="numeric"
                value={answers.investment_amount ? answers.investment_amount.toLocaleString() : ''}
                onChange={(e) => {
                  const raw = e.target.value.replace(/[^0-9]/g, '');
                  setAnswers((prev) => ({ ...prev, investment_amount: raw ? Number(raw) : 0 }));
                }}
                placeholder="10,000"
                className="w-full h-10 pl-7 pr-3 rounded-lg bg-muted/60 border border-border text-[14px] font-mono focus:bg-card focus:border-brand/40 focus:outline-none focus:ring-2 focus:ring-brand/10 text-foreground"
              />
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      <button
        type="button"
        onClick={handleGenerate}
        disabled={!canGenerate || generating}
        className="w-full h-12 rounded-full bg-primary text-primary-foreground text-[14px] font-semibold disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition-opacity flex items-center justify-center gap-2"
      >
        {generating ? <Loader2 size={16} className="animate-spin" /> : <Wallet size={16} />}
        {generating ? 'Generating…' : 'Generate Portfolio'}
      </button>
      {!canGenerate && (
        <p className="text-[12px] text-muted-foreground text-center">
          {sectorTotal !== 100 && `Sector weights must sum to 100% (currently ${sectorTotal}%). `}
          {geoTotal !== 100 && `Geography weights must sum to 100% (currently ${geoTotal}%). `}
          {answers.investment_amount <= 0 && 'Enter an investment amount.'}
        </p>
      )}
    </div>
  );

  // ── Results view ─────────────────────────────────────────────────────────────
  const resultsView = portfolio && (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Total Investment</div>
          <div className="text-[18px] font-bold font-mono text-foreground mt-1">
            ${portfolio.total_investment.toLocaleString()}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Holdings</div>
          <div className="text-[18px] font-bold font-mono text-foreground mt-1">{activeItems.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Sectors</div>
          <div className="text-[18px] font-bold font-mono text-foreground mt-1">{sectorCount}</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleGenerate}
          disabled={generating}
          className="h-9 px-4 rounded-full border border-border bg-card text-[13px] font-medium text-foreground hover:bg-muted disabled:opacity-50 flex items-center gap-1.5"
        >
          {generating ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Regenerate
        </button>
        <button
          type="button"
          onClick={() => setView('questionnaire')}
          className="h-9 px-4 rounded-full border border-border bg-card text-[13px] font-medium text-foreground hover:bg-muted"
        >
          Retake Questionnaire
        </button>
        <button
          type="button"
          onClick={() => setView('templates')}
          className="h-9 px-4 rounded-full border border-border bg-card text-[13px] font-medium text-foreground hover:bg-muted"
        >
          Browse Strategies
        </button>
        <button
          type="button"
          onClick={() => exportCsv(activeItems, activeTab)}
          disabled={activeItems.length === 0}
          className="h-9 px-4 rounded-full border border-border bg-card text-[13px] font-medium text-foreground hover:bg-muted disabled:opacity-40 flex items-center gap-1.5 md:ml-auto"
        >
          <Download size={14} />
          Export CSV
        </button>
      </div>

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'etf' | 'stocks')}>
        <TabsList>
          <TabsTrigger value="etf">Diversified ETFs</TabsTrigger>
          <TabsTrigger value="stocks">Individual Stocks</TabsTrigger>
        </TabsList>
      </Tabs>

      {activeBreakdowns && <AllocationCharts breakdowns={activeBreakdowns} />}
      <HoldingsTable items={activeItems} mode={activeTab} />
    </div>
  );

  const body = loading ? (
    <div className="flex items-center justify-center py-16 text-muted-foreground">
      <Loader2 size={20} className="animate-spin mr-2" /> Loading…
    </div>
  ) : view === 'templates' ? templatesView : view === 'questionnaire' ? questionnaireView : resultsView;

  if (layoutMode === 'mobile') {
    return (
      <div className="min-h-full flex flex-col bg-background">
        <TabHero title="Robo Strategy" />
        <div className="px-4 py-3">{body}</div>
      </div>
    );
  }

  return (
    <>
      <TabHero title="Robo Strategy" />
      <PageContainer size="wide" flush className="py-5 md:py-6">
        {body}
      </PageContainer>
    </>
  );
}
