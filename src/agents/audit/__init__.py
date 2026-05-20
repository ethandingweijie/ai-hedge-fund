"""Card QA Agent — self-learning frontend card quality system.

See plan: C:/Users/ethan/.claude/plans/mighty-gliding-graham.md

Layer A (this module) — per-run LLM-as-judge that audits the cards
visible on each ticker's report, flags missing mandatory data, and
auto-remediates via hinted re-extraction. Runs as Phase 10.5 in
src/pipeline.py (added in Phase 6 of the rollout).

Layer B — pattern_analysis.py + pattern_alerts.py — aggregates Layer A's
audits over time to surface recurring failures for engineering fixes.

Phase 1 scope: foundation + one card (biopharma_pipeline_rnpv) end-to-end,
no LLM yet. See card_qa_agent.py for the orchestrator.
"""
