import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, PieChart, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  getPortfolioDashboard, addHolding, deleteHolding,
  getReplayEvents, startPortfolioReplay, pollPortfolioReplayJob,
  type PortfolioDashboard, type ReplayEventMeta, type ReplayEventResult,
  type ReplayResult,
} from '@/lib/api';
import { PageContainer } from '@/components/layout/PageContainer';
import { TabHero } from '@/components/layout/TabHero';
import { toast } from 'sonner';

// ── small presentational helpers ────────────────────────────────────────────

function fmtNum(v: number | null | undefined, dp = 2): string {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function PnlText({ value, pct }: { value: number | null | undefined; pct?: number | null }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const cls = value > 0 ? 'text-emerald-600 dark:text-emerald-400'
    : value < 0 ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground';
  return (
    <span className={`${cls} font-medium tabular-nums`}>
      {value > 0 ? '+' : ''}{fmtMoney(value)}
      {pct != null && <span className="text-xs ml-1">({pct > 0 ? '+' : ''}{fmtNum(pct, 1)}%)</span>}
    </span>
  );
}

/** Signed-percentage cell (replay returns / drawdowns). */
function RetText({ value, dp = 1 }: { value: number | null | undefined; dp?: number }) {
  if (value == null || Number.isNaN(value)) return <span className="text-muted-foreground">—</span>;
  const cls = value > 0 ? 'text-emerald-600 dark:text-emerald-400'
    : value < 0 ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground';
  return (
    <span className={`${cls} font-medium tabular-nums`}>
      {value > 0 ? '+' : ''}{fmtNum(value, dp)}%
    </span>
  );
}

const DECISION_CLASS: Record<string, string> = {
  BUY: 'text-emerald-600 dark:text-emerald-400',
  STRONG_BUY: 'text-emerald-600 dark:text-emerald-400',
  SELL: 'text-red-600 dark:text-red-400',
  SHORT: 'text-red-600 dark:text-red-400',
  HOLD: 'text-amber-600 dark:text-amber-400',
};

function ageLabel(runAt?: string | null): string {
  if (!runAt) return '—';
  const then = new Date(runAt.includes('T') ? runAt : `${runAt}T00:00:00`);
  if (Number.isNaN(then.getTime())) return runAt.slice(0, 10);
  const days = Math.floor((Date.now() - then.getTime()) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return '1d ago';
  if (days < 60) return `${days}d ago`;
  return then.toISOString().slice(0, 10);
}

// Regime vocabulary mirrors src/portfolio/event_library.py (exact-match
// similarity scoring depends on these labels staying aligned).
const REGIME_DIMS: Array<{ key: string; label: string }> = [
  { key: 'risk_appetite', label: 'Risk appetite' },
  { key: 'rate_direction', label: 'Rate direction' },
  { key: 'dollar_trend', label: 'Dollar trend' },
  { key: 'volatility_regime', label: 'Volatility regime' },
  { key: 'recession_risk', label: 'Recession risk' },
];

function matchChip(matches: number): string {
  if (matches >= 4) return 'bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-700/40';
  if (matches >= 2) return 'bg-amber-600/20 text-amber-700 dark:text-amber-200 border-amber-700/40';
  return 'bg-muted text-muted-foreground border-border';
}

// ── page ────────────────────────────────────────────────────────────────────

type PortfolioTab = 'positions' | 'replay' | 'regime';

export function PortfolioPage() {
  const [dash, setDash] = useState<PortfolioDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<PortfolioTab>('positions');

  // Add-position form
  const [ticker, setTicker] = useState('');
  const [qty, setQty] = useState('');
  const [cost, setCost] = useState('');
  const [notes, setNotes] = useState('');
  const [adding, setAdding] = useState(false);
  const [removingId, setRemovingId] = useState<number | null>(null);

  // Crisis replay state — lifted here so switching tabs doesn't discard a
  // completed result (Radix Tabs unmount inactive content).
  const [events, setEvents] = useState<ReplayEventMeta[] | null>(null);
  const [replay, setReplay] = useState<ReplayResult | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayProgress, setReplayProgress] = useState<string | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const replayStartedRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDash(await getPortfolioDashboard());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load portfolio');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Event library metadata is cheap + unauthenticated — load once so the
  // replay tab can show the six crises even before the first run.
  useEffect(() => {
    getReplayEvents().then(r => setEvents(r.events)).catch(() => { /* library preview is optional */ });
  }, []);

  const runReplay = useCallback(async () => {
    if (replayBusy) return;
    setReplayBusy(true);
    setReplayError(null);
    setReplayProgress(null);
    try {
      const start = await startPortfolioReplay();
      if (start.cached && start.result) {
        setReplay(start.result);
        return;
      }
      if (!start.job_id) throw new Error('Replay request returned neither a cached result nor a job.');
      const final = await pollPortfolioReplayJob(start.job_id, {
        onProgress: (s) => setReplayProgress(s.progress_msg),
      });
      setReplay(final.result?.result ?? null);
    } catch (e) {
      setReplayError(e instanceof Error ? e.message : 'Replay failed');
    } finally {
      setReplayBusy(false);
      setReplayProgress(null);
    }
  }, [replayBusy]);

  // Auto-run once when the replay/regime tab is first visited with holdings
  // present. POST is cache-first (unchanged holdings → instant {cached:true})
  // so the auto-fire is idempotent and cheap.
  useEffect(() => {
    if (tab !== 'replay' && tab !== 'regime') return;
    if (replayStartedRef.current || replayBusy || replay) return;
    if (!dash || dash.summary.position_count === 0) return;
    replayStartedRef.current = true;
    void runReplay();
  }, [tab, dash, replay, replayBusy, runReplay]);

  async function onAdd() {
    const t = ticker.trim().toUpperCase();
    const q = parseFloat(qty);
    const c = parseFloat(cost);
    if (!t || !(q > 0) || !(c > 0)) {
      toast.error('Ticker, quantity and average cost are required (qty/cost > 0).');
      return;
    }
    setAdding(true);
    try {
      await addHolding({ ticker: t, quantity: q, avg_cost: c, notes: notes.trim() || null });
      setTicker(''); setQty(''); setCost(''); setNotes('');
      toast.success(`${t} position saved`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to add holding');
    } finally {
      setAdding(false);
    }
  }

  async function onDelete(id: number, t: string) {
    setRemovingId(id);
    try {
      await deleteHolding(id);
      toast.success(`${t} removed`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to remove holding');
    } finally {
      setRemovingId(null);
    }
  }

  const s = dash?.summary;
  const positionCount = s?.position_count ?? 0;

  return (
    <PageContainer size="wide">
      <TabHero
        title="Portfolio"
        subtitle="Your holdings against the system's latest valuation signals"
        icon={PieChart}
        actions={
          <button
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-hero-foreground hover:bg-hero-foreground/10"
            title="Refresh prices & signals"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        }
      />

      {error && (
        <Card className="p-4 mt-4 text-sm text-red-600 dark:text-red-400">{error}</Card>
      )}

      <Tabs value={tab} onValueChange={(v) => setTab(v as PortfolioTab)} className="mt-4">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="positions">Positions &amp; indicators</TabsTrigger>
          <TabsTrigger value="replay">Crisis replay</TabsTrigger>
          <TabsTrigger value="regime">Regime then vs now</TabsTrigger>
        </TabsList>

        {/* ── Tab 1: Positions & indicators (P1) ─────────────────────────── */}
        <TabsContent value="positions">
          {/* Summary strip */}
          {dash && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
              <Card className="p-4">
                <div className="text-xs text-muted-foreground">Market value</div>
                <div className="text-xl font-semibold tabular-nums">{fmtMoney(s?.total_market_value)}</div>
              </Card>
              <Card className="p-4">
                <div className="text-xs text-muted-foreground">Cost basis</div>
                <div className="text-xl font-semibold tabular-nums">{fmtMoney(s?.total_cost_basis)}</div>
              </Card>
              <Card className="p-4">
                <div className="text-xs text-muted-foreground">Unrealized P&L</div>
                <div className="text-xl font-semibold"><PnlText value={s?.total_unrealized_pnl} pct={s?.total_pnl_pct} /></div>
              </Card>
              <Card className="p-4">
                <div className="text-xs text-muted-foreground">Positions</div>
                <div className="text-xl font-semibold tabular-nums">
                  {s?.position_count ?? 0}
                  {s?.top_weight_pct != null && (
                    <span className="text-xs text-muted-foreground ml-2">top {fmtNum(s.top_weight_pct, 1)}%</span>
                  )}
                </div>
              </Card>
            </div>
          )}

          {/* Add position */}
          <Card className="p-4 mt-4">
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Ticker</label>
                <input
                  value={ticker} onChange={e => setTicker(e.target.value)}
                  placeholder="e.g. BABA"
                  className="w-28 rounded-md border border-input bg-background px-2.5 py-1.5 text-sm uppercase"
                  onKeyDown={e => e.key === 'Enter' && void onAdd()}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Quantity</label>
                <input
                  value={qty} onChange={e => setQty(e.target.value)}
                  placeholder="100" inputMode="decimal"
                  className="w-24 rounded-md border border-input bg-background px-2.5 py-1.5 text-sm"
                  onKeyDown={e => e.key === 'Enter' && void onAdd()}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Avg cost</label>
                <input
                  value={cost} onChange={e => setCost(e.target.value)}
                  placeholder="150.00" inputMode="decimal"
                  className="w-28 rounded-md border border-input bg-background px-2.5 py-1.5 text-sm"
                  onKeyDown={e => e.key === 'Enter' && void onAdd()}
                />
              </div>
              <div className="flex-1 min-w-40">
                <label className="text-xs text-muted-foreground block mb-1">Notes (optional)</label>
                <input
                  value={notes} onChange={e => setNotes(e.target.value)}
                  placeholder="thesis, entry rationale…"
                  className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm"
                  onKeyDown={e => e.key === 'Enter' && void onAdd()}
                />
              </div>
              <Button onClick={() => void onAdd()} disabled={adding} className="gap-1.5">
                <Plus size={15} /> {adding ? 'Saving…' : 'Add position'}
              </Button>
            </div>
          </Card>

          {/* Holdings table */}
          <Card className="mt-4 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Ticker</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Avg cost</TableHead>
                  <TableHead className="text-right">Price</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead className="text-right">Weight</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead className="text-center">Decision</TableHead>
                  <TableHead className="text-right">IV (bear/base/bull)</TableHead>
                  <TableHead className="text-right">IV upside</TableHead>
                  <TableHead>Sector</TableHead>
                  <TableHead className="text-right">Run</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && !dash && (
                  <TableRow><TableCell colSpan={13} className="text-center text-muted-foreground py-8">Loading portfolio…</TableCell></TableRow>
                )}
                {dash && dash.holdings.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={13} className="text-center text-muted-foreground py-10">
                      No positions yet — add your first holding above.
                    </TableCell>
                  </TableRow>
                )}
                {dash?.holdings.map(h => {
                  const sig = h.signals ?? null;
                  const ivs = sig && (sig.iv_bear ?? sig.iv_base ?? sig.iv_bull)
                    ? `${fmtNum(sig.iv_bear ?? null, 0)} / ${fmtNum(sig.iv_base ?? null, 0)} / ${fmtNum(sig.iv_bull ?? null, 0)}`
                    : '—';
                  const decision = sig?.decision ?? null;
                  return (
                    <TableRow key={h.id}>
                      <TableCell className="font-semibold">
                        {h.ticker}
                        {h.notes && <div className="text-[11px] font-normal text-muted-foreground max-w-40 truncate" title={h.notes}>{h.notes}</div>}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(h.quantity, 0)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(h.avg_cost)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(h.price ?? null)}</TableCell>
                      <TableCell className="text-right tabular-nums font-medium">{fmtMoney(h.market_value)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(h.weight_pct ?? null, 1)}%</TableCell>
                      <TableCell className="text-right"><PnlText value={h.unrealized_pnl} pct={h.pnl_pct} /></TableCell>
                      <TableCell className="text-center">
                        {decision
                          ? <span className={`text-xs font-bold ${DECISION_CLASS[decision] ?? ''}`}>{decision.replace('_', ' ')}</span>
                          : <span className="text-muted-foreground text-xs">—</span>}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-xs">{ivs}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {h.iv_upside_pct != null ? (
                          <span className={h.iv_upside_pct >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
                            {h.iv_upside_pct >= 0 ? '+' : ''}{fmtNum(h.iv_upside_pct, 1)}%
                          </span>
                        ) : <span className="text-muted-foreground">—</span>}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground max-w-32 truncate" title={sig?.sector ?? ''}>{sig?.sector ?? '—'}</TableCell>
                      <TableCell className="text-right text-xs text-muted-foreground">{ageLabel(sig?.run_at)}</TableCell>
                      <TableCell className="text-right">
                        <button
                          onClick={() => void onDelete(h.id, h.ticker)}
                          disabled={removingId === h.id}
                          className="text-muted-foreground hover:text-red-500 p-1"
                          title={`Remove ${h.ticker}`}
                        >
                          <Trash2 size={14} />
                        </button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Card>

          {/* Sector exposure */}
          {dash && Object.keys(dash.sector_exposure).length > 0 && (
            <Card className="p-4 mt-4">
              <div className="text-sm font-semibold mb-3">Sector exposure</div>
              <div className="space-y-2">
                {Object.entries(dash.sector_exposure).map(([sector, pct]) => (
                  <div key={sector} className="flex items-center gap-3">
                    <div className="w-48 text-xs text-muted-foreground truncate" title={sector}>{sector}</div>
                    <div className="flex-1 h-2 rounded bg-muted overflow-hidden">
                      <div className="h-full rounded bg-primary/70" style={{ width: `${Math.min(pct, 100)}%` }} />
                    </div>
                    <div className="w-14 text-right text-xs tabular-nums">{fmtNum(pct, 1)}%</div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {dash && (
            <p className="text-[11px] text-muted-foreground mt-3">
              Prices: one batched FMP quote ({new Date(dash.prices_at).toLocaleTimeString()}).
              Signals: latest archived analysis run per ticker — decision, DCF intrinsic
              values (bear/base/bull) and sector are read-only joins, not new valuations.
            </p>
          )}
        </TabsContent>

        {/* ── Tab 2: Crisis replay (P2) ──────────────────────────────────── */}
        <TabsContent value="replay">
          <ReplaySection
            positionCount={positionCount}
            positionsLoaded={!!dash}
            events={events}
            replay={replay}
            busy={replayBusy}
            progress={replayProgress}
            error={replayError}
            onRun={runReplay}
          />
        </TabsContent>

        {/* ── Tab 3: Regime then vs now ──────────────────────────────────── */}
        <TabsContent value="regime">
          <RegimeSection
            positionCount={positionCount}
            replay={replay}
            busy={replayBusy}
            progress={replayProgress}
            onRun={runReplay}
          />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

// ── Crisis replay tab ───────────────────────────────────────────────────────

function ReplaySection({
  positionCount, positionsLoaded, events, replay, busy, progress, error, onRun,
}: {
  positionCount: number;
  positionsLoaded: boolean;
  events: ReplayEventMeta[] | null;
  replay: ReplayResult | null;
  busy: boolean;
  progress: string | null;
  error: string | null;
  onRun: () => Promise<void>;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap mt-2">
        <Button
          onClick={() => void onRun()}
          disabled={busy || positionCount === 0}
          className="gap-1.5"
        >
          {busy ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          {busy ? (progress || 'Replaying…') : replay ? 'Re-run replay' : 'Run crisis replay'}
        </Button>
        <span className="text-xs text-muted-foreground max-w-lg">
          {positionCount === 0
            ? positionsLoaded
              ? 'Add positions on the first tab — the replay uses your actual holdings.'
              : 'Loading portfolio…'
            : replay
              ? `Computed ${replay.holdings_snapshot.position_count} positions × ${replay.event_count} events on a cost-basis snapshot. Cached until your holdings change.`
              : 'Replays your current holdings through six historical crises using actual price history — no models, no LLM.'}
        </span>
      </div>

      {error && (
        <Card className="p-4 text-sm text-red-600 dark:text-red-400 flex items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
          <span>{error}</span>
        </Card>
      )}

      {busy && !replay && (
        <Card className="p-4 text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 size={15} className="animate-spin" />
          {progress || 'Starting replay…'}
        </Card>
      )}

      {replay
        ? (
          <div className="space-y-4">
            {replay.events.map(ev => <EventCard key={ev.key} ev={ev} />)}
          </div>
        )
        : !busy && events && events.length > 0 && (
          <EventLibraryPreview events={events} />
        )}
    </div>
  );
}

/** Pre-run view: the curated event library with reference benchmark numbers. */
function EventLibraryPreview({ events }: { events: ReplayEventMeta[] }) {
  return (
    <div>
      <div className="text-sm font-semibold mb-2">Event library</div>
      <div className="grid md:grid-cols-2 gap-3">
        {events.map(ev => (
          <Card key={ev.key} className="p-4">
            <div className="flex items-baseline justify-between gap-2">
              <div className="text-sm font-semibold">{ev.name}</div>
              <span className="text-[11px] text-muted-foreground tabular-nums whitespace-nowrap">
                {ev.window.start} → {ev.window.end}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 mt-2 text-xs">
              <div>
                <span className="text-muted-foreground">SPY</span>{' '}
                <RetText value={ev.benchmarks.spy_return_pct} />{' '}
                <span className="text-muted-foreground">(DD </span>
                <RetText value={ev.benchmarks.spy_max_dd_pct} />
                <span className="text-muted-foreground">)</span>
              </div>
              <div>
                <span className="text-muted-foreground">QQQ</span>{' '}
                <RetText value={ev.benchmarks.qqq_return_pct} />{' '}
                <span className="text-muted-foreground">(DD </span>
                <RetText value={ev.benchmarks.qqq_max_dd_pct} />
                <span className="text-muted-foreground">)</span>
              </div>
            </div>
            {ev.tags.length > 0 && (
              <div className="flex gap-1 mt-2 flex-wrap">
                {ev.tags.map(t => (
                  <span key={t} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-[10px]">{t}</span>
                ))}
              </div>
            )}
          </Card>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground mt-3">
        Curated crisis windows with reference SPY/QQQ numbers (live-cross-checked on every run).
        Run the replay to see what YOUR portfolio did through each event.
      </p>
    </div>
  );
}

function EventCard({ ev }: { ev: ReplayEventResult }) {
  const sim = ev.regime_similarity;
  const divergent = ev.benchmarks.cross_check === 'divergent';
  const covered = ev.portfolio.covered_weight_pct;
  const partialCoverage = covered != null && covered < 99.95;

  return (
    <Card className="p-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div>
          <div className="text-sm font-semibold">{ev.name}</div>
          <div className="text-[11px] text-muted-foreground tabular-nums">
            {ev.window.start} → {ev.window.end}
          </div>
        </div>
        <span
          className={`px-2 py-0.5 rounded border text-[11px] font-semibold ${matchChip(sim.matches)}`}
          title={`Regime similarity vs today: exact match on ${sim.matches} of ${sim.of} dimensions${sim.matched_dims.length ? ` (${sim.matched_dims.join(', ')})` : ''}`}
        >
          {sim.matches}/{sim.of} regime match
        </span>
      </div>

      {/* Benchmark cross-check warning */}
      {divergent && (
        <div className="mt-2 p-2 rounded border border-amber-600/40 bg-amber-600/10 text-[11px] text-amber-800 dark:text-amber-200 flex items-start gap-2">
          <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
          <span>
            Benchmark cross-check divergent — curated vs live FMP history differ by &gt;8pp on:{' '}
            {ev.benchmarks.divergent_keys.map(k => (
              <span key={k} className="font-mono">
                {k} ({fmtNum(ev.benchmarks.curated[k as keyof typeof ev.benchmarks.curated], 1)}% → {fmtNum(ev.benchmarks.live[k as keyof typeof ev.benchmarks.live], 1)}%)
                {' '}
              </span>
            ))}
            — possible data drift (e.g. split adjustment); treat this event&apos;s numbers with care.
          </span>
        </div>
      )}

      {/* Portfolio impact */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
        <div className="p-2.5 rounded-md bg-muted/40">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Your portfolio</div>
          <div className="text-lg font-semibold"><RetText value={ev.portfolio.window_return_pct} /></div>
          <div className="text-[10px] text-muted-foreground">max DD <RetText value={ev.portfolio.max_dd_pct} /></div>
        </div>
        <div className="p-2.5 rounded-md bg-muted/40">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">SPY (live)</div>
          <div className="text-lg font-semibold"><RetText value={ev.benchmarks.live.spy_return_pct} /></div>
          <div className="text-[10px] text-muted-foreground">max DD <RetText value={ev.benchmarks.live.spy_max_dd_pct} /></div>
        </div>
        <div className="p-2.5 rounded-md bg-muted/40">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">QQQ (live)</div>
          <div className="text-lg font-semibold"><RetText value={ev.benchmarks.live.qqq_return_pct} /></div>
          <div className="text-[10px] text-muted-foreground">max DD <RetText value={ev.benchmarks.live.qqq_max_dd_pct} /></div>
        </div>
        <div className="p-2.5 rounded-md bg-muted/40">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Coverage</div>
          <div className="text-lg font-semibold tabular-nums">{covered != null ? `${fmtNum(covered, 1)}%` : '—'}</div>
          <div className="text-[10px] text-muted-foreground">of cost basis replayed</div>
        </div>
      </div>

      {/* Coverage guard note */}
      {partialCoverage && ev.excluded.length > 0 && (
        <p className="text-[11px] text-muted-foreground mt-2">
          Excluded: <span className="font-semibold">{ev.excluded.join(', ')}</span> — no sufficient
          price history in this window (listed later). Weights renormalized over the covered{' '}
          {fmtNum(covered, 1)}%.
        </p>
      )}

      {/* Per-holding breakdown */}
      <Table className="mt-3">
        <TableHeader>
          <TableRow>
            <TableHead>Holding</TableHead>
            <TableHead className="text-right">Window return</TableHead>
            <TableHead className="text-right">Max DD</TableHead>
            <TableHead className="text-right">Beta vs SPY</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ev.holdings.map(h => (
            <TableRow key={h.ticker}>
              <TableCell className="font-medium">
                {h.ticker}
                {!h.covered && (
                  <span className="ml-2 text-[10px] text-muted-foreground" title="Not listed yet or too few in-window price points — excluded from portfolio aggregates">
                    not covered then
                  </span>
                )}
              </TableCell>
              <TableCell className="text-right">
                {h.covered ? <RetText value={h.window_return_pct} /> : <span className="text-muted-foreground">—</span>}
              </TableCell>
              <TableCell className="text-right">
                {h.covered ? <RetText value={h.max_dd_pct} /> : <span className="text-muted-foreground">—</span>}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {h.covered ? (h.beta != null ? fmtNum(h.beta, 2) : '—') : <span className="text-muted-foreground">—</span>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

// ── Regime then-vs-now tab ──────────────────────────────────────────────────

function RegimeSection({
  positionCount, replay, busy, progress, onRun,
}: {
  positionCount: number;
  replay: ReplayResult | null;
  busy: boolean;
  progress: string | null;
  onRun: () => Promise<void>;
}) {
  if (!replay) {
    return (
      <div className="mt-2">
        {busy ? (
          <Card className="p-4 text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 size={15} className="animate-spin" />
            {progress || 'Starting replay…'}
          </Card>
        ) : (
          <Card className="p-8 text-center">
            <p className="text-sm text-muted-foreground mb-4">
              Regime then-vs-now is computed as part of a crisis replay — it compares today&apos;s
              macro regime against each event&apos;s snapshot.
            </p>
            {positionCount > 0 ? (
              <Button onClick={() => void onRun()} className="gap-1.5">
                <RefreshCw size={15} /> Run crisis replay
              </Button>
            ) : (
              <p className="text-xs text-muted-foreground">Add positions first to enable replays.</p>
            )}
          </Card>
        )}
      </div>
    );
  }

  const today = replay.events[0]?.regime_similarity.today ?? {};

  return (
    <div className="space-y-4">
      <Card className="p-4 mt-2">
        <div className="text-sm font-semibold mb-2">Today&apos;s regime</div>
        <div className="flex flex-wrap gap-2">
          {REGIME_DIMS.map(d => (
            <span key={d.key} className="px-2 py-1 rounded-md border border-border bg-muted/40 text-xs">
              <span className="text-muted-foreground">{d.label}: </span>
              <span className="font-semibold">{today[d.key] ?? '—'}</span>
            </span>
          ))}
        </div>
        <p className="text-[11px] text-muted-foreground mt-2">
          From the latest macro regime snapshot. Similarity = exact match per dimension (5 max);
          events below are ordered most-similar first.
        </p>
      </Card>

      <div className="grid md:grid-cols-2 gap-4">
        {replay.events.map(ev => <RegimeCompareCard key={ev.key} ev={ev} />)}
      </div>
    </div>
  );
}

function RegimeCompareCard({ ev }: { ev: ReplayEventResult }) {
  const sim = ev.regime_similarity;
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">{ev.name}</div>
          <div className="text-[11px] text-muted-foreground tabular-nums">
            {ev.window.start} → {ev.window.end}
          </div>
        </div>
        <span
          className={`px-2 py-0.5 rounded border text-[11px] font-semibold whitespace-nowrap ${matchChip(sim.matches)}`}
          title={`Matched: ${sim.matched_dims.length ? sim.matched_dims.join(', ') : 'none'}`}
        >
          {sim.matches}/{sim.of}
        </span>
      </div>

      <div className="mt-3 space-y-1">
        {REGIME_DIMS.map(d => {
          const then = sim.then[d.key as keyof typeof sim.then];
          const now = sim.today[d.key] ?? null;
          const matched = then != null && then === now;
          return (
            <div
              key={d.key}
              className={`grid grid-cols-[1fr_auto_auto] gap-2 items-center text-xs px-2 py-1 rounded ${matched ? 'bg-emerald-600/10' : ''}`}
            >
              <span className="text-muted-foreground">{d.label}</span>
              <span className="tabular-nums w-20 text-right">{then ?? '—'}</span>
              <span className={`tabular-nums w-20 text-right ${matched ? 'text-emerald-700 dark:text-emerald-300 font-semibold' : 'text-muted-foreground'}`}>
                {now ?? '—'}
              </span>
            </div>
          );
        })}
        <div className="grid grid-cols-[1fr_auto_auto] gap-2 px-2 pt-1 text-[10px] text-muted-foreground">
          <span />
          <span className="w-20 text-right">then</span>
          <span className="w-20 text-right">now</span>
        </div>
      </div>

      {ev.macro.notes && (
        <p className="text-[11px] text-muted-foreground italic mt-3 leading-relaxed">{ev.macro.notes}</p>
      )}
    </Card>
  );
}
