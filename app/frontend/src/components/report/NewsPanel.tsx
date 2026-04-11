/**
 * NewsPanel — latest news articles for the ticker, sourced from FMP.
 * Displayed in the right column beneath the stock chart.
 */

import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { getCompanyNews, type NewsArticle } from '@/lib/api';

interface NewsPanelProps {
  ticker: string;
}

function timeAgo(dateStr: string): string {
  if (!dateStr) return '';
  try {
    const ms = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(ms / 60_000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 30) return `${days}d ago`;
    return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

function cleanDomain(site: string): string {
  if (!site) return '';
  return site.replace(/^https?:\/\/(www\.)?/, '').replace(/\/.*$/, '');
}

export function NewsPanel({ ticker }: NewsPanelProps) {
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getCompanyNews(ticker, 8)
      .then(d => setArticles(d.articles))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [ticker]);

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
        <p className="text-xs text-red-500 py-2">{error}</p>
      )}

      {!loading && !error && articles.length === 0 && (
        <p className="text-xs text-muted-foreground py-2">No recent news found.</p>
      )}

      {!loading && !error && articles.length > 0 && (
        <div className="flex-1 overflow-y-auto min-h-0 pr-1 flex flex-col divide-y divide-border/40">
          {articles.map((article, i) => (
            <a
              key={i}
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="group flex gap-3 py-2.5 hover:bg-muted/30 -mx-1 px-1 rounded transition-colors shrink-0"
            >
              {/* Thumbnail */}
              {article.image ? (
                <img
                  src={article.image}
                  alt=""
                  className="w-12 h-12 rounded object-cover shrink-0 bg-muted"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                />
              ) : (
                <div className="w-12 h-12 rounded bg-muted shrink-0 flex items-center justify-center">
                  <span className="text-[10px] text-muted-foreground font-bold">
                    {(article.site || ticker).slice(0, 2).toUpperCase()}
                  </span>
                </div>
              )}

              {/* Content */}
              <div className="flex flex-col gap-0.5 min-w-0">
                <p className="text-xs font-medium leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                  {article.title}
                </p>
                <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                  <span className="font-medium truncate max-w-[100px]">
                    {cleanDomain(article.site) || article.site}
                  </span>
                  <span>·</span>
                  <span className="shrink-0">{timeAgo(article.publishedDate)}</span>
                </div>
              </div>
            </a>
          ))}
        </div>
      )}
    </Card>
  );
}
