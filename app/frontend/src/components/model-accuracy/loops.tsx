/**
 * Learning loops: one row per loop (state, n / need, next maturity), each
 * expanding to the table the loop reports today. Every number comes from
 * /learning/overview, /overview or /learning/maturity; nothing is scored here.
 */
import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { Card } from '@/components/ui/card';
import type { LearningOverview, LearningScorecard, ModelAccuracyOverview } from '@/lib/api';
import { useI18n } from '@/i18n';
import {
  EST_FIELD_LABEL, GROUP_LABEL, HORIZON_LABEL, PRICE_MIN_N, errText, fmtDate, loopState, pct, share,
  type HorizonMaturity, type LoopState,
} from './labels';
import { Chip, StackTable, type Col } from './shared';

interface ScoreRow { group: string; field: string; c: LearningScorecard['groups'][string][string] }

export function ScorecardTable({ card }: { card: LearningScorecard }) {
  const { t } = useI18n();
  const rows: ScoreRow[] = Object.entries(card.groups).flatMap(([group, fields]) =>
    Object.entries(fields).map(([field, c]) => ({ group, field, c })));
  const ins = (r: ScoreRow, v: ReactNode) => (r.c.status === 'insufficient' ? <span className="text-muted-foreground">—</span> : v);
  const cols: Col<ScoreRow>[] = [
    { key: 'g', label: GROUP_LABEL[card.group_by] ?? card.group_by, primary: true,
      render: (r) => <>{r.group} <span className="font-normal text-muted-foreground">· {t(EST_FIELD_LABEL[r.field] ?? r.field)}</span></> },
    { key: 'n', label: 'n', align: 'right',
      render: (r) => (r.c.status === 'insufficient' ? `${r.c.n} / ${r.c.need ?? 5}` : r.c.n) },
    { key: 'bias', label: 'Bias (median)', align: 'right', render: (r) => ins(r, errText(r.field, r.c.bias)) },
    { key: 'mae', label: 'MAE', align: 'right', render: (r) => ins(r, errText(r.field, r.c.mae)) },
    { key: 'beat', label: 'Beat consensus', align: 'right', render: (r) => ins(r, share(r.c.beat_consensus_share)) },
    { key: 'band', label: 'In bear–bull band', align: 'right', render: (r) => ins(r, share(r.c.in_band_share)) },
  ];
  return <StackTable cols={cols} rows={rows} rowKey={(r) => `${r.group}|${r.field}`} empty={t('No prints scored yet.')} />;
}

function EstimatesBody({ data }: { data: LearningOverview }) {
  const { t } = useI18n();
  const [group, setGroup] = useState<'archetype' | 'confidence' | 'market'>('market');
  const fy = data.estimates?.fy?.[group];
  return (
    <div className="space-y-2">
      <div className="flex gap-1 overflow-x-auto scrollbar-hide">
        {(['market', 'archetype', 'confidence'] as const).map((g) => (
          <button key={g} type="button" onClick={() => setGroup(g)}
            className={`shrink-0 text-xs px-2 py-0.5 rounded-full border ${group === g ? 'bg-foreground text-background border-foreground' : 'border-border text-muted-foreground'}`}>
            {t('by {g}', { g: t(GROUP_LABEL[g]) })}
          </button>
        ))}
      </div>
      <Card className="p-0 overflow-hidden">
        {data.estimates?.status === 'unavailable' ? <p className="px-3 py-3 text-xs">Unavailable: {data.estimates.error}</p>
          : fy ? <ScorecardTable card={fy} /> : <p className="px-3 py-3 text-xs text-muted-foreground">{t('No prints scored yet.')}</p>}
      </Card>
    </div>
  );
}

interface Loop {
  id: string;
  title: string;
  what: string;
  n: number;
  need: number | null;
  anyOk?: boolean;
  /** Short unit for n, e.g. "prints", "labels". */
  unit: string;
  next: string;
  body: ReactNode;
}

const STATE_LABEL: Record<LoopState, string> = { dormant: 'dormant', collecting: 'collecting', live: 'live' };

export function buildLoops(data: LearningOverview, overview: ModelAccuracyOverview | null,
                           maturity: HorizonMaturity[] | null,
                           t: (s: string, v?: Record<string, string | number>) => string): Loop[] {
  const mat = (k: string) => maturity?.find((m) => m.key === k) ?? null;
  const nextOf = (k: string) => {
    const m = mat(k);
    if (!maturity) return t('maturity dates unavailable');
    return m?.next ? t('next runs due {d}', { d: fmtDate(m.next) }) : t('every ledger run is due');
  };
  const counts = data.estimates?.counts ?? {};
  const minN = data.estimates?.min_n ?? 5;
  const anyOkCard = (c?: LearningScorecard) => !!c && Object.values(c.groups).some((f) => Object.values(f).some((x) => x.status === 'ok'));
  const fy = data.estimates?.fy?.market;
  const qt = data.estimates?.q_track?.archetype;
  const gates = Object.entries(data.gates?.gates ?? {});
  const judged = gates.reduce((a, [, g]) => a + (g.n_judged ?? 0), 0);
  const scen = Object.entries(data.scenarios ?? {}).filter(([k]) => k.startsWith('px_'))
    .map(([h, r]) => [h, r] as [string, { n?: number; need?: number; status?: string; mean_predicted?: Record<string, number>; realised_share?: Record<string, number> }]);
  const scenN = Math.max(0, ...scen.map(([, r]) => r.n ?? 0));
  const scenNeed = Math.max(0, ...scen.map(([, r]) => r.need ?? 0)) || PRICE_MIN_N;
  const ov = data.overrides ?? {};
  const ovHorizons = Object.entries(ov.horizons ?? {});
  const pm = data.prior_misses ?? {};
  const labels = overview?.labels ?? {};
  const priceLabels = Object.entries(labels).filter(([k]) => k.startsWith('px_')).reduce((a, [, n]) => a + n, 0);
  const guidanceMarkets = Object.entries(data.guidance?.markets ?? {});

  return [
    {
      id: 'price', title: 'Price outcome labels', unit: 'labels',
      what: 'Each run’s target scored against the price at 30/90/180/365 days; the weekly calibration fit and the diagnostics read these.',
      n: priceLabels, need: PRICE_MIN_N, next: nextOf('px_90d'),
      body: (
        <Card className="p-0 overflow-hidden">
          <StackTable
            cols={[
              { key: 'h', label: 'Horizon', primary: true, render: (r: [string, number]) => HORIZON_LABEL[r[0]] ?? r[0] },
              { key: 'n', label: 'Labels', align: 'right', render: (r) => r[1] },
              { key: 'due', label: 'Ledger runs due', align: 'right', render: (r) => mat(r[0])?.due ?? '—' },
              { key: 'next', label: 'Next due', align: 'right', render: (r) => fmtDate(mat(r[0])?.next) },
            ] as Col<[string, number]>[]}
            rows={['consensus_0d', 'px_30d', 'px_90d', 'px_180d', 'px_365d'].map((k) => [k, labels[k] ?? 0] as [string, number])}
            rowKey={(r) => r[0]} />
        </Card>
      ),
    },
    {
      id: 'est', title: 'Estimates vs the print (FY+1)', unit: 'prints',
      what: 'The agent’s FY+1 revenue growth, EBITDA margin and EPS against the reported year.',
      n: counts['fy:eps'] ?? 0, need: minN, anyOk: anyOkCard(fy), next: 'as companies report',
      body: <EstimatesBody data={data} />,
    },
    {
      id: 'qt', title: 'Quarterly tracking', unit: 'quarters',
      what: 'Latest quarter year on year against the FY+1 growth estimate; an early read, not a verdict.',
      n: counts['q_track:revenue_growth'] ?? 0, need: minN, anyOk: anyOkCard(qt), next: 'as companies report',
      body: qt ? <Card className="p-0 overflow-hidden"><ScorecardTable card={qt} /></Card> : null,
    },
    {
      id: 'guid', title: 'Management guidance credibility', unit: 'guided figures',
      what: 'Guided figures against what printed, per market.',
      n: data.guidance?.n ?? 0, need: null, anyOk: guidanceMarkets.some(([, c]) => c.status === 'ok'), next: 'as companies report',
      body: (
        <Card className="p-0 overflow-hidden">
          <StackTable
            cols={[
              { key: 'm', label: 'Market', primary: true, render: (r: [string, { n: number; beat_rate?: number; hit_rate?: number; status: string }]) => r[0] },
              { key: 'n', label: 'n', align: 'right', render: (r) => r[1].n },
              { key: 'beat', label: 'Met or beaten', align: 'right', render: (r) => (r[1].status === 'insufficient' ? '—' : share(r[1].beat_rate)) },
              { key: 'hit', label: 'Within band', align: 'right', render: (r) => (r[1].status === 'insufficient' ? '—' : share(r[1].hit_rate)) },
            ] as Col<[string, { n: number; beat_rate?: number; hit_rate?: number; status: string }]>[]}
            rows={guidanceMarkets} rowKey={(r) => r[0]} empty="No guided figure has printed yet." />
        </Card>
      ),
    },
    {
      id: 'scen', title: 'Scenario probabilities', unit: 'labels',
      what: 'Predicted bear/base/bull probabilities against the realised share at 180 and 365 days.',
      n: scenN, need: scenNeed, next: nextOf('px_180d'),
      body: (
        <Card className="p-0 overflow-hidden">
          <StackTable
            cols={[
              { key: 'h', label: 'Horizon', primary: true, render: (r: (typeof scen)[number]) => HORIZON_LABEL[r[0]] ?? r[0] },
              { key: 'n', label: 'n', align: 'right', render: (r) => `${r[1].n ?? 0} / ${r[1].need ?? PRICE_MIN_N}` },
              ...(['bear', 'base', 'bull'] as const).map((k) => ({
                key: k, label: k === 'bear' ? 'Bear' : k === 'base' ? 'Base' : 'Bull', align: 'right' as const,
                render: (r: (typeof scen)[number]) => (r[1].status !== 'ok' ? '—' : `${share(r[1].mean_predicted?.[k])} → ${share(r[1].realised_share?.[k])}`),
              })),
            ]}
            rows={scen} rowKey={(r) => r[0]} />
        </Card>
      ),
    },
    {
      id: 'gates', title: 'Gate firings', unit: 'judged',
      what: 'Each valuation gate that fired, judged against the printed year: helped, false alarm or neutral.',
      n: judged, need: data.gates?.min_n ?? 20, next: 'as companies report',
      body: (
        <Card className="p-0 overflow-hidden">
          <StackTable
            cols={[
              { key: 'g', label: 'Gate', primary: true, render: (r: (typeof gates)[number]) => <span className="font-mono text-xs break-all">{r[0]}</span> },
              { key: 'h', label: 'Helped', align: 'right', render: (r) => r[1].HELPED },
              { key: 'f', label: 'False alarm', align: 'right', render: (r) => r[1].FALSE_ALARM },
              { key: 'n', label: 'Neutral', align: 'right', render: (r) => r[1].NEUTRAL },
              { key: 'u', label: 'Unscorable', align: 'right', render: (r) => r[1].UNSCORABLE },
              { key: 's', label: 'False-alarm share', align: 'right', render: (r) => (r[1].status === 'insufficient' ? '—' : share(r[1].false_alarm_share)) },
            ]}
            rows={gates} rowKey={(r) => r[0]} empty="No gate firing has a printed year yet." />
        </Card>
      ),
    },
    {
      id: 'ovr', title: 'User overrides', unit: 'scored',
      what: 'Where a user overrode the agent: whose number was closer once the label matured.',
      n: ov.n_scored_rows ?? 0, need: null, anyOk: ovHorizons.some(([, r]) => r.status === 'ok'),
      next: (ov.n_overrides ?? 0) === 0 ? 'no active override' : nextOf('px_30d'),
      body: (
        <Card className="p-0 overflow-hidden">
          <StackTable
            cols={[
              { key: 'h', label: 'Horizon', primary: true, render: (r: (typeof ovHorizons)[number]) => HORIZON_LABEL[r[0]] ?? r[0] },
              { key: 'n', label: 'n', align: 'right', render: (r) => r[1].n },
              { key: 'a', label: 'Agent miss', align: 'right', render: (r) => (r[1].status !== 'ok' ? '—' : pct(r[1].agent_median_miss_pct)) },
              { key: 'u', label: 'User miss', align: 'right', render: (r) => (r[1].status !== 'ok' ? '—' : pct(r[1].user_median_miss_pct)) },
              { key: 'c', label: 'User closer', align: 'right', render: (r) => (r[1].status !== 'ok' ? '—' : share(r[1].user_closer_share)) },
            ]}
            rows={ovHorizons} rowKey={(r) => r[0]} />
          <p className="px-3 py-2 text-xs text-muted-foreground border-t border-border">
            {ov.n_overrides ?? 0} active override(s)
            {Object.keys(ov.fields_changed ?? {}).length > 0 && ` · fields changed: ${Object.entries(ov.fields_changed ?? {}).map(([f, n]) => `${f} ×${n}`).join(', ')}`}
          </p>
        </Card>
      ),
    },
    {
      id: 'prof', title: 'Profiles that keep missing', unit: 'cells',
      what: 'Market × profile cells whose matured price labels keep missing the same way; a gap becomes a profile lesson.',
      n: pm.cells ?? 0, need: null, anyOk: (pm.profile_lessons_active ?? 0) > 0, next: nextOf('px_90d'),
      body: (
        <Card className="px-4 py-3 text-xs">
          {(pm.gaps ?? []).length === 0 ? (
            <span className="text-muted-foreground">No profile cell has a gap on matured price labels ({pm.cells ?? 0} cell(s) in the ledger; {pm.profile_lessons_active ?? 0} profile lesson(s) active).</span>
          ) : (
            <ul className="space-y-1">
              {(pm.gaps ?? []).map((g) => <li key={`${g.market}|${g.profile}`}><span className="font-medium">{g.market} · {g.profile}</span> — {g.reason}</li>)}
            </ul>
          )}
        </Card>
      ),
    },
  ];
}

export function loopStateOf(l: Loop): LoopState {
  return loopState(l.n, l.need, l.anyOk);
}

export function LoopRow({ loop }: { loop: Loop }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const state = loopStateOf(loop);
  return (
    <li className="border-t border-border first:border-t-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="grid w-full grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-0.5 px-3 py-2.5 text-left hover:bg-muted/40
                   sm:grid-cols-[1rem_minmax(0,1fr)_6.5rem_7rem_minmax(0,12rem)]">
        <ChevronDown size={14} className={`text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} />
        <span className="min-w-0 truncate text-sm font-medium text-foreground">{t(loop.title)}</span>
        <span className="justify-self-end sm:justify-self-start"><Chip strong={state === 'live'}>{t(STATE_LABEL[state])}</Chip></span>
        <span className="col-start-2 text-xs tabular-nums text-muted-foreground sm:col-start-auto sm:text-right">
          {loop.need != null ? `${loop.n} / ${loop.need}` : loop.n} {t(loop.unit)}
        </span>
        <span className="col-start-2 col-span-2 truncate text-xs text-muted-foreground sm:col-start-auto sm:col-span-1 sm:text-right">{t(loop.next)}</span>
      </button>
      {open && (
        <div className="px-3 pb-4 space-y-2">
          <p className="text-xs text-muted-foreground">{t(loop.what)}</p>
          {loop.body}
        </div>
      )}
    </li>
  );
}
