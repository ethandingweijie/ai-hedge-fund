"""Task #25 — SOTP (analyst) promotion into the blended IV.

Guards the blend-promotion contract added to dcf_agent.py:

  * The promotion overlay and its 3.0 weight-share RETIRED 2026-09-26: the
    leg is declared by the profile table at the profile's weight.
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
import inspect

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


# ── Profile overlay: RETIRED (owner, 2026-09-26) ─────────────────────────────

def test_the_analyst_sotp_is_a_profile_method_not_a_promotion():
    """Task #25's 3.0 promotion (75% of the blend on any name the extractor
    produced assumptions for) is gone: the leg enters a blend only where the
    profile table declares "SOTP (analyst)"."""
    assert not hasattr(dcf_agent, "_promote_sotp_analyst_profile")
    assert not hasattr(dcf_agent, "_SOTP_ANALYST_BLEND_WEIGHT")
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    assert "_promote_sotp_analyst_profile(" not in src and "_promote_segment_sotp(" not in src


def test_a_profile_that_declares_the_leg_blends_it_at_its_declared_weight():
    methods = [{"name": "DCF", "weight": 0.30, "anchor": True, "implementable": True},
               {"name": "SOTP (analyst)", "weight": 0.35, "anchor": False, "implementable": True},
               {"name": "P/E", "weight": 0.35, "anchor": False, "implementable": True}]
    values = {"DCF": 100.0, "P/E": 80.0, "SOTP (analyst)": 120.0}
    iv, bd = dcf_agent._blend_methods(methods, values, c_macro=0.0, forward_flags=[], dcf_tv_fraction=0.0)
    assert iv == pytest.approx(0.30 * 100 + 0.35 * 120 + 0.35 * 80)
    # a None leg renormalises onto the rest, as every other method does
    iv2, _ = dcf_agent._blend_methods(methods, {**values, "SOTP (analyst)": None},
                                      c_macro=0.0, forward_flags=[], dcf_tv_fraction=0.0)
    assert iv2 == pytest.approx((0.30 * 100 + 0.35 * 80) / 0.65)


# ── Dispatcher scenario awareness ─────────────────────────────────────────────

def test_dispatcher_scenario_aware_values():
    mr = {"sotp_assumptions": _minimal_assumptions(with_scenarios=True)}
    assert _dispatch(mr, "base") == pytest.approx(150.0)
    assert _dispatch(mr, "bear") == pytest.approx(120.0)
    assert _dispatch(mr, "bull") == pytest.approx(180.0)
    # Base table computed once and cached for the scenario loop + breakdown
    assert mr["sotp_analyst_table"]["per_share"] == pytest.approx(150.0)


def test_dispatcher_flexes_segments_without_scenario_block():
    """Owner decision A+ (2026-09-19): no analyst `_scenarios` block no longer
    means the same value in every scenario. Segment value takes the standard
    0.75x / 1.25x multiple band (no revenue trees attached here), so base is
    unchanged and bear < base < bull. It used to return 150 for all three,
    which let a SOTP-dominated blend keep its bear case above spot."""
    mr = {"sotp_assumptions": _minimal_assumptions()}
    base = _dispatch(mr, "base")
    assert base == pytest.approx(150.0)
    assert _dispatch(mr, "bear") < base < _dispatch(mr, "bull")


def test_dispatcher_base_ignores_scenario_overrides():
    mr = {"sotp_assumptions": _minimal_assumptions(with_scenarios=True)}
    assert _dispatch(mr, "base") == pytest.approx(150.0)


def test_dispatcher_unmatched_override_falls_back_flat():
    a = _minimal_assumptions()
    a["_scenarios"] = {
        "bear": [{"name": "Nonexistent Segment", "pe_multiple": 8.0}]}
    mr = {"sotp_assumptions": a}
    # An override matching no segment yields no scenario TP, so bear falls
    # through to the tree + band flex rather than to the flat base value.
    assert _dispatch(mr, "base") == pytest.approx(150.0)
    assert _dispatch(mr, "bear") < 150.0


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


def test_scenario_tps_never_price_an_unanchored_segment_from_the_tier():
    # Owner, 2026-09-24 (item 1): "Zorblatt" matches no keyword and carries no
    # multiple. It used to price at the tier's EV/Rev constant (3.0x default,
    # 4.5x premium); now the segment is Degraded in every scenario and no TP
    # is published for it, whatever the tier.
    a = {"segments": [{"name": "Zorblatt Division", "revenue_fwd": 50e9}],
         "default_tax_rate": 0.25}
    scenarios = {"bull": [{"name": "Zorblatt Division"}]}
    out_default = sotp_scenario_tps(a, scenarios, shares=_SHARES,
                                    tier="default")
    out_premium = sotp_scenario_tps(a, scenarios, shares=_SHARES,
                                    tier="premium")
    assert "bull" not in out_default and "bull" not in out_premium
    # With a cited EV/Sales fallback the scenario prices on it, tier-blind.
    a2 = {"segments": [{"name": "Zorblatt Division", "revenue_fwd": 50e9,
                        "pe_multiple": 15.0, "ebit": -1e9, "ev_rev_fallback_multiple": 1.2}],
          "default_tax_rate": 0.25}
    scenarios2 = {"bull": [{"name": "Zorblatt Division", "pe_multiple": 20.0}]}
    out2 = sotp_scenario_tps(a2, scenarios2, shares=_SHARES, tier="premium")
    assert out2["bull"]["per_share"] == pytest.approx(50e9 * 1.2 / _SHARES)
