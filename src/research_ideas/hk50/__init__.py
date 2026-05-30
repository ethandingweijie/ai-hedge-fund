"""
src/research_ideas/hk50
=======================
"Long China / HK" two-screener research module.

A single 50-stock China/HK universe is scored by TWO independent 0-100 screens
— High Growth and High Dividend — and valued with the SW46 IV15 engine
(AICT-modulated only for software / internet-platform names). The cohort run is
persisted to SQLite (hk50_runs) and surfaced under the Research Ideas tab as the
"Long China / HK" hero card: top-5 of each screen on the card face, with a
Growth ⇄ Dividend toggle in the detail page.

The deterministic compute kernel lives in scripts/hk50_*.py (resolver, scorers,
IV15). This package is a thin orchestration + schema + persistence layer on top,
mirroring src/research_ideas/sw46.
"""
