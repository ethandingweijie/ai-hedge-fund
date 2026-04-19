/**
 * ScreenerPage.tsx — Reimagined UI (v2)
 *
 * Minimal-fintech Linear/Stripe layout wired to the real backend.
 * - Market segmented control (US · HK · SG)
 * - Search + sector chips + VGPM-only toggle
 * - Sort tabs (Overall / V / G / P / M)
 * - Stock rows with composite score + V/G/P/M chips
 * - SwipeRow: swipe left → Analyse + Watch actions
 * - 15s live price refresh for top 50 by composite score (existing behaviour preserved)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getScreenerStocks,
  getHkScreenerStocks,
  getSgScreenerStocks,
  getScreenerPrices,
  addToWatchlist,
  lookupScreenerTicker,
} from '@/lib/api';
import type { ScreenerResponse } from '@/lib/reportTypes';
import {
  Search,
  Check,
  Bookmark,
  GradeChip,
  SwipeRow,
  BRAND,
} from '@/components/v2/shared';
import { toast } from 'sonner';

type Market = 'US' | 'HK' | 'SG';
type SortKey = 'composite' | 'valuation' | 'growth' | 'profitability' | 'momentum';

const MARKET_LABELS: Record<Market, string> = {
  US: 'US · NASDAQ/NYSE',
  HK: 'HK · HKEX',
  SG: 'SG · SGX',
};

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'composite',     label: 'Overall' },
  { id: 'valuation',     label: 'V' },
  { id: 'growth',        label: 'G' },
  { id: 'profitability', label: 'P' },
  { id: 'momentum',      label: 'M' },
];

const US_SECTORS = ['All', 'Technology', 'Communication Services', 'Financial Services', 'Consumer Cyclical', 'Consumer Defensive', 'Healthcare', 'Industrials', 'Energy', 'Real Estate', 'Utilities', 'Basic Materials'];
const HK_SECTORS = ['All', 'Technology', 'Financials', 'Property', 'Consumer', 'Industrials', 'Healthcare', 'Energy'];
const SG_SECTORS = ['All', 'Financials', 'REIT', 'Tech', 'Industrials', 'Consumer', 'Property', 'Telco', 'Energy'];

// Map market → sector list for the chips row
const sectorsFor = (m: Market) => m === 'US' ? US_SECTORS : m === 'HK' ? HK_SECTORS : SG_SECTORS;

function formatMarketCap(mc: number | null): string {
  if (mc == null) return '—';
  if (mc >= 1e12) return `$${(mc / 1e12).toFixed(2)}T`;
  if (mc >= 1e9)  return `$${(mc / 1e9).toFixed(1)}B`;
  if (mc >= 1e6)  return `$${(mc / 1e6).toFixed(0)}M`;
  return `$${mc.toFixed(0)}`;
}

// Grade-rank helper for V/G/P/M sort (A+ > A > A- > B+ > ...)
function gradeRank(g?: string | null): number {
  if (!g) return -1;
  const base = { A: 90, B: 75, C: 60, D: 40 }[g[0]] ?? 0;
  return base + (g.endsWith('+') ? 3 : g.endsWith('-') ? -3 : 0);
}

export function ScreenerPage() {
  const navigate = useNavigate();

  const [market, setMarket]         = useState<Market>('US');
  const [sector, setSector]         = useState('All');
  const [sortKey, setSortKey]       = useState<SortKey>('composite');
  const [vgpmOnly, setVgpmOnly]     = useState(true);
  const [search, setSearch]         = useState('');

  const [data, setData]             = useState<ScreenerResponse | null>(null);
  const [loading, setLoading]       = useState(false);
  const [lastRefreshed, setLastRef] = useState<Date | null>(null);

  const dataRef = useRef<ScreenerResponse | null>(null);
  useEffect(() => { dataRef.current = data; }, [data]);

  // ── Load universe on market change ───────────────────────────────────────
  const load = useCallback(async (forceRefresh = false) => {
    setLoading(true);
    try {
      let result: ScreenerResponse;
      if (market === 'HK') result = await getHkScreenerStocks(forceRefresh);
      else if (market === 'SG') result = await getSgScreenerStocks(forceRefresh);
      else result = await getScreenerStocks({
        sector: sector !== 'All' ? sector : undefined,
        marketCapMin: 2_000_000_000,
        refresh: forceRefresh,
      });
      setData(result);
      setLastRef(new Date());
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [market, sector]);

  useEffect(() => { load(); }, [load]);

  // ── 15s live price refresh (top 50 by composite) ─────────────────────────
  useEffect(() => {
    const tick = async () => {
      const current = dataRef.current;
      if (!current?.items.length) return;
      const top = [...current.items]
        .sort((a, b) => (b.composite_score ?? -1) - (a.composite_score ?? -1))
        .slice(0, 50);
      const syms = top.map(s => s.symbol);
      try {
        const quotes = await getScreenerPrices(syms);
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
        setLastRef(new Date());
      } catch { /* silent */ }
    };
    const initial = setTimeout(tick, 3000);
    const id = setInterval(tick, 15000);
    return () => { clearTimeout(initial); clearInterval(id); };
  }, []);

  // ── Filter + sort ────────────────────────────────────────────────────────
  const rows = useMemo(() => {
    const items = data?.items ?? [];
    let filtered = items.filter(r => {
      if (sector !== 'All' && r.sector !== sector) return false;
      if (search !== '' && !r.symbol.toLowerCase().includes(search.toLowerCase()) &&
          !(r.companyName || '').toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
    if (vgpmOnly) filtered = filtered.filter(r => r.vgpm !== null);
    return [...filtered].sort((a, b) => {
      if (sortKey === 'composite') return (b.composite_score ?? -1) - (a.composite_score ?? -1);
      const ga = a.vgpm?.[sortKey]?.grade;
      const gb = b.vgpm?.[sortKey]?.grade;
      return gradeRank(gb) - gradeRank(ga);
    });
  }, [data, sector, search, vgpmOnly, sortKey]);

  // ── Ticker not in universe → lookup on demand ────────────────────────────
  useEffect(() => {
    const q = search.trim().toUpperCase();
    if (q.length < 2 || !data) return;
    const exists = data.items.some(s => s.symbol.toUpperCase() === q);
    if (exists) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const stock = await lookupScreenerTicker(q);
        if (cancelled || !stock) return;
        setData(prev => prev ? { ...prev, items: [stock, ...prev.items] } : prev);
      } catch { /* ignore */ }
    }, 500);
    return () => { cancelled = true; clearTimeout(t); };
  }, [search, data]);

  const handleWatch = async (symbol: string) => {
    try {
      await addToWatchlist(symbol);
      toast.success(`${symbol} added to watchlist`);
    } catch (e) {
      toast.error(`Watch failed: ${(e as Error).message}`);
    }
  };

  const handleOpen = (symbol: string) => {
    navigate('/report', { state: { prefillTicker: symbol } });
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-full flex flex-col bg-white dark:bg-zinc-900">
      {/* Market segmented control */}
      <div className="px-3 pt-3" style={{ paddingTop: 'calc(env(safe-area-inset-top) + 12px)' }}>
        <div className="flex items-center gap-1 p-1 bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800 rounded-lg">
          {(['US', 'HK', 'SG'] as Market[]).map(m => (
            <button
              key={m}
              onClick={() => { setMarket(m); setSector('All'); }}
              className={`flex-1 h-8 rounded-md text-[11.5px] font-medium transition-colors
                ${market === m ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-50 shadow-sm border border-zinc-200 dark:border-zinc-800' : 'text-zinc-500 dark:text-zinc-400 active:text-zinc-800'}`}
            >
              {MARKET_LABELS[m]}
            </button>
          ))}
        </div>
      </div>

      {/* Search */}
      <div className="px-3 pt-2.5">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400 dark:text-zinc-500" width={15} height={15}/>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search ticker or name"
            className="w-full h-10 pl-8 pr-3 text-[13px] rounded-lg bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-800 focus:bg-white dark:focus:bg-zinc-900 focus:border-zinc-300 dark:focus:border-zinc-700 focus:outline-none focus:ring-2 placeholder:text-zinc-400 text-zinc-900 dark:text-zinc-50"
            style={{ ['--tw-ring-color' as any]: `${BRAND}1a` }}
          />
        </div>
      </div>

      {/* Sector chips + VGPM toggle */}
      <div className="px-3 pt-2.5 flex items-center gap-1.5 overflow-x-auto phone-scroll">
        {sectorsFor(market).map(s => (
          <button
            key={s}
            onClick={() => setSector(s)}
            className={`h-8 px-2.5 text-[11px] rounded-lg border flex items-center shrink-0 transition-colors
              ${sector === s
                ? 'bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 border-zinc-900 dark:border-zinc-100'
                : 'bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-800 active:bg-zinc-50 dark:active:bg-zinc-800'}`}
          >
            {s}
          </button>
        ))}
        <button
          onClick={() => setVgpmOnly(v => !v)}
          className={`h-8 px-2.5 text-[11px] rounded-lg border flex items-center gap-1 shrink-0 transition-colors
            ${vgpmOnly ? 'bg-[#ecf5ed] dark:bg-[#2e7d32]/15 border-[#d0e7d2] dark:border-[#2e7d32]/40 text-[#2e7d32] dark:text-[#4ea354]' : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400'}`}
        >
          <Check width={11} height={11}/> VGPM only
        </button>
      </div>

      {/* Sort tabs */}
      <div className="border-b border-zinc-100 dark:border-zinc-800 mt-2">
        <div className="px-3 flex items-center gap-1 overflow-x-auto phone-scroll">
          {SORTS.map(s => (
            <button
              key={s.id}
              onClick={() => setSortKey(s.id)}
              className={`h-9 px-2.5 text-[11.5px] font-medium border-b-[2px] -mb-px transition-colors shrink-0
                ${sortKey === s.id ? 'text-zinc-900 dark:text-zinc-50 border-[#2e7d32]' : 'text-zinc-500 dark:text-zinc-400 border-transparent active:text-zinc-800'}`}
            >
              Sort: {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* Rows */}
      <div className="px-3 pt-2 pb-6 flex-1">
        <div className="flex items-center justify-between px-1 mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-zinc-400 dark:text-zinc-500">
              Top candidates
            </span>
            {lastRefreshed && (
              <span className="inline-flex items-center gap-1 text-[10px] text-zinc-400 dark:text-zinc-500">
                <span className="w-1 h-1 rounded-full bg-[#2e7d32] dark:bg-[#4ea354]"/>
                updated {lastRefreshed.toLocaleTimeString()}
              </span>
            )}
          </div>
          <span className="text-[10px] text-zinc-400 dark:text-zinc-500">{rows.length} · {market}</span>
        </div>

        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-hidden shadow-sm">
          {/* Header row */}
          <div className="px-3 py-2 border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50/60 dark:bg-zinc-800/40 grid grid-cols-12 items-center text-[10px] font-medium uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
            <span className="col-span-5">Ticker · Sector</span>
            <span className="col-span-2 text-right">Score</span>
            <span className="col-span-5 text-right pr-1">V · G · P · M</span>
          </div>

          {loading && !data ? (
            <div className="px-3 py-10 text-center text-[12px] text-zinc-400 dark:text-zinc-500">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="px-3 py-10 text-center text-[12px] text-zinc-400 dark:text-zinc-500">
              No matches. Adjust filters.
            </div>
          ) : (
            rows.slice(0, 200).map((r, i) => (
              <SwipeRow
                key={r.symbol}
                onClick={() => handleOpen(r.symbol)}
                className={i > 0 ? 'border-t border-zinc-100 dark:border-zinc-800' : ''}
                actions={[
                  {
                    icon: <Search width={18} height={18} strokeWidth={2}/>,
                    label: 'Analyse',
                    color: '#3b82f6',
                    onClick: () => handleOpen(r.symbol),
                  },
                  {
                    icon: <Bookmark width={18} height={18} strokeWidth={2}/>,
                    label: 'Watch',
                    color: '#22c55e',
                    onClick: () => handleWatch(r.symbol),
                  },
                ]}
              >
                <div className="w-full text-left grid grid-cols-12 items-center gap-2 px-3 py-2.5 active:bg-zinc-50 dark:active:bg-zinc-800 transition-colors">
                  <div className="col-span-5 min-w-0">
                    <div className="text-[12.5px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums truncate">{r.symbol}</div>
                    <div className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">{r.companyName}</div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-400 dark:text-zinc-500">
                      <span className="truncate">{r.sector || '—'}</span>
                      <span className="text-zinc-300 dark:text-zinc-600">·</span>
                      <span className="tabular-nums">{formatMarketCap(r.marketCap)}</span>
                    </div>
                  </div>
                  <div className="col-span-2 flex flex-col items-end">
                    <div className="flex items-baseline gap-1">
                      <span className="text-[14px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums">
                        {r.composite_score ?? '—'}
                      </span>
                      <span className="text-[9px] text-zinc-400 dark:text-zinc-500">/100</span>
                    </div>
                    <div className="w-full h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden mt-1">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.max(0, Math.min(100, r.composite_score ?? 0))}%`,
                          backgroundColor: BRAND,
                        }}
                      />
                    </div>
                  </div>
                  <div className="col-span-5 flex items-center justify-end gap-2">
                    <GradeChip grade={r.vgpm?.valuation?.grade}     label="V"/>
                    <GradeChip grade={r.vgpm?.growth?.grade}        label="G"/>
                    <GradeChip grade={r.vgpm?.profitability?.grade} label="P"/>
                    <GradeChip grade={r.vgpm?.momentum?.grade}      label="M"/>
                  </div>
                </div>
              </SwipeRow>
            ))
          )}
        </div>

        <div className="mt-3 px-1 text-[10.5px] text-zinc-400 dark:text-zinc-500 leading-relaxed">
          Universe: {data?.total ?? 0} stocks · Composite = 0.30·V + 0.25·G + 0.25·P + 0.20·M, sector-neutralised.
        </div>
      </div>
    </div>
  );
}
