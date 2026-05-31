"""
src/research_ideas/hk50/selection.py
=====================================
Dynamic-universe membership selection for the "Long China / HK" cohort.

The eligible POOL is ~100 hand-curated names (src/research_ideas/hk50/universe.py),
all scored every run on the two ABSOLUTE quant screens. Displayed membership —
the "top 50" the user sees highlighted — is an OUTPUT of the screen, decided here
by a two-band hysteresis gate on `lead_score = max(growth_score, dividend_score)`:

    ENTER (55)  a NON-member joins only if lead_score >= ENTER
    STAY  (48)  a current member is KEPT as long as lead_score >= STAY
    CAP   (50)  hard display ceiling ("top 50")

The ENTER>STAY gap is the anti-churn mechanism: a name hovering at 50 that is
already a member stays put (>= STAY 48), but a *new* name at 50 does not join
(< ENTER 55). Same pattern as the IV15 alert (arm 1.05 / fire 1.00) and the
index promotion/relegation bands. Honest-threshold: if only N<50 names clear
their band, the cohort is N —
the CAP is a ceiling, never a floor (no backfill).

Membership is QUANT-ONLY by design (locked product decision): the qualitative
policy/moat overlay never feeds selection, so there is no chicken-and-egg of
needing membership to decide who gets the (expensive) LLM deep-research spend.
"""
from __future__ import annotations

from typing import Iterable

from src.research_ideas.hk50.schemas import HK50TickerResult

# ── Tuning knobs ──────────────────────────────────────────────────────────────
# Calibrated against the first full 100-name run: the lead_score distribution put
# ~44 names >= 55, which lands in the intended "top ~40-50" band. ENTER>STAY keeps
# the anti-churn gap. Both float honestly — if quality thins, fewer than 44 show.
ENTER_THRESHOLD = 55.0   # lead_score a NON-member must clear to join
STAY_THRESHOLD = 48.0    # lead_score a member must hold to stay (hysteresis floor)
DISPLAY_CAP = 50         # hard ceiling on the displayed cohort ("top 50")


def select_cohort(
    results: list[HK50TickerResult],
    prev_in: Iterable[str],
    *,
    enter: float = ENTER_THRESHOLD,
    stay: float = STAY_THRESHOLD,
    cap: int = DISPLAY_CAP,
) -> tuple[int, list[dict], list[dict]]:
    """Tag membership on `results` IN PLACE and return cohort deltas.

    `results` — every scored name, each with `lead_score` already set.
    `prev_in` — set of hk_ticker that were in_cohort in the latest stored run
                (empty on cold start → everyone is evaluated against ENTER).

    Mutates each result's `in_cohort` / `cohort_rank` / `membership`, then
    returns `(displayed_count, promoted, relegated)`:
       promoted  = names newly in the cohort     [{ticker, name, lead_score}]
       relegated = prior members no longer shown  [{..., reason}]
    """
    prev = set(prev_in or ())

    # 1. Hysteresis eligibility: members face STAY, non-members face ENTER.
    eligible = [
        r for r in results
        if r.lead_score >= (stay if r.hk_ticker in prev else enter)
    ]

    # 2. Rank by lead_score desc (ticker tiebreak) and apply the display cap.
    eligible.sort(key=lambda r: (-(r.lead_score or 0.0), r.ticker))
    in_cohort = eligible[:cap]
    in_set = {r.hk_ticker for r in in_cohort}

    # 3. Tag the displayed cohort.
    for rank, r in enumerate(in_cohort, 1):
        r.in_cohort = True
        r.cohort_rank = rank
        r.membership = "member" if r.hk_ticker in prev else "promoted"

    # 4. Tag everyone else: relegated (was a member) or bench.
    for r in results:
        if r.hk_ticker in in_set:
            continue
        r.in_cohort = False
        r.cohort_rank = None
        r.membership = "relegated" if r.hk_ticker in prev else "bench"

    # 5. Cohort deltas vs the prior run.
    promoted = [
        {"ticker": r.ticker, "name": r.name, "lead_score": r.lead_score}
        for r in in_cohort
        if r.hk_ticker not in prev
    ]
    relegated = [
        {
            "ticker": r.ticker,
            "name": r.name,
            "lead_score": r.lead_score,
            "reason": (
                f"fell below STAY {stay:.0f}"
                if r.lead_score < stay
                else f"bumped by {cap}-cap"
            ),
        }
        for r in results
        if r.hk_ticker in prev and r.hk_ticker not in in_set
    ]

    return len(in_cohort), promoted, relegated
