/**
 * sotpBreakdown — the one sum-of-the-parts block a report renders.
 *
 * Owner, 2026-10-03: every SOTP leg (analyst, segments, look-through, published, FRE + carry) shows
 * in the analyst format. New runs carry `sotp_breakdown` in that shape for whichever SOTP leg carries
 * weight. Archived runs from before this change may carry the retired `segment_sotp` block instead;
 * it is adapted here so an old refining report reads the same as a new one.
 */
import type { DcfRange, SotpBreakdown } from '@/lib/reportTypes';
import { blendLeg, legWeight, SOTP_FAMILY_LEGS, SOTP_SEGMENT_LEGS } from '@/lib/blendLegs';

interface LegacySegmentRow {
  segment?: string | null; basis?: string | null; revenue?: number | null; ebitda?: number | null;
  multiple?: number | null; ev?: number | null; note?: string | null; band_rationale?: string | null;
}
interface LegacySegmentSotp {
  currency?: string | null; segments?: LegacySegmentRow[]; total_ev?: number | null; sum_of_parts_ev?: number | null;
  growth_premium?: number | null; value_per_share?: number | null; shares?: number | null; basis_note?: string | null;
  checks?: { reminders?: string[] | null } | null;
}

const BASIS_LABEL: Record<string, string> = { ev_ebitda: 'EV/EBITDA', ev_revenue: 'EV/Rev', carrying_value: 'Book', excluded: 'Excluded' };

function adaptLegacySegments(s: LegacySegmentSotp): SotpBreakdown | null {
  const segs = s.segments ?? [];
  if (!segs.length) return null;
  const parts = s.sum_of_parts_ev ?? segs.reduce((a, r) => a + (r.ev ?? 0), 0);
  const total = s.total_ev ?? parts;
  const positive = segs.reduce((a, r) => a + Math.max(0, r.ev ?? 0), 0);
  const rows = segs.map((r) => ({
    name: r.segment ?? '',
    revenue_fwd: r.revenue ?? undefined,
    ebit: r.ebitda ?? undefined,
    method: BASIS_LABEL[r.basis ?? ''] ?? (r.basis ?? ''),
    multiple: r.multiple ?? undefined,
    value: r.ev ?? 0,
    value_split_pct: positive > 0 && (r.ev ?? 0) > 0 ? (r.ev ?? 0) / positive : 0,
    rationale: r.note ?? r.band_rationale ?? undefined,
  }));
  const premium = s.growth_premium ?? (parts > 0 ? total / parts : 1);
  const adjustments = Math.abs(premium - 1) > 1e-6 ? [{ label: `Growth premium ×${premium.toFixed(2)}`, amount: total - parts }] : [];
  return {
    method: 'SOTP (segments)',
    reporting_currency: (s.currency ?? 'USD').toUpperCase(),
    revenue_label: 'Revenue', earnings_label: 'EBITDA (est.)',
    rows, segment_value: parts, associates: 0, net_cash: 0, adjustments,
    nav: total, holdco_discount_pct: 0, holdco_discount: 0, final: total,
    per_share: s.value_per_share ?? null, per_share_reporting: s.value_per_share ?? null,
    shares: s.shares ?? null, fx_to_reporting: 1,
    reminders: (s.checks?.reminders ?? []).filter((r) => !r.startsWith('Segment shares of enterprise value sum to')),
    basis_note: s.basis_note ?? null,
  };
}

/** The SOTP block to render, its leg and weight — or null when no SOTP leg carries weight. */
export function sotpFor(d: DcfRange | null | undefined): { breakdown: SotpBreakdown; leg: string; weight: number | null } | null {
  const leg = blendLeg(d, SOTP_FAMILY_LEGS);
  if (!d || !leg) return null;
  const weight = legWeight(d, leg);
  if (d.sotp_breakdown && (d.sotp_breakdown.rows?.length ?? 0) > 0) return { breakdown: d.sotp_breakdown, leg, weight };
  if (SOTP_SEGMENT_LEGS.includes(leg)) {
    const legacy = (d as unknown as { segment_sotp?: LegacySegmentSotp | null }).segment_sotp;
    const adapted = legacy ? adaptLegacySegments(legacy) : null;
    if (adapted) return { breakdown: adapted, leg, weight };
  }
  return null;
}
