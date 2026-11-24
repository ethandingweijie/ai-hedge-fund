"""Workbook financial-model audit (owner, 2026-11-25; CFA / FAST standard): the Checks tab and master flag, peer
multiples linked to Comps, the shadow DCF, reference vs input vs engine-output styling, one money unit, model
invariants and the Model-vs-DCF FCF reconciliation, sources and dates, sensitivities, navigation -- and the
value-driver terminal value (TV x (1 - g/RONIC), NOPAT/UFCF ratio) when the engine records it."""
import copy
import importlib.util
import inspect
import io
import os
import re
import tempfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.agents.analysis import dcf_agent as d
from src.utils.valuation_workbook import build_workbook

_HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_wbt = _load("_wbt_1125", _HERE / "test_valuation_workbook.py")
_tie = _load("_tieout_1125", _HERE.parent / "scripts" / "workbook_tieout.py")


def _wb(run, ticker="TEST", **kw):
    return load_workbook(io.BytesIO(build_workbook(run, ticker, **kw)))


def _calc(run, ticker="TEST", **kw):
    """(workbook, {(sheet, cell): value}) after a full recalculation."""
    formulas = pytest.importorskip("formulas")
    blob = build_workbook(run, ticker, **kw)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "audit.xlsx")
        Path(p).write_bytes(blob)
        sol = {k.upper(): v for k, v in formulas.ExcelModel().loads(p).finish().calculate().items()}
        checks = _tie._checks(p)
    out = {}
    for k, v in sol.items():
        m = re.match(r"'\[AUDIT\.XLSX\](.+)'!([A-Z]+\d+)$", k)
        if m:
            v = getattr(v, "value", v)
            try:
                v = v[0][0]
            except Exception:  # noqa: BLE001
                pass
            out[(m.group(1), m.group(2))] = v
    return load_workbook(io.BytesIO(blob)), out, checks


def _col_a(ws):
    return [str(c.value) for c in ws["A"] if c.value is not None]


def _row(ws, prefix, col=1):
    return next(c.row for c in ws[ws.cell(row=1, column=col).column_letter] if str(c.value or "").startswith(prefix))


# ── 1. Checks tab ────────────────────────────────────────────────────────────

def test_the_checks_tab_lists_every_check_and_its_master_flag_agrees_with_the_tieout():
    run = _wbt._run()
    wb, v, tie = _calc(run, load_statements=_wbt._statements)
    ws = wb["Checks"]
    assert wb.sheetnames.index("Checks") == wb.sheetnames.index("Summary") + 1
    kinds = [ws.cell(row=r, column=1).value for r in range(9, ws.max_row + 1)]
    n_tie = sum(1 for k in kinds if k == "Tie-out")
    tie_rows = [x for x in tie if x[0] != "Checks"]
    assert n_tie == len(tie_rows) and n_tie >= 10           # every tie-out cell the script reads has its own row
    tabs = {ws.cell(row=r, column=2).value for r in range(9, ws.max_row + 1) if ws.cell(row=r, column=1).value == "Tie-out"}
    assert {"DCF", "Blend", "Target", "Multiples", "WACC"} <= tabs
    bad = [x for x in tie_rows if not ((isinstance(x[2], str) and x[2] in ("OK", "ALL OK"))
                                       or (isinstance(x[2], (int, float)) and abs(x[2]) <= 0.01))]
    assert v[("CHECKS", "C5")] == len(bad)                  # the fixture's published target (1.0) does not tie
    assert v[("CHECKS", "C4")] == ("ALL OK" if not bad and not v[("CHECKS", "C6")] else "FAIL")
    # shown on the front pages
    assert any(c.value == "='Checks'!$C$4" for row in wb["Summary"].iter_rows() for c in row)
    assert any(str(c.value) == "='Checks'!$C$4" for row in wb["Cover"].iter_rows() for c in row)


def test_the_summary_net_debt_check_links_the_net_debt_used_and_an_empty_sotp_sums_no_cell_of_its_own():
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    nd = dr["base"]["leg_inputs"]["DCF"]["net_debt"]
    dr["financials_used"] = {"fx_rate": 1.0, "source_currency": "USD", "net_debt_basis": {
        "balance_sheet_date": "2026-06-30", "components": {"feed_net_debt": nd + 50.0, "short_term_investments": 30.0,
                                                            "long_term_investments": 20.0, "leases_removed": 0.0,
                                                            "debt_like": 0.0, "result": nd}}}
    wb = _wb(run)
    s = wb["Summary"]
    r = _row(s, "Check: bridge")
    f = s.cell(row=r, column=2).value
    assert "'Assumptions'!" in f and not re.search(r"-\d", f.split("*")[-1].replace("'Assumptions'!", ""))
    # SOTP with no segment rows
    run2 = _wbt._run()
    sotp = {"kind": "sotp", "source": "segments", "value": 90.0, "table": {"rows": [], "segment_value": 1000.0,
                                                                           "associates": 0.0, "net_cash": -100.0,
                                                                           "nav": 900.0, "shares": 10.0, "holdco_discount": 0.0,
                                                                           "holdco_discount_pct": 0.0}}
    for s_ in ("bear", "base", "bull"):
        sc = run2["data"]["dcf_range"]["TEST"][s_]
        sc["leg_inputs"] = {**sc["leg_inputs"], "SOTP": sotp}
        sc["effective_weights"] = sc["effective_weights"] + [{"method": "SOTP", "value_key": "SOTP", "bucket": "sotp", "weight": 0.0001}]
    ws = _wb(run2)["SOTP"]
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("=SUM("):
                a, b = re.findall(r"[A-Z]+(\d+)", c.value)[:2]
                assert int(a) <= int(b) and not (int(a) <= c.row <= int(b)), (c.coordinate, c.value)


# ── 2. Multiples linked to Comps; through-cycle baskets ─────────────────────

def _norm_run():
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    mem = [{"symbol": s_, "name": s_, "market_cap": 1e10, "value": v} for s_, v in (("A", 11.0), ("B", 12.0), ("C", 13.0))]
    dr["multiples_used"] = {"comp_market": "US", "fields": {
        "ev_ebitda": {"value": 12.0, "basis": "industry", "cohort": "all", "key": "Tech", "exchange": "US", "members_used": mem},
        "pe": {"value": 25.0, "basis": "industry", "cohort": "all", "key": "Tech", "exchange": "US"}}}
    for s_ in ("bear", "base", "bull"):
        legs = copy.deepcopy(dr[s_]["leg_inputs"])
        legs["EV/EBITDA"]["multiple_parts"]["peer_source"] = "peer median ev_ebitda"
        legs["P/E"]["metric"] = "Net income (5y normalised: mid-cycle EBIT margin 20% x trend revenue)"
        legs["P/E"]["multiple_parts"].update(peer_source="dynamic through-cycle pe_norm (US industry: Tech; auto 2026-09-21)")
        dr[s_]["leg_inputs"] = legs
    dr["cycle"] = {"cyclical": True, "position": "peak", "mid_cycle_margin": 0.2, "latest_margin": 0.4}
    return run


def test_the_peer_multiple_links_to_comps_and_a_through_cycle_basket_says_its_members_are_unavailable():
    wb, v, tie = _calc(_norm_run())
    m = wb["Multiples"]
    rows = {m.cell(row=r, column=2).value: r for r in range(5, m.max_row + 1) if m.cell(row=r, column=1).value == "base"}
    assert str(m.cell(row=rows["EV/EBITDA"], column=5).value).startswith("='Comps'!")
    assert str(m.cell(row=rows["P/E"], column=5).value).startswith("='Comps'!")
    assert m.cell(row=rows["P/E"], column=3).value.startswith("EPS: Net income")          # the label says what the cell holds
    c = wb["Comps"]
    text = " ".join(_col_a(c))
    assert "Through-cycle pe_norm -- P/E" in text and "Members unavailable" in text
    assert all(abs(x[2]) <= 0.01 for x in tie if x[0] == "Multiples")                    # still ties through the link
    # money in millions with the units in the header; net debt and shares link to Assumptions
    hdr = [m.cell(row=4, column=k).value for k in range(1, 19)]
    assert "Net debt (USD m)" in hdr and "Diluted shares (m)" in hdr
    assert str(m.cell(row=rows["EV/EBITDA"], column=11).value).startswith("='Assumptions'!")
    assert str(m.cell(row=rows["EV/EBITDA"], column=14).value).startswith("='Assumptions'!")


# ── 3. Shadow DCF (see also test_workbook_review_fixes) ─────────────────────

def test_a_shadow_dcf_ties_and_stays_out_of_the_blend():
    run = _wbt._run_with({}, [{"method": "EV/EBITDA", "value_key": "EV/EBITDA", "bucket": "multi", "weight": 0.6},
                              {"method": "P/E", "value_key": "P/E", "bucket": "multi", "weight": 0.4}])
    wb, v, tie = _calc(run)
    dcf_checks = [x for x in tie if x[0] == "DCF"]
    assert dcf_checks and all(abs(x[2]) < 1e-6 for x in dcf_checks)
    assert "SHADOW" in str(wb["DCF"]["A4"].value)
    assert not any("'DCF'!" in str(c.value) for row in wb["Blend"].iter_rows() for c in row if c.value)


# ── 4. Inputs, engine outputs and reference values ──────────────────────────

def test_dead_inputs_are_grey_reference_values_and_the_margin_path_is_an_engine_output():
    wb = _wb(_wbt._run())
    a = wb["Assumptions"]
    cap = _row(a, "FCF margin cap")
    assert a.cell(row=cap, column=3).font.color.rgb == "FF808080"
    assert "Reference value" in str(a.cell(row=cap, column=8).value)
    base = _row(a, "FCF margin base")
    assert a.cell(row=base, column=3).font.color.rgb == "FF808080"
    path = _row(a, "FCF margin by projection year (ENGINE OUTPUT")
    assert a.cell(row=path + 2, column=4).font.color.rgb == "FF7030A0"
    assert not any("Statement unit divisor" in v for v in _col_a(a))
    # sources and dates
    assert a.cell(row=4, column=6).value == "Source" and a.cell(row=4, column=7).value == "As of"
    assert a.cell(row=_row(a, "Share price at valuation"), column=7).value == "2026-09-19"


# ── 5. Units ────────────────────────────────────────────────────────────────

def test_money_is_in_millions_on_the_dcf_and_assumptions():
    run = _wbt._run()
    run["data"]["dcf_range"]["TEST"]["base"]["leg_inputs"]["DCF"]["revenue_base"] = 2.5e9
    wb = _wb(run)
    a = wb["Assumptions"]
    r = _row(a, "Revenue base (last actual, USD millions)")
    assert a.cell(row=r, column=4).value == pytest.approx(2500.0)
    assert "USD millions" in str(wb["DCF"]["A4"].value)


# ── 6. Model honesty ────────────────────────────────────────────────────────

def test_model_invariants_and_the_fcf_reconciliation_feed_the_checks_tab_and_a_failed_build_is_on_the_summary():
    ts = _load("_ts_1125", _HERE / "test_three_statement.py")
    p = ts._payload_with_statements()
    wb = _wb(p, "MOH", load_statements=_wbt._statements)
    m = wb["Model"]
    labels = _col_a(m)
    assert "Invariant: gross margin ≥ EBIT margin" in labels
    assert any(x.startswith("Invariant: gross margin within the Assumptions band") for x in labels)
    ck = wb["Checks"]
    descs = [str(ck.cell(row=r, column=4).value) for r in range(9, ck.max_row + 1)]
    assert any("gross margin ≥ EBIT margin" in x for x in descs) and any("reconciliation suite" in x for x in descs)
    if "DCF" in wb.sheetnames:
        assert any(x.startswith("6. Reconciliation: Model FCF vs DCF FCF") for x in labels)
    gaps = [str(c.value) for c in wb["Data Gaps"]["B"] if c.value]
    assert not any("No balance-sheet or cash-flow forecast" in x for x in gaps)
    assert any(x.startswith("Three-statement forecast built") for x in gaps)
    bad = copy.deepcopy(p)
    bad["data"]["dcf_range"]["MOH"]["three_statements"] = {"skipped": "RECONCILIATION FAILED: FY2026E #1", "reconciliation": {
        "ok": False, "failures": [{"id": 1, "name": "Total assets = total liabilities + equity", "year": "FY2026E",
                                   "lhs": 1e9, "rhs": 0.9e9, "diff": 1e8, "ok": False}]}, "fy_labels": None}
    wb2 = _wb(bad, "MOH", load_statements=_wbt._statements)
    assert any(x.startswith("THREE-STATEMENT MODEL FAILED") for x in _col_a(wb2["Summary"]))
    ck2 = wb2["Checks"]
    assert any(ck2.cell(row=r, column=1).value == "Integrity" and ck2.cell(row=r, column=2).value == "Model"
               for r in range(9, ck2.max_row + 1))


# ── 7. Sources, beta, rating, WACC used ─────────────────────────────────────

def test_beta_from_the_engine_capm_check_the_agency_rating_priced_and_the_wacc_used():
    run = _wbt._run()
    wbd = run["data"]["dcf_range"]["TEST"]["wacc_build"]
    wbd.update(rating="AAA", agency_rating_note="Cost of debt on the agency rating BBB+ (verified: S&P BBB+) -- not the "
                                                "rating implied by interest cover",
               capm={"beta": 1.3, "rf": 0.0395, "erp": 0.0446, "cost_of_equity": 0.0975, "wacc": 0.0975, "status": "used"}, wacc_final=0.0951)
    run["data"]["dcf_range"]["TEST"]["base"]["leg_inputs"]["DCF"]["wacc"] = 0.0951
    wb, v, _ = _calc(run)                                     # no statements loader: beta comes from the CAPM record
    w = wb["WACC"]
    r = _row(w, "Cost of equity = rf + beta")
    assert isinstance(v[("WACC", f"C{r}")], float)
    rp = _row(w, "Rating priced (agency)")
    assert rp and w.cell(row=rp, column=3).value == "BBB+"
    assert any(str(c.value).strip().startswith("Synthetic rating (from interest cover; NOT priced") for c in w["A"])
    bt = next(x for x in _col_a(wb["Backtest"]) if x.startswith("WACC used by the valuation"))
    assert bt.startswith("WACC used by the valuation 9.51%")
    assert "Status" in _col_a(wb["Backtest"])                 # readable keys, not snake_case


# ── 8. Sensitivities ────────────────────────────────────────────────────────

def test_target_sensitivities_centre_on_the_base_target_and_cyclicals_get_a_margin_grid():
    run = _norm_run()
    wb, v, _ = _calc(run)
    t = wb["Target"]
    labels = _col_a(t)
    r = _row(t, "Sensitivity: base target vs the EV/EBITDA leg's peer multiple")
    centre = v[("TARGET", f"D{r + 5}")]                       # row 0, column 0 of a 5 x 5 grid
    assert centre == pytest.approx(v[("TARGET", "C6")], abs=0.01)
    assert any(x.startswith("Sensitivity: probability-weighted target vs capture") for x in labels)
    rc = _row(t, "Cyclical: margin ±20%")
    hdr = [t.cell(row=rc + 1, column=k).value for k in range(1, 10)]
    assert "Base target" in hdr
    mid = rc + 4                                              # factor 1.00
    col = hdr.index("Base target") + 1
    assert v[("TARGET", f"{t.cell(row=1, column=col).column_letter}{mid}")] == pytest.approx(v[("TARGET", "C6")], abs=0.01)


# ── 9. Navigation ───────────────────────────────────────────────────────────

def test_navigation_contents_freeze_panes_and_methodology_notes():
    wb = _wb(_wbt._run(), load_statements=_wbt._statements)
    names = [c.value for c in wb["Cover"]["A"] if c.hyperlink]
    assert {"Summary", "Checks", "Assumptions", "DCF", "Multiples", "Comps"} <= set(names)
    assert wb["Assumptions"].freeze_panes and wb["DCF"].freeze_panes
    for tab in ("Summary", "Assumptions", "DCF", "Multiples", "Comps", "Blend", "Target", "Checks"):
        assert wb[tab]["A3"].value and len(str(wb[tab]["A3"].value)) > 60, tab


# ── Owner 2026-11-25: the value-driver terminal value ───────────────────────

_VD = "terminal_ronic_spread" in inspect.signature(d._project_dcf).parameters


def test_a_run_priced_before_the_value_driver_terminal_keeps_the_gordon_form():
    # a trace with no value-driver record (every run before 2026-11-25) is rebuilt on the Gordon form
    run = _wbt._run()
    for s in ("bear", "base", "bull"):
        tr = run["data"]["dcf_range"]["TEST"][s]["leg_inputs"]["DCF"]
        tr.pop("terminal_nopat_ratio", None)
        tr.pop("terminal_ronic_spread", None)
    wb, v, tie = _calc(run)
    if not _VD:                                               # on the old engine the fixture's value is Gordon: it ties
        assert all(abs(x[2]) < 1e-6 for x in tie if x[0] == "DCF")
    a = wb["Assumptions"]
    r = _row(a, "Value-driver terminal value applies")
    assert a.cell(row=r, column=4).value == 0
    rr = _row(wb["DCF"], "Reinvestment factor 1 − g / RONIC")
    assert v[("DCF", f"B{rr}")] == 1
    dcf = wb["DCF"]
    assert any(str(c.value).startswith("RONIC: return on new invested capital") for c in dcf["A"])
    assert any(str(c.value).startswith("Reinvestment factor 1 − g / RONIC") for c in dcf["A"])


@pytest.mark.skipif(not _VD, reason="engine predates the value-driver terminal value")
def test_the_value_driver_terminal_value_ties_with_a_nopat_ratio_and_a_ronic_spread():
    tm = d._dcf_timing("2025-09-30", "2026-06-30", "2026-10-04")
    sched = [0.18, 0.19, 0.2, 0.2, 0.21, 0.21, 0.21, 0.21, 0.21, 0.21]
    iv, pv_f, pv_t, rows = d._project_dcf(1000.0, 0.2, 0.08, 0.0, 0.09, 0.03, -0.05, 100.0, 10.0, margin_schedule=sched,
                                          timing=tm, terminal_nopat_ratio=1.15, terminal_ronic_spread=0.04)
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    for s in ("bear", "base", "bull"):
        legs = copy.deepcopy(dr[s]["leg_inputs"])
        legs["DCF"].update(value=iv, pv_fcf_per_share=pv_f, pv_tv_per_share=pv_t, projection_rows=rows, growth_schedule=None,
                           timing=tm, terminal_nopat_ratio=1.15, terminal_ronic_spread=0.04)
        dr[s]["leg_inputs"] = legs
    wb, v, tie = _calc(run)
    dcf = [x for x in tie if x[0] == "DCF"]
    assert dcf and all(abs(x[2]) < 1e-6 for x in dcf)
