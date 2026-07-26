/**
 * TickerChatPage.tsx — per-ticker discussion thread.
 *
 * Top-level messages + one level of replies (replies fetched lazily on
 * "N replies" click) + likes. New messages appear via polling (since_id),
 * not WebSockets — see the plan's confirmed decision (no push infra exists
 * elsewhere in this app).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { toast } from 'sonner';
import { ArrowLeft, Heart, MessageCircle, Send, Trash2 } from 'lucide-react';
import {
  getChatMessages, getChatReplies, postChatMessage, postChatReply,
  deleteChatMessage, toggleChatLike, type ChatMessage,
} from '@/lib/api';
import { useAuth } from '@/contexts/auth-context';
import { timeAgo } from '@/lib/utils';
import { PageContainer } from '@/components/layout/PageContainer';
import { Button } from '@/components/ui/button';

const POLL_INTERVAL_MS = 12_000;

function MessageComposer({
  onSubmit, placeholder, autoFocus, compact,
}: {
  onSubmit: (content: string) => Promise<void>;
  placeholder: string;
  autoFocus?: boolean;
  compact?: boolean;
}) {
  const [text, setText] = useState('');
  const [posting, setPosting] = useState(false);

  async function submit() {
    const content = text.trim();
    if (!content || posting) return;
    setPosting(true);
    try {
      await onSubmit(content);
      setText('');
    } catch (e) {
      toast.error((e as Error).message || 'Failed to post.');
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="flex items-start gap-2">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder}
        autoFocus={autoFocus}
        maxLength={4000}
        rows={compact ? 1 : 2}
        className="flex-1 resize-none rounded-lg border border-border bg-card px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand/10 focus:border-brand placeholder:text-muted-foreground/70"
      />
      <Button size={compact ? 'sm' : 'default'} onClick={submit} disabled={posting || !text.trim()}>
        <Send size={14} />
        {!compact && <span className="ml-1">Post</span>}
      </Button>
    </div>
  );
}

function MessageRow({
  message, ticker, currentUserId, onLikeToggle, onDelete, onReplyPosted, depth,
}: {
  message: ChatMessage;
  ticker: string;
  currentUserId: number | undefined;
  onLikeToggle: (id: number) => void;
  onDelete: (id: number) => void;
  onReplyPosted: (parentId: number) => void;
  depth: 0 | 1;
}) {
  const [repliesOpen, setRepliesOpen] = useState(false);
  const [replies, setReplies] = useState<ChatMessage[] | null>(null);
  const [repliesLoading, setRepliesLoading] = useState(false);
  const [replying, setReplying] = useState(false);

  async function toggleReplies() {
    if (repliesOpen) { setRepliesOpen(false); return; }
    setRepliesOpen(true);
    if (replies !== null) return;
    setRepliesLoading(true);
    try {
      const data = await getChatReplies(ticker, message.id);
      setReplies(data);
    } catch (e) {
      toast.error((e as Error).message || 'Failed to load replies.');
      setRepliesOpen(false);
    } finally {
      setRepliesLoading(false);
    }
  }

  const isAuthor = currentUserId != null && message.user_id === currentUserId;

  return (
    <div className={depth === 1 ? 'ml-8 mt-3' : ''}>
      <div className="rounded-lg border border-border bg-card px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-baseline gap-2 min-w-0">
            <span className="font-semibold text-sm text-foreground truncate">{message.author_name}</span>
            <span className="text-[11px] text-muted-foreground shrink-0">{timeAgo(message.created_at)}</span>
          </div>
          {isAuthor && !message.is_deleted && (
            <button
              onClick={() => onDelete(message.id)}
              className="text-muted-foreground hover:text-destructive shrink-0"
              title="Delete message"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
        <p className={`mt-1.5 text-sm whitespace-pre-wrap break-words ${message.is_deleted ? 'italic text-muted-foreground' : 'text-foreground'}`}>
          {message.content}
        </p>
        <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
          <button
            onClick={() => onLikeToggle(message.id)}
            className={`flex items-center gap-1 hover:text-foreground transition-colors ${message.liked_by_me ? 'text-red-500 hover:text-red-500' : ''}`}
          >
            <Heart size={13} fill={message.liked_by_me ? 'currentColor' : 'none'} />
            {message.like_count > 0 && <span>{message.like_count}</span>}
          </button>
          {depth === 0 && (
            <button onClick={toggleReplies} className="flex items-center gap-1 hover:text-foreground transition-colors">
              <MessageCircle size={13} />
              {message.reply_count > 0 ? `${message.reply_count} repl${message.reply_count === 1 ? 'y' : 'ies'}` : 'Reply'}
            </button>
          )}
        </div>
      </div>

      {depth === 0 && repliesOpen && (
        <div className="mt-2 space-y-2">
          {repliesLoading && <p className="ml-8 text-xs text-muted-foreground">Loading replies...</p>}
          {replies?.map((r) => (
            <MessageRow
              key={r.id}
              message={r}
              ticker={ticker}
              currentUserId={currentUserId}
              onLikeToggle={onLikeToggle}
              onDelete={onDelete}
              onReplyPosted={onReplyPosted}
              depth={1}
            />
          ))}
          <div className="ml-8">
            {!replying ? (
              <button
                onClick={() => setReplying(true)}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Write a reply...
              </button>
            ) : (
              <MessageComposer
                compact
                autoFocus
                placeholder={`Reply to ${message.author_name}...`}
                onSubmit={async (content) => {
                  const reply = await postChatReply(ticker, message.id, content);
                  setReplies((prev) => [...(prev ?? []), reply]);
                  onReplyPosted(message.id);
                  setReplying(false);
                }}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function TickerChatPage() {
  const { ticker: rawTicker } = useParams<{ ticker: string }>();
  const ticker = (rawTicker ?? '').toUpperCase();
  const navigate = useNavigate();
  const { user } = useAuth();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const highestIdRef = useRef(0);

  const load = useCallback(async () => {
    if (!ticker) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getChatMessages(ticker, { limit: 50 });
      setMessages(data.messages);
      highestIdRef.current = data.messages.reduce((max, m) => Math.max(max, m.id), 0);
    } catch (e) {
      setError((e as Error).message || 'Failed to load thread.');
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => { load(); }, [load]);

  // Poll for new top-level messages.
  useEffect(() => {
    if (!ticker) return;
    const interval = setInterval(async () => {
      try {
        const data = await getChatMessages(ticker, { sinceId: highestIdRef.current });
        if (data.messages.length > 0) {
          setMessages((prev) => {
            const existingIds = new Set(prev.map((m) => m.id));
            const fresh = data.messages.filter((m) => !existingIds.has(m.id));
            highestIdRef.current = fresh.reduce((max, m) => Math.max(max, m.id), highestIdRef.current);
            return [...prev, ...fresh];
          });
        }
      } catch {
        // Silent — a missed poll tick isn't worth surfacing.
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [ticker]);

  async function handlePostTopLevel(content: string) {
    const message = await postChatMessage(ticker, content);
    setMessages((prev) => [...prev, message]);
    highestIdRef.current = Math.max(highestIdRef.current, message.id);
  }

  function handleReplyPosted(parentId: number) {
    setMessages((prev) => prev.map((m) => (
      m.id === parentId ? { ...m, reply_count: m.reply_count + 1 } : m
    )));
  }

  async function handleLikeToggle(id: number) {
    // Optimistic flip.
    setMessages((prev) => prev.map((m) => (
      m.id === id
        ? { ...m, liked_by_me: !m.liked_by_me, like_count: m.like_count + (m.liked_by_me ? -1 : 1) }
        : m
    )));
    try {
      const result = await toggleChatLike(ticker, id);
      setMessages((prev) => prev.map((m) => (
        m.id === id ? { ...m, liked_by_me: result.liked, like_count: result.like_count } : m
      )));
    } catch (e) {
      // Revert on failure.
      setMessages((prev) => prev.map((m) => (
        m.id === id
          ? { ...m, liked_by_me: !m.liked_by_me, like_count: m.like_count + (m.liked_by_me ? -1 : 1) }
          : m
      )));
      toast.error((e as Error).message || 'Failed to update like.');
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm('Delete this message?')) return;
    try {
      await deleteChatMessage(ticker, id);
      setMessages((prev) => prev.map((m) => (
        m.id === id ? { ...m, is_deleted: true, content: '[deleted]' } : m
      )));
    } catch (e) {
      toast.error((e as Error).message || 'Failed to delete message.');
    }
  }

  return (
    <PageContainer size="prose">
      <div className="flex items-center gap-2 mb-4">
        <button
          onClick={() => navigate('/discuss')}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft size={15} />
          Discuss
        </button>
        <span className="text-muted-foreground/50">/</span>
        <Link to={`/report/${ticker}`} className="font-mono font-bold text-foreground hover:underline">
          {ticker}
        </Link>
      </div>

      <div className="mb-4">
        <MessageComposer
          placeholder={`Share your thoughts on ${ticker}...`}
          onSubmit={handlePostTopLevel}
        />
      </div>

      {loading && <p className="text-sm text-muted-foreground">Loading discussion...</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {!loading && !error && messages.length === 0 && (
        <p className="text-sm text-muted-foreground">No messages yet — be the first to share your thoughts on {ticker}.</p>
      )}

      <div className="space-y-3">
        {messages.map((m) => (
          <MessageRow
            key={m.id}
            message={m}
            ticker={ticker}
            currentUserId={user?.id}
            onLikeToggle={handleLikeToggle}
            onDelete={handleDelete}
            onReplyPosted={handleReplyPosted}
            depth={0}
          />
        ))}
      </div>
    </PageContainer>
  );
}
