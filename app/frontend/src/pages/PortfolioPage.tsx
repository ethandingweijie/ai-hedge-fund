import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, BookOpen, FlaskConical, GitFork, Loader2, MessageSquare,
  PieChart, Plus, RefreshCw, Trash2, X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  getPortfolioDashboard, addHolding, deleteHolding,
  getReplayEvents, startPortfolioReplay, pollPortfolioReplayJob,
  getWhatIfMeta, startWhatIf, pollWhatIfJob,
  getWhatIfLibrary, getWhatIfScenario, addWhatIfNote,
  compareWhatIfToHoldings, startAssumptionCheck,
  ALL_SECTOR_ETFS,
  type PortfolioDashboard, type ReplayEventMeta, type ReplayEventResult,
  type ReplayResult, type ReplaySectorPerf,
  type WhatIfMeta, type WhatIfResult, type WhatIfHoldingSkeleton,
  type WhatIfLibraryEntry, type WhatIfScenarioDetail, type WhatIfCompareResult,
  type WhatIfLibraryAssumption,
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

type PortfolioTab = 'positions' | 'replay' | 'regime' | 'whatif';

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

  // What-if simulator state — lifted for the same reason as replay results.
  const [whatIfMeta, setWhatIfMeta] = useState<WhatIfMeta | null>(null);
  const [whatIf, setWhatIf] = useState<WhatIfResult | null>(null);
  const [whatIfBusy, setWhatIfBusy] = useState(false);
  const [whatIfProgress, setWhatIfProgress] = useState<string | null>(null);
  const [whatIfError, setWhatIfError] = useState<string | null>(null);
  // P6 joint scenario memory: library list + "build on this" prefill target.
  const [library, setLibrary] = useState<WhatIfLibraryEntry[] | null>(null);
  const [buildOn, setBuildOn] = useState<{ id: string; category: string } | null>(null);

  const refreshLibrary = useCallback(async () => {
    try {
      setLibrary(await getWhatIfLibrary());
    } catch {
      /* library panel degrades to a retry affordance */
    }
  }, []);

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
  // replay tab can show the seven crises even before the first run.
  useEffect(() => {
    getReplayEvents().then(r => setEvents(r.events)).catch(() => { /* library preview is optional */ });
  }, []);

  // What-if form metadata (categories, reference crises, product knowledge)
  useEffect(() => {
    getWhatIfMeta().then(setWhatIfMeta).catch(() => { /* form degrades to free text */ });
  }, []);

  // Joint scenario memory — load when the what-if tab is first visited.
  useEffect(() => {
    if (tab !== 'whatif' || library !== null) return;
    void refreshLibrary();
  }, [tab, library, refreshLibrary]);

  const runWhatIf = useCallback(async (req: {
    category: string; concerns: string; reference_key: string | null;
    search_override: 'auto' | 'always' | 'never'; horizon_days: number;
    share: boolean; parent_id: string | null;
  }) => {
    if (whatIfBusy) return;
    setWhatIfBusy(true);
    setWhatIfError(null);
    setWhatIfProgress(null);
    try {
      const start = await startWhatIf(req);
      if (start.cached && start.result) {
        setWhatIf(start.result);
        if (req.share) {
          // Cached runs publish synchronously server-side — refresh the
          // memory so the (possibly new) row shows up immediately.
          void refreshLibrary();
          toast.success('Scenario saved to the shared scenario memory.');
        }
        setBuildOn(null);
        return;
      }
      if (!start.job_id) throw new Error('What-if request returned neither a cached result nor a job.');
      const final = await pollWhatIfJob(start.job_id, {
        onProgress: (s) => setWhatIfProgress(s.progress_msg),
      });
      setWhatIf(final.result?.result ?? null);
      if (final.result?.library_scenario_id) {
        toast.success('Scenario saved to the shared scenario memory.');
        void refreshLibrary();
      }
      setBuildOn(null);
    } catch (e) {
      setWhatIfError(e instanceof Error ? e.message : 'Simulation failed');
    } finally {
      setWhatIfBusy(false);
      setWhatIfProgress(null);
    }
  }, [whatIfBusy, refreshLibrary]);

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
          <TabsTrigger value="whatif">What-if simulator</TabsTrigger>
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

        {/* ── Tab 4: What-if crisis simulator (P5) + joint memory (P6) ───── */}
        <TabsContent value="whatif">
          <WhatIfSection
            positionCount={positionCount}
            positionsLoaded={!!dash}
            meta={whatIfMeta}
            result={whatIf}
            busy={whatIfBusy}
            progress={whatIfProgress}
            error={whatIfError}
            onRun={runWhatIf}
            parentId={buildOn?.id ?? null}
            parentLabel={buildOn?.category ?? null}
            onClearParent={() => setBuildOn(null)}
          />
          <ScenarioLibrary
            entries={library}
            onRefresh={refreshLibrary}
            onBuildOn={(id, category) => {
              setBuildOn({ id, category });
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
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
              : 'Replays your current holdings through seven historical crises using actual price history — no models, no LLM.'}
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
            <SectorWinnersLaggards perf={ev.sector_performance} />
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

/** Sector winners/laggards — top-3 vs bottom-3 window returns across the
 * GICS sector SPDRs (curated, calibrated to FMP EOD). Sectors whose ETFs
 * did not exist yet are named as absent, never zero-filled. */
function SectorWinnersLaggards({ perf }: { perf: ReplaySectorPerf[] | undefined }) {
  if (!perf || perf.length === 0) return null;
  const best = perf.slice(0, 3);
  const worst = perf.slice(-3);
  const listed = new Set(perf.map(s => s.symbol));
  const missing = ALL_SECTOR_ETFS.filter(e => !listed.has(e.symbol));

  const row = (s: ReplaySectorPerf) => (
    <div key={s.symbol} className="flex items-center justify-between gap-2 text-xs px-2 py-1 rounded bg-muted/30">
      <span className="truncate">
        {s.sector} <span className="text-muted-foreground">({s.symbol})</span>
      </span>
      <RetText value={s.return_pct} />
    </div>
  );

  return (
    <div className="mt-3">
      <div className="grid md:grid-cols-2 gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1"
               title="Best window returns across the GICS sector SPDR ETFs — positive (green) means the sector actually rose during the crisis">
            Sectors that held up
          </div>
          <div className="space-y-1">{best.map(row)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1"
               title="Worst window returns across the GICS sector SPDR ETFs">
            Sectors hit hardest
          </div>
          <div className="space-y-1">{worst.map(row)}</div>
        </div>
      </div>
      {missing.length > 0 && (
        <p className="text-[10px] text-muted-foreground mt-1.5">
          {missing.map(m => `${m.sector} (${m.symbol})`).join(' and ')} ETF{missing.length > 1 ? 's' : ''} not
          listed yet during this window — excluded, not zero-filled.
        </p>
      )}
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

      {/* Sector winners/laggards */}
      <SectorWinnersLaggards perf={ev.sector_performance} />

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

// ── What-if crisis simulator tab (P5) ───────────────────────────────────────

const WHAT_IF_HORIZONS = [30, 60, 90, 180, 365];

const WHAT_IF_ACTION_CLASS: Record<string, string> = {
  SHORT: 'bg-red-600/15 text-red-700 dark:text-red-300 border-red-700/40',
  BUY: 'bg-emerald-600/15 text-emerald-700 dark:text-emerald-300 border-emerald-700/40',
  GOLD: 'bg-amber-600/15 text-amber-700 dark:text-amber-200 border-amber-700/40',
  CASH: 'bg-sky-600/15 text-sky-700 dark:text-sky-300 border-sky-700/40',
  HOLD: 'bg-muted text-muted-foreground border-border',
};

function WhatIfSection({
  positionCount, positionsLoaded, meta, result, busy, progress, error, onRun,
  parentId, parentLabel, onClearParent,
}: {
  positionCount: number;
  positionsLoaded: boolean;
  meta: WhatIfMeta | null;
  result: WhatIfResult | null;
  busy: boolean;
  progress: string | null;
  error: string | null;
  onRun: (req: {
    category: string; concerns: string; reference_key: string | null;
    search_override: 'auto' | 'always' | 'never'; horizon_days: number;
    share: boolean; parent_id: string | null;
  }) => Promise<void>;
  parentId: string | null;
  parentLabel: string | null;
  onClearParent: () => void;
}) {
  const [category, setCategory] = useState('');
  const [concerns, setConcerns] = useState('');
  const [referenceKey, setReferenceKey] = useState('');
  const [searchOverride, setSearchOverride] = useState<'auto' | 'always' | 'never'>('auto');
  const [horizon, setHorizon] = useState(90);
  const [share, setShare] = useState(true);

  // Default category once metadata arrives (dropdown is meta-driven)
  useEffect(() => {
    if (meta && !category) setCategory(meta.categories[0] ?? 'Custom');
  }, [meta, category]);

  const selectCls = 'rounded-md border border-input bg-background px-2.5 py-1.5 text-sm';

  function run() {
    if (positionCount === 0) return;
    if (concerns.trim().length < 10) {
      toast.error('Describe your concerns in at least a sentence — the scenario is built from them.');
      return;
    }
    void onRun({
      category: category || 'Custom',
      concerns: concerns.trim(),
      reference_key: referenceKey || null,
      search_override: searchOverride,
      horizon_days: horizon,
      share,
      parent_id: parentId,
    });
  }

  return (
    <div className="space-y-4">
      {/* Scenario form */}
      <Card className="p-4 mt-2">
        <div className="text-sm font-semibold mb-1">Simulate a crisis that hasn&apos;t happened yet</div>
        <p className="text-[11px] text-muted-foreground mb-3">
          Deterministic skeleton first (sector anchors from the reference crisis, leveraged-product
          time decay computed in closed form), then ONE deepseek-v4-flash call adds the sectoral
          narrative, assumptions to watch and recommendations. Short/inverse products you hold
          (PSQ, MUD, CORD…) are modelled explicitly.
        </p>

        {parentId && (
          <div className="mb-3 flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-violet-700/40 bg-violet-600/10 text-violet-700 dark:text-violet-300 text-[11px]">
              <GitFork size={12} />
              Building on: {parentLabel ?? 'shared scenario'}
            </span>
            <button
              onClick={onClearParent}
              className="text-muted-foreground hover:text-foreground"
              title="Start fresh instead of building on this scenario"
            >
              <X size={13} />
            </button>
          </div>
        )}

        <div className="grid md:grid-cols-4 gap-3">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Category</label>
            <select value={category} onChange={e => setCategory(e.target.value)} className={`${selectCls} w-full`}>
              {(meta?.categories ?? ['Custom']).map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1"
                   title="Closest historical crisis — its calibrated sector returns anchor the scenario. None = model from concerns only.">
              Reference crisis
            </label>
            <select value={referenceKey} onChange={e => setReferenceKey(e.target.value)} className={`${selectCls} w-full`}>
              <option value="">None — model from concerns only</option>
              {(meta?.reference_events ?? []).map(ev => (
                <option key={ev.key} value={ev.key}>
                  {ev.name} (SPY {ev.spy_return_pct}%)
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Horizon</label>
            <select value={horizon} onChange={e => setHorizon(parseInt(e.target.value, 10))} className={`${selectCls} w-full`}>
              {WHAT_IF_HORIZONS.map(d => (
                <option key={d} value={d}>{d} days{d === 90 ? ' (1 quarter)' : ''}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1"
                   title="auto = search only when the engine decides it's needed (unknown short product, no reference crisis). Tavily quota permitting.">
              Online search
            </label>
            <select value={searchOverride}
                    onChange={e => setSearchOverride(e.target.value as 'auto' | 'always' | 'never')}
                    className={`${selectCls} w-full`}>
              <option value="auto">Auto (recommended)</option>
              <option value="always">Always search</option>
              <option value="never">Never search</option>
            </select>
          </div>
        </div>

        <div className="mt-3">
          <label className="text-xs text-muted-foreground block mb-1">
            Industry &amp; macro concerns
          </label>
          <textarea
            value={concerns}
            onChange={e => setConcerns(e.target.value)}
            rows={3}
            placeholder="e.g. Rising rates pressure AI data centres — PE-owned capacity is rate-sensitive; circular vendor financing; multiple-compression risk in memory stocks…"
            className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm"
          />
        </div>

        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <Button onClick={run} disabled={busy || positionCount === 0} className="gap-1.5">
            {busy ? <Loader2 size={15} className="animate-spin" /> : <FlaskConical size={15} />}
            {busy ? (progress || 'Simulating…') : result ? 'Re-run simulation' : 'Simulate scenario'}
          </Button>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
            <Checkbox
              checked={share}
              onCheckedChange={(v) => setShare(v === true)}
              id="whatif-share"
            />
            <span>
              Publish to scenario memory
              <span className="text-muted-foreground/70"> (others can see &amp; build on it)</span>
            </span>
          </label>
          <span className="text-xs text-muted-foreground max-w-xl">
            {positionCount === 0
              ? positionsLoaded
                ? 'Add positions on the first tab — the simulation compares the scenario against your actual holdings.'
                : 'Loading portfolio…'
              : meta?.product_map?.length
                ? `Short-product knowledge: ${meta.product_map.map(p =>
                    p.confidence === 'unknown'
                      ? `${p.ticker} (unknown — add a note)`
                      : `${p.ticker} ${p.leverage}×${p.underlying} (${p.confidence})`).join(' · ')}`
                : 'Runs against your current holdings on a cost basis.'}
          </span>
        </div>
      </Card>

      {error && (
        <Card className="p-4 text-sm text-red-600 dark:text-red-400 flex items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
          <span>{error}</span>
        </Card>
      )}

      {busy && !result && (
        <Card className="p-4 text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 size={15} className="animate-spin" />
          {progress || 'Building scenario…'}
        </Card>
      )}

      {result && <WhatIfResultView result={result} />}
    </div>
  );
}

function WhatIfResultView({ result }: { result: WhatIfResult }) {
  const llm = result.llm;
  const skel = result.skeleton;
  const ref = result.reference_event;

  return (
    <div className="space-y-4">
      {/* Header card */}
      <Card className="p-4">
        <div className="flex items-start justify-between gap-2 flex-wrap">
          <div>
            <div className="text-sm font-semibold">{result.category}</div>
            <div className="text-[11px] text-muted-foreground mt-0.5 max-w-2xl">{result.concerns}</div>
          </div>
          <div className="flex gap-1.5 flex-wrap">
            <span className="px-2 py-0.5 rounded border text-[11px] bg-muted/40 border-border">
              {result.horizon_days}d horizon
            </span>
            {ref && (
              <span className="px-2 py-0.5 rounded border text-[11px] bg-muted/40 border-border">
                anchor: {ref.name}
              </span>
            )}
            <span
              className={`px-2 py-0.5 rounded border text-[11px] ${
                result.search.used
                  ? 'bg-sky-600/15 text-sky-700 dark:text-sky-300 border-sky-700/40'
                  : result.search.recommended
                    ? 'bg-amber-600/15 text-amber-700 dark:text-amber-200 border-amber-700/40'
                    : 'bg-muted text-muted-foreground border-border'}`}
              title={result.search.reasons.join('; ') || 'No search needed'}
            >
              search: {result.search.used ? 'used'
                : result.search.unavailable ? 'unavailable'
                : result.search.recommended ? 'recommended, no results' : 'not needed'}
            </span>
          </div>
        </div>

        {/* Warnings (assumed classifications, unknown products, missing LLM) */}
        {result.warnings.length > 0 && (
          <div className="mt-3 p-2.5 rounded border border-amber-600/40 bg-amber-600/10 text-[11px] text-amber-800 dark:text-amber-200 space-y-1">
            {result.warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2">
                <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        )}

        {/* Headline tiles */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
          <div className="p-2.5 rounded-md bg-muted/40">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Portfolio est. impact</div>
            <div className="text-lg font-semibold"><RetText value={skel.portfolio_est_impact_pct} /></div>
            <div className="text-[10px] text-muted-foreground">cost-basis weighted</div>
          </div>
          <div className="p-2.5 rounded-md bg-muted/40">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Coverage</div>
            <div className="text-lg font-semibold tabular-nums">
              {skel.covered_weight_pct != null ? `${fmtNum(skel.covered_weight_pct, 1)}%` : '—'}
            </div>
            <div className="text-[10px] text-muted-foreground">of cost basis estimated</div>
          </div>
          <div className="p-2.5 rounded-md bg-muted/40">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Model</div>
            <div className="text-lg font-semibold">{result.model.name.replace('deepseek-v4-flash', 'DS-v4-flash')}</div>
            <div className="text-[10px] text-muted-foreground">est. cost ${result.model.cost_usd_est.toFixed(4)}</div>
          </div>
          <div className="p-2.5 rounded-md bg-muted/40">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Reference SPY/QQQ</div>
            <div className="text-lg font-semibold">
              {ref ? (<><RetText value={ref.benchmarks.spy_return_pct} /> / <RetText value={ref.benchmarks.qqq_return_pct} /></>) : '—'}
            </div>
            <div className="text-[10px] text-muted-foreground">{ref ? `${ref.window.start} → ${ref.window.end}` : 'no anchor'}</div>
          </div>
        </div>
      </Card>

      {/* Scenario narrative */}
      {llm && (
        <Card className="p-4">
          <div className="text-sm font-semibold mb-2">Scenario</div>
          <p className="text-sm leading-relaxed">{llm.scenario_summary}</p>
          <div className="grid md:grid-cols-2 gap-3 mt-3">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Most affected</div>
              <div className="flex gap-1 flex-wrap">
                {llm.most_affected_sectors.map(s => (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-red-600/10 text-red-700 dark:text-red-300 text-[10px]">{s}</span>
                ))}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Hedged / holds up</div>
              <div className="flex gap-1 flex-wrap">
                {llm.hedged_sectors.map(s => (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-emerald-600/10 text-emerald-700 dark:text-emerald-300 text-[10px]">{s}</span>
                ))}
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Sector impacts */}
      {llm && llm.sector_impacts.length > 0 && (
        <Card className="p-4">
          <div className="text-sm font-semibold mb-2">Sector-level scenario</div>
          <div className="space-y-1">
            {[...llm.sector_impacts]
              .sort((a, b) => b.est_return_pct - a.est_return_pct)
              .map(si => (
                <div key={si.sector} className="grid grid-cols-[11rem_4rem_1fr] gap-2 items-center text-xs px-2 py-1 rounded bg-muted/30">
                  <span className="truncate">
                    {si.sector}{si.symbol ? <span className="text-muted-foreground"> ({si.symbol})</span> : null}
                  </span>
                  <RetText value={si.est_return_pct} />
                  <span className="text-muted-foreground truncate" title={si.rationale}>{si.rationale}</span>
                </div>
              ))}
          </div>
        </Card>
      )}

      {/* Assumptions to watch */}
      {llm && llm.assumptions_to_watch.length > 0 && (
        <Card className="p-4">
          <div className="text-sm font-semibold mb-1">Assumptions to watch across the quarterlies</div>
          <p className="text-[11px] text-muted-foreground mb-2">
            The datapoints that confirm or disconfirm this scenario as the quarter unfolds.
          </p>
          <div className="grid md:grid-cols-2 gap-2">
            {llm.assumptions_to_watch.map((a, i) => (
              <div key={i} className="p-2.5 rounded-md bg-muted/40">
                <div className="text-xs font-semibold">{a.metric}</div>
                <div className="text-[11px] text-muted-foreground mt-0.5">{a.watch_for}</div>
                <div className="text-[10px] text-primary mt-1">{a.timing}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Holdings impact (deterministic skeleton + LLM rationale) */}
      <Card className="p-4 overflow-x-auto">
        <div className="text-sm font-semibold mb-2">Your holdings under this scenario</div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Holding</TableHead>
              <TableHead>Type</TableHead>
              <TableHead className="text-right">Est. impact</TableHead>
              <TableHead className="text-right">Time-decay drag</TableHead>
              <TableHead>Rationale</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {skel.holdings.map(h => {
              const llmRow = llm?.holding_impacts.find(r => r.ticker === h.ticker);
              return <WhatIfHoldingRow key={h.ticker} h={h} rationale={llmRow?.rationale ?? null} />;
            })}
          </TableBody>
        </Table>
        <p className="text-[10px] text-muted-foreground mt-2">
          Product estimates are closed-form: log(return) ≈ k·L − k(k−1)/2·N·σ². Inverse and leveraged
          products decay with volatility even when the underlying moves in your favour — the drag column
          shows the percentage points lost to that effect over the horizon.
        </p>
      </Card>

      {/* Recommendations */}
      {llm && llm.recommendations.length > 0 && (
        <Card className="p-4">
          <div className="text-sm font-semibold mb-2">Recommended tools</div>
          <div className="grid md:grid-cols-2 gap-2">
            {llm.recommendations.map((r, i) => (
              <div key={i} className="p-2.5 rounded-md border border-border">
                <div className="flex items-center justify-between gap-2">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${WHAT_IF_ACTION_CLASS[r.action] ?? WHAT_IF_ACTION_CLASS.HOLD}`}>
                    {r.action}
                  </span>
                  <span className="text-xs font-semibold">{r.instrument}</span>
                  <span className="text-[10px] text-muted-foreground tabular-nums" title="Model confidence">
                    {Math.round(r.confidence * 100)}%
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground mt-1.5">{r.rationale}</p>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-muted-foreground mt-2">
            Not investment advice — scenario-conditioned suggestions from a single cheap-model pass
            {result.search.used ? ', grounded in the search evidence gathered above' : ''}.
          </p>
        </Card>
      )}
    </div>
  );
}

function WhatIfHoldingRow({ h, rationale }: { h: WhatIfHoldingSkeleton; rationale: string | null }) {
  const typeBadge = h.kind === 'product' ? (
    <span className={`px-1.5 py-0.5 rounded border text-[10px] ${
      h.product?.confidence === 'confirmed'
        ? 'bg-emerald-600/10 text-emerald-700 dark:text-emerald-300 border-emerald-700/40'
        : h.product?.confidence === 'assumed'
          ? 'bg-amber-600/15 text-amber-700 dark:text-amber-200 border-amber-700/40'
          : 'bg-muted text-muted-foreground border-border'}`}
      title={h.product?.name ?? ''}>
      {h.product?.leverage != null ? `${h.product.leverage}×` : ''}{h.product?.underlying ?? '?'}
      {h.product?.confidence === 'assumed' ? ' (assumed)' : ''}
    </span>
  ) : h.kind === 'unknown_product' ? (
    <span className="px-1.5 py-0.5 rounded border text-[10px] bg-red-600/10 text-red-700 dark:text-red-300 border-red-700/40"
          title={h.product?.hint ?? 'Unknown product'}>
      unclassified
    </span>
  ) : (
    <span className="text-[10px] text-muted-foreground">{h.gics ?? h.sector ?? '—'}</span>
  );

  return (
    <TableRow>
      <TableCell className="font-medium">{h.ticker}</TableCell>
      <TableCell>{typeBadge}</TableCell>
      <TableCell className="text-right">
        {h.est_impact_pct != null ? <RetText value={h.est_impact_pct} /> : <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell className="text-right">
        {h.decay_drag_pp != null ? (
          <span title={`No-decay estimate ${fmtNum(h.no_decay_return_pct ?? null)}% · vol ${fmtNum(h.vol_pct ?? null)}% (${h.vol_source ?? ''}), ${h.horizon_days ?? 90}d`}>
            <RetText value={h.decay_drag_pp} />
          </span>
        ) : <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell className="text-[11px] text-muted-foreground max-w-72">{rationale ?? '—'}</TableCell>
    </TableRow>
  );
}

// ── P6: joint scenario memory ───────────────────────────────────────────────
// Completed what-if runs auto-publish (opt-out in the form) into a shared
// library. Anyone can read the narrative, compare the scenario against their
// OWN portfolio deterministically, append notes, fork it as a starting
// point, and verify the scenario's assumptions against market data or a
// deep-research sweep. Sensitivity numbers are Python re-running the
// skeleton math — never the LLM.

const VERDICT_CLASS: Record<string, string> = {
  confirmed: 'bg-emerald-600/15 text-emerald-700 dark:text-emerald-300 border-emerald-700/40',
  disconfirmed: 'bg-red-600/15 text-red-700 dark:text-red-300 border-red-700/40',
  inconclusive: 'bg-amber-600/15 text-amber-700 dark:text-amber-200 border-amber-700/40',
  no_data: 'bg-muted text-muted-foreground border-border',
  open: 'bg-sky-600/15 text-sky-700 dark:text-sky-300 border-sky-700/40',
};

function ScenarioLibrary({ entries, onRefresh, onBuildOn }: {
  entries: WhatIfLibraryEntry[] | null;
  onRefresh: () => Promise<void>;
  onBuildOn: (id: string, category: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (selectedId) {
    return (
      <ScenarioDetail
        scenarioId={selectedId}
        onBack={() => setSelectedId(null)}
        onOpen={setSelectedId}
        onBuildOn={(id, category) => { setSelectedId(null); onBuildOn(id, category); }}
      />
    );
  }

  return (
    <Card className="p-4 mt-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <BookOpen size={16} className="text-muted-foreground" />
          <div className="text-sm font-semibold">Scenario memory</div>
          <span className="text-[11px] text-muted-foreground">
            shared by everyone — open one to compare it against your portfolio,
            check its assumptions, or build on it
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={() => void onRefresh()} className="gap-1.5">
          <RefreshCw size={13} /> Refresh
        </Button>
      </div>

      {entries === null ? (
        <div className="mt-3 p-4 rounded-md border border-border text-sm text-muted-foreground flex items-center justify-between gap-3 flex-wrap">
          <span>Couldn&apos;t load the scenario library.</span>
          <Button variant="outline" size="sm" onClick={() => void onRefresh()}>Retry</Button>
        </div>
      ) : entries.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          No shared scenarios yet — run a simulation above with
          &ldquo;Publish to scenario memory&rdquo; on and it will appear here for everyone.
        </p>
      ) : (
        <div className="grid md:grid-cols-2 gap-3 mt-3">
          {entries.map(e => (
            <div key={e.scenario_id} className="p-3 rounded-md border border-border bg-muted/20 flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold truncate">{e.category}</span>
                <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                  {e.created_by_name ?? 'anonymous'} · {ageLabel(e.created_at)}
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground line-clamp-2">{e.concerns_excerpt}</p>
              <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
                {e.reference_key && (
                  <span className="px-1.5 py-0.5 rounded border border-border bg-muted/40">
                    anchor: {e.reference_key}
                  </span>
                )}
                {e.horizon_days != null && (
                  <span className="px-1.5 py-0.5 rounded border border-border bg-muted/40">
                    {e.horizon_days}d
                  </span>
                )}
                {e.author_portfolio_est_pct != null && (
                  <span className="px-1.5 py-0.5 rounded border border-border bg-muted/40">
                    author: <RetText value={e.author_portfolio_est_pct} />
                  </span>
                )}
                {Object.entries(e.assumption_status_tally ?? {})
                  .filter(([k, n]) => n > 0 && k !== 'open')
                  .map(([k, n]) => (
                    <span key={k} className={`px-1.5 py-0.5 rounded border ${VERDICT_CLASS[k] ?? VERDICT_CLASS.open}`}>
                      {n} {k}
                    </span>
                  ))}
                {e.build_count > 0 && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-violet-700/40 bg-violet-600/10 text-violet-700 dark:text-violet-300">
                    <GitFork size={10} /> {e.build_count}
                  </span>
                )}
                {e.notes_count > 0 && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-border bg-muted/40">
                    <MessageSquare size={10} /> {e.notes_count}
                  </span>
                )}
              </div>
              <div>
                <Button variant="outline" size="sm" onClick={() => setSelectedId(e.scenario_id)}>
                  Open scenario
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function ScenarioDetail({ scenarioId, onBack, onOpen, onBuildOn }: {
  scenarioId: string;
  onBack: () => void;
  onOpen: (id: string) => void;
  onBuildOn: (id: string, category: string) => void;
}) {
  const [detail, setDetail] = useState<WhatIfScenarioDetail | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [compare, setCompare] = useState<WhatIfCompareResult | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);
  const [compareErr, setCompareErr] = useState<string | null>(null);
  const [noteText, setNoteText] = useState('');
  const [noteBusy, setNoteBusy] = useState(false);
  const [checking, setChecking] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      setDetail(await getWhatIfScenario(scenarioId));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load scenario');
    } finally {
      setBusy(false);
    }
  }, [scenarioId]);

  useEffect(() => {
    setCompare(null);
    setCompareErr(null);
    setNoteText('');
    setChecking({});
    void load();
  }, [load]);

  async function runCompare() {
    setCompareBusy(true);
    setCompareErr(null);
    try {
      setCompare(await compareWhatIfToHoldings(scenarioId));
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Compare failed';
      setCompareErr(msg.includes('HTTP 400')
        ? 'Add holdings on the first tab — the comparison uses your actual positions.'
        : msg);
    } finally {
      setCompareBusy(false);
    }
  }

  async function submitNote() {
    const text = noteText.trim();
    if (!text || noteBusy) return;
    setNoteBusy(true);
    try {
      await addWhatIfNote(scenarioId, text);
      setNoteText('');
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not add note';
      if (msg.includes('HTTP 401')) toast.error('Sign in to add notes.');
      else toast.error(msg);
    } finally {
      setNoteBusy(false);
    }
  }

  async function runCheck(a: WhatIfLibraryAssumption, method: 'market_data' | 'deep_research') {
    if (checking[a.assumption_id]) return;
    setChecking(prev => ({ ...prev, [a.assumption_id]: method }));
    try {
      const start = await startAssumptionCheck(a.assumption_id, method);
      const final = await pollWhatIfJob(start.job_id, { timeoutMs: 8 * 60 * 1000 });
      if (final.status === 'completed') {
        toast.success(`${a.metric}: check ${final.result?.verdict ?? 'finished'}.`);
      } else {
        toast.error(`Check failed: ${final.error ?? final.status}`);
      }
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Check failed');
    } finally {
      setChecking(prev => {
        const next = { ...prev };
        delete next[a.assumption_id];
        return next;
      });
    }
  }

  if (busy) {
    return (
      <Card className="p-4 mt-4 text-sm text-muted-foreground flex items-center gap-2">
        <Loader2 size={15} className="animate-spin" /> Loading scenario…
      </Card>
    );
  }
  if (err || !detail) {
    return (
      <Card className="p-4 mt-4">
        <div className="text-sm text-red-600 dark:text-red-400 flex items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
          <span>{err ?? 'Scenario not found.'}</span>
        </div>
        <Button variant="outline" size="sm" onClick={onBack} className="mt-3">Back to library</Button>
      </Card>
    );
  }

  const llm = detail.result?.llm ?? null;
  const skel = detail.result?.skeleton;

  return (
    <div className="space-y-4 mt-4">
      {/* Header */}
      <Card className="p-4">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <button onClick={onBack} className="text-muted-foreground hover:text-foreground" title="Back to library">
                <X size={16} />
              </button>
              <span className="text-sm font-semibold">{detail.category}</span>
              {detail.horizon_days != null && (
                <span className="px-2 py-0.5 rounded border text-[11px] bg-muted/40 border-border">
                  {detail.horizon_days}d horizon
                </span>
              )}
              {detail.reference_key && (
                <span className="px-2 py-0.5 rounded border text-[11px] bg-muted/40 border-border">
                  anchor: {detail.reference_key}
                </span>
              )}
            </div>
            <div className="text-[11px] text-muted-foreground mt-1">
              {detail.created_by_name ?? 'anonymous'} · {ageLabel(detail.created_at)}
              {detail.build_count > 0 && ` · built on ${detail.build_count}×`}
              {detail.children_count > 0 && ` · ${detail.children_count} fork${detail.children_count > 1 ? 's' : ''}`}
            </div>
            <p className="text-[11px] text-muted-foreground mt-1.5 max-w-2xl">{detail.concerns}</p>
            {detail.parent && (
              <button
                onClick={() => onOpen(detail.parent!.scenario_id)}
                className="mt-2 inline-flex items-center gap-1.5 px-2 py-1 rounded border border-violet-700/40 bg-violet-600/10 text-violet-700 dark:text-violet-300 text-[11px] hover:bg-violet-600/20"
                title="Open the scenario this one was built on"
              >
                <GitFork size={12} />
                Built on: {detail.parent.category} ({detail.parent.created_by_name ?? 'anonymous'})
              </button>
            )}
          </div>
          <Button onClick={() => onBuildOn(detail.scenario_id, detail.category)} className="gap-1.5">
            <GitFork size={15} /> Build on this scenario
          </Button>
        </div>

        {/* Author headline tile */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-3">
          <div className="p-2.5 rounded-md bg-muted/40">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Author&apos;s portfolio est. impact</div>
            <div className="text-lg font-semibold">
              <RetText value={skel?.portfolio_est_impact_pct ?? null} />
            </div>
            <div className="text-[10px] text-muted-foreground">cost-basis weighted</div>
          </div>
          <div className="p-2.5 rounded-md bg-muted/40">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Assumptions tracked</div>
            <div className="text-lg font-semibold tabular-nums">{detail.assumptions.length}</div>
            <div className="text-[10px] text-muted-foreground">verifiable datapoints</div>
          </div>
          <div className="p-2.5 rounded-md bg-muted/40">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Community notes</div>
            <div className="text-lg font-semibold tabular-nums">{detail.notes.length}</div>
            <div className="text-[10px] text-muted-foreground">append your own below</div>
          </div>
        </div>
      </Card>

      {/* Narrative (author's run result) */}
      {llm && (
        <Card className="p-4">
          <div className="text-sm font-semibold mb-2">Scenario narrative</div>
          <p className="text-sm leading-relaxed">{llm.scenario_summary}</p>
          <div className="grid md:grid-cols-2 gap-3 mt-3">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Most affected</div>
              <div className="flex gap-1 flex-wrap">
                {llm.most_affected_sectors.map(s => (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-red-600/10 text-red-700 dark:text-red-300 text-[10px]">{s}</span>
                ))}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Hedged / holds up</div>
              <div className="flex gap-1 flex-wrap">
                {llm.hedged_sectors.map(s => (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-emerald-600/10 text-emerald-700 dark:text-emerald-300 text-[10px]">{s}</span>
                ))}
              </div>
            </div>
          </div>
          {llm.sector_impacts.length > 0 && (
            <div className="space-y-1 mt-3">
              {[...llm.sector_impacts]
                .sort((a, b) => b.est_return_pct - a.est_return_pct)
                .map(si => (
                  <div key={si.sector} className="grid grid-cols-[11rem_4rem_1fr] gap-2 items-center text-xs px-2 py-1 rounded bg-muted/30">
                    <span className="truncate">
                      {si.sector}{si.symbol ? <span className="text-muted-foreground"> ({si.symbol})</span> : null}
                    </span>
                    <RetText value={si.est_return_pct} />
                    <span className="text-muted-foreground truncate" title={si.rationale}>{si.rationale}</span>
                  </div>
                ))}
            </div>
          )}
        </Card>
      )}

      {/* Compare to my portfolio (deterministic, viewer-specific) */}
      <Card className="p-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <div className="text-sm font-semibold">Compare to my portfolio</div>
            <p className="text-[11px] text-muted-foreground max-w-xl">
              Re-runs the scenario&apos;s deterministic skeleton against YOUR holdings — no LLM,
              no re-simulation of the narrative. Sensitivities show how each assumption,
              if it holds, moves your portfolio.
            </p>
          </div>
          <Button onClick={() => void runCompare()} disabled={compareBusy} className="gap-1.5">
            {compareBusy ? <Loader2 size={15} className="animate-spin" /> : <PieChart size={15} />}
            {compareBusy ? 'Computing…' : compare ? 'Recompute' : 'Compare'}
          </Button>
        </div>

        {compareErr && (
          <div className="mt-3 text-xs text-red-600 dark:text-red-400">{compareErr}</div>
        )}

        {compare && (
          <div className="mt-3 space-y-3">
            <div className="grid grid-cols-2 gap-3 max-w-md">
              <div className="p-2.5 rounded-md bg-muted/40">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Your est. impact</div>
                <div className="text-lg font-semibold">
                  <RetText value={compare.skeleton.portfolio_est_impact_pct} />
                </div>
              </div>
              <div className="p-2.5 rounded-md bg-muted/40">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Coverage</div>
                <div className="text-lg font-semibold tabular-nums">
                  {compare.skeleton.covered_weight_pct != null
                    ? `${fmtNum(compare.skeleton.covered_weight_pct, 1)}%` : '—'}
                </div>
              </div>
            </div>

            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Holding</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className="text-right">Est. impact</TableHead>
                    <TableHead className="text-right">Time-decay drag</TableHead>
                    <TableHead>Rationale</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {compare.skeleton.holdings.map(h => (
                    <WhatIfHoldingRow key={h.ticker} h={h} rationale={null} />
                  ))}
                </TableBody>
              </Table>
            </div>

            {compare.assumption_sensitivities.length > 0 && (
              <div>
                <div className="text-xs font-semibold mb-1.5">Assumption sensitivity on your portfolio</div>
                <div className="space-y-1">
                  {compare.assumption_sensitivities.map(s => (
                    <div key={s.assumption_id} className="flex items-center gap-2 flex-wrap text-xs px-2 py-1.5 rounded bg-muted/30">
                      <span className="font-medium truncate max-w-56" title={s.metric}>{s.metric}</span>
                      {s.base_portfolio_est_pct != null ? (
                        <span className="text-muted-foreground tabular-nums">
                          if holds: <RetText value={s.base_portfolio_est_pct} /> →{' '}
                          <RetText value={s.adjusted_portfolio_est_pct} />
                          <span className="ml-1">
                            ({s.delta_pp > 0 ? '+' : ''}{fmtNum(s.delta_pp, 1)}pp)
                          </span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">no portfolio linkage</span>
                      )}
                      {s.affected_tickers.length > 0 && (
                        <span className="text-muted-foreground">· {s.affected_tickers.join(', ')}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <p className="text-[10px] text-muted-foreground">
              Computed {new Date(compare.computed_at).toLocaleString()} · deterministic skeleton math only.
            </p>
          </div>
        )}
      </Card>

      {/* Assumptions ledger */}
      {detail.assumptions.length > 0 && (
        <Card className="p-4">
          <div className="text-sm font-semibold mb-1">Assumptions to watch</div>
          <p className="text-[11px] text-muted-foreground mb-3">
            Anyone can verify whether an assumption is holding true — market data checks a
            deterministic FMP reading; research check runs one web-search sweep. Verdicts are
            LLM judgement, always attributed.
          </p>
          <div className="space-y-2.5">
            {detail.assumptions.map(a => {
              const inFlight = checking[a.assumption_id];
              return (
                <div key={a.assumption_id} className="p-2.5 rounded-md bg-muted/40">
                  <div className="flex items-start justify-between gap-2 flex-wrap">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold">{a.metric}</div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">{a.watch_for}</div>
                      <div className="text-[10px] text-primary mt-1">{a.timing}</div>
                    </div>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className={`px-1.5 py-0.5 rounded border text-[10px] ${VERDICT_CLASS[a.status] ?? VERDICT_CLASS.open}`}>
                        {a.status}
                      </span>
                      {a.author_delta && a.author_delta.base_portfolio_est_pct != null && (
                        <span
                          className="px-1.5 py-0.5 rounded border border-border bg-muted/60 text-[10px] tabular-nums"
                          title={`Author's portfolio if this holds — ${a.author_delta.affected_tickers.join(', ') || 'no direct linkage'}`}
                        >
                          if holds: <RetText value={a.author_delta.base_portfolio_est_pct} /> →{' '}
                          <RetText value={a.author_delta.adjusted_portfolio_est_pct} />
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                    <Button
                      variant="outline" size="sm"
                      disabled={!!inFlight}
                      onClick={() => void runCheck(a, 'market_data')}
                      className="gap-1 text-[11px]"
                      title="Deterministic FMP reading (rates / inflation / sector ETF) judged against the watch-point"
                    >
                      {inFlight === 'market_data' ? <Loader2 size={12} className="animate-spin" /> : null}
                      Market data
                    </Button>
                    <Button
                      variant="outline" size="sm"
                      disabled={!!inFlight}
                      onClick={() => void runCheck(a, 'deep_research')}
                      className="gap-1 text-[11px]"
                      title="One web-research sweep (qwen) judging the latest evidence"
                    >
                      {inFlight === 'deep_research' ? <Loader2 size={12} className="animate-spin" /> : null}
                      Research check
                    </Button>
                    {a.checks_count > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        {a.checks_count} check{a.checks_count > 1 ? 's' : ''} recorded
                      </span>
                    )}
                  </div>
                  {a.latest_check && (
                    <div className="mt-2 p-2 rounded border border-border bg-background/60">
                      <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
                        <span className={`px-1.5 py-0.5 rounded border ${VERDICT_CLASS[a.latest_check.verdict] ?? VERDICT_CLASS.open}`}>
                          {a.latest_check.verdict}
                        </span>
                        <span className="text-muted-foreground">
                          {a.latest_check.user_name ?? 'anonymous'} · {a.latest_check.method === 'market_data' ? 'market data' : 'research'} · {ageLabel(a.latest_check.checked_at)}
                        </span>
                        {a.latest_check.source && (
                          <span className="text-muted-foreground truncate max-w-52" title={a.latest_check.source}>
                            src: {a.latest_check.source}
                          </span>
                        )}
                      </div>
                      {a.latest_check.evidence && (
                        <p className="text-[11px] text-muted-foreground mt-1">{a.latest_check.evidence}</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Community notes */}
      <Card className="p-4">
        <div className="text-sm font-semibold mb-2">Community notes</div>
        {detail.notes.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No notes yet — add the first observation below.</p>
        ) : (
          <div className="space-y-2 mb-3">
            {detail.notes.map(n => (
              <div key={n.note_id} className="p-2 rounded border border-border">
                <div className="text-[10px] text-muted-foreground">
                  {n.user_name ?? 'anonymous'} · {ageLabel(n.created_at)}
                </div>
                <p className="text-xs mt-0.5 whitespace-pre-wrap">{n.note}</p>
              </div>
            ))}
          </div>
        )}
        <div className="flex gap-2 items-start">
          <textarea
            value={noteText}
            onChange={e => setNoteText(e.target.value)}
            rows={2}
            placeholder="Add an observation, datapoint or update for everyone tracking this scenario…"
            className="flex-1 rounded-md border border-input bg-background px-2.5 py-1.5 text-xs"
          />
          <Button onClick={() => void submitNote()} disabled={noteBusy || noteText.trim().length === 0} className="gap-1.5">
            {noteBusy ? <Loader2 size={14} className="animate-spin" /> : <MessageSquare size={14} />}
            Add note
          </Button>
        </div>
      </Card>
    </div>
  );
}
