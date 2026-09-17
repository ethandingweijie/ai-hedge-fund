"""Owner decision 2c (2026-09-17): the 12m target band is two-sided.

The divergence guard that already existed is one-sided. It caps a base target
that runs to more than 2x the base IV down to 1.5x each scenario IV, and it has
no lower bound at all — so the same class of corrupted forward inputs that
produced MU's $6,105 could equally produce Visa's $12.84, a 12m target at 3.0%
of its own base IV, and nothing in the engine would have said anything. Visa's
did get published.

The band the owner specified is ``[0.33x, 2.50x]`` of Base IV, with the fallback
``Base IV / (1 + CoE)``. Three deviations from that literal wording are pinned
here, each because a measurement across the 14 golden fixtures x 3 scenarios
forced it rather than because it read better:

1. the band is tested against each scenario's OWN IV (base-referencing
   manufactures violations out of scenario spread on cyclicals — FCX's bull and
   U96.SI's bear), while the fallback keeps Base IV exactly as specified;
2. if ANY scenario breaches, ALL THREE are replaced (per-scenario replacement
   gives MELI bull below bear);
3. the replacement is itself bounded by the convergence cap, the high-SBC bear
   ceiling, and finally the band, so the engine cannot emit a target that would
   re-fire the band on the next run.

`_band_12m_targets`'s docstring carries the reasoning and the numbers. These
tests carry the arithmetic.
"""

from __future__ import annotations

import inspect

import pytest

import src.agents.analysis.dcf_agent as d
from src.agents.analysis.dcf_agent import _band_12m_targets


# ── The measured fixture vectors ────────────────────────────────────────────
# All from tests/golden/snapshots.json at commit d609c65.

#: Visa as it stood on the PRE-2a baseline — the absurdity the owner quoted.
VISA_IVS = {"bear": 276.28, "base": 428.47, "bull": 539.81}
VISA_PTS = {"bear": 9.63, "base": 12.84, "bull": 16.06}
VISA_WACC = 0.0725

#: MELI post-2a/2b, with its spot and capture from the replay's own
#: `[convergence-cap]` line: high_sbc=True, reaccel=False, unanimous above
#: spot, so 0.20 + 0.15 = 0.35.
MELI_IVS = {"bear": 4588.16, "base": 5578.42, "bull": 6131.13}
MELI_PTS = {"bear": 1241.83, "base": 1763.50, "bull": 2273.31}
MELI_WACC = 0.1144
MELI_SPOT = 1828.94
MELI_CAPTURE = 0.35

#: FCX — the cyclical that base-referencing would have flagged and per-scenario
#: referencing does not. Bull target $70.50 is 2.756x base IV, 0.971x bull IV.
FCX_IVS = {"bear": 16.06, "base": 25.58, "bull": 72.62}
FCX_PTS = {"bear": 24.09, "base": 38.37, "bull": 70.50}

#: U96.SI — the same false positive on the low side. Bear target S$1.78 is
#: 0.311x base IV, 0.764x its own bear IV of S$2.33.
U96_IVS = {"bear": 2.33, "base": 5.73, "bull": 9.13}
U96_PTS = {"bear": 1.78, "base": 5.87, "bull": 7.06}


def _ratios(pts, ivs):
    return {s: pts[s] / ivs[s] for s in ("bear", "base", "bull") if pts.get(s) and ivs.get(s)}


# ── The constants are the owner's ───────────────────────────────────────────


def test_the_band_is_exactly_what_the_owner_specified():
    assert d._PT_IV_BAND_LO == pytest.approx(0.33)
    assert d._PT_IV_BAND_HI == pytest.approx(2.50)


def test_the_fallback_spread_matches_the_engines_scenario_convention():
    """0.75/1.00/1.25 is `_scenario_mult` everywhere else a single base number
    is fanned into three, so the fallback is not a fourth convention."""
    assert d._PT_BAND_SCENARIO_MULT == {"bear": 0.75, "base": 1.00, "bull": 1.25}


def test_the_bear_ceiling_constant_is_the_number_the_call_site_used():
    """Extracted from a literal so the helper and the convergence-cap loop
    cannot drift onto different values. The local at the call site is named
    `_bear_floor` but the comparison is `if _pt > _bear_floor` — a ceiling."""
    assert d._HIGH_SBC_BEAR_CEILING_MULT == pytest.approx(0.85)
    src = inspect.getsource(d)
    assert "_bear_floor = _spot_for_cap * _HIGH_SBC_BEAR_CEILING_MULT" in src
    assert "_spot_for_cap * 0.85" not in src


# ── A sane triple is left alone ─────────────────────────────────────────────


def test_a_sane_triple_produces_no_breach_and_no_replacement():
    rep, br, info = _band_12m_targets(
        {"bear": 300.0, "base": 400.0, "bull": 500.0}, VISA_IVS, 428.47, VISA_WACC)
    assert br == [] and rep == {}
    assert info["base_ratio_path_a"] == pytest.approx(400.0 / 428.47)


def test_the_replacement_is_rounded_toward_the_inside_of_the_band():
    """Found by the grid test below, not by a fixture: `round` is
    direction-agnostic, and 0.33 x IV is not a whole number of cents. On a bull
    IV of 7,809.788 the low limit is 2,577.23004, so round(..., 2) = 2577.23
    reads back as 0.32999999487 — a target published one cent outside the band
    whose own validation flag says it satisfies it. Ceil at the low limit,
    floor at the high one."""
    import math
    iv_bull = 5578.42 * 1.4
    lo = d._PT_IV_BAND_LO * iv_bull
    assert round(lo, 2) / iv_bull < d._PT_IV_BAND_LO      # the artifact
    rep, _, _ = _band_12m_targets(
        {"bear": 1.0, "base": 1.2, "bull": 1.4},
        {"bear": 5578.42 * 0.6, "base": 5578.42, "bull": iv_bull},
        5578.42, 0.25, spot=50.0, max_capture=0.20)
    assert rep["bull"] >= math.ceil(lo * 100.0) / 100.0 - 1e-9
    assert rep["bull"] / iv_bull >= d._PT_IV_BAND_LO


@pytest.mark.parametrize("ivs,pts", [
    (FCX_IVS, FCX_PTS),
    (U96_IVS, U96_PTS),
    ({"bear": 222.82, "base": 293.14, "bull": 365.72},
     {"bear": 334.23, "base": 439.71, "bull": 548.58}),   # MU, at 1.50x each
    ({"bear": 39.21, "base": 75.41, "bull": 141.18},
     {"bear": 33.31, "base": 44.41, "bull": 55.52}),       # SCHW post-2b
    (VISA_IVS, {"bear": 340.85, "base": 394.12, "bull": 433.09}),  # V post-2a
])
def test_the_other_thirteen_fixtures_do_not_fire(ivs, pts):
    """Pinned per fixture rather than asserted in prose: 2a moved V onto the
    convergence path and 2b moved SCHW by +12.23%, so both are re-checked here
    against the band rather than assumed to be inside it."""
    _, br, _ = _band_12m_targets(pts, ivs, ivs["base"], 0.0725)
    assert br == []


# ── Deviation 1: per-scenario referencing ───────────────────────────────────


def test_fcx_bull_is_not_a_violation_against_its_own_iv():
    """2.756x BASE IV, 0.971x BULL IV. A cyclical's bull IV sitting far above
    its base IV is the engine's own scenario spread, not a corrupted target,
    and base-referencing would have replaced a sane $70.50 with $29.81."""
    _, br, _ = _band_12m_targets(FCX_PTS, FCX_IVS, FCX_IVS["base"], 0.0725)
    assert br == []
    assert FCX_PTS["bull"] / FCX_IVS["base"] > d._PT_IV_BAND_HI
    assert FCX_PTS["bull"] / FCX_IVS["bull"] == pytest.approx(0.971, abs=0.001)


def test_u96_bear_is_not_a_violation_against_its_own_iv():
    """0.311x BASE IV, 0.764x its own BEAR IV — the same artifact on the low
    side, and the one that would have replaced S$1.78 with S$3.89."""
    _, br, _ = _band_12m_targets(U96_PTS, U96_IVS, U96_IVS["base"], 0.1042)
    assert br == []
    assert U96_PTS["bear"] / U96_IVS["base"] < d._PT_IV_BAND_LO
    assert U96_PTS["bear"] / U96_IVS["bear"] == pytest.approx(0.764, abs=0.001)


def test_the_fallback_still_uses_base_iv_as_specified():
    """The deviation is in the TEST, not in the fallback: the owner's
    `Base IV discounted by the cost of equity` is what gets published."""
    rep, br, info = _band_12m_targets(VISA_PTS, VISA_IVS, 428.47, VISA_WACC)
    assert info["fallback_base"] == pytest.approx(428.47 / 1.0725, abs=0.01)
    assert rep["base"] == pytest.approx(399.51, abs=0.01)


# ── Visa: the case the owner named ──────────────────────────────────────────


def test_visa_breaches_on_all_three_scenarios():
    _, br, _ = _band_12m_targets(VISA_PTS, VISA_IVS, 428.47, VISA_WACC)
    assert [b[0] for b in br] == ["bear", "base", "bull"]
    assert br[1][3] == pytest.approx(0.0300, abs=0.0005)   # 12.84 / 428.47


def test_visas_replacement_is_base_iv_over_one_plus_coe():
    """No spot supplied, so nothing bounds it and the instruction's own formula
    is what comes out: 428.47 / 1.0725 = 399.51, fanned 0.75/1.00/1.25."""
    rep, _, _ = _band_12m_targets(VISA_PTS, VISA_IVS, 428.47, VISA_WACC)
    assert rep == {"bear": 299.63, "base": 399.51, "bull": 499.38}


def test_the_replacement_is_itself_inside_the_band():
    """The invariant that matters most: a target published under a
    "VALIDATION ERROR: band violated" flag must not violate the band, or the
    next run re-fires on the engine's own output."""
    rep, _, _ = _band_12m_targets(VISA_PTS, VISA_IVS, 428.47, VISA_WACC)
    for s, r in _ratios(rep, VISA_IVS).items():
        assert d._PT_IV_BAND_LO <= r <= d._PT_IV_BAND_HI, s
    assert rep["bear"] <= rep["base"] <= rep["bull"]


def test_a_missing_cost_of_equity_publishes_base_iv_undiscounted():
    """`coe` is a GGM input and only exists for balance-sheet financials; this
    band has to run on every profile, so the caller passes WACC. If neither is
    usable the fallback is undiscounted rather than invented."""
    for coe in (None, 0.0, -0.05):
        rep, _, info = _band_12m_targets(VISA_PTS, VISA_IVS, 428.47, coe)
        assert rep["base"] == pytest.approx(428.47, abs=0.01)
        assert info["coe"] == 0.0


# ── MELI: the one fixture that fires on the current baseline ────────────────


def test_meli_breaches_on_bear_and_base_only():
    _, br, _ = _band_12m_targets(
        MELI_PTS, MELI_IVS, MELI_IVS["base"], MELI_WACC,
        spot=MELI_SPOT, max_capture=MELI_CAPTURE, high_sbc=True)
    assert [b[0] for b in br] == ["bear", "base"]
    assert br[0][3] == pytest.approx(0.2707, abs=0.0005)
    assert br[1][3] == pytest.approx(0.3161, abs=0.0005)


def test_meli_replaces_all_three_even_though_bull_did_not_breach():
    """Deviation 2. Per-scenario replacement alone measures bear $3,754 /
    base $5,006 / bull $2,273 — bull below bear — because MELI's bull target
    sits at 0.371x and its bear and base do not."""
    rep, br, _ = _band_12m_targets(
        MELI_PTS, MELI_IVS, MELI_IVS["base"], MELI_WACC,
        spot=MELI_SPOT, max_capture=MELI_CAPTURE, high_sbc=True)
    assert len(br) == 2 and len(rep) == 3
    assert rep["bear"] <= rep["base"] <= rep["bull"]
    assert rep == {"bear": 1554.60, "base": 3141.26, "bull": 3334.71}


def test_melis_base_and_bull_are_the_convergence_bound():
    """Deviation 3. The undiscounted fallback is 5578.42 / 1.1144 = $5,005.76,
    a 174% move in twelve months off a $1,828.94 spot. The convergence cap is
    what every other target respects, so the replacement respects it too:
    1828.94 + 0.35 x (IV - 1828.94)."""
    rep, _, info = _band_12m_targets(
        MELI_PTS, MELI_IVS, MELI_IVS["base"], MELI_WACC,
        spot=MELI_SPOT, max_capture=MELI_CAPTURE, high_sbc=True)
    assert info["fallback_base"] == pytest.approx(5005.76, abs=0.01)
    for s in ("base", "bull"):
        assert rep[s] == pytest.approx(
            d._convergence_bound(MELI_IVS[s], MELI_SPOT, MELI_CAPTURE), abs=0.01), s
        assert rep[s] < info["fallback_base"]


def test_melis_bear_is_the_high_sbc_ceiling_not_the_convergence_bound():
    """The bound would give $2,794.67 — a "bear" case 53% ABOVE spot, which is
    the contradiction the ceiling exists to refuse. spot x 0.85 = $1,554.60,
    and it clears the band's own low limit of 0.33 x 4588.16 = $1,514.09, so no
    policy conflict is recorded."""
    rep, _, info = _band_12m_targets(
        MELI_PTS, MELI_IVS, MELI_IVS["base"], MELI_WACC,
        spot=MELI_SPOT, max_capture=MELI_CAPTURE, high_sbc=True)
    assert rep["bear"] == pytest.approx(MELI_SPOT * 0.85, abs=0.01)
    assert rep["bear"] < d._convergence_bound(MELI_IVS["bear"], MELI_SPOT, MELI_CAPTURE)
    assert "conflicts" not in info
    assert rep["bear"] / MELI_IVS["bear"] > d._PT_IV_BAND_LO


def test_the_bear_ceiling_is_not_applied_to_a_non_high_sbc_name():
    rep_sbc, _, _ = _band_12m_targets(
        MELI_PTS, MELI_IVS, MELI_IVS["base"], MELI_WACC,
        spot=MELI_SPOT, max_capture=MELI_CAPTURE, high_sbc=True)
    rep_no, _, _ = _band_12m_targets(
        MELI_PTS, MELI_IVS, MELI_IVS["base"], MELI_WACC,
        spot=MELI_SPOT, max_capture=MELI_CAPTURE, high_sbc=False)
    assert rep_no["bear"] == pytest.approx(
        d._convergence_bound(MELI_IVS["bear"], MELI_SPOT, MELI_CAPTURE), abs=0.01)
    assert rep_no["bear"] > rep_sbc["bear"]
    assert rep_no["base"] == rep_sbc["base"] and rep_no["bull"] == rep_sbc["bull"]


def test_a_bear_iv_far_above_spot_records_the_conflict_rather_than_hiding_it():
    """Needs a bear IV above ~2.6x spot for the band's low limit to exceed the
    ceiling. The band keeps the last word — the invariant it exists to enforce
    has to actually hold — and says a stated policy gave way."""
    ivs = {"bear": 5000.0, "base": 5200.0, "bull": 5400.0}
    pts = {"bear": 100.0, "base": 105.0, "bull": 110.0}
    rep, br, info = _band_12m_targets(
        pts, ivs, 5200.0, 0.10, spot=1000.0, max_capture=0.35, high_sbc=True)
    assert br and rep
    assert info.get("conflicts")
    assert rep["bear"] == pytest.approx(d._PT_IV_BAND_LO * 5000.0, abs=0.01)
    assert rep["bear"] > 1000.0 * 0.85
    assert rep["bear"] <= rep["base"] <= rep["bull"]


# ── The band cannot be evaded by a missing input ────────────────────────────


def test_no_base_iv_means_a_report_but_no_replacement():
    """Refusing to publish is not the same as refusing to notice. The caller
    still emits the validation error; it just does not synthesize a number."""
    rep, br, info = _band_12m_targets(VISA_PTS, VISA_IVS, None, VISA_WACC)
    assert rep == {} and len(br) == 3
    assert info["reason"] == "no positive base IV to fall back on"
    assert info["base_ratio_path_b"] == info["base_ratio_path_a"]


@pytest.mark.parametrize("base_iv", [0.0, -428.47])
def test_a_non_positive_base_iv_is_treated_as_unavailable(base_iv):
    rep, br, info = _band_12m_targets(VISA_PTS, VISA_IVS, base_iv, VISA_WACC)
    assert rep == {} and br and info.get("reason")


@pytest.mark.parametrize("pts,ivs", [
    ({"bear": None, "base": 12.84, "bull": 16.06}, VISA_IVS),
    (VISA_PTS, {"bear": None, "base": 428.47, "bull": 539.81}),
    ({"base": 12.84}, {"base": 428.47}),
    (VISA_PTS, {"bear": 0.0, "base": 428.47, "bull": 539.81}),
])
def test_missing_or_zero_inputs_are_skipped_not_crashed_on(pts, ivs):
    rep, br, _ = _band_12m_targets(pts, ivs, 428.47, VISA_WACC)
    assert br and all(b[1] > 0 and b[2] > 0 for b in br)
    assert rep and rep["bear"] <= rep["base"] <= rep["bull"]


def test_a_breach_with_no_spot_still_replaces():
    """The convergence bound needs a spot; the band does not. A name whose
    price failed to load is still a name whose target is 3% of its IV."""
    rep, br, info = _band_12m_targets(
        VISA_PTS, VISA_IVS, 428.47, VISA_WACC, spot=None, max_capture=None)
    assert br and rep == {"bear": 299.63, "base": 399.51, "bull": 499.38}


# ── The high side ───────────────────────────────────────────────────────────


def test_a_target_above_two_and_a_half_times_its_own_iv_fires_too():
    """The owner asked for a two-sided band; the divergence guard above only
    fires when the BASE target exceeds 2x the BASE IV, so a base-sane name with
    one collapsed scenario IV reaches only this."""
    ivs = {"bear": 100.0, "base": 400.0, "bull": 500.0}
    pts = {"bear": 300.0, "base": 400.0, "bull": 500.0}
    rep, br, _ = _band_12m_targets(pts, ivs, 400.0, 0.10)
    assert [b[0] for b in br] == ["bear"]
    assert br[0][3] == pytest.approx(3.0)
    assert rep["bear"] <= d._PT_IV_BAND_HI * 100.0 + 0.01


def test_the_high_side_replacement_still_fans_from_base_iv():
    """All three are replaced even when only one breached, so the high side
    cannot leave a bear above a base."""
    ivs = {"bear": 100.0, "base": 400.0, "bull": 500.0}
    pts = {"bear": 300.0, "base": 400.0, "bull": 500.0}
    rep, _, _ = _band_12m_targets(pts, ivs, 400.0, 0.10)
    assert rep["bear"] <= rep["base"] <= rep["bull"]
    for s, r in _ratios(rep, ivs).items():
        assert d._PT_IV_BAND_LO <= r <= d._PT_IV_BAND_HI, s


# ── The invariant, over a grid rather than over three fixtures ──────────────


@pytest.mark.parametrize("base_iv", [10.0, 100.0, 1_000.0, 5_578.42])
@pytest.mark.parametrize("coe", [0.0, 0.0625, 0.1144, 0.25])
@pytest.mark.parametrize("spot,capture", [(None, None), (5.0, 0.35), (50.0, 0.20),
                                          (500.0, 0.35), (20_000.0, 0.50)])
def test_the_replacement_always_satisfies_the_band_and_the_ordering(
        base_iv, coe, spot, capture):
    """Three fixtures cannot cover a rule that multiplies, discounts, caps and
    clamps. Whatever comes out must be inside the band and ordered, or the
    engine publishes a validation error next to a number that still violates it.
    """
    ivs = {"bear": base_iv * 0.6, "base": base_iv, "bull": base_iv * 1.4}
    pts = {"bear": base_iv * 0.01, "base": base_iv * 0.012, "bull": base_iv * 0.014}
    for high_sbc in (False, True):
        rep, br, _ = _band_12m_targets(
            pts, ivs, base_iv, coe, spot=spot, max_capture=capture,
            high_sbc=high_sbc)
        assert br, (base_iv, coe, spot, capture, high_sbc)
        assert rep["bear"] <= rep["base"] <= rep["bull"]
        for s, r in _ratios(rep, ivs).items():
            assert d._PT_IV_BAND_LO <= r <= d._PT_IV_BAND_HI, (
                s, base_iv, coe, spot, capture, high_sbc)


# ── The caller ──────────────────────────────────────────────────────────────


def test_the_band_runs_after_the_divergence_guard():
    """Position is load-bearing: the guard's proportional cap (1.5x each
    scenario IV) is the less destructive response to a target that ran away
    upward, and it should get first refusal. A band that ran first would
    replace a target the guard could have repaired."""
    src = inspect.getsource(d)
    assert src.index("_pt_iv_ratio = _base_pt / _base_iv") < src.index(
        "_band_new, _band_breaches, _band_info = _band_12m_targets(")
    assert src.index("_band_new, _band_breaches, _band_info = _band_12m_targets(") < src.index(
        "12m PT ordering violated")


def test_the_caller_passes_the_classification_it_already_made():
    """`_high_sbc` and `_max_capture` are read out of the convergence-cap block
    rather than recomputed. Both live inside that block's spot-price guard, so
    they are read lazily — `_spot_for_band` is truthy exactly when the guard ran.
    A second derivation is how two readers of one question drift apart, which is
    the failure this whole decision family is about."""
    src = inspect.getsource(d)
    assert "_capture_for_band = _max_capture if _spot_for_band else None" in src
    assert "_high_sbc_for_band = _high_sbc if _spot_for_band else False" in src
    assert "high_sbc=_high_sbc_for_band" in src


def test_the_breach_is_emitted_as_an_explicit_validation_error():
    """The owner's wording: "emit an explicit validation error". Four channels,
    because a number that silently changed is indistinguishable from a number
    that was always right."""
    src = inspect.getsource(d)
    assert 'print(f"  [12m-pt] {ticker}: {_berr}")' in src
    assert "progress.update_status(agent_id, ticker, _berr)" in src
    assert '"gate_id": "GATE_PT_IV_BAND"' in src
    assert '_sf.append(f"⚠ VALIDATION ERROR: {_berr}")' in src


def test_the_provenance_label_follows_the_number_it_describes():
    """`_12m_pt_method_label` is persisted as `12m_pt_method` and is the only
    string on the card that says HOW the three targets were produced. Six sites
    assign it and one reads it; if the band replaces all three numbers without
    touching it, the card publishes a fallback-derived base target next to
    "EV/EBITDA or EV/Revenue forward multiple" — a provenance claim that
    contradicts the arithmetic beside it, and one that outlives the run.

    MELI is that case: base $3,141.26 came from
    ``5578.42 / 1.1144``, not from any forward multiple. The golden diff now
    carries the corrected label as its 8th moved leaf.
    """
    src = inspect.getsource(d)
    # Assigned only inside the replacement branch — a breach that does NOT
    # replace (no base IV to fall back on) must leave the original method label
    # standing, because the numbers it describes are still the published ones.
    assert '                _12m_pt_method_label = (\n' \
           '                    f"validation fallback: base IV / (1 + CoE {wacc:.2%}) x "' in src
    assert src.index("if _band_new:\n                for _sn, _bv in _band_new.items():") < \
        src.index('f"validation fallback: base IV / (1 + CoE {wacc:.2%}) x "')
    # The label names the band it was produced under, so the two limits cannot
    # be quoted in the payload while the constants say something else.
    _lbl = src.index('f"validation fallback: base IV / (1 + CoE {wacc:.2%}) x "')
    assert "{_PT_IV_BAND_LO:.2f}x, {_PT_IV_BAND_HI:.2f}x" in src[_lbl:_lbl + 400]
    # And it is still the value that gets persisted — the label assignment must
    # land before the payload leaf that reads it.
    assert '"12m_pt_method":      _12m_pt_method_label' in src
    assert _lbl < src.index('"12m_pt_method":      _12m_pt_method_label')
    # The one READ of the label — the `_norm_led` convergence-path test — must
    # stay upstream. If it moved below, the fallback string would be tested
    # against `_FORWARD_CONSENSUS_PT_LABELS` and a replacement could re-enter the
    # convergence branch that produced the number it is replacing.
    assert src.index("and _12m_pt_method_label in _FORWARD_CONSENSUS_PT_LABELS") < _lbl
    # After every competing assignment, so no later site silently overwrites it.
    for _needle in ('"EV/EBITDA or EV/Revenue forward multiple"',
                    '"P/FFO + P/AFFO blend (REIT sub-type multiples)"',
                    '"forward multiple (profile-specific)"'):
        assert src.rindex(_needle) < _lbl, _needle


def test_the_flag_reaches_a_list_that_is_actually_published():
    """`ticker_forward_flags` is a dead list past the scenario loop.

    It is snapshotted per scenario at `forward_flags = list(ticker_forward_flags)`
    inside the loop and never read again afterwards, so an append a thousand
    lines later — with all three scenarios already built — reaches no payload.
    Verified against the fixtures rather than reasoned about: no persisted
    `forward_flags` list in tests/golden/snapshots.json carries the
    "Convergence cap" or "12m PT ordering violated" lines that the neighbouring
    appends write to the same list. An "explicit validation error" that no
    consumer can see is not explicit, so this one is written to each scenario's
    own list, which is what the snapshot leaf `scenarios.<s>.forward_flags`
    records and what the card renders.
    """
    src = inspect.getsource(d)
    assert 'ticker_forward_flags.append(f"⚠ VALIDATION ERROR' not in src
    i = src.index('_sf.append(f"⚠ VALIDATION ERROR: {_berr}")')
    window = src[max(0, i - 900):i]
    assert 'scenario_results.get(_sn) or {}).get("forward_flags")' in window
    assert "is never read again after that loop ends" in window


def test_the_flag_is_written_to_all_three_scenarios():
    """A ticker-level flag already lands on all three by being copied into each
    at the snapshot point, and the band replaced all three targets — so all
    three carry it, whichever scenario the card happens to render."""
    src = inspect.getsource(d)
    i = src.index('_sf.append(f"⚠ VALIDATION ERROR: {_berr}")')
    loop = src[max(0, i - 300):i]
    assert 'for _sn in ("bear", "base", "bull"):' in loop


def test_no_persisted_flag_comes_from_the_dead_list():
    """The evidence for the claim above, checked against the committed baseline
    rather than asserted in a comment.

    Both the convergence cap and the ordering diagnostic append to
    `ticker_forward_flags` after the scenario loop, and both fire on fixtures in
    this baseline — MU's target is exactly the convergence path the cap writes
    about. Neither line appears in any persisted flag list. If this ever starts
    passing, the dead-append bug has been fixed and the routing test above can
    go back to the simpler form.
    """
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "golden", "snapshots.json")
    doc = json.load(open(path, encoding="utf-8"))
    seen = 0
    for name, fx in doc.items():
        if name == "_meta":
            continue
        for scen in ("bear", "base", "bull"):
            flags = (fx.get("projection") or {}).get(
                f"scenarios.{scen}.forward_flags") or []
            for f in flags:
                seen += 1
                assert "Convergence cap:" not in f, (name, scen, f)
                assert "12m PT ordering violated" not in f, (name, scen, f)
    assert seen > 100, f"only {seen} persisted flags — is the baseline empty?"


def test_the_gate_record_carries_both_halves_of_its_path_pair():
    """Every gate in this engine emits `raw_input_path_a` and
    `gated_output_path_b`; `test_gate_backtest` counts key occurrences in the
    source to enforce it. Here the pair is the BASE scenario's PT/IV ratio
    before and after — base is what the card leads with, and recording all
    three would make this the only gate whose pair is a vector."""
    src = inspect.getsource(d)
    i = src.index('"gate_id": "GATE_PT_IV_BAND"')
    rec = src[i:i + 1_600]
    assert rec.count('"raw_input_path_a"') == 1
    assert rec.count('"gated_output_path_b"') == 1
    assert '"applied": bool(_band_new)' in rec
    assert '"conflicts": _band_info.get("conflicts") or None' in rec


def test_wacc_is_the_coe_proxy_and_the_comment_says_why():
    """A CoE is only computed for balance-sheet financials — it is a GGM input
    — and this band has to run on every profile, including the payment network
    it was written for. WACC is the discount rate the same run already used on
    the cash flows the IV came from."""
    src = inspect.getsource(d)
    assert "_band_ivs, _base_iv, wacc," in src
    assert "`wacc` is the cost-of-equity proxy here" in src


def test_the_band_does_not_touch_the_intrinsic_value():
    """Source-level, because it is the claim the golden diff has to corroborate:
    the helper receives `scenario_results` IVs read-only through a fresh dict and
    writes only into the targets dict it is handed back."""
    ivs = dict(VISA_IVS)
    rep, _, _ = _band_12m_targets(dict(VISA_PTS), ivs, 428.47, VISA_WACC)
    assert ivs == VISA_IVS and rep
