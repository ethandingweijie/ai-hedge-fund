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
  getHK50Cohort, refreshHK50, runHK50DeepResearch, runHK50TickerQual, getHK50Job,
  type HK50Cohort, type HK50TickerResult, type HK50Qualitative,
  type HK50QualDimension, type HK50QualJob,
} from '@/lib/api';
import { ArrowLeft, RefreshCw, Loader2, AlertTriangle, X, Search, TrendingUp, Coins, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { PageContainer } from '@/components/layout/PageContainer';
import { rankTone } from '@/lib/semanticColors';


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

// Light mode: dark text (700) for legibility on the pale `/20` badge.
// Dark mode (`dark:`): keep the original light-300 text.
const AICT_COLOR: Record<string, string> = {
  Fortress: 'bg-surface-2 text-content-high border-[var(--hairline)]',
  Castle:   'bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-700/40',
  Chapel:   'bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-700/40',
  Stone:    'bg-orange-600/20 text-orange-700 dark:text-orange-300 border-orange-700/40',
  Wood:     'bg-surface-2 text-content-high border-[var(--hairline)]',
};
const aictClass = (tier: string): string =>
  AICT_COLOR[tier] ?? 'bg-muted text-muted-foreground border-border';

// Provenance dot for a resolved metric source.
const SOURCE_DOT: Record<string, string> = {
  primary:    'bg-surface-2',
  fmp_growth: 'bg-cyan-400',
  yfinance:   'bg-blue-400',
  compute:    'bg-amber-400',
  structural: 'bg-purple-400',
  missing:    'bg-muted-foreground/40',
};


function scoreColor(score: number): string {
  // Light mode: dark text (700/800) for legibility on the pale `/30` pill.
  // Dark mode (`dark:`): keep the original light-200 text.
  if (score >= 75) return rankTone(0);
  if (score >= 55) return rankTone(1);
  if (score >= 35) return rankTone(2);
  return rankTone(3);
}


// ─── Qualitative overlay colours (parallel to the two quant screens) ─────────
// Combined conviction badge — QUANT-RICH / POLICY-RISK flag the traps the two
// pure quant screens can't see.
const CONVICTION_COLOR: Record<string, string> = {
  // Light mode: dark text (800) for legibility on the pale `/30` badge.
  // Dark mode (`dark:`): keep the original light-200 text.
  'HIGH-CONVICTION': 'bg-surface-2 text-content-high border-[var(--hairline)]',
  'SOLID':           'bg-blue-600/30 text-blue-800 dark:text-blue-200 border-blue-700/40',
  'QUANT-RICH':      'bg-amber-600/30 text-amber-800 dark:text-amber-200 border-amber-700/40',
  'QUAL-SUPPORT':    'bg-cyan-600/30 text-cyan-800 dark:text-cyan-200 border-cyan-700/40',
  'WATCH':           'bg-muted text-muted-foreground border-border',
  'POLICY-RISK':     'bg-surface-2 text-content-high border-[var(--hairline)]',
  'UNSCORED':        'bg-muted/30 text-muted-foreground/70 border-border border-dashed',
};
const convictionClass = (c: string): string =>
  CONVICTION_COLOR[c] ?? CONVICTION_COLOR.UNSCORED;

// Short badge labels for the narrow table cell (full label in the title attr).
const CONVICTION_SHORT: Record<string, string> = {
  'HIGH-CONVICTION': 'HIGH',
  'SOLID':           'SOLID',
  'QUANT-RICH':      'TRAP?',
  'QUAL-SUPPORT':    'QUAL',
  'WATCH':           'WATCH',
  'POLICY-RISK':     'RISK',
  'UNSCORED':        '—',
};

// Policy ladder: Tailwind > Favorable > Neutral > Headwind > Crackdown.
// Light mode: dark text (700) for legibility on the white card.
// Dark mode (`dark:`): keep the original light-300 text.
const POLICY_TEXT: Record<string, string> = {
  Tailwind:  'text-content-high',
  Favorable: 'text-blue-700 dark:text-blue-300',
  Neutral:   'text-muted-foreground',
  Headwind:  'text-orange-700 dark:text-orange-300',
  Crackdown: 'text-content-high',
};
// Moat ladder reuses the AICT names generalised to every sector.
const MOAT_TEXT: Record<string, string> = {
  Fortress: 'text-content-high',
  Castle:   'text-blue-700 dark:text-blue-300',
  Chapel:   'text-amber-700 dark:text-amber-300',
  Stone:    'text-orange-700 dark:text-orange-300',
  Wood:     'text-content-high',
};


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
  // Pool view: the displayed cohort only (default) vs the full eligible pool
  // with the bench greyed. Membership is decided server-side; this is display.
  const [poolView, setPoolView] = useState<'cohort' | 'all'>('cohort');
  const [search, setSearch]   = useState('');
  const [selected, setSelected] = useState<HK50TickerResult | null>(null);
  // Conviction popover — elaborates the Policy × Moat breakdown for one row.
  const [convDetail, setConvDetail] = useState<HK50TickerResult | null>(null);
  const [deepJob, setDeepJob] = useState<HK50QualJob | null>(null);
  const [deepRunning, setDeepRunning] = useState(false);
  // Per-ticker manual research (drill-in drawer). Tracks one in-flight ticker job.
  const [tickerJob, setTickerJob] = useState<HK50QualJob | null>(null);

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

  // Phase 2 — LLM Policy + Moat deep-research over the top-20 names. The quant
  // cards (this cohort) must already exist; rows are patched live as each name
  // streams in, so we re-pull the cohort on every poll.
  const handleDeepResearch = async () => {
    if (!cohort || cohort.results.length === 0) {
      toast.error('Run the cohort (Refresh) first — quant cards must exist before deep research.');
      return;
    }
    setDeepRunning(true);
    try {
      const { job_id, deduped } = await runHK50DeepResearch({ topN: 20 });
      toast.info(
        deduped
          ? 'Deep research already in flight — attaching to it.'
          : 'Deep research started · top-20 by lead score · runs sequentially (~10-20 min). Cards update live.',
      );
      setDeepJob({
        job_id, kind: 'hk50_qual', ticker: null, status: 'pending',
        started_at: null, finished_at: null, progress_msg: null, result: null, error: null,
      });
    } catch (e) {
      toast.error((e as Error).message);
      setDeepRunning(false);
    }
  };

  // Poll the job + live-reload the cohort while it runs. Keyed on job_id only so
  // status transitions don't churn the interval; terminal states stop it inside.
  useEffect(() => {
    if (!deepJob?.job_id) return;
    if (deepJob.status === 'completed' || deepJob.status === 'failed') return;
    const jobId = deepJob.job_id;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    const tick = async () => {
      try {
        const j = await getHK50Job(jobId);
        if (cancelled) return;
        setDeepJob(j);
        try { setCohort(await getHK50Cohort()); } catch { /* keep last cohort */ }
        if (j.status === 'completed' || j.status === 'failed') {
          cancelled = true;
          if (timer) clearInterval(timer);
          setDeepRunning(false);
          if (j.status === 'completed') {
            const s = (j.result?.phase2_summary ?? {}) as {
              qual_scored?: string[]; total_deep_escalations?: number;
            };
            toast.success(
              `Deep research complete — ${s.qual_scored?.length ?? 0} names scored, ` +
              `${s.total_deep_escalations ?? 0} deep escalations.`,
            );
          } else {
            toast.error(`Deep research failed: ${j.error ?? 'unknown error'}`);
          }
        }
      } catch { /* transient — keep polling */ }
    };
    timer = setInterval(tick, 4000);
    tick();
    return () => { cancelled = true; if (timer) clearInterval(timer); };
  }, [deepJob?.job_id, deepJob?.status]);

  // Manual deep-research for ONE name (the open drawer). Same LLM pass as the
  // batch, scoped to a single ticker; the row is patched live so the drawer
  // fills in as sub-metrics stream. Separate job-id from the batch job so both
  // can run independently.
  const handleTickerResearch = async (r: HK50TickerResult) => {
    try {
      const { job_id, deduped } = await runHK50TickerQual(r.ticker, { forceRefresh: false });
      toast.info(
        deduped
          ? `Research already in flight for ${r.name} — attaching.`
          : `Generating Policy + Moat research for ${r.name}… (~2-5 min). Card updates live.`,
      );
      setTickerJob({
        job_id, kind: 'hk50_qual_ticker', ticker: r.ticker, status: 'pending',
        started_at: null, finished_at: null, progress_msg: null, result: null, error: null,
      });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  // Poll the per-ticker job + live-reload the cohort while it runs (mirrors the
  // batch poller). Keyed on job_id + status so transitions don't churn the timer.
  useEffect(() => {
    if (!tickerJob?.job_id) return;
    if (tickerJob.status === 'completed' || tickerJob.status === 'failed') return;
    const jobId = tickerJob.job_id;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    const tick = async () => {
      try {
        const j = await getHK50Job(jobId);
        if (cancelled) return;
        setTickerJob(j);
        try { setCohort(await getHK50Cohort()); } catch { /* keep last cohort */ }
        if (j.status === 'completed' || j.status === 'failed') {
          cancelled = true;
          if (timer) clearInterval(timer);
          if (j.status === 'completed') {
            const conv = (j.result?.conviction as string | undefined) ?? null;
            const n = (j.result?.deep_escalations as number | undefined) ?? 0;
            toast.success(
              `Research complete for ${j.ticker ?? 'ticker'}` +
              (conv ? ` — ${conv}` : '') + (n ? ` · ${n} deep escalations` : '') + '.',
            );
          } else {
            toast.error(`Research failed: ${j.error ?? 'unknown error'}`);
          }
        }
      } catch { /* transient — keep polling */ }
    };
    timer = setInterval(tick, 4000);
    tick();
    return () => { cancelled = true; if (timer) clearInterval(timer); };
  }, [tickerJob?.job_id, tickerJob?.status]);

  // Live row for the open drawer (reflects mid-job patches without reopening).
  const selectedLive = useMemo(() => {
    if (!selected) return null;
    return cohort?.results.find((r) => r.ticker === selected.ticker) ?? selected;
  }, [selected, cohort]);

  // Live row behind the conviction popover (fills in as research streams).
  const convDetailLive = useMemo(() => {
    if (!convDetail) return null;
    return cohort?.results.find((r) => r.ticker === convDetail.ticker) ?? convDetail;
  }, [convDetail, cohort]);

  // Is a per-ticker research job in flight for the currently-open drawer name?
  const drawerResearching = useMemo(() => {
    if (!selectedLive || !tickerJob) return false;
    return (
      tickerJob.ticker === selectedLive.ticker &&
      (tickerJob.status === 'pending' || tickerJob.status === 'running')
    );
  }, [selectedLive, tickerJob]);

  // Top-5 of each screen for the hero strip.
  const top5Growth = useMemo(
    () => [...(cohort?.results ?? [])].sort((a, b) => b.growth_score - a.growth_score).slice(0, 5),
    [cohort],
  );
  const top5Dividend = useMemo(
    () => [...(cohort?.results ?? [])].sort((a, b) => b.dividend_score - a.dividend_score).slice(0, 5),
    [cohort],
  );

  // Active-screen ranked + filtered rows. In "cohort" view we show only the
  // displayed members; in "all" view the full pool with bench rows greyed.
  const rows = useMemo(() => {
    if (!cohort?.results) return [];
    const q = search.trim().toUpperCase();
    return cohort.results
      .filter((r) => poolView === 'all' || r.in_cohort)
      .filter((r) =>
        !q ||
        r.ticker.toUpperCase().includes(q) ||
        r.hk_ticker.toUpperCase().includes(q) ||
        r.name.toUpperCase().includes(q),
      )
      .slice()
      .sort((a, b) => screenScore(b, screen) - screenScore(a, screen));
  }, [cohort, screen, search, poolView]);

  const benchCount = useMemo(
    () => (cohort?.results ?? []).filter((r) => !r.in_cohort).length,
    [cohort],
  );

  const metricKeys = screenMetrics(screen);

  return (
    <PageContainer size="wide">
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
          ~100-name eligible pool · the displayed <strong>top 50</strong> is an output of the screen (lead-score threshold + hysteresis) ·
          two independent 0-100 screens · IV15 (AICT-modulated for software / internet)
        </p>

        {/* ── Cohort summary bar ──────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-4 mb-3 p-3 rounded-md border border-border bg-card">
          <Stat label="Avg Growth" value={cohort?.avg_growth != null ? cohort.avg_growth.toFixed(1) : '—'} />
          <Stat label="Avg Dividend" value={cohort?.avg_dividend != null ? cohort.avg_dividend.toFixed(1) : '—'} />
          <Stat label="Median P/IV15" value={cohort?.median_p_iv15 != null ? `${cohort.median_p_iv15.toFixed(2)}×` : '—'} />
          <Stat label="Lead split" value={cohort?.displayed_count ? `${cohort.lead_growth_count}G / ${cohort.displayed_count - cohort.lead_growth_count}D` : '—'} />
          <Stat
            label="Displayed / Pool"
            value={`${cohort?.displayed_count ?? 0} / ${cohort?.eligible_count ?? 0}`}
            title="Names in the displayed cohort vs the full eligible pool scored this run."
          />
          <Stat
            label="ENTER / STAY"
            value={cohort?.enter_threshold ? `${cohort.enter_threshold.toFixed(0)} / ${cohort.stay_threshold.toFixed(0)}` : '—'}
            title="Hysteresis bands on lead score = max(Growth, Dividend). A non-member joins at ENTER; a member is kept down to STAY. Cap 50."
          />
          {cohort?.failed_tickers && cohort.failed_tickers.length > 0 && (
            <Stat
              label="Failed"
              value={String(cohort.failed_tickers.length)}
              tone="warn"
              title={cohort.failed_tickers.map((f) => `${f.ticker}: ${f.reason}`).join('\n')}
            />
          )}
          <Stat label="Last run" value={formatRunTime(cohort?.created_at ?? null)} mono={false} />
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={handleDeepResearch}
              disabled={deepRunning || !cohort || cohort.results.length === 0}
              title="LLM Policy + Moat deep-research over the top-20 names by lead score. Quant cards must exist first; runs sequentially to avoid rate limits and patches rows live."
              className="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-border bg-card text-foreground text-sm font-medium hover:bg-muted disabled:opacity-50"
            >
              {deepRunning ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} className="text-violet-400" />}
              {deepRunning ? 'Researching…' : 'Deep research'}
            </button>
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

        {/* ── Deep-research live progress strip ───────────────────────────── */}
        {deepJob && (deepJob.status === 'pending' || deepJob.status === 'running') && (
          <div className="mb-3 flex items-center gap-2 px-3 py-2 rounded-md border border-violet-700/40 bg-violet-600/10 text-xs text-violet-200">
            <Loader2 size={12} className="animate-spin flex-shrink-0" />
            <span className="truncate">{deepJob.progress_msg || 'Starting deep research…'}</span>
            <span className="ml-auto text-violet-300/70 hidden sm:inline flex-shrink-0">
              Policy + Moat · cards update live · sequential to avoid 429s
            </span>
          </div>
        )}

        {/* ── Membership changes since last run ───────────────────────────── */}
        {!loading && cohort && ((cohort.promoted?.length ?? 0) > 0 || (cohort.relegated?.length ?? 0) > 0) && (
          <div className="mb-4 flex flex-wrap items-start gap-x-4 gap-y-2 px-3 py-2 rounded-md border border-border bg-card">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground pt-1">Changes since last run</span>
            <div className="flex flex-wrap items-center gap-1.5">
              {(cohort.promoted ?? []).map((p) => (
                <span
                  key={`up-${p.ticker}`}
                  title={`Promoted into the cohort · lead score ${p.lead_score.toFixed(1)} (cleared ENTER ${cohort.enter_threshold.toFixed(0)})`}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-surface-2 text-content-high border border-[var(--hairline)]"
                >
                  ▲ {p.name} <span className="font-mono opacity-70">{p.lead_score.toFixed(0)}</span>
                </span>
              ))}
              {(cohort.relegated ?? []).map((r) => (
                <span
                  key={`down-${r.ticker}`}
                  title={`Relegated · lead score ${r.lead_score.toFixed(1)} · ${r.reason ?? ''}`}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-600/15 text-amber-700 dark:text-amber-300 border border-amber-700/30"
                >
                  ▼ {r.name} <span className="font-mono opacity-70">{r.lead_score.toFixed(0)}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* ── Hero strip: top-5 of each screen ────────────────────────────── */}
        {!loading && cohort && cohort.results.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
            <TopFive
              title="Top 5 · Growth"
              icon={<TrendingUp size={14} className="text-content-high" />}
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
          {/* Cohort-only vs full-pool view */}
          <div className="inline-flex rounded-md border border-border overflow-hidden">
            {([
              ['cohort', `Cohort (${cohort?.displayed_count ?? 0})`],
              ['all', `Full pool (${cohort?.eligible_count ?? 0})`],
            ] as [typeof poolView, string][]).map(([v, label]) => (
              <button
                key={v}
                onClick={() => setPoolView(v)}
                title={v === 'cohort'
                  ? 'Show only the displayed cohort (names above the membership line).'
                  : `Show the full eligible pool — ${benchCount} bench names greyed below the line.`}
                className={`px-3 py-1.5 text-xs font-semibold transition-colors ${
                  poolView === v
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-card text-muted-foreground hover:bg-muted'
                }`}
              >
                {label}
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
                  <th className="px-2 py-2" title="Policy × Moat qualitative overlay (parallel to the quant screens). Click a cell to elaborate on the moat breakdown; run deep research to populate.">Conviction</th>
                  <th className="px-2 py-2 text-right text-foreground">{screen} Score</th>
                  <th className="px-2 py-2">AICT</th>
                  <th className="px-2 py-2 text-right">Other</th>
                  <th className="px-2 py-2 text-right">IV15</th>
                  <th className="px-2 py-2 text-right">Price</th>
                  <th className="px-2 py-2 text-right">P/IV15</th>
                  <th className="px-2 py-2">Key {screen} Metrics</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const other: Screen = screen === 'Growth' ? 'Dividend' : 'Growth';
                  const benched = !r.in_cohort;
                  return (
                    <tr
                      key={r.ticker}
                      onClick={() => setSelected(r)}
                      className={`border-t border-border hover:bg-muted/30 cursor-pointer ${
                        r.membership === 'promoted' ? 'bg-surface-2' : ''
                      } ${benched ? 'opacity-50' : ''}`}
                    >
                      <td className="px-2 py-1.5 text-right text-muted-foreground">
                        {r.in_cohort && r.cohort_rank != null
                          ? <span className="text-foreground font-semibold">{r.cohort_rank}</span>
                          : <span className="text-muted-foreground/50">·</span>}
                      </td>
                      <td className="px-2 py-1.5">
                        <div className="font-bold text-foreground flex items-center gap-1 whitespace-nowrap">
                          {r.name}
                          {r.lead === screen && (
                            <span className="text-[8px] px-1 rounded bg-primary/20 text-primary uppercase tracking-wide">lead</span>
                          )}
                          {r.membership === 'promoted' && (
                            <span title="Newly promoted into the cohort this run" className="text-[8px] px-1 rounded bg-surface-2 text-content-high uppercase tracking-wide">new</span>
                          )}
                          {benched && (
                            <span title="Bench — below the membership line (not in the displayed cohort)" className="text-[8px] px-1 rounded bg-muted text-muted-foreground uppercase tracking-wide">bench</span>
                          )}
                        </div>
                        <div className="text-[10px] text-muted-foreground truncate max-w-[140px]">
                          {r.ticker}{r.ticker !== r.hk_ticker ? ` · ${r.hk_ticker}` : ''}
                        </div>
                      </td>
                      <td className="px-2 py-1.5">
                        <QualCell q={r.qualitative} onClick={() => setConvDetail(r)} />
                      </td>
                      <td className="px-2 py-1.5 text-right">
                        <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold ${scoreColor(screenScore(r, screen))}`}>
                          {screenScore(r, screen).toFixed(1)}
                        </span>
                      </td>
                      <td className="px-2 py-1.5">
                        <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${aictClass(r.aict_tier)}`}>
                          {r.aict_tier}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-right text-muted-foreground" title={`${other} score`}>
                        {screenScore(r, other).toFixed(1)}
                      </td>
                      <td className="px-2 py-1.5 text-right">{fmtPrice(r.iv15)}</td>
                      <td className="px-2 py-1.5 text-right">{fmtPrice(r.price)}</td>
                      <td className="px-2 py-1.5 text-right">{r.p_iv15 == null ? '—' : `${r.p_iv15.toFixed(2)}×`}</td>
                      <td className="px-2 py-1.5">
                        {/* nowrap: keep all metric chips on ONE line (extends the
                            column horizontally with the rest of the wide table)
                            instead of wrapping to ~5 lines and stretching every
                            row tall. Matches the SW46 / Complacency row height. */}
                        <div className="flex flex-nowrap gap-1">
                          {metricKeys.map((m) => (
                            <span
                              key={m}
                              className="inline-flex items-center gap-1 px-1 py-0.5 rounded bg-muted/40 text-[10px] whitespace-nowrap"
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
            <div className="px-3 py-2 text-[11px] text-muted-foreground border-t border-border leading-relaxed">
              <span className="font-semibold text-foreground">Conviction</span> — a qualitative
              Policy&nbsp;×&nbsp;Moat overlay, <em>never</em> summed into the {screen} score:
              <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1.5 items-center">
                <span className="inline-flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${convictionClass('HIGH-CONVICTION')}`}>HIGH</span>
                  strong screen + favourable policy + wide moat
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${convictionClass('SOLID')}`}>SOLID</span>
                  decent screen, adequate policy &amp; moat
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${convictionClass('QUAL-SUPPORT')}`}>QUAL</span>
                  weak screen but strong policy &amp; moat — await catalyst
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${convictionClass('WATCH')}`}>WATCH</span>
                  mixed signals — no dimension decisive
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${convictionClass('QUANT-RICH')}`}>TRAP?</span>
                  top screen undercut by policy headwind / thin moat — verify it isn&apos;t a value trap
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${convictionClass('POLICY-RISK')}`}>RISK</span>
                  active crackdown or eroding moat — overrides screen rank
                </span>
              </div>
            </div>
          </div>
        )}

      {selectedLive && (
        <DetailDrawer
          ticker={selectedLive}
          initialScreen={screen}
          onClose={() => setSelected(null)}
          onGenerateResearch={() => handleTickerResearch(selectedLive)}
          researching={drawerResearching}
          researchMsg={drawerResearching ? tickerJob?.progress_msg : null}
        />
      )}

      {convDetailLive && (
        <ConvictionPopover
          row={convDetailLive}
          onClose={() => setConvDetail(null)}
          onOpenDetail={() => { setSelected(convDetailLive); setConvDetail(null); }}
        />
      )}
    </PageContainer>
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
      <span className={`text-[10px] uppercase tracking-wider ${labelColor}`}>{label}</span>
      <span className={`${mono ? 'font-mono' : ''} text-sm font-bold ${valueColor}`}>{value}</span>
    </div>
  );
}


// ─── Detail drawer ───────────────────────────────────────────────────────────

function DetailDrawer({ ticker, initialScreen, onClose, onGenerateResearch, researching, researchMsg }: {
  ticker: HK50TickerResult;
  initialScreen: Screen;
  onClose: () => void;
  onGenerateResearch: () => void;
  researching: boolean;
  researchMsg?: string | null;
}) {
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
            {d.oe_retention != null && (
              <div className="mt-3 pt-2 border-t border-border/40">
                <h3 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1">
                  Owner-earnings adjustment (Method E) {d.is_net_diluter ? '· net diluter' : '· net buyer'}
                </h3>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <KV label="Raw EPS (pre-adj)" value={fmtNum(d.e0_raw)} />
                  <KV label="OE retention" value={fmtPct(d.oe_retention)} highlight />
                  <KV label="SBC % of NI" value={fmtPct(d.sbc_pct_ni)} />
                  <KV label="Unfunded comp ($M)" value={d.unfunded_comp == null ? '—' : fmtNum(d.unfunded_comp / 1e6, 0)} />
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  E₀ is charged for the stock comp buybacks did not fund: retention = (NI − cash-tax
                  {d.oe_cash_tax_estimated ? ' (est. SBC×0.37)' : ''} − max(0, SBC − buybacks)) ÷ NI, clamped 30–100%.
                  Net buyers carry no charge; SOEs/banks (sparse SBC) and native-HK names are not adjusted.
                </p>
              </div>
            )}
            <p className="text-[10px] text-muted-foreground mt-2">
              IV15 = 15-year owner-earnings discount @ 15% required return; blended between a Gordon-terminal valuation
              and {d.terminal_multiple == null ? 'no Buffett-multiple leg (pure Gordon)' : 'a Buffett terminal-multiple leg'}.
              {d.aict === '—' && ' AICT is not applicable to this name (non-software / non-internet).'}
            </p>
          </section>

          {/* Qualitative overlay (Policy + Moat) — always shown so the user can
              generate research on demand even when the name is unscored. */}
          <QualitativeBlock
            q={ticker.qualitative}
            onGenerate={onGenerateResearch}
            researching={researching}
            researchMsg={researchMsg}
          />
        </div>
      </div>
    </div>
  );
}


// ─── Compact qualitative cell (table) ────────────────────────────────────────

function QualCell({ q, onClick }: { q?: HK50Qualitative | null; onClick?: () => void }) {
  const handle = onClick
    ? (e: React.MouseEvent) => { e.stopPropagation(); onClick(); }
    : undefined;

  if (!q || q.conviction === 'UNSCORED') {
    return (
      <button
        onClick={handle}
        title="No qualitative research yet — click to view / generate the Policy × Moat overlay"
        className="text-[10px] text-muted-foreground/40 hover:text-muted-foreground"
      >
        —
      </button>
    );
  }
  const pol = q.policy?.tier ?? '—';
  const moat = q.moat?.tier ?? '—';
  const lines = [
    `Conviction: ${q.conviction}`,
    `Policy: ${pol}`,
    `Moat: ${moat}`,
    `Source: ${q.source}${q.incomplete ? ' (streaming…)' : ''}`,
    q.flags?.length ? `Flags: ${q.flags.join(' · ')}` : '',
  ].filter(Boolean);
  const title = `${lines.join('\n')}\n\n(click to elaborate on the moat breakdown)`;
  return (
    <button onClick={handle} className="flex flex-col gap-0.5 text-left hover:opacity-80 cursor-pointer" title={title}>
      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-bold w-fit ${convictionClass(q.conviction)}`}>
        {q.incomplete && <Loader2 size={8} className="animate-spin" />}
        {CONVICTION_SHORT[q.conviction] ?? q.conviction}
      </span>
      <span className="text-[10px] leading-tight whitespace-nowrap">
        <span className={POLICY_TEXT[pol] ?? 'text-muted-foreground'}>P:{pol}</span>
        <span className="text-muted-foreground/40"> · </span>
        <span className={MOAT_TEXT[moat] ?? 'text-muted-foreground'}>M:{moat}</span>
      </span>
    </button>
  );
}


// ─── Conviction popover (table click → elaborate on Policy × Moat) ──────────

function ConvictionPopover({ row, onClose, onOpenDetail }: {
  row: HK50TickerResult;
  onClose: () => void;
  onOpenDetail: () => void;
}) {
  const q = row.qualitative;
  const scored = !!q && q.conviction !== 'UNSCORED';
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 animate-in fade-in duration-150" />
      <div
        className="relative w-full max-w-lg bg-background border border-border rounded-lg shadow-2xl max-h-[85vh] overflow-y-auto animate-in zoom-in-95 duration-150"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-background border-b border-border px-4 py-3 flex items-center justify-between gap-2 z-10">
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            <span className="text-sm font-bold text-foreground truncate">{row.name}</span>
            <span className="text-[10px] text-muted-foreground font-mono flex-shrink-0">{row.ticker}</span>
            {scored && (
              <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold flex-shrink-0 ${convictionClass(q!.conviction)}`}>
                {q!.incomplete && <Loader2 size={8} className="inline animate-spin mr-1" />}
                {q!.conviction}
              </span>
            )}
          </div>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-muted flex-shrink-0" aria-label="Close">
            <X size={16} className="text-foreground" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          {!scored ? (
            <p className="text-xs text-muted-foreground">
              No qualitative research yet for this name. Open the full detail to generate the
              Policy × Moat overlay on demand.
            </p>
          ) : (
            <>
              <p className="text-[10px] text-muted-foreground">
                Conviction blends the <strong>Policy posture</strong> and <strong>competitive-moat</strong> tiers
                (parallel to the quant screens — <strong>never</strong> summed into Growth / Dividend). Each card
                below breaks the dimension into its scored sub-metrics.
              </p>
              {q!.flags && q!.flags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {q!.flags.map((f, i) => (
                    <span key={i} className="px-1.5 py-0.5 rounded bg-amber-600/15 text-amber-300 text-[10px] border border-amber-700/30">{f}</span>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-1 gap-3">
                <QualDimensionCard dim={q!.policy} title="Policy posture" tierColors={POLICY_TEXT} />
                <QualDimensionCard dim={q!.moat} title="Competitive moat" tierColors={MOAT_TEXT} />
              </div>
            </>
          )}
          <button
            onClick={onOpenDetail}
            className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-md border border-border bg-card text-foreground text-xs font-medium hover:bg-muted"
          >
            {scored ? 'Full details' : 'Open details · generate research'} →
          </button>
        </div>
      </div>
    </div>
  );
}


// ─── Qualitative overlay section (drawer) ────────────────────────────────────

function QualitativeBlock({ q, onGenerate, researching, researchMsg }: {
  q?: HK50Qualitative | null;
  onGenerate: () => void;
  researching: boolean;
  researchMsg?: string | null;
}) {
  const scored = !!q && q.conviction !== 'UNSCORED';
  return (
    <section>
      <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2 flex items-center gap-2 flex-wrap">
        Qualitative overlay
        {scored && (
          <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${convictionClass(q!.conviction)}`}>{q!.conviction}</span>
        )}
        {q && (
          <span className="text-[10px] text-muted-foreground/70 normal-case">
            source: {q.source}{q.incomplete ? ' · streaming…' : ''}
          </span>
        )}
        <button
          onClick={onGenerate}
          disabled={researching}
          title="Run the LLM Policy + Moat deep-research for THIS name only. Patches the card live; ~2-5 min. Runs independently of the cohort-wide deep research."
          className="ml-auto inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-violet-700/40 bg-violet-600/10 text-violet-200 text-[10px] font-medium hover:bg-violet-600/20 disabled:opacity-50"
        >
          {researching ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} className="text-violet-400" />}
          {researching ? 'Researching…' : scored ? 'Re-run research' : 'Generate research'}
        </button>
      </h2>
      <p className="text-[10px] text-muted-foreground mb-2">
        Parallel to the two quant screens — <strong>never</strong> summed into Growth / Dividend. Policy posture
        (state tailwind ↔ crackdown) × competitive moat, confidence-weighted.
      </p>
      {researching && (
        <div className="mb-2 flex items-center gap-2 px-2 py-1.5 rounded border border-violet-700/40 bg-violet-600/10 text-[10px] text-violet-200">
          <Loader2 size={10} className="animate-spin flex-shrink-0" />
          <span className="truncate">{researchMsg || 'Starting research…'}</span>
        </div>
      )}
      {!scored ? (
        <p className="text-[11px] text-muted-foreground">
          No qualitative research yet for this name. Click <strong>Generate research</strong> to run the
          Policy × Moat overlay on demand.
        </p>
      ) : (
        <>
          {q!.flags && q!.flags.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {q!.flags.map((f, i) => (
                <span key={i} className="px-1.5 py-0.5 rounded bg-amber-600/15 text-amber-300 text-[10px] border border-amber-700/30">{f}</span>
              ))}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <QualDimensionCard dim={q!.policy} title="Policy posture" tierColors={POLICY_TEXT} />
            <QualDimensionCard dim={q!.moat} title="Competitive moat" tierColors={MOAT_TEXT} />
          </div>
        </>
      )}
    </section>
  );
}

function QualDimensionCard({ dim, title, tierColors }: {
  dim: HK50QualDimension | null | undefined;
  title: string;
  tierColors: Record<string, string>;
}) {
  if (!dim) {
    return (
      <div className="rounded border border-border bg-muted/20 p-2">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</div>
        <div className="text-xs text-muted-foreground mt-1">Not scored</div>
      </div>
    );
  }
  const indicators = Object.values(dim.indicators ?? {});
  return (
    <div className="rounded border border-border bg-muted/20 p-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</span>
        <span className={`text-xs font-bold ${tierColors[dim.tier] ?? 'text-foreground'}`}>{dim.tier}</span>
      </div>
      <div className="flex items-center flex-wrap gap-x-2 mt-1 text-[10px] text-muted-foreground">
        <span>score {dim.score_0_100 == null ? '—' : dim.score_0_100.toFixed(0)}/100</span>
        <span>· {dim.n_scored} scored</span>
        <span>· conf {(dim.mean_confidence * 100).toFixed(0)}%</span>
      </div>
      {dim.summary && <p className="text-[10px] text-foreground/80 mt-1">{dim.summary}</p>}
      {indicators.length > 0 && (
        <div className="mt-2 space-y-1">
          {indicators.map((ind) => (
            <div key={ind.indicator} className="text-[10px] border-t border-border/40 pt-1">
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground truncate" title={ind.indicator}>{ind.indicator}</span>
                <span className="font-mono text-foreground flex-shrink-0">
                  {ind.score}/5 · {(ind.confidence * 100).toFixed(0)}%
                </span>
              </div>
              {ind.summary && <div className="text-foreground/70 mt-0.5">{ind.summary}</div>}
            </div>
          ))}
        </div>
      )}
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
              <span className="text-[10px] text-muted-foreground/70 uppercase">{mr?.source ?? 'missing'}</span>
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
