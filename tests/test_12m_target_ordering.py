"""
Guard: 12-month price targets must satisfy bear <= base <= bull.

The convergence cap used to be gated on `scen_iv > spot and pt > spot`, so a
scenario whose intrinsic value fell BELOW spot skipped the cap entirely and
kept its raw uncapped multiple, while its siblings were compressed toward
spot. That inverts the ordering for any name where bear IV < spot < base IV.

Observed on 02888.HK (Standard Chartered), run 53399960, 2026-09-02:

    spot 228.60
    IVs      bear 215.89  base 258.04  bull 298.10   (correctly ordered)
    targets  bear 296.36  base 238.90  bull 252.93   (bear highest of the three)

The bear target was the raw GGM multiple 395.15 x 0.75; base and bull were
capped to spot + 0.35 x (IV - spot). Making the bound sign-agnostic restores
ordering by construction: the final target is min() of two monotonic
sequences (raw multiple x 0.75/1.00/1.25, and the per-scenario bound).
"""
from __future__ import annotations

import itertools

import pytest

from src.agents.analysis.dcf_agent import _convergence_bound

# Profile-dependent capture rates used by the engine.
STABLE, HIGH_SBC, REACCEL = 0.35, 0.20, 0.30


def _targets(ggm: float, ivs: dict[str, float], spot: float, capture: float) -> dict[str, float]:
    """Reproduce the engine's per-scenario target: raw multiple, then capped."""
    smult = {"bear": 0.75, "base": 1.00, "bull": 1.25}
    out = {}
    for sn, m in smult.items():
        pt = ggm * m
        out[sn] = round(min(pt, _convergence_bound(ivs[sn], spot, capture)), 2)
    return out


def test_02888_hk_regression():
    """The exact shipped inputs must now produce an ordered set."""
    spot = 228.60
    ivs = {"bear": 215.89, "base": 258.04, "bull": 298.10}
    t = _targets(395.15, ivs, spot, STABLE)

    assert t["bear"] <= t["base"] <= t["bull"], t
    # base/bull were already correct — they must not move.
    assert t["base"] == pytest.approx(238.90, abs=0.01)
    assert t["bull"] == pytest.approx(252.93, abs=0.01)
    # bear was 296.36 (raw, uncapped); it is now bounded below spot.
    assert t["bear"] == pytest.approx(224.15, abs=0.01)
    assert t["bear"] < spot, "a bear target above spot contradicts the scenario"


def test_bound_is_symmetric_about_spot():
    spot, capture = 100.0, 0.35
    assert _convergence_bound(200.0, spot, capture) == pytest.approx(135.0)
    assert _convergence_bound(0.0, spot, capture) == pytest.approx(65.0)
    assert _convergence_bound(spot, spot, capture) == pytest.approx(spot)


def test_bound_only_ever_tightens():
    """A target already inside the bound is untouched."""
    spot, capture = 100.0, 0.35
    bound = _convergence_bound(200.0, spot, capture)   # 135
    assert min(120.0, bound) == 120.0    # below bound -> unchanged
    assert min(400.0, bound) == bound    # above bound -> clamped


@pytest.mark.parametrize("capture", [STABLE, HIGH_SBC, REACCEL])
def test_ordering_holds_across_the_input_space(capture):
    """Ordered IVs + ordered raw multiples must yield ordered targets."""
    spot = 228.60
    grid = [40, 120, 215, 229, 260, 400, 900]
    checked = 0
    for a, b, c in itertools.product(grid, repeat=3):
        if not (a < b < c):
            continue
        ivs = {"bear": float(a), "base": float(b), "bull": float(c)}
        for ggm in (50.0, 200.0, 395.15, 1200.0):
            t = _targets(ggm, ivs, spot, capture)
            assert t["bear"] <= t["base"] <= t["bull"], (ggm, ivs, capture, t)
            checked += 1
    # C(7,3) ordered triples x 4 GGM anchors = 140.
    assert checked == 140, f"grid changed shape ({checked} combinations)"


def test_old_gated_behaviour_is_preserved_where_it_used_to_fire():
    """Cases the previous guard already handled must be unchanged.

    The fix must only affect scenarios the old guard skipped, so anything
    with IV above spot AND a target above spot keeps its previous value.
    """
    spot, capture = 228.60, STABLE
    for iv in (240.0, 300.0, 500.0):
        for pt in (250.0, 400.0, 900.0):
            old_bound = spot + capture * (iv - spot)     # old formula, guard passed
            old = round(min(pt, old_bound), 2)
            new = round(min(pt, _convergence_bound(iv, spot, capture)), 2)
            assert old == new, (iv, pt, old, new)
