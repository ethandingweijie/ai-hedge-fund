/**
 * Plans and the caller's plan, from the server (pricing 2026-11-28).
 *
 * The server is the single source of truth: GET /billing/plans (public) is what
 * each plan includes, GET /billing/me is the caller's plan and usage. Before
 * this, the plan lived in localStorage and "Activate" set it for free; nothing
 * on the server read it. A paid plan is written only by the Stripe webhook.
 */
import { useEffect, useState } from 'react';
import { API_BASE_URL } from '@/config';
import { getStoredToken } from '@/contexts/auth-context';

export type Tier = 'free' | 'starter' | 'professional';

export const UNLIMITED = -1;

export interface PlanInfo {
  id: Tier;
  name: string;
  price_monthly: number;      // monthly billing only (owner, 2026-11-28: no annual plan)
  credits: number;
  credit_period: 'lifetime' | 'month';
  watchlist_limit: number;   // UNLIMITED = -1
  history_months: number;    // UNLIMITED = -1
  pdf_export: boolean;
  excel_export: boolean;
  addon: { credits: number; price: number } | null;
  purchasable: boolean;
}

/** Credits a run spends: covered ticker (stored research), first-ever ticker, any Anthropic-model run. */
export interface CreditCosts { covered: number; new: number; limited?: number; premium: number }

export interface PlansResponse {
  plans: PlanInfo[];
  every_run: string[];
  credit_costs: CreditCosts;
  payments_enabled: boolean;
  enforced: boolean;
}

export interface Usage {
  plan: Tier;
  period: 'lifetime' | 'month';
  period_start: string | null;
  included: number;
  used: number;
  plan_left: number;
  addon_left: number;
  left: number;              // UNLIMITED for exempt (admin) accounts
  enforced: boolean;
  exempt: boolean;
}

export interface BillingMe {
  plan: Tier;
  status: string | null;
  interval: string | null;
  period_end: string | null;
  has_billing_account: boolean;
  usage: Usage;
  limits: PlanInfo;
}

const BASE = API_BASE_URL;

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw await errorFromResponse(res);
  return res.json() as Promise<T>;
}

/** An Error carrying the server's plan message (402 plan limit, 403 owner-only job, 503 billing not set up),
 * else `HTTP <status>: <body>`. */
export async function errorFromResponse(res: Response): Promise<Error> {
  const text = await res.text().catch(() => res.statusText);
  if (res.status === 402 || res.status === 403 || (res.status === 503 && res.url.includes('/billing/'))) {
    try {
      const detail = JSON.parse(text)?.detail;
      // A 403 carries a plan message only as {code, message}; a bare-string 403 stays an HTTP error.
      const msg = typeof detail === 'string' ? (res.status === 403 ? undefined : detail) : detail?.message;
      if (msg) {
        const e = new Error(msg) as Error & { code?: string; planLimit?: boolean };
        e.code = typeof detail === 'object' ? detail?.code : undefined;
        e.planLimit = res.status === 402;
        return e;
      }
    } catch { /* not JSON */ }
  }
  // Market questions (2026-11-28): 404/409/410/422/429 from /analysis/ask and an ask-carrying run say what happened
  // in a sentence (string detail, or {message}); FastAPI's own 422 list falls through to the raw form.
  if ([404, 409, 410, 422, 429, 503].includes(res.status)) {
    try {
      const detail = JSON.parse(text)?.detail;
      const msg = typeof detail === 'string' ? detail : (detail && typeof detail.message === 'string' ? detail.message : undefined);
      if (msg) return new Error(msg);
    } catch { /* not JSON */ }
  }
  return new Error(`HTTP ${res.status}: ${text}`);
}

export const fetchPlans = () => getJson<PlansResponse>('/billing/plans');
export const fetchBillingMe = () => getJson<BillingMe>('/billing/me');

export async function startCheckout(plan: Tier, interval: 'monthly' | 'addon'): Promise<void> {
  const { url } = await getJson<{ url: string }>('/billing/checkout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan, interval }),
  });
  window.location.assign(url);
}

export async function openBillingPortal(): Promise<void> {
  const { url } = await getJson<{ url: string }>('/billing/portal', { method: 'POST' });
  window.location.assign(url);
}

/** Plans + the caller's billing state. `me` is null when signed out. */
export function useBilling() {
  const [plans, setPlans] = useState<PlansResponse | null>(null);
  const [me, setMe] = useState<BillingMe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    fetchPlans().then(p => alive && setPlans(p)).catch(e => alive && setError(String(e.message || e)));
    if (getStoredToken()) {
      fetchBillingMe().then(m => alive && setMe(m)).catch(() => alive && setMe(null));
    }
    return () => { alive = false; };
  }, [tick]);

  return { plans, me, error, refresh: () => setTick(t => t + 1) };
}

/** The caller's plan only (no plans list): for components that gate one action. Null when signed out. */
export function useBillingMe() {
  const [me, setMe] = useState<BillingMe | null>(null);
  useEffect(() => {
    let alive = true;
    if (getStoredToken()) fetchBillingMe().then(m => alive && setMe(m)).catch(() => alive && setMe(null));
    return () => { alive = false; };
  }, []);
  return me;
}

/** The plan that unlocks `feature` for this caller, or null when it is open (included, exempt, or not enforced). */
export function lockedBy(me: BillingMe | null, feature: 'pdf_export' | 'excel_export'): Tier | null {
  if (!me || !me.usage.enforced || me.usage.exempt || me.limits[feature]) return null;
  return feature === 'excel_export' ? 'professional' : 'starter';
}

export const PLAN_NAMES: Record<Tier, string> = { free: 'Free', starter: 'Starter', professional: 'Professional' };

export const isUnlimited = (n: number) => n === UNLIMITED;
export const fmtLimit = (n: number) => (n === UNLIMITED ? 'Unlimited' : String(n));
