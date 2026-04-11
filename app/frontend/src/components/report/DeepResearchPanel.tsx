/**
 * DeepResearchPanel — renders the deep research report with:
 *  1. Inline [n] citation superscripts, each hyperlinking to the references table
 *  2. A structured references table: #, Source, Date, URL (clickable), Verified
 *
 * Requires: react-markdown, remark-gfm, rehype-raw (npm install rehype-raw)
 */

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { Card } from '@/components/ui/card';
import type { CitationRegistryEntry } from '@/lib/reportTypes';

interface DeepResearchPanelProps {
  reportText?: string;
  annotatedText?: string;   // report text with [n] markers inserted
  registry?: CitationRegistryEntry[];
  ticker: string;
}

// ── Pre-process: convert [n] markers → clickable superscript HTML ────────────
// When the citation registry has a URL for ref n, the superscript links directly
// to the external source (one click). Falls back to the in-page anchor (#ref-n).
function injectCitationLinks(text: string, refUrlMap: Map<number, string>): string {
  // Match [n] where n is digits, but NOT markdown links like [text](url)
  return text.replace(/\[(\d+)\](?!\()/g, (_match, n) => {
    const refNum = parseInt(n, 10);
    const url = refUrlMap.get(refNum);
    const href = url || `#ref-${n}`;
    const target = url ? ' target="_blank" rel="noopener noreferrer"' : '';
    return `<sup><a href="${href}"${target} class="citation-link">[${n}]</a></sup>`;
  });
}

// ── Knowledge-base check ─────────────────────────────────────────────────────
function isKnowledgeBase(type?: string, name?: string): boolean {
  const t = type?.toLowerCase() ?? '';
  const n = name?.toLowerCase() ?? '';
  return t === 'knowledge_base' || t === 'knowledge base' || n.includes('knowledge base');
}

// ── Trusted-source inference — mirrors CitationPanel.isVerified ───────────────
// Returns true when source type or name identifies a known primary source even
// when the backend did not explicitly set verified=true.
function isTrustedSource(type?: string, name?: string, url?: string): boolean {
  if (isKnowledgeBase(type, name)) return true;
  const txt = `${type ?? ''} ${name ?? ''} ${url ?? ''}`.toLowerCase();
  return (
    txt.includes('sec_filing') ||
    txt.includes('sec.gov')    ||
    txt.includes('edgar')      ||
    txt.includes('10-k')       ||
    txt.includes('20-f')       ||
    txt.includes('f-1')        ||
    txt.includes('annual report') ||
    txt.includes('press_release') ||
    txt.includes('press release') ||
    txt.includes('earnings release') ||
    txt.includes('financial_data') ||
    txt.includes('research report') ||
    txt.includes('analyst report')
  );
}

// ── Source type badge color ───────────────────────────────────────────────────
function sourceColor(type?: string, name?: string): string {
  if (isKnowledgeBase(type, name))
    return 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300';
  switch (type?.toLowerCase()) {
    case 'sec_filing':
    case '20-f':
    case '10-k':
      return 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300';
    case 'press_release':
      return 'bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300';
    case 'web':
    case 'news':
      return 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300';
    case 'financial_data':
    case 'fmp':
      return 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300';
    default:
      return 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300';
  }
}

// ── Format source type label ──────────────────────────────────────────────────
function sourceLabel(type?: string, name?: string): string {
  if (isKnowledgeBase(type, name)) return 'Equitable AI knowledge base';
  if (name) return name;
  switch (type?.toLowerCase()) {
    case 'sec_filing': return 'SEC Filing';
    case '20-f':       return 'Form 20-F';
    case '10-k':       return 'Form 10-K';
    case 'press_release': return 'Press Release';
    case 'web':        return 'Web';
    case 'news':       return 'News';
    case 'financial_data': return 'Financial Data';
    default:           return type ?? 'Unknown';
  }
}

// ── Type badge label ──────────────────────────────────────────────────────────
function typeBadgeLabel(type?: string, name?: string): string {
  if (isKnowledgeBase(type, name)) return 'Equitable AI KB';
  return (type ?? '').toUpperCase().replace('_', ' ');
}

export function DeepResearchPanel({
  reportText,
  annotatedText,
  registry,
  ticker,
}: DeepResearchPanelProps) {
  const text = annotatedText || reportText;

  // Build ref → URL map for direct external linking from [n] superscripts
  const refUrlMap = new Map<number, string>();
  (registry ?? []).forEach(e => {
    if (e.ref_id != null && e.url) refUrlMap.set(e.ref_id, e.url);
  });

  if (!text) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">
          Deep research report not available for {ticker}.
        </p>
      </Card>
    );
  }

  // Pre-process: inject hyperlinked superscripts with direct external URLs
  const processedHtml = injectCitationLinks(text, refUrlMap);

  // Deduplicate registry entries by ref_id
  const entries: CitationRegistryEntry[] = registry
    ? [...registry].sort((a, b) => (a.ref_id ?? 0) - (b.ref_id ?? 0))
    : [];

  return (
    <div className="space-y-6">
      {/* ── Report body ──────────────────────────────────────────────────── */}
      <Card className="p-6">
        <div className="flex items-center gap-2 mb-4">
          <h3 className="text-sm font-semibold">Deep Research Report — {ticker}</h3>
          {entries.length > 0 && (
            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300">
              {entries.length} citation{entries.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Inject citation CSS inline — targets .citation-link */}
        <style>{`
          .deep-research-body .citation-link {
            color: #3b82f6;
            text-decoration: none;
            font-size: 0.7em;
            font-weight: 600;
            vertical-align: super;
            line-height: 0;
          }
          .deep-research-body .citation-link:hover {
            text-decoration: underline;
          }
          .deep-research-body h1 { font-size: 1.25rem; font-weight: 700; margin: 1rem 0 0.5rem; }
          .deep-research-body h2 { font-size: 1.1rem;  font-weight: 700; margin: 1rem 0 0.5rem; }
          .deep-research-body h3 { font-size: 1rem;    font-weight: 600; margin: 0.75rem 0 0.4rem; }
          .deep-research-body h4 { font-size: 0.9rem;  font-weight: 600; margin: 0.6rem 0 0.3rem; }
          .deep-research-body p  { margin-bottom: 0.6rem; line-height: 1.65; font-size: 0.875rem; }
          .deep-research-body ul { list-style: disc;   padding-left: 1.4rem; margin-bottom: 0.6rem; }
          .deep-research-body ol { list-style: decimal; padding-left: 1.4rem; margin-bottom: 0.6rem; }
          .deep-research-body li { margin-bottom: 0.25rem; font-size: 0.875rem; line-height: 1.55; }
          .deep-research-body strong { font-weight: 600; }
          .deep-research-body em { font-style: italic; }
          .deep-research-body table { width: 100%; border-collapse: collapse; margin-bottom: 1rem; font-size: 0.8rem; }
          .deep-research-body th { background: hsl(var(--muted)); text-align: left; padding: 6px 10px; font-weight: 600; border-bottom: 1px solid hsl(var(--border)); }
          .deep-research-body td { padding: 5px 10px; border-bottom: 1px solid hsl(var(--border)/0.5); vertical-align: top; }
          .deep-research-body blockquote { border-left: 3px solid hsl(var(--border)); padding-left: 1rem; margin: 0.5rem 0; color: hsl(var(--muted-foreground)); font-style: italic; }
          .deep-research-body code { font-family: monospace; font-size: 0.8rem; background: hsl(var(--muted)); padding: 1px 4px; border-radius: 3px; }
          .deep-research-body pre  { background: hsl(var(--muted)); padding: 0.75rem 1rem; border-radius: 6px; overflow-x: auto; margin-bottom: 0.75rem; }
          .deep-research-body pre code { background: none; padding: 0; }
          .deep-research-body hr { border: none; border-top: 1px solid hsl(var(--border)); margin: 1.25rem 0; }
        `}</style>

        <div className="deep-research-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeRaw]}
          >
            {processedHtml}
          </ReactMarkdown>
        </div>
      </Card>

      {/* ── References table ─────────────────────────────────────────────── */}
      {entries.length > 0 && (() => {
        const hasKB = entries.some(e => isKnowledgeBase(e.source_type, e.source_name));
        return (
        <Card className="p-4">
          <h3 className="text-sm font-semibold mb-3">References</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 pr-3 w-8 text-muted-foreground font-semibold">#</th>
                  <th className="text-left py-2 pr-3 text-muted-foreground font-semibold">Source</th>
                  <th className="text-left py-2 pr-3 w-24 text-muted-foreground font-semibold">Type</th>
                  <th className="text-left py-2 pr-3 w-24 text-muted-foreground font-semibold">Date</th>
                  <th className="text-left py-2 pr-3 text-muted-foreground font-semibold">Claim / Quote</th>
                  <th className="text-left py-2 w-16 text-muted-foreground font-semibold">Status</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr
                    key={entry.ref_id}
                    id={`ref-${entry.ref_id}`}
                    className="border-b border-border/40 hover:bg-muted/30 transition-colors"
                  >
                    {/* # */}
                    <td className="py-2 pr-3 align-top font-mono text-muted-foreground">
                      [{entry.ref_id}]
                    </td>

                    {/* Source name + URL */}
                    <td className="py-2 pr-3 align-top max-w-[220px]">
                      {entry.url ? (
                        <a
                          href={entry.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-500 hover:underline break-all leading-tight block"
                          title={entry.url}
                        >
                          {sourceLabel(entry.source_type, entry.source_name)}
                          {isKnowledgeBase(entry.source_type, entry.source_name) && (
                            <sup className="text-indigo-500 font-bold ml-0.5">*</sup>
                          )}
                        </a>
                      ) : (
                        <span className="text-foreground/80">
                          {sourceLabel(entry.source_type, entry.source_name)}
                          {isKnowledgeBase(entry.source_type, entry.source_name) && (
                            <sup className="text-indigo-500 font-bold ml-0.5">*</sup>
                          )}
                        </span>
                      )}
                      {entry.speaker && (
                        <span className="block text-muted-foreground mt-0.5">{entry.speaker}</span>
                      )}
                    </td>

                    {/* Type badge */}
                    <td className="py-2 pr-3 align-top">
                      {entry.source_type && (
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${sourceColor(entry.source_type, entry.source_name)}`}>
                          {typeBadgeLabel(entry.source_type, entry.source_name)}
                        </span>
                      )}
                    </td>

                    {/* Date */}
                    <td className="py-2 pr-3 align-top text-muted-foreground whitespace-nowrap">
                      {entry.date ?? '—'}
                    </td>

                    {/* Claim / Quote */}
                    <td className="py-2 pr-3 align-top max-w-[320px]">
                      {entry.quote ? (
                        <span className="italic text-muted-foreground line-clamp-3 leading-snug">
                          "{entry.quote}"
                        </span>
                      ) : entry.claim ? (
                        <span className="text-foreground/80 line-clamp-3 leading-snug">
                          {entry.claim}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>

                    {/* Verified status — explicit flag OR inferred from trusted source type */}
                    <td className="py-2 align-top">
                      {(entry.verified === true || isTrustedSource(entry.source_type, entry.source_name, entry.url)) ? (
                        <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400 font-medium whitespace-nowrap">
                          <span>✓</span> <span>Verified</span>
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400 whitespace-nowrap">
                          <span>?</span> <span>Unverified</span>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Footnote — shown only when at least one KB entry exists */}
          {hasKB && (
            <p className="mt-3 text-[10px] text-muted-foreground leading-relaxed border-t border-border/40 pt-2">
              <sup className="text-indigo-500 font-bold mr-0.5">*</sup>
              <span className="font-medium text-foreground/60">Equitable AI Knowledge Base</span>
              {' '}is trained on a continual basis from user queries. This knowledge base would have been verified in earlier runs.
            </p>
          )}
        </Card>
        );
      })()}
    </div>
  );
}
