/**
 * SW46Page.tsx
 * =============
 * Software-46 screener — Cassandra Unchained / Scion methodology.
 *
 * Layout follows the Screener tab's information density:
 *   - Pooled-ΔE header with refresh action
 *   - Search box (ticker / name)
 *   - Sort-chip row: Overall · Shareholder · Quality · Valuation · ΔE · ROIC · P/IV15
 *   - AICT filter chips: All · Fortress · Castle · Chapel · Stone · Wood
 *   - Compact ranked table; row click -> detail drawer
 *
 * Re-ranking is instantaneous on the client; the cohort run itself is on
 * the backend via POST /research/ideas/sw46/refresh.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getSW46Cohort, refreshSW46,
  type SW46Cohort, type SW46TickerResult, type AICTTier, type TATier,
} from '@/lib/api';
import { ArrowLeft, RefreshCw, Loader2, AlertTriangle, X, Search } from 'lucide-react';
import { toast } from 'sonner';


// ─── Formatters ────────────────────────────────────────────────────────────

const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;

const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v == null || Number.isNaN(v) ? '—' : v.toFixed(digits);

const fmtMoney = (v: number | null | undefined): string => {
  if (v == null) return '—';
  if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (Math.abs(v) >= 1e9)  return `$${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6)  return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toFixed(0)}`;
};

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


// ─── Tier colour chips ────────────────────────────────────────────────────

const AICT_COLOR: Record<AICTTier, string> = {
  Fortress: 'bg-emerald-600/20 text-emerald-300 border-emerald-700/40',
  Castle:   'bg-blue-600/20 text-blue-300 border-blue-700/40',
  Chapel:   'bg-amber-600/20 text-amber-300 border-amber-700/40',
  Stone:    'bg-orange-600/20 text-orange-300 border-orange-700/40',
  Wood:     'bg-red-600/20 text-red-300 border-red-700/40',
};

const TA_COLOR: Record<TATier, string> = {
  'Not-TT':  'bg-emerald-600/20 text-emerald-300 border-emerald-700/40',
  'Near-TT': 'bg-amber-600/20 text-amber-300 border-amber-700/40',
  'TT*':     'bg-red-600/20 text-red-300 border-red-700/40',
  'N/A':     'bg-muted text-muted-foreground border-border',
};

function verdictLabel(score: number): string {
  if (score >= 60) return 'Fat Pitch';
  if (score >= 45) return 'Watch';
  if (score >= 25) return 'Not Close';
  if (score >= 10) return 'Avoid';
  return 'Stay Away';
}

function verdictColor(score: number): string {
  if (score >= 60) return 'bg-emerald-600/30 text-emerald-200';
  if (score >= 45) return 'bg-blue-600/30 text-blue-200';
  if (score >= 25) return 'bg-amber-600/30 text-amber-200';
  if (score >= 10) return 'bg-orange-600/30 text-orange-200';
  return 'bg-red-600/30 text-red-200';
}


// ─── Sort dimensions (each one re-ranks the whole table) ───────────────────

type RankKey =
  | 'composite'   // total 100-pt score
  | 'shareholder' // 30-pt bucket
  | 'quality'     // 35-pt bucket
  | 'valuation'   // 35-pt bucket
  | 'delta_e'     // pooled ΔE
  | 'roic'        // fully-adjusted ROIC
  | 'p_iv15'      // price / IV15-per-share (ascending = cheap on top)
  | 'ivb';        // implied LT return at current price (desc = best return on top)

const RANK_DEFS: { id: RankKey; label: string; tooltip: string; asc: boolean }[] = [
  { id: 'composite',   label: 'Overall',     tooltip: '100-pt composite score',                            asc: false },
  { id: 'ivb',         label: 'IVB',         tooltip: 'Implied LT return at current price (higher = better)', asc: false },
  { id: 'shareholder', label: 'Shareholder', tooltip: 'Shareholder bucket (30) — TA tier · ΔE · SBC trend',asc: false },
  { id: 'quality',     label: 'Quality',     tooltip: 'Quality bucket (35) — AICT · ROIC · growth',       asc: false },
  { id: 'valuation',   label: 'Valuation',   tooltip: 'Valuation bucket (35) — P/IV15 brackets',          asc: false },
  { id: 'delta_e',     label: 'ΔE',          tooltip: 'Pooled ΔE = ΣOE / ΣN',                              asc: false },
  { id: 'roic',        label: 'ROIC',        tooltip: 'Fully-adjusted ROIC',                              asc: false },
  { id: 'p_iv15',      label: 'P/IV15',      tooltip: 'Price / IV15-per-share (cheapest first)',          asc: true  },
];

const AICT_FILTERS: Array<AICTTier | 'All'> = ['All', 'Fortress', 'Castle', 'Chapel', 'Stone', 'Wood'];

function rankValue(row: SW46TickerResult, key: RankKey): number {
  switch (key) {
    case 'composite':   return row.composite.total          ?? -999;
    case 'shareholder': return row.composite.shareholder_bucket ?? -999;
    case 'quality':     return row.composite.quality_bucket  ?? -999;
    case 'valuation':   return row.composite.valuation_bucket ?? -999;
    case 'delta_e':     return row.tragic_algebra.pooled_delta_e ?? -999;
    case 'roic':        return row.roic.roic                 ?? -999;
    case 'p_iv15':      return row.composite.p_iv15          ?? 9999;
    case 'ivb':         return row.ivb_pct                   ?? -999;
  }
}


// ─── Main page ─────────────────────────────────────────────────────────────

export function SW46Page() {
  const navigate = useNavigate();

  const [cohort, setCohort]   = useState<SW46Cohort | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [rankBy, setRankBy]   = useState<RankKey>('composite');
  const [aictFilter, setAictFilter] = useState<AICTTier | 'All'>('All');
  const [search, setSearch]   = useState('');
  const [selected, setSelected] = useState<SW46TickerResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const c = await getSW46Cohort();
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
    toast.info('Refreshing SW46 cohort — this takes ~2-3 minutes.');
    try {
      await refreshSW46({ historyYears: 7 });
      await load();
      toast.success('SW46 cohort refreshed.');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  };

  // ─── Filter + re-rank ────────────────────────────────────────────────────
  const rows = useMemo(() => {
    if (!cohort?.results) return [];
    const asc = RANK_DEFS.find((r) => r.id === rankBy)?.asc ?? false;
    const q = search.trim().toUpperCase();

    return cohort.results
      .filter((r) => aictFilter === 'All' ? true : r.aict.tier === aictFilter)
      .filter((r) => !q || r.ticker.includes(q) || r.name.toUpperCase().includes(q))
      .slice()
      .sort((a, b) => {
        const av = rankValue(a, rankBy);
        const bv = rankValue(b, rankBy);
        return asc ? av - bv : bv - av;
      });
  }, [cohort, rankBy, aictFilter, search]);

  const activeRank = RANK_DEFS.find((r) => r.id === rankBy)!;

  return (
    <div className="min-h-screen bg-background pt-16 pb-20">
      <div className="px-4 max-w-7xl mx-auto">
        {/* ── Title bar ───────────────────────────────────────────────────── */}
        <div className="flex items-center gap-3 mb-2">
          <button
            onClick={() => navigate('/research-ideas')}
            className="p-1 rounded hover:bg-muted text-muted-foreground"
            aria-label="Back to ideas"
          >
            <ArrowLeft size={18} />
          </button>
          <h1 className="text-xl font-bold text-foreground">SW46 — Software Screener</h1>
        </div>
        <p className="text-xs text-muted-foreground mb-4 ml-7">
          Cassandra Unchained / Scion · Tragic Algebra · AICT · IV15 · re-rank by any dimension
        </p>

        {/* ── Cohort summary bar ──────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-4 mb-3 p-3 rounded-md border border-border bg-card">
          <Stat label="Pooled ΔE" value={cohort?.cohort_pooled_delta_e != null ? `${(cohort.cohort_pooled_delta_e * 100).toFixed(1)}%` : '—'} />
          <Stat label="Tickers" value={String(cohort?.ticker_count ?? 0)} />
          {cohort?.failed_tickers && cohort.failed_tickers.length > 0 && (
            <Stat
              label="Failed"
              value={String(cohort.failed_tickers.length)}
              tone="warn"
              title={cohort.failed_tickers.map((f) => `${f.ticker}: ${f.reason}`).join('\n')}
            />
          )}
          <Stat label="Last run" value={formatRunTime(cohort?.created_at ?? null)} mono={false} />
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

        {/* ── Search + AICT filter row ────────────────────────────────────── */}
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
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground mr-1">AICT</span>
            {AICT_FILTERS.map((t) => (
              <button
                key={t}
                onClick={() => setAictFilter(t)}
                className={`px-2 py-1 text-[10px] font-semibold rounded-md border transition-colors ${
                  aictFilter === t
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-card text-muted-foreground border-border hover:bg-muted'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* ── Rank-by chips ───────────────────────────────────────────────── */}
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

        {/* ── States: loading / empty / table ──────────────────────────── */}
        {loading && (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading SW46…
          </div>
        )}

        {!loading && (!cohort || cohort.results.length === 0) && !refreshing && (
          <div className="p-6 border border-dashed border-border rounded-md text-center">
            <AlertTriangle className="mx-auto mb-2 text-amber-500" size={20} />
            <p className="text-sm text-muted-foreground">No SW46 cohort run yet. Click <strong>Refresh</strong> to run the first one.</p>
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
                  <th className="px-2 py-2 w-10 text-right">SW46 #</th>
                  <th className="px-2 py-2">Ticker</th>
                  <th className="px-2 py-2">AICT</th>
                  <th className="px-2 py-2">TA</th>
                  <th className="px-2 py-2 text-right text-foreground">Score</th>
                  <th className="px-2 py-2">Verdict</th>
                  <th className="px-2 py-2 text-right">Price</th>
                  <th className="px-2 py-2 text-right" title="Implied LT return at current price (higher is better)">IVB</th>
                  <th className="px-2 py-2 text-right">IV15</th>
                  <th className="px-2 py-2 text-right">P/IV15</th>
                  <th className="px-2 py-2 text-right">IV12</th>
                  <th className="px-2 py-2 text-right">P/IV12</th>
                  <th className="px-2 py-2 text-right">IV18</th>
                  <th className="px-2 py-2 text-right">P/IV18</th>
                  <th className="px-2 py-2 text-right">ΔE</th>
                  <th className="px-2 py-2 text-right">ROIC</th>
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
                    <td className="px-2 py-1.5">
                      <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${AICT_COLOR[r.aict.tier]}`}>
                        {r.aict.tier}
                      </span>
                    </td>
                    <td className="px-2 py-1.5">
                      <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${TA_COLOR[r.tragic_algebra.ta_tier]}`}>
                        {r.tragic_algebra.ta_tier}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <span className="font-bold text-foreground" title={r.justification ?? `Shareholder ${r.composite.shareholder_bucket.toFixed(1)} / Quality ${r.composite.quality_bucket.toFixed(1)} / Valuation ${r.composite.valuation_bucket.toFixed(1)}`}>{r.composite.total.toFixed(1)}</span>
                    </td>
                    <td className="px-2 py-1.5" title={r.justification ?? ''}>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${verdictColor(r.composite.total)}`}>
                        {verdictLabel(r.composite.total)}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right">{fmtPrice(r.price)}</td>
                    <td className="px-2 py-1.5 text-right font-semibold">{r.ivb_pct == null ? '—' : `${(r.ivb_pct * 100).toFixed(1)}%`}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPrice(r.iv15.iv15_per_share)}</td>
                    <td className="px-2 py-1.5 text-right">
                      {r.composite.p_iv15 == null ? '—' : `${r.composite.p_iv15.toFixed(2)}×`}
                    </td>
                    <td className="px-2 py-1.5 text-right">{fmtPrice(r.iv12?.iv15_per_share ?? null)}</td>
                    <td className="px-2 py-1.5 text-right">{r.p_iv12 == null ? '—' : `${r.p_iv12.toFixed(2)}×`}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPrice(r.iv18?.iv15_per_share ?? null)}</td>
                    <td className="px-2 py-1.5 text-right">{r.p_iv18 == null ? '—' : `${r.p_iv18.toFixed(2)}×`}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPct(r.tragic_algebra.pooled_delta_e)}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPct(r.roic.roic)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-3 py-2 text-[10px] text-muted-foreground border-t border-border">
              Showing <strong className="text-foreground">{rows.length}</strong> of {cohort.ticker_count} ·
              ranked by <strong className="text-foreground">{activeRank.label}</strong> ({activeRank.asc ? 'asc' : 'desc'}) ·
              click any row for the full Tragic Algebra / ROIC / IV15 breakdown
            </div>
          </div>
        )}
      </div>

      {/* ── Detail drawer ───────────────────────────────────────────────── */}
      {selected && (
        <DetailDrawer ticker={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}


// ─── Small summary chip ────────────────────────────────────────────────────

function Stat({ label, value, tone, mono = true, title }: { label: string; value: string; tone?: 'warn'; mono?: boolean; title?: string }) {
  const valueColor = tone === 'warn' ? 'text-amber-500' : 'text-foreground';
  const labelColor = tone === 'warn' ? 'text-amber-500/80' : 'text-muted-foreground';
  return (
    <div className="flex flex-col" title={title}>
      <span className={`text-[9px] uppercase tracking-wider ${labelColor}`}>{label}</span>
      <span className={`${mono ? 'font-mono' : ''} text-sm font-bold ${valueColor}`}>{value}</span>
    </div>
  );
}


// ─── Detail drawer (unchanged from v1; deep dive into the math) ────────────

function DetailDrawer({ ticker, onClose }: { ticker: SW46TickerResult; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[80] flex" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 animate-in fade-in duration-150" />
      <div
        className="ml-auto relative h-full w-full max-w-2xl bg-background border-l border-border shadow-2xl overflow-y-auto animate-in slide-in-from-right duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-background border-b border-border px-4 py-3 z-10">
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <div className="flex items-baseline gap-2">
                <span className="text-lg font-bold text-foreground">{ticker.ticker}</span>
                <span className="text-sm text-muted-foreground">{ticker.name}</span>
                {ticker.rank && (
                  <span className="text-[10px] text-muted-foreground ml-auto">SW46 #{ticker.rank}</span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${AICT_COLOR[ticker.aict.tier]}`}>AICT · {ticker.aict.tier}</span>
                <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${TA_COLOR[ticker.tragic_algebra.ta_tier]}`}>TA · {ticker.tragic_algebra.ta_tier}</span>
                <span className="text-xs text-muted-foreground">Composite <strong className="text-foreground">{ticker.composite.total.toFixed(1)}</strong> / 100</span>
                {ticker.ivb_pct != null && (
                  <span className="text-xs text-muted-foreground">IVB <strong className="text-foreground">{(ticker.ivb_pct * 100).toFixed(1)}%</strong></span>
                )}
              </div>
            </div>
            <button onClick={onClose} className="p-1 rounded hover:bg-muted ml-2">
              <X size={18} className="text-muted-foreground" />
            </button>
          </div>
          {ticker.justification && (
            <p className="mt-2 text-xs text-foreground/90 leading-relaxed italic">
              {ticker.justification}
            </p>
          )}
        </div>

        <div className="p-4 space-y-6">

          {/* Composite bucket breakdown */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Composite breakdown</h2>
            <div className="flex h-3 rounded overflow-hidden border border-border">
              <div className="bg-rose-500/70" style={{ width: `${ticker.composite.shareholder_bucket}%` }} title={`Shareholder ${ticker.composite.shareholder_bucket.toFixed(1)} / 30`} />
              <div className="bg-cyan-500/70" style={{ width: `${ticker.composite.quality_bucket}%` }} title={`Quality ${ticker.composite.quality_bucket.toFixed(1)} / 35`} />
              <div className="bg-emerald-500/70" style={{ width: `${Math.max(ticker.composite.valuation_bucket, 0)}%` }} title={`Valuation ${ticker.composite.valuation_bucket.toFixed(1)} / 35`} />
            </div>
            <div className="grid grid-cols-3 gap-2 mt-2 text-[11px]">
              <Bucket label="Shareholder" value={ticker.composite.shareholder_bucket} cap={30} subs={[
                { label: 'TA tier', value: ticker.composite.pts_ta_tier },
                { label: 'ΔE', value: ticker.composite.pts_delta_e },
                { label: 'SBC trend', value: ticker.composite.pts_sbc_trend },
              ]} />
              <Bucket label="Quality" value={ticker.composite.quality_bucket} cap={35} subs={[
                { label: 'AICT tier', value: ticker.composite.pts_aict_tier },
                { label: 'ROIC', value: ticker.composite.pts_roic },
                { label: 'Growth', value: ticker.composite.pts_growth },
              ]} />
              <Bucket label="Valuation" value={ticker.composite.valuation_bucket} cap={35} subs={[
                { label: 'P/IV15', value: ticker.composite.pts_p_iv15 },
              ]} />
            </div>
          </section>

          {/* IV15 breakdown */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">IV15 (15-yr, 15% required return)</h2>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <KV label="IV15 / share" value={fmtPrice(ticker.iv15.iv15_per_share)} highlight />
              <KV label="Current price" value={fmtPrice(ticker.price)} />
              <KV label="DDM total" value={fmtMoney(ticker.iv15.iv15_ddm_total)} />
              <KV label="Buffett-multiple total" value={fmtMoney(ticker.iv15.iv15_buffett_total)} />
              <KV label="Base OE" value={fmtMoney(ticker.iv15.base_oe)} />
              <KV label="Terminal multiple" value={ticker.iv15.terminal_multiple_used == null ? '—' : `${ticker.iv15.terminal_multiple_used.toFixed(1)}×`} />
              <KV label="Growth Y1-5" value={fmtPct(ticker.iv15.growth_year1_5)} />
              <KV label="Growth Y6-10" value={fmtPct(ticker.iv15.growth_year6_10)} />
              <KV label="Growth Y11-15" value={fmtPct(ticker.iv15.growth_year11_15)} />
              <KV label="Shares out" value={ticker.iv15.shares_outstanding == null ? '—' : `${(ticker.iv15.shares_outstanding / 1e6).toFixed(1)}M`} />
            </div>
          </section>

          {/* ROIC breakdown */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Fully-adjusted ROIC</h2>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <KV label="ROIC" value={fmtPct(ticker.roic.roic)} highlight />
              <KV label="" value="" />
              <KV label="Owner earnings" value={fmtMoney(ticker.roic.owner_earnings)} />
              <KV label="− Interest income" value={fmtMoney(ticker.roic.interest_income)} />
              <KV label="− Capital lease pmts" value={fmtMoney(ticker.roic.capital_lease_payments)} />
              <KV label="= Numerator" value={fmtMoney(ticker.roic.numerator)} />
              <KV label="Total capital" value={fmtMoney(ticker.roic.total_capital)} />
              <KV label="− LT op leases" value={fmtMoney(ticker.roic.lt_operating_leases)} />
              <KV label="− Net cash" value={fmtMoney(ticker.roic.net_cash)} />
              <KV label="+ Purchase oblig." value={fmtMoney(ticker.roic.purchase_obligations)} />
              <KV label="= Denominator" value={fmtMoney(ticker.roic.denominator)} />
            </div>
          </section>

          {/* Tragic Algebra year-by-year */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Tragic Algebra · year-by-year</h2>
            {ticker.tragic_algebra.estimated_c_years > 0 && (
              <p className="text-[10px] text-amber-500 mb-2">
                <AlertTriangle size={10} className="inline mb-0.5" /> C estimated for {ticker.tragic_algebra.estimated_c_years} year(s) — FMP /stable/ does not break out RSU tax withholding.
              </p>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-[10px] font-mono">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="px-1 py-1 text-left">FY</th>
                    <th className="px-1 py-1 text-right">N</th>
                    <th className="px-1 py-1 text-right">G</th>
                    <th className="px-1 py-1 text-right">C</th>
                    <th className="px-1 py-1 text-right">B</th>
                    <th className="px-1 py-1 text-right">ΔS</th>
                    <th className="px-1 py-1 text-right">P</th>
                    <th className="px-1 py-1 text-right">Ω</th>
                    <th className="px-1 py-1 text-right">OE</th>
                    <th className="px-1 py-1 text-right">ΔE</th>
                  </tr>
                </thead>
                <tbody>
                  {ticker.tragic_algebra.years.map((y) => (
                    <tr key={y.fiscal_year} className="border-t border-border">
                      <td className="px-1 py-1 text-left text-foreground">{y.fiscal_year}</td>
                      <td className="px-1 py-1 text-right">{fmtMoney(y.net_income)}</td>
                      <td className="px-1 py-1 text-right">{fmtMoney(y.sbc_expense)}</td>
                      <td className="px-1 py-1 text-right">
                        {fmtMoney(y.cash_tax_withholding)}
                        {y.cash_tax_withholding_estimated && <span className="text-amber-500 ml-0.5">*</span>}
                      </td>
                      <td className="px-1 py-1 text-right">{fmtMoney(y.buybacks)}</td>
                      <td className="px-1 py-1 text-right">{y.share_change == null ? '—' : `${(y.share_change / 1e6).toFixed(1)}M`}</td>
                      <td className="px-1 py-1 text-right">{fmtPrice(y.avg_share_price)}</td>
                      <td className="px-1 py-1 text-right">{fmtMoney(y.omega)}</td>
                      <td className="px-1 py-1 text-right">{fmtMoney(y.owner_earnings)}</td>
                      <td className="px-1 py-1 text-right">{fmtPct(y.delta_e)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-[10px] text-muted-foreground mt-2">
                Pooled ΔE = <strong className="text-foreground">{fmtPct(ticker.tragic_algebra.pooled_delta_e)}</strong>;
                SBC trend slope = {fmtNum(ticker.tragic_algebra.sbc_trend ? ticker.tragic_algebra.sbc_trend * 100 : null, 3)}% per yr
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}


// ─── Small leaf components ─────────────────────────────────────────────────

function KV({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <>
      <div className="text-muted-foreground">{label}</div>
      <div className={`text-right font-mono ${highlight ? 'text-foreground font-bold' : 'text-foreground'}`}>{value}</div>
    </>
  );
}

function Bucket(
  { label, value, cap, subs }: { label: string; value: number; cap: number; subs: { label: string; value: number }[] },
) {
  return (
    <div className="p-2 rounded border border-border bg-muted/30">
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
        <span className="text-xs font-mono font-bold text-foreground">{value.toFixed(1)}/{cap}</span>
      </div>
      <div className="mt-1 space-y-0.5">
        {subs.map((s) => (
          <div key={s.label} className="flex items-center justify-between text-[10px]">
            <span className="text-muted-foreground">{s.label}</span>
            <span className="font-mono text-foreground">{s.value.toFixed(1)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
