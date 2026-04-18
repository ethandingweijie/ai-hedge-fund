/**
 * LiveSearchPanel — Shows live deep research activity below the progress bar.
 *
 * During Qwen deep research: shows the model's thinking process (reasoning_content)
 * streaming live, similar to how Claude shows its thinking in chat.
 *
 * During Anthropic deep research: shows web search queries and sources (Claude-chat style).
 *
 * Auto-expanded during research, auto-collapsed when complete.
 * Thinking content persisted via liveData (sessionStorage) so it survives SSE reconnects.
 */
import { useState, useEffect, useMemo, useRef } from 'react';
import { ChevronDown, Brain, Search, Globe } from 'lucide-react';
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
  thinking?: string;  // direct prop for reactivity — extracted from liveData in parent
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

export function LiveSearchPanel({ streamEvents, liveData, thinking: thinkingProp, isResearchPhase, isComplete }: LiveSearchPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const thinkingRef = useRef<HTMLDivElement>(null);

  // Auto-collapse when deep research completes
  useEffect(() => {
    if (isComplete) setExpanded(false);
  }, [isComplete]);

  // Auto-expand when deep research starts
  useEffect(() => {
    if (isResearchPhase && !isComplete) setExpanded(true);
  }, [isResearchPhase, isComplete]);

  // Extract thinking content — prefer direct prop (reactive), fallback to liveData
  const thinking = thinkingProp || (liveData.deep_research_thinking as string) || '';

  // Extract "Thinking:" status messages from events for additional context
  const thinkingStatuses = useMemo(() => {
    return streamEvents
      .filter(ev => ev.status?.startsWith('Thinking:') || ev.status?.startsWith('Writing'))
      .map(ev => ev.status)
      .slice(-5); // last 5 status messages
  }, [streamEvents]);

  // Extract search queries from progress events
  const searches = useMemo(() => {
    const queries: SearchQuery[] = [];
    const seen = new Set<string>();
    for (const ev of streamEvents) {
      const lsq = ev.partial_data?.live_search_query as SearchQuery | undefined;
      if (lsq && lsq.query && !seen.has(lsq.query)) {
        seen.add(lsq.query);
        queries.push(lsq);
        continue;
      }
      const match = ev.status?.match(/^Web search (\d+)\/(\d+): (.+)$/);
      if (match && !seen.has(match[3])) {
        seen.add(match[3]);
        queries.push({ index: parseInt(match[1]), total: parseInt(match[2]), query: match[3] });
      }
    }
    return queries;
  }, [streamEvents]);

  // Extract sources from liveData
  const sources = useMemo(() => {
    const raw = liveData.live_search_sources as SearchSource[] | undefined;
    if (!raw || !Array.isArray(raw)) return [];
    const seen = new Set<string>();
    return raw.filter(s => {
      if (seen.has(s.url)) return false;
      seen.add(s.url);
      return true;
    });
  }, [liveData]);

  // Auto-scroll thinking to bottom
  useEffect(() => {
    if (thinkingRef.current && thinking) {
      thinkingRef.current.scrollTop = thinkingRef.current.scrollHeight;
    }
  }, [thinking]);

  // Determine mode: thinking, searching, or nothing
  const hasThinking = thinking.length > 0 || thinkingStatuses.length > 0;
  const hasSearches = searches.length > 0;
  const isWriting = streamEvents.some(ev => ev.status?.startsWith('Writing'));

  // Don't render if nothing to show
  if (!hasThinking && !hasSearches && !isResearchPhase && sources.length === 0) return null;

  const headerText = isComplete
    ? `Deep research complete${hasThinking ? ` (${thinking.length.toLocaleString()} chars reasoning)` : hasSearches ? ` (${searches.length} searches)` : ''}`
    : isWriting
      ? 'Writing research report...'
      : hasThinking
        ? 'Thinking through the analysis...'
        : hasSearches
          ? `Searching the web (${searches.length} searches)...`
          : 'Starting deep research...';

  const HeaderIcon = hasThinking ? Brain : Globe;

  return (
    <div className="mx-4 mb-2">
      {/* Header — clickable to toggle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 w-full text-left py-1.5"
      >
        <HeaderIcon size={13} className={`shrink-0 ${hasThinking && !isComplete ? 'text-purple-400 animate-pulse' : 'text-muted-foreground/60'}`} />
        <span className="text-[11px] font-medium text-muted-foreground/80">
          {headerText}
        </span>
        <ChevronDown
          size={12}
          className={`text-muted-foreground/50 transition-transform duration-200 ml-auto ${expanded ? '' : '-rotate-90'}`}
        />
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="pb-2">
          {/* ── Thinking content (Qwen reasoning) ────────────────────── */}
          {hasThinking && (
            <div
              ref={thinkingRef}
              className="max-h-[200px] overflow-y-auto rounded-lg bg-purple-500/5 border border-purple-500/10 px-3 py-2 mb-2"
            >
              {thinking ? (
                <p className="text-[11px] text-purple-300/80 leading-relaxed whitespace-pre-wrap font-mono">
                  {thinking}
                  {!isComplete && !isWriting && (
                    <span className="inline-block w-1.5 h-3 bg-purple-400 animate-pulse ml-0.5 align-middle" />
                  )}
                </p>
              ) : (
                /* Show status-based thinking when no raw reasoning available */
                thinkingStatuses.map((s, i) => (
                  <p key={i} className="text-[11px] text-purple-300/60 leading-relaxed">
                    {s}
                  </p>
                ))
              )}
            </div>
          )}

          {/* ── Search queries (Anthropic web search) ────────────────── */}
          {hasSearches && (
            <div className="space-y-0.5 mb-1">
              {searches.map((sq, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <Search size={11} className="text-blue-400/70 shrink-0" />
                  <span className="text-[11px] text-foreground/80 font-medium">{sq.query}</span>
                </div>
              ))}
            </div>
          )}

          {/* ── Sources ──────────────────────────────────────────────── */}
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

          {/* ── Loading shimmer ──────────────────────────────────────── */}
          {isResearchPhase && !isComplete && !hasThinking && !hasSearches && (
            <div className="flex items-center gap-1.5 animate-pulse">
              <Brain size={11} className="text-purple-400/40" />
              <div className="h-2.5 w-32 bg-muted/40 rounded" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
