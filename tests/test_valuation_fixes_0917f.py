"""Item 3b: the audit fields that decided two gates are now persisted (2026-09-17).

Before this change the golden baseline could see `growth_premium` and `tgr` — the
two OUTPUTS — but not `forward_roic`, `roic_source` or `_sector_g_avg`, the three
INPUTS that produce them. The cost was paid in full during the Gate B audit: to
find out which fixtures had terminal growth zeroed and why, the ROIC had to be
regex-ed back out of a sentence,

    "Gate B (bear): Forward ROIC (-7.3% [Y10 projected]) < threshold (5.9%)"

across 49 archived production rows (`scratchpad/census_gateB_prod.py`) and then
again across four live ones (`scratchpad/verify_gateB_prod.py`). A value that
decides whether terminal growth survives is a field, not a substring. The owner's
item 3 asked for exactly this, "to eliminate the need for offline blind-fitting in
future audits", and the reason it matters is that blind-fitting is what let the
`× 10` survive: with the ROIC in the payload, `tgr == 0` and `forward_roic > wacc`
in the same document is a contradiction a diff can see.

Four things are now persisted, and each immediately paid for itself:

`forward_roic` / `roic_source` (per scenario)
    Agrees with the Gate B prose to display precision on all 8 fixtures where the
    gate still fires, and `roic_source` agrees with the prose's bracket on all 8.
    It also settles a question the audit could only answer by inference: **D05_SI
    is the one fixture with no ROIC at all** — `None` with `roic_source = "n/a"` in
    all three scenarios — which is why Gate B never reached it. Pre-3b, "the gate
    did not fire" and "the gate had nothing to judge" were the same absence of a
    flag.

`sector_g_avg` / `sector_g_avg_basis` (per scenario)
    The basis carries the COHORT, and the cohort is the whole of item 3a. Read off
    the baseline: every US fixture resolves `static`/`US`, so the `market_cap`
    divergence at the `_peer_for_gp` call site cannot bite them; all five HK/SG
    fixtures that resolve at all resolve the **`all`** cohort — not one gets
    `large`, while the three legs inside `_compute_method_value` pass a real
    `market_cap` and do. And U96_SI resolves nothing: no `_comp_basis` at all, so
    `_sector_g_avg` falls back to the hardcoded 0.08 — a fabricated sector average
    with no provenance, sitting beside BN4_SI, which has the SAME profile and
    measures 0.0643 from 22 peers.

`composite_bridge` (top level, one per run)
    The composite published its multiple via `composite_applied` and nothing else.
    Now the Q/R/C decomposition, the weights, the sub-score notes, the cap and the
    `bank_clamp` are all in the baseline. Three findings fell out: MELI is the only
    capped fixture (raw 2.174 against `cap_high` 1.85); `final_multiplier` is the
    PRE-clamp value, so the two money-center banks publish 1.278 and 1.453 there
    while `composite_applied` carries the clamped 1.100 — Decision 1's narrowing is
    now checkable, and so is its negative case (SCHW, profile `Brokerage`, has no
    `bank_clamp` leaf at all); and **five fixtures run the composite on zero
    extracted KPIs** (AAPL, C38U_SI, FCX, SCHW, V), landing on `composite_score`
    50 and a multiplier of exactly 1.0 labelled "in-band". Three of those five are
    names the owner asked about for persistent undervaluation: their composite
    contributes nothing not because they are average but because nothing was
    measured.

`normalized_net_income` (top level)
    Read by several legs, recorded by none. Equals the value in the
    "Normalized NI: TTM … → 5y-cycle …" prose on all four fixtures that carry that
    flag, and is in the same currency as `revenue_base` — which the two Alibaba
    lines prove, since their scale differs 7.8× while their net margin is identical
    to four decimals (0.0947).

The change is ADDITIVE, verified before the baseline was regenerated: all 14
fixtures replayed with zero removed leaves, zero changed leaves and bit-identical
`base_iv` (`scratchpad/drive_delta_additive.py`). Nothing here moves a valuation.
The `market_cap` alignment that WILL is item 3a, deliberately left for its own
named golden update — bundling it would make the resulting delta unattributable.
"""
from __future__ import annotations

import inspect
import json
import os
import re

import pytest

from src.agents.analysis import dcf_agent
from src.data.regional_comps import MIN_INDUSTRY_PEERS

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SNAPSHOTS = os.path.join(_REPO, "tests", "golden", "snapshots.json")
_SCENARIOS = ("bear", "base", "bull")

with open(_SNAPSHOTS, encoding="utf-8") as _fh:
    _SNAP = json.load(_fh)


def _fixtures() -> dict:
    """The 14 fixture entries, without the `_meta` sibling that has no projection."""
    return {k: v for k, v in _SNAP.items() if k != "_meta"}


def _proj(name: str) -> dict:
    return _fixtures()[name]["projection"]


_ALL = sorted(_fixtures())

#: The seven US filings. They resolve their peer multiples from the static table,
#: which is why the `market_cap` divergence documented at the `_peer_for_gp` call
#: site cannot reach them.
_US = ("AAPL", "BABA", "COST", "FCX", "MELI", "MU", "SCHW", "V")

#: The HK/SG fixtures that DO resolve a live cohort. Every one of them lands on
#: the whole-grouping `all` cohort, never the size-matched `large` one.
_LIVE_COHORT = {
    #            basis      level     cohort  peers
    "02888_HK": ("industry", "all", 7),
    "09988_HK": ("industry", "all", 18),
    "BN4_SI":   ("sector",   "all", 22),
    "C38U_SI":  ("industry", "all", 5),
    "D05_SI":   ("sector",   "all", 9),
}

#: The one fixture with no peer set at all, so `_sector_g_avg` is the default.
_DEFAULT_G_AVG = 0.08

#: Gate B still fires on these eight, so each carries the flag prose to compare
#: the persisted field against.
_STILL_FIRES = ("09988_HK", "BABA", "BN4_SI", "C38U_SI", "FCX", "MU", "SCHW",
                "U96_SI")

#: Gate B deactivated on these five when the `× 10` was dropped: their corrected
#: bear ROIC clears WACC, so terminal growth survives.
_DEACTIVATED = ("02888_HK", "AAPL", "COST", "MELI", "V")

#: The two money-center banks, and the only two fixtures with a `bank_clamp` leaf.
_BANKS = ("02888_HK", "D05_SI")
_BANK_CLAMP_TO = 1.100

#: Fixtures whose composite ran on ZERO extracted KPIs across both scored bands.
_ZERO_KPI = ("AAPL", "C38U_SI", "FCX", "SCHW", "V")

_GATE_B_RE = re.compile(
    r"^Gate B \(bear\): Forward ROIC \((-?[\d.]+)%(?:\s*\[([^\]]+)\])?\)")
_NORM_NI_RE = re.compile(r"Normalized NI: TTM .*? 5y-cycle [S$]*\$?([\d.]+)B")


def _bridge(name: str) -> dict:
    """The composite bridge as a dict, reassembled from its dotted leaves."""
    p = _proj(name)
    pre = "composite_bridge."
    return {k[len(pre):]: v for k, v in p.items() if k.startswith(pre)}


def _basis(name: str, scenario: str) -> dict | None:
    """The peer provenance for one scenario.

    `_flatten` expands a dict into dotted leaves, so a resolved basis arrives as
    three sibling keys and an unresolved one as a single key holding `None`. Both
    shapes are real and the difference is the finding, so neither may be papered
    over by a `.get` chain that returns `None` for both.
    """
    p = _proj(name)
    root = f"scenarios.{scenario}.sector_g_avg_basis"
    if root in p:
        assert p[root] is None, f"{name}: {root} is a scalar but not None"
        return None
    sub = {k[len(root) + 1:]: v for k, v in p.items() if k.startswith(root + ".")}
    assert sub, f"{name}: {root} is neither a leaf nor a parent"
    return sub


def _gate_b_bear(name: str) -> tuple[float, str | None] | None:
    """`(roic_pct, source)` from the bear flag prose, or None if it did not fire."""
    for flag in _proj(name).get("scenarios.bear.forward_flags") or []:
        m = _GATE_B_RE.match(flag)
        if m:
            return float(m.group(1)), m.group(2)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# A. The change was additive: nothing was lost, nothing moved
# ══════════════════════════════════════════════════════════════════════════════


def test_every_fixture_carries_the_new_scenario_leaves():
    for name in _ALL:
        p = _proj(name)
        for s in _SCENARIOS:
            assert f"scenarios.{s}.forward_roic" in p, (name, s)
            assert f"scenarios.{s}.roic_source" in p, (name, s)
            assert f"scenarios.{s}.sector_g_avg" in p, (name, s)


def test_every_fixture_carries_the_new_top_level_leaves():
    for name in _ALL:
        p = _proj(name)
        assert "normalized_net_income" in p, name
        assert any(k.startswith("composite_bridge.") for k in p), name


def test_roic_source_only_ever_takes_one_of_the_three_chain_values():
    """The assignment is an exhaustive if/elif/else, so the field is total. A
    fourth value would mean a new branch was added without deciding what it
    publishes."""
    allowed = {"Y10 projected", "trailing (fallback)", "n/a"}
    for name in _ALL:
        for s in _SCENARIOS:
            v = _proj(name)[f"scenarios.{s}.roic_source"]
            assert v in allowed, (name, s, v)


# ══════════════════════════════════════════════════════════════════════════════
# B. The persisted ROIC is the ROIC the gate acted on
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", _STILL_FIRES)
def test_the_persisted_bear_roic_is_the_one_gate_b_printed(name):
    """The whole point of persisting it. Tolerance is half a display digit, not
    zero, because the field is rounded to 4dp and the prose to 0.1pp — a double
    rounding that can disagree in the last place. It is still a strong test: the
    `× 10` defect moved this value by a factor of −0.80 (tens of percentage
    points) and a sign flip moves it by twice the value."""
    prose = _gate_b_bear(name)
    assert prose is not None, f"{name}: expected Gate B to fire in bear"
    field = _proj(name)["scenarios.bear.forward_roic"]
    assert field is not None
    assert abs(prose[0] - round(field * 100, 1)) <= 0.05, (name, prose, field)


@pytest.mark.parametrize("name", _STILL_FIRES)
def test_the_persisted_provenance_is_the_one_gate_b_printed(name):
    prose = _gate_b_bear(name)
    assert prose is not None and prose[1] is not None, f"{name}: no source bracket"
    assert _proj(name)["scenarios.bear.roic_source"] == prose[1]


def test_the_field_and_the_prose_never_disagree_about_whether_the_gate_fired():
    """A Gate B flag exists exactly where the persisted ROIC is at or below WACC,
    and nowhere else. This is the contradiction that would have caught the `× 10`
    from the payload alone."""
    for name in _ALL:
        p = _proj(name)
        roic, wacc = p["scenarios.bear.forward_roic"], p["wacc"]
        fired = _gate_b_bear(name) is not None
        if roic is None:
            assert not fired, f"{name}: gate fired with no ROIC to judge"
        else:
            assert fired == (roic <= wacc), (name, roic, wacc, fired)


def test_terminal_growth_survives_bear_exactly_where_the_roic_clears_wacc():
    """`tgr` is the gate's output; `forward_roic` and `wacc` are its inputs. All
    three are now in the same document, so the relationship is checkable rather
    than something an auditor has to re-derive from a log line."""
    for name in _ALL:
        p = _proj(name)
        roic, wacc, tgr = (p["scenarios.bear.forward_roic"], p["wacc"],
                           p["scenarios.bear.tgr"])
        if roic is None:
            assert tgr > 0.0, f"{name}: no ROIC, so the gate could not zero tgr"
        elif roic <= wacc:
            assert tgr == 0.0, (name, roic, wacc, tgr)
        else:
            assert tgr > 0.0, (name, roic, wacc, tgr)


_QUANT = 5e-5   # every one of these leaves is published rounded to 4 decimals


def _ratio_tol(ro_s: float, ro_base: float, fmb: float, md_s: float,
               md_base: float) -> float:
    """How far `ro_s / ro_base` may sit from `(fmb + md_s) / (fmb + md_base)`.

    Both sides are built from 4dp-rounded leaves, so neither is exact and a flat
    tolerance would either fail on rounding or be too loose to mean anything. This
    is the first-order propagation of ±5e-5 through all five inputs:

        ratio side   q * (1/|ro_base| + |ro_s| / ro_base**2)
        margin side  q * (1/|D| + |N| / D**2 + |D - N| / D**2),  N = fmb+md_s,
                                                               D = fmb+md_base

    The margin side dominates wherever `fmb` is small, because the ratio is then a
    quotient of two small numbers: COST has `fcf_margin_base` 0.0232 and
    `margin_delta_absolute` -0.0046, where -0.0046 is itself -0.00464 rounded —
    that single rounding moves the expected ratio by 1.8e-3, which is the whole of
    the observed gap.

    The headroom is the point: the `× 10` defect moved the bear ratio from 0.80 to
    -1.00, a 225% error, against a tolerance under 1%.
    """
    n, d = fmb + md_s, fmb + md_base
    left = _QUANT * (1.0 / abs(ro_base) + abs(ro_s) / (ro_base * ro_base))
    right = _QUANT * (1.0 / abs(d) + abs(n) / (d * d)
                      + abs(d - n) / (d * d))
    return left + right + 1e-4


def test_the_three_scenario_roics_are_the_base_one_scaled_by_the_margin_delta():
    """The invariant that would have caught the `× 10` from the payload alone, with
    no prose and no regex — which is the property item 3b was asked to buy.

    The Y10 ROIC is linear in the Y10 FCF margin (`_y10_ic` and `_y10_fcf` carry
    the same revenue multiplier, so it cancels), and the Y10 margin is
    `fcf_margin_base + margin_delta_absolute`. Both terms are now persisted, so the
    three scenarios must satisfy

        roic[s] / roic[base] == (fmb + md[s]) / (fmb + md[base])

    Under the defect the bear margin was `fmb * (10m - 9)` instead of `fmb * m`,
    which makes the left side -1.00 where the right side says 0.80 — a
    factor-of-ten error in an input, now visible as a broken ratio between two
    persisted numbers in the same document.

    Stated against the persisted margins rather than against the multiplier
    constants, so it still holds on a run where `guidance_margin_adj` moves the
    BASE margin off zero. All 14 fixtures currently have `md[base] == 0` (pinned by
    0917e), and `test_that_ratio_reduces_to_the_scenario_margin_multipliers` is the
    same identity with that term cancelled out — a tighter check that does not
    depend on two rounded inputs.
    """
    checked = 0
    for name in _ALL:
        p = _proj(name)
        fmb = p["fcf_margin_base"]
        ro = {s: p[f"scenarios.{s}.forward_roic"] for s in _SCENARIOS}
        md = {s: p[f"scenarios.{s}.margin_delta_absolute"] for s in _SCENARIOS}
        if any(v is None for v in ro.values()):
            continue                      # D05_SI: no ROIC in any scenario
        assert fmb + md["base"] != 0.0, (name, fmb, md)
        for s in ("bear", "bull"):
            checked += 1
            ratio = ro[s] / ro["base"]
            expected = (fmb + md[s]) / (fmb + md["base"])
            assert ratio == pytest.approx(
                expected,
                abs=_ratio_tol(ro[s], ro["base"], fmb, md[s], md["base"])), \
                (name, s, ro, md, fmb, ratio, expected)
    assert checked == 2 * (len(_ALL) - 1), checked


def test_that_ratio_reduces_to_the_scenario_margin_multipliers():
    """The reduction of the identity above, stated in the constants' own terms so a
    change to `_MARGIN_DELTA_MULT` shows up as a golden move with a name attached.
    `md = fmb * (m - 1)` makes `fmb` cancel, so this compares two ROICs against a
    constant and inherits rounding from the ROICs only. Bear/base is 0.80 and
    bull/base is 1.20 for standard profiles, 0.65 and 1.15 for the high-SBC one —
    and MELI is the only fixture in the second group."""
    from src.agents.analysis.dcf_agent import _MARGIN_DELTA_MULT, \
        _MARGIN_DELTA_MULT_HIGH_SBC
    checked = 0
    for name in _ALL:
        p = _proj(name)
        base = p["scenarios.base.forward_roic"]
        if base is None:
            continue
        mults = (_MARGIN_DELTA_MULT_HIGH_SBC if name == "MELI"
                 else _MARGIN_DELTA_MULT)
        for s in ("bear", "bull"):
            checked += 1
            ro_s = p[f"scenarios.{s}.forward_roic"]
            ratio = ro_s / base
            assert ratio == pytest.approx(
                mults[s], abs=_QUANT * (1.0 / abs(base)
                                        + abs(ratio) / abs(base)) + 1e-4), \
                (name, s, ratio, mults[s])
    assert checked == 2 * (len(_ALL) - 1), checked
    assert _MARGIN_DELTA_MULT["bear"] == 0.80
    assert _MARGIN_DELTA_MULT["bull"] == 1.20
    assert _MARGIN_DELTA_MULT_HIGH_SBC["bear"] == 0.65
    assert _MARGIN_DELTA_MULT_HIGH_SBC["bull"] == 1.15
    # MELI is the only high-SBC fixture in the set.
    for name in _ALL:
        assert (_proj(name)["profile"] in dcf_agent._HIGH_SBC_PROFILES) \
            == (name == "MELI"), name


@pytest.mark.parametrize("name", _DEACTIVATED)
def test_the_five_deactivations_are_five_roics_above_wacc(name):
    p = _proj(name)
    assert p["scenarios.bear.forward_roic"] > p["wacc"], name
    assert p["scenarios.bear.tgr"] > 0.0, name
    assert _gate_b_bear(name) is None, f"{name}: Gate B should not fire any more"


@pytest.mark.parametrize("name", _STILL_FIRES)
def test_the_eight_survivors_are_eight_positive_roics_below_wacc(name):
    """Positive, because the `× 10` had forced all eight negative and a negative
    ROIC is below any positive WACC by construction — the gate was firing on an
    arithmetic error rather than on economics. They still fire now, correctly."""
    p = _proj(name)
    roic = p["scenarios.bear.forward_roic"]
    assert roic is not None and roic > 0.0, (name, roic)
    assert roic <= p["wacc"], (name, roic, p["wacc"])


def test_schw_fires_by_six_percent_of_wacc_which_is_why_its_bear_column_is_flat():
    """SCHW was one of the three names in the undervaluation investigation. Its
    bear ROIC is 0.939× WACC — close enough that the gate's decision is a rounding
    question in any direction, and the reason its bear IV is the multiples blend
    with no terminal value at all."""
    p = _proj("SCHW")
    ratio = p["scenarios.bear.forward_roic"] / p["wacc"]
    assert 0.90 < ratio < 1.00, ratio
    assert p["scenarios.base.forward_roic"] > p["wacc"], "base must not fire"


def test_d05_is_the_one_fixture_gate_b_had_nothing_to_judge():
    """`forward_roic` is None with `roic_source` "n/a" in all three scenarios —
    the exhaustive chain's else branch, meaning neither the Y10 projection nor the
    trailing fallback produced a value. Pre-3b this was indistinguishable from a
    gate that ran and did not fire, and the only way to tell was to notice a flag
    was missing."""
    p = _proj("D05_SI")
    for s in _SCENARIOS:
        assert p[f"scenarios.{s}.forward_roic"] is None, s
        assert p[f"scenarios.{s}.roic_source"] == "n/a", s
    assert _gate_b_bear("D05_SI") is None
    assert p["scenarios.bear.tgr"] > 0.0, "no ROIC means no zeroing"


@pytest.mark.parametrize("name", [n for n in _ALL if n != "D05_SI"])
def test_the_projected_roic_rises_monotonically_across_the_scenarios(name):
    """bear < base < bull for all 13 fixtures that compute one. The `× 10` broke
    this in bear for every one of them, and nothing in the payload could show it."""
    p = _proj(name)
    r = [p[f"scenarios.{s}.forward_roic"] for s in _SCENARIOS]
    assert all(v is not None for v in r), r
    assert r[0] < r[1] < r[2], (name, r)


def test_the_quality_gate_saturates_in_bear_for_the_four_names_above_twice_wacc():
    """`_quality = min(1.0, (forward_roic - wacc) / wacc)` is pinned at 1.0 for any
    ROIC >= 2× WACC. These four are already saturated in the BEAR scenario — AAPL
    at 4.53×, V at 4.60×, MELI at 2.71×, COST at 2.21× — so their `growth_premium`
    is the raw growth term with no quality haircut, which is why the bear
    deactivations moved their premiums all the way to `_gp_raw_growth`.

    The fifth deactivation is NOT here, and the exclusion is the interesting part:
    02888_HK's bear ROIC clears WACC by only 1.106×, so `_quality` is 0.106 and its
    premium lands at 0.969 rather than on the raw growth term. It is also the one
    fixture of the five whose bear IV FELL, and the two facts are the same fact."""
    saturated = sorted(n for n in _ALL
                       if _proj(n)["scenarios.bear.forward_roic"] is not None
                       and _proj(n)["scenarios.bear.forward_roic"]
                       >= 2 * _proj(n)["wacc"])
    assert saturated == ["AAPL", "COST", "MELI", "V"]
    assert _proj("02888_HK")["scenarios.bear.forward_roic"] / _proj("02888_HK")["wacc"] \
        == pytest.approx(1.106, abs=5e-4)
    assert _proj("02888_HK")["scenarios.bear.growth_premium"] == pytest.approx(
        0.969, abs=5e-4)


# ══════════════════════════════════════════════════════════════════════════════
# C. normalized_net_income agrees with its own prose and with revenue_base
# ══════════════════════════════════════════════════════════════════════════════


def test_the_persisted_net_income_is_the_one_the_flag_announced():
    """Four fixtures carry "Normalized NI: TTM … → 5y-cycle $X.XXB — P/E (norm)
    will use normalized figure". The persisted field is that figure, which is the
    same field-vs-prose check that makes `forward_roic` trustworthy."""
    seen = []
    for name in _ALL:
        p = _proj(name)
        for flag in p.get("scenarios.base.forward_flags") or []:
            m = _NORM_NI_RE.search(flag)
            if not m:
                continue
            seen.append(name)
            billions = float(m.group(1))
            assert abs(p["normalized_net_income"] / 1e9 - billions) < 0.005, (
                name, billions, p["normalized_net_income"])
    assert sorted(seen) == ["D05_SI", "FCX", "MU", "U96_SI"], seen


def test_the_two_alibaba_lines_agree_on_the_currency_invariant_margin():
    """09988_HK and BABA are the same company captured at two scales — revenue and
    net income both differ by exactly 7.818× — yet their net margins are identical
    to four decimals. That is the proof the new leaf is the same quantity in both
    fixtures rather than a mis-scaled one, and it needs no FX assumption because a
    ratio of two same-currency figures is currency-free."""
    a, b = _proj("09988_HK"), _proj("BABA")
    scale = a["revenue_base"] / b["revenue_base"]
    assert scale == pytest.approx(
        a["normalized_net_income"] / b["normalized_net_income"], rel=1e-6)
    assert scale == pytest.approx(7.818, abs=0.001)
    ma = a["normalized_net_income"] / a["revenue_base"]
    mb = b["normalized_net_income"] / b["revenue_base"]
    assert ma == pytest.approx(mb, abs=1e-4)
    assert ma == pytest.approx(0.0947, abs=5e-5)


def test_the_persisted_net_income_is_in_revenue_bases_currency():
    """The margin every fixture implies is a plausible net margin, which it would
    not be if the numerator and denominator were in different units. Note this
    makes the leaf currency-relative, NOT USD-normalised — its sibling
    `revenue_base_usd` repeats `revenue_base` verbatim on all 14 fixtures
    including three whose `reported_currency` is HKD or CNY, so nothing in this
    payload is safe to compare across fixtures without reading the currency
    fields. Recorded as a separate finding, not pinned here as intended."""
    for name in _ALL:
        p = _proj(name)
        margin = p["normalized_net_income"] / p["revenue_base"]
        assert 0.0 < margin < 0.75, (name, margin)


def test_the_net_income_is_positive_for_every_fixture():
    """All 14 are profitable on the normalized figure, so no leg that reads it is
    being handed a negative denominator. Worth pinning because `P/E (norm)` divides
    by it and a sign flip would invert the leg rather than fail it."""
    for name in _ALL:
        assert _proj(name)["normalized_net_income"] > 0.0, name


# ══════════════════════════════════════════════════════════════════════════════
# D. sector_g_avg and its cohort — item 3a, measured from the baseline
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", _US)
def test_every_us_fixture_resolves_its_sector_growth_from_the_static_table(name):
    """`_stamp` fills `{basis: static, cohort: <market>, peer_count: None}` for any
    field not resolved live, and `growth_avg` is one of those for all eight US
    fixtures. Consequence for item 3a: `_peer_for_gp` omitting `market_cap` cannot
    change a US name's cohort, because there is no live cohort to change."""
    b = _basis(name, "bear")
    assert b == {"basis": "static", "cohort": "US", "peer_count": None}, (name, b)


@pytest.mark.parametrize("name,expected", sorted(_LIVE_COHORT.items()))
def test_every_hk_and_sg_fixture_resolves_a_live_cohort_and_all_are_the_all_one(
        name, expected):
    """The measured shape of item 3a's divergence, read off the baseline instead of
    re-derived. Level, cohort and peer count for all five. **Not one is `large`**,
    while the three legs inside `_compute_method_value` pass
    `(_market_cap or revenue_base * 10)` and get the size-matched cohort. A census
    of the production `regional_comps` table found 436 groupings carrying both
    cohorts and 401 of them disagreeing — for 02888_HK's own grouping the whole
    industry average is +17.11% against +4.88% for the large-cap cohort, and
    `_gp_raw_growth` is INVERSELY proportional to that average, so the choice moves
    the growth premium by roughly 28% on a bank growing at 5%."""
    level, cohort, peers = expected
    b = _basis(name, "bear")
    assert b is not None, f"{name}: expected a live cohort"
    assert b["cohort"] == cohort, (name, b)
    assert b["peer_count"] == peers, (name, b)
    assert b["basis"] != "static", (name, b)


def test_the_five_live_cohorts_are_all_whole_grouping_never_size_matched():
    for name in _LIVE_COHORT:
        assert _basis(name, "bear")["cohort"] == "all", name


def test_c38u_sits_exactly_on_the_minimum_industry_peer_count():
    """Five peers, and `MIN_INDUSTRY_PEERS` is five. One fewer constituent and
    C38U_SI silently drops to the static table — the same silent-fallback shape
    that let the 2026-09-13 comps outage run 17 days undetected. Now the baseline
    shows the margin, so a drop is a visible change rather than a quiet one."""
    assert _basis("C38U_SI", "bear")["peer_count"] == MIN_INDUSTRY_PEERS


def test_u96_is_the_one_fixture_with_no_peer_set_so_it_takes_the_default():
    """No `_comp_basis` at all, therefore `sector_g_avg` is the hardcoded 0.08.
    A fabricated sector average with no provenance is now visible as one, which is
    the entire argument for persisting the basis alongside the number."""
    p = _proj("U96_SI")
    for s in _SCENARIOS:
        assert _basis("U96_SI", s) is None, s
        assert p[f"scenarios.{s}.sector_g_avg"] == _DEFAULT_G_AVG, s


def test_u96_and_bn4_share_a_profile_but_not_a_sector_growth_average():
    """The same-profile A/B that proves 0.08 is a default and not a property of
    `Conglomerate / Industrial (SG)`: BN4_SI measures 0.0643 from 22 peers. A
    28% difference in the denominator of `_gp_raw_growth` for two companies the
    taxonomy treats identically."""
    u, b = _proj("U96_SI"), _proj("BN4_SI")
    assert u["profile"] == b["profile"] == "Conglomerate / Industrial (SG)"
    assert b["scenarios.bear.sector_g_avg"] == pytest.approx(0.0643, abs=5e-5)
    assert u["scenarios.bear.sector_g_avg"] == _DEFAULT_G_AVG
    assert _basis("BN4_SI", "bear") is not None


def test_the_basis_is_scenario_invariant():
    """The peer set does not depend on the scenario, so the three copies must be
    identical. If they ever diverge it means the cohort is being chosen per
    scenario, which would be a new and undocumented behaviour."""
    for name in _ALL:
        bs = [json.dumps(_basis(name, s), sort_keys=True, default=str)
              for s in _SCENARIOS]
        assert len(set(bs)) == 1, (name, bs)


def test_the_sector_growth_average_is_scenario_invariant_too():
    for name in _ALL:
        p = _proj(name)
        vals = {p[f"scenarios.{s}.sector_g_avg"] for s in _SCENARIOS}
        assert len(vals) == 1, (name, vals)


def test_02888_carries_a_forty_six_percent_bank_sector_growth_average():
    """The single largest exposure in item 3a, now in the baseline. A whole-industry
    HKSE Banks average of +45.9% from 7 peers is not a growth rate a bank can be
    measured against; it is what `all` returns when the grouping is thin. Feeding
    it to `_gp_raw_growth = 1 + 0.30*(g - avg)/avg` is what pulls 02888_HK's base
    premium down to 0.891."""
    p = _proj("02888_HK")
    assert p["scenarios.bear.sector_g_avg"] == pytest.approx(0.4592, abs=5e-5)
    assert p["scenarios.base.growth_premium"] == pytest.approx(0.891, abs=5e-4)


# ══════════════════════════════════════════════════════════════════════════════
# E. The composite bridge
# ══════════════════════════════════════════════════════════════════════════════


def test_the_bridge_carries_the_three_sub_scores_their_weights_and_the_cap():
    required = {
        "quality", "quality_weight", "risk", "risk_weight", "commodity",
        "commodity_weight", "raw_composite", "final_multiplier", "cap_high",
        "was_capped", "composite_score", "tier_label", "completeness_score",
        "mandatory_missing", "quality_note", "risk_note", "commodity_note",
        "quality_extracted", "quality_total", "risk_extracted", "risk_total",
    }
    for name in _ALL:
        missing = required - set(_bridge(name))
        assert not missing, (name, sorted(missing))


def test_the_weights_sum_to_one_for_every_fixture():
    for name in _ALL:
        b = _bridge(name)
        total = b["quality_weight"] + b["risk_weight"] + b["commodity_weight"]
        assert total == pytest.approx(1.0, abs=1e-9), (name, total)


def test_the_final_multiplier_is_the_raw_one_unless_it_was_capped():
    """The cap is the only thing allowed to sit between the two. Pinning the
    relationship means a new clamp has to announce itself via `was_capped`."""
    for name in _ALL:
        b = _bridge(name)
        if b["was_capped"]:
            assert b["final_multiplier"] == b["cap_high"], (name, b)
            assert b["raw_composite"] > b["cap_high"], (name, b)
        else:
            assert b["final_multiplier"] == b["raw_composite"], (name, b)
            assert b["raw_composite"] <= b["cap_high"], (name, b)


def test_meli_is_the_only_capped_composite_and_it_is_capped_hard():
    """Raw 2.174 against a ceiling of 1.85, `composite_score` 100. Two "elite"
    quality KPIs (take-rate expansion 150bp, Rule of 40 at 83.4) at a 0.7 quality
    weight, with `commodity_weight` 0.0 and a risk sub-score of 0.92 that cannot
    reach it. This is the decomposition behind MELI's base IV of 5578.42 — the
    known live overstatement — and until now the baseline published only the
    resulting 1.85 with nothing to read it against."""
    capped = [n for n in _ALL if _bridge(n)["was_capped"]]
    assert capped == ["MELI"]
    b = _bridge("MELI")
    assert b["raw_composite"] == pytest.approx(2.174, abs=5e-4)
    assert b["cap_high"] == 1.85 and b["final_multiplier"] == 1.85
    assert b["composite_score"] == 100
    assert b["quality"] == pytest.approx(1.5)
    assert b["risk"] == pytest.approx(0.92)
    assert b["quality_weight"] == pytest.approx(0.7)
    assert b["commodity_weight"] == 0.0


@pytest.mark.parametrize("name", _BANKS)
def test_the_bank_clamp_is_a_note_on_the_raw_value_not_a_change_to_it(name):
    """`final_multiplier` is the PRE-clamp figure; the clamp lands on
    `composite_applied`, outside the bridge. That split is why Decision 1 was
    unauditable before: the bridge was not persisted at all, and the one number
    that WAS (`composite_applied`) could not be told apart from a profile that
    simply scored 1.1. Now the note states the move and both ends are checkable."""
    b = _bridge(name)
    m = re.match(r"^([\d.]+)x → ([\d.]+)x ", b["bank_clamp"])
    assert m, b["bank_clamp"]
    assert float(m.group(1)) == pytest.approx(b["raw_composite"], abs=5e-4)
    assert float(m.group(2)) == pytest.approx(_BANK_CLAMP_TO)
    assert b["final_multiplier"] == b["raw_composite"], "clamp is not in the bridge"
    p = _proj(name)
    for s in _SCENARIOS:
        assert p[f"scenarios.{s}.composite_applied"] == pytest.approx(
            _BANK_CLAMP_TO), (name, s)


def test_the_clamp_actually_bites_on_both_banks():
    """A clamp to 1.100 only means something where the raw value exceeded it."""
    for name in _BANKS:
        assert _bridge(name)["raw_composite"] > _BANK_CLAMP_TO, name
    assert _bridge("02888_HK")["raw_composite"] == pytest.approx(1.278, abs=5e-4)
    assert _bridge("D05_SI")["raw_composite"] == pytest.approx(1.453, abs=5e-4)


def test_schw_is_a_brokerage_and_is_not_clamped():
    """Decision 1 narrowed the clamp from "the composite's bank list" to a real
    bank test, and `Brokerage` was the profile that made the difference. The
    baseline can now SEE that: SCHW has no `bank_clamp` leaf and its applied
    multiplier is its own raw 1.0, while both money-center banks are clamped."""
    p = _proj("SCHW")
    assert p["profile"] == "Brokerage"
    assert "bank_clamp" not in _bridge("SCHW")
    for s in _SCENARIOS:
        assert p[f"scenarios.{s}.composite_applied"] == pytest.approx(1.0), s


def test_the_clamp_is_present_on_exactly_the_two_money_center_banks():
    clamped = sorted(n for n in _ALL if "bank_clamp" in _bridge(n))
    assert clamped == sorted(_BANKS)
    for name in _BANKS:
        assert "Bank" in _proj(name)["profile"], (name, _proj(name)["profile"])


def test_composite_applied_is_exactly_the_ratio_of_the_two_multiples_leaves():
    """The composite is applied as a pure multiplier on the multiples blend, in
    all three scenarios, for all 14 fixtures. Tolerance covers the 4dp rounding of
    `composite_applied` against full-precision `iv_multi`/`iv_multi_post`."""
    checked = 0
    for name in _ALL:
        p = _proj(name)
        for s in _SCENARIOS:
            pre, post = p[f"scenarios.{s}.iv_multi"], p[f"scenarios.{s}.iv_multi_post"]
            if not pre:
                continue
            checked += 1
            assert post / pre == pytest.approx(
                p[f"scenarios.{s}.composite_applied"], abs=2e-4), (name, s)
    # Guard against the `continue` above turning this into a test of nothing.
    # Every one of the 14 fixtures has a multiples leg in all three scenarios.
    assert checked == len(_ALL) * len(_SCENARIOS), checked


def test_the_z_score_path_fires_on_no_fixture_at_all():
    """`quality_z`, `risk_z`, `quality_cohort`, `risk_cohort` and
    `risk_cap_gate_kpi` are None in all 14. Every sub-score in the baseline is a
    raw threshold band, never a cohort-normalised z-score — which is the same
    absence Phase 2.1 looks for in `zscore_engine.py` (no `tanh`, no `0.175`),
    now visible from the valuation side. A change here would be a real change in
    how the composite scores, and the baseline would say so."""
    for name in _ALL:
        b = _bridge(name)
        for key in ("quality_z", "risk_z", "quality_cohort", "risk_cohort",
                    "risk_cap_gate_kpi"):
            assert b[key] is None, (name, key, b[key])


@pytest.mark.parametrize("name", _ZERO_KPI)
def test_five_fixtures_run_the_composite_on_zero_extracted_kpis(name):
    """`quality_extracted == 0` and `risk_extracted == 0`, and the composite still
    emits a confident answer: score 50, multiplier exactly 1.0, tier "in-band".
    Nothing in that output says "measured nothing". Three of the five — AAPL, SCHW
    and V — are names from the undervaluation investigation, so their composite
    contributes precisely nothing to the verdict, not because they are average but
    because no KPI reached it. The notes name what was missing."""
    b = _bridge(name)
    assert b["quality_extracted"] == 0, (name, b)
    assert b["risk_extracted"] == 0, (name, b)
    assert b["composite_score"] == 50, (name, b)
    assert b["final_multiplier"] == 1.0, (name, b)
    assert b["tier_label"] == "in-band", (name, b)
    assert "not extracted" in b["risk_note"] or "no quality" in b["risk_note"]


def test_the_zero_kpi_set_is_exactly_those_five():
    zero = sorted(n for n in _ALL
                  if _bridge(n)["quality_extracted"] == 0
                  and _bridge(n)["risk_extracted"] == 0)
    assert zero == sorted(_ZERO_KPI)


def test_fcx_gives_eighty_percent_of_its_weight_to_a_band_it_could_not_score():
    """`commodity_weight` is 0.8 — the highest in the baseline — and the commodity
    sub-score is 1.0 with a note naming three absent price KPIs, while
    `mandatory_missing` is empty and the tier still reads "in-band". A missing band
    scoring as in-band is the general form of the defect; FCX is where it carries
    the most weight, on a Mining (Major) in `_CYCLICAL_PROFILES`."""
    b = _bridge("FCX")
    assert b["commodity_weight"] == pytest.approx(0.8)
    assert b["commodity"] == 1.0
    assert "no commodity price KPIs" in b["commodity_note"]
    assert b["mandatory_missing"] == []
    assert b["cap_high"] == 1.7, "cyclicals carry a lower ceiling"


def test_mandatory_missing_and_completeness_are_recorded_where_they_apply():
    """Three fixtures declare a gap. The other eleven carry an empty list, so the
    field distinguishes "nothing mandatory was missing" from "we did not look"."""
    expect = {
        "02888_HK": (["nim_pct"], 0.75),
        "MU":       (["dram_bit_growth"], 0.75),
        "U96_SI":   (["weighted_avg_contract_life"], 0.67),
    }
    for name, (missing, score) in expect.items():
        b = _bridge(name)
        assert b["mandatory_missing"] == missing, (name, b)
        assert b["completeness_score"] == pytest.approx(score), (name, b)
    for name in _ALL:
        if name not in expect:
            assert _bridge(name)["mandatory_missing"] == [], name


def test_a_sub_score_note_names_the_kpi_it_scored_where_one_was_scored():
    """The notes are the audit trail for the sub-scores. Where a KPI was extracted
    the note carries its name and value; where it was not the note says so. Either
    way the note is non-empty, so a future silent band cannot pass unnoticed."""
    for name in _ALL:
        b = _bridge(name)
        for key in ("quality_note", "risk_note", "commodity_note"):
            assert isinstance(b[key], str) and b[key].strip(), (name, key)


def test_the_bridge_is_recorded_once_per_run_not_once_per_scenario():
    """`_composite_bridge` is computed before the scenario loop, so it is a
    top-level leaf. Triplicating it would have made a per-scenario divergence
    representable — and silently possible."""
    for name in _ALL:
        p = _proj(name)
        assert not any(k.startswith("scenarios.") and "composite_bridge" in k
                       for k in p), name


# ══════════════════════════════════════════════════════════════════════════════
# F. The engine still computes these, it does not merely publish them
# ══════════════════════════════════════════════════════════════════════════════


def test_the_roic_chain_is_still_exhaustive_in_the_engine():
    """Publishing a field must not become the reason it is computed. The chain that
    binds `forward_roic` is if/elif/else over the Y10 projection, the trailing
    fallback and None, and the three `roic_source` strings are its only outputs."""
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    for lit in ('"Y10 projected"', '"trailing (fallback)"', '"n/a"'):
        assert lit in src, lit
    assert "forward_roic = _forward_roic_proj" in src


def test_the_quality_gate_still_reads_the_variable_it_publishes():
    """Gate B's block overwrites `forward_roic` and the growth-premium quality gate
    reads the same name. That coupling is the reason a defect in the projection
    arithmetic reached the multiples blend, and it is unchanged by persisting the
    value — pinned so the coupling stays deliberate."""
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    assert "_quality = min(1.0, (forward_roic - wacc) / wacc)" in src
    assert "if forward_roic <= wacc:" in src
