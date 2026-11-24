/**
 * SectorValuationCard — production sector-specific valuation card.
 *
 * Consumes the `SectorCardPayload` produced by the backend
 * (src/data/sector_kpi_framework.render_card_payload):
 *   - Header: sector · profile, "Sector KPIs", the valuation legs that carried weight
 *   - One flat section per KPI group (Profitability / Capital / Risk / Growth / Operations)
 *   - Per KPI: value, plus a quiet note when it sits near or outside its expected
 *     band, or when a required KPI was missing and the engine used a fallback
 *
 * Owner, 2026-11-24 (UI clean-up): no tinted group panels, no mandatory/missing
 * counts, no "M" or ALL-CAPS status pills, no data-source footer. Optional KPIs
 * with no value are left out; a group with nothing to show is left out.
 *
 * Mount points: ReportViewPage / ReportPage (Valuation section) and the
 * V2ReportView Valuation tab. Legacy sub-profiles (SaaS / REIT / Biopharma)
 * are excluded backend-side — the payload is absent and this renders nothing.
 */
import { Card } from '@/components/ui/card';
import type { DcfRange, SectorCardPayload, SectorKpi } from '@/lib/reportTypes';
import { legsInBlend } from '@/lib/blendLegs';

function fmt(k: SectorKpi): string {
  if (k.value == null || k.value === '') return '—';
  const v = Number(k.value);
  // Non-numeric `string` values (dates, free text) render verbatim. A numeric
  // value tagged `string` still gets rounded below rather than dumped raw
  // (guards against the historic "-0.4326732673" leak).
  if (k.format === 'string' && !Number.isFinite(v)) return String(k.value);
  if (!Number.isFinite(v)) return '—';
  const d = k.decimals ?? (k.format === 'pct' || k.format === 'pct100' ? 1 : 2);
  switch (k.format) {
    case 'pct':    return `${(v * 100).toFixed(d)}%`;
    case 'pct100': return `${v.toFixed(d)}%`;
    case 'bps':    return `${v.toFixed(0)} bps`;
    case 'usd':    return `$${v.toLocaleString(undefined, { maximumFractionDigits: d })}`;
    case 'usd_b':  return `$${v.toFixed(d)}B`;
    case 'x':      return `${v.toFixed(d)}×`;
    case 'int':    return v.toLocaleString();
    default:       return v.toFixed(d);   // numeric fallback — never raw float
  }
}

/** Only notable band positions get a note; "in band" is the unremarkable default. */
function bandNote(k: SectorKpi): string | null {
  if (k.value == null || k.value === '' || k.format === 'string') return null;
  const v = Number(k.value);
  if (!Number.isFinite(v) || k.clamp_low == null || k.clamp_high == null) return null;
  if (v < k.clamp_low || v > k.clamp_high) return 'Outside expected range';
  const range = k.clamp_high - k.clamp_low;
  if (v < k.clamp_low + range * 0.1)  return 'Near the low end';
  if (v > k.clamp_high - range * 0.1) return 'Near the high end';
  return null;
}

const isMissing = (k: SectorKpi) => k.value == null || k.value === '';

interface Props {
  payload?: SectorCardPayload | null;
  /** The engine's result, so the method strip lists the legs that carried weight. */
  dcfRange?: DcfRange | null;
}

export function SectorValuationCard({ payload, dcfRange }: Props) {
  if (!payload || !payload.groups || payload.groups.length === 0) return null;
  // Owner, 2026-09-27 (MOH): list the legs that carried weight, at their weights,
  // never the KPI framework's hard-coded `anchor_methods`.
  const legs = Array.from(legsInBlend(dcfRange)).sort((a, b) => b[1] - a[1]);

  // Optional KPIs with no value are dropped; required ones stay so the
  // fallback note can say the engine filled the gap.
  const groups = payload.groups
    .map(g => ({ ...g, kpis: g.kpis.filter(k => !isMissing(k) || k.mandatory) }))
    .filter(g => g.kpis.some(k => !isMissing(k)));
  if (groups.length === 0) return null;

  return (
    <Card className="p-5">
      <p className="text-[13px] text-muted-foreground">
        {payload.sector} · {payload.profile_name}
        {payload.sub_profile ? ` (${payload.sub_profile})` : ''}
      </p>
      <h3 className="mt-0.5 text-[15px] font-semibold text-foreground">Sector KPIs</h3>

      {legs.length > 0 && (
        <p className="mt-2 text-[13px] text-muted-foreground">
          Valued on{' '}
          {legs.map(([m, w], i) => (
            <span key={m}>
              {i > 0 && ', '}
              <span className={i === 0 ? 'font-semibold text-foreground' : 'text-foreground'}>{m}</span>
              {w > 0 ? ` ${(w * 100).toFixed(0)}%` : ''}
            </span>
          ))}
        </p>
      )}

      <div className="mt-4 divide-y divide-border">
        {groups.map((g) => (
          <section key={g.title} className="py-4 first:pt-2 last:pb-0">
            <h4 className="text-[13px] font-semibold text-foreground mb-3">{g.title}</h4>
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
              {g.kpis.map((k) => {
                const missing = isMissing(k);
                const note = missing ? 'Not reported, fallback used' : bandNote(k);
                return (
                  <div key={k.key} className="min-w-0">
                    <p className="text-[13px] text-muted-foreground truncate" title={k.label}>{k.label}</p>
                    <p className="mt-0.5 flex items-baseline gap-1.5">
                      <span className={`text-[17px] font-semibold tabular-nums ${missing ? 'text-muted-foreground' : 'text-foreground'}`}>
                        {fmt(k)}
                      </span>
                      {/* Skip a unit the formatted value already carries ("5.30×" + "×"). */}
                      {k.unit && !missing && !fmt(k).endsWith(k.unit.trim()) && (
                        <span className="text-[12px] text-muted-foreground">{k.unit}</span>
                      )}
                    </p>
                    {note && <p className="mt-0.5 text-[12px] text-muted-foreground">{note}</p>}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </Card>
  );
}
