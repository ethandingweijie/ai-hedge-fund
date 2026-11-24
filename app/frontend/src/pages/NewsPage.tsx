/**
 * NewsPage — one feed across everything the user has chosen to monitor.
 *
 * The watchlist IS the subscription: a ticker's news appears here because
 * someone added that ticker, and disappears when they remove it. There is no
 * second list to curate.
 *
 * Responsive through a class swap rather than a second render tree, following
 * HistoryPage. The report card had to be mounted twice because desktop and
 * mobile take different paths there; a page built from scratch does not need
 * to inherit that.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { PageContainer } from '@/components/layout/PageContainer';
import { NewsItemRow } from '@/components/report/NewsItemRow';
import { getNewsFeed, type NewsArticle } from '@/lib/api';
import { useNewsStream } from '@/hooks/useNewsStream';
import { useLayoutMode } from '@/contexts/layout-mode-context';

export function NewsPage() {
  const navigate = useNavigate();
  const { mode } = useLayoutMode();
  const isMobile = mode === 'mobile';

  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [tickers, setTickers] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);

  const load = useCallback((isRefresh = false) => {
    if (isRefresh) setRefreshing(true); else setLoading(true);
    setError(null);
    getNewsFeed(60)
      .then(d => { setArticles(d.articles || []); setTickers(d.tickers || []); })
      .catch(e => setError(e.message))
      .finally(() => { setLoading(false); setRefreshing(false); });
  }, []);

  useEffect(() => { load(); }, [load]);

  const onItem = useCallback((a: NewsArticle) => {
    setArticles(prev =>
      prev.some(p => (p.id && a.id ? p.id === a.id : p.url === a.url))
        ? prev
        : [a, ...prev]);
  }, []);

  useNewsStream({
    tickers,
    onItem,
    onResync: useCallback(() => load(true), [load]),
  });

  const shown = filter ? articles.filter(a => a.symbol === filter) : articles;

  return (
    <PageContainer size="default">
      <div className="flex items-center justify-between gap-3 mb-6">
        <h1 className="text-[32px] md:text-[36px] font-bold tracking-tight leading-none text-foreground">News</h1>
        <Button
          variant="outline"
          size="sm"
          onClick={() => load(true)}
          disabled={loading || refreshing}
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </Button>
      </div>

      {/* Per-ticker filter. Only worth showing once there is more than one. */}
      {tickers.length > 1 && (
        <div className="flex items-center gap-2 flex-wrap mb-4">
          <button
            onClick={() => setFilter(null)}
            className={`h-9 px-4 rounded-full text-sm font-semibold border transition-colors
              ${filter === null
                ? 'bg-brand text-brand-foreground border-transparent'
                : 'text-foreground border-border hover:bg-muted'}`}
          >
            All
          </button>
          {tickers.map(t => (
            <button
              key={t}
              onClick={() => setFilter(t === filter ? null : t)}
              className={`h-9 px-4 rounded-full text-sm font-semibold border tabular-nums transition-colors
                ${filter === t
                  ? 'bg-brand text-brand-foreground border-transparent'
                  : 'text-foreground border-border hover:bg-muted'}`}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <Card className="p-8">
          <p className="text-xs text-content-muted text-center">Loading news…</p>
        </Card>
      )}

      {!loading && error && (
        <Card className="p-6">
          <p className="text-xs text-content-high">{error}</p>
        </Card>
      )}

      {/* An empty watchlist is not an error — it is the thing to fix, so the
          page says how rather than showing a bare "no results". */}
      {!loading && !error && tickers.length === 0 && (
        <Card className="p-8 flex flex-col items-center gap-3 text-center">
          <p className="text-[15px] text-content-high font-semibold">No watchlist yet</p>
          <Button size="sm" className="rounded-full" onClick={() => navigate('/watchlist')}>
            Add tickers
          </Button>
        </Card>
      )}

      {!loading && !error && tickers.length > 0 && shown.length === 0 && (
        <Card className="p-8">
          <p className="text-xs text-content-muted text-center">
            No recent news for {filter ?? 'these tickers'}.
          </p>
        </Card>
      )}

      {!loading && !error && shown.length > 0 && (
        <div
          className={isMobile ? 'border-t border-border' : 'border-t border-border'}
        >
          <div className="flex flex-col divide-y divide-border/40">
            {shown.map((a, i) => (
              <NewsItemRow key={a.id || `${a.url}-${i}`} article={a} showSymbol />
            ))}
          </div>
        </div>
      )}
    </PageContainer>
  );
}
