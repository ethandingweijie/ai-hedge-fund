"""Task #25 — SOTP (analyst) promotion into the blended IV.

Guards the blend-promotion contract added to dcf_agent.py:

  * ``_promote_sotp_analyst_profile`` — copy-on-write overlay of the method
    onto the resolved valuation profile; bit-identical pass-through when
    there is nothing to promote (production safety: no assumptions, zero
    weight, empty profile, method already present).
  * Weight-share math — profile weights sum to 1.0, so the promoted weight
    w buys a w/(1+w) share of the blended IV (default w=3.0 → 75%: the
    analyst SOTP carries three quarters, today's DCF+multiples blend one
    quarter, renormalized); a None SOTP value renormalizes back to today's
    blend.
  * Composite consistency — the method lands in the multi bucket, so the
    v3.19 composite multiplier applies to its leg like every other
    peer-relative method.
  * ``_compute_method_value`` scenario awareness — base caches the engine
    table; bear/bull prefer the Tier 3.8 scenario TPs and fall back flat;
    net_debt threads into the scenario engine so bear/bull stay consistent
    with the base table.
  * ``sotp_scenario_tps`` net_debt/tier passthrough.
"""
from __future__ import annotations

import copy

import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.sotp_report_extras import sotp_scenario_tps


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SHARES = 1e9

_PROFILE = {
    "name": "Test Profile",
    "methods": [
        {"name": "DCF", "weight": 0.6, "anchor": True, "implementable": True},
        {"name": "P/E", "weight": 0.4, "anchor": False, "implementable": True},
    ],
}


def _minimal_assumptions(with_scenarios: bool = False) -> dict:
    """One P/E-anchored segment: 20B EBIT × (1 − 0.25) × 10x = $150B NAV.

    No ``net_cash`` key so the engine's net_debt fallback path is testable.
    """
    a = {
        "segments": [{
            "name": "Core Commerce",
            "revenue_fwd": 100e9,
            "ebit": 20e9,
            "pe_multiple": 10.0,
        }],
        "default_tax_rate": 0.25,
    }
    if with_scenarios:
        a["_scenarios"] = {
            "bear": [{"name": "Core Commerce", "pe_multiple": 8.0}],
            "bull": [{"name": "Core Commerce", "pe_multiple": 12.0}],
        }
    return a


def _dispatch(most_recent: dict, scenario: str, shares: float = _SHARES,
              net_debt: float = 0.0):
    return dcf_agent._compute_method_value(
        method_name="SOTP (analyst)",
        most_recent=most_recent,
        revenue_base=100e9,
        shares=shares,
        net_debt=net_debt,
        market_cap=1e12,
        wacc=0.10,
        growth_base=0.10,
        fcf_margin_base=0.10,
        tgr=0.03,
        fcf_floor=0.0,
        sector="Technology",
        scenario=scenario,
    )


# ── Profile overlay: _promote_sotp_analyst_profile ───────────────────────────

def test_promote_adds_method_at_blend_weight():
    out = dcf_agent._promote_sotp_analyst_profile(_PROFILE, True)
    assert out is not _PROFILE
    assert [m["name"] for m in out["methods"]] == [
        "DCF", "P/E", "SOTP (analyst)"]
    entry = out["methods"][-1]
    assert entry["weight"] == pytest.approx(
        dcf_agent._SOTP_ANALYST_BLEND_WEIGHT)
    assert entry["implementable"] is True
    assert entry["anchor"] is False
    # Non-method keys carry over
    assert out["name"] == "Test Profile"


def test_promote_copy_on_write_never_mutates_input():
    before = copy.deepcopy(_PROFILE)
    out = dcf_agent._promote_sotp_analyst_profile(_PROFILE, True)
    assert _PROFILE == before  # shared INDUSTRY_VALUATION_PROFILES safety
    out["methods"].append({"name": "junk"})
    assert len(_PROFILE["methods"]) == 2


def test_promote_no_assumptions_returns_same_object():
    assert dcf_agent._promote_sotp_analyst_profile(_PROFILE, False) is _PROFILE


def test_promote_zero_weight_returns_same_object(monkeypatch):
    monkeypatch.setattr(dcf_agent, "_SOTP_ANALYST_BLEND_WEIGHT", 0.0)
    assert dcf_agent._promote_sotp_analyst_profile(_PROFILE, True) is _PROFILE


def test_promote_idempotent_when_method_present():
    once = dcf_agent._promote_sotp_analyst_profile(_PROFILE, True)
    twice = dcf_agent._promote_sotp_analyst_profile(once, True)
    assert twice is once


def test_promote_empty_or_missing_profile():
    assert dcf_agent._promote_sotp_analyst_profile(None, True) is None
    assert dcf_agent._promote_sotp_analyst_profile({}, True) == {}
    assert dcf_agent._promote_sotp_analyst_profile(
        {"methods": []}, True) == {"methods": []}


# ── Blend weight-share math ───────────────────────────────────────────────────

def test_promoted_profile_blends_at_75_percent():
    """Default w=3.0 → SOTP carries w/(1+w) = 75% of the blended IV."""
    w = dcf_agent._SOTP_ANALYST_BLEND_WEIGHT
    assert w == pytest.approx(3.0)
    values = {"DCF": 100.0, "P/E": 80.0, "SOTP (analyst)": 120.0}
    legacy_iv, _ = dcf_agent._blend_methods(
        _PROFILE["methods"], values, c_macro=0.0, forward_flags=[],
        dcf_tv_fraction=0.0, composite_mult=1.0)
    promoted = dcf_agent._promote_sotp_analyst_profile(_PROFILE, True)
    iv, bd = dcf_agent._blend_methods(
        promoted["methods"], values, c_macro=0.0, forward_flags=[],
        dcf_tv_fraction=0.0, composite_mult=1.0)
    assert legacy_iv == pytest.approx(92.0)
    # Weight-share contract: IV = (legacy sum + w·V_sotp) / (1 + w)
    assert iv == pytest.approx(
        (0.6 * 100.0 + 0.4 * 80.0 + w * 120.0) / (1.0 + w))
    # …which at w=3.0 is exactly 25% legacy + 75% SOTP:
    assert iv == pytest.approx(0.25 * legacy_iv + 0.75 * 120.0)
    assert iv == pytest.approx(113.0)
    # Weight shares: DCF 0.6/4, multi bucket (P/E 0.4 + SOTP 3.0)/4
    assert bd["weight_dcf"] == pytest.approx(0.6 / (1.0 + w))
    assert bd["weight_multi"] == pytest.approx((0.4 + w) / (1.0 + w))


def test_sotp_none_renormalizes_to_legacy_blend():
    """No assumptions → dispatcher returns None → today's blend unchanged."""
    values = {"DCF": 100.0, "P/E": 80.0, "SOTP (analyst)": None}
    promoted = dcf_agent._promote_sotp_analyst_profile(_PROFILE, True)
    iv, bd = dcf_agent._blend_methods(
        promoted["methods"], values, c_macro=0.0, forward_flags=[],
        dcf_tv_fraction=0.0, composite_mult=1.0)
    assert iv == pytest.approx(92.0)
    assert bd["weight_dcf"] == pytest.approx(0.6)
    assert bd["weight_multi"] == pytest.approx(0.4)


def test_composite_applies_to_sotp_leg():
    """SOTP sits in the multi bucket → v3.19 composite biases its leg."""
    w = dcf_agent._SOTP_ANALYST_BLEND_WEIGHT
    promoted = dcf_agent._promote_sotp_analyst_profile(_PROFILE, True)
    values = {"DCF": 100.0, "P/E": 80.0, "SOTP (analyst)": 120.0}
    iv, _ = dcf_agent._blend_methods(
        promoted["methods"], values, c_macro=0.0, forward_flags=[],
        dcf_tv_fraction=0.0, composite_mult=0.9)
    legacy_c = 0.6 * 100.0 + 0.4 * 80.0 * 0.9   # composite-biased legacy
    assert iv == pytest.approx((legacy_c + 0.9 * w * 120.0) / (1.0 + w))
    assert iv == pytest.approx(103.2)  # at w=3.0


# ── Dispatcher scenario awareness ─────────────────────────────────────────────

def test_dispatcher_scenario_aware_values():
    mr = {"sotp_assumptions": _minimal_assumptions(with_scenarios=True)}
    assert _dispatch(mr, "base") == pytest.approx(150.0)
    assert _dispatch(mr, "bear") == pytest.approx(120.0)
    assert _dispatch(mr, "bull") == pytest.approx(180.0)
    # Base table computed once and cached for the scenario loop + breakdown
    assert mr["sotp_analyst_table"]["per_share"] == pytest.approx(150.0)


def test_dispatcher_flat_without_scenario_block():
    mr = {"sotp_assumptions": _minimal_assumptions()}
    for scen in ("base", "bear", "bull"):
        assert _dispatch(mr, scen) == pytest.approx(150.0)


def test_dispatcher_base_ignores_scenario_overrides():
    mr = {"sotp_assumptions": _minimal_assumptions(with_scenarios=True)}
    assert _dispatch(mr, "base") == pytest.approx(150.0)


def test_dispatcher_unmatched_override_falls_back_flat():
    a = _minimal_assumptions()
    a["_scenarios"] = {
        "bear": [{"name": "Nonexistent Segment", "pe_multiple": 8.0}]}
    mr = {"sotp_assumptions": a}
    assert _dispatch(mr, "bear") == pytest.approx(150.0)


def test_dispatcher_net_debt_threads_into_scenarios():
    """No net_cash on assumptions → engine uses net_debt; scenario TPs must
    see the same net_debt (passthrough regression guard)."""
    mr = {"sotp_assumptions": _minimal_assumptions(with_scenarios=True)}
    assert _dispatch(mr, "base", net_debt=-30e9) == pytest.approx(180.0)
    assert _dispatch(mr, "bear", net_debt=-30e9) == pytest.approx(150.0)


def test_dispatcher_no_assumptions_returns_none():
    assert _dispatch({}, "base") is None


# ── sotp_scenario_tps net_debt/tier passthrough ──────────────────────────────

def test_scenario_tps_net_debt_passthrough():
    a = _minimal_assumptions()  # no net_cash key
    scenarios = {"bear": [{"name": "Core Commerce", "pe_multiple": 8.0}]}
    out = sotp_scenario_tps(a, scenarios, shares=_SHARES, fx=1.0,
                            net_debt=-30e9)
    assert out["bear"]["per_share"] == pytest.approx(150.0)  # 120B + 30B cash
    out0 = sotp_scenario_tps(a, scenarios, shares=_SHARES, fx=1.0)
    assert out0["bear"]["per_share"] == pytest.approx(120.0)


def test_scenario_tps_tier_passthrough_changes_fallback_multiple():
    # "Zorblatt" matches no keyword → tier-default EV/Rev fallback multiple
    # (3.0 default tier vs 4.5 premium tier).
    a = {"segments": [{"name": "Zorblatt Division", "revenue_fwd": 50e9}],
         "default_tax_rate": 0.25}
    scenarios = {"bull": [{"name": "Zorblatt Division"}]}
    out_default = sotp_scenario_tps(a, scenarios, shares=_SHARES,
                                    tier="default")
    out_premium = sotp_scenario_tps(a, scenarios, shares=_SHARES,
                                    tier="premium")
    assert out_default["bull"]["per_share"] == pytest.approx(50e9 * 3.0 / _SHARES)
    assert out_premium["bull"]["per_share"] == pytest.approx(50e9 * 4.5 / _SHARES)
