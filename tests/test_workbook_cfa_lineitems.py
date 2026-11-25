"""Workbook builder fixes from the HK/SG workbook audit (2026-11-25, D05 / Z74 / C6L / 01299 / 00941 / C38U).

1. scripts/workbook_tieout.py loads .env.local for --statements (never under pytest, never overriding the environment).
2. WACC: the firm CAPM is rebuilt from labelled inputs -- no "Engine rounding" plug; the legacy CAPM is a cross-check.
3. REIT: S-REIT DDM, NAV (Cap Rates) and P/AFFO are rebuilt on a REIT tab, not pasted as engine values.
4. Minority interest and a cited embedded value per share are Assumptions inputs with source and as-of.
5. Checks: the reported statements' tie-outs count in the master flag (Statement / Statement mapping); the Model's
   reconciliation rows are integrity, not tie-out, and none is counted twice.
6. Summary: DPS from the engine's statement rows; per-share currency stated and converted; bridge currency named.
7. Comps: a leg recording the median at 6 d.p. links to the basket; a cohort's members are rendered from the trace.
"""
import copy
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.agents.analysis import dcf_agent as d
from src.data import report_families as rf
from src.utils.valuation_workbook import build_workbook

_HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_wbt = _load("_wbt_cfa", _HERE / "test_valuation_workbook.py")
_tie = _load("_tieout_cfa", _HERE.parent / "scripts" / "workbook_tieout.py")
SC = ("bear", "base", "bull")


def _calc(run, ticker="TEST", **kw):
    """(workbook, {(SHEET, CELL): value}, tie-out rows, other Checks rows) after a full recalculation."""
    formulas = pytest.importorskip("formulas")
    blob = build_workbook(run, ticker, **kw)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "cfa.xlsx")
        Path(p).write_bytes(blob)
        sol = formulas.ExcelModel().loads(p).finish().calculate()
        other: list = []
        tie = _tie._checks(p, other)
    out = {}
    for k, v in sol.items():
        k = k.upper()
        if "]" not in k or "!" not in k:
            continue
        sheet, cell = k.split("]", 1)[1].rsplit("!", 1)
        v = getattr(v, "value", v)
        try:
            v = v[0][0]
        except Exception:  # noqa: BLE001
            pass
        out[(sheet.strip("'"), cell)] = v
    return load_workbook(io.BytesIO(blob)), out, tie, other


def _failing(tie):
    return [(s, c, v) for s, c, v in tie
            if s != "Checks" and not (v in ("OK", "ALL OK") or (isinstance(v, (int, float)) and abs(v) <= 0.01))]


def _find(ws, prefix, col=1):
    return next(c.row for c in ws[ws.cell(row=1, column=col).column_letter] if str(c.value or "").startswith(prefix))


def _set_blend(run, legs_by_s, weights):
    """Replace every scenario's legs and weights; restate the engine IVs and targets so the Blend / Target tie."""
    dr = run["data"]["dcf_range"]["TEST"]
    for s in SC:
        legs = legs_by_s[s]
        sc = dr[s]
        sc["leg_inputs"] = legs
        sc["method_iv_table"] = {k: round(v["value"], 2) for k, v in legs.items()}
        sc["effective_weights"] = [{"method": k, "value_key": k, "bucket": "multi", "weight": w} for k, w in weights.items()]
        sc["intrinsic_value"] = round(sum(w * legs[k]["value"] for k, w in weights.items()), 2)
    pb = dr["pt_bridge"]
    t = dr["12m_targets"] = {s: round(pb["spot"] + pb["capture"] * (dr[s]["intrinsic_value"] - pb["spot"]), 2) for s in SC}
    run["data"]["scenario_analysis"]["TEST"]["reconciliation"]["12m_price_target"] = round(
        0.25 * t["bear"] + 0.5 * t["base"] + 0.25 * t["bull"], 2)
    return run


# ── 1. .env.local for --statements ──────────────────────────────────────────

def test_tieout_env_loader_is_a_no_op_under_pytest(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text("WBT_CFA_FROM_FILE=1\n", encoding="utf-8")
    monkeypatch.setattr(_tie, "ROOT", tmp_path)
    monkeypatch.delenv("WBT_CFA_FROM_FILE", raising=False)
    assert _tie._load_env() is False and "WBT_CFA_FROM_FILE" not in os.environ


def test_tieout_env_loader_reads_env_local_without_overriding(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    (tmp_path / ".env.local").write_text("WBT_CFA_FROM_FILE=file\nWBT_CFA_KEEP=file\n", encoding="utf-8")
    monkeypatch.setattr(_tie, "ROOT", tmp_path)
    monkeypatch.setenv("WBT_CFA_KEEP", "shell")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delitem(sys.modules, "pytest")
    try:
        assert _tie._load_env() is True
        assert os.environ["WBT_CFA_FROM_FILE"] == "file" and os.environ["WBT_CFA_KEEP"] == "shell"
    finally:
        os.environ.pop("WBT_CFA_FROM_FILE", None)


# ── 2. Firm-CAPM WACC ───────────────────────────────────────────────────────

def _firm_capm(own_raw=1.2):
    rf_, erp, bu, t, de, kd = 0.04, 0.05, 0.9, 0.21, 0.25, 0.05
    bl = bu * (1 + (1 - t) * de)
    adj = 0.67 * own_raw + 0.33 if 0.4 <= own_raw <= 2.5 else None
    beta = 0.5 * (bl + adj) if adj is not None else bl
    ke = rf_ + beta * erp
    dv = de / (1 + de)
    w = max((1 - dv) * ke + dv * kd * (1 - t), rf_ + 0.01)
    return {"wacc": round(w, 5), "industry": "Software", "beta_unlevered": bu, "beta_levered": round(beta, 4),
            "beta_industry_relevered": round(bl, 4), "beta_own_raw": own_raw,
            "beta_own_adjusted": round(adj, 4) if adj is not None else None, "de_ratio": de, "dv_ratio": round(dv, 4),
            "rf": rf_, "rf_source": "FRED DGS10 (latest)", "erp": erp, "crp": 0.0, "cost_of_equity": round(ke, 5),
            "cost_of_debt": kd, "tax_rate": t, "contracted_discount": 0.0, "floored": False,
            "source": "Damodaran, Betas by Sector (US), data as of January 2026; implied ERP and T-bond rate 2026-11-01",
            "table_rate_replaced": 0.09}


@pytest.mark.parametrize("own_raw", [1.2, 0.25])
def test_the_firm_capm_wacc_is_rebuilt_from_inputs_with_no_plug(own_raw):
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    fc = _firm_capm(own_raw)
    dr["wacc_build"].update(firm_capm=fc, wacc_final=fc["wacc"], insider_bps=5.0, rd_live=0.05, source="fred")
    wb, v, tie, _ = _calc(run)
    ws = wb["WACC"]
    labels = [str(c.value) for c in ws["A"] if c.value]
    assert not any(l.startswith("Engine rounding") or l.startswith("Residual NOT") for l in labels)
    used = _find(ws, "WACC used")
    assert ws.cell(row=used, column=3).value.startswith("=") and v[("WACC", f"C{used}")] == pytest.approx(fc["wacc"], abs=1e-12)
    assert [x for x in _failing(tie) if x[0] == "WACC"] == []
    # every input the build reads is an Assumptions cell with a source
    a = wb["Assumptions"]
    for prefix in ("Risk-free rate (government 10y", "Equity risk premium (Damodaran implied)", "Industry unlevered beta",
                   "D/E for relevering", "Own beta, raw", "Pre-tax cost of debt"):
        rr = _find(a, prefix)
        assert a.cell(row=rr, column=6).value, prefix
    # the legacy CAPM is labelled a cross-check, not the WACC used
    assert a.cell(row=_find(a, "Legacy CAPM cross-check only -- NOT the WACC used"), column=3).value is not None
    # the table memo ends at the rate the firm CAPM replaced: the insider overlay is not applied under the firm CAPM
    memo = _find(ws, "Table WACC the firm CAPM replaced")
    assert v[("WACC", f"C{memo}")] == pytest.approx(0.09)
    assert "not applied" in ws.cell(row=_find(ws, "Insider-activity overlay"), column=1).value
    if own_raw == 0.25:
        assert v[("WACC", f"C{_find(ws, 'Blume-adjusted own beta')}")] == "n/a"


# ── 3. REIT tab ─────────────────────────────────────────────────────────────

def _reit_run(run_at="2026-11-26T00:00:00+00:00"):
    run = _wbt._run()
    run["run_at"] = run_at
    dr = run["data"]["dcf_range"]["TEST"]
    dr["profile"] = "S-REIT"
    cfg = d._SREIT_DDM_CALIBRATION[d._sgx_reit_subtype("TEST")]
    pre_fix = run_at < "2026-11-25T06:18:34+00:00"
    band = {"bear": 0.75, "base": 1.0, "bull": 1.25}
    gdpu = {"bear": 0.03, "base": 0.05, "bull": 0.20}               # the bull rate is clamped at 15%
    dpu, units, affo = 0.09, 10.0, 1.0
    dr["reit_breakdown"] = {"subtype": "retail", "ffo": 1.2, "normalized_maintenance_capex": 0.2, "affo": affo,
                            "dps": dpu, "p_affo_peer": 15.0, "shares": units}
    nav = {"kind": "nav", "cap_rate": 0.05, "cap_rate_source": "research cap_rate_market", "subtype": "retail",
           "noi": 0.5, "noi_basis": "EBITDA", "total_debt": 3.0, "cash": 0.5, "value": (0.5 / 0.05 - 3.0 + 0.5) / units}
    legs_by_s = {}
    for s in SC:
        dcf = copy.deepcopy(dr[s]["leg_inputs"]["DCF"])
        dcf["growth_base"] = gdpu[s]
        g = max(min(gdpu[s], 0.15), -0.15)
        cap = dpu * (1 + g) * ((1 + cfg["g"]) if pre_fix else 1.0)
        dr[s]["growth_premium"] = 1.0
        legs_by_s[s] = {"DCF": dcf, "DDM (S-REIT)": {"value": round(cap / (cfg["coe"] - cfg["g"]), 4) * band[s]},
                        "NAV (Cap Rates)": nav, "P/AFFO": {"value": affo / units * 15.0 * band[s]}}
    run = _set_blend(run, legs_by_s, {"DDM (S-REIT)": 0.55, "NAV (Cap Rates)": 0.3, "P/AFFO": 0.15})
    return run


@pytest.mark.parametrize("run_at,years", [("2026-11-26T00:00:00+00:00", 1), ("2026-11-25T03:57:44+00:00", 2)])
def test_the_reit_legs_are_rebuilt_on_a_reit_tab(run_at, years):
    wb, v, tie, _ = _calc(_reit_run(run_at))
    assert "REIT" in wb.sheetnames
    ws = wb["REIT"]
    assert ws.cell(row=_find(ws, "Years of growth in the capitalised distribution"), column=3).value == years
    reit = [x for x in tie if x[0] == "REIT"]
    assert len(reit) >= 9 and _failing(tie) == []
    blend = wb["Blend"]
    linked = [c.value for row in blend.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("='REIT'!")]
    assert len(linked) == 9                                     # three legs x three scenarios
    multiples = " ".join(str(c.value) for row in wb["Multiples"].iter_rows() for c in row if c.value)
    assert "no metric × multiple form" not in multiples and "rebuilt on the REIT tab" in multiples
    assert v[("CHECKS", "C4")] == "ALL OK"


# ── 4. Minority interest and embedded value per share ───────────────────────

def _mi_ev_run(monkeypatch=None, cited=None):
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    mi = 5.0                                                    # the fixture's money is in units (millions = 1e-6)
    legs = copy.deepcopy(dr["base"]["leg_inputs"])
    legs["DCF"]["minority_interest"] = mi
    legs["DCF"]["value"] = legs["DCF"]["value"] - mi / legs["DCF"]["shares"]
    ev = legs["EV/EBITDA"]
    ev.update(minority_interest=mi, equity=ev["ev"] - ev["net_debt"] - mi, value=(ev["ev"] - ev["net_debt"] - mi) / ev["shares"])
    legs["Embedded Value"] = {"kind": "equity_multiple", "metric": "Embedded value per share (owner-accepted input)",
                              "metric_value": 20.0, "per_share_metric": 20.0, "multiple": 0.8,
                              "multiple_parts": {"peer_multiple": 0.8, "scenario_band": 1.0}, "value": 16.0}
    if cited is not None and monkeypatch is not None:
        from src.data import industry_inputs as ii
        monkeypatch.setattr(ii, "entry", lambda t, k, doc=None: {"data": {"ev_per_share": cited}} if k == "embedded_value" else None)
    return _set_blend(run, {s: legs for s in SC}, {"DCF": 0.4, "EV/EBITDA": 0.4, "Embedded Value": 0.2})


def test_minority_interest_links_to_a_sourced_assumptions_input():
    wb, v, tie, _ = _calc(_mi_ev_run())
    a = wb["Assumptions"]
    rr = _find(a, "Minority interest deducted from EV")
    assert a.cell(row=rr, column=3).value == pytest.approx(5.0e-6) and a.cell(row=rr, column=6).value and a.cell(row=rr, column=7).value is not None
    ref = f"'Assumptions'!$C${rr}"
    dcf = wb["DCF"]
    assert all(dcf.cell(row=r_, column=2).value == f"=-{ref}" for r_ in
               [c.row for c in dcf["A"] if c.value == "Less: minority interest"])
    m = wb["Multiples"]
    ls = [m.cell(row=r_, column=12).value for r_ in range(5, m.max_row + 1) if m.cell(row=r_, column=2).value == "EV/EBITDA"]
    assert ls and all(ref in str(x) for x in ls)
    assert _failing(tie) == []


def test_embedded_value_per_share_is_an_input_cited_or_traced(monkeypatch):
    wb, v, tie, _ = _calc(_mi_ev_run())
    a, m = wb["Assumptions"], wb["Multiples"]
    rr = _find(a, "Embedded value per share in USD")
    assert "Engine leg trace" in a.cell(row=rr, column=6).value
    ds = [m.cell(row=r_, column=4).value for r_ in range(5, m.max_row + 1) if m.cell(row=r_, column=2).value == "Embedded Value"]
    assert len(ds) == 3 and all(x == f"='Assumptions'!$C${rr}" for x in ds)
    # with the owner-accepted citation on file at the traced value, the cited figure is the input
    wb, v, tie, _ = _calc(_mi_ev_run(monkeypatch, {"value": 20.0, "currency": "USD", "period": "FY2025",
                                                    "quote": "EV per share 20.00", "source_url": "https://example.com/ar"}))
    a = wb["Assumptions"]
    rc = _find(a, "Embedded value per share as cited (USD")
    assert a.cell(row=rc, column=7).value == "FY2025" and "example.com" in a.cell(row=rc, column=6).value
    assert a.cell(row=_find(a, "Embedded value per share in USD"), column=3).value == f"='Assumptions'!$C${rc}"
    assert _failing(tie) == []


# ── 5. Checks: statements in the master flag; Model rows are integrity ──────

def _bad_statements(ticker, end_date):
    st = _wbt._statements(ticker, end_date)
    st["rows"][0]["pretax_income"] += 50e6                     # FMP's lines no longer tie to its pre-tax income
    return st


@pytest.mark.parametrize("profile,kind", [("Test Profile", "Statement"), ("Insurance", "Statement mapping")])
def test_reported_statement_tieouts_count_in_the_master_flag(profile, kind):
    run = _wbt._run()
    run["data"]["dcf_range"]["TEST"]["profile"] = profile
    wb, v, tie, other = _calc(run, load_statements=_bad_statements)
    ws = wb["Checks"]
    rows = [(ws.cell(row=r_, column=1).value, ws.cell(row=r_, column=2).value) for r_ in range(9, ws.max_row + 1)]
    assert (kind, "IS") in rows
    assert v[("CHECKS", "C7")] == 1 and v[("CHECKS", "C4")] == "FAIL"
    assert [o for o in other if o[0] == kind and o[2] == "FAIL"]
    # the script's engine tie-out count still agrees with the Checks tab's
    assert v[("CHECKS", "C5")] == len(_failing([x for x in tie if x[0] != "Checks"]))
    # no Model / statement cell is labelled a tie-out
    assert not [r for r in rows if r[0] == "Tie-out" and r[1] in ("Model", "IS", "BS", "CFS")]


def test_no_model_cell_is_counted_twice_on_the_checks_tab():
    run = _wbt._run()
    wb, v, tie, _ = _calc(run, load_statements=_wbt._statements)
    ws = wb["Checks"]
    refs = [ws.cell(row=r_, column=5).value for r_ in range(9, ws.max_row + 1)
            if ws.cell(row=r_, column=1).value in ("Tie-out", "Integrity", "Statement", "Statement mapping")]
    assert len(refs) == len(set(refs))


# ── 6. Summary: DPS, currencies, net-debt bridge ────────────────────────────

def test_family_rows_read_dps_from_the_engine_rows_when_the_raw_records_lack_it():
    raw = {"FY2024": {"revenue": 10e9, "net_income": 1e9, "total_equity": 8e9, "shares_outstanding": 1e9},
           "FY2025": {"revenue": 11e9, "net_income": 1.1e9, "total_equity": 9e9, "shares_outstanding": 1e9}}
    fb = [{"period": "2024-12-31", "dividends_per_share": 7.8}, {"period": "2025-12-31", "dividends_per_share": 15.6}]
    _, rows = rf.family_rows(raw, "Banks", fallback_rows=fb, fallback_scale=1 / 7.8)
    assert dict((l, vals) for l, _, vals in rows)["Dividends / share"] == [pytest.approx(1.0), pytest.approx(2.0)]
    _, rows = rf.family_rows(raw, "Banks")
    assert dict((l, vals) for l, _, vals in rows)["Dividends / share"] == [None, None]


def test_summary_dps_currency_and_bridge_label():
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    dr["profile"] = "Money Center Bank"
    dr["reported_currency"] = "HKD"
    nd = dr["base"]["leg_inputs"]["DCF"]["net_debt"]
    dr["financials_used"] = {"currency": "HKD", "source_currency": "USD", "fx_rate": 7.8,
                             "rows": [{"period": "2025-12-31", "dividends_per_share": 15.6}],
                             "net_debt_basis": {"balance_sheet_date": "2025-12-31", "components": {
                                 "feed_net_debt": nd, "short_term_investments": 0.0, "result": nd}}}
    run["data"]["raw_financials"] = {"FY2025": {"revenue": 10e9, "net_income": 1e9, "total_equity": 8e9,
                                                "shares_outstanding": 1e9, "book_value_per_share": 8.0},
                                     "notes": {"currency": "HKD"}}
    wb, v, tie, _ = _calc(run)
    ws = wb["Summary"]
    hdr = _find(ws, "Metric")
    assert [c.value for c in ws[hdr] if c.value] == ["Metric", "FY2025", "Unit"]          # no "notes" year column
    dps = _find(ws, "Dividends / share")
    assert ws.cell(row=dps, column=2).value == pytest.approx(2.0)                         # 15.6 HKD / 7.8 -> USD as filed
    assert ws.cell(row=dps, column=3).value == "USD per share, as filed"
    conv = dps + 1
    assert ws.cell(row=conv, column=1).value == "Dividends / share (HKD)" and v[("SUMMARY", f"B{conv}")] == pytest.approx(15.6)
    bridge = _find(ws, "Bridge, ")
    assert ws.cell(row=bridge, column=1).value.startswith("Bridge, HKD millions (components already converted from USD")
    assert [x for x in _failing(tie) if x[0] != "Target"] == []     # the fixture's published target (1.0) does not tie


# ── 7. Comps: 6-d.p. legs and cohort members ────────────────────────────────

def test_comps_links_a_six_decimal_leg_and_renders_a_cohort_basket():
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    dr["multiples_used"] = {"comp_market": "XMKT", "fields": {"pb": {
        "value": 1.5926, "basis": "profile", "cohort": "all", "key": "Airlines", "exchange": "XMKT", "peer_count": 3,
        "members_used": [{"symbol": "AAA", "value": 1.2}, {"symbol": "BBB", "value": 1.592597}, {"symbol": "CCC", "value": 2.0}]}}}
    legs = copy.deepcopy(dr["base"]["leg_inputs"])
    legs["P/BV"] = {"kind": "equity_multiple", "metric": "Book value per share", "per_share_metric": 5.0,
                    "multiple": 1.592597, "multiple_parts": {"peer_multiple": 1.592597, "peer_source": "peer median pb",
                                                             "scenario_band": 1.0, "growth_premium": 1.0},
                    "value": 5.0 * 1.592597}
    mem = [{"symbol": "X.HK", "price": 97.0, "ev_per_share": 122.0361, "currency": "HKD", "p_ev": 0.7948},
           {"symbol": "Y.HK", "price": 33.34, "ev_per_share": 42.5598, "currency": "HKD", "p_ev": 0.7834},
           {"symbol": "Z.SI", "price": 18.01, "ev_per_share": 21.23, "currency": "SGD", "p_ev": 0.8483}]
    legs["Embedded Value"] = {"kind": "equity_multiple", "metric": "Embedded value per share (owner-accepted input)",
                              "per_share_metric": 50.0, "multiple": 0.7948,
                              "multiple_parts": {"peer_multiple": 0.7948, "scenario_band": 1.0, "members_used": mem,
                                                 "members_missing": ["MFC"],
                                                 "peer_source": "median P/EV, pan_asian life cohort (3 peers, subject excluded)"},
                              "value": 50.0 * 0.7948}
    run = _set_blend(run, {s: legs for s in SC}, {"DCF": 0.4, "P/BV": 0.3, "Embedded Value": 0.3})
    wb, v, tie, _ = _calc(run)
    c = wb["Comps"]
    six = _find(c, "Median at the legs' recorded precision (6 d.p.)", col=5)
    m = wb["Multiples"]
    pbv = [m.cell(row=r_, column=5).value for r_ in range(5, m.max_row + 1) if m.cell(row=r_, column=2).value == "P/BV"]
    assert pbv and all(x == f"='Comps'!$F${six}" for x in pbv)
    tickers = [x.value for x in c["A"]]
    assert {"X.HK", "Y.HK", "Z.SI"} <= set(tickers)
    s = wb["Summary"]
    assert not any("members not recorded" in str(x.value) for row in s.iter_rows() for x in row)
    assert _failing(tie) == []


def test_a_rebased_cost_of_debt_is_rebuilt_from_the_live_rate_and_both_curves():
    """capm.firm_wacc moves a US-yield Kd onto the local government curve (live − US 10y + local rf, floored at rf);
    when the engine records the US 10y the workbook rebuilds the rebased Kd instead of typing it."""
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    fc = _firm_capm(1.2)
    live, us = 0.0515, 0.0492
    kd = max(live - us + fc["rf"], fc["rf"])
    dv, ke, t = 0.25 / 1.25, fc["cost_of_equity"], fc["tax_rate"]
    bl = 0.9 * (1 + 0.79 * 0.25)
    ke = 0.04 + 0.5 * (bl + 0.67 * 1.2 + 0.33) * 0.05
    fc.update(cost_of_debt=round(kd, 5), cost_of_debt_rebased=True, us_rf=us,
              wacc=round(max((1 - dv) * ke + dv * kd * (1 - t), 0.05), 5))
    dr["wacc_build"].update(firm_capm=fc, wacc_final=fc["wacc"], rd_live=live, source="fred")
    wb, v, tie, _ = _calc(run)
    ws = wb["WACC"]
    kd_row = _find(ws, "Pre-tax cost of debt on the local curve")
    assert ws.cell(row=kd_row, column=3).value.startswith("=MAX(")
    assert v[("WACC", f"C{_find(ws, 'WACC used')}")] == pytest.approx(fc["wacc"], abs=1e-12)
    assert [x for x in _failing(tie) if x[0] == "WACC"] == []
