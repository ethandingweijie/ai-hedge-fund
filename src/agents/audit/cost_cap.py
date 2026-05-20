"""Cost cap with budget heartbeat for the Card QA Agent.

Per-run hard cap of $0.50 (override with `DD_QA_BUDGET_USD` env var).
Enforced INSIDE the LLM call wrapper (`call_qwen_capped` in llm_judge.py)
— not at card boundaries — so a single card with many re-extractions
can't blow past the limit mid-card.

Why budget heartbeat instead of upfront budgeting:
  * A typical card audit costs ~$0.01 (judge call only)
  * Worst case: WRONG_PROFILE flag + 5 hinted re-extractions → ~$0.10
  * Pathological case: misclassified profile + 10 cards × 3 reextracts → $1.50
  * Without heartbeat, the pathological case overshoots silently.

Public surface:
  CostCap(max_usd=...).check_headroom() → bool
  CostCap.add(usd: float)               → None
  CostCap.remaining()                   → float
  DEFAULT_BUDGET_USD                    → 0.50
  BUDGET_HEADROOM_USD                   → 0.05   (refuse new calls when < this remains)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Hard cap per ticker audit. Env override for testing or per-deploy tuning.
DEFAULT_BUDGET_USD: float = float(os.environ.get("DD_QA_BUDGET_USD", "0.50"))

# Refuse to start a new LLM call once we're within $0.05 of the cap. The
# headroom prevents a borderline call from pushing the total over the limit
# (we estimate cost AFTER the call when token counts are known, so without
# headroom an "almost-at-cap" call could push it $0.03 over).
BUDGET_HEADROOM_USD: float = float(os.environ.get("DD_QA_BUDGET_HEADROOM_USD", "0.05"))


@dataclass
class CostCap:
    """Tracks accumulated LLM cost for one QA run (one ticker).

    Create fresh per-ticker. Not thread-safe — but a single ticker audit
    runs sequentially in Phase 10.5, so no synchronization needed.
    """

    max_usd: float = DEFAULT_BUDGET_USD
    accumulated_usd: float = 0.0
    headroom_usd: float = BUDGET_HEADROOM_USD

    def check_headroom(self) -> bool:
        """True iff there's still room for another LLM call.

        Use this BEFORE every LLM call (the budget heartbeat). When it
        returns False, the caller should return a sentinel verdict
        (e.g. budget_exhausted_mid_run) and stop calling the LLM.
        """
        return self.accumulated_usd < (self.max_usd - self.headroom_usd)

    def add(self, usd: float) -> None:
        """Record a completed LLM call's cost. Always called AFTER the
        call regardless of whether the budget was exceeded — we want
        accurate cost reporting in the final audit dict even if we
        overshot by a small amount."""
        if usd < 0:
            logger.warning("cost_cap: negative cost ignored: %f", usd)
            return
        self.accumulated_usd += usd

    def remaining(self) -> float:
        """USD remaining before hitting the headroom-respecting cap.
        Always >= 0."""
        return max(0.0, (self.max_usd - self.headroom_usd) - self.accumulated_usd)
