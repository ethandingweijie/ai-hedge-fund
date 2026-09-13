/**
 * NewsPanel — latest news for one ticker.
 *
 * Served from the backend news store rather than a live upstream call, so a
 * warm ticker renders from a local read. Mounted on BOTH render paths: the
 * desktop report pages and V2ReportView. It previously lived only on desktop,
 * which meant news did not exist on mobile at all — the exact trap the
 * comments in ReportViewPage and V2ReportView warn about.
 */

import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { getCompanyNews, type NewsArticle } from '@/lib/api';
import { NewsItemRow } from '@/components/report/NewsItemRow';
import { useNewsStream } from '@/hooks/useNewsStream';

interface NewsPanelProps {
  ticker: string;
}

export function NewsPanel({ ticker }: NewsPanelProps) {
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    getCompanyNews(ticker, 8)
      .then(d => setArticles(d.articles))
      .catch(e => { if (!quiet) setError(e.message); })
      .finally(() => { if (!quiet) setLoading(false); });
  }, [ticker]);

  useEffect(() => { load(); }, [load]);

  // Live items for this ticker. De-duplicated against what is already shown:
  // the snapshot and the stream can legitimately carry the same item when a
  // poll lands between the two, and the store's id is the same either way.
  const onItem = useCallback((a: NewsArticle) => {
    setArticles(prev =>
      prev.some(p => (p.id && a.id ? p.id === a.id : p.url === a.url))
        ? prev
        : [a, ...prev].slice(0, 8));
  }, []);

  useNewsStream({
    tickers: ticker ? [ticker] : [],
    onItem,
    // A reconnect means items may have been published while the stream was
    // down; re-read quietly rather than flashing the panel back to Loading.
    onResync: useCallback(() => load(true), [load]),
  });

  return (
    <Card className="p-4 flex flex-col gap-3 h-full">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Latest News
        </h3>
        <div className="flex items-center gap-2">
          {!loading && articles.length > 0 && (
            <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded-full tabular-nums">
              {articles.length}
            </span>
          )}
          <span className="text-[10px] text-muted-foreground">{ticker}</span>
        </div>
      </div>

      {loading && (
        <p className="flex-1 text-xs text-muted-foreground flex items-center justify-center">Loading news…</p>
      )}

      {!loading && error && (
        <p className="text-xs text-content-high py-2">{error}</p>
      )}

      {!loading && !error && articles.length === 0 && (
        <p className="text-xs text-muted-foreground py-2">
          No recent news for {ticker}.
        </p>
      )}

      {!loading && !error && articles.length > 0 && (
        <div className="flex-1 overflow-y-auto min-h-0 pr-1 flex flex-col divide-y divide-border/40">
          {articles.map((article, i) => (
            <NewsItemRow key={article.id || i} article={article} fallbackLabel={ticker} />
          ))}
        </div>
      )}
    </Card>
  );
}
