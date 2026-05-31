/**
 * MomentumPage.tsx
 * ================
 * Two-layer signed-momentum screen (turning & accelerating, long + short).
 *
 * - Sector momentum map: every sector ETF scored by the same engine, sorted
 *   by |composite|. A Long/Short tailwinds toggle switches between a green
 *   long table and a red short table (ETF · sector · status · rank).
 * - Ticker table: LONG / SHORT / ALL toggle, ranked by signal strength.
 *   STATE / TURN / ACCEL pillar chips + composite + verdict + sector-alignment
 *   flag. Row click → detail drawer with all evidence (flag notes).
 * - Refresh control with an optional `as_of` date so the whole screen can be
 *   pointed at a historical window (e.g. the software sell-down) for
 *   validation.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getMomentumCohort, refreshMomentum,
  type MomentumCohort, type MomentumTickerResult, type MomentumSectorResult,
  type MomentumDirection,
} from '@/lib/api';
import { ArrowLeft, RefreshCw, Loader2, X } from 'lucide-react';
import { toast } from 'sonner';
import { PageContainer } from '@/components/layout/PageContainer';


// ─── Formatters ────────────────────────────────────────────────────────────

const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;

const fmtPrice = (v: number | null | undefined): string =>
  v == null ? '—' : `$${v.toFixed(2)}`;

const fmtSigned = (v: number | null | undefined, digits = 0): string =>
  v == null || Number.isNaN(v) ? '—' : (v > 0 ? `+${v.toFixed(digits)}` : v.toFixed(digits));

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


// ─── Colour chips ──────────────────────────────────────────────────────────

// Signed composite (-6..+6): green = long, red = short, intensity = strength.
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
  if (v === 'Accelerating-Long') return 'bg-emerald-600/30 text-black dark:text-emerald-200 border-emerald-700/40';
  if (v === 'Turning-Long')      return 'bg-emerald-600/15 text-black dark:text-emerald-300 border-emerald-700/30';
  if (v === 'Accelerating-Short') return 'bg-red-600/30 text-black dark:text-red-200 border-red-700/40';
  if (v === 'Turning-Short')     return 'bg-red-600/15 text-black dark:text-red-300 border-red-700/30';
  return 'bg-muted text-muted-foreground border-border';
}


type DirFilter = 'LONG' | 'SHORT' | 'ALL';

export function MomentumPage() {
  const navigate = useNavigate();

  const [cohort, setCohort] = useState<MomentumCohort | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dir, setDir] = useState<DirFilter>('LONG');
  const [sectorDir, setSectorDir] = useState<'LONG' | 'SHORT'>('LONG');
  const [view, setView] = useState<'SECTOR' | 'EQUITY'>('SECTOR');
  const [selected, setSelected] = useState<MomentumTickerResult | null>(null);

  const load = () => {
    setLoading(true);
    getMomentumCohort()
      .then(setCohort)
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    const toastId = toast.loading('Running live momentum screen…', { duration: Infinity });
    try {
      await refreshMomentum({});
      toast.dismiss(toastId);
      toast.success('Momentum cohort refreshed.');
      load();
    } catch (e) {
      toast.dismiss(toastId);
      toast.error(`Refresh failed: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  };

  const sectors: MomentumSectorResult[] = cohort?.sectors ?? [];
  const longSectors = sectors.filter((s) => (s.composite ?? 0) > 0).sort((a, b) => (b.composite ?? 0) - (a.composite ?? 0));
  const shortSectors = sectors.filter((s) => (s.composite ?? 0) < 0).sort((a, b) => (a.composite ?? 0) - (b.composite ?? 0));

  const rows = useMemo(() => {
    const results = cohort?.results ?? [];
    const filtered = dir === 'ALL' ? results : results.filter((r) => r.direction === dir);
    // Rank by signal strength (|composite|); aligned names break ties.
    return [...filtered].sort((a, b) => {
      const ds = (b.signal_strength ?? 0) - (a.signal_strength ?? 0);
      if (ds !== 0) return ds;
      const aa = a.sector_aligned ? 0 : 1;
      const ba = b.sector_aligned ? 0 : 1;
      if (aa !== ba) return aa - ba;
      return a.ticker.localeCompare(b.ticker);
    });
  }, [cohort, dir]);

  const longCount = cohort?.long_count ?? 0;
  const shortCount = cohort?.short_count ?? 0;

  return (
    <PageContainer size="wide">
      {/* Header */}
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
            <h1 className="text-xl font-semibold text-foreground truncate">Momentum — Turning &amp; Accelerating</h1>
            <p className="text-xs text-muted-foreground">
              Last run {formatRunTime(cohort?.created_at ?? null)}
              {cohort?.as_of ? ` · as of ${cohort.as_of}` : ' · live'}
              {' · '}
              <span className="text-emerald-700 dark:text-emerald-300 font-semibold">{longCount} long</span>
              {' / '}
              <span className="text-red-700 dark:text-red-300 font-semibold">{shortCount} short</span>
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted disabled:opacity-50"
          >
            {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading cohort…
        </div>
      )}

      {!loading && (!cohort || cohort.run_id == null) && (
        <div className="text-sm text-muted-foreground p-6 border border-border rounded-md">
          No momentum run yet — click Refresh to score the cohort.
        </div>
      )}

      {!loading && cohort && cohort.run_id != null && (
        <>
          {/* ── View toggle: Sector vs Equity momentum ─────────────────── */}
          <div className="flex items-center gap-2 mb-4 border-b border-border pb-3">
            {(['SECTOR', 'EQUITY'] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={
                  'px-4 py-2 min-h-[40px] text-sm font-semibold rounded-md border select-none touch-manipulation ' +
                  (view === v
                    ? 'bg-primary/15 border-primary/50 text-primary'
                    : 'border-border text-muted-foreground hover:bg-muted')
                }
              >
                {v === 'SECTOR' ? 'Sector Momentum' : 'Equity Momentum'}
              </button>
            ))}
          </div>

          {view === 'SECTOR' && (
          /* ── Sector momentum map ────────────────────────────────────── */
          <div className="mb-5">
            <div className="flex items-center justify-between gap-3 mb-2 flex-wrap">
              <h2 className="text-sm font-semibold text-foreground">Sector momentum map</h2>
              <div className="flex items-center gap-1.5">
                {(['LONG', 'SHORT'] as const).map((d) => (
                  <button
                    key={d}
                    onClick={() => setSectorDir(d)}
                    className={
                      'px-4 py-2 min-h-[40px] text-sm font-semibold rounded-full border select-none touch-manipulation ' +
                      (sectorDir === d
                        ? (d === 'LONG'
                            ? 'bg-emerald-600/20 border-emerald-600/50 text-emerald-700 dark:text-emerald-300'
                            : 'bg-red-600/20 border-red-600/50 text-red-700 dark:text-red-300')
                        : 'border-border text-muted-foreground hover:bg-muted')
                    }
                  >
                    {d === 'LONG'
                      ? `Long tailwinds (${longSectors.length})`
                      : `Short tailwinds (${shortSectors.length})`}
                  </button>
                ))}
              </div>
            </div>
            <SectorTable sectors={sectorDir === 'LONG' ? longSectors : shortSectors} dir={sectorDir} />
          </div>
          )}

          {view === 'EQUITY' && (
          <>
          {/* ── Direction toggle ──────────────────────────────────────── */}
          <div className="flex items-center gap-2 mb-3">
            {(['LONG', 'SHORT', 'ALL'] as DirFilter[]).map((d) => (
              <button
                key={d}
                onClick={() => setDir(d)}
                className={
                  'px-4 py-2 min-h-[40px] text-sm font-semibold rounded-full border select-none touch-manipulation ' +
                  (dir === d
                    ? (d === 'LONG' ? 'bg-emerald-600/20 border-emerald-600/50 text-emerald-700 dark:text-emerald-300'
                      : d === 'SHORT' ? 'bg-red-600/20 border-red-600/50 text-red-700 dark:text-red-300'
                      : 'bg-primary/15 border-primary/50 text-primary')
                    : 'border-border text-muted-foreground hover:bg-muted')
                }
              >
                {d === 'LONG' ? `Long (${longCount})` : d === 'SHORT' ? `Short (${shortCount})` : `All (${cohort.ticker_count})`}
              </button>
            ))}
          </div>

          {/* ── Ticker table ──────────────────────────────────────────── */}
          <div className="overflow-x-auto border border-border rounded-md">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40 text-[11px] uppercase tracking-wider text-muted-foreground">
                  <th className="text-left px-3 py-2 font-semibold">#</th>
                  <th className="text-left px-3 py-2 font-semibold">Ticker</th>
                  <th className="text-left px-3 py-2 font-semibold">Verdict</th>
                  <th className="text-right px-3 py-2 font-semibold" title="Composite -6..+6">Comp</th>
                  <th className="text-center px-2 py-2 font-semibold" title="STATE pillar (-2..+2)">S</th>
                  <th className="text-center px-2 py-2 font-semibold" title="TURN pillar (-2..+2)">T</th>
                  <th className="text-center px-2 py-2 font-semibold" title="ACCEL pillar (-2..+2)">A</th>
                  <th className="text-right px-3 py-2 font-semibold" title="1-month return">1m</th>
                  <th className="text-right px-3 py-2 font-semibold" title="3-month return">3m</th>
                  <th className="text-center px-2 py-2 font-semibold" title="Sector-aligned (direction agrees with sector ETF)">Sec</th>
                  <th className="text-left px-3 py-2 font-semibold">Sector</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => setSelected(r)}
                    className="border-b border-border/60 hover:bg-muted/40 cursor-pointer"
                  >
                    <td className="px-3 py-2 text-muted-foreground">{r.rank ?? '—'}</td>
                    <td className="px-3 py-2">
                      <span className="font-mono font-bold text-foreground">{r.ticker}</span>
                      <span className="ml-2 text-[11px] text-muted-foreground hidden md:inline">{r.name}</span>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold border ${verdictColor(r.verdict)}`}>
                        {r.verdict}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold font-mono ${compositeColor(r.composite)}`}>
                        {fmtSigned(r.composite)}
                      </span>
                    </td>
                    <td className="px-2 py-2 text-center"><span className={`px-1 py-0.5 rounded text-[11px] font-mono ${pillarColor(r.state_score)}`}>{fmtSigned(r.state_score)}</span></td>
                    <td className="px-2 py-2 text-center"><span className={`px-1 py-0.5 rounded text-[11px] font-mono ${pillarColor(r.turn_score)}`}>{fmtSigned(r.turn_score)}</span></td>
                    <td className="px-2 py-2 text-center"><span className={`px-1 py-0.5 rounded text-[11px] font-mono ${pillarColor(r.accel_score)}`}>{fmtSigned(r.accel_score)}</span></td>
                    <td className="px-3 py-2 text-right font-mono text-[12px]">{fmtPct(r.r_21d)}</td>
                    <td className="px-3 py-2 text-right font-mono text-[12px]">{fmtPct(r.r_63d)}</td>
                    <td className="px-2 py-2 text-center">{r.sector_aligned ? <span className="text-[11px]">✓</span> : ''}</td>
                    <td className="px-3 py-2 text-[12px] text-muted-foreground">{r.sector_etf ?? r.sector ?? '—'}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={11} className="px-3 py-6 text-center text-muted-foreground">No {dir === 'ALL' ? '' : dir.toLowerCase()} names this run.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Column legend */}
          <p className="text-[11px] text-muted-foreground mt-2 leading-relaxed">
            <span className="font-semibold text-foreground">Comp</span> = composite (−6…+6, sum of the three pillars; + long / − short) ·{' '}
            <span className="font-semibold text-foreground">S</span> = State (where the trend is now) ·{' '}
            <span className="font-semibold text-foreground">T</span> = Turn (MACD/SMA/RSI inflection) ·{' '}
            <span className="font-semibold text-foreground">A</span> = Accel (ROC-of-ROC, ADX, volume) — each pillar −2…+2 ·{' '}
            <span className="font-semibold text-foreground">Sec</span> ✓ = direction agrees with its sector ETF
          </p>
          </>
          )}
        </>
      )}

      {/* ── Detail drawer ─────────────────────────────────────────────── */}
      {selected && (
        <DetailDrawer row={selected} onClose={() => setSelected(null)} />
      )}
    </PageContainer>
  );
}


// Strip the direction suffix — the toggle already conveys long vs short.
function verdictShort(v: string): string {
  return v.replace(/-(Long|Short)$/, '');
}

// Drop a trailing "(XXX)" — it just repeats the ETF symbol already in column 1.
function sectorName(s: MomentumSectorResult): string {
  return (s.label ?? s.sector ?? '').replace(/\s*\([^)]*\)\s*$/, '').trim();
}

function SectorTable({ sectors, dir }: {
  sectors: MomentumSectorResult[];
  dir: 'LONG' | 'SHORT';
}) {
  return (
    <div className="overflow-x-auto border border-border rounded-md">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-[11px] uppercase tracking-wider text-muted-foreground">
            <th className="text-left px-3 py-2 font-semibold">ETF</th>
            <th className="text-left px-3 py-2 font-semibold">Sector</th>
            <th className="text-left px-3 py-2 font-semibold">Status</th>
            <th className="text-right px-3 py-2 font-semibold" title="Composite -6..+6 (now)">Rank</th>
            <th className="text-right px-3 py-2 font-semibold" title="Composite -6..+6 a month ago (~21 sessions)">Rank 1M</th>
            <th className="text-right px-3 py-2 font-semibold" title="Composite -6..+6 a quarter ago (~63 sessions)">Rank 3M</th>
          </tr>
        </thead>
        <tbody>
          {sectors.map((s) => (
            <tr key={s.etf} className="border-b border-border/60" title={s.justification ?? ''}>
              <td className="px-3 py-2 font-mono font-bold text-foreground">{s.etf}</td>
              <td className="px-3 py-2 text-[12px] text-muted-foreground">{sectorName(s)}</td>
              <td className="px-3 py-2">
                {s.verdict && s.verdict !== 'Neutral' ? (
                  <span className={`px-1.5 py-0.5 rounded text-[11px] font-semibold border ${verdictColor(s.verdict)}`}>
                    {verdictShort(s.verdict)}
                  </span>
                ) : (
                  <span className="text-[11px] text-muted-foreground/60">Neutral</span>
                )}
              </td>
              <td className="px-3 py-2 text-right">
                <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold font-mono ${compositeColor(s.composite)}`}>
                  {fmtSigned(s.composite)}
                </span>
              </td>
              <td className="px-3 py-2 text-right">
                {s.composite_1m == null ? (
                  <span className="text-[11px] text-muted-foreground/50 font-mono">—</span>
                ) : (
                  <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold font-mono ${compositeColor(s.composite_1m)}`}>
                    {fmtSigned(s.composite_1m)}
                  </span>
                )}
              </td>
              <td className="px-3 py-2 text-right">
                {s.composite_3m == null ? (
                  <span className="text-[11px] text-muted-foreground/50 font-mono">—</span>
                ) : (
                  <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold font-mono ${compositeColor(s.composite_3m)}`}>
                    {fmtSigned(s.composite_3m)}
                  </span>
                )}
              </td>
            </tr>
          ))}
          {sectors.length === 0 && (
            <tr>
              <td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">
                No {dir === 'LONG' ? 'long' : 'short'} tailwinds this run.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}


function DetailDrawer({ row, onClose }: { row: MomentumTickerResult; onClose: () => void }) {
  const dirCls: Record<MomentumDirection, string> = {
    LONG: 'text-emerald-700 dark:text-emerald-300',
    SHORT: 'text-red-700 dark:text-red-300',
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
              <span className="font-mono text-lg font-bold text-foreground">{row.ticker}</span>
              <span className={`text-sm font-semibold ${dirCls[row.direction]}`}>{row.direction}</span>
            </div>
            <div className="text-xs text-muted-foreground">{row.name}</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-muted" aria-label="Close"><X size={16} /></button>
        </div>

        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <span className={`px-2 py-1 rounded text-xs font-semibold border ${verdictColor(row.verdict)}`}>{row.verdict}</span>
          <span className={`px-2 py-1 rounded text-xs font-bold font-mono ${compositeColor(row.composite)}`}>composite {fmtSigned(row.composite)}/6</span>
          <span className="px-2 py-1 rounded text-xs font-mono bg-muted text-muted-foreground">strength {row.signal_strength?.toFixed(0) ?? '—'}</span>
        </div>

        {row.justification && (
          <p className="text-sm text-foreground/80 mb-4 leading-relaxed">{row.justification}</p>
        )}

        <div className="grid grid-cols-3 gap-2 mb-4">
          <Stat label="STATE" value={fmtSigned(row.state_score)} cls={pillarColor(row.state_score)} />
          <Stat label="TURN" value={fmtSigned(row.turn_score)} cls={pillarColor(row.turn_score)} />
          <Stat label="ACCEL" value={fmtSigned(row.accel_score)} cls={pillarColor(row.accel_score)} />
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm mb-4">
          <Row label="Price" value={fmtPrice(row.price)} />
          <Row label="Sector ETF" value={row.sector_etf ?? '—'} />
          <Row label="1m return" value={fmtPct(row.r_21d)} />
          <Row label="3m return" value={fmtPct(row.r_63d)} />
          <Row label="6m return" value={fmtPct(row.r_126d)} />
          <Row label="12-1 mom" value={fmtPct(row.r_12_1)} />
          <Row label="MA stack" value={fmtSigned(row.ma_stack)} />
          <Row label="RSI(14)" value={row.rsi != null ? row.rsi.toFixed(0) : '—'} />
          <Row label="ADX(14)" value={row.adx != null ? row.adx.toFixed(0) : '—'} />
          <Row label="Vol ratio" value={row.volume_ratio != null ? `${row.volume_ratio.toFixed(1)}×` : '—'} />
          <Row label="Days since turn" value={row.days_since_turn != null ? `${row.days_since_turn}d` : '—'} />
          <Row label="Sector aligned" value={row.sector_aligned == null ? '—' : row.sector_aligned ? 'yes' : 'divergent'} />
        </div>

        {row.flag_notes?.length > 0 && (
          <div>
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1.5">Evidence</div>
            <ul className="space-y-1 text-[13px] text-foreground/80 list-disc pl-4">
              {row.flag_notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          </div>
        )}
      </div>
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
    <div className="flex items-center justify-between border-b border-border/40 py-0.5">
      <span className="text-muted-foreground text-[12px]">{label}</span>
      <span className="font-mono text-foreground text-[12px]">{value}</span>
    </div>
  );
}
