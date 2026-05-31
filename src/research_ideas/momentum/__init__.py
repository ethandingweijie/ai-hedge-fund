"""
Momentum research idea
=======================
Two-layer momentum screen (sector ETF + ticker) that flags where momentum is
TURNING (inflection) and ACCELERATING (2nd derivative), producing both LONG
and SHORT ideas on a single signed scale.

  run_momentum(as_of=None, max_workers=4, save=True) -> MomentumCohortResult

Pipeline mirrors the Complacency idea:
  universe -> data_fetch (FMP EOD) -> indicators -> scoring (state/turn/accel)
  -> sector-alignment overlay -> cohort assembly -> SQLite persistence.
"""
