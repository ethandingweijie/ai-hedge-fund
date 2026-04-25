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

type TimeOption = 'Last 30 days' | 'Last 10 days' | 'Last 5 days' | 'Yesterday';
const TIME_OPTIONS: readonly TimeOption[] = ['Last 30 days', 'Last 10 days', 'Last 5 days', 'Yesterday'];

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
  const d = new Date(iso);
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

  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  // ── Filter state ──────────────────────────────────────────────────────────
  const [sectorFilter, setSectorFilter] = useState<string>('All sectors');
  const [marketFilter, setMarketFilter] = useState<MarketOption>('All markets');
  const [timeFilter, setTimeFilter]     = useState<TimeOption>('Last 30 days');
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
      const runDate = new Date(r.run_at);
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
    <div className="min-h-full flex flex-col bg-white dark:bg-zinc-900">
      {/* Search */}
      <div className="px-3 pt-3" style={{ paddingTop: 'calc(env(safe-area-inset-top) + 12px)' }}>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400 dark:text-zinc-500" width={15} height={15}/>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search ticker or company"
            className="w-full h-10 pl-8 pr-3 text-[13px] rounded-lg bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-800 focus:bg-white dark:focus:bg-zinc-900 focus:border-zinc-300 dark:focus:border-zinc-700 focus:outline-none focus:ring-2 focus:ring-[#2e7d32]/10 placeholder:text-zinc-400 text-zinc-900 dark:text-zinc-50"
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
          label="Last search"
          value={timeFilter}
          options={TIME_OPTIONS as readonly string[]}
          onChange={v => setTimeFilter(v as TimeOption)}
          active={timeFilter !== 'Last 30 days'}
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
            className="w-full mb-3 p-3 rounded-xl border border-[#d0e7d2] dark:border-[#2e7d32]/40 bg-[#ecf5ed]/70 dark:bg-[#2e7d32]/10 active:bg-[#ecf5ed] text-left flex items-center gap-2.5 transition-colors"
          >
            <div className="relative w-8 h-8 rounded-md bg-white dark:bg-zinc-900 border border-[#d0e7d2] dark:border-[#2e7d32]/40 flex items-center justify-center">
              <span className="absolute inset-0 rounded-md border-2 border-[#2e7d32] border-t-transparent animate-spin" />
              <Clock width={12} height={12} className="text-[#2e7d32] dark:text-[#4ea354]" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums">{r.ticker}</span>
                <span className="text-[10px] font-medium uppercase tracking-wider text-[#2e7d32] dark:text-[#4ea354]">Ongoing</span>
              </div>
              <div className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                started {daysAgo(r.startedAt)}
              </div>
            </div>
            <ChevRight width={14} height={14} className="text-[#2e7d32] dark:text-[#4ea354]" />
          </button>
        ))}

        {/* Recent analyses header */}
        <div className="flex items-center justify-between px-1 mb-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-zinc-400 dark:text-zinc-500">
            Recent analyses
          </span>
          <span className="text-[10px] text-zinc-400 dark:text-zinc-500">
            {history?.total ?? 0} total
          </span>
        </div>

        {/* History list */}
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-hidden shadow-sm">
          {loading && !history ? (
            <div className="px-3 py-10 text-center text-[12px] text-zinc-400 dark:text-zinc-500">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="px-3 py-10 text-center text-[12px] text-zinc-400 dark:text-zinc-500">
              {q ? 'No matches. Clear search to see all runs.' : 'No analysis runs yet. Run your first one from Home.'}
            </div>
          ) : (
            rows.map((r, i) => <HistoryRow
              key={r.run_id}
              row={r}
              name={names[r.ticker]}
              isNew={recentlyCompleted?.runId === r.run_id}
              className={i > 0 ? 'border-t border-zinc-100 dark:border-zinc-800' : ''}
              onOpen={() => navigate(`/report/${r.run_id}`)}
              onDelete={() => handleDelete(r.run_id)}
            />)
          )}
        </div>

        {/* Pagination */}
        {history && history.total > 50 && (
          <div className="flex items-center justify-between mt-4 px-1">
            <span className="text-[11px] text-zinc-400 dark:text-zinc-500">
              Page {history.page} · {history.items.length} of {history.total}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="h-8 px-3 text-[11px] rounded-md border border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400 active:bg-zinc-50 dark:active:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={page * 50 >= history.total}
                className="h-8 px-3 text-[11px] rounded-md border border-zinc-200 dark:border-zinc-800 text-zinc-700 dark:text-zinc-300 active:bg-zinc-50 dark:active:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────── Row ───────── */
function HistoryRow({
  row,
  name,
  isNew,
  className = '',
  onOpen,
  onDelete,
}: {
  row: RunSummary;
  name?: string;
  isNew?: boolean;
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
        className={`w-full text-left p-3 flex items-center gap-3 transition-colors ${isNew ? 'bg-[#ecf5ed] dark:bg-[#2e7d32]/10' : ''}`}
      >
        {/* Only the ticker column triggers the open action — price + VGPM
            cells sit outside the data-tap="open" subtree. Swipe-to-delete
            still works anywhere on the row. */}
        <div data-tap="open" className="min-w-0 w-[40%] active:bg-zinc-50 dark:active:bg-zinc-800 rounded-md -m-1 p-1 cursor-pointer">
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums tracking-tight">
              {row.ticker}
            </span>
            {isNew && (
              <span className="text-[9px] font-bold uppercase tracking-wider text-[#2e7d32] dark:text-[#4ea354]">
                new
              </span>
            )}
          </div>
          <div className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
            {name || row.sector || '—'}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <ActionPill action={row.final_action || null} />
            <span className="text-[10px] text-zinc-400 dark:text-zinc-500">
              {daysAgo(row.run_at)}
            </span>
          </div>
        </div>
        <div className="w-[24%]">
          {row.price_target != null ? (
            <>
              <div className="text-[12px] font-semibold text-zinc-900 dark:text-zinc-50 tabular-nums">
                ${row.price_target.toLocaleString(undefined, {
                  maximumFractionDigits: row.price_target < 10 ? 2 : 0,
                })}
              </div>
              <div className="text-[10px]"><Delta v={upside}/></div>
              <div className="text-[9px] text-zinc-400 dark:text-zinc-500 mt-0.5 uppercase tracking-wider">
                Target
              </div>
            </>
          ) : (
            <div className="text-[10px] text-zinc-400 dark:text-zinc-500">—</div>
          )}
        </div>
        <div className="flex items-center gap-2 ml-auto">
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
            ? 'bg-[#ecf5ed] dark:bg-[#2e7d32]/15 border-[#d0e7d2] dark:border-[#2e7d32]/50 text-[#2e7d32] dark:text-[#4ea354]'
            : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400 active:bg-zinc-50 dark:active:bg-zinc-800'
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
          className="overflow-y-auto bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-lg shadow-xl z-[200] py-1"
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
                    ? 'bg-[#ecf5ed] dark:bg-[#2e7d32]/15 text-[#2e7d32] dark:text-[#4ea354] font-medium'
                    : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800'
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
