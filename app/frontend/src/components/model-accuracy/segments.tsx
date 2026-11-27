/** Segment memory (SOTP-valued names) and carried estimate overrides. */
import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  reviewSegmentMemory, revokeCarriedEstimateOverride,
  type CarriedEstimateOverride, type SegmentMemory, type SegmentMemoryCell, type SegmentMemoryTicker,
} from '@/lib/api';
import { useI18n } from '@/i18n';
import { BASIS_LABEL, REVIEW_LABEL, fmtAmount, gapText } from './labels';
import { Chip, StackTable, type Col } from './shared';

function SegmentCell({ c }: { c: SegmentMemoryCell }) {
  if (c.revenue == null) return <span className="text-muted-foreground">—</span>;
  return (
    <>
      <a href={c.revenue_url} target="_blank" rel="noreferrer" title={c.revenue_quote}
         className="text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground">
        {fmtAmount(c.revenue)}
      </a>
      <span className="text-muted-foreground" title={c.profit_measure ?? undefined}>
        {' · '}
        {c.margin == null ? 'profit n/d' : (
          c.profit_url
            ? <a href={c.profit_url} target="_blank" rel="noreferrer" className="hover:text-foreground">{(c.margin * 100).toFixed(1)}% margin</a>
            : `${(c.margin * 100).toFixed(1)}% margin`
        )}
      </span>
    </>
  );
}

/** Desktop: segments × fiscal years. Phone: one card per segment, a line per year. */
function SegmentTable({ t: tk }: { t: SegmentMemoryTicker }) {
  const years = tk.years ?? [];
  return (
    <>
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-muted-foreground">
              <th className="text-left font-medium py-2 pr-3">Segment ({tk.currency})</th>
              {years.map((y) => <th key={y} className="text-right font-medium py-2 px-2">FY ending {y}</th>)}
            </tr>
          </thead>
          <tbody>
            {(tk.segments ?? []).map((s) => (
              <tr key={s.name} className="border-t border-border/60 align-top">
                <td className="py-2 pr-3 text-foreground">{s.name}</td>
                {s.years.map((c) => (
                  <td key={c.year} className="py-2 px-2 text-right [&>span]:block"><SegmentCell c={c} /></td>
                ))}
              </tr>
            ))}
            <tr className="border-t border-border text-muted-foreground">
              <td className="py-2 pr-3">Segments vs FMP revenue</td>
              {years.map((y) => {
                const r = tk.reconciliation?.[y];
                return (
                  <td key={y} className="py-2 px-2 text-right" title={r?.fmp_revenue ? `FMP ${fmtAmount(r.fmp_revenue, tk.currency)}` : undefined}>
                    {gapText(r?.segment_gap)}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </div>
      <div className="sm:hidden space-y-2 text-xs tabular-nums">
        {(tk.segments ?? []).map((s) => (
          <div key={s.name} className="rounded-md border border-border p-2 min-w-0">
            <div className="font-medium text-foreground mb-1 break-words">{s.name} <span className="text-muted-foreground font-normal">({tk.currency})</span></div>
            {s.years.map((c) => (
              <div key={c.year} className="flex justify-between gap-2">
                <span className="text-muted-foreground shrink-0">FY {c.year}</span>
                <span className="text-right min-w-0 break-words"><SegmentCell c={c} /></span>
              </div>
            ))}
          </div>
        ))}
        <div className="rounded-md border border-border p-2 text-muted-foreground">
          <div className="mb-1">Segments vs FMP revenue</div>
          {years.map((y) => (
            <div key={y} className="flex justify-between"><span>FY {y}</span><span>{gapText(tk.reconciliation?.[y]?.segment_gap)}</span></div>
          ))}
        </div>
      </div>
    </>
  );
}

function ReviewControls({ t, onChanged }: { t: SegmentMemoryTicker; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const status = t.review?.status ?? 'unknown';
  const act = (action: 'accept' | 'revoke') => {
    setBusy(true);
    reviewSegmentMemory(t.ticker, action)
      .then(() => { toast.success(action === 'accept' ? `${t.ticker} accepted for live valuations` : `${t.ticker} revoked`); onChanged(); })
      .catch((e: Error) => toast.error(`Could not ${action}: ${e.message}`))
      .finally(() => setBusy(false));
  };
  const effect = t.live_effect;
  return (
    <div className="rounded-lg border border-border p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Chip strong={status === 'accepted'}>{REVIEW_LABEL[status] ?? status}</Chip>
        {t.review?.reviewed_at && (
          <span className="text-xs text-muted-foreground">
            {t.review.reviewer ? `${t.review.reviewer} · ` : ''}{t.review.reviewed_at.slice(0, 16).replace('T', ' ')} UTC
          </span>
        )}
        <span className="ml-auto flex flex-wrap gap-2">
          {status !== 'accepted' && (
            <Button size="sm" disabled={busy} onClick={() => act('accept')}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : 'Accept for live valuations'}
            </Button>
          )}
          {(status === 'accepted' || status === 'changed_since_acceptance') && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => act('revoke')}>Revoke</Button>
          )}
        </span>
      </div>
      {effect && effect.method === 'SOTP / NAV (look-through)' ? (
        <p className="text-xs text-muted-foreground">
          {effect.applies
            ? `Live effect: the holdco look-through (SOTP / NAV) uses the accepted division EBITDA${(effect.supplied ?? []).length ? ` for ${effect.supplied!.join(', ')}` : ''}.`
            : `No live effect: ${effect.reason}`}
        </p>
      ) : effect && (effect.applies ? (
        <div className="text-xs text-muted-foreground space-y-1">
          <p>
            Live effect for {(t.listings ?? [t.ticker]).join(' and ')}: SOTP segment revenue = FMP consensus × this mix,
            margin = 3-year average; multiples stay with the current SOTP row.
          </p>
          <ul className="list-none space-y-0.5 tabular-nums">
            {(effect.mapping ?? []).map((m) => (
              <li key={m.memory} className="break-words">
                {m.memory} ({(m.share * 100).toFixed(1)}%{m.margin_avg3 != null ? `, ${(m.margin_avg3 * 100).toFixed(1)}% margin` : ''}) ← multiple of “{m.row}”
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No live effect: {effect.reason}</p>
      ))}
    </div>
  );
}

export function SegmentMemoryBody({ memory, onChanged }: { memory: SegmentMemory; onChanged: () => void }) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string | null>(() => memory.tickers.find((x) => !x.error)?.ticker ?? null);
  const current = memory.tickers.find((x) => x.ticker === selected) ?? null;
  if (memory.tickers.length === 0) return <Card className="p-4 text-sm text-muted-foreground">{t('Not built yet.')}</Card>;
  return (
    <Card className="p-4 space-y-4 min-w-0">
      <div className="flex flex-wrap gap-1.5">
        {memory.tickers.map((x) => (
          <button key={x.ticker} type="button" onClick={() => setSelected(x.ticker)}
            className={`text-xs px-2 py-1 rounded-md border tabular-nums
              ${x.ticker === selected ? 'bg-foreground text-background border-foreground'
                : x.error ? 'border-dashed border-border text-muted-foreground'
                : 'border-border text-foreground hover:bg-muted'}`}>
            {x.ticker}{x.review?.status === 'accepted' ? ' ·✓' : ''}
          </button>
        ))}
      </div>
      {current && (current.error ? (
        <p className="text-sm text-muted-foreground">{current.company}: not retrieved ({current.error}).</p>
      ) : (
        <>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-sm font-semibold text-foreground">{current.company}</span>
            {(current.listings ?? []).length > 1 && (
              <span className="text-xs text-muted-foreground">serves {current.listings!.join(' · ')}</span>
            )}
            <Chip>{BASIS_LABEL[current.sotp_basis] ?? current.sotp_basis}</Chip>
            <span className="text-xs text-muted-foreground tabular-nums">
              {current.source === 'sec_segment_footnote' ? 'SEC filing · '
                : current.source === 'fmp_product_segmentation' ? 'FMP product split · '
                : 'Gemini, cited · '}
              cited {Math.round((current.citation_coverage ?? 0) * 100)}% · profit disclosed {Math.round((current.profit_coverage ?? 0) * 100)}%
              {current.retrieved ? ` · retrieved ${current.retrieved}` : ''}
            </span>
          </div>
          <ReviewControls t={current} onChanged={onChanged} />
          {(current.division_ebitda ?? []).length > 0 && (
            <div className="space-y-1 text-xs tabular-nums">
              <div className="text-muted-foreground font-medium">Division EBITDA (look-through)</div>
              {current.division_ebitda!.map((d) => (
                <div key={d.division} className="flex flex-wrap justify-between gap-x-3 border-t border-border/60 pt-1">
                  <span className="text-foreground">{d.division}</span>
                  <span className="text-right">
                    <a href={d.source_url} target="_blank" rel="noreferrer" title={d.quote}
                       className="text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground">
                      {d.currency} {d.value.toLocaleString()} {d.scale}
                    </a>
                    <span className="text-muted-foreground">
                      {d.fiscal_year ? ` · ${d.fiscal_year}` : ''} · {d.measure}{d.includes_share_of_associates ? ' · incl. share of associates/JVs' : ''}
                    </span>
                  </span>
                </div>
              ))}
              {(current.division_ebitda_missing ?? []).length > 0 && (
                <p className="text-muted-foreground">Not disclosed: {current.division_ebitda_missing!.join(', ')}</p>
              )}
            </div>
          )}
          {(current.segments ?? []).length > 0 && <SegmentTable t={current} />}
          {current.history_error && (current.segments ?? []).length === 0 && (
            <p className="text-xs text-muted-foreground">Segment history not retrieved ({current.history_error}).</p>
          )}
          {(current.notes ?? []).length > 0 && (
            <ul className="text-xs text-foreground/80 space-y-1 list-none">
              {current.notes!.map((n) => <li key={n}>• {n}</li>)}
            </ul>
          )}
          {current.resegmentation && (
            <p className="text-xs text-muted-foreground">Resegmentation: {current.resegmentation}</p>
          )}
        </>
      ))}
    </Card>
  );
}

export function EstimateOverridesBody({ rows, onChanged, onError }: {
  rows: CarriedEstimateOverride[]; onChanged: () => void; onError: (m: string) => void;
}) {
  const fmt = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));
  const cols: Col<CarriedEstimateOverride>[] = [
    { key: 'ticker', label: 'Ticker', primary: true,
      render: (r) => <>{r.ticker}<span className="font-normal text-muted-foreground"> · run {r.run_id.slice(0, 8)}</span></> },
    { key: 'fields', label: 'Fields', render: (r) => r.fields.join(', ') },
    { key: 'why', label: 'Why', render: (r) => <span className="text-muted-foreground">{r.note ?? '—'}</span> },
    { key: 'iv', label: 'Agent IV → user IV', align: 'right', render: (r) => `${fmt(r.before?.intrinsic_value)} → ${fmt(r.after?.intrinsic_value)}` },
    { key: 'saved', label: 'Saved', render: (r) => <span className="text-muted-foreground">{r.created_at.slice(0, 10)}</span> },
    { key: 'act', label: '', align: 'right', render: (r) => (
      <button type="button" className="underline decoration-dotted underline-offset-2"
        onClick={() => revokeCarriedEstimateOverride(r.ticker).then(onChanged).catch((e: Error) => onError(e.message))}>
        revoke
      </button>
    ) },
  ];
  return <Card className="p-0 overflow-hidden"><StackTable cols={cols} rows={rows} rowKey={(r) => String(r.id)} /></Card>;
}
