/**
 * ModelAccuracyPage.tsx
 * =====================
 * Owner-only (MODEL_ACCURACY_EMAILS). Where the valuation learning loop reports back and asks for a
 * decision (B6). Four tabs, chosen by ?tab=:
 *
 *   • Scorecard      — headline tiles (each with n and an explicit insufficient state),
 *                      the label-maturity timeline, diagnostics.
 *   • Needs review   — calibration proposals, industry inputs and multiples (paged, a
 *                      drawer per input), segment memory, carried estimate overrides.
 *   • Learning loops — one row per loop: state, n / need, next maturity; expands to its table.
 *   • Settings       — calibrations in force and kill switches, dynamic multiples, history.
 *
 * Sections are collapsed by default. Monochrome throughout: green/red are reserved for price change.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Gauge, Loader2, RefreshCw } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { PageContainer } from '@/components/layout/PageContainer';
import { TabHero } from '@/components/layout/TabHero';
import { useAuth } from '@/contexts/auth-context';
import { useI18n } from '@/i18n';
import {
  getModelAccuracyOverview, getSegmentMemory, getIndustryInputs, getCarriedEstimateOverrides,
  getDynamicMultiples, getLearningOverview, getLearningMaturity,
  type CarriedEstimateOverride, type DynamicMultiplesLog, type IndustryInputRow, type LearningMaturity,
  type LearningOverview, type ModelAccuracyOverview, type SegmentMemory,
} from '@/lib/api';
import { ActiveCard, ProposalCard } from '@/components/model-accuracy/calibration';
import { IndustryInputsList } from '@/components/model-accuracy/inputs';
import { FAMILY_LABEL, PRICE_MIN_N, horizonMaturity } from '@/components/model-accuracy/labels';
import { LoopRow, buildLoops, loopStateOf } from '@/components/model-accuracy/loops';
import { MultiplesSummary, MultiplesTable, outsideBand } from '@/components/model-accuracy/multiples';
import { ScorecardTab } from '@/components/model-accuracy/scorecard';
import { EstimateOverridesBody, SegmentMemoryBody } from '@/components/model-accuracy/segments';
import { Chip, ErrorCard, Section, StackTable, type Col } from '@/components/model-accuracy/shared';

type TabId = 'scorecard' | 'review' | 'loops' | 'settings';
const TABS: TabId[] = ['scorecard', 'review', 'loops', 'settings'];

interface Resource<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** One endpoint: loaded once the owner gate passes, reloadable on its own. */
function useResource<T>(fetcher: () => Promise<T>, allowed: boolean): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const reload = useCallback(() => {
    setLoading(true);
    fetcher().then((d) => { setData(d); setError(null); }).catch((e: Error) => setError(e.message)).finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { if (allowed) reload(); }, [allowed, reload]);
  return { data, error, loading, reload };
}

const pendingInput = (r: IndustryInputRow) => r.status === 'pending' || r.status === 'changed_since_acceptance';

// ── Needs review ────────────────────────────────────────────────────────────

function ReviewTab({ overview, inputs, segment, overrides, multiples }: {
  overview: Resource<ModelAccuracyOverview>;
  inputs: Resource<{ rows: IndustryInputRow[]; kinds: string[] }>;
  segment: Resource<SegmentMemory>;
  overrides: Resource<{ rows: CarriedEstimateOverride[]; carry_enabled: boolean }>;
  multiples: Resource<DynamicMultiplesLog>;
}) {
  const { t } = useI18n();
  const [ovError, setOvError] = useState<string | null>(null);
  const proposals = overview.data?.proposals ?? [];
  const rows = inputs.data?.rows ?? [];
  const pending = rows.filter(pendingInput).length;
  const overlaysPending = rows.filter((r) => r.overlay && r.overlay.status === 'pending').length;
  const mem = segment.data;
  const memUsable = (mem?.tickers ?? []).filter((x) => !x.error);
  const memPending = memUsable.filter((x) => x.review?.status !== 'accepted').length;
  const outside = useMemo(() => (multiples.data?.multiples ?? []).filter(outsideBand), [multiples.data]);
  const ovRows = overrides.data?.rows ?? [];

  return (
    <div className="border-b border-border">
      <Section title="Recommendations" count={proposals.length > 0 ? proposals.length : undefined}
        summary={proposals.length === 0 ? t('No proposals open.') : t('{n} ready for your decision', { n: overview.data?.eligible_count ?? 0 })}
        hint="Changes the system can apply. Each must pass a backtest and 28 days in shadow before Promote unlocks.">
        {overview.error && <ErrorCard what="the overview" error={overview.error} />}
        {proposals.length === 0 ? (
          <Card className="p-4 text-sm text-muted-foreground">
            No proposals yet. One is fitted every Sunday once enough valuations have outcomes to learn from.
          </Card>
        ) : (
          <div className="space-y-3">{proposals.map((c) => <ProposalCard key={c.id} card={c} onChanged={overview.reload} />)}</div>
        )}
      </Section>

      <Section title="Industry inputs" count={inputs.data ? t('{n} pending', { n: pending }) : undefined}
        summary={inputs.data ? t('{a} of {n} accepted', { a: rows.filter((r) => r.status === 'accepted').length, n: rows.length })
          + (overlaysPending ? ` · ${t('{n} overlay(s) pending', { n: overlaysPending })}` : '') : undefined}
        hint="Figures FMP does not report, taken as each filing prints them. The baseline is the audited actual; management guidance is a separate delta that applies only while the forward overlay is switched on. Checked against FMP; used in valuations only once accepted. Open a row for its figures, checks and source.">
        {inputs.error && <ErrorCard what="industry inputs" error={inputs.error} />}
        {inputs.data && rows.length === 0 && <Card className="p-4 text-sm text-muted-foreground">{t('Not built yet.')}</Card>}
        {rows.length > 0 && <IndustryInputsList rows={rows} onChanged={inputs.reload} />}
      </Section>

      <Section title="Multiples outside their band" count={multiples.data ? outside.length : undefined}
        summary={multiples.data ? t('{n} of {total} live multiples', { n: outside.length, total: multiples.data.multiples.length }) : undefined}
        hint="Industries whose market multiple today sits outside the ±20% band around their through-cycle average, so the live multiple is held at the band. Pin one to override it; the full table is under Settings.">
        {multiples.error && <ErrorCard what="the multiples log" error={multiples.error} />}
        {multiples.data && <MultiplesTable rows={outside} onChanged={multiples.reload} />}
      </Section>

      <Section title="Segment memory" count={mem ? t('{n} pending', { n: memPending }) : undefined}
        summary={mem ? t('{a} of {n} accepted', { a: memUsable.length - memPending, n: memUsable.length }) : undefined}
        hint={mem
          ? `Reported segment revenue and profit for SOTP-valued names, each figure cited${mem.model ? ` · ${mem.model}` : ''}${mem.updated ? ` · updated ${mem.updated}` : ''}. Click a figure for its source.`
          : 'Reported segment revenue and profit for SOTP-valued names.'}>
        {segment.error && <ErrorCard what="segment memory" error={segment.error} />}
        {mem && <SegmentMemoryBody memory={mem} onChanged={segment.reload} />}
      </Section>

      {(ovRows.length > 0 || overrides.error) && (
        <Section title="Estimate overrides carried forward" count={ovRows.length}
          summary={overrides.data && !overrides.data.carry_enabled ? t('carry-forward OFF (ESTIMATE_OVERRIDE_CARRY)') : undefined}
          hint="Estimates a user saved on a report in place of the agent's. The latest per ticker is carried into that name's next run (its numbers replace the research's in the guidance block; its engine inputs, WACC and terminal growth replace the engine's) until revoked here. Revoking does not change runs that already carry it.">
          {(overrides.error || ovError) && <ErrorCard what="estimate overrides" error={(overrides.error || ovError)!} />}
          {ovRows.length > 0 && <EstimateOverridesBody rows={ovRows} onChanged={overrides.reload} onError={setOvError} />}
        </Section>
      )}
    </div>
  );
}

// ── Settings ────────────────────────────────────────────────────────────────

function SettingsTab({ overview, learning, multiples }: {
  overview: Resource<ModelAccuracyOverview>; learning: Resource<LearningOverview>; multiples: Resource<DynamicMultiplesLog>;
}) {
  const { t } = useI18n();
  const [allHistory, setAllHistory] = useState(false);
  const data = overview.data;
  const actives = [
    ...(data?.active ? [['iv', data.active] as const] : []),
    ...Object.entries(data?.actives ?? {}).filter(([fam]) => fam !== 'iv'),
  ];
  const switches = Object.entries(learning.data?.kill_switches ?? {});
  const off = switches.filter(([, v]) => v.state === 'off').length;
  const history = data?.history ?? [];
  const shown = allHistory ? history : history.filter((c) => c.status !== 'dismissed').slice(0, 5);
  const log = multiples.data;
  type Sw = [string, { what: string; state: 'on' | 'off'; value: string | null }];
  const swCols: Col<Sw>[] = [
    { key: 'k', label: 'Switch', primary: true, render: (r) => <span className="font-mono text-xs break-all">{r[0]}</span> },
    { key: 'w', label: 'What it controls', render: (r) => <span className="text-muted-foreground">{r[1].what}</span> },
    { key: 's', label: 'State', align: 'right', render: (r) => <Chip strong={r[1].state === 'on'}>{t(r[1].state)}</Chip> },
  ];

  return (
    <div className="border-b border-border">
      <Section title="Calibrations in force" count={actives.length}
        summary={actives.length === 0 ? t('none promoted') : actives.map(([f]) => t(FAMILY_LABEL[f] ?? f)).join(' · ')}
        hint="In force for every new valuation, 12-month target or forecast, by family.">
        {actives.length === 0 && <Card className="p-4 text-sm text-muted-foreground">{t('No calibration is in force; valuations run on the owner-set constants.')}</Card>}
        {actives.map(([fam, card]) => (
          <div key={fam} className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t('Live {f} calibration', { f: t(FAMILY_LABEL[fam] ?? fam) })}</p>
            <ActiveCard card={card} onChanged={overview.reload} />
          </div>
        ))}
      </Section>

      <Section title="Kill switches" count={switches.length > 0 ? t('{n} off', { n: off }) : undefined}
        summary={switches.length > 0 ? t('{n} switches', { n: switches.length }) : undefined}>
        {learning.error && <ErrorCard what="the learning scorecards" error={learning.error} />}
        <Card className="p-0 overflow-hidden"><StackTable cols={swCols} rows={switches} rowKey={(r) => r[0]} /></Card>
      </Section>

      <Section title="Dynamic multiples" count={log ? t('{n} live', { n: log.multiples.length }) : undefined}
        summary={log?.runs?.[0] ? t('last update {d}', { d: log.runs[0].run_at.slice(0, 10) }) : undefined}
        hint="Every industry's through-cycle multiple, updated automatically each quarter after the comps history backfill (the 2nd of Jan/Apr/Jul/Oct). Bounded to ±20% of the industry's own through-cycle average; moves under 5% are held as noise; a pinned industry is left alone. Valuations use it in the normalised legs, EV/EBITDA (norm) and P/E (norm).">
        {multiples.error && <ErrorCard what="the multiples log" error={multiples.error} />}
        {log && <MultiplesSummary log={log} />}
        {log && <MultiplesTable rows={log.multiples} onChanged={multiples.reload} />}
      </Section>

      {history.length > 0 && (
        <Section title="History" count={history.length}
          summary={t('{n} dismissed', { n: history.filter((c) => c.status === 'dismissed').length })}>
          <Card className="px-4 py-1">
            {shown.length === 0 && <p className="py-2 text-xs text-muted-foreground">{t('Every recent entry was dismissed.')}</p>}
            {shown.map((c) => (
              <div key={c.id} className="py-2 border-t border-border/60 first:border-t-0 flex items-center justify-between gap-3">
                <span className="min-w-0 text-sm text-foreground truncate">
                  {c.title}<span className="text-xs text-muted-foreground"> · {c.created_at.slice(0, 10)}</span>
                </span>
                <Chip>{c.stage}</Chip>
              </div>
            ))}
          </Card>
          <button type="button" onClick={() => setAllHistory((v) => !v)}
            className="text-xs underline decoration-dotted underline-offset-2 text-muted-foreground hover:text-foreground">
            {allHistory ? t('Show the last 5 non-dismissed') : t('Show all {n}, including dismissed', { n: history.length })}
          </button>
        </Section>
      )}
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────────

export function ModelAccuracyPage() {
  const { user } = useAuth();
  const { t } = useI18n();
  const allowed = user?.can_view_model_accuracy === true;
  const [params, setParams] = useSearchParams();
  const tabParam = params.get('tab') as TabId | null;
  const tab: TabId = tabParam && TABS.includes(tabParam) ? tabParam : 'scorecard';

  const overview = useResource(getModelAccuracyOverview, allowed);
  const learning = useResource(getLearningOverview, allowed);
  const maturityRes = useResource<LearningMaturity>(getLearningMaturity, allowed);
  const inputs = useResource(getIndustryInputs, allowed);
  const segment = useResource(getSegmentMemory, allowed);
  const overrides = useResource(getCarriedEstimateOverrides, allowed);
  const multiples = useResource(getDynamicMultiples, allowed);
  const all = [overview, learning, maturityRes, inputs, segment, overrides, multiples];
  const loading = all.some((r) => r.loading);
  const reloadAll = () => all.forEach((r) => r.reload());

  // A missing maturity endpoint (older backend) degrades to "dates unavailable", never an error card.
  const maturity = useMemo(() => (maturityRes.data ? horizonMaturity(maturityRes.data, PRICE_MIN_N) : null), [maturityRes.data]);
  // On a phone the strip scrolls sideways: keep the selected tab in view.
  useEffect(() => {
    document.getElementById(`ma-tab-${tab}`)?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  }, [tab]);
  const loops = useMemo(() => (learning.data ? buildLoops(learning.data, overview.data, maturity, t) : []),
    [learning.data, overview.data, maturity, t]);

  if (!allowed) {
    return (
      <PageContainer size="prose">
        <Card className="p-6 text-sm text-muted-foreground">Model Accuracy is not available to this account.</Card>
      </PageContainer>
    );
  }

  const rows = inputs.data?.rows ?? [];
  const reviewCount = (overview.data?.eligible_count ?? 0)
    + rows.filter(pendingInput).length
    + rows.filter((r) => r.overlay && r.overlay.status === 'pending').length
    + (segment.data?.tickers ?? []).filter((x) => !x.error && x.review?.status !== 'accepted').length
    + (overrides.data?.rows.length ?? 0);
  const liveLoops = loops.filter((l) => loopStateOf(l) === 'live').length;
  const priceLabels = Object.entries(overview.data?.labels ?? {}).filter(([k]) => k.startsWith('px_')).reduce((a, [, n]) => a + n, 0);
  const tabLabel: Record<TabId, string> = {
    scorecard: overview.data ? t('Scorecard · {n}', { n: priceLabels }) : t('Scorecard'),
    review: inputs.data || segment.data ? t('Needs review · {n}', { n: reviewCount }) : t('Needs review'),
    loops: learning.data ? t('Learning loops · {a}/{n} live', { a: liveLoops, n: loops.length }) : t('Learning loops'),
    settings: multiples.data ? t('Settings · {n}', { n: multiples.data.multiples.length }) : t('Settings'),
  };
  const setTab = (id: TabId) => {
    const next = new URLSearchParams(params);
    if (id === 'scorecard') next.delete('tab'); else next.set('tab', id);
    setParams(next, { replace: true });
  };

  return (
    <>
      <TabHero
        title="Model Accuracy"
        icon={Gauge}
        subtitle="How valuations turned out, and what to change"
        actions={
          <button onClick={reloadAll} aria-label={t('Refresh')}
            className="p-2 rounded-md text-hero-foreground hover:bg-hero-foreground/10">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        }
      />
      <PageContainer size="default" className="space-y-6">
        <div role="tablist" aria-label={t('Model Accuracy')}
          className="-mx-4 px-4 sm:mx-0 sm:px-0 flex gap-1 overflow-x-auto scrollbar-hide border-b border-border">
          {TABS.map((id) => (
            <button key={id} id={`ma-tab-${id}`} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={`shrink-0 whitespace-nowrap px-3 py-2 text-sm tabular-nums border-b-2 -mb-px transition-colors
                ${tab === id ? 'border-foreground text-foreground font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
              {tabLabel[id]}
            </button>
          ))}
        </div>

        {overview.error && tab === 'scorecard' && <ErrorCard what="the overview" error={overview.error} />}
        {loading && !overview.data && !overview.error && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 size={16} className="animate-spin" /> {t('Loading…')}</div>
        )}

        {tab === 'scorecard' && (overview.data || learning.data) && (
          <ScorecardTab overview={overview.data} learning={learning.data} maturity={maturity} />
        )}
        {tab === 'review' && (
          <ReviewTab overview={overview} inputs={inputs} segment={segment} overrides={overrides} multiples={multiples} />
        )}
        {tab === 'loops' && (
          <div className="space-y-3">
            {learning.error && <ErrorCard what="the learning scorecards" error={learning.error} />}
            {learning.data && (
              <>
                <p className="text-xs text-muted-foreground">
                  {t('What each loop has learned from the agent’s own runs. Dormant: nothing to learn from yet; collecting: under its minimum; live: at or over it. A cell under the minimum says insufficient rather than a number.')}
                </p>
                <Card className="p-0 overflow-hidden">
                  <div className="hidden sm:grid grid-cols-[1rem_minmax(0,1fr)_6.5rem_7rem_minmax(0,12rem)] gap-x-2 px-3 py-2 text-xs text-muted-foreground border-b border-border">
                    <span /><span>{t('Loop')}</span><span>{t('State')}</span><span className="text-right">{t('n / need')}</span><span className="text-right">{t('Next maturity')}</span>
                  </div>
                  <ul>{loops.map((l) => <LoopRow key={l.id} loop={l} />)}</ul>
                </Card>
              </>
            )}
          </div>
        )}
        {tab === 'settings' && <SettingsTab overview={overview} learning={learning} multiples={multiples} />}
      </PageContainer>
    </>
  );
}
