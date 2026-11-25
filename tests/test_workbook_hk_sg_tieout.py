"""Workbook tie-out fixes from the HK/SG production runs (2026-11-25).

* The holdco look-through SOTP (BN4.SI Keppel, U96.SI Sembcorp) and the segment-EV SOTP (VLO) carry no analyst
  `table`; the SOTP tab rendered an empty table (segment value 0) and its Check read the whole leg value.
  Each is now rebuilt from its own parts with live formulas.
* A DCF-family leg ("DCF (2-stage)", 00941.HK) is priced on the core DCF's value-driver terminal inputs but its trace
  leaves them out; it was rebuilt on the Gordon terminal.
* The bear case floored at the reserve value (00883.HK) was rebuilt as the unfloored blend.
"""
import copy
import importlib.util
import inspect
import io
import json
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


_wbt = _load("_wbt_hksg", _HERE / "test_valuation_workbook.py")
_tie = _load("_tieout_hksg", _HERE.parent / "scripts" / "workbook_tieout.py")


def _checks(run, ticker="TEST"):
    pytest.importorskip("formulas")
    blob = build_workbook(run, ticker)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "hksg.xlsx")
        Path(p).write_bytes(blob)
        return load_workbook(io.BytesIO(blob)), _tie._checks(p)


def _failing(checks):
    return [(s, c, v) for s, c, v in checks
            if s != "Checks" and not (v in ("OK", "ALL OK") or (isinstance(v, (int, float)) and abs(v) <= 0.01))]


def _with_leg(run, name, leg, weight=0.5):
    """Put `leg` in every scenario's blend at `weight` (the rest on P/E) and restate the engine IV and target."""
    dr = run["data"]["dcf_range"]["TEST"]
    for s in ("bear", "base", "bull"):
        sc = dr[s]
        sc["leg_inputs"] = {**sc["leg_inputs"], name: leg}
        sc["method_iv_table"] = {**sc["method_iv_table"], name: round(leg["value"], 2)}
        sc["effective_weights"] = [{"method": name, "value_key": name, "bucket": "multi", "weight": weight},
                                   {"method": "P/E", "value_key": "P/E", "bucket": "multi", "weight": 1 - weight}]
        sc["intrinsic_value"] = round(weight * leg["value"] + (1 - weight) * 180.0, 2)
    _retarget(run)
    return run


def _retarget(run):
    dr = run["data"]["dcf_range"]["TEST"]
    pb = dr["pt_bridge"]
    t = dr["12m_targets"] = {s: round(pb["spot"] + pb["capture"] * (dr[s]["intrinsic_value"] - pb["spot"]), 2)
                             for s in ("bear", "base", "bull")}
    run["data"]["scenario_analysis"]["TEST"]["reconciliation"]["12m_price_target"] = round(
        0.25 * t["bear"] + 0.5 * t["base"] + 0.25 * t["bull"], 2)


# ── 1. Holdco look-through SOTP ─────────────────────────────────────────────

def _lookthrough_leg():
    """Parts the way holdco_sotp.look_through_value records them, and the per-share it computes."""
    parts = [
        # a listed stake at market: market cap 4,000m x 37.48% x FX 1.0
        {"division": "Listed REIT", "basis": "market_stake", "listed": "K71U.SI", "stake_pct": 0.3748,
         "currency": "SGD", "fx_to_reporting": 1.0, "discount_pct": 0.0,
         "gross_value": 4_000e6 * 0.3748, "value": 4_000e6 * 0.3748},
        # a stake in another currency with its own placement discount
        {"division": "US-listed stake", "basis": "market_stake", "listed": "XYZ", "stake_pct": 0.2,
         "currency": "USD", "fx_to_reporting": 1.35, "discount_pct": 0.1,
         "gross_value": 1_000e6 * 0.2 * 1.35, "value": 1_000e6 * 0.2 * 1.35 * 0.9},
        # segment net profit (template millions) x P/E band midpoint
        {"division": "Asset management", "basis": "pe_range", "discount_pct": 0.0, "fiscal_year": 2025,
         "net_profit": 189, "multiple_range": [18.0, 22.0], "currency": "SGD", "equity_value": True,
         "gross_value": 189 * 20.0 * 1e6, "value": 189 * 20.0 * 1e6},
        {"division": "Legacy rigs", "basis": "fixed_value", "discount_pct": 0.0, "amount": 2590.0, "currency": "SGD",
         "gross_value": 2590e6, "value": 2590e6},
        {"division": "Perpetual securities", "basis": "fixed_value", "discount_pct": 0.0, "amount": -726.0,
         "currency": "SGD", "gross_value": -726e6, "value": -726e6},
        {"division": "Stub", "basis": "nil", "discount_pct": 0.0, "gross_value": 0.0, "value": 0.0},
    ]
    gross = sum(p["value"] for p in parts)
    nd, disc, fx, shares = 150e6, 0.1, 1.0, 1_000e6
    nav = (gross - nd) * (1 - disc)
    ps = nav * fx / shares
    lt = {"parts": parts, "skipped": [], "reporting_currency": "SGD", "gross_asset_value": gross,
          "parent_net_debt": nd, "holdco_discount": disc, "holdco_discount_range": [disc, disc],
          "net_asset_value": nav, "scenario_toggles": {}, "complete": True, "fx_to_listing": fx,
          "listing_currency": "SGD", "shares": shares, "per_share": ps}
    return {"kind": "sotp", "source": "look-through template", "lookthrough": lt, "value": ps}


def test_the_look_through_sotp_is_rebuilt_from_its_parts_and_ties():
    leg = _lookthrough_leg()
    wb, checks = _checks(_with_leg(_wbt._run(), "SOTP / NAV", leg))
    ws = wb["SOTP"]
    sotp = [x for x in checks if x[0] == "SOTP"]
    assert len(sotp) == 3 and not _failing(checks), _failing(checks)
    # every part is a live formula on its driver, never the engine's per-share pasted in
    vals = [ws.cell(row=r, column=7).value for r in range(1, ws.max_row + 1)]
    assert sum(1 for v in vals if isinstance(v, str) and re.match(r"=C\d+\*D\d+\*E\d+\*\(1-F\d+\)", v)) == 3 * 6
    labels = [str(ws.cell(row=r, column=1).value or "") for r in range(1, ws.max_row + 1)]
    assert "Gross asset value" in labels and any(x.startswith("Less: parent net debt") for x in labels)
    blend = wb["Blend"]
    assert any(str(blend.cell(row=r, column=5).value or "").startswith("='SOTP'!") for r in range(1, blend.max_row + 1))


def test_a_look_through_listed_in_another_currency_carries_the_nav_over_at_its_rate():
    leg = copy.deepcopy(_lookthrough_leg())
    lt = leg["lookthrough"]
    lt["fx_to_listing"] = 7.8                                # a USD template on an HKD line
    lt["per_share"] = lt["net_asset_value"] * 7.8 / lt["shares"]
    leg["value"] = lt["per_share"]
    _, checks = _checks(_with_leg(_wbt._run(), "SOTP / NAV", leg))
    assert [x for x in checks if x[0] == "SOTP"] and not _failing(checks), _failing(checks)


# ── 2. Segment-EV SOTP (VLO) ────────────────────────────────────────────────

def test_the_segment_ev_sotp_is_rebuilt_from_revenue_margin_scaler_and_multiple():
    scaler = 0.6446657603694829
    segs = [{"segment": "Refining", "revenue": 50_917e6, "basis": "ev_ebitda", "ebitda_margin": 0.0886,
             "multiple": 4.5, "reconciliation_scaler": scaler, "ebitda_estimated": 50_917e6 * 0.0886 * scaler,
             "ev": 50_917e6 * 0.0886 * scaler * 4.5},
            {"segment": "Software", "revenue": 500e6, "basis": "ev_revenue", "multiple": 3.0, "ev": 1_500e6},
            {"segment": "Chemicals JV", "revenue": 0.0, "basis": "carrying_value", "multiple": None,
             "ev": 2_000e6, "carrying_value": 2_000e6},
            {"segment": "Eliminations", "revenue": 100e6, "basis": "excluded", "multiple": None, "ev": 0.0}]
    ev = sum(s["ev"] for s in segs) * 1.0
    nd, mi, shares = 1_161e6, 3_110e6, 278.88e6
    eq = ev - nd - mi
    leg = {"kind": "sotp", "metric": "Sum of segment EVs", "metric_value": ev, "shares": shares, "tier": "default",
           "segments": segs, "priced_share_of_revenue": 1.0, "growth_premium": 1.0, "ev": ev, "net_debt": nd,
           "minority_interest": mi, "preferred_equity": 0.0, "equity": eq, "floored_at_zero": False,
           "value": eq / shares}
    wb, checks = _checks(_with_leg(_wbt._run(), "SOTP (segments)", leg, weight=0.4))
    assert len([x for x in checks if x[0] == "SOTP"]) == 3 and not _failing(checks), _failing(checks)
    ws = wb["SOTP"]
    labels = [str(ws.cell(row=r, column=1).value or "") for r in range(1, ws.max_row + 1)]
    assert "Sum of segment EVs" in labels and "Less: minority interest" in labels


# ── 3. DCF-family leg on the core DCF's value-driver terminal ───────────────

_VD = "terminal_ronic_spread" in inspect.signature(d._project_dcf).parameters


@pytest.mark.skipif(not _VD, reason="engine predates the value-driver terminal value")
def test_a_dcf_family_leg_without_terminal_inputs_takes_the_core_dcfs_and_ties():
    tm = d._dcf_timing("2025-12-31", "2026-09-30", "2026-11-25")
    sched = [0.01] + [0.015] * 9
    iv, pv_f, pv_t, rows = d._project_dcf(1_000.0, 0.15, 0.015, 0.0, 0.09, 0.015, 0.05, -300.0, 10.0,
                                          growth_schedule=sched, timing=tm, terminal_nopat_ratio=1.19,
                                          terminal_ronic_spread=0.0, minority_interest=5.0)
    vd_on = d._switch_on("VALUE_DRIVER_TV")
    core = {"kind": "dcf", "value": iv, "revenue_base": 1_000.0, "fcf_margin_base": 0.15, "growth_base": 0.015,
            "growth_schedule": sched, "terminal_loe_multiplier": 1.0, "terminal_nopat_ratio": 1.19,
            "terminal_ronic_spread": 0.0, "terminal_formula": "value_driver" if vd_on else "gordon",
            "margin_delta_absolute": 0.0, "wacc": 0.09, "wacc_schedule": None, "tgr": 0.015, "fcf_floor": 0.05,
            "net_debt": -300.0, "shares": 10.0, "minority_interest": 5.0, "preferred_equity": 0.0,
            "pv_fcf_per_share": pv_f, "pv_tv_per_share": pv_t, "projection_rows": rows, "timing": tm}
    # the family leg's trace, as dcf_agent records it: the same projection, no terminal value-driver fields
    two = {k: v for k, v in core.items() if k not in ("terminal_nopat_ratio", "terminal_ronic_spread", "terminal_formula")}
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    for s in ("bear", "base", "bull"):
        sc = dr[s]
        sc["leg_inputs"] = {**sc["leg_inputs"], "DCF": core, "DCF (2-stage)": two}
        sc["method_iv_table"] = {**sc["method_iv_table"], "DCF": iv, "DCF (2-stage)": iv}
        sc["effective_weights"] = [{"method": "DCF (2-stage)", "value_key": "DCF (2-stage)", "bucket": "dcf", "weight": 0.2},
                                   {"method": "P/E", "value_key": "P/E", "bucket": "multi", "weight": 0.8}]
        sc["intrinsic_value"] = round(0.2 * iv + 0.8 * 180.0, 2)
    _retarget(run)
    wb, checks = _checks(run)
    assert [x for x in checks if x[0] == "DCF"] and not _failing(checks), _failing(checks)


# ── 4. Bear case floored at the reserve value ───────────────────────────────

def test_the_bear_reserve_floor_is_rebuilt_as_max_of_the_blend_and_the_floor():
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    bear = dr["bear"]
    before = bear["intrinsic_value"]
    floor = round(before + 7.5, 2)
    bear["intrinsic_value"] = floor
    bear["reserve_floor"] = {"floor_per_share": floor, "before": before, "period": "FY2025"}
    for s in ("bear", "base", "bull"):
        dr[s]["method_iv_table"]["Reserve NPV (PV-10)"] = floor
    _retarget(run)
    wb, checks = _checks(run)
    assert not _failing(checks), _failing(checks)
    ws = wb["Blend"]
    f = [str(ws.cell(row=r, column=6).value or "") for r in range(1, ws.max_row + 1)]
    assert sum(1 for x in f if x.startswith("=MAX(")) == 1      # bear only


def test_a_reserve_value_that_does_not_bind_leaves_the_bear_blend_as_it_is():
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    for s in ("bear", "base", "bull"):
        dr[s]["method_iv_table"]["Reserve NPV (PV-10)"] = 1.0
    _retarget(run)
    _, checks = _checks(run)
    assert not _failing(checks), _failing(checks)


# ── 5. The production payloads (local only) ─────────────────────────────────

_TMP = Path(os.environ.get("TEMP") or tempfile.gettempdir())
_PROD = [_TMP / "hksg" / "BN4.SI.json", _TMP / "hksg2" / "U96.SI.json", _TMP / "hksg" / "00941.HK.json",
         _TMP / "hksg3" / "00883.HK.json"]


@pytest.mark.parametrize("path", _PROD, ids=[p.stem for p in _PROD])
def test_the_production_runs_tie_out(path):
    if not path.exists():
        pytest.skip(f"production payload not present: {path}")
    run = json.loads(path.read_text(encoding="utf-8"))
    ticker = next(iter(run["data"]["dcf_range"]))
    pytest.importorskip("formulas")
    blob = build_workbook(run, ticker)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "prod.xlsx")
        Path(p).write_bytes(blob)
        checks = _tie._checks(p)
    assert checks and not _failing(checks), _failing(checks)
