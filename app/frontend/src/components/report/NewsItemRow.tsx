/**
 * NewsItemRow — one headline, shared by the report panel and the news page.
 *
 * Extracted rather than duplicated because the report card and the standalone
 * feed must not drift into two different news idioms; this repo already has a
 * card that exists on desktop and not on mobile, which is the same class of
 * problem one step earlier.
 *
 * Colour: monochrome throughout. Green and red are reserved for price change
 * (see lib/semanticColors.ts), and a news feed has no price in it.
 */

import { timeAgo } from '@/lib/utils';
import type { NewsArticle } from '@/lib/api';

function cleanDomain(site: string): string {
  if (!site) return '';
  return site.replace(/^https?:\/\/(www\.)?/, '').replace(/\/.*$/, '');
}

/** Label for a primary source, or null for the unremarkable case.
 *
 *  Only primary sources are badged: marking every aggregator would put a chip
 *  on almost every row and stop meaning anything.
 *
 *  The `regulatory` tier covers two different things — an exchange or SEC
 *  filing, and a company press release — so the publisher decides the wording.
 *  Labelling "Apple unveils iPhone Duo" from Business Wire as a FILING is
 *  wrong: it is the company speaking, but it is not a regulatory disclosure.
 */
function tierLabel(tier: NewsArticle['tier'], site: string): string | null {
  const s = (site || '').toLowerCase();
  if (/hkexnews|hkex\.com|sec\.gov|sgx\.com/.test(s)) return 'FILING';
  if (tier === 'regulatory') return 'PR';
  if (tier === 'authoritative') return 'WIRE';
  return null;
}

interface NewsItemRowProps {
  article: NewsArticle;
  /** Show which ticker this belongs to — on for the merged feed, off on a
   *  ticker's own panel where it would repeat on every row. */
  showSymbol?: boolean;
  fallbackLabel?: string;
}

export function NewsItemRow({ article, showSymbol = false, fallbackLabel = '' }: NewsItemRowProps) {
  const badge = tierLabel(article.tier, article.site);
  return (
    <a
      href={article.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex gap-3 py-2.5 hover:bg-muted/30 -mx-1 px-1 rounded transition-colors shrink-0"
    >
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
            {(article.site || article.symbol || fallbackLabel).slice(0, 2).toUpperCase()}
          </span>
        </div>
      )}

      <div className="flex flex-col gap-0.5 min-w-0">
        <p className="text-xs font-medium leading-snug line-clamp-2 group-hover:text-primary transition-colors">
          {article.title}
        </p>
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
          {showSymbol && article.symbol && (
            <>
              <span className="font-semibold text-content-high tabular-nums">{article.symbol}</span>
              <span>·</span>
            </>
          )}
          {badge && (
            <>
              <span className="px-1 py-px rounded-sm bg-surface-2 text-content-high border border-[var(--hairline)] tracking-[0.08em]">
                {badge}
              </span>
              <span>·</span>
            </>
          )}
          <span className="font-medium truncate max-w-[110px]">
            {cleanDomain(article.site) || article.site}
          </span>
          <span>·</span>
          <span className="shrink-0">{timeAgo(article.publishedDate)}</span>
        </div>
      </div>
    </a>
  );
}
