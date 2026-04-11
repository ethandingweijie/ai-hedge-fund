import { useState, useEffect } from 'react';
import { Check, X, Zap } from 'lucide-react';
import { ResearchNav } from '@/components/layout/ResearchNav';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  type Tier,
  type TierProfile,
  TIER_PROFILES,
  TIER_ORDER,
  getActiveTier,
  setActiveTier,
} from '@/lib/tier';

// ── Feature comparison rows ───────────────────────────────────────────────────

interface FeatureRow {
  label: string;
  category: string;
  free: string | boolean;
  starter: string | boolean;
  professional: string | boolean;
}

const FEATURES: FeatureRow[] = [
  // Analysis
  { category: 'Analysis', label: 'Full pipeline runs / month', free: '1 (lifetime)', starter: '5', professional: '20' },
  { category: 'Analysis', label: 'Investor agent personas', free: false, starter: 'Deep value (7)', professional: 'All 12' },
  { category: 'Analysis', label: 'DCF + scenario analysis', free: true, starter: true, professional: true },
  { category: 'Analysis', label: 'Deep research (web search)', free: false, starter: true, professional: true },
  { category: 'Analysis', label: 'Debate round (bull vs bear)', free: true, starter: true, professional: true },
  { category: 'Analysis', label: 'Agent selection', free: false, starter: false, professional: true },
  { category: 'Analysis', label: 'Multi-ticker batch (tickers/run)', free: '1', starter: '1', professional: '3' },
  { category: 'Analysis', label: 'Add-on runs', free: false, starter: '5 for $12', professional: '10 for $18' },
  // Screener
  { category: 'Screener', label: 'Screener results', free: 'Top 5', starter: 'Unlimited', professional: 'Unlimited' },
  { category: 'Screener', label: 'Fast VGPM scoring', free: true, starter: true, professional: true },
  { category: 'Screener', label: 'Sector / exchange filters', free: true, starter: true, professional: true },
  // Watchlist
  { category: 'Watchlist', label: 'Tracked tickers', free: '3', starter: '15', professional: 'Unlimited' },
  { category: 'Watchlist', label: 'Auto-refresh VGPM on analysis', free: false, starter: true, professional: true },
  { category: 'Watchlist', label: 'Pipeline VGPM upgrade', free: false, starter: true, professional: true },
  // History
  { category: 'History', label: 'Run history', free: false, starter: '6 months', professional: '24 months' },
  { category: 'History', label: 'Re-open past reports', free: false, starter: true, professional: true },
  // Export
  { category: 'Export', label: 'PDF report export', free: false, starter: true, professional: true },
  { category: 'Export', label: 'Read-only API access', free: false, starter: false, professional: false },
];

// ── Value cell ────────────────────────────────────────────────────────────────

function Val({ v }: { v: string | boolean }) {
  if (v === true)  return <Check size={16} className="text-green-500 mx-auto" />;
  if (v === false) return <X    size={16} className="text-muted-foreground/40 mx-auto" />;
  return <span className="text-sm text-center block">{v}</span>;
}

// ── Tier card ─────────────────────────────────────────────────────────────────

function TierCard({
  profile,
  active,
  billing,
  onSelect,
}: {
  profile: TierProfile;
  active: boolean;
  billing: 'monthly' | 'annual';
  onSelect: () => void;
}) {
  const price = billing === 'annual' ? profile.priceAnnual : profile.priceMonthly;
  const perMonth = billing === 'annual' && profile.priceAnnual
    ? Math.round(profile.priceAnnual / 12)
    : profile.priceMonthly;

  const isHighlighted = profile.id === 'professional';

  const highlights: string[] = [];
  if (profile.id === 'free') {
    highlights.push('1 lifetime full analysis run');
    highlights.push('Top 10 screener results');
    highlights.push('Watchlist up to 3 tickers');
    highlights.push('Fast VGPM on screener');
  } else if (profile.id === 'starter') {
    highlights.push('5 full pipeline runs / month');
    highlights.push('Deep value agents: Graham · Buffett · Munger · Pabrai · Fisher · Burry · Damodaran');
    highlights.push('Deep research with web search');
    highlights.push('PDF export');
    highlights.push('Watchlist up to 15 tickers');
    highlights.push('6-month history');
  } else {
    highlights.push('20 full pipeline runs / month');
    highlights.push('Agent selection (pick your analysts)');
    highlights.push('Multi-ticker batch — 3 per run');
    highlights.push('Watchlist unlimited');
    highlights.push('24-month history');
    highlights.push('Priority processing queue');
  }

  return (
    <Card
      className={[
        'relative flex flex-col p-6 transition-shadow',
        isHighlighted ? 'border-blue-500 shadow-blue-500/10 shadow-lg ring-1 ring-blue-500' : '',
        active ? 'bg-primary/5' : '',
      ].join(' ')}
    >
      {isHighlighted && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2">
          <span className="bg-blue-500 text-white text-[11px] font-bold px-3 py-0.5 rounded-full">
            MOST POPULAR
          </span>
        </div>
      )}

      {active && (
        <div className="absolute top-3 right-3">
          <span className="bg-green-500/15 text-green-600 dark:text-green-400 text-[10px] font-bold px-2 py-0.5 rounded-full">
            CURRENT PLAN
          </span>
        </div>
      )}

      <div className="mb-4">
        <h3 className="text-lg font-bold">{profile.name}</h3>
        {price === null ? (
          <div className="mt-2">
            <span className="text-3xl font-bold">$0</span>
            <span className="text-muted-foreground text-sm ml-1">/ forever</span>
          </div>
        ) : (
          <div className="mt-2">
            <span className="text-3xl font-bold">${perMonth}</span>
            <span className="text-muted-foreground text-sm ml-1">/ mo</span>
            {billing === 'annual' && (
              <p className="text-xs text-muted-foreground mt-0.5">
                Billed ${price}/year · save {Math.round((1 - price / (profile.priceMonthly! * 12)) * 100)}%
              </p>
            )}
          </div>
        )}
      </div>

      <ul className="space-y-2.5 mb-6 flex-1">
        {highlights.map(h => (
          <li key={h} className="flex items-start gap-2 text-sm">
            <Check size={14} className="text-green-500 mt-0.5 flex-shrink-0" />
            <span>{h}</span>
          </li>
        ))}
      </ul>

      {active ? (
        <Button variant="outline" disabled className="w-full">Active plan</Button>
      ) : profile.id === 'free' ? (
        <Button variant="outline" className="w-full" onClick={onSelect}>
          Use Free
        </Button>
      ) : (
        <Button
          className={`w-full ${isHighlighted ? 'bg-blue-500 hover:bg-blue-600 text-white' : ''}`}
          onClick={onSelect}
        >
          <Zap size={14} className="mr-1.5" />
          {`Activate ${profile.name}`}
        </Button>
      )}

      {profile.addOnRuns && !active && (
        <p className="text-[11px] text-muted-foreground text-center mt-2">
          Add-on: {profile.addOnRuns.count} runs for ${profile.addOnRuns.price}
        </p>
      )}
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function PricingPage() {
  const [billing, setBilling] = useState<'monthly' | 'annual'>('monthly');
  const [activeTier, setActive] = useState<Tier>(getActiveTier());

  // Sync if changed in another tab
  useEffect(() => {
    const handler = (e: Event) => setActive((e as CustomEvent<Tier>).detail);
    window.addEventListener('tierchange', handler);
    return () => window.removeEventListener('tierchange', handler);
  }, []);

  const handleSelect = (tier: Tier) => {
    setActiveTier(tier);
    setActive(tier);
  };

  // Group features by category
  const categories = Array.from(new Set(FEATURES.map(f => f.category)));

  return (
    <div className="min-h-screen bg-background">
      <ResearchNav />
      <div className="p-4 md:p-8">
        <div className="max-w-5xl mx-auto">

          {/* Header */}
          <div className="text-center mb-8">
            <h1 className="text-3xl font-bold mb-2">Choose Your Plan</h1>
            <p className="text-muted-foreground text-sm">
              Institutional-quality AI research at every level.
            </p>

            {/* Billing toggle */}
            <div className="flex items-center justify-center gap-3 mt-5">
              <button
                className={`text-sm font-medium transition-colors ${billing === 'monthly' ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                onClick={() => setBilling('monthly')}
              >
                Monthly
              </button>
              <button
                className={`relative w-10 h-5 rounded-full transition-colors ${billing === 'annual' ? 'bg-blue-500' : 'bg-muted'}`}
                onClick={() => setBilling(b => b === 'monthly' ? 'annual' : 'monthly')}
                aria-label="Toggle billing period"
              >
                <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${billing === 'annual' ? 'translate-x-5' : ''}`} />
              </button>
              <button
                className={`text-sm font-medium transition-colors ${billing === 'annual' ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                onClick={() => setBilling('annual')}
              >
                Annual
                <span className="ml-1.5 text-[10px] bg-green-500/15 text-green-600 dark:text-green-400 font-bold px-1.5 py-0.5 rounded-full">
                  SAVE 20%
                </span>
              </button>
            </div>
          </div>

          {/* Tier cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-10">
            {TIER_ORDER.map(tierId => (
              <TierCard
                key={tierId}
                profile={TIER_PROFILES[tierId]}
                active={activeTier === tierId}
                billing={billing}
                onSelect={() => handleSelect(tierId)}
              />
            ))}
          </div>

          {/* Active tier summary */}
          <div className="mb-8 p-4 rounded-lg border border-border bg-muted/30">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wide font-semibold mb-0.5">
                  Active profile
                </p>
                <p className="font-bold text-sm">
                  {TIER_PROFILES[activeTier].name} plan
                  {TIER_PROFILES[activeTier].priceMonthly != null && (
                    <span className="ml-2 font-normal text-muted-foreground">
                      ${TIER_PROFILES[activeTier].priceMonthly}/month
                    </span>
                  )}
                </p>
              </div>
              <div className="flex gap-6 text-sm">
                <div className="text-center">
                  <p className="font-bold text-lg">
                    {TIER_PROFILES[activeTier].runsPerMonth === Infinity
                      ? '∞'
                      : TIER_PROFILES[activeTier].runsPerMonth}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {TIER_PROFILES[activeTier].lifetimeCapOnly ? 'lifetime run' : 'runs / mo'}
                  </p>
                </div>
                <div className="text-center">
                  <p className="font-bold text-lg">
                    {TIER_PROFILES[activeTier].watchlistLimit === Infinity
                      ? '∞'
                      : TIER_PROFILES[activeTier].watchlistLimit}
                  </p>
                  <p className="text-xs text-muted-foreground">watchlist</p>
                </div>
                <div className="text-center">
                  <p className="font-bold text-lg">
                    {TIER_PROFILES[activeTier].screenerLimit === Infinity
                      ? '∞'
                      : TIER_PROFILES[activeTier].screenerLimit}
                  </p>
                  <p className="text-xs text-muted-foreground">screener</p>
                </div>
                <div className="text-center">
                  <p className="font-bold text-lg">
                    {TIER_PROFILES[activeTier].historyMonths === 0
                      ? '—'
                      : `${TIER_PROFILES[activeTier].historyMonths}mo`}
                  </p>
                  <p className="text-xs text-muted-foreground">history</p>
                </div>
              </div>
            </div>
          </div>

          {/* Feature comparison table */}
          <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-muted/50">
                  <th className="text-left px-4 py-3 font-semibold text-muted-foreground w-1/2">Feature</th>
                  {TIER_ORDER.map(t => (
                    <th key={t} className={`px-4 py-3 font-semibold text-center ${activeTier === t ? 'text-blue-600 dark:text-blue-400' : ''}`}>
                      {TIER_PROFILES[t].name}
                      {activeTier === t && <span className="ml-1 text-[10px]">✓</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {categories.map(cat => (
                  <>
                    <tr key={`cat-${cat}`} className="bg-muted/20">
                      <td colSpan={4} className="px-4 py-1.5 text-[11px] font-bold uppercase tracking-widest text-muted-foreground">
                        {cat}
                      </td>
                    </tr>
                    {FEATURES.filter(f => f.category === cat).map(f => (
                      <tr key={f.label} className="border-t border-border/50 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-2.5 text-muted-foreground">{f.label}</td>
                        <td className="px-4 py-2.5 text-center"><Val v={f.free} /></td>
                        <td className="px-4 py-2.5 text-center"><Val v={f.starter} /></td>
                        <td className="px-4 py-2.5 text-center"><Val v={f.professional} /></td>
                      </tr>
                    ))}
                  </>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-center text-xs text-muted-foreground mt-6">
            Plans activate immediately in this session. No payment integration connected — for internal use only.
          </p>

        </div>
      </div>
    </div>
  );
}
