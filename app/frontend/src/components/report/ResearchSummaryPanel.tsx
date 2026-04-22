/**
 * ResearchSummaryPanel
 * --------------------
 * Generates a ~200-word analyst summary from the industry brief + deep research
 * via the backend /analysis/research-summary endpoint (Qwen LLM, cached by run_id).
 * Below the summary, the full source panels are accessible as collapsible accordions
 * — rendered with their original components (tables, layout intact).
 */

import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react';
import { API_BASE_URL } from '@/config';

const API_BASE = API_BASE_URL;

const SECTION_LABELS = ['Industry Structure', 'Corporate Developments', 'Growth Potential', 'Key Risks'];

/**
 * Parse the LLM output into typed blocks.
 * Recognises lines that are a known section label (no bullets, no leading whitespace)
 * and bullet lines that start with "•" or "-".
 */
function parseSummary(text: string): React.ReactNode[] {
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  const blocks: React.ReactNode[] = [];
  let currentSection: string | null = null;
  let bullets: string[] = [];

  const flush = () => {
    if (!currentSection && bullets.length === 0) return;
    blocks.push(
      <div key={currentSection ?? blocks.length} className="flex flex-col gap-1">
        {currentSection && (
          <p className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
            {currentSection}
          </p>
        )}
        {bullets.map((b, i) => (
          <div key={i} className="flex gap-2">
            <span className="mt-0.5 text-primary shrink-0">•</span>
            <span>{b}</span>
          </div>
        ))}
      </div>
    );
    currentSection = null;
    bullets = [];
  };

  for (const line of lines) {
    const cleanLine = line.replace(/^\*+|\*+$/g, '').trim();
    // Detect section header: matches one of the known labels (case-insensitive)
    const isHeader = SECTION_LABELS.some(
      label => cleanLine.toLowerCase().startsWith(label.toLowerCase().split(' ')[0]) &&
               cleanLine.length < 60 && !cleanLine.startsWith('•') && !cleanLine.startsWith('-')
    );
    if (isHeader) {
      flush();
      currentSection = cleanLine.replace(/[-—:].*$/, '').trim();
    } else if (line.startsWith('•') || line.startsWith('-')) {
      bullets.push(cleanLine.replace(/^[•\-]\s*/, ''));
    } else {
      // Plain sentence — treat as a bullet
      bullets.push(cleanLine);
    }
  }
  flush();
  return blocks;
}

interface Props {
  runId: string;
  ticker: string;
  industryBrief?: string;
  deepResearch?: string;
  /** Pre-rendered panel components for the dropdowns (retains original formatting). */
  industryBriefContent?: React.ReactNode;
  deepResearchContent?: React.ReactNode;
}

function Accordion({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-foreground hover:bg-muted/50 transition-colors"
      >
        <span>{title}</span>
        {open
          ? <ChevronUp size={15} className="text-muted-foreground" />
          : <ChevronDown size={15} className="text-muted-foreground" />}
      </button>
      {open && (
        <div className="border-t border-border">
          {children}
        </div>
      )}
    </div>
  );
}

export function ResearchSummaryPanel({
  runId, ticker,
  industryBrief, deepResearch,
  industryBriefContent, deepResearchContent,
}: Props) {
  const [summary, setSummary] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  // Reset state when runId changes (navigating between reports)
  useEffect(() => {
    setSummary(null);
    setError(null);
  }, [runId]);

  useEffect(() => {
    if (!industryBrief && !deepResearch) return;
    if (summary) return; // already fetched for this runId
    // Mid-run streaming: runId is empty until event: complete. In that case
    // still RENDER the research/brief accordion content (from props), but
    // skip the AI summary fetch — it'll fire later when runId arrives.
    if (!runId) return;

    // ── Cross-contamination guard (frontend-side) ─────────────────────
    // During navigation transitions, React may render with stale props
    // from the previous ticker. Check that the content actually mentions
    // the current ticker before calling the API — prevents caching NEE's
    // research summary under V's run_id.
    const contentSample = ((industryBrief ?? '') + ' ' + (deepResearch ?? '')).slice(0, 3000).toUpperCase();
    if (ticker && !contentSample.includes(ticker.toUpperCase())) {
      // Content is stale (from previous ticker) — skip, will re-fire when correct props arrive
      return;
    }

    let cancelled = false;
    setLoading(true);
    fetch(`${API_BASE}/analysis/research-summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        run_id: runId,
        ticker,
        industry_brief: industryBrief ?? '',
        deep_research: deepResearch ?? '',
      }),
    })
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail ?? 'Error')))
      .then(d => { if (!cancelled) setSummary(d.summary); })
      .catch(e => { if (!cancelled) setError(typeof e === 'string' ? e : 'Failed to generate summary'); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [runId, ticker, industryBrief, deepResearch]);

  if (!industryBrief && !deepResearch) return null;

  return (
    <div className="flex flex-col gap-3">
      {/* ── AI Summary card ── */}
      <div className="rounded-2xl border border-border bg-card p-5">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={15} className="text-amber-500" />
          <span className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
            AI Research Summary
          </span>
          <span className="ml-auto text-[10px] text-muted-foreground/50 font-mono">{ticker}</span>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <div className="w-3 h-3 rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground animate-spin" />
            Generating analyst summary…
          </div>
        )}

        {error && (
          <p className="text-sm text-red-500">{error}</p>
        )}

        {summary && (
          <div className="flex flex-col gap-3 text-sm leading-relaxed text-foreground">
            {parseSummary(summary)}
          </div>
        )}
      </div>

      {/* ── Collapsible source panels — original formatted components ── */}
      {(industryBriefContent || industryBrief) && (
        <Accordion title="Industry Intelligence Brief">
          {industryBriefContent ?? (
            <div className="px-4 py-3 text-sm text-muted-foreground whitespace-pre-wrap leading-relaxed">
              {industryBrief}
            </div>
          )}
        </Accordion>
      )}
      {(deepResearchContent || deepResearch) && (
        <Accordion title="Deep Research">
          {deepResearchContent ?? (
            <div className="px-4 py-3 text-sm text-muted-foreground whitespace-pre-wrap leading-relaxed">
              {deepResearch}
            </div>
          )}
        </Accordion>
      )}
    </div>
  );
}
