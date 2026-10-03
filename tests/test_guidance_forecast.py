"""The guidance-to-forecast engine (owner's five principles, 2026-10-03): archetype routing, target
deconstruction, the archetype margin curves, the three-statement build, the fade, the terminal
diagnostics and the invariants; and its wiring into the DCF leg, the extractor and the surfaces."""
import inspect
from pathlib import Path

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.analysis import guidance_forecast as gf

_SERIES = [{"revenue": 34e9, "ebit": 1.6e9, "net_income": 1.1e9, "interest_expense": 110e6, "depreciation_and_amortization": 180e6,
            "capital_expenditure": -120e6, "change_in_working_capital": -200e6, "shares_outstanding": 58e6, "invested_capital": 7e9, "share_buyback": -500e6},
           {"revenue": 38e9, "ebit": 1.9e9, "net_income": 1.3e9, "interest_expense": 110e6, "depreciation_and_amortization": 190e6,
            "capital_expenditure": -140e6, "change_in_working_capital": -250e6, "shares_outstanding": 56e6, "invested_capital": 7.5e9, "share_buyback": -600e6},
           {"revenue": 42e9, "ebit": 1.2e9, "net_income": 0.75e9, "interest_expense": 120e6, "depreciation_and_amortization": 200e6,
            "capital_expenditure": -150e6, "change_in_working_capital": -100e6, "shares_outstanding": 52e6, "invested_capital": 8e9, "share_buyback": -700e6}]
_BLOCK = {"fiscal_year_1": "FY2026", "fiscal_year_2": "FY2027", "confidence": "MEDIUM",
          "estimates": {"base": {"revenue_growth_fy1": -0.075, "revenue_growth_fy2": 0.107, "ebitda_margin_fy1": 0.035, "ebitda_margin_fy2": 0.05, "eps_fy1": 5.3, "eps_fy2": 10.25},
                        "bear": {"revenue_growth_fy1": -0.085, "revenue_growth_fy2": 0.09, "ebitda_margin_fy2": 0.04, "eps_fy2": 9.2},
                        "bull": {"revenue_growth_fy1": -0.065, "revenue_growth_fy2": 0.125, "ebitda_margin_fy2": 0.06, "eps_fy2": 11.5}},
          "medium_term_target": {"metric": "eps", "target_year": "FY2029", "low": 20, "mid": 25, "high": 30, "unit": "USD", "basis": "workplan", "source": "Investor day"}}


def _fc(scenario="base", **kw):
    args = dict(scenario=scenario, series=_SERIES, profile_name="Managed Care", sector="Healthcare", wacc=0.08, tgr=0.025,
                shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)
    args.update(kw)
    return gf.build_forecast(_BLOCK, **args)


def test_archetypes_route_by_profile_then_family_then_sector():
    assert gf.archetype_for("Managed Care")[0] == "I" and gf.archetype_for("Regulated Utility")[0] == "I"
    assert gf.archetype_for("Capital Goods")[0] == "II" and gf.archetype_for("Offshore Marine & Resources (SG)")[0] == "II"
    assert gf.archetype_for("Specialty Retail")[0] == "III" and gf.archetype_for("Restaurants")[0] == "III"
    assert gf.archetype_for("China Internet Platform")[0] == "IV" and gf.archetype_for("Mature SaaS")[0] == "IV"
    assert gf.archetype_for("Some New Profile", "Tech")[0] == "IV"              # sector fallback
    assert gf.archetype_for("", "")[0] == "G"


def test_the_margin_curves_have_their_archetype_shapes_and_end_at_one():
    s = gf.margin_curve("I", 4)
    assert s[-1] == 1.0 and s[0] <= 0.15 and s[1] == pytest.approx(0.5) and s[0] < s[1] < s[2] < s[3]     # S-curve, year-1 lag
    j = gf.margin_curve("II", 4)
    assert j == [pytest.approx(x) for x in (1 / 16, 4 / 16, 9 / 16, 1.0)]                                   # convex J-curve
    r = gf.margin_curve("III", 4)
    assert r[0] < 0.25 and r[0] == pytest.approx(0.25 - 0.25 / 4) and r[1:] == [pytest.approx(x) for x in (0.5, 0.75, 1.0)]  # pre-opening drag
    assert gf.margin_curve("IV", 4) == [pytest.approx(x) for x in (0.25, 0.5, 0.75, 1.0)]
    assert gf.margin_curve("I", 1) == [1.0]


def test_the_target_year_is_back_solved_and_the_horizon_reaches_it():
    fc = _fc()
    assert fc["archetype"] == "I" and fc["horizon_years"] == 4                     # FY2026..FY2029
    dec = fc["deconstruction"]
    assert dec["eps_T_guided"] == 25.0 and dec["margin_T_guided"] == 0.05
    assert dec["ebit_T_implied"] == pytest.approx(dec["revenue_T"] * 0.05)
    assert dec["net_income_T_from_eps"] == pytest.approx(25.0 * 52e6)
    assert 0.0 <= dec["implied_tax_rate"] <= 0.45                                  # compatible endpoints: no flag
    assert fc["flags"] == []
    # an incompatible EPS target raises the compatibility flag
    hot = {**_BLOCK, "medium_term_target": {**_BLOCK["medium_term_target"], "mid": 60}}
    fc2 = gf.build_forecast(hot, scenario="base", series=_SERIES, profile_name="Managed Care", sector="Healthcare", wacc=0.08, tgr=0.025, shares=52e6, net_debt=1e9)
    assert any("not compatible" in f for f in fc2["flags"])
    # a guided CAGR 500bp over the market is flagged as share capture / capacity / M&A
    fast = {**_BLOCK, "estimates": {"base": {"revenue_growth_fy1": 0.15, "revenue_growth_fy2": 0.15}}, "medium_term_target": None}
    fc3 = gf.build_forecast(fast, scenario="base", series=_SERIES, profile_name="Mature SaaS", sector="Tech", wacc=0.08, tgr=0.025, shares=52e6, net_debt=0.0, market_growth=0.04)
    assert any("above the market" in f for f in fc3["flags"])


def test_the_three_statements_and_the_fade_hold_their_identities():
    fc = _fc()
    rows = fc["rows"]
    assert len(rows) == 10 and [r["phase"] for r in rows] == ["guided"] * 4 + ["fade"] * 4 + ["steady"] * 2
    assert rows[0]["growth"] == pytest.approx(-0.075) and rows[1]["growth"] == pytest.approx(0.107)
    assert rows[3]["ebit_margin"] == pytest.approx(0.05)                             # the target margin in the target year
    g = fc["growth_schedule"]
    assert g[-1] == pytest.approx(0.025) and g[-2] == pytest.approx(0.025)
    assert all(abs(g[i + 1] - 0.025) <= abs(g[i] - 0.025) + 1e-9 for i in range(4, 8))   # fade converges on tgr from either side
    # an EPS target says nothing about revenue: the bridge years step from the last guided rate toward the market's, never flat zero
    assert 0.04 < rows[3]["growth"] < rows[2]["growth"] < 0.107                    # 10.7% stepping down toward the market's 4%
    h = fc["history"]
    assert h["tax_rate_source"] == "history" and 0.10 <= h["tax_rate"] <= 0.35
    r1 = rows[0]
    assert r1["nopat"] == pytest.approx(r1["ebit"] * (1 - h["tax_rate"]))
    assert r1["capex"] == pytest.approx(r1["da"] + h["capex_alpha"] * max(r1["revenue"] - 42e9, 0.0))
    assert r1["ufcf"] == pytest.approx(r1["nopat"] + r1["da"] - r1["capex"] - r1["delta_nwc"])
    assert r1["net_income"] == pytest.approx((r1["ebit"] - h["interest"]) * (1 - h["tax_rate"]))
    assert fc["fcf_margin_schedule"][0] == pytest.approx(r1["ufcf"] / r1["revenue"], abs=1e-6)   # the schedule is rounded to 6 d.p.
    # D&A rolls forward from capex
    assert rows[1]["da"] == pytest.approx(rows[0]["da"] + (rows[0]["capex"] - rows[0]["da"]) / 10)


def test_scenarios_use_their_own_endpoints():
    bear, base, bull = _fc("bear"), _fc("base"), _fc("bull")
    assert bear["deconstruction"]["eps_T_guided"] == 20 and bull["deconstruction"]["eps_T_guided"] == 30
    assert bear["rows"][0]["revenue"] < base["rows"][0]["revenue"] < bull["rows"][0]["revenue"]
    assert bear["margin_target"] < base["margin_target"] < bull["margin_target"]


def test_the_invariants_report_each_principle():
    fc = _fc()
    ids = {i["id"]: i for i in fc["invariants"]}
    assert set(ids) == {1, 2, 3, 4, 5}
    assert ids[2]["ok"] is True and "UFCF / net income" in ids[2]["detail"]
    assert ids[4]["ok"] is False and "vs mid-cycle peer median 9.0x" in ids[4]["detail"]     # the toy's exit multiple is above 9x
    assert ids[3]["ok"] is True and "implies" in ids[3]["detail"]
    assert ids[5]["ok"] is None
    t = fc["terminal"]
    assert t["roic_terminal"] >= 0.10 and t["reinvestment_rate"] == pytest.approx(0.025 / t["roic_terminal"])
    assert fc["target"]["target_year"] == "FY2029" and fc["target"]["year_index"] == 4


def test_the_engine_declines_without_a_block_or_fy1_growth_and_summary_drops_the_internals():
    assert gf.build_forecast({}, scenario="base", series=_SERIES, profile_name="x", sector="Tech", wacc=0.08, tgr=0.025, shares=1e6, net_debt=0.0) is None
    assert gf.build_forecast({"estimates": {"base": {"revenue_growth_fy1": None}}}, scenario="base", series=_SERIES, profile_name="x", sector="Tech", wacc=0.08, tgr=0.025, shares=1e6, net_debt=0.0) is None
    assert gf.build_forecast(_BLOCK, scenario="base", series=[], profile_name="x", sector="Tech", wacc=0.08, tgr=0.025, shares=1e6, net_debt=0.0) is None
    s = gf.summary(_fc())
    assert "curve" not in s and set(s["rows"][0]) >= {"year", "revenue", "growth", "ebit_margin", "eps", "ufcf", "fcf_margin", "phase"}
    assert "nopat" not in s["rows"][0]
    assert gf.summary(None) is None


def test_the_wiring_runs_the_dcf_on_the_forecast_schedules_and_publishes_it():
    src = inspect.getsource(d.run_dcf_agent)
    assert "_gfm.build_forecast(" in src and "_gf_margin_sched = _gf[\"fcf_margin_schedule\"]" in src
    assert '"margin_schedule": _gf_margin_sched' in src and "margin_schedule=_gf_margin_sched," in src
    assert '"guidance_forecast": _guidance_forecast_payload(_gf_base),' in src
    assert "_gc = None if _gf else _guidance_channel_schedule(" in src                 # the FY+1/FY+2 channel is the fallback
    cmv = inspect.getsource(d._compute_method_value)
    assert 'margin_schedule=_pj.get("margin_schedule"),' in cmv
    from src.agents.industry import deep_research as dr
    assert '"medium_term_target"' in dr._GUIDANCE_ESTIMATES_SYSTEM
    out = dr._normalize_guidance_estimates({"estimates": {"base": {"revenue_growth_fy1": 0.06}}, "confidence": "HIGH",
                                            "medium_term_target": {"metric": "EPS", "target_year": "FY2029", "low": 20, "mid": 25, "high": 30, "unit": "USD"}})
    assert out["medium_term_target"] == {"metric": "eps", "target_year": "FY2029", "low": 20.0, "mid": 25.0, "high": 30.0, "unit": "USD", "basis": None, "source": None}
    out2 = dr._normalize_guidance_estimates({"estimates": {"base": {"revenue_growth_fy1": 0.06}}, "medium_term_target": {"metric": "ebitda_margin", "target_year": "FY2028", "mid": "18%"}})
    assert out2["medium_term_target"]["mid"] == 0.18
    from src.data import valuation_constants as vc
    blk = vc.load()["guidance_forecast"]
    assert blk["status"] == "PROPOSED" and blk["fade_years"] == 4 and blk["curves"]["I"]["shape"] == "s_curve"
    assert gf.load_cfg()["fade_years"] == 4
    root = Path(__file__).resolve().parents[1]
    assert "ForecastSection" in (root / "app/frontend/src/components/report/GuidanceEstimatesPanel.tsx").read_text(encoding="utf-8")
    assert "guidance_forecast?: GuidanceForecast | null;" in (root / "app/frontend/src/lib/reportTypes.ts").read_text(encoding="utf-8")
    from src.utils import pdf_report as pr, valuation_workbook as vw
    assert "_guidance_forecast_block_pdf(dcf_t, styles, width)" in inspect.getsource(pr)
    assert "Guidance forecast —" in inspect.getsource(vw._Book.guidance_tab)
    from src.agents import portfolio_manager as pm
    assert "Forecast path (" in inspect.getsource(pm._forward_estimates_block)
