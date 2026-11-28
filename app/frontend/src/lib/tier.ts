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
  price_monthly: number;
  price_annual: number;
  annual_saving_pct: number;
  credits: number;
  credit_period: 'lifetime' | 'month';
  watchlist_limit: number;   // UNLIMITED = -1
  screener_limit: number;    // UNLIMITED = -1
  history_months: number;    // UNLIMITED = -1
  pdf_export: boolean;
  excel_export: boolean;
  addon: { credits: number; price: number } | null;
  purchasable: boolean;
}

export interface PlansResponse {
  plans: PlanInfo[];
  every_run: string[];
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

/** An Error carrying the server's plan message for a 402, else `HTTP <status>: <body>`. */
export async function errorFromResponse(res: Response): Promise<Error> {
  const text = await res.text().catch(() => res.statusText);
  if (res.status === 402 || (res.status === 503 && res.url.includes('/billing/'))) {
    try {
      const detail = JSON.parse(text)?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message;
      if (msg) {
        const e = new Error(msg) as Error & { code?: string; planLimit?: boolean };
        e.code = typeof detail === 'object' ? detail?.code : undefined;
        e.planLimit = res.status === 402;
        return e;
      }
    } catch { /* not JSON */ }
  }
  return new Error(`HTTP ${res.status}: ${text}`);
}

export const fetchPlans = () => getJson<PlansResponse>('/billing/plans');
export const fetchBillingMe = () => getJson<BillingMe>('/billing/me');

export async function startCheckout(plan: Tier, interval: 'monthly' | 'annual' | 'addon'): Promise<void> {
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

export const isUnlimited = (n: number) => n === UNLIMITED;
export const fmtLimit = (n: number) => (n === UNLIMITED ? 'Unlimited' : String(n));
