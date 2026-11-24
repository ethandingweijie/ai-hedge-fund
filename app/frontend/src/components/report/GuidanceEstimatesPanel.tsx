/**
 * GuidanceEstimatesPanel — management guidance → the model's financial estimates.
 *
 * Owner, 2026-10-03 (objective iii): show the estimates the research derived from
 * management guidance, beside the guidance itself and consensus, and say exactly how
 * the valuation used them. Renders dcf_range[ticker].guidance_estimates, built by
 * deep research section 2G → the guidance_estimates extractor (a thinking pass on the
 * research model) → dcf_agent._guidance_estimates_payload. Every number here is the
 * engine's own: `channel.schedule` is the growth path the base DCF ran on.
 *
 * Monochrome by design-system rule (lib/semanticColors): no green/red outside price change.
 */
import { useState } from 'react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { EstimateWorkbench } from '@/components/report/EstimateWorkbench';
import { ClampText } from '@/components/report/shared/ClampText';
import type { DcfRange, GuidanceEstimates, GuidanceForecast, GuidanceScenarioRow, RunResult } from '@/lib/reportTypes';

const LABEL_CLS = 'text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground/70';
/** A compact figure: small label over a tabular value. */
function Fig({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-[14px] font-medium tabular-nums text-foreground">{value}</div>
    </div>
  );
}
const SCENARIOS = ['bear', 'base', 'bull'] as const;

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || !isFinite(v)) return '—';
  return `${v >= 0 ? '+' : '−'}${(Math.abs(v) * 100).toFixed(digits)}%`;
}
function margin(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}
function num(v: number | null | undefined, digits = 2): string {
  if (v == null || !isFinite(v)) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function range(r: { low?: number | null; mid?: number | null; high?: number | null } | null | undefined,
  fmt: (v: number | null | undefined) => string): string {
  if (!r) return '—';
  const parts = [r.low, r.mid, r.high];
  if (parts.every((p) => p == null)) return '—';
  if (r.low != null && r.high != null && r.low !== r.high) {
    return `${fmt(r.low)} – ${fmt(r.high)}${r.mid != null ? ` (mid ${fmt(r.mid)})` : ''}`;
  }
  return fmt(r.mid ?? r.low ?? r.high);
}
function amountRange(r: GuidanceEstimates['guidance']['revenue']): string {
  if (!r) return '—';
  const unit = [r.currency, r.scale].filter(Boolean).join(' ');
  const f = (v: number | null | undefined) => (v == null ? '—' : `${num(v, 1)}`);
  const body = range(r, f);
  return body === '—' ? body : `${body}${unit ? ` ${unit}` : ''}`;
}

/** One estimates row: label + the three scenario values. */
function EstimateRow({ label, rows, field, fmt }: {
  label: string; rows: Record<string, GuidanceScenarioRow>; field: keyof GuidanceScenarioRow;
  fmt: (v: number | null | undefined) => string;
}) {
  const vals = SCENARIOS.map((s) => rows[s]?.[field]);
  if (vals.every((v) => v == null)) return null;
  return (
    <tr className="border-t border-border/50">
      <td className="py-1.5 pr-2 text-foreground/85">{label}</td>
      {vals.map((v, i) => (
        <td key={SCENARIOS[i]} className={`py-1.5 text-right ${SCENARIOS[i] === 'base' ? 'font-medium text-foreground' : 'text-foreground/85'}`}>
          {fmt(v)}
        </td>
      ))}
    </tr>
  );
}

function amountBn(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return (v / 1e9).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** Model checks: failures and flags shown, passes folded into one line. */
function Checks({ invariants, flags }: { invariants: NonNullable<GuidanceForecast['invariants']>; flags: string[] }) {
  const failed = invariants.filter((i) => i.ok === false);
  const rest = invariants.filter((i) => i.ok !== false);
  const passed = invariants.filter((i) => i.ok === true).length;
  if (!invariants.length && !flags.length) return null;
  return (
    <div className="mt-4 space-y-1.5 text-[13px]">
      {failed.map((inv) => (
        <div key={inv.id} className="flex gap-2">
          <span className="shrink-0 rounded bg-foreground px-1.5 text-xs font-semibold leading-5 text-background">FAIL</span>
          <div className="min-w-0">
            <span className="font-medium text-foreground">{inv.name}</span>
            {inv.detail ? <ClampText text={inv.detail} lines={2} className="text-muted-foreground" /> : null}
          </div>
        </div>
      ))}
      {flags.map((f, i) => (
        <div key={`f${i}`} className="flex gap-2">
          <span className="shrink-0 rounded border border-border px-1.5 text-xs font-medium leading-5 text-muted-foreground">FLAG</span>
          <ClampText text={f} lines={2} className="min-w-0 text-foreground/85" />
        </div>
      ))}
      {rest.length ? (
        <details className="group">
          <summary className="cursor-pointer list-none text-muted-foreground hover:text-foreground">
            <span className="inline-block transition-transform group-open:rotate-90">›</span> {passed} of {invariants.length} checks passed
          </summary>
          <ul className="mt-1.5 space-y-1">
            {rest.map((inv) => (
              <li key={inv.id} className="flex gap-2">
                <span className="shrink-0 w-9 text-xs leading-5 text-muted-foreground">{inv.ok === true ? 'PASS' : 'n/a'}</span>
                <span className="text-foreground/85">{inv.name}{inv.detail ? <span className="text-muted-foreground"> — {inv.detail}</span> : null}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/** The forecast the DCF ran on: archetype, target-year deconstruction, the intermediate years, the checks. */
function ForecastSection({ fc: base, scenarios }: { fc: GuidanceForecast; scenarios?: Partial<Record<'bear' | 'bull', GuidanceForecast>> | null }) {
  const [which, setWhich] = useState<'bear' | 'base' | 'bull'>('base');
  const fc = which === 'base' ? base : (scenarios?.[which] ?? base);
  const rows = fc.rows ?? [];
  if (!rows.length) return null;
  const T = fc.horizon_years ?? 0;
  const shown = rows.filter((r) => r.phase !== 'steady' || r.year === rows[rows.length - 1].year);
  const t = fc.target;
  const dec = fc.deconstruction ?? {};
  const term = fc.terminal;
  return (
    <div className="mt-5 border-t border-border/60 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className={LABEL_CLS}>Forecast the DCF ran on</span>
          <Badge variant="outline" className="text-xs">{fc.archetype_name}</Badge>
        </div>
        {scenarios && (scenarios.bear || scenarios.bull) ? (
          <div className="flex gap-1">
            {(['bear', 'base', 'bull'] as const).map((s) => (
              <button key={s} type="button" onClick={() => setWhich(s)} disabled={s !== 'base' && !scenarios[s]}
                className={`rounded-full px-2.5 py-1 text-xs capitalize ${s === which ? 'bg-foreground text-background' : 'border border-border text-muted-foreground hover:text-foreground disabled:opacity-40'}`}>
                {s}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-3">
        <Fig label={`${T}-year path to`} value={t ? `${t.target_year} ${t.metric} ${num(t.value)}` : 'FY+2 est.'} />
        <Fig label={`EBIT margin${fc.margin_source ? ` (${fc.margin_source})` : ''}`} value={`${margin(fc.margin_start)} → ${margin(fc.margin_target)}`} />
        <Fig label={`${fc.fade_years}-year fade to`} value={`${margin(term?.tgr)} growth`} />
      </div>
      {fc.override ? (
        <div className="mt-2 text-[13px] font-medium text-foreground">
          User override{fc.override.created_at ? ` (${fc.override.created_at.slice(0, 10)})` : ''}: {fc.override.fields?.join(', ')}{fc.override.note ? ` — ${fc.override.note}` : ''}. Agent's base IV {num(fc.override.before?.intrinsic_value)} → {num(fc.override.after?.intrinsic_value)}.
        </div>
      ) : null}
      {/* Owner, 2026-10-03: the agent's thinking, step by step, so the user can query and contest it. */}
      {fc.steps?.length ? (
        <details className="group mt-3">
          <summary className="cursor-pointer list-none text-[13px] font-medium text-muted-foreground hover:text-foreground">
            <span className="inline-block transition-transform group-open:rotate-90">›</span> How the agent built it · {fc.steps.length} steps
          </summary>
          <ol className="mt-2 space-y-2 text-[13px] leading-snug">
            {fc.steps.map((s) => (
              <li key={s.n} className="flex gap-2">
                <span className="shrink-0 w-4 text-right tabular-nums text-muted-foreground">{s.n}</span>
                <div className="min-w-0">
                  <div className="font-medium text-foreground">{s.title}</div>
                  {s.detail ? <ClampText text={s.detail} lines={2} className="text-muted-foreground" /> : null}
                </div>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-[13px] tabular-nums">
          <thead>
            <tr className="whitespace-nowrap text-xs uppercase tracking-[0.08em] text-muted-foreground/70">
              <th className="pb-1.5 text-left font-medium">Year</th>
              <th className="pb-1.5 text-right font-medium">Revenue (bn)</th>
              <th className="pb-1.5 text-right font-medium">Growth</th>
              <th className="pb-1.5 text-right font-medium">EBIT margin</th>
              <th className="pb-1.5 text-right font-medium">EPS</th>
              <th className="pb-1.5 text-right font-medium">UFCF (bn)</th>
              <th className="pb-1.5 text-right font-medium">FCF margin</th>
              <th className="pb-1.5 text-left font-medium pl-3">Phase</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.year} className={`border-t border-border/50 ${r.phase === 'guided' ? 'text-foreground' : 'text-foreground/70'}`}>
                <td className="py-1 pr-2">{r.year}</td>
                <td className="py-1 text-right">{amountBn(r.revenue)}</td>
                <td className="py-1 text-right">{pct(r.growth)}</td>
                <td className="py-1 text-right">{margin(r.ebit_margin)}</td>
                <td className="py-1 text-right">{num(r.eps)}</td>
                <td className="py-1 text-right">{amountBn(r.ufcf)}</td>
                <td className="py-1 text-right">{margin(r.fcf_margin)}</td>
                <td className="py-1 pl-3 text-muted-foreground">{r.phase}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dec.ebit_T_implied != null || term ? (
        <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          {dec.ebit_T_implied != null ? <Fig label="Target-year revenue" value={`${amountBn(dec.revenue_T as number)}bn`} /> : null}
          {dec.ebit_T_implied != null ? <Fig label="Implied EBIT" value={`${amountBn(dec.ebit_T_implied as number)}bn`} /> : null}
          {dec.guided_cagr != null && dec.market_cagr != null
            ? <Fig label="CAGR guided vs market" value={`${margin(dec.guided_cagr as number)} vs ${margin(dec.market_cagr as number)}`} /> : null}
          {dec.implied_tax_rate != null ? <Fig label="Tax & non-op take" value={margin(dec.implied_tax_rate as number)} /> : null}
          {term ? <Fig label="Terminal growth" value={margin(term.tgr)} /> : null}
          {term ? <Fig label="Terminal ROIC" value={margin(term.roic_terminal)} /> : null}
          {term ? <Fig label="Reinvestment" value={margin(term.reinvestment_rate)} /> : null}
          {term?.implied_exit_ev_ebitda != null
            ? <Fig label="Exit EV/EBITDA" value={`${term.implied_exit_ev_ebitda.toFixed(1)}x${term.peer_ev_ebitda_median != null ? ` · peers ${term.peer_ev_ebitda_median.toFixed(1)}x` : ''}`} /> : null}
        </div>
      ) : null}
      <Checks invariants={fc.invariants ?? []} flags={fc.flags ?? []} />
    </div>
  );
}

export function GuidanceEstimatesPanel({ block: blockIn, forecast, dcfRange, runId, ticker, onRunUpdated }: {
  block: GuidanceEstimates | null | undefined; forecast?: GuidanceForecast | null;
  /** The ticker's dcf_range (forecast_context, estimate_override); with runId + ticker the workbench renders. */
  dcfRange?: DcfRange; runId?: string | null; ticker?: string | null; onRunUpdated?: (r: RunResult) => void;
}) {
  const block: GuidanceEstimates = blockIn ?? { guidance: {}, consensus: {}, estimates: {}, applied: false, not_applied_reason: 'the research produced no guidance block; enter estimates below' };
  const g = block.guidance ?? {};
  const c = block.consensus ?? {};
  const rows = block.estimates ?? {};
  const fy1 = block.fiscal_year_1 ?? dcfRange?.forecast_context?.fiscal_year_1 ?? 'FY+1';
  const fy2 = block.fiscal_year_2 ?? dcfRange?.forecast_context?.fiscal_year_2 ?? 'FY+2';
  const ch = block.channel;
  const explicitYears = ch?.explicit_years ?? 0;
  const fadeYears = ch?.fade_years ?? 0;
  const canWork = !!runId && !!ticker && !!(dcfRange?.forecast_context || forecast);

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className={LABEL_CLS}>Management guidance → estimates</div>
          <div className="mt-1 text-[15px] font-medium text-foreground">
            {block.applied ? `Drives DCF years 1–${explicitYears}` : 'Not used in the DCF'}
          </div>
          {block.applied
            ? <div className="text-[13px] text-muted-foreground">Fades onto the engine path by year {explicitYears + fadeYears + 1}</div>
            : <ClampText text={block.not_applied_reason ?? 'the DCF did not use it'} lines={2} className="text-[13px] text-muted-foreground" />}
        </div>
        <div className="flex items-center gap-1.5">
          <Badge variant="outline" className="text-xs">confidence {block.confidence ?? '—'}</Badge>
          {g.status && g.status !== 'none' ? <Badge variant="outline" className="text-xs">guidance {g.status}</Badge> : null}
          {block.as_of ? <span className="text-xs text-muted-foreground">as of {block.as_of}</span> : null}
        </div>
      </div>

      {/* 1. Guidance as stated vs consensus */}
      <div className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-[13px] sm:grid-cols-2">
        <div>
          <div className={LABEL_CLS}>Guidance ({fy1})</div>
          <dl className="mt-1 space-y-1 tabular-nums">
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Revenue growth</dt><dd>{range(g.revenue_growth, pct)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Revenue</dt><dd>{amountRange(g.revenue)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">EBITDA / op. margin</dt><dd>{range(g.ebitda_margin, margin)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">EPS{g.eps?.currency ? ` (${g.eps.currency})` : ''}</dt><dd>{range(g.eps, (v) => num(v))}</dd></div>
            {g.basis ? <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Basis</dt><dd>{g.basis}</dd></div> : null}
          </dl>
        </div>
        <div>
          <div className={LABEL_CLS}>Consensus ({fy1})</div>
          <dl className="mt-1 space-y-1 tabular-nums">
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Revenue growth</dt><dd>{pct(c.revenue_growth_fy1)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">EPS</dt><dd>{num(c.eps_fy1)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Guidance vs cons.</dt><dd>{pct(block.guidance_vs_consensus_pct)}</dd></div>
            {c.as_of ? <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Consensus as of</dt><dd>{c.as_of}</dd></div> : null}
          </dl>
        </div>
      </div>
      {g.quote ? (
        <ClampText text={`“${g.quote}”${g.source ? ` — ${g.source}` : ''}`} lines={2} className="mt-2 text-[13px] italic leading-snug text-muted-foreground" />
      ) : null}

      {/* 2. The model's estimates */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-[13px] tabular-nums">
          <thead>
            <tr className="whitespace-nowrap text-xs uppercase tracking-[0.08em] text-muted-foreground/70">
              <th className="pb-1.5 text-left font-medium">Model estimate</th>
              {SCENARIOS.map((s) => <th key={s} className="pb-1.5 text-right font-medium">{s}</th>)}
            </tr>
          </thead>
          <tbody>
            <EstimateRow label={`Revenue growth ${fy1}`} rows={rows} field="revenue_growth_fy1" fmt={pct} />
            <EstimateRow label={`Revenue growth ${fy2}`} rows={rows} field="revenue_growth_fy2" fmt={pct} />
            <EstimateRow label={`EBITDA margin ${fy1}`} rows={rows} field="ebitda_margin_fy1" fmt={margin} />
            <EstimateRow label={`EBITDA margin ${fy2}`} rows={rows} field="ebitda_margin_fy2" fmt={margin} />
            <EstimateRow label={`EPS ${fy1}`} rows={rows} field="eps_fy1" fmt={(v) => num(v)} />
            <EstimateRow label={`EPS ${fy2}`} rows={rows} field="eps_fy2" fmt={(v) => num(v)} />
          </tbody>
        </table>
      </div>

      {/* 3. How the DCF used it */}
      {ch?.schedule?.length ? (
        <div className="mt-4 text-[13px] text-muted-foreground">
          <span className={LABEL_CLS}>Base DCF growth path</span>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 tabular-nums">
            {ch.schedule.map((v, i) => (
              <span key={i} className={i < explicitYears ? 'font-medium text-foreground' : i < explicitYears + fadeYears ? 'text-foreground/80' : ''}>
                Y{i + 1} {pct(v)}
              </span>
            ))}
          </div>
          <div className="mt-1 text-xs">
            Bold = from guidance · engine Y1 was {pct(ch.engine_year1)}
          </div>
        </div>
      ) : null}

      {dcfRange?.estimate_override_carried ? (
        <div className="mt-3 text-[13px] text-foreground">
          <span className="font-medium">Your saved estimates applied</span>: {dcfRange.estimate_override_carried.fields?.join(', ')}{dcfRange.estimate_override_carried.note ? ` — ${dcfRange.estimate_override_carried.note}` : ''} · saved {dcfRange.estimate_override_carried.created_at?.slice(0, 10)}
          <span className="text-muted-foreground"> · revoke on Model Accuracy</span>
        </div>
      ) : null}
      {forecast ? <ForecastSection fc={forecast} scenarios={dcfRange?.guidance_forecast_scenarios} /> : null}
      {canWork ? <EstimateWorkbench runId={runId!} ticker={ticker!} block={blockIn} forecast={forecast} dcfRange={dcfRange} onRunUpdated={onRunUpdated} /> : null}

      {block.rationale ? <ClampText text={block.rationale} lines={3} className="mt-4 text-[13px] leading-snug text-foreground/85" /> : null}
      {block.track_record ? <ClampText text={`Track record: ${block.track_record}`} lines={2} className="mt-2 text-xs text-muted-foreground" /> : null}
      {block.citations?.length ? (
        <ClampText text={`Sources: ${block.citations.join(' · ')}${block.model ? ` · model ${block.model}` : ''}`} lines={1} className="mt-2 text-xs text-muted-foreground/80" />
      ) : null}
    </Card>
  );
}

export default GuidanceEstimatesPanel;
