"""Owner decision 3 (2026-09-17): the CAGR-divergence gate is two-sided.

The gate as shipped in ``c87d38c`` tested for divergence symmetrically —
``abs(g_ntm − CAGR) > 15pp`` — but rewrote only upward, with ``min()``. A
consensus far BELOW a high historical CAGR therefore passed straight through,
which is exactly what ICE did: a −12.71% NTM consensus against a +8.3% five-year
CAGR, 21.0pp apart, compounded into an explicit ten-year cash flow model.

The floor this file pins is narrow on purpose. It binds only when ALL of:

  * the divergence test fires (|g_ntm − CAGR| > 15pp);
  * ``g_ntm < min(CAGR, 0) − 5pp``, so a merely conservative forecast is left
    alone — ``test_gate_is_one_sided`` in ``test_growth_convergence.py`` still
    holds unchanged for exactly that reason;
  * ``CAGR > 0``, so a business with negative measured history gets no
    secular floor to defend;
  * the profile is not in ``_CYCLICAL_PROFILES``, where a −20% to −40%
    down-cycle in DRAM or shipping is genuine;
  * neither of the gate's two exceptions stood it down — company guidance and
    EBIT-margin inflection are GATE-WIDE, so they suppress the floor exactly as
    they suppress the ceiling.

A naming note on one of the owner's three examples: the exemption set holds
PROFILE names, and ``Consumer Staples`` is a SECTOR whose profiles are
"Beverage (Alcoholic)", "Food Processing" and so on. Every one of them is
non-cyclical, so they all receive the floor, which is the intent — but the guard
is a profile test and the example was a sector. ``Market Infrastructure`` (ICE)
and ``Payment Networks`` (V) are both real profiles and both are covered below.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import src.agents.analysis.dcf_agent as d
from src.agents.analysis.dcf_agent import (
    _CAGR_DIVERGENCE_HEADROOM,
    _CAGR_DIVERGENCE_THRESHOLD,
    _CYCLICAL_PROFILES,
    _gate_growth_cagr_divergence,
    _growth_convergence_schedule,
    _scale_analyst_bands_to_cap,
    _shift_analyst_bands_to_floor,
)


# ── The measured ICE vector ─────────────────────────────────────────────────
# Profile "Market Infrastructure"; the numbers are the ones the one-sided gate
# waved through, quoted in the owner's decision.

ICE_G_NTM = -0.1271
ICE_CAGR = 0.083
ICE_PROFILE = "Market Infrastructure"
#: max(-0.1271, 0.083 - 0.15) = max(-0.1271, -0.067)
ICE_FLOOR_VALUE = -0.067

#: Real, non-cyclical profile names — every one of them appears in the golden
#: fixtures, so none is invented for the sake of a test.
NON_CYCLICAL_PROFILES = [
    "Market Infrastructure",
    "Payment Networks",
    "Hyper-Growth Platform",
    "Membership / Subscription Retail",
    "Hyperscaler / Tech Conglomerate",
    "Conglomerate / Industrial (SG)",
    "Money Center Bank",
    "Brokerage",
    "S-REIT",
    "Beverage (Alcoholic)",
]


def _gate(g_ntm, cagr, profile=ICE_PROFILE, **kw):
    kw.setdefault("series", [])
    return _gate_growth_cagr_divergence(
        g_ntm, cagr, profile_name=profile, **kw)


# ── ICE reproduces ──────────────────────────────────────────────────────────


def test_ice_reproduces_exactly():
    """The case the decision names, to six decimals."""
    gated, record, exc = _gate(ICE_G_NTM, ICE_CAGR)
    assert exc is None
    assert gated == pytest.approx(ICE_FLOOR_VALUE, abs=1e-9)
    assert record is not None
    assert record["direction"] == "floor"
    assert record["raw_input_path_a"] == pytest.approx(ICE_G_NTM)
    assert record["gated_output_path_b"] == pytest.approx(ICE_FLOOR_VALUE)


def test_the_floor_record_keeps_the_same_ledger_contract():
    """Same gate_id, same metric, same path A/B pair, same ``applied`` flag.

    The forward ledger and ``test_gate_backtest``'s structural check both key
    off these, so a new direction must arrive inside the existing record shape
    rather than as a second gate."""
    _, record, _ = _gate(ICE_G_NTM, ICE_CAGR)
    assert record["gate_id"] == "GATE_GROWTH_CAGR_DIVERGENCE"
    assert record["metric"] == "revenue_growth"
    assert record["applied"] is True
    assert record["raw_input_path_a"] < record["gated_output_path_b"], (
        "a floor must RAISE the figure; if path B is below path A the "
        "direction label and the arithmetic disagree")


def test_the_basis_names_the_direction_it_took():
    """The basis string is the only human-readable half of the record, and it
    is what lands in the published forward flag. A reader must be able to tell
    a cap from a floor without decoding the numbers."""
    _, cap_rec, _ = _gate(0.2026, -0.025, profile="Conglomerate / Industrial (SG)")
    _, floor_rec, _ = _gate(ICE_G_NTM, ICE_CAGR)
    assert cap_rec["direction"] == "cap"
    assert "capped at" in cap_rec["basis"]
    assert "raised to CAGR" in floor_rec["basis"]
    assert "non-cyclical profile" in floor_rec["basis"]


def test_ice_year_one_is_the_floored_figure_not_the_raw_consensus():
    """The gate rewrites g_ntm BEFORE it seeds the convergence schedule, so
    year 1 of the ten-year model is −6.7% and not −12.71%. This is the whole
    point of the decision: the corruption was being compounded, not just
    displayed."""
    gated, record, _ = _gate(ICE_G_NTM, ICE_CAGR)
    assert record is not None
    schedule = _growth_convergence_schedule(gated, 0.025)
    assert schedule[0] == pytest.approx(ICE_FLOOR_VALUE)
    assert schedule[0] != pytest.approx(ICE_G_NTM)
    raw = _growth_convergence_schedule(ICE_G_NTM, 0.025)
    assert schedule[0] > raw[0]


# ── The five conditions, each isolated ──────────────────────────────────────


def test_the_floor_sits_five_points_below_zero_on_a_positive_cagr():
    """``min(CAGR, 0) − 5pp``. With CAGR positive the ``min`` is inert and the
    floor is always −5%: a company with a positive secular history is still
    allowed a bad forward year, and only below that is the figure treated as an
    estimate error rather than a forecast."""
    assert min(ICE_CAGR, 0.0) - _CAGR_DIVERGENCE_HEADROOM == pytest.approx(-0.05)
    gated, record, _ = _gate(-0.049, 0.20)
    assert gated == pytest.approx(-0.049) and record is None


def test_the_floor_boundary_is_exclusive():
    """At exactly −5% the floor does not bind; a hair below it, it does.

    Both inputs clear the 15pp divergence trigger against a +20% CAGR, so the
    only thing separating them is the ``g_ntm < floor`` comparison."""
    at, rec_at, _ = _gate(-0.05, 0.20)
    assert at == pytest.approx(-0.05) and rec_at is None
    below, rec_below, _ = _gate(-0.0501, 0.20)
    assert rec_below is not None and rec_below["direction"] == "floor"
    assert below == pytest.approx(0.20 - _CAGR_DIVERGENCE_THRESHOLD)


def test_the_rewrite_is_cagr_minus_the_threshold_not_the_floor():
    """Two different numbers, and the boundary test above is where they part
    company. The TRIGGER is ``min(CAGR, 0) − 5pp``; the VALUE written is
    ``CAGR − 15pp``. Conflating them would either let a −4.9% consensus through
    or raise a −30% one only as far as −5%.

    Here the CAGR is below the threshold, so the rewritten value (−3.0%) is
    itself ABOVE the −5% trigger floor: a −30% consensus on a +12% history is
    raised past the point that made it fire. That is intended — the rewrite aims
    at the history, not at the trigger."""
    gated, _, _ = _gate(-0.30, 0.12)
    assert gated == pytest.approx(0.12 - _CAGR_DIVERGENCE_THRESHOLD)
    assert gated == pytest.approx(-0.03)
    assert gated > -_CAGR_DIVERGENCE_HEADROOM


@pytest.mark.parametrize("g_ntm,cagr", [
    (-0.1271, 0.083),      # ICE — floor binds
    (-0.30, 0.12),         # floor binds and overshoots the trigger
    (-0.20, 0.30),         # floor is POSITIVE: a sign flip
    (-0.45, 0.02),         # floor is deeply negative
    (0.2026, -0.025),      # cap binds
    (0.40, 0.10),          # cap binds
    (0.60, -0.30),         # cap binds on a shrinking history
])
def test_the_gate_is_idempotent_in_both_directions(g_ntm, cagr):
    """Feeding a gate's own output back through it must be a no-op, with no
    record and no further movement. A gate that ratchets would compound on
    every re-run, and this engine re-runs on cached state.

    The floor is idempotent by construction rather than by luck: the rewrite is
    ``CAGR − THRESHOLD``, which sits exactly THRESHOLD away from CAGR, and the
    divergence test early-returns on ``<= THRESHOLD``. The cap reaches the same
    place by a different route — its output is within HEADROOM of a floored
    CAGR, and where it is not, ``min()`` no longer binds."""
    gated, record, _ = _gate(g_ntm, cagr)
    assert record is not None, "precondition: the gate binds on this vector"
    again, record2, exc2 = _gate(gated, cagr)
    assert again == pytest.approx(gated)
    assert record2 is None and exc2 is None



@pytest.mark.parametrize("g_ntm,cagr", [(-0.30, -0.02), (-0.45, 0.0), (-0.20, -0.25)])
def test_a_non_positive_cagr_gets_no_floor(g_ntm, cagr):
    """There is nothing secular to defend when the history is itself flat or
    shrinking. This is what keeps a genuine structural decliner from being
    argued into growth — the mirror of the ceiling's ``max(CAGR, 0)``, which
    exists so a shrinking business is still allowed modest forward growth."""
    gated, record, exc = _gate(g_ntm, cagr)
    assert gated == pytest.approx(g_ntm)
    assert record is None and exc is None


@pytest.mark.parametrize("profile", sorted(_CYCLICAL_PROFILES))
def test_every_cyclical_profile_is_exempt_from_the_floor(profile):
    """Parametrised over the frozenset itself, not over a hand-picked subset,
    so a profile added to ``_CYCLICAL_PROFILES`` is exempt automatically.
    −35% in DRAM and −25% in shipping are the down-cycle, not a data error."""
    gated, record, exc = _gate(-0.35, 0.10, profile=profile)
    assert gated == pytest.approx(-0.35)
    assert record is None and exc is None


def test_the_ceiling_still_applies_to_cyclicals():
    """The exemption is to the FLOOR only. A cyclical whose consensus runs away
    upward is still capped — the exemption exists because down-cycles are
    genuine, not because cyclicals are unregulated."""
    gated, record, _ = _gate(0.40, 0.10, profile="Memory / DRAM-NAND")
    assert record is not None and record["direction"] == "cap"
    assert gated == pytest.approx(0.10 + _CAGR_DIVERGENCE_HEADROOM)


@pytest.mark.parametrize("profile", NON_CYCLICAL_PROFILES)
def test_secular_and_non_cyclical_profiles_do_get_the_floor(profile):
    """The owner's wording: "apply it across secular and non-cyclical profiles".
    Each name here is a real profile, and the two the decision names by example
    — Market Infrastructure and Payment Networks — are the first two."""
    gated, record, _ = _gate(ICE_G_NTM, ICE_CAGR, profile=profile)
    assert record is not None and record["direction"] == "floor"
    assert gated == pytest.approx(ICE_FLOOR_VALUE)


def test_guidance_stands_down_the_floor_too():
    """Gate-wide, not ceiling-only. Management's own forward-year figure is
    exactly the information a consensus-vs-history divergence cannot see, in
    either direction — and a guided −12.71% on a +8.3% history is a real
    forecast, not a corrupted estimate."""
    gated, record, exc = _gate(ICE_G_NTM, ICE_CAGR, data_source="guided")
    assert gated == pytest.approx(ICE_G_NTM)
    assert record is None
    assert exc is not None and "guided" in exc


def test_a_margin_inflection_stands_down_the_floor_too():
    """Also gate-wide. A non-cyclical whose latest EBIT margin sits ≥5pp above
    its 3-year mean is inflecting, and an inflection is a reason the historical
    CAGR is a poor reference in BOTH directions."""
    series = [{"revenue": 100.0, "ebit": m * 100.0} for m in (0.05, 0.05, 0.05, 0.25)]
    gated, record, exc = _gate(ICE_G_NTM, ICE_CAGR, series=series)
    assert gated == pytest.approx(ICE_G_NTM)
    assert record is None
    assert exc is not None and "inflection" in exc


def test_a_margin_inflection_does_not_stand_down_a_cyclical():
    """Unchanged by this decision, but load-bearing for it: the inflection
    exception is itself cyclical-exempt, so a peak margin cannot unlock the
    gate on the very profiles the floor already exempts. Belt and braces that
    have to agree."""
    series = [{"revenue": 100.0, "ebit": m * 100.0} for m in (0.05, 0.05, 0.05, 0.25)]
    gated, record, exc = _gate(0.40, 0.10, profile="Memory / DRAM-NAND", series=series)
    assert exc is None
    assert record is not None and record["direction"] == "cap"


def test_a_thin_history_still_stands_the_whole_gate_down():
    gated, record, exc = _gate(-0.30, None)
    assert gated == pytest.approx(-0.30) and record is None and exc is None
    gated, record, exc = _gate(None, ICE_CAGR)
    assert gated is None and record is None and exc is None


# ── The two halves cannot both bind ─────────────────────────────────────────


@pytest.mark.parametrize("g_ntm", [-0.60, -0.30, -0.10, -0.05, 0.0,
                                   0.05, 0.20, 0.40, 0.80])
@pytest.mark.parametrize("cagr", [-0.20, -0.02, 0.0, 0.03, 0.12, 0.30])
@pytest.mark.parametrize("profile", ["Market Infrastructure", "Mining (Major)"])
def test_at_most_one_half_binds_and_the_direction_always_agrees(g_ntm, cagr, profile):
    """54 combinations x 2 profiles. The ceiling binds only above
    ``max(CAGR, 0) + 5pp`` and the floor only below ``min(CAGR, 0) − 5pp``, and
    those two bounds cannot both contain one number, so a record must name
    exactly one direction and the rewrite must move g_ntm that way."""
    gated, record, exc = _gate(g_ntm, cagr, profile=profile)
    if record is None:
        assert gated == pytest.approx(g_ntm)
        return
    direction = record["direction"]
    assert direction in ("cap", "floor")
    if direction == "cap":
        assert gated < g_ntm
        assert gated == pytest.approx(min(g_ntm, max(cagr, 0.0) + _CAGR_DIVERGENCE_HEADROOM))
    else:
        assert gated > g_ntm
        assert gated == pytest.approx(max(g_ntm, cagr - _CAGR_DIVERGENCE_THRESHOLD))
        assert g_ntm < min(cagr, 0.0) - _CAGR_DIVERGENCE_HEADROOM
        assert cagr > 0.0
        assert profile not in _CYCLICAL_PROFILES


def test_the_existing_one_sided_test_still_holds_for_its_own_input():
    """``test_gate_is_one_sided`` in test_growth_convergence.py pins
    (0.05, 0.42) → unchanged. It passes untouched, and this restates WHY: a
    conservative-but-positive forecast on a fast-growing history is 5pp above
    the floor and never reaches it. The old docstring's claim that conservatism
    is simply not the defect is now true only inside the −5% slack."""
    gated, record, exc = _gate(0.05, 0.42, profile="Hyper-Growth Platform")
    assert gated == pytest.approx(0.05) and record is None and exc is None
    # ... and the same shape one tick below the floor does fire.
    gated, record, _ = _gate(-0.0501, 0.42, profile="Hyper-Growth Platform")
    assert record is not None and record["direction"] == "floor"


# ── _shift_analyst_bands_to_floor ───────────────────────────────────────────


ICE_BANDS = {"bear": -0.30, "base": ICE_G_NTM, "bull": 0.05}


def test_the_shift_preserves_spread_and_ordering_exactly():
    shifted, shift = _shift_analyst_bands_to_floor(ICE_BANDS, ICE_FLOOR_VALUE)
    assert shift == pytest.approx(ICE_FLOOR_VALUE - ICE_G_NTM)
    assert shifted["base"] == pytest.approx(ICE_FLOOR_VALUE)
    assert shifted["bear"] == pytest.approx(ICE_BANDS["bear"] + shift)
    assert shifted["bull"] == pytest.approx(ICE_BANDS["bull"] + shift)
    assert (shifted["bull"] - shifted["bear"]) == pytest.approx(
        ICE_BANDS["bull"] - ICE_BANDS["bear"])
    assert shifted["bear"] < shifted["base"] < shifted["bull"]


def test_the_shift_is_the_reason_the_two_functions_are_separate():
    """A proportional rewrite of this band flips it. ``floor / base`` is
    ``-0.067 / -0.1271 = 0.527``, which happens to be positive here — but it
    drags the POSITIVE bull down toward zero while raising the bear, so the
    dispersion only survives for the half of the band sharing the base's sign.
    Both operations are computed and compared rather than asserted in prose."""
    additive, shift = _shift_analyst_bands_to_floor(ICE_BANDS, ICE_FLOOR_VALUE)
    scale = ICE_FLOOR_VALUE / ICE_G_NTM
    proportional = {k: v * scale for k, v in ICE_BANDS.items()}
    assert additive["bull"] > proportional["bull"]
    assert (additive["bull"] - additive["bear"]) > (
        proportional["bull"] - proportional["bear"])


def test_a_positive_floor_would_invert_a_proportional_band_but_not_a_shifted_one():
    """The sign flip, made concrete. Whenever CAGR > 15pp the floor
    ``CAGR − 15pp`` is positive, so ``floor / base`` is NEGATIVE and a
    proportional rewrite flips every member's sign and inverts the ordering.
    An additive shift does neither. This is the case the separate function
    exists for."""
    bands = {"bear": -0.40, "base": -0.20, "bull": 0.00}
    floor = 0.30 - _CAGR_DIVERGENCE_THRESHOLD          # CAGR 0.30 → floor 0.15
    shifted, shift = _shift_analyst_bands_to_floor(bands, floor)
    assert shift == pytest.approx(0.35)
    assert shifted == pytest.approx({"bear": -0.05, "base": 0.15, "bull": 0.35})
    assert shifted["bear"] < shifted["base"] < shifted["bull"]

    scale = floor / bands["base"]
    assert scale < 0
    proportional = {k: v * scale for k, v in bands.items()}
    assert proportional["bear"] > proportional["base"] > proportional["bull"], (
        "precondition: the proportional rewrite inverts the ordering, which is "
        "what the additive one exists to avoid")


def test_the_shift_leaves_a_band_already_above_the_floor_alone():
    bands = {"bear": -0.02, "base": 0.01, "bull": 0.05}
    shifted, shift = _shift_analyst_bands_to_floor(bands, -0.067)
    assert shift is None and shifted == bands


def test_the_shift_leaves_a_band_exactly_on_the_floor_alone():
    """``base >= floor`` is the test, so equality does not fire. A band already
    sitting on the floor must not be nudged a float-epsilon above it."""
    bands = {"bear": -0.10, "base": ICE_FLOOR_VALUE, "bull": 0.02}
    shifted, shift = _shift_analyst_bands_to_floor(bands, ICE_FLOOR_VALUE)
    assert shift is None and shifted == bands


def test_non_scenario_band_keys_are_not_shifted():
    """``analyst_count`` rides along in the band dict and is a COUNT. Shifting
    it would corrupt the dispersion plausibility guard downstream — the same
    reason the ceiling path scales only the three scenario keys."""
    bands = {**ICE_BANDS, "analyst_count": 9, "source": "consensus"}
    shifted, shift = _shift_analyst_bands_to_floor(bands, ICE_FLOOR_VALUE)
    assert shifted["analyst_count"] == 9
    assert shifted["source"] == "consensus"
    assert shift is not None


@pytest.mark.parametrize("bands", [None, {}, {"base": None}, {"base": "n/a"},
                                   {"bear": -0.3, "bull": 0.1}])
def test_a_band_without_a_numeric_base_is_returned_unchanged(bands):
    shifted, shift = _shift_analyst_bands_to_floor(bands, ICE_FLOOR_VALUE)
    assert shift is None and shifted == bands


def test_the_cap_function_is_untouched_and_still_refuses_a_non_positive_cap():
    """Pinned here because decision 3 adds a sibling, not a replacement.
    ``_scale_analyst_bands_to_cap`` is load-bearing for the revenue-scale tier
    and is separately pinned by test_cash_conversion_gate.py:153-213; this is
    the guard against "just add a sign parameter" collapsing the two."""
    assert _scale_analyst_bands_to_cap({"base": 0.20, "bull": 0.30}, 0.15)[1] \
        == pytest.approx(0.75)
    assert _scale_analyst_bands_to_cap({"base": -0.20}, -0.067)[1] is None
    assert _scale_analyst_bands_to_cap({"base": -0.20}, 0.15)[1] is None


# ── The caller ──────────────────────────────────────────────────────────────


def test_the_caller_applies_each_direction_in_its_own_idiom():
    """``min()`` + proportional scale upward, ``max()`` + additive shift
    downward. Reading the direction out of the record is what keeps one gate
    from having two callers that disagree about which half fired."""
    src = inspect.getsource(d.run_dcf_agent)
    at = src.index('_cagr_gate_rec.get("direction") == "floor"')
    floor_branch = src[at:at + 1400]
    assert "growth_base = max(growth_base, _gate_value)" in floor_branch
    assert "_shift_analyst_bands_to_floor(" in floor_branch
    cap_branch = floor_branch[floor_branch.index("else:"):]
    assert "growth_base = min(growth_base, _gate_value)" in cap_branch
    assert "_scale_analyst_bands_to_cap(" in cap_branch


def test_the_cap_path_never_reaches_the_shift_function():
    """The two branches are exclusive, so a cap cannot be applied additively.
    Asserted on ordering: the ``max``/shift pair precedes the ``min``/scale
    pair and each sits inside its own branch."""
    src = inspect.getsource(d.run_dcf_agent)
    at = src.index('_cagr_gate_rec.get("direction") == "floor"')
    branch = src[at:]
    assert branch.index("growth_base = max(growth_base, _gate_value)") < \
        branch.index("growth_base = min(growth_base, _gate_value)")
    assert "_shift_analyst_bands_to_floor(" not in \
        branch[branch.index("growth_base = min(growth_base, _gate_value)"):]


def test_the_gate_still_runs_before_the_scenario_loop():
    """Unchanged by this decision and already pinned by
    test_growth_convergence.py, restated here because the new branch sits
    inside the same block: the floor has to move ``growth_base`` and the band
    BEFORE the loop snapshots either."""
    src = inspect.getsource(d.run_dcf_agent)
    assert src.index("_gate_growth_cagr_divergence") < \
        src.index('_cagr_gate_rec.get("direction") == "floor"') < \
        src.index('for scenario in ("base", "bear", "bull")')


def test_the_published_flag_format_is_unchanged_for_the_cap_direction():
    """Rewording the flag prefix would churn the published string on every
    fixture the cap fires on — BN4.SI and U96.SI — for no information, because
    the basis in the parenthetical already names the direction. Byte-identical
    arrow, direction-specific band note.

    Scoped to the block only, and deliberately NOT asserting on the words
    "capped at": they appear in this block's own explanatory comment, and a
    substring test that matches the comment describing it is a test that passes
    for the wrong reason.
    """
    src = inspect.getsource(d.run_dcf_agent)
    start = src.index("if _cagr_gate_rec is not None:")
    block = src[start:src.index("elif _cagr_gate_exc is not None", start)]
    # Both band notes exist, one per direction, and each is built in its own
    # branch next to the arithmetic that needs it.
    assert 'f"; analyst bands scaled {_gate_band_adj:.2f}x"' in block
    assert 'f"; analyst bands shifted {_gate_band_adj:+.1%}"' in block
    assert block.index("shifted") < block.index("scaled"), (
        "the floor branch must come first, matching the direction test")

    # The append itself is unconditional: one arrow prefix for both directions,
    # with the direction carried by `_gate_band_note` and by the basis string.
    append_at = block.index('f"CAGR-divergence gate: NTM growth {_gate_ref:+.1%} → "')
    # Not `index(")")`: the basis f-string contains `})`, so the first closing
    # paren after it is inside a string literal.
    end = block.index("\n            )", append_at) + len("\n            )")
    append = block[append_at:end]
    assert append.count('f"CAGR-divergence gate') == 1
    assert append == (
        'f"CAGR-divergence gate: NTM growth {_gate_ref:+.1%} → "\n'
        '                f"{_gate_value:+.1%}"\n'
        '                + _gate_band_note\n'
        '                + f" ({_cagr_gate_rec[\'basis\']})"\n'
        '            )'
    ), append


def test_this_flag_reaches_a_payload_unlike_its_neighbours():
    """The append below the scenario loop is dead (see
    tests/test_valuation_fixes_0917b.py). THIS one is above it, so it is live —
    worth pinning, because the two look identical and the difference decides
    whether a reader ever sees the floor fire."""
    src = inspect.getsource(d.run_dcf_agent)
    assert src.index("f\"CAGR-divergence gate: NTM growth {_gate_ref:+.1%} → \"") < \
        src.index("forward_flags: list[str] = list(ticker_forward_flags)")


# ── Why the golden baseline does not move ───────────────────────────────────


def test_no_fixture_sits_below_the_downside_floor():
    """Decision 3 predicts an EMPTY golden diff, and this is the measurement
    that makes that a claim rather than a hope: no persisted growth_rate in any
    of the 14 fixtures x 3 scenarios is below −5%, and no fixture's BASE growth
    is negative at all. The floor cannot bind on a number it does not reach.

    The gate is applied to the base figure before scenario differentiation, so
    the two negative bear values below are downstream of it in any case — and
    both are inside the −5% slack regardless.

    ICE is not a fixture. Its verification is the arithmetic above plus a
    production basket run; closing the fixture gap means recording one from a
    released profile, the same open item as decisions 1 and 2b.
    """
    snap = json.loads(
        (Path(__file__).parent / "golden" / "snapshots.json").read_text("utf-8"))
    seen = 0
    for name, entry in snap.items():
        if name == "_meta" or not isinstance(entry, dict):
            continue
        proj = entry.get("projection", {})
        for scen in ("bear", "base", "bull"):
            g = proj.get(f"scenarios.{scen}.growth_rate")
            if isinstance(g, (int, float)):
                seen += 1
                assert g > -_CAGR_DIVERGENCE_HEADROOM, f"{name}/{scen} = {g}"
            if scen == "base":
                assert g is None or g >= 0.0, f"{name} base growth is negative"
    assert seen == 42
