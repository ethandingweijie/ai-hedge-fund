"""The report shows the valuation that was computed, never a figure computed only to be shown.

Owner, 2026-09-27 (MOH): MOH's Valuation tab headlined $3,365.70 a share from a segment SOTP that
carried no weight (the published value came from P/E (norm), EV/EBITDA, DCF and EPV), priced its
parts on tech-tier EV/Revenue multiples, and printed segment shares summing to 117.6%. The rule
pinned here: every valuation block, headline and method row is published only for a leg that
carries weight in the blend, and prints that leg's own value and weight.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.utils import pdf_report as pr
from src.utils import valuation_workbook as vw


def _scen(weights: dict, table: dict, legs: dict | None = None) -> dict:
    return {"effective_weights": [{"method": k, "value_key": k, "bucket": "multi", "weight": w} for k, w in weights.items()],
            "methods_used": list(weights), "method_iv_table": table, "leg_inputs": legs or {}}


_MOH_TABLE = {"DCF": 352.0, "EPV": 213.64, "P/E (norm)": 357.99, "EV/EBITDA": 328.59, "Forward P/E": 77.1,
              "Forward EV/EBITDA": 201.98, "SOTP (segments)": 3365.7, "SOTP 12m (probabilistic)": 4100.74}
_MOH_W = {"P/E (norm)": 0.4, "EV/EBITDA": 0.3, "DCF": 0.2, "EPV": 0.1}
MOH = {s: _scen(_MOH_W, _MOH_TABLE) for s in ("bear", "base", "bull")}


def test_the_blend_is_the_weighted_legs_only():
    legs = d._blend_legs(MOH)
    assert legs == _MOH_W
    assert "SOTP (segments)" not in legs and "Forward P/E" not in legs
    # a run with no weight record falls back to methods_used
    assert set(d._blend_legs({"base": {"methods_used": ["DCF"]}})) == {"DCF"}


def test_the_payload_publishes_display_blocks_only_for_weighted_legs():
    src = inspect.getsource(d.run_dcf_agent)
    assert "_legs_in_blend = _blend_legs(scenario_results)" in src
    assert '"segment_sotp":          _segment_sotp_pub,' in src
    assert "if _sotp_analyst_weight is not None else None" in src
    assert "and _sotp_analyst_declared) else None)" in src


def test_segment_shares_are_measured_against_the_sum_of_the_parts():
    parts = [{"segment": "Medicaid", "type": "default", "basis": "ev_revenue", "revenue": 32.24e9, "multiple": 4.5, "ev": 145.08e9},
             {"segment": "Medicare", "type": "default", "basis": "ev_revenue", "revenue": 6.235e9, "multiple": 4.5, "ev": 28.0575e9},
             {"segment": "Marketplace", "type": "marketplace", "basis": "ev_revenue", "revenue": 4.487e9, "multiple": 6.0, "ev": 26.922e9},
             {"segment": "Other", "type": "default", "basis": "ev_revenue", "revenue": 0.177e9, "multiple": 4.5, "ev": 0.7965e9}]
    parts_sum = sum(p["ev"] for p in parts)
    base = {"leg_inputs": {"SOTP (segments)": {"segments": parts, "metric_value": parts_sum * 0.85,
                                               "growth_premium": 0.85, "value": 3365.70, "priced_share_of_revenue": 1.0}}}
    blk = d._segment_sotp_block(base, 52.2e6, "USD")
    assert blk["checks"]["share_of_ev_sums_to_100"] is True            # was 117.6% on MOH
    assert blk["sum_of_parts_ev"] == pytest.approx(parts_sum) and blk["growth_premium"] == 0.85
    assert blk["total_ev"] == pytest.approx(parts_sum * 0.85)
    assert "EV/Revenue rows" in blk["basis_note"] and "EV/EBITDA rows" not in blk["basis_note"]


def test_the_reit_headline_is_the_nav_leg_or_nothing():
    rb = {"nav_per_share": 9.99, "gross_asset_value": 1.0, "noi": 5.0, "cap_rate_used": 0.065}
    tr = {"NAV (Cap Rates)": {"gross_asset_value": 200.0, "noi": 11.0, "cap_rate": 0.055, "cap_rate_source": "live", "total_debt": 80.0}}
    reit = {s: _scen({"NAV (Cap Rates)": 0.6, "P/AFFO": 0.4}, {"NAV (Cap Rates)": 0.75, "P/AFFO": 1.89}, tr)
            for s in ("bear", "base", "bull")}
    out = d._align_reit_breakdown(rb, reit, d._blend_legs(reit), 100.0)
    assert out["nav_per_share"] == 0.75 and out["nav_method"] == "NAV (Cap Rates)" and out["nav_weight"] == 0.6
    assert out["gross_asset_value"] == 200.0 and out["cap_rate_used"] == 0.055 and out["noi"] == 11.0
    dev = {s: _scen({"P/BV": 1.0}, {"P/BV": 4.2}) for s in ("bear", "base", "bull")}
    none = d._align_reit_breakdown(rb, dev, d._blend_legs(dev), 100.0)
    assert none["nav_per_share"] is None and none["nav_method"] is None and none["gross_asset_value"] is None


def test_the_bank_headline_is_the_ggm_leg_or_nothing():
    bb = {"fair_value_per_share": 151.0, "fair_p_tbv": 0.93, "ptbv_excluded": False}
    bank = {s: _scen({"GGM (P/B)": 0.3, "Residual Income": 0.7}, {"GGM (P/B)": 337.31, "Residual Income": 238.57},
                     {"GGM (P/B)": {"target_pb": 2.15}}) for s in ("bear", "base", "bull")}
    out = d._align_bank_breakdown(bb, bank, d._blend_legs(bank))
    assert out["fair_value_per_share"] == 337.31 and out["fair_value_method"] == "GGM (P/B)" and out["fair_p_tbv"] == 2.15
    nogg = {s: _scen({"Residual Income": 1.0}, {"Residual Income": 238.57}) for s in ("bear", "base", "bull")}
    out2 = d._align_bank_breakdown(bb, nogg, d._blend_legs(nogg))
    assert out2["fair_value_per_share"] is None and out2["ptbv_excluded"] is True


def test_the_pdf_lists_only_weighted_legs_and_omits_unweighted_sotp():
    assert set(pr._legs_in_blend(MOH)) == set(_MOH_W)
    assert pr._leg_carries_weight(MOH, pr._SOTP_SEGMENT_LEGS) is False
    assert pr._leg_carries_weight({}, pr._SOTP_SEGMENT_LEGS) is True       # legacy payload: no weight record
    assert pr._segment_sotp_block_pdf({**MOH, "segment_sotp": {"segments": [{"segment": "x"}]}}, None, 400.0) == []
    src = inspect.getsource(pr)
    assert "Cross-checks (computed, not in the blend)" not in src


def test_the_workbook_rebuilds_only_weighted_legs():
    book = vw._Book.__new__(vw._Book)
    book.dr = MOH
    assert book.in_blend("P/E (norm)") and not book.in_blend("SOTP (segments)") and not book.in_blend("Forward P/E")
    legacy = vw._Book.__new__(vw._Book)
    legacy.dr = {"base": {"method_iv_table": {"X": 1.0}}}
    assert legacy.in_blend("X")                                             # no weight record: unchanged
