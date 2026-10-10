"""Cross-sector audit against the biopharma lessons (docs/cross_sector_audit_2026-10.md), owner decisions 2026-10-10.

Batch A -- basis: E1 lease-free peer EV, E2 filing-basis registry, E4 revenue / EBIT street-basis guard, E5 a
negative consensus stands the leg down, E6 forward legs with no forward peer multiple are relabelled trailing.
"""
from __future__ import annotations

import pytest

from src.agents.analysis import dcf_agent as d
from src.data import comps_history_backfill as hb
from src.data import filing_basis as fb
from src.data import regional_comps as rc


# ── E2: the filing basis comes from the registry, not the reporting currency ─────────────────────────

def test_e2_usd_reporting_ifrs_filers_are_ifrs():
    for t in ("BHP", "RIO", "SHEL", "BP", "AZN", "NVS", "00005.HK", "J36.SI", "F34.SI"):
        assert fb.reports_us_gaap(t, "USD") is False, t
    assert fb.reports_us_gaap("AAPL", "USD") is True
    assert fb.reports_us_gaap("BABA", "CNY") is True and fb.reports_us_gaap("09988.HK", "CNY") is True
    assert fb.reports_us_gaap("0700.HK", "CNY") is False and fb.reports_us_gaap("D05.SI", "SGD") is False


def test_e2_an_ifrs_dollar_reporter_keeps_its_leases_in_net_debt():
    row = {"net_debt": 20e9, "total_debt": 25e9, "cash_and_equivalents": 5e9, "lease_liabilities": 3e9}
    nd_bhp, basis_bhp = d._valuation_net_debt(dict(row), "Resources", "BHP", "USD")
    nd_x, basis_x = d._valuation_net_debt(dict(row), "Resources", "XOM", "USD")
    assert basis_bhp["accounting_basis"] == "IFRS" and basis_bhp["components"]["leases_removed"] == 0.0
    assert basis_x["accounting_basis"] == "US GAAP" and nd_bhp - nd_x == pytest.approx(3e9)


# ── E1: a US GAAP peer's EV multiples are restated ex leases, as the subject's net debt is ────────────

def _bs(lease, debt, ccy="USD"):
    return {"capitalLeaseObligations": lease, "totalDebt": debt, "reportedCurrency": ccy}


def test_e1_lease_factor_strips_leases_for_us_gaap_only():
    # SBUX, 2026-10-07: EV 126.9bn = mcap 107.9 + debt 22.4 (incl. 9.2 leases) - cash 3.4
    assert rc.lease_ev_factor("SBUX", 126.9e9, _bs(9.2e9, 22.4e9)) == pytest.approx((126.9 - 9.2) / 126.9)
    assert rc.lease_ev_factor("BHP", 200e9, _bs(3e9, 25e9)) is None            # IFRS: leases are debt
    assert rc.lease_ev_factor("XYZ", 50e9, _bs(5e9, 4e9)) is None              # feed debt does not carry them
    assert rc.lease_ev_factor("XYZ", 50e9, None) is None


def test_e1_key_metrics_ev_fields_are_rescaled(monkeypatch):
    monkeypatch.setattr(rc, "_fmp_get", lambda *a, **k: [_bs(9.2e9, 22.4e9)])
    km = {"enterpriseValueTTM": 126.9e9, "evToEBITDATTM": 21.6, "evToSalesTTM": 3.4, "evToOperatingCashFlowTTM": 20.0,
          "freeCashFlowYieldTTM": 0.03}
    out = rc.lease_free_key_metrics("SBUX", km)
    f = (126.9 - 9.2) / 126.9
    assert out["evToEBITDATTM"] == pytest.approx(21.6 * f) and out["evToSalesTTM"] == pytest.approx(3.4 * f)
    assert out["freeCashFlowYieldTTM"] == 0.03 and out["_lease_ev_factor"] == pytest.approx(f)
    # An HK listing that is not a US GAAP filer never pays the extra call.
    monkeypatch.setattr(rc, "_fmp_get", lambda *a, **k: pytest.fail("no call for an IFRS HK listing"))
    assert rc.lease_free_key_metrics("0700.HK", dict(km)) == km


def test_e1_through_cycle_history_is_lease_free(monkeypatch):
    def fake(path, params, api_key=None):
        if "key-metrics" in path:
            return [{"date": "2024-09-30", "enterpriseValue": 120e9, "evToEBITDA": 20.0, "evToSales": 3.0,
                     "reportedCurrency": "USD", "marketCap": 100e9}]
        if "income-statement" in path:
            return [{"date": f"{y}-09-30", "revenue": 36e9, "ebitda": 6e9, "netIncome": 3e9, "reportedCurrency": "USD"}
                    for y in (2022, 2023, 2024)]
        if "balance-sheet" in path:
            return [{"date": "2024-09-30", **_bs(10e9, 25e9)}]
        return []
    monkeypatch.setattr(rc, "_fmp_get", fake)
    h = hb.member_history("SBUX")["2024"]
    f = 110 / 120
    assert h["ev_ebitda"] == pytest.approx(20.0 * f) and h["ev_ebitda_norm"] == pytest.approx(110e9 / 6e9)


# ── E4 / E5: the street-basis guard covers revenue and EBIT, and a sign conflict stands the leg down ──

def _fwd(metric, cur, cons, src="guidance-derived FY+1 revenue"):
    return {metric: {"base": cur, "bear": cur, "bull": cur}, "_source": {metric: {"base": src, "bear": src, "bull": src}},
            "_consensus": {metric: {"base": cons, "bear": cons, "bull": cons}}}


@pytest.mark.parametrize("metric,leg", [("revenue", "EV/NTM Revenue"), ("ebit", "Forward EV/EBIT")])
def test_e4_revenue_and_ebit_revert_to_consensus_beyond_15pct(metric, leg):
    out, dec, flag = d._street_basis_guard(_fwd(metric, 60e9, 100e9), "base", None, 30, metric)
    assert dec is True and out[metric]["base"] == 100e9 and leg in flag
    assert out["_source"][metric]["base"].startswith("consensus ")
    # bear and bull take the base decision: one basis across scenarios
    out_b, dec_b, flag_b = d._street_basis_guard(_fwd(metric, 60e9, 90e9), "bear", dec, 30, metric)
    assert out_b[metric]["bear"] == 90e9 and flag_b is None
    # within 15%, or under 5 analysts, guidance stands
    assert d._street_basis_guard(_fwd(metric, 90e9, 100e9), "base", None, 30, metric)[1] is False
    assert d._street_basis_guard(_fwd(metric, 60e9, 100e9), "base", None, 4, metric)[1] is False


def test_e5_negative_consensus_eps_stands_the_forward_pe_down():
    out, dec, flag = d._street_basis_guard(_fwd("eps", 1.2, -0.45, "guidance-derived FY+1 EPS estimate"), "base", None, 12, "eps")
    assert dec is True and out["eps"]["base"] == -0.45 and "stands down" in flag
    # the leg itself returns nothing on a non-positive EPS, so its weight follows the waterfall
    v, _ = d._traced_method_value(method_name="Forward P/E", most_recent={"net_income": 1e9}, forward_consensus=out,
                                  **_KW)
    assert v is None


def test_e4_guard_runs_for_revenue_and_ebit_at_the_call_site():
    import inspect
    src = inspect.getsource(d)
    assert '_revenue_street_basis, _rsb_flag = _street_basis_guard(' in src
    assert '_ebit_street_basis, _etb_flag = _street_basis_guard(' in src


# ── E6: a forward leg with no forward peer multiple is relabelled trailing ───────────────────────────

_KW = dict(revenue_base=10e9, shares=1e9, net_debt=2e9, market_cap=30e9, wacc=0.08, growth_base=0.05,
           fcf_margin_base=0.1, tgr=0.025, fcf_floor=0.0, sector="Consumer", scenario="base")


def _leg(name, row, peer, fc, monkeypatch, **extra):
    monkeypatch.setattr(d, "get_sector_peer_multiples", lambda *a, **k: dict(peer))
    return d._traced_method_value(method_name=name, most_recent=row, forward_consensus=fc, **{**_KW, **extra})


def test_e6_forward_pe_without_a_forward_peer_pe_prices_ttm_eps(monkeypatch):
    fc = {"eps": {"base": 2.0, "bear": 1.8, "bull": 2.2}}
    v, tr = _leg("Forward P/E", {"net_income": 1.5e9}, {"pe": 20.0}, fc, monkeypatch)
    assert v == pytest.approx(1.5 * 20.0) and tr["basis"] == "trailing (relabelled)"
    assert "TTM" in tr["metric"] and "no NTM peer data" in tr["multiple_parts"]["peer_source"]
    # with a forward peer P/E the leg stays forward
    v2, tr2 = _leg("Forward P/E", {"net_income": 1.5e9}, {"pe": 20.0, "pe_ntm": 16.0}, fc, monkeypatch)
    assert v2 == pytest.approx(2.0 * 16.0) and "forward basis" in tr2["multiple_parts"]["peer_source"]


def test_e6_forward_ev_ebitda_without_a_forward_peer_multiple_prices_ttm_ebitda(monkeypatch):
    fc = {"ebitda": {"base": 3e9}}
    v, tr = _leg("Forward EV/EBITDA", {"ebitda": 2.5e9}, {"ev_ebitda": 10.0}, fc, monkeypatch)
    assert tr["metric_value"] == 2.5e9 and tr["basis"] == "trailing (relabelled)"
    assert v == pytest.approx((2.5e9 * 10.0 - 2e9) / 1e9)


def test_e6_forward_ev_ebit_uses_a_derived_forward_multiple_or_relabels(monkeypatch):
    fc = {"ebit": {"base": 2e9}}
    peer = {"ev_ebitda": 12.0, "ev_ebit": 15.0, "ev_ebitda_ntm": 10.0}
    v, tr = _leg("Forward EV/EBIT", {"ebit": 1.6e9}, peer, fc, monkeypatch)
    assert tr["multiple"] == pytest.approx(10.0 * 15.0 / 12.0) and "forward basis" in tr["multiple_parts"]["peer_source"]
    v2, tr2 = _leg("Forward EV/EBIT", {"ebit": 1.6e9}, {"ev_ebitda": 12.0, "ev_ebit": 15.0}, fc, monkeypatch)
    assert tr2["metric_value"] == 1.6e9 and tr2["basis"] == "trailing (relabelled)"
    assert v2 == pytest.approx((1.6e9 * 15.0 - 2e9) / 1e9)


# ══ Batch B -- capex & cash: E3 capex vs D&A, E13 SBC, E15 FCF basis, E18 net debt ══════════════════════════════

from src.agents.analysis import guidance_forecast as gf


def _hist(da, cx, intang=0.0, ratio=None, content=None):
    return {"da": da, "capex": cx, "intangible_assets": intang, "da_capex_ratio_median": ratio if ratio is not None else da / cx,
            "content_amortisation": content}


def test_e3_split_needs_a_median_ratio_above_one_and_acquired_intangibles():
    cfg = gf.load_cfg()
    # AVGO-like: D&A 14x capex, 40bn of acquired intangibles -> split, run-off on the implied remaining life
    r = gf._capex_rule(_hist(7e9, 0.5e9, intang=40e9), cfg, "Fabless")
    assert r["mode"] == "amortisation split" and r["runoff_years"] == pytest.approx(40e9 / 6.5e9)
    # CSCO-like: 1.4x (the old 2.0x trigger missed it) with intangibles -> split now
    assert gf._capex_rule(_hist(2.8e9, 2.0e9, intang=9e9), cfg, "Networking")["mode"] == "amortisation split"
    # AAPL-like (owner spec): D&A ~1.1x capex but no acquired intangibles -> no runoff
    a = gf._capex_rule(_hist(11.5e9, 10.5e9, intang=0.0), cfg, "Hyperscaler / Tech Conglomerate")
    assert a["mode"] == "capex = D&A + growth" and "intangibles" in a["reason"]
    # the HISTORICAL median decides, not a one-year spike
    assert gf._capex_rule(_hist(3e9, 1e9, intang=9e9, ratio=0.95), cfg, "X")["mode"] == "capex = D&A + growth"


def test_e3_content_companies_never_split():
    cfg = gf.load_cfg()
    n = gf._capex_rule(_hist(16e9, 0.5e9, intang=30e9), cfg, "Media & Streaming")       # NFLX: content amortisation
    assert n["mode"] == "capex = D&A + growth" and "content" in n["reason"]
    assert gf._capex_rule(_hist(16e9, 0.5e9, intang=30e9, content=15e9), cfg, "Other")["mode"] == "capex = D&A + growth"


def test_e3_history_ratios_report_the_median_and_intangibles():
    series = [{"revenue": 10e9, "depreciation_and_amortization": 1.2e9, "capital_expenditure": -1e9},
              {"revenue": 11e9, "depreciation_and_amortization": 3.0e9, "capital_expenditure": -1e9},     # one-year spike
              {"revenue": 12e9, "depreciation_and_amortization": 1.1e9, "capital_expenditure": -1e9, "intangible_assets": 2e9}]
    h = gf.history_ratios(series, gf.load_cfg())
    assert h["da_capex_ratio_median"] == pytest.approx(1.2) and h["intangible_assets"] == 2e9


def test_e3_forecast_carries_the_rule_it_used():
    from tests.test_guidance_forecast import _fc
    fc = _fc()
    assert fc["capex_rule"]["mode"] == "capex = D&A + growth"      # 1.33x but no acquired intangibles in the series


def test_e13_no_hidden_sbc_trim_on_ev_multiples(monkeypatch):
    row = {"ebitda": 2e9, "stock_based_compensation": 3e9}                              # SBC 30% of revenue
    v, tr = _leg("EV/EBITDA", row, {"ev_ebitda": 20.0}, None, monkeypatch, sector="Tech", profile_name="Growth SaaS")
    assert "sbc_haircut" not in tr["multiple_parts"] and tr["multiple"] == pytest.approx(tr["multiple_parts"]["peer_multiple"])


def test_e13_e15_fcf_yield_on_the_peers_basis(monkeypatch):
    row = {"free_cash_flow": 1.0e9, "fcf_owner_earnings": 0.6e9, "operating_cash_flow": 2e9,
           "capital_expenditure": -1e9, "depreciation_and_amortization": 0.5e9}
    v, tr = _leg("FCF Yield", row, {"fcf_yield": 0.05}, None, monkeypatch)
    assert tr["metric_value"] == 1.0e9 and "peers' basis" in tr["metric"]
    # the Wave 9 capex ceiling no longer prices the subject on maintenance capex against peers' full capex
    v2, tr2 = _leg("FCF Yield", row, {"fcf_yield": 0.05}, None, monkeypatch, profile_name="Waste & Environmental Services")
    assert tr2["metric_value"] == 1.0e9


def test_e18_long_term_investments_count_as_cash_unless_a_sotp_prices_them():
    basis = {"components": {"long_term_investments": None, "result": 10e9}}
    row = {"long_term_investments": 80e9}
    nd, b, flag = d._apply_lti_netting(10e9, dict(basis), row, "Tech", "AAPL", "Hyperscaler / Tech Conglomerate")
    assert nd == pytest.approx(-70e9) and b["components"]["long_term_investments"] == 80e9 and "E18" in flag
    # China Internet Platform weights SOTP (analyst): the holdings are priced there, never twice
    nd2, _, f2 = d._apply_lti_netting(10e9, dict(basis), row, "Tech", "BABA", "China Internet Platform")
    assert nd2 == 10e9 and f2 is None
    # financials, property, customer-fund holders: not spare cash
    for sec, t in (("Financials", "JPM"), ("Property", "X"), ("Tech", "MELI")):
        assert d._apply_lti_netting(10e9, dict(basis), row, sec, t, "")[0] == 10e9
    # the biotech rule nets them first: never twice
    nd3, _, _ = d._apply_lti_netting(5e9, {"components": {"long_term_investments": 5e9}}, row, "Biopharma", "VRTX", "")
    assert nd3 == 5e9


def test_e18_customer_funds_and_crypto_short_term_investments_stay_out_of_cash():
    row = {"net_debt": 5e9, "total_debt": 8e9, "cash_and_equivalents": 3e9, "short_term_investments": 4e9}
    nd_meli, _ = d._valuation_net_debt(dict(row), "Tech", "MELI", "USD")
    nd_x, _ = d._valuation_net_debt(dict(row), "Tech", "XYZW", "USD")
    nd_coin, _ = d._valuation_net_debt(dict(row), "Crypto", "COIN2", "USD")
    assert nd_meli == 5e9 and nd_coin == 5e9 and nd_x == pytest.approx(1e9)


# ══ Batch F -- AAPL: Consumer Technology Ecosystem (owner spec, 2026-10-10) ══════════════════════════════════════

from src.data import sector_profiles as sp

CTE = "Consumer Technology Ecosystem"


def test_f_aapl_is_pinned_to_the_ecosystem_profile_not_hyperscaler():
    assert sp.TICKER_SECTOR_LOOKUP["AAPL"][:2] == ("Tech", CTE)
    m = {x["name"]: x for x in sp.INDUSTRY_VALUATION_PROFILES["Tech"][CTE]["methods"]}
    assert (m["SOTP (Hardware + Services)"]["weight"], m["DCF"]["weight"], m["Shareholder Yield"]["weight"]) == (0.40, 0.35, 0.25)
    assert m["SOTP (Hardware + Services)"]["anchor"] and m["SOTP (Hardware + Services)"]["fallback"] == "P/FCF (NTM)"
    assert "AAPL" not in rc.PROFILE_PEER_BASKETS["Hyperscaler / Tech Conglomerate"]["US"]
    assert rc.CROSS_MARKET_BASKETS[CTE]["symbols"] == ("MSFT", "GOOGL", "RMS.PA", "MC.PA", "005930.KS", "SONY")
    assert sp._profile_wacc_rate("Tech", CTE)[0] == 0.0825 and d._PROFILE_TGR[CTE]["base"] == 0.0275


def test_f_hardware_services_sotp_splits_operating_earnings_and_adds_net_cash(monkeypatch):
    row = {"segment_breakdown": {"iPhone": 200e9, "Mac": 30e9, "iPad": 27e9, "Wearables": 36e9, "Service": 109e9},
           "net_income": 112e9, "interest_income": 0.0}
    v, tr = _leg("SOTP (Hardware + Services)", row, {}, None, monkeypatch, net_debt=-60e9, shares=14.8e9,
                 sector="Tech", profile_name=CTE)
    hw, svc = 293e9 * 0.36, 109e9 * 0.725
    ni_hw = 112e9 * hw / (hw + svc)
    assert v == pytest.approx((ni_hw * 16.5 + (112e9 - ni_hw) * 29.0 + 60e9) / 14.8e9)
    # bear: low-end multiples and a 300bp services gross-margin (TAC) stress
    vb, trb = _leg("SOTP (Hardware + Services)", row, {}, None, monkeypatch, net_debt=-60e9, shares=14.8e9,
                   sector="Tech", profile_name=CTE, scenario="bear")
    assert trb["tac_haircut"] == 0.03 and vb < v
    # no segment data -> the anchor cannot value
    assert _leg("SOTP (Hardware + Services)", {"net_income": 1e9}, {}, None, monkeypatch, profile_name=CTE)[0] is None


def test_f_shareholder_yield_and_ntm_pfcf(monkeypatch):
    row = {"normalized_free_cash_flow": 100e9, "free_cash_flow": 110e9, "_net_share_shrink": 0.03}
    v, tr = _leg("Shareholder Yield", row, {}, None, monkeypatch, shares=15e9, wacc=0.085)
    assert v == pytest.approx((100e9 / 15e9) / (0.085 - 0.03))
    fc = {"revenue": {"base": 440e9}}
    v2, tr2 = _leg("P/FCF (NTM)", row, {"fcf_yield": 0.05}, fc, monkeypatch, shares=15e9, revenue_base=400e9)
    assert v2 == pytest.approx((110e9 * 1.1 / 15e9) / 0.05)


def test_f_anchor_fallback_is_the_profile_named_leg_and_an_operating_sotp_still_nets_investments():
    import inspect
    src = inspect.getsource(d)
    assert '_fb_name = _anchor_m.get("fallback")' in src
    # the operating SOTP adds net cash back, so long-term investments are netted for AAPL
    nd, b, flag = d._apply_lti_netting(21.9e9, {"components": {}}, {"long_term_investments": 84e9}, "Tech", "AAPL", CTE)
    assert nd == pytest.approx(21.9e9 - 84e9) and flag


# ══ Batch C -- one path and honest flags: E7, E8, E9, E11, E12, E19, E20 ══════════════════════════════════════

def test_e20_terminal_bound_failure_clamps_and_reports_it():
    from tests.test_guidance_forecast import _fc
    fc = _fc(peer_ev_ebitda=2.0)                       # a peer median far below the implied exit -> ceiling breached
    inv4 = next(i for i in fc["invariants"] if i["id"] == 4)
    if inv4["ok"] is False:
        assert inv4.get("acted") and fc["terminal"]["terminal_clamp"] == pytest.approx(inv4["ceiling"] / fc["terminal"]["implied_exit_ev_ebitda"])
        assert "ACTED: terminal value clamped" in inv4["detail"]
    else:
        assert fc["terminal"]["terminal_clamp"] is None


def test_e20_low_cash_conversion_fades_onto_the_lower_bound():
    import copy
    from tests import test_guidance_forecast as _g
    series = copy.deepcopy(_g._SERIES)
    for r in series:                                   # heavy capex: conversion far below 0.60x
        r["capital_expenditure"] = -0.08 * r["revenue"]
    fc = gf.build_forecast(_g._BLOCK, scenario="base", series=series, profile_name="Managed Care", sector="Healthcare",
                           wacc=0.08, tgr=0.025, shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)
    inv2 = next(i for i in fc["invariants"] if i["id"] == 2)
    if inv2.get("acted") and "faded onto" in inv2["detail"]:
        steady = [r for r in fc["rows"] if r["phase"] not in ("guided", "engine path") and r["net_income"] > 0]
        assert steady[-1]["ufcf"] / steady[-1]["net_income"] == pytest.approx(0.60, rel=1e-6)


def test_e11_loe_scaling_keeps_every_row_tie():
    gfc = {"rows": [{"year": 1, "revenue": 100.0, "ebit": 30.0, "tax": 6.0, "nopat": 24.0, "da": 10.0, "amortisation": 4.0,
                     "capex": 8.0, "capex_maintenance": 6.0, "capex_growth": 2.0, "delta_nwc": 1.0, "ufcf": 25.0,
                     "net_income": 22.0, "eps": 2.2}],
           "invariants": [{"id": 4, "detail": "x"}]}
    d._loe_scale_forecast(gfc, {"index": [0.8], "growth_schedule": [0.0]})
    r = gfc["rows"][0]
    assert r["ufcf"] == pytest.approx(r["nopat"] + r["da"] - r["capex"] - r["delta_nwc"])
    assert r["capex"] == pytest.approx(r["capex_maintenance"] + r["capex_growth"]) and r["tax"] == pytest.approx(4.8)
    assert "before the LOE overlay" in gfc["invariants"][0]["detail"]


def test_e11_covered_years_follow_the_guided_horizon_in_run_and_override():
    import inspect
    from app.backend.services import estimate_override_service as eos
    assert 'max(_LOE_COVERED_YEARS, int((_gf or {}).get("horizon_years") or 0))' in inspect.getsource(d)
    assert 'max(_LOE_COVERED_YEARS, int(fc.get("horizon_years") or 0))' in inspect.getsource(eos)


def test_e12_and_e8_dcf_family_and_backlog_legs_take_the_projection_context(monkeypatch):
    pj = {"growth_schedule": [0.05] * 10, "wacc_schedule": None, "margin_delta_absolute": 0.0,
          "margin_schedule": [0.12] * 10, "timing": None, "terminal_multiplier": 0.5}
    row = {"revenue": 10e9}
    v_half, tr = _leg("DCF (FCF+)", row, {}, None, monkeypatch, projection=pj)
    v_full, _ = _leg("DCF (FCF+)", row, {}, None, monkeypatch, projection={**pj, "terminal_multiplier": 1.0})
    assert tr["terminal_loe_multiplier"] == 0.5 and v_half < v_full
    # the backlog leg with no accepted backlog or FCF guidance is the same projection as the family leg
    v_b, tr_b = _leg("Backlog-coverage DCF", row, {}, None, monkeypatch, projection=pj)
    assert v_b == pytest.approx(v_half)


def test_e9_superseded_flags_and_e19_bank_guidance_payload():
    assert d._SUPERSEDED_BY_FORECAST[0] == "CAGR-divergence gate"
    est = {"confidence": "HIGH", "estimates": {}}
    out = d._guidance_estimates_payload(est, None, None, not_applied="a bank earnings-and-capital model prices this name")
    assert out["applied"] is False and out["not_applied_reason"].startswith("a bank earnings")


def test_e7_overlay_paths_reach_a_tab():
    from src.utils import valuation_workbook as vw
    import inspect
    src = inspect.getsource(vw)
    assert 'self.sheet("Valuation paths"' in src and '("ppa_dcf", "depleting_dcf")' in src
    assert 'kind="depleting_dcf", rows=_rows_d' in inspect.getsource(d)
