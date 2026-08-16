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
import { TabHero } from '@/components/layout/TabHero';
import { useLayoutMode } from '@/contexts/layout-mode-context';

type Market = 'US' | 'HK' | 'SG';
type SortKey = 'composite' | 'valuation' | 'growth' | 'profitability' | 'momentum';

/**
 * Desktop row grid — the header row and every body row share this exact
 * class string so columns never drift apart (same lesson as the mobile
 * grid-cols-12 pair).
 *   md (≥768):  Ticker · Sector | Score | V G P M | hover actions
 *   lg (≥1024): adds Price and % Change columns
 * The Price/Change cells carry `hidden lg:block` so they drop out of the
 * grid in perfect sync with the template change.
 */
const DESK_GRID =
  'grid items-center gap-3 ' +
  'md:grid-cols-[minmax(0,1fr)_112px_144px_76px] ' +
  'lg:grid-cols-[minmax(0,1fr)_96px_84px_112px_144px_76px]';

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
  const { mode } = useLayoutMode();
  const isDesktop = mode === 'desktop';

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
    <div className="min-h-full flex flex-col bg-background">
      <TabHero title="Screener" />
      {/* Market segmented control */}
      <div className="px-3 md:px-6 pt-3">
        <div className="flex items-center gap-1 p-1 bg-muted/60 border border-border/60 rounded-lg">
          {(['US', 'HK', 'SG'] as Market[]).map(m => (
            <button
              key={m}
              onClick={() => { setMarket(m); setSector('All'); }}
              className={`flex-1 h-8 md:h-9 rounded-md text-[11.5px] md:text-[12.5px] font-medium transition-colors
                ${market === m ? 'bg-card text-foreground shadow-sm border border-border' : 'text-muted-foreground active:text-foreground'}`}
            >
              {MARKET_LABELS[m]}
            </button>
          ))}
        </div>
      </div>

      {/* Search */}
      <div className="px-3 md:px-6 pt-2.5">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/70" width={15} height={15}/>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search ticker or name"
            className="w-full h-10 md:h-11 pl-8 pr-3 text-[13px] md:text-[14px] rounded-lg bg-muted/60 border border-border focus:bg-card focus:border-brand/40 focus:outline-none focus:ring-2 placeholder:text-muted-foreground/70 text-foreground"
            style={{ ['--tw-ring-color' as any]: `${BRAND}1a` }}
          />
        </div>
      </div>

      {/* Sector chips + VGPM toggle */}
      <div className="px-3 md:px-6 pt-2.5 flex items-center gap-1.5 overflow-x-auto phone-scroll">
        {sectorsFor(market).map(s => (
          <button
            key={s}
            onClick={() => setSector(s)}
            className={`h-8 md:h-9 px-2.5 md:px-3 text-[11px] md:text-[12px] rounded-lg border flex items-center shrink-0 transition-colors
              ${sector === s
                ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-card text-muted-foreground border-border active:bg-muted'}`}
          >
            {s}
          </button>
        ))}
        <button
          onClick={() => setVgpmOnly(v => !v)}
          className={`h-8 md:h-9 px-2.5 md:px-3 text-[11px] md:text-[12px] rounded-lg border flex items-center gap-1 shrink-0 transition-colors
            ${vgpmOnly ? 'bg-brand/10 border-brand/30 text-brand' : 'bg-card border-border text-muted-foreground'}`}
        >
          <Check width={11} height={11}/> VGPM only
        </button>
      </div>

      {/* Sort tabs */}
      <div className="border-b border-border/60 mt-2">
        <div className="px-3 md:px-6 flex items-center gap-1 overflow-x-auto phone-scroll">
          {SORTS.map(s => (
            <button
              key={s.id}
              onClick={() => setSortKey(s.id)}
              className={`h-9 md:h-10 px-2.5 text-[11.5px] md:text-[12.5px] font-medium border-b-[2px] -mb-px transition-colors shrink-0
                ${sortKey === s.id ? 'text-foreground border-brand' : 'text-muted-foreground border-transparent active:text-foreground'}`}
            >
              Sort: {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* Rows */}
      <div className="px-3 md:px-6 pt-2 pb-6 flex-1">
        <div className="flex items-center justify-between px-1 mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] md:text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/70">
              Top candidates
            </span>
            {lastRefreshed && (
              <span className="inline-flex items-center gap-1 text-[10px] md:text-[11px] text-muted-foreground/70">
                <span className="w-1 h-1 rounded-full bg-brand"/>
                updated {lastRefreshed.toLocaleTimeString()}
              </span>
            )}
          </div>
          <span className="text-[10px] md:text-[11px] text-muted-foreground/70">{rows.length} · {market}</span>
        </div>

        <div className="rounded-lg border border-border bg-card overflow-hidden shadow-sm">
          {/* Header row — the desktop header shares DESK_GRID with the body
              rows; the mobile header's gap-2 must match the mobile body rows'
              grid so the 12-column tracks line up exactly under each row. */}
          {isDesktop ? (
            <div className={`px-4 py-2.5 border-b border-border/60 bg-muted/50 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70 ${DESK_GRID}`}>
              <span>Ticker</span>
              <span className="hidden lg:block text-right">Price</span>
              <span className="hidden lg:block text-right">Change</span>
              <span className="text-right">Score</span>
              <div className="flex justify-end">
                {['V', 'G', 'P', 'M'].map(l => (
                  <span key={l} className="w-9 text-center">{l}</span>
                ))}
              </div>
              <span aria-hidden />
            </div>
          ) : (
            <div className="px-3 py-2 border-b border-border/60 bg-muted/50 grid grid-cols-12 items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
              <span className="col-span-5">Ticker · Sector</span>
              <span className="col-span-2 text-right">Score</span>
              <div className="col-span-5 flex justify-end">
                {['V', 'G', 'P', 'M'].map(l => (
                  <span key={l} className="w-9 text-center">{l}</span>
                ))}
              </div>
            </div>
          )}

          {loading && !data ? (
            <div className="px-3 py-10 text-center text-[12px] text-muted-foreground/70">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="px-3 py-10 text-center text-[12px] text-muted-foreground/70">
              No matches. Adjust filters.
            </div>
          ) : isDesktop ? (
            /* ── Desktop rows: dense table-style list, hover actions ────────
               Click anywhere on the row to open the report; Analyse/Watch
               reveal on hover (swipe gestures are phone-only). */
            rows.slice(0, 200).map((r, i) => (
              <div
                key={r.symbol}
                onClick={() => handleOpen(r.symbol)}
                className={`group cursor-pointer px-4 py-3 hover:bg-muted/40 transition-colors ${i > 0 ? 'border-t border-border/60' : ''}`}
              >
                <div className={DESK_GRID}>
                  {/* Ticker · company · sector·mcap */}
                  <div className="min-w-0">
                    <div className="flex items-baseline gap-2 min-w-0">
                      <span className="text-[13.5px] font-semibold text-foreground tabular-nums shrink-0">{r.symbol}</span>
                      <span className="text-[12px] text-muted-foreground truncate">{r.companyName}</span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
                      <span className="truncate">{r.sector || '—'}</span>
                      <span className="text-muted-foreground/50">·</span>
                      <span className="tabular-nums shrink-0">{formatMarketCap(r.marketCap)}</span>
                    </div>
                  </div>
                  {/* Price — lg+ only, in sync with DESK_GRID's template */}
                  <div className="hidden lg:block text-right text-[13px] font-medium text-foreground tabular-nums">
                    {r.price != null
                      ? `$${r.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                      : '—'}
                  </div>
                  {/* % change — lg+ only */}
                  <div className="hidden lg:block text-right">
                    {r.change_pct != null ? (
                      <span className={`text-[12.5px] font-semibold tabular-nums ${r.change_pct >= 0 ? 'text-brand' : 'text-rose-600 dark:text-rose-400'}`}>
                        {r.change_pct >= 0 ? '+' : ''}{r.change_pct.toFixed(2)}%
                      </span>
                    ) : (
                      <span className="text-[12.5px] text-muted-foreground/40">—</span>
                    )}
                  </div>
                  {/* Score */}
                  <div className="flex flex-col items-end">
                    <div className="flex items-baseline gap-1">
                      <span className="text-[14px] font-semibold text-foreground tabular-nums">
                        {r.composite_score ?? '—'}
                      </span>
                      <span className="text-[10.5px] text-muted-foreground/70">/100</span>
                    </div>
                    <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden mt-1">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.max(0, Math.min(100, r.composite_score ?? 0))}%`,
                          backgroundColor: BRAND,
                        }}
                      />
                    </div>
                  </div>
                  {/* Grades under the V/G/P/M header letters */}
                  <div className="flex items-center justify-end gap-0">
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.valuation?.grade}/></div>
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.growth?.grade}/></div>
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.profitability?.grade}/></div>
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.momentum?.grade}/></div>
                  </div>
                  {/* Hover actions */}
                  <div
                    className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity"
                    onClick={e => e.stopPropagation()}
                  >
                    <button
                      type="button"
                      title="Analyse"
                      aria-label={`Analyse ${r.symbol}`}
                      onClick={() => handleOpen(r.symbol)}
                      className="p-1.5 rounded-md text-muted-foreground/70 hover:text-foreground hover:bg-muted transition-colors"
                    >
                      <Search width={15} height={15}/>
                    </button>
                    <button
                      type="button"
                      title="Add to watchlist"
                      aria-label={`Add ${r.symbol} to watchlist`}
                      onClick={() => handleWatch(r.symbol)}
                      className="p-1.5 rounded-md text-muted-foreground/70 hover:text-foreground hover:bg-muted transition-colors"
                    >
                      <Bookmark width={15} height={15}/>
                    </button>
                  </div>
                </div>
              </div>
            ))
          ) : (
            rows.slice(0, 200).map((r, i) => (
              <SwipeRow
                key={r.symbol}
                onClick={() => handleOpen(r.symbol)}
                className={i > 0 ? 'border-t border-border/60' : ''}
                actions={[
                  {
                    icon: <Search width={18} height={18} strokeWidth={2}/>,
                    label: 'Analyse',
                    color: '#163300',
                    onClick: () => handleOpen(r.symbol),
                  },
                  {
                    icon: <Bookmark width={18} height={18} strokeWidth={2}/>,
                    label: 'Watch',
                    color: '#297A4B',
                    onClick: () => handleWatch(r.symbol),
                  },
                ]}
              >
                <div className="w-full text-left grid grid-cols-12 items-center gap-2 px-3 py-4 active:bg-muted/60 transition-colors">
                  <div className="col-span-5 min-w-0">
                    <div className="text-[12.5px] font-semibold text-foreground tabular-nums truncate">{r.symbol}</div>
                    <div className="text-[11px] text-muted-foreground truncate">{r.companyName}</div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground/70">
                      <span className="truncate">{r.sector || '—'}</span>
                      <span className="text-muted-foreground/50">·</span>
                      <span className="tabular-nums">{formatMarketCap(r.marketCap)}</span>
                    </div>
                  </div>
                  <div className="col-span-2 flex flex-col items-end">
                    <div className="flex items-baseline gap-1">
                      <span className="text-[14px] font-semibold text-foreground tabular-nums">
                        {r.composite_score ?? '—'}
                      </span>
                      <span className="text-[10px] text-muted-foreground/70">/100</span>
                    </div>
                    <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden mt-1">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.max(0, Math.min(100, r.composite_score ?? 0))}%`,
                          backgroundColor: BRAND,
                        }}
                      />
                    </div>
                  </div>
                  {/* Bare grades under the V/G/P/M column header */}
                  <div className="col-span-5 flex items-center justify-end gap-0">
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.valuation?.grade}/></div>
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.growth?.grade}/></div>
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.profitability?.grade}/></div>
                    <div className="w-9 flex justify-center"><GradeChip grade={r.vgpm?.momentum?.grade}/></div>
                  </div>
                </div>
              </SwipeRow>
            ))
          )}
        </div>

        <div className="mt-3 px-1 text-[10.5px] md:text-[11.5px] text-muted-foreground/70 leading-relaxed">
          Universe: {data?.total ?? 0} stocks · Composite = 0.30·V + 0.25·G + 0.25·P + 0.20·M, sector-neutralised.
        </div>
      </div>
    </div>
  );
}
