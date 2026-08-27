/**
 * HundredQPage.tsx
 * =================
 * FengHe Asset Management-style 100-Question screener: quant-first
 * deterministic scoring (FMP/EDGAR) with an event-triggered LLM
 * qualitative overlay (DeepSeek), rolled into a flat composite
 * (yes / answered, quant + qual combined) and three lifecycle tiers:
 *
 *   Active Pass (>=65%) / On-Deck (55-64%) / Cool-off (<55%)
 *
 * - Tier tabs, each with its own ranked table (composite / quant / qual).
 * - Row click -> detail drawer: per-pillar rollup bars + the full
 *   question ledger (quant/qual badge, answer, evidence citation on hover).
 * - Refresh kicks off a background full quant-batch job and polls it.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getHundredQCohort, getHundredQTier, getHundredQTicker, getHundredQTickerHistory,
  refreshHundredQ, pollHundredQJob, rescoreHundredQTicker,
  type HundredQCohortSummary, type HundredQWatchlistRow, type HundredQTickerResult,
  type HundredQTier, type HundredQTierHistoryRow, type HundredQQuestionAnswer,
} from '@/lib/api';
import { ArrowLeft, RefreshCw, Loader2, X, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { PageContainer } from '@/components/layout/PageContainer';
import { rankTone } from '@/lib/semanticColors';

const TIERS: HundredQTier[] = ['active_pass', 'on_deck', 'cooloff'];

const TIER_LABEL: Record<HundredQTier, string> = {
  active_pass: 'Active Pass',
  on_deck: 'On-Deck',
  cooloff: 'Cool-off',
  not_evaluated: 'Not Evaluated',
};

const TIER_TAB_CLS: Record<HundredQTier, string> = {
  active_pass: 'bg-surface-2 border-[var(--hairline)] text-content-high',
  on_deck: 'bg-amber-500/20 border-amber-600/60 text-amber-800 dark:text-amber-300',
  cooloff: 'bg-surface-2 border-[var(--hairline)] text-content-high',
  not_evaluated: 'bg-muted border-border text-muted-foreground',
};

function fmtPct(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(0)}%`;
}

function formatRunTime(iso: string | null | undefined): string {
  if (!iso) return 'never run';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function compositeColor(pct: number | null | undefined): string {
  if (pct == null) return 'bg-muted text-muted-foreground';
  if (pct >= 0.65) return rankTone(0);
  if (pct >= 0.55) return rankTone(2);
  return rankTone(3);
}

export function HundredQPage() {
  const navigate = useNavigate();

  const [cohort, setCohort] = useState<HundredQCohortSummary | null>(null);
  const [rowsByTier, setRowsByTier] = useState<Partial<Record<HundredQTier, HundredQWatchlistRow[]>>>({});
  const [activeTier, setActiveTier] = useState<HundredQTier>('active_pass');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    Promise.all([getHundredQCohort(), ...TIERS.map((t) => getHundredQTier(t))])
      .then(([summary, ...tierResults]) => {
        setCohort(summary);
        const map: Partial<Record<HundredQTier, HundredQWatchlistRow[]>> = {};
        TIERS.forEach((t, i) => { map[t] = tierResults[i].tickers; });
        setRowsByTier(map);
      })
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    const toastId = toast.loading('Scoring pilot universe…', { duration: Infinity });
    try {
      const { job_id } = await refreshHundredQ();
      const final = await pollHundredQJob(job_id, {
        onProgress: (s) => {
          if (s.progress_msg) toast.loading(`Refreshing · ${s.progress_msg}`, { id: toastId, duration: Infinity });
        },
      });
      toast.dismiss(toastId);
      if (final.status === 'completed') {
        toast.success(`Refreshed ${final.result?.ticker_count ?? 0} tickers.`);
        load();
      } else {
        toast.error(`Refresh failed: ${final.error ?? 'unknown error'}`);
      }
    } catch (e) {
      toast.dismiss(toastId);
      toast.error(`Refresh failed: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  };

  const rows = useMemo(
    () => [...(rowsByTier[activeTier] ?? [])].sort((a, b) => (b.composite_pct ?? -1) - (a.composite_pct ?? -1)),
    [rowsByTier, activeTier],
  );

  const tierCount = (t: HundredQTier) => cohort?.tier_counts?.[t] ?? 0;

  return (
    <PageContainer size="wide">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/research-ideas')}
            className="p-1.5 rounded-full hover:bg-muted flex-shrink-0"
            aria-label="Back"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="min-w-0">
            <h1 className="text-2xl font-bold tracking-tight text-foreground truncate">100-Question Screener</h1>
            <p className="text-[13px] text-muted-foreground mt-0.5">
              Last run {formatRunTime(cohort?.latest_run?.created_at)} · {cohort?.ticker_count ?? 0} tickers tracked
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-semibold rounded-full border-2 border-foreground/70 text-foreground bg-card hover:bg-muted disabled:opacity-50 transition-colors"
          >
            {refreshing ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
            Refresh
          </button>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading cohort…
        </div>
      )}

      {!loading && !cohort && (
        <div className="text-sm text-muted-foreground p-6 border border-border rounded-md">
          No hundred-q run yet — click Refresh to score the pilot universe.
        </div>
      )}

      {!loading && cohort && (
        <>
          {/* Tier tabs */}
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            {TIERS.map((t) => (
              <button
                key={t}
                onClick={() => setActiveTier(t)}
                className={
                  'px-5 py-2.5 min-h-[44px] text-sm font-semibold rounded-full border-2 select-none touch-manipulation transition-colors ' +
                  (activeTier === t ? TIER_TAB_CLS[t] : 'bg-card border-foreground/70 text-foreground hover:bg-muted')
                }
              >
                {TIER_LABEL[t]} ({tierCount(t)})
              </button>
            ))}
          </div>

          {/* Ranked table */}
          <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
            <table className="w-full text-[14px]">
              <thead>
                <tr className="border-b border-border bg-muted/50 text-[11px] uppercase tracking-wider text-foreground/70">
                  <th className="text-left px-4 py-3 font-bold">Ticker</th>
                  <th className="text-right px-4 py-3 font-bold" title="Flat composite: yes / answered, quant+qual combined">Composite</th>
                  <th className="text-right px-4 py-3 font-bold" title="Quant-only composite">Quant</th>
                  <th className="text-right px-4 py-3 font-bold" title="Qualitative-only composite">Qual</th>
                  <th className="text-left px-4 py-3 font-bold">Sector</th>
                  <th className="text-left px-4 py-3 font-bold">Entered tier</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => setSelectedTicker(r.ticker)}
                    className="border-b border-border/60 hover:bg-muted/40 cursor-pointer"
                  >
                    <td className="px-4 py-3.5">
                      <span className="font-mono font-bold text-foreground">{r.ticker}</span>
                      <span className="ml-2.5 text-[13px] font-semibold text-foreground hidden md:inline">{r.company_name}</span>
                    </td>
                    <td className="px-4 py-3.5 text-right">
                      <span className={`px-2.5 py-1 rounded-full text-[11.5px] font-bold font-mono ${compositeColor(r.composite_pct)}`}>
                        {fmtPct(r.composite_pct)}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right font-mono text-[13px] text-foreground/80">{fmtPct(r.quant_composite_pct)}</td>
                    <td className="px-4 py-3.5 text-right font-mono text-[13px] text-foreground/80">{fmtPct(r.qual_composite_pct)}</td>
                    <td className="px-4 py-3.5 text-[13px] text-muted-foreground">{r.sector ?? '—'}</td>
                    <td className="px-4 py-3.5 text-[12px] text-muted-foreground">{formatRunTime(r.entered_tier_at)}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">No names in {TIER_LABEL[activeTier]} this run.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <p className="text-[12px] text-muted-foreground mt-3 leading-relaxed">
            Composite = flat aggregation (yes answers / answered questions) across all ~90 auto-scored questions,
            quant (deterministic FMP/EDGAR checks) and qual (DeepSeek-scored, event-triggered) combined — no pillar weighting.
          </p>
        </>
      )}

      {selectedTicker && (
        <TickerDrawer ticker={selectedTicker} onClose={() => setSelectedTicker(null)} onRescored={load} />
      )}
    </PageContainer>
  );
}


// ─── Detail drawer ──────────────────────────────────────────────────────────

const PILLAR_ORDER = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6'];

function TickerDrawer({ ticker, onClose, onRescored }: { ticker: string; onClose: () => void; onRescored: () => void }) {
  const [detail, setDetail] = useState<HundredQTickerResult | null>(null);
  const [history, setHistory] = useState<HundredQTierHistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [rescoring, setRescoring] = useState(false);
  const [expandedPillar, setExpandedPillar] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([getHundredQTicker(ticker), getHundredQTickerHistory(ticker)])
      .then(([d, h]) => { setDetail(d); setHistory(h.history); })
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  }, [ticker]);

  const handleRescore = async (forceQual: boolean) => {
    if (rescoring) return;
    setRescoring(true);
    const toastId = toast.loading(forceQual ? 'Rescoring quant + all qualitative questions…' : 'Rescoring quant…', { duration: Infinity });
    try {
      const result = await rescoreHundredQTicker(ticker, { forceQual });
      toast.dismiss(toastId);
      toast.success(`${ticker} rescored — composite ${fmtPct(result.composite_pct)}, tier ${TIER_LABEL[result.tier]}.`);
      setDetail(result);
      onRescored();
    } catch (e) {
      toast.dismiss(toastId);
      toast.error(`Rescore failed: ${(e as Error).message}`);
    } finally {
      setRescoring(false);
    }
  };

  const ledgerByPillar = useMemo(() => {
    const map = new Map<string, HundredQQuestionAnswer[]>();
    for (const qa of detail?.question_ledger ?? []) {
      const list = map.get(qa.pillar) ?? [];
      list.push(qa);
      map.set(qa.pillar, list);
    }
    for (const list of map.values()) list.sort((a, b) => a.question_id.localeCompare(b.question_id, undefined, { numeric: true }));
    return map;
  }, [detail]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div
        className="relative w-full max-w-lg h-full overflow-y-auto bg-background border-l border-border p-5"
        onClick={(e) => e.stopPropagation()}
      >
        {loading && (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 size={20} className="animate-spin mr-2" /> Loading…
          </div>
        )}

        {!loading && detail && (
          <>
            <div className="flex items-start justify-between mb-3">
              <div>
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-lg font-bold text-foreground">{detail.ticker}</span>
                  <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${TIER_TAB_CLS[detail.tier]}`}>
                    {TIER_LABEL[detail.tier]}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground">{detail.name} · {detail.sector ?? '—'}</div>
              </div>
              <button onClick={onClose} className="p-1.5 rounded-full hover:bg-muted" aria-label="Close"><X size={16} /></button>
            </div>

            <div className="flex items-center gap-2 mb-4 flex-wrap">
              <span className={`px-2 py-1 rounded text-xs font-bold font-mono ${compositeColor(detail.composite_pct)}`}>
                composite {fmtPct(detail.composite_pct)}
              </span>
              <span className="px-2 py-1 rounded text-xs font-mono bg-muted text-muted-foreground">quant {fmtPct(detail.quant_composite_pct)}</span>
              <span className="px-2 py-1 rounded text-xs font-mono bg-muted text-muted-foreground">qual {fmtPct(detail.qual_composite_pct)}</span>
            </div>

            {/* Force-rescore actions */}
            <div className="flex items-center gap-2 mb-5">
              <button
                onClick={() => handleRescore(false)}
                disabled={rescoring}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-full border border-foreground/40 text-foreground hover:bg-muted disabled:opacity-50"
              >
                {rescoring ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                Rescore quant
              </button>
              <button
                onClick={() => handleRescore(true)}
                disabled={rescoring}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-full border border-primary/50 text-primary hover:bg-primary/10 disabled:opacity-50"
                title="Re-scores quant AND every registered qualitative question — costs real LLM calls"
              >
                <Sparkles size={13} />
                Full rescore (quant + all qual)
              </button>
            </div>

            {/* Pillar rollups */}
            <div className="space-y-2 mb-5">
              {PILLAR_ORDER.map((pid) => {
                const ps = detail.pillar_scores.find((p) => p.pillar === pid);
                if (!ps) return null;
                const pct = ps.pillar_pct;
                const isOpen = expandedPillar === pid;
                return (
                  <div key={pid} className="border border-border rounded-md overflow-hidden">
                    <button
                      onClick={() => setExpandedPillar(isOpen ? null : pid)}
                      className="w-full flex items-center justify-between px-3 py-2.5 hover:bg-muted/40 text-left"
                    >
                      <span className="text-[13px] font-semibold text-foreground">{ps.label}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-[11px] text-muted-foreground font-mono">{ps.questions_yes}/{ps.questions_answered}</span>
                        <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold font-mono ${compositeColor(pct)}`}>{fmtPct(pct)}</span>
                      </span>
                    </button>
                    {isOpen && (
                      <div className="border-t border-border divide-y divide-border/60">
                        {(ledgerByPillar.get(pid) ?? []).map((qa) => (
                          <QuestionRow key={qa.question_id} qa={qa} />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Tier history */}
            {history.length > 0 && (
              <div>
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1.5">Tier history</div>
                <ul className="space-y-1.5 text-[12px] text-foreground/80">
                  {history.map((h) => (
                    <li key={h.id} className="flex items-start gap-2">
                      <span className="text-muted-foreground font-mono whitespace-nowrap">{formatRunTime(h.changed_at)}</span>
                      <span>
                        {h.from_tier ? `${TIER_LABEL[h.from_tier as HundredQTier] ?? h.from_tier} → ` : ''}
                        <span className="font-semibold">{TIER_LABEL[h.to_tier as HundredQTier] ?? h.to_tier}</span>
                        {h.reason ? ` (${h.reason})` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function QuestionRow({ qa }: { qa: HundredQQuestionAnswer }) {
  const badgeCls = qa.q_type === 'qual' ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground';
  const answerGlyph = qa.answer == null ? '—' : qa.answer ? '✓' : '✗';
  const answerCls = qa.answer == null ? 'text-muted-foreground' : qa.answer ? 'text-content-high' : 'text-content-high';
  return (
    <div className="px-3 py-2.5 group relative">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <span className={`flex-shrink-0 font-bold text-sm ${answerCls}`}>{answerGlyph}</span>
          <div className="min-w-0">
            <div className="text-[12.5px] text-foreground leading-snug">{qa.label}</div>
            {qa.raw_value && <div className="text-[11px] text-muted-foreground font-mono mt-0.5">{qa.raw_value}</div>}
            {qa.threshold_desc && qa.answer == null && (
              <div className="text-[11px] text-muted-foreground/80 italic mt-0.5">{qa.threshold_desc}</div>
            )}
          </div>
        </div>
        <span className={`flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${badgeCls}`}>
          {qa.q_type}
        </span>
      </div>
      {qa.evidence.length > 0 && (
        <div className="mt-1.5 ml-6 space-y-1">
          {qa.evidence.slice(0, 2).map((ev, i) => (
            <div key={i} className="text-[11px] text-muted-foreground/90 border-l-2 border-border pl-2">
              <span className="font-semibold">{ev.source}:</span> "{ev.quote}"
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
