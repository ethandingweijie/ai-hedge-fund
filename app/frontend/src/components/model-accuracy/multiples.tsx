/** Dynamic multiples: the three-way confirmation summary and a paged table. */
import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { pinDynamicMultiple, unpinDynamicMultiple, type DynamicMultipleRow, type DynamicMultiplesLog } from '@/lib/api';
import { useI18n } from '@/i18n';
import { FIELD_LABEL } from './labels';
import { Pager, StackTable, usePaged, type Col } from './shared';

export const multipleKey = (r: DynamicMultipleRow) => `${r.exchange}|${r.level}|${r.key}|${r.field}`;

/** The market's multiple sits outside the ±20% band, so the live multiple is held at the band's edge. */
export function outsideBand(r: DynamicMultipleRow): boolean {
  return r.market_now != null && r.band_lo != null && r.band_hi != null
    && (r.market_now < r.band_lo || r.market_now > r.band_hi);
}

/** The quarterly multiple update, confirmed three ways (owner 2026-09-21): it RAN, it reached the
 *  engine for EVERY industry, and the multiples REACHED VALUATIONS. */
export function MultiplesSummary({ log }: { log: DynamicMultiplesLog }) {
  const last = log.runs?.[0];
  const totals = last?.summary?.totals ?? {};
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Card className="p-4 min-w-0">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">1 · Update ran</div>
        {last ? (
          <>
            <div className="text-sm font-medium mt-1">{last.run_at.replace('T', ' ').slice(0, 16)} UTC</div>
            <div className="text-xs text-muted-foreground mt-1">
              {totals.initial ?? 0} new · {totals.updated ?? 0} updated · {totals.held_inertia ?? 0} held
              · {totals.pinned ?? 0} pinned · {totals.insufficient_history ?? 0} too little history
            </div>
            <div className="text-xs text-muted-foreground break-words">trigger: {last.trigger}</div>
          </>
        ) : <div className="text-sm mt-1">No update has run yet.</div>}
      </Card>
      <Card className="p-4 min-w-0">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">2 · In the engine, every industry</div>
        {Object.entries(log.coverage).map(([ex, c]) => (
          <div key={ex} className="text-xs mt-1 tabular-nums">
            <span className="font-medium">{ex}</span>: {c.multiples_live} of {c.multiple_slots} multiples live
            <span className="text-muted-foreground"> ({c.baskets_with_history} baskets)</span>
          </div>
        ))}
      </Card>
      <Card className="p-4 min-w-0">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">3 · Reached valuations</div>
        <div className="text-sm font-medium mt-1 tabular-nums">{log.reached_valuations.tickers} valuations</div>
        <div className="text-xs text-muted-foreground mt-1 tabular-nums">
          across {log.reached_valuations.baskets_used} industries;
          {' '}{log.reached_valuations.baskets_used_since_last_update} since the last update
        </div>
        <div className="text-xs text-muted-foreground">
          auto-update {log.switches.auto_update_enabled ? 'on' : 'OFF'} · valuations read it
          {' '}{log.switches.valuations_read_enabled ? 'on' : 'OFF'}
        </div>
      </Card>
    </div>
  );
}

export function MultiplesTable({ rows, onChanged }: { rows: DynamicMultipleRow[]; onChanged: () => void }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? rows.filter((r) => r.key.toLowerCase().includes(f) || r.exchange.toLowerCase() === f) : rows;
  }, [rows, filter]);
  const paged = usePaged(filtered, filter);

  const onPin = async (r: DynamicMultipleRow) => {
    const input = window.prompt(`Pin ${r.key} (${r.exchange}, ${FIELD_LABEL[r.field] ?? r.field}) at what multiple?`,
      r.multiple.toFixed(2));
    const v = input ? parseFloat(input) : NaN;
    if (!isFinite(v) || v <= 0) return;
    setBusy(multipleKey(r));
    try { await pinDynamicMultiple(r, v); onChanged(); } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  };
  const onUnpin = async (r: DynamicMultipleRow) => {
    setBusy(multipleKey(r));
    try { await unpinDynamicMultiple(r); onChanged(); } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  };

  const x = (v: number | null | undefined, d = 2) => (v != null ? `${v.toFixed(d)}x` : '—');
  const cols: Col<DynamicMultipleRow>[] = [
    { key: 'ind', label: 'Industry', primary: true,
      render: (r) => <>{r.key}<span className="font-normal text-muted-foreground"> · {r.exchange} · {r.level}</span></> },
    { key: 'field', label: 'Multiple', render: (r) => FIELD_LABEL[r.field] ?? r.field },
    { key: 'live', label: 'Live', align: 'right', render: (r) => <span className="font-medium">{x(r.multiple)}</span> },
    { key: 'base', label: 'Baseline', align: 'right', render: (r) => x(r.baseline) },
    { key: 'band', label: 'Band', align: 'right',
      render: (r) => (r.band_lo != null && r.band_hi != null ? `${r.band_lo.toFixed(1)}–${r.band_hi.toFixed(1)}x` : '—') },
    { key: 'now', label: 'Market now', align: 'right',
      render: (r) => <>{x(r.market_now)}{outsideBand(r) && <span className="text-muted-foreground"> · {t('outside band')}</span>}</> },
    { key: 'src', label: 'Source', render: (r) => (r.pinned ? `pinned (${r.source})` : r.source) },
    { key: 'act', label: '', align: 'right',
      render: (r) => r.pinned
        ? <Button size="sm" variant="outline" disabled={busy === multipleKey(r)} onClick={() => void onUnpin(r)}>Unpin</Button>
        : <Button size="sm" variant="outline" disabled={busy === multipleKey(r)} onClick={() => void onPin(r)}>Pin</Button> },
  ];

  return (
    <Card className="p-0 overflow-hidden">
      <div className="p-3 border-b border-border">
        <input className="w-full sm:w-72 bg-transparent border border-border rounded px-2 py-1 text-xs"
               placeholder={t('Filter by industry or market (US, HKSE, SES)')}
               value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <StackTable cols={cols} rows={paged.slice} rowKey={multipleKey} empty={t('No multiple matches.')} />
      <Pager {...paged} />
    </Card>
  );
}
