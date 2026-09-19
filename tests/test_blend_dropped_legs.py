"""The blend's dropped legs are disclosed, distinguished, and never zero-filled.

`_blend_methods` walks the profile's method table and skips any leg that did not
produce a usable value. That skip used to be one bare `continue` covering two
events that are not the same event:

    if value is None or value <= 0:
        continue

`value is None` — nothing computed. There is no opinion to average in, so
dropping the leg and renormalising over the survivors is the right arithmetic.

`value <= 0` — the method DID compute and returned a non-positive equity value.
That is an opinion, and the most bearish one in the set. Dropping it hands its
weight to the more optimistic survivors, which is the mechanism by which the
growth-reinvestment charge produced a HIGHER valuation from a more conservative
input: 09988_HK 160.84 → 188.82 (+17.40%), `weight_dcf` 0.2778 → 0.0, `iv_dcf`
→ None, `methods_used` loses DCF.

Measured on the shipped baseline the non-positive case is not hypothetical — it
fires on 6 legs across 3 of the 14 golden fixtures (BN4_SI ×3, MU ×1, U96_SI ×2),
every one of them DCF except a single `EV/EBITDA` at exactly 0.0.

THE FIX THAT WAS MEASURED AND REJECTED. Flooring a computed non-positive leg at
0.0 and keeping its weight moves 2 of 14, both down: BN4_SI base 5.20 → 3.90
(−25.000%) and U96_SI base 5.73 → 3.52 (−38.569%). Each move is exactly the
dropped leg's profile weight, because a zero at weight w pulls a weighted mean
down by w. So the magnitude comes from the weight table and not from anything
the method computed — a DCF of −1.318 and one of −15.630 are treated
identically, and the information in the value is discarded by saturation just as
completely as by omission. On U96_SI it would turn one surviving leg of 5.7269
into "60% EV/EBITDA + 40% fabricated zero". Both names are also in
`_LOOKTHROUGH_PROMOTE`, added because they "are valued by the street as a sum of
parts" and their templates "previewed at +10% to price" — a negative
CONSOLIDATED DCF on a conglomerate whose value sits in listed stakes is a
statement about the model's reach, not about the equity. Limited liability makes
0 a FLOOR on true value, not an estimate of it.

So the arithmetic is unchanged and the silence is removed. `test_the_rejected_
zero_fill_does_not_move_the_number` is the pin on that decision: two very
different negative DCFs must produce the same IV, and must still be recorded
separately.
"""
from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import _blend_methods

# The BN4_SI / U96_SI shape: a scenario-insensitive published SOTP, a multiple,
# and a DCF. Weights are the ones those profiles actually carry, so the
# measured percentages in the docstrings above are reproducible from this table.
METHODS = [
    {"name": "SOTP (published)", "weight": 0.35, "implementable": True},
    {"name": "EV/EBITDA",        "weight": 0.40, "implementable": True},
    {"name": "DCF",              "weight": 0.25, "implementable": True},
]

SOTP = 11.1562
EVE = 1.3016


def _blend(method_values, *, tv=0.0):
    """Run the blend and return (iv, breakdown, flags)."""
    flags: list[str] = []
    iv, bd = _blend_methods(
        profile_methods=METHODS,
        method_values=method_values,
        c_macro=0.0,
        forward_flags=flags,
        dcf_tv_fraction=tv,
    )
    return iv, bd, flags


def _only(bd, method):
    rows = [d for d in (bd.get("legs_dropped") or []) if d["method"] == method]
    assert len(rows) == 1, f"expected exactly one dropped {method}: {bd.get('legs_dropped')}"
    return rows[0]


# ── The two drop reasons are different events and are now told apart ─────────


class TestDropReasonsAreDistinguished:
    def test_a_leg_that_computed_negative_is_recorded_as_non_positive(self):
        _, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                           "DCF": -1.318})
        row = _only(bd, "DCF")
        assert row["reason"] == "non_positive"
        assert row["value"] == pytest.approx(-1.318)
        assert row["weight"] == pytest.approx(0.25)

    def test_a_leg_that_computed_nothing_is_recorded_as_uncomputable(self):
        _, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                           "DCF": None})
        row = _only(bd, "DCF")
        assert row["reason"] == "uncomputable"
        assert row["value"] is None
        assert row["weight"] == pytest.approx(0.25)

    def test_an_absent_leg_is_also_uncomputable(self):
        """A key missing from `method_values` and a key present as None are the
        same event to the blend, so they must be the same event in the record.
        Distinguishing them would imply the engine knows something it does not."""
        _, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE})
        assert _only(bd, "DCF")["reason"] == "uncomputable"

    def test_exactly_zero_is_non_positive_not_uncomputable(self):
        """The `EV/EBITDA` case from BN4_SI's bear scenario: a multiple whose
        EBITDA is negative emits a literal 0.0, which is not None, so it takes
        this branch. 0 is a computed answer and is recorded as one."""
        _, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": 0.0,
                           "DCF": 5.0})
        row = _only(bd, "EV/EBITDA")
        assert row["reason"] == "non_positive"
        assert row["value"] == 0.0

    def test_the_proxy_is_named_when_the_drop_went_through_one(self):
        methods = [{"name": "SOTP / NAV", "weight": 0.9,
                    "implementable": False, "proxy": "P/BV"}]
        flags: list[str] = []
        _, bd = _blend_methods(
            profile_methods=methods,
            method_values={"P/BV": 0.0},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.0)
        row = _only(bd, "SOTP / NAV")
        assert row["proxy"] == "P/BV"
        assert row["reason"] == "non_positive"
        assert row["value"] == 0.0

    def test_no_proxy_key_when_the_real_method_was_used(self):
        _, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                           "DCF": -1.0})
        assert _only(bd, "DCF")["proxy"] is None


# ── The rejected zero-fill is pinned as rejected ────────────────────────────


class TestTheRejectedZeroFillDoesNotMoveTheNumber:
    """The whole design decision in two assertions.

    The magnitude of a non-positive leg does NOT enter the arithmetic — so a
    DCF of −1.318 and a DCF of −15.630 produce the same IV — but it IS
    recorded, so the two are still distinguishable downstream. That pairing is
    what the zero-fill could not do: it discarded the value AND moved the
    number, by an amount set entirely by the weight table.
    """

    def test_two_very_different_negatives_give_the_same_iv_but_different_records(self):
        iv_a, bd_a, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                                "DCF": -1.318})
        iv_b, bd_b, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                                "DCF": -15.630})
        assert iv_a == pytest.approx(iv_b)
        assert _only(bd_a, "DCF")["value"] != _only(bd_b, "DCF")["value"]

    def test_the_iv_is_the_renormalised_survivor_mean_with_no_zero_term(self):
        """The dropped weight leaves the denominator too.

        With the zero-fill in effect this same input would give
        (0.35·11.1562 + 0.40·1.3016 + 0.25·0.0) / 1.00 = 4.4253 — a 25% cut,
        which is exactly the DCF's profile weight and exactly the BN4_SI move
        that was measured and rejected.
        """
        iv, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                            "DCF": -1.318})
        assert iv == pytest.approx((0.35 * SOTP + 0.40 * EVE) / 0.75)
        assert iv == pytest.approx(5.9004, abs=5e-4)
        assert iv != pytest.approx(4.4253, abs=5e-4)   # the rejected zero-fill
        assert bd["iv_dcf"] is None
        assert bd["weight_dcf"] == 0.0
        assert bd["weight_multi"] == pytest.approx(1.0)

    def test_a_single_survivor_iv_is_that_legs_value_verbatim(self):
        """U96_SI's actual shape, and the reason the zero-fill was rejected:
        SOTP cannot be computed, DCF came out negative, and the published base
        IV of 5.7269 is the `EV/EBITDA` leg value to four decimals. A "blend"
        that is one multiple does not become more honest by averaging in a zero
        the DCF never produced — it becomes a number neither input supports."""
        iv, bd, _ = _blend({"SOTP (published)": None, "EV/EBITDA": 5.7269,
                            "DCF": -5.0166})
        assert iv == pytest.approx(5.7269)
        assert bd["single_method"] is True
        assert bd["weight_surviving"] == pytest.approx(0.40)

    def test_a_negative_leg_leaves_the_number_exactly_where_the_drop_left_it(self):
        """The pre-fix and post-fix IV are the same value. This is the pin that
        the disclosure changed nothing numeric: recompute the blend by hand from
        the profile table and require it to match to the last digit."""
        iv, _, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                           "DCF": -1.318})
        hand = (0.35 * SOTP + 0.40 * EVE) / (0.35 + 0.40)
        assert iv == pytest.approx(hand, rel=0, abs=1e-12)


# ── weight_surviving ────────────────────────────────────────────────────────


class TestWeightSurviving:
    def test_all_legs_voting_reports_one(self):
        _, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                           "DCF": 5.0})
        assert bd["weight_surviving"] == pytest.approx(1.0)
        assert bd["weight_intended"] == pytest.approx(1.0)
        assert bd["legs_dropped"] == []

    def test_a_dropped_quarter_reports_three_quarters(self):
        """The −25.000% the zero-fill would have moved BN4_SI by, expressed as
        the weight that left the blend instead of as a price change."""
        _, bd, _ = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                           "DCF": -1.318})
        assert bd["weight_surviving"] == pytest.approx(0.75)

    def test_two_of_three_dropped_reports_the_surviving_share(self):
        """U96_SI's shape: 0.40 of 1.00 survives, and the published IV is the
        EV/EBITDA leg value itself."""
        iv, bd, _ = _blend({"SOTP (published)": None, "EV/EBITDA": 5.7269,
                            "DCF": -5.0166})
        assert bd["weight_surviving"] == pytest.approx(0.40)
        assert iv == pytest.approx(5.7269)

    def test_gate_a_that_finds_a_floor_does_not_report_lost_weight(self):
        """Gate A moves weight between buckets; nothing left the blend, so the
        ratio must still read 1.0 even though the DCF leg's own weight fell."""
        methods = [{"name": "DCF", "weight": 0.5, "implementable": True},
                   {"name": "EV/EBITDA", "weight": 0.5, "implementable": True}]
        flags: list[str] = []
        _, bd = _blend_methods(
            profile_methods=methods,
            method_values={"DCF": 10.0, "EV/EBITDA": 20.0, "P/BV": 4.0},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.90)
        assert bd["weight_surviving"] == pytest.approx(1.0)
        assert bd["methods_surviving"] == 3     # incl. the P/BV asset floor
        assert any("80/20 Rule" in f for f in flags)

    def test_gate_a_with_no_floor_available_reports_the_weight_it_destroyed(self):
        """The one place in this function where weight is destroyed rather than
        moved: Gate A takes 20% off the DCF, P/BV is unavailable, and the share
        goes nowhere. Previously silent; now the ratio says 0.90."""
        methods = [{"name": "DCF", "weight": 0.5, "implementable": True},
                   {"name": "EV/EBITDA", "weight": 0.5, "implementable": True}]
        flags: list[str] = []
        _, bd = _blend_methods(
            profile_methods=methods,
            method_values={"DCF": 10.0, "EV/EBITDA": 20.0},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.90)
        assert bd["weight_surviving"] == pytest.approx(0.90)
        assert bd["methods_surviving"] == 2


# ── The single-survivor case ────────────────────────────────────────────────


class TestSingleSurvivorIsFlagged:
    def test_one_voting_leg_is_flagged_and_named(self):
        iv, bd, flags = _blend({"SOTP (published)": None, "EV/EBITDA": 5.7269,
                                "DCF": -5.0166})
        assert bd["single_method"] is True
        assert bd["methods_surviving"] == 1
        sm = [f for f in flags if f.startswith("Single-method blend")]
        assert len(sm) == 1
        assert "EV/EBITDA" in sm[0]
        assert "40%" in sm[0]           # surviving share, stated in the prose
        assert iv == pytest.approx(5.7269)

    def test_two_voting_legs_are_not_flagged(self):
        """Threshold-free by construction: the test is `len(parts) == 1`, so
        there is no constant here to tune, and two legs is not one."""
        _, bd, flags = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                               "DCF": -1.318})
        assert bd["single_method"] is False
        assert bd["methods_surviving"] == 2
        assert not any(f.startswith("Single-method blend") for f in flags)

    def test_the_flag_is_not_duplicated_when_the_blend_runs_twice(self):
        flags: list[str] = []
        for _ in range(2):
            _blend_methods(
                profile_methods=METHODS,
                method_values={"SOTP (published)": None, "EV/EBITDA": 5.7269,
                               "DCF": None},
                c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.0)
        assert sum(f.startswith("Single-method blend") for f in flags) == 1


# ── Forward flags ───────────────────────────────────────────────────────────


class TestForwardFlags:
    def test_a_non_positive_drop_is_flagged_with_the_value_it_discarded(self):
        _, _, flags = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                              "DCF": -1.318})
        f = [x for x in flags if x.startswith("Non-positive leg dropped")]
        assert len(f) == 1
        assert "DCF" in f[0]
        assert "-1.3180" in f[0]        # the magnitude survives in the prose
        assert "25%" in f[0]            # and so does the weight it carried

    def test_an_uncomputable_drop_is_not_flagged(self):
        """18 legs drop as uncomputable across the 14 golden fixtures against 6
        that drop as non-positive. Flagging all 24 would be prose no reader
        reaches the end of — the opposite failure from silence."""
        _, _, flags = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                              "DCF": None})
        assert not any(x.startswith("Non-positive leg dropped") for x in flags)

    def test_a_fully_voting_blend_raises_neither_new_flag(self):
        _, _, flags = _blend({"SOTP (published)": SOTP, "EV/EBITDA": EVE,
                              "DCF": 5.0})
        assert flags == []

    def test_both_flags_can_fire_on_the_same_call(self):
        """BN4_SI's bear scenario: EV/EBITDA computes 0.0 and is dropped as
        non-positive, SOTP cannot be computed, and the sole survivor is a
        scenario-INSENSITIVE published analyst SOTP."""
        iv, bd, flags = _blend({"SOTP (published)": 8.3672,
                                "EV/EBITDA": 0.0, "DCF": None})
        assert bd["single_method"] is True
        assert _only(bd, "EV/EBITDA")["reason"] == "non_positive"
        assert _only(bd, "DCF")["reason"] == "uncomputable"
        assert any(x.startswith("Non-positive leg dropped") for x in flags)
        assert any(x.startswith("Single-method blend") for x in flags)
        assert iv == pytest.approx(8.3672)


# ── Degenerate inputs must not raise ────────────────────────────────────────


class TestDegenerateInputs:
    def test_nothing_at_all_still_reports_what_it_dropped(self):
        """The degenerate return used to be `None, {}`. That publishes
        `legs_dropped: None` downstream, which reads as "nothing was dropped" —
        the exact silence this module exists to remove, arriving one branch
        later, on the profile that dropped EVERY leg. `None` for the IV is
        correct and unchanged; the record is not optional."""
        flags: list[str] = []
        iv, bd = _blend_methods(
            profile_methods=METHODS, method_values={},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.0)
        assert iv is None
        assert bd != {}
        assert len(bd["legs_dropped"]) == 3
        assert all(d["reason"] == "uncomputable" for d in bd["legs_dropped"])
        assert bd["weight_surviving"] == 0.0
        assert bd["methods_surviving"] == 0
        assert bd["single_method"] is False       # zero survivors is not one
        # The numeric keys are genuinely absent, not zeroed: no bucket existed,
        # so there is no weighted mean to report. Consumers use `.get()`.
        for k in ("iv_dcf", "iv_multi", "weight_dcf", "effective_weights"):
            assert bd.get(k) is None

    def test_all_legs_non_positive_still_reports_them(self):
        flags: list[str] = []
        iv, bd = _blend_methods(
            profile_methods=METHODS,
            method_values={"SOTP (published)": -2.0, "EV/EBITDA": 0.0,
                           "DCF": -0.5},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.0)
        assert iv is None
        assert len(bd["legs_dropped"]) == 3
        assert all(d["reason"] == "non_positive" for d in bd["legs_dropped"])
        assert bd["weight_surviving"] == 0.0
        assert sum(f.startswith("Non-positive leg dropped") for f in flags) == 3

    def test_a_zero_weight_profile_does_not_divide_by_zero(self):
        methods = [{"name": "DCF", "weight": 0.0, "implementable": True}]
        flags: list[str] = []
        iv, bd = _blend_methods(
            profile_methods=methods, method_values={"DCF": -3.0},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.0)
        assert iv is None
        assert bd["weight_surviving"] is None      # intended_w == 0
        assert bd["weight_intended"] == 0.0
        assert _only(bd, "DCF")["reason"] == "non_positive"

    def test_a_profile_whose_weights_cancel_reports_no_ratio_at_all(self):
        """`intended_w` is the sum of the RAW table weights, so a negative
        weight can cancel a positive one and leave 0.0 as the denominator even
        though a leg is voting. `None` is the honest answer: the ratio has no
        meaning when the profile did not intend a positive amount of weight.
        Asserting 1.0 here would be asserting that a nonsense input produced a
        sensible-looking output."""
        methods = [{"name": "DCF", "weight": -0.5, "implementable": True},
                   {"name": "EV/EBITDA", "weight": 0.5, "implementable": True}]
        flags: list[str] = []
        iv, bd = _blend_methods(
            profile_methods=methods, method_values={"DCF": None,
                                                    "EV/EBITDA": 10.0},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.0)
        assert iv == pytest.approx(10.0)
        assert bd["weight_intended"] == 0.0
        assert bd["weight_surviving"] is None
        assert bd["single_method"] is True
        # The single-survivor prose must survive a None ratio rather than
        # raising inside the f-string, which is why it has two branches.
        sm = [f for f in flags if f.startswith("Single-method blend")]
        assert len(sm) == 1
        assert "%" not in sm[0]

    def test_an_empty_profile_returns_nothing_and_claims_nothing(self):
        flags: list[str] = []
        iv, bd = _blend_methods(
            profile_methods=[], method_values={"DCF": 5.0},
            c_macro=0.0, forward_flags=flags, dcf_tv_fraction=0.0)
        assert iv is None
        assert bd["legs_dropped"] == []
        assert bd["methods_surviving"] == 0
        assert bd["weight_intended"] == 0.0
        assert flags == []


# ── The payload actually publishes it ────────────────────────────────────────


class TestThePayloadPublishesTheDisclosure:
    """A breakdown key nobody copies into the payload is a key nobody reads.

    Pinned by source inspection rather than by a full agent run, because the
    scenario payload is assembled ~4000 lines into `run_dcf_agent` and reaching
    it needs a fixture. The five keys are published beside `weight_dcf` and
    `effective_weights`, which are the survivors-only fields they complete.
    """

    KEYS = ("legs_dropped", "weight_surviving", "weight_intended",
            "methods_surviving", "single_method")

    def test_all_five_keys_are_copied_into_the_scenario_payload(self):
        import inspect
        import src.agents.analysis.dcf_agent as da

        src_txt = inspect.getsource(da)
        for k in self.KEYS:
            needle = f'"{k}":'
            # once in the breakdown the blend builds, once in the payload it is
            # copied into. Fewer than two and the value never reaches a reader.
            assert src_txt.count(needle) >= 2, (
                f"{k} is not published into the scenario payload — it is built "
                f"in `_blend_methods` and dropped on the floor"
            )
            assert f'blend_breakdown.get("{k}")' in src_txt, (
                f"{k} is published by something other than a `.get()` on the "
                f"blend breakdown, so the degenerate `None, disclosure` return "
                f"would raise"
            )

    def test_the_payload_readers_are_get_not_index(self):
        """The five new keys must be read with `.get()`, never indexed: on the degenerate return they are present but
        the numeric ones are not, and an unguarded index would turn "no leg
        produced a value" into a KeyError."""
        import inspect
        import src.agents.analysis.dcf_agent as da

        src_txt = inspect.getsource(da)
        for k in self.KEYS:
            assert f'blend_breakdown["{k}"]' not in src_txt
