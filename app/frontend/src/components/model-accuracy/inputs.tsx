/**
 * Industry inputs: one-line rows, 20 a page, filtered by status; the full
 * figures, checks, overlay and review actions open in a drawer.
 */
import { useMemo, useState } from 'react';
import { ChevronRight, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { reviewIndustryInput, type CitedFigure, type IndustryInputRow } from '@/lib/api';
import { useI18n } from '@/i18n';
import { INPUT_LABEL, INPUT_SHORT, REVIEW_LABEL, SOURCE_LABEL } from './labels';
import { Chip, Pager, Pills, usePaged } from './shared';

/** kind === 'sotp': the segment table with a period label on every figure (owner, 2026-09-24). */
function SotpInputFigures({ row }: { row: IndustryInputRow }) {
  const d = row.detail;
  if (!d?.segments) return null;
  const fig = (c?: CitedFigure | null) => c?.value == null ? '—'
    : `${c.value.toLocaleString()} ${c.currency ?? ''} ${c.scale ?? ''}`.trim();
  const tag = (p?: string | null) => (
    <span className="ml-1 rounded border border-border px-1 text-xs text-muted-foreground">{p ?? 'period n/a'}</span>
  );
  return (
    <div className="text-left text-xs">
      <div className="text-muted-foreground">Forward year {tag(d.fiscal_year)}</div>
      {d.segments.map((s) => (
        <div key={s.name} className="mt-1">
          <div className="font-medium">{s.name}</div>
          <div className="tabular-nums">
            {s.revenue.source_url ? (
              <a href={s.revenue.source_url} target="_blank" rel="noreferrer" title={s.revenue.quote ?? undefined}
                 className="underline decoration-dotted underline-offset-2">{fig(s.revenue)}</a>
            ) : fig(s.revenue)}
            {tag(s.revenue.period_label)}
          </div>
          {s.margin && s.margin.value != null && (
            <div className="tabular-nums">margin {(s.margin.value * 100).toFixed(1)}%{tag(s.margin.period_label)}</div>
          )}
          <div className="text-muted-foreground tabular-nums">
            {s.multiple.metric === 'pe' ? 'P/E' : 'EV/Sales'} {s.multiple.low}–{s.multiple.high}x
            {s.ev_sales_fallback && ` · EV/Sales ${s.ev_sales_fallback.low}–${s.ev_sales_fallback.high}x if unprofitable`}
          </div>
        </div>
      ))}
      {d.net_cash && <div className="mt-1 tabular-nums">net cash {fig(d.net_cash)}{tag(d.net_cash.period_label)}</div>}
      {d.associates && <div className="tabular-nums">associates {fig(d.associates)}{tag(d.associates.period_label)}</div>}
      {d.holdco_discount_pct != null && <div className="text-muted-foreground">holdco discount {(d.holdco_discount_pct * 100).toFixed(0)}%</div>}
    </div>
  );
}

function ReviewButtons({ row, onChanged, overlay = false }:
    { row: IndustryInputRow; onChanged: () => void; overlay?: boolean }) {
  const [busy, setBusy] = useState(false);
  const status = overlay ? (row.overlay?.status ?? 'pending') : row.status;
  const act = (action: 'accept' | 'revoke') => {
    setBusy(true);
    reviewIndustryInput(row.ticker, row.kind, action, overlay)
      .then(() => { toast.success(`${row.ticker} ${INPUT_LABEL[row.kind]} ${action === 'accept' ? 'accepted' : 'revoked'}`); onChanged(); })
      .catch((e: Error) => toast.error(`Could not ${action}: ${e.message}`))
      .finally(() => setBusy(false));
  };
  if ((status as string) === 'omitted') return null;
  return (
    <span className="flex gap-2">
      {status !== 'accepted' && (
        <Button size="sm" disabled={busy} onClick={() => act('accept')}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : overlay ? 'Accept overlay' : 'Accept'}
        </Button>
      )}
      {(status === 'accepted' || status === 'changed_since_acceptance') && (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => act('revoke')}>Revoke</Button>
      )}
    </span>
  );
}

type Cited = { value?: number | null; currency?: string | null; scale?: string | null; unit?: string | null;
  period_label?: string | null; source_url?: string | null; quote?: string | null };

const isCited = (v: unknown): v is Cited =>
  !!v && typeof v === 'object' && !Array.isArray(v) && 'value' in (v as object);

const citedText = (c: Cited) => (c.value == null ? '—'
  : `${c.value.toLocaleString()} ${c.currency ?? ''} ${c.scale && c.scale !== 'units' ? c.scale : ''} ${c.unit ?? ''}`.replace(/\s+/g, ' ').trim());

/** Kinds that carry their figures in `detail` (NAV, PPA, pipeline, alt manager): the cited figures, scalars and lists, in order. */
function detailEntries(d: IndustryInputRow['detail']): { key: string; cited?: Cited; text?: string }[] {
  if (!d) return [];
  const out: { key: string; cited?: Cited; text?: string }[] = [];
  for (const [k, v] of Object.entries(d)) {
    const key = k.replace(/_/g, ' ');
    if (v == null) continue;
    if (isCited(v)) out.push({ key, cited: v });
    else if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') out.push({ key, text: String(v) });
    else if (Array.isArray(v)) {
      const names = v.map((x) => (x && typeof x === 'object' && 'name' in x ? String((x as { name: unknown }).name) : typeof x === 'object' ? '' : String(x))).filter(Boolean);
      out.push({ key, text: names.length ? `${v.length}: ${names.join(' · ')}` : v.map(String).join(' – ') });
    }
  }
  return out;
}

function valueText(r: IndustryInputRow): string {
  if (r.kind === 'sotp') return `${r.detail?.segments?.length ?? 0} segments`;
  if (r.value == null) {
    const first = detailEntries(r.detail).find((e) => e.cited && e.cited.value != null);
    return first?.cited ? citedText(first.cited) : '—';
  }
  return `${r.value.toLocaleString()} ${r.currency ?? ''} ${r.scale ?? ''}`.trim();
}

function DetailFigures({ row }: { row: IndustryInputRow }) {
  const entries = detailEntries(row.detail);
  if (entries.length === 0) return null;
  return (
    <dl className="mt-2 grid grid-cols-[minmax(0,40%)_minmax(0,1fr)] gap-x-3 gap-y-1">
      {entries.map((e) => (
        <div key={e.key} className="contents">
          <dt className="text-muted-foreground break-words">{e.key}</dt>
          <dd className="min-w-0 break-words tabular-nums">
            {e.cited ? (
              <>
                {e.cited.source_url ? (
                  <a href={e.cited.source_url} target="_blank" rel="noreferrer" title={e.cited.quote ?? undefined}
                     className="underline decoration-dotted underline-offset-2">{citedText(e.cited)}</a>
                ) : citedText(e.cited)}
                {e.cited.period_label && <span className="ml-1 rounded border border-border px-1 text-muted-foreground">{e.cited.period_label}</span>}
              </>
            ) : e.text}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function checkTally(r: IndustryInputRow): { ok: number; bad: number } {
  return {
    ok: r.checks.filter((c) => c.ok === true).length,
    bad: r.checks.filter((c) => c.ok === false).length + (r.basis && r.basis !== 'actual' ? 1 : 0),
  };
}

function InputDetail({ row, onChanged }: { row: IndustryInputRow; onChanged: () => void }) {
  const { t } = useI18n();
  return (
    <div className="space-y-4 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Chip strong={row.status === 'accepted'}>{REVIEW_LABEL[row.status] ?? row.status}</Chip>
        {row.source && <span className="text-muted-foreground">{SOURCE_LABEL[row.source] ?? row.source}</span>}
        {row.model && <span className="text-muted-foreground">{row.model}</span>}
        {row.built_at && <span className="text-muted-foreground">{t('built')} {row.built_at.slice(0, 10)}</span>}
      </div>
      <div className="flex flex-wrap gap-2"><ReviewButtons row={row} onChanged={onChanged} /></div>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">{t('As filed')}</h4>
        {row.kind === 'sotp' ? <SotpInputFigures row={row} /> : (
          <div className="tabular-nums">
            {row.source_url ? (
              <a href={row.source_url} target="_blank" rel="noreferrer" className="underline decoration-dotted underline-offset-2 text-sm">
                {valueText(row)}
              </a>
            ) : <span className="text-sm">{valueText(row)}</span>}
            {row.period && <div className="text-muted-foreground">{row.period}</div>}
            {row.quote && <blockquote className="mt-1 border-l-2 border-border pl-2 text-muted-foreground break-words">{row.quote}</blockquote>}
            {row.value == null && <DetailFigures row={row} />}
          </div>
        )}
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">{t('Checks against FMP')}</h4>
        <div className="space-y-0.5 text-muted-foreground break-words">
          {row.checks.length === 0 && !row.omitted_reason && <div>{t('No checks recorded.')}</div>}
          {row.checks.map((c) => (
            <div key={c.check}><span className="text-foreground">{c.ok === false ? '✕' : c.ok ? '✓' : '·'}</span> {c.check}: {c.detail}</div>
          ))}
          {row.omitted_reason && <div>{row.omitted_reason}</div>}
          {row.remark && <div className="mt-1 italic">{row.remark}</div>}
          {row.basis && row.basis !== 'actual' && (
            <div><span className="text-foreground">✕</span> not the latest audited actual — a forward figure belongs in the overlay</div>
          )}
        </div>
      </div>

      {row.overlay && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">{t('Forward overlay')}</h4>
          <div className="space-y-2">
            <div>
              {row.overlay.source_url ? (
                <a href={row.overlay.source_url} target="_blank" rel="noreferrer" title={row.overlay.quote ?? undefined}
                   className="underline decoration-dotted underline-offset-2">
                  {row.overlay.delta_pct != null ? `${(row.overlay.delta_pct * 100).toFixed(1)}%` : '—'}
                </a>
              ) : (row.overlay.delta_pct != null ? `${(row.overlay.delta_pct * 100).toFixed(1)}%` : '—')}
              {row.overlay.note ? ` · ${row.overlay.note}` : ''}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Chip strong={row.overlay.status === 'accepted'}>{REVIEW_LABEL[row.overlay.status] ?? row.overlay.status}</Chip>
              <ReviewButtons row={row} overlay onChanged={onChanged} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

type StatusFilter = 'pending' | 'accepted' | 'omitted' | 'revoked' | 'all';

export function IndustryInputsList({ rows, onChanged }: { rows: IndustryInputRow[]; onChanged: () => void }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<StatusFilter>('pending');
  const [q, setQ] = useState('');
  const [openKey, setOpenKey] = useState<string | null>(null);
  const keyOf = (r: IndustryInputRow) => `${r.ticker}|${r.kind}`;

  const count = (s: string) => rows.filter((r) => (s === 'pending'
    ? r.status === 'pending' || r.status === 'changed_since_acceptance' : r.status === s)).length;
  const filtered = useMemo(() => {
    const f = q.trim().toLowerCase();
    return rows.filter((r) => {
      if (status === 'pending' && !(r.status === 'pending' || r.status === 'changed_since_acceptance')) return false;
      if (status !== 'pending' && status !== 'all' && r.status !== status) return false;
      if (!f) return true;
      return r.ticker.toLowerCase().includes(f) || (r.company ?? '').toLowerCase().includes(f)
        || (INPUT_SHORT[r.kind] ?? r.kind).toLowerCase().includes(f);
    });
  }, [rows, status, q]);
  const paged = usePaged(filtered, `${status}|${q}`);
  const open = rows.find((r) => keyOf(r) === openKey) ?? null;

  return (
    <>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <Pills value={status} onChange={setStatus} options={[
          { value: 'pending', label: 'Pending', count: count('pending') },
          { value: 'accepted', label: 'Accepted', count: count('accepted') },
          { value: 'omitted', label: 'Omitted', count: count('omitted') },
          { value: 'revoked', label: 'Revoked', count: count('revoked') },
          { value: 'all', label: 'All', count: rows.length },
        ]} />
        <input className="w-full sm:w-56 bg-transparent border border-border rounded px-2 py-1 text-xs"
               placeholder={t('Filter by ticker, company or input')} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <Card className="p-0 overflow-hidden">
        {paged.slice.length === 0 && <p className="px-3 py-3 text-xs text-muted-foreground">{t('Nothing in this filter.')}</p>}
        <ul className="divide-y divide-border">
          {paged.slice.map((r) => {
            const tally = checkTally(r);
            return (
              <li key={keyOf(r)}>
                <button type="button" onClick={() => setOpenKey(keyOf(r))}
                  className="flex w-full min-w-0 items-center gap-3 px-3 py-2 text-left text-xs hover:bg-muted/50">
                  <span className="w-20 shrink-0 font-medium tabular-nums text-foreground">{r.ticker}</span>
                  <span className="min-w-0 flex-1 truncate">
                    <span className="text-foreground">{t(INPUT_SHORT[r.kind] ?? r.kind)}</span>
                    {r.company && <span className="text-muted-foreground"> · {r.company}</span>}
                  </span>
                  <span className="hidden md:inline shrink-0 tabular-nums text-muted-foreground">{valueText(r)}</span>
                  <span className="hidden sm:inline shrink-0 tabular-nums text-muted-foreground" title={t('checks passed / failed')}>
                    ✓{tally.ok} ✕{tally.bad}
                  </span>
                  {r.overlay && <span className="hidden sm:inline"><Chip>{t('overlay')}</Chip></span>}
                  <span className="shrink-0"><Chip strong={r.status === 'accepted'}>{t(r.status === 'pending' ? 'pending' : (REVIEW_LABEL[r.status] ?? r.status))}</Chip></span>
                  <ChevronRight size={14} className="shrink-0 text-muted-foreground" />
                </button>
              </li>
            );
          })}
        </ul>
        <Pager {...paged} />
      </Card>

      <Sheet open={open != null} onOpenChange={(o) => { if (!o) setOpenKey(null); }}>
        <SheetContent side="right" className="w-full sm:max-w-xl overflow-y-auto">
          {open && (
            <>
              <SheetHeader className="text-left mb-4 pr-6">
                <SheetTitle>{open.ticker} · {t(INPUT_SHORT[open.kind] ?? open.kind)}</SheetTitle>
                <SheetDescription>{[open.company, INPUT_LABEL[open.kind]].filter(Boolean).join(' · ')}</SheetDescription>
              </SheetHeader>
              <InputDetail row={open} onChanged={onChanged} />
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
