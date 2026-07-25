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
import { PageContainer } from '@/components/layout/PageContainer';


function formatTime(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
  } catch { return iso; }
}


// Single brand-green accent for strong conviction, quiet neutral otherwise —
// the score number itself carries the precision, the pill just flags "high".
function convictionColor(score: number | null | undefined): string {
  if (score == null) return 'bg-muted text-muted-foreground';
  if (score >= 7) return 'bg-primary/20 text-brand';
  return 'bg-muted text-foreground/70';
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
      <PageContainer size="default">
        <div className="flex items-center justify-center py-16">
          <Loader2 size={20} className="animate-spin text-muted-foreground" />
        </div>
      </PageContainer>
    );
  }

  if (!idea) {
    return (
      <PageContainer size="default">
        <div className="p-6 border border-border rounded-md text-sm text-muted-foreground">
          Idea not found.
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer size="default">
        {/* Header */}
        <div className="flex items-start gap-3 mb-5">
          <button
            onClick={() => navigate('/research-ideas')}
            className="mt-1 p-1.5 rounded-full hover:bg-muted text-muted-foreground shrink-0"
            aria-label="Back"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2.5 flex-wrap">
              <Sparkles size={18} className="text-brand shrink-0" />
              <h1 className="text-2xl font-bold text-foreground tracking-tight">{idea.ticker}</h1>
              <span className="text-[15px] text-muted-foreground truncate">{idea.company_name}</span>
              <span className={`ml-auto px-3 py-1 rounded-full text-[12px] font-bold ${convictionColor(idea.conviction_score)}`}>
                Conviction {idea.conviction_score}/10
              </span>
            </div>
            <div className="text-[13px] text-muted-foreground mt-2 flex items-center gap-2 flex-wrap">
              {/* Mode badge — tells the user HOW this idea was generated
                  (bottom-up deep value vs top-down thematic) so they know
                  what kind of research to expect. One quiet brand-tinted
                  pill style for every mode — the label text disambiguates,
                  colour no longer needs to. */}
              {idea.idea_mode && (
                <span
                  className="px-2.5 py-1 rounded-full text-[11px] font-semibold bg-primary/15 text-brand"
                  title={
                    idea.idea_mode === 'thematic_geographic' ? 'Top-down country/region thesis → stock' :
                    idea.idea_mode === 'thematic_sector'     ? 'Top-down industry trend → stock' :
                    idea.idea_mode === 'special_situation'   ? 'Spin-off / M&A arb / restructuring' :
                                                                'Bottom-up contrarian deep-value pick'
                  }
                >
                  {idea.idea_mode.replace('_', ' ')}
                </span>
              )}
              {idea.region && (
                <span className="px-2.5 py-1 rounded-full bg-muted text-foreground/80 text-[11px] font-semibold">
                  {idea.region}
                </span>
              )}
              {idea.expression_vehicle && idea.expression_vehicle !== 'stock' && (
                <span className="px-2.5 py-1 rounded-full bg-muted text-foreground/80 text-[11px] font-semibold uppercase">
                  {idea.expression_vehicle}
                </span>
              )}
              <span>{idea.sector || '—'}</span>
              {idea.market_cap_usd != null && (
                <span>· ${(idea.market_cap_usd / 1e9).toFixed(1)}B mcap</span>
              )}
              <span>· generated {formatTime(idea.generated_at)}</span>
            </div>
          </div>
        </div>

        {/* Write-up — flows like a Slack message: no cards, no per-section
            colour, just bold inline labels + plain sentences at a large,
            relaxed reading size. Generous vertical gaps between blocks do
            the separating work that borders/tints used to. */}
        <div className="space-y-6 mb-6">
          {/* Theme — shown only for thematic modes (geographic / sector).
              Renders ABOVE the hypothesis so the macro framing comes first. */}
          {(idea.theme || idea.industry_theme) && (
            <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground">
              <span className="font-bold">
                {idea.idea_mode === 'thematic_geographic' ? 'Geographic theme: ' : 'Sector theme: '}
              </span>
              {idea.theme || idea.industry_theme}
            </p>
          )}

          <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground">
            <span className="font-bold">Hypothesis: </span>
            {idea.hypothesis}
          </p>

          <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground">
            <span className="font-bold">Deep value ({idea.deep_value_score}/10): </span>
            {idea.deep_value_angle}
          </p>

          <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground">
            <span className="font-bold">Asymmetric ({idea.asymmetry_score}/10): </span>
            {idea.asymmetric_angle}
          </p>

          <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground">
            <span className="font-bold">Contrarian ({idea.contrarian_score}/10): </span>
            {idea.contrarian_angle}
          </p>

          <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground">
            <span className="font-bold">Catalyst: </span>
            {idea.primary_catalyst}
            {idea.catalyst_timeline && (
              <span className="italic text-foreground/70"> · {idea.catalyst_timeline}</span>
            )}
          </p>

          {(idea.key_risks || []).length > 0 && (
            <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground">
              <span className="font-bold inline-flex items-center gap-1.5">
                <AlertTriangle size={15} className="text-rose-600 dark:text-rose-400" /> Key risks:
              </span>{' '}
              {(idea.key_risks || []).join('; ')}
            </p>
          )}
        </div>

        {/* Sources */}
        {idea.sources && idea.sources.length > 0 && (
          <div className="mb-6">
            <p className="text-[16px] sm:text-[17px] leading-[1.6] text-foreground font-bold mb-1.5">
              Sources ({idea.sources.length})
            </p>
            <div className="space-y-2">
              {idea.sources.map((s, i) => (
                <a
                  key={i}
                  href={s.url || '#'}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-start gap-2 text-[15px] leading-[1.5] text-foreground/80 hover:text-brand transition-colors"
                >
                  <ExternalLink size={13} className="mt-1 flex-shrink-0 text-muted-foreground" />
                  <span className="flex-1">{s.title}{s.date ? ` (${s.date})` : ''}</span>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Action bar — shortlist + delete */}
        <div className="flex items-center gap-2 mb-5">
          <button
            onClick={handleShortlistToggle}
            disabled={shortlisting}
            className={
              idea._shortlisted
                ? 'flex-1 inline-flex items-center justify-center gap-2 h-11 rounded-full bg-primary/15 border border-primary/30 text-brand text-[14px] font-semibold disabled:opacity-50 transition-colors'
                : 'flex-1 inline-flex items-center justify-center gap-2 h-11 rounded-full bg-primary text-primary-foreground text-[14px] font-semibold hover:opacity-90 disabled:opacity-50 transition-colors'
            }
          >
            {shortlisting ? <Loader2 size={15} className="animate-spin" /> : (idea._shortlisted ? <BookmarkCheck size={15} /> : <Bookmark size={15} />)}
            {idea._shortlisted ? 'In shortlist · click to remove' : 'Add to shortlist'}
          </button>
          <button
            onClick={() => navigate('/research-ideas/shortlist')}
            className="h-11 px-4 rounded-full border border-border bg-card text-foreground text-[14px] font-medium hover:bg-muted transition-colors"
            title="View all shortlisted ideas"
          >
            View shortlist
          </button>
          <button
            onClick={handleDelete}
            className="h-11 w-11 flex items-center justify-center rounded-full border border-border bg-card text-muted-foreground hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
            title="Delete this idea"
            aria-label="Delete"
          >
            <Trash2 size={15} />
          </button>
        </div>

        {/* Chat panel */}
        <div className="border border-border rounded-lg bg-card">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <Sparkles size={14} className="text-brand" />
            <h3 className="text-[13px] font-bold text-foreground">
              Discuss · stress-test the thesis
            </h3>
            <span className="ml-auto text-[11px] text-muted-foreground italic hidden sm:inline">qwen3.6-plus · web search enabled</span>
          </div>
          <div className="max-h-[400px] overflow-y-auto p-4 space-y-3">
            {messages.length === 0 && (
              <p className="text-[13.5px] text-muted-foreground italic text-center py-4 leading-relaxed">
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
                      ? 'max-w-[85%] px-3.5 py-2.5 rounded-2xl bg-primary text-primary-foreground text-[13.5px] leading-relaxed whitespace-pre-wrap'
                      : 'max-w-[85%] px-3.5 py-2.5 rounded-2xl bg-muted text-foreground text-[13.5px] leading-relaxed whitespace-pre-wrap'
                  }
                >
                  {m.content}
                  <div className={`mt-1 text-[10px] ${m.role === 'user' ? 'text-primary-foreground/60' : 'text-muted-foreground'}`}>
                    {formatTime(m.created_at)}
                  </div>
                </div>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>
          <div className="border-t border-border p-3 flex items-end gap-2">
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
              className="flex-1 resize-none px-3 py-2 text-[13.5px] rounded-lg border border-border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-brand/40 max-h-32"
              disabled={sending}
            />
            <button
              onClick={handleSend}
              disabled={sending || !input.trim()}
              className="h-10 w-10 flex items-center justify-center rounded-full bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40 transition-opacity"
              aria-label="Send"
            >
              {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </div>
        </div>
    </PageContainer>
  );
}
