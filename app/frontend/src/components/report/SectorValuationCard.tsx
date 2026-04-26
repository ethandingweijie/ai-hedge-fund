/**
 * SectorValuationCard — production sector-specific valuation card.
 *
 * Consumes the `SectorCardPayload` produced by the backend
 * (src/data/sector_kpi_framework.render_card_payload). Visual treatment
 * matches "Option B" from the stylistic preview:
 *   - Hero strip with sector › profile + ticker + anchor-method badges
 *   - Themed sections grouped by accent (Profitability / Capital / Risk /
 *     Growth / Operations) with colored gradient + left-border accent
 *   - Per-KPI in-band / near-floor / out-of-band / fallback badges
 *
 * Mount points:
 *   - MobileReportView Valuation section (after MobilePriceTarget)
 *   - V2ReportView Valuation tab (after V2ScenarioBars)
 *
 * Legacy sub-profiles (SaaS / REIT / Biopharma) are excluded backend-side
 * — the payload will be absent from `data.sector_card` and the frontend
 * just renders nothing.
 */
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';
import type { SectorCardPayload, SectorKpi, SectorKpiAccent } from '@/lib/reportTypes';

const accentBg: Record<SectorKpiAccent, string> = {
  blue:   'from-blue-500/15 to-blue-500/0 border-blue-500/30',
  green:  'from-emerald-500/15 to-emerald-500/0 border-emerald-500/30',
  amber:  'from-amber-500/15 to-amber-500/0 border-amber-500/30',
  rose:   'from-rose-500/15 to-rose-500/0 border-rose-500/30',
  violet: 'from-violet-500/15 to-violet-500/0 border-violet-500/30',
};

const accentText: Record<SectorKpiAccent, string> = {
  blue:   'text-blue-600 dark:text-blue-400',
  green:  'text-emerald-600 dark:text-emerald-400',
  amber:  'text-amber-600 dark:text-amber-400',
  rose:   'text-rose-600 dark:text-rose-400',
  violet: 'text-violet-600 dark:text-violet-400',
};

function fmt(k: SectorKpi): string {
  if (k.value == null || k.value === '') return '—';
  if (k.format === 'string') return String(k.value);
  const v = Number(k.value);
  if (!Number.isFinite(v)) return '—';
  const d = k.decimals ?? (k.format === 'pct' ? 1 : 2);
  switch (k.format) {
    case 'pct': return `${(v * 100).toFixed(d)}%`;
    case 'usd': return `$${v.toLocaleString(undefined, { maximumFractionDigits: d })}`;
    case 'x':   return `${v.toFixed(d)}×`;
    case 'int': return v.toLocaleString();
    default:    return String(v);
  }
}

type StatusLabel = 'IN-BAND' | 'NEAR FLOOR' | 'NEAR CEIL' | 'OUT-OF-BAND';
type StatusVariant = 'success' | 'warning' | 'destructive' | 'outline';

function statusBadge(k: SectorKpi): { label: StatusLabel; variant: StatusVariant } | null {
  if (k.value == null || k.value === '' || k.format === 'string') return null;
  const v = Number(k.value);
  if (!Number.isFinite(v) || k.clamp_low == null || k.clamp_high == null) return null;
  if (v < k.clamp_low || v > k.clamp_high) return { label: 'OUT-OF-BAND', variant: 'destructive' };
  const range = k.clamp_high - k.clamp_low;
  if (v < k.clamp_low + range * 0.1)  return { label: 'NEAR FLOOR', variant: 'warning' };
  if (v > k.clamp_high - range * 0.1) return { label: 'NEAR CEIL',  variant: 'warning' };
  return { label: 'IN-BAND', variant: 'success' };
}

interface Props {
  payload?: SectorCardPayload | null;
}

export function SectorValuationCard({ payload }: Props) {
  if (!payload || !payload.groups || payload.groups.length === 0) return null;

  return (
    <Card className="overflow-hidden">
      {/* Hero strip */}
      <div className="bg-gradient-to-br from-zinc-100 via-zinc-50 to-white dark:from-zinc-900 dark:via-zinc-950 dark:to-black px-5 py-4 border-b border-border">
        <div className="text-xs uppercase tracking-widest text-muted-foreground">
          {payload.sector} · {payload.profile_name}
          {payload.sub_profile && (
            <span className="ml-1 text-muted-foreground/70">({payload.sub_profile})</span>
          )}
        </div>
        <div className="text-xl font-bold tracking-tight mt-0.5">
          {payload.ticker}{' '}
          <span className="text-muted-foreground text-xs font-normal">Sector Valuation</span>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-widest text-muted-foreground mr-1">
            Anchors
          </span>
          {payload.anchor_methods.map((m, i) => (
            <Badge
              key={m}
              variant={i === 0 ? 'success' : 'outline'}
              className="h-5 px-2 text-[11px]"
            >
              {m}
            </Badge>
          ))}
        </div>
      </div>

      {/* Themed groups */}
      <div className="divide-y divide-border">
        {payload.groups.map((g) => {
          const mandatoryCount = g.kpis.filter(k => k.mandatory).length;
          const missingCount   = g.kpis.filter(k => k.value == null || k.value === '').length;
          return (
            <section
              key={g.title}
              className={cn(
                'bg-gradient-to-r border-l-4 px-5 py-4',
                accentBg[g.accent] ?? accentBg.blue,
              )}
            >
              <div className="flex items-center justify-between mb-3">
                <h4 className={cn(
                  'text-xs font-semibold uppercase tracking-wider',
                  accentText[g.accent] ?? accentText.blue,
                )}>
                  {g.title}
                </h4>
                <span className="text-[10px] text-muted-foreground">
                  {mandatoryCount} mandatory · {missingCount} missing
                </span>
              </div>

              <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                {g.kpis.map((k) => {
                  const sb = statusBadge(k);
                  const missing = k.value == null || k.value === '';
                  return (
                    <div key={k.key} className="min-w-0">
                      <div className="flex items-center gap-1 text-[11px] text-muted-foreground truncate">
                        <span className="truncate" title={k.label}>{k.label}</span>
                        {k.mandatory && (
                          <Badge
                            variant="outline"
                            className="h-3.5 px-1 text-[8px] border-amber-500/40 text-amber-500 shrink-0"
                          >
                            M
                          </Badge>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-baseline gap-1.5">
                        <span className={cn(
                          'text-base font-semibold tabular-nums',
                          missing && 'text-zinc-500',
                        )}>
                          {fmt(k)}
                        </span>
                        {k.unit && !missing && (
                          <span className="text-[10px] text-muted-foreground">{k.unit}</span>
                        )}
                      </div>
                      {sb && (
                        <Badge variant={sb.variant} className="mt-1 h-3.5 px-1 text-[8px]">
                          {sb.label}
                        </Badge>
                      )}
                      {missing && k.mandatory && (
                        <Badge variant="warning" className="mt-1 h-3.5 px-1 text-[8px]">
                          FALLBACK USED
                        </Badge>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      {/* V3 Audit Bridge — Pre-IV × Quality × Risk × Commodity → Final */}
      {payload.audit_bridge && (
        <>
          <Separator />
          <AuditBridgeBar bridge={payload.audit_bridge} />
        </>
      )}

      {/* Footer */}
      {payload.source_priority && payload.source_priority.length > 0 && (
        <>
          <Separator />
          <div className="px-5 py-3">
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1">
              Source priority
            </div>
            <div className="flex flex-wrap gap-1.5">
              {payload.source_priority.map((s, i) => (
                <Badge key={s} variant="outline" className="h-5 px-2 text-[11px]">
                  {i + 1}. {s}
                </Badge>
              ))}
            </div>
          </div>
        </>
      )}
    </Card>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
// V3 AuditBridgeBar — renders the Pre-IV → Quality × Risk × Commodity → Final
// breakdown so the user can immediately see WHICH lever drove the IV
// adjustment. Each lever has its own color tone matching its role:
//   Quality   → blue   (operational excellence)
//   Risk      → green  (balance sheet strength)
//   Commodity → amber  (forward macro leverage)
// ─────────────────────────────────────────────────────────────────────────────

import type { AuditBridge } from '@/lib/reportTypes';

function AuditBridgeBar({ bridge }: { bridge: AuditBridge }) {
  const fmtMult = (v: number) => `${v.toFixed(2)}×`;
  const isLift  = (v: number) => v > 1.005;
  const isDrag  = (v: number) => v < 0.995;
  const tone    = (v: number) =>
    isLift(v) ? 'text-emerald-600 dark:text-emerald-400'
              : isDrag(v) ? 'text-rose-500'
                          : 'text-muted-foreground';

  // V4-β z-chip: shows peer-cohort z-score when present. Tone matches sign so
  // user can scan at a glance whether the lever is peer-validated.
  const zChip = (z?: number | null, n?: number | null) => {
    if (z == null || !Number.isFinite(z)) return null;
    const tier =
      Math.abs(z) >= 1.5 ? 'top/bot decile' :
      Math.abs(z) >= 1.0 ? 'top/bot quartile' :
      Math.abs(z) >= 0.5 ? 'above/below' : 'near median';
    const cls = z > 0
      ? 'border-emerald-500/40 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10'
      : z < 0
        ? 'border-rose-500/40 text-rose-500 bg-rose-500/10'
        : 'border-zinc-500/30 text-muted-foreground bg-zinc-500/10';
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded px-1 py-0.5 text-[8px] font-semibold tabular-nums border',
          cls,
        )}
        title={`Peer cohort: n=${n ?? '?'}, ${tier}`}
      >
        z {z >= 0 ? '+' : ''}{z.toFixed(1)}
        {n != null && <span className="opacity-70">·n{n}</span>}
      </span>
    );
  };

  const hasAnyZ = bridge.quality_z != null || bridge.risk_z != null;

  return (
    <div className="px-5 py-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
          Composite Adjustment Bridge
          {hasAnyZ && (
            <span className="ml-2 inline-flex items-center rounded border border-violet-500/40 bg-violet-500/10 px-1 py-px text-[8px] font-semibold text-violet-600 dark:text-violet-400">
              V4-β · Z-DRIVEN
            </span>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground">
          cap: {bridge.cap_high.toFixed(2)}×
          {bridge.was_capped && (
            <span className="ml-1 text-amber-500 font-semibold">CAPPED</span>
          )}
        </div>
      </div>

      {/* Visual bridge: Q × R × C → Final */}
      <div className="grid grid-cols-7 items-center gap-1 text-center">
        {/* Quality */}
        <div className="col-span-2 rounded-md border border-blue-500/30 bg-blue-500/10 px-2 py-1.5">
          <div className="flex items-center justify-center gap-1 text-[9px] uppercase tracking-wider text-blue-600 dark:text-blue-400">
            <span>Quality</span>
            {bridge.quality_weight != null && (
              <span className="text-muted-foreground normal-case tracking-normal">
                w={bridge.quality_weight.toFixed(2)}
              </span>
            )}
          </div>
          <div className={cn('text-sm font-bold tabular-nums', tone(bridge.quality))}>
            {fmtMult(bridge.quality)}
          </div>
          <div className="mt-0.5 flex items-center justify-center">
            {zChip(bridge.quality_z, bridge.quality_cohort)}
          </div>
          <div className="text-[9px] text-muted-foreground truncate" title={bridge.quality_note}>
            {bridge.quality_note}
          </div>
        </div>

        <div className="text-muted-foreground text-sm">×</div>

        {/* Risk */}
        <div className="col-span-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5">
          <div className="flex items-center justify-center gap-1 text-[9px] uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
            <span>Risk</span>
            {bridge.risk_weight != null && (
              <span className="text-muted-foreground normal-case tracking-normal">
                w={bridge.risk_weight.toFixed(2)}
              </span>
            )}
          </div>
          <div className={cn('text-sm font-bold tabular-nums', tone(bridge.risk))}>
            {fmtMult(bridge.risk)}
          </div>
          <div className="mt-0.5 flex items-center justify-center">
            {zChip(bridge.risk_z, bridge.risk_cohort)}
          </div>
          <div className="text-[9px] text-muted-foreground truncate" title={bridge.risk_note}>
            {bridge.risk_note}
          </div>
        </div>

        <div className="text-muted-foreground text-sm">×</div>

        {/* Commodity */}
        <div className="col-span-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5">
          <div className="flex items-center justify-center gap-1 text-[9px] uppercase tracking-wider text-amber-600 dark:text-amber-400">
            <span>Comm</span>
            {bridge.commodity_weight != null && (
              <span className="text-muted-foreground normal-case tracking-normal">
                w={bridge.commodity_weight.toFixed(2)}
              </span>
            )}
          </div>
          <div className={cn('text-sm font-bold tabular-nums', tone(bridge.commodity))}>
            {fmtMult(bridge.commodity)}
          </div>
        </div>
      </div>

      {/* Final composite */}
      <div className="mt-2 flex items-center justify-between rounded-md border border-border bg-muted/40 px-3 py-1.5">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Final composite multiplier
        </div>
        <div className="text-base font-bold tabular-nums">
          <span className="text-muted-foreground text-xs font-normal">
            {fmtMult(bridge.raw_composite)} raw →
          </span>{' '}
          <span className={tone(bridge.final_multiplier)}>
            {fmtMult(bridge.final_multiplier)}
          </span>
        </div>
      </div>
    </div>
  );
}
