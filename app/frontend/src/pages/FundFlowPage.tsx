/**
 * FundFlowPage.tsx
 * ================
 * Geographic fund-flow screen — where money is moving across the key regional
 * equity ETFs, and how hard.
 *
 * Three layers, top to bottom:
 *   1. Summary brief — headline, regime, key flows, key changes, implications,
 *      watch items. Written by DeepSeek from computed facts (or the
 *      deterministic draft when the model is unavailable); the source is
 *      always shown so the reader knows which they are looking at.
 *   2. The STRENGTH CHART: signed -6..+6 flow composite per geography on one
 *      diverging axis, with a ghost marker at where it stood one period ago.
 *   3. The table, with a flow-pressure sparkline per row, and a detail drawer.
 *
 * A PERIOD SELECTOR (1M / 3M / 6M / 1Y) drives the chart's ghost marker and
 * every period-dependent column, so a month's reading can be judged against
 * the longer trend instead of in isolation. The composite itself is always
 * scored on the 1-month window — that is the horizon a flow signal is
 * actionable over; the longer windows are the benchmark you read it against.
 * The drawer lays all four periods out side by side.
 *
 * UNITS — the one thing not to get wrong on this page. Two different
 * quantities travel together:
 *   • flow pressure / relative flow — tape-derived, in SIGMA off a region's
 *     own baseline. Present for every region daily. Never a dollar figure.
 *   • issuer flow — measured creation/redemption in DOLLARS, null wherever
 *     the share-count feed is stale.
 * Every label below says which one it is showing.
 *
 * Mark colours are pinned hex rather than Tailwind tokens because the
 * inflow/outflow pair was CVD-validated at these exact steps (deuteranopia
 * ΔE 8.6 light, 9.6 dark). Sign, position and verdict text carry the same
 * information alongside colour, so nothing here is colour-alone.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getFundFlowCohort, refreshFundFlow,
  type FundFlowCohort, type FundFlowRegionResult, type FundFlowSummary,
} from '@/lib/api';
import {
  ArrowLeft, RefreshCw, Loader2, X, Sparkles, TrendingUp, TrendingDown,
  Repeat, Eye, Info, FlaskConical, ChevronDown,
} from 'lucide-react';
import { toast } from 'sonner';
import { PageContainer } from '@/components/layout/PageContainer';


// ─── Formatters ────────────────────────────────────────────────────────────

const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;

/** Signed percent — for flow figures, where "+0.69%" and "-6.80%" must be
 *  distinguishable at a glance and an unsigned positive reads as ambiguous. */
const fmtPctSigned = (v: number | null | undefined, digits = 2): string =>
  v == null || Number.isNaN(v) ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`;

const fmtSigned = (v: number | null | undefined, digits = 0): string =>
  v == null || Number.isNaN(v) ? '—' : (v > 0 ? `+${v.toFixed(digits)}` : v.toFixed(digits));

const fmtSigma = (v: number | null | undefined, digits = 2): string =>
  v == null || Number.isNaN(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}σ`;

/** Compact USD — flows on this page span $10m to $50bn in one column. */
const fmtUsd = (v: number | null | undefined): string => {
  if (v == null || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  const s = v < 0 ? '−' : '+';
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}bn`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(0)}m`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(0)}k`;
  return `${s}$${a.toFixed(0)}`;
};

const fmtAum = (v: number | null | undefined): string => {
  if (v == null) return '—';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}tn`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}bn`;
  return `$${(v / 1e6).toFixed(0)}m`;
};

const formatRunTime = (iso: string | null): string => {
  if (!iso) return 'never run';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
  } catch { return iso; }
};


// ─── Colour chips ──────────────────────────────────────────────────────────

// Signed composite (-6..+6): green = inflow, red = outflow, intensity = strength.
function compositeColor(c: number | null | undefined): string {
  if (c == null) return 'bg-muted text-muted-foreground';
  if (c >= 4) return 'bg-emerald-600/30 text-black dark:text-emerald-200';
  if (c >= 1) return 'bg-emerald-600/15 text-black dark:text-emerald-300';
  if (c <= -4) return 'bg-red-600/30 text-black dark:text-red-200';
  if (c <= -1) return 'bg-red-600/15 text-black dark:text-red-300';
  return 'bg-muted text-muted-foreground';
}

// Signed pillar (-2..+2).
function pillarColor(s: number | null | undefined): string {
  if (s == null) return 'bg-muted text-muted-foreground';
  if (s >= 1) return 'bg-emerald-600/20 text-black dark:text-emerald-300';
  if (s <= -1) return 'bg-red-600/20 text-black dark:text-red-300';
  return 'bg-muted text-muted-foreground';
}

function verdictColor(v: string): string {
  if (v === 'Accelerating-Inflow') return 'bg-emerald-600/30 text-black dark:text-emerald-200 border-emerald-700/40';
  if (v === 'Turning-Inflow') return 'bg-emerald-600/15 text-black dark:text-emerald-300 border-emerald-700/30';
  if (v === 'Accelerating-Outflow') return 'bg-red-600/30 text-black dark:text-red-200 border-red-700/40';
  if (v === 'Turning-Outflow') return 'bg-red-600/15 text-black dark:text-red-300 border-red-700/30';
  return 'bg-muted text-muted-foreground border-border';
}

function divergenceColor(d: string | null): string {
  if (d === 'flow-leads') return 'bg-primary/15 text-brand';
  if (d === 'price-leads') return 'bg-amber-500/20 text-amber-800 dark:text-amber-300';
  if (d === 'confirming') return 'bg-muted text-muted-foreground';
  return 'bg-transparent text-muted-foreground/50';
}

// The verdict is written out in full deliberately. The sector-momentum table
// can shorten "Accelerating-Long" to "Accelerating" because a Long/Short
// toggle above it already fixes the side; this table shows both directions at
// once, so dropping the suffix would leave Singapore and Indonesia both
// reading "Accelerating" and the difference carried by colour alone.
const verdictLabel = (v: string): string => v.replace('-', ' ');


type TableView = 'REGIONS' | 'BENCHMARKS';

/** The four reporting horizons, and how each maps onto the scored fields. */
type PeriodKey = '1M' | '3M' | '6M' | '1Y';

interface PeriodSpec {
  key: PeriodKey;
  label: string;
  /** Flow pressure over this window, in sigma off the region's own baseline. */
  pressure: (r: FundFlowRegionResult) => number | null;
  /** Where the flow composite stood one period ago. */
  was: (r: FundFlowRegionResult) => number | null;
  /** Measured issuer creation/redemption over this window, USD. */
  issuer: (r: FundFlowRegionResult) => number | null;
  issuerPct: (r: FundFlowRegionResult) => number | null;
  /** ETF total return over this window, USD terms. */
  ret: (r: FundFlowRegionResult) => number | null;
}

const PERIODS: PeriodSpec[] = [
  {
    key: '1M', label: '1 month',
    pressure: (r) => r.cmf_z_21, was: (r) => r.composite_1m,
    issuer: (r) => r.implied_flow_21d, issuerPct: (r) => r.implied_flow_21d_pct_aum,
    ret: (r) => r.r_21d,
  },
  {
    key: '3M', label: '3 months',
    pressure: (r) => r.cmf_z_63, was: (r) => r.composite_3m,
    issuer: (r) => r.implied_flow_63d, issuerPct: (r) => r.implied_flow_63d_pct_aum,
    ret: (r) => r.r_63d,
  },
  {
    key: '6M', label: '6 months',
    pressure: (r) => r.cmf_z_126, was: (r) => r.composite_6m,
    issuer: (r) => r.implied_flow_126d, issuerPct: (r) => r.implied_flow_126d_pct_aum,
    ret: (r) => r.r_126d,
  },
  {
    key: '1Y', label: '1 year',
    pressure: (r) => r.cmf_z_252, was: (r) => r.composite_12m,
    issuer: (r) => r.implied_flow_252d, issuerPct: (r) => r.implied_flow_252d_pct_aum,
    ret: (r) => r.r_252d,
  },
];


export function FundFlowPage() {
  const navigate = useNavigate();
  const [cohort, setCohort] = useState<FundFlowCohort | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<FundFlowRegionResult | null>(null);
  const [tableView, setTableView] = useState<TableView>('REGIONS');
  const [periodKey, setPeriodKey] = useState<PeriodKey>('1M');
  const period = PERIODS.find((p) => p.key === periodKey)!;

  const load = () => {
    setLoading(true);
    getFundFlowCohort()
      .then(setCohort)
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    const toastId = toast.loading(
      'Scoring geographic fund flows… (~60s: 27 ETFs + AI summary)',
      { duration: Infinity },
    );
    try {
      const res = await refreshFundFlow({});
      toast.dismiss(toastId);
      toast.success(
        `Fund flow refreshed — ${res.inflow_count} inflow / ${res.outflow_count} outflow`
        + (res.summary_source === 'deterministic' ? ' (AI summary unavailable, showing computed brief)' : ''),
      );
      load();
    } catch (e) {
      toast.dismiss(toastId);
      toast.error(`Refresh failed: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  };

  const regions = cohort?.regions ?? [];
  const benchmarks = cohort?.benchmarks ?? [];

  // Strength bars read best strongest-inflow at the top, strongest-outflow at
  // the bottom — a single signed axis, not two ranked lists.
  const barRows = useMemo(
    () => [...regions].sort((a, b) => (b.composite ?? 0) - (a.composite ?? 0)),
    [regions],
  );

  const tableRows = tableView === 'REGIONS' ? barRows : benchmarks;

  return (
    <PageContainer size="wide">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/research-ideas')}
            className="p-1.5 rounded-full hover:bg-muted flex-shrink-0"
            aria-label="Back"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="min-w-0">
            <h1 className="text-2xl font-bold tracking-tight text-foreground truncate">
              Fund Flow (Geographic)
            </h1>
            <p className="text-[13px] text-muted-foreground mt-0.5">
              Last run {formatRunTime(cohort?.created_at ?? null)}
              {cohort?.as_of ? ` · as of ${cohort.as_of}` : ' · live'}
              {' · '}
              <span className="text-emerald-700 dark:text-emerald-300 font-semibold">
                {cohort?.inflow_count ?? 0} inflow
              </span>
              {' / '}
              <span className="text-red-700 dark:text-red-300 font-semibold">
                {cohort?.outflow_count ?? 0} outflow
              </span>
            </p>
          </div>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-semibold rounded-full border-2 border-foreground/70 text-foreground bg-card hover:bg-muted disabled:opacity-50 transition-colors flex-shrink-0"
        >
          {refreshing ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          Refresh
        </button>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading flow map…
        </div>
      )}

      {!loading && (!cohort || cohort.run_id == null) && (
        <div className="text-sm text-muted-foreground p-6 border border-border rounded-md">
          No fund-flow run yet — click Refresh to score the nine geographies.
        </div>
      )}

      {!loading && cohort && cohort.run_id != null && (
        <>
          {cohort.summary && <SummaryPanel summary={cohort.summary} />}

          {/* ── Period selector ──────────────────────────────────────────
              Drives the chart's ghost marker and every period-dependent
              column below. One control for the whole page, so the reader
              never has to reconcile two horizons on screen at once. */}
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground mr-1">
              Compare vs
            </span>
            {PERIODS.map((p) => (
              <button
                key={p.key}
                onClick={() => setPeriodKey(p.key)}
                className={
                  'px-4 py-2 min-h-[40px] text-sm font-semibold rounded-full border-2 select-none touch-manipulation transition-colors '
                  + (periodKey === p.key
                    ? 'bg-primary border-primary text-primary-foreground'
                    : 'bg-card border-foreground/70 text-foreground hover:bg-muted')
                }
                title={`Show flow measured over the trailing ${p.label}`}
              >
                {p.key}
              </button>
            ))}
          </div>

          <StrengthBars rows={barRows} onSelect={setSelected} period={period} />

          {/* ── Table ────────────────────────────────────────────────── */}
          <div className="flex items-center gap-2 mb-3 mt-6 flex-wrap">
            {(['REGIONS', 'BENCHMARKS'] as TableView[]).map((v) => (
              <button
                key={v}
                onClick={() => setTableView(v)}
                className={
                  'px-5 py-2.5 min-h-[44px] text-sm font-semibold rounded-full border-2 select-none touch-manipulation transition-colors '
                  + (tableView === v
                    ? 'bg-primary border-primary text-primary-foreground'
                    : 'bg-card border-foreground/70 text-foreground hover:bg-muted')
                }
              >
                {v === 'REGIONS' ? `Geographies (${regions.length})` : `Global anchors (${benchmarks.length})`}
              </button>
            ))}
          </div>

          <FlowTable rows={tableRows} onSelect={setSelected} period={period} />

          <Legend />

          <Methodology />

          {cohort.failed_regions.length > 0 && (
            <p className="text-[12px] text-amber-700 dark:text-amber-400 mt-3">
              Skipped this run: {cohort.failed_regions.map((f) => `${f.region} (${f.reason})`).join(', ')}
            </p>
          )}
        </>
      )}

      {selected && <DetailDrawer row={selected} onClose={() => setSelected(null)} />}
    </PageContainer>
  );
}


// ─── Summary brief ─────────────────────────────────────────────────────────

function SummaryPanel({ summary }: { summary: FundFlowSummary }) {
  const isAi = summary.summary_source === 'deepseek';
  return (
    <div className="mb-6 rounded-lg border-2 border-primary/30 bg-card p-5 md:p-6">
      <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={
              'inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider '
              + (isAi ? 'bg-primary/20 text-brand' : 'bg-muted text-muted-foreground')
            }
            title={
              isAi
                ? `Written by ${summary.model_used} from the computed flow facts — figures are supplied to the model, never derived by it`
                : 'Computed directly from the scored fields (the AI narrator was unavailable this run)'
            }
          >
            <Sparkles size={10} />
            {isAi ? `AI brief · ${summary.model_used}` : 'Computed brief'}
          </span>
          <span className="px-2.5 py-1 rounded-full bg-muted text-foreground/80 text-[11.5px] font-semibold">
            {summary.inflow_count} inflow / {summary.outflow_count} outflow
          </span>
          {summary.net_implied_flow_21d != null && (
            <span
              className="px-2.5 py-1 rounded-full bg-muted text-foreground/80 text-[11.5px] font-semibold font-mono"
              title={`Summed measured issuer creation/redemption across the ${summary.implied_coverage} geographies with a live share-count feed`}
            >
              Net issuer flow 1m {fmtUsd(summary.net_implied_flow_21d)}
            </span>
          )}
        </div>
      </div>

      <p className="text-[15px] md:text-base leading-relaxed text-foreground font-medium mb-2">
        {summary.headline}
      </p>
      <p className="text-[13px] text-muted-foreground leading-relaxed mb-4">
        <span className="font-semibold text-foreground">Regime:</span> {summary.regime}
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-4">
        <SummarySection
          icon={TrendingUp}
          title="Key flows"
          items={summary.key_flows}
          tone="text-emerald-700 dark:text-emerald-400"
        />
        <SummarySection
          icon={Repeat}
          title="Key changes"
          items={summary.key_changes}
          tone="text-brand"
        />
        {/* Implications span both columns — this is the section the reader
            came for, and it earns the extra measure. */}
        <div className="lg:col-span-2">
          <SummarySection
            icon={TrendingDown}
            title="Implications"
            items={summary.implications}
            tone="text-foreground"
            emphasis
          />
        </div>
        {summary.watch_items.length > 0 && (
          <div className="lg:col-span-2">
            <SummarySection
              icon={Eye}
              title="Watch"
              items={summary.watch_items}
              tone="text-amber-700 dark:text-amber-400"
            />
          </div>
        )}
      </div>
    </div>
  );
}

function SummarySection({ icon: Icon, title, items, tone, emphasis = false }: {
  icon: typeof TrendingUp;
  title: string;
  items: string[];
  tone: string;
  emphasis?: boolean;
}) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <div className={`flex items-center gap-1.5 mb-2 ${tone}`}>
        <Icon size={13} />
        <span className="text-[11px] font-bold uppercase tracking-wider">{title}</span>
      </div>
      <ul className={
        'space-y-1.5 ' + (emphasis
          ? 'text-[13.5px] text-foreground/90 leading-relaxed'
          : 'text-[13px] text-foreground/80 leading-relaxed')
      }>
        {items.map((it, i) => (
          <li key={i} className="flex gap-2">
            <span className="text-muted-foreground/50 flex-shrink-0 select-none">•</span>
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}


// ─── Flow strength bars ────────────────────────────────────────────────────

/**
 * Signed -6..+6 composite per geography on one diverging axis, with a ghost
 * marker at the reading one selected period ago so direction of travel is
 * visible without a second chart. Switching the period moves the ghost, which
 * is what turns a snapshot into a benchmark: a +5 that was +5 a year ago is a
 * standing regime, a +5 that was -3 is a turn.
 */
function StrengthBars({ rows, onSelect, period }: {
  rows: FundFlowRegionResult[];
  onSelect: (r: FundFlowRegionResult) => void;
  period: PeriodSpec;
}) {
  if (rows.length === 0) return null;
  const MAXC = 6;
  // Percentage geometry so the bars reflow with the container on any width.
  const pos = (v: number) => 50 + (v / MAXC) * 50;

  return (
    <section className="mb-6">
      <div className="flex items-baseline justify-between gap-3 mb-2 flex-wrap">
        <h2 className="text-base font-bold text-foreground">Flow strength</h2>
        <p className="text-[12px] text-muted-foreground">
          Signed flow composite (−6 … +6) · ghost marker = {period.label} ago
        </p>
      </div>

      <div className="rounded-lg border border-border bg-card p-3 md:p-4">
        <div className="space-y-1.5">
          {rows.map((r) => {
            const c = r.composite ?? 0;
            const was = period.was(r);
            const left = Math.min(pos(0), pos(c));
            const width = Math.abs(pos(c) - pos(0));
            const isIn = c >= 0;
            return (
              <button
                key={r.region}
                onClick={() => onSelect(r)}
                className="w-full flex items-center gap-2 text-left group py-0.5"
                title={`${r.label} — ${r.verdict} · composite ${fmtSigned(c)}${
                  was != null ? ` (was ${fmtSigned(was)} ${period.label} ago)` : ''
                }${
                  period.issuer(r) != null
                    ? ` · issuer flow ${fmtUsd(period.issuer(r))} over ${period.label}`
                    : ''
                }`}
              >
                <span className="w-[104px] md:w-[150px] flex-shrink-0 text-[12.5px] font-semibold text-foreground truncate">
                  {r.emoji} {r.label}
                </span>

                <span className="relative flex-1 h-6 rounded bg-muted/40 overflow-hidden">
                  {/* Zero rule */}
                  <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
                  {/* The bar — 4px rounded data-end, square against the baseline */}
                  <span
                    className={
                      'absolute inset-y-1 group-hover:brightness-110 transition-all '
                      + (isIn
                        ? 'bg-emerald-600/80 rounded-r'
                        : 'bg-red-600/80 dark:bg-red-500/80 rounded-l')
                    }
                    style={{ left: `${left}%`, width: `${Math.max(width, 0.4)}%` }}
                  />
                  {/* Ghost marker at the reading one selected period ago */}
                  {was != null && (
                    <span
                      className="absolute inset-y-0.5 w-[2px] bg-foreground/45"
                      style={{ left: `${pos(was)}%` }}
                      aria-hidden
                    />
                  )}
                </span>

                <span className={`w-11 flex-shrink-0 text-center px-1 py-0.5 rounded text-[11.5px] font-bold font-mono ${compositeColor(c)}`}>
                  {fmtSigned(c)}
                </span>
              </button>
            );
          })}
        </div>
        <div className="flex items-center justify-between text-[10.5px] text-muted-foreground mt-2 px-1">
          <span>← outflow</span>
          <span>0</span>
          <span>inflow →</span>
        </div>
      </div>
    </section>
  );
}


// ─── Sparkline ─────────────────────────────────────────────────────────────

/** Flow-pressure trace, in sigma. Shared vertical scale across every row so
 *  the small multiples are comparable, with a zero rule for the baseline. */
function Sparkline({ points }: { points: { d: string; v: number }[] }) {
  if (!points || points.length < 2) {
    return <span className="text-muted-foreground/40 text-[11px]">—</span>;
  }
  const W = 68, H = 20;
  const vals = points.map((p) => p.v);
  const lim = Math.max(1.5, ...vals.map(Math.abs));
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => H / 2 - (v / lim) * (H / 2 - 1.5);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const last = vals[vals.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="overflow-visible" aria-hidden>
      <line x1={0} y1={H / 2} x2={W} y2={H / 2} className="stroke-border" strokeWidth={1} />
      <path d={d} fill="none" strokeWidth={2}
            className={last >= 0 ? 'stroke-emerald-600' : 'stroke-red-600 dark:stroke-red-500'} />
      <circle cx={x(points.length - 1)} cy={y(last)} r={2.2}
              className={last >= 0 ? 'fill-emerald-600' : 'fill-red-600 dark:fill-red-500'} />
    </svg>
  );
}


// ─── Table ─────────────────────────────────────────────────────────────────

function FlowTable({ rows, onSelect, period }: {
  rows: FundFlowRegionResult[];
  onSelect: (r: FundFlowRegionResult) => void;
  period: PeriodSpec;
}) {
  const P = period.key;
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
      <table className="w-full text-[14px]">
        <thead>
          <tr className="border-b border-border bg-muted/50 text-[11px] uppercase tracking-wider text-foreground/70">
            <th className="text-left px-4 py-3 font-bold">Geography</th>
            <th className="text-left px-4 py-3 font-bold">Verdict</th>
            <th className="text-right px-3 py-3 font-bold" title="Flow composite, −6…+6">Flow</th>
            <th className="text-center px-2 py-3 font-bold" title="PRESSURE pillar (−2…+2)">P</th>
            <th className="text-center px-2 py-3 font-bold" title="TURN pillar (−2…+2)">T</th>
            <th className="text-center px-2 py-3 font-bold" title="ACCELERATION pillar (−2…+2)">A</th>
            <th className="text-center px-3 py-3 font-bold" title="Flow-pressure trace, last 6 months (sigma)">6m trace</th>
            <th className="text-right px-3 py-3 font-bold" title={`Where the flow composite stood ${period.label} ago`}>Was {P}</th>
            <th className="text-right px-3 py-3 font-bold" title={`Flow pressure measured over the trailing ${period.label}, in sigma off this geography's own baseline`}>Press {P}</th>
            <th className="text-right px-3 py-3 font-bold" title={`Measured issuer creation/redemption over the trailing ${period.label}. Blank where the share-count feed is stale.`}>Issuer {P}</th>
            <th className="text-right px-3 py-3 font-bold" title={`Measured issuer flow over ${period.label} as a share of basket assets`}>% AUM</th>
            <th className="text-right px-3 py-3 font-bold" title={`ETF total return over the trailing ${period.label}, in USD`}>Return {P}</th>
            <th className="text-right px-3 py-3 font-bold" title="Flow pressure relative to the global benchmark (ACWI), in sigma. Always the 1-month window — this is the rotation read.">vs World</th>
            <th className="text-right px-3 py-3 font-bold" title="Change in relative flow over the past month, in sigma — the rotation axis">Rot 1m</th>
            <th className="text-right px-3 py-3 font-bold" title="Price momentum composite from the sector-momentum engine, −6…+6">Price</th>
            <th className="text-left px-3 py-3 font-bold" title="Whether flow and price agree, and which is leading">Flow vs price</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.region}
              onClick={() => onSelect(r)}
              className="border-b border-border/60 hover:bg-muted/40 cursor-pointer"
            >
              <td className="px-4 py-3 whitespace-nowrap">
                <span className="font-semibold text-foreground">{r.emoji} {r.label}</span>
                <span className="ml-2 font-mono text-[11.5px] text-muted-foreground">{r.etf}</span>
              </td>
              <td className="px-4 py-3">
                {r.verdict !== 'Neutral' ? (
                  <span className={`px-2.5 py-1 rounded-full text-[11.5px] font-semibold whitespace-nowrap border ${verdictColor(r.verdict)}`}>
                    {verdictLabel(r.verdict)}
                  </span>
                ) : (
                  <span className="text-[12px] text-muted-foreground/60">Neutral</span>
                )}
              </td>
              <td className="px-3 py-3 text-right">
                <span className={`px-2.5 py-1 rounded-full text-[11.5px] font-bold font-mono ${compositeColor(r.composite)}`}>
                  {fmtSigned(r.composite)}
                </span>
              </td>
              <td className="px-2 py-3 text-center"><span className={`px-2 py-1 rounded-full text-[11.5px] font-mono ${pillarColor(r.pressure_score)}`}>{fmtSigned(r.pressure_score)}</span></td>
              <td className="px-2 py-3 text-center"><span className={`px-2 py-1 rounded-full text-[11.5px] font-mono ${pillarColor(r.turn_score)}`}>{fmtSigned(r.turn_score)}</span></td>
              <td className="px-2 py-3 text-center"><span className={`px-2 py-1 rounded-full text-[11.5px] font-mono ${pillarColor(r.accel_score)}`}>{fmtSigned(r.accel_score)}</span></td>
              <td className="px-3 py-3"><div className="flex justify-center"><Sparkline points={r.spark} /></div></td>
              <td className="px-3 py-3 text-right">
                <span className={`px-2 py-1 rounded-full text-[11.5px] font-mono ${compositeColor(period.was(r))}`}>
                  {fmtSigned(period.was(r))}
                </span>
              </td>
              <td className="px-3 py-3 text-right font-mono text-[12.5px] text-foreground">{fmtSigma(period.pressure(r))}</td>
              <td className="px-3 py-3 text-right font-mono text-[12.5px] text-foreground whitespace-nowrap">
                {period.issuer(r) != null ? fmtUsd(period.issuer(r)) : (
                  <span className="text-muted-foreground/50" title="Issuer share-count feed is stale for this basket — no measured flow available">n/a</span>
                )}
              </td>
              <td className="px-3 py-3 text-right font-mono text-[12.5px] text-muted-foreground">
                {fmtPctSigned(period.issuerPct(r))}
              </td>
              <td className="px-3 py-3 text-right font-mono text-[12.5px] text-foreground">{fmtPctSigned(period.ret(r), 1)}</td>
              <td className="px-3 py-3 text-right font-mono text-[12.5px] text-foreground">{fmtSigma(r.rel_flow_z)}</td>
              <td className={
                'px-3 py-3 text-right font-mono text-[12.5px] font-semibold '
                + ((r.rel_flow_z_delta ?? 0) > 0
                  ? 'text-emerald-700 dark:text-emerald-400'
                  : (r.rel_flow_z_delta ?? 0) < 0 ? 'text-red-700 dark:text-red-400' : 'text-muted-foreground')
              }>
                {fmtSigma(r.rel_flow_z_delta)}
              </td>
              <td className="px-3 py-3 text-right">
                <span className={`px-2 py-1 rounded-full text-[11.5px] font-mono ${compositeColor(r.price_composite)}`}>
                  {fmtSigned(r.price_composite)}
                </span>
              </td>
              <td className="px-3 py-3">
                {r.divergence ? (
                  <span className={`px-2 py-1 rounded text-[11px] font-semibold whitespace-nowrap ${divergenceColor(r.divergence)}`}>
                    {r.divergence}
                  </span>
                ) : <span className="text-muted-foreground/40">—</span>}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={14} className="px-4 py-8 text-center text-muted-foreground">No rows this run.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}


function Legend() {
  return (
    <div className="mt-3 rounded-md border border-border/60 bg-muted/20 p-3">
      <div className="flex items-center gap-1.5 mb-1.5 text-muted-foreground">
        <Info size={12} />
        <span className="text-[11px] font-bold uppercase tracking-wider">How to read this</span>
      </div>
      <p className="text-[12px] text-muted-foreground leading-relaxed">
        <span className="font-semibold text-foreground">Flow</span> = composite (−6…+6, the sum of three
        signed pillars; + money arriving / − money leaving) ·{' '}
        <span className="font-semibold text-foreground">P</span> = Pressure (where flow stands versus this
        geography's own baseline) ·{' '}
        <span className="font-semibold text-foreground">T</span> = Turn (a fresh inflection in flow) ·{' '}
        <span className="font-semibold text-foreground">A</span> = Acceleration (whether it is strengthening)
        — each pillar −2…+2.
      </p>
      <p className="text-[12px] text-muted-foreground leading-relaxed mt-1.5">
        The composite is always scored on the <span className="font-semibold text-foreground">1-month</span>{' '}
        window — that is the horizon a flow signal is actionable over. The{' '}
        <span className="font-semibold text-foreground">1M / 3M / 6M / 1Y</span> selector changes the
        benchmark you read it against: <span className="font-semibold text-foreground">Was</span> is where the
        composite stood that long ago, and{' '}
        <span className="font-semibold text-foreground">Press / Issuer / Return</span> are measured over that
        trailing window. A +5 that was +5 a year ago is a standing regime; a +5 that was −3 is a turn.
      </p>
      <p className="text-[12px] text-muted-foreground leading-relaxed mt-1.5">
        <span className="font-semibold text-foreground">Press / vs World / Rot</span> are tape-derived flow
        pressure in standard deviations (σ) — a conviction-weighted share of turnover measured against each
        geography's own history, never a dollar amount.{' '}
        <span className="font-semibold text-foreground">Issuer 1m</span> is the separate, measured
        creation/redemption figure in dollars, and reads n/a wherever the issuer's share-count feed has gone
        stale. The two corroborate each other; only the σ figures drive the score.
      </p>
    </div>
  );
}


// ─── Methodology ───────────────────────────────────────────────────────────

/**
 * How the two flow measures were derived, including what was tried and
 * rejected. Collapsed by default — it is reference material, not something to
 * re-read every visit — but it lives on the page rather than in a wiki
 * because the single easiest way to misread this screen is to treat a sigma
 * figure as dollars, and the correction has to be one click away.
 */
function Methodology() {
  const [open, setOpen] = useState(false);
  return (
    <section className="mt-4 rounded-lg border border-border bg-card">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-muted/40 transition-colors rounded-lg"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2">
          <FlaskConical size={14} className="text-brand" />
          <span className="text-[13px] font-bold text-foreground">Methodology</span>
          <span className="text-[12px] text-muted-foreground hidden sm:inline">
            how flow pressure and issuer flow are derived
          </span>
        </span>
        <ChevronDown
          size={16}
          className={'text-muted-foreground transition-transform ' + (open ? 'rotate-180' : '')}
        />
      </button>

      {open && (
        <div className="px-4 pb-5 pt-1 space-y-5 text-[13px] leading-relaxed text-foreground/85">

          <MethodBlock title="The problem this had to solve">
            <p>
              There is no reported fund-flow feed behind this screen. The market-data
              provider (FMP) serves prices, volumes and market caps — not the
              creation/redemption ledgers that ETF issuers publish. So "how much money
              moved into Japan this month" had to be reconstructed from what is
              actually available. Two independent reconstructions are used, and they
              are deliberately never mixed: one is available every day for every
              geography but is an <em>estimate</em>; the other is a real
              <em> measurement</em> but is missing for some funds.
            </p>
          </MethodBlock>

          <MethodBlock title="1 · Flow pressure — derived from the tape">
            <p>
              The primitive is one number per session, per ETF:
            </p>
            <Formula>
              CLV = ((Close − Low) − (High − Close)) ÷ (High − Low)  →  [−1, +1]<br />
              flow = CLV × volume × close  →  US dollars
            </Formula>
            <p>
              CLV is the close location value (Chaikin). It asks where inside the day's
              own range the tape settled. A close on the high means buyers absorbed
              everything offered — accumulation. A close on the low means sellers
              cleared the book — distribution. That is a <em>shape</em>; multiplying by
              dollar volume turns it into a <em>magnitude</em>. Summed over a window it
              estimates net dollars accumulated: the same quantity issuers report,
              derived from the tape instead of the ledger.
            </p>
            <p>
              Region-level aggregation sums the raw dollar series across every ETF in a
              basket <em>before</em> computing any ratio, so a region's reading is
              money-weighted — SPY's dollars dominate the US row exactly as they should,
              with no arbitrary weighting scheme.
            </p>
            <p>
              Two normalisations then make regions comparable. <strong>CMF</strong> (net
              flow ÷ gross turnover over the window) reads as "of every dollar that
              traded, what share was accumulation" — scale-free, so Indonesia and the US
              sit on one axis. Then each region's CMF is <strong>z-scored against its own
              trailing year</strong>, which is the single most important step here.
            </p>
            <Rejected>
              <strong>Rejected: raw CMF.</strong> Equity ETFs drift upward, and an asset
              that drifts up closes in the top half of its range more often than not — so
              every geography reads "accumulation" in any normal year. The first working
              version put <strong>eight of nine geographies at +6</strong>: technically
              correct, useless for ranking. Measuring each region against its own
              baseline removes the structural drift and leaves the deviation, which is
              the part that carries information. It also makes one fixed threshold
              (±0.5σ) fair across geographies whose baselines genuinely differ.
            </Rejected>
            <Rejected>
              <strong>Rejected: expressing tape flow as a percentage of assets.</strong>{' '}
              This is conviction-weighted turnover, not a creation ledger, and dividing
              it by AUM produced figures like "Korea +26% of assets this month" — off by
              orders of magnitude, because a fund's monthly turnover bears no fixed
              relationship to its asset base. Tape flow is therefore reported only in σ,
              never in % of assets. That restriction is enforced in the code, not just
              by convention.
            </Rejected>
          </MethodBlock>

          <MethodBlock title="2 · Issuer flow — a real measurement">
            <p>
              An ETF's market capitalisation is shares outstanding × price. Dividing the
              provider's daily market-cap series by that day's close recovers{' '}
              <strong>shares outstanding</strong>, and the change in shares, valued at
              the close, is genuine creation/redemption:
            </p>
            <Formula>
              shares = marketCap ÷ close<br />
              issuer flow = Δshares × close  →  US dollars
            </Formula>
            <p>
              This is the number an issuer reports, and it is the only figure on this
              page legitimate to describe as actual money in or out — which is why it is
              also the only one shown as a percentage of assets.
            </p>
            <Rejected>
              <strong>Why it does not drive the score.</strong> The provider refreshes
              share counts on its own cadence, per ticker. Checked over three years:
              EWJ and EWH move on most sessions; MCHI and VGK roughly monthly; INDA and
              EWY did not change once. A frozen series silently prints "zero flow",
              which is worse than printing nothing. So issuer flow corroborates the
              composite and never feeds it, and each basket is graded by how many
              change-days its share series actually contains.
            </Rejected>
            <p>
              Grading is <strong>weighted by assets, not counted by fund</strong>. India's
              basket is INDA (frozen, ~87% of the basket) plus SMIN (live): counting
              members would grade it "partial" and print a near-zero figure that looks
              like a measurement. When more than 35% of a basket's assets sit behind a
              frozen feed, the whole row is graded stale and reads <strong>n/a</strong>{' '}
              — visibly absent rather than quietly wrong.
            </p>
          </MethodBlock>

          <MethodBlock title="3 · Scoring, and why the two are kept apart">
            <p>
              The composite reuses the Sectors (US) momentum engine's shape so the two
              screens read identically: three signed pillars, each −2…+2, summing to
              −6…+6. <strong>Pressure</strong> (flow versus the region's own baseline,
              via de-biased CMF, a money-flow index, and a net-flow z-score),{' '}
              <strong>Turn</strong> (the de-biased CMF crossing its own baseline, plus a
              fast/slow crossover and the de-meaned accumulation line reclaiming its
              average — fresh crosses only), and <strong>Acceleration</strong> (rising
              pressure, turnover surge, accumulation slope, and the share of sessions
              that were net-buy).
            </p>
            <p>
              Thresholds sit near ±0.5σ. Tighter and the pillars saturate on ordinary
              variation; wider and genuine turns arrive too late to act on. The
              composite is always scored on the <strong>1-month</strong> window — the
              horizon a flow signal is actionable over — while the 3, 6 and 12-month
              windows are the benchmark you read it against.
            </p>
            <p>
              Two overlays sit on top. <strong>Relative flow</strong> subtracts the
              global benchmark (ACWI): in a broad risk-on month every geography shows
              inflow, and only the excess over the world separates a real allocation
              preference from the tide. <strong>Flow versus price</strong> compares the
              flow composite with the price-momentum composite from the sector engine,
              flagging where money is moving ahead of price — the early read, and the
              reason a flow screen earns its keep.
            </p>
          </MethodBlock>

          <MethodBlock title="4 · The written brief">
            <p>
              Every figure in the summary is computed first, in code. The model
              (deepseek-v4-flash) receives those figures and writes them up; it is a
              writer, never a calculator, and is instructed to reuse supplied numbers
              verbatim and to compare each region's 1-month reading against its 3, 6 and
              12-month readings. If the model is unavailable, returns malformed output,
              or omits a headline, the panel falls back to a deterministic brief
              assembled directly from the scored fields — and the badge at the top of
              the summary always says which one you are reading.
            </p>
          </MethodBlock>

          <p className="text-[12px] text-muted-foreground border-t border-border pt-3">
            Known limits: tape-derived flow is an estimate of accumulation, not a
            custody record, and it cannot see off-exchange or primary-market activity.
            Issuer flow is only as timely as the provider's share-count refresh.
            Baskets of US-listed ETFs proxy a geography's equity market and will miss
            flows that route through local venues, futures, or swaps. Read the two
            measures together — where they disagree is usually the interesting part,
            and the brief calls those disagreements out explicitly.
          </p>
        </div>
      )}
    </section>
  );
}

function MethodBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-[12.5px] font-bold text-foreground mb-1.5">{title}</h3>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Formula({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md bg-muted/50 border border-border/60 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-foreground overflow-x-auto">
      {children}
    </div>
  );
}

/** A design decision that was tried and reversed — the reasoning is the point. */
function Rejected({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-l-2 border-amber-500/60 pl-3 py-0.5 text-[12.5px] text-foreground/80">
      {children}
    </div>
  );
}


// ─── Detail drawer ─────────────────────────────────────────────────────────

function DetailDrawer({ row, onClose }: { row: FundFlowRegionResult; onClose: () => void }) {
  const dirCls: Record<string, string> = {
    INFLOW: 'text-emerald-700 dark:text-emerald-300',
    OUTFLOW: 'text-red-700 dark:text-red-300',
    NEUTRAL: 'text-muted-foreground',
  };
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div
        className="relative w-full max-w-md h-full overflow-y-auto bg-background border-l border-border p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-lg font-bold text-foreground">{row.emoji} {row.label}</span>
              <span className={`text-sm font-semibold ${dirCls[row.direction]}`}>{row.direction}</span>
            </div>
            <div className="text-xs text-muted-foreground">
              {row.basket.join(' · ')} · {fmtAum(row.aum)} assets
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-muted" aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <span className={`px-2 py-1 rounded text-xs font-semibold border ${verdictColor(row.verdict)}`}>{row.verdict}</span>
          <span className={`px-2 py-1 rounded text-xs font-bold font-mono ${compositeColor(row.composite)}`}>
            flow {fmtSigned(row.composite)}/6
          </span>
          <span className="px-2 py-1 rounded text-xs font-mono bg-muted text-muted-foreground">
            strength {row.signal_strength?.toFixed(0) ?? '—'}
          </span>
          {row.divergence && (
            <span className={`px-2 py-1 rounded text-xs font-semibold ${divergenceColor(row.divergence)}`}>
              {row.divergence}
            </span>
          )}
        </div>

        {row.justification && (
          <p className="text-sm text-foreground/80 mb-4 leading-relaxed">{row.justification}</p>
        )}

        <div className="grid grid-cols-3 gap-2 mb-4">
          <Stat label="PRESSURE" value={fmtSigned(row.pressure_score)} cls={pillarColor(row.pressure_score)} />
          <Stat label="TURN" value={fmtSigned(row.turn_score)} cls={pillarColor(row.turn_score)} />
          <Stat label="ACCEL" value={fmtSigned(row.accel_score)} cls={pillarColor(row.accel_score)} />
        </div>

        {/* All four horizons side by side. The table above shows one period
            at a time to stay readable; this is where the comparison across
            them actually happens. */}
        <SectionLabel>Across periods</SectionLabel>
        <div className="overflow-x-auto mb-4 -mx-1 px-1">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-wider text-muted-foreground border-b border-border">
                <th className="text-left py-1 font-semibold">Measure</th>
                {PERIODS.map((p) => (
                  <th key={p.key} className="text-right py-1 font-semibold">{p.key}</th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono">
              <tr className="border-b border-border/40">
                <td className="py-1 text-muted-foreground font-sans">Flow pressure (σ)</td>
                {PERIODS.map((p) => (
                  <td key={p.key} className="text-right py-1 text-foreground">{fmtSigma(p.pressure(row))}</td>
                ))}
              </tr>
              <tr className="border-b border-border/40">
                <td className="py-1 text-muted-foreground font-sans">Composite then</td>
                {PERIODS.map((p) => (
                  <td key={p.key} className="text-right py-1 text-foreground">{fmtSigned(p.was(row))}</td>
                ))}
              </tr>
              <tr className="border-b border-border/40">
                <td className="py-1 text-muted-foreground font-sans">Issuer flow</td>
                {PERIODS.map((p) => (
                  <td key={p.key} className="text-right py-1 text-foreground whitespace-nowrap">
                    {p.issuer(row) != null ? fmtUsd(p.issuer(row)) : 'n/a'}
                  </td>
                ))}
              </tr>
              <tr className="border-b border-border/40">
                <td className="py-1 text-muted-foreground font-sans">Issuer % assets</td>
                {PERIODS.map((p) => (
                  <td key={p.key} className="text-right py-1 text-foreground">{fmtPctSigned(p.issuerPct(row))}</td>
                ))}
              </tr>
              <tr>
                <td className="py-1 text-muted-foreground font-sans">Return (USD)</td>
                {PERIODS.map((p) => (
                  <td key={p.key} className="text-right py-1 text-foreground">{fmtPctSigned(p.ret(row), 1)}</td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>

        <SectionLabel>Flow detail (1-month window — what the score uses)</SectionLabel>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm mb-4">
          <Row label="1m change in pressure" value={fmtSigma(row.cmf_z_delta_21)} />
          <Row label="Relative to world" value={fmtSigma(row.rel_flow_z)} />
          <Row label="Rotation (1m)" value={fmtSigma(row.rel_flow_z_delta)} />
          <Row label="Money-flow index" value={row.mfi_14 != null ? row.mfi_14.toFixed(0) : '—'} />
          <Row label="Up-flow sessions" value={row.flow_breadth_21 != null ? fmtPct(row.flow_breadth_21, 0) : '—'} />
          <Row label="Turnover vs normal" value={row.turnover_surge != null ? `${row.turnover_surge.toFixed(2)}×` : '—'} />
          <Row label="Days since inflection" value={row.days_since_turn != null ? `${row.days_since_turn}d` : '—'} />
          <Row label="Issuer feed quality" value={row.implied_quality} />
        </div>

        <SectionLabel>Price (for comparison)</SectionLabel>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm mb-4">
          <Row label="Price composite" value={fmtSigned(row.price_composite)} />
          <Row label="Price verdict" value={row.price_verdict ?? '—'} />
          <Row label="FX contribution 1m" value={row.fx_drag_21d != null ? `${(row.fx_drag_21d * 100).toFixed(1)}pp` : '—'} />
        </div>

        {row.flag_notes?.length > 0 && (
          <div className="mb-4">
            <SectionLabel>Evidence</SectionLabel>
            <ul className="space-y-1 text-[13px] text-foreground/80 list-disc pl-4">
              {row.flag_notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          </div>
        )}

        {row.data_notes?.length > 0 && (
          <div>
            <SectionLabel>Data notes</SectionLabel>
            <ul className="space-y-1 text-[12px] text-muted-foreground list-disc pl-4">
              {row.data_notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1.5 mt-1">
      {children}
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: string; cls: string }) {
  return (
    <div className="text-center">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">{label}</div>
      <div className={`py-1.5 rounded font-mono font-bold ${cls}`}>{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border/40 py-0.5 gap-2">
      <span className="text-muted-foreground text-[12px]">{label}</span>
      <span className="font-mono text-foreground text-[12px] text-right">{value}</span>
    </div>
  );
}
