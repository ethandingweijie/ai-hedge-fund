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
import { useNavigate } from 'react-router-dom';
import { getHistory, getCompanyNames, deleteRun } from '@/lib/api';
import type { HistoryResponse, RunSummary } from '@/lib/reportTypes';
import { useActiveRun } from '@/contexts/active-run-context';
import {
  Search,
  X,
  Clock,
  ChevRight,
  Filter,
  ActionPill,
  GradeChip,
  Delta,
  SwipeRow,
} from '@/components/v2/shared';
import { toast } from 'sonner';

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
  const { activeRuns, recentlyCompleted, clearCompleted } = useActiveRun();

  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const deleteGuard = useRef<Set<string>>(new Set());

  // Fallback: read activeRuns from sessionStorage if context lost them (iOS Safari)
  const effectiveActiveRuns: Array<{ ticker: string; startedAt: string }> = activeRuns.length > 0
    ? activeRuns
    : (() => {
        try {
          const stored = sessionStorage.getItem('activeRuns') || sessionStorage.getItem('activeRun');
          if (!stored) return [];
          const parsed = JSON.parse(stored);
          const arr = Array.isArray(parsed) ? parsed : [parsed];
          return arr.filter((r: any) => Date.now() - new Date(r.startedAt).getTime() < 45 * 60 * 1000);
        } catch { return []; }
      })();

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

  // ── Filter by search ──────────────────────────────────────────────────────
  const rows = useMemo(() => {
    const items = history?.items ?? [];
    if (!q) return items;
    const query = q.toLowerCase();
    return items.filter(r =>
      r.ticker.toLowerCase().includes(query) ||
      (names[r.ticker] || '').toLowerCase().includes(query)
    );
  }, [history, q, names]);

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

      {/* Filter chips (static for now — filters already in backend endpoint) */}
      <div className="px-3 pt-2.5 pb-1 flex items-center gap-1.5 overflow-x-auto phone-scroll">
        {['All sectors', 'US · HK · SGX', 'Last 30d', 'Any action'].map(c => (
          <span key={c} className="h-8 px-2.5 text-[11px] rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400 flex items-center gap-1 shrink-0">
            {c}
          </span>
        ))}
        <button
          className="h-8 w-8 rounded-lg border border-zinc-200 dark:border-zinc-800 active:bg-zinc-50 dark:active:bg-zinc-800 flex items-center justify-center text-zinc-500 dark:text-zinc-400 shrink-0"
          aria-label="Filter"
        >
          <Filter width={13} height={13}/>
        </button>
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
        className={`w-full text-left p-3 flex items-center gap-3 active:bg-zinc-50 dark:active:bg-zinc-800 transition-colors ${isNew ? 'bg-[#ecf5ed] dark:bg-[#2e7d32]/10' : ''}`}
      >
        <div className="min-w-0 w-[40%]">
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
