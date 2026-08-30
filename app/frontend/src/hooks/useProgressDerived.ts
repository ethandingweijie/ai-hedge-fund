/**
 * useProgressDerived — turns a raw `phaseMap` (from useActiveRun's SSE
 * stream) into the two things a progress UI actually needs to show:
 *   • phaseLabel     — short human-readable phase name ("Deep Research")
 *   • thinkingDetail — the live status/summary detail for that phase
 * plus `isResearchPhase`, used to gate the live research-thinking panel.
 *
 * Shared by V2ReportView (mobile) and ReportPage (desktop) so both
 * surfaces derive progress text identically instead of drifting apart.
 */
import { useMemo } from 'react';
import type { ProgressEvent } from '@/lib/reportTypes';
import { buildPhaseRecord, derivePhaseState } from '@/lib/runProgress';

// Short display names for the backend phase keys. Row 1 of ProgressHeader
// stays on a single line alongside % / Cancel, so these are intentionally
// terser than ReportPage's PHASE_LABELS.running map.
const PHASE_SHORT: Record<string, string> = {
  macro_regime_classifier: 'Macro Regime',
  strategic_router:        'Sector Routing',
  intelligence_agents:     'Intelligence',
  // M2 progress fix: phases 2_8 archive-cache-load + 2_9 freshness delta emit
  // under this key; it now carries a terminal ✓ event so the bar moves through
  // the reuse work instead of freezing at the front-block percentage.
  archive_cache:           'Research Memory',
  data_router:             'Data Router',
  deep_research_agent:     'Deep Research',
  deep_research:           'Deep Research',
  industry_specialist:     'Industry Brief',
  dcf_engine:              'DCF Engine',
  // investor_agents / debate_round rows removed with the committee (M2 E).
  power_law_agent:         'Power Law',
  value_trap_agent:        'Value Trap',
  phase7_complete:         'Wrapping Up',
  advanced_risk_manager:   'Risk Manager',
  portfolio_manager:       'Portfolio Decision',
  pipeline_queued:         'Queued',
};

export function useIsResearchPhase(phaseMap: Record<string, ProgressEvent>): boolean {
  return useMemo(
    () => Object.values(phaseMap).some(p =>
      (p.phase === 'deep_research_agent' || p.phase === 'deep_research') && !p.status?.toLowerCase().match(/done|complete/)
    ),
    [phaseMap],
  );
}

export function useProgressDerived(
  phaseMap: Record<string, ProgressEvent>,
): { phaseLabel?: string; thinkingDetail?: string } {
  return useMemo(() => {
    const events = Object.values(phaseMap);
    if (events.length === 0) return { phaseLabel: undefined, thinkingDetail: undefined };

    // Shared with ChainOfThought (lib/runProgress) so the header and the
    // timeline never name different current phases. Object.values(phaseMap) is
    // in key-insertion order, not chronological, so the previous
    // "last non-done entry" pick was effectively arbitrary — and it never
    // finished phases like scenario_agent that emit no terminal status.
    const record = buildPhaseRecord([], phaseMap);
    const { activePhase: activeKey } = derivePhaseState(record);
    const activePhase = (activeKey && record.get(activeKey)) || events[events.length - 1];
    const phaseKey = activeKey ?? activePhase.phase;

    const phaseLabel = PHASE_SHORT[phaseKey] ?? phaseKey.replace(/_/g, ' ');

    // Detail text: prefer summary (longer, richer), then status. Skip if
    // it matches phase label (avoids redundant echo).
    const raw = (activePhase.summary || activePhase.status || '').trim();
    const thinkingDetail = raw && raw.toLowerCase() !== phaseLabel.toLowerCase()
      ? raw : undefined;

    return { phaseLabel, thinkingDetail };
  }, [phaseMap]);
}
