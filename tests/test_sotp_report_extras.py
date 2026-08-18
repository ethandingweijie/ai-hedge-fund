"""Stage 7 / Phase 7i — SOTP report enrichment (Tier 1 package) tests.

Guards ``sotp_report_extras`` (pure logic, no I/O):

  * ``build_sotp_sentence``   — GS-style one-line NAV bridge.
  * ``build_sotp_snapshot`` / ``diff_sotp_snapshots`` — New-vs-Old revisions.
  * ``sotp_elasticities``     — ±10% perturbation TP impacts.
  * ``sotp_scenario_tps``     — bear/bull multiple overrides.
  * ``build_sotp_breakdown``  — payload orchestration.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.agents.analysis.dcf_agent import _sotp_analyst_style
from src.agents.analysis.sotp_report_extras import (
    build_sotp_breakdown,
    build_sotp_sentence,
    build_sotp_snapshot,
    diff_sotp_snapshots,
    sotp_elasticities,
    sotp_scenario_tps,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sotp"


@pytest.fixture(scope="module")
def meituan_fixture() -> dict:
    with open(_FIXTURE_DIR / "gs_meituan_exhibit17.json", encoding="utf-8") as fh:
        return json.load(fh)


def _meituan_table(fixture):
    return _sotp_analyst_style(
        fixture["assumptions"],
        shares=fixture["_meta"]["shares"],
        fx_to_reporting=fixture["_meta"]["fx_usd_to_hkd"],
    )


# ── Tier 1.1: valuation sentence ─────────────────────────────────────────────

def test_sentence_meituan_shape(meituan_fixture):
    table = _meituan_table(meituan_fixture)
    s = build_sotp_sentence(table, reporting_ccy="HKD")
    assert s.startswith("TP HK$1")               # GS TP ~HK$123
    assert "Σ segments" in s
    assert "associates" in s
    assert "net cash" in s
    assert "− 15% holdco" in s
    assert "÷ 6,250M shares" in s               # 6.25bn shares
    assert "P/E" in s and "EV/Rev" in s


def test_sentence_net_debt_shape():
    """AMZN replication: negative net cash renders as '− net debt'."""
    assumptions = {
        "default_tax_rate": 0.0,
        "net_cash": -59.22e9,
        "segments": [{"name": "Core", "revenue_fwd": 100e9,
                      "ev_rev_multiple": 1.0}],
    }
    table = _sotp_analyst_style(assumptions, shares=10e9)
    s = build_sotp_sentence(table, reporting_ccy="USD")
    assert "− net debt $59.2B" in s
    assert "net cash" not in s
    # Zero associates / zero holdco are skipped entirely.
    assert "associates" not in s
    assert "holdco" not in s


def test_sentence_single_segment_no_bridge():
    """MSFT shape: one segment, no bridge items — sentence stays readable."""
    assumptions = {
        "default_tax_rate": 0.15,
        "segments": [{"name": "Microsoft Group", "revenue_fwd": 470e9,
                      "ebit_margin": 0.45, "pe_multiple": 24.0}],
    }
    table = _sotp_analyst_style(assumptions, shares=7.43e9)
    s = build_sotp_sentence(table)
    assert "Microsoft Group 24x P/E" in s
    assert "→" in s and "÷" in s


def test_sentence_empty_table():
    assert build_sotp_sentence({}) == ""
    assert build_sotp_sentence(None) == ""


# ── Tier 1.2: snapshot + revision diff ───────────────────────────────────────

def test_snapshot_round_trip(meituan_fixture):
    table = _meituan_table(meituan_fixture)
    snap = build_sotp_snapshot(meituan_fixture["assumptions"], table)
    assert snap["version"] == 1
    assert len(snap["segments"]) == len(table["rows"])
    assert snap["per_share_reporting"] == table["per_share_reporting"]
    assert snap["holdco_discount_pct"] == 0.15
    assert snap["consensus"] is None            # fixture has no FMP estimates


def test_diff_identical_snapshots_empty(meituan_fixture):
    table = _meituan_table(meituan_fixture)
    snap = build_sotp_snapshot(meituan_fixture["assumptions"], table)
    assert diff_sotp_snapshots(snap, copy.deepcopy(snap)) == []


def test_diff_detects_multiple_and_revenue_changes(meituan_fixture):
    table = _meituan_table(meituan_fixture)
    prev = build_sotp_snapshot(meituan_fixture["assumptions"], table)

    # Perturb one segment's multiple + revenue, then rebuild.
    a2 = copy.deepcopy(meituan_fixture["assumptions"])
    seg = next(s for s in a2["segments"]
               if "delivery" in str(s.get("name", "")).lower())
    seg["pe_multiple"] = seg.get("pe_multiple", 12.0) * 1.25
    seg["revenue_fwd"] = seg["revenue_fwd"] * 1.05
    table2 = _sotp_analyst_style(
        a2, shares=meituan_fixture["_meta"]["shares"],
        fx_to_reporting=meituan_fixture["_meta"]["fx_usd_to_hkd"])
    curr = build_sotp_snapshot(a2, table2)

    rows = diff_sotp_snapshots(prev, curr)
    items = {r["item"] for r in rows}
    assert any("multiple" in i for i in items)
    assert any("fwd revenue" in i for i in items)
    assert any("per share" in i for i in items)
    mult_row = next(r for r in rows if "multiple" in r["item"])
    assert mult_row["delta_pct"] == pytest.approx(0.25, rel=1e-6)


def test_diff_added_and_removed_segments():
    prev = {"segments": [{"name": "Old Co", "value": 100.0,
                          "multiple": 10.0, "revenue_fwd": 10.0,
                          "method": "P/E"}]}
    curr = {"segments": [{"name": "New Co", "value": 150.0,
                          "multiple": 12.0, "revenue_fwd": 12.0,
                          "method": "EV/Rev"}]}
    rows = diff_sotp_snapshots(prev, curr)
    labels = {r["item"] for r in rows}
    assert any("(added)" in i for i in labels)
    assert any("(removed)" in i for i in labels)


def test_diff_tiny_drift_suppressed():
    """Sub-0.5% FMP rounding noise must not surface as 'revisions'."""
    prev = {"net_cash": 10e9, "segments": []}
    curr = {"net_cash": 10e9 * 1.002, "segments": []}
    assert diff_sotp_snapshots(prev, curr) == []


def test_diff_ampersand_name_fold():
    """'&' vs 'and' flips across runs are the same segment, not add+remove."""
    prev = {"segments": [{"name": "In-store, Hotel & Travel", "value": 100.0,
                          "multiple": 10.0, "revenue_fwd": 10.0,
                          "method": "P/E"}]}
    curr = {"segments": [{"name": "In-store, Hotel and Travel", "value": 100.0,
                          "multiple": 10.0, "revenue_fwd": 10.0,
                          "method": "P/E"}]}
    assert diff_sotp_snapshots(prev, curr) == []


def test_diff_none_inputs():
    assert diff_sotp_snapshots(None, {"segments": []}) == []
    assert diff_sotp_snapshots({"segments": []}, None) == []


# ── Tier 1.4: elasticities ───────────────────────────────────────────────────

_ELAST_ASSUMPTIONS = {
    "default_tax_rate": 0.15,
    "net_cash": 10e9,
    "segments": [{"name": "Core", "revenue_fwd": 100e9, "ebit": 20e9,
                  "pe_multiple": 12.0}],
}


def test_elasticities_pe_hand_computed():
    """±10% on the P/E: impact = TP × perturb × (segment/nav)."""
    rows = sotp_elasticities(_ELAST_ASSUMPTIONS, shares=10e9)
    pe_row = next(r for r in rows if r["parameter"] == "pe_multiple")
    # base: seg 204e9 + cash 10e9 = 214e9 / 10e9 = 21.4
    # up 13.2x -> 234.4/10 = 23.44; down 10.8x -> 193.6/10 = 19.36
    assert pe_row["impact_per_share"] == pytest.approx((23.44 - 19.36) / 2, rel=1e-9)
    assert pe_row["impact_pct"] == pytest.approx(2.04 / 21.4, rel=1e-9)
    assert pe_row["elasticity"] == pytest.approx(2.04 / 21.4 / 0.10, rel=1e-9)


def test_elasticities_sorted_and_zero_skipped():
    rows = sotp_elasticities(_ELAST_ASSUMPTIONS, shares=10e9)
    impacts = [abs(r["impact_per_share"]) for r in rows]
    assert impacts == sorted(impacts, reverse=True)
    # Holdco is zero/absent → no candidate; revenue-only shocks on a pure
    # P/E segment with no EV/Rev anchor produce zero impact → dropped.
    params = {r["parameter"] for r in rows}
    assert "holdco_discount_pct" not in params
    rev_rows = [r for r in rows if r["parameter"] == "revenue_fwd"]
    assert all(abs(r["impact_per_share"]) > 1e-9 for r in rev_rows)


def test_elasticities_top_n_and_globals():
    rows = sotp_elasticities(_ELAST_ASSUMPTIONS, shares=10e9, top_n=2)
    assert len(rows) <= 2
    rows_all = sotp_elasticities(_ELAST_ASSUMPTIONS, shares=10e9, top_n=50)
    assert any(r["parameter"] == "net_cash" for r in rows_all)
    nc = next(r for r in rows_all if r["parameter"] == "net_cash")
    # ±10% on 10e9 cash over 10e9 shares → ±0.1/sh → central diff 0.1.
    assert nc["impact_per_share"] == pytest.approx(0.1, rel=1e-9)


def test_elasticities_no_input():
    assert sotp_elasticities({}, shares=10e9) == []
    assert sotp_elasticities(_ELAST_ASSUMPTIONS, shares=0) == []


# ── Tier 3.8: scenario multiples ─────────────────────────────────────────────

_SCEN_ASSUMPTIONS = {
    "default_tax_rate": 0.15,
    "segments": [{"name": "Cloud", "revenue_fwd": 100e9, "ebit": 10e9,
                  "pe_multiple": 20.0}],
}


def test_scenario_bear_hand_computed():
    """Cloud 20x→10x P/E: 10×0.85×20=170e9 vs 10×0.85×10=85e9."""
    out = sotp_scenario_tps(
        _SCEN_ASSUMPTIONS,
        {"bear": [{"name": "Cloud", "pe_multiple": 10.0}]},
        shares=10e9)
    assert "bear" in out and "bull" not in out
    assert out["bear"]["per_share"] == pytest.approx(8.5, rel=1e-9)
    assert out["bear"]["applied"] == ["Cloud"]


def test_scenario_substring_match_and_one_metric_rule():
    """Override name 'Cloud Infrastructure' matches segment 'Cloud'; the
    one-metric rule clears the other multiple before applying."""
    out = sotp_scenario_tps(
        _SCEN_ASSUMPTIONS,
        {"bull": [{"name": "Cloud Infrastructure", "pe_multiple": 30.0}]},
        shares=10e9)
    assert out["bull"]["per_share"] == pytest.approx(10 * 0.85 * 30 / 10, rel=1e-9)


def test_scenario_no_match_omitted():
    out = sotp_scenario_tps(
        _SCEN_ASSUMPTIONS,
        {"bear": [{"name": "Nonexistent", "pe_multiple": 5.0}]},
        shares=10e9)
    assert out == {}


def test_scenario_input_not_mutated():
    a = copy.deepcopy(_SCEN_ASSUMPTIONS)
    sotp_scenario_tps(a, {"bear": [{"name": "Cloud", "pe_multiple": 5.0}]},
                      shares=10e9)
    assert a == _SCEN_ASSUMPTIONS


# ── Orchestrator ─────────────────────────────────────────────────────────────

def test_breakdown_meituan_payload(meituan_fixture):
    # Production shape: dcf_agent attaches fx_usd_to_reporting to the
    # assumptions dict at attach time (dcf_agent.py ~:3764).
    a = copy.deepcopy(meituan_fixture["assumptions"])
    a["fx_usd_to_reporting"] = meituan_fixture["_meta"]["fx_usd_to_hkd"]
    meta = meituan_fixture["_meta"]
    bd = build_sotp_breakdown(a, reporting_ccy="HKD", shares=meta["shares"])
    assert bd is not None
    assert bd["method"] == "SOTP (analyst)"
    assert bd["reporting_currency"] == "HKD"
    assert bd["sentence"].startswith("TP HK$")
    assert len(bd["rows"]) >= 4
    assert bd["holdco_discount_pct"] == 0.15
    assert abs(bd["per_share_reporting"] - meta["gs_12m_tp_hkd"]) <= 1.0
    assert bd["snapshot"]["version"] == 1
    assert isinstance(bd["elasticities"], list) and bd["elasticities"]
    assert bd["scenarios"] == {}                 # fixture carries no scenarios
    assert bd["forward_estimates"] == []         # fixture carries no FMP ests
    # Keys that ride the persistence chain must all be JSON-serializable.
    json.dumps(bd, default=str)


def test_breakdown_with_estimates_and_scenarios():
    a = copy.deepcopy(_SCEN_ASSUMPTIONS)
    a["_shares"] = 10e9
    a["_fwd_estimates"] = {"period_end": "2027-06-30",
                           "revenue_avg": 120e9, "ebit_avg": 12e9,
                           "ebitda_avg": 15e9, "net_income_avg": 9e9}
    a["_scenarios"] = {"bear": [{"name": "Cloud", "pe_multiple": 10.0}],
                       "bull": [{"name": "Cloud", "pe_multiple": 30.0}]}
    bd = build_sotp_breakdown(a, reporting_ccy="USD")
    assert bd is not None
    assert len(bd["forward_estimates"]) == 1
    assert bd["forward_estimates"][0]["period_end"] == "2027-06-30"
    assert bd["forward_estimates"][0]["source"] == "FMP consensus"
    assert bd["scenarios"]["bear"]["per_share"] == pytest.approx(8.5)
    assert bd["scenarios"]["bull"]["per_share"] == pytest.approx(25.5)
    # shares default from _shares; per-share base = 170/10 + cash 0 = 17.0
    assert bd["per_share"] == pytest.approx(17.0)


def test_breakdown_none_on_missing_shares():
    assert build_sotp_breakdown({"segments": []}) is None
    assert build_sotp_breakdown({}) is None
    assert build_sotp_breakdown(None) is None
