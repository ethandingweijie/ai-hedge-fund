/**
 * ShortlistedIdeasPage.tsx
 * =========================
 * Stack of contrarian ideas the user has accepted. Tap to drill into the
 * full card + chat. Each row shows ticker, hypothesis, conviction score,
 * and the date it was shortlisted.
 */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listContrarianShortlist, removeContrarianFromShortlist,
  type ContrarianShortlistEntry,
} from '@/lib/api';
import { ArrowLeft, Loader2, Bookmark, X, Sparkles, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import { PageContainer } from '@/components/layout/PageContainer';
import { PageHeader } from '@/components/layout/PageHeader';


function fmtDate(iso: string | null): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
    });
  } catch { return iso; }
}


function convictionColor(score: number | null | undefined): string {
  if (score == null) return 'bg-muted text-muted-foreground';
  if (score >= 8) return 'bg-emerald-600/30 text-emerald-900 dark:text-emerald-200';
  if (score >= 6) return 'bg-amber-600/30 text-amber-900 dark:text-amber-200';
  return 'bg-orange-600/30 text-orange-900 dark:text-orange-200';
}


export function ShortlistedIdeasPage() {
  const navigate = useNavigate();
  const [entries, setEntries] = useState<ContrarianShortlistEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listContrarianShortlist();
      setEntries(res.shortlist || []);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRemove = async (e: React.MouseEvent, ideaId: string, ticker: string) => {
    e.stopPropagation();
    if (!window.confirm(`Remove ${ticker} from shortlist?`)) return;
    try {
      await removeContrarianFromShortlist(ideaId);
      toast.success(`${ticker} removed.`);
      setEntries((prev) => prev.filter((x) => x.idea_id !== ideaId));
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  return (
    <PageContainer size="default">
      <PageHeader
        leading={
          <button
            onClick={() => navigate('/research-ideas')}
            className="mt-0.5 p-1 rounded hover:bg-muted text-muted-foreground"
            aria-label="Back"
          >
            <ArrowLeft size={18} />
          </button>
        }
        icon={Bookmark}
        iconClassName="text-emerald-500"
        title="Shortlisted Ideas"
        subtitle="Contrarian deep-value hypotheses you've saved. Snapshot preserved at shortlist time."
      />

        {loading && (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading shortlist…
          </div>
        )}

        {!loading && entries.length === 0 && (
          <div className="text-sm text-muted-foreground p-6 border border-dashed border-border rounded-md text-center">
            No shortlisted ideas yet. Generate a Research Idea of the Day and add it from the card detail.
          </div>
        )}

        <div className="space-y-3">
          {entries.map((entry) => {
            const idea = entry.idea_snapshot;
            return (
              <button
                key={entry.idea_id}
                onClick={() => navigate(`/research-ideas/idea-of-the-day/${entry.idea_id}`)}
                className="w-full text-left p-4 rounded-lg border border-emerald-400/40 dark:border-emerald-500/30 bg-emerald-50/40 dark:bg-emerald-900/10 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 transition-colors group"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <Sparkles size={12} className="text-purple-600 dark:text-purple-400" />
                      <span className="font-mono text-base font-bold text-foreground">{idea.ticker}</span>
                      <span className="text-xs text-muted-foreground truncate max-w-[200px]">{idea.company_name}</span>
                      <span className={`ml-auto px-1.5 py-0.5 rounded text-[10px] font-bold ${convictionColor(idea.conviction_score)}`}>
                        {idea.conviction_score}/10
                      </span>
                    </div>
                    <p className="text-xs italic text-foreground/80 leading-snug line-clamp-3 mb-1">
                      {idea.hypothesis}
                    </p>
                    <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                      <span>{idea.sector || '—'}</span>
                      {idea.market_cap_usd != null && (
                        <span>${(idea.market_cap_usd / 1e9).toFixed(1)}B</span>
                      )}
                      <span>shortlisted {fmtDate(entry.shortlisted_at)}</span>
                    </div>
                    {entry.user_note && (
                      <p className="mt-1 text-[11px] text-foreground/70 italic">📝 {entry.user_note}</p>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-2">
                    <button
                      onClick={(e) => handleRemove(e, entry.idea_id, idea.ticker)}
                      className="p-1.5 rounded-full bg-muted/40 hover:bg-red-500/20 text-muted-foreground hover:text-red-600 dark:hover:text-red-400"
                      title="Remove from shortlist"
                      aria-label="Remove"
                    >
                      <X size={14} />
                    </button>
                    <ChevronRight size={16} className="text-muted-foreground group-hover:text-foreground" />
                  </div>
                </div>
              </button>
            );
          })}
        </div>
    </PageContainer>
  );
}
