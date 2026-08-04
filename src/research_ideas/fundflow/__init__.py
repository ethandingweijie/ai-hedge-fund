"""
src/research_ideas/fundflow/
=============================
Geographic fund-flow screen. Tracks how money is rotating across the key
regional equity ETFs (US, Europe, Japan, Korea, China, India, Hong Kong,
Singapore, Indonesia) and scores each geography with a SIGNED three-pillar
flow-momentum composite, mirroring the Sectors (US) momentum engine.

  run_fundflow(as_of=None) -> FundFlowCohortResult
"""

from src.research_ideas.fundflow.runner import run_fundflow

__all__ = ["run_fundflow"]
