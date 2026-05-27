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
  getComplacencyCohort, refreshComplacency, scoreComplacencyTickerAdhoc,
  type ComplacencyCohort, type ComplacencyTickerResult, type ComplacencyVerdict,
  type QualitativeAssessment, type QualConvictionLabel,
} from '@/lib/api';
import { ArrowLeft, RefreshCw, Loader2, AlertTriangle, X, Search, Plus } from 'lucide-react';
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

// Light-mode: text is BLACK (per user request) so it stays legible on the
// lightly-tinted chip backgrounds. Dark-mode keeps the existing pastel hues.
const VERDICT_COLOR: Record<ComplacencyVerdict, string> = {
  'Strong-Short': 'bg-red-600/30 text-black dark:text-red-200 border-red-700/40',
  'Watch':        'bg-orange-600/30 text-black dark:text-orange-200 border-orange-700/40',
  'Borderline':   'bg-amber-600/30 text-black dark:text-amber-200 border-amber-700/40',
  'Pass':         'bg-emerald-600/20 text-black dark:text-emerald-300 border-emerald-700/40',
  'N/A':          'bg-muted text-muted-foreground border-border',
};

function pillarColor(score: number): string {
  if (score >= 2) return 'bg-red-600/30 text-black dark:text-red-200';
  if (score >= 1) return 'bg-amber-600/30 text-black dark:text-amber-200';
  return 'bg-muted text-muted-foreground';
}

// Aggregate-score chip color: rank by total 0-100. Used in the new table
// column AND the drawer header. Dark mode keeps current vibrancy.
function aggregateColor(score: number | null | undefined): string {
  if (score == null) return 'bg-muted text-muted-foreground';
  if (score >= 70) return 'bg-red-600/30 text-black dark:text-red-200';
  if (score >= 50) return 'bg-orange-600/30 text-black dark:text-orange-200';
  if (score >= 30) return 'bg-amber-600/30 text-black dark:text-amber-200';
  return 'bg-emerald-600/20 text-black dark:text-emerald-300';
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
  // Ad-hoc scored tickers — appended to the displayed table but NOT persisted.
  const [adhoc, setAdhoc] = useState<ComplacencyTickerResult[]>([]);
  const [scoringAdhoc, setScoringAdhoc] = useState(false);

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

    // Proactively request browser-notification permission so the completion
    // alert can fire even if the user has switched tabs/apps. Safe no-op on
    // browsers that don't support Notification (e.g. iOS Safari < 16.4
    // outside PWA mode).
    if (typeof window !== 'undefined' && 'Notification' in window) {
      try {
        if (Notification.permission === 'default') {
          await Notification.requestPermission();
        }
      } catch {
        // ignore — permission can be denied or unavailable
      }
    }

    // Persistent loading toast for the entire two-phase refresh.
    // Phase 1 (quant for all 50): ~30-60s. Phase 2 (qual for gate
    // passers, sequential): 5-15 min depending on how many gate
    // passers there are and whether their qual cache is fresh.
    const toastId = toast.loading(
      'Refresh In Progress. This will take 1-15 mins.',
      {
        duration: Infinity,
        style: {
          fontSize: '15px',
          padding: '16px 20px',
          fontWeight: 500,
        },
      },
    );

    // Live cohort reload during Phase 2 so the table picks up incremental
    // qualitative indicator landings on each gate ticker. Without this, the
    // user would see the table update only after Phase 2 fully completes.
    let phase1DoneObserved = false;
    let lastReload = 0;
    const reloadCohortDebounced = async () => {
      const now = Date.now();
      if (now - lastReload < 5000) return;  // 5s debounce
      lastReload = now;
      try { await load(); } catch { /* swallow — toast still shows */ }
    };

    try {
      await refreshComplacency({
        maxWorkers: 4,
        onProgress: (s) => {
          if (s.progress_msg) {
            toast.loading(
              `Refresh · ${s.progress_msg}`,
              {
                id: toastId,
                duration: Infinity,
                style: { fontSize: '15px', padding: '16px 20px', fontWeight: 500 },
              },
            );
          }
          // Phase 1 → Phase 2 transition: cohort has just been saved with
          // all-50 quant data. Trigger an immediate reload so the table
          // refreshes with the new verdicts/composites/aggregates BEFORE
          // Phase 2 (qualitative) starts.
          if (!phase1DoneObserved && s.progress_msg?.includes('phase 1 done')) {
            phase1DoneObserved = true;
            void reloadCohortDebounced();
          }
          // Phase 2 indicator landings → debounced cohort reload so the
          // gate ticker rows pick up new qualitative state.
          if (s.progress_msg?.includes('phase 2:') || s.progress_msg?.includes('qual ')) {
            void reloadCohortDebounced();
          }
        },
      });
      await load();
      toast.dismiss(toastId);
      toast.success('Refresh of Complacency Detector Completed', {
        duration: 8000,
        style: {
          fontSize: '15px',
          padding: '16px 20px',
          fontWeight: 600,
        },
      });
      // Push notification — fires even if the user has switched away from
      // the tab. Requires permission granted above.
      if (
        typeof window !== 'undefined'
        && 'Notification' in window
        && Notification.permission === 'granted'
      ) {
        try {
          new Notification('Complacency Detector', {
            body: 'Refresh of Complacency Detector Completed',
            icon: '/favicon.ico',
            tag: 'complacency-refresh',  // collapses duplicate notifications
          });
        } catch {
          // ignore — some browsers throw if not in a secure context
        }
      }
    } catch (e) {
      toast.dismiss(toastId);
      toast.error((e as Error).message, {
        duration: 10000,
        style: { fontSize: '15px', padding: '16px 20px' },
      });
    } finally {
      setRefreshing(false);
    }
  };

  // Merge cohort results with ad-hoc scored tickers; ad-hoc overrides cohort
  // for the same ticker (so re-scoring a cohort name in the search bar shows
  // the fresh values).
  const allResults = useMemo(() => {
    const cohortRows = cohort?.results ?? [];
    if (adhoc.length === 0) return cohortRows;
    const adhocTickers = new Set(adhoc.map((r) => r.ticker));
    return [...cohortRows.filter((r) => !adhocTickers.has(r.ticker)), ...adhoc];
  }, [cohort, adhoc]);

  const rows = useMemo(() => {
    const asc = RANK_DEFS.find((r) => r.id === rankBy)?.asc ?? false;
    const q = search.trim().toUpperCase();

    return allResults
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
  }, [allResults, rankBy, verdictFilter, search]);

  const activeRank = RANK_DEFS.find((r) => r.id === rankBy)!;

  // "Score X" handler — fires when the search query matches no row but looks
  // like a valid ticker symbol (1-6 alphabetic chars, all upper or auto-cased).
  const trimmedQuery = search.trim().toUpperCase();
  const looksLikeTicker = /^[A-Z]{1,6}$/.test(trimmedQuery);
  const noMatches = rows.length === 0 && trimmedQuery.length > 0;
  const showScoreButton = noMatches && looksLikeTicker;

  const handleScoreAdhoc = async () => {
    if (!trimmedQuery) return;
    setScoringAdhoc(true);
    toast.info(`Scoring ${trimmedQuery} ad-hoc — ~10-15 sec.`);
    try {
      const result = await scoreComplacencyTickerAdhoc(trimmedQuery);
      // Replace existing adhoc entry for this ticker if any
      setAdhoc((prev) => [
        ...prev.filter((r) => r.ticker !== result.ticker),
        result,
      ]);
      // Open the drawer immediately so user sees the full breakdown
      setSelected(result);
      toast.success(`${result.ticker}: ${result.verdict} (${result.composite.toFixed(1)}/8)`);
    } catch (e) {
      toast.error(`Could not score ${trimmedQuery}: ${(e as Error).message}`);
    } finally {
      setScoringAdhoc(false);
    }
  };

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
        <p className="text-xs text-muted-foreground mb-1 ml-7">
          Ackman 4-pillar equity screen · Valuation / Behavioural / Technical / Quality · gate ≥ 6/8 & all pillars ≥ 1
        </p>
        <p className="text-[10px] text-muted-foreground/70 mb-4 ml-7">
          Valuation pillar uses live S&P 500 sector medians (cached weekly). Hover EV/S column for per-row comparison.
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
              placeholder="Filter / type a ticker to score ad-hoc"
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
          <div className="p-4 text-center border border-dashed border-border rounded-md">
            <p className="text-xs text-muted-foreground mb-3">
              No tickers in the cohort match your search.
            </p>
            {showScoreButton ? (
              <button
                onClick={handleScoreAdhoc}
                disabled={scoringAdhoc}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
              >
                {scoringAdhoc ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Plus size={14} />
                )}
                {scoringAdhoc ? `Scoring ${trimmedQuery}…` : `Score ${trimmedQuery} on-the-fly`}
              </button>
            ) : (
              <p className="text-[11px] text-muted-foreground/70">
                Type a valid US ticker (1-6 letters) to score it ad-hoc.
              </p>
            )}
          </div>
        )}

        {!loading && cohort && rows.length > 0 && (
          <div className="overflow-x-auto border border-border rounded-md bg-card">
            <table className="w-full text-xs font-mono">
              <thead className="bg-muted/40 text-left text-muted-foreground">
                {/* Pillar grouping band — visually segments downstream columns.
                    Light-mode: deepened text hues so the band labels stay legible
                    on white. Dark-mode keeps the pastel softness via `dark:`. */}
                <tr className="border-b border-border/50">
                  <th colSpan={5} className="px-2 py-1"></th>
                  <th colSpan={1} className="px-2 py-1 text-center text-[9px] uppercase tracking-wider font-bold bg-violet-500/15 text-violet-900 dark:text-violet-300">
                    Aggregate
                  </th>
                  <th colSpan={4} className="px-2 py-1 text-center text-[9px] uppercase tracking-wider font-bold bg-muted/60 text-foreground border-l border-r border-border/30">
                    Pillar scores
                  </th>
                  <th colSpan={2} className="px-2 py-1 text-center text-[9px] uppercase tracking-wider font-bold bg-rose-500/15 text-rose-900 dark:text-rose-300">
                    Valuation
                  </th>
                  <th colSpan={1} className="px-2 py-1 text-center text-[9px] uppercase tracking-wider font-bold bg-orange-500/15 text-orange-900 dark:text-orange-300">
                    Behav.
                  </th>
                  <th colSpan={2} className="px-2 py-1 text-center text-[9px] uppercase tracking-wider font-bold bg-cyan-500/15 text-cyan-900 dark:text-cyan-300">
                    Technical
                  </th>
                  <th colSpan={2} className="px-2 py-1 text-center text-[9px] uppercase tracking-wider font-bold bg-emerald-500/15 text-emerald-900 dark:text-emerald-300">
                    Quality
                  </th>
                  <th colSpan={1} className="px-2 py-1"></th>
                  <th colSpan={1} className="px-2 py-1 text-center text-[9px] uppercase tracking-wider font-bold bg-purple-500/15 text-purple-900 dark:text-purple-300">
                    Put rec
                  </th>
                </tr>
                <tr>
                  <th className="px-2 py-2 w-10 text-right">#</th>
                  <th className="px-2 py-2">Ticker</th>
                  <th className="px-2 py-2">Sector</th>
                  <th className="px-2 py-2">Verdict</th>
                  <th className="px-2 py-2 text-right bg-violet-500/5" title="Aggregate 0-100 (quant 0-50 + qual 0-50)">Agg/100</th>
                  <th className="px-2 py-2 text-right">Comp/8</th>
                  <th className="px-2 py-2 text-right border-l border-border/30" title="Valuation (EV/S, FCF yield)">V</th>
                  <th className="px-2 py-2 text-right" title="Behavioural (insider A/D, EPS rev, range)">B</th>
                  <th className="px-2 py-2 text-right" title="Technical (200DMA ext, weekly RSI)">T</th>
                  <th className="px-2 py-2 text-right border-r border-border/30" title="Quality (Altman Z, Piotroski)">Q</th>
                  <th className="px-2 py-2 text-right" title="EV/Sales TTM">EV/S</th>
                  <th className="px-2 py-2 text-right" title="FCF Yield TTM">FCF Y</th>
                  <th className="px-2 py-2 text-right" title="Insider Acquired ÷ (Acquired + Disposed) 4Q avg. 0 = all sales (bearish), 1 = all buys (bullish), null = no transactions in window">A/D</th>
                  <th className="px-2 py-2 text-right" title="Weekly RSI(14)">RSI</th>
                  <th className="px-2 py-2 text-right" title="% above 200-day SMA">200x</th>
                  <th className="px-2 py-2 text-right" title="Altman Z-Score">Alt Z</th>
                  <th className="px-2 py-2 text-right" title="Piotroski (0-9)">Pio</th>
                  <th className="px-2 py-2 text-right">Price</th>
                  <th className="px-2 py-2 bg-purple-500/5" title="Put recommendation (Strong-Short / Watch only)">Strike · Exp</th>
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
                      <div className="font-bold text-foreground flex items-center gap-1">
                        {r.ticker}
                        {adhoc.some((a) => a.ticker === r.ticker) && (
                          <span
                            className="text-[8px] uppercase tracking-wider px-1 py-0.5 rounded bg-amber-500/20 text-amber-300 font-semibold"
                            title="Ad-hoc — scored on-the-fly, not part of the persisted cohort"
                          >
                            ad-hoc
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground truncate max-w-[110px]">{r.name}</div>
                    </td>
                    <td className="px-2 py-1.5 text-[10px] text-muted-foreground truncate max-w-[120px]">{r.sector ?? '—'}</td>
                    <td className="px-2 py-1.5">
                      <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${VERDICT_COLOR[r.verdict]}`}>
                        {r.verdict}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right bg-violet-500/5">
                      {r.aggregate_score != null ? (
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${aggregateColor(r.aggregate_score)}`}
                          title={[
                            `Aggregate ${r.aggregate_score.toFixed(1)} / 100`,
                            `quant ${(r.aggregate_quant_pts ?? 0).toFixed(1)} + qual ${(r.aggregate_qual_pts ?? 0).toFixed(1)}`,
                            (r.aggregate_qual_pts ?? 0) === 0 ? '(qual not yet scored — click row to generate)' : null,
                          ].filter(Boolean).join('\n')}
                        >
                          {r.aggregate_score.toFixed(0)}
                        </span>
                      ) : (
                        <span className="text-muted-foreground/50 italic">—</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <span className="font-bold text-foreground">{r.composite.toFixed(1)}</span>
                    </td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.val_score)}`}>{r.val_score}</span></td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.beh_score)}`}>{r.beh_score}</span></td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.tech_score)}`}>{r.tech_score}</span></td>
                    <td className="px-2 py-1.5 text-right"><span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${pillarColor(r.qual_score)}`}>{r.qual_score}</span></td>
                    <td
                      className="px-2 py-1.5 text-right"
                      title={
                        r.ev_sales == null
                          ? 'EV/Sales TTM unavailable'
                          : [
                              `EV/Sales (TTM): ${r.ev_sales.toFixed(2)}×`,
                              r.ev_sales_sector_median != null
                                ? `Sector median (live S&P 500): ${r.ev_sales_sector_median.toFixed(2)}×`
                                : 'Sector median: not available',
                              r.ev_sales_relative != null
                                ? `→ ${r.ev_sales_relative.toFixed(2)}× sector median`
                                : null,
                            ].filter(Boolean).join('\n')
                      }
                    >
                      {r.ev_sales == null ? '—' : (
                        <>
                          {r.ev_sales.toFixed(1)}×
                          {r.ev_sales_relative != null && r.ev_sales_relative >= 2.5 && (
                            <span className="text-red-400 ml-1" title="≥ 2.5× sector median (strong flag)">▲</span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right">{fmtPct(r.fcf_yield_ttm)}</td>
                    <td
                      className="px-2 py-1.5 text-right"
                      title={
                        r.ad_ratio_4q_avg == null
                          ? 'No insider transactions in 12-month window'
                          : r.ad_ratio_4q_avg === 0
                            ? '0.00 = 100% insider sales, zero purchases (strong sell signal)'
                            : r.ad_ratio_4q_avg < 0.2
                              ? `${r.ad_ratio_4q_avg.toFixed(2)} = insiders mostly selling (< 0.20 strong flag)`
                              : `${r.ad_ratio_4q_avg.toFixed(2)} = insider activity`
                      }
                    >
                      {r.ad_ratio_4q_avg == null ? (
                        <span className="text-muted-foreground/50 italic">n/a</span>
                      ) : (
                        <>
                          {r.ad_ratio_4q_avg.toFixed(2)}
                          {r.ad_ratio_4q_avg < 0.20 && (
                            <span className="text-red-400 ml-1" title="< 0.20 = strong sell">▼</span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right">{r.rsi_weekly == null ? '—' : r.rsi_weekly.toFixed(0)}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPct(r.sma200_extension, 0)}</td>
                    <td className="px-2 py-1.5 text-right">{r.altman_z == null ? '—' : r.altman_z.toFixed(1)}</td>
                    <td className="px-2 py-1.5 text-right">{r.piotroski == null ? '—' : `${r.piotroski}/9`}</td>
                    <td className="px-2 py-1.5 text-right">{fmtPrice(r.price)}</td>
                    <td className="px-2 py-1.5 bg-purple-500/5">
                      {r.put_recommendation ? (
                        <span
                          className="text-purple-300 font-semibold"
                          title={[
                            `Strike  ${fmtPrice(r.put_recommendation.strike)}  (${(r.put_recommendation.strike_pct_otm * 100).toFixed(0)}% OTM)`,
                            `Expiry  ${r.put_recommendation.expiry}  (${r.put_recommendation.days_to_expiry} days)`,
                            r.put_recommendation.mid != null ? `Mid     ${fmtPrice(r.put_recommendation.mid)}` : null,
                            r.put_recommendation.implied_volatility != null ? `IV      ${(r.put_recommendation.implied_volatility * 100).toFixed(0)}%` : null,
                            r.put_recommendation.open_interest != null ? `OI      ${r.put_recommendation.open_interest.toLocaleString()}` : null,
                          ].filter(Boolean).join('\n')}
                        >
                          ${r.put_recommendation.strike.toFixed(0)}P · {r.put_recommendation.expiry.slice(5)}
                        </span>
                      ) : (
                        (r.verdict === 'Strong-Short' || r.verdict === 'Watch') ? (
                          <span className="text-muted-foreground/60 italic text-[10px]" title="No liquid put in target tenor band">illiquid</span>
                        ) : (
                          <span className="text-muted-foreground/40">—</span>
                        )
                      )}
                    </td>
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
        <ComplacencyDrawer
          ticker={selected}
          onClose={() => setSelected(null)}
          onQualGenerated={(updated) => {
            // Replace the row in adhoc OR re-bind the cohort row in-place so
            // both the table and the open drawer reflect the new aggregate.
            setSelected(updated);
            setAdhoc((prev) => {
              const others = prev.filter((r) => r.ticker !== updated.ticker);
              // If the ticker was in the cohort, the backend has persisted
              // the patched row server-side — refresh the cohort cache too.
              return [...others, updated];
            });
            // If this ticker is in the persisted cohort, the server already
            // patched it. Re-fetch the cohort so a hard reload would also
            // show the same numbers, and so other rows pick up the change.
            const inCohort = (cohort?.results ?? []).some(
              (r) => r.ticker === updated.ticker,
            );
            if (inCohort) {
              load();
            }
          }}
        />
      )}
    </div>
  );
}


// ─── Detail drawer ─────────────────────────────────────────────────────────

function ComplacencyDrawer({
  ticker,
  onClose,
  onQualGenerated,
}: {
  ticker: ComplacencyTickerResult;
  onClose: () => void;
  onQualGenerated?: (updated: ComplacencyTickerResult) => void;
}) {
  const [generatingQual, setGeneratingQual] = useState(false);

  const isGate = ticker.verdict === 'Strong-Short' || ticker.verdict === 'Watch';
  const hasQual = !!ticker.qualitative
    && Object.keys(ticker.qualitative.indicators ?? {}).length > 0;

  const handleGenerateQual = async () => {
    if (generatingQual) return;
    setGeneratingQual(true);

    // Proactively request notification permission so completion alert can
    // fire even if user has switched apps during the 4-10 min wait.
    if (typeof window !== 'undefined' && 'Notification' in window) {
      try {
        if (Notification.permission === 'default') await Notification.requestPermission();
      } catch { /* ignore */ }
    }

    // Persistent loading toast — replaces the disappearing toast.info that
    // made users think nothing was happening during the long backend run.
    const toastId = toast.loading(
      `Generating Qualitative for ${ticker.ticker}. This will take 4-10 mins.`,
      {
        duration: Infinity,
        style: { fontSize: '15px', padding: '16px 20px', fontWeight: 500 },
      },
    );

    try {
      const updated = await scoreComplacencyTickerAdhoc(ticker.ticker, {
        forceQual: true,
        onProgress: (s) => {
          // Live job progress from backend two-phase worker.
          // Toast text reflects current phase:
          //   "phase 1: quant scoring NVDA"
          //   "phase 1 done · NVDA Strong-Short · composite 8.0/8 · agg 50/100"
          //   "phase 2: qual NVDA 3/10 · A2_catalyst_proximity = 4/5 conf 75%"
          //   "...elapsed Xm Ys"
          if (s.progress_msg) {
            toast.loading(
              `Re-score · ${s.progress_msg}`,
              {
                id: toastId, duration: Infinity,
                style: { fontSize: '15px', padding: '16px 20px', fontWeight: 500 },
              },
            );
          }
          // CRITICAL — live drawer updates:
          // The backend publishes partial results via update_running_result()
          // after Phase 1 (quant) completes AND after each Phase 2 indicator
          // lands. Pushing those into the drawer here makes the qualitative
          // panel + aggregate score tick up in real time instead of waiting
          // 5-10 min for the final completion.
          if (s.result && typeof s.result === 'object') {
            const partial = s.result as ComplacencyTickerResult & { _persisted_to_cohort?: boolean };
            // Only push if it has the shape we expect (a ticker row, not a job summary)
            if (partial.ticker && partial.composite != null) {
              onQualGenerated?.(partial);
            }
          }
        },
      });
      toast.dismiss(toastId);

      // Three outcomes possible from the two-phase backend:
      //   A) PURE SUCCESS — all 10 indicators scored, no backend error
      //   B) PARTIAL — quant done, qualitative incomplete (e.g. 7/10).
      //              Show informational toast + hint that next click only
      //              re-runs failed indicators (idempotent restart).
      //   C) FAILURE — no qualitative scored at all (e.g. API key missing,
      //              every Qwen call failed).
      const backendError = (updated as { error?: string | null }).error;
      const indicatorCount = Object.keys(updated.qualitative?.indicators ?? {}).length;
      const expectedTotal = updated.qualitative?.max_possible
        ? Math.round(updated.qualitative.max_possible / 5)
        : 10;
      const isIncomplete = updated.qualitative?.incomplete ?? false;
      const isPartial = indicatorCount > 0 && (isIncomplete || indicatorCount < expectedTotal);

      if (indicatorCount === 0) {
        // C — failure
        const reason = backendError
          || 'no qualitative indicators were scored — see /research/diag/llm-health';
        toast.error(
          `${updated.ticker} re-score: qualitative did not produce results. ${reason}`,
          {
            duration: 15000,
            style: { fontSize: '15px', padding: '16px 20px', fontWeight: 500 },
          },
        );
      } else if (isPartial) {
        // B — partial success, hint idempotent restart
        const qualSummary = `${updated.qualitative!.composite}/${updated.qualitative!.max_possible} qual`;
        const aggSummary = updated.aggregate_score != null
          ? `agg ${updated.aggregate_score.toFixed(1)}/100`
          : 'agg —';
        toast(
          `${updated.ticker} partial · ${indicatorCount}/${expectedTotal} indicators · `
          + `${qualSummary} · ${aggSummary}\n`
          + `Click Re-score again — only the ${expectedTotal - indicatorCount} unfinished `
          + `indicators will re-run (high-conf ones preserved).`,
          {
            duration: 18000,
            style: {
              fontSize: '15px', padding: '16px 20px', fontWeight: 500,
              background: 'rgb(252 211 77 / 0.15)',
              border: '1px solid rgb(217 119 6 / 0.5)',
            },
            icon: '⚠',
          },
        );
      } else {
        // A — pure success
        const qualSummary = `${updated.qualitative!.composite}/${updated.qualitative!.max_possible} qual`;
        const aggSummary = updated.aggregate_score != null
          ? `agg ${updated.aggregate_score.toFixed(1)}/100`
          : 'agg —';
        toast.success(
          `${updated.ticker} re-scored: ${qualSummary} · ${aggSummary}`,
          {
            duration: 10000,
            style: { fontSize: '15px', padding: '16px 20px', fontWeight: 600 },
          },
        );
      }
      // Browser push notification (fires even if user switched apps)
      if (typeof window !== 'undefined' && 'Notification' in window
          && Notification.permission === 'granted') {
        try {
          new Notification('Complacency Detector', {
            body: `${updated.ticker} qualitative re-score complete · aggregate ${updated.aggregate_score?.toFixed(0) ?? '—'}/100`,
            icon: '/favicon.ico',
            tag: `complacency-qual-${updated.ticker}`,
          });
        } catch { /* ignore */ }
      }
      onQualGenerated?.(updated);
    } catch (e) {
      toast.dismiss(toastId);
      toast.error(`Qual generation failed for ${ticker.ticker}: ${(e as Error).message}`, {
        duration: 12000,
        style: { fontSize: '15px', padding: '16px 20px' },
      });
    } finally {
      setGeneratingQual(false);
    }
  };

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
                {ticker.qualitative && Object.keys(ticker.qualitative.indicators).length > 0 && (
                  <span
                    className={`px-1.5 py-0.5 rounded border text-[10px] font-bold uppercase tracking-wider ${CONVICTION_COLOR[ticker.qualitative.conviction_label]}`}
                    title={`Qual conviction · ${ticker.qualitative.composite}/${ticker.qualitative.max_possible}`}
                  >
                    {ticker.qualitative.conviction_label}
                  </span>
                )}
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
          {/* ════════════════════════════════════════════════════════════════
              1. QUANTITATIVE SCORE  (initial 4-pillar Ackman scoring)
             ════════════════════════════════════════════════════════════════ */}
          <section className="border border-cyan-500/20 rounded-md bg-cyan-500/5 p-3">
            <div className="flex items-baseline justify-between mb-2">
              <h2 className="text-xs font-bold uppercase tracking-wider text-cyan-300">
                1 · Quantitative Score
              </h2>
              <span className="text-[10px] font-mono font-bold text-foreground">
                {ticker.composite.toFixed(1)} / 8
                {ticker.passes_gate && (
                  <span className="ml-2 px-1.5 py-0.5 rounded bg-cyan-500/30 text-cyan-200 text-[9px] uppercase">
                    Gate passed
                  </span>
                )}
              </span>
            </div>
            <p className="text-[10px] text-muted-foreground italic mb-2">
              Initial Ackman 4-pillar scoring against FMP fundamentals + live S&P 500 sector medians.
            </p>
            <div className="grid grid-cols-4 gap-2 text-[11px]">
              <Pillar
                label="Valuation"
                score={ticker.val_score}
                inputs={[
                  ticker.ev_sales_relative != null ? `EV/S ${ticker.ev_sales_relative.toFixed(2)}× sector` : null,
                  ticker.fcf_yield_ttm != null ? `FCF Y ${(ticker.fcf_yield_ttm * 100).toFixed(2)}%` : null,
                ]}
              />
              <Pillar
                label="Behavioural"
                score={ticker.beh_score}
                inputs={[
                  ticker.ad_ratio_4q_avg != null ? `A/D ${ticker.ad_ratio_4q_avg.toFixed(2)}` : 'A/D n/a',
                  ticker.range_position != null ? `range pos ${(ticker.range_position * 100).toFixed(0)}%` : null,
                  ticker.eps_revision_yoy != null ? `EPS rev ${(ticker.eps_revision_yoy * 100).toFixed(0)}%` : null,
                ]}
              />
              <Pillar
                label="Technical"
                score={ticker.tech_score}
                inputs={[
                  ticker.rsi_weekly != null ? `RSI ${ticker.rsi_weekly.toFixed(0)}` : null,
                  ticker.sma200_extension != null ? `200x ${(ticker.sma200_extension * 100).toFixed(0)}%` : null,
                ]}
              />
              <Pillar
                label="Quality"
                score={ticker.qual_score}
                inputs={[
                  ticker.altman_z != null ? `Z ${ticker.altman_z.toFixed(1)}` : null,
                  ticker.piotroski != null ? `Pio ${ticker.piotroski}/9` : null,
                ]}
              />
            </div>
          </section>

          {/* ════════════════════════════════════════════════════════════════
              2. QUALITATIVE THESIS  (LLM-scored rationale)
             ════════════════════════════════════════════════════════════════ */}
          {hasQual && (
            <div className="space-y-2">
              <QualitativePanel qual={ticker.qualitative!} headerPrefix="2 · " />
              {/* Re-score button — for gate-passers whose qual is already
                  populated, allows forcing a fresh deep-research run.
                  Without this, cached high-conf scores never re-trigger the
                  deep-research path on subsequent refreshes. */}
              <div className="flex items-center justify-end gap-2 px-3">
                <span className="text-[10px] text-muted-foreground italic">
                  Cached {ticker.qualitative!.indicators &&
                    Object.values(ticker.qualitative!.indicators).filter(
                      (i) => (i.model_used || '').includes('deep'),
                    ).length}/{Object.keys(ticker.qualitative!.indicators).length} via deep-research.
                </span>
                <button
                  onClick={handleGenerateQual}
                  disabled={generatingQual}
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-border bg-card text-foreground text-[10px] font-medium hover:bg-muted disabled:opacity-50"
                  title="Force a fresh qualitative scoring run, invalidating the per-indicator cache. Re-fires evidence gathering (incl. earnings Q&A) and deep-research escalation for low-confidence indicators. Costs ~$0.05-0.10 in Qwen calls and ~4-5 min."
                >
                  {generatingQual ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
                  {generatingQual ? 'Re-scoring…' : 'Re-score (force fresh)'}
                </button>
              </div>
            </div>
          )}
          {!hasQual && (
            <section
              className={`border border-dashed rounded-md p-3 text-xs italic ${
                isGate ? 'border-border text-muted-foreground' : 'border-border/50 text-muted-foreground/70'
              }`}
            >
              <div className="flex items-start justify-between gap-3 flex-wrap not-italic">
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-semibold mb-1">2 · Qualitative Thesis</div>
                  <p className="text-[11px] italic leading-snug">
                    {isGate
                      ? <>Not yet scored. The agent runs qwen3.6-plus on 10-K, 10-Q diff, 8-K, DEF 14A, Form 144 and Tavily evidence. This run may have been cached without it or the Qwen key (<code className="text-[10px]">DEEP_RESEARCH_API_KEY</code>) may not be set.</>
                      : <>Not auto-generated for {ticker.verdict.toLowerCase()} verdicts (auto-qual only fires for Strong-Short / Watch). You can still trigger it manually — the aggregate score will be recomputed.</>
                    }
                  </p>
                </div>
                <button
                  onClick={handleGenerateQual}
                  disabled={generatingQual}
                  className="flex-shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-primary text-primary-foreground text-[11px] font-medium hover:opacity-90 disabled:opacity-50 not-italic"
                  title="Run the 10-indicator qualitative scorer on this ticker. Costs ~$0.02 in Qwen calls and ~4-5 min wall clock."
                >
                  {generatingQual ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                  {generatingQual ? 'Generating…' : 'Generate Qualitative'}
                </button>
              </div>
            </section>
          )}

          {/* ════════════════════════════════════════════════════════════════
              3. AGGREGATE SCORE  (quant + qual combined 0-100)
             ════════════════════════════════════════════════════════════════ */}
          {ticker.aggregate_score != null && (
            <AggregateScorePanel
              total={ticker.aggregate_score}
              quantPts={ticker.aggregate_quant_pts ?? 0}
              qualPts={ticker.aggregate_qual_pts ?? 0}
              convictionLabel={ticker.qualitative?.conviction_label ?? null}
              hasQual={!!ticker.qualitative && Object.keys(ticker.qualitative.indicators ?? {}).length > 0}
            />
          )}

          {/* Put recommendation (Strong-Short / Watch only — actionable layer) */}
          {ticker.put_recommendation && (
            <section className="border border-purple-500/30 rounded-md bg-purple-500/5 p-3">
              <div className="flex items-baseline justify-between mb-2">
                <h2 className="text-xs font-bold uppercase tracking-wider text-purple-300">Put Recommendation</h2>
                <span className="text-[9px] text-muted-foreground italic">v1 · yfinance · no IV percentile</span>
              </div>
              <div className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
                <FragmentRow row={{ label: 'Strike',  value: `${fmtPrice(ticker.put_recommendation.strike)}  (${(ticker.put_recommendation.strike_pct_otm * 100).toFixed(1)}% OTM)`, highlight: true }} />
                <FragmentRow row={{ label: 'Expiry',  value: `${ticker.put_recommendation.expiry}  (${ticker.put_recommendation.days_to_expiry} days)` }} />
                <FragmentRow row={{ label: 'Mid',     value: fmtPrice(ticker.put_recommendation.mid) }} />
                <FragmentRow row={{ label: 'Bid / Ask',  value: `${fmtPrice(ticker.put_recommendation.bid)} / ${fmtPrice(ticker.put_recommendation.ask)}` }} />
                <FragmentRow row={{ label: 'Implied volatility', value: ticker.put_recommendation.implied_volatility != null ? `${(ticker.put_recommendation.implied_volatility * 100).toFixed(1)}%` : '—' }} />
                <FragmentRow row={{ label: 'Open interest',      value: ticker.put_recommendation.open_interest != null ? ticker.put_recommendation.open_interest.toLocaleString() : '—' }} />
                <FragmentRow row={{ label: 'Volume today',       value: ticker.put_recommendation.volume != null ? ticker.put_recommendation.volume.toLocaleString() : '—' }} />
              </div>
              {ticker.put_recommendation.contract_symbol && (
                <div className="mt-2 pt-2 border-t border-purple-500/20 font-mono text-[10px] text-muted-foreground">
                  OCC: <span className="text-foreground">{ticker.put_recommendation.contract_symbol}</span>
                </div>
              )}
              <p className="mt-2 text-[11px] text-foreground/80 leading-relaxed italic">
                {ticker.put_recommendation.rationale}
              </p>
              <p className="mt-2 text-[9px] text-amber-500/70">
                ⚠ Mechanical recommendation only. US options premiums are subject to 30% withholding for non-US residents.
                Verify chain, liquidity, and earnings calendar before trading.
              </p>
            </section>
          )}

          {(ticker.verdict === 'Strong-Short' || ticker.verdict === 'Watch') && !ticker.put_recommendation && (
            <section className="border border-dashed border-border rounded-md p-3 text-xs text-muted-foreground italic">
              No liquid put contract found in the target tenor band. Chain may be illiquid or yfinance may be temporarily unavailable.
            </section>
          )}

          {/* Pillar inputs — grouped by pillar so it's obvious which input feeds which score */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Inputs by pillar</h2>

            <InputGroup
              title="Valuation"
              accent="rose"
              score={ticker.val_score}
              rows={[
                { label: "EV/Sales (TTM)",         value: ticker.ev_sales == null ? '—' : `${ticker.ev_sales.toFixed(2)}×`,
                  threshold: "abs > 25× strong · > 15× weak" },
                { label: "… vs S&P 500 sector median", value: ticker.ev_sales_relative == null ? '—' : `${ticker.ev_sales_relative.toFixed(2)}×`,
                  threshold: "> 2.5× sector strong · > 1.5× weak" },
                { label: "Sector median (live)",    value: ticker.ev_sales_sector_median == null ? '—' : `${ticker.ev_sales_sector_median.toFixed(2)}×` },
                { label: "FCF Yield (TTM)",         value: fmtPct(ticker.fcf_yield_ttm, 2),
                  threshold: "< -0.5% strong · < 1.5% weak", highlight: true },
              ]}
            />

            <InputGroup
              title="Behavioural"
              accent="orange"
              score={ticker.beh_score}
              rows={[
                { label: "Insider A/D (4Q avg)",   value: fmtNum(ticker.ad_ratio_4q_avg, 2),
                  threshold: "< 0.20 strong · < 0.35 weak  (0 = all sales)" },
                { label: "52-week range position", value: fmtPct(ticker.range_position, 0),
                  threshold: "> 90% combined with EPS rev < 0 → strong" },
                { label: "EPS revision (Y/Y)",     value: fmtPct(ticker.eps_revision_yoy) },
              ]}
            />

            <InputGroup
              title="Technical"
              accent="cyan"
              score={ticker.tech_score}
              rows={[
                { label: "% above 200DMA",         value: fmtPct(ticker.sma200_extension, 0),
                  threshold: "> 40% strong · > 20% weak" },
                { label: "Weekly RSI(14)",         value: ticker.rsi_weekly == null ? '—' : ticker.rsi_weekly.toFixed(0),
                  threshold: "> 75 strong · > 65 weak" },
              ]}
            />

            <InputGroup
              title="Quality"
              accent="emerald"
              score={ticker.qual_score}
              rows={[
                { label: "Altman Z",               value: ticker.altman_z == null ? '—' : ticker.altman_z.toFixed(2),
                  threshold: "< 1.81 distress · > 50 detached-mkt-cap = strong" },
                { label: "Piotroski",              value: ticker.piotroski == null ? '—' : `${ticker.piotroski}/9`,
                  threshold: "≤ 3 strong · ≤ 5 weak" },
              ]}
            />

            {/* Meta — not pillar-scored */}
            <div className="mt-3 pt-3 border-t border-border">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Meta</div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <KV label="Price"      value={fmtPrice(ticker.price)} />
                <KV label="Market cap" value={ticker.market_cap == null ? '—' : `$${(ticker.market_cap / 1e9).toFixed(1)}B`} />
              </div>
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

// ─── Qualitative panel (LLM-scored short thesis) ──────────────────────────

const CONVICTION_COLOR: Record<QualConvictionLabel, string> = {
  'EXCEPTIONAL': 'bg-red-600/30 text-black dark:text-red-200 border-red-700/40',
  'BOTH':        'bg-orange-600/30 text-black dark:text-orange-200 border-orange-700/40',
  'QUANT-ONLY':  'bg-amber-600/30 text-black dark:text-amber-200 border-amber-700/40',
  'QUAL-ONLY':   'bg-blue-600/30 text-black dark:text-blue-200 border-blue-700/40',
  'PASS':        'bg-muted text-muted-foreground border-border',
};

const CONVICTION_BLURB: Record<QualConvictionLabel, string> = {
  'EXCEPTIONAL': 'Both quant and qualitative pillars at max conviction.',
  'BOTH':        'Quant gate passed AND qualitative thesis confirms.',
  'QUANT-ONLY':  'Quant numbers say short but narrative is thin — could be value trap.',
  'QUAL-ONLY':   'Qualitative thesis bearish but quant hasn\'t caught up — too early.',
  'PASS':        'No qualitative thesis.',
};

const QUAL_INDICATOR_THEME: Record<string, { theme: string; label: string; accent: string }> = {
  // Theme A — Narrative fragility
  'A1_single_thesis_dependence':   { theme: 'A · Narrative',  label: 'Single-thesis dependence',  accent: 'rose' },
  'A2_catalyst_proximity':         { theme: 'A · Narrative',  label: 'Catalyst proximity',        accent: 'rose' },
  'A3_consensus_uniformity':       { theme: 'A · Narrative',  label: 'Consensus uniformity',      accent: 'rose' },
  // Theme B — Business-model fragility
  'B1_customer_concentration':     { theme: 'B · Biz model',  label: 'Customer concentration',    accent: 'orange' },
  'B2_competitive_disintermediation': { theme: 'B · Biz model', label: 'Competitive disintermediation', accent: 'orange' },
  'B3_pricing_power_erosion':      { theme: 'B · Biz model',  label: 'Pricing power erosion',     accent: 'orange' },
  // Theme C — Disclosure & accounting
  'C1_disclosure_deterioration':   { theme: 'C · Disclosure', label: 'Disclosure deterioration',  accent: 'amber' },
  'C2_accounting_aggressiveness':  { theme: 'C · Disclosure', label: 'Accounting aggressiveness', accent: 'amber' },
  // Theme D — Management & insider
  'D1_management_red_flags':       { theme: 'D · Mgmt',       label: 'Management red flags',      accent: 'red' },
  'D2_insider_behavior_depth':     { theme: 'D · Mgmt',       label: 'Insider behavior depth',    accent: 'red' },
};

function scoreChipColor(score: number): string {
  if (score >= 4) return 'bg-red-600/30 text-red-200';
  if (score >= 3) return 'bg-orange-600/30 text-orange-200';
  if (score >= 2) return 'bg-amber-600/30 text-amber-200';
  if (score >= 1) return 'bg-yellow-600/20 text-yellow-200';
  return 'bg-muted text-muted-foreground';
}

function QualitativePanel(
  { qual, headerPrefix = '' }:
  { qual: QualitativeAssessment; headerPrefix?: string }
) {
  const [expandedIndicator, setExpandedIndicator] = useState<string | null>(null);

  // Count of indicators that escalated to deep-research web search.
  // model_used == "qwen3.6-plus+deep_web_search" marks deep escalation;
  // == "qwen3.6-plus" marks first-pass only (which is correct when
  // first-pass conf already > 0.50 — deep research wasn't needed).
  const deepCount = Object.values(qual.indicators ?? {}).filter(
    (i) => (i.model_used || '').includes('deep'),
  ).length;
  const totalIndicators = Object.keys(qual.indicators ?? {}).length;

  return (
    <section className="border border-indigo-500/30 rounded-md bg-indigo-500/5 p-3">
      <div className="flex items-baseline justify-between mb-2 gap-2 flex-wrap">
        <h2 className="text-xs font-bold uppercase tracking-wider text-indigo-300">{headerPrefix}Qualitative Thesis</h2>
        <div className="flex items-center gap-1.5">
          {deepCount > 0 && (
            <span
              className="px-1.5 py-0.5 rounded bg-purple-500/30 text-purple-900 dark:text-purple-100 text-[9px] font-bold uppercase tracking-wider"
              title="Indicators that escalated to Qwen native web search after a low-confidence first-pass score."
            >
              ★ {deepCount}/{totalIndicators} deep-research
            </span>
          )}
          <span className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase tracking-wider ${CONVICTION_COLOR[qual.conviction_label]}`}>
            {qual.conviction_label}
          </span>
        </div>
      </div>
      <p className="text-[10px] text-muted-foreground italic mb-3">{CONVICTION_BLURB[qual.conviction_label]}</p>

      {/* Composite bar */}
      <div className="mb-3">
        <div className="flex items-baseline justify-between text-xs mb-1">
          <span className="text-muted-foreground">Composite</span>
          <span className="font-mono font-bold text-foreground">
            {qual.composite} / {qual.max_possible}
            <span className="text-muted-foreground/60 ml-2">({(qual.composite_normalized * 100).toFixed(0)}%)</span>
          </span>
        </div>
        <div className="h-1.5 rounded overflow-hidden bg-muted/30">
          <div
            className="h-full bg-indigo-500/70"
            style={{ width: `${qual.composite_normalized * 100}%` }}
          />
        </div>
      </div>

      {/* Per-indicator rows (click to expand evidence) */}
      <div className="space-y-1.5">
        {Object.values(qual.indicators).map((s) => {
          const meta = QUAL_INDICATOR_THEME[s.indicator] ?? { theme: '?', label: s.indicator, accent: 'gray' };
          const expanded = expandedIndicator === s.indicator;
          return (
            <div key={s.indicator} className="text-xs">
              <button
                className="w-full flex items-baseline gap-2 px-2 py-1.5 rounded hover:bg-muted/30 text-left"
                onClick={() => setExpandedIndicator(expanded ? null : s.indicator)}
              >
                <span className="text-[9px] uppercase tracking-wider text-muted-foreground w-20 flex-shrink-0">{meta.theme}</span>
                <span className="text-foreground flex-1 truncate">{meta.label}</span>
                {(s.model_used || '').includes('deep') && (
                  <span
                    className="px-1 py-0.5 rounded bg-purple-500/25 text-purple-900 dark:text-purple-100 text-[8px] font-bold tracking-wider"
                    title="This indicator escalated to deep-research (Qwen native web search) after a low-confidence first-pass score."
                  >
                    ★ DEEP
                  </span>
                )}
                <span className="text-[10px] text-muted-foreground/60">
                  conf {(s.confidence * 100).toFixed(0)}%
                </span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold font-mono ${scoreChipColor(s.score)}`}>
                  {s.score}/5
                </span>
              </button>
              {expanded && (
                <div className="pl-22 pr-2 py-2 bg-muted/20 rounded mb-1">
                  <p className="text-[11px] italic text-foreground/80 mb-2">{s.summary}</p>
                  {s.evidence.length === 0 ? (
                    <p className="text-[10px] text-muted-foreground/60 italic">No evidence cited.</p>
                  ) : (
                    <ul className="space-y-2">
                      {s.evidence.map((e, i) => (
                        <li key={i} className="text-[10px] leading-relaxed">
                          <div className="text-indigo-300 font-semibold">
                            {e.source} {e.date && <span className="text-muted-foreground/60 font-normal">· {e.date}</span>}
                          </div>
                          <div className="text-foreground/80 italic pl-2 border-l border-indigo-500/30 mt-1">
                            “{e.quote}”
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {qual.incomplete && (
        <div className="mt-2 p-2 rounded border border-amber-500/30 bg-amber-500/10">
          <p className="text-[11px] text-amber-900 dark:text-amber-200 font-semibold not-italic">
            ⚠ Partial assessment — {Object.keys(qual.indicators).length}/{Math.round(qual.max_possible / 5)} indicators scored
          </p>
          <p className="text-[10px] text-amber-900/80 dark:text-amber-200/80 italic mt-0.5">
            Click <strong className="not-italic">Re-score (force fresh)</strong> below to fill the gaps —
            high-confidence indicators are preserved, only the unfinished ones re-run.
            Cost ≈ ${(0.01 * (Math.round(qual.max_possible / 5) - Object.keys(qual.indicators).length)).toFixed(2)} this time.
          </p>
        </div>
      )}
      <p className="mt-2 text-[9px] text-muted-foreground/60 italic">
        Scored by {Object.values(qual.indicators)[0]?.model_used ?? 'qwen3.6-plus'} on 10-K + transcripts + 90-day news.
        {qual.assessed_at && ` Assessed ${qual.assessed_at.slice(0, 10)}.`}
      </p>
    </section>
  );
}


// ─── Aggregate score panel (quant + qual combined 0-100) ─────────────────

function aggregateTier(score: number): { label: string; color: string; blurb: string } {
  if (score >= 75) return { label: 'EXCEPTIONAL', color: 'bg-red-600/30 text-red-200 border-red-700/40',          blurb: 'Both quant and qualitative pillars at max conviction — textbook short setup' };
  if (score >= 55) return { label: 'HIGH',        color: 'bg-orange-600/30 text-orange-200 border-orange-700/40', blurb: 'Strong combined signal — actionable short candidate' };
  if (score >= 40) return { label: 'MODERATE',    color: 'bg-amber-600/30 text-amber-200 border-amber-700/40',    blurb: 'Mixed signal — track for confirmation before sizing' };
  if (score >= 25) return { label: 'WEAK',        color: 'bg-yellow-600/20 text-yellow-200 border-yellow-700/40', blurb: 'Limited setup — qualitative or quant pillar missing' };
  return { label: 'PASS', color: 'bg-emerald-600/20 text-emerald-300 border-emerald-700/40', blurb: 'No actionable short signal' };
}

function AggregateScorePanel(
  { total, quantPts, qualPts, convictionLabel, hasQual }:
  {
    total: number;
    quantPts: number;
    qualPts: number;
    convictionLabel: QualConvictionLabel | null;
    hasQual: boolean;
  }
) {
  const tier = aggregateTier(total);
  return (
    <section className="border border-emerald-500/30 rounded-md bg-gradient-to-br from-emerald-500/5 to-amber-500/5 p-3">
      <div className="flex items-baseline justify-between mb-2 gap-2 flex-wrap">
        <h2 className="text-xs font-bold uppercase tracking-wider text-foreground">
          3 · Aggregate Score
        </h2>
        <span className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase tracking-wider ${tier.color}`}>
          {tier.label}
        </span>
      </div>
      <p className="text-[10px] text-muted-foreground italic mb-3">
        Quant (0-50) + Qual (0-50) = combined 0-100 conviction.
      </p>

      {/* Big headline number */}
      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-3xl font-bold font-mono text-foreground">{total.toFixed(0)}</span>
        <span className="text-base font-mono text-muted-foreground">/ 100</span>
        {convictionLabel && hasQual && (
          <span className={`ml-auto px-1.5 py-0.5 rounded border text-[10px] font-bold uppercase tracking-wider ${CONVICTION_COLOR[convictionLabel]}`}>
            {convictionLabel}
          </span>
        )}
      </div>

      {/* Stacked bar showing quant vs qual contribution */}
      <div className="h-3 rounded overflow-hidden bg-muted/30 flex">
        <div
          className="h-full bg-cyan-500/70"
          style={{ width: `${quantPts}%` }}
          title={`Quant: ${quantPts.toFixed(1)} / 50`}
        />
        <div
          className="h-full bg-indigo-500/70"
          style={{ width: `${qualPts}%` }}
          title={`Qual: ${qualPts.toFixed(1)} / 50`}
        />
      </div>
      <div className="flex justify-between text-[10px] mt-1">
        <span className="text-cyan-300">
          ● Quant {quantPts.toFixed(1)} / 50
        </span>
        <span className="text-indigo-300">
          ● Qual {qualPts.toFixed(1)} / 50 {!hasQual && '(not scored)'}
        </span>
      </div>

      <p className="mt-2 text-[10px] text-foreground/80 italic">{tier.blurb}</p>
    </section>
  );
}


function Pillar(
  { label, score, inputs = [] }:
  { label: string; score: number; inputs?: (string | null)[] }
) {
  const liveInputs = inputs.filter((x): x is string => Boolean(x));
  return (
    <div className="p-2 rounded border border-border bg-muted/30">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`mt-1 inline-block px-2 py-0.5 rounded text-xs font-bold ${pillarColor(score)}`}>
        {score} / 2
      </div>
      {liveInputs.length > 0 && (
        <div className="mt-1 space-y-0.5">
          {liveInputs.map((line, i) => (
            <div key={i} className="text-[9px] text-muted-foreground leading-tight">{line}</div>
          ))}
        </div>
      )}
      {liveInputs.length === 0 && score === 0 && (
        <div className="text-[9px] text-muted-foreground/60 italic mt-1">no inputs returned</div>
      )}
    </div>
  );
}


// Pillar-grouped Inputs section in the drawer. Each group shows its score
// chip, then the rows that feed it with their threshold legend, so it's
// obvious which input drives which score.
type Accent = 'rose' | 'orange' | 'cyan' | 'emerald';
const ACCENT_BORDER: Record<Accent, string> = {
  rose:     'border-l-rose-500/70',
  orange:   'border-l-orange-500/70',
  cyan:     'border-l-cyan-500/70',
  emerald:  'border-l-emerald-500/70',
};
const ACCENT_BG: Record<Accent, string> = {
  rose:    'bg-rose-500/10',
  orange:  'bg-orange-500/10',
  cyan:    'bg-cyan-500/10',
  emerald: 'bg-emerald-500/10',
};

function InputGroup(
  { title, score, accent, rows }:
  {
    title: string;
    score: number;
    accent: Accent;
    rows: { label: string; value: string; threshold?: string; highlight?: boolean }[];
  }
) {
  return (
    <div className={`mt-3 border-l-2 ${ACCENT_BORDER[accent]} pl-3`}>
      <div className={`flex items-center gap-2 px-2 py-1 rounded-t ${ACCENT_BG[accent]}`}>
        <span className="text-[10px] font-bold uppercase tracking-wider text-foreground">{title}</span>
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ml-auto ${pillarColor(score)}`}>
          {score} / 2
        </span>
      </div>
      <div className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs px-2 py-2">
        {rows.map((r) => (
          <FragmentRow key={r.label} row={r} />
        ))}
      </div>
    </div>
  );
}

function FragmentRow({ row }: { row: { label: string; value: string; threshold?: string; highlight?: boolean } }) {
  return (
    <>
      <div>
        <div className="text-muted-foreground">{row.label}</div>
        {row.threshold && (
          <div className="text-[9px] text-muted-foreground/60 italic">{row.threshold}</div>
        )}
      </div>
      <div className={`text-right font-mono self-start ${row.highlight ? 'text-foreground font-bold' : 'text-foreground'}`}>
        {row.value}
      </div>
    </>
  );
}
