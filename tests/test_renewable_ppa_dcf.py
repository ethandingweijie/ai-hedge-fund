"""Wave 10 renewables (owner methodology, 2026-10-03): the PPA project-finance DCF behind the IPP
profile's "PPA-backed DCF" anchor -- P50 generation with degradation, a contracted window at the
PPA, a capture-adjusted merchant tail, opex and maintenance reserves, tax net of credits,
bifurcated discounting, and FCFE through sculpted project debt when the input carries it.
"""
import inspect
from pathlib import Path

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.industry import gemini_params as gp
from src.data import industry_inputs as ii

_CFG = {**d._PPA_DEFAULTS}
_INP = {"period": "FY2025", "capacity_mw": 1000.0, "generation_gwh": 2500.0, "contracted_pct": 0.90, "remaining_ppa_years": 12.0,
        "avg_ppa_price": 50.0, "ppa_escalator_pct": 0.01, "merchant_price": 35.0, "opex": 30e6,
        "technology_mix": [{"technology": "solar", "share": 0.6}, {"technology": "wind", "share": 0.4}]}


def test_the_registration_is_complete():
    assert "ppa" in ii.KINDS and "ppa" in ii.NO_OVERLAY and ii.BOUNDS["ppa"] == ("revenue", 0.3, 1.5)
    assert gp.INDUSTRY_INPUT_SCHEMAS["ppa"] is gp.PpaPortfolio
    assert set(gp.INDUSTRY_INPUT_SCHEMAS) == set(ii.KINDS) == set(gp._INDUSTRY_ASK) == set(gp._OVERLAY_ASK)
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    ipp_profile = next(v["IPP"] for v in P.values() if isinstance(v, dict) and "IPP" in v) if "IPP" not in P else P["IPP"]
    ipp = [m for m in ipp_profile["methods"] if m["name"] == "PPA-backed DCF"][0]
    assert ipp["anchor"] is True and ipp["weight"] == 0.40 and "accepted ppa input" in ipp["note"]
    from src.data import valuation_constants as vc
    blk = vc.load()["ppa_project_finance"]
    assert blk["status"] == "PROPOSED" and blk["reviewed"]["reviewer"] is None
    assert blk["degradation_by_technology"]["solar"] == 0.005 and blk["merchant_wacc_premium"] == 0.015
    cfg = d._ppa_cfg()
    assert cfg["capture_rate"] == 0.85 and cfg["scenario"]["bear"]["merchant_price"] == 0.80
    root = Path(__file__).resolve().parents[1] / "app" / "frontend" / "src"
    assert "ppa:" in (root / "pages" / "ModelAccuracyPage.tsx").read_text(encoding="utf-8")
    assert "| 'nav' | 'ppa';" in (root / "lib" / "api.ts").read_text(encoding="utf-8")


def test_ppa_to_engine_converts_quantities_ratios_and_money_and_derives_the_price():
    fx = lambda ccy: 1.0   # noqa: E731
    data = {"fiscal_year": "FY2025",
            "capacity_mw": {"value": 1000, "unit": "MW", "period": "FY2025", "source_url": "https://x", "quote": "q"},
            "generation_gwh": {"value": 2500, "unit": "GWh", "period": "FY2025", "source_url": "https://x", "quote": "q"},
            "contracted_pct": {"value": 90, "period": "FY2025", "source_url": "https://x", "quote": "q"},             # 90 -> 0.90
            "remaining_ppa_years": {"value": 12, "unit": "years", "period": "FY2025", "source_url": "https://x", "quote": "q"},
            "contracted_revenue": {"value": 112.5, "currency": "USD", "scale": "mn", "period": "FY2025", "source_url": "https://x", "quote": "q"},
            "project_debt": {"value": 1.2, "currency": "USD", "scale": "bn", "period": "FY2025", "source_url": "https://x", "quote": "q"},
            "project_debt_cost": {"value": 0.05, "period": "FY2025", "source_url": "https://x", "quote": "q"},
            "technology_mix": [{"technology": "solar", "share_pct": 60}, {"technology": "wind", "share_pct": 0.4}]}
    out = gp.ppa_to_engine(data, fx)
    assert out["contracted_pct"] == 0.9 and out["project_debt"] == 1.2e9 and out["project_debt_cost"] == 0.05
    assert out["contracted_revenue"] == 112.5e6
    assert out["avg_ppa_price"] == pytest.approx(112.5e6 / (2500 * 1000 * 0.9)) and out["avg_ppa_price_derived"] is True  # $50/MWh
    assert out["technology_mix"] == [{"technology": "solar", "share": 0.6}, {"technology": "wind", "share": 0.4}]
    assert "merchant_price" not in out


def test_the_leg_prices_the_contracted_window_then_the_merchant_tail_and_stops_at_the_asset_life():
    out = d._ppa_project_finance_dcf(_INP, scenario="base", wacc=0.06, shares=100e6, net_debt=400e6, cfg=_CFG)
    assert out["basis"] == "FCFF (bifurcated WACC)" and out["value_fcfe"] is None
    assert out["asset_life_years"] == 28 and out["contract_years"] == 12 and len(out["rows"]) == 28   # 0.6*30 + 0.4*25
    assert out["degradation"] == pytest.approx(0.6 * 0.005 + 0.4 * 0.002)
    r1, r13 = out["rows"][0], out["rows"][12]
    assert r1["mwh"] == 2_500_000 and r1["contracted"] is True and r13["contracted"] is False
    assert r1["revenue_contracted"] == pytest.approx(2.5e6 * 0.9 * 50.0, rel=1e-6)
    assert r1["revenue_merchant"] == pytest.approx(2.5e6 * 0.1 * 35.0 * 0.85, rel=1e-6)                 # capture-adjusted
    assert r13["revenue_contracted"] == 0 and r13["revenue_merchant"] > 0
    assert out["wacc_merchant"] == pytest.approx(0.075) and out["opex_basis"] == "stated opex / generation"
    assert out["opex_per_mwh"] == pytest.approx(12.0)
    assert out["pv_contracted"] > out["pv_merchant"] > 0
    assert out["value"] == pytest.approx((out["ev"] - 400e6) / 100e6)
    # scenarios: bear takes P75-like generation and a weaker merchant price; bull a stronger one
    bear = d._ppa_project_finance_dcf(_INP, scenario="bear", wacc=0.06, shares=100e6, net_debt=400e6, cfg=_CFG)
    bull = d._ppa_project_finance_dcf(_INP, scenario="bull", wacc=0.06, shares=100e6, net_debt=400e6, cfg=_CFG)
    assert bear["rows"][0]["mwh"] == 2_375_000 and bear["value"] < out["value"] < bull["value"]


def test_fcfe_through_sculpted_project_debt_is_preferred_when_the_input_carries_it():
    inp = {**_INP, "project_debt": 1.2e9, "project_debt_cost": 0.05, "tax_equity": 100e6}
    out = d._ppa_project_finance_dcf(inp, scenario="base", wacc=0.06, shares=100e6, net_debt=1.5e9, cfg=_CFG)
    assert out["basis"] == "FCFE (sculpted project debt)" and out["debt_amort_years"] == 12
    assert out["rows"][0]["principal"] == 100_000_000 and out["rows"][0]["interest"] == 60_000_000
    assert out["rows"][12]["principal"] == 0 and out["rows"][12]["interest"] == 0                         # repaid with the contract
    assert out["corporate_net_debt"] == pytest.approx(0.3e9) and out["cost_of_equity"] == pytest.approx(0.075)
    assert out["value"] == out["value_fcfe"] == pytest.approx((out["pv_fcfe"] - 0.3e9 - 100e6) / 100e6)
    assert out["value_fcff"] is not None and out["value_fcff"] != out["value_fcfe"]


def test_the_leg_declines_without_generation_term_or_price_and_the_dispatcher_falls_through():
    assert d._ppa_project_finance_dcf({**_INP, "avg_ppa_price": None}, scenario="base", wacc=0.06, shares=1e6, net_debt=0.0, cfg=_CFG) is None
    assert d._ppa_project_finance_dcf({**_INP, "remaining_ppa_years": None}, scenario="base", wacc=0.06, shares=1e6, net_debt=0.0, cfg=_CFG) is None
    src = inspect.getsource(d._compute_method_value)
    assert 'method_name == "PPA-backed DCF" and isinstance(most_recent.get("_ppa_detail"), dict)' in src
    assert 'kind="ppa_dcf"' in src
    run = inspect.getsource(d.run_dcf_agent)
    assert '_ii_p.accepted_entry(ticker, "ppa")' in run and '"gate_id": "GATE_PPA_INPUT"' in run
    assert "PPA-backed DCF ran as the core DCF" in run


def test_the_builder_has_the_ppa_branch_and_the_wave_10_set():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "scripts" / "build_industry_inputs.py"
    src = p.read_text(encoding="utf-8")
    assert 'if kind == "ppa":' in src and '"capacity factor plausible"' in src and '"contracted price available"' in src
    assert '"10": WAVE10' in src and '"00916.HK", "BEPI", "ENLT", "BEPC", "CWEN", "XIFR", "ORA"' in src
