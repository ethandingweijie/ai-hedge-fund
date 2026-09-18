"""The scenario-ordering invariant: Bear IV <= Base IV <= Bull IV.

Why this module exists, in one measured paragraph. On BN4.SI the recorded
golden baseline published bear 7.37 against base 5.20 and bull 7.12 — a bear
case ABOVE the base case, on a name trading at S$11.10, which is how a SELL got
a downside target richer than the central one. The cause is not a disordered
input and not a broken cash-flow model. BN4.SI has **no DCF leg in any
scenario** (`weight_dcf` 0.0 and `iv_dcf` None throughout). Two multi legs
vote: the profile's anchor `EV/EBITDA` at 0.533333 and `SOTP (published)` at
0.466667. In bear only, the anchor fails its equity bridge — EV SGD 9.021bn
against net debt SGD 9.325bn plus minority interest SGD 0.322bn is negative
equity, which `_ev_to_equity_ps` floors to a literal `0.0` rather than refusing
— and the blend then drops that zero as a non-positive leg and renormalises
onto the single survivor. The survivor is the HIGHEST of the three legs bear
computed (SOTP 8.37 against Forward P/E 4.21 and Forward EV/EBITDA 0.07), so
bear lands at 7.37. Retaining the zero at its intended weight gives
0.0 x 0.5333 + 8.37 x 0.4667 = 3.906, or 3.44 after the 0.8812 composite:
below base, correctly ordered. The dropout is worth **+3.93** to the bear IV.

The clamp is on the output, not on the blend. Flooring the leg at zero and
keeping its weight was tried and rejected by the owner — "base IV for BN4 and
u96 drop too much. not intuitive" — and `967a3c5` shipped the dropout as a
disclosure instead. So the leg still drops; this stops a dropped leg from
inverting the scenario set, and the gate record names the composition change
that caused it rather than leaving the clamp to look like an unexplained edit
to a published number.

Base is the PIVOT and is never moved. Every test below that touches a number
asserts base is untouched, because an invariant whose enforcement rewrites the
headline number the card leads with is an invariant nobody will trust.

Blast radius, measured one subprocess per fixture across all 14 golden
baselines: exactly ONE violation, BN4.SI, bear > base. No `base > bull`
anywhere. So the bull half of this invariant ships implemented but unexercised
against a real run — `TestTheBullHalf` is the only coverage it has, and it is
synthetic by necessity rather than by choice.

Run:
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
      -m pytest tests/test_scenario_ordering_invariant.py -v
"""
from __future__ import annotations

import copy
import inspect

import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import (
    _SCENARIO_ORDER,
    _enforce_scenario_ordering,
    _voted_legs,
)
from src.memory import golden_replay

_ENGINE_SRC: str | None = None


def _engine_src() -> str:
    global _ENGINE_SRC
    if _ENGINE_SRC is None:
        _ENGINE_SRC = inspect.getsource(dcf_agent)
    return _ENGINE_SRC


def _call_site_block() -> str:
    """The source of the clamp's call site, from the helper call to its record.

    Sliced rather than grepped line-by-line so the assertions below are about
    the block that actually runs, and so a refactor that moves the block whole
    keeps passing while one that splits it fails loudly. The end anchor is
    INCLUDED, so `'"applied": True,'` is part of the slice and can be asserted
    on rather than having to be inferred from the boundary.
    """
    src = _engine_src()
    start = src.index("_order_rec = _enforce_scenario_ordering(scenario_results)")
    anchor = '"applied": True,'
    end = src.index(anchor, start) + len(anchor)
    return src[start:end]


def _code_only(block: str) -> str:
    """The block with comments removed.

    Needed because the call site carries a long comment naming
    `ticker_forward_flags` precisely to say it is NOT used there — the same
    trap that defeated a `not in src` assertion in
    `test_the_reinvestment_gate_is_observation_only_and_says_so`, where five
    occurrences of `sales_to_capital=` existed in the engine and four were
    prose. Unlike that case the block here has no string containing `#`, so
    line-level stripping is sound; it is still applied only to this one slice
    and never to the whole module.
    """
    out = []
    for line in block.splitlines():
        code = line.split("#", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out)


# ── BN4.SI's measured shape ─────────────────────────────────────────────────
# Every number here came out of a golden replay of the BN4_SI fixture and is
# written down in `scratchpad/scen_BN4_SI_after.json`. They are the fixture's
# own values, not an invention: weights to six places, IVs to the two the
# engine rounds to at write time.
_BN4_BEAR = {
    "intrinsic_value": 7.37,
    "iv_multi_post": 7.3734,
    "effective_weights": [
        {"bucket": "multi", "method": "SOTP (published)",
         "value_key": "SOTP (published)", "weight": 1.0},
    ],
    # `methods_used` is built from raw profile rows and on BN4.SI names legs
    # that carried no weight at all. Present here so the tests can pin that
    # the helper does NOT read it.
    "methods_used": ["EV/EBITDA", "SOTP (published)", "DCF"],
    "forward_flags": [],
}
_BN4_BASE = {
    "intrinsic_value": 5.20,
    "iv_multi_post": 5.1997,
    "effective_weights": [
        {"bucket": "multi", "method": "EV/EBITDA",
         "value_key": "EV/EBITDA", "weight": 0.533333},
        {"bucket": "multi", "method": "SOTP (published)",
         "value_key": "SOTP (published)", "weight": 0.466667},
    ],
    "methods_used": ["EV/EBITDA", "SOTP (published)", "DCF"],
    "forward_flags": [],
}
_BN4_BULL = {
    "intrinsic_value": 7.12,
    "iv_multi_post": 7.1194,
    "effective_weights": [
        {"bucket": "multi", "method": "EV/EBITDA",
         "value_key": "EV/EBITDA", "weight": 0.533333},
        {"bucket": "multi", "method": "SOTP (published)",
         "value_key": "SOTP (published)", "weight": 0.466667},
    ],
    "methods_used": ["EV/EBITDA", "SOTP (published)", "DCF"],
    "forward_flags": [],
}


def _bn4_triple() -> dict[str, dict]:
    return {"bear": copy.deepcopy(_BN4_BEAR),
            "base": copy.deepcopy(_BN4_BASE),
            "bull": copy.deepcopy(_BN4_BULL)}


class TestTheMeasuredInversion:
    """BN4.SI's recorded baseline, replayed against the pure helper."""

    def test_bear_above_base_is_detected_and_clamped_onto_base(self):
        rec = _enforce_scenario_ordering(_bn4_triple())
        assert rec is not None
        assert rec["clamped"] == [{"scenario": "bear", "before": 7.37,
                                   "after": 5.2}], rec["clamped"]

    def test_base_is_the_pivot_and_is_never_in_the_clamped_list(self):
        rec = _enforce_scenario_ordering(_bn4_triple())
        assert rec["pivot"] == "base"
        assert rec["base_iv"] == 5.2
        assert [c["scenario"] for c in rec["clamped"]] == ["bear"]
        assert "base" not in [c["scenario"] for c in rec["clamped"]]

    def test_bull_at_7_12_is_above_base_and_is_left_alone(self):
        """The recorded BN4.SI triple violates on ONE side only.

        Bull 7.12 > base 5.20, so it is correctly ordered and must not appear
        in the record. Pinning this separately from the bear clamp is what
        stops a "clamp everything onto base" implementation from passing.
        """
        rec = _enforce_scenario_ordering(_bn4_triple())
        assert {c["scenario"] for c in rec["clamped"]} == {"bear"}

    def test_the_flag_string_is_byte_identical_to_the_owner_specification(self):
        """The owner specified this string verbatim. Not paraphrased, not reflowed.

        `INVARIANT_VIOLATION_SCENARIO_INVERSION: Bear ({bear_iv:.2f}) > Base
        ({base_iv:.2f}); clamped to Base`
        """
        rec = _enforce_scenario_ordering(_bn4_triple())
        assert rec["flags"] == [
            "⚠ INVARIANT_VIOLATION_SCENARIO_INVERSION: "
            "Bear (7.37) > Base (5.20); clamped to Base"
        ], rec["flags"]

    def test_the_two_decimal_format_is_applied_not_the_repr(self):
        """`:.2f` on both numbers, so 5.2 renders as `5.20` and not `5.2`."""
        rec = _enforce_scenario_ordering(_bn4_triple())
        assert "(5.20)" in rec["flags"][0]
        assert "(5.2)" not in rec["flags"][0].replace("(5.20)", "")

    def test_the_composition_record_names_the_dropped_anchor_not_a_dcf_leg(self):
        """The owner's step 1, answered with data rather than with prose.

        The narrative that authorised this fix said a DCF leg had zeroed out
        and handed the bear valuation to trailing multiples. Measured, there is
        no DCF leg at all. What dropped is `EV/EBITDA` — the profile's ANCHOR —
        and it dropped inside the multi bucket. This test is what keeps the
        shipped record telling the truth instead of the story.
        """
        comp = _enforce_scenario_ordering(_bn4_triple())["composition"]
        assert comp["bear"] == {
            "legs_voted": ["SOTP (published)"],
            "n_legs": 1,
            "single_method": True,
            "legs_lost_vs_base": ["EV/EBITDA"],
            "weight_lost_vs_base": 0.533333,
        }, comp["bear"]

    def test_base_and_bull_report_no_loss_against_base(self):
        comp = _enforce_scenario_ordering(_bn4_triple())["composition"]
        for name in ("base", "bull"):
            assert comp[name]["legs_lost_vs_base"] == [], comp[name]
            assert comp[name]["weight_lost_vs_base"] == 0.0, comp[name]
            assert comp[name]["n_legs"] == 2, comp[name]
            assert comp[name]["single_method"] is False, comp[name]

    def test_the_composition_covers_all_three_scenarios_even_the_ordered_ones(self):
        """Bull did not violate and still gets a composition row.

        The diagnostic is meant to distinguish "a leg dropped" from "the inputs
        were genuinely disordered", and it cannot do that if it only describes
        the scenario that tripped the invariant — there would be nothing to
        compare against.
        """
        comp = _enforce_scenario_ordering(_bn4_triple())["composition"]
        assert sorted(comp) == ["base", "bear", "bull"]

    def test_a_dropout_driven_inversion_is_distinguishable_from_disordered_inputs(self):
        """The claim in the docstring, discharged rather than asserted.

        Same IVs, same violation — but no leg lost. `weight_lost_vs_base` is
        zero, which says "look upstream at the inputs", not "a leg dropped".
        """
        triple = _bn4_triple()
        triple["bear"]["effective_weights"] = copy.deepcopy(
            triple["base"]["effective_weights"])
        rec = _enforce_scenario_ordering(triple)
        assert rec["clamped"][0]["before"] == 7.37          # still inverted
        assert rec["composition"]["bear"]["legs_lost_vs_base"] == []
        assert rec["composition"]["bear"]["weight_lost_vs_base"] == 0.0
        assert rec["composition"]["bear"]["single_method"] is False


class TestOrderedInputsAreLeftAlone:
    """The invariant must be silent on 13 of the 14 recorded baselines."""

    def test_an_ordered_triple_returns_none(self):
        ordered = {
            "bear": {"intrinsic_value": 156.17, "effective_weights": []},
            "base": {"intrinsic_value": 219.80, "effective_weights": []},
            "bull": {"intrinsic_value": 296.35, "effective_weights": []},
        }
        assert _enforce_scenario_ordering(ordered) is None

    def test_every_recorded_fixture_triple_returns_none(self):
        """The 13 correctly-ordered baselines, measured one subprocess each.

        Bear / base / bull as replayed from `tests/golden/snapshots.json`. If
        this list ever needs a fourteenth entry the invariant has started
        firing somewhere new, and that is the signal to look before shipping.
        """
        ordered = {
            "02888_HK": (236.10, 284.38, 331.93),
            "09988_HK": (114.32, 160.84, 213.42),
            "AAPL":     (156.17, 219.80, 296.35),
            "BABA":     (142.27, 193.13, 247.76),
            "C38U_SI":  (1.41, 1.81, 2.27),
            "COST":     (956.33, 1324.57, 1711.50),
            "D05_SI":   (39.21, 47.89, 56.56),
            "FCX":      (16.90, 26.69, 46.92),
            "MELI":     (4940.12, 5578.42, 6131.13),
            "MU":       (222.82, 293.14, 365.72),
            "SCHW":     (51.21, 75.41, 108.18),
            "U96_SI":   (2.33, 5.73, 9.13),
            "V":        (310.93, 428.47, 539.81),
        }
        for name, (b, m, u) in ordered.items():
            assert b <= m <= u, f"{name} fixture triple is not monotonic"
            triple = {
                "bear": {"intrinsic_value": b, "effective_weights": []},
                "base": {"intrinsic_value": m, "effective_weights": []},
                "bull": {"intrinsic_value": u, "effective_weights": []},
            }
            assert _enforce_scenario_ordering(triple) is None, name

    def test_ties_are_not_violations(self):
        """`bear == base` and `bull == base` are ordered.

        The comparison is strict (`>` and `<`), so a tie passes. This matters
        after the clamp fires: bear is set TO base, and a second pass over the
        same triple must be a no-op rather than re-flagging forever.
        """
        tied = {
            "bear": {"intrinsic_value": 5.20, "effective_weights": []},
            "base": {"intrinsic_value": 5.20, "effective_weights": []},
            "bull": {"intrinsic_value": 5.20, "effective_weights": []},
        }
        assert _enforce_scenario_ordering(tied) is None

    def test_the_clamp_is_idempotent(self):
        """Applying the record and re-running finds nothing.

        A clamp that is not idempotent would flag on every re-entry, and the
        flag is a user-visible operational warning.
        """
        triple = _bn4_triple()
        rec = _enforce_scenario_ordering(triple)
        for cl in rec["clamped"]:
            triple[cl["scenario"]]["intrinsic_value"] = cl["after"]
        assert _enforce_scenario_ordering(triple) is None

    def test_a_single_leg_in_every_scenario_is_not_itself_a_violation(self):
        """`single_method` is a diagnostic, never a trigger.

        Thirteen of the fourteen baselines vote on more than one leg, but a
        one-leg profile is legitimate — `single_method: True` describes the
        composition, it does not condemn it.
        """
        one = {
            "bear": {"intrinsic_value": 10.0,
                     "effective_weights": [{"method": "P/E", "weight": 1.0}]},
            "base": {"intrinsic_value": 12.0,
                     "effective_weights": [{"method": "P/E", "weight": 1.0}]},
            "bull": {"intrinsic_value": 14.0,
                     "effective_weights": [{"method": "P/E", "weight": 1.0}]},
        }
        assert _enforce_scenario_ordering(one) is None


class TestFailClosed:
    """No pivot, no clamp. A missing number is not an inversion."""

    @pytest.mark.parametrize("triple", [
        {},
        {"bear": {"intrinsic_value": 7.37}, "bull": {"intrinsic_value": 7.12}},
        {"base": {}},
        {"base": {"intrinsic_value": None}, "bear": {"intrinsic_value": 7.37}},
        {"base": {"intrinsic_value": "5.20"}, "bear": {"intrinsic_value": 7.37}},
        {"base": {"intrinsic_value": float("nan")},
         "bear": {"intrinsic_value": 7.37}},
    ], ids=["empty", "no-base-key", "base-empty", "base-none",
            "base-string", "base-nan"])
    def test_a_missing_or_non_numeric_base_refuses(self, triple):
        """No pivot, no clamp.

        Four of these six are refused by the `isinstance` guard at the top. The
        `nan` case is NOT, and the distinction is worth stating rather than
        letting one parametrisation hide it: `isinstance(nan, float)` is True,
        so `nan` passes the guard, builds the composition record, and then
        produces no clamp because every comparison against `nan` is False. Same
        observable answer — `None` — reached by a different route, and the
        route matters because it means a `nan` base leaves its own `nan` in
        `scenario_results` visible downstream instead of being papered over by
        a clamp. `test_a_nan_base_is_refused_by_comparison_not_by_the_type_guard`
        pins that separately so the two mechanisms cannot be confused.
        """
        assert _enforce_scenario_ordering(triple) is None

    def test_a_nan_base_is_refused_by_comparison_not_by_the_type_guard(self):
        """The mechanism, not just the answer.

        A `nan` base clears the type guard, so the composition diagnostic is
        still built — it just never finds a violation, because `7.37 > nan` is
        False. If somebody later "optimises" the guard to `base_iv is not None`
        the behaviour here is unchanged, which is the point: the invariant does
        not depend on the guard catching `nan`.
        """
        triple = {"base": {"intrinsic_value": float("nan"),
                           "effective_weights": []},
                  "bear": {"intrinsic_value": 7.37, "effective_weights": []}}
        assert isinstance(float("nan"), float)          # the guard cannot catch it
        assert not (7.37 > float("nan"))                # the comparison can
        assert _enforce_scenario_ordering(triple) is None

    def test_a_boolean_base_is_refused_even_though_bool_is_an_int_subclass(self):
        """`isinstance(True, int)` is True in Python. The guard says so explicitly."""
        assert _enforce_scenario_ordering(
            {"base": {"intrinsic_value": True},
             "bear": {"intrinsic_value": 7.37}}) is None

    def test_a_missing_outer_scenario_is_not_a_violation(self):
        """A scenario that never computed cannot be out of order.

        Base alone, with bear absent entirely: nothing to clamp, nothing to
        report. Treating absence as an inversion would flag every partial run.
        """
        assert _enforce_scenario_ordering(
            {"base": {"intrinsic_value": 5.20, "effective_weights": []}}) is None

    @pytest.mark.parametrize("bad", [None, "7.37", float("nan"), True])
    def test_a_non_numeric_outer_scenario_is_skipped_not_clamped(self, bad):
        triple = {
            "bear": {"intrinsic_value": bad, "effective_weights": []},
            "base": {"intrinsic_value": 5.20, "effective_weights": []},
            "bull": {"intrinsic_value": 7.12, "effective_weights": []},
        }
        assert _enforce_scenario_ordering(triple) is None

    def test_a_none_scenario_value_is_treated_as_absent(self):
        """`scenario_results.get(name)` can legitimately be None mid-build."""
        assert _enforce_scenario_ordering(
            {"bear": None, "base": {"intrinsic_value": 5.20}, "bull": None}) is None


class TestTheBullHalf:
    """Implemented by symmetry, unexercised by all 14 recorded baselines.

    No fixture has `base > bull`. These tests are the only coverage that half
    has, which is worth stating plainly rather than letting a green suite imply
    it was observed in production.
    """

    def test_bull_below_base_is_clamped_up_onto_base(self):
        triple = {
            "bear": {"intrinsic_value": 4.0, "effective_weights": []},
            "base": {"intrinsic_value": 5.20, "effective_weights": []},
            "bull": {"intrinsic_value": 5.00, "effective_weights": []},
        }
        rec = _enforce_scenario_ordering(triple)
        assert rec["clamped"] == [{"scenario": "bull", "before": 5.0,
                                   "after": 5.2}], rec["clamped"]

    def test_the_bull_flag_says_bull_and_uses_the_less_than_operator(self):
        triple = {
            "bear": {"intrinsic_value": 4.0, "effective_weights": []},
            "base": {"intrinsic_value": 5.20, "effective_weights": []},
            "bull": {"intrinsic_value": 5.00, "effective_weights": []},
        }
        assert _enforce_scenario_ordering(triple)["flags"] == [
            "⚠ INVARIANT_VIOLATION_SCENARIO_INVERSION: "
            "Bull (5.00) < Base (5.20); clamped to Base"
        ]

    def test_clamping_bull_up_does_not_move_base(self):
        """Raising a bull case to base says "no upside modelled".

        It does not invent value and it does not touch the headline number —
        which is the only reading of the invariant that leaves base intact on
        both sides.
        """
        triple = {
            "bear": {"intrinsic_value": 4.0, "effective_weights": []},
            "base": {"intrinsic_value": 5.20, "effective_weights": []},
            "bull": {"intrinsic_value": 5.00, "effective_weights": []},
        }
        rec = _enforce_scenario_ordering(triple)
        assert rec["base_iv"] == 5.20
        assert all(c["after"] == 5.20 for c in rec["clamped"])
        assert "base" not in {c["scenario"] for c in rec["clamped"]}

    def test_both_directions_at_once_are_reported_in_bear_then_bull_order(self):
        triple = {
            "bear": {"intrinsic_value": 9.0, "effective_weights": []},
            "base": {"intrinsic_value": 5.20, "effective_weights": []},
            "bull": {"intrinsic_value": 1.0, "effective_weights": []},
        }
        rec = _enforce_scenario_ordering(triple)
        assert [c["scenario"] for c in rec["clamped"]] == ["bear", "bull"]
        assert len(rec["flags"]) == 2
        assert rec["flags"][0].startswith("⚠ INVARIANT_VIOLATION_SCENARIO_INVERSION: Bear")
        assert rec["flags"][1].startswith("⚠ INVARIANT_VIOLATION_SCENARIO_INVERSION: Bull")


class TestTheHelperIsPure:
    """The caller applies the record. The helper only reads."""

    def test_the_helper_does_not_mutate_its_input(self):
        """Purity is what makes this testable without an engine run.

        A scenario triple is the whole input, so if the helper also wrote to it
        the tests above would be order-dependent and the call site would have
        two writers of `intrinsic_value` with no way to tell which won.
        """
        triple = _bn4_triple()
        before = copy.deepcopy(triple)
        _enforce_scenario_ordering(triple)
        assert triple == before

    def test_the_returned_record_shares_no_mutable_state_with_the_input(self):
        """Mutating the record must not reach the scenario dicts."""
        triple = _bn4_triple()
        rec = _enforce_scenario_ordering(triple)
        rec["composition"]["bear"]["legs_voted"].append("INJECTED")
        rec["clamped"][0]["after"] = -1.0
        rec["flags"].append("INJECTED")
        assert triple == {"bear": copy.deepcopy(_BN4_BEAR),
                          "base": copy.deepcopy(_BN4_BASE),
                          "bull": copy.deepcopy(_BN4_BULL)}

    def test_voted_legs_does_not_mutate_the_scenario(self):
        scen = copy.deepcopy(_BN4_BASE)
        before = copy.deepcopy(scen)
        _voted_legs(scen)
        assert scen == before


class TestVotedLegs:
    """`effective_weights` is the blend's own record of what voted."""

    def test_methods_used_is_not_consulted(self):
        """The reason `_voted_legs` exists.

        `methods_used` is built from raw profile rows, so on BN4.SI it names
        `'DCF'` in base and `'EV/EBITDA'` in bear while neither carried any
        weight at all. Reading it would report `n_legs == 3` and
        `legs_lost_vs_base == []` on the exact fixture this invariant was
        written for — a clean bill of health on a composition that inverted the
        scenario set.
        """
        assert _voted_legs(_BN4_BEAR) == {"SOTP (published)": 1.0}
        assert "DCF" not in _voted_legs(_BN4_BEAR)
        assert "EV/EBITDA" not in _voted_legs(_BN4_BEAR)
        assert _BN4_BEAR["methods_used"] == ["EV/EBITDA", "SOTP (published)", "DCF"]

    def test_base_reports_both_legs_with_their_measured_weights(self):
        assert _voted_legs(_BN4_BASE) == {"EV/EBITDA": 0.533333,
                                          "SOTP (published)": 0.466667}

    def test_a_gained_leg_is_not_reported_as_lost(self):
        """`legs_lost_vs_base` is a set difference in ONE direction only.

        A scenario that votes on a leg base did not has gained composition, and
        reporting that as "lost" would make the diagnostic useless — it is meant
        to answer "did this scenario lose the anchor", not "did the two differ".
        """
        triple = {
            "bear": {"intrinsic_value": 4.0,
                     "effective_weights": [{"method": "P/E", "weight": 0.5},
                                           {"method": "EV/EBITDA", "weight": 0.5}]},
            "base": {"intrinsic_value": 5.0,
                     "effective_weights": [{"method": "EV/EBITDA", "weight": 1.0}]},
            "bull": {"intrinsic_value": 6.0, "effective_weights": []},
        }
        comp = _enforce_scenario_ordering(triple)
        assert comp is None                       # ordered: no record at all

    def test_a_gain_is_visible_through_legs_voted_even_though_not_as_a_loss(self):
        triple = {
            "bear": {"intrinsic_value": 9.0,
                     "effective_weights": [{"method": "P/E", "weight": 0.5},
                                           {"method": "EV/EBITDA", "weight": 0.5}]},
            "base": {"intrinsic_value": 5.0,
                     "effective_weights": [{"method": "EV/EBITDA", "weight": 1.0}]},
            "bull": {"intrinsic_value": 6.0, "effective_weights": []},
        }
        comp = _enforce_scenario_ordering(triple)["composition"]
        assert comp["bear"]["legs_lost_vs_base"] == []
        assert comp["bear"]["legs_voted"] == ["EV/EBITDA", "P/E"]
        assert comp["bear"]["n_legs"] == 2

    @pytest.mark.parametrize("rows,expected", [
        (None, {}),
        ([], {}),
        ("not a list", {}),
        ([{"weight": 0.5}], {}),                     # no method name
        ([{"method": "", "weight": 0.5}], {}),        # falsy method name
        ([{"method": "P/E"}], {"P/E": 0.0}),          # no weight
        ([{"method": "P/E", "weight": None}], {"P/E": 0.0}),
        ([{"method": "P/E", "weight": "x"}], {"P/E": 0.0}),
        (["P/E", {"method": "EV/EBITDA", "weight": 1.0}], {"EV/EBITDA": 1.0}),
    ], ids=["none", "empty", "wrong-type", "no-method", "empty-method",
            "no-weight", "none-weight", "unparseable-weight", "non-dict-row"])
    def test_malformed_rows_degrade_to_an_empty_or_zero_entry(self, rows, expected):
        """Fail soft on the diagnostic, never raise inside the valuation path.

        `_voted_legs` only feeds a record. If a malformed row from an upstream
        refactor could raise here, the invariant check would take the whole
        scenario build down with it — and the composition diagnostic is worth
        considerably less than the IV.
        """
        assert _voted_legs({"effective_weights": rows}) == expected

    def test_a_missing_scenario_key_gives_an_empty_map(self):
        assert _voted_legs(None) == {}
        assert _voted_legs({}) == {}

    def test_a_duplicate_method_name_keeps_the_last_row(self):
        """Documented behaviour, not a claim that it is correct.

        The blend does not emit two rows for one method, so this cannot arise
        from a real run; pinning it anyway means a future change that starts
        emitting duplicates fails a test rather than silently summing.
        """
        assert _voted_legs({"effective_weights": [
            {"method": "P/E", "weight": 0.3},
            {"method": "P/E", "weight": 0.7},
        ]}) == {"P/E": 0.7}


class TestTheCallSite:
    """Pinned from source, because the helper being right is not enough.

    A pure helper with a correct record still does nothing if the caller writes
    the flag to a list nobody reads, or clamps before something else has
    already read the unclamped IV. Both of those failures exist in this file's
    neighbourhood: the `[12m-pt]` ordering diagnostic ~370 lines below appends
    to `ticker_forward_flags`, a list documented 60 lines above IT as reaching
    no payload at all — so the engine detected this very inversion on BN4.SI
    for an unknown number of runs and told nobody.
    """

    def test_the_clamp_runs_after_the_scenario_loop_and_before_the_first_iv_reader(self):
        src = _engine_src()
        clamp = src.index("_order_rec = _enforce_scenario_ordering(scenario_results)")
        unanimous = src.index("_unanimous = len(_scen_ivs) == 3")
        base_reader = src.index('base_iv = scenario_results["base"]["intrinsic_value"]')
        assert clamp < unanimous < base_reader, (clamp, unanimous, base_reader)

    def test_base_is_never_written_by_the_call_site(self):
        """The pivot is read, never assigned.

        Asserted on the block rather than the whole file: `scenario_results`
        is written once, at the end of the scenario loop, and the clamp must
        not add a second writer for base.
        """
        code = _code_only(_call_site_block())
        assert 'scenario_results["base"]["intrinsic_value"] =' not in code
        assert '_scen_cl["intrinsic_value"] = _cl["after"]' in code
        # base can only be reached through the `clamped` list, which by
        # construction holds bear and bull only.
        assert '("bear", lambda a, b: a > b), ("bull", lambda a, b: a < b)' in _engine_src()

    def test_the_unclamped_value_is_preserved_beside_the_clamp(self):
        block = _call_site_block()
        assert '_scen_cl["intrinsic_value_unclamped"] = _cl["before"]' in block
        # ...and the blend's own arithmetic is not rewritten to match. Checked
        # on the comment-stripped block, because the comment above this very
        # assertion names all three.
        code = _code_only(block)
        for preserved in ("iv_multi", "iv_multi_post",
                          "intrinsic_value_pre_composite"):
            assert f'"{preserved}"' not in code, preserved

    def test_the_flag_reaches_all_three_scenarios_not_the_dead_ticker_list(self):
        block = _call_site_block()
        assert "for _sn_fl in _SCENARIO_ORDER:" in block
        assert '_sf_fl.extend(_order_rec["flags"])' in block
        # Comment-stripped: the block names `ticker_forward_flags` in a comment
        # whose whole purpose is to say it is NOT used here.
        assert "ticker_forward_flags" not in _code_only(block)

    def test_the_composition_is_attached_to_the_scenario_that_moved(self):
        block = _call_site_block()
        assert '_scen_cl["ordering_composition"] = (' in block
        assert '_order_rec["composition"].get(_sn_cl)' in block

    def test_the_gate_record_is_live_and_carries_the_diagnostic(self):
        block = _call_site_block()
        assert '"gate_id": "GATE_SCENARIO_ORDERING"' in block
        assert '"metric": "scenario_iv_ordering"' in block
        assert '"applied": True,' in block
        assert '"composition": _order_rec["composition"]' in block
        assert '"clamped": _order_rec["clamped"]' in block
        assert '"pivot": _order_rec["pivot"]' in block

    def test_the_gate_record_is_appended_only_when_something_moved(self):
        """`if _order_rec:` guards the whole block, so 13 of 14 fixtures emit nothing.

        A gate that records on every run would put `scenario_iv_ordering` in
        `gate_metrics` for every fixture and the golden diff would name 14
        moves instead of 1.
        """
        src = _engine_src()
        i = src.index("_order_rec = _enforce_scenario_ordering(scenario_results)")
        assert src[i:i + 400].splitlines()[1].strip() == "if _order_rec:"

    def test_the_scenario_order_tuple_is_the_three_names_in_risk_order(self):
        assert _SCENARIO_ORDER == ("bear", "base", "bull")


class TestTheGoldenProjection:
    """Two new keys are pinned; five existing ones are deliberately not."""

    def test_the_two_new_scenario_keys_are_in_the_projection(self):
        """Without `intrinsic_value_unclamped` a clamped 5.20 and a computed
        5.20 are the same leaf, and the baseline could not tell them apart.
        `ordering_composition` freezes the owner's step-1 diagnostic as data.
        """
        keys = golden_replay._SCENARIO_KEYS
        assert "intrinsic_value_unclamped" in keys
        assert "ordering_composition" in keys

    def test_the_five_blend_disclosure_keys_are_deliberately_not_pinned(self):
        """An open decision, written down where the next reader will find it.

        `967a3c5` added `legs_dropped`, `weight_surviving`, `weight_intended`,
        `methods_surviving` and `single_method` to every scenario of every
        fixture. Pinning them is a genuine improvement and it is NOT done here,
        because it would add ~70 leaves across the 14 baselines and make the
        named-move set for THIS change unreadable. Recorded in a comment in
        `golden_replay.py` and pinned as an absence so the omission is a choice
        somebody has to reverse on purpose.
        """
        keys = set(golden_replay._SCENARIO_KEYS)
        for disclosure in ("legs_dropped", "weight_surviving", "weight_intended",
                           "methods_surviving"):
            assert disclosure not in keys, disclosure

    def test_a_conditional_key_is_safe_to_add_to_the_projection(self):
        """`project` copies only keys present in the block, so a key that
        exists on one scenario of one fixture does not force the other 41 to
        grow a null. Pinned because the whole re-baseline rests on it.
        """
        src = inspect.getsource(golden_replay)
        assert "{k: block[k] for k in _SCENARIO_KEYS if k in block}" in src
