/** Calibration proposal and live-calibration cards (unchanged behaviour, moved from the page). */
import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  getCalibrationDetail, promoteCalibration, rollbackCalibration, dismissCalibration,
  type CalibrationCard, type CalibrationDetail, type DiagnosticCard,
} from '@/lib/api';
import { Chip } from './shared';
import { CATEGORY_LABEL, FAMILY_LABEL, HORIZON_LABEL, pct } from './labels';

function ShadowProgress({ card }: { card: CalibrationCard }) {
  if (!card.shadow) return null;
  const days = Math.min(card.shadow.days_in_shadow, 28);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Shadow period</span>
        <span className="tabular-nums">day {card.shadow.days_in_shadow} of 28</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div className="h-full bg-foreground/70" style={{ width: `${(days / 28) * 100}%` }} />
      </div>
      {Object.entries(card.shadow.horizons).map(([h, s]) => (
        <p key={h} className="text-xs text-muted-foreground tabular-nums">
          {HORIZON_LABEL[h] ?? h}: {s.n_touched} run(s) — live miss {pct(s.live_miss_pct)}, with this change {pct(s.cand_miss_pct)}
        </p>
      ))}
    </div>
  );
}

export function ProposalCard({ card, onChanged }: { card: CalibrationCard; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const blockers = card.status === 'rejected' ? card.backtest.reasons : card.shadow?.reasons ?? [];

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      toast.success(label);
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-base font-semibold text-foreground">{card.title}</p>
          {card.changes.length > 1 && (
            <ul className="text-sm text-foreground/80 list-disc pl-5">
              {card.changes.map((c) => <li key={c}>{c}</li>)}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">
            Scored against the {card.label} · proposed {card.created_at.slice(0, 10)}
          </p>
        </div>
        <div className="flex flex-wrap justify-end items-center gap-2 shrink-0">
          {card.family && card.family !== 'iv' && <Chip>{FAMILY_LABEL[card.family]}</Chip>}
          <Chip strong={card.actions.promote}>{card.stage}</Chip>
        </div>
      </div>

      <p className="text-sm">
        <span className="font-medium">Backtest:</span>{' '}
        {card.backtest.passed ? 'passed on runs it never saw' : 'did not pass'}
      </p>
      <ShadowProgress card={card} />

      {blockers.length > 0 && (
        <div className="rounded-md border border-border bg-muted/40 p-3">
          <p className="text-xs font-semibold text-foreground mb-1">
            {card.status === 'rejected' ? 'Why it was rejected' : 'Before it can be promoted'}
          </p>
          <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-0.5">
            {blockers.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </div>
      )}

      {open && card.backtest.folds.length > 0 && (
        <div className="space-y-1 text-xs tabular-nums">
          {card.backtest.folds.map((f) => (
            <div key={f.cutoff} className="flex flex-wrap justify-between gap-x-3 border-t border-border/60 pt-1">
              <span>trained to {f.cutoff} · tested to {f.test_end} · {f.test} runs</span>
              <span className="text-muted-foreground">
                live {pct(f.live_miss_pct)} → {pct(f.cand_miss_pct)} · {f.improved ? 'better' : 'not better'}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button
          size="sm"
          disabled={!card.actions.promote || busy}
          title={card.actions.promote ? 'Apply this calibration to live valuations' : 'Not eligible yet'}
          onClick={() => {
            if (!window.confirm(`Promote "${card.title}"? New valuations will use it; you can roll it back.`)) return;
            void act('Calibration promoted', () => promoteCalibration(card.id));
          }}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : 'Promote'}
        </Button>
        {card.actions.dismiss && (
          <Button size="sm" variant="outline" disabled={busy}
            onClick={() => void act('Proposal dismissed', () => dismissCalibration(card.id))}>
            Dismiss
          </Button>
        )}
        {card.backtest.folds.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide evidence' : 'Show backtest'}
          </Button>
        )}
      </div>
    </Card>
  );
}

export function ActiveCard({ card, onChanged }: { card: CalibrationCard; onChanged: () => void }) {
  const [detail, setDetail] = useState<CalibrationDetail | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getCalibrationDetail(card.id).then(setDetail).catch(() => setDetail(null));
  }, [card.id]);

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1 min-w-0">
          <p className="text-base font-semibold text-foreground">{card.title}</p>
          {card.changes.length > 1 && (
            <ul className="text-sm text-foreground/80 list-disc pl-5">{card.changes.map((c) => <li key={c}>{c}</li>)}</ul>
          )}
        </div>
        <Chip strong>{card.stage}</Chip>
      </div>
      {detail?.cohort && (
        <div className="text-xs text-muted-foreground space-y-0.5 tabular-nums">
          <p>{detail.cohort.n_frozen} run(s) frozen at promotion — their record can never be refit:</p>
          {Object.keys(detail.cohort.horizons).length === 0 && <p>No labels have matured for them yet.</p>}
          {Object.entries(detail.cohort.horizons).map(([h, s]) => (
            <p key={h}>{HORIZON_LABEL[h] ?? h}: {s.n} run(s) — before {pct(s.live_miss_pct)}, with it {pct(s.cand_miss_pct)}</p>
          ))}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        A daily check rolls this back on its own if runs made under it miss by more than the same runs without it.
      </p>
      <Button
        size="sm" variant="outline" disabled={busy}
        onClick={async () => {
          const reason = window.prompt('Roll back this calibration? Optional reason:', '');
          if (reason === null) return;
          setBusy(true);
          try {
            const out = await rollbackCalibration(card.id, reason);
            toast.success(`Rolled back — restored ${String(out.restored)}`);
            onChanged();
          } catch (e) {
            toast.error((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        Roll back
      </Button>
    </Card>
  );
}

export function DiagnosticItem({ card, openProposals }: { card: DiagnosticCard; openProposals: number }) {
  return (
    <div className="py-3 border-t border-border/60 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <Chip>{CATEGORY_LABEL[card.category]}</Chip>
        <span className="text-xs uppercase tracking-wider text-muted-foreground">
          {card.action === 'code_change' ? 'Needs a code change'
            : openProposals > 0 ? 'Addressed by a calibration proposal'
            : 'A calibration would address this · none open'}
        </span>
      </div>
      <p className="text-sm font-medium text-foreground">{card.title}</p>
      <p className="text-xs text-muted-foreground mt-0.5">{card.detail}</p>
    </div>
  );
}
