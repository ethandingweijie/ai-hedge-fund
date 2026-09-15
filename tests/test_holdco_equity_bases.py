"""Equity-value look-through parts (pe_range, fixed_value) and look-through grading.

Keppel's and Sembcorp's brokers value segments on NET PROFIT x P/E, an equity
value with project debt already inside it. Deducting group net debt on top
(S$9.3bn and S$13.9bn) would count that debt twice.
"""
import json
from pathlib import Path

import pytest

from src.agents.analysis import holdco_sotp as h
from src.agents.analysis import sotp_ground_truth as gt


def test_pe_range_is_net_profit_times_the_band_midpoint(monkeypatch):
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0)
    val, detail = h._stated_division_value(
        {"basis": "pe_range", "net_profit": 189, "multiple_range": [18, 22], "units_multiplier": 1e6}, "SGD")
    assert val == pytest.approx(189e6 * 20)
    assert detail["equity_value"] is True and detail["value_low"] == pytest.approx(189e6 * 18)


def test_pe_range_refuses_a_loss_unless_allowed(monkeypatch):
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0)
    assert h._stated_division_value({"basis": "pe_range", "net_profit": -23, "multiple_range": [9, 11]}, "SGD") is None


def test_fixed_value_carries_a_haircut_amount_or_a_senior_claim(monkeypatch):
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0)
    assert h._stated_division_value({"basis": "fixed_value", "amount": 2590, "units_multiplier": 1e6}, "SGD")[0] == 2590e6
    assert h._stated_division_value({"basis": "fixed_value", "amount": -726}, "SGD") is None
    assert h._stated_division_value({"basis": "fixed_value", "amount": -726, "negative_ok": True,
                                     "units_multiplier": 1e6}, "SGD")[0] == -726e6


def test_equity_bases_never_call_for_consolidated_net_debt():
    assert {"market_stake", "pe_range", "fixed_value", "nil"} <= h.EQUITY_BASES
    assert "ev_ebitda_range" not in h.EQUITY_BASES and "cap_rate" not in h.EQUITY_BASES


def test_keppel_template_is_all_equity_and_needs_no_division_ebitda():
    t = json.loads((Path(h.__file__).resolve().parents[2] / "data" / "holdco_sotp_templates.json")
                   .read_text(encoding="utf-8"))["templates"]
    for ticker in ("BN4.SI", "U96.SI"):
        assert all(d["basis"] in h.EQUITY_BASES for d in t[ticker]["divisions"]), ticker
    assert t["BN4.SI"]["holdco_discount"] == [0.1, 0.1] and t["U96.SI"]["holdco_discount"] == [0.05, 0.05]


def test_look_through_values_an_equity_only_template(monkeypatch):
    tpl = {"currency": "SGD", "holdco_discount": [0.1, 0.1], "divisions": [
        {"name": "Keppel REIT", "basis": "market_stake", "listed": "K71U.SI", "stake_pct": 0.5},
        {"name": "Asset management", "basis": "pe_range", "net_profit": 100, "multiple_range": [20, 20],
         "units_multiplier": 1e6},
        {"name": "Perpetuals", "basis": "fixed_value", "amount": -500, "negative_ok": True, "units_multiplier": 1e6},
    ]}
    monkeypatch.setattr(h, "template_for", lambda t: tpl)
    monkeypatch.setattr(h, "_market_value", lambda listed, end: 2e9)
    monkeypatch.setattr(h, "_fx", lambda a, b: 1.0)
    res = h.look_through_value("BN4.SI", "2026-09-15")
    assert res["complete"] is True
    assert res["gross_asset_value"] == pytest.approx(1e9 + 2e9 - 0.5e9)
    assert res["net_asset_value"] == pytest.approx(2.5e9 * 0.9)


def test_look_through_grading_matches_parts_by_keyword_in_order(monkeypatch):
    ref = {"kind": "lookthrough", "source": "Maybank", "as_of": "2026-09-01", "unit": "SGD per share",
           "total_per_share": [11.7, 14.3],
           "parts": [{"name": "Listed REIT and trust stakes", "keywords": ["reit", "trust"], "per_share": [1.5, 1.9]},
                     {"name": "Infrastructure and Real Estate", "keywords": ["infrastructure", "realestate"],
                      "per_share": [5.1, 6.3]},
                     {"name": "Unlisted fund stakes", "keywords": ["unlistedfund"], "per_share": [1.0, 1.2]}]}
    monkeypatch.setattr(gt, "ground_truth_for", lambda t: (ref, 1.0))
    result = {"net_asset_value": 22.5e9, "parts": [
        {"division": "Keppel Infrastructure Trust", "value": 0.6e9},      # a trust, not operating infra
        {"division": "Keppel REIT", "value": 2.2e9},
        {"division": "Infrastructure and Real Estate (operating)", "value": 12.9e9},
        {"division": "Perpetual securities", "value": -0.7e9}]}
    g = gt.check_lookthrough("BN4.SI", result, 1800e6)
    parts = {p["name"]: p for p in g["parts"]}
    assert parts["Listed REIT and trust stakes"]["value_per_share"] == pytest.approx(2.8e9 / 1800e6, rel=1e-4)
    assert parts["Infrastructure and Real Estate"]["status"] == "above"
    assert parts["Unlisted fund stakes"]["status"] == "missing"
    assert g["unmatched_parts"] == ["Perpetual securities"]
    assert g["total"]["status"] == "in_range" and g["plausible"] is True


@pytest.mark.parametrize("ticker", ["BN4.SI", "U96.SI", "S08.SI"])
def test_keppel_and_sembcorp_gain_sotp_nav_when_their_look_through_completes(monkeypatch, ticker):
    import src.agents.analysis.dcf_agent as da
    monkeypatch.setattr(h, "enabled", lambda: True)
    monkeypatch.setattr(h, "can_value", lambda t, end, **kw: True)
    monkeypatch.delenv(h.TICKERS_ENV, raising=False)
    profile = {"methods": [{"name": "DCF", "weight": 0.6}, {"name": "Utility P/E", "weight": 0.4}]}
    promoted, on = da._promote_lookthrough_sotp(profile, ticker, "2026-09-15")
    assert on is True
    weights = {m["name"]: m["weight"] for m in promoted["methods"]}
    assert weights["SOTP / NAV"] == pytest.approx(da._LOOKTHROUGH_PROMOTE_WEIGHT)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert profile["methods"][0]["weight"] == 0.6                          # copy-on-write


def test_a_name_outside_the_promotion_set_is_untouched(monkeypatch):
    import src.agents.analysis.dcf_agent as da
    monkeypatch.setattr(h, "enabled", lambda: True)
    monkeypatch.setattr(h, "can_value", lambda t, end, **kw: True)
    profile = {"methods": [{"name": "DCF", "weight": 1.0}]}
    assert da._promote_lookthrough_sotp(profile, "D05.SI", "2026-09-15") == (profile, False)


def test_the_table_grader_ignores_look_through_references(monkeypatch):
    monkeypatch.setattr(gt, "ground_truth_for", lambda t: ({"kind": "lookthrough"}, 1.0))
    assert gt.check_table("BN4.SI", {"shares": 1, "rows": []}) is None
