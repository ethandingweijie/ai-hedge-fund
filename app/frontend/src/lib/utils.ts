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
