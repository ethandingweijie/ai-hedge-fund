"""Phase 1.2B — the cyclical peak-consensus trigger and the mid-cycle leg swap.

The defect: a one-year consensus jump was capitalised as though it were a
permanent level, on businesses whose whole characteristic is that a good year
does not repeat. MU published $6,105 against a spot price in the $100s. There
was no unit error to find — it was $156.08 of FY27 consensus EPS times a peer
multiple, arithmetic on an input that has not happened and, on a memory
manufacturer at a cycle top, will not hold.

These tests cover the DECISION logic. The valuation consequence is covered by
the golden baseline, which is the only place a whole-engine output is pinned.

Everything here ships observation-only (`applied: False`): the plan's shipping
rule holds an item back until its backward test clears acceptance — hit-rate
≥ 0.50, error no worse than legacy, ≥ 10 scoreable firings — and row 1.2B has
zero scoreable firings until Phase 3 back-fills US history, because historical
consensus is not archived. The section marked "Observation-only wiring" below
pins the four ways the gate could stop being observation-only without the diff
making it obvious; the published-IV consequence is pinned by the golden
baseline, which recorded MU at 293.14 and FCX at 25.58 before and after.
"""
from __future__ import annotations

import ast
import inspect
import io
import statistics
import tokenize

import pytest

import src.agents.analysis.dcf_agent as dcf_agent
from src.agents.analysis.dcf_agent import (
    _compute_method_value,
    _CYCLICAL_PROFILES,
    _historical_eps_series,
    _MID_CYCLE_LEG_SWAPS,
    _mid_cycle_leg_swaps,
    _PB_ROE_MIN_PB,
    _PB_ROE_MIN_SPREAD,
    _PEAK_EPS_MAX_MULTIPLE,
    _PEAK_EPS_SIGMA_MULTIPLE,
    _PEAK_MIN_OBSERVATIONS_FOR_SIGMA,
    _peak_consensus_trigger,
)
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES


# ── Real shapes, measured from the golden fixtures ──────────────────────────
# These are DILUTED EPS, which is what the engine actually computes: the
# LineItem field `shares_outstanding` maps to FMP's `weightedAverageShsOutDil`,
# not `weightedAverageShsOut`, so `_historical_eps_series` divides net income by
# the diluted count. That is the right side to compare against — analyst
# consensus EPS is diluted too — but it means the basic-share figures (MU FY22
# 7.81, FY25 7.65; FCX FY21 2.93) are NOT what the trigger sees, and quoting
# them makes the published flag unreproducible from the test. `tests/golden/
# snapshots.json` carries `fwd EPS 156.08 vs 15.48` for MU, and 15.48 is
# 2 × 7.7424, the diluted max; 2 × 7.8121 would be 15.62.
#
# MU diluted: FY21 5.14, FY22 7.74, FY23 −5.34 (the downcycle), FY24 0.70,
# FY25 7.59. Forward consensus $156.08 (FY27, 24 EPS analysts).
# FCX diluted: 2.90 / 2.39 / 1.28 / 1.30 / 1.53, forward $2.95.
_MU_EPS = [5.1367, 7.7424, -5.3367, 0.6959, 7.5902]
_MU_FWD = 156.08
_FCX_EPS = [2.9008, 2.3853, 1.2765, 1.3031, 1.5274]
_FCX_FWD = 2.95

#: The lines the golden baseline publishes, so a fixture edit that quietly
#: changes them fails here rather than surfacing as an unexplained golden move.
_MU_MAX_LINE = 15.4848
_MU_SIGMA_LINE = 14.2472
_FCX_MAX_LINE = 5.8016
_FCX_SIGMA_LINE = 3.3342


def _series(eps: list[float], *, shares: float = 1e9) -> list[dict]:
    """Annual rows shaped like `_extract_annual_series` output."""
    return [{"net_income": e * shares, "shares_outstanding": shares,
             "revenue": 20e9, "period": f"202{i}-12-31"}
            for i, e in enumerate(eps)]


def _consensus(eps: float) -> dict:
    """Forward consensus in the shape `_peak_consensus_trigger` reads: a per-
    scenario EPS spread. The bear/bull values are filler — the trigger is
    evaluated on base, and `test_the_trigger_reads_the_base_scenario_only` is
    what pins that."""
    return {"eps": {"bear": eps * 0.8, "base": eps, "bull": eps * 1.2}}


# ── _historical_eps_series ──────────────────────────────────────────────────

def test_eps_is_derived_because_the_series_carries_no_eps_field():
    rows = _series([2.0, 4.0])
    assert _historical_eps_series(rows) == pytest.approx([2.0, 4.0])


def test_rows_without_a_share_count_are_dropped_not_zero_filled():
    """A zero would drag the mean down and widen sigma, both of which push the
    trigger toward firing. Dropping is the honest option: the quotient is
    undefined, not small."""
    rows = _series([2.0, 4.0])
    rows.append({"net_income": 5e9, "shares_outstanding": None, "revenue": 1e9})
    rows.append({"net_income": 5e9, "shares_outstanding": 0, "revenue": 1e9})
    rows.append({"net_income": None, "shares_outstanding": 1e9, "revenue": 1e9})
    assert _historical_eps_series(rows) == pytest.approx([2.0, 4.0])


def test_empty_and_none_series():
    assert _historical_eps_series(None) == []
    assert _historical_eps_series([]) == []
    assert _historical_eps_series([{"revenue": 1e9}]) == []


# ── _peak_consensus_trigger ─────────────────────────────────────────────────

def test_mu_fires_both_arms():
    got = _peak_consensus_trigger(_series(_MU_EPS), _consensus(_MU_FWD))
    assert got is not None and got["fired"]
    assert set(got["arms"]) == {"multiple-of-max", "mean-plus-sigma"}
    assert got["eps_max"] == pytest.approx(max(_MU_EPS))
    assert got["max_line"] == pytest.approx(_PEAK_EPS_MAX_MULTIPLE * max(_MU_EPS))
    assert got["multiple_of_max"] == pytest.approx(_MU_FWD / max(_MU_EPS))
    assert got["n_years"] == 5


def test_the_lines_are_the_ones_the_golden_baseline_publishes():
    """Pin the fixture to the recorded output, not just to itself.

    `tests/golden/snapshots.json` carries MU's base forward flag as
    "peak trigger fired (multiple-of-max+mean-plus-sigma, fwd EPS 156.08 vs
    15.48)". The 15.48 is `max_line` formatted to two places, so these four
    literals are what makes the published string reproducible from the fixture
    in this file. They caught a real mistake: the fixture originally held
    basic-share EPS, whose max line is 15.62, and every test in this file
    passed anyway because they were all written in terms of the fixture rather
    than of anything the engine emits.
    """
    mu = _peak_consensus_trigger(_series(_MU_EPS), _consensus(_MU_FWD))
    fcx = _peak_consensus_trigger(_series(_FCX_EPS), _consensus(_FCX_FWD))
    assert f"{mu['max_line']:.2f}" == "15.48"
    assert mu["max_line"] == pytest.approx(_MU_MAX_LINE, rel=1e-3)
    assert mu["sigma_line"] == pytest.approx(_MU_SIGMA_LINE, rel=1e-3)
    assert fcx["max_line"] == pytest.approx(_FCX_MAX_LINE, rel=1e-3)
    assert fcx["sigma_line"] == pytest.approx(_FCX_SIGMA_LINE, rel=1e-3)


def test_fcx_fires_neither_arm():
    got = _peak_consensus_trigger(_series(_FCX_EPS), _consensus(_FCX_FWD))
    assert got is not None and not got["fired"]
    assert got["arms"] == []


def test_fcx_is_close_to_the_sigma_line_and_that_is_recorded_not_tuned():
    """The knife-edge, stated. FCX's forward $2.95 sits 11.5% below its own
    mean+2σ line of $3.33, so a small feed revision flips this name into the
    gate. That is a property of a two-arm trigger on five observations, not a
    defect to smooth over — but it is the reason the record stores the lines
    themselves rather than only the verdict, so a future flip is legible in the
    ledger instead of looking like a behaviour change."""
    got = _peak_consensus_trigger(_series(_FCX_EPS), _consensus(_FCX_FWD))
    assert got["sigma_line"] is not None
    gap = (got["sigma_line"] - _FCX_FWD) / got["sigma_line"]
    assert 0.0 < gap < 0.15, gap
    assert _FCX_FWD < got["max_line"]


def test_no_consensus_is_unscoreable_not_quiet():
    """None, not `fired: False`. A name that cannot be evaluated must not enter
    the denominator of a hit-rate measured against 'at least 10 scoreable
    firings' — counting it as a quiet pass would let the bar be cleared by
    running the gate on names with no forward data."""
    assert _peak_consensus_trigger(_series(_MU_EPS), None) is None
    assert _peak_consensus_trigger(_series(_MU_EPS), {}) is None
    assert _peak_consensus_trigger(_series(_MU_EPS), {"eps": {}}) is None


def test_no_history_is_unscoreable_not_quiet():
    assert _peak_consensus_trigger([], _consensus(_MU_FWD)) is None
    assert _peak_consensus_trigger(None, _consensus(_MU_FWD)) is None


def test_non_positive_forward_eps_is_unscoreable():
    assert _peak_consensus_trigger(_series(_MU_EPS), _consensus(0.0)) is None
    assert _peak_consensus_trigger(_series(_MU_EPS), _consensus(-3.0)) is None


def test_the_trigger_reads_the_base_scenario_only():
    """Whether a business is at a cycle top is a fact about the cycle, not about
    the spread drawn around it. Evaluating per scenario would let the bull case
    route itself to P/B-ROE and the bear case keep its forward legs, on the same
    history, in the same run."""
    fc = {"eps": {"bear": 1.0, "base": _MU_FWD, "bull": 400.0}}
    series = _series(_MU_EPS)
    assert _peak_consensus_trigger(series, fc)["eps_forward"] == pytest.approx(_MU_FWD)
    # Asking for a non-base scenario is still answered, but the caller does not.
    assert _peak_consensus_trigger(series, fc, scenario="bull")["eps_forward"] \
        == pytest.approx(400.0)


def test_the_sigma_arm_needs_enough_of_a_cycle():
    """With two points a standard deviation describes the two points, not a
    cycle, and mean+2σ sits BELOW the maximum by construction — the arm would
    fire on any short history with one good year. The multiple-of-max arm is
    unaffected and still applies."""
    two = _peak_consensus_trigger(_series([1.0, 5.0]), _consensus(12.0))
    assert two is not None
    assert two["sigma_line"] is None, "sigma arm must stand down below the floor"
    assert two["fired"] and two["arms"] == ["multiple-of-max"]
    assert two["n_years"] == 2

    four = _peak_consensus_trigger(_series([1.0, 2.0, 3.0, 4.0]), _consensus(12.0))
    assert four["sigma_line"] is not None
    assert "mean-plus-sigma" in four["arms"]


def test_a_uniformly_depressed_history_still_fires_on_the_max_arm():
    """The case the sigma arm misses and the max arm catches: every year bad, so
    mean+2σ is low and easily cleared — but that is exactly when a 2x jump on a
    cyclical is a cycle, not an inflection."""
    got = _peak_consensus_trigger(_series([0.10, 0.12, 0.08, 0.11, 0.09]),
                                  _consensus(0.30))
    assert got["fired"]
    assert "multiple-of-max" in got["arms"]


def test_a_single_spike_does_not_fire_the_max_arm_on_a_modest_forward():
    """The case the max arm misses: one anomalous year raises the bar so far that
    a genuinely elevated but ordinary forward number passes. The sigma arm is
    what catches it, which is why the trigger is two-armed."""
    got = _peak_consensus_trigger(_series([1.0, 1.1, 12.0, 1.0, 1.2]),
                                  _consensus(3.0))
    assert got["max_line"] == pytest.approx(24.0)
    assert not got["fired"] or "multiple-of-max" not in got["arms"]


def test_negative_history_years_are_kept():
    """MU's FY23 loss of −5.34 is part of the cycle and must pull the mean down
    and push sigma up. Filtering to positive years would flatter the trigger on
    precisely the names it exists for — measured on the diluted series, it moves
    MU's mean from 3.17 to 5.29 and its sigma from 5.54 to 3.29, which drops the
    mean+2σ line from 14.25 to 11.87 and makes the sigma arm MORE trigger-happy,
    not less. MU fires both arms by a factor of ten either way, so this is not
    about the verdict on MU; it is about the next name that sits near the line."""
    got = _peak_consensus_trigger(_series(_MU_EPS), _consensus(_MU_FWD))
    assert got["eps_mean"] == pytest.approx(statistics.mean(_MU_EPS))
    assert got["eps_sigma"] == pytest.approx(statistics.stdev(_MU_EPS))
    assert got["eps_mean"] < 0.5 * got["eps_max"], (
        "the loss year is not dragging the mean; it has been filtered out")
    positive = [e for e in _MU_EPS if e > 0]
    assert got["eps_mean"] < statistics.mean(positive)
    # Assert the direction of the claim in the docstring rather than leaving it
    # as narration: dropping the loss year lowers the sigma line, so a name near
    # the boundary would fire that otherwise would not.
    filtered_line = statistics.mean(positive) + 2 * statistics.stdev(positive)
    assert filtered_line < got["sigma_line"]
    assert got["sigma_line"] == pytest.approx(14.2472, rel=1e-3)
    assert filtered_line == pytest.approx(11.8675, rel=1e-3)


# ── _mid_cycle_leg_swaps ────────────────────────────────────────────────────

def _profile(name: str) -> dict:
    for sector, profiles in INDUSTRY_VALUATION_PROFILES.items():
        if not isinstance(profiles, dict):
            continue
        for pname, pdata in profiles.items():
            if pname == name and isinstance(pdata, dict) and "methods" in pdata:
                return pdata
    raise AssertionError(f"no profile named {name!r}")


def test_non_cyclical_profiles_are_untouched():
    assert _mid_cycle_leg_swaps(_profile("Mature SaaS"), "Mature SaaS") == []
    assert _mid_cycle_leg_swaps(_profile("Money Center Bank"),
                                "Money Center Bank") == []


def test_the_cyclical_profiles_are_the_ones_the_plans_name():
    # The eight of the hardening plan, plus the four commodity-cycle profiles
    # of Wave 1 oil, gas & coal (owner-approved 2026-09-20).
    assert _CYCLICAL_PROFILES == frozenset({
        "Memory / DRAM-NAND", "Mining (Major)", "Upstream Oil & Gas",
        "Steel / Metals", "Specialty Chemicals", "Airlines",
        "Automotive (OEM)", "Digital Asset Mining",
        "Integrated Oil & Gas", "Refining & Marketing",
        "Oilfield Services & Drilling", "Coal"})
    for name in _CYCLICAL_PROFILES:
        _profile(name)          # raises if the taxonomy drifted


def test_memory_dram_swaps_only_its_trailing_ev_ebitda_leg():
    """MU. `P/E (norm)` is already the 0.45 anchor; the 0.30 `EV/EBITDA` leg is
    the one still capitalising a peak year — measured at 346.21 against a base
    IV of 293.14, i.e. the leg pulling the blend up."""
    got = _mid_cycle_leg_swaps(_profile("Memory / DRAM-NAND"), "Memory / DRAM-NAND")
    assert [(s["from"], s["to"]) for s in got] == [("EV/EBITDA", "EV/EBITDA (norm)")]
    assert got[0]["weight"] == pytest.approx(0.30)
    assert got[0]["anchor"] is False


def test_automotive_swaps_both_legs_including_its_anchor():
    got = _mid_cycle_leg_swaps(_profile("Automotive (OEM)"), "Automotive (OEM)")
    assert {(s["from"], s["to"]) for s in got} == {
        ("EV/EBITDA", "EV/EBITDA (norm)"), ("P/E", "P/E (norm)")}
    anchor = next(s for s in got if s["anchor"])
    assert anchor["from"] == "EV/EBITDA" and anchor["weight"] == pytest.approx(0.40)


def test_profiles_already_normalised_swap_nothing():
    """A second copy of an already-present leg would DOUBLE its weight in the
    blend, because _blend_methods sums whatever the profile lists."""
    for name in ("Mining (Major)", "Digital Asset Mining"):
        got = _mid_cycle_leg_swaps(_profile(name), name)
        assert not any(s["to"] == "EV/EBITDA (norm)" for s in got), name


def test_steel_keeps_its_capital_n_anchor_and_swaps_only_pe():
    got = _mid_cycle_leg_swaps(_profile("Steel / Metals"), "Steel / Metals")
    assert [(s["from"], s["to"]) for s in got] == [("P/E", "P/E (norm)")]


def test_airlines_ebitdar_is_not_touched_by_an_exact_name_match():
    """`EV/EBITDAR` is a different method whose lease normalisation is separately
    unimplemented. A prefix or substring match here would rewrite it into
    `EV/EBITDA (norm)` and silently change an airline's 0.50 anchor from an
    EBITDAR leg to an EBITDA one."""
    got = _mid_cycle_leg_swaps(_profile("Airlines"), "Airlines")
    assert [(s["from"], s["to"]) for s in got] == [("P/E", "P/E (norm)")]


def test_upstream_oil_and_gas_has_nothing_to_swap():
    """Recorded because it is a hole in the item, not a success. The profile
    anchors on P/CF at 0.60 with a Depleting Asset DCF and P/BV — no EV/EBITDA
    and no P/E — so a peak oil price flows straight through the anchor and this
    gate does nothing for it. Normalising a P/CF leg is a different change with a
    different data requirement."""
    assert _mid_cycle_leg_swaps(_profile("Upstream Oil & Gas"),
                                "Upstream Oil & Gas") == []


def test_an_excluded_target_is_not_swapped_in():
    """`profile_data["excluded"]` is applied by popping from method_values after
    the fact, so swapping in an excluded leg would compute a value that is then
    thrown away — and, worse, record it as path B."""
    pd = {"methods": [{"name": "EV/EBITDA", "weight": 0.5},
                      {"name": "P/BV", "weight": 0.5}],
          "excluded": ["EV/EBITDA (norm)"]}
    # The exclusion filter lives at the call site, mirroring how `excluded` is
    # applied to method_values; assert the helper still proposes and the caller
    # is what filters.
    proposed = _mid_cycle_leg_swaps(pd, "Memory / DRAM-NAND")
    assert [s["to"] for s in proposed] == ["EV/EBITDA (norm)"]
    kept = [s for s in proposed if s["to"] not in pd["excluded"]]
    assert kept == []


def test_no_profile_or_none_is_safe():
    assert _mid_cycle_leg_swaps(None, "Memory / DRAM-NAND") == []
    assert _mid_cycle_leg_swaps({"methods": []}, "Memory / DRAM-NAND") == []
    assert _mid_cycle_leg_swaps({"methods": [{"name": "P/E"}]}, None) == []


def test_the_swap_table_is_exactly_two_entries():
    assert _MID_CYCLE_LEG_SWAPS == {"EV/EBITDA": "EV/EBITDA (norm)",
                                    "P/E": "P/E (norm)"}


# ── P/B-ROE (mid-cycle) ─────────────────────────────────────────────────────

_ROW = {
    "revenue": 20e9, "ebitda": 4e9, "ebit": 2.5e9, "net_income": 2e9,
    "total_equity": 10e9, "total_assets": 18e9,
    "normalized_ebitda": 3e9, "normalized_net_income": 1.5e9,
    "cash": 1e9, "total_debt": 2e9, "net_debt": 1e9,
    "book_value_per_share": 50.0, "shares_outstanding": 200e6,
    "operating_cash_flow": 3e9, "capital_expenditure": -1e9,
    "free_cash_flow": 2e9,
}
_PEER = {"ev_ebitda": 8.0, "ev_revenue": 1.5, "pe": 14.0, "pb": 1.2,
         "fcf_yield": 0.06}


def _call(method_name: str, *, wacc: float = 0.09, tgr: float = 0.025,
          row: dict | None = None, scenario: str = "base"):
    """Returns (row, value) — the row is handed back because the branch records
    any binding floor on it, and a floor that binds silently is the failure mode
    the owner's two bounds exist to prevent."""
    r = dict(_ROW if row is None else row)
    return r, _compute_method_value(
        method_name=method_name, most_recent=r, revenue_base=r["revenue"],
        shares=200e6, net_debt=r["net_debt"], market_cap=10e9, wacc=wacc,
        growth_base=0.03, fcf_margin_base=0.10, tgr=tgr, fcf_floor=0.0,
        sector="Semiconductor", scenario=scenario, reported_currency="USD",
        is_hk=False, growth_premium=1.0, sbc_pe_discount=1.0,
        profile_name="Memory / DRAM-NAND", ticker="", end_date="2026-09-17")


@pytest.fixture(autouse=True)
def _fixed_peer(monkeypatch):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples",
                        lambda *a, **k: dict(_PEER))


def test_pb_roe_is_the_gordon_form_on_normalised_roe():
    _row, got = _call("P/B-ROE (mid-cycle)")
    roe_norm = _ROW["normalized_net_income"] / _ROW["total_equity"]   # 0.15
    expected_pb = (roe_norm - 0.025) / (0.09 - 0.025)                # 1.923...
    assert got == pytest.approx(_ROW["book_value_per_share"] * expected_pb)
    assert _row.get("_pb_roe_floors") in (None, []), "no floor should bind here"


def test_it_uses_normalised_not_reported_earnings():
    """The entire point. Reported net income is 2e9 (ROE 0.20) and normalised is
    1.5e9 (ROE 0.15); at a cycle top the normalised figure is the lower one, and
    using reported earnings would publish the peak multiple."""
    _row, got = _call("P/B-ROE (mid-cycle)")
    peak_pb = (0.20 - 0.025) / (0.09 - 0.025)
    assert got < _ROW["book_value_per_share"] * peak_pb


def test_the_spread_floor_binds_and_is_recorded():
    """CoE − g below 2.5% divides by almost nothing. Reachable, not theoretical:
    the projection loop already forces tgr = wacc − 0.005 when wacc <= tgr, so a
    0.5% spread arrives here from the engine's own clamp."""
    _row, got = _call("P/B-ROE (mid-cycle)", wacc=0.03, tgr=0.025)
    roe_norm = _ROW["normalized_net_income"] / _ROW["total_equity"]
    assert got == pytest.approx(
        _ROW["book_value_per_share"] * (roe_norm - 0.025) / _PB_ROE_MIN_SPREAD)
    assert _row["_pb_roe_floors"], "a binding floor must be recorded, not silent"
    assert any("CoE-g" in f for f in _row["_pb_roe_floors"])


def test_the_spread_floor_also_covers_a_negative_spread():
    _row, got = _call("P/B-ROE (mid-cycle)", wacc=0.02, tgr=0.03)
    assert got is not None and got > 0
    assert _row["_pb_roe_floors"]


def test_the_pb_floor_binds_and_is_recorded():
    """A trough ROE below g gives a negative justified P/B. The floor is a
    statement that the Gordon form has left its domain, not a valuation view."""
    row = {**_ROW, "normalized_net_income": 0.1e9}      # ROE_norm = 0.01 < g
    _row, got = _call("P/B-ROE (mid-cycle)", row=row)
    assert got == pytest.approx(row["book_value_per_share"] * _PB_ROE_MIN_PB)
    assert any("P/B" in f for f in _row["_pb_roe_floors"])


def test_there_is_no_upper_cap_on_pb():
    """The owner specified floors. A cap would be an unauthorised clamp on an
    estimate, and the standing constraint on this work is that the only new
    bounds are the ones chosen."""
    row = {**_ROW, "normalized_net_income": 9e9}         # ROE_norm = 0.90
    _row, got = _call("P/B-ROE (mid-cycle)", row=row)
    uncapped_pb = (0.90 - 0.025) / (0.09 - 0.025)
    assert got == pytest.approx(row["book_value_per_share"] * uncapped_pb)
    assert uncapped_pb > 10.0


def test_bvps_falls_back_to_equity_over_shares():
    row = {**_ROW, "book_value_per_share": None}
    _row, got = _call("P/B-ROE (mid-cycle)", row=row)
    roe_norm = row["normalized_net_income"] / row["total_equity"]
    bv = row["total_equity"] / 200e6
    assert got == pytest.approx(bv * (roe_norm - 0.025) / (0.09 - 0.025))


def test_it_returns_none_without_normalised_earnings_or_equity():
    """Unavailable, not invented — the same rule the plan sets for ROIC."""
    assert _call("P/B-ROE (mid-cycle)",
                 row={**_ROW, "normalized_net_income": None})[1] is None
    assert _call("P/B-ROE (mid-cycle)",
                 row={**_ROW, "total_equity": 0})[1] is None
    assert _call("P/B-ROE (mid-cycle)",
                 row={**_ROW, "total_equity": -1e9})[1] is None


def test_the_scenario_multiplier_applies():
    _row_b, base = _call("P/B-ROE (mid-cycle)", scenario="base")
    _row_u, bull = _call("P/B-ROE (mid-cycle)", scenario="bull")
    _row_d, bear = _call("P/B-ROE (mid-cycle)", scenario="bear")
    # Every scenario runs at sm=1.0 unless the table says otherwise; assert the
    # relationship rather than the constant so a recalibration does not silently
    # pass a test that stopped testing anything.
    assert base > 0 and bull > 0 and bear > 0
    assert bull >= bear


def test_the_leg_name_is_a_dispatch_literal():
    """`P/B-ROE (mid-cycle)` appears in no profile — the gate injects it — so the
    table-walking coverage test in test_profile_method_names_are_dispatched.py
    never reaches it. A renamed leg would fall out of `_compute_method_value`'s
    trailing `return None` and the record would carry a null path B: the gate
    would look like it had measured a substitution worth nothing.

    Tokenized rather than substring-matched, for the reason that test file gives
    at length: this name is also written down in comments in the same module.
    """
    src = inspect.getsource(dcf_agent)
    lits = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(tok.string)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, str):
            lits.add(value)
    assert "P/B-ROE (mid-cycle)" in lits
    assert _call("P/B-ROE (mid-cycle)")[1] is not None


# ── Observation-only wiring ─────────────────────────────────────────────────
#
# These are source assertions, not behaviour, and they are deliberately narrow:
# the function under test is the nine-thousand-line DCF entry point, and its
# end-to-end effect is pinned by the golden baseline, which is the only place a
# whole-engine output is checked. What is asserted here is the three ways the
# gate could stop being observation-only without anyone noticing in a diff —
# each of which the baseline would catch only as an unexplained IV move.

_GATE_SRC = inspect.getsource(dcf_agent)


def test_the_gate_record_is_marked_not_applied():
    assert '"applied": False' in _GATE_SRC
    # The gate id the ledger and the forward test join on.
    assert "GATE_CYCLICAL_PEAK_CONSENSUS" in _GATE_SRC


def test_the_observation_flag_lands_on_the_scenario_it_was_measured_on():
    """Regression pin for a bug the golden diff caught and a unit test would not
    have.

    `forward_flags = list(ticker_forward_flags)` runs EARLIER in the same
    scenario iteration, so appending the observation to the ticker-level list
    publishes it on the NEXT scenario: base — the only one the trigger is
    evaluated on — would not carry it, and bear and bull would carry an
    observation about a run they never made. The replay showed exactly that:
    `scenarios.bear.forward_flags` and `scenarios.bull.forward_flags` changed,
    `scenarios.base.forward_flags` did not.
    """
    i = _GATE_SRC.index("Cyclical peak-consensus gate (observation-only,")
    tail = _GATE_SRC[i - 400:i]
    assert "forward_flags.append(" in tail, (
        "the observation flag went back onto ticker_forward_flags, which is "
        "snapshotted per scenario above this point — base loses the flag and "
        "bear/bull inherit it")
    assert "ticker_forward_flags.append(" not in tail


def test_the_path_b_blend_is_base_only():
    """The blend block runs for every scenario. Without the base guard, base's
    record would be overwritten with a bear IV in `base_iv_path_a`, and the
    forward test would score a path-A/path-B pair measured on different
    scenarios."""
    i = _GATE_SRC.index("_cyc_rec[\"base_iv_path_a\"] = blended_iv")
    head = _GATE_SRC[max(0, i - 1200):i]
    assert 'scenario == "base"' in head


def _enclosing_branches(tree: ast.AST, target_lineno: int) -> list[ast.AST]:
    """Every `if`/`while`/`for` enclosing the statement at `target_lineno`,
    outermost first."""
    chain: list[ast.AST] = []

    def walk(node, parents):
        if node is not tree and getattr(node, "lineno", None) == target_lineno:
            chain.extend(parents)
            return True
        branch = node if isinstance(node, (ast.If, ast.While, ast.For)) else None
        for child in ast.iter_child_nodes(node):
            if walk(child, parents + ([branch] if branch else [])):
                return True
        return False

    walk(tree, [])
    return chain


def _branch_key(node: ast.AST) -> str:
    if isinstance(node, ast.For):
        return f"for {ast.unparse(node.target)} in {ast.unparse(node.iter)}"
    return f"{type(node).__name__.lower()} {ast.unparse(node.test)}"


def _conjuncts(test: ast.AST) -> list[ast.AST]:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        out: list[ast.AST] = []
        for value in test.values:
            out.extend(_conjuncts(value))
        return out
    return [test]


def _is_weaker_or_equal(a: ast.AST, b: ast.AST) -> bool:
    """Does passing `b` guarantee `a` was passed?

    Deliberately narrow: equal loops, or an `if` whose test has `a`'s test among
    its top-level conjuncts. Anything else is reported as a failure rather than
    reasoned about — an unsound "probably implies" is what let the first version
    of this test pass on the bug it exists to catch.
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, ast.For):
        return _branch_key(a) == _branch_key(b)
    b_conjuncts = {ast.dump(c) for c in _conjuncts(b.test)}
    return all(ast.dump(c) in b_conjuncts for c in _conjuncts(a.test))


_SYNTHETIC_DOMINATES = '''
def f():
    for scenario in scenarios:
        if profile_data:
            rec = None
            if scenario == "base":
                rec = {"a": 1}
        if profile_data and profile_data.get("methods"):
            iv = blend()
            if scenario == "base" and rec is not None:
                rec["base_iv_path_a"] = iv
'''

_SYNTHETIC_DOES_NOT = '''
def f():
    for scenario in scenarios:
        if profile_data:
            if scenario == "base":
                rec = None
                rec = {"a": 1}
        if profile_data and profile_data.get("methods"):
            iv = blend()
            if scenario == "base" and rec is not None:
                rec["base_iv_path_a"] = iv
'''


def _dominates(src: str, var: str) -> bool:
    """Does `var = None` dominate the guard that reads `var`?"""
    lines = src.splitlines()
    tree = ast.parse(src)
    init_line = next(i for i, l in enumerate(lines, 1)
                     if l.strip() == f"{var} = None")
    read_line = next(i for i, l in enumerate(lines, 1)
                     if l.strip().startswith(f'{var}["base_iv_path_a"]'))

    init_chain = _enclosing_branches(tree, init_line)
    read_chain = _enclosing_branches(tree, read_line)
    guards = [n for n in read_chain
              if isinstance(n, (ast.If, ast.While)) and var in ast.unparse(n.test)]
    assert guards, "no enclosing guard mentions the variable"
    guard_chain = _enclosing_branches(tree, guards[-1].lineno)

    if len(init_chain) > len(guard_chain):
        return False
    return all(_is_weaker_or_equal(a, b)
               for a, b in zip(init_chain, guard_chain))


def test_the_domination_check_catches_the_mutation_it_exists_for():
    """Guard the guard.

    The first version of this check compared guard HEADERS as substrings, and
    `_is_weaker_or_equal` on the mutated source below returned True, because
    `if scenario == "base"` is a substring of `if scenario == "base" and rec is
    not None`. The mutation is exactly the bug being tested for — the
    initialisation moved inside the observation guard, so a ticker that never
    enters it reaches `rec is not None` with an unbound local and raises instead
    of skipping. A domination test that passes on the mutated source is not a
    test.
    """
    assert _dominates(_SYNTHETIC_DOMINATES, "rec") is True
    assert _dominates(_SYNTHETIC_DOES_NOT, "rec") is False


def test_the_observation_state_is_initialialised_outside_the_profile_branch():
    """`_cyc_rec` is read by the path-B blend, which sits under a DIFFERENT
    condition than the one that sets it. A name with no resolved profile never
    enters the setting branch; reading an unbound local there takes the whole
    ticker down rather than skipping the gate.

    Measured structure, which is what the assertion encodes:

        if profile_data:                                    # indent 12
            _cyc_rec = None                                 # indent 16
            _cyc_profile_b = None
            _cyc_values_b = None
            if scenario == "base" and profile_name in _CYCLICAL_PROFILES:
                ...                                         # sets them
        ...
        if profile_data and profile_data.get("methods"):    # indent 12
            blended_iv, _ = _blend_methods(...)
            if scenario == "base" and _cyc_rec is not None and ...:
                _cyc_rec["base_iv_path_a"] = blended_iv     # reads them

    The read's profile guard is strictly STRONGER than the init's, so every path
    to the read passed the init. That implication is the whole safety argument,
    and it is invisible in a diff: moving the initialisation inside the
    observation guard looks like tidying and removes it.
    """
    assert _dominates(inspect.getsource(dcf_agent), "_cyc_rec"), (
        "_cyc_rec is no longer bound on every path that reaches the guard "
        "reading it; a ticker with no resolved profile raises UnboundLocalError "
        "instead of skipping the gate")

    src = inspect.getsource(dcf_agent)
    tree = ast.parse(src)
    lines = src.splitlines()

    def _line_of(stmt: str) -> int:
        return next(i for i, l in enumerate(lines, 1) if l.strip() == stmt)

    init_chain = [_branch_key(n)
                  for n in _enclosing_branches(tree, _line_of("_cyc_rec = None"))]
    for name in ("_cyc_profile_b", "_cyc_values_b"):
        # All three must be initialised together, under the same guards. A
        # fourth state variable added to the record without joining them is the
        # same bug with a different name.
        assert [_branch_key(n)
                for n in _enclosing_branches(tree, _line_of(f"{name} = None"))
                ] == init_chain, name


def test_the_path_b_blend_does_not_publish_its_own_flags():
    """`_blend_methods` MUTATES the flag list it is given (the 80/20 rule).
    Passing the real list would publish path B's Gate A and asset-floor flags as
    though path B had been applied — an observation-only leg editing the
    rationale a reader sees next to a published IV."""
    i = _GATE_SRC.index("_cyc_rec[\"base_iv_path_b\"] = _iv_b")
    head = _GATE_SRC[max(0, i - 900):i]
    assert "_scratch_flags" in head
    assert "forward_flags=_scratch_flags" in head


# ── Owner-specified constants ───────────────────────────────────────────────

def test_the_owner_bounds_are_the_only_new_bounds():
    """Pinned because the standing constraint on this work is that no clamp is
    added to an estimate unless the owner chose it. These four numbers are the
    ones in the plan; anything else in this item is a measurement."""
    assert _PEAK_EPS_MAX_MULTIPLE == 2.0
    assert _PEAK_EPS_SIGMA_MULTIPLE == 2.0
    assert _PB_ROE_MIN_SPREAD == 0.025
    assert _PB_ROE_MIN_PB == 0.20
    assert _PEAK_MIN_OBSERVATIONS_FOR_SIGMA == 4
