const FMP_BASE = "https://financialmodelingprep.com/stable";

function getApiKey(): string {
  const key = process.env.FMP_API_KEY;
  if (!key || key === "your_fmp_api_key_here") {
    throw new Error("FMP_API_KEY not configured");
  }
  return key;
}

export interface StockQuote {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changesPercentage: number;
  dayLow: number;
  dayHigh: number;
  yearLow: number;
  yearHigh: number;
  marketCap: number;
  volume: number;
  avgVolume?: number;
  eps?: number;
  pe?: number;
}

export interface StockProfile {
  symbol: string;
  companyName?: string;
  price: number;
  mktCap?: number;
  marketCap?: number;
  industry?: string;
  sector?: string;
  country?: string;
  exchange?: string;
  description?: string;
  beta?: number;
  volAvg?: number;
  lastDiv?: number;
  lastDividend?: number;
  range?: string;
  changes?: number;
  change?: number;
  website?: string;
  ceo?: string;
  isEtf?: boolean;
  isActivelyTrading?: boolean;
}

export interface HistoricalPrice {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Stable API returns these fields — normalize to our StockQuote shape
function normalizeQuote(raw: Record<string, unknown>): StockQuote {
  return {
    symbol: String(raw.symbol ?? ""),
    name: String(raw.name ?? ""),
    price: Number(raw.price ?? 0),
    change: Number(raw.change ?? raw.changes ?? 0),
    changesPercentage: Number(raw.changePercentage ?? raw.changesPercentage ?? 0),
    dayLow: Number(raw.dayLow ?? 0),
    dayHigh: Number(raw.dayHigh ?? 0),
    yearLow: Number(raw.yearLow ?? 0),
    yearHigh: Number(raw.yearHigh ?? 0),
    marketCap: Number(raw.marketCap ?? 0),
    volume: Number(raw.volume ?? 0),
    avgVolume: Number(raw.avgVolume ?? 0),
    eps: raw.eps != null ? Number(raw.eps) : undefined,
    pe: raw.pe != null ? Number(raw.pe) : undefined,
  };
}

export async function getQuote(symbol: string): Promise<StockQuote | null> {
  try {
    const res = await fetch(
      `${FMP_BASE}/quote?symbol=${encodeURIComponent(symbol)}&apikey=${getApiKey()}`,
      { next: { revalidate: 300 } },
    );
    if (!res.ok) return null;
    const data = await res.json();
    const item = Array.isArray(data) ? data[0] : data;
    return item ? normalizeQuote(item) : null;
  } catch {
    return null;
  }
}

/**
 * Fetch quotes for multiple symbols in parallel.
 * The stable API only supports single-symbol quote requests,
 * so we fan out in parallel and aggregate the results.
 */
export async function getQuotes(
  symbols: string[],
): Promise<Record<string, StockQuote>> {
  if (symbols.length === 0) return {};
  const key = getApiKey();

  const results = await Promise.allSettled(
    symbols.map(async (sym) => {
      const res = await fetch(
        `${FMP_BASE}/quote?symbol=${encodeURIComponent(sym)}&apikey=${key}`,
        { next: { revalidate: 300 } },
      );
      if (!res.ok) return null;
      const data = await res.json();
      const item = Array.isArray(data) ? data[0] : data;
      return item ? normalizeQuote(item) : null;
    }),
  );

  const map: Record<string, StockQuote> = {};
  for (let i = 0; i < symbols.length; i++) {
    const result = results[i];
    if (result.status === "fulfilled" && result.value) {
      map[symbols[i]] = result.value;
    }
  }
  return map;
}

export async function getProfile(symbol: string): Promise<StockProfile | null> {
  try {
    const res = await fetch(
      `${FMP_BASE}/profile?symbol=${encodeURIComponent(symbol)}&apikey=${getApiKey()}`,
      { next: { revalidate: 3600 } },
    );
    if (!res.ok) return null;
    const data = await res.json();
    return (Array.isArray(data) ? data[0] : data) ?? null;
  } catch {
    return null;
  }
}

export async function getHistoricalPrices(
  symbol: string,
  from: string,
  to: string,
): Promise<HistoricalPrice[]> {
  try {
    const res = await fetch(
      `${FMP_BASE}/historical-price-full?symbol=${encodeURIComponent(symbol)}&from=${from}&to=${to}&apikey=${getApiKey()}`,
      { next: { revalidate: 3600 } },
    );
    if (!res.ok) return [];
    const data = await res.json();
    return data?.historical ?? [];
  } catch {
    return [];
  }
}

export async function searchTicker(
  query: string,
  limit = 10,
): Promise<
  { symbol: string; name: string; currency: string; stockExchange: string }[]
> {
  try {
    const res = await fetch(
      `${FMP_BASE}/search-name?query=${encodeURIComponent(query)}&limit=${limit}&apikey=${getApiKey()}`,
      { next: { revalidate: 3600 } },
    );
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}
