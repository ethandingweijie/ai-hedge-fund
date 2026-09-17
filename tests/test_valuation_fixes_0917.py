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


# ── Decision 4 (2026-09-17): verify the Brokerage taxonomy ───────────────────
#
# The owner declined to re-admit the FCFF DCF on brokerages and asked instead
# that the profile taxonomy be verified. No code changed for this decision;
# these tests are the verification, and one of them records a gap in the
# remedy the decision names.


def _schw_projection() -> dict:
    import json
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "golden", "snapshots.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)["SCHW"]["projection"]


def test_the_brokerage_profile_declares_exactly_four_legs():
    """The input to the strip, read from the profile table rather than recalled.

    Brokerage declares P/E (norm) 0.35 (anchor), P/BV 0.25, DCF 0.20 and FCF
    Yield 0.20, summing to 1.00. What survives the strip — and the 0.35/0.25
    renormalisation that follows — is pinned by the next test against SCHW's
    published output; this one exists so a future edit to the declared weights
    fails here, at the source, instead of only as a moved snapshot.
    """
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    legs = {m["name"]: m["weight"] for prof in P.values()
            for name, cfg in prof.items() if name == "Brokerage"
            for m in cfg["methods"]}
    assert legs == {"P/E (norm)": 0.35, "P/BV": 0.25,
                    "DCF": 0.20, "FCF Yield": 0.20}
    assert sum(legs.values()) == pytest.approx(1.0)
    # The two Tier 1 removes are both present to be removed.
    assert {"DCF", "FCF Yield"} <= set(legs)


def test_schws_published_weights_are_the_survivors_renormalised():
    """0.35/0.60 and 0.25/0.60, in all three scenarios, with no DCF bucket.

    This is the pinned output of decision 4's "keep 1.2A intact for
    Brokerages": SCHW's IV comes from two book-and-earnings legs and nothing
    that touches its operating cash flow.
    """
    proj = _schw_projection()
    assert proj["profile"] == "Brokerage"
    assert proj["profile_fallback_used"] is False
    for scen in ("bear", "base", "bull"):
        assert proj[f"scenarios.{scen}.weight_dcf"] == 0.0
        assert proj[f"scenarios.{scen}.weight_multi"] == 1.0
        w = {proj[f"scenarios.{scen}.effective_weights.{i}.method"]:
             proj[f"scenarios.{scen}.effective_weights.{i}.weight"]
             for i in (0, 1)}
        assert w == {"P/E (norm)": pytest.approx(0.35 / 0.60, abs=1e-6),
                     "P/BV": pytest.approx(0.25 / 0.60, abs=1e-6)}
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)
        assert proj[f"scenarios.{scen}.methods_used"] == ["P/BV", "P/E (norm)"]


def test_brokerage_is_tier_1_and_tier_2_never_runs_for_it():
    """The classification rests on the name, and that is worth saying plainly.

    `_is_balance_sheet_financial` checks Tier 1 (profile is a balance-sheet
    business by construction) and returns True before Tier 2 is reached. Tier 2
    only consults the measured customer-balance ratio for profiles in the
    CONDITIONAL set — asset managers, payment networks, fintech, market
    infrastructure — and Brokerage is not in it. So there is no second live
    route here: SCHW's DCF weight of 0.0 follows from its profile name alone.

    The measurement still matters as a robustness check. The figures below are
    the ones the Brokerage evidence comment records for SCHW's FY2025 —
    US$397.8bn of customer payables plus US$107.6bn of receivables on
    US$491.0bn of assets — mapped onto the line keys the ratio actually reads,
    so the row is constructed from recorded evidence rather than fetched. The
    ratio is 1.03, which is 3.4x the 0.30 threshold. If someone later moved
    Brokerage to conditional to make the classification "evidence-based", the
    evidence agrees and nothing would change. What would change is a demotion to
    the UNCLASSIFIED set, which no measurement rescues; the membership
    assertions below exist so that move trips a test instead of silently
    re-admitting an FCFF DCF on customer-deposit cash flow.
    """
    from src.data.sector_profiles import (
        BALANCE_SHEET_FINANCIAL_PROFILES,
        BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES,
        BALANCE_SHEET_FINANCIAL_UNCLASSIFIED,
    )
    from src.agents.analysis.dcf_agent import (
        _TIER2_CUSTOMER_BALANCE_RATIO, _is_balance_sheet_financial,
        _tier2_customer_balance_ratio,
    )
    assert "Brokerage" in BALANCE_SHEET_FINANCIAL_PROFILES
    assert "Brokerage" not in BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES
    assert "Brokerage" not in BALANCE_SHEET_FINANCIAL_UNCLASSIFIED

    schw_row = {"total_assets": 491.0e9, "accounts_payable": 397.8e9,
                "accounts_receivable": 107.6e9}
    ratio, breakdown = _tier2_customer_balance_ratio(schw_row)
    assert ratio == pytest.approx(1.029, abs=0.005)
    assert ratio >= _TIER2_CUSTOMER_BALANCE_RATIO * 3.0
    assert breakdown["customer_payables"] == pytest.approx(397.8e9)
    # The live classification path, on both the real row and a row the feed
    # stripped: Tier 1 answers from the name, so a missing balance sheet still
    # classifies correctly and does not silently pass as non-financial.
    assert _is_balance_sheet_financial("Brokerage", schw_row) is True
    assert _is_balance_sheet_financial("Brokerage", {}) is True
    assert _is_balance_sheet_financial("Brokerage", None) is True


def test_the_owners_brokerage_remedy_names_legs_the_profile_does_not_declare():
    """THE FINDING. Decision 4 defers brokerage undervaluation to "ensuring
    Forward P/E and Residual Income / Excess Capital legs use normalized
    earnings and through-cycle NIM rather than depressed trough provisions".

    Brokerage declares no Residual Income leg and no Excess Capital leg, and
    neither does any non-bank profile: every profile in the taxonomy carrying
    those two legs is a bank. So implementing the deferred direction is not a
    normalisation of existing legs — it is ADDING two legs to the Brokerage
    profile, with weights, and taking them from somewhere. That is a taxonomy
    change with its own golden diff, and it should not be described as a
    tuning exercise when it is picked up.

    "Forward P/E" likewise maps to `P/E (norm)` here; there is no leg by that
    name in this profile.
    """
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    brokerage = None
    ri, ec, fwd = [], [], []
    for prof in P.values():
        for name, cfg in prof.items():
            names = [m["name"] for m in cfg.get("methods", [])]
            if name == "Brokerage":
                brokerage = names
            if "Residual Income" in names:
                ri.append(name)
            if "Excess Capital" in names:
                ec.append(name)
            if "Forward P/E" in names:
                fwd.append(name)
    assert brokerage is not None
    assert "Residual Income" not in brokerage
    assert "Excess Capital" not in brokerage
    assert "Forward P/E" not in brokerage
    assert "P/E (norm)" in brokerage

    # Measured, not recalled: both legs are declared by exactly the same ten
    # bank profiles, so the owner's phrase names one bank-only mechanism twice.
    assert sorted(ri) == sorted(ec) == [
        "Bank / Lending Institution", "EM Bank", "EM Bank (Premium)",
        "Investment Bank", "Money Center Bank", "Money Center Bank (EU)",
        "Money Center Bank (SG)", "Neo/Challenger", "Regional Bank",
        "Super-Regional Bank",
    ]
    # "Forward P/E" DOES exist in the taxonomy — on other profiles — so this is
    # a naming mismatch on Brokerage, not a missing concept. Recorded so nobody
    # adds a duplicate leg under a second name.
    assert fwd and "Brokerage" not in fwd

    # Asserted on the shared marker rather than a hand-list, so a future profile
    # that genuinely should carry these legs fails here and gets thought about.
    from src.data.sector_profiles import BALANCE_SHEET_FINANCIAL_PROFILES
    non_bank = [c for c in ri if c not in BALANCE_SHEET_FINANCIAL_PROFILES]
    assert non_bank == [], non_bank


def test_schws_industry_key_is_the_cohort_whose_fcf_median_was_corrupt():
    """Cross-link to decision 5, and the reason SCHW is the exposure case.

    SCHW maps to the industry key `Financial - Capital Markets`, which is
    exactly the cohort whose live `fcf_yield` median measured -0.003152 in both
    the `all` (18 peers) and `large` (10 peers) cohorts on production. The
    profile-level static table for Brokerage carries a valid 0.055, so the
    corrupt figure only reaches the engine when the lookup falls through from
    profile to industry — which is why the golden baseline never saw it and why
    this was a production-only defect.

    V is the contrast that proves the cohort is the variable: same sector,
    industry key `Financial - Credit Services`, whose medians measured +0.0602
    (all) and +0.0372 (large).
    """
    from src.data.sector_profiles import TICKER_SECTOR_LOOKUP as T
    assert T["SCHW"][2] == "Financial - Capital Markets"
    assert T["SCHW"][1] == "Brokerage"
    assert T["V"][2] == "Financial - Credit Services"
    from src.data.regional_comps import MIN_VALID_FCF_YIELD
    # The static profile table stays valid under the new band, so the fallback
    # path is unaffected by decision 5 and only the live cohort path was broken.
    from src.data.sector_profiles import SECTOR_PEER_MULTIPLES as S
    assert S["Brokerage"]["fcf_yield"] == 0.055
    assert S["Brokerage"]["fcf_yield"] > MIN_VALID_FCF_YIELD
