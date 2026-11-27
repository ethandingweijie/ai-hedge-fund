"""D2 (owner, 2026-11-27): equity-accounted stakes enter the ONE shared equity bridge for every leg.

Singtel equity-accounts Airtel, AIS, Globe and Telkomsel (S$66bn at market). Their profit sits below EBITDA, so the
40%-weight EV/EBITDA leg never priced them (S$2.04 a share) while the DCF carried their income: two perimeters in
one blend. Now: market value x (1 - holdco discount 0.15) is added in build_equity_bridge for every EV leg and the
DCF, the DCF's associates-income line goes to 0 (holdings netted), and the SOTP leg -- which already holds the stakes
in its parts -- adds nothing.
"""
from __future__ import annotations

import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.analysis import holdco_sotp as hs

#: The 2026-11-26 production run's look-through parts (S$), as recorded in its leg trace.
_PARTS = [
    {"division": "Bharti Airtel", "basis": "market_stake", "value": 49116584980.67065},
    {"division": "Advanced Info Service (AIS)", "basis": "market_stake", "value": 9020712696.673807},
    {"division": "Gulf Development", "basis": "market_stake", "value": 1572293952.8335066},
    {"division": "Globe Telecom", "basis": "fixed_value", "value": 1702177426.846261},
    {"division": "Telkomsel", "basis": "pe_range", "value": 4410000000.0},
    {"division": "Consolidated operations (Singtel Singapore, Optus, NCS, Digital InfraCo)",
     "basis": "ev_ebitda_range", "value": 29282135000.0},
]
_SHARES = 16335757532.995308


@pytest.fixture
def stored_lookthrough(monkeypatch):
    monkeypatch.setattr(hs, "look_through_value",
                        lambda t, e, **k: {"parts": _PARTS, "skipped": [], "fx_fallback_pairs": [],
                                           "reporting_currency": "SGD"})


def test_bridge_adds_the_stakes_and_never_subtracts_them():
    assert d.build_equity_bridge(100.0, 30.0, 5.0, 1.0) == pytest.approx(64.0)
    assert d.build_equity_bridge(100.0, 30.0, 5.0, 1.0, 20.0) == pytest.approx(84.0)
    assert d.build_equity_bridge(100.0, 30.0, 5.0, 1.0, -20.0) == pytest.approx(64.0)


def test_singtel_stakes_are_the_non_consolidated_rows_at_market_less_15pct(stored_lookthrough):
    st = d._equity_accounted_stakes("Z74.SI", "2026-11-26", "SGD")
    gross = sum(p["value"] for p in _PARTS if p["basis"] != "ev_ebitda_range")
    assert gross == pytest.approx(65.82e9, rel=1e-3)
    assert st["holdco_discount"] == pytest.approx(0.15)               # the template's own 10-20% midpoint
    assert st["value"] == pytest.approx(gross * 0.85)
    assert st["value"] / _SHARES == pytest.approx(3.425, abs=1e-3)     # prototype: +3.425 a share
    assert not any("Consolidated" in p["division"] for p in st["parts"])


def test_only_a_template_that_opts_in(stored_lookthrough):
    assert hs.template_for("Z74.SI").get("bridge_equity_accounted_stakes") is True
    for t in ("00019.HK", "01548.HK", "02020.HK", "VC2.SI", "AAPL"):
        assert d._equity_accounted_stakes(t, "2026-11-26", None) is None


def test_every_ev_leg_and_the_dcf_read_the_one_figure():
    row = {"minority_interest": 163900000.0, "preferred_equity": 0.0}
    ev, nd = 42954479187.535835, 9428700000.0
    base = d._ev_to_equity_ps(ev, nd, row, _SHARES)
    assert base == pytest.approx(2.0423, abs=1e-4)                     # the stored EV/EBITDA leg
    tok = d._BRIDGE_NON_OPERATING_ASSETS.set(55.95e9)
    try:
        with_stakes = d._ev_to_equity_ps(ev, nd, row, _SHARES)
        opted_out = d._ev_to_equity_ps(ev, nd, row, _SHARES, non_operating_assets=0.0)
        kw = dict(revenue_base=1e10, fcf_margin_base=0.1, growth_rate=0.0, margin_delta_per_year=0.0, wacc=0.06,
                  tgr=0.02, fcf_floor=0.05, net_debt=nd, shares=_SHARES)
        dcf_with = d._project_dcf(**kw)[0]
        dcf_without = d._project_dcf(**kw, non_operating_assets=0.0)[0]
    finally:
        d._BRIDGE_NON_OPERATING_ASSETS.reset(tok)
    assert with_stakes - base == pytest.approx(55.95e9 / _SHARES)
    assert opted_out == pytest.approx(base)
    assert dcf_with - dcf_without == pytest.approx(55.95e9 / _SHARES)
    # default is zero: no run state, no stakes
    assert d._ev_to_equity_ps(ev, nd, row, _SHARES) == pytest.approx(base)


def test_sotp_legs_do_not_add_the_stakes_again(monkeypatch):
    monkeypatch.setattr(hs, "enabled_for", lambda t: True)
    monkeypatch.setattr(hs, "value_per_share", lambda *a, **k: 4.46)
    monkeypatch.setattr(hs, "parent_net_debt", lambda *a, **k: 9.4287e9)
    monkeypatch.setattr("src.tools.api.get_listing_currency", lambda *a, **k: "SGD", raising=False)
    tok = d._BRIDGE_NON_OPERATING_ASSETS.set(55.95e9)
    try:
        v = d._compute_method_value(
            method_name="SOTP / NAV (look-through)", most_recent={}, revenue_base=1.4e10, shares=_SHARES,
            net_debt=9.4287e9, market_cap=7e10, wacc=0.0529, growth_base=0.03, fcf_margin_base=0.08, tgr=0.02,
            fcf_floor=0.05, sector="Telco", scenario="base", profile_name="Telecom Carrier", ticker="Z74.SI",
            end_date="2026-11-26")
    finally:
        d._BRIDGE_NON_OPERATING_ASSETS.reset(tok)
    assert v == pytest.approx(4.46)
    src = inspect.getsource(d._compute_method_value)
    assert "return _ev_to_equity_ps(total_ev, net_debt, most_recent, shares, non_operating_assets=0.0)" in src


def test_the_associates_income_line_goes_to_zero_when_the_stakes_are_bridged():
    from src.agents.analysis import guidance_forecast as gfm
    hist = {"non_operating_ratio": 0.0414}
    assert gfm.with_non_operating_decision(hist, False)["non_operating_ratio_used"] == pytest.approx(0.0414)
    assert gfm.with_non_operating_decision(hist, True)["non_operating_ratio_used"] == 0.0
    src = inspect.getsource(d.run_dcf_agent)
    assert "_holdings_netted = bool(_lti_already or (_bridge_stakes" in src
    assert src.count("holdings_netted=_holdings_netted)") == 2
    assert "_gfm.history_ratios(series, _gfm.load_cfg()), _holdings_netted)" in src
    # sourced once per ticker, reset at the top of every ticker and at the end of the run
    assert src.count("_BRIDGE_NON_OPERATING_ASSETS.set(0.0)") >= 2


def test_the_workbook_rebuilds_the_stakes_in_both_bridges_and_ties():
    """The Excel export reconciles: the DCF tab and the Multiples tab add the same stakes the engine did."""
    import copy
    import importlib.util
    import io
    import os
    import tempfile
    from pathlib import Path
    pytest.importorskip("formulas")
    from openpyxl import load_workbook
    from src.utils.valuation_workbook import build_workbook
    here = Path(__file__).resolve().parent

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    wbt = _load("_wbt_d2", here / "test_valuation_workbook.py")
    tie = _load("_tie_d2", here.parent / "scripts" / "workbook_tieout.py")
    run = wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    noa = 50.0
    legs = copy.deepcopy(dr["base"]["leg_inputs"])
    legs["DCF"]["non_operating_assets"] = noa
    legs["DCF"]["value"] = legs["DCF"]["value"] + noa / legs["DCF"]["shares"]
    ev = legs["EV/EBITDA"]
    ev.update(non_operating_assets=noa, equity=ev["equity"] + noa, value=(ev["equity"] + noa) / ev["shares"])
    for s in ("bear", "base", "bull"):
        sc = dr[s]
        sc["leg_inputs"] = legs
        sc["method_iv_table"] = {"DCF": legs["DCF"]["value"], "EV/EBITDA": ev["value"], "P/E": 180.0}
        sc["intrinsic_value"] = round(0.4 * legs["DCF"]["value"] + 0.4 * ev["value"] + 0.2 * 180.0, 2)
    pb = dr["pt_bridge"]
    t = dr["12m_targets"] = {s: round(pb["spot"] + pb["capture"] * (dr[s]["intrinsic_value"] - pb["spot"]), 2)
                             for s in ("bear", "base", "bull")}
    run["data"]["scenario_analysis"]["TEST"]["reconciliation"]["12m_price_target"] = round(
        0.25 * t["bear"] + 0.5 * t["base"] + 0.25 * t["bull"], 2)
    blob = build_workbook(run, "TEST")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "d2.xlsx")
        Path(p).write_bytes(blob)
        checks = tie._checks(p)
    bad = [(s, c, v) for s, c, v in checks
           if s != "Checks" and not (v in ("OK", "ALL OK") or (isinstance(v, (int, float)) and abs(v) <= 0.01))]
    assert checks and not bad, bad
    ws = load_workbook(io.BytesIO(blob))["DCF"]
    labels = [str(ws.cell(row=r, column=1).value or "") for r in range(1, ws.max_row + 1)]
    assert any(x.startswith("Add: equity-accounted stakes") for x in labels)


def test_override_recompute_carries_the_stakes():
    from app.backend.services import estimate_override_service as eos
    src = inspect.getsource(eos)
    assert 'non_operating_assets=float(leg.get("non_operating_assets") or 0.0)' in src
    assert 'max(float(tr.get("non_operating_assets") or 0.0), 0.0)' in src
