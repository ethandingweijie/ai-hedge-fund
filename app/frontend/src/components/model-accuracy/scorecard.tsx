/**
 * Scorecard: headline tiles from what the endpoints already compute, each with
 * its n and an explicit insufficient state; a label-maturity timeline; and
 * clearly marked placeholders for the metrics the accuracy review recommends
 * but the backend does not compute yet.
 */
import { Card } from '@/components/ui/card';
import type { LearningCell, LearningOverview, ModelAccuracyOverview } from '@/lib/api';
import { useI18n } from '@/i18n';
import { DiagnosticItem } from './calibration';
import {
  FAMILY_LABEL, HORIZON_LABEL, HORIZON_SHORT, PRICE_MIN_N, errText, fmtDate, share, type HorizonMaturity,
} from './labels';
import { Chip, Section } from './shared';

interface Tile {
  title: string;
  value?: string;
  sub?: string;
  n: number;
  need: number | null;
  /** When n < need: when it is expected to fill. */
  eta?: string;
  placeholder?: boolean;
}

function TileCard({ tile }: { tile: Tile }) {
  const { t } = useI18n();
  const insufficient = !tile.placeholder && tile.need != null && tile.n < tile.need && tile.value == null;
  return (
    <Card className={`p-4 min-w-0 ${tile.placeholder ? 'border-dashed bg-transparent' : ''}`}>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{t(tile.title)}</div>
      {tile.placeholder ? (
        <>
          <div className="mt-1 text-sm text-muted-foreground">{t('Not computed yet')}</div>
          <div className="mt-1 text-xs text-muted-foreground">{t('Recommended by the accuracy review; the backend does not compute it.')}</div>
        </>
      ) : insufficient ? (
        <>
          <div className="mt-1 text-sm font-medium text-foreground">{t('Insufficient')}</div>
          <div className="mt-1 text-xs tabular-nums text-muted-foreground">
            {tile.n} / {tile.need}{tile.eta ? ` · ${tile.eta}` : ''}
          </div>
        </>
      ) : (
        <>
          <div className="mt-1 text-2xl font-semibold tabular-nums text-foreground">{tile.value ?? tile.n}</div>
          <div className="mt-1 text-xs tabular-nums text-muted-foreground">
            n = {tile.n}{tile.sub ? ` · ${tile.sub}` : ''}
          </div>
        </>
      )}
    </Card>
  );
}

/** The ok cell with the most observations across a market-grouped scorecard. */
function bestCell(groups: Record<string, Record<string, LearningCell>> | undefined, field: string): [string, LearningCell] | null {
  let best: [string, LearningCell] | null = null;
  for (const [g, fields] of Object.entries(groups ?? {})) {
    const c = fields[field];
    if (c && c.status === 'ok' && (!best || c.n > best[1].n)) best = [g, c];
  }
  return best;
}

function MaturityTimeline({ maturity, labels }: { maturity: HorizonMaturity[] | null; labels: Record<string, number> }) {
  const { t } = useI18n();
  if (!maturity) {
    return (
      <Card className="p-4 text-xs text-muted-foreground">
        {t('Maturity dates need the /learning/maturity endpoint; label counts:')}{' '}
        {Object.entries(labels).map(([h, n]) => `${HORIZON_SHORT[h] ?? h} ${n}`).join(' · ')}
      </Card>
    );
  }
  return (
    <Card className="p-4 space-y-3">
      {maturity.map((m) => {
        const lab = labels[m.key] ?? 0;
        const total = Math.max(m.total, 1);
        const labW = Math.min(lab, m.total) / total * 100;
        const dueW = Math.max(0, m.due - Math.min(lab, m.due)) / total * 100;
        const lag = m.due - lab;
        return (
          <div key={m.key} className="grid grid-cols-[3rem_minmax(0,1fr)] gap-x-3 gap-y-1 items-center">
            <span className="text-xs font-medium tabular-nums text-foreground">{HORIZON_SHORT[m.key]}</span>
            <div className="h-2 rounded-full bg-muted overflow-hidden flex" role="img"
              aria-label={t('{lab} labelled, {due} of {total} ledger runs due', { lab, due: m.due, total: m.total })}>
              <div className="h-full bg-foreground" style={{ width: `${labW}%` }} />
              <div className="h-full bg-foreground/30" style={{ width: `${dueW}%` }} />
            </div>
            <span />
            <span className="text-xs tabular-nums text-muted-foreground">
              {t('{lab} labelled · {due} of {total} ledger runs due', { lab, due: m.due, total: m.total })}
              {lag > 0 && ` · ${t('{n} due but unlabelled', { n: lag })}`}
              {m.next && ` · ${t('next due {d}', { d: fmtDate(m.next) })}`}
            </span>
          </div>
        );
      })}
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-foreground" />{t('labelled')}</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-foreground/30" />{t('due, not labelled')}</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-muted border border-border" />{t('not yet due')}</span>
      </p>
    </Card>
  );
}

export function ScorecardTab({ overview, learning, maturity }: {
  overview: ModelAccuracyOverview | null; learning: LearningOverview | null; maturity: HorizonMaturity[] | null;
}) {
  const { t } = useI18n();
  const labels = overview?.labels ?? {};
  const totalLabels = Object.values(labels).reduce((a, b) => a + b, 0);
  const mat = (k: string) => maturity?.find((m) => m.key === k) ?? null;
  const etaFor = (k: string, need: number) => {
    const m = mat(k);
    if (!maturity) return undefined;
    if (!m || m.total < need) return t('the ledger holds {n} runs', { n: m?.total ?? 0 });
    if (m.due >= need) return t('{n} runs already due, unlabelled', { n: m.due });
    return t('first matures ~{d}', { d: fmtDate(m.reachesNeed) });
  };

  const est = learning?.estimates;
  const minN = est?.min_n ?? 5;
  const eps = bestCell(est?.fy?.market?.groups, 'eps');
  const epsN = est?.counts?.['fy:eps'] ?? 0;
  const guid = Object.entries(learning?.guidance?.markets ?? {}).filter(([, c]) => c.status === 'ok').sort((a, b) => b[1].n - a[1].n)[0];
  const scen180 = (learning?.scenarios?.px_180d ?? {}) as { n?: number; need?: number; status?: string };
  const gates = Object.values(learning?.gates?.gates ?? {});
  const judged = gates.reduce((a, g) => a + (g.n_judged ?? 0), 0);
  const helped = gates.reduce((a, g) => a + (g.HELPED ?? 0), 0);
  const gateNeed = learning?.gates?.min_n ?? 20;
  const ovN = learning?.overrides?.n_scored_rows ?? 0;

  const tiles: Tile[] = [
    { title: 'Outcome labels', value: String(totalLabels), n: totalLabels, need: null,
      sub: Object.entries(labels).map(([h, n]) => `${HORIZON_SHORT[h] ?? h} ${n}`).join(' · ') },
    { title: 'Price labels at 90 days', n: labels.px_90d ?? 0, need: PRICE_MIN_N, eta: etaFor('px_90d', PRICE_MIN_N),
      value: (labels.px_90d ?? 0) >= PRICE_MIN_N ? String(labels.px_90d) : undefined },
    eps
      ? { title: 'FY+1 EPS error (MAE)', value: errText('eps', eps[1].mae), n: eps[1].n, need: minN,
          sub: `${eps[0]} · ${t('bias')} ${errText('eps', eps[1].bias)}` }
      : { title: 'FY+1 EPS error (MAE)', n: epsN, need: minN, eta: t('matures as companies report') },
    eps
      ? { title: 'EPS inside bear–bull band', value: share(eps[1].in_band_share), n: eps[1].n, need: minN,
          sub: `${eps[0]} · ${t('beat consensus')} ${share(eps[1].beat_consensus_share)}` }
      : { title: 'EPS inside bear–bull band', n: epsN, need: minN, eta: t('matures as companies report') },
    guid
      ? { title: 'Guidance met or beaten', value: share(guid[1].beat_rate), n: guid[1].n, need: null,
          sub: `${guid[0]} · ${t('within band')} ${share(guid[1].hit_rate)}` }
      : { title: 'Guidance met or beaten', n: learning?.guidance?.n ?? 0, need: minN, eta: t('matures as companies report') },
    { title: 'Scenario probabilities, 180 days', n: scen180.n ?? 0, need: scen180.need ?? PRICE_MIN_N,
      eta: etaFor('px_180d', scen180.need ?? PRICE_MIN_N),
      value: scen180.status === 'ok' ? t('see Learning loops') : undefined },
    { title: 'Gates that helped', n: judged, need: gateNeed, eta: t('matures as companies report'),
      value: judged >= gateNeed ? `${helped} / ${judged}` : undefined },
    { title: 'User overrides scored', n: ovN, need: null, value: String(ovN),
      sub: t('{n} active', { n: learning?.overrides?.n_overrides ?? 0 }) },
  ];
  const placeholders: Tile[] = [
    { title: 'Excess return vs benchmark (180d / 365d)', n: 0, need: null, placeholder: true },
    { title: 'Information coefficient', n: 0, need: null, placeholder: true },
    { title: 'Brier score on P(outperform)', n: 0, need: null, placeholder: true },
    { title: 'P10–P90 interval coverage', n: 0, need: null, placeholder: true },
  ];

  const families = Object.entries(learning?.families ?? {}).filter(([k]) => k === 'iv' || k === 'pt' || k === 'est');
  const ledger = learning?.ledger ?? {};
  const excluded = (ledger.override_carried ?? 0) + (ledger.unrated ?? 0) + (ledger.cache_copy ?? 0);
  const diags = overview?.diagnostics ?? [];

  return (
    <div className="space-y-6">
      <Card className="px-4 py-3 text-xs text-muted-foreground flex flex-wrap gap-x-5 gap-y-1">
        {ledger.rows != null && (
          <span>{t('{n} runs in the ledger', { n: ledger.rows })}{excluded > 0 ? ` · ${t('{n} excluded', { n: excluded })}` : ''}</span>
        )}
        {families.map(([fam, v]) => (
          <span key={fam}>{t('{f} calibration', { f: t(FAMILY_LABEL[fam] ?? fam) })}: <span className="text-foreground">{(v as { version_id?: string } | null)?.version_id ?? t('none promoted')}</span></span>
        ))}
        {overview && overview.eligible_count > 0 && <span className="text-foreground">{t('{n} proposal(s) ready for your decision', { n: overview.eligible_count })}</span>}
      </Card>

      <div className="grid gap-3 grid-cols-1 min-[431px]:grid-cols-2 lg:grid-cols-4">
        {tiles.map((x) => <TileCard key={x.title} tile={x} />)}
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">{t('Label maturity')}</h3>
        <MaturityTimeline maturity={maturity} labels={labels} />
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">{t('Not computed yet')}</h3>
        <div className="grid gap-3 grid-cols-1 min-[431px]:grid-cols-2 lg:grid-cols-4">
          {placeholders.map((x) => <TileCard key={x.title} tile={x} />)}
        </div>
      </div>

      <div className="border-b border-border">
        <Section title="What the numbers say" count={diags.length}
          summary={diags[0]?.title}
          hint={overview?.diagnostics_horizon ? `From runs scored against the ${HORIZON_LABEL[overview.diagnostics_horizon] ?? overview.diagnostics_horizon}.` : undefined}>
          {diags.length === 0 ? (
            <Card className="p-4 text-sm text-muted-foreground">Nothing stands out yet — findings appear as outcomes accumulate.</Card>
          ) : (
            <Card className="px-4 py-1">
              {diags.map((d) => <DiagnosticItem key={d.id} card={d} openProposals={overview?.proposals.length ?? 0} />)}
            </Card>
          )}
        </Section>
      </div>
      {totalLabels === 0 && <Chip>{t('No outcome labels yet')}</Chip>}
    </div>
  );
}
