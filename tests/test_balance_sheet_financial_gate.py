"""Phase 1.2A — balance-sheet financials lose their EV, DCF and FCF legs.

The defect: 02888.HK (Standard Chartered, profile "Money Center Bank")
published a forward EV/EBITDA of HK$730 per share. Enterprise value is market
cap plus debt minus cash; for a deposit-funded business the "debt" term IS the
product, so the subtraction removes the thing being valued and the quotient has
no interpretation. No test caught it. SCHW, IBKR and HOOD were blending DCF and
FCF Yield legs built on cash flows dominated by customer-balance movement.

Two tiers, because "financial" is not one thing: Tier 1 is the profile itself
(banks, insurance, brokerage, holdcos); Tier 2 is a measurement, for the
fee-based profiles (asset managers, payment networks, exchanges, fintechs)
where a name routed there may still be float-funded in fact.

Every balance-sheet figure below is a LIVE FMP reading taken 2026-09-17 for the
named company, not a synthetic round number — the gate's threshold and its
clearing-house exemption both sit close enough to real values that a made-up
fixture would not have exercised them.
"""
from __future__ import annotations

import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.data.sector_profiles import (
    BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES,
    BALANCE_SHEET_FINANCIAL_PROFILES,
    BALANCE_SHEET_FINANCIAL_UNCLASSIFIED,
    INDUSTRY_VALUATION_PROFILES,
    TIER2_EXEMPT_PROFILES,
)

THRESHOLD = d._TIER2_CUSTOMER_BALANCE_RATIO


# ── Live balance sheets (FMP, annual, 2026-09-17) ───────────────────────────
# Frozen here on purpose. These are the readings the design was decided
# against; if the feed's mapping changes, the numbers below should be re-taken
# and the change should be a deliberate act, not a silent one.
#
# And the mapping DID change, once, before this shipped. Every payable figure
# below is FMP's balance-sheet stock `accountPayables`. Until the day these were
# pinned, `search_line_items` published the cash-flow statement's
# `accountsPayables` — a working-capital DELTA, one letter apart — under that
# same field name, so production read SCHW as 0.7700 against the 1.0292 below
# and S68.SI as 0.3135 against 0.4738. The fixtures were right and the feed was
# wrong; the disagreement is how the bug was found. Fixed in `src/tools/api.py`,
# pinned by `tests/test_cashflow_delta_shadowing.py`. Re-run
# `scripts/backtest_valuation_fixes.py --item 1.2A --show-rows` after any change
# here: its ratio column must reproduce these numbers, which is the only thing
# that proves a frozen dict still describes what production computes.

# Values are FMP's own, unrounded — do not tidy them into round numbers.
# A rounded fixture would let the ratio drift past the 0.30 threshold without
# anything in the suite noticing, which is the whole class of failure the
# fixture exists to prevent.

#: SCHW FY2025 (USD) — brokerage, US$491.0bn of assets. `otherCurrentLiabilities`
#: US$255.7bn is the bank deposit book; `accountsReceivables` US$107.5bn is
#: margin lending. Ratio 1.0292. Current liabilities US$406.5bn against current
#: assets US$215.7bn: this is NOT the matched pass-through shape ICE has.
SCHW = {"total_assets": 4.90995e11, "accounts_payable": 1.4203e11,
        "other_current_liabilities": 2.55747e11,
        "accounts_receivable": 1.07547e11,
        "total_current_assets": 2.15653e11,
        "total_current_liabilities": 4.0654e11}

#: IBKR FY2025 (USD) — ratio 1.2173. `accountPayables` alone is 77% of assets.
#: FMP reports no `totalDeposits` and no `otherCurrentLiabilities` for IBKR; the
#: ratio is carried entirely by payables plus margin receivables.
IBKR = {"total_assets": 2.0324e11, "accounts_payable": 1.5672e11,
        "other_payables": 2.17e8, "accounts_receivable": 9.0475e10,
        "total_current_assets": 1.78091e11,
        "total_current_liabilities": 1.57277e11}

#: HOOD FY2025 (USD) — ratio 0.9326.
HOOD = {"total_assets": 3.8137e10, "accounts_payable": 4.63e8,
        "other_payables": 1.1986e10, "other_current_liabilities": 4.696e9,
        "accounts_receivable": 1.842e10, "total_current_assets": 3.6305e10,
        "total_current_liabilities": 2.8771e10}

#: PYPL FY2025 (USD) — ratio 0.9913. `otherCurrentLiabilities` US$40.2bn is
#: customer funds (50% of assets); receivables are the loan and BNPL book (49%).
PYPL = {"total_assets": 8.0173e10, "accounts_payable": 2.4e8,
        "other_current_liabilities": 4.0198e10,
        "accounts_receivable": 3.9038e10, "total_current_assets": 5.9759e10,
        "total_current_liabilities": 4.6443e10}

#: V FY2025 Q3 (USD) — ratio 0.2420, i.e. BELOW the 0.30 threshold. Visa's
#: payables are settlement owed to members, not funding. This is the control
#: that proves Tier 2 measures rather than assumes: a payment network with a
#: genuinely fee-based balance sheet keeps its EV and DCF legs. It is also the
#: closest name to the threshold in the whole basket, so it is the false
#: positive the 0.30 cut has to survive.
VISA = {"total_assets": 9.9627e10, "accounts_payable": 5.55e8,
        "other_payables": 4.568e9, "other_current_liabilities": 1.5857e10,
        "accounts_receivable": 3.126e9, "total_current_assets": 3.7766e10,
        "total_current_liabilities": 3.5048e10}

#: MA FY2025 (USD) — ratio 0.2486, also below.
MASTERCARD = {"total_assets": 5.4157e10, "accounts_payable": 9.99e8,
              "other_payables": 9.14e8, "other_current_liabilities": 6.942e9,
              "accounts_receivable": 4.609e9,
              "total_current_assets": 2.3558e10,
              "total_current_liabilities": 2.2762e10}

#: ICE FY2025 (USD) — ratio 0.6135. `otherCurrentLiabilities` US$76.9bn is
#: pass-through margin and guaranty-fund collateral: current assets US$85.78bn
#: against current liabilities US$84.12bn — matched to within 2%. THE reason
#: the exemption exists. Contrast SCHW above, where the same line is 1.9x the
#: matching assets.
ICE = {"total_assets": 1.36887e11, "accounts_payable": 1.078e9,
       "other_payables": 4.437e9, "other_current_liabilities": 7.6907e10,
       "accounts_receivable": 1.552e9, "total_current_assets": 8.5778e10,
       "total_current_liabilities": 8.4116e10}

#: CME FY2025 (USD) — ratio 0.0036. FMP does not break out CME's collateral at
#: all, so it stays quiet unaided and needs no exemption.
CME = {"total_assets": 1.98548e11, "accounts_payable": 7.18e7,
       "other_current_liabilities": -7.18e7, "accounts_receivable": 6.392e8,
       "total_current_assets": 5.1876e9,
       "total_current_liabilities": 5.58e7}

#: S68.SI (SGX) H1 2026 (SGD) — ratio 0.4738 AS FLOORED. Note the negative
#: plug: `otherCurrentLiabilities` is −S$1.153bn, and an unclamped sum would
#: cancel the S$0.961bn + S$0.192bn of real payables and read 0.2213 instead,
#: i.e. below the threshold. SGX trips for a DIFFERENT reason than ICE: current
#: liabilities are only S$0.644bn against S$3.378bn of current assets, so the
#: ratio is shape-driven on a small denominator, not collateral-driven.
#:
#: Exact integers, not 6-sig-fig floats, because this is the one fixture
#: `test_the_frozen_sgx_dict_is_what_the_live_feed_produces` reconciles against
#: the feed — and at 6 figures it disagreed in the 7th decimal (0.47376997 vs
#: 0.47376888), which is a rounding artefact of the fixture, not a mapping
#: difference. A reconciliation test that has to loosen its tolerance to pass
#: has stopped reconciling.
SGX = {"total_assets": 4563092000, "accounts_payable": 961143000,
       "other_payables": 192232000, "other_current_liabilities": -1153375000,
       "accounts_receivable": 1008476000, "total_current_assets": 3377766000,
       "total_current_liabilities": 643538000}

#: 0388.HK (HKEX) FY2025 (HKD) — ratio 0.2084. Negative plug −HK$53.29bn
#: against +HK$53.08bn of payables; unclamped the payables sum goes NEGATIVE
#: (−HK$0.21bn) and the ratio would read 0.1159.
HKEX = {"total_assets": 5.80775e11, "accounts_payable": 4.6259e10,
        "other_payables": 6.816e9, "other_current_liabilities": -5.3289e10,
        "accounts_receivable": 6.7958e10, "total_current_assets": 3.86613e11,
        "total_current_liabilities": 5.57e8}

#: BN4.SI (Keppel) FY2025 (SGD) — a non-financial control at 0.2026.
KEPPEL = {"total_assets": 2.70878e10, "accounts_payable": 2.38383e9,
          "other_payables": 2.42891e8, "other_current_liabilities": 1.14924e9,
          "accounts_receivable": 1.71208e9, "total_current_assets": 7.76283e9,
          "total_current_liabilities": 5.77871e9}


def _profile(name: str) -> dict:
    """A fresh copy of a Financials profile, so no test can leak into another."""
    src = INDUSTRY_VALUATION_PROFILES["Financials"][name]
    return {"methods": [dict(m) for m in src["methods"]],
            "excluded": list(src.get("excluded") or []),
            "rationale": src.get("rationale", "")}


def _gate(profile_name: str, most_recent: dict | None):
    """Returns ``(profile, record, exception_reason, is_financial)``."""
    return d._gate_balance_sheet_financial(_profile(profile_name),
                                           profile_name, most_recent)


# ── Tier 2: the ratio itself ────────────────────────────────────────────────

@pytest.mark.parametrize("row,expected", [
    (SCHW, 1.0292), (IBKR, 1.2173), (HOOD, 0.9326), (PYPL, 0.9913),
    (VISA, 0.2420), (MASTERCARD, 0.2486),
    (ICE, 0.6135), (CME, 0.0036), (SGX, 0.4738), (HKEX, 0.2084),
    (KEPPEL, 0.2026),
])
def test_the_ratio_reproduces_the_live_readings(row, expected):
    """Pinned to four decimals against the 2026-09-17 FMP readings.

    This is the test that would catch a mapping change in the feed: if FMP
    renames `otherCurrentLiabilities` or the proxy lines move, these ratios move
    and the threshold decisions with them. Note how close Visa (0.2420) and
    Mastercard (0.2486) sit to the 0.30 cut — that is deliberate, and it is why
    the tolerance is 5e-4 rather than something loose enough to hide a drift.
    """
    ratio, breakdown = d._tier2_customer_balance_ratio(row)
    assert ratio == pytest.approx(expected, abs=5e-4)
    assert breakdown["total_assets"] == pytest.approx(row["total_assets"])


def test_a_negative_balancing_plug_cannot_subtract_real_payables():
    """S68.SI: −S$1.15bn plug against S$1.15bn of payables.

    Unclamped, the two cancel and the ratio reads 0.221 — under the threshold.
    Floored, it reads 0.474. HKEX is worse: unclamped its payables sum to
    −HK$0.21bn, a NEGATIVE customer balance. Letting a feed artefact reduce the
    numerator would understate exactly the funding dependence the gate exists
    to detect.
    """
    sgx, _ = d._tier2_customer_balance_ratio(SGX)
    hkex, _ = d._tier2_customer_balance_ratio(HKEX)
    assert sgx == pytest.approx(0.474, abs=5e-4)
    assert hkex == pytest.approx(0.208, abs=5e-4)
    # And the components are individually non-negative.
    _, b = d._tier2_customer_balance_ratio(SGX)
    assert b["customer_payables"] >= 0.0
    _, b = d._tier2_customer_balance_ratio(HKEX)
    assert b["customer_payables"] >= 0.0


def test_a_missing_denominator_is_none_not_zero():
    """No total assets means the ratio is UNKNOWN, not small.

    Zero would pass every name whose balance sheet the feed failed to deliver —
    including the banks this gate exists to catch.
    """
    assert d._tier2_customer_balance_ratio({})[0] is None
    assert d._tier2_customer_balance_ratio({"total_assets": 0})[0] is None
    assert d._tier2_customer_balance_ratio({"total_assets": -5})[0] is None
    assert d._tier2_customer_balance_ratio(None)[0] is None
    # And a None ratio never fires Tier 2.
    assert not d._is_balance_sheet_financial("FinTech", {})
    assert not d._is_balance_sheet_financial("FinTech", None)


def test_the_loan_book_is_not_double_counted():
    """`loans_receivable` (netLoans) and `loans_held_for_investment` are two
    spellings of the same book in FMP's map. Summing them would inflate the
    ratio; the proxy reads the first that reports."""
    row = {"total_assets": 100e9, "loans_receivable": 40e9,
           "loans_held_for_investment": 40e9}
    ratio, b = d._tier2_customer_balance_ratio(row)
    assert ratio == pytest.approx(0.40)
    assert b["loans_receivable"] == pytest.approx(40e9)


def test_deposits_are_counted():
    row = {"total_assets": 100e9, "total_deposits": 62e9}
    ratio, b = d._tier2_customer_balance_ratio(row)
    assert ratio == pytest.approx(0.62)
    assert b["deposits"] == pytest.approx(62e9)


def test_the_breakdown_names_the_lines_it_read():
    """A firing has to be auditable against the filing, not just trusted."""
    _, b = d._tier2_customer_balance_ratio(SCHW)
    joined = " ".join(b["lines"])
    assert "accounts_payable" in joined
    assert "other_current_liabilities" in joined
    assert "accounts_receivable" in joined


# ── Tier 1 vs Tier 2 classification ────────────────────────────────────────

@pytest.mark.parametrize("profile_name", sorted(BALANCE_SHEET_FINANCIAL_PROFILES))
def test_tier_1_fires_on_the_profile_alone(profile_name):
    """No measurement is consulted: an empty balance sheet still classifies."""
    assert d._is_balance_sheet_financial(profile_name, {})
    assert d._is_balance_sheet_financial(profile_name, None)
    assert d._is_balance_sheet_financial(profile_name, KEPPEL)


@pytest.mark.parametrize("profile_name",
                         sorted(BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES
                                - TIER2_EXEMPT_PROFILES))
def test_tier_2_is_measured_not_assumed(profile_name):
    """Same profile, two balance sheets, two answers."""
    assert d._is_balance_sheet_financial(profile_name, PYPL)      # 0.991
    assert not d._is_balance_sheet_financial(profile_name, VISA)  # 0.242


def test_a_payment_network_with_a_fee_based_balance_sheet_keeps_its_legs():
    """V at 0.242 and MA at 0.249 both sit below 0.30 and must not be stripped.

    Visa's payables are settlement owed to members. Calling that deposit
    funding would delete 0.60 of a monopoly toll-road's profile weight on a
    misreading — the false positive the threshold has to survive.
    """
    for row in (VISA, MASTERCARD):
        out, rec, exc, _fin = _gate("Payment Networks", row)
        assert rec is None and exc is None
        assert [m["name"] for m in out["methods"]] == [
            "P/E (norm)", "EV/EBITDA", "DCF", "FCF Yield"]


@pytest.mark.parametrize("profile_name", sorted(TIER2_EXEMPT_PROFILES))
def test_clearing_houses_are_exempt_even_above_the_threshold(profile_name):
    """ICE measures 0.613 and SGX 0.474 — both would be stripped without this.

    The exemption is load-bearing, not decorative: ICE's
    `otherCurrentLiabilities` of US$76.9bn is pass-through margin and
    guaranty-fund collateral, matched by US$85.8bn of current assets against
    US$84.1bn of current liabilities.
    """
    for row in (ICE, SGX, HKEX, CME):
        assert not d._is_balance_sheet_financial(profile_name, row)


@pytest.mark.parametrize("profile_name,row", [
    ("Market Infrastructure", ICE),
    ("Market Infrastructure (SG)", SGX),
])
def test_an_exemption_is_published_not_silent(profile_name, row):
    """"Exempt" and "never looked" must be distinguishable in the run row.

    The stand-down reason carries the measured ratio and the lines behind it,
    so a future reader can tell whether the exemption was exercised on real
    numbers or merely never reached.
    """
    out, rec, exc, _fin = _gate(profile_name, row)
    assert rec is None
    assert exc is not None
    assert "exempt" in exc
    assert "customer-balance ratio" in exc
    assert [m["name"] for m in out["methods"]] == [
        m["name"] for m in _profile(profile_name)["methods"]]


def test_an_exempt_profile_below_the_threshold_stays_silent():
    """CME at 0.0036 needs no stand-down note — nothing was ever near firing."""
    out, rec, exc, _fin = _gate("Market Infrastructure", CME)
    assert rec is None and exc is None


def test_the_exemption_is_about_matched_collateral_not_about_exchanges():
    """ICE and SGX both trip Tier 2, but for DIFFERENT reasons — and the
    difference decides what to do about a third exchange.

    ICE's liabilities are genuinely pass-through: US$84.1bn of current
    liabilities matched by US$85.8bn of current assets (ratio 0.98). Collateral
    held is collateral owed; neither is funding.

    SGX trips on ratio shape instead: S$0.644bn of current liabilities against
    S$3.378bn of current assets (ratio 0.19). Its payables are ordinary
    trade payables on a small denominator, not margin.

    SCHW is the counter-case that must NOT be exempted: the same
    `otherCurrentLiabilities` line, but US$406.5bn of current liabilities
    against US$215.7bn of current assets (ratio 0.53) — the liabilities are
    real funding, not held-for-others collateral. If a future profile is
    proposed for this exemption, this matched-assets test is the criterion to
    apply, not "it is an exchange".
    """
    def _matched(row):
        return row["total_current_liabilities"] / row["total_current_assets"]

    assert _matched(ICE) == pytest.approx(0.9806, abs=1e-3)   # pass-through
    assert _matched(SCHW) == pytest.approx(1.8852, abs=1e-3)  # real funding
    assert _matched(SGX) < 0.25                               # shape, not collateral
    # The exemption is scoped to the profile lists, never to a balance-sheet
    # shape, so SCHW is stripped by Tier 1 regardless of how it measures.
    assert d._is_balance_sheet_financial("Brokerage", SCHW)


def test_a_profile_in_neither_tier_is_never_consulted():
    assert not d._is_balance_sheet_financial("Mature SaaS", SCHW)
    assert not d._is_balance_sheet_financial("Memory / DRAM-NAND", SCHW)
    assert not d._is_balance_sheet_financial("", SCHW)
    assert not d._is_balance_sheet_financial(None, SCHW)


# ── The strip ───────────────────────────────────────────────────────────────

def test_brokerage_loses_the_dcf_and_fcf_legs():
    """SCHW's profile carries DCF at 0.20 and FCF Yield at 0.20.

    Reported FCF there is a deposit-flow artefact: US$397.8bn of payables and
    US$107.6bn of receivables move against US$491.0bn of assets, and a swing in
    either dwarfs the operating cash flow the ratio is built from.
    """
    out, rec, exc, _fin = _gate("Brokerage", SCHW)
    assert exc is None
    assert [m["name"] for m in out["methods"]] == ["P/E (norm)", "P/BV"]
    assert rec["raw_input_path_a"] == pytest.approx(0.40)
    assert rec["gated_output_path_b"] == 0.0
    assert rec["applied"] is True


def test_fintech_loses_every_ev_leg_and_keeps_only_earnings():
    out, rec, _, _fin = _gate("FinTech", PYPL)
    assert [m["name"] for m in out["methods"]] == ["P/E (norm)"]
    assert rec["raw_input_path_a"] == pytest.approx(0.85)


def test_fintech_stablecoin_keeps_its_forward_pe_anchor():
    out, rec, _, _fin = _gate("Fintech/Stablecoin", PYPL)
    assert [m["name"] for m in out["methods"]] == ["Forward P/E", "P/E (norm)"]
    assert rec["raw_input_path_a"] == pytest.approx(0.55)


def test_asset_manager_loses_fcf_yield_and_ev_ebitda():
    out, rec, _, _fin = _gate("Asset Manager", PYPL)
    assert [m["name"] for m in out["methods"]] == ["P/E (norm)", "Forward P/E"]
    assert rec["raw_input_path_a"] == pytest.approx(0.40)


@pytest.mark.parametrize("profile_name", sorted(BALANCE_SHEET_FINANCIAL_PROFILES))
def test_the_allowed_legs_survive_every_tier_1_profile(profile_name):
    """P/TBV, P/E (norm), Residual Income, GGM/DDM, Excess Capital stay.

    The plan's allowed set, asserted against the real taxonomy rather than a
    hand-written list: whatever survives must be a member of it.
    """
    allowed = {"P/TBV", "P/E (norm)", "Residual Income", "GGM (P/B)", "DDM",
               "Excess Capital", "P/BV", "P/E (ops)", "Embedded Value",
               "Combined Ratio Gate", "SOTP / NAV", "NAV Discount",
               "SOTP (published)", "P/FRE", "P/DE", "AUM Multiple",
               "SOTP (FRE+Carry)"}
    out, _, _, _fin = _gate(profile_name, SCHW)
    surviving = {m["name"] for m in out["methods"]}
    assert surviving <= allowed, (
        f"{profile_name}: an EV/DCF/FCF leg survived the strip — "
        f"{sorted(surviving - allowed)}")
    assert not any(d._is_stripped_balance_sheet_leg(n) for n in surviving)


def test_the_strip_is_copy_on_write():
    """Profile dicts are references into INDUSTRY_VALUATION_PROFILES. Mutating
    one leaks into every later ticker sharing it — the trap _gate_ev_revenue's
    docstring already records."""
    before = [dict(m) for m in
              INDUSTRY_VALUATION_PROFILES["Financials"]["Brokerage"]["methods"]]
    _gate("Brokerage", SCHW)
    after = INDUSTRY_VALUATION_PROFILES["Financials"]["Brokerage"]["methods"]
    assert [m["name"] for m in after] == [m["name"] for m in before]
    assert any(m["name"] == "DCF" for m in after)


def test_the_shared_anchor_flag_is_not_mutated_either():
    """Re-anchoring copies each method dict first; setting `anchor` on the copy
    must not set it on the shared profile."""
    before = [m.get("anchor") for m in
              INDUSTRY_VALUATION_PROFILES["Financials"]["FinTech"]["methods"]]
    _gate("FinTech", PYPL)
    after = [m.get("anchor") for m in
             INDUSTRY_VALUATION_PROFILES["Financials"]["FinTech"]["methods"]]
    assert after == before


def test_a_profile_whose_every_leg_would_be_stripped_is_left_intact():
    """Unreachable in today's taxonomy, and that is the point.

    An empty method list makes `_blend_methods` return None, which blanks
    `dcf_range[ticker]`, which is the known "portfolio manager printed nothing"
    symptom. Refusing to strip and logging is the safe failure: a wrong answer
    the operator can see beats no answer at all.
    """
    synthetic = {"methods": [
        {"name": "EV/EBITDA", "weight": 0.50, "anchor": True},
        {"name": "DCF", "weight": 0.30},
        {"name": "FCF Yield", "weight": 0.20},
    ]}
    out, rec, exc, fin = d._gate_balance_sheet_financial(synthetic, "FinTech", PYPL)
    assert rec is None and exc is None
    assert out is synthetic                      # untouched, not a copy
    assert len(out["methods"]) == 3
    assert out["methods"][0]["anchor"] is True
    # Refusing to strip does not un-classify the company: the shadow skip and
    # any future EV-based computation still have to stand down.
    assert fin is True


def test_a_missing_profile_passes_straight_through():
    """Profile resolution can fail (unknown ticker, empty sector). The gate must
    not be the thing that turns that into an exception."""
    for empty in (None, {}, {"methods": None}, {"methods": []}):
        out, rec, exc, fin = d._gate_balance_sheet_financial(empty, "Brokerage", SCHW)
        assert out is empty and rec is None and exc is None
        assert fin is True


# ── Re-anchoring ────────────────────────────────────────────────────────────

def test_stripping_the_anchor_promotes_the_heaviest_survivor():
    """FinTech anchors on EV/EBITDA at 0.35 — the leg being removed.

    `_anchor_method` in run_dcf_agent defaults to the literal string "DCF" and
    is only overwritten by a method carrying anchor=True. Without this, a
    stripped FinTech would publish "Anchor: DCF" on a profile whose DCF leg was
    just deleted: the same class of defect (a report claiming a method that
    contributed nothing) that this gate exists to remove.
    """
    out, rec, _, _fin = _gate("FinTech", PYPL)
    anchors = [m["name"] for m in out["methods"] if m.get("anchor")]
    assert anchors == ["P/E (norm)"]
    assert "Anchor moved EV/EBITDA" in rec["basis"]


def test_a_surviving_anchor_is_left_alone():
    """Brokerage anchors on P/E (norm), which survives. Re-anchoring it to
    itself would be a no-op at best and a weight-order mistake at worst."""
    out, rec, _, _fin = _gate("Brokerage", SCHW)
    anchors = [m["name"] for m in out["methods"] if m.get("anchor")]
    assert anchors == ["P/E (norm)"]
    assert "Anchor moved" not in rec["basis"]


def test_no_stripped_profile_can_leave_the_anchor_falling_back_to_dcf():
    """The invariant, across the whole taxonomy rather than one profile.

    For every Financials profile and every live balance sheet above, either the
    gate does not fire or the result still names an anchor. A profile with no
    anchor is what makes run_dcf_agent print "Anchor: DCF".
    """
    rows = [SCHW, IBKR, HOOD, PYPL, VISA, MASTERCARD, ICE, CME, SGX, HKEX,
            KEPPEL, {}]
    for profile_name in INDUSTRY_VALUATION_PROFILES["Financials"]:
        for row in rows:
            out, rec, _, _fin = _gate(profile_name, row)
            if rec is None:
                continue
            assert any(m.get("anchor") for m in out["methods"]), (
                f"{profile_name}: the strip removed the anchor and promoted "
                f"nothing — run_dcf_agent would publish 'Anchor: DCF'")


# ── Classification is not the same fact as "legs were stripped" ─────────────
#
# This section exists because of a bug the golden baseline caught and the unit
# tests did not. The shadow-method skip was originally keyed off the ledger
# record, and the record is only emitted when a strip removed something. Every
# bank profile carries no EV/DCF/FCF leg, so on 02888.HK — the exact name that
# published HK$730 — the record was None, the skip did not fire, and the
# forward EV/EBITDA row was computed and published unchanged. The gate was a
# no-op on the defect it was written for.

def test_a_bank_with_nothing_to_strip_still_classifies_as_financial():
    """The regression. `is_financial` must be True for a Tier 1 profile even
    when the profile has no EV leg to remove, because the shadow skip and any
    future EV-based computation key off the classification, not the record."""
    for profile_name in ("Money Center Bank", "Money Center Bank (SG)",
                         "Money Center Bank (EU)", "Regional Bank",
                         "Super-Regional Bank", "EM Bank", "EM Bank (Premium)",
                         "Bank / Lending Institution", "Investment Bank",
                         "Neo/Challenger", "Mortgage/GSE", "Insurance",
                         "Insurance (P&C)", "Holding Company", "Brokerage",
                         "WealthTech & Specialty Financials (SG)"):
        out, rec, exc, fin = _gate(profile_name, SCHW)
        assert fin is True, f"{profile_name} did not classify as a financial"
        assert exc is None


@pytest.mark.parametrize("profile_name", sorted(BALANCE_SHEET_FINANCIAL_PROFILES))
def test_the_classification_survives_a_profile_with_no_legs_at_all(profile_name):
    """An empty or missing profile dict must not suppress the classification —
    the company is a bank whether or not profile resolution succeeded."""
    for empty in (None, {}, {"methods": None}, {"methods": []}):
        _out, _rec, _exc, fin = d._gate_balance_sheet_financial(
            empty, profile_name, SCHW)
        assert fin is True, f"{profile_name} with {empty!r}"


def test_a_bank_profile_with_a_synthetic_ev_leg_strips_it_and_records():
    """Banks carry no EV leg TODAY. If one is ever added — or a calibration
    overlay injects one — the strip must fire on the same profile that
    currently reports nothing to strip."""
    synthetic = {"methods": [
        {"name": "GGM (P/B)", "weight": 0.50, "anchor": True},
        {"name": "EV/EBITDA", "weight": 0.30},
        {"name": "DCF", "weight": 0.20},
    ]}
    out, rec, exc, fin = d._gate_balance_sheet_financial(
        synthetic, "Money Center Bank", SCHW)
    assert fin is True
    assert exc is None
    assert [m["name"] for m in out["methods"]] == ["GGM (P/B)"]
    assert rec["raw_input_path_a"] == pytest.approx(0.50)
    assert "tier 1" in rec["basis"]


@pytest.mark.parametrize("profile_name",
                         sorted(BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES))
def test_a_conditional_profile_below_the_threshold_does_not_classify(profile_name):
    """The mirror of the regression above: Visa at 0.2420 must be False, or its
    Forward EV/EBITDA row would be suppressed too and a legitimate multiple
    would disappear from the report."""
    for row in (VISA, MASTERCARD, CME, KEPPEL):
        _out, _rec, _exc, fin = _gate(profile_name, row)
        assert fin is False, f"{profile_name} classified on ratio < {THRESHOLD}"


@pytest.mark.parametrize("profile_name", sorted(TIER2_EXEMPT_PROFILES))
def test_an_exempt_profile_does_not_classify_even_when_it_measures_high(profile_name):
    """ICE at 0.6135 is exempt, so `is_financial` is False and its Forward
    EV/EBITDA shadow row stays. Exempt means treated as a non-financial, not
    merely "not stripped"."""
    for row in (ICE, SGX, HKEX):
        _out, _rec, exc, fin = _gate(profile_name, row)
        assert fin is False


def test_the_shadow_skip_is_keyed_off_the_classification_not_the_record():
    """Source-level pin on the wiring. `_bsf_is_financial` and `_bsf_stripped`
    are DIFFERENT variables and the shadow guard must use the former; the
    ledger, the progress message and the forward flag use the latter.

    Written as a source assertion because the shadow list is built deep inside
    the scenario loop, unreachable from a unit test.
    """
    src = inspect.getsource(d.run_dcf_agent)
    assert "_bsf_is_financial" in src, (
        "the classification is no longer unpacked from the gate")
    assert "if not _bsf_is_financial:" in src, (
        "the Forward EV/EBITDA shadow guard is not keyed off the "
        "classification — a bank with no EV leg to strip would compute it")
    assert "if not _bsf_stripped:" not in src, (
        "a shadow guard is still keyed off `_bsf_stripped`, which is None for "
        "every bank profile and was the bug the golden baseline caught")
    # The ledger and its flags stay keyed off the record, so no-op firings are
    # not written.
    assert "if _bsf_stripped:" in src


# ── The ledger record ───────────────────────────────────────────────────────

def test_a_tier_1_profile_with_nothing_to_strip_records_nothing():
    """Every bank, insurance, GSE and holdco profile already carries no EV/DCF
    leg. Recording a firing that removed nothing would put a no-op in the
    forward ledger on every bank run."""
    for profile_name in ("Money Center Bank", "Money Center Bank (SG)",
                         "Regional Bank", "Insurance", "Insurance (P&C)",
                         "Mortgage/GSE", "Holding Company",
                         "Bank / Lending Institution", "Investment Bank",
                         "Neo/Challenger", "EM Bank", "EM Bank (Premium)",
                         "Super-Regional Bank", "Alt Asset Manager",
                         "WealthTech & Specialty Financials (SG)"):
        out, rec, exc, _fin = _gate(profile_name, SCHW)
        assert rec is None, f"{profile_name} recorded a no-op strip"
        assert exc is None
        assert out["methods"] == _profile(profile_name)["methods"]


def test_the_record_shape_matches_the_forward_ledger_contract():
    """gate_forward.py scores whatever is in gate_evaluations, so the keys are
    a contract. tests/test_gate_backtest.py additionally counts the two path
    halves in the module source and requires them equal."""
    _, rec, _, _fin = _gate("Brokerage", SCHW)
    assert rec["gate_id"] == "GATE_BALANCE_SHEET_FINANCIAL"
    assert rec["metric"] == "ev_dcf_weight_share"
    assert isinstance(rec["raw_input_path_a"], float)
    assert isinstance(rec["gated_output_path_b"], float)
    assert rec["applied"] is True
    assert "Brokerage" in rec["basis"]
    assert "tier 1" in rec["basis"]


def test_a_tier_2_record_states_the_measurement_it_acted_on():
    """The basis is the only thing in the run row that says WHY. A tier 2
    firing that did not carry the ratio and its lines would be unauditable."""
    _, rec, _, _fin = _gate("FinTech", PYPL)
    assert "tier 2" in rec["basis"]
    assert "0.991" in rec["basis"]
    assert "other_current_liabilities" in rec["basis"]


def test_the_gate_id_is_present_once_and_both_path_halves_stay_balanced():
    """Two literal occurrences, one per metric the gate can emit: the strip
    (`ev_dcf_weight_share`, built inside the gate function) and the shadow-row
    suppression (`forward_ev_ebitda_row`, built at the wiring site because only
    there is `forward_consensus` known). A third would mean an unreviewed
    metric.

    The path-halve balance is the assertion `tests/test_gate_backtest.py` makes
    across the whole module: every `raw_input_path_a` needs its
    `gated_output_path_b`, or the forward scorer reads a firing with no
    counterfactual and scores it UNSCORABLE forever.
    """
    src = inspect.getsource(d)
    assert src.count('"GATE_BALANCE_SHEET_FINANCIAL"') == 2
    assert src.count('"raw_input_path_a"') == src.count('"gated_output_path_b"')
    assert src.count('"metric":') >= 2


def test_a_bank_suppressing_the_shadow_row_is_recorded_as_a_firing():
    """The ledger has to see the gate fire on 02888.HK and DBS, or it is blind
    to the two names whose published row WAS the defect.

    Their profiles carry no EV leg, so the strip emits nothing — and a gate that
    only records strips would produce zero firings for every bank in production.
    The forward test's ≥10-scoreable-firings bar could then only ever be met
    from Brokerage and Tier 2 names, and the review rule would never have
    enough evidence to demote a gate that was misbehaving on banks.
    """
    src = inspect.getsource(d.run_dcf_agent)
    assert '"metric": "forward_ev_ebitda_row"' in src
    # Emitted only when a row would actually have been computed. Without this
    # guard the firing count inflates on every bank run that had no forward
    # consensus, and the acceptance bar would be measured on no-ops.
    assert "and forward_consensus is not None" in src
    # And it must not double-record alongside a real strip.
    assert "_bsf_is_financial and not _bsf_stripped" in src


def test_a_suppression_record_does_not_claim_an_iv_moved():
    """The basis has to say the IV is unchanged. A reader of the run row who
    sees a gate firing on a bank and no IV movement needs to know that is the
    design, not a silent failure."""
    src = inspect.getsource(d.run_dcf_agent)
    i = src.index('"metric": "forward_ev_ebitda_row"')
    basis = src[i:i + 1400]
    assert "intrinsic value is unchanged" in basis
    assert "IV unchanged" in src[i - 200:i + 1800]


def test_the_suppression_record_does_not_store_the_number_it_refused():
    """Path A is 1.0 (row published), not HK$730. Recomputing the value in
    order to store it would re-introduce the EV bridge the gate exists to
    refuse, and would put a meaningless per-share figure back into the payload
    that the run row serialises."""
    src = inspect.getsource(d.run_dcf_agent)
    i = src.index('"metric": "forward_ev_ebitda_row"')
    block = src[i:i + 1200]
    assert '"raw_input_path_a": 1.0' in block
    assert '"gated_output_path_b": 0.0' in block


# ── Guards against silent drift ─────────────────────────────────────────────

def test_every_financials_profile_is_classified_exactly_once():
    """A new Financials profile must be a decision, not a default.

    Without this, adding "Specialty Lender (SG)" would silently fall into
    neither tier and keep its EV legs — the defect returning through the one
    door nobody was watching.
    """
    taxonomy = set(INDUSTRY_VALUATION_PROFILES["Financials"])
    classified = (BALANCE_SHEET_FINANCIAL_PROFILES
                  | BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES
                  | set(BALANCE_SHEET_FINANCIAL_UNCLASSIFIED))
    assert taxonomy - classified == set(), (
        f"Financials profiles in no tier: {sorted(taxonomy - classified)}")
    assert classified - taxonomy == set(), (
        f"tier lists name profiles that no longer exist: "
        f"{sorted(classified - taxonomy)}")
    overlaps = (BALANCE_SHEET_FINANCIAL_PROFILES
                & BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES)
    assert not overlaps, f"profile in both tiers: {sorted(overlaps)}"
    assert not (set(BALANCE_SHEET_FINANCIAL_UNCLASSIFIED)
                & (BALANCE_SHEET_FINANCIAL_PROFILES
                   | BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES))


def test_exempt_profiles_are_a_subset_of_the_conditional_tier():
    """An exemption from Tier 2 only means something for a profile that would
    otherwise be measured by it."""
    assert TIER2_EXEMPT_PROFILES <= BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES


def _all_method_names() -> set[str]:
    names = set()
    for profiles in INDUSTRY_VALUATION_PROFILES.values():
        for prof in profiles.values():
            for m in (prof.get("methods") or []):
                if isinstance(m, dict) and m.get("name"):
                    names.add(m["name"])
    return names


def test_every_ev_leg_in_the_taxonomy_is_caught_by_the_prefix_test():
    """Prefix-matched rather than enumerated, so a new EV/* leg on a financial
    profile is caught without anyone remembering to extend a list. This asserts
    the predicate actually covers today's taxonomy."""
    ev = {n for n in _all_method_names() if n.startswith("EV/")}
    assert ev, "no EV/* methods in the taxonomy — the test has nothing to check"
    assert all(d._is_enterprise_value_leg(n) for n in ev)


def test_every_dcf_leg_in_the_taxonomy_is_caught():
    """Includes the names the dispatcher does NOT project —
    "Depleting Asset DCF (Finite Life, No TV)" is in no _DCF_* family, so the
    substring test is what catches it."""
    dcf = {n for n in _all_method_names() if "DCF" in n}
    assert dcf, "no DCF methods in the taxonomy"
    assert all(d._is_dcf_leg(n) for n in dcf)
    assert "Depleting Asset DCF (Finite Life, No TV)" in dcf
    assert d._is_dcf_leg("Depleting Asset DCF (Finite Life, No TV)")


def test_the_equity_multiples_that_must_survive_are_not_caught():
    """P/* legs price the equity directly and need no EV bridge. Stripping one
    would leave a bank with nothing at all."""
    for name in ("P/E (norm)", "P/E", "P/BV", "P/TBV", "Forward P/E",
                 "Residual Income", "GGM (P/B)", "DDM", "Excess Capital",
                 "Embedded Value", "SOTP / NAV", "NAV Discount", "P/FRE",
                 "AUM Multiple", "P/E (ops)", "Combined Ratio Gate"):
        assert not d._is_stripped_balance_sheet_leg(name), name


# ── Feed mapping ────────────────────────────────────────────────────────────

def test_the_proxy_lines_reach_the_row_builder():
    """The four generic lines the Tier 2 proxy reads must be (a) mapped from
    FMP's camelCase and (b) copied into the series. Four prior fields were read
    without being requested and were None on every row for years."""
    from src.tools.api import _BALANCE_MAP
    for fmp_key, ours in (("accountPayables", "accounts_payable"),
                          ("otherPayables", "other_payables"),
                          ("otherCurrentLiabilities", "other_current_liabilities"),
                          ("accountsReceivables", "accounts_receivable"),
                          ("totalDeposits", "total_deposits"),
                          ("netLoans", "loans_receivable"),
                          ("loansHeldForInvestment", "loans_held_for_investment")):
        assert _BALANCE_MAP.get(fmp_key) == ours, fmp_key

    # NOT `accountsPayables`. That spelling is the cash-flow statement's
    # working-capital DELTA, and mapping it here published a flow as a level on
    # 36 of 36 names measured -- which is how this gate's own Tier-2 ratio came
    # to read 0.3135 on S68.SI against a 0.4738 truth. See
    # tests/test_cashflow_delta_shadowing.py, which owns the invariant.
    assert "accountsPayables" not in _BALANCE_MAP

    row_src = inspect.getsource(d._extract_annual_series)
    for field in ("accounts_payable", "accounts_receivable", "other_payables",
                  "other_current_liabilities", "total_deposits",
                  "loans_receivable", "loans_held_for_investment"):
        assert f'getattr(li, "{field}"' in row_src, field


#: S68.SI (SGX) H1 2026, all four FMP /stable statements as returned on
#: 2026-09-17, trimmed to the keys that matter. The cash-flow row carries
#: `accountsPayables` = S$229.8mn -- a working-capital DELTA, one letter apart
#: from the balance sheet's `accountPayables` = S$961.1mn STOCK. Feeding both
#: through the real merge and translation is what proves the frozen `SGX` dict
#: above describes what production computes rather than what a filing says.
_SGX_DATE = "2026-06-30"
_SGX_INCOME = {"date": _SGX_DATE, "period": "FY", "reportedCurrency": "SGD",
               "revenue": 1559469000, "netIncome": 698411000}
_SGX_BALANCE = {
    "date": _SGX_DATE, "period": "FY", "reportedCurrency": "SGD",
    "totalAssets": 4563092000,
    "accountPayables": 961143000,
    "otherPayables": 192232000,
    "taxPayables": 0,
    "totalPayables": 1153375000,
    "accountsReceivables": 1008476000,
    "netReceivables": 1139570000,
    "otherCurrentLiabilities": -1153375000,
    "totalCurrentAssets": 3377766000,
    "totalCurrentLiabilities": 643538000,
}
_SGX_CASHFLOW = {
    "date": _SGX_DATE, "period": "FY", "reportedCurrency": "SGD",
    "accountsPayables": 229822000,
    "accountsReceivables": -234134000,
    "operatingCashFlow": 874419000,
    "capitalExpenditure": -81922000,
    "changeInWorkingCapital": -4312000,
}


def test_the_frozen_sgx_dict_is_what_the_live_feed_produces(monkeypatch):
    """Reconcile the frozen fixture against the feed, end to end.

    Every ratio assertion in this file is computed from a hand-frozen dict. That
    is only evidence if the dict is what production actually reads -- and for one
    session it was not: `accounts_payable` was being served from the cash-flow
    statement's delta, so SGX read 0.3135 in production against the 0.4738
    pinned here, a margin of 0.0135 over a 0.30 threshold that the Market
    Infrastructure exemption is justified against. ~140 unit tests passed
    throughout, because not one of them crossed from the fixture into the feed.

    This test is that crossing. It runs the real `search_line_items` merge and
    the real `_extract_annual_series` row builder over real payloads containing
    BOTH spellings, and asserts the ratio lands on the frozen number.
    """
    import src.tools.api as api

    def fake_get(path, params, api_key=None, uncap=False):
        if path.endswith("/income-statement"):
            return [_SGX_INCOME]
        if path.endswith("/balance-sheet-statement"):
            return [_SGX_BALANCE]
        if path.endswith("/cash-flow-statement"):
            return [_SGX_CASHFLOW]
        if path.endswith("/ratios"):
            return [{"date": _SGX_DATE, "period": "FY"}]
        return []

    monkeypatch.setattr(api, "_fmp_get", fake_get)
    monkeypatch.setattr(api, "_statement_cache_get", lambda key: None)
    monkeypatch.setattr(api, "_statement_cache_put", lambda key, value: None)
    monkeypatch.setattr(api.time, "sleep", lambda *_a, **_k: None)

    rows = api.search_line_items(
        "S68.SI",
        ["revenue", "total_assets", "total_deposits", "loans_receivable",
         "loans_held_for_investment", "accounts_payable", "accounts_receivable",
         "other_payables", "other_current_liabilities"],
        end_date="2026-09-17", period="annual", limit=3, api_key="x")
    assert len(rows) == 1, "the revenue>0 filter in _extract_annual_series ate it"

    series, _ccy = d._extract_annual_series(rows)
    assert len(series) == 1
    most_recent = series[-1]

    # The field the whole bug turned on, checked before the ratio so a failure
    # names the cause instead of the symptom.
    assert most_recent["accounts_payable"] == pytest.approx(
        SGX["accounts_payable"]), (
        "the feed served the cash-flow delta, not the balance-sheet stock")

    ratio, breakdown = d._tier2_customer_balance_ratio(most_recent)
    frozen, _ = d._tier2_customer_balance_ratio(SGX)
    assert ratio == pytest.approx(frozen, abs=1e-9), (
        f"live-feed path gives {ratio}, frozen fixture gives {frozen}")
    assert ratio == pytest.approx(0.4738, abs=5e-4), ratio
    # And the negative plug really is present in what the feed delivered, or the
    # flooring this fixture exists to exercise is not being exercised.
    assert most_recent["other_current_liabilities"] < 0
    assert breakdown["customer_payables"] == pytest.approx(
        SGX["accounts_payable"] + SGX["other_payables"])


def test_forward_pe_stays_computed_for_a_balance_sheet_financial():
    """Only the EV-based shadow row goes. Forward P/E is an equity multiple —
    no EV bridge, no deposit subtraction — and for a bank it is one of the few
    remaining cross-checks on the GGM and Residual Income legs.

    Asserted by ORDER in the source, because the shadow list is built deep
    inside the scenario loop where a unit test cannot reach it. "Shadow" is not
    harmless: a shadow method is excluded from the blend but still surfaced in
    the per-method table, and that table is where 02888.HK's HK$730 forward
    EV/EBITDA was published.
    """
    src = inspect.getsource(d.run_dcf_agent)
    fwd_pe = src.index('_shadow_methods.append("Forward P/E")')
    guard = src.index("if not _bsf_is_financial:")
    fwd_ev = src.index('_shadow_methods.append("Forward EV/EBITDA")')
    assert fwd_pe < guard < fwd_ev, (
        "Forward P/E must be appended BEFORE the balance-sheet-financial guard "
        "and Forward EV/EBITDA after it, so a bank keeps one and loses the other")


def test_the_gate_is_wired_into_run_dcf_agent():
    src = inspect.getsource(d.run_dcf_agent)
    assert "_gate_balance_sheet_financial(" in src
    assert "gate_evaluations.append(_bsf_gate_rec)" in src
    assert "Balance-sheet-financial gate:" in src
