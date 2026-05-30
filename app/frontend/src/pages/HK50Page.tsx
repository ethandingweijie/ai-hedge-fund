/**
 * HK50Page.tsx
 * =============
 * "Long China / HK" two-screener cohort — High Growth ⇄ High Dividend.
 *
 * One 50-name China/HK universe carries BOTH screen scores per row. The page
 * is a single toggle between the Growth ranking and the Dividend ranking; the
 * hero strip shows the top-5 of each screen at a glance.
 *
 * Layout:
 *   - Cohort summary bar (avg Growth, avg Dividend, median P/IV15, lead split)
 *   - Growth ⇄ Dividend screen toggle
 *   - Hero strip: top-5 Growth + top-5 Dividend
 *   - Compact ranked table: S/N · Stock · Score · IV15 · Price · AICT · Key Metrics
 *   - Row click → detail drawer (both screens' 5 metrics + IV15 build-up)
 *
 * Re-ranking is instantaneous on the client; the cohort run is on the backend
 * via POST /research/ideas/hk50/refresh.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getHK50Cohort, refreshHK50,
  type HK50Cohort, type HK50TickerResult,
} from '@/lib/api';
import { ArrowLeft, RefreshCw, Loader2, AlertTriangle, X, Search, TrendingUp, Coins } from 'lucide-react';
import { toast } from 'sonner';


// ─── Screen definitions ─────────────────────────────────────────────────────

type Screen = 'Growth' | 'Dividend';

const GROWTH_METRICS = ['revenue_3y_cagr', 'eps_fwd_growth', 'fcf_growth', 'op_income_growth', 'peg'];
const DIV_METRICS    = ['dividend_yield', 'dividend_5y_cagr', 'payout_ratio', 'fcf_coverage', 'yield_vs_5yr_avg'];

const METRIC_META: Record<string, { label: string; short: string; kind: 'pct' | 'x' | 'num' }> = {
  revenue_3y_cagr:  { label: 'Revenue 3Y CAGR',   short: 'Rev3y',  kind: 'pct' },
  eps_fwd_growth:   { label: 'EPS fwd growth',    short: 'EPSfwd', kind: 'pct' },
  fcf_growth:       { label: 'FCF growth',        short: 'FCFg',   kind: 'pct' },
  op_income_growth: { label: 'Op income growth',  short: 'OpInc',  kind: 'pct' },
  peg:              { label: 'PEG ratio',         short: 'PEG',    kind: 'x'   },
  dividend_yield:   { label: 'Dividend yield',    short: 'Yield',  kind: 'pct' },
  dividend_5y_cagr: { label: 'Dividend 5Y CAGR',  short: 'Div5y',  kind: 'pct' },
  payout_ratio:     { label: 'Payout ratio',      short: 'Payout', kind: 'pct' },
  fcf_coverage:     { label: 'FCF cover',         short: 'FCFcov', kind: 'x'   },
  yield_vs_5yr_avg: { label: 'Yield vs 5y avg',   short: 'Yvs5y',  kind: 'x'   },
};


// ─── Formatters ──────────────────────────────────────────────────────────────

const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;

const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v == null || Number.isNaN(v) ? '—' : v.toFixed(digits);

const fmtMetric = (id: string, v: number | null | undefined): string => {
  if (v == null || Number.isNaN(v)) return '—';
  const kind = METRIC_META[id]?.kind ?? 'num';
  if (kind === 'pct') return `${(v * 100).toFixed(1)}%`;
  if (kind === 'x') return `${v.toFixed(2)}×`;
  return v.toFixed(2);
};

const fmtPrice = (v: number | null | undefined, ccy = ''): string =>
  v == null ? '—' : `${ccy ? ccy + ' ' : ''}${v.toFixed(2)}`;

const formatRunTime = (iso: string | null): string => {
  if (!iso) return 'never run';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
  } catch { return iso; }
};


// ─── AICT colour (software / internet names only; "—" otherwise) ────────────

const AICT_COLOR: Record<string, string> = {
  Fortress: 'bg-emerald-600/20 text-emerald-300 border-emerald-700/40',
  Castle:   'bg-blue-600/20 text-blue-300 border-blue-700/40',
  Chapel:   'bg-amber-600/20 text-amber-300 border-amber-700/40',
  Stone:    'bg-orange-600/20 text-orange-300 border-orange-700/40',
  Wood:     'bg-red-600/20 text-red-300 border-red-700/40',
};
const aictClass = (tier: string): string =>
  AICT_COLOR[tier] ?? 'bg-muted text-muted-foreground border-border';

// Provenance dot for a resolved metric source.
const SOURCE_DOT: Record<string, string> = {
  primary:    'bg-emerald-400',
  fmp_growth: 'bg-cyan-400',
  yfinance:   'bg-blue-400',
  compute:    'bg-amber-400',
  structural: 'bg-purple-400',
  missing:    'bg-muted-foreground/40',
};


function scoreColor(score: number): string {
  if (score >= 75) return 'bg-emerald-600/30 text-emerald-200';
  if (score >= 55) return 'bg-blue-600/30 text-blue-200';
  if (score >= 35) return 'bg-amber-600/30 text-amber-200';
  if (score >= 15) return 'bg-orange-600/30 text-orange-200';
  return 'bg-red-600/30 text-red-200';
}


// ─── Per-screen accessors ────────────────────────────────────────────────────

const screenScore = (r: HK50TickerResult, s: Screen): number =>
  s === 'Growth' ? r.growth_score : r.dividend_score;
const screenMetrics = (s: Screen): string[] =>
  s === 'Growth' ? GROWTH_METRICS : DIV_METRICS;


// ─── Main page ─────────────────────────────────────────────────────────────

export function HK50Page() {
  const navigate = useNavigate();

  const [cohort, setCohort]   = useState<HK50Cohort | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [screen, setScreen]   = useState<Screen>('Growth');
  const [search, setSearch]   = useState('');
  const [selected, setSelected] = useState<HK50TickerResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCohort(await getHK50Cohort());
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRefresh = async () => {
    setRefreshing(true);
    toast.info('Refreshing the China / HK cohort — this takes ~2-3 minutes.');
    try {
      await refreshHK50({ maxWorkers: 6 });
      await load();
      toast.success('Cohort refreshed.');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  };

  // Top-5 of each screen for the hero strip.
  const top5Growth = useMemo(
    () => [...(cohort?.results ?? [])].sort((a, b) => b.growth_score - a.growth_score).slice(0, 5),
    [cohort],
  );
  const top5Dividend = useMemo(
    () => [...(cohort?.results ?? [])].sort((a, b) => b.dividend_score - a.dividend_score).slice(0, 5),
    [cohort],
  );

  // Active-screen ranked + filtered rows.
  const rows = useMemo(() => {
    if (!cohort?.results) return [];
    const q = search.trim().toUpperCase();
    return cohort.results
      .filter((r) =>
        !q ||
        r.ticker.toUpperCase().includes(q) ||
        r.hk_ticker.toUpperCase().includes(q) ||
        r.name.toUpperCase().includes(q),
      )
      .slice()
      .sort((a, b) => screenScore(b, screen) - screenScore(a, screen));
  }, [cohort, screen, search]);

  const metricKeys = screenMetrics(screen);

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
          <h1 className="text-xl font-bold text-foreground">Long China / HK — Growth &amp; Dividend</h1>
        </div>
        <p className="text-xs text-muted-foreground mb-4 ml-7">
          50-name China / Hong Kong universe · two independent 0-100 screens · IV15 (AICT-modulated for software / internet) · toggle the ranking
        </p>

        {/* ── Cohort summary bar ──────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-4 mb-3 p-3 rounded-md border border-border bg-card">
          <Stat label="Avg Growth" value={cohort?.avg_growth != null ? cohort.avg_growth.toFixed(1) : '—'} />
          <Stat label="Avg Dividend" value={cohort?.avg_dividend != null ? cohort.avg_dividend.toFixed(1) : '—'} />
          <Stat label="Median P/IV15" value={cohort?.median_p_iv15 != null ? `${cohort.median_p_iv15.toFixed(2)}×` : '—'} />
          <Stat label="Lead split" value={cohort?.ticker_count ? `${cohort.lead_growth_count}G / ${cohort.ticker_count - cohort.lead_growth_count}D` : '—'} />
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

        {/* ── Hero strip: top-5 of each screen ────────────────────────────── */}
        {!loading && cohort && cohort.results.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
            <TopFive
              title="Top 5 · Growth"
              icon={<TrendingUp size={14} className="text-emerald-400" />}
              rows={top5Growth}
              screen="Growth"
              onPick={setSelected}
              onFocus={() => setScreen('Growth')}
            />
            <TopFive
              title="Top 5 · Dividend"
              icon={<Coins size={14} className="text-amber-400" />}
              rows={top5Dividend}
              screen="Dividend"
              onPick={setSelected}
              onFocus={() => setScreen('Dividend')}
            />
          </div>
        )}

        {/* ── Screen toggle + search ──────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <div className="inline-flex rounded-md border border-border overflow-hidden">
            {(['Growth', 'Dividend'] as Screen[]).map((s) => (
              <button
                key={s}
                onClick={() => setScreen(s)}
                className={`inline-flex items-center gap-1.5 px-4 py-1.5 text-sm font-semibold transition-colors ${
                  screen === s
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-card text-muted-foreground hover:bg-muted'
                }`}
              >
                {s === 'Growth' ? <TrendingUp size={14} /> : <Coins size={14} />}
                {s} Screener
              </button>
            ))}
          </div>
          <div className="relative flex-1 min-w-[180px] max-w-xs ml-auto">
            <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by ticker or name"
              className="w-full pl-7 pr-2 py-1.5 text-xs rounded-md border border-border bg-card focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        {/* ── States: loading / empty / table ──────────────────────────── */}
        {loading && (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading China / HK cohort…
          </div>
        )}

        {!loading && (!cohort || cohort.results.length === 0) && !refreshing && (
          <div className="p-6 border border-dashed border-border rounded-md text-center">
            <AlertTriangle className="mx-auto mb-2 text-amber-500" size={20} />
            <p className="text-sm text-muted-foreground">No cohort run yet. Click <strong>Refresh</strong> to run the first one.</p>
          </div>
        )}

        {!loading && cohort && rows.length === 0 && cohort.results.length > 0 && (
          <div className="p-4 text-center text-xs text-muted-foreground border border-dashed border-border rounded-md">
            No tickers match your filter.
          </div>
        )}

        {!loading && cohort && rows.length > 0 && (
          <div className="overflow-x-auto border border-border rounded-md bg-card">
            <table className="w-full text-xs font-mono">
              <thead className="bg-muted/40 text-left text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 w-10 text-right">S/N</th>
                  <th className="px-2 py-2">Stock</th>
                  <th className="px-2 py-2 text-right text-foreground">{screen} Score</th>
                  <th className="px-2 py-2 text-right">Other</th>
                  <th className="px-2 py-2 text-right">IV15</th>
                  <th className="px-2 py-2 text-right">Price</th>
                  <th className="px-2 py-2 text-right">P/IV15</th>
                  <th className="px-2 py-2">AICT</th>
                  <th className="px-2 py-2">Key {screen} Metrics</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const other: Screen = screen === 'Growth' ? 'Dividend' : 'Growth';
                  return (
                    <tr
                      key={r.ticker}
                      onClick={() => setSelected(r)}
                      className="border-t border-border hover:bg-muted/30 cursor-pointer"
                    >
                      <td className="px-2 py-1.5 text-right text-muted-foreground">{i + 1}</td>
                      <td className="px-2 py-1.5">
                        <div className="font-bold text-foreground flex items-center gap-1">
                          {r.name}
                          {r.lead === screen && (
                            <span className="text-[8px] px-1 rounded bg-primary/20 text-primary uppercase tracking-wide">lead</span>
                          )}
                        </div>
                        <div className="text-[10px] text-muted-foreground truncate max-w-[140px]">
                          {r.ticker}{r.ticker !== r.hk_ticker ? ` · ${r.hk_ticker}` : ''}
                        </div>
                      </td>
                      <td className="px-2 py-1.5 text-right">
                        <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold ${scoreColor(screenScore(r, screen))}`}>
                          {screenScore(r, screen).toFixed(1)}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-right text-muted-foreground" title={`${other} score`}>
                        {screenScore(r, other).toFixed(1)}
                      </td>
                      <td className="px-2 py-1.5 text-right">{fmtPrice(r.iv15)}</td>
                      <td className="px-2 py-1.5 text-right">{fmtPrice(r.price)}</td>
                      <td className="px-2 py-1.5 text-right">{r.p_iv15 == null ? '—' : `${r.p_iv15.toFixed(2)}×`}</td>
                      <td className="px-2 py-1.5">
                        <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${aictClass(r.aict_tier)}`}>
                          {r.aict_tier}
                        </span>
                      </td>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-wrap gap-1">
                          {metricKeys.map((m) => (
                            <span
                              key={m}
                              className="inline-flex items-center gap-1 px-1 py-0.5 rounded bg-muted/40 text-[10px]"
                              title={`${METRIC_META[m]?.label ?? m} · source: ${r.metrics[m]?.source ?? 'missing'}`}
                            >
                              <span className={`w-1 h-1 rounded-full ${SOURCE_DOT[r.metrics[m]?.source ?? 'missing'] ?? SOURCE_DOT.missing}`} />
                              <span className="text-muted-foreground">{METRIC_META[m]?.short ?? m}</span>
                              <span className="text-foreground">{fmtMetric(m, r.metrics[m]?.value)}</span>
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="px-3 py-2 text-[10px] text-muted-foreground border-t border-border">
              Showing <strong className="text-foreground">{rows.length}</strong> of {cohort.ticker_count} ·
              ranked by <strong className="text-foreground">{screen}</strong> score (desc) ·
              click any row for both screens + the IV15 build-up
            </div>
          </div>
        )}
      </div>

      {selected && (
        <DetailDrawer ticker={selected} initialScreen={screen} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}


// ─── Top-5 hero card ─────────────────────────────────────────────────────────

function TopFive({
  title, icon, rows, screen, onPick, onFocus,
}: {
  title: string;
  icon: React.ReactNode;
  rows: HK50TickerResult[];
  screen: Screen;
  onPick: (r: HK50TickerResult) => void;
  onFocus: () => void;
}) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <button onClick={onFocus} className="flex items-center gap-1.5 mb-2 group">
        {icon}
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground group-hover:text-foreground">{title}</span>
      </button>
      <div className="space-y-1">
        {rows.map((r, i) => (
          <button
            key={r.ticker}
            onClick={() => onPick(r)}
            className="w-full flex items-center gap-2 px-2 py-1 rounded hover:bg-muted/40 text-left"
          >
            <span className="text-[10px] text-muted-foreground w-4 text-right">{i + 1}</span>
            <span className="text-xs font-semibold text-foreground flex-1 truncate">{r.name}</span>
            <span className="text-[10px] text-muted-foreground font-mono">{r.ticker}</span>
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold font-mono ${scoreColor(screenScore(r, screen))}`}>
              {screenScore(r, screen).toFixed(1)}
            </span>
          </button>
        ))}
      </div>
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


// ─── Detail drawer ───────────────────────────────────────────────────────────

function DetailDrawer({ ticker, initialScreen, onClose }: { ticker: HK50TickerResult; initialScreen: Screen; onClose: () => void }) {
  const d = ticker.iv15_detail;
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
                <span className="text-lg font-bold text-foreground">{ticker.name}</span>
                <span className="text-sm text-muted-foreground font-mono">{ticker.ticker}</span>
                {ticker.ticker !== ticker.hk_ticker && (
                  <span className="text-[10px] text-muted-foreground font-mono">({ticker.hk_ticker})</span>
                )}
                <span className="text-[10px] text-muted-foreground ml-auto">{ticker.route_label}</span>
              </div>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${aictClass(ticker.aict_tier)}`}>AICT · {ticker.aict_tier}</span>
                <span className="text-xs text-muted-foreground">Lead: <strong className="text-foreground">{ticker.lead}</strong></span>
                <span className="text-xs text-muted-foreground">{ticker.currency}</span>
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
          {/* Score banner */}
          <div className="grid grid-cols-2 gap-2 mt-3">
            <ScoreBanner label="Growth Screen" score={ticker.growth_score} rank={ticker.growth_rank} active={initialScreen === 'Growth'} />
            <ScoreBanner label="Dividend Screen" score={ticker.dividend_score} rank={ticker.dividend_rank} active={initialScreen === 'Dividend'} />
          </div>
        </div>

        <div className="p-4 space-y-6">
          {ticker.error && (
            <p className="text-[11px] text-amber-500">
              <AlertTriangle size={11} className="inline mb-0.5" /> {ticker.error}
            </p>
          )}

          {/* Growth metrics */}
          <MetricSection title="Growth metrics" keys={GROWTH_METRICS} metrics={ticker.metrics} />

          {/* Dividend metrics */}
          <MetricSection title="Dividend metrics" keys={DIV_METRICS} metrics={ticker.metrics} />

          {/* IV15 build-up */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
              IV15 build-up {d.aict !== '—' ? `· AICT-modulated (${d.aict})` : '· neutral Gordon'}
            </h2>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <KV label="IV15 / share" value={fmtPrice(d.iv15, d.currency)} highlight />
              <KV label="Current price" value={fmtPrice(d.price, d.currency)} />
              <KV label="P / IV15" value={d.p_iv15 == null ? '—' : `${d.p_iv15.toFixed(2)}×`} highlight />
              <KV label="Valuation pts" value={d.valuation_points == null ? '—' : `${d.valuation_points} / 35`} />
              <KV label="Base EPS (E₀)" value={fmtNum(d.base_eps)} />
              <KV label="Stage-1 growth" value={fmtPct(d.stage1_growth)} />
              <KV label="IV (Gordon)" value={fmtNum(d.iv_gordon)} />
              <KV label="IV (Buffett)" value={d.iv_buffett == null ? '—' : fmtNum(d.iv_buffett)} />
              <KV label="Growth haircut" value={d.haircut == null ? '—' : fmtPct(d.haircut)} />
              <KV label="Terminal multiple" value={d.terminal_multiple == null ? '—' : `${d.terminal_multiple.toFixed(0)}×`} />
              <KV label="Blend weight (Gordon)" value={d.blend_weight_a == null ? '—' : fmtPct(d.blend_weight_a)} />
            </div>
            <p className="text-[10px] text-muted-foreground mt-2">
              IV15 = 15-year owner-earnings discount @ 15% required return; blended between a Gordon-terminal valuation
              and {d.terminal_multiple == null ? 'no Buffett-multiple leg (pure Gordon)' : 'a Buffett terminal-multiple leg'}.
              {d.aict === '—' && ' AICT is not applicable to this name (non-software / non-internet).'}
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}


// ─── Leaf components ─────────────────────────────────────────────────────────

function ScoreBanner({ label, score, rank, active }: { label: string; score: number; rank: number | null; active: boolean }) {
  return (
    <div className={`p-2 rounded border ${active ? 'border-primary bg-primary/5' : 'border-border bg-muted/20'}`}>
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
        {rank != null && <span className="text-[10px] text-muted-foreground">#{rank}</span>}
      </div>
      <span className={`inline-block mt-1 px-2 py-0.5 rounded text-sm font-bold font-mono ${scoreColor(score)}`}>
        {score.toFixed(1)}
      </span>
      <span className="text-[10px] text-muted-foreground ml-1">/ 100</span>
    </div>
  );
}

function MetricSection({ title, keys, metrics }: { title: string; keys: string[]; metrics: Record<string, { value: number | null; source: string }> }) {
  return (
    <section>
      <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">{title}</h2>
      <div className="grid grid-cols-1 gap-1 text-xs">
        {keys.map((m) => {
          const mr = metrics[m];
          return (
            <div key={m} className="flex items-center gap-2 py-1 border-b border-border/50 last:border-0">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${SOURCE_DOT[mr?.source ?? 'missing'] ?? SOURCE_DOT.missing}`} />
              <span className="text-muted-foreground flex-1">{METRIC_META[m]?.label ?? m}</span>
              <span className="text-[9px] text-muted-foreground/70 uppercase">{mr?.source ?? 'missing'}</span>
              <span className="font-mono text-foreground w-16 text-right">{fmtMetric(m, mr?.value)}</span>
            </div>
          );
        })}
      </div>
    </section>
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
