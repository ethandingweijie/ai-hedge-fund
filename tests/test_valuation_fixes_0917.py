"""Owner decisions 2a and 2b (2026-09-17): the book-value path is for balance-sheet businesses.

Two defects, one mechanism. The 12-month target dispatched on the SECTOR
(``profile_name in _BANK_PROFILES or sector == "Financials"``), so every
Financials name was priced off a Gordon Growth target P/B applied to book value
— including fee-driven franchises whose tangible book is negative. When the
method needed a tangible book per share and did not find a positive one,
``_compute_bank_metrics`` supplied one: its derivation is floored at 70% of
equity, and it accepts a REPORTED ``tangible_book_value_per_share`` only when
that is ``> 0``, so a reported negative was discarded and the floor substituted.

Visa, on the golden baseline: a 12m target of $12.84 against a base IV of
$428.47 — 3.0% of its own valuation, published as
"GGM target P/B x book value per share". ICE took the same path for the same
reason.

2a routes the dispatch through ``_is_balance_sheet_financial`` — the same
classification Phase 1.2A already made when it stripped the EV/DCF/FCF legs — so
the legs the IV is built from and the method the target is built from come from
one answer. 2b makes a non-positive tangible book fail the method outright
rather than be synthesized positive.

The 70% floor in ``_compute_bank_metrics`` is deliberately NOT removed. It is
load-bearing there: JPM's blind-strip derivation over-strips by ~$15B against
the issuer's own convention (which retains MSRs as tangible), and the floor is
what keeps a real bank from being marked down for a derivation artifact. The
hard stop here computes tangible book independently instead, so the floor can
stay where it earns its keep.
"""

from __future__ import annotations

import inspect

import pytest

import src.agents.analysis.dcf_agent as d
from src.agents.analysis.dcf_agent import (
    _bank_ggm_assumptions,
    _compute_bank_metrics,
    _compute_ggm_pb,
    _is_balance_sheet_financial,
)


# ── 2a: the dispatch classification ─────────────────────────────────────────

#: Released from the book-value path. Each is in the Financials sector and each
#: was therefore routed to GGM target P/B before the change.
RELEASED_PROFILES = (
    "Market Infrastructure",
    "Market Infrastructure (SG)",
    "Payment Networks",
    "Real Estate Asset Manager (SG)",
)

#: Still on the book-value path. Tier 1 — a balance-sheet business by
#: construction, no measurement needed.
KEPT_TIER1 = (
    "Money Center Bank", "Money Center Bank (SG)", "Money Center Bank (EU)",
    "Regional Bank", "Super-Regional Bank", "EM Bank", "EM Bank (Premium)",
    "Investment Bank", "Mortgage/GSE", "Neo/Challenger", "Insurance",
    "Brokerage", "Bank / Lending Institution",
)


@pytest.mark.parametrize("profile", RELEASED_PROFILES)
def test_released_profiles_are_not_balance_sheet_financials(profile):
    """The four the owner named. No balance-sheet row is supplied, so this
    tests the classification on profile alone — Tier 1 membership and the
    Tier-2 exemption, neither of which needs a measurement."""
    assert _is_balance_sheet_financial(profile, None) is False


@pytest.mark.parametrize("profile", KEPT_TIER1)
def test_tier1_profiles_stay_on_the_book_path(profile):
    """All 16 Tier-1 profiles are unchanged by the dispatch swap."""
    assert _is_balance_sheet_financial(profile, None) is True


def test_brokerage_is_kept_per_owner_decision_4():
    """SCHW stays on the book path and its DCF weight stays 0.

    The owner declined to re-admit the FCFF DCF on brokerages: SCHW's operating
    cash flow oscillates $1.2bn -> 18.9bn -> 2.0bn -> 8.8bn on swings in
    bank-depository sweep balances and brokerage receivables, so the undervaluation
    is to be fixed through normalised earnings and through-cycle NIM in the
    Forward P/E and Residual Income legs rather than by re-admitting an unstable
    cash flow method.
    """
    assert _is_balance_sheet_financial("Brokerage", None) is True


def test_the_dispatch_reads_the_gate_classification_not_the_sector():
    """Pins the one-line change and the removal of the sector test.

    Reusing `_bsf_is_financial` rather than calling the classifier again is the
    point: the two must not be able to disagree, and a second call site with its
    own arguments is how they would.
    """
    src = inspect.getsource(d)
    assert "_use_pe_only = _bsf_is_financial" in src
    # the sector catch-all is gone from the dispatch
    assert 'profile_name in _BANK_PROFILES\n            or sector == "Financials"' not in src


def test_a_non_financial_sector_cannot_reach_the_book_path():
    """The old predicate's first arm was the sector; the new one has no sector
    term at all, so a Financials-sector name with a fee-based profile goes to
    the standard waterfall and a non-Financials name never reached it either."""
    for profile in RELEASED_PROFILES:
        assert _is_balance_sheet_financial(profile, {"total_deposits": 1e12}) is False


def test_ggm_label_is_still_excluded_from_forward_consensus_paths():
    """Owner decision 2d: the reason the GGM label is not a forward-consensus
    PT is now the same reason it is not dispatched for non-bank financials.

    "Deprecate GGM book target dispatch for non-bank financials; eliminate
    synthetic positive tangible book fallback."
    """
    assert "GGM target P/B x book value per share" not in d._FORWARD_CONSENSUS_PT_LABELS
    assert "EV/EBITDA or EV/Revenue forward multiple" in d._FORWARD_CONSENSUS_PT_LABELS


# ── 2b: the negative tangible book hard stop ────────────────────────────────

#: Visa's shape: an asset-light network whose acquired goodwill and intangibles
#: exceed total equity, so real tangible book is negative.
VISA_LIKE = {
    "book_value_per_share": 8.0,
    "total_equity": 20e9,
    "goodwill": 30e9,
    "intangible_assets": 5e9,
    "net_income": 1e9,
}
SHARES = 2.5e9          # -> derived TBVPS = (20e9 - 35e9) / 2.5e9 = -6.00
VISA_LIKE = {**VISA_LIKE, "shares_outstanding": SHARES}

#: Not in `_BANK_GGM_OVERRIDES` and carries no analyst basis, so the ROE
#: precedence falls through to the realised+target midpoint branch — the only
#: branch the removed floor was feeding.
MIDPOINT_TICKER = "MIDPOINT.SI"


def test_reported_negative_tangible_book_fails_the_method():
    """FMP reports the negative directly; the method must not value off it."""
    row = {**VISA_LIKE, "tangible_book_value_per_share": -6.0}
    assert _compute_ggm_pb("V", "Payment Networks", row, SHARES) is None


def test_derived_negative_tangible_book_fails_the_method():
    """And when nothing is reported, the unfloored derivation must fail it too.

    This is the case `_compute_bank_metrics` fabricated: it accepts a reported
    TBVPS only when `> 0`, so with no reported figure it falls to
    `max(equity - goodwill - intangibles, equity * 0.70)` and returns a positive
    book for a company that has none. The first assertion pins the fabrication
    itself — S$5.60 of tangible book invented out of a S$15bn negative — so the
    second reads as a bypass rather than as the floor having been quietly
    deleted where it is still load-bearing.
    """
    m = _compute_bank_metrics(dict(VISA_LIKE), profile_name="Payment Networks")
    assert m["tbv_per_share"] == pytest.approx(5.6), (
        "the floored metric still synthesizes a positive book"
    )
    assert _compute_ggm_pb("V", "Payment Networks", dict(VISA_LIKE), SHARES) is None


def test_zero_tangible_book_also_fails():
    """`<= 0`, not `< 0`: a zero book gives a P/B multiple nothing to multiply."""
    row = {**VISA_LIKE, "tangible_book_value_per_share": 0.0}
    assert _compute_ggm_pb("V", "Payment Networks", row, SHARES) is None


def test_a_real_bank_with_positive_tangible_book_still_values():
    """The hard stop must not catch the names the method exists for.

    A DBS-shaped SG bank: BVPS S$30, reported TBVPS S$29, positive on both the
    reported and the derived basis.
    """
    row = {
        "book_value_per_share": 30.0,
        "total_equity": 75e9,
        "goodwill": 1.5e9,
        "intangible_assets": 1.0e9,
        "net_income": 11e9,
        "tangible_book_value_per_share": 29.0,
    }
    res = _compute_ggm_pb("D05.SI", "Money Center Bank (SG)", row, 2.5e9)
    assert res is not None
    value_ps, target_pb, a = res
    assert value_ps > 0 and 0.3 <= target_pb <= 4.0
    assert a["tbv_per_share"] == pytest.approx(29.0)


def test_a_bank_whose_derived_book_is_small_but_positive_still_values():
    """The over-strip case the 70% floor in `_compute_bank_metrics` exists for.

    Derived TBVPS here is 40% of book — under the floor — but positive, so the
    method runs and the conversion still reads the FLOORED metric. That is
    deliberate: removing the floor would mark a real bank down for a derivation
    artifact (JPM over-strips by ~$15B against its own convention, which retains
    MSRs as tangible).
    """
    row = {
        "book_value_per_share": 100.0,
        "total_equity": 100e9,
        "goodwill": 50e9,
        "intangible_assets": 10e9,
        "net_income": 12e9,
        "shares_outstanding": 1e9,
    }
    res = _compute_ggm_pb("JPM", "Money Center Bank", row, 1e9)
    assert res is not None
    assert _compute_bank_metrics(row, profile_name="Money Center Bank")[
        "tbv_per_share"] == pytest.approx(70.0)


def test_a_missing_tangible_book_does_not_fail_the_method():
    """Unavailable is not invented, and neither is it a failure. With no reported
    figure and no equity to derive from, the conversion is skipped exactly as
    before rather than declining a bank for a missing field."""
    row = {"book_value_per_share": 30.0, "net_income": 11e9}
    assert _compute_ggm_pb("D05.SI", "Money Center Bank (SG)", row, 2.5e9) is not None


# ── 2b: the assumptions-side floor ──────────────────────────────────────────


def test_realised_roe_is_not_computed_on_a_synthesized_book():
    """`_bank_ggm_assumptions` had the same floor on its midpoint ROE.

    With tangible equity negative the `_teq > 0` guard already declines the
    midpoint; the floor was defeating that guard by making `_teq` positive. Now
    a negative tangible book falls back to the profile's target ROE and SAYS SO
    in the provenance, instead of reporting a midpoint measured on an invented
    book.
    """
    row = {**VISA_LIKE}
    a = _bank_ggm_assumptions("V", "Payment Networks", row)
    assert "profile" in a["provenance"][0]
    assert "midpoint" not in a["provenance"][0]


def test_a_positive_tangible_book_still_earns_the_midpoint():
    """And the floor's removal does not disable the midpoint where it applies.

    Realised RoTE here is 11e9 / 72.5e9 = 15.2%, averaged with the SG profile's
    14.5% target. The ticker must not be one of the three carrying a published
    broker GGM table — a broker ROE outranks the midpoint branch entirely, which
    is how this test came to assert against "ROE 16.6% (broker)" on its first
    run.
    """
    assert MIDPOINT_TICKER not in d._BANK_GGM_OVERRIDES
    row = {
        "total_equity": 75e9, "goodwill": 1.5e9, "intangible_assets": 1.0e9,
        "net_income": 11e9, "book_value_per_share": 30.0,
        "shares_outstanding": 2.5e9,
    }
    a = _bank_ggm_assumptions(MIDPOINT_TICKER, "Money Center Bank (SG)", row)
    assert "midpoint" in a["provenance"][0]


def test_the_floors_are_gone_from_the_two_sites_named_by_the_owner():
    """Pinned by source, because a floor of this shape is easy to reintroduce
    and impossible to notice in an output that still looks like a number.

    Matched on the ASSIGNMENT, not the expression: both comments that replaced a
    floor quote the old one, so a bare substring test would pass on the very
    regression it exists to catch.
    """
    src = inspect.getsource(d)
    assert "_teq = max(_eq - _gw - _in, _eq * 0.70)" not in src
    assert "_teq = (_eq - _gw - _in) if _eq else None" in src
    # `_compute_bank_metrics` keeps its own floor, with its JPM justification.
    assert "tbv = max(equity - (goodwill or 0) - (intang or 0), equity * 0.70)" in src
