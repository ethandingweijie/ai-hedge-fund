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
  listResearchIdeas, getSW46Cohort,
  deleteContrarianIdea, generateContrarianIdea,
  type SW46IdeaMeta, type SW46TickerResult,
} from '@/lib/api';
import { Lightbulb, ChevronRight, Loader2, Sparkles, X, Plus } from 'lucide-react';
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


export function ResearchIdeasPage() {
  const navigate = useNavigate();
  const [ideas, setIdeas] = useState<SW46IdeaMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [topPicks, setTopPicks] = useState<SW46TickerResult[]>([]);
  const [generatingIdea, setGeneratingIdea] = useState(false);

  const load = () => {
    setLoading(true);
    Promise.all([
      listResearchIdeas(),
      getSW46Cohort().catch(() => null),
    ])
      .then(([ideasRes, cohort]) => {
        setIdeas(ideasRes.ideas);
        if (cohort && cohort.results.length > 0) {
          setTopPicks(cohort.results.slice(0, 3));
        }
      })
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

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

                    {/* IoTD preview: latest ticker + hypothesis */}
                    {isAi && idea.latest_idea_ticker && (
                      <div className="mt-2 pt-2 border-t border-purple-300/40 dark:border-purple-500/20">
                        <div className="flex items-baseline gap-2 mb-1">
                          <span className="font-mono text-sm font-bold text-purple-900 dark:text-purple-100">
                            {idea.latest_idea_ticker}
                          </span>
                          <span className="text-[10px] uppercase tracking-wider text-purple-700/70 dark:text-purple-300/70">
                            Today's hypothesis
                          </span>
                        </div>
                        <p className="text-[11px] text-foreground/80 italic leading-snug line-clamp-3">
                          {idea.latest_idea_hypothesis}
                        </p>
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
