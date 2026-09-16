"""Replay a gate at a past date and score it against what was actually reported.

The forward loop cannot start yet. Production holds 43 runs, the oldest three
weeks old, and every `ticker_signals.outcome` is `PENDING` — there is no ground
truth in the system and there cannot be for a quarter. Every Bayesian mechanism
in the Store A design would spend that quarter decaying rules toward deletion
with nothing to score them on.

Backward testing sidesteps that entirely: pick a date far enough in the past
that the following year has already been reported, rebuild the drivers as they
stood then, project forward under both the gated and ungated assumption, and
compare each to the figure the company actually printed. Ground truth is not
waited for — it is already on file.

**Why this is valid despite the point-in-time gaps.** The audit found no
filing-date awareness anywhere, and analyst estimates are a latest-snapshot
only, so a full pipeline replay would be contaminated. This module does not
replay the pipeline. It scores ONE intervention, and both paths are projected
with the *same* growth rate drawn from the trailing series. Growth, WACC,
share count and every other driver are identical on both sides and cancel out
of the difference. What survives is the margin intervention alone, which is
exactly what a gate's alpha and beta should be earned on.

The residual exposure is restatement — the trailing series is fetched as
filed today, not as filed then. For cash-flow lines that is small, and it
biases both paths identically.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The band inside which a difference is noise rather than evidence. Their
# design proposes a flat 2% of the reported metric; that is far too tight for
# free cash flow, which swings on working-capital timing, and too loose for
# revenue. Left as a parameter with a per-metric default rather than one
# global constant.
EPSILON_BY_METRIC: dict[str, float] = {
    # The metric the cash-conversion gate actually makes a claim about. See
    # `realised_owner_earnings_margin` for why reported FCF was the wrong
    # target.
    "owner_earnings_margin": 0.10,
    "free_cash_flow": 0.10,
    "revenue":        0.02,
    "ebitda":         0.05,
    "net_income":     0.08,
}
_DEFAULT_EPSILON = 0.05

# Symmetric, per the audit: every documented incident in this codebase is
# overvaluation (MELI 9.4x, PDD 11.8x, JD 8.7x, MNDY $475 on $65, INTU PT
# $1,076 on $396) and there is no recorded over-conservatism failure, so
# penalising the conservative direction harder would tune against the observed
# error distribution.
ALPHA_STEP = 1.0
BETA_STEP = 1.0


def epsilon_for(metric: str) -> float:
    return EPSILON_BY_METRIC.get(metric, _DEFAULT_EPSILON)


def delta_error_verdict(
    path_a: Optional[float],
    path_b: Optional[float],
    actual: Optional[float],
    metric: str = "free_cash_flow",
    epsilon: Optional[float] = None,
) -> dict[str, Any]:
    """Score one gate firing. Path A is ungated, Path B is gated.

        delta = (|B - actual| - |A - actual|) / |actual|

    Negative means the intervention moved the projection toward reality.
    Returns a verdict dict; never raises on degenerate input, because a gate
    that cannot be scored must be left unscored rather than guessed at.
    """
    eps = epsilon_for(metric) if epsilon is None else epsilon
    out: dict[str, Any] = {
        "metric": metric, "path_a": path_a, "path_b": path_b,
        "actual": actual, "epsilon": eps,
        "delta_error_pct": None, "verdict": "UNSCORABLE",
        "alpha_delta": 0.0, "beta_delta": 0.0,
    }
    if path_a is None or path_b is None or actual is None:
        return out
    try:
        a, b, act = float(path_a), float(path_b), float(actual)
    except (TypeError, ValueError):
        return out
    if act == 0:
        return out

    delta = (abs(b - act) - abs(a - act)) / abs(act)
    out["delta_error_pct"] = delta
    out["error_a_pct"] = abs(a - act) / abs(act)
    out["error_b_pct"] = abs(b - act) / abs(act)
    if delta <= -eps:
        out["verdict"] = "HELPED"
        out["alpha_delta"] = ALPHA_STEP
    elif delta >= eps:
        out["verdict"] = "FALSE_ALARM"
        out["beta_delta"] = BETA_STEP
    else:
        out["verdict"] = "NEUTRAL"
    return out


# ── Replay ──────────────────────────────────────────────────────────────────

_LINE_ITEMS = [
    "revenue", "free_cash_flow", "net_income", "capital_expenditure",
    "depreciation_and_amortization", "operating_cash_flow",
    "change_in_working_capital", "ebitda", "stock_based_compensation",
]


def _series(ticker: str, end_date: str, limit: int = 8) -> list[dict]:
    """Annual rows with period_end <= end_date, oldest first."""
    from src.agents.analysis.dcf_agent import _extract_annual_series
    from src.tools.api import search_line_items

    li = search_line_items(ticker, _LINE_ITEMS, end_date,
                           period="annual", limit=limit)
    rows, _ccy = _extract_annual_series(li or [])
    return rows


# ── 1.2A: the balance-sheet-financial classifier ────────────────────────────
#
# The plan's backward-test row for this item names the metric as *classifier
# precision/recall* against a hand-labelled set, not a delta-error against a
# projected figure — because the gate does not project anything. It decides
# whether enterprise-value arithmetic is meaningful for a given company, and
# that decision has a ground truth an analyst can state: is this business
# funded by money it holds for other people?
#
# `delta_error_verdict` cannot score that (there is no `actual` to be wrong
# about), so this section scores a confusion matrix instead. The acceptance bar
# is mapped from the plan's, and the mapping is stated rather than assumed:
#
#   plan: hit-rate ≥ 0.50        → precision ≥ 0.50  (of the names it fires
#                                                     on, how many are truly
#                                                     deposit/float-funded)
#   plan: MAE no worse than      → n/a; no numeric projection exists
#   plan: ≥10 scoreable firings  → ≥10 labels actually resolved against live
#                                  balance-sheet data
#
# plus one bar the plan's phrasing does not cover and this defect requires:
# **recall ≥ 0.90**. The two failure modes are not symmetric. A false positive
# deletes legitimate valuation legs and produces a worse estimate. A false
# negative publishes an EV multiple on a bank — 02888.HK's HK$730 forward
# EV/EBITDA per share — which is not a worse estimate but a meaningless one,
# and it reaches the user. Precision alone would have passed a gate that never
# fired at all.

#: Hand-labelled set. `is_financial` is the analyst judgement: True means the
#: business is funded by balances it holds for others (deposits, customer
#: cash, margin, insurance float) so enterprise value subtracts the product.
#:
#: `sector`/`profile` are what the engine ACTUALLY resolves the ticker to, not
#: what it should resolve to. That distinction is the point: HOOD is labelled a
#: balance-sheet financial on its economics but routes to `Crypto` /
#: `Crypto Exchange`, which is in neither tier, so the gate cannot fire and the
#: backtest reports a false negative rather than hiding it behind a profile
#: chosen to make the answer come out right.
#:
#: `profile_source` says whether the pair came from the curated
#: TICKER_SECTOR_LOOKUP (asserted at run time, so a routing change fails the
#: backtest loudly) or was recorded from a live run because the lookup does not
#: cover the ticker and the industry-map branch needs the LLM sector
#: classifier, which is not reproducible offline.
BSF_LABELS: tuple[dict, ...] = (
    # ── Positives: deposit / float funded. The gate MUST classify these. ──
    {"ticker": "JPM", "sector": "Financials", "profile": "Money Center Bank",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "BAC", "sector": "Financials", "profile": "Money Center Bank",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "WFC", "sector": "Financials", "profile": "Money Center Bank",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "C", "sector": "Financials", "profile": "Money Center Bank",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "GS", "sector": "Financials", "profile": "Investment Bank",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "MS", "sector": "Financials", "profile": "Investment Bank",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "D05.SI", "sector": "Financials",
     "profile": "Money Center Bank (SG)", "is_financial": True,
     "profile_source": "lookup", "tier": 1},
    {"ticker": "U11.SI", "sector": "Financials",
     "profile": "Money Center Bank (SG)", "is_financial": True,
     "profile_source": "lookup", "tier": 1},
    {"ticker": "O39.SI", "sector": "Financials",
     "profile": "Money Center Bank (SG)", "is_financial": True,
     "profile_source": "lookup", "tier": 1},
    # The defect itself: HK$730 forward EV/EBITDA per share on a bank.
    {"ticker": "02888.HK", "sector": "Financials",
     "profile": "Money Center Bank", "is_financial": True,
     "profile_source": "lookup", "tier": 1},
    {"ticker": "SCHW", "sector": "Financials", "profile": "Brokerage",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "MET", "sector": "Financials", "profile": "Insurance",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "PRU", "sector": "Financials", "profile": "Insurance",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "AIG", "sector": "Financials", "profile": "Insurance",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    {"ticker": "BRK.B", "sector": "Financials", "profile": "Holding Company",
     "is_financial": True, "profile_source": "lookup", "tier": 1},
    # Not in the curated lookup — profile recorded from a live run.
    {"ticker": "IBKR", "sector": "Financials", "profile": "Brokerage",
     "is_financial": True, "profile_source": "recorded", "tier": 1,
     "note": "accountPayables 77% of assets; margin receivables 45%"},
    {"ticker": "ALL", "sector": "Financials", "profile": "Insurance (P&C)",
     "is_financial": True, "profile_source": "recorded", "tier": 1},
    # Tier 2 measured positive: FinTech with 99.1% of assets in customer
    # balances and the loan book.
    {"ticker": "PYPL", "sector": "Financials", "profile": "FinTech",
     "is_financial": True, "profile_source": "recorded", "tier": 2,
     "note": "customer funds 50% of assets, receivables 49%"},
    # Labelled positive on its economics, but the router puts it in Crypto /
    # Crypto Exchange, which is in NEITHER tier. Expected FALSE NEGATIVE.
    {"ticker": "HOOD", "sector": "Crypto", "profile": "Crypto Exchange",
     "is_financial": True, "profile_source": "recorded", "tier": 1,
     "note": "ROUTING GAP: the plan lists HOOD under Brokerage; live routing "
             "gives Crypto Exchange. Customer cash and margin receivables are "
             "87% of assets, so the economics are a brokerage's."},

    # ── Negatives: fee-based. The gate must NOT classify these. ──
    {"ticker": "V", "sector": "Financials", "profile": "Payment Networks",
     "is_financial": False, "profile_source": "lookup", "tier": 2,
     "note": "0.2420 — settlement owed to members is not funding; the closest "
             "name to the 0.30 cut"},
    {"ticker": "MA", "sector": "Financials", "profile": "Payment Networks",
     "is_financial": False, "profile_source": "lookup", "tier": 2,
     "note": "0.2486"},
    {"ticker": "CME", "sector": "Financials",
     "profile": "Market Infrastructure", "is_financial": False,
     "profile_source": "lookup", "tier": 2,
     "note": "0.0036 — FMP breaks out no collateral, quiet without help"},
    {"ticker": "ICE", "sector": "Financials",
     "profile": "Market Infrastructure", "is_financial": False,
     "profile_source": "lookup", "tier": 2,
     "note": "0.6135 — cleared ONLY by the exemption: US$76.9bn pass-through "
             "margin against US$85.8bn of current assets"},
    {"ticker": "S68.SI", "sector": "Financials",
     "profile": "Market Infrastructure (SG)", "is_financial": False,
     "profile_source": "lookup", "tier": 2,
     "note": "0.4738 — cleared by the exemption, but for a different reason "
             "than ICE: ratio shape on a small denominator"},
    {"ticker": "BLK", "sector": "Financials", "profile": "Asset Manager",
     "is_financial": False, "profile_source": "lookup", "tier": 2},
    {"ticker": "BX", "sector": "Financials", "profile": "Alt Asset Manager",
     "is_financial": False, "profile_source": "lookup", "tier": 2},
    {"ticker": "TROW", "sector": "Financials", "profile": "Asset Manager",
     "is_financial": False, "profile_source": "lookup", "tier": 2},
    {"ticker": "APO", "sector": "Financials", "profile": "Alt Asset Manager",
     "is_financial": False, "profile_source": "lookup", "tier": 2},
    {"ticker": "KKR", "sector": "Financials", "profile": "Alt Asset Manager",
     "is_financial": False, "profile_source": "lookup", "tier": 2},
    # Non-financial controls: a conglomerate and two industrials whose payables
    # are ordinary trade credit.
    {"ticker": "BN4.SI", "sector": "Industrials",
     "profile": "Conglomerate / Industrial (SG)", "is_financial": False,
     "profile_source": "lookup", "tier": 0, "note": "0.2026"},
    {"ticker": "U96.SI", "sector": "Industrials",
     "profile": "Conglomerate / Industrial (SG)", "is_financial": False,
     "profile_source": "lookup", "tier": 0, "note": "0.1842"},
    # 0.4593 — ABOVE the 0.30 Tier-2 threshold, and correctly not stripped.
    # This is the single most important row in the set. AAPL's payables are
    # trade credit from a contract-manufacturing base, not customer money, and
    # the ratio cannot tell the two apart. It is the measured proof that the
    # ratio is only ever a conditional second tier, keyed off a profile that is
    # already fee-based, and never a classifier on its own: a ratio-only gate
    # would strip the DCF out of Apple. Read this row before anyone proposes
    # promoting Tier 2 to Tier 1.
    {"ticker": "AAPL", "sector": "Tech",
     "profile": "Hyperscaler / Tech Conglomerate", "is_financial": False,
     "profile_source": "lookup", "tier": 0, "note": "0.4593"},
    {"ticker": "MU", "sector": "Semiconductor", "profile": "Memory / DRAM-NAND",
     "is_financial": False, "profile_source": "lookup", "tier": 0,
     "note": "0.2034"},
)

#: The plan's acceptance bar, mapped onto a confusion matrix. See the comment
#: above for why recall carries a bar the plan's phrasing does not state.
BSF_MIN_PRECISION = 0.50
BSF_MIN_RECALL = 0.90
BSF_MIN_FIRINGS = 10

_BSF_LINE_ITEMS = [
    # `revenue` is not an input to the ratio. It is here because
    # `_extract_annual_series` DROPS every row whose revenue is None or <= 0,
    # so a balance-sheet-only request yields zero rows and the backtest scores
    # nothing while looking like a clean pass. Production always requests
    # revenue, so this only ever affected the test — but silently.
    "revenue",
    "total_assets", "total_deposits", "loans_receivable",
    "loans_held_for_investment", "accounts_payable", "accounts_receivable",
    "other_payables", "other_current_liabilities",
]


def _bsf_most_recent(ticker: str, as_of: str) -> Optional[dict]:
    """The latest annual balance sheet through the PRODUCTION path.

    Deliberately not a direct FMP call: `search_line_items` applies the
    camelCase→snake_case map and the HK/SG routing, and `_extract_annual_series`
    is the row builder that also performs the FX conversion. Scoring a
    hand-rolled dict would test the ratio arithmetic and miss the mapping,
    which is where the 0388.HK false positive lived (a numerator left in HKD
    against a denominator converted to USD reads 1.62 instead of 0.208).
    """
    from src.agents.analysis.dcf_agent import _extract_annual_series
    from src.tools.api import search_line_items

    li = search_line_items(ticker, _BSF_LINE_ITEMS, as_of,
                           period="annual", limit=1)
    rows, _ccy = _extract_annual_series(li or [])
    return rows[-1] if rows else None


def backtest_balance_sheet_financial(
    as_of: str = "2026-09-17",
    labels: tuple[dict, ...] = BSF_LABELS,
) -> dict[str, Any]:
    """Classifier precision/recall for GATE_BALANCE_SHEET_FINANCIAL.

    Each label is resolved against its live balance sheet and run through the
    same two functions production uses — `_tier2_customer_balance_ratio` and
    `_is_balance_sheet_financial` — so the result measures the shipped gate,
    not a model of it.

    Returns a summary dict with the confusion counts, precision, recall, the
    per-ticker rows and an `accepted` flag against the bar above. Rows whose
    balance sheet the feed could not deliver are counted separately as
    `no_data`, never silently dropped: a name that cannot be measured keeps
    its EV legs, and for a Tier 2 profile that is a coverage gap the report has
    to show rather than hide inside a tidy precision figure.
    """
    from src.agents.analysis.dcf_agent import (
        _is_balance_sheet_financial,
        _tier2_customer_balance_ratio,
    )
    from src.data.sector_profiles import get_wacc_profile_for_ticker

    rows: list[dict] = []
    tp = fp = tn = fn = no_data = 0

    for lab in labels:
        ticker, profile = lab["ticker"], lab["profile"]
        truth = bool(lab["is_financial"])
        row: dict[str, Any] = {
            "ticker": ticker, "sector": lab["sector"], "profile": profile,
            "label": truth, "tier": lab.get("tier"),
            "profile_source": lab.get("profile_source"),
            "note": lab.get("note", ""),
        }

        # If the curated lookup resolves this ticker, it is the authority and
        # the recorded pair must match it. A mismatch means the routing moved
        # under the label set, and scoring the old profile would quietly test a
        # gate that production no longer reaches.
        lk_sector, lk_profile = get_wacc_profile_for_ticker(ticker)
        if lk_profile:
            row["lookup_profile"] = lk_profile
            if (lk_sector, lk_profile) != (lab["sector"], profile):
                row["routing_drift"] = (
                    f"curated lookup now gives ({lk_sector}, {lk_profile}); "
                    f"label set says ({lab['sector']}, {profile})")

        most_recent = _bsf_most_recent(ticker, as_of)
        if not most_recent:
            no_data += 1
            row["fired"] = None
            row["outcome"] = "NO_DATA"
            row["skip_reason"] = "feed returned no annual balance sheet"
            rows.append(row)
            continue

        row["period"] = most_recent.get("period")
        ratio, breakdown = _tier2_customer_balance_ratio(most_recent)
        row["customer_balance_ratio"] = ratio
        row["ratio_lines"] = breakdown.get("lines")
        fired = _is_balance_sheet_financial(profile, most_recent)
        row["fired"] = fired

        if fired and truth:
            row["outcome"], tp = "TP", tp + 1
        elif fired and not truth:
            row["outcome"], fp = "FP", fp + 1
        elif not fired and truth:
            row["outcome"], fn = "FN", fn + 1
        else:
            row["outcome"], tn = "TN", tn + 1
        rows.append(row)

    scored = tp + fp + tn + fn
    firings = tp + fp
    precision = (tp / firings) if firings else None
    recall = (tp / (tp + fn)) if (tp + fn) else None

    reasons: list[str] = []
    if scored == 0:
        # Nothing could be measured. That is an infrastructure failure — a dead
        # key, a feed outage, a request list that drops every row — not evidence
        # about the gate. Reported as `accepted: None` so the summary says
        # NO DATA instead of REJECT and nobody reads a broken run as a verdict.
        reasons.append(f"0 of {len(labels)} labels resolved against live data")
        return {
            "gate_id": "GATE_BALANCE_SHEET_FINANCIAL",
            "as_of": as_of, "metric": "classifier_precision_recall",
            "labels": len(labels), "scored": 0, "no_data": no_data,
            "tp": 0, "fp": 0, "tn": 0, "fn": 0, "firings": 0,
            "precision": None, "recall": None,
            "bar": {"precision": BSF_MIN_PRECISION, "recall": BSF_MIN_RECALL,
                    "min_firings": BSF_MIN_FIRINGS},
            "accepted": None,
            "reject_reasons": reasons,
            "rows": rows,
        }

    if precision is None or precision < BSF_MIN_PRECISION:
        reasons.append(f"precision {precision} < {BSF_MIN_PRECISION}")
    if recall is None or recall < BSF_MIN_RECALL:
        reasons.append(f"recall {recall} < {BSF_MIN_RECALL}")
    if firings < BSF_MIN_FIRINGS:
        reasons.append(f"{firings} firings < {BSF_MIN_FIRINGS}")

    return {
        "gate_id": "GATE_BALANCE_SHEET_FINANCIAL",
        "as_of": as_of, "metric": "classifier_precision_recall",
        "labels": len(labels), "scored": scored, "no_data": no_data,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn, "firings": firings,
        "precision": precision, "recall": recall,
        "bar": {"precision": BSF_MIN_PRECISION, "recall": BSF_MIN_RECALL,
                "min_firings": BSF_MIN_FIRINGS},
        "accepted": not reasons,
        "reject_reasons": reasons,
        "rows": rows,
    }


def _next_reported_year(ticker: str, after_period: str,
                        today: str) -> Optional[dict]:
    """The first annual row the company reported AFTER `after_period`.

    This is the ground truth the replay is scored against — a figure that was
    unknowable at the as-of date and is on file now.
    """
    for row in _series(ticker, today, limit=10):
        if str(row.get("period") or "") > str(after_period):
            return row
    return None


def backtest_cash_conversion(ticker: str, as_of: str,
                             today: str = "2026-08-30") -> dict[str, Any]:
    """Replay the cash-conversion gate at `as_of` and score it.

    Both paths carry the SAME growth rate, drawn from the trailing series, so
    the growth model cancels out of the comparison and the verdict is earned
    by the margin intervention alone.
    """
    from src.agents.analysis.dcf_agent import (
        _CASH_CONVERSION_TOLERANCE,
        _historical_cagr,
        _mean_fcf_margin,
        _projectable_fcf_margin_cap,
    )

    out: dict[str, Any] = {
        "ticker": ticker, "as_of": as_of, "gate_id": "GATE_CASH_CONVERSION",
        "fired": False, "verdict": "UNSCORABLE",
    }
    trailing = _series(ticker, as_of, limit=5)
    if len(trailing) < 2:
        out["skip_reason"] = "insufficient trailing history"
        return out

    last_period = str(trailing[-1].get("period") or "")
    out["trailing_through"] = last_period

    margin_raw = _mean_fcf_margin(trailing)
    cap, _trailing_margin, _basis = _projectable_fcf_margin_cap(trailing)
    if margin_raw is None:
        out["skip_reason"] = "no trailing FCF margin"
        return out

    out["margin_path_a"] = margin_raw
    out["margin_cap"] = cap
    if not (cap is not None and cap > 0
            and margin_raw > cap * _CASH_CONVERSION_TOLERANCE):
        out["skip_reason"] = "gate does not fire at this date"
        out["margin_path_b"] = margin_raw
        return out

    out["fired"] = True
    out["margin_path_b"] = cap

    revenue_base = trailing[-1].get("revenue")
    g = _historical_cagr(trailing, revenue_base) or 0.0
    out["growth_used"] = g

    actual_row = _next_reported_year(ticker, last_period, today)
    if not actual_row:
        out["skip_reason"] = "no reported year after the as-of date yet"
        return out
    out["actual_period"] = actual_row.get("period")

    # Same revenue path on both sides; only the margin differs.
    rev_1 = float(revenue_base) * (1.0 + g)
    out["projected_revenue"] = rev_1
    out["actual_revenue"] = actual_row.get("revenue")

    verdict = delta_error_verdict(
        path_a=rev_1 * margin_raw,
        path_b=rev_1 * cap,
        actual=actual_row.get("free_cash_flow"),
        metric="free_cash_flow",
    )
    out.update(verdict)
    return out


# ── Scoring the claim the gate actually makes ───────────────────────────────

def _owner_earnings_margin(row: dict) -> Optional[float]:
    """(FCF - change in working capital) / revenue for one reported year.

    The cash-conversion gate does not claim to predict next year's reported
    free cash flow. It claims that the part of reported FCF which comes from
    working capital is not repeatable and must not be capitalised for ten
    years. Scoring it against reported FCF therefore tested a claim it never
    made — and marked it a false alarm on MELI for correctly declining to
    project a float that had not yet stopped growing.
    """
    rev = row.get("revenue")
    fcf = row.get("free_cash_flow")
    if not rev or rev <= 0 or fcf is None:
        return None
    dwc = row.get("change_in_working_capital") or 0.0
    return (fcf - dwc) / rev


def realised_owner_earnings_margin(
    ticker: str, after_period: str, today: str, max_years: int = 3,
) -> tuple[Optional[float], int, list[str]]:
    """Mean owner-earnings MARGIN over the years reported after `after_period`.

    Two deliberate choices, both to match what the gate asserts:

    * **A margin, not a cash level.** The gate outputs a margin, so comparing
      margin to margin removes the growth rate, the revenue base and the
      compounding from the comparison entirely. Previously both paths were
      projected forward and their absolute errors reached 300-450% on names
      like BABA and JPM — noise from the growth assumption, which the gate
      does not touch, swamping the intervention it does.
    * **A mean over several years, not one.** "Terminal" means the level that
      persists. A single year is dominated by timing; averaging is the closest
      observable proxy for steady state.
    """
    margins: list[float] = []
    periods: list[str] = []
    for row in _series(ticker, today, limit=10):
        if str(row.get("period") or "") <= str(after_period):
            continue
        m = _owner_earnings_margin(row)
        if m is None:
            continue
        margins.append(m)
        periods.append(str(row.get("period")))
        if len(margins) >= max_years:
            break
    if not margins:
        return None, 0, []
    return sum(margins) / len(margins), len(margins), periods


def backtest_cash_conversion_owner_earnings(
    ticker: str, as_of: str, today: str = "2026-08-30", max_years: int = 3,
) -> dict[str, Any]:
    """Replay the cash-conversion gate and score it on terminal owner earnings.

    Path A and Path B are the two margins the gate chose between. No
    projection is involved, so nothing but the intervention is being scored.
    """
    from src.agents.analysis.dcf_agent import (
        _CASH_CONVERSION_TOLERANCE,
        _DEEP_CUT_OBSERVATION_FRACTION,
        _mean_fcf_margin,
        _projectable_fcf_margin_cap,
    )

    out: dict[str, Any] = {
        "ticker": ticker, "as_of": as_of, "gate_id": "GATE_CASH_CONVERSION",
        "metric": "owner_earnings_margin", "fired": False,
        "deep_cut": False, "verdict": "UNSCORABLE",
    }
    trailing = _series(ticker, as_of, limit=5)
    if len(trailing) < 2:
        out["skip_reason"] = "insufficient trailing history"
        return out

    last_period = str(trailing[-1].get("period") or "")
    out["trailing_through"] = last_period

    margin_raw = _mean_fcf_margin(trailing)
    cap, _tr, _basis = _projectable_fcf_margin_cap(trailing)
    if margin_raw is None:
        out["skip_reason"] = "no trailing FCF margin"
        return out
    out["margin_path_a"] = margin_raw
    out["margin_cap"] = cap
    out["basis"] = _basis
    if not (cap is not None and cap > 0
            and margin_raw > cap * _CASH_CONVERSION_TOLERANCE):
        out["skip_reason"] = "gate does not fire at this date"
        return out

    # The deep-cut flag is an observation, not a gate. It was briefly a
    # suppression rule; held-out data inverted it (see
    # _DEEP_CUT_OBSERVATION_FRACTION), so the gate fires regardless and the
    # flag is carried for whatever rule eventually separates a thin-earnings
    # ramp from a financial.
    out["deep_cut"] = cap < _DEEP_CUT_OBSERVATION_FRACTION * margin_raw
    out["retained_fraction"] = (cap / margin_raw) if margin_raw else None
    out["fired"] = True
    realised, n, periods = realised_owner_earnings_margin(
        ticker, last_period, today, max_years)
    out["realised_periods"] = periods
    out["realised_years"] = n
    if realised is None:
        out["skip_reason"] = "no reported year after the as-of date yet"
        return out
    out["realised_owner_earnings_margin"] = realised
    # `deep_cut` segments the firings without changing which of them happen.

    out.update(delta_error_verdict(
        path_a=margin_raw, path_b=cap, actual=realised,
        metric="owner_earnings_margin",
    ))
    return out
