/**
 * TickerChatPage.tsx — per-ticker discussion thread.
 *
 * Top-level messages + one level of replies (replies fetched lazily on
 * "N replies" click) + likes. New messages appear via polling (since_id),
 * not WebSockets — see the plan's confirmed decision (no push infra exists
 * elsewhere in this app).
 *
 * Owner, 2026-11-25: chat-app layout in the app's own design language —
 *   - consecutive messages from one person are grouped: name + time once above
 *     the group, a coloured initial avatar once at the group's bottom-left
 *   - large rounded grey bubbles, no card chrome
 *   - likes show as a floating reaction pill on the bubble's top-right corner
 *   - "N replies" under a message; each reply quotes its parent, and the
 *     composer shows a "Replying to" quote while replying
 *   - a pinned pill composer ("Start a new thread…") at the bottom
 * Tap (or hover on desktop) a bubble for Like / Reply / Delete.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { toast } from 'sonner';
import { ArrowLeft, ArrowUp, FileText, Heart, Reply, Trash2, X } from 'lucide-react';
import {
  getChatMessages, getChatReplies, postChatMessage, postChatReply,
  deleteChatMessage, toggleChatLike, type ChatMessage,
} from '@/lib/api';
import { useAuth } from '@/contexts/auth-context';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { useCompanyName } from '@/hooks/use-company-name';
import { timeAgo, parseBackendIso } from '@/lib/utils';

const POLL_INTERVAL_MS = 12_000;
/** Messages from the same person within this window share one name/avatar. */
const GROUP_WINDOW_MS = 5 * 60_000;

// ── Avatar ───────────────────────────────────────────────────────────────────

/** Stable per-user colour: identity, not data, so any hue is fine; lightness
 *  is fixed so white initials always clear contrast. */
function avatarColor(userId: number): string {
  const hue = (userId * 47) % 360;
  return `hsl(${hue} 45% 40%)`;
}

function Avatar({ userId, name, size = 36 }: { userId: number; name: string; size?: number }) {
  const initial = (name.trim()[0] ?? '?').toUpperCase();
  return (
    <div
      aria-hidden="true"
      className="rounded-full flex items-center justify-center text-white font-semibold shrink-0 select-none"
      style={{ width: size, height: size, backgroundColor: avatarColor(userId), fontSize: size * 0.42 }}
    >
      {initial}
    </div>
  );
}

// ── Bubble ───────────────────────────────────────────────────────────────────

function time(iso: string): number {
  try { return parseBackendIso(iso).getTime(); } catch { return 0; }
}

function Bubble({
  message, isAuthor, quote, small, onLike, onReply, onDelete,
}: {
  message: ChatMessage;
  isAuthor: boolean;
  /** Parent message quoted at the top of a reply. */
  quote?: ChatMessage;
  small?: boolean;
  onLike: () => void;
  onReply?: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const deleted = message.is_deleted;

  return (
    <div className={`group/bubble relative max-w-[88%] md:max-w-[75%] ${message.like_count > 0 ? 'mt-5' : ''}`}>
      {/* Reaction pill, floating on the bubble's top-right corner */}
      {message.like_count > 0 && (
        <button
          type="button"
          onClick={onLike}
          aria-label={`${message.like_count} like${message.like_count === 1 ? '' : 's'}${message.liked_by_me ? ', liked by you' : ''}`}
          className={`absolute -top-[22px] right-3 z-10 inline-flex items-center gap-1 h-7 px-2 rounded-full border text-[13px] font-semibold tabular-nums shadow-sm ${
            message.liked_by_me
              ? 'bg-foreground text-background border-transparent'
              : 'bg-background text-foreground border-border'
          }`}
        >
          <Heart size={13} fill="currentColor" className={message.liked_by_me ? '' : 'text-loss'} />
          {message.like_count}
        </button>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`block w-full text-left rounded-[22px] bg-muted ${small ? 'px-3.5 py-2' : 'px-4 py-2.5'}`}
      >
        {quote && (
          <span className="mb-1.5 border-l-2 border-foreground/30 pl-2.5 text-[13px] text-muted-foreground line-clamp-1">
            <span className="font-semibold text-foreground/80">{quote.author_name}</span>{' '}
            {quote.is_deleted ? 'Message deleted' : quote.content}
          </span>
        )}
        <span
          className={`block whitespace-pre-wrap break-words ${small ? 'text-[15px]' : 'text-[16px]'} leading-snug ${
            deleted ? 'italic text-muted-foreground' : 'text-foreground'
          }`}
        >
          {deleted ? 'Message deleted' : message.content}
        </span>
      </button>

      {/* Actions: tap to open on touch; also shown on hover with a mouse */}
      {!deleted && (
        <div
          className={`${open ? 'flex' : 'hidden'} [@media(hover:hover)]:group-hover/bubble:flex items-center gap-1 mt-1 pl-1`}
        >
          <ActionButton onClick={onLike} label={message.liked_by_me ? 'Unlike' : 'Like'}>
            <Heart size={14} fill={message.liked_by_me ? 'currentColor' : 'none'} />
          </ActionButton>
          {onReply && (
            <ActionButton onClick={() => { setOpen(false); onReply(); }} label="Reply">
              <Reply size={14} />
            </ActionButton>
          )}
          {isAuthor && (
            <ActionButton onClick={onDelete} label="Delete">
              <Trash2 size={14} />
            </ActionButton>
          )}
        </div>
      )}
    </div>
  );
}

function ActionButton({ onClick, label, children }: { onClick: () => void; label: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="h-9 px-3 rounded-full inline-flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted"
    >
      {children}
      {label}
    </button>
  );
}

// ── Thread (one top-level message + its replies) ─────────────────────────────

function Thread({
  message, ticker, currentUserId, showHeader, showAvatar, onLikeToggle, onDelete, onReply, repliesVersion,
}: {
  message: ChatMessage;
  ticker: string;
  currentUserId: number | undefined;
  showHeader: boolean;
  showAvatar: boolean;
  onLikeToggle: (id: number, parentId?: number) => void;
  onDelete: (id: number, parentId?: number) => void;
  onReply: (m: ChatMessage) => void;
  /** Bumped by the page after a reply to this message is posted. */
  repliesVersion: number;
}) {
  const [repliesOpen, setRepliesOpen] = useState(false);
  const [replies, setReplies] = useState<ChatMessage[] | null>(null);
  const [loading, setLoading] = useState(false);

  const loadReplies = useCallback(async () => {
    setLoading(true);
    try {
      setReplies(await getChatReplies(ticker, message.id));
    } catch (e) {
      toast.error((e as Error).message || 'Failed to load replies.');
      setRepliesOpen(false);
    } finally {
      setLoading(false);
    }
  }, [ticker, message.id]);

  // A reply was just posted to this message: open the thread and refresh it.
  useEffect(() => {
    if (repliesVersion > 0) {
      setRepliesOpen(true);
      void loadReplies();
    }
  }, [repliesVersion, loadReplies]);

  async function toggleReplies() {
    if (repliesOpen) { setRepliesOpen(false); return; }
    setRepliesOpen(true);
    if (replies === null) await loadReplies();
  }

  async function likeReply(id: number) {
    setReplies((prev) => prev?.map((r) => (r.id === id
      ? { ...r, liked_by_me: !r.liked_by_me, like_count: r.like_count + (r.liked_by_me ? -1 : 1) } : r)) ?? prev);
    onLikeToggle(id, message.id);
  }

  async function deleteReply(id: number) {
    onDelete(id, message.id);
    setReplies((prev) => prev?.map((r) => (r.id === id ? { ...r, is_deleted: true } : r)) ?? prev);
  }

  const isAuthor = currentUserId != null && message.user_id === currentUserId;
  const replyCount = Math.max(message.reply_count, replies?.length ?? 0);
  const repliers = useMemo(() => {
    const seen = new Map<number, string>();
    (replies ?? []).forEach((r) => { if (!seen.has(r.user_id)) seen.set(r.user_id, r.author_name); });
    return [...seen.entries()].slice(0, 3);
  }, [replies]);

  return (
    <div className={showHeader ? 'mt-5' : 'mt-1.5'}>
      {showHeader && (
        <div className="pl-12 mb-1 flex items-baseline gap-2">
          <span className="text-[14px] font-medium text-muted-foreground">{message.author_name}</span>
          <span className="text-[12px] text-muted-foreground/80">{timeAgo(message.created_at)}</span>
        </div>
      )}
      <div className="flex items-end gap-3">
        <div className="w-9 shrink-0 self-end mb-0.5">
          {showAvatar && <Avatar userId={message.user_id} name={message.author_name} />}
        </div>
        <Bubble
          message={message}
          isAuthor={isAuthor}
          onLike={() => onLikeToggle(message.id)}
          onReply={() => onReply(message)}
          onDelete={() => onDelete(message.id)}
        />
      </div>

      {replyCount > 0 && (
        <button
          type="button"
          onClick={toggleReplies}
          aria-expanded={repliesOpen}
          className="ml-12 mt-1.5 h-9 inline-flex items-center gap-2 text-[15px] text-muted-foreground hover:text-foreground"
        >
          {repliers.length > 0 && (
            <span className="flex -space-x-2">
              {repliers.map(([id, name]) => (
                <span key={id} className="rounded-full ring-2 ring-background">
                  <Avatar userId={id} name={name} size={22} />
                </span>
              ))}
            </span>
          )}
          {repliesOpen ? 'Hide replies' : `${replyCount} repl${replyCount === 1 ? 'y' : 'ies'}`}
        </button>
      )}

      {repliesOpen && (
        <div className="ml-12 mt-1 space-y-3 border-l border-border pl-3">
          {loading && replies === null && <p className="text-[13px] text-muted-foreground py-1">Loading replies…</p>}
          {replies?.map((r) => (
            <div key={r.id} className="pt-1">
              <div className="mb-1 flex items-baseline gap-2 pl-9">
                <span className="text-[13px] font-medium text-muted-foreground">{r.author_name}</span>
                <span className="text-[12px] text-muted-foreground/80">{timeAgo(r.created_at)}</span>
              </div>
              <div className="flex items-end gap-2">
                <div className="w-7 shrink-0 self-end mb-0.5">
                  <Avatar userId={r.user_id} name={r.author_name} size={28} />
                </div>
                <Bubble
                  small
                  message={r}
                  quote={message}
                  isAuthor={currentUserId != null && r.user_id === currentUserId}
                  onLike={() => likeReply(r.id)}
                  onDelete={() => deleteReply(r.id)}
                />
              </div>
            </div>
          ))}
          <button
            type="button"
            onClick={() => onReply(message)}
            className="h-9 text-[14px] font-medium text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5"
          >
            <Reply size={14} /> Reply to {message.author_name}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Composer ─────────────────────────────────────────────────────────────────

function Composer({
  replyTo, onCancelReply, onSubmit, ticker, bottomOffset,
}: {
  replyTo: ChatMessage | null;
  onCancelReply: () => void;
  onSubmit: (content: string) => Promise<void>;
  ticker: string;
  bottomOffset: string;
}) {
  const [text, setText] = useState('');
  const [posting, setPosting] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (replyTo) inputRef.current?.focus(); }, [replyTo]);

  // Grow with the text, up to ~5 lines.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  }, [text]);

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
    // Pinned to the very bottom with the offset as padding, so the background
    // also covers the strip between the composer and the mobile tab bar.
    <div
      className="sticky bottom-0 z-20 bg-background/95 backdrop-blur pt-2"
      style={{ paddingBottom: `calc(${bottomOffset} + 12px)` }}
    >
      {replyTo && (
        <div className="mb-2 flex items-start gap-2 rounded-2xl bg-muted/60 px-3 py-2">
          <div className="min-w-0 flex-1 border-l-2 border-foreground/30 pl-2.5">
            <p className="text-[13px] font-semibold text-foreground">Replying to {replyTo.author_name}</p>
            <p className="text-[13px] text-muted-foreground line-clamp-1">{replyTo.content}</p>
          </div>
          <button
            type="button"
            onClick={onCancelReply}
            aria-label="Cancel reply"
            className="w-9 h-9 -m-1 rounded-full flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            <X size={16} />
          </button>
        </div>
      )}
      <div className="flex items-end gap-2 rounded-[26px] border border-border bg-muted/50 pl-5 pr-1.5 py-1.5">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(); }
            if (e.key === 'Escape' && replyTo) onCancelReply();
          }}
          rows={1}
          maxLength={4000}
          aria-label={replyTo ? `Reply to ${replyTo.author_name}` : `New message about ${ticker}`}
          placeholder={replyTo ? `Reply to ${replyTo.author_name}…` : 'Start a new thread…'}
          className="flex-1 min-w-0 resize-none bg-transparent py-2 text-[16px] leading-6 text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={posting || !text.trim()}
          aria-label="Send"
          className="w-10 h-10 shrink-0 rounded-full bg-foreground text-background flex items-center justify-center disabled:opacity-30 transition-opacity"
        >
          <ArrowUp size={18} strokeWidth={2.4} />
        </button>
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function TickerChatPage() {
  const { ticker: rawTicker } = useParams<{ ticker: string }>();
  const ticker = (rawTicker ?? '').toUpperCase();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { mode } = useLayoutMode();
  const isMobile = mode === 'mobile';
  const companyName = useCompanyName(ticker);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [replyTo, setReplyTo] = useState<ChatMessage | null>(null);
  const [replyVersions, setReplyVersions] = useState<Record<number, number>>({});
  const highestIdRef = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);
  const scrolledOnce = useRef(false);

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

  // Open at the latest message, like any chat.
  useEffect(() => {
    if (!loading && messages.length > 0 && !scrolledOnce.current) {
      scrolledOnce.current = true;
      endRef.current?.scrollIntoView({ block: 'end' });
    }
  }, [loading, messages.length]);

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

  async function handleSubmit(content: string) {
    if (replyTo) {
      await postChatReply(ticker, replyTo.id, content);
      const parentId = replyTo.id;
      setMessages((prev) => prev.map((m) => (m.id === parentId ? { ...m, reply_count: m.reply_count + 1 } : m)));
      setReplyVersions((v) => ({ ...v, [parentId]: (v[parentId] ?? 0) + 1 }));
      setReplyTo(null);
      return;
    }
    const message = await postChatMessage(ticker, content);
    setMessages((prev) => [...prev, message]);
    highestIdRef.current = Math.max(highestIdRef.current, message.id);
    requestAnimationFrame(() => endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }));
  }

  async function handleLikeToggle(id: number, parentId?: number) {
    const flip = (m: ChatMessage) => ({ ...m, liked_by_me: !m.liked_by_me, like_count: m.like_count + (m.liked_by_me ? -1 : 1) });
    if (parentId == null) setMessages((prev) => prev.map((m) => (m.id === id ? flip(m) : m)));
    try {
      const result = await toggleChatLike(ticker, id);
      if (parentId == null) {
        setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, liked_by_me: result.liked, like_count: result.like_count } : m)));
      }
    } catch (e) {
      if (parentId == null) setMessages((prev) => prev.map((m) => (m.id === id ? flip(m) : m)));
      toast.error((e as Error).message || 'Failed to update like.');
    }
  }

  async function handleDelete(id: number, parentId?: number) {
    if (!window.confirm('Delete this message?')) return;
    try {
      await deleteChatMessage(ticker, id);
      if (parentId == null) {
        setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, is_deleted: true, content: '[deleted]' } : m)));
      }
    } catch (e) {
      toast.error((e as Error).message || 'Failed to delete message.');
    }
  }

  // Group consecutive messages from the same person within GROUP_WINDOW_MS:
  // the first of a run shows the name, the last shows the avatar.
  const layout = messages.map((m, i) => {
    const prev = messages[i - 1];
    const next = messages[i + 1];
    const samePrev = !!prev && prev.user_id === m.user_id && prev.reply_count === 0
      && time(m.created_at) - time(prev.created_at) < GROUP_WINDOW_MS;
    const sameNext = !!next && next.user_id === m.user_id && m.reply_count === 0
      && time(next.created_at) - time(m.created_at) < GROUP_WINDOW_MS;
    return { m, showHeader: !samePrev, showAvatar: !sameNext };
  });

  // Mobile: sit the composer above the floating bottom nav.
  const composerBottom = isMobile ? 'calc(env(safe-area-inset-bottom, 0px) + 80px)' : '0px';

  return (
    <div className="min-h-full bg-background">
      <div className="mx-auto w-full max-w-3xl px-4 md:px-8">
        {/* Header: back + ticker pill (links to the report) */}
        <div
          className="sticky top-0 z-20 bg-background/95 backdrop-blur flex items-center gap-2 py-3 pr-14 md:pr-0"
          style={isMobile ? { paddingTop: 'calc(env(safe-area-inset-top, 0px) + 12px)' } : undefined}
        >
          <button
            type="button"
            onClick={() => navigate('/discuss')}
            aria-label="Back to discussions"
            className="w-11 h-11 shrink-0 rounded-full border border-border flex items-center justify-center text-foreground hover:bg-muted"
          >
            <ArrowLeft size={18} />
          </button>
          <Link
            to={`/report/${ticker}`}
            className="min-w-0 flex-1 md:flex-none flex items-center gap-2.5 h-11 pl-1.5 pr-4 rounded-full border border-border hover:bg-muted"
            title={`Open the ${ticker} report`}
          >
            <span className="w-8 h-8 rounded-full bg-foreground text-background flex items-center justify-center text-[11px] font-bold shrink-0">
              {ticker.slice(0, 4)}
            </span>
            <span className="min-w-0 leading-tight">
              <span className="block text-[15px] font-semibold text-foreground truncate">{ticker} discussion</span>
              {companyName && <span className="block text-[12px] text-muted-foreground truncate">{companyName}</span>}
            </span>
            <FileText size={15} className="ml-auto md:ml-2 shrink-0 text-muted-foreground" />
          </Link>
        </div>

        <div className="pb-4">
          {loading && <p className="py-8 text-center text-[14px] text-muted-foreground">Loading discussion…</p>}
          {error && <p className="py-8 text-center text-[14px] text-foreground">{error}</p>}
          {!loading && !error && messages.length === 0 && (
            <div className="py-16 text-center">
              <p className="text-[17px] font-semibold text-foreground">No messages yet</p>
              <p className="mt-1 text-[14px] text-muted-foreground">Start the first thread on {ticker}.</p>
            </div>
          )}
          {layout.map(({ m, showHeader, showAvatar }) => (
            <Thread
              key={m.id}
              message={m}
              ticker={ticker}
              currentUserId={user?.id}
              showHeader={showHeader}
              showAvatar={showAvatar}
              onLikeToggle={handleLikeToggle}
              onDelete={handleDelete}
              onReply={setReplyTo}
              repliesVersion={replyVersions[m.id] ?? 0}
            />
          ))}
          <div ref={endRef} />
        </div>

        <Composer
          ticker={ticker}
          replyTo={replyTo}
          onCancelReply={() => setReplyTo(null)}
          onSubmit={handleSubmit}
          bottomOffset={composerBottom}
        />
      </div>
    </div>
  );
}
