/**
 * Model Accuracy: labels, formatters and the client-side maturity arithmetic.
 * Nothing here scores anything; it only formats what the endpoints return.
 */
import type { DiagnosticCard, IndustryInputRow, LearningMaturity } from '@/lib/api';

export const HORIZON_LABEL: Record<string, string> = {
  consensus_0d: 'Street consensus',
  px_30d: 'price after 30 days',
  px_90d: 'price after 90 days',
  px_180d: 'price after 180 days',
  px_365d: 'price after a year',
};

export const HORIZON_SHORT: Record<string, string> = {
  consensus_0d: 'Consensus',
  px_30d: '30d',
  px_90d: '90d',
  px_180d: '180d',
  px_365d: '365d',
};

/** The price horizons a run comes due at, in days after the run date. */
export const PRICE_HORIZONS: { key: string; days: number }[] = [
  { key: 'px_30d', days: 30 },
  { key: 'px_90d', days: 90 },
  { key: 'px_180d', days: 180 },
  { key: 'px_365d', days: 365 },
];

/** Minimum matured labels before a price-horizon tile reports a number. The
 *  learning layer's scenario and gate loops use 20; the tiles use the same. */
export const PRICE_MIN_N = 20;

export const CATEGORY_LABEL: Record<DiagnosticCard['category'], string> = {
  bias: 'Market bias',
  method: 'Method accuracy',
  shared_bias: 'Inputs & parameters',
  routing: 'Profile routing',
};

/** Calibration families of the self-learning layer. */
export const FAMILY_LABEL: Record<string, string> = { iv: 'intrinsic value', pt: 'target', est: 'estimates' };

export const BASIS_LABEL: Record<string, string> = {
  sotp_analyst: 'SOTP (analyst)',
  'sotp_analyst+segment_sotp': 'SOTP (analyst) + segments',
  segment_sotp: 'SOTP (segments)',
  holdco_lookthrough: 'Holdco look-through',
  sotp_blocked_no_segments: 'SOTP blocked: no segment data',
};

export const REVIEW_LABEL: Record<string, string> = {
  pending: 'pending review',
  accepted: 'accepted · live',
  revoked: 'revoked',
  omitted: 'omitted',
  changed_since_acceptance: 'figures changed · re-accept',
  unknown: 'status unavailable',
};

export const SOURCE_LABEL: Record<string, string> = {
  sec_10k: 'read from the 10-K',
  sec_10q: 'read from the 10-Q',
  annual_report_verified: 'annual report, verified twice',
};

export const INPUT_LABEL: Record<IndustryInputRow['kind'], string> = {
  pv10: 'Reserve value (PV-10)',
  backlog: 'Backlog',
  maintenance_capex: 'Maintenance capex',
  rate_base: 'Regulated rate base',
  fcf_guidance: 'FCF guidance (faded overlay)',
  sotp: 'SOTP segments (Gemini, cited)',
  pipeline: 'Pipeline assets (rNPV, Gemini, cited)',
  embedded_value: 'Embedded value (life insurer, Gemini, cited)',
  alt_manager: 'Distributable earnings and multiples (alt manager, Gemini, cited)',
  nav: 'Published NAV / RNAV and cap rate (Gemini, cited)',
  ppa: 'PPA portfolio: capacity, generation, contracts, project debt (renewable owner, Gemini, cited)',
};

/** One-line name for the collapsed row. */
export const INPUT_SHORT: Record<IndustryInputRow['kind'], string> = {
  pv10: 'PV-10',
  backlog: 'Backlog',
  maintenance_capex: 'Maintenance capex',
  rate_base: 'Rate base',
  fcf_guidance: 'FCF guidance',
  sotp: 'SOTP segments',
  pipeline: 'Pipeline (rNPV)',
  embedded_value: 'Embedded value',
  alt_manager: 'Alt-manager DE',
  nav: 'NAV / RNAV',
  ppa: 'PPA portfolio',
};

export const FIELD_LABEL: Record<string, string> = {
  ev_ebitda_norm: 'EV/EBITDA (through-cycle)',
  pe_norm: 'P/E (through-cycle)',
};

export const EST_FIELD_LABEL: Record<string, string> = { revenue_growth: 'Revenue growth', ebitda_margin: 'EBITDA margin', eps: 'EPS' };
export const GROUP_LABEL: Record<string, string> = { archetype: 'forecast archetype', confidence: 'guidance confidence', market: 'market' };

export function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${v.toFixed(1)}%`;
}

export function share(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`;
}

/** A signed error: growth and EPS are log errors (shown as %), the margin is in points. */
export function errText(field: string, v: number | null | undefined): string {
  if (v == null) return '—';
  if (field === 'ebitda_margin') return `${(v * 100).toFixed(1)} pt`;
  return `${((Math.exp(v) - 1) * 100).toFixed(1)}%`;
}

export function fmtAmount(v: number | null | undefined, ccy?: string): string {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const [div, unit] = abs >= 1e12 ? [1e12, 'tn'] : abs >= 1e9 ? [1e9, 'bn'] : [1e6, 'mn'];
  return `${ccy ? `${ccy} ` : ''}${(v / div).toLocaleString(undefined, { maximumFractionDigits: 1 })}${unit}`;
}

export function gapText(g: number | null | undefined): string {
  return g == null ? '—' : `${g >= 0 ? '+' : ''}${(g * 100).toFixed(1)}%`;
}

/** "Dec 3, 2026" from an ISO date. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export interface HorizonMaturity {
  key: string;
  days: number;
  /** Ledger runs whose run date + horizon is on or before today. */
  due: number;
  /** Ledger runs in total (the most this horizon can ever label from today's ledger). */
  total: number;
  /** Earliest date a not-yet-due run comes due, or null if every run is due. */
  next: string | null;
  /** Date by which `need` ledger runs will have come due, or null if the ledger holds fewer. */
  reachesNeed: string | null;
}

/** Calendar arithmetic over the ledger's run dates: when each horizon comes due. */
export function horizonMaturity(m: LearningMaturity | null, need: number, today = new Date()): HorizonMaturity[] {
  const todayIso = today.toISOString().slice(0, 10);
  const dates = Object.entries(m?.runs_by_date ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const total = dates.reduce((a, [, n]) => a + n, 0);
  return PRICE_HORIZONS.map(({ key, days }) => {
    let due = 0;
    let next: string | null = null;
    let reachesNeed: string | null = null;
    let cum = 0;
    for (const [d, n] of dates) {
      const at = addDays(d, days);
      if (at <= todayIso) due += n;
      else if (next == null) next = at;
      cum += n;
      if (reachesNeed == null && cum >= need) reachesNeed = at;
    }
    return { key, days, due, total, next, reachesNeed };
  });
}

/** dormant: nothing to learn from yet; collecting: under the minimum; live: at or over it. */
export type LoopState = 'dormant' | 'collecting' | 'live';

export function loopState(n: number, need: number | null, anyOk?: boolean): LoopState {
  if (anyOk) return 'live';
  if (n <= 0) return 'dormant';
  if (need != null && n >= need) return 'live';
  return 'collecting';
}
