"""D1 (owner, 2026-11-27): the rating benchmark is the MARKET cost of equity on the valuation's own CAPM inputs, and
the 12-month target carries the full price path ("a-full"):

    target = [spot + capture x (IV - spot)] x (1 + Ke_firm - dividend_yield)

so a fairly valued stock's 12-month TSR is its own cost of equity and it rates Neutral. The structural test applies
the same carry to intrinsic value. Before: SG hurdle 8.91% (hard-coded rf 3.95% + ERP 4.46% + CRP 0.5%) against a
valuation discounting on the 2.29% SGS curve (6.70%), and a target struck at today's value -- a fairly valued SG stock
rated Underweight.
"""
from __future__ import annotations

import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.decisions import ratings as R

SG_CAPM = {"rf": 0.0229, "erp": 0.0441, "crp": 0.0, "cost_of_equity": 0.0529}
US_CAPM = {"rf": 0.0494, "erp": 0.0441, "crp": 0.0}


def test_benchmark_is_built_from_the_runs_capm_inputs():
    b = R.resolve_benchmark("Z74.SI", "Telco", capm=SG_CAPM)
    assert b["expected_return"] == pytest.approx(0.0670, abs=1e-4)
    assert "2.29%" in b["basis"] and "valuation's CAPM inputs" in b["basis"]
    assert R.resolve_benchmark("V", "Financials", capm=US_CAPM)["expected_return"] == pytest.approx(0.0935, abs=1e-4)
    # no CAPM build on the run: the old constants, said so
    fb = R.resolve_benchmark("Z74.SI", "Telco")
    assert fb["expected_return"] == pytest.approx(R.RISK_FREE_RATE + R.EQUITY_RISK_PREMIUM + 0.005, abs=1e-4)
    assert "fallback" in fb["basis"]
    # an implausible build is not trusted
    assert R.resolve_benchmark("Z74.SI", "Telco", capm={"rf": None, "erp": 0.0441})["source"].startswith("fallback")


def test_rating_definition_names_the_hurdle_composition():
    assert "risk-free rate, equity risk premium and country risk premium the valuation itself uses" in R.RATING_DEFINITION


def test_carry_prefers_the_firm_ke_then_the_ggm_coe_then_the_market():
    k = d._price_path_carry(firm_ke=0.0529, ggm_coe=0.088, market_capm=SG_CAPM, dps=0.184121, spot=4.30)
    assert k["factor"] == pytest.approx(1 + 0.0529 - 0.184121 / 4.30, abs=1e-6) and k["ke_source"].startswith("firm")
    k = d._price_path_carry(firm_ke=None, ggm_coe=0.088, market_capm=SG_CAPM, dps=2.228868, spot=35.44)
    assert k["factor"] == pytest.approx(1 + 0.088 - 2.228868 / 35.44, abs=1e-6) and k["ke_source"].startswith("GGM")
    k = d._price_path_carry(firm_ke=None, ggm_coe=None, market_capm=SG_CAPM, dps=None, spot=10.0)
    assert k["factor"] == pytest.approx(1.0529) and k["ke_source"].startswith("market")
    assert d._price_path_carry(firm_ke=None, ggm_coe=None, market_capm=None, dps=1.0, spot=10.0)["factor"] == 1.0


@pytest.mark.parametrize("ke,dy,capm,ticker", [
    (0.0849, 0.0064, US_CAPM, "V"),             # V-like
    (0.0529, 0.0428, SG_CAPM, "Z74.SI"),        # Z74-like
    (0.1226, 0.0005, US_CAPM, "MU"),            # MU-like (beta 1.66)
    (0.0880, 0.0629, SG_CAPM, "U11.SI"),        # U11-like (bank, GGM CoE)
])
def test_a_fairly_valued_stock_is_neutral(ke, dy, capm, ticker):
    spot, cap = 100.0, 0.35
    carry = d._price_path_carry(firm_ke=ke, ggm_coe=None, market_capm=capm, dps=dy * spot, spot=spot)
    target = d._convergence_bound(spot, spot, cap) * carry["factor"]          # every scenario IV = spot
    v = R.build_research_view(ticker=ticker, sector="", price=spot, price_as_of="2026-11-27", target_12m=target,
                              intrinsic_value=spot, dps=dy * spot, capm=capm, carry=carry["factor"])
    assert v["tsr_12m"] == pytest.approx(ke, abs=1e-6)                         # TSR = the stock's own Ke
    assert v["research_rating"] == "NEUTRAL" and v["tactical_rating"] == "NEUTRAL"
    assert v["structural_rating"] == "NEUTRAL"                                 # IV x carry on the same horizon
    assert v["compliance"]["status"] == "clean"


def test_before_d1_the_same_fair_value_sg_stock_was_underweight():
    """The defect, pinned: no carry and the hard-coded SG hurdle (an S68-like 2.2% yielder at fair value)."""
    spot, dy = 100.0, 0.022
    v = R.build_research_view(ticker="Z74.SI", sector="", price=spot, price_as_of=None, target_12m=spot,
                              intrinsic_value=spot, dps=dy * spot)
    assert v["benchmark"]["expected_return"] == pytest.approx(0.0891, abs=1e-4)
    assert v["excess_return_bps"] == round((dy - 0.0891) * 1e4)
    assert v["research_rating"] == "UNDERWEIGHT"


def test_the_structural_rating_carries_the_iv():
    v = R.build_research_view(ticker="Z74.SI", sector="", price=4.30, price_as_of=None, target_12m=4.60,
                              intrinsic_value=4.81, dps=0.184121, capm=SG_CAPM, carry=1.010081)
    assert v["carry"] == pytest.approx(1.010081)
    s_tr = 4.81 * 1.010081 / 4.30 - 1 + 0.184121 / 4.30
    assert R.rate(s_tr, 0.067).value == v["structural_rating"]


def test_the_engine_applies_the_carry_per_scenario_and_records_it():
    src = inspect.getsource(d.run_dcf_agent)
    assert "_convergence_bound(_siv, float(_spot_for_cap), _max_capture) * _carry[\"factor\"]" in src
    assert '"rule": "target = [spot + capture x (IV - spot)] x (1 + Ke - dividend yield)"' in src
    assert '"carry": _carry,' in src
    assert '_wacc_build["market_capm"] = {' in src


def test_the_pm_passes_the_capm_and_the_carry():
    from src.agents import portfolio_manager as pm
    src = inspect.getsource(pm._research_view_for)
    assert '.get("firm_capm") or (dcf.get("wacc_build") or {}).get("market_capm")' in src
    assert 'carry=((dcf.get("pt_bridge") or {}).get("carry") or {}).get("factor")' in src


def test_override_recompute_and_calibration_use_the_carry():
    from app.backend.services import estimate_override_service as eos
    assert inspect.getsource(eos).count("* carry   # D1") == 2
    from src.memory import calibration_fit as cf
    row = {"run_date": "2026-11-28", "spot": 100.0, "capture": 0.35, "iv_bear": 80.0, "iv_base": 120.0,
           "iv_bull": 150.0, "prob_bear": 0.25, "prob_base": 0.5, "prob_bull": 0.25,
           "pt_base": (100.0 + 0.35 * 20.0) * 1.05}
    assert cf._row_carry(row) == pytest.approx(1.05)
    pre = 0.25 * (100 + 0.35 * -20) + 0.5 * (100 + 0.35 * 20) + 0.25 * (100 + 0.35 * 50)
    assert cf.pt_from(row) == pytest.approx(pre * 1.05)
    assert cf._row_carry({**row, "run_date": "2026-11-01"}) == 1.0


def test_change_attribution_separates_the_carry():
    from src.agents.pm.reader_text import build_change_attribution
    prior = {"run_at": "2026-11-01", "recap_json": {"price_at_run": 100.0, "price_target": 107.0,
                                                    "blended_iv": 120.0, "capture": 0.35, "rating_label": "Neutral"}}
    t1 = 107.0 * 1.05
    out = build_change_attribution(prior, spot=100.0, blended_iv=120.0, capture=0.35, target=t1,
                                   rating_label="Neutral", carry=1.05)
    assert out["drivers"]["carry"] == pytest.approx(t1 - 107.0, abs=0.01)
    assert out["drivers"]["residual"] == pytest.approx(0.0, abs=0.01)
    assert "cost of equity less the dividend yield" in out["sentence"]
