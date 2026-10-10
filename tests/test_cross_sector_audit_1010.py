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
