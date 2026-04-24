import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Platform detection utility
export function isMac(): boolean {
  return typeof navigator !== 'undefined' && navigator.platform.toUpperCase().indexOf('MAC') >= 0;
}

// Keyboard shortcut formatting utility
export function formatKeyboardShortcut(key: string): string {
  const modifierKey = isMac() ? '⌘' : 'Ctrl';
  return `${modifierKey}${key.toUpperCase()}`;
}

/**
 * Return the currency symbol for a ticker.
 * HK tickers are purely numeric (1–5 digits) with an optional .HK suffix.
 * SG tickers end with .SI suffix.
 * All others default to USD ($).
 */
export function currencySymbol(ticker: string): string {
  if (!ticker) return '$';
  const upper = ticker.trim().toUpperCase();
  // SG: ends with .SI
  if (upper.endsWith('.SI')) return 'S$';
  // HK: purely numeric
  const cleanHK = upper.replace(/\.HK$/, '');
  if (/^\d{1,5}$/.test(cleanHK)) return 'HK$';
  return '$';
}

/**
 * Extract the latest fiscal-year values for R&D, revenue, and FCF from the
 * FY-keyed raw_financials dict (shape: `{FY2022: {revenue, ...}, FY2023: ...}`).
 *
 * Returns nulls for any field that isn't present on the newest FY. Used by the
 * Biopharma valuation panel (and potentially others) to avoid reimplementing
 * the "which key is newest?" logic at every call site.
 *
 * Scale: raw FMP values are in dollars (not billions). Callers downstream decide
 * whether to convert (BiopharmaValuationPanel detects scale via `Math.abs(v) > 1e6`).
 */
export function extractLatestFinancials(
  rawFinancials: Record<string, unknown> | null | undefined
): { rd_spend: number | null; revenue: number | null; fcf: number | null } {
  if (!rawFinancials || typeof rawFinancials !== 'object') {
    return { rd_spend: null, revenue: null, fcf: null };
  }
  // FY keys look like "FY2023", "FY2024", "2023", etc. Lexical sort works for
  // consistent zero-padded formats; we take the last (newest).
  const fyKeys = Object.keys(rawFinancials)
    .filter(k => rawFinancials[k] && typeof rawFinancials[k] === 'object')
    .sort();
  if (fyKeys.length === 0) return { rd_spend: null, revenue: null, fcf: null };
  const latest = rawFinancials[fyKeys[fyKeys.length - 1]] as Record<string, unknown>;
  const num = (v: unknown): number | null => {
    if (v == null) return null;
    const n = typeof v === 'number' ? v : parseFloat(String(v));
    return isNaN(n) ? null : n;
  };
  return {
    rd_spend: num(latest.research_and_development),
    revenue:  num(latest.revenue),
    fcf:      num(latest.free_cash_flow),
  };
}

/**
 * True if the given sector string denotes a biopharma / biotech company.
 *
 * Why this exists: the LLM-driven sector classifier can emit any of these
 * strings for what's conceptually the same thing —
 *   "Biopharma"  (internal canonical — validated via TICKER_SECTOR_LOOKUP)
 *   "Biotechnology", "Biotech", "biotech"  (LLM-returned variants)
 *   "Healthcare / Biotechnology", "Biopharma/Biotech"  (compound forms)
 *   "Pharmaceuticals"  (sometimes emitted for big pharma)
 *
 * A strict `sector === 'Biopharma'` check misses all of these. This helper
 * catches the common roots case-insensitively and tolerates compound strings.
 *
 * Trade-off: it may match some "healthcare" tickers that aren't drug companies
 * (e.g. Teladoc, GoodRx). That's acceptable because the BiopharmaValuationPanel
 * gracefully renders an empty pipeline message when `pipeline_assets` is
 * missing — misrouting to it costs ~1 card of wasted space, NOT a crash.
 */
export function isBiopharmaSector(sector: string | null | undefined): boolean {
  if (!sector || typeof sector !== 'string') return false;
  const s = sector.toLowerCase();
  return s.includes('biopharm') || s.includes('biotech') || s.includes('pharmaceutical');
}

/**
 * True if the given sector string denotes a tech / software company.
 *
 * Matches "Tech", "Technology", any sector containing "software",
 * "information technology", plain "it", or "it services" — case-insensitively.
 * Mirrors the backend `is_tech_sector` helper in
 * src/agents/industry/sector_prompts.py so frontend + backend gate identically
 * on the same LLM-classifier sector variants.
 *
 * Used by the report pages to gate the Tech-specific valuation panel. Note
 * that sector match alone is NOT enough — see classifyTechProfile below:
 * we only render the Tech panel for profiles we can classify into a
 * sub-type, otherwise we fall through to the generic ValuationLadder.
 */
export function isTechSector(sector: string | null | undefined): boolean {
  if (!sector || typeof sector !== 'string') return false;
  const s = sector.toLowerCase();
  return (
    s === 'tech' || s === 'technology' || s.includes('software')
    || s.includes('information technology') || s === 'it' || s.includes('it services')
  );
}

/** Tech sub-type (one of three views the panel routes between, or null when
 *  the profile string can't be classified). */
export type TechSubtype = 'hyperscaler' | 'mature_saas' | 'growth_saas' | null;

/**
 * Classify a profile string into one of the Tech sub-type views, or null when
 * no sub-type matches. Callers MUST treat `null` as "fall through to the
 * generic ValuationLadder" — sub-type screens render ONLY for their
 * sub-segment; we never show a generic Tech panel on an unknown sub-type.
 *
 * Patterns matched (case-insensitive substring):
 *   - 'hyperscaler' / 'conglomerate'        → 'hyperscaler'
 *   - 'mature saas' / 'mature'              → 'mature_saas'
 *   - 'growth saas' / 'hyper-growth' / 'high-growth' / 'cybersecurity' /
 *     'mission-critical'                    → 'growth_saas'
 */
export function classifyTechProfile(profile: string | null | undefined): TechSubtype {
  if (!profile || typeof profile !== 'string') return null;
  const p = profile.toLowerCase();
  if (p.includes('hyperscaler') || p.includes('conglomerate')) return 'hyperscaler';
  if (p.includes('mature saas') || p.includes('mature')) return 'mature_saas';
  if (p.includes('growth saas') || p.includes('hyper-growth') || p.includes('high-growth')
      || p.includes('cybersecurity') || p.includes('mission-critical')) return 'growth_saas';
  return null;
}

/**
 * Ticker → TechSubtype lookup. Mirrors the `profile_name` overrides in the
 * backend's TICKER_SECTOR_LOOKUP for common Tech tickers. Used as a FALLBACK
 * when a historical run's stored data is missing `profile_name` (old runs
 * made before the backend started emitting profile_names to state).
 *
 * WHY this exists: the Tech gate is strict — `isTechSector(sector) &&
 * classifyTechProfile(profile)` — so historical runs with just `sector="Tech"`
 * but no profile fall through to the generic ValuationLadder even though we
 * know from the ticker which sub-type panel SHOULD render. This map closes
 * that gap for the ~30 most-analysed Tech tickers.
 *
 * New/unknown tickers fall through to the generic panel (same as before) —
 * no silent mis-routing.
 */
const TECH_TICKER_PROFILES: Record<string, TechSubtype> = {
  // Hyperscalers
  MSFT: 'hyperscaler', AMZN: 'hyperscaler', GOOGL: 'hyperscaler', GOOG: 'hyperscaler',
  META: 'hyperscaler', ORCL: 'hyperscaler',
  // Mature SaaS
  CRM: 'mature_saas', NOW: 'mature_saas', ADBE: 'mature_saas', WDAY: 'mature_saas',
  SAP: 'mature_saas', INTU: 'mature_saas', VEEV: 'mature_saas',
  // Growth SaaS
  SNOW: 'growth_saas', PLTR: 'growth_saas', HUBS: 'growth_saas', FRSH: 'growth_saas',
  DDOG: 'growth_saas', MDB: 'growth_saas', TEAM: 'growth_saas', ZM: 'growth_saas',
  OKTA: 'growth_saas', TWLO: 'growth_saas', MNDY: 'growth_saas', BILL: 'growth_saas',
  GTLB: 'growth_saas', S: 'growth_saas',
  // Cybersecurity (classified as growth_saas on frontend — backend marks profile
  // explicitly "Cybersecurity / Mission-Critical SaaS" which matches growth_saas
  // in classifyTechProfile, so fresh runs route correctly; these entries cover
  // historical runs)
  CRWD: 'growth_saas', PANW: 'growth_saas', ZS: 'growth_saas', FTNT: 'growth_saas',
  NET: 'growth_saas',
};

/**
 * Resolve the Tech sub-type using the profile string FIRST, then falling back
 * to a ticker lookup for historical runs that don't have profile stored.
 * Returns null when neither path yields a match (→ generic ladder renders).
 */
export function classifyTechSubtype(
  profile: string | null | undefined,
  ticker: string | null | undefined,
): TechSubtype {
  const fromProfile = classifyTechProfile(profile);
  if (fromProfile) return fromProfile;
  if (!ticker) return null;
  return TECH_TICKER_PROFILES[ticker.toUpperCase()] ?? null;
}

// Provider color utility for consistent styling across components
export function getProviderColor(provider: string): string {
  return 'bg-gray-600/20 text-primary border-gray-600/40';
  // switch (provider.toLowerCase()) {
  //   case 'anthropic':
  //     return 'bg-orange-600/20 text-orange-300 border-orange-600/40';
  //   case 'google':
  //     return 'bg-green-600/20 text-green-300 border-green-600/40';
  //   case 'groq':
  //     return 'bg-red-600/20 text-red-300 border-red-600/40';
  //   case 'deepseek':
  //     return 'bg-blue-600/20 text-blue-300 border-blue-600/40';
  //   case 'openai':
  //     return 'bg-gray-900/60 text-gray-200 border-gray-700/60';
  //   case 'ollama':
  //     return 'bg-white/90 text-gray-800 border-gray-300';
  //   default:
  //     return 'bg-gray-600/20 text-gray-300 border-gray-600/40';
  // }
}
