/**
 * SegmentSotpPanel — the segment sum-of-the-parts, with its working shown.
 *
 * Renders dcf_range[ticker].segment_sotp (built backend-side by
 * dcf_agent._segment_sotp_block). Distinct from SotpAnalystPanel, which
 * renders the ANALYST SOTP (BABA, 09988.HK, 09618.HK) and carries forward
 * estimates, revisions and elasticities this one has no equivalent of.
 *
 * What this panel is for: the leg used to publish $2,621 a share for Marathon
 * — segment REVENUE times a multiple meant for a different kind of business —
 * and the record behind it held only the aggregate, so nothing on screen could
 * be checked. Every column here is a step of that arithmetic: revenue, the
 * margin used to estimate segment EBITDA (and where that margin came from),
 * the owner's through-cycle band, and the EV it produces.
 *
 * The estimated-EBITDA caveat is shown, not buried: segment EBITDA is not
 * disclosed in these filings, and a reader has to know that the middle column
 * is derived rather than reported.
 *
 * Owner, 2026-09-27 (MOH): mounted only when the segment leg carries weight in
 * the blend (see lib/blendLegs), and it says what that weight is. Shares of EV
 * are measured against the sum of the parts; the growth premium the leg applies
 * to that sum is its own line (MOH read 117.6% dividing pre-premium parts by the
 * post-premium total).
 */

import { Card } from '@/components/ui/card';
import type { SegmentSotp } from '@/lib/reportTypes';

const CCY_SYM: Record<string, string> = {
  USD: '$', HKD: 'HK$', CNY: 'RMB', RMB: 'RMB', SGD: 'S$',
  EUR: '€', GBP: '£', JPY: '¥',
};

const LABEL_CLS =
  'text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70';

function fmtBn(v: number | null | undefined, sym: string): string {
  if (v == null || !isFinite(v)) return '—';
  const a = Math.abs(v);
  const s = v < 0 ? '−' : '';
  if (a >= 1e9) return `${s}${sym}${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}${sym}${(a / 1e6).toFixed(0)}M`;
  return `${s}${sym}${a.toFixed(0)}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

/** The multiple, or the reason there isn't one. */
function multipleCell(r: SegmentSotp['segments'][number]): string {
  if (r.basis === 'carrying_value') return 'at book';
  if (r.multiple == null) return '—';
  const band = r.band && r.band.length === 2
    ? ` (${r.band[0]}–${r.band[1]}x)`
    : '';
  return `${r.multiple.toFixed(2)}x${band}`;
}

const TYPE_LABEL: Record<string, string> = {
  refining: 'Refining',
  midstream: 'Midstream',
  chemicals: 'Chemicals',
  renewable_fuels: 'Renewable fuels',
  ethanol: 'Ethanol',
  fuel_marketing: 'Fuel marketing',
  upstream: 'Upstream',
  oilfield_services: 'Oilfield services',
  equity_method: 'Equity-accounted',
  non_business: 'Not a business',
};

function basisSubtitle(rows: SegmentSotp['segments']): string {
  const bases = new Set(rows.map((r) => r.basis));
  if (bases.has('ev_ebitda') && bases.has('ev_revenue')) {
    return 'Each segment valued on its own basis: a through-cycle EV/EBITDA band, or an EV/Revenue multiple for its business type.';
  }
  if (bases.has('ev_revenue')) return 'Each segment valued at an EV/Revenue multiple for its business type.';
  return 'Each segment valued on a through-cycle EV/EBITDA band for its own business type.';
}

export function SegmentSotpPanel({ sotp, weight }: { sotp: SegmentSotp; weight?: number | null }) {
  const sym = CCY_SYM[(sotp.currency ?? 'USD').toUpperCase()] ?? '$';
  const rows = sotp.segments ?? [];
  if (!rows.length) return null;
  // The parts sum BEFORE the growth premium; the leg's EV is that sum x the premium.
  const partsEv = sotp.sum_of_parts_ev ?? rows.reduce((a, r) => a + (r.ev ?? 0), 0);
  const premium = sotp.growth_premium
    ?? (partsEv > 0 && sotp.total_ev != null ? sotp.total_ev / partsEv : 1);
  const share = (ev?: number | null) => (ev != null && partsEv > 0 ? ev / partsEv : null);
  const w = weight ?? sotp.weight ?? null;

  // Any estimated EBITDA on the table means the caveat has to be on it too.
  const anyEstimated = rows.some((r) => r.ebitda != null);
  const anyUnpriced = rows.some((r) => r.multiple == null && r.basis !== 'carrying_value');
  // One rationale per business type, in first-appearance order.
  const bandNotes: [string, string][] = [];
  const seenTypes = new Set<string>();
  for (const r of rows) {
    const ty = r.type ?? '';
    if (r.band_rationale && ty && !seenTypes.has(ty)) {
      seenTypes.add(ty);
      bandNotes.push([ty, r.band_rationale]);
    }
  }

  return (
    <Card className="p-4 sm:p-5 space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className={LABEL_CLS}>Sum of the parts — by business segment</div>
          <div className="text-sm text-muted-foreground mt-1">{basisSubtitle(rows)}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums">
            {sotp.value_per_share != null && isFinite(sotp.value_per_share)
              ? `${sym}${sotp.value_per_share.toLocaleString(undefined, {
                  minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : '—'}
          </div>
          <div className={LABEL_CLS}>
            per share{w != null && w > 0 ? ` · ${(w * 100).toFixed(0)}% of the valuation` : ''}
          </div>
        </div>
      </div>

      {/* Table on sm+, stacked cards on mobile — the row is six columns wide
          and squeezing it onto a phone makes every number unreadable. */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className={`${LABEL_CLS} border-b border-border/60`}>
              <th className="text-left font-semibold py-2">Segment</th>
              <th className="text-right font-semibold py-2">Revenue</th>
              <th className="text-right font-semibold py-2">Margin</th>
              <th className="text-right font-semibold py-2">EBITDA<span className="lowercase"> (est.)</span></th>
              <th className="text-right font-semibold py-2">Multiple</th>
              <th className="text-right font-semibold py-2">EV</th>
              <th className="text-right font-semibold py-2">% of EV</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.segment}-${i}`} className="border-b border-border/30 last:border-0">
                <td className="py-2">
                  <div className="font-medium">{r.segment}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {TYPE_LABEL[r.type ?? ''] ?? r.type}
                    {r.multiple_source?.startsWith('dynamic') && ' · dynamic multiple'}
                  </div>
                </td>
                <td className="text-right tabular-nums py-2">{fmtBn(r.revenue, sym)}</td>
                <td className="text-right tabular-nums py-2">{fmtPct(r.ebitda_margin)}</td>
                <td className="text-right tabular-nums py-2">{fmtBn(r.ebitda, sym)}</td>
                <td className="text-right tabular-nums py-2">{multipleCell(r)}</td>
                <td className="text-right tabular-nums py-2">{fmtBn(r.ev, sym)}</td>
                <td className="text-right tabular-nums py-2">{fmtPct(share(r.ev))}</td>
              </tr>
            ))}
            <tr className="font-medium border-t border-border/60">
              <td className="py-2">Sum of the parts</td>
              <td colSpan={4} />
              <td className="text-right tabular-nums py-2">{fmtBn(partsEv, sym)}</td>
              <td className="text-right tabular-nums py-2">{fmtPct(partsEv > 0 ? 1 : null)}</td>
            </tr>
            {Math.abs(premium - 1) > 1e-6 && (
              <tr className="text-muted-foreground">
                <td className="py-2">Growth premium</td>
                <td colSpan={4} />
                <td className="text-right tabular-nums py-2">×{premium.toFixed(2)}</td>
                <td />
              </tr>
            )}
            <tr className="font-semibold">
              <td className="py-2">Enterprise value</td>
              <td colSpan={4} />
              <td className="text-right tabular-nums py-2">{fmtBn(sotp.total_ev, sym)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      </div>

      <div className="sm:hidden space-y-2">
        {rows.map((r, i) => (
          <div key={`${r.segment}-m-${i}`} className="rounded-lg border border-border/40 p-3">
            <div className="flex items-baseline justify-between gap-2">
              <div className="font-medium text-sm">{r.segment}</div>
              <div className="tabular-nums text-sm">{fmtBn(r.ev, sym)}</div>
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">
              {TYPE_LABEL[r.type ?? ''] ?? r.type} · {fmtPct(share(r.ev))} of the parts
              {r.multiple_source?.startsWith('dynamic') && ' · dynamic multiple'}
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
              <div>
                <div className={LABEL_CLS}>Revenue</div>
                <div className="tabular-nums">{fmtBn(r.revenue, sym)}</div>
              </div>
              <div>
                <div className={LABEL_CLS}>EBITDA est.</div>
                <div className="tabular-nums">{fmtBn(r.ebitda, sym)}</div>
              </div>
              <div>
                <div className={LABEL_CLS}>Multiple</div>
                <div className="tabular-nums">{multipleCell(r)}</div>
              </div>
            </div>
            {r.note && (
              <div className="text-[11px] text-muted-foreground mt-2">{r.note}</div>
            )}
          </div>
        ))}
        <div className="flex items-baseline justify-between pt-1 text-sm">
          <span>Sum of the parts</span>
          <span className="tabular-nums">{fmtBn(partsEv, sym)}</span>
        </div>
        {Math.abs(premium - 1) > 1e-6 && (
          <div className="flex items-baseline justify-between text-sm text-muted-foreground">
            <span>Growth premium</span>
            <span className="tabular-nums">×{premium.toFixed(2)}</span>
          </div>
        )}
        <div className="flex items-baseline justify-between text-sm font-semibold">
          <span>Enterprise value</span>
          <span className="tabular-nums">{fmtBn(sotp.total_ev, sym)}</span>
        </div>
      </div>

      {/* Why each band sits where it does -- one line per business type,
          so a reader can see what the multiple was bounded by and why. */}
      {bandNotes.length > 0 && (
        <div className="space-y-1.5 border-t border-border/40 pt-3">
          <div className={LABEL_CLS}>Band rationale</div>
          {bandNotes.map(([type, text]) => (
            <div key={`band-${type}`} className="text-[11px] text-muted-foreground">
              <span className="font-medium text-foreground">{TYPE_LABEL[type] ?? type}:</span>{' '}
              {text}
            </div>
          ))}
        </div>
      )}

      <div className="space-y-1 text-[11px] text-muted-foreground">
        {(sotp.checks?.reminders ?? [])
          // Archived runs measured shares against the post-premium total; the table above no longer does.
          .filter((r) => !r.startsWith('Segment shares of enterprise value sum to'))
          .map((r, i) => (
            <div key={`chk-${i}`} className="font-medium text-foreground">{r}</div>
          ))}
        {/* Archived runs carry an EV/EBITDA-only note even when every row was priced on revenue. */}
        {(anyEstimated || rows.some((r) => r.basis === 'ev_revenue')) && sotp.basis_note
          && (anyEstimated || !sotp.basis_note.startsWith('Segment EBITDA is estimated')) && (
          <div>{sotp.basis_note}</div>
        )}
        {rows.some((r) => r.ebitda_margin_source) && (
          <div>
            Margin sources:{' '}
            {Array.from(new Set(rows.map((r) => r.ebitda_margin_source).filter(Boolean))).join('; ')}
          </div>
        )}
        {anyUnpriced && (
          <div>
            Segments shown without a multiple carry no owner-set band and are
            valued at nothing in the total.
          </div>
        )}
      </div>
    </Card>
  );
}
