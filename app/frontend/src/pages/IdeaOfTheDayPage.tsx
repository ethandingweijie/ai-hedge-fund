/**
 * IdeaOfTheDayPage.tsx
 * ====================
 * Detail view for a single contrarian deep-value idea. Shows the full
 * hypothesis card on the left, and a chat panel on the right (stacked
 * on mobile). User can:
 *   - Read the full idea breakdown (3 pillars + catalyst + risks + sources)
 *   - Chat with the AI agent to stress-test the thesis
 *   - Add to shortlist (with optional note)
 *   - Delete the idea
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  getContrarianIdea, getContrarianChat, postContrarianChat,
  addContrarianToShortlist, removeContrarianFromShortlist, deleteContrarianIdea,
  type ContrarianIdea, type ContrarianChatMessage,
} from '@/lib/api';
import {
  ArrowLeft, Sparkles, Loader2, Send, Bookmark, BookmarkCheck, Trash2,
  AlertTriangle, ExternalLink,
} from 'lucide-react';
import { toast } from 'sonner';


function formatTime(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
  } catch { return iso; }
}


function convictionColor(score: number | null | undefined): string {
  if (score == null) return 'bg-muted text-muted-foreground';
  if (score >= 8) return 'bg-emerald-600/30 text-emerald-900 dark:text-emerald-200';
  if (score >= 6) return 'bg-amber-600/30 text-amber-900 dark:text-amber-200';
  return 'bg-orange-600/30 text-orange-900 dark:text-orange-200';
}


export function IdeaOfTheDayPage() {
  const { ideaId } = useParams<{ ideaId: string }>();
  const navigate = useNavigate();

  const [idea, setIdea] = useState<ContrarianIdea | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<ContrarianChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [shortlisting, setShortlisting] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const scrollChatToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  };

  const loadAll = useCallback(async () => {
    if (!ideaId) return;
    setLoading(true);
    try {
      const [i, c] = await Promise.all([
        getContrarianIdea(ideaId),
        getContrarianChat(ideaId).catch(() => ({ messages: [] as ContrarianChatMessage[] })),
      ]);
      setIdea(i);
      setMessages(c.messages || []);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [ideaId]);

  useEffect(() => { loadAll(); }, [loadAll]);

  useEffect(() => { scrollChatToBottom(); }, [messages.length]);

  const handleSend = async () => {
    const content = input.trim();
    if (!content || !ideaId || sending) return;
    setInput('');
    setSending(true);
    // Optimistic user message
    const tempMsg: ContrarianChatMessage = {
      message_id: 'temp-' + Date.now(),
      idea_id: ideaId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
      cost_usd: null,
    };
    setMessages((prev) => [...prev, tempMsg]);
    try {
      const res = await postContrarianChat(ideaId, content);
      // Replace temp message with real pair from server
      setMessages((prev) => [
        ...prev.filter((m) => m.message_id !== tempMsg.message_id),
        res.user_message,
        res.assistant_message,
      ]);
    } catch (e) {
      toast.error(`Chat failed: ${(e as Error).message}`);
      setMessages((prev) => prev.filter((m) => m.message_id !== tempMsg.message_id));
    } finally {
      setSending(false);
    }
  };

  const handleShortlistToggle = async () => {
    if (!idea || shortlisting) return;
    setShortlisting(true);
    try {
      if (idea._shortlisted) {
        await removeContrarianFromShortlist(idea.idea_id);
        toast.success('Removed from shortlist.');
        setIdea({ ...idea, _shortlisted: false });
      } else {
        await addContrarianToShortlist(idea.idea_id);
        toast.success(`${idea.ticker} added to shortlist.`);
        setIdea({ ...idea, _shortlisted: true });
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setShortlisting(false);
    }
  };

  const handleDelete = async () => {
    if (!idea) return;
    if (!window.confirm(`Delete idea for ${idea.ticker}? Chat history is preserved.`)) return;
    try {
      await deleteContrarianIdea(idea.idea_id);
      toast.success('Idea deleted.');
      navigate('/research-ideas');
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-background pt-16 pb-20 flex items-center justify-center">
        <Loader2 size={20} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!idea) {
    return (
      <div className="min-h-screen bg-background pt-16 pb-20 px-4">
        <div className="max-w-2xl mx-auto p-6 border border-border rounded-md text-sm text-muted-foreground">
          Idea not found.
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background pt-16 pb-20">
      <div className="px-4 max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-3">
          <button
            onClick={() => navigate('/research-ideas')}
            className="p-1 rounded hover:bg-muted text-muted-foreground"
            aria-label="Back"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <Sparkles size={16} className="text-purple-600 dark:text-purple-400" />
              <h1 className="text-xl font-bold text-foreground">{idea.ticker}</h1>
              <span className="text-sm text-muted-foreground truncate">{idea.company_name}</span>
              <span className={`ml-auto px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${convictionColor(idea.conviction_score)}`}>
                conviction {idea.conviction_score}/10
              </span>
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">
              {idea.sector || '—'} · {idea.market_cap_usd ? `$${(idea.market_cap_usd / 1e9).toFixed(1)}B mcap` : ''} · generated {formatTime(idea.generated_at)}
            </div>
          </div>
        </div>

        {/* Hypothesis card */}
        <div className="p-4 rounded-lg border-2 border-purple-400/40 dark:border-purple-500/30 bg-purple-50 dark:bg-purple-900/15 mb-4">
          <p className="text-sm font-semibold text-purple-900 dark:text-purple-100 leading-relaxed">
            {idea.hypothesis}
          </p>
        </div>

        {/* Three pillars */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-4">
          <Pillar label="Deep Value" score={idea.deep_value_score} body={idea.deep_value_angle} color="emerald" />
          <Pillar label="Asymmetric" score={idea.asymmetry_score} body={idea.asymmetric_angle} color="blue" />
          <Pillar label="Contrarian" score={idea.contrarian_score} body={idea.contrarian_angle} color="purple" />
        </div>

        {/* Catalyst + Risks */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
          <div className="p-3 rounded-md border border-cyan-400/40 dark:border-cyan-500/30 bg-cyan-50 dark:bg-cyan-900/10">
            <h3 className="text-[10px] uppercase tracking-wider font-bold text-cyan-800 dark:text-cyan-300 mb-1">
              Primary Catalyst
            </h3>
            <p className="text-xs text-foreground leading-relaxed">{idea.primary_catalyst}</p>
            {idea.catalyst_timeline && (
              <p className="text-[11px] text-muted-foreground italic mt-1">{idea.catalyst_timeline}</p>
            )}
          </div>
          <div className="p-3 rounded-md border border-red-400/40 dark:border-red-500/30 bg-red-50 dark:bg-red-900/10">
            <h3 className="text-[10px] uppercase tracking-wider font-bold text-red-800 dark:text-red-300 mb-1 flex items-center gap-1">
              <AlertTriangle size={10} /> Key Risks
            </h3>
            <ul className="text-xs text-foreground space-y-0.5 list-disc list-inside">
              {(idea.key_risks || []).map((r, i) => (
                <li key={i} className="leading-relaxed">{r}</li>
              ))}
            </ul>
          </div>
        </div>

        {/* Sources */}
        {idea.sources && idea.sources.length > 0 && (
          <div className="mb-4">
            <h3 className="text-[10px] uppercase tracking-wider font-bold text-muted-foreground mb-1.5">
              Sources ({idea.sources.length})
            </h3>
            <div className="space-y-1">
              {idea.sources.map((s, i) => (
                <a
                  key={i}
                  href={s.url || '#'}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-start gap-2 text-[11px] text-foreground/80 hover:text-foreground"
                >
                  <ExternalLink size={10} className="mt-0.5 flex-shrink-0 text-muted-foreground" />
                  <span className="flex-1">{s.title}{s.date ? ` (${s.date})` : ''}</span>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Action bar — shortlist + delete */}
        <div className="flex items-center gap-2 mb-4">
          <button
            onClick={handleShortlistToggle}
            disabled={shortlisting}
            className={
              idea._shortlisted
                ? 'flex-1 inline-flex items-center justify-center gap-2 py-2 rounded-md bg-emerald-500/20 border border-emerald-600/40 text-emerald-900 dark:text-emerald-100 text-sm font-medium disabled:opacity-50'
                : 'flex-1 inline-flex items-center justify-center gap-2 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50'
            }
          >
            {shortlisting ? <Loader2 size={14} className="animate-spin" /> : (idea._shortlisted ? <BookmarkCheck size={14} /> : <Bookmark size={14} />)}
            {idea._shortlisted ? 'In Shortlist · click to remove' : 'Add to Shortlist'}
          </button>
          <button
            onClick={() => navigate('/research-ideas/shortlist')}
            className="px-3 py-2 rounded-md border border-border bg-card text-foreground text-sm hover:bg-muted"
            title="View all shortlisted ideas"
          >
            View shortlist
          </button>
          <button
            onClick={handleDelete}
            className="p-2 rounded-md border border-border bg-card text-muted-foreground hover:text-red-600 dark:hover:text-red-400 hover:bg-red-500/10"
            title="Delete this idea"
            aria-label="Delete"
          >
            <Trash2 size={14} />
          </button>
        </div>

        {/* Chat panel */}
        <div className="border border-purple-400/40 dark:border-purple-500/30 rounded-lg bg-purple-50/40 dark:bg-purple-900/10">
          <div className="px-3 py-2 border-b border-purple-300/40 dark:border-purple-500/20 flex items-center gap-2">
            <Sparkles size={12} className="text-purple-600 dark:text-purple-400" />
            <h3 className="text-xs font-bold uppercase tracking-wider text-purple-900 dark:text-purple-100">
              Discuss · stress-test the thesis
            </h3>
            <span className="ml-auto text-[10px] text-muted-foreground italic">qwen3.6-plus · web search enabled</span>
          </div>
          <div className="max-h-[400px] overflow-y-auto p-3 space-y-3">
            {messages.length === 0 && (
              <p className="text-xs text-muted-foreground italic text-center py-4">
                No messages yet. Ask: "What's the bear case I should worry about?", "What would change your conviction?", or "Has anything material changed since this was generated?"
              </p>
            )}
            {messages.map((m) => (
              <div
                key={m.message_id}
                className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={
                    m.role === 'user'
                      ? 'max-w-[85%] px-3 py-2 rounded-lg bg-primary text-primary-foreground text-xs leading-relaxed whitespace-pre-wrap'
                      : 'max-w-[85%] px-3 py-2 rounded-lg bg-card border border-border text-foreground text-xs leading-relaxed whitespace-pre-wrap'
                  }
                >
                  {m.content}
                  <div className={`mt-1 text-[9px] ${m.role === 'user' ? 'text-primary-foreground/60' : 'text-muted-foreground'}`}>
                    {formatTime(m.created_at)}
                  </div>
                </div>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>
          <div className="border-t border-purple-300/40 dark:border-purple-500/20 p-2 flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !sending) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="Ask the agent…"
              rows={1}
              className="flex-1 resize-none px-2 py-1.5 text-xs rounded-md border border-border bg-background focus:outline-none focus:ring-1 focus:ring-primary max-h-32"
              disabled={sending}
            />
            <button
              onClick={handleSend}
              disabled={sending || !input.trim()}
              className="p-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40"
              aria-label="Send"
            >
              {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}


function Pillar({
  label, score, body, color,
}: {
  label: string;
  score: number;
  body: string;
  color: 'emerald' | 'blue' | 'purple';
}) {
  const colorClasses = {
    emerald: 'border-emerald-400/40 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-900/10 text-emerald-900 dark:text-emerald-200',
    blue:    'border-blue-400/40 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-900/10 text-blue-900 dark:text-blue-200',
    purple:  'border-purple-400/40 dark:border-purple-500/30 bg-purple-50 dark:bg-purple-900/10 text-purple-900 dark:text-purple-200',
  }[color];
  return (
    <div className={`p-3 rounded-md border ${colorClasses}`}>
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-[10px] uppercase tracking-wider font-bold">{label}</h3>
        <span className="text-[10px] font-mono font-bold">{score}/10</span>
      </div>
      <p className="text-xs leading-relaxed text-foreground/90">{body}</p>
    </div>
  );
}
