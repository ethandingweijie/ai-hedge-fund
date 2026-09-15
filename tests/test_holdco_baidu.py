"""Baidu look-through: revenue-multiple parts, one template for BIDU and 09888.HK,
an off-by-default Kunlunxin IPO toggle, and a staging-only hold."""
import json
from pathlib import Path

import pytest

from src.agents.analysis import holdco_sotp as h

TEMPLATES = json.loads((Path(h.__file__).resolve().parents[2] / "data" / "holdco_sotp_templates.json")
                       .read_text(encoding="utf-8"))["templates"]


def test_revenue_multiple_is_revenue_times_the_band_midpoint(monkeypatch):
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0)
    val, detail = h._stated_division_value(
        {"basis": "revenue_multiple", "revenue": 29200, "multiple_range": [4.6, 5.0], "units_multiplier": 1e6}, "CNY")
    assert val == pytest.approx(29200e6 * 4.8)
    assert detail["value_low"] == pytest.approx(29200e6 * 4.6) and "equity_value" not in detail
    assert h._stated_division_value({"basis": "revenue_multiple", "revenue": 0, "multiple_range": [4, 5]}, "CNY") is None


def test_revenue_multiple_is_an_enterprise_basis():
    assert "revenue_multiple" not in h.EQUITY_BASES


@pytest.mark.parametrize("ticker", ["BIDU", "09888.HK", "9888.HK"])
def test_both_listings_resolve_to_the_one_baidu_template(ticker):
    assert h.template_for(ticker)["name"] == "Baidu Inc"


def _patched(monkeypatch):
    fx = {("CNY", "USD"): 1 / 6.7089, ("USD", "HKD"): 7.8433, ("HKD", "USD"): 1 / 7.8433,
          ("CNY", "HKD"): 7.8433 / 6.7089}
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0 if a == b else fx.get((a, b)))
    monkeypatch.setattr(h, "_market_value", lambda listed, end: {"IQ": 0.96e9, "TCOM": 25.39e9}[listed])


def test_the_hk_line_evaluates_identical_sotp_maths(monkeypatch):
    _patched(monkeypatch)
    ads = 341.7e6
    nd_usd = h.parent_net_debt("BIDU", "2026-09-15", None, "USD")
    nd_hkd = h.parent_net_debt("09888.HK", "2026-09-15", None, "HKD")
    per_ads = h.value_per_share("BIDU", "2026-09-15", ads, to_currency="USD", net_debt=nd_usd)
    per_share_hk = h.value_per_share("09888.HK", "2026-09-15", ads * 8, to_currency="HKD", net_debt=nd_hkd)
    assert per_share_hk == pytest.approx(per_ads * 7.8433 / 8, rel=1e-9)


def test_kunlunxin_ipo_case_is_off_unless_switched_on(monkeypatch):
    _patched(monkeypatch)
    assert TEMPLATES["BIDU"]["scenario_toggles"] == {"enable_kunlunxin_ipo_bull_case": False}
    base = h.look_through_value("BIDU", "2026-09-15", net_debt=0.0)
    bull = h.look_through_value("BIDU", "2026-09-15", net_debt=0.0,
                                scenario_toggles={"enable_kunlunxin_ipo_bull_case": True})
    k_base = next(p for p in base["parts"] if p["division"].startswith("Kunlunxin"))
    k_bull = next(p for p in bull["parts"] if p["division"].startswith("Kunlunxin"))
    assert k_base["value"] == pytest.approx(0.5767 * 21e9 / 6.7089, rel=1e-3) and "scenario" not in k_base
    assert k_bull["value"] == pytest.approx(0.5767 * 50e9, rel=1e-3)
    assert k_bull["scenario"] == "enable_kunlunxin_ipo_bull_case"
    assert base["scenario_toggles"]["enable_kunlunxin_ipo_bull_case"] is False
    unknown = h.look_through_value("BIDU", "2026-09-15", net_debt=0.0, scenario_toggles={"not_declared": True})
    assert unknown["gross_asset_value"] == pytest.approx(base["gross_asset_value"])


def test_baidu_is_held_out_of_production():
    import src.agents.analysis.dcf_agent as da
    assert "BIDU" not in da._LOOKTHROUGH_PROMOTE and "09888.HK" not in da._LOOKTHROUGH_PROMOTE
    gate = TEMPLATES["BIDU"]["go_live_gate"]
    assert "Form A1" in gate and "peer reporting" in gate


def test_the_allowlisted_production_names_exclude_baidu(monkeypatch):
    monkeypatch.setenv(h.FLAG, "true")
    monkeypatch.setenv(h.TICKERS_ENV, "C07.SI,00148.HK,Y92.SI,F34.SI,P15.SI,01548.HK,S08.SI,VC2.SI,BN4.SI,U96.SI")
    assert h.enabled_for("BIDU") is False and h.enabled_for("09888.HK") is False


@pytest.mark.parametrize("allowlist", ["", "BIDU,09888.HK"])
def test_a_staging_template_is_never_live_even_when_allowlisted(monkeypatch, allowlist):
    monkeypatch.setenv(h.FLAG, "true")
    monkeypatch.setenv(h.TICKERS_ENV, allowlist)
    assert h.is_staging("BIDU") and h.is_staging("09888.HK")
    assert h.enabled_for("BIDU") is False and h.enabled_for("09888.HK") is False
    assert h.enabled_for("BN4.SI") is (allowlist == "")


def test_staging_keeps_the_memory_card_on_the_analyst_path(monkeypatch):
    from src.data import segment_memory as sm
    entry = sm.load()["tickers"]["BIDU"]
    effect = sm.live_effect("BIDU", entry, fx_to=lambda c: 1.0)
    assert effect.get("method") != "SOTP / NAV (look-through)"


def test_look_through_ground_truth_nests_beside_the_analyst_reference():
    from src.agents.analysis import sotp_ground_truth as gt
    ref, _ = gt.ground_truth_for("BIDU")
    assert "segments" in ref and ref["lookthrough"]["kind"] == "lookthrough"
    graded = gt.check_lookthrough("BIDU", {"parts": [{"division": "AI Cloud Infra", "value": 20e9}],
                                          "net_asset_value": 155 * 341.7e6}, 341.7e6)
    assert graded["total"]["status"] == "in_range" and graded["parts"][1]["status"] == "in_range"


def test_baidu_net_cash_is_a_dated_haircut_override():
    ov = TEMPLATES["BIDU"]["net_debt_override"]
    assert ov["value"] < 0 and ov["as_of"] == "2026-06-30" and "30%" in ov["basis"]
