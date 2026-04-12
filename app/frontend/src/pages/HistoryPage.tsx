import { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';
import { getHistory, getCompanyNames, deleteRun } from '@/lib/api';
import type { HistoryResponse } from '@/lib/reportTypes';
import { useActiveRun } from '@/contexts/active-run-context';
import { ResearchNav } from '@/components/layout/ResearchNav';
import { gradeColorClass } from '@/lib/gradeColors';
import { Filter, X } from 'lucide-react';

const ACTION_COLORS: Record<string, string> = {
  BUY:   'bg-green-600 text-white',
  SELL:  'bg-red-600 text-white',
  SHORT: 'bg-orange-500 text-white',
  COVER: 'bg-blue-600 text-white',
  HOLD:  'bg-yellow-500 text-white',
};

function GradePill({ grade, label }: { grade?: string; label: string }) {
  const clean = grade?.replace(/^~/, '') ?? '—';
  const isBlank = clean === '—';
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className="text-[8px] font-medium uppercase tracking-wider text-gray-400 dark:text-white/30">{label}</span>
      <span className={`text-[11px] font-bold px-1.5 py-0.5 rounded ${isBlank ? 'text-muted-foreground/40' : gradeColorClass(clean)}`}>
        {clean}
      </span>
    </div>
  );
}

const nameCache: Record<string, string> = {};

export function HistoryPage() {
  const navigate = useNavigate();
  const { user, logout, loading: authLoading } = useAuth();

  useEffect(() => {
    if (!authLoading && !user) navigate('/login', { state: { from: '/history' }, replace: true });
  }, [user, authLoading, navigate]);

  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nameMap, setNameMap] = useState<Record<string, string>>({});

  const { activeRuns, recentlyCompleted, clearCompleted, streamState } = useActiveRun();
  const isStreamRunning = streamState === 'running' || streamState === 'reconnecting';

  // Fallback: read activeRuns from sessionStorage if context lost them (iOS Safari)
  const effectiveActiveRuns: Array<{ ticker: string; startedAt: string }> = activeRuns.length > 0
    ? activeRuns
    : (() => {
        try {
          const stored = sessionStorage.getItem('activeRuns') || sessionStorage.getItem('activeRun');
          if (!stored) return [];
          const parsed = JSON.parse(stored);
          const arr = Array.isArray(parsed) ? parsed : [parsed];
          return arr.filter((r: any) => Date.now() - new Date(r.startedAt).getTime() < 30 * 60 * 1000);
        } catch { return []; }
      })();

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const handleDelete = async (runId: string) => {
    setDeletingId(runId);
    try {
      await deleteRun(runId);
      setHistory(prev => prev ? { ...prev, items: prev.items.filter(r => r.run_id !== runId), total: prev.total - 1 } : prev);
      setConfirmId(null);
    } catch {} finally { setDeletingId(null); }
  };

  useEffect(() => {
    if (!recentlyCompleted) return;
    const t = setTimeout(() => clearCompleted(), 60_000);
    return () => clearTimeout(t);
  }, [recentlyCompleted, clearCompleted]);

  const [showFilters, setShowFilters] = useState(false);
  const [ticker, setTicker] = useState('');
  const [sector, setSector] = useState('');
  const [regime, setRegime] = useState('');
  const [action, setAction] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(1);

  const load = useCallback(async (p = 1) => {
    setLoading(true); setError(null);
    try {
      const data = await getHistory({ ticker: ticker || undefined, sector: sector || undefined, regime: regime || undefined, action: action || undefined, date_from: dateFrom || undefined, date_to: dateTo || undefined, page: p, page_size: 50 });
      setHistory(data); setPage(p);
      const tickers = [...new Set(data.items.map(r => r.ticker))].filter(t => !nameCache[t]);
      if (tickers.length > 0) {
        try {
          const names = await getCompanyNames(tickers);
          const map: Record<string, string> = {};
          for (const n of names) { map[n.ticker] = n.name; nameCache[n.ticker] = n.name; }
          setNameMap(prev => ({ ...prev, ...map }));
        } catch {}
      }
    } catch (err: unknown) { setError(err instanceof Error ? err.message : 'Failed to load'); } finally { setLoading(false); }
  }, [ticker, sector, regime, action, dateFrom, dateTo]);

  // Load on mount AND when any filter changes (load depends on all filter states via useCallback)
  useEffect(() => { load(1); }, [load]);
  const totalPages = history ? Math.ceil(history.total / 50) : 0;
  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen relative" style={{ backgroundImage: 'url(/bg-wallpaper.jpg)', backgroundSize: 'cover', backgroundPosition: 'center' }}>
      <div className="absolute inset-0 bg-black/40 dark:bg-background pointer-events-none" />
      <div className="relative z-10">
        <ResearchNav />
        <div className="max-w-5xl mx-auto px-3 pt-3 pb-6">

          {/* Header */}
          <div className="flex items-center justify-between mb-3">
            <div />
            <div className="flex items-center gap-2">
              <select value={sector} onChange={e => { setSector(e.target.value); /* auto-loads via useEffect on load */; }}
                className="h-7 px-2 text-[10px] rounded-lg bg-muted text-muted-foreground border border-border">
                <option value="">All Sectors</option>
                {['Tech','Semiconductor','Financials','Consumer','Biopharma','Energy','Industrials','ProfessionalServices'].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <input type="date" value={dateFrom} onChange={e => { setDateFrom(e.target.value); /* auto-loads via useEffect on load */; }}
                className="h-7 px-1.5 text-[10px] rounded-lg bg-muted text-muted-foreground border border-border" />
              <button onClick={() => setShowFilters(v => !v)} title="Filters"
                className={`w-7 h-7 rounded-lg flex items-center justify-center ${showFilters ? 'bg-white text-emerald-900' : 'bg-gray-100 text-muted-foreground hover:bg-muted'}`}>
                <Filter size={12} />
              </button>
            </div>
          </div>

          {showFilters && (
            <div className="mb-3 p-2 rounded-xl bg-card/90 backdrop-blur border border-border flex flex-wrap gap-2 items-center">
              <input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} placeholder="Ticker"
                className="h-7 w-20 px-2 text-[10px] rounded-lg bg-white/10 text-white border border-border placeholder:text-muted-foreground/60" />
              <select value={action} onChange={e => setAction(e.target.value)}
                className="h-7 px-2 text-[10px] rounded-lg bg-muted text-muted-foreground border border-border">
                <option value="">Action</option>
                {['BUY','SELL','SHORT','HOLD'].map(a => <option key={a} value={a}>{a}</option>)}
              </select>
              <button onClick={() => load(1)} className="h-7 px-3 text-[10px] font-bold rounded-lg bg-white text-emerald-900">Go</button>
              <button onClick={() => { setTicker(''); setSector(''); setRegime(''); setAction(''); setDateFrom(''); setDateTo(''); /* auto-loads via useEffect on load */; }}
                className="text-muted-foreground/60 hover:text-white"><X size={12} /></button>
            </div>
          )}

          {error && <div className="mb-2 p-2 rounded-lg bg-red-500/20 text-red-300 text-[10px]">{error}</div>}

          {/* Ongoing runs — one green bar per active analysis */}
          {effectiveActiveRuns.map(run => (
            <div key={run.ticker}
              className="mb-2 p-3 rounded-xl bg-emerald-500/15 border border-emerald-500/30 cursor-pointer hover:bg-emerald-500/25 transition-colors"
              onClick={() => navigate('/report')}>
              <div className="flex items-center gap-2">
                <svg className="h-3.5 w-3.5 animate-spin text-emerald-400" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span className="font-mono font-bold text-xs text-white">{run.ticker}</span>
                <span className="text-[9px] font-bold text-emerald-300 bg-emerald-400/20 px-1.5 py-0.5 rounded ring-1 ring-emerald-400/30">ONGOING</span>
                <span className="ml-auto text-[10px] text-emerald-400">View →</span>
              </div>
            </div>
          ))}

          {/* Cards grid */}
          {history && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
              {history.items.length === 0 && effectiveActiveRuns.length === 0 ? (
                <div className="col-span-full py-12 text-center text-muted-foreground/60 text-sm">No runs found.</div>
              ) : (
                history.items.map(row => {
                  const isNew = recentlyCompleted?.runId === row.run_id;
                  const evUp = row.ev_upside_pct;
                  const d = new Date(row.run_at);
                  const dateStr = `${d.getDate()}/${d.getMonth() + 1}`;
                  const name = nameMap[row.ticker] || nameCache[row.ticker];
                  return (
                    <div
                      key={row.run_id}
                      className={`group relative rounded-xl bg-card border border-border shadow-sm p-3 cursor-pointer hover:bg-muted/50 transition-all ${isNew ? 'ring-1 ring-emerald-500/50' : ''}`}
                      onClick={() => navigate(`/report/${row.run_id}`)}
                    >
                      {/* Single row: Ticker+Action | Price+Upside | VGPM | Date */}
                      <div className="flex items-center gap-2">
                        {/* Left: ticker + action badge */}
                        <div className="flex flex-col items-start min-w-[60px]">
                          <span className="font-mono font-bold text-sm text-foreground">{row.ticker}</span>
                          {row.final_action && (
                            <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold mt-0.5 ${ACTION_COLORS[row.final_action] ?? 'bg-gray-100 text-muted-foreground'}`}>
                              {row.final_action}
                            </span>
                          )}
                        </div>

                        {/* Price + upside */}
                        <div className="flex flex-col items-start min-w-[55px]">
                          <span className="text-sm font-bold text-foreground font-mono">
                            {row.price_target != null && row.price_target > 0 ? `$${row.price_target.toFixed(0)}` : '—'}
                          </span>
                          {evUp != null && (
                            <span className={`text-[10px] font-semibold ${evUp >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {evUp > 0 ? '+' : ''}{evUp.toFixed(1)}%
                            </span>
                          )}
                        </div>

                        {/* VGPM inline */}
                        <div className="flex gap-1.5 flex-1 justify-center">
                          <GradePill grade={row.vgpm_grades?.valuation} label="VAL" />
                          <GradePill grade={row.vgpm_grades?.growth} label="GRW" />
                          <GradePill grade={row.vgpm_grades?.profitability} label="PRF" />
                          <GradePill grade={row.vgpm_grades?.momentum} label="MOM" />
                        </div>

                        {/* Date + badges */}
                        <div className="flex flex-col items-end ml-auto">
                          <span className="text-[10px] text-muted-foreground/60 font-mono">{dateStr}</span>
                          {isNew && <span className="text-[8px] px-1 py-0.5 rounded-full bg-emerald-500 text-white font-bold mt-0.5">NEW</span>}
                        </div>
                      </div>

                      {/* Delete button */}
                      <div className="absolute top-1 right-1" onClick={e => e.stopPropagation()}>
                        {confirmId === row.run_id ? (
                          <span className="flex gap-1">
                            <button onClick={() => handleDelete(row.run_id)} disabled={deletingId === row.run_id}
                              className="text-[9px] text-red-400 font-bold">{deletingId === row.run_id ? '…' : '✓'}</button>
                            <button onClick={() => setConfirmId(null)} className="text-[9px] text-muted-foreground/60">✗</button>
                          </span>
                        ) : (
                          <button onClick={() => setConfirmId(row.run_id)}
                            className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-400 transition-all">
                            <X size={11} />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          )}

          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-3">
              <span className="text-[9px] text-muted-foreground/60">{history?.total} runs · {page}/{totalPages}</span>
              <div className="flex gap-1">
                <button disabled={page <= 1} onClick={() => load(page - 1)} className="px-2 py-1 text-[9px] rounded bg-gray-100 text-muted-foreground disabled:opacity-30">←</button>
                <button disabled={page >= totalPages} onClick={() => load(page + 1)} className="px-2 py-1 text-[9px] rounded bg-gray-100 text-muted-foreground disabled:opacity-30">→</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
