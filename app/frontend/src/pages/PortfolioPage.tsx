import { useCallback, useEffect, useState } from 'react';
import { PieChart, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  getPortfolioDashboard, addHolding, deleteHolding,
  type PortfolioDashboard,
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

// ── page ────────────────────────────────────────────────────────────────────

export function PortfolioPage() {
  const [dash, setDash] = useState<PortfolioDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Add-position form
  const [ticker, setTicker] = useState('');
  const [qty, setQty] = useState('');
  const [cost, setCost] = useState('');
  const [notes, setNotes] = useState('');
  const [adding, setAdding] = useState(false);
  const [removingId, setRemovingId] = useState<number | null>(null);

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
    </PageContainer>
  );
}
