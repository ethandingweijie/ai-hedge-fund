"""The FCF-guidance overlay that fades (owner, 2026-09-22).

"A 25% FCF margin cannot and should not be held flat across a 10-year DCF ... Contract liabilities
represent cash borrowed from the future." The overlay is consumed by the Backlog-coverage DCF leg.

The Backlog-Gated Long Cycle profile and its eligibility gate were removed on 2026-09-27 (owner:
remove the profiles no routing row or pin reached); its gate tests went with it, and one test
below pins that the gate cannot quietly return.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent
from src.data import valuation_constants as vc

fade = vc.fcf_guidance_margin_schedule


def test_the_long_cycle_profile_and_its_gate_are_gone():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    assert all("Backlog-Gated Long Cycle" not in ps for ps in P.values())
    assert not hasattr(dcf_agent, "_long_cycle_gate") and not hasattr(vc, "long_cycle_eligibility")
    assert "backlog_gated_long_cycle" not in vc.load()
    assert "GATE_LONG_CYCLE_ELIGIBILITY" not in inspect.getsource(dcf_agent)


# ── the fade ─────────────────────────────────────────────────────────────────

def test_the_schedule_is_the_owners_three_phases():
    s = fade(12.0 / 46.0)
    assert s["schedule"] == [0.25, 0.25, 0.25, 0.2275, 0.205, 0.1825, 0.16, 0.15, 0.15, 0.15]
    assert s["ceiling_applied"] and s["explicit_margin"] == 0.25 and s["floor"] == 0.15
    cfg = vc.load()["fcf_guidance_fade"]
    assert cfg["explicit_margin_band"] == [0.22, 0.25] and cfg["fade_bps_band"] == [200, 250]
    assert cfg["floor_margin_band"] == [0.145, 0.155]


def test_the_fade_never_goes_through_the_floor_and_never_lifts_a_low_guidance_onto_it():
    assert fade(0.18)["schedule"] == [0.18, 0.18, 0.18, 0.1575, 0.15, 0.15, 0.15, 0.15, 0.15, 0.15]
    assert fade(0.15) is None and fade(0.09) is None and fade(None) is None


def test_the_terminal_takes_the_floor_because_it_takes_the_last_years_margin():
    base = dict(revenue_base=4e10, fcf_margin_base=0.07, growth_rate=0.05, margin_delta_per_year=0.0,
                wacc=0.0825, tgr=0.02, fcf_floor=0.0, net_debt=0.0, shares=2.7e8)
    flat25, *_ = dcf_agent._project_dcf(**base, margin_schedule=[0.25] * 10)
    faded, _, _, rows = dcf_agent._project_dcf(**base, margin_schedule=fade(0.26)["schedule"])
    audited, *_ = dcf_agent._project_dcf(**base)
    assert audited < faded < flat25
    assert [round(r["fcf_margin"], 4) for r in rows][-3:] == [0.15, 0.15, 0.15]
    # Held flat, 25% would be worth far more: most of a DCF is its terminal.
    assert flat25 / faded > 1.4


def test_no_margin_schedule_leaves_the_projector_exactly_as_it_was():
    base = dict(revenue_base=4e10, fcf_margin_base=0.07, growth_rate=0.05, margin_delta_per_year=0.0,
                wacc=0.0825, tgr=0.02, fcf_floor=0.0, net_debt=0.0, shares=2.7e8, margin_delta_absolute=0.01)
    assert dcf_agent._project_dcf(**base) == dcf_agent._project_dcf(**base, margin_schedule=None)


def test_the_overlay_needs_an_acceptance_and_the_forward_overlay_switch():
    """Guidance is never a baseline (owner, 2026-09-20)."""
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    at = src.index('accepted_detail(ticker, "fcf_guidance", _mc_ccy)')
    assert "if _ii.overlay_enabled():" in src[at - 200: at]


def test_the_scenarios_stay_ordered_under_the_faded_overlay(monkeypatch):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {})
    row = {"_fcf_guidance_detail": {"guided_margin": 0.26}}
    vals = []
    for scen, md in (("bear", -0.0146), ("base", 0.0), ("bull", 0.0146)):
        vals.append(dcf_agent._compute_method_value(
            method_name="Backlog-coverage DCF", most_recent=row, revenue_base=4e10, shares=2.7e8, net_debt=-9e9,
            market_cap=2.5e11, wacc=0.0825, growth_base=0.05, fcf_margin_base=0.0731, tgr=0.02, fcf_floor=0.0,
            sector="Industrials", scenario=scen, profile_name="Defense Primes",   # a profile that declares the leg
            projection={"growth_schedule": [0.05] * 10, "margin_delta_absolute": md}))
    assert vals[0] < vals[1] < vals[2]


# ── the review gate for guidance ─────────────────────────────────────────────

def test_guidance_must_be_for_a_year_that_has_not_ended_and_everything_else_for_one_that_has():
    from src.data import industry_inputs as ii
    ctx = {"period": "2025-12-31", "revenue": 3.8e10}
    period = lambda kind, p: [c["ok"] for c in ii.reconcile(kind, 1.2e10 if kind == "fcf_guidance" else 1e9, ctx, period=p)
                              if "period" in c["check"]]
    assert period("fcf_guidance", "FY2026E") == [True]
    assert period("fcf_guidance", "FY2025") == [False]          # a year already reported is an actual
    assert period("fcf_guidance", "FY2029E") == [False]         # too far out to be next-year guidance
    assert any(c["ok"] is False for c in ii.reconcile("fcf_guidance", 1.2e13, ctx, period="FY2026E"))   # bn read as tn
