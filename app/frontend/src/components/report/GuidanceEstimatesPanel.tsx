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
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { GuidanceEstimates, GuidanceScenarioRow } from '@/lib/reportTypes';

const LABEL_CLS = 'text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70';
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

export function GuidanceEstimatesPanel({ block }: { block: GuidanceEstimates }) {
  const g = block.guidance ?? {};
  const c = block.consensus ?? {};
  const rows = block.estimates ?? {};
  const fy1 = block.fiscal_year_1 ?? 'FY+1';
  const fy2 = block.fiscal_year_2 ?? 'FY+2';
  const ch = block.channel;
  const explicitYears = ch?.explicit_years ?? 0;
  const fadeYears = ch?.fade_years ?? 0;

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className={LABEL_CLS}>Management guidance → estimates</div>
          <div className="mt-0.5 text-[13px] text-foreground">
            {block.applied
              ? `Sets the DCF's year 1–${explicitYears} growth per scenario, fading onto the engine path by year ${explicitYears + fadeYears + 1}`
              : `Shown, not applied — ${block.not_applied_reason ?? 'the DCF did not use it'}`}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <Badge variant="outline" className="text-[10px]">confidence {block.confidence ?? '—'}</Badge>
          {g.status && g.status !== 'none' ? <Badge variant="outline" className="text-[10px]">guidance {g.status}</Badge> : null}
          {block.as_of ? <span className="text-[10px] text-muted-foreground">as of {block.as_of}</span> : null}
        </div>
      </div>

      {/* 1. Guidance as stated vs consensus */}
      <div className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 text-[12px] sm:grid-cols-2">
        <div>
          <div className={LABEL_CLS}>Guidance ({fy1})</div>
          <dl className="mt-1 space-y-1 tabular-nums">
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Revenue growth</dt><dd>{range(g.revenue_growth, pct)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Revenue</dt><dd>{amountRange(g.revenue)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">EBITDA / operating margin</dt><dd>{range(g.ebitda_margin, margin)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">EPS{g.eps?.currency ? ` (${g.eps.currency})` : ''}</dt><dd>{range(g.eps, (v) => num(v))}</dd></div>
            {g.basis ? <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Basis</dt><dd>{g.basis}</dd></div> : null}
          </dl>
        </div>
        <div>
          <div className={LABEL_CLS}>Consensus ({fy1})</div>
          <dl className="mt-1 space-y-1 tabular-nums">
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Revenue growth</dt><dd>{pct(c.revenue_growth_fy1)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">EPS</dt><dd>{num(c.eps_fy1)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Guidance vs consensus</dt><dd>{pct(block.guidance_vs_consensus_pct)}</dd></div>
            {c.as_of ? <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Consensus as of</dt><dd>{c.as_of}</dd></div> : null}
          </dl>
        </div>
      </div>
      {g.quote ? (
        <div className="mt-2 text-[11px] italic leading-snug text-muted-foreground">“{g.quote}”{g.source ? <span className="not-italic"> — {g.source}</span> : null}</div>
      ) : null}

      {/* 2. The model's estimates */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-[12px] tabular-nums">
          <thead>
            <tr className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground/70">
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
        <div className="mt-3 text-[11px] text-muted-foreground">
          <span className={LABEL_CLS}>Base DCF growth path</span>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 tabular-nums">
            {ch.schedule.map((v, i) => (
              <span key={i} className={i < explicitYears ? 'font-medium text-foreground' : i < explicitYears + fadeYears ? 'text-foreground/80' : ''}>
                Y{i + 1} {pct(v)}
              </span>
            ))}
          </div>
          <div className="mt-1">
            Years 1–{explicitYears} from the guidance-derived estimate; engine year 1 was {pct(ch.engine_year1)}; the terminal is untouched.
          </div>
        </div>
      ) : null}

      {block.rationale ? <div className="mt-3 text-[11.5px] leading-snug text-foreground/85">{block.rationale}</div> : null}
      {block.track_record ? <div className="mt-1 text-[11px] text-muted-foreground">Track record: {block.track_record}</div> : null}
      {block.citations?.length ? (
        <div className="mt-2 text-[10.5px] text-muted-foreground/80">
          Sources: {block.citations.join(' · ')}{block.model ? ` · model ${block.model}` : ''}
        </div>
      ) : null}
    </Card>
  );
}

export default GuidanceEstimatesPanel;
