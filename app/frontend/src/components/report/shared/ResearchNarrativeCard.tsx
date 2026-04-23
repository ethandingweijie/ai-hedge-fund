/**
 * ResearchNarrativeCard
 * ----------------------
 * Reusable panel that extracts a specific subsection from a deep-research
 * Section 2 block and renders it as flowing narrative text.
 *
 * Purpose: offload sector-specific narrative asks (LOE exposure, defensive
 * strategy, credit cycle commentary, AI capex commentary, etc.) from structured
 * extraction to direct display of the LLM-written research text. Avoids
 * backend schema extensions for fields that are better read as prose than
 * parsed into numbers.
 *
 * Graceful degradation: component returns null when:
 *   - sectionText is null / empty (research didn't run or extractor missed it)
 *   - subsection can't be found inside the section text
 *   - extracted text is shorter than minLength (likely spurious match)
 *
 * Usage examples:
 *   <ResearchNarrativeCard
 *     title="Patent Cliff / Legacy Decline"
 *     sectionText={data.deep_research_sections?.["2f"]}
 *     subsection="2F.3"
 *     sourceLabel="Deep research · Section 2F.3"
 *   />
 *
 *   <ResearchNarrativeCard
 *     title="Industry Cycle Position"
 *     sectionText={data.deep_research_sections?.["2d"]}
 *     sourceLabel="Deep research · Section 2D"
 *   />  // no subsection — shows entire 2D
 */

interface ResearchNarrativeCardProps {
  /** Card title shown in the uppercase tracked-widest heading */
  title: string;
  /** Raw Section 2X text (e.g. state.data.deep_research_sections["2f"]) */
  sectionText?: string | null;
  /** Optional subsection key like "2F.3" — extracts just that paragraph.
      When omitted, shows the entire sectionText. */
  subsection?: string;
  /** Small footer label citing the source */
  sourceLabel?: string;
  /** Minimum length of extracted text to render (prevents spurious matches) */
  minLength?: number;
  /** Max length to render before truncation with "…read more" hint */
  maxLength?: number;
  /** Icon emoji shown in the title (optional) */
  icon?: string;
}

/**
 * Extract a specific subsection heading (e.g. "2F.3") from a larger Section
 * text. Heading boundaries: matches the requested subsection through to either
 * the next sibling subsection (e.g. "2F.4") or the end of the section.
 *
 * Handles variations the LLM might write:
 *   - "2F.3 LOE / PATENT CLIFF"
 *   - "2F.3. LOE / PATENT CLIFF"
 *   - "2F.3: LOE / PATENT CLIFF"
 *   - "**2F.3** LOE / PATENT CLIFF" (markdown bold)
 */
function extractSubsection(sectionText: string, subsection: string): string | null {
  if (!sectionText || !subsection) return null;

  // Parse "2F.3" → letter="F", numeric="3"
  const match = subsection.match(/^(\d+)?([A-Z])\.?(\d+)$/i);
  if (!match) return null;
  const letter = match[2].toUpperCase();
  const num = parseInt(match[3], 10);
  const nextNum = num + 1;

  // Build a regex that captures the subsection paragraph up to the next sibling
  // or end of text. Flexible about punctuation/markdown around the heading.
  const startPattern = new RegExp(
    `(?:^|\\n)[\\s*#_]*(?:2?)${letter}\\.${num}(?![0-9])[\\s\\.:*_\\-]*`,
    'i'
  );
  const endPattern = new RegExp(
    `\\n[\\s*#_]*(?:2?)${letter}\\.${nextNum}(?![0-9])`,
    'i'
  );

  const startMatch = sectionText.match(startPattern);
  if (!startMatch) return null;
  const startIdx = startMatch.index! + startMatch[0].length;

  const remaining = sectionText.slice(startIdx);
  const endMatch = remaining.match(endPattern);
  const endIdx = endMatch ? endMatch.index! : remaining.length;

  const extracted = remaining.slice(0, endIdx).trim();
  // Strip any leftover markdown or heading artifacts at the start
  return extracted.replace(/^[*#_\s]+/, '').trim();
}

/**
 * Normalize common LLM text artifacts for readable display:
 *   - Collapse 3+ newlines to 2
 *   - Strip markdown bold/italic asterisks that don't render natively
 *   - Trim leading/trailing whitespace
 */
function cleanForDisplay(text: string): string {
  return text
    .replace(/\n{3,}/g, '\n\n')
    .replace(/\*\*([^*]+)\*\*/g, '$1')   // **bold** → bold (inline)
    .replace(/(^|\s)\*([^*\n]+)\*(?=\s|$)/g, '$1$2')   // *italic* → italic
    .trim();
}

export function ResearchNarrativeCard({
  title,
  sectionText,
  subsection,
  sourceLabel,
  minLength = 80,
  maxLength = 1500,
  icon,
}: ResearchNarrativeCardProps) {
  // Step 1 — no text at all → hide
  if (!sectionText || typeof sectionText !== 'string') return null;

  // Step 2 — extract subsection if requested, otherwise use whole section
  let body: string | null = sectionText;
  if (subsection) {
    body = extractSubsection(sectionText, subsection);
  }

  // Step 3 — extraction failed or result too short → hide
  if (!body || body.length < minLength) return null;

  // Step 4 — truncate if very long (rare — Section 2F subsections are
  // typically 200-800 chars, so maxLength 1500 is headroom)
  const truncated = body.length > maxLength;
  const display = cleanForDisplay(
    truncated ? body.slice(0, maxLength).trim() + '…' : body
  );

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-zinc-500 dark:text-zinc-400">
          {icon && <span className="mr-1.5">{icon}</span>}
          {title}
        </p>
        {truncated && (
          <span className="text-[10px] text-zinc-500 dark:text-zinc-400">(excerpt)</span>
        )}
      </div>
      <div className="text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed whitespace-pre-wrap">
        {display}
      </div>
      {sourceLabel && (
        <p className="text-[10px] text-zinc-500 dark:text-zinc-400 mt-3 italic">
          {sourceLabel}
        </p>
      )}
    </div>
  );
}
