import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { SlidersHorizontal, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { getScreenerStocks, lookupScreenerTicker, addToWatchlist, getScreenerPrices, getHkScreenerStocks } from '@/lib/api';
import type { ScreenerStock, ScreenerResponse } from '@/lib/reportTypes';
import { ResearchNav } from '@/components/layout/ResearchNav';
import { getProfile } from '@/lib/tier';
import { gradeColorClass, formatSector } from '@/lib/gradeColors';
import { AgentOrbIcon } from '@/components/report/AgentOrbIcon';
import { useIsMobile } from '@/hooks/use-mobile';
import { SwipeableCard } from '@/components/mobile/SwipeableCard';

// ── Constants ──────────────────────────────────────────────────────────────────

const SECTORS = [
  'All',
  'Technology',
  'Healthcare',
  'Consumer Cyclical',
  'Financial Services',
  'Communication Services',
  'Consumer Defensive',
  'Energy',
  'Industrials',
  'Basic Materials',
  'Real Estate',
  'Utilities',
];

const EXCHANGES = ['All', 'NASDAQ', 'NYSE', 'AMEX'];

// HK sectors — short-form names used in HK screener data
const HK_SECTORS = [
  'All',
  'Tech',
  'Consumer',
  'Financials',
  'Industrials',
  'Biopharma',
  'RealEstate',
  'Telco',
  'Energy',
];

const MARKET_CAP_RANGES: { label: string; min: number; max?: number }[] = [
  { label: '$2B – $12B',    min: 2_000_000_000,   max: 12_000_000_000  },
  { label: '$12B – $50B',   min: 12_000_000_000,  max: 50_000_000_000  },
  { label: '$50B – $100B',  min: 50_000_000_000,  max: 100_000_000_000 },
  { label: '$100B – $500B', min: 100_000_000_000, max: 500_000_000_000 },
  { label: '$500B – $1T',   min: 500_000_000_000, max: 1_000_000_000_000 },
  { label: '> $1T',         min: 1_000_000_000_000 },
];
const CAP_RANGE_LABELS = ['All', ...MARKET_CAP_RANGES.map(r => r.label)];

// HK market cap ranges (HKD) — default floor is HK$15B
const HK_DEFAULT_CAP = 'All (≥ HK$15B)';
const HK_MARKET_CAP_RANGES: { label: string; min: number; max?: number }[] = [
  { label: 'HK$15B – HK$50B',   min: 15_000_000_000,  max: 50_000_000_000  },
  { label: 'HK$50B – HK$200B',  min: 50_000_000_000,  max: 200_000_000_000 },
  { label: 'HK$200B – HK$500B', min: 200_000_000_000, max: 500_000_000_000 },
  { label: '> HK$500B',         min: 500_000_000_000  },
];
const HK_CAP_RANGE_LABELS = [HK_DEFAULT_CAP, ...HK_MARKET_CAP_RANGES.map(r => r.label)];

type Market  = 'US' | 'HK';
type SortKey = 'composite' | 'valuation' | 'growth' | 'profitability' | 'momentum';

const SORT_LABELS: Record<SortKey, string> = {
  composite:     'Overall',
  valuation:     'V',
  growth:        'G',
  profitability: 'P',
  momentum:      'M',
};

function getSortScore(s: ScreenerStock, key: SortKey): number {
  if (key === 'composite') return s.composite_score ?? -1;
  return s.vgpm?.[key]?.score ?? -1;
}

// ── Grade pill ─────────────────────────────────────────────────────────────────

function GradePill({ grade, estimated }: { grade?: string; estimated?: boolean }) {
  if (!grade) return <span className="text-muted-foreground/40 text-xs">—</span>;
  return (
    <span
      className={`text-sm font-mono font-bold px-1.5 py-0.5 rounded ${gradeColorClass(grade)}`}
      title={estimated ? 'FMP-estimated score (run full analysis for authoritative grade)' : 'Pipeline-verified score'}
    >
      {grade}
    </span>
  );
}

// ── Market cap formatter ───────────────────────────────────────────────────────

function fmtCap(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

function fmtVol(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(v);
}

// ── Skeleton loader ────────────────────────────────────────────────────────────

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 10 }).map((_, i) => (
        <TableRow key={i} className="animate-pulse">
          {Array.from({ length: 13 }).map((__, j) => (
            <TableCell key={j}>
              <div className="h-3 bg-muted rounded w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function ScreenerPage() {
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [showFilters, setShowFilters] = useState(false);

  // ── Market tab (US / HK)
  const [market,   setMarket]   = useState<Market>('US');

  // ── Filters
  const [search,   setSearch]   = useState('');
  const [sector,   setSector]   = useState('All');
  const [exchange, setExchange] = useState('All');
  const [capRange, setCapRange] = useState('All');
  const [vgpmOnly, setVgpmOnly] = useState(false);
  const [activeSort, setActiveSort] = useState<SortKey>('composite');
  const [watchlistAdded, setWatchlistAdded] = useState<Set<string>>(new Set());

  // ── Data
  const [data,         setData]         = useState<ScreenerResponse | null>(null);
  const [loading,      setLoading]      = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const [lookupResult, setLookupResult] = useState<ScreenerStock | null>(null);
  const [lookupLoading,setLookupLoading]= useState(false);

  // ── Live price refresh (every 15s) + flash indicators
  const [priceFlash,    setPriceFlash]    = useState<Record<string, 'up' | 'down'>>({});
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const prevPricesRef  = useRef<Record<string, number>>({});
  const flashTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const dataRef        = useRef<ScreenerResponse | null>(null);

  // Keep dataRef in sync so the interval closure always sees fresh data
  useEffect(() => {
    dataRef.current = data;
    // Seed prevPrices on first load so first auto-refresh doesn't flash everything
    data?.items.forEach(s => {
      if (s.price != null && prevPricesRef.current[s.symbol] == null)
        prevPricesRef.current[s.symbol] = s.price;
    });
  }, [data]);

  // 15-second silent price refresh — patches data.items directly so sorted re-renders
  useEffect(() => {
    const tick = async () => {
      const current = dataRef.current;
      if (!current?.items.length) return;
      // Only refresh prices for the top 50 tickers (by composite score) —
      // refreshing all 1,600+ tickers would jam the FMP API quota.
      const top = [...current.items]
        .sort((a, b) => (b.composite_score ?? -1) - (a.composite_score ?? -1))
        .slice(0, 50);
      const syms = top.map(s => s.symbol);
      try {
        const quotes = await getScreenerPrices(syms);
        if (!Object.keys(quotes).length) return;

        // Compute flashes + update prevPricesRef OUTSIDE the state updater
        // (state updaters must be pure; Strict Mode calls them twice)
        const flashes: Record<string, 'up' | 'down'> = {};
        Object.entries(quotes).forEach(([sym, q]) => {
          const newP = q.price;
          if (newP == null) return;
          const oldP = prevPricesRef.current[sym];
          if (oldP != null && newP !== oldP)
            flashes[sym] = newP > oldP ? 'up' : 'down';
          prevPricesRef.current[sym] = newP;
        });

        // Pure state update — only returns new state, no side effects
        setData(prev => {
          if (!prev) return prev;
          const newItems = prev.items.map(item => {
            const q = quotes[item.symbol];
            if (!q) return item;
            return {
              ...item,
              price:      q.price      ?? item.price,
              marketCap:  q.marketCap  ?? item.marketCap,
              volume:     q.volume     ?? item.volume,
              beta:       q.beta       ?? item.beta,
              change_pct: q.change_pct != null ? q.change_pct : item.change_pct,
            };
          });
          return { ...prev, items: newItems };
        });

        setLastRefreshed(new Date());
        if (Object.keys(flashes).length) {
          setPriceFlash(prev => ({ ...prev, ...flashes }));
          Object.keys(flashes).forEach(sym => {
            if (flashTimersRef.current[sym]) clearTimeout(flashTimersRef.current[sym]);
            flashTimersRef.current[sym] = setTimeout(() =>
              setPriceFlash(prev => { const n = { ...prev }; delete n[sym]; return n; }), 1800);
          });
        }
      } catch { /* silent — don't disrupt UI */ }
    };

    // Fire once immediately (after data loads via dataRef) then every 15s
    const initial = setTimeout(tick, 3000);
    const id = setInterval(tick, 15000);
    return () => { clearTimeout(initial); clearInterval(id); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async (forceRefresh = false) => {
    setLoading(true);
    setError(null);
    setLookupResult(null);
    try {
      if (market === 'HK') {
        const result = await getHkScreenerStocks(forceRefresh);
        setData(result);
      } else {
        const capDef = MARKET_CAP_RANGES.find(r => r.label === capRange);
        const result = await getScreenerStocks({
          sector:       sector   !== 'All' ? sector   : undefined,
          exchange:     exchange !== 'All' ? exchange : undefined,
          marketCapMin: capDef ? capDef.min : 2_000_000_000, // default $2B+ floor
          marketCapMax: capDef?.max,
          refresh:      forceRefresh,
        });
        setData(result);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [market, sector, exchange, capRange]); // re-created when filters/market change → effect below auto-refetches

  // Load on mount and whenever filters change
  useEffect(() => { load(); }, [load]);

  // Direct ticker lookup when search returns 0 results and looks like a symbol
  useEffect(() => {
    const q = search.trim().toUpperCase();
    if (!q || q.length < 1 || q.length > 8) { setLookupResult(null); return; }
    // Only trigger lookup when no results found in loaded data
    if (!data) return;
    const hasMatch = data.items.some(
      s => s.symbol.toUpperCase().includes(q) || s.companyName.toUpperCase().includes(q)
    );
    if (hasMatch) { setLookupResult(null); return; }
    // Looks like a ticker with no local match — try direct lookup
    setLookupLoading(true);
    setLookupResult(null);
    lookupScreenerTicker(q)
      .then(r => setLookupResult(r))
      .catch(() => setLookupResult(null))
      .finally(() => setLookupLoading(false));
  }, [search, data]);

  // ── Client-side sort + optional VGPM filter + search
  const sorted = useMemo(() => {
    const q = search.trim().toUpperCase();
    let items = data ? [...data.items] : [];
    if (q) items = items.filter(s =>
      s.symbol.toUpperCase().includes(q) ||
      s.companyName.toUpperCase().includes(q)
    );
    if (vgpmOnly) items = items.filter(s => s.vgpm !== null);
    // HK client-side filters (sector + market cap) — all HK data pre-loaded in one fetch
    if (market === 'HK' && sector !== 'All') {
      items = items.filter(s => s.sector === sector);
    }
    // HK market cap filter (client-side — all HK data pre-loaded)
    // Stocks with null marketCap are included (unknown = give benefit of doubt;
    // once the cache refreshes with market_cap_hkd data, filtering becomes precise)
    if (market === 'HK') {
      if (capRange === HK_DEFAULT_CAP) {
        // Default floor: HK$15B+ (null = unknown → include)
        items = items.filter(s => s.marketCap == null || (s.marketCap as number) >= 15_000_000_000);
      } else {
        const hkDef = HK_MARKET_CAP_RANGES.find(r => r.label === capRange);
        if (hkDef) {
          items = items.filter(s =>
            s.marketCap == null || (
              (s.marketCap as number) >= hkDef.min &&
              (hkDef.max == null || (s.marketCap as number) <= hkDef.max)
            )
          );
        }
      }
    }
    // If lookup found a result not already in the batch, prepend it
    if (lookupResult && !items.some(s => s.symbol === lookupResult.symbol)) {
      items = [lookupResult, ...items];
    }
    return items.sort((a, b) => getSortScore(b, activeSort) - getSortScore(a, activeSort));
  }, [data, activeSort, vgpmOnly, search, lookupResult, market, capRange]);

  // ── Tier enforcement
  // Backend fetches up to 200 candidates; UI shows the best 20 after scoring + filters.
  const DISPLAY_CAP = 20;
  const tierProfile = getProfile();
  const screenerLimit = tierProfile.screenerLimit;
  const isFree = tierProfile.id === 'free';
  const capped = sorted.slice(0, DISPLAY_CAP);
  const visibleSorted = screenerLimit === Infinity ? capped : capped.slice(0, screenerLimit);
  const isLimited = screenerLimit !== Infinity && sorted.length > screenerLimit;

  // ── Derived stats
  const vgpmCount = data ? data.items.filter(s => s.vgpm !== null).length : 0;

  // ── MOBILE LAYOUT ─────────────────────────────────────────────────────────────
  if (isMobile) {
    const isDark = document.documentElement.classList.contains('dark');
    return (
      <div
        className="min-h-screen flex flex-col"
        style={isDark ? { backgroundColor: '#1e2028' } : { backgroundImage: 'url(/bg-wallpaper.jpg)', backgroundSize: 'cover', backgroundPosition: 'center' }}
      >
        {!isDark && <div className="absolute inset-0 bg-black/40 pointer-events-none" />}
        <div className="relative z-10 flex flex-col min-h-screen">
          {/* Hero header */}
          <div className="px-4 pt-4 pb-6">
            {/* Header row — centred */}
            <div className="flex items-center justify-center gap-3 mb-4">
              <div className="flex items-center gap-2">
                {/* Market tabs */}
                <div className="flex items-center rounded-lg border border-white/30 p-0.5 bg-white/10 backdrop-blur">
                  {(['US', 'HK'] as Market[]).map(m => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => { setMarket(m); setData(null); setSearch(''); setSector('All'); setExchange('All'); setCapRange(m === 'HK' ? HK_DEFAULT_CAP : 'All'); }}
                      className={`px-4 py-1.5 text-sm font-semibold rounded-md transition-colors ${
                        market === m
                          ? 'bg-white text-green-900 shadow-sm'
                          : 'text-white/70 hover:text-white'
                      }`}
                    >
                      {m}
                    </button>
                  ))}
                </div>
                <Button size="sm" onClick={() => navigate('/report')} className="bg-white/15 backdrop-blur text-white border-white/30 hover:bg-white/25 text-xs">
                  + New Analysis
                </Button>
              </div>
            </div>
          </div>

          <div className="px-4 py-3 space-y-3">
          {/* Search + Filters row */}
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Ticker or company…"
              className="flex-1 h-11 rounded-full border border-white/20 bg-white/90 dark:bg-card/90 px-4 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <button
              onClick={() => setShowFilters(f => !f)}
              className={`h-11 px-4 rounded-full border flex items-center gap-1.5 text-sm font-medium transition-colors ${
                showFilters ? 'bg-primary text-primary-foreground border-primary' : 'border-white/20 bg-white/90 dark:bg-card/90 text-muted-foreground'
              }`}
            >
              <SlidersHorizontal size={14} />
              Filters
            </button>
            <button
              onClick={() => load(true)}
              disabled={loading}
              className="h-11 w-11 rounded-full border border-white/20 bg-white/90 dark:bg-card/90 flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
            >
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>

          {/* Collapsible filter panel */}
          {showFilters && (
            <Card className="p-3 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] text-muted-foreground block mb-1 uppercase tracking-wider">Sector</label>
                  <select value={sector} onChange={e => setSector(e.target.value)} className="w-full h-9 rounded-md border border-input bg-background px-2 text-sm">
                    {(market === 'HK' ? HK_SECTORS : SECTORS).map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                {market === 'US' && (
                  <div>
                    <label className="text-[10px] text-muted-foreground block mb-1 uppercase tracking-wider">Exchange</label>
                    <select value={exchange} onChange={e => setExchange(e.target.value)} className="w-full h-9 rounded-md border border-input bg-background px-2 text-sm">
                      {EXCHANGES.map(x => <option key={x} value={x}>{x}</option>)}
                    </select>
                  </div>
                )}
                <div>
                  <label className="text-[10px] text-muted-foreground block mb-1 uppercase tracking-wider">Market Cap</label>
                  <select value={capRange} onChange={e => setCapRange(e.target.value)} className="w-full h-9 rounded-md border border-input bg-background px-2 text-sm">
                    {(market === 'HK' ? HK_CAP_RANGE_LABELS : CAP_RANGE_LABELS).map(l => <option key={l} value={l}>{l}</option>)}
                  </select>
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={vgpmOnly} onChange={e => setVgpmOnly(e.target.checked)} className="rounded" />
                VGPM scored only
              </label>
              {/* Sort pills */}
              <div className="flex items-center gap-1">
                <span className="text-xs text-muted-foreground mr-1">Sort by:</span>
                {(Object.keys(SORT_LABELS) as SortKey[]).map(key => (
                  <button key={key} type="button" onClick={() => setActiveSort(key)}
                    className={`text-xs font-bold px-2.5 py-1 rounded-full border transition-colors ${
                      activeSort === key ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground'
                    }`}>{SORT_LABELS[key]}</button>
                ))}
              </div>
            </Card>
          )}

          {/* Column headers — aligned over VGPM in cards */}
          <div className="flex items-center px-3 mb-1">
            <div className="flex-1" />
            <div className="flex items-center gap-0">
              <span className="w-[52px] text-center text-[7px] font-semibold uppercase tracking-wider text-white/70">Valuation</span>
              <span className="w-[52px] text-center text-[7px] font-semibold uppercase tracking-wider text-white/70">Growth</span>
              <span className="w-[52px] text-center text-[7px] font-semibold uppercase tracking-wider text-white/70">Profit.</span>
              <span className="w-[52px] text-center text-[7px] font-semibold uppercase tracking-wider text-white/70">Momentum</span>
              <span className="w-[42px] text-center text-[7px] font-semibold uppercase tracking-wider text-white/70">Score</span>
            </div>
          </div>

          {/* Stock cards */}
          {loading && !data ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading…</div>
          ) : sorted.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">No stocks found.</div>
          ) : (
            <div className="space-y-2">
              {visibleSorted.map(stock => (
                <SwipeableCard
                  key={stock.symbol}
                  onClick={() => { sessionStorage.setItem('screener_prefill', stock.symbol); navigate('/report'); }}
                  className="w-full bg-white/85 dark:bg-[#252830] backdrop-blur-sm border border-white/40 border-l-[3px] border-l-green-600/50 px-3 py-2.5 text-left flex items-center shadow-sm cursor-pointer"
                  actions={[
                    {
                      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>,
                      color: 'bg-blue-500',
                      onClick: () => { sessionStorage.setItem('screener_prefill', stock.symbol); navigate('/report'); },
                    },
                    {
                      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>,
                      color: 'bg-green-500',
                      onClick: () => { addToWatchlist(stock.symbol).then(() => setWatchlistAdded(prev => new Set(prev).add(stock.symbol))).catch(() => {}); },
                    },
                  ]}
                >
                  {/* Left: Ticker/Company + Price/Change stacked */}
                  <div className="shrink-0 min-w-0">
                    <div className="flex items-baseline gap-2">
                      <span className="font-bold text-[15px] leading-none w-[72px] shrink-0">{stock.symbol}</span>
                      <span className="text-sm font-semibold tabular-nums shrink-0">
                        {stock.price != null ? `${market === 'HK' ? 'HK$' : '$'}${(stock.price as number).toFixed(2)}` : '—'}
                      </span>
                    </div>
                    <div className="flex items-baseline gap-2 mt-1">
                      <span className="text-[9px] text-muted-foreground w-[72px] shrink-0 leading-tight">
                        {stock.companyName}
                      </span>
                      {stock.change_pct != null && (
                        <span className={`text-[10px] font-semibold tabular-nums ${stock.change_pct >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                          {stock.change_pct >= 0 ? '+' : ''}{(stock.change_pct as number).toFixed(2)}%
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Right: V G P M + Score — vertically centered */}
                  <div className="flex items-center gap-0 ml-auto">
                    {(['valuation', 'growth', 'profitability', 'momentum'] as const).map(dim => (
                      <div key={dim} className="w-[52px] text-center">
                        {isFree ? (
                          <span className="text-muted-foreground/40 text-xs">🔒</span>
                        ) : (
                          <GradePill grade={stock.vgpm?.[dim]?.grade} estimated={stock.vgpm_estimated} />
                        )}
                      </div>
                    ))}
                    <div className="w-[34px] text-center">
                      {isFree ? (
                        <span className="text-muted-foreground/40 text-xs">🔒</span>
                      ) : stock.composite_score != null ? (
                        <span className="text-sm font-mono font-bold">{stock.composite_score}</span>
                      ) : (
                        <span className="text-muted-foreground/40 text-xs">—</span>
                      )}
                    </div>
                  </div>
                </SwipeableCard>
              ))}
            </div>
          )}

          {isLimited && (
            <div className="flex items-center justify-between px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/5">
              <p className="text-xs text-amber-600 dark:text-amber-400">Free plan — {screenerLimit} of {sorted.length}</p>
              <a href="#/pricing" className="text-xs font-semibold text-amber-600 dark:text-amber-400 underline">Upgrade →</a>
            </div>
          )}
          </div>
        </div>
      </div>
    );
  }

  // ── DESKTOP LAYOUT ──────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-background">
      <ResearchNav />
      <div className="p-4 md:p-8">
      <div className="max-w-screen-xl mx-auto">

        {/* Header */}
        <div className="flex items-center gap-4 mb-6">
          {/* Market tabs */}
          <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-muted/30">
            {(['US', 'HK'] as Market[]).map(m => (
              <button
                key={m}
                type="button"
                onClick={() => { setMarket(m); setData(null); setSearch(''); setSector('All'); setExchange('All'); setCapRange(m === 'HK' ? HK_DEFAULT_CAP : 'All'); }}
                className={`px-4 py-1.5 text-sm font-semibold rounded-md transition-colors ${
                  market === m
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {m}
              </button>
            ))}
          </div>
          <Button variant="outline" size="sm" onClick={() => navigate('/report')}>
            + New Analysis
          </Button>
        </div>

        {/* Controls */}
        <Card className="p-4 mb-5">
          <div className="flex flex-wrap items-end gap-4">

            {/* Search */}
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Search</label>
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Ticker or company…"
                className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring w-44"
              />
            </div>

            {/* Sector */}
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Sector</label>
              <select
                value={sector}
                onChange={e => setSector(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {(market === 'HK' ? HK_SECTORS : SECTORS).map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            {/* Exchange — US only */}
            {market === 'US' && (
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Exchange</label>
                <select
                  value={exchange}
                  onChange={e => setExchange(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  {EXCHANGES.map(x => <option key={x} value={x}>{x}</option>)}
                </select>
              </div>
            )}

            {/* Market Cap — US (USD) or HK (HKD, floor HK$15B) */}
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Market Cap</label>
              <select
                value={capRange}
                onChange={e => setCapRange(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {(market === 'HK' ? HK_CAP_RANGE_LABELS : CAP_RANGE_LABELS).map(l =>
                  <option key={l} value={l}>{l}</option>
                )}
              </select>
            </div>

            {/* VGPM only toggle */}
            <label className="flex items-center gap-2 text-sm cursor-pointer select-none pb-0.5">
              <input
                type="checkbox"
                checked={vgpmOnly}
                onChange={e => setVgpmOnly(e.target.checked)}
                className="rounded"
              />
              VGPM scored only
            </label>

            {/* Sort priority pills */}
            <div className="flex items-center gap-1 ml-auto">
              <span className="text-xs text-muted-foreground mr-1">Sort by:</span>
              {(Object.keys(SORT_LABELS) as SortKey[]).map(key => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setActiveSort(key)}
                  className={`text-xs font-bold px-2.5 py-1 rounded-full border transition-colors ${
                    activeSort === key
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/40'
                  }`}
                >
                  {SORT_LABELS[key]}
                </button>
              ))}
            </div>

            {/* Refresh */}
            <Button
              size="sm"
              variant="outline"
              disabled={loading}
              onClick={() => load(true)}
            >
              {loading ? 'Loading…' : '↻ Refresh'}
            </Button>
          </div>
        </Card>

        {/* Stats row */}
        {data && (
          <div className="flex items-center gap-4 mb-3 text-xs text-muted-foreground flex-wrap">
            <span>{data.total} {market === 'HK' ? 'HKEX' : ''} stocks</span>
            <span>·</span>
            <span>{vgpmCount} with VGPM scores</span>
            <span>·</span>
            <span className="font-mono">~grade</span><span>= FMP estimated · no prefix = pipeline-verified</span>
            {lastRefreshed && (
              <span className="text-green-500/70 ml-auto">
                prices updated {lastRefreshed.toLocaleTimeString()}
              </span>
            )}
            {!lastRefreshed && data.cached && (
              <span className="px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 font-medium ml-auto">
                cached · refresh to update
              </span>
            )}
          </div>
        )}

        {error && (
          <div className="mb-4 p-3 border border-red-500/40 rounded text-sm text-red-400">
            {error}
          </div>
        )}

        {/* Table */}
        <div className="rounded border overflow-x-auto bg-card [&_table]:bg-card [&_tr]:bg-card [&_td]:bg-card [&_th]:bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-20">Symbol</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Sector</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <TableHead className="text-right">% Change</TableHead>
                <TableHead className="text-right">Mkt Cap</TableHead>
                <TableHead className="text-right">Volume</TableHead>
                <TableHead className="text-right">Beta</TableHead>
                {/* VGPM columns */}
                {(['valuation', 'growth', 'profitability', 'momentum'] as const).map(dim => (
                  <TableHead key={dim} className="text-center">
                    <button
                      type="button"
                      onClick={() => setActiveSort(dim)}
                      className={`font-semibold capitalize transition-colors ${
                        activeSort === dim ? 'text-primary' : 'text-muted-foreground hover:text-foreground'
                      }`}
                      title={`Sort by ${dim}`}
                    >
                      {dim.charAt(0).toUpperCase() + dim.slice(1)}
                    </button>
                  </TableHead>
                ))}
                <TableHead
                  className="text-center w-16 cursor-pointer"
                  onClick={() => setActiveSort('composite')}
                >
                  <span className={`font-bold text-xs transition-colors ${activeSort === 'composite' ? 'text-primary' : 'text-muted-foreground'}`}>
                    Score
                  </span>
                </TableHead>
                <TableHead className="w-24" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && !data ? (
                <SkeletonRows />
              ) : lookupLoading ? (
                <SkeletonRows />
              ) : sorted.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={14} className="text-center text-muted-foreground py-10">
                    {loading ? 'Loading…' : 'No stocks found.'}
                  </TableCell>
                </TableRow>
              ) : (
                visibleSorted.map(stock => (
                  <TableRow key={stock.symbol} className="group hover:bg-muted/30">
                    <TableCell>
                      <span className="font-mono font-bold text-sm">{stock.symbol}</span>
                    </TableCell>
                    <TableCell className="max-w-[180px]">
                      <span className="truncate block text-sm">{stock.companyName}</span>
                      <span className="text-[10px] text-muted-foreground">{stock.industry}</span>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatSector(stock.sector) || '—'}</TableCell>
                    <TableCell className="text-right text-sm font-mono">
                      {(() => {
                        const flash = priceFlash[stock.symbol];
                        return (
                          <span className={`inline-flex items-center gap-0.5 transition-colors duration-700 ${
                            flash === 'up'   ? 'text-green-500' :
                            flash === 'down' ? 'text-red-500'   : ''
                          }`}>
                            {stock.price != null ? `${market === 'HK' ? 'HK$' : '$'}${(stock.price as number).toFixed(2)}` : '—'}
                            {flash === 'up'   && <span className="text-[10px] leading-none">▲</span>}
                            {flash === 'down' && <span className="text-[10px] leading-none">▼</span>}
                          </span>
                        );
                      })()}
                    </TableCell>
                    <TableCell className="text-right text-sm font-mono tabular-nums">
                      {stock.change_pct != null ? (
                        <span className={stock.change_pct >= 0 ? 'text-green-500' : 'text-red-500'}>
                          {stock.change_pct >= 0 ? '+' : ''}{(stock.change_pct as number).toFixed(2)}%
                        </span>
                      ) : <span className="text-muted-foreground/40">—</span>}
                    </TableCell>
                    <TableCell className="text-right text-sm font-mono">{fmtCap(stock.marketCap as number)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">{fmtVol(stock.volume as number)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">
                      {stock.beta != null ? (stock.beta as number).toFixed(2) : '—'}
                    </TableCell>

                    {/* VGPM grade cells */}
                    {(['valuation', 'growth', 'profitability', 'momentum'] as const).map(dim => (
                      <TableCell key={dim} className="text-center">
                        {isFree ? (
                          <a href="#/pricing" title="Upgrade to see VGPM scores" className="inline-flex items-center justify-center w-8 h-6 rounded bg-muted/60 cursor-pointer hover:bg-muted transition-colors">
                            <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground/50">
                              <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                            </svg>
                          </a>
                        ) : (
                          <GradePill grade={stock.vgpm?.[dim]?.grade} estimated={stock.vgpm_estimated} />
                        )}
                      </TableCell>
                    ))}

                    {/* Composite score */}
                    <TableCell className="text-center">
                      {isFree ? (
                        <a href="#/pricing" title="Upgrade to see score" className="inline-flex items-center justify-center w-6 h-6 rounded bg-muted/60 cursor-pointer hover:bg-muted transition-colors">
                          <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground/50">
                            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                          </svg>
                        </a>
                      ) : stock.composite_score != null ? (
                        <span className="text-xs font-mono font-bold">{stock.composite_score}</span>
                      ) : (
                        <span className="text-muted-foreground/40 text-xs">—</span>
                      )}
                    </TableCell>

                    {/* Action */}
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          type="button"
                          onClick={() => {
                            sessionStorage.setItem('screener_prefill', stock.symbol);
                            navigate('/report');
                          }}
                          title="Agentic analysis"
                          className="p-1 rounded text-muted-foreground/60 hover:text-foreground hover:bg-muted transition-colors"
                        >
                          <AgentOrbIcon size={18} />
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (watchlistAdded.has(stock.symbol)) {
                              navigate('/watchlist');
                              return;
                            }
                            addToWatchlist(stock.symbol).then(() => {
                              setWatchlistAdded(prev => new Set(prev).add(stock.symbol));
                            }).catch(() => {
                              navigate('/watchlist');
                            });
                          }}
                          title={watchlistAdded.has(stock.symbol) ? 'View Watchlist' : 'Add to Watchlist'}
                          className="text-xs text-muted-foreground hover:text-foreground"
                        >
                          {watchlistAdded.has(stock.symbol) ? '✓ Watching' : '＋Watch'}
                        </button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {isLimited && (
          <div className="mt-3 flex items-center justify-between px-4 py-2.5 rounded-lg border border-amber-500/30 bg-amber-500/5">
            <p className="text-xs text-amber-600 dark:text-amber-400">
              Free plan — showing {screenerLimit} of {sorted.length} results.
            </p>
            <a href="#/pricing" className="text-xs font-semibold text-amber-600 dark:text-amber-400 underline underline-offset-2 hover:opacity-80">
              Upgrade for unlimited →
            </a>
          </div>
        )}
        {visibleSorted.length > 0 && !isLimited && (
          <p className="text-xs text-muted-foreground mt-3 text-right">
            {visibleSorted.length} of {data?.total ?? 0} stocks shown
          </p>
        )}
      </div>
      </div>
    </div>
  );
}
