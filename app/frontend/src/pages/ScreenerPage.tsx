/**
 * ScreenerPage.tsx — Reimagined UI (v2)
 *
 * Minimal-fintech Linear/Stripe layout wired to the real backend.
 * - Market segmented control (US · HK · SG)
 * - Search bar with the filters flush right: Sector | Market cap | VGPM
 *   (the VGPM dropdown is the sort — Overall / V / G / P / M)
 * - Stock rows with composite score + V/G/P/M chips
 * - SwipeRow: swipe left → Analyse + Watch actions
 * - 15s live price refresh for top 50 by composite score (existing behaviour preserved)
 *
 * Filtering model: one universe fetch per market switch; sector, market-cap
 * and search filters all apply client-side so changing a filter is instant.
 * Sector options are DERIVED from the loaded universe rather than
 * hard-coded — FMP uses the same 11 sector names in every market, and the
 * old hand-maintained HK/SG lists ("Financials", "REIT", "Telco", …) never
 * matched FMP's actual tagging, which made those filters return empty.
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
import type { ScreenerResponse, ScreenerStock } from '@/lib/reportTypes';
import {
  Search,
  Check,
  ChevronDn,
  Bookmark,
  GradeChip,
  SwipeRow,
  BRAND,
} from '@/components/v2/shared';
import { toast } from 'sonner';
import { TabHero } from '@/components/layout/TabHero';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { useI18n } from '@/i18n';

type Market = 'US' | 'HK' | 'SG';
type SortKey =
  | 'composite' | 'valuation' | 'growth' | 'profitability' | 'momentum'
  | 'marketCap' | 'price' | 'change';

/** Rows per rendered page (the old view silently truncated at 200). */
const PAGE_SIZE = 100;
/** US universe is fetched in server pages of this size. */
const US_FETCH_PAGE = 500;
/** Safety bound on US pages fetched for the client-side universe. */
const US_MAX_PAGES = 12;
/** A screener request that takes longer than this is abandoned with a
 *  retry prompt instead of an endless spinner. Cold builds can be slow;
 *  the backend keeps building and the retry is served from it. */
const LOAD_TIMEOUT_MS = 60_000;
/** After this long, the loading state explains a first-time build. */
const SLOW_LOAD_MS = 8_000;
/** Live price tick. */
const PRICE_TICK_MS = 15_000;

const CCY_SYMBOL: Record<string, string> = {
  USD: '$', HKD: 'HK$', SGD: 'S$', CNY: 'CN¥',
};
const MARKET_CCY: Record<Market, string> = { US: 'USD', HK: 'HKD', SG: 'SGD' };

function ccySymbol(ccy?: string | null): string {
  if (!ccy) return '$';
  return CCY_SYMBOL[ccy] ?? `${ccy} `;
}

/** Market cap in USD for the USD bands; null when it cannot be converted. */
function capUsd(r: ScreenerStock): number | null {
  if (r.marketCapUsd !== undefined) return r.marketCapUsd ?? null;
  return (!r.currency || r.currency === 'USD') ? r.marketCap : null;
}

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
  { id: 'marketCap',     label: 'Market cap' },
  { id: 'price',         label: 'Price' },
  { id: 'change',        label: 'Change' },
];

// The Sort dropdown in the search bar is the sort control: VGPM grades,
// or market cap / price / day change (largest first).
const SORT_OPTIONS: FilterOption[] = SORTS.map(s => ({ id: s.id, label: s.label }));

// Canonical FMP sector order — used only to keep the derived dropdown
// stable and readable. The options themselves come from the data, so a
// sector the provider adds tomorrow shows up automatically.
const FMP_SECTOR_ORDER = [
  'Technology', 'Financial Services', 'Healthcare',
  'Consumer Cyclical', 'Consumer Defensive',
  'Industrials', 'Communication Services', 'Energy',
  'Basic Materials', 'Real Estate', 'Utilities',
];

// Market-cap ranges in USD — labels mirror the backend's
// _FRONTEND_CAP_RANGES. HK/SG rows are placed by marketCapUsd (converted
// server-side at live FX); a row with no FX rate cannot be placed in a band.
const CAP_RANGES: { id: string; label: string; min: number | null; max: number | null }[] = [
  { id: 'all',     label: 'Any market cap', min: null,  max: null },
  { id: '2-12',    label: '$2B – $12B',     min: 2e9,   max: 12e9 },
  { id: '12-50',   label: '$12B – $50B',    min: 12e9,  max: 50e9 },
  { id: '50-100',  label: '$50B – $100B',   min: 50e9,  max: 100e9 },
  { id: '100-500', label: '$100B – $500B',  min: 100e9, max: 500e9 },
  { id: '500-1t',  label: '$500B – $1T',    min: 500e9, max: 1e12 },
  { id: 'gt-1t',   label: 'Above $1T',      min: 1e12,  max: null },
];

type FilterOption = { id: string; label: string; count?: number };

const TIMEOUT_ERROR = '__timeout__';

/** Market cap in its listing currency (HK$ / S$ / $), never relabelled. */
function formatMarketCap(mc: number | null, ccy?: string | null): string {
  if (mc == null) return '—';
  const s = ccySymbol(ccy);
  if (mc >= 1e12) return `${s}${(mc / 1e12).toFixed(2)}T`;
  if (mc >= 1e9)  return `${s}${(mc / 1e9).toFixed(1)}B`;
  if (mc >= 1e6)  return `${s}${(mc / 1e6).toFixed(0)}M`;
  return `${s}${mc.toFixed(0)}`;
}

function formatPrice(p: number | null, ccy?: string | null): string {
  if (p == null) return '—';
  return `${ccySymbol(ccy)}${p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Grade-rank helper for V/G/P/M sort (A+ > A > A- > B+ > ...)
function gradeRank(g?: string | null): number {
  if (!g) return -1;
  const base = { A: 90, B: 75, C: 60, D: 40 }[g[0]] ?? 0;
  return base + (g.endsWith('+') ? 3 : g.endsWith('-') ? -3 : 0);
}

/**
 * Compact filter dropdown with an optional type-ahead search box.
 * Replaces the old horizontally-scrolling chip row: 11+ options no longer
 * fit on a phone screen, and the search box lets the user jump straight to
 * a sector instead of scrolling.
 */
function FilterDropdown({
  label, value, options, onSelect, searchable = false, defaultValue = 'all',
}: {
  label: string;
  value: string;
  options: FilterOption[];
  onSelect: (id: string) => void;
  searchable?: boolean;
  /** Value that counts as "no selection applied" (default 'all'). */
  defaultValue?: string;
}) {
  const { t } = useI18n();
  const [open, setOpen]     = useState(false);
  const [query, setQuery]   = useState('');
  const rootRef  = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Close on outside click / Escape
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Reset + focus the search box each time the panel opens
  useEffect(() => {
    if (!open) return;
    setQuery('');
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const selected = options.find(o => o.id === value);
  const active   = value !== defaultValue;
  const q        = query.trim().toLowerCase();
  const visible  = searchable && q
    ? options.filter(o => o.label.toLowerCase().includes(q))
    : options;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`h-8 md:h-9 pl-2.5 md:pl-3 pr-2 md:pr-2.5 text-xs md:text-[12px] rounded-lg border flex items-center gap-1.5 transition-colors
          ${active
            ? 'bg-primary text-primary-foreground border-primary'
            : 'bg-card text-muted-foreground border-border active:bg-muted'}`}
      >
        {/* Truncated display: at the default value show only the category
            name (Sector | Market Cap | VGPM) — the "All sectors" /
            "Any market cap" / "Overall" defaults are redundant. The
            selected value only appears once a real filter is active. */}
        <span className={active ? 'opacity-70' : ''}>
          {active ? `${t(label)}:` : t(label)}
        </span>
        {active && (
          <span className="max-w-[9.5rem] truncate font-medium">
            {selected?.label ?? ''}
          </span>
        )}
        <ChevronDn width={12} height={12} className={`opacity-60 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}/>
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-1.5 z-50 w-60 max-w-[calc(100vw-1.5rem)] rounded-lg border border-border bg-card shadow-lg overflow-hidden">
          {searchable && (
            <div className="p-1.5 border-b border-border/60">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground/60" width={13} height={13}/>
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder={`Find ${label.toLowerCase()}…`}
                  className="w-full h-8 pl-7 pr-2 text-[12px] rounded-md bg-muted/60 border border-border/60 focus:bg-card focus:border-brand/40 focus:outline-none placeholder:text-muted-foreground/60 text-foreground"
                />
              </div>
            </div>
          )}
          <div role="listbox" aria-label={t(label)} className="max-h-64 overflow-y-auto py-1">
            {visible.length === 0 ? (
              <div className="px-3 py-2.5 text-[12px] text-muted-foreground/70">{t("No matches")}</div>
            ) : visible.map(o => (
              <button
                key={o.id}
                type="button"
                role="option"
                aria-selected={o.id === value}
                onClick={() => { onSelect(o.id); setOpen(false); }}
                className={`w-full h-9 px-3 flex items-center justify-between gap-2 text-left text-[12.5px] transition-colors
                  ${o.id === value
                    ? 'bg-muted text-foreground font-medium'
                    : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}
              >
                <span className="truncate">{t(o.label)}</span>
                <span className="flex items-center gap-1.5 shrink-0">
                  {o.count != null && (
                    <span className="text-xs text-muted-foreground/60 tabular-nums">{o.count}</span>
                  )}
                  {o.id === value && <Check width={12} height={12}/>}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ScreenerPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { mode } = useLayoutMode();
  const isDesktop = mode === 'desktop';

  const [market, setMarket]         = useState<Market>('US');
  const [sector, setSector]         = useState('all'); // 'all' | 'Other' | FMP sector name
  const [capId, setCapId]           = useState('all');
  const [sortKey, setSortKey]       = useState<SortKey>('composite');
  const [search, setSearch]         = useState('');

  const [data, setData]             = useState<ScreenerResponse | null>(null);
  const [loading, setLoading]       = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [slowLoad, setSlowLoad]     = useState(false);
  const [loadError, setLoadError]   = useState<string | null>(null);
  const [lastRefreshed, setLastRef] = useState<Date | null>(null);
  const [page, setPage]             = useState(0);

  const dataRef = useRef<ScreenerResponse | null>(null);
  useEffect(() => { dataRef.current = data; }, [data]);

  // Monotonic load id: a response is applied only if no newer load (e.g. a
  // market switch) started after it, so US rows can never land under the
  // HK label.
  const loadSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // ── Load universe on market change ───────────────────────────────────────
  // Sector and cap filtering happen client-side on the full universe, so
  // changing a filter never refetches — only switching markets does. The
  // US universe arrives in server pages; the first page renders at once and
  // the rest stream in behind it.
  const load = useCallback(async (forceRefresh = false) => {
    const seq = ++loadSeq.current;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const isCurrent = () => seq === loadSeq.current;

    // Clear the previous market's rows immediately.
    setData(null);
    setLoadError(null);
    setSlowLoad(false);
    setLoading(true);
    setLoadingMore(false);
    const slowTimer = setTimeout(() => { if (isCurrent()) setSlowLoad(true); }, SLOW_LOAD_MS);
    const timeout = setTimeout(() => ctrl.abort(), LOAD_TIMEOUT_MS);
    try {
      let result: ScreenerResponse;
      if (market === 'HK') result = await getHkScreenerStocks(forceRefresh, ctrl.signal);
      else if (market === 'SG') result = await getSgScreenerStocks(forceRefresh, ctrl.signal);
      else result = await getScreenerStocks({
        marketCapMin: 2_000_000_000,
        limit: US_FETCH_PAGE,
        offset: 0,
        refresh: forceRefresh,
      }, ctrl.signal);
      if (!isCurrent()) return;
      setData(result);
      setLastRef(new Date());
      setLoading(false);
      clearTimeout(timeout);

      // Remaining US pages.
      if (market === 'US' && result.has_more) {
        setLoadingMore(true);
        let offset = result.items.length;
        for (let p = 1; p < US_MAX_PAGES; p++) {
          const next = await getScreenerStocks({
            // No live overlay on background pages: the price tick refreshes
            // whatever rows are on screen, and skipping it makes each page
            // a cache read.
            marketCapMin: 2_000_000_000, limit: US_FETCH_PAGE, offset, live: false,
          }, ctrl.signal);
          if (!isCurrent()) return;
          setData(prev => {
            if (!prev) return prev;
            const have = new Set(prev.items.map(i => i.symbol));
            return {
              ...next,
              items: [...prev.items, ...next.items.filter(i => !have.has(i.symbol))],
            };
          });
          offset += next.items.length;
          if (!next.has_more || next.items.length === 0) break;
        }
      }
    } catch (e) {
      if (!isCurrent()) return;
      const aborted = (e as Error).name === 'AbortError';
      // A partial US universe is still useful — keep what arrived.
      if (!dataRef.current?.items.length) {
        setLoadError(aborted ? TIMEOUT_ERROR : (e as Error).message);
      } else if (!aborted) {
        toast.error((e as Error).message);
      }
    } finally {
      clearTimeout(slowTimer);
      clearTimeout(timeout);
      if (isCurrent()) {
        setLoading(false);
        setLoadingMore(false);
        setSlowLoad(false);
      }
    }
  }, [market]);

  useEffect(() => {
    load();
    return () => { abortRef.current?.abort(); };
  }, [load]);

  // Symbols of the rows currently on screen — what the price tick refreshes.
  const visibleSymsRef = useRef<string[]>([]);
  const tickInFlight = useRef(false);

  // ── 15s live price refresh (the rows actually visible) ──────────────────
  // Skips a tick while the previous one is still in flight, so slow
  // provider responses can never stack into overlapping polls.
  useEffect(() => {
    const tick = async () => {
      if (tickInFlight.current) return;
      if (typeof document !== 'undefined' && document.hidden) return;
      const syms = visibleSymsRef.current.slice(0, PAGE_SIZE);
      if (!syms.length) return;
      const seq = loadSeq.current;
      tickInFlight.current = true;
      try {
        const { quotes } = await getScreenerPrices(syms);
        if (seq !== loadSeq.current) return;      // market switched mid-tick
        setData(prev => {
          if (!prev) return prev;
          const newItems = prev.items.map(item => {
            const q = quotes[item.symbol];
            // A stale quote (provider failure / delisted line) is not a
            // live price — keep what the row already shows.
            if (!q || q.stale) return item;
            return {
              ...item,
              price:      q.price      ?? item.price,
              // Quote market caps are in the listing currency; only USD rows
              // can take them without re-converting the USD band figure.
              ...((!item.currency || item.currency === 'USD') && q.marketCap != null
                ? { marketCap: q.marketCap, marketCapUsd: q.marketCap }
                : {}),
              volume:     q.volume     ?? item.volume,
              change_pct: q.change_pct != null ? q.change_pct : item.change_pct,
            };
          });
          return { ...prev, items: newItems };
        });
        setLastRef(new Date());
      } catch { /* silent */ } finally {
        tickInFlight.current = false;
      }
    };
    const initial = setTimeout(tick, 3000);
    const id = setInterval(tick, PRICE_TICK_MS);
    return () => { clearTimeout(initial); clearInterval(id); };
  }, []);

  // ── Sector dropdown options (derived from the loaded universe) ──────────
  // FMP tags every market with the same 11 sector names; deriving options
  // from the data guarantees the filter can never drift from the tagging.
  const sectorOptions = useMemo<FilterOption[]>(() => {
    const counts = new Map<string, number>();
    let other = 0;
    for (const r of data?.items ?? []) {
      const s = (r.sector || '').trim();
      if (!s || s === 'Unknown') { other += 1; continue; }
      counts.set(s, (counts.get(s) ?? 0) + 1);
    }
    const opts: FilterOption[] = [...counts.entries()]
      .sort((a, b) => {
        const ia = FMP_SECTOR_ORDER.indexOf(a[0]);
        const ib = FMP_SECTOR_ORDER.indexOf(b[0]);
        return ((ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)) || a[0].localeCompare(b[0]);
      })
      .map(([s, c]) => ({ id: s, label: s, count: c }));
    if (other > 0) opts.push({ id: 'Other', label: 'Other / unclassified', count: other });
    return [{ id: 'all', label: 'All sectors' }, ...opts];
  }, [data]);

  const capOptions = useMemo<FilterOption[]>(
    () => CAP_RANGES.map(c => ({ id: c.id, label: c.label })),
    [],
  );
  const capRange = useMemo(
    () => CAP_RANGES.find(c => c.id === capId) ?? CAP_RANGES[0],
    [capId],
  );

  // ── Filter + sort (client-side) ─────────────────────────────────────────
  const rows = useMemo(() => {
    const items = data?.items ?? [];
    const q = search.trim().toLowerCase();
    const capMin = capRange.min;
    const capMax = capRange.max;
    let filtered = items.filter(r => {
      if (sector !== 'all') {
        const s = (r.sector || '').trim();
        const isOther = !s || s === 'Unknown';
        if (sector === 'Other') {
          if (!isOther) return false;
        } else if (isOther || s !== sector) return false;
      }
      if (capMin != null || capMax != null) {
        // Bands are USD; HK/SG rows carry marketCapUsd. No FX rate → the
        // row cannot be placed in a band, so it is excluded, not guessed.
        const mc = capUsd(r);
        if (mc == null) return false;               // can't verify → exclude
        if (capMin != null && mc < capMin) return false;
        if (capMax != null && mc >= capMax) return false;
      }
      if (q !== '' && !r.symbol.toLowerCase().includes(q) &&
          !(r.companyName || '').toLowerCase().includes(q)) return false;
      return true;
    });
    // Numeric sorts: largest first, missing values always last.
    const num = (v: number | null | undefined) => (v == null ? -Infinity : v);
    return [...filtered].sort((a, b) => {
      if (sortKey === 'composite') return (b.composite_score ?? -1) - (a.composite_score ?? -1);
      if (sortKey === 'marketCap') return num(capUsd(b) ?? b.marketCap) - num(capUsd(a) ?? a.marketCap);
      if (sortKey === 'price')     return num(b.price) - num(a.price);
      if (sortKey === 'change')    return num(b.change_pct) - num(a.change_pct);
      const ga = a.vgpm?.[sortKey]?.grade;
      const gb = b.vgpm?.[sortKey]?.grade;
      return gradeRank(gb) - gradeRank(ga);
    });
  }, [data, sector, capRange, search, sortKey]);

  // ── Paging (client-side over the filtered universe) ─────────────────────
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  useEffect(() => { setPage(0); }, [market, sector, capId, sortKey, search]);
  useEffect(() => { if (page > pageCount - 1) setPage(pageCount - 1); }, [page, pageCount]);
  const pageRows = useMemo(
    () => rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [rows, page],
  );
  useEffect(() => { visibleSymsRef.current = pageRows.map(r => r.symbol); }, [pageRows]);

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
    <div className="min-h-full flex flex-col bg-background ui-flat">
      <TabHero title={t("Screener")} />
      {/* Market segmented control */}
      <div className="px-3 md:px-6 pt-3">
        <div className="flex items-center gap-1 p-1 bg-muted/60 border border-border/60 rounded-lg">
          {(['US', 'HK', 'SG'] as Market[]).map(m => (
            <button
              key={m}
              onClick={() => {
                if (m === market) return;
                // Drop the old market's rows in the same render as the label
                // change; load() then fetches the new universe.
                abortRef.current?.abort();
                loadSeq.current += 1;
                setData(null);
                setLoading(true);
                setMarket(m); setSector('all'); setCapId('all');
              }}
              className={`flex-1 h-8 md:h-9 rounded-md text-xs md:text-[12.5px] font-medium transition-colors
                ${market === m ? 'bg-card text-foreground shadow-sm border border-border' : 'text-muted-foreground active:text-foreground'}`}
            >
              {MARKET_LABELS[m]}
            </button>
          ))}
        </div>
      </div>

      {/* Search bar with the filters inside, flush right:
          Sector | Market cap | VGPM(sort) */}
      <div className="px-3 md:px-6 pt-2.5">
        <div className="flex flex-wrap items-center gap-1.5 rounded-xl bg-muted/60 border border-border focus-within:bg-card focus-within:border-brand/40 px-2 py-1.5 transition-colors">
          <div className="relative flex-1 min-w-[9rem]">
            <Search className="absolute left-1.5 top-1/2 -translate-y-1/2 text-muted-foreground/70" width={15} height={15}/>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder={t("Search ticker or name")}
              className="w-full h-11 md:h-10 pl-7 pr-2 text-[13px] md:text-[14px] bg-transparent focus:outline-none placeholder:text-muted-foreground/70 text-foreground"
            />
          </div>
          <div className="flex flex-wrap items-center gap-1.5 ml-auto justify-end">
            <FilterDropdown label="Sector" value={sector} options={sectorOptions} onSelect={setSector} searchable/>
            <FilterDropdown label="Market Cap" value={capId} options={capOptions} onSelect={setCapId}/>
            <FilterDropdown label="Sort" value={sortKey} options={SORT_OPTIONS}
                            onSelect={id => setSortKey(id as SortKey)} defaultValue="composite"/>
          </div>
        </div>
      </div>

      {/* Rows */}
      <div className="px-3 md:px-6 pt-2 pb-6 flex-1">
        <div className="flex items-center justify-between px-1 mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[15px] md:text-[17px] font-semibold text-foreground">
              {t("Top candidates")}
            </span>
            {lastRefreshed && (
              <span className="inline-flex items-center gap-1 text-xs md:text-xs text-muted-foreground/70">
                <span className="w-1 h-1 rounded-full bg-brand"/>
                {t('updated {time}', { time: lastRefreshed.toLocaleTimeString() })}
              </span>
            )}
          </div>
          <span className="text-xs md:text-xs text-muted-foreground/70">
            {rows.length.toLocaleString()} · {market}
            {loadingMore && <> · {t("loading more…")}</>}
          </span>
        </div>

        <div className="rounded-lg border border-border bg-card overflow-hidden shadow-sm">
          {/* Header row — the desktop header shares DESK_GRID with the body
              rows; the mobile header's gap-2 must match the mobile body rows'
              grid so the 12-column tracks line up exactly under each row. */}
          {isDesktop ? (
            <div className={`px-4 py-2.5 border-b border-border/60 text-xs font-medium text-muted-foreground ${DESK_GRID}`}>
              <span>{t("Ticker")}</span>
              <span className="hidden lg:block text-right">{t("Price")}</span>
              <span className="hidden lg:block text-right">{t("Change")}</span>
              <span className="text-right">{t("Score")}</span>
              <div className="flex justify-end">
                {['V', 'G', 'P', 'M'].map(l => (
                  <span key={l} className="w-9 text-center">{l}</span>
                ))}
              </div>
              <span aria-hidden />
            </div>
          ) : (
            <div className="px-3 py-2 border-b border-border/60 grid grid-cols-12 items-center gap-2 text-xs font-medium text-muted-foreground">
              <span className="col-span-5">{t("Ticker · Sector")}</span>
              <span className="col-span-2 text-right">{t("Score")}</span>
              <div className="col-span-5 flex justify-end">
                {['V', 'G', 'P', 'M'].map(l => (
                  <span key={l} className="w-9 text-center">{l}</span>
                ))}
              </div>
            </div>
          )}

          {loadError && !data ? (
            <div className="px-3 py-10 text-center text-[12px] text-muted-foreground">
              <div>
                {loadError === TIMEOUT_ERROR
                  ? t('The screener is taking longer than usual to load. It keeps building in the background — try again in a moment.')
                  : t('Could not load the screener: {msg}', { msg: loadError })}
              </div>
              <button
                type="button"
                onClick={() => load()}
                className="mt-3 h-8 px-3 rounded-md border border-border bg-card text-foreground text-[12px] hover:bg-muted transition-colors"
              >
                {t("Retry")}
              </button>
            </div>
          ) : loading && !data ? (
            <div className="px-3 py-10 text-center text-[12px] text-muted-foreground/70" role="status">
              <div>{t("Loading…")}</div>
              {slowLoad && (
                <div className="mt-1.5">
                  {t('Building the {market} screener — a first load can take up to a minute.', { market })}
                </div>
              )}
            </div>
          ) : rows.length === 0 ? (
            <div className="px-3 py-10 text-center text-[12px] text-muted-foreground/70">
              {t("No matches. Adjust filters.")}
            </div>
          ) : isDesktop ? (
            /* ── Desktop rows: dense table-style list, hover actions ────────
               Click anywhere on the row to open the report; Analyse/Watch
               reveal on hover (swipe gestures are phone-only). */
            pageRows.map((r, i) => (
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
                    <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground/70">
                      <span className="truncate">{r.sector ? t(r.sector) : '—'}</span>
                      <span className="text-muted-foreground/50">·</span>
                      <span className="tabular-nums shrink-0">{formatMarketCap(r.marketCap, r.currency ?? MARKET_CCY[market])}</span>
                    </div>
                  </div>
                  {/* Price — lg+ only, in sync with DESK_GRID's template */}
                  <div className="hidden lg:block text-right text-[13px] font-medium text-foreground tabular-nums">
                    {formatPrice(r.price, r.currency ?? MARKET_CCY[market])}
                  </div>
                  {/* % change — lg+ only */}
                  <div className="hidden lg:block text-right">
                    {r.change_pct != null ? (
                      <span className={`text-[12.5px] font-semibold tabular-nums ${r.change_pct >= 0 ? 'text-gain' : 'text-loss'}`}>
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
                      <span className="text-xs text-muted-foreground/70">/100</span>
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
                      title={t("Analyse")}
                      aria-label={`Analyse ${r.symbol}`}
                      onClick={() => handleOpen(r.symbol)}
                      className="p-1.5 rounded-md text-muted-foreground/70 hover:text-foreground hover:bg-muted transition-colors"
                    >
                      <Search width={15} height={15}/>
                    </button>
                    <button
                      type="button"
                      title={t("Add to watchlist")}
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
            pageRows.map((r, i) => (
              <SwipeRow
                key={r.symbol}
                onClick={() => handleOpen(r.symbol)}
                className={i > 0 ? 'border-t border-border/60' : ''}
                actions={[
                  {
                    icon: <Search width={18} height={18} strokeWidth={2}/>,
                    label: 'Analyse',
                    color: 'hsl(var(--brand))',
                    textColor: 'hsl(var(--brand-foreground))',
                    onClick: () => handleOpen(r.symbol),
                  },
                  {
                    icon: <Bookmark width={18} height={18} strokeWidth={2}/>,
                    label: 'Watch',
                    color: 'hsl(var(--surface-2-active))',
                    onClick: () => handleWatch(r.symbol),
                  },
                ]}
              >
                <div className="w-full text-left grid grid-cols-12 items-center gap-2 px-3 py-4 active:bg-muted/60 transition-colors">
                  <div className="col-span-5 min-w-0">
                    <div className="text-[12.5px] font-semibold text-foreground tabular-nums truncate">{r.symbol}</div>
                    <div className="text-xs text-muted-foreground truncate">{r.companyName}</div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground/70">
                      <span className="truncate">{r.sector ? t(r.sector) : '—'}</span>
                      <span className="text-muted-foreground/50">·</span>
                      <span className="tabular-nums">{formatMarketCap(r.marketCap, r.currency ?? MARKET_CCY[market])}</span>
                    </div>
                  </div>
                  <div className="col-span-2 flex flex-col items-end">
                    <div className="flex items-baseline gap-1">
                      <span className="text-[14px] font-semibold text-foreground tabular-nums">
                        {r.composite_score ?? '—'}
                      </span>
                      <span className="text-xs text-muted-foreground/70">/100</span>
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

        {rows.length > PAGE_SIZE && (
          <div className="mt-3 px-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage(p => Math.max(0, p - 1))}
              className="h-8 px-3 rounded-md border border-border bg-card disabled:opacity-40 hover:bg-muted transition-colors"
            >
              {t("Previous")}
            </button>
            <span className="tabular-nums">
              {(page * PAGE_SIZE + 1).toLocaleString()}–{Math.min(rows.length, (page + 1) * PAGE_SIZE).toLocaleString()} {t("of")} {rows.length.toLocaleString()}
            </span>
            <button
              type="button"
              disabled={page >= pageCount - 1}
              onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))}
              className="h-8 px-3 rounded-md border border-border bg-card disabled:opacity-40 hover:bg-muted transition-colors"
            >
              {t("Next")}
            </button>
          </div>
        )}

        <div
          className="mt-3 px-1 text-xs text-muted-foreground"
          title="Score = 0.30·V + 0.25·G + 0.25·P + 0.20·M, sector-neutralised"
        >
          {t('{n} stocks', { n: data?.total ?? 0 })}
          {market !== 'US' && (
            <> · {t('Market caps shown in {ccy}; market-cap bands are USD at live FX', { ccy: MARKET_CCY[market] })}</>
          )}
          {data?.stale && <> · {t("refreshing in background")}</>}
        </div>
      </div>
    </div>
  );
}
