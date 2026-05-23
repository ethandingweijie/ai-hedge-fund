"""
src/research_ideas/sw46/
=========================
Software-46 cohort valuation, Cassandra Unchained / Scion methodology.

Layers (deterministic Python, no LLM):
  tragic_algebra.py  -- SBC truth: Omega = G + C + B + dS*P, OE = N + G - Omega, dE = OE / N
  roic.py            -- fully-adjusted ROIC
  aict.py            -- 5-tier AI Competitive Threat ladder (v1: JSON table)
  iv15.py            -- 15-yr / 15% RR hybrid DDM + Buffett terminal multiple
  composite_score.py -- 100-pt Shareholder / Quality / Valuation bucket score

Glue:
  schemas.py    -- pydantic output models
  universe.py   -- SW46 ticker list loader
  data_fetch.py -- FMP wrappers for TA-specific line items + share count / price
  runner.py     -- orchestrator (cohort run, ThreadPoolExecutor parallel)
"""
