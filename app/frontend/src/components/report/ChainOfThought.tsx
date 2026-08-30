/**
 * ChainOfThought — the live run, as a nested timeline.
 *
 * The pipeline already streams everything this shows: ~177 `update_status()`
 * calls naming real sources ("Resolving SEC EDGAR annual filing", "Fetching
 * macro data (economic indicators + treasury rates)", "Web search 3/12: …"),
 * and `useRunStream` accumulates every one of them. Before this component the
 * UI collapsed all of it into ProgressHeader's single `thinkingDetail` line and
 * threw the rest away, so the user could see *that* deep research was running
 * but not which sources were being read.
 *
 * Three levels:
 *   1. Parent  — pipeline phase, labelled via lib/phaseLabels
 *   2. Child   — one row per status event in that phase
 *   3. Grandchild — individual web searches + discovered sources, which only
 *      deep research emits as structured `partial_data`
 *
 * Two layout rules that are load-bearing, not cosmetic:
 *
 *   • Only ONE parent is expanded at a time (the active one). Completed parents
 *     collapse to a single summary row. This bounds the height structurally
 *     rather than by luck, which matters because a run emits ~177 events.
 *   • The panel scrolls INSIDE itself (max-height + overflow-y-auto) and holds a
 *     stable min-height. Directly below this component sits the stock chart on
 *     both paths, and an unbounded list would shove it down the page as events
 *     arrived, then jitter it on every new row.
 *
 * Colour is blue/black/white/grey only — cobalt `--brand` marks the single
 * active node, everything else is the monochrome content ramp. No green/red
 * (reserved for price change) and no amber.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ProgressEvent } from '@/lib/reportTypes';
import { PHASE_LABELS, PHASE_ORDER, canonicalPhase, refinePhaseLabel } from '@/lib/phaseLabels';
import { buildPhaseRecord, derivePhaseState } from '@/lib/runProgress';

/* ── Structured payloads only deep research emits ─────────────────────────── */
interface SearchQuery { index: number; total: number; query: string }
interface SearchSource { url: string; title: string }

type PhaseState = 'pending' | 'running' | 'done';

interface PhaseNode {
  phase: string;
  label: string;
  state: PhaseState;
  /** One entry per status event, in arrival order, consecutive repeats removed. */
  steps: string[];
  searches: SearchQuery[];
  sources: SearchSource[];
  thinking: string;
}

/* ── Identifier highlighting ──────────────────────────────────────────────────
   Matches snake_case keys (`net_revenue_retention`) and exchange-qualified
   tickers (`00700.HK`). Deliberately conservative: over-matching would speckle
   ordinary prose with code chips. */
const CODE_TOKEN = /([a-z][a-z0-9]*(?:_[a-z0-9]+)+|\b[A-Z]{1,5}\.[A-Z]{1,3}\b)/g;

function withCode(text: string) {
  const parts = text.split(CODE_TOKEN);
  return parts.map((part, i) =>
    // split() with a capture group puts every captured token at an odd index.
    // Do NOT use CODE_TOKEN.test() here: it is /g and therefore stateful.
    i % 2 === 1 ? (
      <code
        key={i}
        className="rounded-tag bg-surface-2 px-1 py-px text-[0.92em] numeric text-content-high"
      >
        {part}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url.split('/')[2]?.replace(/^www\./, '') || url;
  }
}

/* ── Glyphs ───────────────────────────────────────────────────────────────── */
function Spinner({ size = 11 }: { size?: number }) {
  return (
    <span
      className="inline-block shrink-0 rounded-full border-2 border-brand border-t-transparent animate-spin"
      style={{ width: size, height: size }}
      aria-hidden="true"
    />
  );
}

function StateGlyph({ state }: { state: PhaseState }) {
  if (state === 'running') return <Spinner />;
  return (
    <span
      aria-hidden="true"
      className={`inline-block shrink-0 rounded-full ${
        state === 'done'
          ? 'bg-content-high'
          : 'border border-[var(--hairline)] bg-transparent'
      }`}
      style={{ width: 9, height: 9, marginLeft: 1, marginRight: 1 }}
    />
  );
}

/* ── Event stream → phase nodes ───────────────────────────────────────────── */
/** Exported for direct testing of the reconnect/persistence behaviour. */
export function buildNodes(
  events: ProgressEvent[],
  liveData: Record<string, unknown>,
  phaseMap: Record<string, ProgressEvent>,
  archived: boolean,
): PhaseNode[] {
  const byPhase = new Map<string, ProgressEvent[]>();
  const seenOrder: string[] = [];

  for (const ev of events) {
    if (!ev?.phase) continue;
    const key = canonicalPhase(ev.phase);
    if (!byPhase.has(key)) {
      byPhase.set(key, []);
      seenOrder.push(key);
    }
    byPhase.get(key)!.push(ev);
  }

  // Durable record + order-aware completion live in lib/runProgress so the
  // ProgressHeader derives the identical current phase. Critically, several
  // agents (scenario_agent among them) never emit a terminal status, so a
  // phase is also complete once a later one has settled -- otherwise it spins
  // for the rest of the run.
  const lastByPhase = buildPhaseRecord(events, phaseMap);
  const { activePhase, donePhases } = derivePhaseState(lastByPhase);

  // Known phases in pipeline order first, then anything unexpected in the order
  // it actually appeared — so a new backend phase still shows up rather than
  // being silently dropped.
  const ordered = [
    ...PHASE_ORDER.filter((p) => byPhase.has(p) || PHASE_LABELS[p]),
    ...seenOrder.filter((p) => !PHASE_ORDER.includes(p)),
    // Phases known only from phaseMap (their events were evicted).
    ...[...lastByPhase.keys()].filter(
      (p) => !PHASE_ORDER.includes(p) && !seenOrder.includes(p),
    ),
  ];

  return ordered
    // The archived trail is a record of what happened: a phase with no
    // entry in the stored log has nothing to say, so omit it rather than
    // listing an empty row. Live view keeps them as upcoming steps.
    .filter((phase) => !archived || lastByPhase.has(phase))
    .map((phase) => {
    const evs = byPhase.get(phase) ?? [];
    // Durable per-phase record (see lastByPhase above): present whenever the
    // phase has run, even if its events were evicted by the 50-event cap.
    const record = lastByPhase.get(phase);

    // Display text comes from `summary`, NOT `status`. The backend handler
    // (app/backend/services/analysis_service.py) normalises every terminal
    // status to the literal "Done" so the progress bar can count phases
    // uniformly, and keeps the descriptive original in `summary`. Reading
    // `status` here would render a timeline of "Done" rows and throw away
    // exactly the text this component exists to show -- "EDGAR OK: 10-K",
    // "Cache HIT (2.3d old)", "✓ Routing complete".
    const steps: string[] = [];
    for (const ev of evs) {
      const s = (ev.summary ?? '').trim() || (ev.status ?? '').trim();
      if (!s || s === 'Done') continue;
      if (steps[steps.length - 1] === s) continue; // collapse consecutive repeats
      steps.push(s);
    }

    // Reconnect fallback: events evicted, but phaseMap kept the last line.
    if (steps.length === 0 && record) {
      const surviving = (record.summary ?? '').trim() || (record.status ?? '').trim();
      if (surviving && surviving !== 'Done') steps.push(surviving);
    }

    // Deep research is the only phase emitting structured children.
    const searches: SearchQuery[] = [];
    const seenQ = new Set<string>();
    let sources: SearchSource[] = [];
    for (const ev of evs) {
      const q = ev.partial_data?.live_search_query as SearchQuery | undefined;
      if (q?.query && !seenQ.has(q.query)) {
        seenQ.add(q.query);
        searches.push(q);
      }
      const s = ev.partial_data?.live_search_sources as SearchSource[] | undefined;
      if (Array.isArray(s)) sources = s;
    }
    if (phase === 'deep_research' && sources.length === 0) {
      const fromLive = liveData.live_search_sources as SearchSource[] | undefined;
      if (Array.isArray(fromLive)) sources = fromLive;
    }
    const dedupSources: SearchSource[] = [];
    const seenU = new Set<string>();
    for (const s of sources) {
      if (!s?.url || seenU.has(s.url)) continue;
      seenU.add(s.url);
      dedupSources.push(s);
    }

    const thinking =
      phase === 'deep_research' ? ((liveData.deep_research_thinking as string) ?? '') : '';

    // State comes from the durable record, so an evicted early phase still
    // reads as done rather than regressing to pending after a reconnect.
    let state: PhaseState = 'pending';
    if (record) {
      state = donePhases.has(phase) ? 'done' : phase === activePhase ? 'running' : 'done';
    }

    return {
      phase,
      // Venue-aware: the filings phase is named after the registry it
      // actually reached (SEC / HKEX / SGX), not its module name.
      //
      // Pending phases take the present-tense copy too: passing `false` here
      // would render the COMPLETED wording ("Financial data ready") for a step
      // that has not run, which reads as a false claim.
      label: refinePhaseLabel(phase, steps, state !== 'done'),
      state,
      steps,
      searches,
      sources: dedupSources,
      thinking,
    };
  });
}

/* ── Component ────────────────────────────────────────────────────────────── */
export function ChainOfThought({
  events,
  phaseMap = {},
  liveData = {},
  isComplete = false,
  className = '',
  /** Read-only trail on a finished report: starts collapsed, never auto-expands. */
  archived = false,
}: {
  events: ProgressEvent[];
  /** Durable per-phase record; survives reconnect where `events` is truncated. */
  phaseMap?: Record<string, ProgressEvent>;
  liveData?: Record<string, unknown>;
  isComplete?: boolean;
  className?: string;
  archived?: boolean;
}) {
  const nodes = useMemo(
    () => buildNodes(events, liveData, phaseMap, archived),
    [events, liveData, phaseMap, archived],
  );
  const activePhase = nodes.find((n) => n.state === 'running')?.phase ?? null;

  // `pinned` is the user's explicit choice and overrides the active phase.
  // null (the default) means "follow the run".
  // null = follow the run; COLLAPSED = user closed every node.
  const COLLAPSED = '__collapsed__';
  const [pinned, setPinned] = useState<string | null>(null);
  const [trailOpen, setTrailOpen] = useState(false);
  const activeRef = useRef<HTMLDivElement>(null);

  const collapsedAll = archived ? !trailOpen : isComplete && pinned === null;
  const expandedPhase = collapsedAll ? null : (pinned ?? activePhase);

  // Keep the working node in view without scrolling the page itself.
  useEffect(() => {
    if (activeRef.current && !collapsedAll) {
      activeRef.current.scrollIntoView({ block: 'nearest' });
    }
  }, [events.length, expandedPhase, collapsedAll]);

  const done = nodes.filter((n) => n.state === 'done').length;
  const total = nodes.length;

  if (nodes.length === 0) return null;

  // Finished run (or archived trail): one summary row until asked to open.
  if (collapsedAll) {
    const dr = nodes.find((n) => n.phase === 'deep_research');
    const bits = [
      `${done}/${total} steps`,
      dr && dr.searches.length > 0 ? `${dr.searches.length} searches` : null,
      dr && dr.sources.length > 0 ? `${dr.sources.length} sources` : null,
    ].filter(Boolean);
    return (
      <div className={className}>
        <button
          onClick={() => (archived ? setTrailOpen(true) : setPinned(nodes[nodes.length - 1].phase))}
          className="w-full rounded-control border border-[var(--hairline)] bg-surface-2 px-3 py-2 flex items-center gap-2 text-left hover:bg-surface-2-hover transition-colors"
        >
          <StateGlyph state="done" />
          <span className="text-[12px] font-medium text-content-high">
            How this was researched
          </span>
          <span className="text-[11px] text-content-muted ml-auto numeric">
            {bits.join(' · ')}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className={className}>
      <div
        className="rounded-control border border-[var(--hairline)] bg-surface-2 overflow-y-auto max-h-[280px] md:max-h-[320px] min-h-[132px]"
        role="log"
        aria-live="polite"
        aria-label="Analysis chain of thought"
      >
        <ul className="py-1.5">
          {nodes.map((node) => {
            const open = node.phase === expandedPhase;
            const isActive = node.state === 'running';
            return (
              <li key={node.phase}>
                {/* ── Parent row ───────────────────────────────────────────── */}
                <div ref={isActive ? activeRef : undefined}>
                  <button
                    onClick={() => setPinned(open ? COLLAPSED : node.phase)}
                    disabled={node.state === 'pending'}
                    className="w-full px-3 py-1.5 flex items-center gap-2 text-left disabled:cursor-default hover:bg-surface-2-hover disabled:hover:bg-transparent transition-colors"
                  >
                    <StateGlyph state={node.state} />
                    <span
                      className={`text-[12px] truncate ${
                        isActive
                          ? 'text-brand font-medium'
                          : node.state === 'done'
                            ? 'text-content-high'
                            : 'text-content-disabled'
                      }`}
                    >
                      {node.label}
                    </span>
                    {!open && node.state === 'done' && node.steps.length > 0 && (
                      <span className="ml-auto shrink-0 text-[10.5px] text-content-muted numeric">
                        {node.phase === 'deep_research' && node.searches.length > 0
                          ? `${node.searches.length} searches · ${node.sources.length} sources`
                          : `${node.steps.length} step${node.steps.length === 1 ? '' : 's'}`}
                      </span>
                    )}
                  </button>
                </div>

                {/* ── Children ─────────────────────────────────────────────── */}
                {open && (
                  <div className="ml-[17px] border-l border-[var(--hairline)] pl-3 pb-1.5">
                    {node.steps.length === 0 && (
                      <p className="text-[11px] text-content-muted py-1">
                        No detail reported for this step.
                      </p>
                    )}
                    {node.steps.map((s, i) => (
                      <p
                        key={i}
                        className={`text-[11px] leading-snug py-[3px] ${
                          i === node.steps.length - 1 && isActive
                            ? 'text-content-high'
                            : 'text-content-muted'
                        }`}
                      >
                        {withCode(s)}
                      </p>
                    ))}

                    {/* Level 3 — deep research only */}
                    {node.thinking && (
                      <div className="mt-1.5">
                        <p className="text-[10.5px] font-medium text-content-high mb-0.5">
                          Thinking
                        </p>
                        <p className="text-[11px] leading-snug text-content-muted line-clamp-4">
                          {node.thinking.slice(-600)}
                        </p>
                      </div>
                    )}

                    {node.searches.length > 0 && (
                      <div className="mt-1.5">
                        <p className="text-[10.5px] font-medium text-content-high mb-0.5">
                          Searching the web
                          <span className="ml-1.5 text-content-muted numeric font-normal">
                            {node.searches.length}
                            {node.searches[0]?.total ? `/${node.searches[0].total}` : ''}
                          </span>
                        </p>
                        <ul className="space-y-[3px]">
                          {node.searches.slice(-6).map((q, i) => (
                            <li
                              key={`${q.query}-${i}`}
                              className="text-[11px] text-content-medium leading-snug flex gap-1.5"
                            >
                              <span className="text-content-disabled shrink-0">›</span>
                              <span className="truncate">{q.query}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {node.sources.length > 0 && (
                      <div className="mt-1.5">
                        <p className="text-[10.5px] font-medium text-content-high mb-0.5">
                          Sources found
                          <span className="ml-1.5 text-content-muted numeric font-normal">
                            {node.sources.length}
                          </span>
                        </p>
                        <p className="text-[11px] text-content-muted leading-snug">
                          {[...new Set(node.sources.map((s) => extractDomain(s.url)))]
                            .slice(0, 8)
                            .join(' · ')}
                          {node.sources.length > 8 ? ` · +${node.sources.length - 8}` : ''}
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
