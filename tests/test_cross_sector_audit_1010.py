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
