/**
 * ResearchIdeasPage.tsx
 * ======================
 * Catalogue of research ideas. v1 surfaces one card: SW46 — software-46
 * cohort using the Cassandra Unchained / Scion methodology.
 *
 * Click a card to drill into the cohort detail page.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listResearchIdeas, getSW46Cohort, getComplacencyCohort,
  deleteContrarianIdea, generateContrarianIdea,
  type SW46IdeaMeta, type SW46TickerResult, type ComplacencyTickerResult,
} from '@/lib/api';
import { Lightbulb, ChevronRight, Loader2, Sparkles, X, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { PageContainer } from '@/components/layout/PageContainer';
import { PageHeader } from '@/components/layout/PageHeader';
import { useLayoutMode } from '@/contexts/layout-mode-context';


function formatRunTime(iso: string | null): string {
  if (!iso) return 'never run';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
  } catch { return iso; }
}

function verdictColor(score: number): string {
  if (score >= 60) return 'bg-emerald-600/30 text-emerald-200';
  if (score >= 45) return 'bg-blue-600/30 text-blue-200';
  if (score >= 25) return 'bg-amber-600/30 text-amber-200';
  if (score >= 10) return 'bg-orange-600/30 text-orange-200';
  return 'bg-red-600/30 text-red-200';
}

// Aggregate-score color (0-100 scale, INVERTED — high = bearish short signal)
function aggColor(score: number | null | undefined): string {
  if (score == null) return 'bg-muted text-muted-foreground';
  if (score >= 70) return 'bg-red-600/30 text-red-900 dark:text-red-200';
  if (score >= 50) return 'bg-orange-600/30 text-orange-900 dark:text-orange-200';
  if (score >= 30) return 'bg-amber-600/30 text-amber-900 dark:text-amber-200';
  return 'bg-emerald-600/20 text-emerald-900 dark:text-emerald-300';
}

// Screen-score color (0-100, NORMAL — high = strong screen fit)
function screenScoreColor(score: number | null | undefined): string {
  if (score == null) return 'bg-muted text-muted-foreground';
  if (score >= 75) return 'bg-emerald-600/30 text-emerald-900 dark:text-emerald-200';
  if (score >= 55) return 'bg-blue-600/30 text-blue-900 dark:text-blue-200';
  if (score >= 35) return 'bg-amber-600/30 text-amber-900 dark:text-amber-200';
  return 'bg-orange-600/30 text-orange-900 dark:text-orange-200';
}

// Long vs short framing for each cohort idea. SW46 (cheap-quality software)
// and HK50 (growth/dividend China-HK) are long screens; the Complacency
// Detector flags structurally complacent names to SHORT. The AI idea-of-the-
// day is neither (it carries its own per-idea direction).
function ideaSide(id: string): { label: string; cls: string } | null {
  if (id === 'sw46' || id === 'hk50')
    return { label: 'Long Ideas', cls: 'bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-600/40' };
  if (id === 'complacency')
    return { label: 'Short Ideas', cls: 'bg-red-600/20 text-red-700 dark:text-red-300 border border-red-600/40' };
  return null;
}


export function ResearchIdeasPage() {
  const navigate = useNavigate();
  const { mode } = useLayoutMode();
  const isDesktop = mode === 'desktop';
  // Desktop/iPad size tokens — larger cards + fonts (mobile phone-frame unchanged).
  const sz = {
    pad: isDesktop ? 'p-6' : 'p-4',
    title: isDesktop ? 'text-2xl' : 'text-base',
    blurb: isDesktop ? 'text-[15px] leading-relaxed' : 'text-xs leading-relaxed',
    meta: isDesktop ? 'text-[13px]' : 'text-[11px]',
    badge: isDesktop ? 'text-[11px]' : 'text-[10px]',
    preview: isDesktop ? 'text-[14px]' : 'text-[11px]',
    previewLabel: isDesktop ? 'text-[11px]' : 'text-[9px]',
    chip: isDesktop ? 'text-[11px]' : 'text-[9px]',
  };
  const [ideas, setIdeas] = useState<SW46IdeaMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [topPicks, setTopPicks] = useState<SW46TickerResult[]>([]);
  const [topComplacency, setTopComplacency] = useState<ComplacencyTickerResult[]>([]);
  const [complacencyCohortSize, setComplacencyCohortSize] = useState(0);
  const [generatingIdea, setGeneratingIdea] = useState(false);

  const load = () => {
    setLoading(true);
    Promise.all([
      listResearchIdeas(),
      getSW46Cohort().catch(() => null),
      getComplacencyCohort().catch(() => null),
    ])
      .then(([ideasRes, sw46Cohort, complCohort]) => {
        setIdeas(ideasRes.ideas);
        if (sw46Cohort && sw46Cohort.results.length > 0) {
          setTopPicks(sw46Cohort.results.slice(0, 3));
        }
        if (complCohort && complCohort.results.length > 0) {
          setComplacencyCohortSize(complCohort.results.length);
          // Top 5 by aggregate score (0-100) across the WHOLE cohort, not
          // just gate passers. Force-rescored tickers with new aggregates
          // float to the top immediately. For tickers without aggregate yet,
          // fall back to composite × 12.5 (mathematically equivalent to
          // quant-only ranking on the same 0-100 scale) so they're
          // positioned consistently with rescored ones.
          const scored = complCohort.results
            .map((r) => ({
              row: r,
              rankScore: r.aggregate_score ?? (r.composite ?? 0) * 12.5,
            }))
            .sort((a, b) => b.rankScore - a.rankScore)
            .slice(0, 5)
            .map((x) => x.row);
          setTopComplacency(scored);
        }
      })
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  // Auto-refresh on window focus / tab visibility change. Without this,
  // a user who clicks Research Ideas → Complacency Detector → force-
  // rescores a ticker → navigates back, sees STALE state because
  // useEffect([]) only fires on mount and the back-nav reuses the
  // cached component (depending on router config).
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible' || loading) return;
      // Silent refresh (no spinner / no toast) so EVERY hero card re-ranks
      // after the user refreshed a cohort or rescored a ticker on a detail
      // page and navigated back. Mirrors the table-rerank contract:
      //   • Complacency top-5 — re-rank by latest aggregate (quant+qual).
      //   • HK50 top-5 G/D    — re-pull catalogue meta (top5_growth /
      //                          top5_dividend reflect the latest cohort run).
      //   • SW46 top-3        — re-rank by latest composite.
      listResearchIdeas()
        .then((res) => setIdeas(res.ideas))
        .catch(() => { /* silent */ });
      getSW46Cohort().then((sw46Cohort) => {
        if (sw46Cohort && sw46Cohort.results.length > 0) {
          setTopPicks(sw46Cohort.results.slice(0, 3));
        }
      }).catch(() => { /* silent */ });
      getComplacencyCohort().then((complCohort) => {
        if (complCohort && complCohort.results.length > 0) {
          setComplacencyCohortSize(complCohort.results.length);
          const scored = complCohort.results
            .map((r) => ({
              row: r,
              rankScore: r.aggregate_score ?? (r.composite ?? 0) * 12.5,
            }))
            .sort((a, b) => b.rankScore - a.rankScore)
            .slice(0, 5)
            .map((x) => x.row);
          setTopComplacency(scored);
        }
      }).catch(() => { /* silent — initial load handles toasts */ });
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [loading]);

  const handleClick = (idea: SW46IdeaMeta) => {
    if (idea.id === 'sw46') navigate('/research-ideas/sw46');
    else if (idea.id === 'hk50') navigate('/research-ideas/hk50');
    else if (idea.id === 'complacency') navigate('/research-ideas/complacency');
    else if (idea.id === 'idea_of_the_day' && idea.latest_idea_id) {
      navigate(`/research-ideas/idea-of-the-day/${idea.latest_idea_id}`);
    }
  };

  const handleDeleteIdea = async (e: React.MouseEvent, ideaId: string) => {
    e.stopPropagation();   // don't trigger card click
    try {
      await deleteContrarianIdea(ideaId);
      toast.success('Idea deleted.');
      load();
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const handleGenerateIdea = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (generatingIdea) return;
    setGeneratingIdea(true);
    const toastId = toast.loading('AI generating contrarian deep-value idea… (~30-90s)', {
      duration: Infinity,
      style: { fontSize: '15px', padding: '16px 20px', fontWeight: 500 },
    });
    try {
      const idea = await generateContrarianIdea({
        onProgress: (s) => {
          if (s.progress_msg) {
            toast.loading(`Generating Idea · ${s.progress_msg}`, {
              id: toastId, duration: Infinity,
              style: { fontSize: '15px', padding: '16px 20px', fontWeight: 500 },
            });
          }
        },
      });
      toast.dismiss(toastId);
      toast.success(`New idea: ${idea.ticker} — ${idea.company_name}`, {
        duration: 8000,
        style: { fontSize: '15px', padding: '16px 20px', fontWeight: 600 },
      });
      load();
      navigate(`/research-ideas/idea-of-the-day/${idea.idea_id}`);
    } catch (err) {
      toast.dismiss(toastId);
      toast.error(`Generation failed: ${(err as Error).message}`);
    } finally {
      setGeneratingIdea(false);
    }
  };

  return (
    <PageContainer size={isDesktop ? 'wide' : 'default'}>
      <PageHeader
        icon={Lightbulb}
        iconClassName="text-amber-500"
        title="Research Ideas"
        subtitle="Standalone valuation cohorts. Each idea lives outside the main DCF pipeline."
        actions={
          <button
            onClick={() => { if (!loading) load(); }}
            disabled={loading}
            className="p-1.5 rounded-full hover:bg-muted disabled:opacity-50"
            title="Re-fetch latest cohorts (auto-fires on tab focus too)"
            aria-label="Refresh"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} className="text-muted-foreground" />}
          </button>
        }
      />

        {loading && (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading ideas…
          </div>
        )}

        {!loading && ideas.length === 0 && (
          <div className="text-sm text-muted-foreground p-6 border border-border rounded-md">
            No research ideas yet.
          </div>
        )}

        {/* Desktop: 2-column card grid (gated on layout MODE, not viewport, so
            the 430px mobile phone-frame preview stays single-column). The AI
            "Idea of the Day" hero card spans both columns. `items-stretch` makes
            the two cards in a row share the tallest height so their outlines
            line up (matters at iPad widths where titles wrap to two lines). */}
        <div className={isDesktop ? 'grid grid-cols-1 lg:grid-cols-2 gap-3 items-stretch' : 'space-y-3'}>
          {ideas.map((idea) => {
            const isAi = idea.id === 'idea_of_the_day';
            const side = ideaSide(idea.id);
            return (
              <button
                key={idea.id}
                onClick={() => handleClick(idea)}
                className={
                  (isAi
                    ? `w-full text-left ${sz.pad} rounded-lg border-2 border-purple-400/40 dark:border-purple-500/30 bg-purple-50 dark:bg-purple-900/15 hover:bg-purple-100 dark:hover:bg-purple-900/25 transition-colors group relative`
                    : `w-full text-left ${sz.pad} rounded-lg border border-border bg-card hover:bg-muted/50 transition-colors group`)
                  + (isDesktop ? ' h-full' : '')
                  + (isDesktop && isAi ? ' lg:col-span-2' : '')
                }
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      {isAi && (
                        <span
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-purple-500/30 text-purple-900 dark:text-purple-100 text-[9px] font-bold uppercase tracking-wider"
                          title="Generated daily by Qwen3.6-plus with native web search"
                        >
                          <Sparkles size={10} />
                          AI
                        </span>
                      )}
                      <span className={`${sz.title} font-semibold text-foreground`}>{idea.name}</span>
                      {isAi && (idea.ticker_count ?? 0) > 0 && (
                        <span
                          className={`${sz.badge} px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-800 dark:text-purple-200 font-semibold uppercase tracking-wider`}
                          title="Number of ideas you've shortlisted"
                        >
                          {idea.ticker_count} shortlisted
                        </span>
                      )}
                    </div>
                    {/* Direction tag + stock count share one line, below the
                        title, so the LONG/SHORT badge always sits beside the
                        stock count regardless of how long the title wraps
                        (mobile and desktop alike). */}
                    {!isAi && (
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        {side && (
                          <span className={`${sz.badge} px-1.5 py-0.5 rounded font-bold uppercase tracking-wider ${side.cls}`}>
                            {side.label}
                          </span>
                        )}
                        <span className={`${sz.badge} px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold uppercase tracking-wider`}>
                          {idea.ticker_count} stocks
                        </span>
                      </div>
                    )}
                    <p className={`${sz.blurb} text-muted-foreground mb-2`}>{idea.blurb}</p>
                    <div className={`flex items-center gap-4 ${sz.meta} text-muted-foreground mb-2 flex-wrap`}>
                      <span>
                        {isAi ? 'Last idea: ' : 'Last run: '}
                        <span className="font-mono">{formatRunTime(idea.last_run_at)}</span>
                      </span>
                      {idea.last_pooled_delta_e != null && (
                        <span>Pooled ΔE: <span className="font-mono font-semibold text-foreground">{(idea.last_pooled_delta_e * 100).toFixed(1)}%</span></span>
                      )}
                      {idea.last_gate_passers != null && (
                        <span>Gate passers: <span className="font-mono font-semibold text-foreground">{idea.last_gate_passers}</span></span>
                      )}
                      {idea.id === 'hk50' && idea.last_avg_growth != null && (
                        <span>Avg G / D: <span className="font-mono font-semibold text-foreground">{idea.last_avg_growth.toFixed(1)} / {idea.last_avg_dividend != null ? idea.last_avg_dividend.toFixed(1) : '—'}</span></span>
                      )}
                      {isAi && idea.latest_idea_conviction != null && (
                        <span>
                          Conviction:{' '}
                          <span className="font-mono font-semibold text-purple-700 dark:text-purple-300">
                            {idea.latest_idea_conviction}/10
                          </span>
                        </span>
                      )}
                    </div>

                    {/* IoTD preview: richer hero-card excerpt with mode
                        badge, theme (for thematic modes), ticker, hypothesis,
                        catalyst preview. Mirrors the detail-page format so
                        the card meaningfully summarises the idea at a glance. */}
                    {isAi && idea.latest_idea_ticker && (
                      <div className="mt-2 pt-2 border-t border-purple-300/40 dark:border-purple-500/20 space-y-1.5">
                        {/* Mode + region + vehicle badges */}
                        <div className="flex items-center gap-1.5 flex-wrap">
                          {idea.latest_idea_mode && (
                            <span
                              className={
                                'px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider ' +
                                (idea.latest_idea_mode === 'thematic_geographic'
                                  ? 'bg-amber-500/20 text-amber-900 dark:text-amber-200'
                                  : idea.latest_idea_mode === 'thematic_sector'
                                  ? 'bg-cyan-500/20 text-cyan-900 dark:text-cyan-200'
                                  : idea.latest_idea_mode === 'special_situation'
                                  ? 'bg-rose-500/20 text-rose-900 dark:text-rose-200'
                                  : 'bg-emerald-500/20 text-emerald-900 dark:text-emerald-200')
                              }
                            >
                              {idea.latest_idea_mode.replace('_', ' ')}
                            </span>
                          )}
                          {idea.latest_idea_region && (
                            <span className="px-1.5 py-0.5 rounded bg-muted text-foreground/80 text-[9px] font-semibold">
                              {idea.latest_idea_region}
                            </span>
                          )}
                          {idea.latest_idea_vehicle && idea.latest_idea_vehicle !== 'stock' && (
                            <span className="px-1.5 py-0.5 rounded bg-muted text-foreground/80 text-[9px] font-semibold uppercase">
                              {idea.latest_idea_vehicle}
                            </span>
                          )}
                        </div>

                        {/* Ticker + company line */}
                        <div className="flex items-baseline gap-2 flex-wrap">
                          <span className="font-mono text-sm font-bold text-purple-900 dark:text-purple-100">
                            {idea.latest_idea_ticker}
                          </span>
                          {idea.latest_idea_company && (
                            <span className="text-[10px] text-muted-foreground truncate max-w-[180px]">
                              {idea.latest_idea_company}
                            </span>
                          )}
                          <span className="text-[10px] uppercase tracking-wider text-purple-700/70 dark:text-purple-300/70 ml-auto">
                            Today's hypothesis
                          </span>
                        </div>

                        {/* Theme line for thematic modes */}
                        {idea.latest_idea_theme && (
                          <p className="text-[10px] text-amber-800 dark:text-amber-300 leading-snug line-clamp-2">
                            <span className="font-semibold">Theme:</span>{' '}
                            {idea.latest_idea_theme}
                          </p>
                        )}

                        {/* Hypothesis */}
                        <p className="text-[11px] text-foreground/80 italic leading-snug line-clamp-3">
                          {idea.latest_idea_hypothesis}
                        </p>

                        {/* Catalyst preview */}
                        {idea.latest_idea_catalyst && (
                          <p className="text-[10px] text-cyan-800 dark:text-cyan-300 leading-snug line-clamp-2">
                            <span className="font-semibold">Catalyst:</span>{' '}
                            {idea.latest_idea_catalyst}
                          </p>
                        )}
                      </div>
                    )}

                    {isAi && !idea.latest_idea_ticker && (
                      <div className="mt-2 pt-2 border-t border-purple-300/40 dark:border-purple-500/20">
                        <p className="text-[11px] text-muted-foreground italic">
                          No idea generated yet. Click the + button to spin one up.
                        </p>
                      </div>
                    )}

                    {/* SW46 top-3 picks preview */}
                    {idea.id === 'sw46' && topPicks.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-border">
                        <div className={`${sz.previewLabel} uppercase tracking-wider text-muted-foreground mb-1.5`}>Top 3 ranked</div>
                        <div className="flex flex-wrap gap-1.5">
                          {topPicks.map((t) => (
                            <div
                              key={t.ticker}
                              className={`flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-background/60 ${sz.preview}`}
                              title={t.justification ?? ''}
                            >
                              <span className="font-mono font-bold text-foreground">{t.ticker}</span>
                              <span className={`px-1 py-0.5 rounded ${sz.chip} font-semibold ${verdictColor(t.composite.total)}`}>
                                {t.composite.total.toFixed(0)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* HK50 dual top-5 preview — Growth + Dividend side by side.
                        Both lists ship inline in the catalogue meta (top5_growth /
                        top5_dividend), so no extra fetch is needed. This realises
                        the hero-card spec: "indicate the top 5 for growth and top
                        5 for dividend" under the main card. */}
                    {idea.id === 'hk50' && ((idea.top5_growth?.length ?? 0) > 0 || (idea.top5_dividend?.length ?? 0) > 0) && (
                      <div className="mt-2 pt-2 border-t border-border grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <div className={`${sz.previewLabel} uppercase tracking-wider text-emerald-600 dark:text-emerald-400 mb-1.5`}>Top 5 · Growth</div>
                          <div className="space-y-1">
                            {(idea.top5_growth ?? []).map((t, i) => (
                              <div key={t.ticker} className={`flex items-center gap-1.5 ${sz.preview}`}>
                                <span className="text-muted-foreground w-3 text-right">{i + 1}</span>
                                <span className="font-semibold text-foreground flex-1 truncate">{t.name}</span>
                                <span className={`px-1 py-0.5 rounded ${sz.chip} font-bold font-mono ${screenScoreColor(t.score)}`}>
                                  {t.score != null ? t.score.toFixed(1) : '—'}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                        <div>
                          <div className={`${sz.previewLabel} uppercase tracking-wider text-amber-600 dark:text-amber-400 mb-1.5`}>Top 5 · Dividend</div>
                          <div className="space-y-1">
                            {(idea.top5_dividend ?? []).map((t, i) => (
                              <div key={t.ticker} className={`flex items-center gap-1.5 ${sz.preview}`}>
                                <span className="text-muted-foreground w-3 text-right">{i + 1}</span>
                                <span className="font-semibold text-foreground flex-1 truncate">{t.name}</span>
                                <span className={`px-1 py-0.5 rounded ${sz.chip} font-bold font-mono ${screenScoreColor(t.score)}`}>
                                  {t.score != null ? t.score.toFixed(1) : '—'}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Complacency top-5 preview — ranked by aggregate score (0-100).
                        Aggregate reflects the latest force-rescore / qualitative refresh.
                        Falls back to composite × 12.5 for tickers without qual yet (same scale). */}
                    {idea.id === 'complacency' && topComplacency.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-border">
                        <div className={`${sz.previewLabel} uppercase tracking-wider text-muted-foreground mb-1.5`}>
                          Top {topComplacency.length} by aggregate score
                          <span className="ml-1 normal-case text-muted-foreground/70">
                            (of {complacencyCohortSize})
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {topComplacency.map((t) => {
                            const rankScore = t.aggregate_score ?? (t.composite ?? 0) * 12.5;
                            const hasQual = t.aggregate_score != null;
                            return (
                              <div
                                key={t.ticker}
                                className={`flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-background/60 ${sz.preview}`}
                                title={[
                                  `${t.ticker} — ${t.name}`,
                                  `Verdict: ${t.verdict}  ·  Composite ${t.composite.toFixed(1)}/8`,
                                  hasQual
                                    ? `Aggregate ${t.aggregate_score!.toFixed(1)}/100  (quant ${t.aggregate_quant_pts?.toFixed(1) ?? '—'} + qual ${t.aggregate_qual_pts?.toFixed(1) ?? '—'})`
                                    : `Aggregate ${rankScore.toFixed(0)}/100 (quant-only — qualitative not yet scored)`,
                                ].join('\n')}
                              >
                                <span className="font-mono font-bold text-foreground">{t.ticker}</span>
                                <span className={`px-1 py-0.5 rounded ${sz.chip} font-bold ${aggColor(rankScore)}`}>
                                  {rankScore.toFixed(0)}
                                  {!hasQual && <span className="ml-0.5 opacity-60">*</span>}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                        {topComplacency.some((t) => t.aggregate_score == null) && (
                          <p className="mt-1 text-[9px] text-muted-foreground/70 italic">
                            * quant-only score (qual not yet computed —
                            cohort row's aggregate_score is null;
                            force-rescore the ticker to populate it)
                          </p>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Right side: chevron + IoTD action buttons */}
                  <div className="flex flex-col items-end gap-2 flex-shrink-0">
                    {isAi && (
                      <div className="flex items-center gap-1">
                        {/* Generate new idea button */}
                        <button
                          onClick={handleGenerateIdea}
                          disabled={generatingIdea}
                          className="p-1.5 rounded-full bg-purple-500/30 hover:bg-purple-500/50 text-purple-900 dark:text-purple-100 disabled:opacity-50"
                          title="Generate a new contrarian idea (~30-90s, costs ~$0.01)"
                          aria-label="Generate new idea"
                        >
                          {generatingIdea ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                        </button>
                        {/* Delete latest idea button — top-right per user request */}
                        {idea.latest_idea_id && (
                          <button
                            onClick={(e) => handleDeleteIdea(e, idea.latest_idea_id!)}
                            className="p-1.5 rounded-full bg-muted/40 hover:bg-red-500/20 text-muted-foreground hover:text-red-600 dark:hover:text-red-400"
                            title="Delete this idea"
                            aria-label="Delete idea"
                          >
                            <X size={14} />
                          </button>
                        )}
                      </div>
                    )}
                    <ChevronRight size={18} className="text-muted-foreground group-hover:text-foreground" />
                  </div>
                </div>
              </button>
            );
          })}
        </div>
    </PageContainer>
  );
}
