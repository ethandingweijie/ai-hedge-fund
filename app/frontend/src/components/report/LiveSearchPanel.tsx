/**
 * LiveSearchPanel — Claude-chat-style "Searched the web" UI
 * Shows live web search queries and source results during deep research.
 * Auto-expanded during research, auto-collapsed when complete.
 */
import { useState, useEffect, useMemo } from 'react';
import { ChevronDown, Search, Globe } from 'lucide-react';
import type { ProgressEvent } from '@/lib/reportTypes';

interface SearchQuery {
  index: number;
  total: number;
  query: string;
}

interface SearchSource {
  url: string;
  title: string;
}

interface LiveSearchPanelProps {
  streamEvents: ProgressEvent[];
  liveData: Record<string, unknown>;
  isResearchPhase: boolean;
  isComplete: boolean;
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url.split('/')[2]?.replace(/^www\./, '') || '';
  }
}

export function LiveSearchPanel({ streamEvents, liveData, isResearchPhase, isComplete }: LiveSearchPanelProps) {
  const [expanded, setExpanded] = useState(true);

  // Auto-collapse when deep research completes
  useEffect(() => {
    if (isComplete) setExpanded(false);
  }, [isComplete]);

  // Auto-expand when deep research starts
  useEffect(() => {
    if (isResearchPhase && !isComplete) setExpanded(true);
  }, [isResearchPhase, isComplete]);

  // Extract search queries from progress events
  const searches = useMemo(() => {
    const queries: SearchQuery[] = [];
    const seen = new Set<string>();
    for (const ev of streamEvents) {
      if (ev.phase !== 'deep_research_agent') continue;

      // From partial_data (structured)
      const lsq = ev.partial_data?.live_search_query as SearchQuery | undefined;
      if (lsq && lsq.query && !seen.has(lsq.query)) {
        seen.add(lsq.query);
        queries.push(lsq);
      }

      // Fallback: parse from status string "Web search N/M: query"
      if (!lsq) {
        const match = ev.status?.match(/^Web search (\d+)\/(\d+): (.+)$/);
        if (match && !seen.has(match[3])) {
          seen.add(match[3]);
          queries.push({ index: parseInt(match[1]), total: parseInt(match[2]), query: match[3] });
        }
      }
    }
    return queries;
  }, [streamEvents]);

  // Extract sources from liveData
  const sources = useMemo(() => {
    const raw = liveData.live_search_sources as SearchSource[] | undefined;
    if (!raw || !Array.isArray(raw)) return [];
    // Dedupe by URL
    const seen = new Set<string>();
    return raw.filter(s => {
      if (seen.has(s.url)) return false;
      seen.add(s.url);
      return true;
    });
  }, [liveData]);

  // Don't render if no searches detected
  if (searches.length === 0 && !isResearchPhase) return null;

  const searchCount = searches.length;
  const sourceCount = sources.length;

  return (
    <div className="mx-4 mb-2">
      {/* Header — clickable to toggle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 w-full text-left py-1.5"
      >
        <Globe size={13} className="text-muted-foreground/60 shrink-0" />
        <span className="text-[11px] font-medium text-muted-foreground/80">
          {isComplete
            ? `Searched the web (${searchCount} searches, ${sourceCount} sources)`
            : isResearchPhase
              ? `Searching the web${searchCount > 0 ? ` (${searchCount} searches)` : ''}...`
              : `Searched the web`
          }
        </span>
        <ChevronDown
          size={12}
          className={`text-muted-foreground/50 transition-transform duration-200 ml-auto ${expanded ? '' : '-rotate-90'}`}
        />
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="pl-1 pb-2 space-y-2 max-h-[280px] overflow-y-auto">
          {/* Search queries */}
          {searches.map((sq, i) => (
            <div key={i} className="space-y-0.5">
              {/* Query */}
              <div className="flex items-center gap-1.5">
                <Search size={11} className="text-blue-400/70 shrink-0 mt-0.5" />
                <span className="text-[11px] text-foreground/80 font-medium">{sq.query}</span>
              </div>
            </div>
          ))}

          {/* Sources — shown below all queries */}
          {sources.length > 0 && (
            <div className="ml-4 mt-1 space-y-0">
              {sources.map((src, i) => {
                const domain = extractDomain(src.url);
                return (
                  <a
                    key={i}
                    href={src.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 py-1 px-2 rounded hover:bg-muted/40 transition-colors group"
                  >
                    <img
                      src={`https://www.google.com/s2/favicons?domain=${domain}&sz=16`}
                      alt=""
                      className="w-3.5 h-3.5 shrink-0 rounded-sm"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                    />
                    <span className="text-[10px] text-foreground/70 truncate flex-1 group-hover:text-foreground/90">
                      {src.title || domain}
                    </span>
                    <span className="text-[9px] text-muted-foreground/50 shrink-0">
                      {domain}
                    </span>
                  </a>
                );
              })}
            </div>
          )}

          {/* Loading shimmer during active research */}
          {isResearchPhase && !isComplete && (
            <div className="flex items-center gap-1.5 ml-0.5 animate-pulse">
              <Search size={11} className="text-blue-400/40" />
              <div className="h-2.5 w-32 bg-muted/40 rounded" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
