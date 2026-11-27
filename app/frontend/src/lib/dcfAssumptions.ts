/**
 * dcfAssumptions — the growth and FCF-margin figures the DCF actually ran on.
 *
 * Owner rule: no display-only valuation. A scenario carries two kinds of
 * growth / margin fields:
 *   - `growth_rate` / `fcf_margin_start`: the legacy single-value inputs. When
 *     the guidance forecast is active the DCF does NOT run on them (Z74.SI base
 *     shows 4.5% / 17.7% there while the DCF used 3.5% / 8.3% in Year 1).
 *   - `leg_inputs.DCF.projection_rows`: the year-by-year rows the DCF leg
 *     discounted (growth_pct, fcf_margin, pv_fcf). These are the figures used.
 *
 * Read the rows first; fall back to `leg_inputs.DCF.growth_schedule` for
 * growth, and to the legacy fields only when no schedule exists (older runs).
 * Mirrors `_dcf_assumptions_used` in src/utils/pdf_report.py so web and PDF
 * print the same numbers.
 */
import type { DcfCase } from '@/lib/reportTypes';

export interface DcfAssumptionsUsed {
  /** 'schedule' = read from the DCF leg's own projection; 'legacy' = single-value fields. */
  source: 'schedule' | 'legacy';
  y1Growth: number | null;
  /** Compound growth over projection years 1-5 (null when fewer than 5 years or legacy). */
  cagr5: number | null;
  y1Margin: number | null;
  /** Margin of the final projection year (the one the terminal value capitalises). */
  terminalMargin: number | null;
  /** Number of projection years the terminal margin belongs to. */
  years: number;
  /** Legacy one-shot margin delta — only meaningful when source === 'legacy'. */
  marginDelta: number | null;
}

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

export function cagr(growths: Array<number | null>, n = 5): number | null {
  if (growths.length < n) return null;
  let f = 1;
  for (let i = 0; i < n; i++) {
    const g = growths[i];
    if (g == null) return null;
    f *= 1 + g;
  }
  return f > 0 ? Math.pow(f, 1 / n) - 1 : null;
}

export function dcfAssumptionsUsed(c: DcfCase | null | undefined): DcfAssumptionsUsed {
  const leg = c?.leg_inputs?.DCF;
  const rows = Array.isArray(leg?.projection_rows) ? leg!.projection_rows! : [];
  const legacyMd = num(c?.margin_delta_absolute ?? c?.margin_delta_per_year);

  let growths: Array<number | null> = rows.map((r) => num(r?.growth_pct));
  if (!growths.some((g) => g != null) && Array.isArray(leg?.growth_schedule)) {
    growths = leg!.growth_schedule!.map(num);
  }
  const margins: Array<number | null> = rows.map((r) => num(r?.fcf_margin));
  const hasGrowth = growths.length > 0 && growths[0] != null;
  const hasMargin = margins.length > 0 && margins[0] != null;

  if (!hasGrowth && !hasMargin) {
    return {
      source: 'legacy',
      y1Growth: num(c?.growth_rate),
      cagr5: null,
      y1Margin: num(c?.fcf_margin_start),
      terminalMargin: null,
      years: 0,
      marginDelta: legacyMd,
    };
  }
  return {
    source: 'schedule',
    y1Growth: hasGrowth ? growths[0] : num(c?.growth_rate),
    cagr5: hasGrowth ? cagr(growths) : null,
    y1Margin: hasMargin ? margins[0] : num(c?.fcf_margin_start),
    terminalMargin: hasMargin ? margins[margins.length - 1] : null,
    years: hasMargin ? margins.length : growths.length,
    marginDelta: null,
  };
}
