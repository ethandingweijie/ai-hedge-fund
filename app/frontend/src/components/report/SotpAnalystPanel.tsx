/**
 * SotpAnalystPanel — GS-style sum-of-the-parts report card (Tier 1 package).
 *
 * Renders dcf_range[ticker].sotp_breakdown (built backend-side by
 * src/agents/analysis/sotp_report_extras.build_sotp_breakdown):
 *
 *   1. Valuation sentence — one-line NAV bridge ("TP HK$123 = Σ segments …")
 *   2. Segment table — fwd revenue / EBIT / method / multiple / value / split
 *   3. NAV bridge — segments + associates + net cash − holdco → per share
 *   4. Forward estimates matrix (FMP consensus, Y+1)
 *   5. Estimate revisions (New vs Old vs the previous run's snapshot)
 *   6. "What moves the TP" — per-assumption elasticities as impact bars
 *   7. Bear / Base / Bull scenario strip (2A.5 SCENARIO multiples)
 *   8. Source badges + multiple-basis divergence flags + data limitations
 *
 * Gated by V2ReportView on `dcfRange?.sotp_breakdown` — tickers without SOTP
 * assumptions never reach this component.
 */

import { Card } from '@/components/ui/card';
import type { SotpBreakdown, SotpElasticity, SotpRevision } from '@/lib/reportTypes';

const CCY_SYM: Record<string, string> = {
  USD: '$', HKD: 'HK$', CNY: 'RMB', RMB: 'RMB', SGD: 'S$',
  EUR: '€', GBP: '£', JPY: '¥',
};

const LABEL_CLS =
  'text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70';

function fmtAmount(v: number | null | undefined, sym = '$'): string {
  if (v == null || !isFinite(v)) return '—';
  const a = Math.abs(v);
  const s = v < 0 ? '−' : '';
  if (a >= 1e9) return `${s}${sym}${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${s}${sym}${(a / 1e6).toFixed(1)}M`;
  return `${s}${sym}${a.toFixed(0)}`;
}

function fmtPs(v: number | null | undefined, sym = '$'): string {
  if (v == null || !isFinite(v)) return '—';
  return `${sym}${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtMult(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return `${parseFloat(v.toFixed(2))}x`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
}

function methodChip(method?: string) {
  const m = method ?? '';
  const tone = m.startsWith('P/E')
    ? 'bg-sky-500/10 text-sky-600 dark:text-sky-400'
    : m.includes('fallback')
      ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
      : 'bg-violet-500/10 text-violet-600 dark:text-violet-400';
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${tone}`}>
      {m.replace(' (fallback)', ' · fb')}
    </span>
  );
}

/* ── 2. Segment table ─────────────────────────────────────────────────── */
function SegmentTable({ breakdown, usd }: { breakdown: SotpBreakdown; usd: string }) {
  const rows = breakdown.rows ?? [];
  if (!rows.length) return null;
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-[12px] tabular-nums">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground/70">
            <th className="text-left font-medium pb-1.5">Segment</th>
            <th className="text-right font-medium pb-1.5">Fwd Rev</th>
            <th className="text-right font-medium pb-1.5">EBIT</th>
            <th className="text-left font-medium pb-1.5 pl-3">Method</th>
            <th className="text-right font-medium pb-1.5">Mult</th>
            <th className="text-right font-medium pb-1.5">Value</th>
            <th className="text-right font-medium pb-1.5 pl-3 w-[110px]">Split</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-t border-border/50">
              <td className="py-1.5 pr-2">
                <div className="font-medium text-foreground">{r.name}</div>
                {r.rationale ? (
                  <div className="text-[10.5px] text-muted-foreground/80 leading-snug max-w-[340px]">
                    {r.rationale}
                  </div>
                ) : null}
              </td>
              <td className="py-1.5 text-right text-foreground/85">{fmtAmount(r.revenue_fwd, usd)}</td>
              <td className="py-1.5 text-right text-foreground/85">{fmtAmount(r.ebit, usd)}</td>
              <td className="py-1.5 pl-3">{methodChip(r.method)}</td>
              <td className="py-1.5 text-right text-foreground/85">{fmtMult(r.multiple)}</td>
              <td className="py-1.5 text-right font-medium text-foreground">{fmtAmount(r.value, usd)}</td>
              <td className="py-1.5 pl-3">
                <div className="flex items-center gap-1.5 justify-end">
                  <div className="h-1.5 w-14 rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full rounded-full bg-brand/70"
                      style={{ width: `${Math.min(100, Math.max(0, (r.value_split_pct ?? 0) * 100))}%` }}
                    />
                  </div>
                  <span className="text-[10.5px] text-muted-foreground w-8 text-right">
                    {r.value_split_pct != null ? `${(r.value_split_pct * 100).toFixed(0)}%` : '—'}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── 3. NAV bridge ────────────────────────────────────────────────────── */
function NavBridge({ breakdown, usd, sym }: { breakdown: SotpBreakdown; usd: string; sym: string }) {
  const nc = breakdown.net_cash ?? 0;
  const rows: { label: string; value: string; strong?: boolean }[] = [
    { label: 'Σ segment value', value: fmtAmount(breakdown.segment_value, usd) },
  ];
  if (breakdown.associates) rows.push({ label: 'Associates & investments', value: fmtAmount(breakdown.associates, usd) });
  if (nc !== 0) rows.push({ label: nc > 0 ? 'Net cash' : 'Net debt', value: fmtAmount(Math.abs(nc), usd) });
  rows.push({ label: 'NAV', value: fmtAmount(breakdown.nav, usd), strong: true });
  if ((breakdown.holdco_discount_pct ?? 0) > 0) {
    rows.push({
      label: `Holdco discount (${((breakdown.holdco_discount_pct ?? 0) * 100).toFixed(0)}%)`,
      value: `−${fmtAmount(breakdown.holdco_discount, usd)}`,
    });
  }
  rows.push({ label: 'Equity value', value: fmtAmount(breakdown.final, usd), strong: true });
  return (
    <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_auto]">
      <div className="space-y-1">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-6 text-[12px]">
            <span className={r.strong ? 'font-medium text-foreground' : 'text-muted-foreground'}>{r.label}</span>
            <span className={`tabular-nums ${r.strong ? 'font-semibold text-foreground' : 'text-foreground/80'}`}>
              {r.value}
            </span>
          </div>
        ))}
        <div className="text-[10.5px] text-muted-foreground/70 pt-1">
          ÷ {(breakdown.shares ?? 0) / 1e6 >= 1000
            ? `${((breakdown.shares ?? 0) / 1e9).toFixed(2)}B`
            : `${((breakdown.shares ?? 0) / 1e6).toFixed(0)}M`}{' '}
          shares{breakdown.fx_to_reporting && breakdown.fx_to_reporting !== 1
            ? ` · USD→${breakdown.reporting_currency ?? ''} ${breakdown.fx_to_reporting.toFixed(2)}`
            : ''}
        </div>
      </div>
      <div className="flex sm:flex-col items-center sm:items-end justify-center gap-1 rounded-lg bg-muted/50 px-4 py-3">
        <span className={LABEL_CLS}>SOTP / share</span>
        <span className="text-[24px] font-semibold tabular-nums tracking-tight text-foreground leading-none">
          {fmtPs(breakdown.per_share_reporting, sym)}
        </span>
        {(breakdown.reporting_currency ?? 'USD') !== 'USD' && (
          <span className="text-[10.5px] text-muted-foreground tabular-nums">
            {fmtPs(breakdown.per_share, '$')} USD
          </span>
        )}
      </div>
    </div>
  );
}

/* ── 4. Forward estimates matrix ──────────────────────────────────────── */
function FwdEstimates({ breakdown, usd }: { breakdown: SotpBreakdown; usd: string }) {
  const ests = breakdown.forward_estimates ?? [];
  if (!ests.length) return null;
  return (
    <div className="mt-4">
      <div className={LABEL_CLS}>Forward estimates (consensus)</div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-[12px] tabular-nums">
          <thead>
            <tr className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground/70">
              <th className="text-left font-medium pb-1">Period</th>
              <th className="text-right font-medium pb-1">Revenue</th>
              <th className="text-right font-medium pb-1">EBIT</th>
              <th className="text-right font-medium pb-1">EBITDA</th>
              <th className="text-right font-medium pb-1">Net income</th>
              <th className="text-left font-medium pb-1 pl-3">Source</th>
            </tr>
          </thead>
          <tbody>
            {ests.map((e) => (
              <tr key={e.period_end ?? 'fwd'} className="border-t border-border/50">
                <td className="py-1.5 font-medium text-foreground">FY{e.period_end ?? ''}</td>
                <td className="py-1.5 text-right text-foreground/85">{fmtAmount(e.revenue, usd)}</td>
                <td className="py-1.5 text-right text-foreground/85">{fmtAmount(e.ebit, usd)}</td>
                <td className="py-1.5 text-right text-foreground/85">{fmtAmount(e.ebitda, usd)}</td>
                <td className="py-1.5 text-right text-foreground/85">{fmtAmount(e.net_income, usd)}</td>
                <td className="py-1.5 pl-3 text-[10.5px] text-muted-foreground">{e.source ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── 5. Estimate revisions (New vs Old) ───────────────────────────────── */
function Revisions({ breakdown, usd, sym }: { breakdown: SotpBreakdown; usd: string; sym: string }) {
  const revs = breakdown.revisions ?? [];
  if (!revs.length) return null;

  const fmtVal = (r: SotpRevision, v: number | string | null | undefined): string => {
    if (v == null) return '—';
    if (typeof v === 'string') return v;
    if (r.item.includes('multiple')) return fmtMult(v);
    if (r.item.includes('Holdco') || r.item.includes('tax rate')) return `${(v * 100).toFixed(1)}%`;
    if (r.item.includes('per share')) return fmtPs(v, sym);
    if (r.item.includes('Shares')) return `${v.toFixed(0)}M`;
    return fmtAmount(v, usd);
  };

  let lastSection = '';
  return (
    <div className="mt-4">
      <div className={LABEL_CLS}>
        Estimate revisions — New vs Old
        {breakdown.revisions_prev_run_at && (
          <span className="ml-2 normal-case font-normal tracking-normal text-muted-foreground/60">
            vs {String(breakdown.revisions_prev_run_at).slice(0, 10)} run
          </span>
        )}
      </div>
      <table className="mt-2 w-full text-[12px] tabular-nums">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground/70">
            <th className="text-left font-medium pb-1">Item</th>
            <th className="text-right font-medium pb-1">Old</th>
            <th className="text-right font-medium pb-1">New</th>
            <th className="text-right font-medium pb-1">Δ</th>
          </tr>
        </thead>
        <tbody>
          {revs.map((r, i) => {
            const sectionRow = r.section && r.section !== lastSection;
            lastSection = r.section ?? lastSection;
            const up = (r.delta_pct ?? 0) > 0;
            const dn = (r.delta_pct ?? 0) < 0;
            return (
              <tr key={`${r.item}-${i}`} className={`border-t border-border/50 ${sectionRow ? 'border-t-border' : ''}`}>
                <td className="py-1.5 pr-2">
                  {sectionRow && (
                    <span className="mr-2 text-[9.5px] uppercase tracking-[0.08em] text-muted-foreground/60">
                      {r.section}
                    </span>
                  )}
                  <span className="text-foreground/90">{r.item}</span>
                </td>
                <td className="py-1.5 text-right text-muted-foreground">{fmtVal(r, r.old)}</td>
                <td className="py-1.5 text-right font-medium text-foreground">{fmtVal(r, r.new)}</td>
                <td className={`py-1.5 text-right ${up ? 'text-gain' : dn ? 'text-loss' : 'text-muted-foreground'}`}>
                  {r.delta_pct != null ? fmtPct(r.delta_pct) : ''}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ── 6. Elasticities — what moves the TP ──────────────────────────────── */
function Elasticities({ breakdown, sym }: { breakdown: SotpBreakdown; sym: string }) {
  const els = breakdown.elasticities ?? [];
  if (!els.length) return null;
  const maxAbs = Math.max(...els.map((e) => Math.abs(e.impact_per_share ?? 0)), 1e-9);
  const bar = (e: SotpElasticity) => {
    const w = Math.abs(e.impact_per_share ?? 0) / maxAbs;
    const pos = (e.impact_per_share ?? 0) >= 0;
    return (
      <div className="flex items-center gap-2 w-full">
        <div className="flex-1 flex justify-end">
          {!pos && (
            <div className="h-2 rounded-l-full bg-surface-2" style={{ width: `${w * 42}%` }} />
          )}
        </div>
        <div className="w-px h-3 bg-border" />
        <div className="flex-1">
          {pos && (
            <div className="h-2 rounded-r-full bg-surface-2" style={{ width: `${w * 42}%` }} />
          )}
        </div>
      </div>
    );
  };
  return (
    <div className="mt-4">
      <div className={LABEL_CLS}>What moves the TP (±10% assumption shocks)</div>
      <div className="mt-2 space-y-1.5">
        {els.map((e) => (
          <div key={e.label} className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto] items-center gap-3 text-[11.5px]">
            <span className="truncate text-foreground/85" title={e.label}>{e.label}</span>
            {bar(e)}
            <span className="tabular-nums text-right whitespace-nowrap">
              <span className={(e.impact_per_share ?? 0) >= 0 ? 'text-gain' : 'text-loss'}>
                {(e.impact_per_share ?? 0) >= 0 ? '+' : '−'}{sym}{Math.abs(e.impact_per_share ?? 0).toFixed(2)}
              </span>
              <span className="text-muted-foreground/70 ml-1.5">
                ({fmtPct(e.impact_pct)})
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── 7. Scenario strip ────────────────────────────────────────────────── */
function ScenarioStrip({ breakdown, sym }: { breakdown: SotpBreakdown; sym: string }) {
  const bear = breakdown.scenarios?.bear;
  const bull = breakdown.scenarios?.bull;
  if (!bear && !bull) return null;
  const cells: { label: string; ps?: number | null; note?: string; tone: string }[] = [];
  if (bear) cells.push({ label: 'Bear', ps: bear.per_share_reporting, note: (bear.applied ?? []).join(' · '), tone: 'text-content-high' });
  cells.push({ label: 'Base', ps: breakdown.per_share_reporting, note: 'current assumptions', tone: 'text-foreground' });
  if (bull) cells.push({ label: 'Bull', ps: bull.per_share_reporting, note: (bull.applied ?? []).join(' · '), tone: 'text-content-high' });
  return (
    <div className="mt-4">
      <div className={LABEL_CLS}>Scenario multiples</div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {cells.map((c) => (
          <div key={c.label} className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-center">
            <div className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground/70">{c.label}</div>
            <div className={`text-[16px] font-semibold tabular-nums ${c.tone}`}>
              {c.ps != null ? fmtPs(c.ps, sym) : '—'}
            </div>
            {c.note && <div className="text-[9.5px] text-muted-foreground/70 truncate" title={c.note}>{c.note}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Main panel ───────────────────────────────────────────────────────── */
export function SotpAnalystPanel({ breakdown }: { breakdown: SotpBreakdown }) {
  const ccy = (breakdown.reporting_currency ?? 'USD').toUpperCase();
  const sym = CCY_SYM[ccy] ?? `${ccy} `;
  const usd = '$';
  const basis = breakdown.multiple_basis;
  const flags = basis?.divergence_flags ?? [];
  const sources = breakdown.sources ?? {};

  return (
    <Card className="p-5">
      {/* 1. Header + valuation sentence */}
      <div className={LABEL_CLS}>Sum-of-the-Parts (Analyst)</div>
      {breakdown.sentence && (
        <p className="mt-2 text-[12.5px] font-medium leading-relaxed text-foreground/90">
          {breakdown.sentence}
        </p>
      )}

      {/* 2. Segment table */}
      <SegmentTable breakdown={breakdown} usd={usd} />

      {/* 3. NAV bridge */}
      <NavBridge breakdown={breakdown} usd={usd} sym={sym} />

      {/* 4. Forward estimates */}
      <FwdEstimates breakdown={breakdown} usd={usd} />

      {/* 5. Revisions (New vs Old) */}
      <Revisions breakdown={breakdown} usd={usd} sym={sym} />

      {/* 6. Elasticities */}
      <Elasticities breakdown={breakdown} sym={sym} />

      {/* 7. Scenario strip */}
      <ScenarioStrip breakdown={breakdown} sym={sym} />

      {/* 8. Sources, divergence flags, limitations */}
      {(Object.keys(sources).length > 0 || flags.length > 0 || breakdown.data_limitations) && (
        <div className="mt-4 border-t border-border/50 pt-3 space-y-1.5">
          {Object.keys(sources).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(sources).map(([k, v]) => (
                <span key={k} className="inline-flex items-center rounded bg-muted/70 px-1.5 py-0.5 text-[9.5px] text-muted-foreground">
                  {k}: {String(v)}
                </span>
              ))}
            </div>
          )}
          {basis?.summary && (
            <div className="text-[10.5px] text-muted-foreground/80">Multiple basis: {basis.summary}</div>
          )}
          {flags.map((f, i) => (
            <div key={i} className="text-[10.5px] text-amber-600 dark:text-amber-400">⚠ {f}</div>
          ))}
          {breakdown.data_limitations && (
            <div className="text-[10.5px] text-muted-foreground/70">Limitations: {breakdown.data_limitations}</div>
          )}
        </div>
      )}
    </Card>
  );
}
