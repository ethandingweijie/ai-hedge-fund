"""Consumer Discretionary archetype gates: NKE, ONON, EL, 2020.HK.

This module is the brief's test suite, landed VERBATIM as supplied, plus an
engine-facing section underneath it. The two halves answer different questions
and the split is deliberate — see the banner at Section 0 before reading a pass
count as coverage.

Run:
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
      -m pytest tests/test_consumer_discretionary_gates.py -v
"""
import pytest
import math
import re
import sys

# ----------------------------------------------------------------------
# Gate Invariant 1: Inventory Bullwhip & Days Sales of Inventory (NKE)
# ----------------------------------------------------------------------
def test_gate_inventory_bullwhip_triggers_margin_cap():
    """
    Asserts that an expansion of Days Sales of Inventory (DSI) > 25 days over
    historical median forces a terminal EBIT margin cap (reflecting markdown risk).
    """
    # NKE-like scenario: Inventory swells from $5.2B to $8.4B while sales flatline
    history = [
        {"revenue": 51_000, "cogs": 28_000, "inventory": 8_400, "ebit": 5_800},  # Current DSI: 109.5d
        {"revenue": 50_000, "cogs": 27_000, "inventory": 5_400, "ebit": 6_200},  # Past DSI: 73.0d
        {"revenue": 46_000, "cogs": 25_000, "inventory": 5_100, "ebit": 6_500},  # Past DSI: 74.5d
        {"revenue": 44_000, "cogs": 24_000, "inventory": 4_800, "ebit": 6_100},  # Past DSI: 73.0d
    ]

    current_dsi = (history[0]["inventory"] / history[0]["cogs"]) * 365.0
    past_dsi = [(h["inventory"] / h["cogs"]) * 365.0 for h in history[1:]]
    median_dsi = sorted(past_dsi)[len(past_dsi) // 2]

    dsi_expansion = current_dsi - median_dsi
    assert dsi_expansion > 25.0, f"DSI expanded by {dsi_expansion:.1f} days"

    # Engine logic: Cap terminal margin to the lowest margin of the trailing 3 years
    trailing_3y_margins = [h["ebit"] / h["revenue"] for h in history[:3]]
    capped_terminal_margin = min(trailing_3y_margins)

    # Verify that the engine does not extrapolate the prior peak margin (14.1%)
    assert capped_terminal_margin <= 0.115
    assert capped_terminal_margin < (history[2]["ebit"] / history[2]["revenue"])


# ----------------------------------------------------------------------
# Gate Invariant 2: Hyper-Growth Reinvestment Penalty (ONON)
# ----------------------------------------------------------------------
def test_gate_high_growth_requires_capital_reinvestment():
    """
    Asserts that brands growing > 15% cannot produce FCF without an explicit
    incremental reinvestment charge (Sales-to-Capital = 1.85x).
    """
    # ONON-like scenario: Compounding top line by 28%
    rev_base = 2_000.0
    g1 = 0.28
    projected_delta_rev = rev_base * g1  # $560M incremental revenue
    operating_cash_flow_unadjusted = 380.0  # Reported strong operating cash flow

    # Invariant: If g1 > 0.15, free cash flow must deduct delta_rev / sales_to_capital
    sales_to_capital_ratio = 1.85
    reinvestment_charge = projected_delta_rev / sales_to_capital_ratio  # $302.7M

    adjusted_fcf = operating_cash_flow_unadjusted - reinvestment_charge

    assert g1 > 0.15
    assert reinvestment_charge > 0
    # Adjusted cash flow must reflect the working capital and capex burden of 28% growth
    assert adjusted_fcf == pytest.approx(77.3, rel=1e-2)
    assert adjusted_fcf < (operating_cash_flow_unadjusted * 0.30)


# ----------------------------------------------------------------------
# Gate Invariant 3: Trough/Peak Margin Normalization (EL)
# ----------------------------------------------------------------------
def test_gate_trough_margin_routes_to_normalized_ebit():
    """
    Asserts that when current operating margin deviates > 40% from the 5y median
    (e.g., travel retail channel shock), valuation bypasses unadjusted forward P/E.
    """
    # EL-like scenario: Operating margin collapses to 5.2% vs historical 14.5%
    historical_margins = [0.052, 0.138, 0.152, 0.145, 0.141]  # Median = 0.141
    current_margin = historical_margins[0]
    median_margin = sorted(historical_margins)[len(historical_margins) // 2]

    deviation = abs(current_margin - median_margin) / median_margin
    assert deviation > 0.40, f"Margin deviation is {deviation:.1%}"

    # Engine Decision Gate: If True, do NOT use current year EPS for forward multiples
    route_to_normalized = deviation > 0.40
    assert route_to_normalized is True

    # Assert normalized EBIT baseline replaces depressed trough EBIT
    current_revenue = 15_600.0
    trough_ebit = current_revenue * current_margin      # $811.2M
    normalized_ebit = current_revenue * median_margin  # $2,199.6M

    assert normalized_ebit > (trough_ebit * 2.5)


# ----------------------------------------------------------------------
# Gate Invariant 4: Associate Equity Add-Back & FX Parity (2020.HK)
# ----------------------------------------------------------------------
def test_gate_associate_equity_transparency_and_fx_parity():
    """
    Asserts that unconsolidated JV/associates (Amer Sports / Arc'teryx) > 10% of equity
    are explicitly added to EV and converted using consistent listing FX rates.
    """
    # Anta (2020.HK) Balance Sheet (reported in RMB)
    balance_sheet_rmb = {
        "market_cap_hkd": 230_000.0,
        "total_equity_rmb": 48_000.0,
        "investments_in_associates_rmb": 9_800.0,  # ~20.4% of book equity
        "total_debt_rmb": 12_000.0,
        "cash_and_sti_rmb": 28_000.0,
        "diluted_shares": 2_830.0,
    }
    fx_hkd_rmb = 0.92  # 1 HKD = 0.92 RMB (or 1 RMB = 1.087 HKD)

    # Invariant: Associate book value check
    associate_ratio = (
        balance_sheet_rmb["investments_in_associates_rmb"] /
        balance_sheet_rmb["total_equity_rmb"]
    )
    assert associate_ratio > 0.10, "Associates are material and must be added back"

    # Enterprise Value to Equity conversion with associate equity add-back
    core_operating_ev_rmb = 180_000.0  # From DCF + Multiple legs
    net_cash_rmb = balance_sheet_rmb["cash_and_sti_rmb"] - balance_sheet_rmb["total_debt_rmb"]
    associate_addback_rmb = balance_sheet_rmb["investments_in_associates_rmb"]

    total_equity_value_rmb = core_operating_ev_rmb + net_cash_rmb + associate_addback_rmb

    # Convert to listing currency (HKD) per share
    total_equity_value_hkd = total_equity_value_rmb / fx_hkd_rmb
    implied_iv_per_share_hkd = total_equity_value_hkd / balance_sheet_rmb["diluted_shares"]

    # Verify associate add-back provides an explicit ~HK$3.77/share equity contribution
    associate_contribution_per_share = (
        (associate_addback_rmb / fx_hkd_rmb) / balance_sheet_rmb["diluted_shares"]
    )
    assert pytest.approx(associate_contribution_per_share, rel=1e-2) == 3.76
    assert implied_iv_per_share_hkd > (core_operating_ev_rmb / fx_hkd_rmb / balance_sheet_rmb["diluted_shares"])


# ----------------------------------------------------------------------
# Gate Invariant 5: Gross Margin Qualification for Multiples
# ----------------------------------------------------------------------
def test_terminal_multiple_gross_margin_qualification():
    """
    Asserts that consumer discretionary names cannot capture luxury terminal multiples
    (e.g., 25x+) unless gross margins clear the luxury floor (> 65%).
    """
    profiles = {
        "ONON": {"gross_margin": 0.595, "peer_pe_candidate": 28.0},
        "NKE":  {"gross_margin": 0.445, "peer_pe_candidate": 25.0},
        "EL":   {"gross_margin": 0.720, "peer_pe_candidate": 30.0},
    }

    LUXURY_GROSS_MARGIN_FLOOR = 0.65

    for ticker, data in profiles.items():
        gm = data["gross_margin"]
        target_pe = data["peer_pe_candidate"]

        if gm < LUXURY_GROSS_MARGIN_FLOOR:
            # Scaled down based on pricing power tier
            qualified_pe = min(target_pe, 18.0 + (gm / LUXURY_GROSS_MARGIN_FLOOR) * 4.0)
            assert qualified_pe <= 22.0, f"{ticker} with GM={gm:.1%} must not receive luxury multiple"
        else:
            # Premium luxury multiple allowed (EL with 72% gross margin)
            qualified_pe = target_pe
            assert qualified_pe >= 25.0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0 — read this before reading any pass count above
# ══════════════════════════════════════════════════════════════════════════════
#
# The five tests above are the brief's module, landed verbatim. They import
# `pytest` and `math` and nothing else: no engine module, no fixture, no replay.
# Every number in them is computed from a literal defined three lines earlier and
# asserted against another literal. They pass in 0.08s because nothing runs.
#
# That is not a defect in the arithmetic — the arithmetic is correct and worth
# having as the specification of what each gate should do. It is a defect in what
# a green run means. "All 5 assertions will pass, guaranteeing that inventory
# spikes (NKE), hyper-growth free lunches (ONON), trough margin traps (EL), and
# associate equity omissions (2020.HK) are barred from corrupting intrinsic
# valuations" is not true of the module as supplied, and cannot be: a test that
# never calls the engine cannot constrain it. The five gates it describes —
# `GATE_INVENTORY_STRESS`, a sales-to-capital reinvestment deduction, a
# trough/peak routing rule, an associates-over-10%-of-equity add-back, and a
# luxury gross-margin floor — do not exist in this codebase. Sections A-F below
# measure exactly what the engine does instead, and each brief invariant that the
# engine does not implement is an `xfail(strict=True)`: reported on every run,
# counted, and turning the suite RED the moment someone implements it, so the
# mark cannot outlive the gap.
#
# The brief's integration sketch is also not insertable as written. It says to
# add, inside the `_project_dcf` loop:
#
#     fcf_t = (ebit_t * (1.0 - effective_tax_rate)) + da_t - capex_t - reinvestment_deduction
#
# `_project_dcf` has no `ebit_t`, no `da_t`, no `capex_t`, no
# `effective_tax_rate` and no `overrides` parameter. Its cash flow is
# `fcf_t = rev_t * margin_t` — a margin on revenue, not a built-up cash flow —
# so the sketch is a rewrite of the projection engine, not an insertion into it.
# Section B pins that as a measured fact rather than an opinion.


def test_the_briefs_own_module_never_touches_the_engine():
    """The claim in the banner, checked rather than asserted.

    If a future edit wires one of the five specification tests to a real engine
    call, this fails and the banner has to be updated — which is the point: the
    banner is the thing a reader will otherwise skim past and mis-read as
    coverage.
    """
    import inspect as _inspect

    src = _inspect.getsource(sys.modules[__name__])
    head = src.split("# SECTION 0")[0]
    for forbidden in ("dcf_agent", "sector_profiles", "_project_dcf",
                      "classify_valuation_profile", "golden_replay",
                      "from src", "import src"):
        assert forbidden not in head, (
            f"the verbatim section now references {forbidden!r}; the banner "
            f"claim that it never touches the engine is stale")


# ══════════════════════════════════════════════════════════════════════════════
# Engine-facing sections. Everything below calls or reads the real engine, and
# every number in it was produced by that call — none is transcribed from the
# brief. Where the brief's invariant is not implemented, the test says so with
# `xfail(strict=True)` rather than passing quietly.
# ══════════════════════════════════════════════════════════════════════════════
import ast                                                    # noqa: E402
import inspect                                                # noqa: E402
from pathlib import Path                                      # noqa: E402

from src.agents.analysis import dcf_agent                     # noqa: E402
from src.data.sector_profiles import (                        # noqa: E402
    DAMODARAN_SECTOR_MAP,
    INDUSTRY_VALUATION_PROFILES,
    TICKER_SECTOR_LOOKUP,
    classify_valuation_profile,
)

#: The four archetypes, at the (revenue CAGR, FCF margin, debt/equity) the
#: classifier is actually order-sensitive on. Every row is re-derived from the
#: real function in the test below; this dict is the input side only.
ARCHETYPES: dict[str, tuple[float, float, float]] = {
    "NKE":      (0.03,  0.10, 0.6),
    "ONON":     (0.30,  0.12, 0.3),
    "EL":       (-0.02, 0.10, 0.5),
    "02020.HK": (0.12,  0.18, 0.4),
}

_ENGINE_SRC: str | None = None


def _engine_src() -> str:
    global _ENGINE_SRC
    if _ENGINE_SRC is None:
        _ENGINE_SRC = inspect.getsource(dcf_agent)
    return _ENGINE_SRC


def _consumer_profiles() -> dict:
    return INDUSTRY_VALUATION_PROFILES["Consumer"]


def _methods(profile: str) -> dict[str, dict]:
    """`{method name: entry}` for a Consumer profile."""
    return {m["name"]: m for m in _consumer_profiles()[profile]["methods"]}


# ── A. NKE — inventory bullwhip ──────────────────────────────────────────────


def test_the_engine_turns_inventory_into_days_at_exactly_one_site():
    """The inverse of what this test asserted when the brief was measured.

    It used to pin that `inventory` reaches the engine at three sites and none
    of them is a ratio — the evidence that the brief's Gate 1 was the cheapest
    of the five gaps to close, because the input was present and unconsumed. It
    is now consumed, so the assertion is inverted rather than deleted: four
    sites, and exactly one of them divides.

    "Exactly one" is the load-bearing part. A second ratio site would mean two
    definitions of inventory stress that can disagree, which is the shape the
    `md_abs * 10` defect had — the same quantity computed twice, in two places,
    with one of them wrong. The shape check is by line prefix rather than by
    substring because a substring short enough to survive a re-indent is also
    short enough to match a comment.
    """
    src = _engine_src()
    lines = [ln for ln in src.splitlines() if '"inventory"' in ln]
    shapes = sorted(
        "assignment" if ln.strip().startswith('"inventory":') else
        "request" if ln.strip().startswith('"inventory",') else "other"
        for ln in lines)
    assert shapes == ["assignment", "other", "request", "request"], [
        ln.strip() for ln in lines]
    # The one "other" is the read that feeds the ratio, and it goes through
    # `_safe` like every other line-item read in the row builder.
    assert [ln.strip() for ln in lines
            if not ln.strip().startswith(('"inventory":', '"inventory",'))] == \
        ['inv = _safe(row.get("inventory"))']
    # And it is the only place inventory is divided by anything. Asserted on the
    # AST rather than by filtering lines, because a line filter has to exclude
    # comments AND docstrings to be right, and this helper's own docstring
    # contains the expression in prose — a first version of this assert matched
    # that line and failed on its own documentation. A second version looked for
    # `inv / cogs` and found nothing, because the real expression is
    # `float(inv) / float(cogs)` — so the operands are unwrapped rather than
    # matched, and an assert that has to be corrected twice is an assert whose
    # target should be named once, here.
    def _operand(node):
        while isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "float" and len(node.args) == 1:
            node = node.args[0]
        return node.id if isinstance(node, ast.Name) else None

    divs = [( _operand(n.left), _operand(n.right))
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
            and _operand(n.left) == "inv"]
    assert divs == [("inv", "cogs")], divs

    # The gate exists and is spelled the way the post-mortem spelled it.
    assert '"gate_id": "GATE_INVENTORY_STRESS",' in src
    assert src.count("GATE_INVENTORY_STRESS") == 2, (
        "one at the `#:` constant comment and one at the record — a third "
        "means something else grew a reference to this gate")
    assert dcf_agent._INVENTORY_STRESS_TRIGGER_DAYS == 25.0
    assert dcf_agent._INVENTORY_MARKDOWN_HAIRCUT == 0.15


def test_gate_vocabulary_is_closed_and_has_eleven_members():
    """The set of gate ids the engine can emit, extracted from its own source.

    Eleven. This is what makes a claim about which gates exist a fact about the
    engine rather than a fact about a brief's prose — and it means adding one is
    a deliberate act that turns this test red until the list is updated.

    It was seven when this module was written. The eighth,
    `GATE_GROWTH_REINVESTMENT`, is the brief's Gate 2 built, measured and then
    shipped OBSERVATION-ONLY: `applied` is False on every run and no call site
    passes a ratio to the projector, so it records a counterfactual beside the
    cash-conversion cap it was written to replace. The ninth,
    `GATE_INVENTORY_STRESS`, is the brief's Gate 1 and is the opposite: it is
    live, and when it was wired it was the only gate in the engine whose record
    said `applied: True`. Measured before it was wired, it fires on none of the
    14 golden fixtures — the largest positive DSI expansion is FCX at +7.1 days
    against a 25-day trigger — so it shipped live with a nil blast radius on the
    recorded baseline.

    The tenth, `GATE_SCENARIO_ORDERING`, is not from this brief at all. It
    enforces Bear ≤ Base ≤ Bull as an engine-level invariant after a measured
    inversion on BN4.SI: the profile's anchor leg `EV/EBITDA` failed its equity
    bridge in bear only (EV SGD 9.021bn against net debt SGD 9.325bn plus
    minority interest SGD 0.322bn is negative equity, floored to a literal 0.0
    by `_ev_to_equity_ps` and then dropped as a non-positive leg), so the blend
    renormalised bear onto its single surviving method — `SOTP (published)` at
    weight 1.0, the HIGHEST of the three legs it had computed — and bear
    published 7.37 against base 5.20. Base is the pivot and is never moved; the
    outer scenarios are pulled onto it, and the unclamped value is kept beside
    the clamp as `intrinsic_value_unclamped`. Unlike the ninth gate this one
    fires on the recorded baseline: exactly one fixture of 14, and exactly one
    of the two directions (no `base > bull` anywhere, so the bull half ships
    implemented but unexercised).

    The eleventh, `GATE_BALANCE_SHEET_QUARTERLY_STEP_CHANGE`, is the first that
    is not a gate at all in the sense the other ten are: it changes nothing and
    cannot. `_refresh_balance_sheet_from_latest_quarter` substitutes the latest
    reported quarter for the year-end balance sheet UNCONDITIONALLY — the engine
    anchors on the last annual row, so a March year end carries up to four
    quarters of stale cash and debt into every EV-based method — and this record
    only sizes the substitution it just made, as net CASH annual vs. quarterly
    over `max(abs(annual), 1.0)`. `applied` is a literal False with no branch
    that could set it True; the owner's instruction was "Disclosure / Telemetry
    Only ... Never abort the overlay or revert to stale annual data
    automatically." Both threshold and denominator are owner-set and neither is a
    tuning knob: the denominator on 2026-09-18, replacing the 0.15 in the Phase
    1.4 plan text, and the threshold twice — 0.25 flat on 2026-09-18, then on
    2026-09-19 "Adjust the step-change flag threshold from 0.25 to 0.50 (50%)
    when period_delta_days > 180". The CONDITIONAL is what shipped, not the
    ruling's parenthetical global 0.50, so a 91-day year-end-to-Q1 comparison
    keeps the tighter band; that choice is mine and is recorded as mine next to
    the constant.

    Why the ruling was needed is measured, not argued. At a flat 0.25 the flag
    fired on 8 of the 8 golden fixtures whose overlay applied — a 100% firing
    rate, so it carried no information. A dedicated probe first checked the
    obvious alternative reading, eight trivial firings off the
    `max(abs(annual), 1.0)` denominator floor, and found zero: every firing had
    a real non-zero annual base. The actual cause is temporal. The comparison
    spans year-end → latest quarter, which across those fixtures is 91 to 273
    days (AAPL 2025-09-27 → 2026-06-27, MU 2025-08-28 → 2026-05-28, V
    2025-09-30 → 2026-06-30, COST 252, MELI and U96_SI 181, 09988_HK and BABA
    91), not one quarter. Three quarters of balance-sheet drift is not a step
    change; it is a baseline the annual figure simply predates.

    Post-ruling the flag fires on 7 of the 14 fixtures, ratios 0.6202 to 4.9521.
    Exactly one dropout, MELI: net cash −5.093bn at the 2025-12-31 year end
    against −7.446bn at 2026-06-30, a ratio of 0.4620 over a 181-day gap, the
    only one of the eight sitting between the two thresholds. 09988_HK and BABA
    are unaffected by the ruling on principle — at 91 days they are measured
    against the STRICT 0.25 and both fire on it.

    A disclosure-only gate still moves the golden baseline, and that is a
    property of the projection rather than evidence of a valuation move:
    `golden_replay.py` reduces `gate_evaluations` to sorted metric NAMES, so the
    name lands in `gate_metrics` on each fixture the flag fires on, plus one
    prose line in each of that fixture's three `forward_flags` lists. Numeric
    leaves that moved: zero, verified by an independent whole-document numeric
    diff (14 fixtures, 3192 projection keys), and all fourteen base intrinsic
    values are byte-identical. The 2026-09-19 regeneration moved 4 leaves on
    MELI alone — `gate_metrics` and the three `forward_flags` — and left the
    other thirteen fixtures byte-identical, which is what "one dropout" costs.

    The record also carries `period_delta_days` and `threshold_used`, mine and
    not among the owner's six keys, disclosed as such in the source and pinned
    here. Without the second, a published `delta_ratio` of 0.4620 does not say
    whether it fired against 0.25 or was measured against 0.50 and dropped; all
    three of `basis`, `period_delta_days` and `threshold_used` are
    golden-invisible for the reason above, so the payload became self-describing
    at zero baseline cost.

    Those facts are pinned below by name rather than left to the count, because
    a count alone cannot tell "eleven gates" from "eleven gates, two of which
    moved a number nobody named".
    """
    # Thirteen since 2026-09-21, both named rather than left to a count:
    #   GATE_BACKLOG_VISIBILITY  says whether a backlog-coverage DCF leg was bounded
    #       by an ACCEPTED backlog (applied) or ran the core projection (not applied);
    #   GATE_MARGIN_TURNAROUND   is live: when the FCF margin window opens
    #       cash-burning and improves every year, the base is the last two audited
    #       years instead of a mean that describes the company it was (GE Vernova:
    #       1.4% -> 7.3%). Neither fires on any of the golden fixtures.
    emitted = sorted(set(re.findall(r'"gate_id":\s*"(GATE_[A-Z_]+)"', _engine_src())))
    assert emitted == [
        "GATE_BACKLOG_VISIBILITY",
        "GATE_BALANCE_SHEET_FINANCIAL",
        "GATE_BALANCE_SHEET_QUARTERLY_STEP_CHANGE",
        "GATE_CASH_CONVERSION",
        "GATE_CYCLICAL_PEAK_CONSENSUS",
        "GATE_GROWTH_CAGR_DIVERGENCE",
        # Seventeenth (2026-09-26, owner): consensus target more than the owner's
        # threshold above spot tags the name Growth_Inflection_Speculative; `applied`
        # is a literal False (observation, scored apart).
        "GATE_GROWTH_INFLECTION",
        "GATE_GROWTH_REINVESTMENT",
        "GATE_INVENTORY_STRESS",
        # Fourteenth (2026-09-22): records, for a name with an accepted backlog,
        # each of the three long-cycle eligibility rules with its reading and
        # threshold. `applied` is the verdict, so it is an expression, not a literal.
        "GATE_LONG_CYCLE_ELIGIBILITY",
        "GATE_MARGIN_TURNAROUND",
        "GATE_PT_IV_BAND",
        "GATE_REVENUE_SCALE_CAP",
        "GATE_SCENARIO_ORDERING",
        # Fifteenth (2026-09-23, owner rule 3): once an analyst SOTP is blended,
        # the look-through leg is computed, published as a cross-check and never
        # weighted; the record carries both figures. `applied` is a literal True.
        # Sixteenth (2026-09-26): the pipeline extractor's SOTP, graded against
        # the owner-accepted leg and never weighted. `applied` is a literal False.
        "GATE_SOTP_EXTRACTOR_CROSSCHECK",
        "GATE_SOTP_PRECEDENCE",
    ], emitted
    # The two substring facts that used to be one assert. `"REINVESTMENT"` does
    # not contain `"INVENT"`, which is why the old guard could assert
    # `not any("INVENT" in g ...)` and still be satisfied by the eighth gate —
    # and why it had to become two asserts the moment a ninth one landed.
    assert "INVENT" not in "GATE_GROWTH_REINVESTMENT"
    assert sum("INVENT" in g for g in emitted) == 1, emitted
    # `applied` is a literal on every record, never derived, and the split is
    # the fact worth pinning: six records say True and four say False. A first
    # version of this assert claimed the inventory gate was the ONLY True in the
    # file, which is wrong — CAGR divergence, balance-sheet-financial (twice)
    # and deterministic-KPI precedence all applied what they measured long
    # before it existed, and scenario ordering joined them. Counted from source,
    # so the numbers here cannot be a recollection.
    #
    # This count has a SECOND copy in `test_reinvestment_scope_and_cap.py::
    # TestTheInvariantsAreNotDuplicated::
    # test_the_gate_record_still_says_not_applied_and_publishes_both_gates`.
    # Neither mentions the other in the engine, so a new live gate reddens two
    # modules that look unrelated — which is what happened when
    # GATE_SCENARIO_ORDERING landed and only this one had been widened. Both
    # are now cross-referenced; keep them in step.
    src = _engine_src()
    # FIVE since 2026-09-19: deterministic-KPI precedence recorded the
    # composite as its decision variable and was retired with it.
    assert src.count('"applied": True,') == 7   # +GATE_MARGIN_TURNAROUND (2026-09-21), +GATE_SOTP_PRECEDENCE (2026-09-23), src.count('"applied": True,')
    # FOUR, not three: the eleventh gate is a literal `"applied": False,` and
    # has no branch that could make it True. This is the third time a new
    # observation-only record has moved this count and reddened a module whose
    # name has nothing to do with the gate — the count is a fact about the whole
    # file, so it lives nowhere in particular and breaks everywhere. If you are
    # reading this because it failed, the second copy is in
    # `test_reinvestment_scope_and_cap.py` and both have to move together.
    assert src.count('"applied": False,') == 6   # +GATE_SOTP_EXTRACTOR_CROSSCHECK, +GATE_GROWTH_INFLECTION (2026-09-26), src.count('"applied": False,')
    assert '"applied": _s_to_c is not None' not in src

    # ── the eleventh gate's named facts ──────────────────────────────────────
    # Its record shape is pinned from its own side by
    # `tests/test_phase14_balance_sheet_fallbacks_0918.py`. What belongs HERE is
    # the vocabulary-level claim: that it is disclosure by construction and not
    # by omission, so a future edit cannot quietly give it teeth without
    # reddening the module that counts gates.
    assert dcf_agent._BALANCE_SHEET_STEP_CHANGE_THRESHOLD == 0.25, (
        "owner-set 2026-09-18, replacing the 0.15 in the Phase 1.4 plan text; "
        "15% fires on ordinary quarterly working-capital drift")
    # Owner-set 2026-09-19: "Adjust the step-change flag threshold from 0.25 to
    # 0.50 (50%) when period_delta_days > 180". The conditional is what shipped,
    # not a global 0.50 -- a real 91-day year-end-to-Q1 comparison keeps the
    # tighter band. The measured reason is in the constant's comment: at 0.25
    # alone all 8 golden fixtures the overlay applied on crossed the threshold,
    # so the flag fired 100% of the time and told nobody anything, because the
    # two readings are 8-9 months apart rather than one quarter.
    assert dcf_agent._BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE == 0.50
    assert dcf_agent._BALANCE_SHEET_STEP_CHANGE_WIDE_GAP_DAYS == 180
    assert (dcf_agent._BALANCE_SHEET_STEP_CHANGE_THRESHOLD
            < dcf_agent._BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE), (
        "the wide-gap threshold must be the LOOSER one, or a long gap would "
        "make the flag fire MORE often, which is the opposite of the ruling")
    # The owner's formula, character for character — including the denominator,
    # which is the ANNUAL figure alone floored at one currency unit and not the
    # max of both readings.
    assert ("delta_ratio = abs(q_net_cash - a_net_cash) / "
            "max(abs(a_net_cash), 1.0)") in src
    # Strictly greater than, against whichever threshold the gap selected. The
    # numeric boundary cases for BOTH thresholds are parametrised in the Phase
    # 1.4 module; this one is about the source. The resolved-threshold variable
    # is pinned rather than the constant so that a future edit cannot quietly
    # compare against 0.25 while the record claims 0.50 was used.
    assert "if delta_ratio > _threshold:" in src
    assert "_threshold = _BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE" in src
    assert "> _BALANCE_SHEET_STEP_CHANGE_WIDE_GAP_DAYS" in src
    # And the record says which one it used, so a published `delta_ratio` is
    # interpretable without reading source.
    assert '"threshold_used": _threshold,' in src
    assert '"period_delta_days": _gap_days,' in src
    # One occurrence only. `GATE_INVENTORY_STRESS` has two — the record and a
    # `#:` constant comment — and a gate whose id is mirrored somewhere else is
    # a gate something else can dispatch on. Nothing does here.
    assert src.count("GATE_BALANCE_SHEET_QUARTERLY_STEP_CHANGE") == 1
    _i = src.index('"gate_id": "GATE_BALANCE_SHEET_QUARTERLY_STEP_CHANGE"')
    _rec = src[_i:src.index('"applied":', _i)]
    assert '"applied": True,' not in _rec
    # And it is not reachable from the scorer: `gate_backtest` keys off gate-id
    # literals, so a record carrying net-CASH values in `raw_input_path_a` /
    # `gated_output_path_b` would be graded as a forecast if it ever iterated
    # `gate_evaluations` generically. Cross-module, so asserted on that module.
    from src.memory import gate_backtest
    assert "BALANCE_SHEET_QUARTERLY_STEP_CHANGE" not in inspect.getsource(
        gate_backtest)
    assert "gate_evaluations" not in inspect.getsource(gate_backtest)



def test_the_reinvestment_gate_is_observation_only_and_says_so():
    """The eighth gate records a counterfactual. Pin that it does not apply one.

    Three separate facts, all of which have to hold for `applied: False` to mean
    anything:

      * the record is appended with `applied` hard-False, not derived from
        whether the ratio was measurable. Deriving it would conflate "we could
        not compute this" with "we chose not to act on it", which is the exact
        distinction the Phase 1.2B cash-conversion gate exists to make, and
        would report `applied: True` on the 13 of 14 fixtures where S/C measures
        cleanly while nothing moves.
      * no call site passes `sales_to_capital=` as a keyword argument. Checked
        on the AST rather than by grepping lines, because five occurrences of
        that exact text exist in the engine and four of them are prose — a
        docstring explaining why the parameter is unused and three comments
        recording the wiring to restore. A `not in src` assertion fails on the
        documentation; a line-based filter that strips `#` comments fails on the
        docstring, which is what the first version of this test did. Walking
        `ast.Call` keywords cannot be fooled by either.
      * `_y10_fcf_margin` does not deduct. Parity between the projection and the
        terminal estimate Gate B judges is an obligation the moment the charge
        goes live, and it is written down at both sites; while observation-only
        it must NOT hold, because a charged terminal estimate against an
        uncharged projection would fire Gate B on a margin the DCF never used.
    """
    src = _engine_src()

    # (1) hard-False, and adjacent to the gate id rather than somewhere else
    rec = src[src.index('"gate_id": "GATE_GROWTH_REINVESTMENT"'):]
    rec = rec[:rec.index("})", 2)]
    assert '"applied": False,' in rec, rec
    assert '"applied": _s_to_c is not None' not in src

    # (2) no live keyword argument at any call site, anywhere in the module
    charged = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            charged += [(node.lineno, kw.arg) for kw in node.keywords
                        if kw.arg == "sales_to_capital"]
    assert charged == [], charged
    # ... while the parameter itself still exists, so this is "unused" and not
    # "deleted". A revert that dropped the mechanism would satisfy (2) alone.
    assert "sales_to_capital" in inspect.signature(
        dcf_agent._project_dcf).parameters

    # (3) the terminal estimate is uncharged
    y10 = src[src.index("_y10_fcf_margin = min("):]
    y10 = y10[:y10.index("_FCF_MARGIN_CAP)") + len("_FCF_MARGIN_CAP)")]
    assert "_reinvestment_margin_deduction" not in y10, y10


def test_the_projector_still_reproduces_the_flat_margin_projection():
    """`sales_to_capital=None` is not "a small charge", it is byte-identical.

    The parameter exists and no call site uses it, so the only thing standing
    between this change and a 14-fixture baseline rewrite is that the default
    path is the legacy path. Pinned directly on the projector's output rather
    than inferred from the golden tests passing, because the golden tolerance is
    ±5% and a deduction small enough to hide inside it would still be a change
    nobody named.

    Compared against the algebraic legacy form `min(max(base + delta, floor),
    cap)` computed inline here, so the assertion does not depend on the engine
    agreeing with itself — that is the failure mode the `md_abs * 10` defect had.

    STRONGER SINCE 2026-09-18, and the second half of the test changed with it:
    the projector's deduction call now passes no `profile`, and the scope gate
    fails closed, so NO ratio at all makes it charge — not just `None`. The
    default path being the legacy path is no longer the only thing standing
    between this change and a baseline rewrite; the scope gate is. See the inline
    comment where the old "the charge does fire when handed a ratio" assertion
    used to be.
    """
    revenue, fmb, floor = 10_000.0, 0.19, 0.02
    g, wacc, tgr = 0.28, 0.09, 0.025
    sched = [g * (0.9 ** t) for t in range(10)]

    def rows(sc):
        return dcf_agent._project_dcf(
            revenue, fmb, g, 0.0, wacc, tgr, floor, 500.0, 100.0,
            growth_schedule=sched, margin_delta_absolute=-0.03,
            sales_to_capital=sc)[3]

    none_rows, explicit_zero = rows(None), rows(0.0)
    for r in none_rows:
        g_t = r["growth_pct"]
        expected = min(max(fmb - 0.03, floor), 0.60)
        assert r["fcf_margin"] == pytest.approx(expected, abs=1e-12)
        assert r["reinvest_margin_deduction"] == 0.0
    assert [r["fcf_margin"] for r in explicit_zero] == \
           [r["fcf_margin"] for r in none_rows]
    # CHANGED 2026-09-18, and the change is the point of the scoping work rather
    # than collateral from it. This block used to assert the opposite — that
    # handing the projector a ratio makes it charge, "the mechanism is built, not
    # stubbed out". That is no longer true and must not be made true again by
    # accident: `_project_dcf` calls the helper with no `profile` and no
    # `margin_headroom`, and both gates fail closed, so the projector now charges
    # NOTHING for any ratio. 1.85 is the post-mortem's own worked example; it used
    # to produce a 0.28/(1.28 × 1.85) = 11.82% deduction here and now produces
    # zero. That is the scope gate doing its job on a number nobody scoped.
    for sc in (1.85, 0.0625, 1.9968, 10.9116):
        inert = rows(sc)
        assert inert[0]["reinvest_margin_deduction"] == 0.0, sc
        assert [r["fcf_margin"] for r in inert] == \
               [r["fcf_margin"] for r in none_rows], sc

    # The mechanism is still built and not stubbed out — proved one level down,
    # where the scope and the rationing bound can actually be supplied. Wiring it
    # into the projector is a three-part change (signature, the call, and
    # `_y10_fcf_margin` in the same commit), recorded in the comment above the
    # call and pinned by
    # `test_reinvestment_scope_and_cap.py::TestTheInvariantsAreNotDuplicated::
    # test_the_projectors_reinvestment_call_is_scope_inert`.
    charged = dcf_agent._reinvestment_margin_deduction(
        0.28, 1.85, profile="Consumer Growth", margin_headroom=0.30)
    assert charged == pytest.approx(0.28 / (1.28 * 1.85), abs=1e-12)
    # And the cap binds when the headroom is smaller than the algebra's answer.
    capped = dcf_agent._reinvestment_margin_deduction(
        0.28, 1.85, profile="Consumer Growth", margin_headroom=0.05)
    assert capped == 0.05
    assert dcf_agent._reinvestment_margin_deduction_raw(
        0.28, 1.85, profile="Consumer Growth") == pytest.approx(
            0.28 / (1.28 * 1.85), abs=1e-12)


def test_the_briefs_nike_arithmetic_is_right_about_a_cap_the_engine_does_not_use():
    """The DSI expansion in the brief's own scenario is 36.5 days. It is read now.

    Recomputed here so the number is checked rather than quoted: current DSI is
    8400/28000 × 365 = 109.5d against a trailing median of 73.0d, an expansion
    of 36.5d, well past the 25-day trigger. When this test was written nothing in
    the engine read it and the name said so; `GATE_INVENTORY_STRESS` does now,
    and the post-mortem's Failure 4 is what landed.

    What is STILL true, and why the test is kept rather than deleted: the brief's
    proposed remedy was to cap the terminal margin at the lowest trailing-3y EBIT
    margin, 11.373% here. That is not what shipped, and the reason survives the
    implementation. The margin that reaches the terminal value is
    `fcf_margin_base + margin_delta`, clamped to `[fcf_floor, _FCF_MARGIN_CAP]` —
    an FCF margin with a Consumer floor of **+0.02**, not an EBIT margin. Capping
    an FCF margin at an EBIT-margin floor is a category error in whichever
    direction it binds: EBIT excludes interest and tax that FCF has already paid,
    and includes D&A that FCF has replaced with capex. For NKE's archetype inputs
    the floor sits above the brief's cap in the wrong direction entirely — it
    prevents the margin from FALLING, where the brief's cap was meant to prevent
    it from STAYING HIGH. So the shipped gate marks the base margin down
    proportionally instead, which needs no second margin definition to be
    coherent.
    """
    from src.data.sector_profiles import FCF_MARGIN_FLOOR

    hist = [(51_000, 28_000, 8_400, 5_800), (50_000, 27_000, 5_400, 6_200),
            (46_000, 25_000, 5_100, 6_500), (44_000, 24_000, 4_800, 6_100)]
    dsi = [(inv / cogs) * 365.0 for _, cogs, inv, _ in hist]
    past = sorted(dsi[1:])
    expansion = dsi[0] - past[len(past) // 2]
    assert expansion == pytest.approx(36.5, abs=0.05), expansion
    assert expansion > dcf_agent._INVENTORY_STRESS_TRIGGER_DAYS

    cap = min(ebit / rev for rev, _, _, ebit in hist[:3])
    assert cap == pytest.approx(0.11373, abs=1e-5), cap

    # And what the engine actually floors a Consumer margin at:
    assert FCF_MARGIN_FLOOR["Consumer"] == 0.02
    assert FCF_MARGIN_FLOOR["Consumer"] < cap, (
        "if the Consumer floor ever rose above the brief's cap, the two "
        "mechanisms would start to interact and this note would be wrong")


def test_an_inventory_buildup_caps_the_terminal_margin():
    """The brief's Gate 1, passing. Was `xfail(strict=True)`; the strictness is
    the reason this is a green test rather than a silently-skipped one — an
    xfail that starts passing fails the suite, so implementing the gate forced
    this to be updated in the same commit rather than left to rot.

    Kept as the brief wrote it, on the brief's own inputs, so the number it
    asserts (36.5 > 25) is the brief's and not one chosen to fit.
    """
    hist = [
        {"revenue": 51_000, "cost_of_revenue": 28_000, "inventory": 8_400, "ebit": 5_800},
        {"revenue": 50_000, "cost_of_revenue": 27_000, "inventory": 5_400, "ebit": 6_200},
        {"revenue": 46_000, "cost_of_revenue": 25_000, "inventory": 5_100, "ebit": 6_500},
        {"revenue": 44_000, "cost_of_revenue": 24_000, "inventory": 4_800, "ebit": 6_100},
    ]
    fn = getattr(dcf_agent, "_inventory_stress_days", None)
    assert fn is not None, "no inventory-stress helper exists"
    assert fn(hist) > 25.0
    assert fn(hist) == pytest.approx(36.5, abs=0.05), fn(hist)


def test_the_inventory_helper_reads_newest_first_and_says_none_when_it_cannot():
    """The order convention is the whole risk in this helper, so it is pinned.

    `run_dcf_agent`'s `series` is ASCENDING — `most_recent = series[-1]` — and
    this helper takes newest-FIRST, because that is the order the brief's own
    test fixture is written in and the order the post-mortem's
    `dsi - dsi_3y_median` reads in. The call site therefore reverses, and the
    parameter is named `rows_newest_first` so that a call site passing `series`
    looks wrong on its face rather than merely computing the wrong thing.

    It is worth being precise about how wrong. On the brief's NKE inputs the
    reversed order returns −1.5 days instead of +36.5, which is a small number
    and would not obviously look like a bug in a log line. On a series whose
    inventory has genuinely been draining it would return a large POSITIVE
    number and fire the gate on exactly the names it should clear.
    """
    nke = [
        {"inventory": 8_400, "cost_of_revenue": 28_000},
        {"inventory": 5_400, "cost_of_revenue": 27_000},
        {"inventory": 5_100, "cost_of_revenue": 25_000},
        {"inventory": 4_800, "cost_of_revenue": 24_000},
    ]
    fn = dcf_agent._inventory_stress_days
    assert fn(nke) == pytest.approx(36.5, abs=0.05)
    # The same rows in the engine's own order — not a mirror image, and small
    # enough to pass for a rounding difference if nobody checked.
    assert fn(nke[::-1]) == pytest.approx(-1.46, abs=0.01)

    # None, not 0.0, whenever the comparison cannot be made. Zero would mean
    # "measured no stress", which is a different claim from "could not measure".
    assert fn([]) is None
    assert fn([{"inventory": 8_400}]) is None                      # no cogs
    assert fn([{"cost_of_revenue": 28_000}]) is None               # no inventory
    assert fn([{"inventory": 8_400, "cost_of_revenue": 0}]) is None
    assert fn([{"inventory": -1.0, "cost_of_revenue": 28_000},
               {"inventory": 5_400, "cost_of_revenue": 27_000},
               {"inventory": 5_100, "cost_of_revenue": 25_000}]) is None
    # One prior year is not a median.
    assert fn(nke[:2]) is None
    # Two is — and an even count averages the middle pair, which is
    # `statistics.median` and not `sorted(x)[len(x) // 2]`.
    two = fn(nke[:3])
    assert two == pytest.approx(109.5 - (73.0 + 74.5) / 2, abs=0.05), two


def test_the_inventory_helper_looks_at_three_prior_years_and_no_further():
    """`dsi_3y_median` means three years, and the fourth is excluded.

    Pinned because a slice that quietly widens to `rows[1:]` is invisible in
    every other test here. It is also NOT detectable by dropping in an extreme
    outlier, which is what a first version of this test tried: a median over
    four values is the mean of the middle two, so adding a 1460-day year to
    {73.0, 73.0, 74.5} moves the median from 73.0 to 73.7 — 0.8 days, well
    inside any tolerance, because robustness to outliers is the entire reason
    the post-mortem asked for a median. Robustness to an outlier and blindness
    to a widening window are the same property, so the window has to be tested
    with a fourth year that is merely DIFFERENT rather than absurd: prior DSIs of
    10, 20, 30 and 40 give a three-year median of 20 and a four-year median of
    25, which is a five-day difference in the expansion and cannot be hidden by
    a tolerance.
    """
    fn = dcf_agent._inventory_stress_days
    cogs = 1000.0

    def row(days: float) -> dict:
        return {"inventory": days * cogs / 365.0, "cost_of_revenue": cogs}

    rows = [row(60.0), row(10.0), row(20.0), row(30.0), row(40.0)]
    three = fn(rows)
    assert three == pytest.approx(60.0 - 20.0, abs=1e-9), three
    assert three != pytest.approx(60.0 - 25.0, abs=1e-9), (
        "the fourth prior year leaked into the median — the window is `rows[1:4]`")
    # Dropping the fourth year entirely changes nothing, which is the same fact
    # read from the other side.
    assert fn(rows[:4]) == pytest.approx(three, abs=1e-9)
    # And the brief's NKE series, whose prior three are 73.0 / 74.46 / 73.0:
    nke = [
        {"inventory": 8_400, "cost_of_revenue": 28_000},
        {"inventory": 5_400, "cost_of_revenue": 27_000},
        {"inventory": 5_100, "cost_of_revenue": 25_000},
        {"inventory": 4_800, "cost_of_revenue": 24_000},
    ]
    assert fn(nke) == pytest.approx(36.5, abs=0.05)


def test_the_markdown_is_proportional_and_never_touches_a_non_positive_margin():
    """The 15% is of the margin, not 15 percentage points, and it needs a sign.

    Two separate decisions, both worth a test because both are the kind of thing
    that reads as obviously right in a diff and is wrong on the population that
    matters.

    PROPORTIONAL. On NKE's own archetype inputs the base margin is ~11.4%, so a
    proportional haircut takes it to ~9.7% — a real markdown — while an absolute
    15pp deduction takes it to −3.6%, through the Consumer floor of +0.02, and
    hands the projector a number describing the floor. Measured across the 14
    golden fixtures an absolute 15pp deduction would drive 8 of 14 base margins
    negative outright. A proportional haircut cannot exceed the base margin,
    cannot flip its sign and cannot reach the floor from above. That is the
    failure the reinvestment charge was measured to have — its deduction
    exceeded the base margin on seven of fourteen names, and the blend's
    leg-dropping turned the more conservative input into a HIGHER valuation.

    SIGN-GUARDED. `−21.06 × 0.85 = −17.90`. On MSTR's shape a "haircut" would
    improve the margin by 3.2pp, which is the same error with the opposite sign
    and no message to say so. The gate therefore requires
    `fcf_margin_base > 0`, and this test pins the arithmetic that makes the
    guard necessary rather than only asserting the guard is in the source.
    """
    src = _run_body()
    at = src.index("if not _dcf_family_disabled and fcf_margin_base > 0:")
    block = src[at:src.index("# ── Analyst estimates", at)]
    assert "_INVENTORY_MARKDOWN_HAIRCUT" in block
    assert "fcf_margin_base * _INVENTORY_MARKDOWN_HAIRCUT" in block
    assert "- _inv_haircut" in block
    # An absolute deduction would read as a subtraction of the constant itself.
    assert "- _INVENTORY_MARKDOWN_HAIRCUT" not in block
    assert "0.15" not in block.replace("_INVENTORY_MARKDOWN_HAIRCUT", "")

    for fmb, expected in ((0.1137, 0.1137 * 0.85), (0.0232, 0.0232 * 0.85),
                          (0.5667, 0.5667 * 0.85)):
        assert fmb - fmb * 0.15 == pytest.approx(expected, abs=1e-12)
        # A proportional haircut is bounded by the base and keeps its sign.
        assert 0 < fmb - fmb * 0.15 < fmb
    mstr = -21.0605
    assert mstr * 0.85 > mstr, "the unguarded form improves a loss-making margin"
    assert mstr - 0.15 < mstr, "the absolute form does not — it is only wrong " \
        "on the positive side, which is why the sign guard is about the " \
        "multiplicative reading specifically"


def test_the_inventory_gate_sits_between_the_classify_capture_and_the_projector():
    """Position is semantics here, and three boundaries all matter.

    AFTER `_fcf_margin_for_classify` is captured, because that capture exists
    precisely so the profile choice reflects demonstrated economics rather than
    a repaired DCF basis — a haircut is a repair, and letting it reach the
    classifier would let an inventory quarter re-route a company to a different
    valuation profile.

    AFTER the cash-conversion gate, so that gate's `raw_input_path_a` records
    the margin it actually saw rather than a post-haircut one. Each gate should
    describe its own input.

    BEFORE the first `_project_dcf` call, which is the whole point of the gate.

    Asserted on offsets in `run_dcf_agent`'s body rather than by reading the
    file, so a refactor that moves the block fails here instead of silently
    changing what the gate does.
    """
    src = _run_body()
    at = {
        "classify_capture": src.index("_fcf_margin_for_classify = fcf_margin_base"),
        "cash_conversion": src.index('"gate_id": "GATE_CASH_CONVERSION"'),
        "inventory": src.index('"gate_id": "GATE_INVENTORY_STRESS"'),
        "projector": src.index("_project_dcf("),
    }
    assert at["classify_capture"] < at["cash_conversion"] < at["inventory"] \
        < at["projector"], at
    # The haircut mutates the variable the projector reads, not a copy of it.
    gate_at = src.index("if not _dcf_family_disabled and fcf_margin_base > 0:")
    assert "fcf_margin_base = _inv_pre - _inv_haircut" in src[gate_at:]
    assert "_inventory_stress_days(series[::-1])" in src[gate_at:]


def test_the_gate_is_inert_on_the_zero_inventory_population():
    """Five of the fourteen golden fixtures hold exactly zero inventory.

    Measured before this was wired, with `scratchpad/probe_inventory_dsi.py`,
    one subprocess per fixture: 02888_HK, C38U_SI, D05_SI, SCHW and V all report
    inventory of 0.0 against a positive cost of revenue, so their DSI is 0.0 in
    every year and the expansion is exactly 0.0. Banks, a REIT and a payment
    network — there is nothing on a shelf to mark down, and the gate should say
    nothing about them rather than reporting a stress of zero days.

    Pinned here because "the trigger fires on none of the 14" is the entire
    reason this gate ships live instead of observation-only, and that claim is
    only as good as the population it was measured on. Zero is a legitimate
    measurement and not a missing one, so the helper returns 0.0 rather than
    None — and the gate's `> 25` keeps it silent, which is the behaviour under
    test.

    The rest of the inert population splits six and three. Six COMPRESS:
    BN4_SI −74.3, MU −30.6, BABA −15.9, 09988_HK −15.8, COST −3.0, AAPL −1.3.
    Negative cannot clear a `> 25` trigger, which is why the helper is signed
    rather than clamped at zero. Three are positive and under it: FCX +7.1,
    MELI +3.0, U96_SI +1.5, the largest at 28% of the trigger and therefore the
    one a fixture refresh is most likely to push over.
    """
    fn = dcf_agent._inventory_stress_days
    bank = [{"inventory": 0.0, "cost_of_revenue": c} for c in
            (9_000.0, 8_500.0, 8_200.0, 8_000.0)]
    assert fn(bank) == 0.0
    assert not (fn(bank) > dcf_agent._INVENTORY_STRESS_TRIGGER_DAYS)
    # A missing inventory is a different claim from a zero one: None, not 0.0.
    assert fn([{"cost_of_revenue": 9_000.0}] * 4) is None
    # And compressing inventory cannot fire the gate no matter how far it moves.
    draining = [{"inventory": inv, "cost_of_revenue": 28_000.0} for inv in
                (1_000.0, 4_000.0, 6_000.0, 9_000.0)]
    assert fn(draining) < 0
    assert not (fn(draining) > dcf_agent._INVENTORY_STRESS_TRIGGER_DAYS)



# ── B. ONON — hyper-growth capital drain ─────────────────────────────────────


def test_project_dcf_charges_no_capital_for_growth():
    """The measured core of the brief's Gate 2, against the real projector.

    A 28%-a-year grower at a flat 19% FCF margin — ONON's archetype. Every
    year's cash flow is exactly `revenue × margin`: year 1 is 2560 × 0.19 =
    486.4, year 3 is 4194.304 × 0.19 = 796.91776. The margin does not move, and
    nothing is deducted for the working capital or the capacity the growth
    requires. Under the brief's rule year 1 would carry a 560/1.85 = 302.7
    charge against a 560 revenue increment — a 62% cut to that year's cash flow
    — and the engine applies none of it.

    This is the largest of the five gaps by valuation impact, because it
    compounds into the terminal value: for these inputs the terminal value is
    86.0% of the total (measured 0.8595), so an un-charged increment is not a
    year-one rounding difference, it is most of the answer.

    STILL TRUE IN PRODUCTION, but the reason changed and the difference matters.
    The mechanism now exists — `_project_dcf` takes `sales_to_capital` and
    `_reinvestment_margin_deduction` implements the identity above — and every
    call site passes None, because wiring it live moved base IV on 9 of the 14
    golden fixtures over a range of −9.45% to +17.40%, with the sign inverted on
    the two hyper-growth names it exists for. So this test no longer proves "the
    engine cannot charge"; it proves the default path is still the flat-margin
    path, which is the only thing keeping the published numbers where they were.
    `test_the_projector_still_reproduces_the_flat_margin_projection` pins that
    directly, and `test_the_reinvestment_gate_is_observation_only_and_says_so`
    pins that no call site hands it a ratio.
    """
    equity_ps, pv_fcf, pv_tv, rows = dcf_agent._project_dcf(
        2000.0,      # revenue_base
        0.19,        # fcf_margin_base
        0.28,        # growth_rate — ONON-like
        0.0,         # margin_delta_per_year
        0.09, 0.025, -0.05, 0.0, 100.0,
        years=3,
    )
    assert len(rows) == 3
    assert equity_ps == pytest.approx(112.89488, abs=1e-4)
    prev = 2000.0
    for r in rows:
        assert r["fcf"] == pytest.approx(r["revenue"] * r["fcf_margin"], rel=1e-12)
        assert r["fcf_margin"] == pytest.approx(0.19, rel=1e-12), \
            "the margin moved, so something other than a flat margin is in play"
        assert r["reinvest_margin_deduction"] == 0.0
        assert r["revenue"] == pytest.approx(prev * 1.28, rel=1e-12)
        prev = r["revenue"]
    assert pv_tv / (pv_fcf + pv_tv) == pytest.approx(0.8595, abs=1e-4)


def test_the_projector_has_none_of_the_inputs_the_briefs_patch_needs():
    """The integration sketch STILL cannot be inserted as written.

    It says to compute, inside the loop:

        fcf_t = (ebit_t * (1.0 - effective_tax_rate)) + da_t - capex_t
                - reinvestment_deduction

    gated on `overrides["apply_reinvestment_deduction"]`. The real signature has
    no `overrides`, no tax rate and no per-year EBIT, D&A or capex. It takes a
    revenue base and an FCF MARGIN and never builds a cash flow up from
    components, so the change as sketched is a rewrite of the projection engine
    and of everything calibrated against it — not a loop insertion.

    Worth naming what that costs: the projector's margin-only shape is what lets
    a single `fcf_margin_base` plus a scenario multiplier drive all ten years.
    A component build-up needs capex, D&A and working-capital series per year,
    which are requested for the historical rows but never projected forward.

    WHAT DID LAND INSTEAD, and why this test is still worth keeping rather than
    deleting as obsolete: the post-mortem's second attempt at the same fix
    expressed it as a MARGIN deduction, `g/((1+g)·(S/C))`, which the margin-only
    shape does accept — no EBIT, no tax rate, no component series. That is a
    single new parameter, and it is present. So the assertion below is not
    "`sales_to_capital` is absent" any more; it is that the component build-up
    the sketch needed is still absent, and that the margin-only shape which made
    the sketch uninsertable is what made the post-mortem's form insertable. The
    two are the same fact read from opposite sides.
    """
    params = list(inspect.signature(dcf_agent._project_dcf).parameters)
    assert params == [
        "revenue_base", "fcf_margin_base", "growth_rate", "margin_delta_per_year",
        "wacc", "tgr", "fcf_floor", "net_debt", "shares", "years",
        "growth_schedule", "wacc_schedule", "margin_delta_absolute",
        "include_terminal", "sales_to_capital",
        # 2026-09-22: a per-year MARGIN path, for the faded FCF-guidance overlay
        # (years 1-3 guided, 4-7 fading, 8-10 and the terminal on a floor). It
        # was on the absent list below as part of the rejected sketch; it is
        # admitted because it keeps exactly the property this test defends --
        # the projector is still margin-only. It carries ten margins, not ten
        # years of EBIT, D&A and capex, and None reproduces the old path exactly
        # (pinned in tests/test_backlog_gated_long_cycle.py).
        "margin_schedule",
        # 2026-09-26 (owner, Priority 1): the one equity bridge -- minority interest
        # and preferred equity come off the DCF as they do off every EV leg.
        "minority_interest", "preferred_equity",
    ], params
    for absent in ("overrides", "effective_tax_rate", "capex", "da",
                   "depreciation", "ebit",
                   "reinvestment_deduction"):
        assert absent not in params, f"{absent!r} is now a parameter"
    src = _engine_src()
    # The sketch's own identifier, and the cash-line variable name. Neither is a
    # substring of anything that shipped: the mechanism is a margin deduction
    # named `reinvest_margin_deduction`, deliberately, so that grepping for the
    # sketch's vocabulary finds nothing and nobody mistakes one for the other.
    assert "reinvestment_deduction" not in src
    assert 'overrides["apply_reinvestment_deduction"]' not in src
    # And the parameter that did land is unused at every call site — pinned
    # separately, because "present in the signature" says nothing about whether
    # the engine charges anything.
    assert src.count("sales_to_capital=") == 4, (
        "expected four PROSE mentions documenting the wiring to restore and no "
        "live keyword arguments; test_the_reinvestment_gate_is_observation_only_"
        "and_says_so is what pins the latter")


@pytest.mark.xfail(strict=True, reason=(
    "IMPLEMENTED BUT NOT WIRED. The margin form of this charge exists — "
    "`_reinvestment_margin_deduction` computes g/((1+g)·(S/C)) and "
    "`_project_dcf` accepts `sales_to_capital` — and this test still fails "
    "because it passes no ratio and no call site does either, so the engine "
    "levies nothing. It is observation-only behind GATE_GROWTH_REINVESTMENT "
    "because wiring it live moved base IV on 9 of the 14 golden fixtures over "
    "−9.45% to +17.40%, each replayed in its own subprocess, with the sign "
    "INVERTED on 09988_HK (+17.40%) and BABA (+15.60%). The payload names the "
    "mechanism on both: `iv_dcf` 88.09 → None and 114.81 → None, `weight_dcf` "
    "0.2778 → 0.0, `weight_multi` 0.7222 → 1.0, `methods_count` 5 → 4, "
    "`methods_used` loses DCF, and BABA's 12-month base target rose 151.25 → "
    "166.31. On every low-S/C name the deduction exceeded the base margin "
    "(09988_HK 9.51% − 10.72pp, BABA 9.51% − 10.96pp, SCHW 11.53% − 16.19pp), "
    "the floor absorbed the rest, `forward_roic` went negative, and Gate B "
    "zeroed terminal growth on BABA, FCX and SCHW. A dropped leg renormalises "
    "onto the survivors, so where the DCF is the LOW leg — which is where it is "
    "doing its job — charging more values the company HIGHER. "
    "UPDATE 2026-09-18 — BOTH BLOCKERS THIS REASON NAMED ARE NOW CLOSED, and the "
    "test still fails, which is the informative part. The reason used to read: "
    "'the blocker is not the algebra: it is that revenue/invested_capital is not "
    "sales-to-capital for balance-sheet-funded profiles (measured S/C 0.063 for an "
    "S-REIT, 0.806 for a broker, 10.912 for a retailer — a 173x span) and that "
    "nothing decides what happens when a deduction exceeds the margin it is taken "
    "from.' The first is now a positive profile allowlist, "
    "CAPITAL_TURNOVER_PROFILES, applied inside the helper — and the span is 175x "
    "on a cleaner measurement (0.0625 for C38U_SI's S-REIT to 10.9116 for COST's "
    "membership retail), not 173x. The second is now an explicit rationing bound, "
    "min(raw, max(fcf_margin_base − fcf_floor, 0)), owner-specified. Exactly one "
    "of the 14 golden fixtures is inside the allowlist (MELI, Hyper-Growth "
    "Platform, raw +6.53% against a +30.28% base margin) and on that one the cap "
    "does not bind, so after scoping the cap binds on ZERO of the 14 — all seven "
    "names where it binds are out of scope. WHAT STILL BLOCKS IT, and why wiring "
    "the ratio alone is no longer enough: `_project_dcf` calls the helper with no "
    "`profile` and no `margin_headroom`, and both gates fail closed, so the "
    "projector is scope-inert BY CONSTRUCTION and would charge nothing even if "
    "every call site handed it a ratio. Making this test pass is a three-part "
    "change — add both to the projector's signature, pass them at the call, and "
    "move `_y10_fcf_margin` in the same commit — pinned by "
    "`tests/test_reinvestment_scope_and_cap.py`. Also note this test's own form is "
    "the CASH-LINE variant, "
    "`revenue*margin - charge`, which the post-mortem showed is algebraically "
    "identical per year but does NOT reach the terminal value, and the TV is "
    "86.0% of the total for these inputs; a green version of this test would "
    "have to be rewritten to the margin form before it proved anything about "
    "the published number. The two mechanisms that bound hyper-growth still "
    "bound the RATE, not the capital: GATE_REVENUE_SCALE_CAP caps the growth "
    "rate at scale, and GATE_GROWTH_CAGR_DIVERGENCE caps it against history. "
    "Neither charges the cash flow, so a name that clears both still projects a "
    "free lunch."))
def test_a_twenty_eight_percent_grower_pays_delta_rev_over_sales_to_capital():
    _, _, _, rows = dcf_agent._project_dcf(
        2000.0, 0.19, 0.28, 0.0, 0.09, 0.025, -0.05, 0.0, 100.0, years=3)
    y1 = rows[0]
    delta_rev = y1["revenue"] - 2000.0
    assert delta_rev == pytest.approx(560.0, rel=1e-9)
    charge = delta_rev / 1.85
    assert charge == pytest.approx(302.70, abs=0.05)
    assert y1["fcf"] == pytest.approx(
        y1["revenue"] * y1["fcf_margin"] - charge, rel=1e-6)


# ── C. EL — trough/peak margin distortion ────────────────────────────────────


def test_el_changes_profile_with_the_cycle_and_lands_on_trailing_e_at_the_trough():
    """The classifier is the brief's Gate 3, and it points the wrong way.

    Run through the real function at EL's three cycle positions. At a trough —
    the exact state the brief says needs protecting — EL routes to
    `Household / Personal`, whose anchor is plain `P/E` at weight 0.40. At a
    peak it routes to `Luxury Goods`, whose anchor is `P/E (Premium)` at 0.50.
    Both of those method names dispatch to the SAME trailing-12m branch (see
    `test_pe_premium_and_pe_share_the_trailing_branch`), so EL is anchored on
    unadjusted trailing earnings at both ends of the cycle, at 40% and 50% of
    the blend. The only cycle position where it gets a normalized leg at all is
    the healthy middle, `Apparel / Athletic Wear`, which carries `P/E (norm)` at
    0.20 — and that is a sportswear profile applied to a beauty company.

    So the brief's premise ("route forward earnings to normalized median EBIT
    when the margin deviates > 40%") describes a gate; what exists is a
    classifier that responds to the same deviation by moving the name onto a
    MORE trailing-dependent profile.
    """
    c = classify_valuation_profile
    assert c("Consumer", -0.02, 0.10, 0.5) == "Household / Personal"   # trough
    assert c("Consumer", 0.06, 0.16, 0.5) == "Apparel / Athletic Wear"  # healthy
    assert c("Consumer", 0.08, 0.18, 0.5) == "Luxury Goods"             # peak
    assert c("Consumer", 0.08, 0.20, 0.5) == "Luxury Goods"

    m = _methods
    # Wave 4 (2026-09-26): the Household anchor is Forward P/E, same weight.
    assert m("Household / Personal")["Forward P/E"]["anchor"] is True
    assert m("Household / Personal")["Forward P/E"]["weight"] == 0.40
    assert "P/E (norm)" not in m("Household / Personal")
    assert m("Luxury Goods")["P/E (Premium)"]["anchor"] is True
    assert m("Luxury Goods")["P/E (Premium)"]["weight"] == 0.50
    assert "P/E (norm)" not in m("Luxury Goods")
    assert m("Apparel / Athletic Wear")["P/E (norm)"]["weight"] == 0.20
    assert m("Apparel / Athletic Wear")["P/E (norm)"]["anchor"] is False


def test_the_consumer_ladder_no_longer_demotes_a_better_margin():
    """RENAMED AND INVERTED. This test used to be
    `test_the_consumer_ladder_is_a_knife_edge_on_two_thresholds`, and it FAILED on
    four assertion groups when Step 7 landed. It is rewritten here rather than
    deleted, because it was written as a defect witness and the defect is now
    fixed — so its old assertions are the precise record of what was broken.

    What it used to assert, and what the ladder does now:

        cagr 3%, fcf 0.179 -> Apparel / Athletic Wear   (unchanged)
        cagr 3%, fcf 0.180 -> Household / Personal      -> Luxury Goods
        cagr 4.9%, fcf 0.30 -> Household / Personal      -> Luxury Goods
        cagr 50%, fcf 0.12 -> Traditional Retail         -> Apparel / Athletic Wear

    The first two were cliff (i): the `cagr < 0.05` Household rung stood ABOVE the
    `fcf >= 0.15` Luxury rung, so the strongest cash converter in a slow-growth
    band got the second-weakest profile in the sector. The band-pass upper bound
    `fcf < 0.18` on rung 1 is still there — it could not simply be deleted, because
    deleting it would have made the band swallow every margin above 0.05 and left
    Luxury Goods unreachable at a low CAGR — but the fall-through it produces is
    now a promotion instead of a demotion, which is what makes the bound safe.

    The third was cliff (ii), the documented ONON case: `cagr >= 0.40` was outside
    the band's CAGR range, and with `fcf` below 0.15 nothing caught it until
    Traditional Retail — the only Consumer profile with no DCF leg at all. A name
    was punished for growing fast by being valued entirely on what its peers cost.

    Both are now repaired, and the repair is verified over a 3000-cell grid with a
    complete enumeration of the move set in
    `tests/test_consumer_monotonic_ladder.py`. This test keeps the shapes the brief
    cared about; that module keeps the proof.

    Still true and still worth pinning: the ladder remains a first-match ladder
    with no hysteresis, no weighting and no smoothing, so a name near a rung
    re-classifies on a restatement. The migration removed the rungs that classified
    in the WRONG DIRECTION. It did not make the ladder continuous, and one
    margin-axis step into a structural profile survives — `cagr < 0.03`,
    fcf 0.179 -> 0.180 moves Apparel -> Food & Beverage — because repairing it by
    reordering would make Food & Beverage unreachable. That residual is named and
    pinned in `TestTheKnownResiduals` over there.
    """
    c = classify_valuation_profile
    # The lower edge of the band is unchanged: below 5% FCF margin a slow grower
    # is Household / Personal, and this was never a defect.
    for fcf in (0.02, 0.04, 0.049):
        assert c("Consumer", 0.03, fcf, 0.6) == "Household / Personal", fcf
    for fcf in (0.05, 0.10, 0.179):
        assert c("Consumer", 0.03, fcf, 0.6) == "Apparel / Athletic Wear", fcf
    # WAS: Household / Personal. The upper edge now promotes.
    for fcf in (0.18, 0.25, 0.30):
        assert c("Consumer", 0.03, fcf, 0.6) == "Luxury Goods", fcf
    # And it holds across the whole sub-5% CAGR range, not just at NKE's 3%.
    # WAS: in ("Household / Personal", "Food & Beverage"). At cagr < 0.03 the
    # Food & Beverage rung still catches a >= 0.15 margin, which is a structural
    # classification and not a demotion; at 0.04 and 0.049 it is Luxury Goods.
    for cagr in (0.0, 0.02, 0.049):
        assert c("Consumer", cagr, 0.04, 0.6) == "Household / Personal"
        assert c("Consumer", cagr, 0.30, 0.6) in \
            ("Luxury Goods", "Food & Beverage")
    assert c("Consumer", 0.0, 0.30, 0.6) == "Food & Beverage"
    assert c("Consumer", 0.02, 0.30, 0.6) == "Food & Beverage"
    assert c("Consumer", 0.049, 0.30, 0.6) == "Luxury Goods"
    # Crossing 5% CAGR at a strong margin used to JUMP two rungs, from Household
    # (tier 2) to Luxury (tier 5). It is now continuous: Luxury on both sides.
    assert c("Consumer", 0.049, 0.30, 0.6) == "Luxury Goods"
    assert c("Consumer", 0.05, 0.30, 0.6) == "Luxury Goods"

    # The method-quality comparison that made the old cliff a real downgrade and
    # not a relabel. Kept as a table fact: it is what the demotion cost, and it is
    # also what the NEW edge costs nothing, because Apparel -> Luxury keeps a DCF
    # leg and gains a premium earnings anchor.
    ap, hh = _methods("Apparel / Athletic Wear"), _methods("Household / Personal")
    assert ap["DCF (FCF+)"]["weight"] == 0.30 and ap["EV/EBITDA"]["anchor"] is True
    assert hh["DCF"]["weight"] == 0.20 and hh["Forward P/E"]["anchor"] is True   # Wave 4: NTM anchor
    assert hh["Forward P/E"]["weight"] == 0.40
    assert "P/E (norm)" in ap and "P/E (norm)" not in hh
    assert set(ap) & set(hh) == {"EV/EBITDA"}, sorted(set(ap) & set(hh))
    lux = _methods("Luxury Goods")
    assert lux["P/E (Premium)"]["anchor"] is True
    assert "DCF (LTG)" in lux, sorted(lux)
    # Both spellings on the old demotion's anchor and on the new arrival's anchor
    # are in `_PE_NORM_SWAP_LEGS`, so the Failure-2 trough repair reaches a name
    # whichever side of the 0.18 bound it sits on. That is the reason the
    # remaining step is survivable.
    from src.agents.analysis import dcf_agent as _da
    assert {"P/E", "P/E (Premium)"} <= set(_da._PE_NORM_SWAP_LEGS)

    # Anta across the 15% FCF rung — unchanged by the migration.
    assert c("Consumer", 0.12, 0.18, 0.4) == "Luxury Goods"
    assert c("Consumer", 0.12, 0.12, 0.4) == "Apparel / Athletic Wear"
    # ONON across the 40% CAGR rung. WAS: Traditional Retail.
    assert c("Consumer", 0.30, 0.12, 0.3) == "Apparel / Athletic Wear"
    assert c("Consumer", 0.50, 0.12, 0.3) == "Apparel / Athletic Wear"
    assert c("Consumer", 0.399, 0.12, 0.3) == "Apparel / Athletic Wear"
    # The Luxury and Traditional Retail method sets are still disjoint, which is
    # what made the ONON flip violent when it happened. It no longer happens, but
    # the disjointness is the reason the fix was worth making rather than cosmetic.
    assert set(_methods("Luxury Goods")) & set(_methods("Traditional Retail")) == set(), (
        "Luxury Goods and Traditional Retail now share a method, so the cliff-(ii) "
        "demotion this test documents was less violent than stated")
    assert "DCF (LTG)" not in _methods("Traditional Retail")
    assert not any("dcf" in n.lower() for n in _methods("Traditional Retail")), (
        "Traditional Retail now carries a DCF leg, so it is no longer the weakest "
        "methodology in the sector and the tier order in "
        "tests/test_consumer_monotonic_ladder.py needs re-deriving")


def test_pe_premium_and_pe_share_the_trailing_branch():
    """`P/E (Premium)` is not a premium earnings source. It is the same TTM net
    income as plain `P/E`, under a different label.

    Pinned from the engine's own source and its own comment, because the profile
    name and the method name both imply otherwise. The branch set is
    `{"P/E", "P/E (ops)", "P/E (Premium)", "P/E (Ops)"}` and the comment above
    it says the two spellings "differ only in documentation intent, not earnings
    source". The normalized path is a separate branch keyed on
    `{"P/E (norm)", "P/E norm", "Normalized P/E"}`, which no Consumer profile
    other than `Apparel / Athletic Wear` NAMES.

    "Names" is the operative word and it is now doing work it did not used to.
    Since the Failure-2 swap, a profile whose trailing net income deviates more
    than 40% from its five-year norm has its `P/E` / `P/E (Premium)` legs
    rewritten to `P/E (norm)` on a copy of the row before the blend, so at
    runtime three more Consumer profiles can reach the normalized branch. The
    profile TABLES are unchanged, which is what this test reads; the dispatch is
    what the swap changes. See
    `test_the_pe_norm_swap_promotes_a_trailing_leg_to_normalized_earnings`.

    Consequence for the brief's Gate 3, which describes only the over-valuation
    direction: the exposure is two-sided. At a peak, trailing NI × a "stable and
    high" multiple over-values; at a trough the same leg under-values, because
    the multiple does not fall with the earnings. `Luxury Goods`' own rationale
    — "Pricing power and brand equity make P/E multiples stable and high" — is
    the assumption that fails at a turning point, in both directions at once.
    """
    src = _engine_src()
    assert 'if method_name in {"P/E", "P/E (ops)", "P/E (Premium)", "P/E (Ops)"}:' in src
    assert 'if method_name in {"P/E (norm)", "P/E norm", "Normalized P/E"}:' in src
    assert "they differ only in documentation intent, not earnings" in src
    assert "For the TRUE cycle-normalized path use" in src
    # The trailing branch multiplies TTM net income; the normalized branch reads
    # the normalized figure. One reader each, so the two cannot be conflated.
    assert 'eps = (net_income / shares) if (net_income is not None and shares > 0) else None' in src
    assert 'norm_ni = most_recent.get("normalized_net_income")' in src
    assert _consumer_profiles()["Luxury Goods"]["rationale"].startswith(
        "Pricing power and brand equity make P/E multiples stable and high.")


def test_normalized_ebit_is_computed_for_every_name_and_read_by_nothing():
    """The quantity Gate 3 wants already exists, on every run, unused.

    `run_dcf_agent` computes all three normalized figures for every ticker and
    stores them on the most-recent row. `normalized_net_income` has four
    readers; `normalized_ebitda` has one; **`normalized_ebit` has none** — the
    string appears exactly once in the engine, and that occurrence is the write.

    This changes what implementing Gate 3 costs. It is not a modelling gap, it
    is a dispatch gap: the figure the brief asks to route forward earnings to is
    already in `most_recent`, and the only consumer-side work is a branch that
    reads it. Compare `normalized_ebitda`, which is read at exactly one place —
    inside the `EV/EBITDA (norm)` method branch — and is therefore reachable
    only from the four profiles that name that method, none of them Consumer.

    The string count is six and not four because the valuation-ledger row names
    the key twice on one line, once as the dict key and once inside its own
    `_ledger_num(most_recent.get(...))`. The four readers are the `P/E (norm)`
    branch, the `P/B-ROE (mid-cycle)` branch, `_pe_normalization_deviation` —
    the Failure-2 swap, which added the sixth occurrence and is the reason this
    count moved from five — and the ledger. A count that disagrees with this
    list means a reader arrived or left without the list being updated, which is
    the only thing this assertion is for: it cannot tell a good reader from a
    bad one.
    """
    src = _engine_src()
    # The dispatch gap the docstring describes was closed on 2026-09-22: the
    # "EV/EBIT (norm)" leg (Boeing on a normalised EV/EBIT, owner framework) is
    # its one reader. Two occurrences: the write, and that branch's read.
    assert src.count('"normalized_ebit"') == 2, "normalized_ebit gained a reader beyond EV/EBIT (norm)"
    # THREE since 2026-09-20: the segment SOTP reconciles its estimated segment
    # EBITDA to this figure, so the parts sum to the company's own normalised
    # earnings. Phillips 66's segments summed to $16.6bn of EBITDA against the
    # $9.8bn it reported, because a peer basket's margin is a pure-play margin
    # and segment revenue is not pure-play revenue.
    assert src.count('"normalized_ebitda"') == 3
    # SEVEN since 2026-09-19: the P/E (norm) leg trace reads it once more, to
    # label whether the bank fallback (equity x target ROE) supplied the
    # earnings -- a disclosure read for the Excel export, not a valuation one.
    assert src.count('"normalized_net_income"') == 7
    # SIX since 2026-09-20: the FCF Yield leg on a cyclical profile now stands
    # on the same five-year normalisation as the EV/EBITDA and P/E legs, so
    # `_norm_fcf` is computed beside the other three (with a `free_cash_flow`
    # fallback for filers disclosing no SBC, hence two new call sites, not one).
    # Before this the blend was two-thirds mean-reverted and one-fifth raw TTM:
    # Phillips 66's FCF leg priced $64.31 against a $273.13 quote off a trough.
    assert src.count("_normalized_earnings(") == 6   # def + five call sites
    assert '_norm_ebit   = _normalized_earnings(series, "ebit",       window=5)' in src
    assert 'most_recent["normalized_ebit"]       = _norm_ebit' in src


def test_no_consumer_profile_can_reach_the_normalized_ebitda_branch():
    """The one normalized-earnings branch with a reader is unreachable from
    Consumer. Measured across all 85 method names in the taxonomy.

    `EV/EBITDA (norm)` / `EV/EBITDA (Norm)` are named by exactly four profiles:
    Local Services & Instant Retail (Tech), Digital Asset Mining (Crypto),
    Steel / Metals (Materials) and Mining (Major) (Resources) — the four most
    cyclical profiles in the book, which is the right instinct. Consumer's
    13 profiles name none of those spellings, so `normalized_ebitda` is dead for
    every archetype in this brief even though it is computed for all of them.
    """
    norm_ev = {"EV/EBITDA (norm)", "EV/EBITDA (Norm)", "EV/EBITDA norm",
               "Normalized EV/EBITDA"}
    users = []
    for sector, profiles in INDUSTRY_VALUATION_PROFILES.items():
        for pname, d in profiles.items():
            for m in d.get("methods", []):
                if m.get("name") in norm_ev:
                    users.append((sector, pname, m["name"]))
    # Wave 1 oil, gas & coal (owner-approved 2026-09-20) added five users, none of them Consumer.
    # Wave 2 power & transition (owner-confirmed 2026-09-21) added one, the hardware OEM profile.
    assert sorted(users) == [
        ("Consumer", "Agribusiness & Food Processing", "EV/EBITDA (norm)"),   # Wave 4: cyclical by design
        ("Crypto", "Digital Asset Mining", "EV/EBITDA (norm)"),
        ("Energy", "Clean Tech / Power Equipment OEM", "EV/EBITDA (norm)"),
        ("Energy", "Oilfield Services & Drilling", "EV/EBITDA (norm)"),
        ("Energy", "Refining & Marketing", "EV/EBITDA (norm)"),
        ("Industrials", "Defense Primes", "EV/EBITDA (norm)"),      # Wave 3: through-cycle check on the primes
        ("Materials", "Steel / Metals", "EV/EBITDA (Norm)"),
        ("Resources", "Coal", "EV/EBITDA (norm)"),
        ("Resources", "Integrated Oil & Gas", "EV/EBITDA (norm)"),
        ("Resources", "Mining (Major)", "EV/EBITDA (norm)"),
        ("Resources", "Upstream Oil & Gas", "EV/EBITDA (norm)"),
        ("Tech", "China Internet Platform", "EV/EBITDA (norm)"),
        ("Tech", "Local Services & Instant Retail", "EV/EBITDA (norm)"),
    ], users
    consumer_vocab = {m["name"] for d in _consumer_profiles().values()
                      for m in d.get("methods", [])}
    # Wave 4 (2026-09-26): Agribusiness & Food Processing is the one Consumer
    # profile on the normalised EBITDA branch, by design (crush-spread cycle).
    assert consumer_vocab & norm_ev == {"EV/EBITDA (norm)"}
    assert len(_consumer_profiles()) == 16   # +3 Wave 4 profiles (2026-09-26)


def test_the_normalizers_outlier_floor_is_relative_and_the_absolute_floor_is_gone():
    """FIXED. This test previously asserted the defect and its thesis was that
    the engine's filter is loosest exactly where consumer margins are thinnest.
    That thesis is now false, so the assertions are inverted into a guard and
    the old numbers are kept as the record of what the defect cost.

    `_normalized_earnings` drops a year when `|margin − median| > threshold`.
    The threshold was `max(2·IQR, 0.05)` — an ABSOLUTE five-point floor — and is
    now `max(2·IQR, |median| · 0.30)`, the relative form the brief's Gate 3
    asks for. Every number below is produced by calling the real function.

    The two rules differ in BOTH directions, which is the part that is easy to
    get wrong:

      * REMOVING the floor tightens any series whose own dispersion is under
        5pp, because `2·IQR` is then free to bind. That is mechanism (a).
      * The relative term LOOSENS any rich-margin series, because 30% of a 38%
        median is 11.3pp, well past both the old floor and the dispersion.
        That is mechanism (b).

    Both are exercised against the fixture series that actually moved.
    """
    import statistics

    from src.agents.analysis.dcf_agent import _NORMALIZED_OUTLIER_REL

    rev = 10_000.0

    def norm(margins):
        s = [{"revenue": rev, "ebit": rev * m} for m in margins]
        return dcf_agent._normalized_earnings(s, "ebit", window=5) / rev

    assert _NORMALIZED_OUTLIER_REL == 0.30

    # ── EL is UNCHANGED, so the fix costs the name it was designed for nothing.
    # 2·IQR = 0.014, relative term 0.0423: the relative term wins over the
    # dispersion, but the trough's deviation (0.089) exceeds BOTH rules, so the
    # same four years survive and the answer is the same 0.144.
    el = [0.052, 0.138, 0.152, 0.145, 0.141]
    assert norm(el) == pytest.approx(0.144, abs=1e-9)
    el_rev = 15_600.0
    assert el_rev * norm(el) == pytest.approx(2246.4, abs=0.05)
    assert el_rev * statistics.median(el) == pytest.approx(2199.6, abs=0.05)
    assert norm(el) / statistics.median(el) == pytest.approx(1.02128, abs=1e-4)
    assert 0.40 * statistics.median(el) == pytest.approx(0.0564, abs=1e-9)

    # ── THIN margins: the defect, and its fix. ────────────────────────────
    # A flat 5% business with one collapsed year has IQR = 0, so the old
    # threshold was the bare 0.05 floor and NOTHING at a 5% median could ever
    # be excluded — the allowance was 100% of the margin itself. A 60% relative
    # collapse was averaged straight in.
    thin = [0.020, 0.050, 0.050, 0.050, 0.050]
    assert norm(thin) == pytest.approx(0.050, abs=1e-9)
    assert norm(thin) == pytest.approx(statistics.median(thin), abs=1e-9)
    assert statistics.mean(thin) == pytest.approx(0.044, abs=1e-9), (
        "the OLD rule returned this: the trough survived and dragged the "
        "normalized figure 12% below the median")
    deeper = [0.005, 0.050, 0.050, 0.050, 0.050]      # a 90% relative collapse
    assert norm(deeper) == pytest.approx(0.050, abs=1e-9)
    assert statistics.mean(deeper) == pytest.approx(0.041, abs=1e-9), (
        "the OLD rule returned this: 18% below the median it was robust to")

    # ── RICH margins: unchanged here, and only by luck. ───────────────────
    # The relative term is 0.090, LOOSER than the old 0.05, yet the answer is
    # identical because the trough's deviation (0.130) exceeds both. A smaller
    # deviation at this margin level WOULD now survive where it did not before.
    rich = [0.170, 0.300, 0.300, 0.300, 0.300]
    assert norm(rich) == pytest.approx(0.300, abs=1e-9)
    assert 0.300 * _NORMALIZED_OUTLIER_REL == pytest.approx(0.090, abs=1e-9)
    assert abs(0.170 - 0.300) > 0.090 > 0.05

    # ── Mechanism (a), measured on the fixtures that moved. ───────────────
    # 09988_HK and BABA share this exact net-income margin series (same
    # company; margins are currency-invariant). 2·IQR = 0.0348 beats the
    # relative term 0.0255, so the RELATIVE TERM LOSES and the whole move
    # comes from the floor's removal: FY2025 (+53.6% above the median) is
    # excluded and normalized NI falls 9.47%.
    baba_ni = [0.07297, 0.08379, 0.08501, 0.13059, 0.10120]
    med_ni = statistics.median(baba_ni)
    s = sorted(baba_ni)
    assert max((s[3] - s[1]) * 2, med_ni * 0.30) == pytest.approx(0.03482,
                                                                 abs=1e-5)
    assert (s[3] - s[1]) * 2 > med_ni * _NORMALIZED_OUTLIER_REL, (
        "the relative term must LOSE here or this is not mechanism (a)")
    assert norm(baba_ni) / statistics.mean(baba_ni) == pytest.approx(
        1 - 0.0947, abs=1e-3)

    # ── Mechanism (b), measured on FCX. ───────────────────────────────────
    # A monotone five-year decline, so there is no statistical outlier at all.
    # 2·IQR = 0.0528, relative term 0.1135: the RELATIVE TERM WINS, FY2021 is
    # read back in, and normalized EBITDA rises 4.66%. This is the leg FCX's
    # anchor consumes, so it is the only fixture whose IV moved.
    fcx_ebitda = [0.45887, 0.39830, 0.37825, 0.37191, 0.34020]
    med_fcx = statistics.median(fcx_ebitda)
    sf = sorted(fcx_ebitda)
    assert med_fcx * 0.30 > (sf[3] - sf[1]) * 2, (
        "the relative term must WIN here or this is not mechanism (b)")
    assert norm(fcx_ebitda) == pytest.approx(statistics.mean(fcx_ebitda),
                                             abs=1e-9), (
        "all five years must survive: the mean over the window IS the method")
    assert norm(fcx_ebitda) / 0.372166 == pytest.approx(1.0466, abs=1e-3)

    # ── Mechanism (b) on a knife-edge: C38U_SI. ───────────────────────────
    # A high-margin S-REIT whose net margin swings on property fair-value
    # gains. 2·IQR = 0.07126 and the FY2022 deviation is 0.07151, so the OLD
    # rule excluded it by 0.00025 — a near-tie settled in the fourth decimal,
    # which is an artefact rather than a judgement. The relative term is
    # 0.17197 here, so FY2022 comes back in and the average falls 3.06%.
    #
    # The old rule trimmed BOTH tails (the 0.83 revaluation peak and the 0.50
    # trough) and kept three middle years. For a series that swings on
    # revaluation that is the wrong sample: a mid-cycle average should span the
    # swing, not delete both ends of it. The new rule keeps the peak excluded
    # and the trough included, which is the symmetric answer.
    c38u_ni = [0.82992, 0.50173, 0.55295, 0.58858, 0.57324]
    med_c = statistics.median(c38u_ni)
    sc = sorted(c38u_ni)
    iqr2_c = (sc[3] - sc[1]) * 2
    assert iqr2_c == pytest.approx(0.07126, abs=1e-5)
    assert abs(0.50173 - med_c) - iqr2_c == pytest.approx(0.00025, abs=1e-5), (
        "the old rule's exclusion of FY2022 was a knife-edge")
    assert med_c * 0.30 > iqr2_c
    assert norm(c38u_ni) == pytest.approx(0.554127, abs=1e-5)
    assert norm(c38u_ni) / 0.571592 == pytest.approx(1 - 0.0306, abs=1e-3)


def test_the_relative_term_uses_abs_median_so_loss_makers_are_not_exempt():
    """`abs(med)`, not `med`. The signed product is negative for a loss-maker,
    so `max()` would collapse onto `2·IQR` and the relative term would be inert
    for exactly the names whose margins are most distorted. Measured divergence
    on a tight loss series: abs keeps all five and returns -0.1060, signed
    filters one and returns -0.1000.
    """
    import statistics

    rev = 10_000.0

    def norm(margins):
        s = [{"revenue": rev, "ebit": rev * m} for m in margins]
        return dcf_agent._normalized_earnings(s, "ebit", window=5) / rev

    losses = [-0.130, -0.100, -0.095, -0.105, -0.100]
    med = statistics.median(losses)
    assert med < 0
    s = sorted(losses)
    assert abs(med) * 0.30 > (s[3] - s[1]) * 2, "the guard needs the abs term"
    assert norm(losses) == pytest.approx(-0.106, abs=1e-9)
    # A deep loss-maker, where both readings agree — recorded so the choice is
    # not mistaken for one that only matters on shallow series.
    deep = [-0.500, -0.520, -0.480, -0.510, -0.490]
    assert norm(deep) == pytest.approx(statistics.median(deep), abs=1e-9)


def test_the_short_paths_are_unchanged_and_the_median_fallback_is_unreachable():
    """The floor only applies on the `len(margins) > 2` branch. Two observations
    average unconditionally, one or zero returns None.

    The third path — `avg_margin = ... if len(filtered) >= 2 else med` — is
    PROBABLY DEAD, and that is worth pinning because it is the safety net the
    relative-floor change was leaning on. `q1` is taken at index `n // 4` and
    `q3` at `3 * n // 4`, so for n = 5 those are s[1] and s[3]. Both sit within
    one IQR of the median s[2] by construction (s[2] − s[1] ≤ s[3] − s[1] = iqr,
    and s[3] − s[2] ≤ iqr likewise), and `threshold ≥ 2 · iqr ≥ iqr`, so s[1],
    s[2] and s[3] ALWAYS survive. Three survivors minimum, never below two.

    A single extreme value cannot break that either: at n = 5 the outlier lands
    on s[4], which is not a quartile index, so it inflates no threshold and is
    simply excluded. The fallback would need n ≤ 2, and that case returns
    earlier. So the median branch cannot be reached from any input.
    """
    rev = 10_000.0

    def norm(margins):
        s = [{"revenue": rev, "ebit": rev * m} for m in margins]
        return dcf_agent._normalized_earnings(s, "ebit", window=5)

    assert norm([0.10, 0.30]) == pytest.approx(0.20 * rev, abs=1e-6)
    assert norm([0.10]) is None
    assert norm([]) is None

    # The most extreme series that still has five observations: four normal
    # years and one absurd. iqr collapses to 0, the relative term governs, the
    # absurd year is dropped and the four survivors average — the fallback is
    # still not reached, exactly as the index arithmetic above predicts.
    spiked = [0.10, 0.10, 0.10, 0.10, 1e6]
    assert norm(spiked) == pytest.approx(0.10 * rev, abs=1e-6)

    # And the mirror image: an absurd year at the LOW end, on a thin-margin
    # business where the relative term is small. Still three-plus survivors.
    collapsed = [0.10, 0.10, 0.10, 0.10, -1e6]
    assert norm(collapsed) == pytest.approx(0.10 * rev, abs=1e-6)


def test_the_sibling_fcf_normalizer_still_carries_the_absolute_floor():
    """NOT FIXED, and deliberately not fixed here: `_mean_fcf_margin` has the
    identical `max(iqr * 2, 0.05)` line, and it produces `fcf_margin_base` —
    the most load-bearing number in the DCF, since it feeds the margin schedule,
    the ROIC projection and every scenario multiplier. The same thin-margin
    blindness therefore applies with far greater consequence than it did in
    `_normalized_earnings`.

    This pins the asymmetry so it can neither be silently "fixed" by a future
    edit to the shared idiom nor silently forgotten. Changing one without the
    other should fail here first.

    Comments are stripped before matching. `_normalized_earnings` carries a long
    historical note quoting the OLD expression verbatim, so a naive substring
    search over `inspect.getsource` matches the prose describing the fix and
    reports the defect as still present — this test failed that way on its first
    run.
    """
    def live_code(fn) -> str:
        out = []
        for line in inspect.getsource(fn).splitlines():
            code = line.split("#", 1)[0]
            if code.strip():
                out.append(code)
        return "\n".join(out)

    assert "max(iqr * 2, 0.05)" in live_code(dcf_agent._mean_fcf_margin), (
        "_mean_fcf_margin no longer carries the absolute floor — either it was "
        "fixed (update this test and the golden baseline) or its shape changed")

    fixed = live_code(dcf_agent._normalized_earnings)
    assert "_NORMALIZED_OUTLIER_REL" in fixed
    assert "max(iqr * 2, 0.05)" not in fixed


def test_the_normalized_ni_flag_promises_a_leg_most_profiles_do_not_have():
    """FOUND WHILE INVESTIGATING THE RELATIVE-FLOOR GOLDEN DIFF, not from the
    brief. The audit flag appended next to `normalized_net_income` says, in
    terms, that `P/E (norm) will use normalized figure`. It is gated on the
    normalized value existing and the delta exceeding 15% — and on NOTHING else.
    It never checks whether the profile has a `P/E (norm)` leg to use it.

    75 of the 99 profiles have no such leg, so for three quarters of the
    universe the flag asserts a substitution the engine will not perform.

    This stopped being theoretical when the relative floor landed. It moved
    `normalized_net_income` −9.47% on 09988_HK and BABA, which pushed the TTM
    delta past 15% and made the flag FIRE on both names for the first time — in
    all three scenarios. Both are `Hyperscaler / Tech Conglomerate`, whose
    `methods_used` is ['DCF', 'EV/EBITDA', 'P/E']: plain trailing P/E. The flag
    now appears on those runs promising a normalized leg that is not in the
    method set, and base IV does not move (160.84 and 193.13, bit-identical),
    which is the observable proof that nothing consumed the number.

    PARTLY ADDRESSED, and the residual is narrower than it was. The Failure-2
    swap now rewrites ALL FOUR trailing P/E spellings — `P/E`, `P/E (Premium)`,
    `P/E (ops)` and `P/E (Ops)` — to `P/E (norm)` when trailing net income
    deviates more than 40% from its five-year norm, so above 40% the promise is
    kept for all 37 profiles that carry a trailing P/E leg and no normalized P/E
    leg. It used to name two spellings and reach 31; the owner widened it, on the
    reasoning that operating earnings are distorted by the same cycle as
    consolidated ones (see `test_the_ops_spellings_are_now_in_the_swap`). The
    spelling gap is closed.
    Two gaps remain and both are load-bearing:

      * the thresholds differ — the flag fires at 15%, the swap at 40%, so the
        band between them still emits the promise and still does not perform
        the substitution. Both Alibaba fixtures sit in that band at −15.3%.
      * the swap reads `normalized_net_income` and only reaches P/E legs, so
        the other 62 profiles — the ones with no trailing P/E leg at all —
        are untouched by it.
    The flag's own gate is deliberately NOT made profile-aware: it would then
    report the leg it happened to have rather than the deviation it measured,
    and the deviation is the number an auditor wants. Making the disclosure
    honest means aligning the thresholds, not silencing the flag.
    """
    src = _engine_src()
    i = src.index("Normalized NI: TTM")
    block = src[max(0, i - 900): i + 600]

    # The threshold, read from source rather than recalled.
    assert "abs(_delta_pct) > 0.15" in block
    # The promise, verbatim.
    assert "P/E (norm) will use normalized figure" in block
    # And the gate has no profile or method-set condition in it: the only
    # conditions between the value and the append are existence and magnitude.
    guard = src[src.rindex("if _norm_ni is not None", 0, i): i]
    assert "profile" not in guard and "methods" not in guard, (
        "the flag is now profile-aware — update this test")

    # The scale of the false promise, counted off the real profile table.
    total = with_norm = 0
    for _sec, profiles in INDUSTRY_VALUATION_PROFILES.items():
        for _pname, cfg in profiles.items():
            total += 1
            names = [m.get("name", "") for m in cfg.get("methods", [])]
            if any("norm" in n.lower() for n in names):
                with_norm += 1
    # Wave 1 oil, gas & coal (owner-approved 2026-09-20): +5 profiles, all five with a normalised leg.
    # Wave 2 power & transition (owner-confirmed 2026-09-21): +1 profile, with a normalised anchor.
    # Backlog-Gated Long Cycle (2026-09-22): +1 profile, no normalised leg and no trailing P/E.
    # Wave 3 (owner framework 2026-09-22): Aerospace & Defense split into seven profiles: -1 +7 profiles; Defense Primes carries EV/EBITDA (norm) and
    # Commercial Aerospace & Engines carries EV/EBIT (norm).
    assert (total, with_norm) == (116, 38), (total, with_norm)   # +China Internet Platform, +3 Wave 4 (Agribusiness normalised)
    # "Most" means a majority; the earlier 0.30 bound was the census at the
    # time, not the claim (33/104 = 32% after Wave 1).
    assert with_norm / total < 0.50, "most profiles have no normalized leg"


def test_the_normalized_leg_names_are_not_case_consistent():
    """A latent dispatch hazard, surfaced by counting legs for the test above.
    Four profiles anchor on a normalized EBITDA leg and they spell it two ways:
    `EV/EBITDA (norm)` three times and `EV/EBITDA (Norm)` once. Any exact-string
    lookup on the method name silently misses one of them.

    Not fixed here — it is a rename with a golden blast radius of its own, and
    nothing in this change set needed it. Pinned so the inconsistency cannot
    grow, and so a future exact-match dispatch fails here first.
    """
    spellings: dict[str, int] = {}
    for _sec, profiles in INDUSTRY_VALUATION_PROFILES.items():
        for _pname, cfg in profiles.items():
            for m in cfg.get("methods", []):
                n = m.get("name", "")
                if "norm" in n.lower():
                    spellings[n] = spellings.get(n, 0) + 1
    # Wave 1 oil, gas & coal (owner-approved 2026-09-20): +5 EV/EBITDA (norm), +2 P/E (norm) (Refining, OFS).
    assert spellings.get("EV/EBITDA (norm)") == 12, spellings     # +1 Wave 2 hardware OEM, +1 Wave 3 Defense Primes, +1 China Internet Platform, +1 Wave 4 Agribusiness
    assert spellings.get("EV/EBITDA (Norm)") == 1, spellings
    assert spellings.get("P/E (norm)") == 28, spellings            # +1 China Internet Platform, +1 Wave 4 Agribusiness
    assert len(spellings) == 4   # +'EV/EBIT (norm)', Wave 3 (2026-09-22), spellings


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED as a routing rule, though the machinery is now most of the "
    "way there. `normalized_ebit` is computed for every name on every run and "
    "still has zero readers. Two obstacles were named when this was written; "
    "each has since been halved by a shipped fix, and neither is closed: "
    "(1) the deviation must be measured against a MEDIAN while "
    "`_normalized_earnings` averages — the filter is now relative rather than a "
    "fixed 5pp, so that half of the obstacle is gone (see "
    "test_the_normalizers_outlier_floor_is_relative_and_the_absolute_floor_is_gone), "
    "but median-vs-mean is still a real mismatch; (2) routing means swapping a "
    "method branch, and `Luxury Goods` names no normalized method at all. The "
    "Failure-2 swap now supplies exactly that branch swap — `P/E (Premium)` "
    "becomes `P/E (norm)` above a 40% deviation, and on EL's own published "
    "series the deviation is about +1.71, so it fires on the 50%-weighted "
    "anchor. What it does NOT do is route to `normalized_ebit`: `P/E (norm)` "
    "reads `normalized_net_income`, which is the correct input for a P/E and "
    "the reason this test still fails. The brief's Gate 3 asks for the EBIT "
    "figure and nothing reads it. The brief also describes only the "
    "over-valuation direction; the exposure is two-sided. "
    "NEW EVIDENCE THAT THIS IS NOT COSMETIC: the audit flag emitted next to "
    "`normalized_net_income` states in terms that `P/E (norm) will use "
    "normalized figure`, yet on 09988_HK and BABA `methods_used` is "
    "['DCF', 'EV/EBITDA', 'P/E'] — plain TRAILING P/E, no normalized leg. The "
    "flag fires on both (normalized NI now −15% against TTM, past its 15% "
    "audit threshold) and promises a substitution the engine does not perform "
    "at that deviation: 15% clears the flag but not the swap's 40%. Both names "
    "sit in the band between the two thresholds, so the disclosure is still "
    "wrong on them specifically."))
def test_a_trough_margin_routes_the_forward_leg_to_normalized_ebit():
    series = [{"revenue": 15_600.0, "ebit": 15_600.0 * m}
              for m in (0.052, 0.138, 0.152, 0.145, 0.141)]
    fn = getattr(dcf_agent, "_margin_deviation_routes_to_normalized", None)
    assert fn is not None, "no deviation-routing helper exists"
    assert fn(series) is True


# ── C2. Failure 2, shipped — the trailing-P/E to normalized-earnings swap ────
#
# The post-mortem named this "The `P/E (Premium)` Fraud: Documented as differing
# only in label, not earnings source", and prescribed: "replace `P/E` and
# `P/E (Premium)` with `P/E (norm)` whenever `margin_deviation > 0.40`".
#
# Two things in that prescription did not exist and had to be built rather than
# wired up. `margin_deviation` is not a symbol anywhere in src/ — the only
# quantity the engine already computes that matches the description is the
# `_delta_pct` the `Normalized NI` audit flag prints, so the helper below is
# that quantity given a name. And the post-mortem said to reconnect
# `normalized_ebit`; the `P/E (norm)` branch reads `normalized_net_income`
# (dcf_agent.py, `norm_ni = most_recent.get("normalized_net_income")`), and a
# price-to-EARNINGS multiple that divided by EBIT would be wrong by the tax
# rate and the interest line. The net-income figure is used and the discrepancy
# is recorded rather than followed.


def _lux() -> dict:
    """EL's pinned profile: `Luxury Goods`, anchored 50% on trailing P/E."""
    return INDUSTRY_VALUATION_PROFILES["Consumer"]["Luxury Goods"]


def _el_trough() -> dict:
    """EL's own published five-year net-margin series, most recent first.

    The 5.2% year is the current one — that is what makes it the trough-margin
    trap the brief describes, and it is the row `run_dcf_agent` would hold in
    `most_recent`. Returns the row with `normalized_net_income` attached exactly
    the way the engine attaches it: `_normalized_earnings(series, "net_income",
    window=5)` written onto the most-recent row.

    The normalizer excludes the 5.2% year and averages the surviving four to a
    14.4% margin, because |0.052 − 0.141| = 0.089 clears both the dispersion
    term (2 × 0.007 = 0.014) and the relative floor (0.141 × 0.30 = 0.0423).
    That exclusion is what creates the deviation, so this test exercises the
    relative floor and the swap together — which is how they run.
    """
    rev = 15_600.0
    series = [{"revenue": rev, "net_income": rev * m}
              for m in (0.052, 0.138, 0.152, 0.145, 0.141)]
    row = dict(series[0])
    row["normalized_net_income"] = dcf_agent._normalized_earnings(
        series, "net_income", window=5)
    return row


def test_the_pe_norm_swap_promotes_a_trailing_leg_to_normalized_earnings():
    """The shipped behaviour, on the profile the post-mortem named.

    `Luxury Goods` anchors 50% of its blend on `P/E (Premium)`, which the
    engine's own comment says differs from plain `P/E` "only in documentation
    intent, not earnings source". At EL's trough that anchor multiplies a 5.2%
    margin year by a multiple the profile's rationale assumes is "stable and
    high" because of pricing power. The swap replaces the leg name, and because
    `_blend_methods` resolves a row BY NAME — `method_values.get(raw_name)`,
    then the proxy, then the weight from the same row — rewriting the name on a
    copy moves both the value the leg reads and nothing else. No new method
    branch, no new parameter, no change to any profile table.
    """
    mr = _el_trough()
    dev = dcf_agent._pe_normalization_deviation(mr)
    swaps = dcf_agent._pe_norm_leg_swaps(_lux(), dev)

    assert swaps == [{"from": "P/E (Premium)", "to": "P/E (norm)",
                      "weight": 0.5, "anchor": True}], swaps

    eff = dcf_agent._apply_pe_norm_swaps(_lux()["methods"], swaps)
    assert [m["name"] for m in eff] == [
        "P/E (norm)", "EV/EBIT", "DCF (LTG)", "Brand Val"]
    # The weight and the anchor bit travel with the row, so the promoted leg
    # still carries the 50% the profile assigned it. A rename that dropped the
    # anchor would silently re-weight the whole blend.
    assert eff[0]["weight"] == 0.5 and eff[0]["anchor"] is True
    # The two middle rows are untouched. `Brand Val` is excluded from this
    # comparison because it moves too — its proxy is promoted, which is the
    # subject of test_apply_rewrites_the_proxy_even_when_no_leg_of_that_name_exists.
    assert eff[1:3] == _lux()["methods"][1:3]


def test_the_deviation_helper_guards_every_input_that_would_lie():
    """Four ways to get a number out of a ratio that has no meaning.

    `norm <= 0` is the one that matters and it is not defensive padding. A
    normalized LOSS divided by a positive trailing income yields a deviation
    below −1, which clears `abs(dev) > 0.40` and would promote the anchor to a
    `P/E (norm)` leg that immediately returns None — `_blend_methods` skips a
    non-positive value, so the profile would lose its 50% anchor and blend on
    the remaining legs alone. That is a larger change than the swap is
    authorised to make, and it would happen silently, because a dropped leg
    reads as a data gap rather than as a decision. Returning None instead makes
    a trough-to-loss year a no-op.

    `cur <= 0` is the same argument from the other side: dividing by a trailing
    loss gives a sign that depends on which of two negative numbers is larger.
    """
    fn = dcf_agent._pe_normalization_deviation
    assert fn(None) is None
    assert fn("not a dict") is None
    assert fn({}) is None
    assert fn({"net_income": 100.0}) is None
    assert fn({"normalized_net_income": 100.0}) is None
    assert fn({"normalized_net_income": -1.0, "net_income": 100.0}) is None
    assert fn({"normalized_net_income": 0.0, "net_income": 100.0}) is None
    assert fn({"normalized_net_income": 100.0, "net_income": 0.0}) is None
    assert fn({"normalized_net_income": 100.0, "net_income": -50.0}) is None
    # The sign convention: positive means the normalized figure is ABOVE the
    # trailing one, i.e. the current year is the trough. That is EL's case, and
    # it is the direction the post-mortem's trap runs in.
    assert fn({"normalized_net_income": 150.0, "net_income": 100.0}) == pytest.approx(0.50)
    assert fn({"normalized_net_income": 50.0, "net_income": 100.0}) == pytest.approx(-0.50)


def test_the_deviation_is_the_same_quantity_the_audit_flag_prints():
    """Pinned to the flag rather than defined beside it.

    The post-mortem prescribed a threshold on `margin_deviation`, a symbol that
    does not exist in this codebase. The quantity that does exist is `_delta_pct`
    inside the `Normalized NI` audit flag, `(norm - cur) / cur`. If the helper
    and the flag ever diverge — a different denominator, an absolute value, a
    margin instead of an income — then a run would print one deviation and act
    on another, and the printed disclosure would stop describing the valuation.
    So the flag's own expression is asserted to be present in source and the
    helper is asserted to reproduce it on the same inputs.
    """
    src = _engine_src()
    assert "_delta_pct = (_norm_ni - _cur_ni) / _cur_ni" in src

    i = src.index("Normalized NI: TTM")
    assert "abs(_delta_pct) > 0.15" in src[max(0, i - 900): i + 600]

    fn = dcf_agent._pe_normalization_deviation
    for norm, cur in ((2246.4, 811.2), (102489397491.25916, 120964378400.0),
                      (7467226412.859303, 8099000000.0)):
        assert fn({"normalized_net_income": norm, "net_income": cur}) == \
            pytest.approx((norm - cur) / cur, rel=1e-12)

    # And the two thresholds are different numbers with different jobs: 15%
    # discloses, 40% acts. Pinned so the gap cannot be closed by accident in
    # either direction — raising the flag's threshold would hide deviations,
    # lowering the swap's would move valuations on noise.
    assert dcf_agent._PE_NORM_SWAP_DEVIATION == 0.40
    assert dcf_agent._PE_NORM_SWAP_DEVIATION > 0.15


def test_the_swap_threshold_is_strict_so_exactly_forty_percent_does_not_fire():
    """The guard is `<=`, so 0.40 exactly is inside the band and does nothing.

    A boundary that fires at exactly its own threshold is a boundary that moves
    when the number is re-derived at a different float precision. Both sides
    are pinned, and the sign is pinned too: the post-mortem wrote
    `margin_deviation > 0.40`, which reads one-sided, but the trap it describes
    is a trough — a NEGATIVE deviation — and EL's own case is positive only
    because the trough is the current year. A one-sided test would have exempted
    every peak-earnings name, which is the direction the brief's Gate 3 was
    written about. `abs()` covers both and that is deliberate.
    """
    fn = dcf_agent._pe_norm_leg_swaps
    prof = {"methods": [{"name": "P/E", "weight": 0.4, "anchor": True}]}

    assert fn(prof, 0.40) == []
    assert fn(prof, -0.40) == []
    assert fn(prof, 0.4000001) != []
    assert fn(prof, -0.4000001) != []
    # Both directions reach the same leg.
    up = fn(prof, 0.50)[0]
    dn = fn(prof, -0.50)[0]
    assert up["to"] == dn["to"] == "P/E (norm)"
    assert up["from"] == dn["from"] == "P/E"

    # Absent signal, absent action.
    assert fn(prof, None) == []
    assert fn(None, 0.90) == []
    assert fn({}, 0.90) == []
    assert fn({"methods": None}, 0.90) == []


def test_a_profile_that_already_carries_a_normalized_pe_leg_is_skipped():
    """The double-weight guard, which is the one that would be silent.

    A profile with both `P/E` and `P/E (norm)` rows that got its trailing leg
    renamed would end up with two rows named `P/E (norm)`. `_blend_methods`
    resolves each row by name, so both would read the same value and the
    profile's weight on normalized earnings would double — from, say, 0.2 + 0.3
    to 0.5, with nothing in the output to show that a leg had been counted
    twice. The guard skips the whole profile instead.

    Vacuous on today's taxonomy: zero of the 31 swap-eligible profiles also
    name a normalized P/E leg, so the guard has never engaged in production. It
    is kept because it is three lines and the alternative failure is invisible.

    "Already carries" is tested against all three spellings the dispatch branch
    accepts, not just the `P/E (norm)` the swap writes. That widening is also
    vacuous today — `test_the_normalized_leg_names_are_not_case_consistent`
    pins `P/E (norm)` as the only normalized P/E spelling present in any profile
    — but a guard keyed on the output string alone would start double-weighting
    the moment a profile used one of the other two, and would do it silently.
    """
    # The constant must track the dispatch branch. If a fourth spelling is added
    # to the `if method_name in {...}` set and not here, the guard narrows
    # without anything failing.
    src = _engine_src()
    i = src.index('if method_name in {"P/E (norm)"')
    branch = set(re.findall(r'"([^"]+)"', src[i:src.index(":", i)]))
    assert branch == set(dcf_agent._PE_NORM_BRANCH_SPELLINGS), branch
    assert branch == {"P/E (norm)", "P/E norm", "Normalized P/E"}

    fn = dcf_agent._pe_norm_leg_swaps
    both = {"methods": [{"name": "P/E", "weight": 0.3},
                        {"name": "P/E (norm)", "weight": 0.2}]}
    assert fn(both, 0.90) == []
    for spelling in ("P/E norm", "Normalized P/E"):
        assert fn({"methods": [{"name": "P/E", "weight": 0.3},
                               {"name": spelling, "weight": 0.2}]}, 0.90) == []
    # A normalized EBITDA leg does NOT suppress the swap. It is a different
    # earnings basis and it does not collide in the value map.
    assert fn({"methods": [{"name": "P/E", "weight": 0.3},
                           {"name": "EV/EBITDA (norm)", "weight": 0.2}]}, 0.90) != []


def test_the_swap_is_not_gated_on_the_cyclical_profiles():
    """A deliberate divergence from the leg-swap machinery it was modelled on.

    `_mid_cycle_leg_swaps` — the Phase 1.2B machinery this one copies its
    applied-path shape from — returns `[]` unless the profile is in
    `_CYCLICAL_PROFILES`. Reusing that gate here would have been the obvious
    mistake, because the eight cyclicals and the consumer-discretionary archetypes
    have nothing in common: `Luxury Goods` is not in the set, so EL would never
    fire. The exposure this swap addresses is a distorted trailing year, which
    is a property of the SERIES and not of the sector — a beauty company at a
    channel-reset trough and a copper miner at a commodity trough have the same
    problem, and only one of them is cyclical by label.

    Pinned so a future refactor that "unifies the two swap helpers" cannot
    inherit the gate without failing here. Asserted off the compiled code
    object's `co_names` rather than off `inspect.getsource`, because the
    helper's own docstring names `_CYCLICAL_PROFILES` while explaining why it
    is not used — a source-text match cannot tell those apart, and the version
    of this test that tried reported the gate as present on a function that
    provably does not read it.
    """
    prof = {"methods": [{"name": "P/E (Premium)", "weight": 0.5, "anchor": True}]}
    assert "Luxury Goods" not in dcf_agent._CYCLICAL_PROFILES
    assert dcf_agent._pe_norm_leg_swaps(prof, 0.90) != []
    assert "_CYCLICAL_PROFILES" not in dcf_agent._pe_norm_leg_swaps.__code__.co_names
    # The sibling helper DOES read it, so the assertion above is not vacuous.
    assert "_CYCLICAL_PROFILES" in dcf_agent._mid_cycle_leg_swaps.__code__.co_names


def test_the_swap_population_is_thirty_seven_of_ninety_nine():
    """The measured size of the change, off the real profile table.

    RENAMED from `test_the_swap_population_is_thirty_one_of_ninety_nine` and
    INVERTED, because the owner authorised the widening: "Include `P/E (ops)` /
    `P/E (Ops)` in `_PE_NORM_SWAP_LEGS`: Excluding operating P/E while swapping
    standard P/E is an artificial distinction. If a company's consolidated margin
    is cyclically depressed or inflated, operating earnings are equally distorted."

    99 profiles. 37 carry a trailing P/E leg of some spelling. The swap used to
    name two spellings and reached 31 of those 37; it now names four and reaches
    all 37. 10 of the 31 anchored on it and 13 of the 37 do now, so for those
    thirteen the swap changes the earnings source of the single most-weighted leg
    in the profile. None of the 37 also carries a normalized P/E leg, which is what
    makes the double-weight guard below vacuous today.

    Four of the thirteen anchors are Consumer and the widening did not add any:
    `Food & Beverage` (P/E 0.50), `Luxury Goods` (P/E (Premium) 0.50),
    `Household / Personal` (P/E 0.40) and `Membership / Subscription Retail`
    (P/E 0.40). No Consumer profile carries an ops spelling. The three the widening
    added are all healthcare at 0.40 — `Biopharma / Managed Care`,
    `HealthcareServices / Managed Care` and `HealthcareServices / Pharma
    Distribution` — and they are the point of the change: a trough-earnings
    healthcare name now gets the same repair a trough-earnings beauty name already
    got, instead of keeping an unnormalized trailing anchor at 40% of its blend.

    The brief's archetypes are still disproportionately represented in the
    population this change reaches, which is the argument that it was worth
    building rather than documenting.
    """
    tot = trail = elig = anchored = 0
    consumer_anchors = []
    added_anchors = []
    for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
        for pn, cfg in profs.items():
            tot += 1
            ms = [m for m in cfg.get("methods", []) if isinstance(m, dict)]
            names = {m.get("name") for m in ms}
            if names & {"P/E", "P/E (ops)", "P/E (Ops)", "P/E (Premium)"}:
                trail += 1
            if names & dcf_agent._PE_NORM_SWAP_LEGS.keys():
                elig += 1
            for m in ms:
                if m.get("name") in dcf_agent._PE_NORM_SWAP_LEGS and m.get("anchor"):
                    anchored += 1
                    if sec == "Consumer":
                        consumer_anchors.append((pn, m["name"], m["weight"]))
                    if m["name"] in ("P/E (ops)", "P/E (Ops)"):
                        added_anchors.append((sec, pn, m["name"], m["weight"]))
    # Wave 2 (2026-09-21): +1 profile; and Regulated Utility's mislabelled "Utility P/E"
    # (EBITDA x EV/EBITDA) became a real trailing "P/E" carrying the anchor flag, so it
    # joins the swap population and its anchors: a utility in a depressed year is
    # priced on normalised earnings like every other trailing-P/E profile.
    # Backlog-Gated Long Cycle (2026-09-22): +1 profile, no normalised leg and no trailing P/E.
    # Wave 3 (owner framework 2026-09-22): Aerospace & Defense split into seven profiles; none of the new trailing P/E legs is an anchor.
    assert (tot, trail, elig, anchored) == (116, 36, 36, 12)   # Wave 4: F&B and Household anchors moved to Forward P/E (not a swap leg)
    # The swap now names every trailing P/E spelling that exists in the taxonomy,
    # so `elig == trail` is the invariant. If a fifth spelling ever appears, this
    # is the assertion that says the map is stale rather than the census drifting.
    assert elig == trail
    assert sorted(consumer_anchors) == [
        # ("Food & Beverage", "P/E", 0.5) -- Wave 4 (2026-09-26): anchor moved to Forward P/E
        # ("Household / Personal", "P/E", 0.4) -- Wave 4 (2026-09-26): anchor moved to Forward P/E
        ("Luxury Goods", "P/E (Premium)", 0.5),
        ("Membership / Subscription Retail", "P/E", 0.4),
    ], consumer_anchors
    assert sorted(added_anchors) == [
        ("Biopharma", "Managed Care", "P/E (Ops)", 0.4),
        ("HealthcareServices", "Managed Care", "P/E (Ops)", 0.4),
        ("HealthcareServices", "Pharma Distribution", "P/E (Ops)", 0.4),
    ], added_anchors
    assert dcf_agent._PE_NORM_SWAP_LEGS == {
        "P/E": "P/E (norm)", "P/E (Premium)": "P/E (norm)",
        "P/E (ops)": "P/E (norm)", "P/E (Ops)": "P/E (norm)"}
    # Both case spellings, because the taxonomy is not case-consistent and the map
    # keys on exact string match: `P/E (Ops)` appears 4 times and `P/E (ops)` 2.
    assert len({k.lower() for k in dcf_agent._PE_NORM_SWAP_LEGS}) == 3

    # No profile names two swappable spellings, and no profile names any method
    # twice. Both matter MORE now than they did at two keys: the mapping has four
    # keys and one value, so a profile carrying `P/E` and `P/E (Ops)` would produce
    # two rows named `P/E (norm)`, and `_blend_methods` resolves rows by name, so
    # the branch's weight would double with nothing in the output to show it.
    # Widened from a two-name conjunction to a set intersection for that reason.
    keys = set(dcf_agent._PE_NORM_SWAP_LEGS)
    both = dups = 0
    for _sec, profs in INDUSTRY_VALUATION_PROFILES.items():
        for _pn, cfg in profs.items():
            names = [m.get("name") for m in cfg.get("methods", [])
                     if isinstance(m, dict)]
            both += int(len(keys & set(names)) > 1)
            dups += int(len(names) != len(set(names)))
    assert (both, dups) == (0, 0)


def test_the_promotion_is_first_wins_when_two_legs_would_collapse():
    """The collision above is closed structurally, and the policy is a choice.

    With two keys mapping to one target, a hypothetical profile carrying both
    `P/E` (0.30) and `P/E (Premium)` (0.20) has three defensible outcomes and
    none of them is derivable: promote both and double the normalized weight to
    0.50; promote neither and leave the profile reading trailing earnings
    through two labels; or promote one and leave the other trailing, which
    reproduces the exact two-earnings-sources-at-once inconsistency this swap
    exists to remove.

    `emitted` picks the third and takes the first in row order, which is at
    least deterministic and at least does not invent weight. Recorded as a
    choice rather than a fix because no profile in the taxonomy reaches it — the
    assertion above is what makes that a measurement and not an assumption — and
    if one ever does, the correct answer depends on why a profile would carry
    two spellings of the same branch, which nothing today explains.
    """
    prof = {"methods": [
        {"name": "P/E", "weight": 0.30, "anchor": True},
        {"name": "EV/EBITDA", "weight": 0.20},
        {"name": "P/E (Premium)", "weight": 0.20},
    ]}
    swaps = dcf_agent._pe_norm_leg_swaps(prof, 0.90)
    assert swaps == [{"from": "P/E", "to": "P/E (norm)",
                      "weight": 0.30, "anchor": True}], swaps
    eff = dcf_agent._apply_pe_norm_swaps(prof["methods"], swaps)
    assert [m["name"] for m in eff] == ["P/E (norm)", "EV/EBITDA", "P/E (Premium)"]
    # Order determines the winner, so reversing the rows reverses the outcome.
    rev = {"methods": list(reversed(prof["methods"]))}
    assert dcf_agent._pe_norm_leg_swaps(rev, 0.90)[0]["from"] == "P/E (Premium)"


def test_the_ops_spellings_are_now_in_the_swap():
    """RENAMED AND INVERTED. This test used to be
    `test_the_swap_leaves_the_ops_spellings_on_the_trailing_branch`, and it FAILED
    on its own first assertion when Step 7 landed, because its entire premise was
    authorised away by the owner:

        "Include `P/E (ops)` / `P/E (Ops)` in `_PE_NORM_SWAP_LEGS`: Excluding
        operating P/E while swapping standard P/E is an artificial distinction. If
        a company's consolidated margin is cyclically depressed or inflated,
        operating earnings are equally distorted. Add `P/E (ops)` to the swap
        target list."

    The old test asserted `ops & set(_PE_NORM_SWAP_LEGS) == set()` and explained
    the exclusion as scope discipline — "widening an authorised change to cover a
    spelling it did not mention is not the same as completing it". That reasoning
    was correct at the time and is now overruled. What it got RIGHT and what is
    kept below is the census: six profiles dispatch to the identical trailing
    branch under the two ops spellings, three of them anchoring at 0.40, and the
    old test called that omission "an inconsistency between two profiles that read
    the same number, and exactly the kind this swap exists to remove". It was a
    defect witness in the shape of a scope note. The defect is fixed; the census
    stays, because it is the complete list of profiles that read trailing earnings
    through an ops spelling and is what makes the widening's blast radius
    enumerable.

    Also kept: `37 - 31 = 6` was the arithmetic that proved the omission was the
    WHOLE of the gap between the trailing-P/E count and the swappable count. That
    identity is now `37 - 37 = 0`, which is the same proof in its fixed form, and
    it is asserted rather than left as a comment.
    """
    ops = {"P/E (ops)", "P/E (Ops)"}
    assert ops <= set(dcf_agent._PE_NORM_SWAP_LEGS)
    assert set(dcf_agent._PE_NORM_SWAP_LEGS) == ops | {"P/E", "P/E (Premium)"}
    # Every key maps to the one target, so the swap cannot produce two different
    # normalized spellings and `_blend_methods`' name resolution stays unambiguous.
    assert set(dcf_agent._PE_NORM_SWAP_LEGS.values()) == {"P/E (norm)"}

    src = _engine_src()
    # The dispatcher treats all four spellings as the SAME earnings source, which
    # is the economic argument for swapping all four. Pinning it here means a
    # future split of the trailing branch — giving the ops spellings their own
    # operating-earnings source — fails this test rather than silently making the
    # swap promote a leg onto a different number than the one it was normalized
    # against.
    assert 'if method_name in {"P/E", "P/E (ops)", "P/E (Premium)", "P/E (Ops)"}:' in src
    assert 'if method_name in {"P/E (norm)", "P/E norm", "Normalized P/E"}:' in src

    carriers = []
    for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
        for pn, cfg in profs.items():
            for m in cfg.get("methods", []):
                if isinstance(m, dict) and m.get("name") in ops:
                    carriers.append((sec, pn, m["name"], m["weight"],
                                     bool(m.get("anchor"))))
    assert sorted(carriers) == [
        ("Biopharma", "Managed Care", "P/E (Ops)", 0.4, True),
        ("Financials", "Insurance", "P/E (ops)", 0.15, False),
        ("Financials", "Insurance (P&C)", "P/E (ops)", 0.2, False),
        ("HealthcareServices", "Healthcare Providers / Services", "P/E (Ops)", 0.3, False),
        ("HealthcareServices", "Managed Care", "P/E (Ops)", 0.4, True),
        ("HealthcareServices", "Pharma Distribution", "P/E (Ops)", 0.4, True),
    ], carriers
    # The six are the whole of the former gap, and the gap is now closed.
    assert len(carriers) == 37 - 31
    assert all(c[2] in dcf_agent._PE_NORM_SWAP_LEGS for c in carriers)
    # None of the six also carries a normalized leg, so the first-wins dedup is
    # still vacuous after the widening and the three new anchors cannot collide
    # with an existing `P/E (norm)` row.
    offenders = []
    for sec, pn, *_ in carriers:
        names = {m.get("name") for m in
                 INDUSTRY_VALUATION_PROFILES[sec][pn].get("methods", [])
                 if isinstance(m, dict)}
        if names & set(dcf_agent._PE_NORM_BRANCH_SPELLINGS):
            offenders.append((sec, pn, sorted(names)))
    assert not offenders, offenders
    # No cyclical profile carries an ops leg, so `_MID_CYCLE_LEG_SWAPS` — which
    # also targets `P/E (norm)` and is gated on `_CYCLICAL_PROFILES` — cannot race
    # this swap on the same row. The four cyclical profiles with a P/E-family leg
    # all carry plain `P/E`, which both maps already handled, so the widening
    # needed no matching change there.
    for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
        for pn, cfg in profs.items():
            if pn not in dcf_agent._CYCLICAL_PROFILES:
                continue
            names = {m.get("name") for m in cfg.get("methods", [])
                     if isinstance(m, dict)}
            assert not (names & ops), (sec, pn, sorted(names & ops))


def test_apply_rewrites_the_proxy_even_when_no_leg_of_that_name_exists():
    """The bug this test was written to catch, and it was live.

    Exactly two rows in the taxonomy carry a proxy naming a swappable trailing
    spelling, both Consumer, both at weight 0.05: `Food & Beverage`'s
    "Brand Valuation" and `Luxury Goods`' "Brand Val", each `implementable:
    False` with `proxy: "P/E"`. `_blend_methods` resolves a non-implementable
    row through its proxy, so the proxy IS an earnings source with weight on it.

    The first implementation keyed the proxy rewrite on the legs that actually
    swapped: `by_from = {s["from"]: s["to"] for s in swaps}`. For Luxury Goods
    that map is `{"P/E (Premium)": "P/E (norm)"}`, which does not contain
    `"P/E"`, so `Brand Val` came back out with `proxy: "P/E"` beside an anchor
    that had become `P/E (norm)`. The docstring claimed both routes were
    rewritten and they were not, and the profile that broke was EL's — the one
    name in the entire taxonomy the change was written for. Found by running the
    swap on EL's series and reading the output, not by reading the code.

    The rewrite now keys on `_PE_NORM_SWAP_LEGS`, so ANY reference to a trailing
    spelling is promoted once at least one leg has swapped. Still gated on
    `swaps` being non-empty, so a profile whose only trailing exposure is a
    proxy is left alone.
    """
    mr = _el_trough()
    dev = dcf_agent._pe_normalization_deviation(mr)
    swaps = dcf_agent._pe_norm_leg_swaps(_lux(), dev)
    eff = dcf_agent._apply_pe_norm_swaps(_lux()["methods"], swaps)

    brand = [m for m in eff if m["name"] == "Brand Val"][0]
    assert brand["proxy"] == "P/E (norm)", (
        "the proxy route to trailing earnings is open again")
    assert brand["implementable"] is False and brand["weight"] == 0.05
    # After the swap the profile reaches trailing P/E by no route at all, which
    # is the property that was wanted and could not be asserted while the proxy
    # still pointed at `P/E`.
    trail = {"P/E", "P/E (ops)", "P/E (Ops)", "P/E (Premium)"}
    assert not {m.get("name") for m in eff} & trail
    assert not {m.get("proxy") for m in eff} & trail

    # Food & Beverage has a real `P/E` leg, so its proxy was already promoted by
    # the buggy version too. Asserted to show the fix did not change the case
    # that worked.
    fb = INDUSTRY_VALUATION_PROFILES["Consumer"]["Food & Beverage"]
    fb_eff = dcf_agent._apply_pe_norm_swaps(
        fb["methods"], dcf_agent._pe_norm_leg_swaps(fb, dev))
    fb_rows = {m["name"]: m.get("proxy") for m in fb_eff}
    # Wave 4 (2026-09-26): Food & Beverage anchors on Forward P/E, which the
    # swap does not touch, and the Brand Valuation proxy leg is gone.
    assert "P/E (norm)" not in fb_rows and "Brand Valuation" not in fb_rows
    assert set(fb_rows) == {"Forward P/E", "EV/EBITDA", "DCF", "FCF Yield"}

    # Only two such rows exist, so the widened rewrite has no reach beyond these
    # two Consumer profiles. A third appearing is a change worth a test.
    found = []
    for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
        for pn, cfg in profs.items():
            for m in cfg.get("methods", []):
                if (isinstance(m, dict)
                        and m.get("proxy") in dcf_agent._PE_NORM_SWAP_LEGS):
                    found.append((pn, m["name"], m["proxy"], m["weight"]))
    assert sorted(found) == [
        # ("Food & Beverage", "Brand Valuation", "P/E", 0.05) -- Wave 4: the proxy leg is gone
        ("Luxury Goods", "Brand Val", "P/E", 0.05),
    ], found


def test_apply_is_copy_on_write_a_no_op_and_idempotent():
    """Three properties the call sites depend on and none of which is visible
    in the output.

    Copy-on-write: the rows are the very dict objects held in
    `INDUSTRY_VALUATION_PROFILES`. Mutating one rewrites the profile for every
    subsequent ticker in the same process, so the second luxury name analysed
    would inherit the first one's swap. This is the failure mode the SOTP
    overlay's copy-on-write discipline exists to prevent, and a long-running
    worker is exactly where it would show up.

    No-op identity: an empty swap list returns the SAME object, so the common
    case allocates nothing and an `is` comparison downstream stays meaningful.

    Idempotence: a second pass over already-promoted rows changes nothing,
    because `P/E (norm)` is not a key in the mapping. Without it, a retry or a
    re-entrant scenario loop would be a different valuation from the first.
    """
    rows = _lux()["methods"]
    before = [dict(m) for m in rows]

    assert dcf_agent._apply_pe_norm_swaps(rows, []) is rows

    swaps = dcf_agent._pe_norm_leg_swaps(
        _lux(), dcf_agent._pe_normalization_deviation(_el_trough()))
    eff = dcf_agent._apply_pe_norm_swaps(rows, swaps)
    assert eff is not rows
    assert all(eff[i] is not rows[i] for i in range(len(rows)))
    assert [dict(m) for m in rows] == before, "the profile table was mutated"
    assert _lux()["methods"][0]["name"] == "P/E (Premium)"
    assert _lux()["methods"][3]["proxy"] == "P/E"

    again = dcf_agent._apply_pe_norm_swaps(eff, swaps)
    assert again == eff


def test_els_own_series_clears_the_threshold_by_more_than_four_times():
    """The swap fires on the name it was written for, on EL's own numbers.

    Deviation +176.9%: a 5.2% trailing margin year against a 14.4% normalized
    one. The threshold is 40%, so this is not a marginal case and it does not
    depend on where inside the band the trigger sits. EL is pinned to
    `Luxury Goods` in `TICKER_SECTOR_LOOKUP`, and the leg promoted is the
    50%-weighted anchor — the largest single move this change can make.

    What this does NOT establish: EL is not a golden fixture, so no recorded
    baseline exercises the swap, and the numbers here come from the brief's
    five-margin series rather than from a captured EL run. The end-to-end path
    is unit-tested; the production path is not yet observed.
    """
    mr = _el_trough()
    dev = dcf_agent._pe_normalization_deviation(mr)

    assert mr["net_income"] == pytest.approx(811.2, abs=1e-6)
    assert mr["normalized_net_income"] == pytest.approx(2246.4, abs=1e-6)
    assert dev == pytest.approx(1.7692, abs=1e-4)
    assert dev > 4 * dcf_agent._PE_NORM_SWAP_DEVIATION

    assert dcf_agent._pe_norm_leg_swaps(_lux(), dev) != []
    assert TICKER_SECTOR_LOOKUP["EL"][1] == "Luxury Goods"


#: Replay measurements, one process per fixture, off the engine's own
#: `most_recent` dict — the same two numbers the `Normalized NI` flag prints.
#: NOT recomputed live: `tests/test_golden_valuations.py` pins
#: `normalized_net_income` in the snapshot projection, so these can only move if
#: the normalizer moves, and that would fail there first.
_GOLDEN_DEVIATIONS = {
    "02888_HK": ("Money Center Bank",               -0.1012),
    "09988_HK": ("Hyperscaler / Tech Conglomerate", -0.1527),
    "AAPL":     ("Hyperscaler / Tech Conglomerate", -0.0534),
    "BABA":     ("Hyperscaler / Tech Conglomerate", -0.1527),
    "BN4_SI":   ("Conglomerate / Industrial (SG)",  +0.0867),
    "C38U_SI":  ("S-REIT",                          -0.0333),
    "COST":     ("Membership / Subscription Retail", -0.0780),
    "D05_SI":   ("Money Center Bank (SG)",          +0.1659),
    "FCX":      ("Mining (Major)",                  +0.3579),
    "MELI":     ("Hyper-Growth Platform",           -0.0193),
    "MU":       ("Memory / DRAM-NAND",              -0.1755),
    "SCHW":     ("Brokerage",                       -0.1389),
    "U96_SI":   ("Conglomerate / Industrial (SG)",  -0.2776),
    "V":        ("Payment Networks",                +0.0374),
}


def test_no_golden_fixture_can_reach_the_swap_and_four_could_if_they_moved():
    """The named move set for this change is EMPTY, and that was measured.

    The verification contract says any move outside the named set is investigated
    before the baseline is regenerated. For this item the set is empty, so the
    claim to defend is that nothing moves. Two independent reasons, both pinned:

      * the largest deviation anywhere in the fourteen is FCX at +0.3579, which
        does not clear 0.40 — and `Mining (Major)` has no trailing P/E leg, so
        FCX could not swap even at +4.0;
      * of the four fixtures whose profile IS swap-eligible, the largest is
        09988_HK and BABA at −0.1527, less than two fifths of the threshold.

    The second number is the one that carries the argument, and it is worth being
    explicit about why the first does not. A headroom figure computed across all
    fourteen is dominated by fixtures that could never fire, so it overstates the
    safety margin: FCX sitting at 0.3579 tells you nothing about whether COST's
    anchor can move. Restricted to the four that can, the margin is 0.1527
    against 0.40 — real headroom, but 2.6x rather than 1.1x, and a future change
    to the normalizer could close it.

    Four eligible, not none: `Hyperscaler / Tech Conglomerate` (09988_HK, AAPL,
    BABA) and `Membership / Subscription Retail` (COST) both anchor or weight a
    plain `P/E` leg. COST's is the anchor at 0.40. So the baseline contains live
    exposure to this swap and does not fire only because the deviations are
    small — which is the difference between "no blast radius" and "nothing to
    blast". Pinned so that a threshold lowered below 0.1527 is a deliberate act
    that fails here first, with the four names already identified.
    """
    thr = dcf_agent._PE_NORM_SWAP_DEVIATION
    eligible, ineligible_reasons = [], {"no trailing P/E leg": 0,
                                        "already normalized": 0}
    for fx, (pname, dev) in _GOLDEN_DEVIATIONS.items():
        cfg = None
        for _sec, profs in INDUSTRY_VALUATION_PROFILES.items():
            cfg = profs.get(pname)
            if cfg is not None:
                break
        assert cfg is not None, pname
        swaps = dcf_agent._pe_norm_leg_swaps(cfg, dev)
        assert swaps == [], f"{fx} on {pname} would swap at dev={dev:+.4f}"
        names = {m.get("name") for m in cfg.get("methods", [])}
        if names & dcf_agent._PE_NORM_SWAP_LEGS.keys():
            eligible.append((fx, abs(dev)))
        elif names & {"P/E (norm)", "P/E norm", "Normalized P/E"}:
            ineligible_reasons["already normalized"] += 1
        else:
            ineligible_reasons["no trailing P/E leg"] += 1

    assert len(_GOLDEN_DEVIATIONS) == 14
    assert sorted(f for f, _ in eligible) == ["09988_HK", "AAPL", "BABA", "COST"]
    assert max(d for _, d in eligible) == pytest.approx(0.1527, abs=1e-4)
    assert max(d for _, d in eligible) < thr
    assert max(abs(d) for _, (_, d) in _GOLDEN_DEVIATIONS.items()) == \
        pytest.approx(0.3579, abs=1e-4)
    assert ineligible_reasons == {"no trailing P/E leg": 5, "already normalized": 5}
    assert len(eligible) + sum(ineligible_reasons.values()) == 14


def test_cost_is_a_live_fixture_whose_anchor_is_in_the_swap_population():
    """The one golden fixture where this swap could move the valuation, and the
    distance it sits from doing so.

    COST is `Membership / Subscription Retail`, whose anchor is a plain `P/E` at
    weight 0.40 — the single most-weighted leg in the profile, on trailing
    earnings. Its measured deviation is −0.0780, so it does not fire, and base
    IV 1324.57 is unchanged by this commit.

    Stated because it is the sharpest version of the caveat above: the empty
    move set is a property of today's numbers and not of the mechanism. COST
    would need its normalized net income to fall to 60% of trailing — a
    deviation of −0.40 — for its anchor to be promoted, and its
    `fcf_margin_base` of 0.0232 means the Consumer floor already binds on the
    DCF leg beside it. A test that asserted "no fixture is exposed" would be
    green and wrong.
    """
    cfg = INDUSTRY_VALUATION_PROFILES["Consumer"]["Membership / Subscription Retail"]
    ms = [m for m in cfg["methods"] if isinstance(m, dict)]
    anchor = [m for m in ms if m.get("anchor")]
    assert len(anchor) == 1 and anchor[0]["name"] == "P/E"
    assert anchor[0]["weight"] == 0.40

    dev = _GOLDEN_DEVIATIONS["COST"][1]
    assert dev == pytest.approx(-0.0780, abs=1e-4)
    assert dcf_agent._pe_norm_leg_swaps(cfg, dev) == []
    assert dcf_agent._pe_norm_leg_swaps(cfg, -0.41)[0]["anchor"] is True
    # 0.40 / 0.0780 — how many times larger COST's deviation would have to be.
    assert dcf_agent._PE_NORM_SWAP_DEVIATION / abs(dev) == pytest.approx(5.13, abs=0.01)


def _run_body() -> str:
    """`run_dcf_agent`'s source, from its `def` to the next top-level `def`/EOF.

    Scoped to the function because what is being pinned here is control flow —
    where a declaration sits relative to the scenario loop — and the file is
    11,649 lines of other functions that reuse these identifiers in prose.

    `run_dcf_agent` is the LAST top-level `def` in the module (measured: the only
    one after line 7000, at 7065), so the slice normally runs to EOF and the
    fallback is the common path, not the exception. Two traps, both hit while
    writing this:

      * `body.index("\\ndef ", 10)` finds the first NESTED `def` a few hundred
        characters in, so every later assertion silently examines a stub. The
        regex anchors on column 0 instead.
      * `re.M` plus `^def ` matches nothing here, so the slice must tolerate a
        `None` match rather than assert one.
    """
    src = _engine_src()
    start = src.index("def run_dcf_agent(")
    m = re.search(r"^def ", src[start + 10:], re.M)
    return src[start:start + 10 + m.start()] if m else src[start:]


def test_every_blend_that_values_the_profile_reads_the_promoted_rows():
    """The wiring, pinned at every site that turns method rows into a number.

    Two distinct failures live here, and the second is the one that was actually
    found:

      * a swap applied at the build site and not at the blend computes
        `P/E (norm)` and then looks up `P/E` in the value map, finds nothing, and
        drops the leg — the silent-weight-loss failure the copy-on-write comment
        warns about;
      * a swap applied at the blend and not at the DISCLOSURE produces the right
        number and the wrong attribution. Forced on COST: the blend received
        `P/E (norm)` at weight 0.40 with a live value of 860.99 and used it,
        while `methods_used` came back `['DCF', 'EV/EBITDAR', 'FCF Yield']` — a
        report naming three legs for a valuation computed from four, the missing
        one being the anchor. Base IV moved 1324.57 → 1272.79 with nothing in
        the payload explaining why.

    So six consumers are asserted, not one. Three turn rows into a value and read
    the in-loop alias `_eff_profile_methods`:

      * the `methods_to_compute` build, which decides what gets valued at all;
      * the Phase 1.2B path-B builder, so the recorded A/B comparison measures
        one change rather than two — path B inheriting the raw rows while path A
        used the promoted ones would attribute the swap's effect to the
        mid-cycle leg swap instead;
      * the primary `_blend_methods` call.

    Three name rows in the payload. `methods_used` reads the alias; the other two
    sit OUTSIDE the scenario loop and so read `_pe_norm_methods` directly:

      * `_methods_unavailable`, which would otherwise have reported `P/E` as an
        unavailable degradation on a leg that was valued, and would not have
        reported `P/E (norm)` as used — the same wrong attribution twice over;
      * `bank_breakdown`'s `primary_anchor`.

    Two of those three sit outside the loop, which is what forced the whole
    promotion above it. That is asserted as structure and not left to the reader:
    the deviation helper is called EXACTLY ONCE in 271k characters of function
    body, so "three identical lists and three separate chances for the flag
    prose, the value build and the blend to disagree" is closed by construction
    rather than by a test that happens to pass today.

    `_run_backward_gate` is the seventh consumer and deliberately still reads
    `profile_data["methods"]`. Its T-1 row is `series[-2]`, a raw annual row
    carrying no `normalized_net_income` — confirmed empirically, not assumed: on
    the forced-COST run its `most_recent` had `normalized_net_income = None` and
    `net_income = 7367000000.0`, so the deviation cannot be computed there and a
    promoted leg would resolve to None. For the 31-profile population that makes
    a T-1 comparison not like-for-like with production. Left alone because the
    backward scorer is already blocked on Phase 3 and fires on nothing, and
    stacking an unverifiable change onto an unmeasurable one is how a scorer
    stops meaning anything. Documented in source at the site; asserted here so
    the divergence cannot be closed by accident either.
    """
    body = _run_body()

    # ── (1) computed once, above the loop ─────────────────────────────────
    assert body.count("_pe_normalization_deviation(") == 1
    assert body.count("_pe_norm_leg_swaps(") == 1
    assert body.count("_apply_pe_norm_swaps(") == 1
    assert body.count('for scenario in ("base", "bear", "bull"):') == 1

    # On comment-stripped lines, and that is not stylistic. The `_cyc_rec`
    # comment block inside the loop quotes `if profile_data:` in prose to
    # explain its own placement, so a raw `rindex` over the source finds the
    # MENTION and reports a declaration as nested inside a guard it is
    # deliberately outside. The first version of this assertion did exactly that
    # and failed against correct code.
    code = [ln.split("#", 1)[0].rstrip() for ln in body.splitlines()]

    def _at(text: str) -> int:
        hits = [n for n, ln in enumerate(code) if ln.strip() == text]
        assert len(hits) == 1, (text, [n + 1 for n in hits])
        return hits[0]

    def _ind(n: int) -> int:
        return len(code[n]) - len(code[n].lstrip())

    struct = _at("_structurally_unavailable: set = set()")
    pnm = _at('_pe_norm_methods: list = '
              '(profile_data or {}).get("methods") or []')
    dev = _at("_pe_norm_dev = _pe_normalization_deviation(most_recent)")
    excl = _at('_pe_norm_excluded = set('
               '(profile_data or {}).get("excluded") or [])')
    loop = _at('for scenario in ("base", "bear", "bull"):')
    cyc = _at("_cyc_rec: Optional[dict] = None")
    alias = _at("_eff_profile_methods: list = _pe_norm_methods")

    # The whole promotion — rows, flag, deviation, swaps, exclusion filter —
    # sits between the loop-external availability set and the loop itself.
    assert struct < pnm < dev < excl < loop < cyc < alias, (
        struct, pnm, dev, excl, loop, cyc, alias)

    # Same indent as `_structurally_unavailable` and as the loop header, which
    # is what makes "above the loop" true rather than merely unrefuted by the
    # ordering above.
    assert _ind(struct) == _ind(pnm) == _ind(dev) == _ind(excl) == _ind(loop)

    # The in-loop alias is exactly one level inside the loop — beside `_cyc_rec`,
    # which is the precedent for binding unconditionally so the path-B blend can
    # read it without a profile guard.
    assert _ind(cyc) == _ind(alias) == _ind(loop) + 4
    guard_after = min(n for n, ln in enumerate(code)
                      if ln.strip() == "if profile_data:" and n > alias)
    assert _ind(guard_after) == _ind(alias), (
        "the alias is nested inside `if profile_data:` — a ticker with no "
        "resolved profile would reach the blend with it unbound")

    # ── (2) the three sites that turn rows into a value ───────────────────
    # Two `for m in _eff_profile_methods:` loops — the `methods_to_compute`
    # build and the path-B builder.
    assert body.count("for m in _eff_profile_methods:") == 2
    assert "profile_methods=_eff_profile_methods," in body

    # ── (3) the three sites that name rows in the payload ─────────────────
    assert 'methods_used = [m["name"] for m in _eff_profile_methods' in body
    assert '_m["name"] for _m in _pe_norm_methods' in body          # unavailable
    assert '(m["name"] for m in _pe_norm_methods' in body           # primary_anchor

    # ── (4) the raw rows survive at exactly two live sites, both by need ──
    # The proxy clause of the flag reads the PROFILE's rows because
    # `_pe_norm_methods` has already been rewritten, so its proxies no longer
    # match the mapping's keys and the clause would come out empty. Pinned by
    # test_the_flag_discloses_the_proxy_promotion_as_well_as_the_leg.
    assert body.count('for m in ((profile_data or {}).get("methods") or [])') == 1
    # `_total_weight`, the denominator of the Phase 1.2B swap-share diagnostic.
    # A rename is weight-preserving and row-count-preserving — `_apply_pe_norm_
    # swaps` copies each row and touches only `name` and `proxy` — so any SUM
    # over the rows is invariant under it and reading the raw list is correct.
    # It stops being correct the moment a swap adds or removes a row.
    assert body.count('for m in (profile_data.get("methods") or [])') == 1
    assert 'for m in profile_data["methods"]' not in body
    # The remaining two `profile_data.get("methods")` mentions are existence
    # guards, not row iterations.
    assert body.count('if profile_data and profile_data.get("methods"):') == 2

    # ── (5) the exclusion filter, which a rename would otherwise bypass ───
    assert 'if s["to"] not in _pe_norm_excluded' in body

    # ── (6) the backward gate still diverges, deliberately ────────────────
    bw = _engine_src()[_engine_src().index("def _run_backward_gate("):]
    bw = bw[:bw.index("\ndef ", 10)]
    assert "_eff_profile_methods" not in bw and "_pe_norm_methods" not in bw, (
        "the backward gate now inherits the promotion — its T-1 row carries no "
        "normalized_net_income, so a promoted leg would resolve to None")
    assert 'profile_methods=profile_data["methods"]' in bw


def test_the_flag_discloses_the_proxy_promotion_as_well_as_the_leg():
    """A weight-bearing change that the disclosure did not mention.

    The flag lists the legs it swaps. Promoting `Brand Val`'s proxy moves 5% of
    the blend onto a different earnings source without moving any leg name, so
    the first version of the prose described a change that was smaller than the
    change made. On Luxury Goods it read `P/E (Premium)→P/E (norm) w=0.50
    (anchor)` while 0.05 of trailing P/E kept running unannounced.

    The prose is assembled inline at the call site rather than returned from the
    helper, so this test reproduces the comprehension and pins both halves of the
    string. It reads the PROFILE's rows for the proxy clause and not
    `_eff_profile_methods`: that list has already been rewritten, so its proxies
    no longer match the mapping's keys and the clause comes out empty. Getting
    that backwards produces a flag that silently omits the proxy, which is the
    defect being guarded against and the reason it is asserted here.
    """
    mr = _el_trough()
    dev = dcf_agent._pe_normalization_deviation(mr)
    prof = _lux()
    swaps = dcf_agent._pe_norm_leg_swaps(prof, dev)

    legs = ", ".join(
        f"{s['from']}→{s['to']} w={s['weight']:.2f}"
        + (" (anchor)" if s["anchor"] else "") for s in swaps)
    proxy = ", ".join(
        f"{m.get('name')} proxy {m.get('proxy')}→"
        f"{dcf_agent._PE_NORM_SWAP_LEGS[m.get('proxy')]} "
        f"w={float(m.get('weight') or 0.0):.2f}"
        for m in prof["methods"]
        if isinstance(m, dict)
        and m.get("proxy") in dcf_agent._PE_NORM_SWAP_LEGS
        and not m.get("implementable", True))

    assert legs == "P/E (Premium)→P/E (norm) w=0.50 (anchor)"
    assert proxy == "Brand Val proxy P/E→P/E (norm) w=0.05"

    # And the same comprehension over the POST-swap list is empty, which is the
    # trap. Asserted so the ordering cannot be swapped without failing.
    eff = dcf_agent._apply_pe_norm_swaps(prof["methods"], swaps)
    assert [m.get("name") for m in eff
            if m.get("proxy") in dcf_agent._PE_NORM_SWAP_LEGS] == []

    src = _engine_src()
    assert 'f"{s[\'from\']}→{s[\'to\']} w={s[\'weight\']:.2f}"' in src
    assert "proxy {m.get('proxy')}→" in src
    assert "P/E normalization: trailing net income deviates" in src
    assert "The trailing leg and `P/E (norm)` read DIFFERENT earnings" in src


# ── D. 2020.HK — associate equity and FX parity ──────────────────────────────


def _sotp_body() -> str:
    """The body of `_sotp_analyst_style`, from its `def` to the NAV line.

    Scoped to the function rather than to a character budget behind the add-back,
    because a fixed budget wide enough to reach the `def` (4065 chars, measured)
    also reaches into the previous function and starts matching strings that
    have nothing to do with associates.
    """
    src = _engine_src()
    start = src.index("def _sotp_analyst_style(")
    end = src.index("nav = total_seg_value + associates + net_cash")
    assert start < end
    return src[start:end]


def test_associates_are_added_back_unconditionally_and_only_inside_the_segment_path():
    """The add-back exists, has no materiality test, and is no longer losable.

    UPDATED 2026-09-18, and the update is the point rather than collateral. This
    test used to pin the add-back as three inline lines inside
    `_sotp_analyst_style` and its own docstring recorded the consequence:

        "It is also unreachable for a name with no segments — the function
        returns None when `rows` is empty, and rows come from
        `assumptions["segments"]`. Anta qualifies (Amer Sports is a segment
        story), but a single-brand apparel name with a minority JV would not,
        and would silently drop the stake."

    That was the bug the owner then ordered fixed unconditionally (Step 6,
    Route (a)). The three lines are now `_sotp_nonoperating_addback`, called
    ABOVE the `if not rows:` guard, so the zero-row path returns a degraded
    table carrying the add-back instead of returning None and taking the value
    with it. The assertion below moved with it: the pinned literal is now the
    helper's own line, and a new assertion pins the ordering that makes the fix
    real -- the add-back call must appear BEFORE the guard, because the whole
    defect was an ordering.

    What is still true and still pinned: there is no comparison against total
    equity, so the add-back remains all-or-nothing on whether the extractor
    returned a number rather than on whether the stake is material. The brief's
    Gate 4 is still half-implemented; the half that was missing was reachability
    on the degraded path, and that half is now closed.
    """
    src = _engine_src()
    assert 'associates = _safe(a.get("associates_investments")) or 0.0' in src
    assert "nav = total_seg_value + associates + net_cash" in src

    # THE ORDERING IS THE FIX. Hoisting the computation above the guard is what
    # makes the add-back unlosable; a future refactor that moves the call back
    # below the guard reintroduces the original bug while leaving every other
    # assertion in this file green.
    _addback_call = src.index("associates, net_cash, _addback_basis = "
                              "_sotp_nonoperating_addback(")
    _sotp_start = src.index("def _sotp_analyst_style(")
    assert _sotp_start < _addback_call, "the add-back call left the table builder"
    _body_after = src[_addback_call:]
    # 2026-09-24: the guard counts PRICED rows; a Degraded row (owner item 1)
    # is kept for the report and does not count as operating value.
    _guard = _body_after.index("if not priced_rows:")
    assert _guard > 0, "the zero-row guard vanished"
    # And the guard no longer returns None unconditionally.
    _guard_block = _body_after[_guard:_guard + 4000]
    assert "degraded_no_segments" in _guard_block
    assert '"per_share":           None' in _guard_block

    body = _sotp_body()
    assert "if not priced_rows:" in body
    # The value arrives from the LLM extractor, FX-converted to USD there.
    import src.agents.analysis.sotp_extractor as sx
    sxs = inspect.getsource(sx)
    assert 'skeleton["associates"] = float(assoc) * _ccy_fx' in sxs
    assert 'assoc = getattr(row, "equity_method_investments", None) \\\n            or getattr(row, "long_term_investments", None)' in sxs
    assert '_ccy_fx = get_fx_rate(_ccy, "USD", api_key) if _ccy != "USD" else 1.0' in sxs


def test_there_is_no_associates_to_equity_materiality_gate():
    """The brief's 10%-of-equity trigger has no counterpart anywhere.

    Measured by absence across the whole of `_sotp_analyst_style` up to the
    add-back: nothing reads `shareholders_equity` or `total_equity`, and there is
    no ratio. The consequence is symmetric and worth stating both ways — an
    immaterial 0.1% stake in a holdco template is added at full book value just
    as a 20% one is, so the gate's absence over-values thin stakes rather than
    only under-valuing thick ones.
    """
    body = _sotp_body()
    for absent in ("shareholders_equity", "total_equity", "0.10",
                   "associate_ratio", "materiality"):
        assert absent not in body, f"{absent!r} appears in _sotp_analyst_style now"


def test_the_hk_reporting_currency_table_covers_a_quarter_of_the_names_it_serves():
    """A coverage gap that turned out NOT to be a defect — recorded so it is not
    re-investigated.

    `src/tools/hk/currency.py` maps 5-digit codes to a reporting currency over
    40 entries; the sector lookup carries 162 `.HK` names, so 121 fall through
    to `_DEFAULT_CURRENCY`. Anta (`02020`) is one of the 121.

    The default is **CNY**, which is right for Anta and for most HKEX-listed
    mainland issuers, so the fall-through is benign for this brief's archetype:
    `statement_to_hkd(100.0, "02020")` and `statement_to_hkd(100.0, "00700")`
    both return 116.89. The interesting counter-example is `02888.HK`, Standard
    Chartered, which reports in USD, is also absent from the table, and is a
    golden fixture — so if the label were operative its statements would carry a
    CNY→HKD factor where they need USD→HKD. They do not: the fixture records
    `source_currency: "USD"`, `reported_currency: "HKD"` and
    `fx_rate: 7.84469`. The currency in the payload comes from the feed, not
    from this table, which is why a 75% coverage gap has no measured valuation
    consequence on the one fixture that could have exposed it.

    A HKD-reporting name among the 121 would still be mislabelled, and that is
    a real (unmeasured) exposure — but it is not this brief's, and it is not the
    RMB-filing-to-HKD-listing parity the brief asks for, which already works.
    """
    import json
    from src.tools.hk.currency import (
        _DEFAULT_CURRENCY, _REPORTING_CURRENCY, get_reporting_currency,
        statement_to_hkd,
    )

    assert _DEFAULT_CURRENCY == "CNY"
    assert len(_REPORTING_CURRENCY) == 40
    assert get_reporting_currency("02020") == "CNY"
    assert "02020" not in _REPORTING_CURRENCY
    assert "00700" in _REPORTING_CURRENCY and _REPORTING_CURRENCY["00700"] == "CNY"

    hk = [k for k in TICKER_SECTOR_LOOKUP if k.endswith(".HK")]
    missing = [k for k in hk
               if k.split(".")[0].zfill(5) not in _REPORTING_CURRENCY]
    # +1 / +1: 00006.HK (Power Assets, HKD reporter) pinned in Wave 2.
    # +3 / +3: 02357.HK, 02507.HK, 00232.HK pinned in Wave 3 (all HKD reporters).
    assert len(hk) == 166 and len(missing) == 125, (len(hk), len(missing))
    assert "02020.HK" in missing and "02888.HK" in missing

    assert statement_to_hkd(100.0, "02020") == statement_to_hkd(100.0, "00700")
    assert statement_to_hkd(100.0, "02020") == pytest.approx(116.89, abs=0.6)

    snap = json.load(open("tests/golden/snapshots.json", encoding="utf-8"))
    proj = snap["02888_HK"]["projection"]
    assert proj["source_currency"] == "USD"
    assert proj["reported_currency"] == "HKD"
    assert proj["fx_rate"] == pytest.approx(7.84469, abs=1e-5)


def test_the_four_archetypes_are_pinned_and_every_pin_resolves():
    """Guard on the four anchor pins, and on the silent failure mode they have.

    BEFORE the pins this test asserted the opposite — that NKE, ONON and Anta
    were the *unpinned* Consumer rows and that EL was absent from the table
    entirely. That was the finding: of 83 Consumer rows, 39 filled the profile
    override and 44 left it empty, the brief's four archetypes were in the empty
    set, and their two closest listed competitors (LULU, BIRK) were pinned. So
    the anchor names were routed by whatever their latest FCF margin and revenue
    CAGR happened to be, through the band-pass measured in what is now
    `test_the_consumer_ladder_no_longer_demotes_a_better_margin`. Anta's note
    even recorded the intended answer — "P/E ~25x near US level" — in a field
    that is documentation, not routing.

    They are pinned now, and the counts moved 83->84 rows, 39->43 filled,
    44->41 empty. Three of the four pins CHANGE routing rather than freeze it:

        NKE      Apparel / Athletic Wear  (same as it classifies at ~10% FCF;
                                           past 18% the ladder now returns
                                           Luxury Goods, so the pin holds NKE
                                           BELOW where the ladder would put it —
                                           see the correction in
                                           `test_the_pins_override_a_classification_that_would_otherwise_move`)
        ONON     Apparel -> Consumer Growth
        EL       trough Household / Personal -> Luxury Goods   (was absent)
        02020.HK Luxury Goods -> Apparel / Athletic Wear

    The `ONON` line above records the pin against the ladder as it was BEFORE the
    Step 7 monotonic migration. Unpinned, ONON's shape (50% CAGR, 12% FCF) used to
    resolve to Traditional Retail — the sector's only profile with no DCF leg — and
    the pin was what rescued it. That fall-through is now Apparel / Athletic Wear,
    so the pin still overrides but is no longer protecting against the weakest
    methodology in the sector. The pin is retained regardless: it is owner-directed
    and a pin is a stronger guarantee than a rung.

    THE FAILURE MODE THIS GUARD EXISTS FOR. An override that does not resolve is
    not an error. The D3 guard in `dcf_agent` logs a warning, sets
    `_profile_fallback_used`, records `override_unresolved` in the routing trace
    and KEEPS the classified profile — so a misspelled pin degrades silently to
    exactly the unpinned behaviour the pin was added to prevent, and no
    valuation-level test would notice. Asserting the name resolves in
    `INDUSTRY_VALUATION_PROFILES[sector]` is what makes the pin load-bearing.
    The brief's own suggested name for EL, "Prestige Beauty & Personal Care",
    does not exist in any sector and would have failed this way.
    """
    lk = TICKER_SECTOR_LOOKUP
    pins = {
        "NKE":      ("Consumer", "Apparel / Athletic Wear"),
        "ONON":     ("Consumer", "Consumer Growth"),
        "EL":       ("Consumer", "Luxury Goods"),
        "02020.HK": ("Consumer", "Apparel / Athletic Wear"),
    }
    for ticker, (sector, profile) in pins.items():
        assert ticker in lk, f"{ticker} lost its row entirely"
        row = lk[ticker]
        assert (row[0], row[1]) == (sector, profile), f"{ticker}: {row[:2]}"
        # The silent-failure guard. Not redundant with the assertion above: a
        # rename or removal inside INDUSTRY_VALUATION_PROFILES would leave the
        # lookup string intact and route the ticker back onto the band-pass.
        assert profile in INDUSTRY_VALUATION_PROFILES.get(sector, {}), (
            f"{ticker} is pinned to {profile!r}, which does not resolve under "
            f"sector {sector!r} — the D3 guard would silently keep the "
            f"classified profile instead")

    # Nothing else in the table may be left pointing at a profile that no longer
    # exists. Cheap to check, and it is the same silent failure at larger scale.
    unresolved = sorted(t for t, v in lk.items()
                        if v[1] and v[1] not in INDUSTRY_VALUATION_PROFILES.get(v[0], {}))
    assert unresolved == [], unresolved

    # The two pre-existing pins the finding was measured against.
    assert lk["LULU"][1] == "Apparel / Athletic Wear"
    assert lk["BIRK"][1] == "Apparel / Athletic Wear"

    consumer = [v for v in lk.values() if v[0] == "Consumer"]
    assert len(consumer) == 84     # JD -> Tech (2026-09-26); COST pinned Membership / Subscription Retail (Wave 4)
    assert sum(1 for v in consumer if v[1]) == 44   # +COST pin (Wave 4)
    assert sum(1 for v in consumer if not v[1]) == 40   # JD -> Tech / China Internet Platform (owner, 2026-09-26)


def test_the_pins_override_a_classification_that_would_otherwise_move():
    """What each pin is actually holding back, measured off the live classifier.

    A pin that agrees with the classifier is documentation; a pin that disagrees
    is a policy override. Naming which is which is what stops the pins being
    "cleaned up" as redundant later. EL is the important row: the classifier
    gives a DIFFERENT answer at each end of the cycle, so without the pin the
    profile itself flips with the cycle, and no fixed remedy can be attached to
    it. This is the precondition for routing its earnings leg to `P/E (norm)`
    on a margin-deviation test rather than on a profile name.

    Also pins the zero-golden-blast-radius claim: none of the four is a fixture,
    so these overrides cannot move `tests/golden/snapshots.json`. That is why
    this change ships without a golden re-baseline.
    """
    from src.data.sector_profiles import classify_valuation_profile as _c

    would_classify = {
        "NKE":      _c("Consumer", 0.03, 0.10, 0.6),
        "ONON":     _c("Consumer", 0.30, 0.12, 0.3),
        "EL":       _c("Consumer", -0.02, 0.10, 0.5),   # trough
        "02020.HK": _c("Consumer", 0.12, 0.18, 0.4),
    }
    assert would_classify == {
        "NKE":      "Apparel / Athletic Wear",
        "ONON":     "Apparel / Athletic Wear",
        "EL":       "Household / Personal",
        "02020.HK": "Luxury Goods",
    }, would_classify

    pinned = {t: TICKER_SECTOR_LOOKUP[t][1]
              for t in ("NKE", "ONON", "EL", "02020.HK")}
    # NKE is the one pin that currently agrees with the classifier. It is not
    # redundant, but WHAT it is doing changed with the Step 7 monotonic migration
    # and the old comment here was stale. It used to say the classifier "only
    # returns Apparel while NKE's FCF margin stays inside [0.05, 0.18), and the
    # pin is what survives a margin improvement past 0.18" — true, and it framed
    # the pin as protection against a DEMOTION, because past 0.18 the band-pass
    # sent NKE to Household / Personal (tier 2 against Apparel's tier 4).
    #
    # Past 0.18 the ladder now returns Luxury Goods (tier 5). So the pin no longer
    # rescues NKE from a worse methodology; it holds NKE BELOW where the ladder
    # would put it, on EV/EBITDA at 0.40 with a `P/E (norm)` leg instead of a
    # premium trailing P/E anchor at 0.50. That is a policy choice about which
    # method suits the name rather than a guard against a defect, and it is still
    # the owner's choice to make — the pin is retained byte-identical.
    assert pinned["NKE"] == would_classify["NKE"]
    assert _c("Consumer", 0.03, 0.179, 0.6) == "Apparel / Athletic Wear" == pinned["NKE"]
    assert _c("Consumer", 0.03, 0.180, 0.6) == "Luxury Goods" != pinned["NKE"]
    overridden = {t for t in pinned if pinned[t] != would_classify[t]}
    assert overridden == {"ONON", "EL", "02020.HK"}, overridden

    # EL at a PEAK classifies as Luxury Goods, which is now also its pin — so
    # the pin's effect is to make the profile cycle-invariant, not to pick a
    # side. Both ends of the cycle resolve to the same profile.
    assert _c("Consumer", 0.08, 0.20, 0.5) == "Luxury Goods" == pinned["EL"]

    import json as _json
    snap = _json.loads(
        (Path(__file__).resolve().parent / "golden" / "snapshots.json")
        .read_text(encoding="utf-8"))
    fixtures = {k for k in snap if k != "_meta"}
    assert not ({*pinned} & fixtures), "a pinned archetype became a fixture"


def test_sportswear_is_not_a_damodaran_key_and_the_fall_through_is_harmless():
    """Another gap that is not a defect, measured rather than assumed.

    `DAMODARAN_SECTOR_MAP` is keyed on (primary sector, industry group) from
    indname.xls and has `("Consumer Discretionary", "Apparel")` and
    `("Consumer Discretionary", "Shoe")` but no `Sportswear`. `map_damodaran`
    then falls back to a local `_PRIMARY_FALLBACK` dict whose
    `"Consumer Discretionary"` entry is `("Consumer", "")` — byte-identical to
    what the explicit Apparel and Shoe keys return. So Anta's unrouted industry
    lands on the same sector and the same empty WACC profile as Nike's routed
    one, and the missing key changes nothing.

    Pinned because it looks like a bug on grep and is not, and because the
    fallback is a local dict inside the function rather than a module constant,
    so it is easy to break without noticing.
    """
    from src.data.sector_profiles import map_damodaran

    assert ("Consumer Discretionary", "Apparel") in DAMODARAN_SECTOR_MAP
    assert ("Consumer Discretionary", "Shoe") in DAMODARAN_SECTOR_MAP
    assert ("Consumer Discretionary", "Sportswear") not in DAMODARAN_SECTOR_MAP
    assert map_damodaran("Consumer Discretionary", "Sportswear") == ("Consumer", "")
    assert map_damodaran("Consumer Discretionary", "Sportswear") == \
        map_damodaran("Consumer Discretionary", "Apparel")


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED. The add-back itself exists (`nav = total_seg_value + "
    "associates + net_cash`) and is FX-converted, so the missing piece is "
    "narrow: nothing compares associates to total equity, and nothing adds them "
    "outside the segment path. A 10% trigger needs `shareholders_equity` on the "
    "row — it is requested for HK/SG but not read anywhere near the add-back. "
    "Note the gate as written is one-sided; without a floor the same code "
    "over-values immaterial stakes at full book."))
def test_a_stake_above_ten_percent_of_equity_forces_an_add_back():
    body = _sotp_body()
    assert "shareholders_equity" in body
    assert "0.10" in body


# ── E. Gross-margin qualification for terminal multiples ─────────────────────


def test_the_65_percent_floor_is_live_for_tech_and_inert_for_consumer():
    """The exact floor the brief asks for exists, and stops at the sector line.

    `_SAAS_GROSS_MARGIN_FLOOR = 0.65` — the same number the brief names
    `LUXURY_GROSS_MARGIN_FLOOR`, arrived at independently. It is applied inside
    `_qualified_ev_revenue_multiple`, but behind
    `if not _is_tech_subtype(sector, profile_name): return peer, "peer"` on the
    first line, and `_is_tech_subtype` requires `is_tech_sector(sector)`.

    Called directly, with a peer EV/Revenue of 6.0 and a properly shaped row:

        Technology / Hyper-Growth Platform   gm 0.445 ->  6.0  "peer median — gross
                                              margin 45% is below the 65% SaaS floor"
        Technology / Hyper-Growth Platform   gm 0.595 ->  6.0  (same)
        Technology / Hyper-Growth Platform   gm 0.720 -> 10.0  "SaaS terminal"
        Consumer / Luxury Goods              gm 0.445 ->  6.0  "peer"
        Consumer / Luxury Goods              gm 0.595 ->  6.0  "peer"
        Consumer / Luxury Goods              gm 0.720 ->  6.0  "peer"
        Consumer / Apparel / Athletic Wear   gm 0.445 ->  6.0  "peer"
        Consumer / Apparel / Athletic Wear   gm 0.595 ->  6.0  "peer"
        Consumer / Apparel / Athletic Wear   gm 0.720 ->  6.0  "peer"

    The tech row is margin-sensitive and the two consumer rows are flat: the
    same 6.0 at 44.5% and at 72%. The mechanism is not missing, it is scoped —
    and the scoping is the whole of the brief's Gate 5.

    Two notes on what this does and does not prove. The floor qualifies
    EV/REVENUE, not P/E, so even wired to Consumer it would not gate the
    "25x+ luxury terminal multiple" the brief describes; that multiple arrives
    through `peer.get("pe", 18.0)` on the trailing branch, which has no margin
    qualification at all. And the earlier MELI incident that produced this floor
    was an over-valuation at 10x on 2.65x peer revenue — the same failure shape
    the brief predicts for consumer names, already observed once in production.
    """
    from src.agents.analysis.dcf_agent import (
        _SAAS_GROSS_MARGIN_FLOOR, _SAAS_GROSS_MARGIN_REFERENCE,
        _is_tech_subtype, _qualified_ev_revenue_multiple,
    )

    assert _SAAS_GROSS_MARGIN_FLOOR == 0.65
    assert _SAAS_GROSS_MARGIN_REFERENCE == 0.80
    peer = {"ev_revenue": 6.0}

    def row(gm):
        return {"revenue": 1000.0, "cost_of_revenue": 1000.0 * (1 - gm)}

    assert _is_tech_subtype("Technology", "Hyper-Growth Platform") is True
    for sector, profile in (("Consumer", "Luxury Goods"),
                            ("Consumer", "Apparel / Athletic Wear")):
        assert _is_tech_subtype(sector, profile) is False
        for gm in (0.445, 0.595, 0.720):
            mult, basis = _qualified_ev_revenue_multiple(
                sector, profile, "base", peer, row(gm))
            assert (mult, basis) == (6.0, "peer"), (sector, profile, gm, mult, basis)

    assert _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", peer, row(0.445))[0] == 6.0
    assert _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", peer, row(0.595))[0] == 6.0
    tm, tb = _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", peer, row(0.720))
    assert tm == 10.0 and tb == "SaaS terminal"

    # With no peer multiple to fall back on, the scaling leg is what fires.
    scaled, basis = _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", {}, row(0.445))
    assert scaled == pytest.approx(10.0 * (0.445 / 0.80) ** 2, rel=1e-9)
    assert "scaled by" in basis


def test_the_floors_own_rationale_names_inventory_revenue():
    """The comment above the constant makes the sector-agnostic argument itself.

    Verbatim: "A software terminal EV/Revenue multiple prices revenue that
    converts at software gross margins. Below this line the top line is
    transaction or inventory revenue and the multiple is not the right one."

    Inventory revenue is the consumer case — it is the same sentence with
    "software" struck out. Recorded because it means extending the floor is not
    a new policy position; the policy is already written down and its dispatch
    is narrower than its reasoning.
    """
    src = _engine_src()
    i = src.index("_SAAS_GROSS_MARGIN_FLOOR = 0.65")
    # The block is `#:`-prefixed and wrapped, so the sentences are not
    # contiguous substrings. Unwrap before matching, rather than matching a
    # fragment short enough to survive the wrap.
    lines = [ln.strip() for ln in src[:i].splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    block = []
    for s in reversed(lines):
        if s.startswith("#"):
            block.append(s.lstrip("#:").strip())
        else:
            break
    comment = " ".join(w for w in reversed(block) if w)
    assert comment.startswith("A software terminal EV/Revenue multiple"), comment
    assert "converts at software gross margins" in comment
    assert "transaction or inventory revenue" in comment
    assert "the multiple is not the right one" in comment
    assert "software" in comment, (
        "the rationale no longer names software, so the argument that it is "
        "sector-agnostic in everything but its dispatch no longer holds")


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED for Consumer, and not reachable by widening the existing "
    "floor alone: `_qualified_ev_revenue_multiple` gates an EV/REVENUE multiple, "
    "while the luxury P/E the brief wants to qualify arrives through "
    "`peer.get('pe', 18.0)` on the trailing branch, which has no margin test. "
    "Extending `_is_tech_subtype` would change the EV/Revenue leg for every "
    "consumer name and needs a golden re-baseline; adding a P/E qualification "
    "is new code on a branch that 24 profiles dispatch through."))
def test_a_low_gross_margin_consumer_name_cannot_take_a_luxury_multiple():
    from src.agents.analysis.dcf_agent import _qualified_ev_revenue_multiple
    peer = {"ev_revenue": 25.0}
    row = {"revenue": 1000.0, "cost_of_revenue": 555.0}     # gm 0.445
    mult, basis = _qualified_ev_revenue_multiple(
        "Consumer", "Luxury Goods", "base", peer, row)
    assert mult < 22.0, (mult, basis)
    assert "gross margin" in basis


# ── F. Not in the brief: the Consumer default profile contradicts itself ─────


def test_consumer_growth_declares_a_leg_it_always_strips():
    """The Consumer sector DEFAULT profile carries a weight that never applies.

    Found while mapping the archetypes, not in the brief, and it matters more
    than a bookkeeping nit because `_SECTOR_PROFILE_DEFAULT["Consumer"]` is
    `Consumer Growth` — this is the profile a Consumer name lands on when
    classification is skipped or fails, and it is where ONON routes the moment
    its FCF margin clears 15% at a CAGR above 15%.

    Its `methods` declare DCF 0.40 / EV/Revenue 0.25 / **P/E 0.20** /
    EV/EBITDA 0.15 — summing to exactly 1.00 including a leg that its own
    `excluded: ["P/E"]` strips. The strip is real and happens at two places
    (`method_values_t1.pop(ex, None)` on the Tier-1 path and
    `method_values.pop(ex, None)` on the scenario path), and `_blend_methods`
    renormalises whatever survives. Measured through the real function:

        P/E present : weight_dcf 0.40, effective DCF 0.40 / EV-Rev 0.25 /
                      P/E 0.20 / EV-EBITDA 0.15
        P/E stripped: weight_dcf 0.50, effective DCF 0.50 / EV-Rev 0.3125 /
                      EV-EBITDA 0.1875

    So the rationale text — "a blend of DCF intrinsic value (50%)" — describes
    the EFFECTIVE blend and contradicts the declared 0.40. Both readings are
    defensible in isolation; anyone computing an expected blend from `methods`
    gets 40% DCF and anyone computing it from the rationale gets 50%, and the
    engine does the second.

    The 0.20 is not harmless dead text. It is the weight the profile would carry
    if `excluded` were ever dropped, renamed or mis-spelled — and nothing
    validates that the two fields agree. The exclusion is also the reason the
    brief's Gate 3 has no normalized leg to route to on this profile: the
    earnings-based method was removed rather than replaced with `P/E (norm)`,
    which is exactly the substitution Gate 3 asks for.
    """
    from src.agents.analysis.dcf_agent import _blend_methods
    from src.data.sector_profiles import _SECTOR_PROFILE_DEFAULT

    assert _SECTOR_PROFILE_DEFAULT["Consumer"] == "Consumer Growth"
    prof = _consumer_profiles()["Consumer Growth"]
    ms = prof["methods"]
    assert {m["name"]: m["weight"] for m in ms} == {
        "DCF": 0.40, "EV/Revenue": 0.25, "P/E": 0.20, "EV/EBITDA": 0.15}
    assert sum(m["weight"] for m in ms) == pytest.approx(1.0, abs=1e-9)
    assert prof["excluded"] == ["P/E"]
    assert "DCF intrinsic value (50%)" in prof["rationale"]
    assert "P/E excluded" in prof["rationale"]

    flat = {"DCF": 100.0, "EV/Revenue": 100.0, "P/E": 100.0, "EV/EBITDA": 100.0}
    _, with_pe = _blend_methods(ms, dict(flat), 1.0, [], 0.5)
    assert with_pe["weight_dcf"] == pytest.approx(0.40)
    assert {e["method"]: e["weight"] for e in with_pe["effective_weights"]} == \
        {"DCF": 0.40, "EV/Revenue": 0.25, "P/E": 0.20, "EV/EBITDA": 0.15}

    stripped = {k: v for k, v in flat.items() if k != "P/E"}
    iv, no_pe = _blend_methods(ms, dict(stripped), 1.0, [], 0.5)
    assert no_pe["weight_dcf"] == pytest.approx(0.50)
    assert {e["method"]: e["weight"] for e in no_pe["effective_weights"]} == \
        {"DCF": 0.50, "EV/Revenue": 0.3125, "EV/EBITDA": 0.1875}

    # The renormalisation is exact, so the effective weights are checkable by
    # hand rather than merely observed.
    v = {"DCF": 200.0, "EV/Revenue": 100.0, "EV/EBITDA": 60.0}
    iv2, bd = _blend_methods(ms, dict(v), 1.0, [], 0.5)
    assert iv2 == pytest.approx(
        0.50 * 200.0 + 0.50 * (0.625 * 100.0 + 0.375 * 60.0), rel=1e-12)
    assert iv2 == pytest.approx(142.5, rel=1e-12)
    assert bd["iv_multi"] == pytest.approx(85.0, rel=1e-12)


def test_the_two_strip_sites_are_both_live():
    """`excluded` is honoured on the Tier-1 path and the scenario path alike.

    Pinned because the profile contradiction above is only a documentation
    problem while both strips fire. If either stopped reading `excluded`,
    `Consumer Growth` would silently regain a trailing P/E leg at 0.20 and lose
    10 points of DCF weight — a real valuation change with no test failing
    unless this one does.
    """
    src = _engine_src()
    assert src.count('for ex in profile_data.get("excluded", []):') == 1
    assert src.count("excluded = profile_data.get(\"excluded\", [])") == 1
    assert "method_values_t1.pop(ex, None)" in src
    assert "method_values.pop(ex, None)" in src


# ── G. The brief's three integration steps, as a status record ───────────────


def test_none_of_the_briefs_three_integration_steps_is_present():
    """Each of the three edits the brief asks for, checked for by name.

    Recorded as one test rather than three because they are one deliverable, and
    because the useful fact is that NONE of them landed — a partial application
    would be worse than none, since a pre-flight hook with no bound
    reinvestment, or a bound reinvestment with no hook, would each look like the
    gate was live.

    1. a pre-flight hook calling `evaluate_consumer_discretionary_guards` before
       `_project_dcf` — the function does not exist in any module;
    2. a reinvestment deduction bound inside the projection loop via
       `overrides["apply_reinvestment_deduction"]` — neither name exists, and
       the projector has no `overrides` parameter (Section B);
    3. the resulting cash-flow identity
       `fcf_t = (ebit_t * (1 - effective_tax_rate)) + da_t - capex_t
                - reinvestment_deduction` — the real identity is
       `fcf_t = rev_t * margin_t` (Section B, measured through the function).

    This test is deliberately NOT an xfail. The five xfails above mark the
    invariants the engine does not enforce; this one marks the brief's
    implementation plan as not-started, which is a different claim and should
    stay green until someone begins it — at which point it will fail on the
    first name that appears, and the xfail above it can be retired.

    TWO NAMES HAVE SINCE LEFT THIS LIST. `GATE_INVENTORY_STRESS` and
    `_inventory_stress_days` appeared when the post-mortem's Failure 4 landed,
    and this test did exactly what it says it would: it went red on the first
    name that appeared. They are asserted PRESENT below rather than deleted from
    the record, so the list keeps meaning "here is every name the brief and the
    post-mortem asked for, and where each one stands". The brief's three
    integration steps are still not started — the inventory gate is Failure 4 of
    the post-mortem, which is a different deliverable and does not need a
    pre-flight hook, an `overrides` key or a capex-shaped cash-flow identity.
    """
    src = _engine_src()
    for name in ("evaluate_consumer_discretionary_guards",
                 "apply_reinvestment_deduction",
                 "LUXURY_GROSS_MARGIN_FLOOR",
                 "_margin_deviation_routes_to_normalized"):
        assert name not in src, f"{name!r} has appeared; update this record"
    for landed in ("GATE_INVENTORY_STRESS", "_inventory_stress_days"):
        assert landed in src, f"{landed!r} was removed; update this record"
    # Whitespace-insensitive: the real line is column-aligned as
    # `fcf_t    = rev_t * margin_t`, and pinning the padding would make this
    # fail on a reformat that changes nothing.
    assert re.search(r"fcf_t\s*=\s*rev_t \* margin_t", src), \
        "the projector's cash-flow identity is no longer revenue x margin"
    assert "ebit_t" not in src and "da_t" not in src and "capex_t" not in src
    # The hook site the brief names, so the "before _project_dcf" claim has a
    # concrete anchor: the projection call is inside run_dcf_agent's scenario
    # loop, downstream of every margin decision, not upstream of them.
    assert src.count("_project_dcf(") >= 2
