/**
 * ModelAccuracyPage.tsx
 * =====================
 * Owner-only (MODEL_ACCURACY_EMAILS). Where the valuation learning loop reports back and asks for a
 * decision (B6):
 *
 *   • Recommendations — calibration proposals the system can apply ("Raise US
 *     intrinsic values by 10.5%"), each with its backtest and shadow record.
 *     Promote is enabled only once the server says the proposal is eligible.
 *   • Live calibration — what is in force, its frozen-cohort record, Roll back.
 *   • What the numbers say — problems re-weighting cannot fix (a method off by
 *     7x, every method missing the same way, a misrouted profile). These need
 *     a code change, so they carry no action button by design.
 *
 * Monochrome throughout: green/red are reserved for price change.
 */
import { useCallback, useEffect, useState } from 'react';
import { Gauge, Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { PageContainer } from '@/components/layout/PageContainer';
import { TabHero } from '@/components/layout/TabHero';
import { useAuth } from '@/contexts/auth-context';
import {
  getModelAccuracyOverview, getCalibrationDetail, getSegmentMemory, reviewSegmentMemory,
  getIndustryInputs, reviewIndustryInput, type IndustryInputRow,
  getDynamicMultiples, pinDynamicMultiple, unpinDynamicMultiple,
  type DynamicMultiplesLog, type DynamicMultipleRow,
  promoteCalibration, rollbackCalibration, dismissCalibration,
  type CalibrationCard, type CalibrationDetail, type DiagnosticCard, type ModelAccuracyOverview,
  type SegmentMemory, type SegmentMemoryTicker,
} from '@/lib/api';

const HORIZON_LABEL: Record<string, string> = {
  consensus_0d: 'Street consensus',
  px_30d: 'price after 30 days',
  px_90d: 'price after 90 days',
  px_180d: 'price after 180 days',
  px_365d: 'price after a year',
};

const CATEGORY_LABEL: Record<DiagnosticCard['category'], string> = {
  bias: 'Market bias',
  method: 'Method accuracy',
  shared_bias: 'Inputs & parameters',
  routing: 'Profile routing',
};

function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${v.toFixed(1)}%`;
}

function Chip({ children, strong = false }: { children: React.ReactNode; strong?: boolean }) {
  return (
    <span className={`inline-flex items-center text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full border
      ${strong ? 'bg-foreground text-background border-foreground' : 'bg-muted text-muted-foreground border-border'}`}>
      {children}
    </span>
  );
}

function SectionTitle({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold text-foreground">{children}</h2>
      {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
    </div>
  );
}

// ── proposal card ───────────────────────────────────────────────────────────

function ShadowProgress({ card }: { card: CalibrationCard }) {
  if (!card.shadow) return null;
  const days = Math.min(card.shadow.days_in_shadow, 28);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Shadow period</span>
        <span className="tabular-nums">day {card.shadow.days_in_shadow} of 28</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div className="h-full bg-foreground/70" style={{ width: `${(days / 28) * 100}%` }} />
      </div>
      {Object.entries(card.shadow.horizons).map(([h, s]) => (
        <p key={h} className="text-xs text-muted-foreground tabular-nums">
          {HORIZON_LABEL[h] ?? h}: {s.n_touched} run(s) — live miss {pct(s.live_miss_pct)}, with this change {pct(s.cand_miss_pct)}
        </p>
      ))}
    </div>
  );
}

function ProposalCard({ card, onChanged }: { card: CalibrationCard; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const blockers = card.status === 'rejected' ? card.backtest.reasons : card.shadow?.reasons ?? [];

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      toast.success(label);
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-base font-semibold text-foreground">{card.title}</p>
          {card.changes.length > 1 && (
            <ul className="text-sm text-foreground/80 list-disc pl-5">
              {card.changes.map((c) => <li key={c}>{c}</li>)}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">
            Scored against the {card.label} · proposed {card.created_at.slice(0, 10)}
          </p>
        </div>
        <Chip strong={card.actions.promote}>{card.stage}</Chip>
      </div>

      <p className="text-sm">
        <span className="font-medium">Backtest:</span>{' '}
        {card.backtest.passed ? 'passed on runs it never saw' : 'did not pass'}
      </p>
      <ShadowProgress card={card} />

      {blockers.length > 0 && (
        <div className="rounded-md border border-border bg-muted/40 p-3">
          <p className="text-xs font-semibold text-foreground mb-1">
            {card.status === 'rejected' ? 'Why it was rejected' : 'Before it can be promoted'}
          </p>
          <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-0.5">
            {blockers.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </div>
      )}

      {open && card.backtest.folds.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs tabular-nums">
            <thead className="text-muted-foreground">
              <tr><th className="text-left py-1">Trained to</th><th className="text-left">Tested to</th>
                <th className="text-right">Runs</th><th className="text-right">Live miss</th>
                <th className="text-right">With change</th><th className="text-right">Better</th></tr>
            </thead>
            <tbody>
              {card.backtest.folds.map((f) => (
                <tr key={f.cutoff} className="border-t border-border/60">
                  <td className="py-1">{f.cutoff}</td><td>{f.test_end}</td>
                  <td className="text-right">{f.test}</td><td className="text-right">{pct(f.live_miss_pct)}</td>
                  <td className="text-right">{pct(f.cand_miss_pct)}</td><td className="text-right">{f.improved ? 'yes' : 'no'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button
          size="sm"
          disabled={!card.actions.promote || busy}
          title={card.actions.promote ? 'Apply this calibration to live valuations' : 'Not eligible yet'}
          onClick={() => {
            if (!window.confirm(`Promote "${card.title}"? New valuations will use it; you can roll it back.`)) return;
            void act('Calibration promoted', () => promoteCalibration(card.id));
          }}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : 'Promote'}
        </Button>
        {card.actions.dismiss && (
          <Button size="sm" variant="outline" disabled={busy}
            onClick={() => void act('Proposal dismissed', () => dismissCalibration(card.id))}>
            Dismiss
          </Button>
        )}
        {card.backtest.folds.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide evidence' : 'Show backtest'}
          </Button>
        )}
      </div>
    </Card>
  );
}

// ── live calibration ────────────────────────────────────────────────────────

function ActiveCard({ card, onChanged }: { card: CalibrationCard; onChanged: () => void }) {
  const [detail, setDetail] = useState<CalibrationDetail | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getCalibrationDetail(card.id).then(setDetail).catch(() => setDetail(null));
  }, [card.id]);

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <p className="text-base font-semibold text-foreground">{card.title}</p>
          {card.changes.length > 1 && (
            <ul className="text-sm text-foreground/80 list-disc pl-5">{card.changes.map((c) => <li key={c}>{c}</li>)}</ul>
          )}
        </div>
        <Chip strong>{card.stage}</Chip>
      </div>
      {detail?.cohort && (
        <div className="text-xs text-muted-foreground space-y-0.5 tabular-nums">
          <p>{detail.cohort.n_frozen} run(s) frozen at promotion — their record can never be refit:</p>
          {Object.keys(detail.cohort.horizons).length === 0 && <p>No labels have matured for them yet.</p>}
          {Object.entries(detail.cohort.horizons).map(([h, s]) => (
            <p key={h}>{HORIZON_LABEL[h] ?? h}: {s.n} run(s) — before {pct(s.live_miss_pct)}, with it {pct(s.cand_miss_pct)}</p>
          ))}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        A daily check rolls this back on its own if runs made under it miss by more than the same runs without it.
      </p>
      <Button
        size="sm" variant="outline" disabled={busy}
        onClick={async () => {
          const reason = window.prompt('Roll back this calibration? Optional reason:', '');
          if (reason === null) return;
          setBusy(true);
          try {
            const out = await rollbackCalibration(card.id, reason);
            toast.success(`Rolled back — restored ${String(out.restored)}`);
            onChanged();
          } catch (e) {
            toast.error((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        Roll back
      </Button>
    </Card>
  );
}

// ── diagnostics ─────────────────────────────────────────────────────────────

function DiagnosticItem({ card }: { card: DiagnosticCard }) {
  return (
    <div className="py-3 border-t border-border/60 first:border-t-0">
      <div className="flex items-center gap-2 mb-1">
        <Chip>{CATEGORY_LABEL[card.category]}</Chip>
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {card.action === 'code_change' ? 'Needs a code change' : 'Addressed by a calibration proposal'}
        </span>
      </div>
      <p className="text-sm font-medium text-foreground">{card.title}</p>
      <p className="text-xs text-muted-foreground mt-0.5">{card.detail}</p>
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────────

// ── segment memory ──────────────────────────────────────────────────────────

const BASIS_LABEL: Record<string, string> = {
  sotp_analyst: 'SOTP (analyst)',
  'sotp_analyst+segment_sotp': 'SOTP (analyst) + segments',
  segment_sotp: 'SOTP (segments)',
  holdco_lookthrough: 'Holdco look-through',
  sotp_blocked_no_segments: 'SOTP blocked: no segment data',
};

function fmtAmount(v: number | null | undefined, ccy?: string): string {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const [div, unit] = abs >= 1e12 ? [1e12, 'tn'] : abs >= 1e9 ? [1e9, 'bn'] : [1e6, 'mn'];
  return `${ccy ? `${ccy} ` : ''}${(v / div).toLocaleString(undefined, { maximumFractionDigits: 1 })}${unit}`;
}

function gapText(g: number | null | undefined): string {
  return g == null ? '—' : `${g >= 0 ? '+' : ''}${(g * 100).toFixed(1)}%`;
}

function SegmentTable({ t }: { t: SegmentMemoryTicker }) {
  const years = t.years ?? [];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr className="text-muted-foreground">
            <th className="text-left font-medium py-2 pr-3">Segment ({t.currency})</th>
            {years.map((y) => <th key={y} className="text-right font-medium py-2 px-2">FY ending {y}</th>)}
          </tr>
        </thead>
        <tbody>
          {(t.segments ?? []).map((s) => (
            <tr key={s.name} className="border-t border-border/60 align-top">
              <td className="py-2 pr-3 text-foreground">{s.name}</td>
              {s.years.map((c) => (
                <td key={c.year} className="py-2 px-2 text-right">
                  {c.revenue == null ? <span className="text-muted-foreground">—</span> : (
                    <>
                      <a href={c.revenue_url} target="_blank" rel="noreferrer" title={c.revenue_quote}
                         className="text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground">
                        {fmtAmount(c.revenue)}
                      </a>
                      <div className="text-muted-foreground" title={c.profit_measure ?? undefined}>
                        {c.margin == null ? 'profit n/d' : (
                          c.profit_url
                            ? <a href={c.profit_url} target="_blank" rel="noreferrer" className="hover:text-foreground">
                                {(c.margin * 100).toFixed(1)}% margin
                              </a>
                            : `${(c.margin * 100).toFixed(1)}% margin`
                        )}
                      </div>
                    </>
                  )}
                </td>
              ))}
            </tr>
          ))}
          <tr className="border-t border-border text-muted-foreground">
            <td className="py-2 pr-3">Segments vs FMP revenue</td>
            {years.map((y) => {
              const r = t.reconciliation?.[y];
              return (
                <td key={y} className="py-2 px-2 text-right" title={r?.fmp_revenue ? `FMP ${fmtAmount(r.fmp_revenue, t.currency)}` : undefined}>
                  {gapText(r?.segment_gap)}
                </td>
              );
            })}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

const REVIEW_LABEL: Record<string, string> = {
  pending: 'pending review',
  accepted: 'accepted · live',
  revoked: 'revoked',
  changed_since_acceptance: 'figures changed · re-accept',
  unknown: 'status unavailable',
};

function ReviewControls({ t, onChanged }: { t: SegmentMemoryTicker; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const status = t.review?.status ?? 'unknown';
  const act = (action: 'accept' | 'revoke') => {
    setBusy(true);
    reviewSegmentMemory(t.ticker, action)
      .then(() => { toast.success(action === 'accept' ? `${t.ticker} accepted for live valuations` : `${t.ticker} revoked`); onChanged(); })
      .catch((e: Error) => toast.error(`Could not ${action}: ${e.message}`))
      .finally(() => setBusy(false));
  };
  const effect = t.live_effect;
  return (
    <div className="rounded-lg border border-border p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Chip strong={status === 'accepted'}>{REVIEW_LABEL[status] ?? status}</Chip>
        {t.review?.reviewed_at && (
          <span className="text-xs text-muted-foreground">
            {t.review.reviewer ? `${t.review.reviewer} · ` : ''}{t.review.reviewed_at.slice(0, 16).replace('T', ' ')} UTC
          </span>
        )}
        <span className="ml-auto flex gap-2">
          {status !== 'accepted' && (
            <Button size="sm" disabled={busy} onClick={() => act('accept')}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : 'Accept for live valuations'}
            </Button>
          )}
          {(status === 'accepted' || status === 'changed_since_acceptance') && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => act('revoke')}>Revoke</Button>
          )}
        </span>
      </div>
      {effect && effect.method === 'SOTP / NAV (look-through)' ? (
        <p className="text-xs text-muted-foreground">
          {effect.applies
            ? `Live effect: the holdco look-through (SOTP / NAV) uses the accepted division EBITDA${(effect.supplied ?? []).length ? ` for ${effect.supplied!.join(', ')}` : ''}.`
            : `No live effect: ${effect.reason}`}
        </p>
      ) : effect && (effect.applies ? (
        <div className="text-xs text-muted-foreground space-y-1">
          <p>
            Live effect for {(t.listings ?? [t.ticker]).join(' and ')}: SOTP segment revenue = FMP consensus × this mix,
            margin = 3-year average; multiples stay with the current SOTP row.
          </p>
          <ul className="list-none space-y-0.5 tabular-nums">
            {(effect.mapping ?? []).map((m) => (
              <li key={m.memory}>
                {m.memory} ({(m.share * 100).toFixed(1)}%{m.margin_avg3 != null ? `, ${(m.margin_avg3 * 100).toFixed(1)}% margin` : ''}) ← multiple of “{m.row}”
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No live effect: {effect.reason}</p>
      ))}
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  sec_10k: 'read from the 10-K',
  sec_10q: 'read from the 10-Q',
  annual_report_verified: 'annual report, verified twice',
};

const INPUT_LABEL: Record<IndustryInputRow['kind'], string> = {
  pv10: 'Reserve value (PV-10)',
  backlog: 'Backlog',
  maintenance_capex: 'Maintenance capex',
  rate_base: 'Regulated rate base',
  fcf_guidance: 'FCF guidance (faded overlay)',
};

function IndustryInputReview({ row, onChanged, overlay = false }:
    { row: IndustryInputRow; onChanged: () => void; overlay?: boolean }) {
  const [busy, setBusy] = useState(false);
  const status = overlay ? (row.overlay?.status ?? 'pending') : row.status;
  const act = (action: 'accept' | 'revoke') => {
    setBusy(true);
    reviewIndustryInput(row.ticker, row.kind, action, overlay)
      .then(() => { toast.success(`${row.ticker} ${INPUT_LABEL[row.kind]} ${action === 'accept' ? 'accepted' : 'revoked'}`); onChanged(); })
      .catch((e: Error) => toast.error(`Could not ${action}: ${e.message}`))
      .finally(() => setBusy(false));
  };
  return (
    <span className="flex gap-2 justify-end">
      {status !== 'accepted' && (
        <Button size="sm" disabled={busy} onClick={() => act('accept')}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : overlay ? 'Accept overlay' : 'Accept'}
        </Button>
      )}
      {(status === 'accepted' || status === 'changed_since_acceptance') && (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => act('revoke')}>Revoke</Button>
      )}
    </span>
  );
}

const FIELD_LABEL: Record<string, string> = {
  ev_ebitda_norm: 'EV/EBITDA (through-cycle)',
  pe_norm: 'P/E (through-cycle)',
};

/** The quarterly multiple update, confirmed three ways (owner 2026-09-21):
 *  it RAN, it reached the engine for EVERY industry, and the multiples
 *  REACHED VALUATIONS -- the last one recorded by the valuations themselves. */
function DynamicMultiplesSection({ allowed }: { allowed: boolean }) {
  const [log, setLog] = useState<DynamicMultiplesLog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const reload = useCallback(() => {
    getDynamicMultiples().then(setLog).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => { if (allowed) reload(); }, [allowed, reload]);

  const rowKey = (r: DynamicMultipleRow) => `${r.exchange}|${r.level}|${r.key}|${r.field}`;
  const onPin = async (r: DynamicMultipleRow) => {
    const input = window.prompt(`Pin ${r.key} (${r.exchange}, ${FIELD_LABEL[r.field] ?? r.field}) at what multiple?`,
      r.multiple.toFixed(2));
    const v = input ? parseFloat(input) : NaN;
    if (!isFinite(v) || v <= 0) return;
    setBusy(rowKey(r));
    try { await pinDynamicMultiple(r, v); reload(); } finally { setBusy(null); }
  };
  const onUnpin = async (r: DynamicMultipleRow) => {
    setBusy(rowKey(r));
    try { await unpinDynamicMultiple(r); reload(); } finally { setBusy(null); }
  };

  const last = log?.runs?.[0];
  const totals = last?.summary?.totals ?? {};
  const rows = (log?.multiples ?? []).filter((r) =>
    !filter || r.key.toLowerCase().includes(filter.toLowerCase()) || r.exchange.toLowerCase() === filter.toLowerCase());

  return (
    <section>
      <SectionTitle hint="Every industry's through-cycle multiple, updated automatically each quarter after the comps history backfill (the 2nd of Jan/Apr/Jul/Oct). Bounded to ±20% of the industry's own through-cycle average; moves under 5% are held as noise; a pinned industry is left alone. Valuations use it in the normalised legs, EV/EBITDA (norm) and P/E (norm).">
        Dynamic multiples {log && (
          <Chip strong>{log.multiples.length} live</Chip>
        )}
      </SectionTitle>
      {error && <Card className="p-4 text-sm text-foreground">Could not load the multiples log: {error}</Card>}
      {log && (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="p-4">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">1 · Update ran</div>
              {last ? (
                <>
                  <div className="text-sm font-medium mt-1">{last.run_at.replace('T', ' ').slice(0, 16)} UTC</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {totals.initial ?? 0} new · {totals.updated ?? 0} updated · {totals.held_inertia ?? 0} held
                    · {totals.pinned ?? 0} pinned · {totals.insufficient_history ?? 0} too little history
                  </div>
                  <div className="text-xs text-muted-foreground">trigger: {last.trigger}</div>
                </>
              ) : <div className="text-sm mt-1">No update has run yet.</div>}
            </Card>
            <Card className="p-4">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">2 · In the engine, every industry</div>
              {Object.entries(log.coverage).map(([ex, c]) => (
                <div key={ex} className="text-xs mt-1 tabular-nums">
                  <span className="font-medium">{ex}</span>: {c.multiples_live} of {c.multiple_slots} multiples live
                  <span className="text-muted-foreground"> ({c.baskets_with_history} baskets)</span>
                </div>
              ))}
            </Card>
            <Card className="p-4">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">3 · Reached valuations</div>
              <div className="text-sm font-medium mt-1 tabular-nums">{log.reached_valuations.tickers} valuations</div>
              <div className="text-xs text-muted-foreground mt-1 tabular-nums">
                across {log.reached_valuations.baskets_used} industries;
                {' '}{log.reached_valuations.baskets_used_since_last_update} since the last update
              </div>
              <div className="text-xs text-muted-foreground">
                auto-update {log.switches.auto_update_enabled ? 'on' : 'OFF'} · valuations read it
                {' '}{log.switches.valuations_read_enabled ? 'on' : 'OFF'}
              </div>
            </Card>
          </div>
          <Card className="p-0 overflow-x-auto">
            <div className="p-3 border-b border-border">
              <input className="w-full sm:w-72 bg-transparent border border-border rounded px-2 py-1 text-xs"
                     placeholder="Filter by industry or market (US, HKSE, SES)"
                     value={filter} onChange={(e) => setFilter(e.target.value)} />
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left p-2">Market</th>
                  <th className="text-left p-2">Industry</th>
                  <th className="text-left p-2">Multiple</th>
                  <th className="text-right p-2">Live</th>
                  <th className="text-right p-2">Baseline</th>
                  <th className="text-right p-2">Band</th>
                  <th className="text-right p-2">Market now</th>
                  <th className="text-left p-2">Source</th>
                  <th className="p-2" />
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 300).map((r) => (
                  <tr key={rowKey(r)} className="border-b border-border/40">
                    <td className="p-2">{r.exchange}</td>
                    <td className="p-2">{r.key}<span className="text-muted-foreground"> · {r.level}</span></td>
                    <td className="p-2">{FIELD_LABEL[r.field] ?? r.field}</td>
                    <td className="p-2 text-right tabular-nums font-medium">{r.multiple.toFixed(2)}x</td>
                    <td className="p-2 text-right tabular-nums">{r.baseline != null ? `${r.baseline.toFixed(2)}x` : '—'}</td>
                    <td className="p-2 text-right tabular-nums">
                      {r.band_lo != null && r.band_hi != null ? `${r.band_lo.toFixed(1)}–${r.band_hi.toFixed(1)}x` : '—'}
                    </td>
                    <td className="p-2 text-right tabular-nums">{r.market_now != null ? `${r.market_now.toFixed(2)}x` : '—'}</td>
                    <td className="p-2">{r.pinned ? `pinned (${r.source})` : r.source}</td>
                    <td className="p-2 text-right">
                      {r.pinned
                        ? <Button size="sm" variant="outline" disabled={busy === rowKey(r)} onClick={() => onUnpin(r)}>Unpin</Button>
                        : <Button size="sm" variant="outline" disabled={busy === rowKey(r)} onClick={() => onPin(r)}>Pin</Button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 300 && (
              <div className="p-2 text-xs text-muted-foreground">Showing 300 of {rows.length}; filter to narrow.</div>
            )}
          </Card>
        </div>
      )}
    </section>
  );
}

/** Inputs FMP does not carry, cited as filed and checked against FMP. Nothing
 *  reaches a valuation until accepted here (owner decision 2026-09-20). */
function IndustryInputsSection({ allowed }: { allowed: boolean }) {
  const [rows, setRows] = useState<IndustryInputRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    getIndustryInputs().then((r) => setRows(r.rows)).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => { if (allowed) reload(); }, [allowed, reload]);
  return (
    <section>
      <SectionTitle hint="Figures FMP does not report, taken as each filing prints them. The baseline is the audited actual; management guidance is a separate delta that applies only while the forward overlay is switched on. Checked against FMP; used in valuations only once accepted. Click a figure for its source.">
        Industry inputs {rows && (
          <Chip strong>{rows.filter((r) => r.status === 'accepted').length} of {rows.length} accepted</Chip>
        )}
      </SectionTitle>
      {error && <Card className="p-4 text-sm text-foreground">Could not load industry inputs: {error}</Card>}
      {rows && rows.length === 0 && <Card className="p-4 text-sm text-muted-foreground">Not built yet.</Card>}
      {rows && rows.length > 0 && (
        <Card className="p-0 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left font-medium px-3 py-2">Ticker</th>
                <th className="text-left font-medium px-3 py-2">Input</th>
                <th className="text-right font-medium px-3 py-2">As filed</th>
                <th className="text-left font-medium px-3 py-2">Checks against FMP</th>
                <th className="text-left font-medium px-3 py-2">Status</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.ticker}|${r.kind}`} className="border-b border-border last:border-0 align-top">
                  <td className="px-3 py-2 font-medium tabular-nums">
                    {r.ticker}
                    {r.company && <div className="font-normal text-muted-foreground">{r.company}</div>}
                  </td>
                  <td className="px-3 py-2">
                    {INPUT_LABEL[r.kind] ?? r.kind}
                    {r.source && <div className="text-muted-foreground">{SOURCE_LABEL[r.source] ?? r.source}</div>}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {r.source_url ? (
                      <a href={r.source_url} target="_blank" rel="noreferrer" title={r.quote ?? undefined}
                         className="underline decoration-dotted underline-offset-2">
                        {r.value?.toLocaleString()} {r.currency} {r.scale}
                      </a>
                    ) : <span>{r.value?.toLocaleString()} {r.currency} {r.scale}</span>}
                    {r.period && <div className="text-muted-foreground">{r.period}</div>}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {r.checks.map((c) => (
                      <div key={c.check}>{c.ok === false ? '✕' : c.ok ? '✓' : '·'} {c.check}: {c.detail}</div>
                    ))}
                    {r.omitted_reason && <div>{r.omitted_reason}</div>}
                    {r.remark && <div className="mt-1 italic">{r.remark}</div>}
                    {r.basis && r.basis !== 'actual' && (
                      <div>✕ not the latest audited actual — a forward figure belongs in the overlay</div>
                    )}
                    {r.overlay && (
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <span>
                          Forward overlay:{' '}
                          {r.overlay.source_url ? (
                            <a href={r.overlay.source_url} target="_blank" rel="noreferrer" title={r.overlay.quote ?? undefined}
                               className="underline decoration-dotted underline-offset-2">
                              {r.overlay.delta_pct != null ? `${(r.overlay.delta_pct * 100).toFixed(1)}%` : '—'}
                            </a>
                          ) : (r.overlay.delta_pct != null ? `${(r.overlay.delta_pct * 100).toFixed(1)}%` : '—')}
                          {r.overlay.note ? ` · ${r.overlay.note}` : ''}
                        </span>
                        <Chip strong={r.overlay.status === 'accepted'}>{REVIEW_LABEL[r.overlay.status] ?? r.overlay.status}</Chip>
                        <IndustryInputReview row={r} overlay onChanged={reload} />
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2"><Chip strong={r.status === 'accepted'}>{REVIEW_LABEL[r.status] ?? r.status}</Chip></td>
                  <td className="px-3 py-2"><IndustryInputReview row={r} onChanged={reload} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </section>
  );
}

function SegmentMemorySection({ allowed }: { allowed: boolean }) {
  const [memory, setMemory] = useState<SegmentMemory | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    getSegmentMemory()
      .then((m) => {
        setMemory(m);
        setSelected((cur) => cur ?? m.tickers.find((t) => !t.error)?.ticker ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (allowed) reload();
  }, [allowed, reload]);

  const current = memory?.tickers.find((t) => t.ticker === selected) ?? null;

  return (
    <section>
      <SectionTitle hint={memory
        ? `Reported segment revenue and profit for SOTP-valued names, each figure cited${memory.model ? ` · ${memory.model}` : ''}${memory.updated ? ` · updated ${memory.updated}` : ''}. Click a figure for its source.`
        : 'Reported segment revenue and profit for SOTP-valued names.'}>
        Segment memory {memory && (
          <Chip strong>
            {memory.tickers.filter((t) => t.review?.status === 'accepted').length} of {memory.tickers.filter((t) => !t.error).length} accepted
          </Chip>
        )}
      </SectionTitle>
      {error && <Card className="p-4 text-sm text-foreground">Could not load segment memory: {error}</Card>}
      {memory && memory.tickers.length === 0 && (
        <Card className="p-4 text-sm text-muted-foreground">Not built yet.</Card>
      )}
      {memory && memory.tickers.length > 0 && (
        <Card className="p-4 space-y-4">
          <div className="flex flex-wrap gap-1.5">
            {memory.tickers.map((t) => (
              <button key={t.ticker} onClick={() => setSelected(t.ticker)}
                className={`text-xs px-2 py-1 rounded-md border tabular-nums
                  ${t.ticker === selected ? 'bg-foreground text-background border-foreground'
                    : t.error ? 'border-dashed border-border text-muted-foreground'
                    : 'border-border text-foreground hover:bg-muted'}`}>
                {t.ticker}
              </button>
            ))}
          </div>
          {current && (current.error ? (
            <p className="text-sm text-muted-foreground">{current.company}: not retrieved ({current.error}).</p>
          ) : (
            <>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-sm font-semibold text-foreground">{current.company}</span>
                {(current.listings ?? []).length > 1 && (
                  <span className="text-xs text-muted-foreground">serves {current.listings!.join(' · ')}</span>
                )}
                <Chip>{BASIS_LABEL[current.sotp_basis] ?? current.sotp_basis}</Chip>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {current.source === 'sec_segment_footnote' ? 'SEC filing · '
                    : current.source === 'fmp_product_segmentation' ? 'FMP product split · '
                    : 'Gemini, cited · '}
                  cited {Math.round((current.citation_coverage ?? 0) * 100)}% · profit disclosed {Math.round((current.profit_coverage ?? 0) * 100)}%
                  {current.retrieved ? ` · retrieved ${current.retrieved}` : ''}
                </span>
              </div>
              <ReviewControls t={current} onChanged={reload} />
              {(current.division_ebitda ?? []).length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs tabular-nums">
                    <thead>
                      <tr className="text-muted-foreground">
                        <th className="text-left font-medium py-2 pr-3">Division EBITDA (look-through)</th>
                        <th className="text-right font-medium py-2 px-2">Reported</th>
                        <th className="text-left font-medium py-2 px-2">Measure</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.division_ebitda!.map((d) => (
                        <tr key={d.division} className="border-t border-border/60 align-top">
                          <td className="py-2 pr-3 text-foreground">{d.division}</td>
                          <td className="py-2 px-2 text-right">
                            <a href={d.source_url} target="_blank" rel="noreferrer" title={d.quote}
                               className="text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground">
                              {d.currency} {d.value.toLocaleString()} {d.scale}
                            </a>
                            {d.fiscal_year && <div className="text-muted-foreground">{d.fiscal_year}</div>}
                          </td>
                          <td className="py-2 px-2 text-muted-foreground">
                            {d.measure}{d.includes_share_of_associates ? ' · incl. share of associates/JVs' : ''}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {(current.division_ebitda_missing ?? []).length > 0 && (
                    <p className="text-xs text-muted-foreground mt-1">Not disclosed: {current.division_ebitda_missing!.join(', ')}</p>
                  )}
                </div>
              )}
              {(current.segments ?? []).length > 0 && <SegmentTable t={current} />}
              {current.history_error && (current.segments ?? []).length === 0 && (
                <p className="text-xs text-muted-foreground">Segment history not retrieved ({current.history_error}).</p>
              )}
              {(current.notes ?? []).length > 0 && (
                <ul className="text-xs text-foreground/80 space-y-1 list-none">
                  {current.notes!.map((n) => <li key={n}>• {n}</li>)}
                </ul>
              )}
              {current.resegmentation && (
                <p className="text-xs text-muted-foreground">Resegmentation: {current.resegmentation}</p>
              )}
            </>
          ))}
        </Card>
      )}
    </section>
  );
}

export function ModelAccuracyPage() {
  const { user } = useAuth();
  const [data, setData] = useState<ModelAccuracyOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    getModelAccuracyOverview()
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const allowed = user?.can_view_model_accuracy === true;

  useEffect(() => {
    if (allowed) load();
    else setLoading(false);
  }, [allowed, load]);

  if (!allowed) {
    return (
      <PageContainer size="prose">
        <Card className="p-6 text-sm text-muted-foreground">Model Accuracy is not available to this account.</Card>
      </PageContainer>
    );
  }

  const totalLabels = data ? Object.values(data.labels).reduce((a, b) => a + b, 0) : 0;

  return (
    <>
      <TabHero
        title="Model Accuracy"
        icon={Gauge}
        subtitle="How valuations turned out, and what to change"
        actions={
          <button onClick={load} aria-label="Refresh"
            className="p-2 rounded-md text-hero-foreground hover:bg-hero-foreground/10">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        }
      />
      <PageContainer size="default" className="space-y-8">
        {error && <Card className="p-4 text-sm text-foreground">Could not load: {error}</Card>}
        {loading && !data && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 size={16} className="animate-spin" /> Loading…</div>
        )}

        {data && (
          <>
            <p className="text-xs text-muted-foreground tabular-nums">
              {totalLabels} outcome label(s) so far
              {Object.entries(data.labels).map(([h, n]) => ` · ${HORIZON_LABEL[h] ?? h}: ${n}`).join('')}
            </p>

            <section>
              <SectionTitle hint="Changes the system can apply. Each must pass a backtest and 28 days in shadow before Promote unlocks.">
                Recommendations{data.eligible_count > 0 ? ` · ${data.eligible_count} ready for your decision` : ''}
              </SectionTitle>
              {data.proposals.length === 0 ? (
                <Card className="p-4 text-sm text-muted-foreground">
                  No proposals yet. One is fitted every Sunday once enough valuations have outcomes to learn from.
                </Card>
              ) : (
                <div className="space-y-3">
                  {data.proposals.map((c) => <ProposalCard key={c.id} card={c} onChanged={load} />)}
                </div>
              )}
            </section>

            {data.active && (
              <section>
                <SectionTitle hint="In force for every new valuation.">Live calibration</SectionTitle>
                <ActiveCard card={data.active} onChanged={load} />
              </section>
            )}

            <section>
              <SectionTitle hint={data.diagnostics_horizon
                ? `From runs scored against the ${HORIZON_LABEL[data.diagnostics_horizon] ?? data.diagnostics_horizon}.`
                : undefined}>
                What the numbers say
              </SectionTitle>
              {data.diagnostics.length === 0 ? (
                <Card className="p-4 text-sm text-muted-foreground">Nothing stands out yet — findings appear as outcomes accumulate.</Card>
              ) : (
                <Card className="px-4 py-1">
                  {data.diagnostics.map((d) => <DiagnosticItem key={d.id} card={d} />)}
                </Card>
              )}
            </section>

            <SegmentMemorySection allowed={allowed} />

            <IndustryInputsSection allowed={allowed} />

            <DynamicMultiplesSection allowed={allowed} />

            {data.history.length > 0 && (
              <section>
                <SectionTitle>History</SectionTitle>
                <Card className="px-4 py-1">
                  {data.history.map((c) => (
                    <div key={c.id} className="py-2 border-t border-border/60 first:border-t-0 flex items-center justify-between gap-3">
                      <span className="text-sm text-foreground truncate">{c.title}</span>
                      <Chip>{c.stage}</Chip>
                    </div>
                  ))}
                </Card>
              </section>
            )}
          </>
        )}
      </PageContainer>
    </>
  );
}
