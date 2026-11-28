import { Fragment, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Check, X } from 'lucide-react';
import { ResearchNav } from '@/components/layout/ResearchNav';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { getStoredToken } from '@/contexts/auth-context';
import {
  type PlanInfo,
  type Tier,
  fmtLimit,
  isUnlimited,
  openBillingPortal,
  startCheckout,
  useBilling,
} from '@/lib/tier';

// Pricing 2026-11-28: every row is a field the server enforces (app/backend/services/billing.py)
// or something every plan gets today. The investor-agent, agent-selection, batch, priority-queue
// and API rows are gone: none of them exists.

type Cell = string | boolean;
interface Row { category: string; label: string; cell: (p: PlanInfo) => Cell }

const credits = (p: PlanInfo) =>
  p.credit_period === 'lifetime' ? `${p.credits} (one-time)` : `${p.credits} / month`;

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

const months = (n: number) => (isUnlimited(n) ? 'Unlimited' : n === 1 ? '1 month' : `${n} months`);

const ROWS: Row[] = [
  { category: 'Analysis', label: 'Credits included', cell: credits },
  { category: 'Analysis', label: 'Extra credits (never expire)', cell: p => (p.addon ? `${p.addon.credits} for $${p.addon.price}` : false) },
  { category: 'Analysis', label: 'Interactive estimate overrides', cell: () => true },
  { category: 'Export', label: 'PDF investment report', cell: p => p.pdf_export },
  { category: 'Export', label: 'Excel valuation workbook (live formulas, three-statement model)', cell: p => p.excel_export },
  { category: 'Research', label: 'Screener and idea screens', cell: () => true },
  { category: 'Research', label: 'Watchlist', cell: p => (isUnlimited(p.watchlist_limit) ? 'Unlimited' : `${p.watchlist_limit} tickers`) },
  { category: 'Research', label: 'Run history', cell: p => months(p.history_months) },
  { category: 'Portfolio', label: 'Holdings dashboard, crisis replay, what-if scenarios', cell: () => true },
];

function Val({ v }: { v: Cell }) {
  if (v === true) return <Check size={16} className="text-content-high mx-auto" aria-label="Included" />;
  if (v === false) return <X size={16} className="text-muted-foreground/40 mx-auto" aria-label="Not included" />;
  return <span className="text-sm text-center block">{v}</span>;
}

function highlights(p: PlanInfo): string[] {
  const out = [p.credit_period === 'lifetime'
    ? `${plural(p.credits, 'credit')} to try it`
    : `${plural(p.credits, 'credit')} a month`];
  if (p.pdf_export) out.push('PDF investment report');
  if (p.excel_export) out.push('Excel valuation workbook with three-statement model');
  out.push(isUnlimited(p.watchlist_limit) ? 'Unlimited watchlist' : `Watchlist up to ${p.watchlist_limit} tickers`);
  out.push(isUnlimited(p.history_months) ? 'Full run history' : `${months(p.history_months)} of run history`);
  if (p.addon) out.push(`Extra credits: ${p.addon.credits} for $${p.addon.price}`);
  return out;
}

function TierCard({ plan, current, billing, signedIn, paymentsEnabled, hasBillingAccount, busy, onChoose, onManage }: {
  plan: PlanInfo; current: boolean; billing: 'monthly' | 'annual'; signedIn: boolean;
  paymentsEnabled: boolean; hasBillingAccount: boolean; busy: boolean;
  onChoose: () => void; onManage: () => void;
}) {
  const free = plan.id === 'free';
  const perMonth = billing === 'annual' && !free ? Math.round(plan.price_annual / 12) : plan.price_monthly;
  const featured = plan.id === 'professional';

  let cta: React.ReactNode;
  if (current) {
    cta = hasBillingAccount && !free
      ? <Button variant="outline" className="w-full" onClick={onManage} disabled={busy}>Manage billing</Button>
      : <Button variant="outline" disabled className="w-full">Current plan</Button>;
  } else if (free) {
    cta = <Button variant="outline" disabled className="w-full">{signedIn ? 'Included' : 'Sign in to start'}</Button>;
  } else if (!signedIn) {
    cta = <Button variant="outline" disabled className="w-full">Sign in to choose</Button>;
  } else if (!paymentsEnabled || !plan.purchasable) {
    cta = <Button variant="outline" disabled className="w-full">Available soon</Button>;
  } else {
    cta = (
      <Button className={`w-full ${featured ? 'bg-foreground text-background hover:bg-foreground/90' : ''}`}
              onClick={onChoose} disabled={busy}>
        {`Choose ${plan.name}`}
      </Button>
    );
  }

  return (
    <Card className={['relative flex flex-col p-6', featured ? 'border-foreground ring-1 ring-foreground' : '',
                      current ? 'bg-surface-2' : ''].join(' ')}>
      {featured && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2">
          <span className="bg-foreground text-background text-xs font-bold px-3 py-0.5 rounded-full">FULL MODEL</span>
        </div>
      )}
      {current && (
        <div className="absolute top-3 right-3">
          <span className="bg-surface-2 text-content-high text-xs font-bold px-2 py-0.5 rounded-full">CURRENT PLAN</span>
        </div>
      )}
      <div className="mb-4">
        <h3 className="text-lg font-bold">{plan.name}</h3>
        <div className="mt-2">
          <span className="text-3xl font-bold tabular-nums">${perMonth}</span>
          <span className="text-muted-foreground text-sm ml-1">{free ? '/ forever' : '/ mo'}</span>
          {billing === 'annual' && !free && (
            <p className="text-xs text-muted-foreground mt-0.5">
              Billed ${plan.price_annual}/year · save {plan.annual_saving_pct}%
            </p>
          )}
        </div>
      </div>
      <ul className="space-y-2.5 mb-6 flex-1">
        {highlights(plan).map(h => (
          <li key={h} className="flex items-start gap-2 text-sm">
            <Check size={14} className="text-content-high mt-0.5 flex-shrink-0" />
            <span>{h}</span>
          </li>
        ))}
      </ul>
      {cta}
    </Card>
  );
}

export function PricingPage() {
  const { plans, me, error, refresh } = useBilling();
  const [billing, setBilling] = useState<'monthly' | 'annual'>('monthly');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const signedIn = !!getStoredToken();

  // Back from Stripe Checkout. The plan is written by the webhook, not by this redirect.
  useEffect(() => {
    const c = params.get('checkout');
    if (!c) return;
    setNotice(c === 'success'
      ? 'Payment received. Your plan updates as soon as Stripe confirms it, usually within a few seconds.'
      : 'Checkout cancelled. Nothing was charged.');
    params.delete('checkout');
    setParams(params, { replace: true });
    if (c === 'success') {
      const t1 = setTimeout(refresh, 3000);
      const t2 = setTimeout(refresh, 10000);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const go = async (fn: () => Promise<void>) => {
    setBusy(true);
    setNotice(null);
    try { await fn(); } catch (e) { setNotice((e as Error).message); setBusy(false); }
  };

  const ordered = plans?.plans ?? [];
  const current: Tier = me?.plan ?? 'free';
  const currentPlan = ordered.find(p => p.id === current);
  const maxSaving = Math.max(0, ...ordered.map(p => p.annual_saving_pct));
  const categories = Array.from(new Set(ROWS.map(r => r.category)));
  const u = me?.usage;

  return (
    <div className="min-h-screen bg-background">
      <ResearchNav />
      <div className="p-4 md:p-8">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-8">
            <h1 className="text-3xl font-bold mb-2">Plans</h1>
            <p className="text-muted-foreground text-sm">Analyst-style valuations of any listed company, on demand.</p>
            <div className="flex items-center justify-center gap-3 mt-5">
              <button className={`text-sm font-medium ${billing === 'monthly' ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                      onClick={() => setBilling('monthly')}>Monthly</button>
              <button className={`relative w-10 h-5 rounded-full transition-colors ${billing === 'annual' ? 'bg-foreground' : 'bg-muted'}`}
                      onClick={() => setBilling(b => (b === 'monthly' ? 'annual' : 'monthly'))}
                      aria-label="Toggle billing period">
                <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-background shadow transition-transform ${billing === 'annual' ? 'translate-x-5' : ''}`} />
              </button>
              <button className={`text-sm font-medium ${billing === 'annual' ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                      onClick={() => setBilling('annual')}>
                Annual
                {maxSaving > 0 && (
                  <span className="ml-1.5 text-xs bg-surface-2 text-content-high font-bold px-1.5 py-0.5 rounded-full">
                    SAVE UP TO {maxSaving}%
                  </span>
                )}
              </button>
            </div>
          </div>

          {notice && (
            <div className="mb-6 p-3 rounded-lg border border-border text-sm text-center" role="status">{notice}</div>
          )}
          {error && !plans && (
            <div className="mb-6 p-3 rounded-lg border border-border text-sm text-center">Plans could not be loaded: {error}</div>
          )}

          {plans && plans.every_run.length > 0 && (
            <div className="mb-6 text-sm text-center text-muted-foreground">
              Every plan, every analysis: {plans.every_run.join(' · ')}
            </div>
          )}
          {plans?.credit_costs && (
            <div className="mb-6 text-sm text-center text-muted-foreground">
              {`An analysis of a ticker we already cover uses ${plural(plans.credit_costs.covered, 'credit')}; `
                + `a first analysis of a new ticker, ${plans.credit_costs.new}; `
                + `any analysis on the Anthropic model, ${plans.credit_costs.premium}. `
                + 'A failed run uses none, and re-opening a recent run is free.'}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-10">
            {ordered.map(p => (
              <TierCard key={p.id} plan={p} current={signedIn && current === p.id} billing={billing}
                        signedIn={signedIn} paymentsEnabled={!!plans?.payments_enabled}
                        hasBillingAccount={!!me?.has_billing_account} busy={busy}
                        onChoose={() => go(() => startCheckout(p.id, billing))}
                        onManage={() => go(openBillingPortal)} />
            ))}
          </div>

          {u && currentPlan && (
            <div className="mb-8 p-4 rounded-lg border border-border">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wide font-semibold mb-0.5">Your usage</p>
                  <p className="font-bold text-sm">
                    {currentPlan.name} plan
                    {me?.period_end && <span className="ml-2 font-normal text-muted-foreground">renews {me.period_end.slice(0, 10)}</span>}
                  </p>
                </div>
                <div className="flex gap-6 text-sm">
                  <div className="text-center">
                    <p className="font-bold text-lg tabular-nums">{u.exempt ? '∞' : `${u.used} / ${u.included}`}</p>
                    <p className="text-xs text-muted-foreground">{u.period === 'lifetime' ? 'credits used' : 'credits this month'}</p>
                  </div>
                  {u.addon_left > 0 && (
                    <div className="text-center">
                      <p className="font-bold text-lg tabular-nums">{u.addon_left}</p>
                      <p className="text-xs text-muted-foreground">extra credits</p>
                    </div>
                  )}
                  <div className="text-center">
                    <p className="font-bold text-lg tabular-nums">{fmtLimit(currentPlan.watchlist_limit)}</p>
                    <p className="text-xs text-muted-foreground">watchlist</p>
                  </div>
                </div>
                {currentPlan.addon && plans?.payments_enabled && current !== 'free' && (
                  <Button variant="outline" size="sm" disabled={busy}
                          onClick={() => go(() => startCheckout(current, 'addon'))}>
                    {`Add ${currentPlan.addon.credits} credits · $${currentPlan.addon.price}`}
                  </Button>
                )}
              </div>
            </div>
          )}

          <div className="rounded-lg border border-border overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-muted/50">
                  <th className="text-left px-4 py-3 font-semibold text-muted-foreground w-1/2">Feature</th>
                  {ordered.map(p => (
                    <th key={p.id} className="px-4 py-3 font-semibold text-center">{p.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {categories.map(cat => (
                  <Fragment key={cat}>
                    <tr className="bg-muted/20">
                      <td colSpan={ordered.length + 1} className="px-4 py-1.5 text-xs font-bold uppercase tracking-widest text-muted-foreground">{cat}</td>
                    </tr>
                    {ROWS.filter(r => r.category === cat).map(r => (
                      <tr key={r.label} className="border-t border-border/50">
                        <td className="px-4 py-2.5 text-muted-foreground">{r.label}</td>
                        {ordered.map(p => (
                          <td key={p.id} className="px-4 py-2.5 text-center"><Val v={r.cell(p)} /></td>
                        ))}
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-center text-xs text-muted-foreground mt-6">
            {plans?.payments_enabled
              ? 'Payments are processed by Stripe. Cancel or change plan any time from Manage billing.'
              : 'Paid plans open soon.'}
            {' '}Research and valuations are for information only and are not investment advice.
          </p>
        </div>
      </div>
    </div>
  );
}
