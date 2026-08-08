/**
 * HistoryPage.tsx — Reimagined UI (v2)
 *
 * Past analyses list wired to the real backend.
 * - Ongoing runs (green "Ongoing" cards with spinner) — clickable to resume viewing
 * - Search box
 * - Recent analyses with action pill + price target + upside + VGPM grades
 * - SwipeRow: swipe left → Delete
 * - Pagination (page_size=50)
 */

import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { getHistory, getCompanyNames, deleteRun } from '@/lib/api';
import type { HistoryResponse, RunSummary } from '@/lib/reportTypes';
import { useActiveRun } from '@/contexts/active-run-context';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { parseBackendIso } from '@/lib/utils';
import {
  Search,
  X,
  Clock,
  ChevRight,
  ChevronDn,
  ActionPill,
  GradeChip,
  Delta,
  SwipeRow,
} from '@/components/v2/shared';
import { toast } from 'sonner';
import { TabHero } from '@/components/layout/TabHero';

/* ───────── Filter option sets ───────── */
// Union of sector labels across US, HK, SG markets (per ScreenerPage).
// Covers everything RunSummary.sector could hold.
const SECTOR_OPTIONS = [
  'All sectors',
  'Technology', 'Tech',
  'Communication Services',
  'Financial Services', 'Financials',
  'Consumer Cyclical', 'Consumer Defensive', 'Consumer',
  'Healthcare',
  'Industrials',
  'Energy',
  'Real Estate', 'Property', 'REIT',
  'Utilities',
  'Basic Materials',
  'Telco',
] as const;

type MarketOption = 'All markets' | 'US' | 'HK' | 'SG';
const MARKET_OPTIONS: readonly MarketOption[] = ['All markets', 'US', 'HK', 'SG'];

type TimeOption = 'All time' | 'Last 30 days' | 'Last 10 days' | 'Last 5 days' | 'Yesterday';
const TIME_OPTIONS: readonly TimeOption[] = ['All time', 'Last 30 days', 'Last 10 days', 'Last 5 days', 'Yesterday'];

type ActionOption = 'Any action' | 'BUY' | 'HOLD' | 'SELL' | 'SHORT';
const ACTION_OPTIONS: readonly ActionOption[] = ['Any action', 'BUY', 'HOLD', 'SELL', 'SHORT'];

/** Infer listing market from a ticker symbol. */
function marketOf(ticker: string): MarketOption {
  const t = (ticker || '').toUpperCase();
  if (t.endsWith('.HK')) return 'HK';
  if (t.endsWith('.SI')) return 'SG';
  return 'US';
}

/** Convert a TimeOption into a cutoff Date; entries older than this are filtered out. */
function timeCutoff(opt: TimeOption): Date {
  const now = new Date();
  const d = new Date(now);
  switch (opt) {
    case 'All time':
      return new Date(0); // epoch — never filters anything out
    case 'Yesterday':
      d.setDate(d.getDate() - 1); d.setHours(0, 0, 0, 0); return d;
    case 'Last 5 days':
      d.setDate(d.getDate() - 5); return d;
    case 'Last 10 days':
      d.setDate(d.getDate() - 10); return d;
    case 'Last 30 days':
    default:
      d.setDate(d.getDate() - 30); return d;
  }
}

function daysAgo(iso: string): string {
  const d = parseBackendIso(iso);
  const ms = Date.now() - d.getTime();
  const days = Math.floor(ms / (1000 * 60 * 60 * 24));
  if (days === 0) {
    const hrs = Math.floor(ms / (1000 * 60 * 60));
    if (hrs === 0) return `${Math.max(1, Math.floor(ms / 60000))}m ago`;
    return `${hrs}h ago`;
  }
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function HistoryPage() {
  const navigate = useNavigate();
  const { activeRuns, recentlyCompleted, clearCompleted, byTicker } = useActiveRun();
  // Gate "wider + denser" desktop layout on the layout MODE (not raw viewport
  // width) so the 430px mobile phone-frame preview a desktop user can render
  // stays single-column. Combined with Tailwind `lg:` below, iPad-portrait in
  // desktop mode (768–1023px) also stays single-column.
  const { mode } = useLayoutMode();
  const isDesktop = mode === 'desktop';

  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  // ── Filter state ──────────────────────────────────────────────────────────
  const [sectorFilter, setSectorFilter] = useState<string>('All sectors');
  const [marketFilter, setMarketFilter] = useState<MarketOption>('All markets');
  const [timeFilter, setTimeFilter]     = useState<TimeOption>('All time');
  const [actionFilter, setActionFilter] = useState<ActionOption>('Any action');
  const [page, setPage] = useState(1);
  const deleteGuard = useRef<Set<string>>(new Set());

  // Fallback: read activeRuns from sessionStorage if context lost them (iOS Safari)
  const sessionStorageActiveRuns: Array<{ ticker: string; startedAt: string }> = (() => {
    try {
      const stored = sessionStorage.getItem('activeRuns') || sessionStorage.getItem('activeRun');
      if (!stored) return [];
      const parsed = JSON.parse(stored);
      const arr = Array.isArray(parsed) ? parsed : [parsed];
      return arr.filter((r: any) => Date.now() - new Date(r.startedAt).getTime() < 45 * 60 * 1000);
    } catch { return []; }
  })();
  // UI-side safety net (2026-04-25 fix): also derive ongoing tickers from
  // byTicker entries with streamState='running' or 'reconnecting'. Catches
  // the regression where the SSE stream populated the per-ticker slice but
  // markRunStarted was missed (e.g. switchTicker from History → poll() path
  // never wrote to activeRuns), or when sessionStorage was wiped mid-run.
  const byTickerOngoing: Array<{ ticker: string; startedAt: string }> = Object.entries(byTicker)
    .filter(([, slice]) => slice.streamState === 'running' || slice.streamState === 'reconnecting')
    .map(([t]) => ({
      // No real startedAt available from byTicker — use first event timestamp
      // if any, otherwise now. Display only.
      ticker: t,
      startedAt: byTicker[t]?.streamEvents?.[0]?.timestamp || new Date().toISOString(),
    }));
  // Merge (dedupe by ticker) with priority: context activeRuns > sessionStorage > byTicker
  const _seen = new Set<string>();
  const effectiveActiveRuns: Array<{ ticker: string; startedAt: string }> = [];
  for (const src of [activeRuns, sessionStorageActiveRuns, byTickerOngoing]) {
    for (const r of src) {
      const T = r.ticker.toUpperCase();
      if (_seen.has(T)) continue;
      _seen.add(T);
      effectiveActiveRuns.push({ ...r, ticker: T });
    }
  }

  // ── Fetch history ─────────────────────────────────────────────────────────
  const load = useCallback(async (p: number = page) => {
    setLoading(true);
    try {
      const data = await getHistory({ page: p, page_size: 50 });
      setHistory(data);
      // Fetch company names in one batch
      const tickers = Array.from(new Set(data.items.map(r => r.ticker)));
      if (tickers.length > 0) {
        try {
          const nameMap = await getCompanyNames(tickers);
          const simplified: Record<string, string> = {};
          for (const [t, profile] of Object.entries(nameMap)) {
            simplified[t] = (profile as any)?.name || t;
          }
          setNames(prev => ({ ...prev, ...simplified }));
        } catch { /* ignore */ }
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { load(page); }, [load, page]);

  // Refresh on newly completed run
  useEffect(() => {
    if (recentlyCompleted) {
      load(1);
      setTimeout(() => clearCompleted(), 3000);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentlyCompleted]);

  // ── Filter by search + filter chips ───────────────────────────────────────
  const rows = useMemo(() => {
    const items = history?.items ?? [];
    const query = q.trim().toLowerCase();
    const cutoff = timeCutoff(timeFilter);

    return items.filter(r => {
      // Text search
      if (query) {
        const matches = r.ticker.toLowerCase().includes(query) ||
                        (names[r.ticker] || '').toLowerCase().includes(query);
        if (!matches) return false;
      }
      // Sector
      if (sectorFilter !== 'All sectors' && (r.sector || '').toLowerCase() !== sectorFilter.toLowerCase()) {
        return false;
      }
      // Market (inferred from ticker)
      if (marketFilter !== 'All markets' && marketOf(r.ticker) !== marketFilter) {
        return false;
      }
      // Time window — compare run_at to cutoff; Yesterday is a single day window
      const runDate = parseBackendIso(r.run_at);
      if (timeFilter === 'Yesterday') {
        const end = new Date(cutoff); end.setDate(end.getDate() + 1);
        if (runDate < cutoff || runDate >= end) return false;
      } else {
        if (runDate < cutoff) return false;
      }
      // Action
      if (actionFilter !== 'Any action') {
        const a = (r.final_action || '').toUpperCase();
        if (!a.includes(actionFilter)) return false;
      }
      return true;
    });
  }, [history, q, names, sectorFilter, marketFilter, timeFilter, actionFilter]);

  const handleDelete = async (runId: string) => {
    if (deleteGuard.current.has(runId)) return;
    deleteGuard.current.add(runId);
    try {
      await deleteRun(runId);
      setHistory(prev => prev
        ? { ...prev, items: prev.items.filter(r => r.run_id !== runId), total: prev.total - 1 }
        : prev);
      toast.success('Run deleted');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      deleteGuard.current.delete(runId);
    }
  };

  const handleOpenOngoing = (ticker: string) => {
    navigate('/report', { state: { resume: true, switchTicker: ticker } });
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-full flex flex-col bg-background">
      <TabHero title="History" />
      {/* Desktop: constrain to a centered column. Wider on desktop (max-w-7xl)
          to host the 2-column run grid below; narrower (max-w-5xl) otherwise.
          On mobile the max-width exceeds the 430px frame, so it no-ops and the
          content fills the phone width as before. */}
      <div className={`w-full mx-auto flex flex-1 flex-col ${isDesktop ? 'max-w-7xl' : 'max-w-5xl'}`}>
      {/* Search */}
      <div className="px-3 pt-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/70" width={15} height={15}/>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search ticker or company"
            className="w-full h-10 pl-8 pr-3 text-[13px] rounded-lg bg-muted/60 border border-border focus:bg-card focus:border-brand/40 focus:outline-none focus:ring-2 focus:ring-brand/10 placeholder:text-muted-foreground/70 text-foreground"
          />
        </div>
      </div>

      {/* Filter chips — each is a working dropdown. Funnel icon removed per design. */}
      <div className="px-3 pt-2.5 pb-1 flex items-center gap-1.5 overflow-x-auto phone-scroll">
        <FilterPill
          value={sectorFilter}
          options={SECTOR_OPTIONS as readonly string[]}
          onChange={v => setSectorFilter(v)}
          active={sectorFilter !== 'All sectors'}
        />
        <FilterPill
          label="Market"
          value={marketFilter}
          options={MARKET_OPTIONS as readonly string[]}
          onChange={v => setMarketFilter(v as MarketOption)}
          active={marketFilter !== 'All markets'}
        />
        <FilterPill
          label={timeFilter === 'All time' ? 'Any time' : 'Last search'}
          value={timeFilter}
          options={TIME_OPTIONS as readonly string[]}
          onChange={v => setTimeFilter(v as TimeOption)}
          active={timeFilter !== 'All time'}
        />
        <FilterPill
          value={actionFilter}
          options={ACTION_OPTIONS as readonly string[]}
          onChange={v => setActionFilter(v as ActionOption)}
          active={actionFilter !== 'Any action'}
        />
      </div>

      <div className="flex-1 px-3 pt-2 pb-6">
        {/* Ongoing runs */}
        {effectiveActiveRuns.map(r => (
          <button
            key={r.ticker}
            onClick={() => handleOpenOngoing(r.ticker)}
            className="w-full mb-3 p-3 rounded-lg border border-brand/25 bg-brand/10 active:bg-brand/20 text-left flex items-center gap-2.5 transition-colors"
          >
            <div className="relative w-8 h-8 rounded-md bg-card border border-brand/25 flex items-center justify-center">
              <span className="absolute inset-0 rounded-md border-2 border-brand border-t-transparent animate-spin" />
              <Clock width={12} height={12} className="text-brand" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-[13px] font-semibold text-foreground tabular-nums">{r.ticker}</span>
                <span className="text-[10px] font-medium uppercase tracking-wider text-brand">Ongoing</span>
              </div>
              <div className="text-[11px] text-muted-foreground truncate">
                started {daysAgo(r.startedAt)}
              </div>
            </div>
            <ChevRight width={14} height={14} className="text-brand" />
          </button>
        ))}

        {/* Recent analyses header */}
        <div className="flex items-center justify-between px-1 mb-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/70">
            Recent analyses
          </span>
          <span className="text-[10px] text-muted-foreground/70">
            {history?.total ?? 0} total
          </span>
        </div>

        {/* History list. Desktop: 2-column grid of self-contained run cards
            (denser, uses the wider column). Mobile: single bordered card with
            hairline row separators, as before. */}
        <div className={isDesktop
          ? 'grid grid-cols-1 lg:grid-cols-2 gap-3 items-start'
          : 'rounded-lg border border-border bg-card overflow-hidden shadow-sm'}>
          {loading && !history ? (
            <div className={`px-3 py-10 text-center text-[12px] text-muted-foreground/70 ${isDesktop ? 'lg:col-span-2' : ''}`}>Loading…</div>
          ) : rows.length === 0 ? (
            <div className={`px-3 py-10 text-center text-[12px] text-muted-foreground/70 ${isDesktop ? 'lg:col-span-2' : ''}`}>
              {(history?.total ?? 0) === 0
                ? 'No analysis runs yet. Run your first one from Home.'
                : q
                  ? 'No matches. Clear search to see all runs.'
                  : 'No runs match the current filters. Try a wider time range or clearing filters.'}
            </div>
          ) : (
            rows.map((r, i) => <HistoryRow
              key={r.run_id}
              row={r}
              name={names[r.ticker]}
              isNew={recentlyCompleted?.runId === r.run_id}
              isDesktop={isDesktop}
              className={isDesktop
                ? 'rounded-lg border border-border shadow-sm'
                : (i > 0 ? 'border-t border-border/60' : '')}
              onOpen={() => navigate(`/report/${r.run_id}`)}
              onDelete={() => handleDelete(r.run_id)}
            />)
          )}
        </div>

        {/* Pagination */}
        {history && history.total > 50 && (
          <div className="flex items-center justify-between mt-4 px-1">
            <span className="text-[11px] text-muted-foreground/70">
              Page {history.page} · {history.items.length} of {history.total}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="h-8 px-3 text-[11px] rounded-md border border-border text-muted-foreground active:bg-muted/60 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={page * 50 >= history.total}
                className="h-8 px-3 text-[11px] rounded-md border border-border text-foreground/80 active:bg-muted/60 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}

/* ───────── Row ───────── */
function HistoryRow({
  row,
  name,
  isNew,
  isDesktop = false,
  className = '',
  onOpen,
  onDelete,
}: {
  row: RunSummary;
  name?: string;
  isNew?: boolean;
  isDesktop?: boolean;
  className?: string;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const upside = typeof row.ev_upside_pct === 'number' ? row.ev_upside_pct : null;

  return (
    <SwipeRow
      onClick={onOpen}
      className={className}
      actions={[
        {
          icon: <X width={20} height={20} strokeWidth={2}/>,
          label: 'Delete',
          color: '#ef4444',
          onClick: onDelete,
        },
      ]}
    >
      <div
        className={`w-full text-left flex items-center transition-colors ${isDesktop ? 'px-4 py-2.5 gap-5' : 'p-3 gap-3'} ${isNew ? 'bg-brand/10' : ''}`}
      >
        {/* Only the ticker column triggers the open action — price + VGPM
            cells sit outside the data-tap="open" subtree. Swipe-to-delete
            still works anywhere on the row.
            Desktop: identity grows (flex-1) and the price+grades cluster on
            the right (right-aligned price) — this kills the dead middle gap
            the mobile fixed-% columns leave on the wider 2-col cards, and
            bumps fonts up so the cards don't read as sparse. */}
        <div data-tap="open" className={`min-w-0 active:bg-muted/60 rounded-md -m-1 p-1 cursor-pointer ${isDesktop ? 'flex-1' : 'w-[40%]'}`}>
          <div className="flex items-center gap-1.5">
            <span className={`font-semibold text-foreground tabular-nums tracking-tight ${isDesktop ? 'text-[15px]' : 'text-[13px]'}`}>
              {row.ticker}
            </span>
            {isNew && (
              <span className="text-[10px] font-bold uppercase tracking-wider text-brand">
                new
              </span>
            )}
          </div>
          <div className={`text-muted-foreground truncate ${isDesktop ? 'text-[12.5px]' : 'text-[11px]'}`}>
            {name || row.sector || '—'}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <ActionPill action={row.final_action || null} />
            <span className={`text-muted-foreground/70 ${isDesktop ? 'text-[11px]' : 'text-[10px]'}`}>
              {daysAgo(row.run_at)}
            </span>
          </div>
        </div>
        <div className={isDesktop ? 'shrink-0 w-24 text-right' : 'w-[24%]'}>
          {row.price_target != null ? (
            <>
              <div className={`font-semibold text-foreground tabular-nums ${isDesktop ? 'text-[15px]' : 'text-[12px]'}`}>
                ${row.price_target.toLocaleString(undefined, {
                  maximumFractionDigits: row.price_target < 10 ? 2 : 0,
                })}
              </div>
              <div className={isDesktop ? 'text-[12px]' : 'text-[10px]'}><Delta v={upside}/></div>
              <div className={`text-muted-foreground/70 mt-0.5 uppercase tracking-wider ${isDesktop ? 'text-[10px]' : 'text-[10px]'}`}>
                Target
              </div>
            </>
          ) : (
            <div className="text-[10px] text-muted-foreground/70">—</div>
          )}
        </div>
        <div className={`flex items-center ${isDesktop ? 'gap-2.5 shrink-0' : 'gap-2 ml-auto'}`}>
          <GradeChip grade={row.vgpm_grades?.valuation}     label="V"/>
          <GradeChip grade={row.vgpm_grades?.growth}        label="G"/>
          <GradeChip grade={row.vgpm_grades?.profitability} label="P"/>
          <GradeChip grade={row.vgpm_grades?.momentum}      label="M"/>
        </div>
      </div>
    </SwipeRow>
  );
}

/* ───────── FilterPill ───────────────────────────────────────────────────────
   Chip with a small chevron that opens a lightweight dropdown of options.
   - `label` overrides the displayed text when provided (e.g. "Market" instead
     of the raw selected value "US"). When omitted, the current value is shown.
   - `active` colours the pill when a non-default option is selected.
   - Closes on outside-click and on Escape. */
function FilterPill({
  label,
  value,
  options,
  onChange,
  active,
}: {
  label?: string;
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
  active?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const btnRef    = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  // Viewport-fixed position so we escape the parent's overflow-x-auto clip.
  const [pos, setPos] = useState<{ top: number; left: number; minWidth: number } | null>(null);

  const recomputePos = useCallback(() => {
    const btn = btnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const minWidth = Math.max(r.width, 160);
    const viewportW = window.innerWidth;
    // Clamp left so the dropdown never overflows the right edge (common on
    // the last pill "Any action" which sits far right).
    const desiredLeft = r.left;
    const left = Math.min(desiredLeft, viewportW - minWidth - 8);
    setPos({ top: r.bottom + 4, left: Math.max(8, left), minWidth });
  }, []);

  // Open/close side-effects: position, outside-click, Escape, scroll/resize.
  useEffect(() => {
    if (!open) return;
    recomputePos();
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (btnRef.current?.contains(t)) return;
      if (popoverRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    // Any scroll in the document (inc. the chip row itself) should reposition.
    const onScroll = () => recomputePos();
    const onResize = () => recomputePos();
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true); // capture to catch nested scrollers
    window.addEventListener('resize', onResize);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
    };
  }, [open, recomputePos]);

  const display = label ?? value;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`h-8 pl-2.5 pr-1.5 text-[11px] rounded-lg border flex items-center gap-1 shrink-0 transition-colors ${
          active
            ? 'bg-brand/10 border-brand/25 text-brand'
            : 'bg-card border-border text-muted-foreground active:bg-muted/60'
        }`}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {display}
        <ChevronDn width={11} height={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && pos && createPortal(
        <div
          ref={popoverRef}
          role="listbox"
          style={{ position: 'fixed', top: pos.top, left: pos.left, minWidth: pos.minWidth, maxHeight: '60vh' }}
          className="overflow-y-auto bg-card border border-border rounded-lg shadow-xl z-[200] py-1"
        >
          {options.map(opt => {
            const selected = opt === value;
            return (
              <button
                key={opt}
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => { onChange(opt); setOpen(false); }}
                className={`w-full text-left px-3 py-1.5 text-[12px] flex items-center justify-between gap-2 transition-colors ${
                  selected
                    ? 'bg-brand/10 text-brand font-medium'
                    : 'text-foreground/80 hover:bg-muted/60'
                }`}
              >
                <span>{opt}</span>
                {selected && (
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>,
        document.body
      )}
    </>
  );
}
