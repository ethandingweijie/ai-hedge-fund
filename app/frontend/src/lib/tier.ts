/**
 * Tier profile definitions.
 * Single source of truth for feature limits across all pages.
 * Active tier is persisted to localStorage.
 */

export type Tier = 'free' | 'starter' | 'professional';

export interface TierProfile {
  id: Tier;
  name: string;
  priceMonthly: number | null;   // null = free
  priceAnnual: number | null;    // null = free, otherwise yearly total
  runsPerMonth: number;          // 1 for free (lifetime cap), Infinity = unlimited
  lifetimeCapOnly: boolean;      // true = the run count is a one-time lifetime cap
  watchlistLimit: number;        // Infinity = unlimited
  screenerLimit: number;         // Infinity = unlimited results
  historyMonths: number;         // 0 = no access
  pdfExport: boolean;
  deepResearch: boolean;
  agentSelection: boolean;
  batchTickers: number;          // max tickers per batch run (1 = single only)
  apiAccess: boolean;
  addOnRuns: { count: number; price: number } | null;
}

export const TIER_PROFILES: Record<Tier, TierProfile> = {
  free: {
    id: 'free',
    name: 'Free',
    priceMonthly: null,
    priceAnnual: null,
    runsPerMonth: 1,
    lifetimeCapOnly: true,
    watchlistLimit: 3,
    screenerLimit: 5,
    historyMonths: 0,
    pdfExport: false,
    deepResearch: false,
    agentSelection: false,
    batchTickers: 1,
    apiAccess: false,
    addOnRuns: null,
  },
  starter: {
    id: 'starter',
    name: 'Starter',
    priceMonthly: 29,
    priceAnnual: 290,
    runsPerMonth: 5,
    lifetimeCapOnly: false,
    watchlistLimit: 15,
    screenerLimit: Infinity,
    historyMonths: 6,
    pdfExport: true,
    deepResearch: true,
    agentSelection: false,
    batchTickers: 1,
    apiAccess: false,
    addOnRuns: { count: 5, price: 12 },
  },
  professional: {
    id: 'professional',
    name: 'Professional',
    priceMonthly: 79,
    priceAnnual: 790,
    runsPerMonth: 20,
    lifetimeCapOnly: false,
    watchlistLimit: Infinity,
    screenerLimit: Infinity,
    historyMonths: 24,
    pdfExport: true,
    deepResearch: true,
    agentSelection: true,
    batchTickers: 3,
    apiAccess: false,
    addOnRuns: { count: 10, price: 18 },
  },
};

export const TIER_ORDER: Tier[] = ['free', 'starter', 'professional'];

/**
 * Agents available to the Starter tier.
 * These are classic deep-value / quality-value investors.
 * All other agents require Professional.
 */
export const STARTER_ALLOWED_AGENTS = [
  'graham',      // Benjamin Graham — margin of safety
  'buffett',     // Warren Buffett — moat / owner earnings
  'munger',      // Charlie Munger — mental models / quality
  'pabrai',      // Mohnish Pabrai — Dhandho / concentrated value
  'fisher',      // Phil Fisher — scuttlebutt / quality compounders
  'burry',       // Michael Burry — forensic / deep value
  'damodaran',   // Aswath Damodaran — DCF / valuation
] as const;

export type StarterAgent = (typeof STARTER_ALLOWED_AGENTS)[number];

const STORAGE_KEY = 'ai_hf_tier';

export function getActiveTier(): Tier {
  const stored = localStorage.getItem(STORAGE_KEY) as Tier | null;
  return stored && stored in TIER_PROFILES ? stored : 'free';
}

export function setActiveTier(tier: Tier): void {
  localStorage.setItem(STORAGE_KEY, tier);
  // Notify other tabs/components
  window.dispatchEvent(new CustomEvent('tierchange', { detail: tier }));
}

export function getProfile(): TierProfile {
  return TIER_PROFILES[getActiveTier()];
}

/** True if the current tier meets or exceeds the required tier. */
export function hasAccess(required: Tier): boolean {
  return TIER_ORDER.indexOf(getActiveTier()) >= TIER_ORDER.indexOf(required);
}
