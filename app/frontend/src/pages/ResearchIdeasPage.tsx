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


export function ResearchIdeasPage() {
  const navigate = useNavigate();
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
      if (document.visibilityState === 'visible' && !loading) {
        // Lightweight refresh: only re-fetch the cohort that's likely
        // to have changed. SW46 doesn't auto-update so skip it.
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
      }
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
    <div className="min-h-screen bg-background pt-16 pb-20">
      <div className="px-4 max-w-3xl mx-auto">
        <div className="flex items-center gap-3 mb-2">
          <Lightbulb className="text-amber-500" size={22} />
          <h1 className="text-xl font-bold text-foreground">Research Ideas</h1>
          <button
            onClick={() => { if (!loading) load(); }}
            disabled={loading}
            className="ml-auto p-1.5 rounded-full hover:bg-muted disabled:opacity-50"
            title="Re-fetch latest cohorts (auto-fires on tab focus too)"
            aria-label="Refresh"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} className="text-muted-foreground" />}
          </button>
        </div>
        <p className="text-xs text-muted-foreground mb-6">
          Standalone valuation cohorts. Each idea lives outside the main DCF pipeline.
        </p>

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

        <div className="space-y-3">
          {ideas.map((idea) => {
            const isAi = idea.id === 'idea_of_the_day';
            return (
              <button
                key={idea.id}
                onClick={() => handleClick(idea)}
                className={
                  isAi
                    ? 'w-full text-left p-4 rounded-lg border-2 border-purple-400/40 dark:border-purple-500/30 bg-purple-50 dark:bg-purple-900/15 hover:bg-purple-100 dark:hover:bg-purple-900/25 transition-colors group relative'
                    : 'w-full text-left p-4 rounded-lg border border-border bg-card hover:bg-muted/50 transition-colors group'
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
                      <span className="text-base font-semibold text-foreground">{idea.name}</span>
                      {!isAi && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold uppercase tracking-wider">
                          {idea.ticker_count} stocks
                        </span>
                      )}
                      {isAi && (idea.ticker_count ?? 0) > 0 && (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-800 dark:text-purple-200 font-semibold uppercase tracking-wider"
                          title="Number of ideas you've shortlisted"
                        >
                          {idea.ticker_count} shortlisted
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground leading-relaxed mb-2">{idea.blurb}</p>
                    <div className="flex items-center gap-4 text-[11px] text-muted-foreground mb-2 flex-wrap">
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
                        <div className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1.5">Top 3 ranked</div>
                        <div className="flex flex-wrap gap-1.5">
                          {topPicks.map((t) => (
                            <div
                              key={t.ticker}
                              className="flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-background/60 text-[11px]"
                              title={t.justification ?? ''}
                            >
                              <span className="font-mono font-bold text-foreground">{t.ticker}</span>
                              <span className={`px-1 py-0.5 rounded text-[9px] font-semibold ${verdictColor(t.composite.total)}`}>
                                {t.composite.total.toFixed(0)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Complacency top-5 preview — ranked by aggregate score (0-100).
                        Aggregate reflects the latest force-rescore / qualitative refresh.
                        Falls back to composite × 12.5 for tickers without qual yet (same scale). */}
                    {idea.id === 'complacency' && topComplacency.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-border">
                        <div className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1.5">
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
                                className="flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-background/60 text-[11px]"
                                title={[
                                  `${t.ticker} — ${t.name}`,
                                  `Verdict: ${t.verdict}  ·  Composite ${t.composite.toFixed(1)}/8`,
                                  hasQual
                                    ? `Aggregate ${t.aggregate_score!.toFixed(1)}/100  (quant ${t.aggregate_quant_pts?.toFixed(1) ?? '—'} + qual ${t.aggregate_qual_pts?.toFixed(1) ?? '—'})`
                                    : `Aggregate ${rankScore.toFixed(0)}/100 (quant-only — qualitative not yet scored)`,
                                ].join('\n')}
                              >
                                <span className="font-mono font-bold text-foreground">{t.ticker}</span>
                                <span className={`px-1 py-0.5 rounded text-[9px] font-bold ${aggColor(rankScore)}`}>
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
      </div>
    </div>
  );
}
