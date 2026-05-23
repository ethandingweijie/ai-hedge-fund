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
import { listResearchIdeas, getSW46Cohort, type SW46IdeaMeta, type SW46TickerResult } from '@/lib/api';
import { Lightbulb, ChevronRight, Loader2 } from 'lucide-react';
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

  useEffect(() => {
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
  }, []);

  const handleClick = (idea: SW46IdeaMeta) => {
    if (idea.id === 'sw46') navigate('/research-ideas/sw46');
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
          {ideas.map((idea) => (
            <button
              key={idea.id}
              onClick={() => handleClick(idea)}
              className="w-full text-left p-4 rounded-lg border border-border bg-card hover:bg-muted/50 transition-colors group"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-base font-semibold text-foreground">{idea.name}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-semibold uppercase tracking-wider">
                      {idea.ticker_count} stocks
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed mb-2">{idea.blurb}</p>
                  <div className="flex items-center gap-4 text-[11px] text-muted-foreground mb-2">
                    <span>Last run: <span className="font-mono">{formatRunTime(idea.last_run_at)}</span></span>
                    {idea.last_pooled_delta_e != null && (
                      <span>Pooled ΔE: <span className="font-mono font-semibold text-foreground">{(idea.last_pooled_delta_e * 100).toFixed(1)}%</span></span>
                    )}
                  </div>
                  {/* Top-3 picks preview */}
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
                <ChevronRight size={18} className="text-muted-foreground group-hover:text-foreground mt-1 flex-shrink-0" />
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
