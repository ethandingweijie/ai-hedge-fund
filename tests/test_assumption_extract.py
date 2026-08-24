"""Workstream R1 — assumption_extract lenient-parse tests (offline).

Pins the live BABA failure modes (2026-08-24): Qwen under json_mode
returns shape-drifted output — fiscal_quarter "Q1" as a string,
collections as objects instead of arrays, numeric growth rates for
Optional[str] fields, and free-form key aliases (reported_yoy_growth,
drivers). The lenient coercion must salvage all of it.
"""
from __future__ import annotations

import json

from src.memory import assumption_extract as ae


# ── Scalar coercers ──────────────────────────────────────────────────────────

def test_coerce_quarter_forms():
    assert ae._coerce_quarter("Q1") == 1
    assert ae._coerce_quarter("q4") == 4
    assert ae._coerce_quarter("2nd quarter") == 2
    assert ae._coerce_quarter("3") == 3
    assert ae._coerce_quarter(4) == 4
    assert ae._coerce_quarter(7) is None     # out of range stays None
    assert ae._coerce_quarter(None) is None


def test_coerce_year_forms():
    assert ae._coerce_year("FY2027") == 2027
    assert ae._coerce_year("2026") == 2026
    assert ae._coerce_year(2026) == 2026
    assert ae._coerce_year("fiscal year ended 2025") == 2025
    assert ae._coerce_year(None) is None


def test_coerce_list_forms():
    # array passthrough
    assert ae._coerce_list([{"a": 1}]) == [{"a": 1}]
    # {note, items} wrapper — note dropped, items used
    assert ae._coerce_list({"note": "none", "items": [{"m": 1}]}) == [{"m": 1}]
    # dict keyed by name → list with the key injected under every
    # plausible item key field (name/metric/field/item); each item
    # schema picks up the one it declares, extras are ignored
    got = ae._coerce_list({"Cloud": {"growth": "45%"}})
    assert got[0]["growth"] == "45%"
    assert got[0]["name"] == "Cloud"
    assert got[0]["metric"] == "Cloud"
    assert got[0]["field"] == "Cloud"
    # dict of name→string (KPI shape)
    assert ae._coerce_list({"88VIP": "64m"}) == [{"name": "88VIP", "value": "64m"}]
    assert ae._coerce_list(None) == []


def test_coerce_quotes():
    assert ae._coerce_quotes(["a", "b"]) == ["a", "b"]
    assert ae._coerce_quotes({"quote": "x"}) == ["x"]
    assert ae._coerce_quotes([{"text": "y"}]) == ["y"]
    assert ae._coerce_quotes(None) == []


# ── Alias normalization ──────────────────────────────────────────────────────

def test_segment_alias_reported_yoy_growth():
    s = ae.SegmentItem(**{"name": "Cloud", "reported_yoy_growth": "45%",
                          "outlook": None})
    assert s.growth_rate_pct == "45%"
    assert s.outlook == ""


def test_margin_alias_drivers_and_reported():
    m = ae.MarginItem(**{"metric": "operating_margin", "reported": "6%",
                         "direction": "down", "drivers": "impairment"})
    assert m.driver == "impairment"
    assert m.quote == "6%"


def test_kpi_alias_description():
    k = ae.KpiItem(**{"name": "Cloud rev", "description": "RMB12.4bn",
                      "yoy": "45%"})
    assert k.value == "RMB12.4bn"
    assert k.growth_pct == "45%"


# ── End-to-end salvage of the live drifted shapes ───────────────────────────

def test_salvage_object_shaped_collections():
    """First live shape: everything an object, strings for ints."""
    payload = {
        "fiscal_year": "2027",
        "fiscal_quarter": "Q1",
        "fiscal_period_label": "Three months ended June 30, 2026",
        "guidance": {"note": "No explicit guidance", "items": []},
        "segments": {
            "Cloud Intelligence": {"reported_yoy_growth": "45%",
                                   "outlook": "acceleration"},
        },
        "margins": {
            "adjusted_ebita_margin": {"reported": "10%",
                                      "direction": "down",
                                      "drivers": "investment"},
        },
        "kpis": {"88VIP Members": "64 million"},
        "capital_allocation": [],
        "one_offs": [],
        "verbatim_quotes": ["quote one", "quote two"],
    }
    out = ae._salvage(json.dumps(payload), ae.EarningsAssumptionOutput)
    assert out is not None
    assert out.fiscal_year == 2027 and out.fiscal_quarter == 1
    assert out.guidance == []
    assert out.segments[0].name == "Cloud Intelligence"
    assert out.segments[0].growth_rate_pct == "45%"
    assert out.margins[0].driver == "investment"
    assert out.kpis[0].name == "88VIP Members"
    assert out.kpis[0].value == "64 million"


def test_salvage_numeric_growth_and_arrays():
    """Second live shape: correct arrays but numeric growth values."""
    payload = {
        "fiscal_year": 2026,
        "fiscal_quarter": 1,
        "fiscal_period_label": "quarter ended June 30, 2026",
        "guidance": [],
        "segments": [
            {"name": "China E-commerce", "growth_rate_pct": -8,
             "outlook": None, "constant_currency_note": None},
        ],
        "margins": [],
        "kpis": [{"name": "CapEx", "value": "RMB67.7bn",
                  "growth_pct": 75}],
        "capital_allocation": [{"action": "buyback", "detail": "$2B"}],
        "one_offs": [{"item": "goodwill impairment", "amount": "",
                      "impact": "negative"}],
        "verbatim_quotes": ["resilient profits"],
    }
    out = ae._salvage(json.dumps(payload), ae.EarningsAssumptionOutput)
    assert out is not None
    assert out.fiscal_year == 2026 and out.fiscal_quarter == 1
    assert out.segments[0].growth_rate_pct == "-8"
    assert out.kpis[0].growth_pct == "75"
    assert out.capital_allocation[0].action == "buyback"


def test_salvage_analyst_report_shapes():
    payload = {
        "house": "Goldman Sachs",
        "analyst": None,
        "report_date": "2026-08-21",
        "rating": "Buy",
        "price_target": "US$186 / HK$180",
        "price_target_currency": "USD",
        "pt_methodology": "SOTP",
        "estimates": [{"fiscal_year_label": "FY2027", "revenue": 1050.2,
                       "ebitda": None, "eps": "9.1"}],
        "house_vs_consensus": [],
        "scenarios": [],
        "revisions": {"FY27 capex": {"new_value": "Rmb210bn",
                                     "prior_value": "Rmb190bn",
                                     "direction": "up"}},
        "thesis_points": {"cloud": "AI acceleration"},
        "catalysts": [],
        "risks": [],
    }
    out = ae._salvage(json.dumps(payload), ae.AnalystReportOutput)
    assert out is not None
    assert out.house == "Goldman Sachs"
    assert out.analyst == ""
    assert out.estimates[0].revenue == "1050.2"
    assert out.revisions[0].field == "FY27 capex"
    assert out.revisions[0].new_value == "Rmb210bn"
    assert out.thesis_points == ["AI acceleration"] or \
        out.thesis_points[0] == "AI acceleration"


def test_parse_amount_regression():
    # shared with dcf consumption — keep the money forms pinned here too
    assert ae.parse_amount("US$186 / HK$180") == 186.0
    assert ae.parse_amount("Rmb210bn") == 210e9
    assert abs(ae.parse_amount("$130-145bn") - 137.5e9) < 1.0
