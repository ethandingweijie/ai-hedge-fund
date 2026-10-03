"""One format for every sum-of-the-parts leg (owner, 2026-10-03).

The analyst SOTP card -- segment table with method, multiple and value, then the bridge through
associates, net cash and the holdco discount to a per-share figure -- is the format every SOTP leg
publishes in: analyst, segments, look-through, published, FRE + carry. One block, `sotp_breakdown`,
for whichever leg carries weight, with its weight; the segment-only block is retired.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.utils import pdf_report as pr

SHAPE = {"method", "sentence", "reporting_currency", "rows", "segment_value", "associates", "net_cash", "nav",
         "holdco_discount_pct", "holdco_discount", "final", "per_share", "per_share_reporting", "shares",
         "revenue_label", "earnings_label", "adjustments"}
ROW = {"name", "revenue_fwd", "ebit", "method", "multiple", "value", "value_split_pct", "rationale"}


def _scen(weights, legs):
    return {"base": {"effective_weights": [{"method": k, "value_key": k, "bucket": "multi", "weight": w} for k, w in weights.items()],
                     "methods_used": list(weights), "leg_inputs": legs}}


def test_the_segment_sotp_is_the_analyst_format_with_the_premium_as_a_bridge_line():
    parts = [{"segment": "Refining", "type": "refining", "basis": "ev_ebitda", "revenue": 100e9, "ebitda_margin": 0.08,
              "ebitda_estimated": 8e9, "multiple": 5.0, "ev": 40e9, "band_rationale": "crack-spread trough"},
             {"segment": "Midstream", "type": "midstream", "basis": "ev_ebitda", "revenue": 10e9, "ebitda_margin": 0.4,
              "ebitda_estimated": 4e9, "multiple": 12.0, "ev": 48e9},
             {"segment": "Chemicals", "type": "chemicals", "basis": "carrying_value", "revenue": 0.0, "multiple": None, "ev": 6e9,
              "note": "equity-accounted: held at the segment assets the filing discloses"}]
    tr = {"segments": parts, "metric_value": 94e9 * 0.9, "growth_premium": 0.9, "value": 70.0, "priced_share_of_revenue": 1.0,
          "net_debt": 10e9, "minority_interest": 1e9, "preferred_equity": 0.0, "equity": 94e9 * 0.9 - 10e9 - 1e9, "shares": 1e9}
    sr = _scen({"SOTP (segments)": 0.4, "EV/EBITDA (norm)": 0.6}, {"SOTP (segments)": tr})
    out = d._sotp_family_breakdown(sr, d._blend_legs(sr), None, 1e9, "USD")
    assert out and SHAPE <= set(out) and out["method"] == "SOTP (segments)" and out["weight"] == 0.4
    assert [r["method"] for r in out["rows"]] == ["EV/EBITDA", "EV/EBITDA", "Book"] and ROW <= set(out["rows"][0])
    assert out["segment_value"] == pytest.approx(94e9)
    assert [a["label"] for a in out["adjustments"]] == ["Growth premium ×0.90", "Minority interest"]
    assert out["net_cash"] == -10e9 and out["final"] == pytest.approx(tr["equity"]) and out["per_share"] == 70.0
    assert sum(r["value_split_pct"] for r in out["rows"]) == pytest.approx(1.0)
    assert out["revenue_label"] == "Revenue" and "Σ segments" in out["sentence"]


def test_the_look_through_is_the_analyst_format_with_the_template_discount():
    detail = {"parts": [{"division": "Listed stake", "basis": "market_stake", "stake_pct": 0.3, "value": 60e9, "gross_value": 60e9, "discount_pct": 0.0},
                        {"division": "Ports", "basis": "ev_ebitda", "multiple_range": [8, 10], "ebitda": 5e9, "value": 45e9, "gross_value": 45e9, "discount_pct": 0.0}],
              "skipped": [], "reporting_currency": "HKD", "gross_asset_value": 105e9, "parent_net_debt": 20e9,
              "holdco_discount": 0.25, "net_asset_value": 85e9 * 0.75, "complete": True,
              "fx_to_listing": 1.0, "listing_currency": "HKD", "shares": 1e9, "per_share": 63.75}
    sr = _scen({"SOTP / NAV (look-through)": 0.7, "P/BV": 0.3}, {"SOTP / NAV (look-through)": {"kind": "sotp", "lookthrough": detail}})
    out = d._sotp_family_breakdown(sr, d._blend_legs(sr), None, 1e9, "HKD")
    assert out["method"] == "SOTP / NAV (look-through)" and out["weight"] == 0.7
    assert [(r["method"], r["multiple"]) for r in out["rows"]] == [("Market stake", None), ("EV/EBITDA", 9.0)]
    assert out["segment_value"] == 105e9 and out["net_cash"] == -20e9 and out["nav"] == 85e9
    assert out["holdco_discount_pct"] == 0.25 and out["holdco_discount"] == pytest.approx(85e9 * 0.25)
    assert out["final"] == pytest.approx(63.75e9) and out["per_share_reporting"] == 63.75


def test_the_published_and_fre_legs_take_the_same_shape():
    tbl = {"currency": "SGD", "as_of": "2025-08-12", "source": "broker",
           "segments": [{"name": "Infrastructure", "value_mn": 12000.0, "basis": "15x PE"}, {"name": "Listed", "value_mn": 3000.0, "basis": "mark to market"}],
           "adjustments": [{"name": "Group net debt", "value_mn": -7913.0, "basis": "net debt"}, {"name": "Corporate cost", "value_mn": -140.0, "basis": "corporate"}]}
    sr = _scen({"SOTP (published)": 0.35, "P/E (norm)": 0.65},
               {"SOTP (published)": {"kind": "sotp", "published": tbl, "provenance": ["2 segments", "broker (2025-08-12)"], "shares": 1.8e9, "value": 3.86}})
    out = d._sotp_family_breakdown(sr, d._blend_legs(sr), None, 1.8e9, "SGD")
    assert out["method"] == "SOTP (published)" and out["reporting_currency"] == "SGD"
    assert out["segment_value"] == 15e9 and out["net_cash"] == -7.913e9 and out["adjustments"] == [{"label": "Corporate cost", "amount": -140e6}]
    assert out["final"] == pytest.approx(15e9 - 7.913e9 - 140e6) and out["per_share"] == 3.86
    sr2 = _scen({"SOTP (FRE + carry)": 0.3, "P/DE (Forward)": 0.7},
                {"SOTP (FRE + carry)": {"kind": "sotp", "metric_value": 2e9, "multiple": 25.0, "per_share_metric": 70.0,
                                        "multiple_parts": {"net_accrued_carry_total": 3e9}}})
    out2 = d._sotp_family_breakdown(sr2, d._blend_legs(sr2), None, 0.76e9, "USD")
    assert out2["method"] == "SOTP (FRE + carry)" and out2["rows"][0]["method"] == "P/FRE" and out2["rows"][0]["value"] == 50e9
    assert out2["adjustments"] == [{"label": "Net accrued carry", "amount": 3e9}] and out2["final"] == 53e9


def test_the_analyst_block_wins_and_an_unweighted_sotp_publishes_nothing():
    analyst = {"method": "SOTP (analyst)", "rows": [{"name": "Cloud", "method": "EV/Rev", "multiple": 5.3, "value": 1e9}], "per_share_reporting": 1.0}
    sr = _scen({"SOTP (analyst)": 0.35, "DCF": 0.65}, {"SOTP (analyst)": {"kind": "sotp"}})
    out = d._sotp_family_breakdown(sr, d._blend_legs(sr), analyst, 1e9, "USD")
    assert out["weight"] == 0.35 and out["revenue_label"] == "Fwd Rev" and out["rows"] == analyst["rows"]
    none = _scen({"P/E (norm)": 1.0}, {"SOTP (segments)": {"segments": [{"segment": "x", "ev": 1.0}], "metric_value": 1.0}})
    assert d._sotp_family_breakdown(none, d._blend_legs(none), None, 1e9, "USD") is None


def test_the_legs_trace_their_tables_and_the_pdf_prints_one_block():
    src = inspect.getsource(d._compute_method_value)
    assert 'source="look-through template", lookthrough=_lt_detail' in src
    assert 'source="published table", published=res[1]' in src
    assert not hasattr(pr, "_segment_sotp_block_pdf") and "_SOTP_FAMILY_LEGS" in inspect.getsource(pr)
    assert '"segment_sotp"' not in inspect.getsource(d.run_dcf_agent)
