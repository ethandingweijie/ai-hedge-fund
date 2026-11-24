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
    <>
      {/* Hero sits OUTSIDE the padded container (same as Screener/Watchlist)
          so its title hugs the left gutter instead of doubling the px-4. */}
      <TabHero
        title="Discuss"
        subtitle="Search a ticker to join its discussion thread"
        icon={MessageSquare}
      />
      <PageContainer size="prose" flush className="pb-8">
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
            className="w-full h-12 pl-10 pr-4 text-[16px] rounded-full bg-card border border-border focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/10 placeholder:text-muted-foreground/70 text-foreground shadow-sm transition-colors"
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
      </div>

      <div className="mt-8">
        <h2 className="text-[22px] font-semibold tracking-tight text-foreground pb-3 border-b border-border">
          Active discussions
        </h2>
        {activeLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {!activeLoading && activeTickers.length === 0 && (
          <p className="py-6 text-[15px] text-muted-foreground">No discussions yet. Search a ticker to start one.</p>
        )}
        {!activeLoading && activeTickers.length > 0 && (
          <div className="flex flex-col divide-y divide-border">
            {activeTickers.map((t) => (
              <button
                key={t.ticker}
                onClick={() => goToTicker(t.ticker)}
                className="w-full flex items-center gap-3 py-4 -mx-3 px-3 rounded-xl text-left [@media(hover:hover)]:hover:bg-muted/40 transition-colors"
              >
                <span className="w-11 h-11 rounded-full bg-foreground text-background flex items-center justify-center text-[11px] font-bold shrink-0">
                  {t.ticker.slice(0, 4)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[17px] font-semibold text-foreground">{t.ticker}</span>
                  <span className="flex items-center gap-1 text-[13px] text-muted-foreground">
                    <MessageCircle size={13} />
                    {t.message_count} message{t.message_count === 1 ? '' : 's'}
                  </span>
                </span>
                <span className="text-[13px] text-muted-foreground shrink-0">{timeAgo(t.last_activity_at)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      </PageContainer>
    </>
  );
}
