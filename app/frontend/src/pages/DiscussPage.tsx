/**
 * DiscussPage.tsx — landing page for the per-ticker discussion module.
 * Ticker search/jump box, plus a list of currently active discussions
 * (tickers with at least one message, ranked by most recent activity) so
 * the page isn't just a blank search bar once the feature has usage.
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MessageSquare, MessageCircle, Search } from 'lucide-react';
import { searchCompanies, getActiveChatTickers, type CompanySearchResult, type ChatActiveTicker } from '@/lib/api';
import { timeAgo } from '@/lib/utils';
import { PageContainer } from '@/components/layout/PageContainer';
import { TabHero } from '@/components/layout/TabHero';

export function DiscussPage() {
  const navigate = useNavigate();
  const [ticker, setTicker] = useState('');
  const [suggestions, setSuggestions] = useState<CompanySearchResult[]>([]);
  const [showSugg, setShowSugg] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reqIdRef = useRef(0);
  const wrapperRef = useRef<HTMLFormElement>(null);

  const [activeTickers, setActiveTickers] = useState<ChatActiveTicker[]>([]);
  const [activeLoading, setActiveLoading] = useState(true);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowSugg(false);
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  useEffect(() => {
    getActiveChatTickers(12)
      .then(setActiveTickers)
      .catch(() => { /* ignore — empty state renders fine */ })
      .finally(() => setActiveLoading(false));
  }, []);

  function goToTicker(t: string) {
    const clean = t.trim().toUpperCase();
    if (!clean) return;
    navigate(`/discuss/${encodeURIComponent(clean)}`);
  }

  function handleChange(raw: string) {
    const upper = raw.toUpperCase();
    setTicker(upper);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (upper.trim().length < 2) {
      reqIdRef.current++;
      setSuggestions([]);
      setShowSugg(false);
      return;
    }
    const reqId = ++reqIdRef.current;
    debounceRef.current = setTimeout(() => {
      searchCompanies(upper.trim())
        .then((data) => {
          if (reqId !== reqIdRef.current) return;
          setSuggestions(data);
          setShowSugg(data.length > 0);
        })
        .catch(() => { /* ignore */ });
    }, 280);
  }

  return (
    <PageContainer size="prose">
      <TabHero
        title="Discuss"
        subtitle="Search a ticker to join its discussion thread"
        icon={MessageSquare}
      />
      <div className="mt-6">
        <form
          onSubmit={(e) => { e.preventDefault(); goToTicker(ticker); }}
          className="relative"
          ref={wrapperRef}
        >
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground/70" size={16} />
          <input
            value={ticker}
            onChange={(e) => handleChange(e.target.value)}
            onFocus={() => { if (suggestions.length > 0) setShowSugg(true); }}
            placeholder="Search ticker or company..."
            maxLength={20}
            autoFocus
            className="w-full h-12 pl-10 pr-4 text-sm rounded-full bg-card border border-border focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/10 placeholder:text-muted-foreground/70 text-foreground shadow-sm transition-colors"
          />
          {showSugg && suggestions.length > 0 && (
            <div className="absolute top-full left-0 right-0 mt-1 rounded-lg border border-border bg-card shadow-lg max-h-80 overflow-y-auto z-20">
              {suggestions.map((s) => (
                <button
                  key={s.ticker}
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    setShowSugg(false);
                    goToTicker(s.ticker);
                  }}
                  className="w-full text-left px-4 py-2.5 text-sm hover:bg-muted/60 border-b border-border/60 last:border-b-0 flex items-center justify-between gap-3"
                >
                  <span className="font-mono font-semibold">{s.ticker}</span>
                  <span className="text-muted-foreground truncate">{s.name}</span>
                </button>
              ))}
            </div>
          )}
        </form>
        <p className="mt-4 text-xs text-muted-foreground">
          Every ticker has its own thread — post, reply, and like with other users.
        </p>
      </div>

      <div className="mt-8">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-3">
          Active Discussions
        </h2>
        {activeLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {!activeLoading && activeTickers.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No discussions yet — search a ticker above and be the first to start one.
          </p>
        )}
        {!activeLoading && activeTickers.length > 0 && (
          <div className="space-y-2">
            {activeTickers.map((t) => (
              <button
                key={t.ticker}
                onClick={() => goToTicker(t.ticker)}
                className="w-full flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3 text-left hover:border-brand/50 hover:bg-muted/40 transition-colors"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span className="font-mono font-bold text-sm text-foreground shrink-0">{t.ticker}</span>
                  <span className="flex items-center gap-1 text-xs text-muted-foreground shrink-0">
                    <MessageCircle size={12} />
                    {t.message_count}
                  </span>
                </div>
                <span className="text-xs text-muted-foreground shrink-0">{timeAgo(t.last_activity_at)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </PageContainer>
  );
}
