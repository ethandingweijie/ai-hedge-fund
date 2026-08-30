/**
 * Shared "where is the run right now" derivation.
 *
 * ProgressHeader and ChainOfThought were each deciding this for themselves and
 * disagreeing on screen: the header would name one phase while the timeline
 * showed another running. They now both call `derivePhaseState`.
 *
 * Two things make this harder than "the newest event wins":
 *
 * 1. **Not every agent emits a terminal status.** `scenario_agent` only ever
 *    emits "Building scenarios" — there is no closing ✓ — so a naive
 *    last-event-not-settled test leaves it spinning for the rest of the run,
 *    even once later phases have finished. The fix is order-aware: a phase is
 *    also complete once a phase further down PHASE_ORDER has settled.
 *
 * 2. **Some statuses are attributed to the handing-off phase.** `data_router`
 *    emits "Starting deep research (Phase 3.5)", so naming the current step
 *    from that event's text alone reads as if deep research were the active
 *    phase while the label says Data Router. The phase key, not the prose,
 *    decides the label.
 */
import type { ProgressEvent } from '@/lib/reportTypes';
import { PHASE_ORDER, canonicalPhase } from '@/lib/phaseLabels';

/** Completion wording for rows predating status normalisation. */
export const SETTLED_RE = /^✓|\bcomplete\b|\bready\b|\bskipp?(ed|ing)\b/i;

/**
 * The backend handler normalises terminal statuses to the literal "Done" and
 * keeps the descriptive original in `summary`, so `status` is the reliable
 * completion signal and `summary` is the reliable display text.
 */
export function isSettledEvent(ev?: ProgressEvent | null): boolean {
  if (!ev) return false;
  const raw = (ev.status ?? '').trim();
  if (raw === 'Done') return true;
  return SETTLED_RE.test((ev.summary ?? '').trim() || raw);
}

/**
 * Durable per-phase record: phaseMap first (it survives a reconnect where the
 * event log is truncated), then live events, which are newer by construction.
 */
export function buildPhaseRecord(
  events: ProgressEvent[],
  phaseMap: Record<string, ProgressEvent> = {},
): Map<string, ProgressEvent> {
  const rec = new Map<string, ProgressEvent>();
  for (const ev of Object.values(phaseMap ?? {})) {
    if (ev?.phase) rec.set(canonicalPhase(ev.phase), ev);
  }
  for (const ev of events ?? []) {
    if (ev?.phase) rec.set(canonicalPhase(ev.phase), ev);
  }
  return rec;
}

export interface PhaseStateResult {
  /** Phase currently working, or null when nothing is outstanding. */
  activePhase: string | null;
  /** Phases considered finished, including those that never emitted a ✓. */
  donePhases: Set<string>;
}

export function derivePhaseState(record: Map<string, ProgressEvent>): PhaseStateResult {
  const idx = (p: string) => {
    const i = PHASE_ORDER.indexOf(p);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };

  // The furthest-along phase that has actually settled. Anything ordered before
  // it is finished too, whether or not it bothered to say so.
  let maxSettledIdx = -1;
  for (const [phase, ev] of record) {
    if (isSettledEvent(ev)) maxSettledIdx = Math.max(maxSettledIdx, idx(phase));
  }

  const donePhases = new Set<string>();
  for (const [phase, ev] of record) {
    if (isSettledEvent(ev) || idx(phase) < maxSettledIdx) donePhases.add(phase);
  }

  // Active = the outstanding phase with the newest timestamp.
  let activePhase: string | null = null;
  let activeTs = '';
  for (const [phase, ev] of record) {
    if (donePhases.has(phase)) continue;
    const ts = ev.timestamp ?? '';
    if (activePhase === null || ts >= activeTs) {
      activePhase = phase;
      activeTs = ts;
    }
  }

  return { activePhase, donePhases };
}
