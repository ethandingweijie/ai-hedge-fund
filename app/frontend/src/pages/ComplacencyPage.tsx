/**
 * ComplacencyPage.tsx
 * ====================
 * Bill Ackman 4-pillar equity-complacency screener (Valuation /
 * Behavioral / Technical / Quality).
 *
 * - Ranked table sorted by composite score (0-8), highest first.
 * - Verdict badge: Strong-Short / Watch / Borderline / Pass.
 * - Re-rank by composite / each pillar / valuation / quality, etc.
 * - Filter chips for verdict; sector filter dropdown.
 * - Row click → detail drawer with all pillar inputs + flag notes.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getComplacencyCohort, refreshComplacency,
  type ComplacencyCohort, type ComplacencyTickerResult, type ComplacencyVerdict,
} from '@/lib/api';
import { ArrowLeft, RefreshCw, Loader2, AlertTriangle, X, Search } from 'lucide-react';
import { toast } from 'sonner';


// ─── Formatters ────────────────────────────────────────────────────────────

const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;

const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v == null || Number.isNaN(v) ? '—' : v.toFixed(digits);

const fmtPrice = (v: number | null | undefined): string =>
  v == null ? '—' : `$${v.toFixed(2)}`;

const formatRunTime = (iso: string | null): string => {
  if (!iso) return 'never run';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
  } catch { return iso; }
};


// ─── Verdict / pillar colour chips ────────────────────────────────────────

const VERDICT_COLOR: Record<ComplacencyVerdict, string> = {
  'Strong-Short': 'bg-red-600/30 text-red-200 border-red-700/40',
  'Watch':        'bg-orange-600/30 text-orange-200 border-orange-700/40',
  'Borderline':   'bg-amber-600/30 text-amber-200 border-amber-700/40',
  'Pass':         'bg-emerald-600/20 text-emerald-300 border-emerald-700/40',
  'N/A':          'bg-muted text-muted-foreground border-border',
};

function pillarColor(score: number): string {
  if (score >= 2) return 'bg-red-600/30 text-red-200';
  if (score >= 1) return 'bg-amber-600/30 text-amber-200';
  return 'bg-muted text-muted-foreground';
}


type RankKey = 'composite' | 'val' | 'beh' | 'tech' | 'qual' | 'ticker';

const RANK_DEFS: { id: RankKey; label: string; tooltip: string; asc: boolean }[] = [
  { id: 'composite', label: 'Overall',     tooltip: 'Composite 0-8 score', asc: false },
  { id: 'val',       label: 'Valuation',   tooltip: 'Valuation pillar (0-2)', asc: false },
  { id: 'beh',       label: 'Behavioural', tooltip: 'Behavioural pillar (0-2)', asc: false },
  { id: 'tech',      label: 'Technical',   tooltip: 'Technical pillar (0-2)', asc: false },
  { id: 'qual',      label: 'Quality',     tooltip: 'Quality pillar (0-2)', asc: false },
  { id: 'ticker',    label: 'Ticker',      tooltip: 'Alphabetical', asc: true },
];

const VERDICT_FILTERS: Array<ComplacencyVerdict | 'All'> = [
  'All', 'Strong-Short', 'Watch', 'Borderline', 'Pass',
];

function rankValue(row: ComplacencyTickerResult, key: RankKey): number | string {
  switch (key) {
    case 'composite': return row.composite ?? -1;
    case 'val':       return row.val_score ?? -1;
    case 'beh':       return row.beh_score ?? -1;
    case 'tech':      return row.tech_score ?? -1;
    case 'qual':      return row.qual_score ?? -1;
    case 'ticker':    return row.ticker;
  }
}


export function ComplacencyPage() {
  const navigate = useNavigate();

  const [cohort, setCohort] = useState<ComplacencyCohort | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [rankBy, setRankBy] = useState<RankKey>('composite');
  const [verdictFilter, setVerdictFilter] = useState<ComplacencyVerdict | 'All'>('All');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<ComplacencyTickerResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const c = await getComplacencyCohort();
      setCohort(c);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRefresh = async () => {
    setRefreshing(true);
    toast.info('Refreshing Complacency cohort — ~1-2 min on FMP.');
    try {
      await refreshComplacency({ maxWorkers: 4 });
      await load();
      toast.success('Complacency cohort refreshed.');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  };

  const rows = useMemo(() => {
    if (!cohort?.results) return [];
    const asc = RANK_DEFS.find((r) => r.id === rankBy)?.asc ?? false;
    const q = search.trim().toUpperCase();

    return cohort.results
      .filter((r) => verdictFilter === 'All' ? true : r.verdict === verdictFilter)
      .filter((r) => !q || r.ticker.includes(q) || r.name.toUpperCase().includes(q))
      .slice()
      .sort((a, b) => {
        const av = rankValue(a, rankBy);
        const bv = rankValue(b, rankBy);
        if (typeof av === 'string' || typeof bv === 'string') {
          return asc
            ? String(av).localeCompare(String(bv))
            : String(bv).localeCompare(String(av));
        }
        return asc ? (av as number) - (bv as number) : (bv as number) - (av as number);
      });
  }, [cohort, rankBy, verdictFilter, search]);

  const activeRank = RANK_DEFS.find((r) => r.id === rankBy)!;

  return (
    <div className="min-h-screen bg-background pt-16 pb-20">
      <div className="px-4 max-w-7xl mx-auto">
        {/* Title bar */}
        <div className="flex items-center gap-3 mb-2">
          <button
            onClick={() => navigate('/research-ideas')}
            className="p-1 rounded hover:bg-muted text-muted-foreground"
            aria-label="Back to ideas"
          >
            <ArrowLeft size={18} />
          </button>
          <h1 className="text-xl font-bold text-foreground">Complacency Detector</h1>
        </div>
        <p className="text-xs text-muted-foreground mb-4 ml-7">
          Ackman 4-pillar equity screen · Valuation / Behavioural / Technical / Quality · gate ≥ 6/8 & all pillars ≥ 1
        </p>

        {/* Cohort summary bar */}
        <div className="flex flex-wrap items-center gap-4 mb-3 p-3 rounded-md border border-border bg-card">
          <div className="flex flex-col">
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground">Gate Passers</span>
            <span className="text-lg font-bold font-mono text-foreground">
              {cohort?.gate_passers ?? 0} <span className="text-xs text-muted-foreground">/ {cohort?.ticker_count ?? 0}</span>
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground">Tickers</span>
            <span className="text-lg font-bold font-mono text-foreground">{cohort?.ticker_count ?? 0}</span>
          </div>
          {cohort?.failed_tickers && cohort.failed_tickers.length > 0 && (
            <div className="flex flex-col" title={cohort.failed_tickers.map((f) => `${f.ticker}: ${f.reason}`).join('\n')}>
              <span className="text-[9px] uppercase tracking-wider text-amber-500">Failed</span>
              <span className="text-lg font-bold font-mono text-amber-500">{cohort.failed_tickers.length}</span>
            </div>
          )}
          <div className="flex flex-col">
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground">Last run</span>
            <span className="text-xs text-foreground">{formatRunTime(cohort?.created_at ?? null)}</span>
          </div>
          <div className="ml-auto">
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >
              {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              {refreshing ? 'Running…' : 'Refresh'}
            </button>
          </div>
        </div>

        {/* Search + verdict filter */}
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <div className="relative flex-1 min-w-[180px] max-w-xs">
            <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by ticker or name"
              className="w-full pl-7 pr-2 py-1.5 text-xs rounded-md border border-border bg-card focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-wrap items-center gap-1 ml-auto">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground mr-1">Verdict</span>
            {VERDICT_FILTERS.map((v) => (
              <button
                key={v}
                onClick={() => setVerdictFilter(v)}
                className={`px-2 py-1 text-[10px] font-semibold rounded-md border transition-colors ${
                  verdictFilter === v
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-card text-muted-foreground border-border hover:bg-muted'
                }`}
              >
                {v}
              </button>
            ))}
          </div>
        </div>

        {/* Rank chips */}
        <div className="flex items-center gap-2 mb-3 overflow-x-auto pb-1">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground flex-shrink-0">Rank by</span>
          {RANK_DEFS.map((r) => (
            <button
              key={r.id}
              onClick={() => setRankBy(r.id)}
              title={r.tooltip}
              className={`px-3 py-1 text-xs font-semibold rounded-full border whitespace-nowrap transition-colors ${
                rankBy === r.id
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-card text-foreground border-border hover:bg-muted'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>

        {/* States */}
        {loading && (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading Complacency…
          </div>
        )}

        {!loading && (!cohort || cohort.results.length === 0) && !refreshing && (
          <div className="p-6 border border-dashed border-border rounded-md text-center">
            <AlertTriangle className="mx-auto mb-2 text-amber-500" size={20} />
            <p className="text-sm text-muted-foreground">No Complacency run yet. Click <strong>Refresh</strong> to run the screener.</p>
          </div>
        )}

        {!loading && cohort && rows.length === 0 && cohort.results.length > 0 && (
          <div className="p-4 text-center text-xs text-muted-foreground border border-dashed border-border rounded-md">
            No tickers match your filters.
          </div>
        )}

        {!loading && cohort && rows.length > 0 && (
          <div className="overflow-x-auto border border-border rounded-md bg-card">
            <table className="w-full text-xs font-mono">
              <thead className="bg-muted/40 text-left text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 w-10 text-right">#</th>
                  <th className="px-2 py-2">Ticker</th>
                  <th className="px-2 py-2">Sector</th>
                  <th className="px-2 py-2">Verdict</th>
                  <th className="px-2 py-2 text-right">Comp/8</th>
                  <th className="px-2 py-2 text-right" title="Valuation (EV/S, FCF yield)">V</th>
                  <th className="px-2 py-2 text-right" title="Behavioural (insider A/D, EPS rev, range)">B</th>
                  <th className="px-2 py-2 text-right" title="Technical (200DMA ext, weekly RSI)">T</th>
                  <th className="px-2 py-2 text-right" title="Quality (Altman Z, Piotroski)">Q</th>
                  <th className="px-2 py-2 text-right">EV/S</th>
                  <th className="px-2 py-2 text-right" title="FCF Yield TTM">FCF Y</th>
                  <th className="px-2 py-2 text-right" title="Weekly RSI(14)">RSI</th>
                  <th className="px-2 py-2 text-right" title="% above 200-day SMA">200x</th>
                  <th className="px-2 py-2 text-right" title="Altman Z-Score">Alt Z</th>
                  <th className="px-2 py-2 text-right" title="Piotroski (0-9)">Piotroski</th>
                  <th className="px-2 py-2 text-right">Price</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => setSelected(r)}
                    className="border-t border-border hover:bg-muted/30 cursor-pointer"
                  >
                    <td className="px-2 py-1.5 text-right text-muted-foreground">{r.rank ?? '—'}</td>
                    <td className="px-2 py-1.5">
                      <div className="font-bold text-foreground">{r.ticker}</div>
                      <div className="text-[10px] text-muted-foreground truncate max-w-[110px]">{r.name}</div>
                    </td>
                    <td className="px-2 py-1.5 text-[10px] text-muted-foreground truncate max-w-[120px]">{r.sector ?? '—'}</td>
                    <td className="px-2 py-1.5">
                      <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${VERDICT_COLOR[r.verdict]}`}>
                        {r.verdict}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <span className="font-bold text-foreground">{r.composite.toFixed(1)}</span>
                    </td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.val_score)}`}>{r.val_score}</span></td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.beh_score)}`}>{r.beh_score}</span></td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.tech_score)}`}>{r.tech_score}</span></td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.qual_score)}`}>{r.qual_score}</span></td>
                    <td className="px-2 py-1.5 text-right">{r.ev_sales == null ? '—' : `${r.ev_sales.toFixed(1)}×`}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPct(r.fcf_yield_ttm)}</td>
                    <td className="px-2 py-1.5 text-right">{r.rsi_weekly == null ? '—' : r.rsi_weekly.toFixed(0)}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPct(r.sma200_extension, 0)}</td>
                    <td className="px-2 py-1.5 text-right">{r.altman_z == null ? '—' : r.altman_z.toFixed(1)}</td>
                    <td className="px-2 py-1.5 text-right">{r.piotroski == null ? '—' : `${r.piotroski}/9`}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPrice(r.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-3 py-2 text-[10px] text-muted-foreground border-t border-border">
              Showing <strong className="text-foreground">{rows.length}</strong> of {cohort.ticker_count} ·
              ranked by <strong className="text-foreground">{activeRank.label}</strong> ({activeRank.asc ? 'asc' : 'desc'}) ·
              click any row for pillar inputs + flag notes
            </div>
          </div>
        )}
      </div>

      {/* Detail drawer */}
      {selected && (
        <ComplacencyDrawer ticker={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}


// ─── Detail drawer ─────────────────────────────────────────────────────────

function ComplacencyDrawer({ ticker, onClose }: { ticker: ComplacencyTickerResult; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[80] flex" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 animate-in fade-in duration-150" />
      <div
        className="ml-auto relative h-full w-full max-w-2xl bg-background border-l border-border shadow-2xl overflow-y-auto animate-in slide-in-from-right duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="sticky top-0 bg-background border-b border-border px-4 pb-3 z-10"
          style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 12px)' }}
        >
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-lg font-bold text-foreground">{ticker.ticker}</span>
                <span className="text-sm text-muted-foreground">{ticker.name}</span>
                {ticker.rank && (
                  <span className="text-[10px] text-muted-foreground ml-auto">#{ticker.rank}</span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${VERDICT_COLOR[ticker.verdict]}`}>
                  {ticker.verdict}
                </span>
                <span className="text-xs text-muted-foreground">Composite <strong className="text-foreground">{ticker.composite.toFixed(1)}</strong>/8</span>
                {ticker.sector && <span className="text-xs text-muted-foreground">{ticker.sector}</span>}
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 rounded-full hover:bg-muted ml-2 flex-shrink-0 -mt-1 -mr-1 bg-muted/30"
              aria-label="Close detail"
            >
              <X size={20} className="text-foreground" />
            </button>
          </div>
          {ticker.justification && (
            <p className="mt-2 text-xs text-foreground/90 leading-relaxed italic">
              {ticker.justification}
            </p>
          )}
        </div>

        <div className="p-4 space-y-6">
          {/* Pillar grid */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Pillar scores</h2>
            <div className="grid grid-cols-4 gap-2 text-[11px]">
              <Pillar label="Valuation"   score={ticker.val_score} />
              <Pillar label="Behavioural" score={ticker.beh_score} />
              <Pillar label="Technical"   score={ticker.tech_score} />
              <Pillar label="Quality"     score={ticker.qual_score} />
            </div>
          </section>

          {/* Pillar inputs */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Inputs</h2>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <KV label="EV/Sales (TTM)"          value={ticker.ev_sales == null ? '—' : `${ticker.ev_sales.toFixed(1)}×`} />
              <KV label="… vs sector median"      value={ticker.ev_sales_relative == null ? '—' : `${ticker.ev_sales_relative.toFixed(2)}×`} />
              <KV label="FCF Yield (TTM)"         value={fmtPct(ticker.fcf_yield_ttm, 2)} highlight />
              <KV label="Altman Z"                value={ticker.altman_z == null ? '—' : ticker.altman_z.toFixed(2)} />
              <KV label="Piotroski"               value={ticker.piotroski == null ? '—' : `${ticker.piotroski}/9`} />
              <KV label="Insider A/D (4Q avg)"    value={fmtNum(ticker.ad_ratio_4q_avg, 2)} />
              <KV label="EPS revision (Y/Y)"      value={fmtPct(ticker.eps_revision_yoy)} />
              <KV label="52-w range position"     value={fmtPct(ticker.range_position, 0)} />
              <KV label="% above 200DMA"          value={fmtPct(ticker.sma200_extension, 0)} />
              <KV label="Weekly RSI(14)"          value={ticker.rsi_weekly == null ? '—' : ticker.rsi_weekly.toFixed(0)} />
              <KV label="Price"                   value={fmtPrice(ticker.price)} />
              <KV label="Market cap"              value={ticker.market_cap == null ? '—' : `$${(ticker.market_cap / 1e9).toFixed(1)}B`} />
            </div>
          </section>

          {/* Flag notes */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Signals fired</h2>
            {ticker.flag_notes.length === 0 ? (
              <p className="text-xs text-muted-foreground italic">No individual flags surfaced.</p>
            ) : (
              <ul className="space-y-1.5">
                {ticker.flag_notes.map((note, i) => (
                  <li key={i} className="text-xs text-foreground flex gap-2 items-start">
                    <span className="text-amber-500 flex-shrink-0">▸</span>
                    <span>{note}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}


function KV({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <>
      <div className="text-muted-foreground">{label}</div>
      <div className={`text-right font-mono ${highlight ? 'text-foreground font-bold' : 'text-foreground'}`}>{value}</div>
    </>
  );
}

function Pillar({ label, score }: { label: string; score: number }) {
  return (
    <div className="p-2 rounded border border-border bg-muted/30">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`mt-1 inline-block px-2 py-0.5 rounded text-xs font-bold ${pillarColor(score)}`}>
        {score} / 2
      </div>
    </div>
  );
}
