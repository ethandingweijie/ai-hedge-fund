"""The DCF assumptions table prints the growth and margins the DCF ran on.

Owner rule: no display-only valuation. With the guidance forecast active the
DCF discounts its own year-by-year projection rows; the legacy single-value
``growth_rate`` / ``fcf_margin_start`` are superseded. The PDF's section 2f
table printed the legacy pair anyway (Z74.SI base: 4.5% / 17.7% against a DCF
that grew 3.5% in Year 1 at an 8.3% FCF margin). ``_dcf_assumptions_used``
reads the rows; the legacy fields are a fallback for archived runs only.
The web panel (app/frontend/src/lib/dcfAssumptions.ts) mirrors this helper.
"""
from __future__ import annotations

import math

import pytest
from reportlab.platypus import Paragraph, Table

from src.utils import pdf_report
from src.utils.pdf_report import _dcf_assumptions_used


def _rows(growths, margins):
    return [
        {"year_label": f"Yr {i + 1}", "growth_pct": g, "fcf_margin": m}
        for i, (g, m) in enumerate(zip(growths, margins))
    ]


# Z74.SI base, as stored in web_runs on 2026-11-27 (rounded copies).
Z74_BASE = {
    "growth_rate": 0.0454,
    "fcf_margin_start": 0.1771,
    "margin_delta_absolute": 0.003,
    "tgr": 0.015,
    "tv_pct": 0.6,
    "intrinsic_value": 3.1,
    "leg_inputs": {"DCF": {
        "growth_schedule": [0.035, 0.031, 0.027, 0.023, 0.019, 0.015, 0.015, 0.015, 0.015, 0.015],
        "projection_rows": _rows(
            [0.035, 0.031, 0.027, 0.023, 0.019, 0.015, 0.015, 0.015, 0.015, 0.015],
            [0.083084, 0.089832, 0.096631, 0.103485, 0.110391,
             0.117353, 0.117353, 0.117353, 0.117353, 0.117353],
        ),
    }},
}


def test_reads_the_projection_rows_not_the_legacy_fields():
    u = _dcf_assumptions_used(Z74_BASE)
    assert u["source"] == "schedule"
    assert u["y1_growth"] == pytest.approx(0.035)
    expected_cagr = math.prod(1 + g for g in [0.035, 0.031, 0.027, 0.023, 0.019]) ** 0.2 - 1
    assert u["cagr5"] == pytest.approx(expected_cagr)
    assert round(u["cagr5"], 3) == 0.027
    assert u["y1_margin"] == pytest.approx(0.083084)
    assert u["terminal_margin"] == pytest.approx(0.117353)
    assert u["years"] == 10
    # and none of them is the legacy pair
    assert u["y1_growth"] != Z74_BASE["growth_rate"]
    assert u["y1_margin"] != Z74_BASE["fcf_margin_start"]


def test_growth_falls_back_to_the_leg_growth_schedule():
    sc = {"growth_rate": -0.061, "fcf_margin_start": 0.286,
          "leg_inputs": {"DCF": {"growth_schedule": [0.06, 0.07, 0.037, 0.005, -0.028, 0.0]}}}
    u = _dcf_assumptions_used(sc)
    assert u["source"] == "schedule"
    assert u["y1_growth"] == pytest.approx(0.06)
    assert u["cagr5"] is not None and u["cagr5"] > 0
    # no margin rows -> the Year-1 margin input, no terminal
    assert u["y1_margin"] == pytest.approx(0.286)
    assert u["terminal_margin"] is None


@pytest.mark.parametrize("sc", [
    {"growth_rate": 0.08, "fcf_margin_start": 0.2},
    {"growth_rate": 0.08, "fcf_margin_start": 0.2, "leg_inputs": {}},
    {"growth_rate": 0.08, "fcf_margin_start": 0.2, "leg_inputs": {"DCF": {"projection_rows": []}}},
    {"growth_rate": 0.08, "fcf_margin_start": 0.2, "leg_inputs": {"DCF": {"projection_rows": "bad"}}},
])
def test_legacy_fields_only_when_no_schedule_exists(sc):
    u = _dcf_assumptions_used(sc)
    assert u["source"] == "legacy"
    assert u["y1_growth"] == pytest.approx(0.08)
    assert u["y1_margin"] == pytest.approx(0.2)
    assert u["cagr5"] is None and u["terminal_margin"] is None


def test_never_raises_on_empty_or_junk():
    for sc in (None, {}, {"leg_inputs": None}, {"leg_inputs": {"DCF": {"projection_rows": [None, {"growth_pct": "x"}]}}}):
        u = _dcf_assumptions_used(sc)
        assert u["source"] == "legacy"


def test_short_schedule_has_no_five_year_cagr():
    sc = {"leg_inputs": {"DCF": {"projection_rows": _rows([0.05, 0.04, 0.03], [0.1, 0.1, 0.1])}}}
    u = _dcf_assumptions_used(sc)
    assert u["y1_growth"] == pytest.approx(0.05)
    assert u["cagr5"] is None
    assert u["terminal_margin"] == pytest.approx(0.1)


def _table_text(flowables) -> list[list[str]]:
    out = []
    for f in flowables:
        if isinstance(f, Table):
            for row in f._cellvalues:
                out.append([c.text if isinstance(c, Paragraph) else str(c) for c in row])
    return out


def test_section_2f_prints_the_used_figures():
    import copy
    bear = copy.deepcopy(Z74_BASE)
    bull = copy.deepcopy(Z74_BASE)
    dcf = {"base": Z74_BASE, "bear": bear, "bull": bull, "wacc": 0.0529,
           "profile": "Telecom", "revenue_base": 14.26e9, "shares_outstanding": 16.5e9}
    story = pdf_report._section_2f("Z74.SI", dcf, {}, {}, pdf_report._build_styles(), 500.0)
    rows = {r[0]: r[1:] for r in _table_text(story) if r}
    assert rows["Revenue Growth — Year 1"][1] == "3.5%"
    assert rows["Revenue Growth — 5-yr CAGR (Y1–Y5)"][1] == "2.7%"
    assert rows["FCF Margin — Year 1"][1] == "8.3%"
    assert rows["FCF Margin — terminal (Yr 10)"][1] == "11.7%"
    # the legacy single values are not printed as the applied assumptions
    assert "Revenue Growth (applied)" not in rows
    assert "FCF Margin (base year, pre-delta)" not in rows


def test_section_2f_keeps_the_legacy_rows_for_archived_runs():
    legacy = {"growth_rate": 0.0454, "fcf_margin_start": 0.1771, "margin_delta_absolute": 0.003,
              "tgr": 0.015, "intrinsic_value": 3.1}
    dcf = {"base": legacy, "bear": dict(legacy), "bull": dict(legacy), "wacc": 0.0529,
           "revenue_base": 14.26e9, "shares_outstanding": 16.5e9}
    story = pdf_report._section_2f("Z74.SI", dcf, {}, {}, pdf_report._build_styles(), 500.0)
    rows = {r[0]: r[1:] for r in _table_text(story) if r}
    assert rows["Revenue Growth (applied)"][1] == "4.5%"
    assert "Revenue Growth — Year 1" not in rows
