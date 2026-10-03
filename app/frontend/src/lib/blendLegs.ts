/**
 * blendLegs — which valuation legs carry weight, and what each was worth.
 *
 * Owner, 2026-09-27 (MOH): the Valuation tab shows the valuation that was
 * computed, never a figure computed only to be shown. MOH's segment SOTP
 * panel printed $3,365.70 a share from a leg that carried no weight; the
 * published value came from four other legs. Every panel now asks this module
 * whether its leg is in the blend and reads the leg's own value from the
 * scenario's method table.
 *
 * Read off the scenarios (effective weights, else methods_used) rather than
 * a payload flag, so archived runs follow the same rule as new ones.
 */
import type { DcfRange } from '@/lib/reportTypes';

const SCENARIOS = ['bear', 'base', 'bull'] as const;

export const SOTP_ANALYST_LEGS = ['SOTP (analyst)', 'Analyst SOTP'];
export const SOTP_SEGMENT_LEGS = ['SOTP (segments)', 'Sum of Parts', 'SOTP', 'SOTP (Segments)'];
export const SOTP_LOOKTHROUGH_LEGS = ['SOTP / NAV', 'SOTP / NAV (look-through)', 'NAV Discount'];
export const SOTP_PUBLISHED_LEGS = ['SOTP (published)', 'Published SOTP'];
export const SOTP_FRE_LEGS = ['SOTP (FRE + carry)', 'SOTP (FRE+Carry)'];
/** Every sum-of-the-parts leg; all render in the one analyst format (owner, 2026-10-03). */
export const SOTP_FAMILY_LEGS = [
  ...SOTP_ANALYST_LEGS, ...SOTP_SEGMENT_LEGS, ...SOTP_LOOKTHROUGH_LEGS, ...SOTP_PUBLISHED_LEGS, ...SOTP_FRE_LEGS,
];
export const NAV_DISPLAY_LEGS = ['NAV (Cap Rates)', 'RNAV (published)', 'NAV (published)', 'NAV', 'RNAV'];
export const BANK_DISPLAY_LEG = 'GGM (P/B)';
export const DCF_FAMILY_LEGS = [
  'DCF', 'Backlog DCF', 'Backlog-coverage DCF', 'Contracted-backlog DCF', 'Depleting Asset DCF',
  'Reverse DCF', 'Modified DCF',
];

/** Leg (value key) -> the largest weight it carried in any scenario. */
export function legsInBlend(d?: DcfRange | null): Map<string, number> {
  const out = new Map<string, number>();
  if (!d) return out;
  for (const s of SCENARIOS) {
    const sc = d[s];
    if (!sc) continue;
    const eff = sc.effective_weights;
    if (eff && eff.length > 0) {
      for (const e of eff) {
        const k = e.value_key || e.method;
        const w = e.weight ?? 0;
        if (k && w > 0) out.set(k, Math.max(out.get(k) ?? 0, w));
      }
    } else {
      for (const k of sc.methods_used ?? []) if (!out.has(k)) out.set(k, 0);
    }
  }
  return out;
}

/** The first of `names` that carries weight, or null. */
export function blendLeg(d: DcfRange | null | undefined, names: string[]): string | null {
  const legs = legsInBlend(d);
  return names.find((n) => legs.has(n)) ?? null;
}

export function inBlend(d: DcfRange | null | undefined, names: string[]): boolean {
  return blendLeg(d, names) !== null;
}

/** A leg's weight in the blend (0-1), or null when it carries none. */
export function legWeight(d: DcfRange | null | undefined, leg: string): number | null {
  const w = legsInBlend(d).get(leg);
  return w == null ? null : w;
}

/** A leg's per-share value in one scenario, from the engine's own method table. */
export function legValue(
  d: DcfRange | null | undefined, scenario: 'bear' | 'base' | 'bull', leg: string,
): number | null {
  const v = d?.[scenario]?.method_iv_table?.[leg];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
