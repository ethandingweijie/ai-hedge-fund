"""Owner, 2026-11-27 (China Mobile trace, option A): peer EV multiples are restated onto the valued company's own
bridge. FMP's EV nets cash only; a subject whose bridge also counts its short- and long-term investments as cash was
priced on cash-only peer multiples (China Mobile's EV/EBITDA leg at 2.3x spot)."""
import pytest

from src.data import regional_comps as rc
from src.agents.analysis import dcf_agent as d


def _member(ev_ebitda, x_sti, x_inv, pe=10.0):
    return {"ev_ebitda": {"value": ev_ebitda, "in_band": True},
            "ev_ebitda_ntm": {"value": ev_ebitda * 0.9, "in_band": True},
            "pe": {"value": pe, "in_band": True},
            "_ev_x_sti": {"value": x_sti, "in_band": False},
            "_ev_x_inv": {"value": x_inv, "in_band": False}}


def test_no_basis_leaves_feed_values():
    m = _member(7.2, 0.05, 0.18)
    assert rc.on_subject_ev_basis(m) is m


def test_restates_ev_fields_only():
    m = _member(7.2, 0.05, 0.18)
    s = rc.on_subject_ev_basis(m, "sti")
    assert s["ev_ebitda"]["value"] == pytest.approx(7.2 * 0.95)
    assert s["ev_ebitda"]["ev_basis"] == "sti" and s["ev_ebitda"]["feed_value"] == 7.2
    i = rc.on_subject_ev_basis(m, "sti_lti")
    assert i["ev_ebitda"]["value"] == pytest.approx(7.2 * 0.82)
    assert i["ev_ebitda_ntm"]["value"] == pytest.approx(7.2 * 0.9 * 0.82)
    assert i["pe"] == m["pe"]                      # price multiples have no EV in them


def test_investments_above_ev_give_no_reading():
    i = rc.on_subject_ev_basis(_member(3.7, 0.17, 1.10), "sti_lti")
    assert i["ev_ebitda"]["in_band"] is False


def test_member_without_shares_unchanged():
    m = {"ev_ebitda": {"value": 7.2, "in_band": True}}
    assert rc.on_subject_ev_basis(m, "sti_lti") is m


def test_peer_netting_rules_follow_the_subject_rules():
    assert rc.investment_ev_shares("0941.HK", "Communication Services", "Telecommunications Services", 0.17, 0.41) \
        == (0.17, pytest.approx(0.58))
    assert rc.investment_ev_shares("0005.HK", "Financial Services", "Banks - Diversified", 0.3, 0.5) == (0.0, 0.0)
    assert rc.investment_ev_shares("0016.HK", "Real Estate", "Real Estate - Development", 0.02, 0.2) == (0.02, 0.02)
    assert rc.investment_ev_shares("PFE", "Healthcare", "Drug Manufacturers - General", 0.03, 0.05) == (0.03, 0.03)
    assert rc.investment_ev_shares("VRTX", "Healthcare", "Biotechnology", 0.03, 0.05) == (0.03, pytest.approx(0.08))
    assert rc.investment_ev_shares("UNH", "Healthcare", "Medical - Healthcare Plans", 0.1, 0.2) == (0.0, 0.0)
    assert rc.investment_ev_shares("MELI", "Consumer Cyclical", "Specialty Retail", 0.1, 0.2) == (0.0, 0.0)
    assert rc.investment_ev_shares("X", "Technology", "Software", None, None) == (None, None)


def test_ah_restated_ev_carries_the_shares():
    vals = {}
    rc._store_investment_shares(vals, "0728.HK", "Communication Services", "Telecommunications Services",
                                {"ev_x_sti": 0.054, "ev_x_lti": 0.133, "_ev_scale": 0.7})
    assert vals["_ev_x_sti"]["value"] == pytest.approx(0.054 / 0.7)
    assert vals["_ev_x_inv"]["value"] == pytest.approx(0.187 / 0.7)
    assert vals["_ev_x_inv"]["in_band"] is False


def test_subject_basis():
    assert d._subject_peer_ev_basis("Communication Services", "00941.HK", {}, True) == "sti_lti"
    assert d._subject_peer_ev_basis("Tech", "AAPL", {}, False) == "sti"
    assert d._subject_peer_ev_basis("Financials", "D05.SI", {}, False) is None
    assert d._subject_peer_ev_basis("Consumer", "MELI", {}, False) is None
    assert d._subject_peer_ev_basis("Healthcare", "MOH", {"regulated_cash_excluded": True}, False) is None


def test_resolver_retakes_median_on_the_subject_basis(monkeypatch):
    # five telecom peers on the cash-only feed basis, median 7.23; restated net of investments
    peers = [("0728.HK", 7.23, 0.054, 0.187), ("0788.HK", 3.94, 0.0, 0.02), ("6823.HK", 9.80, 0.0, 0.01),
             ("0008.HK", 7.84, 0.0, 0.02), ("0552.HK", 4.12, 0.30, 0.45)]
    mem = [{"symbol": s, "name": s, "market_cap": 1e11, "metrics": _member(v, xs, xi)} for s, v, xs, xi in peers]
    monkeypatch.setattr(rc, "load_comps", lambda *a, **k: {"ev_ebitda": {"value": 7.23, "peer_count": 5,
                                                                          "min_market_cap": 0.0}})
    monkeypatch.setattr(rc, "load_members", lambda *a, **k: [dict(m, metrics=rc.on_subject_ev_basis(m["metrics"]))
                                                             for m in mem])
    feed = rc.get_regional_multiples("HKSE", "Telecommunications Services", None, exclude_symbol="0941.HK")
    assert feed["ev_ebitda"]["value"] == pytest.approx(7.23)
    tok = rc.PEER_EV_BASIS.set("sti_lti")
    try:
        net = rc.get_regional_multiples("HKSE", "Telecommunications Services", None, exclude_symbol="0941.HK")
    finally:
        rc.PEER_EV_BASIS.reset(tok)
    vals = sorted(v * (1 - xi) for _, v, _, xi in peers)
    assert net["ev_ebitda"]["value"] == pytest.approx(vals[2], rel=1e-3)
    assert net["ev_ebitda"]["ev_basis"] == rc.EV_BASIS_LABEL["sti_lti"]
    assert net["pe"]["value"] == pytest.approx(10.0) if "pe" in net else True


def test_ah_restate_applies_to_every_basket_row_of_a_member():
    # China Telecom: FMP key metrics on the A-share cap (HK$690bn) against the H-share screener cap (HK$503bn)
    mm = {"mcap_multiples": 690e9, "ev_multiples": 692.7e9, "ev_ebitda": 6.74, "pe": 22.7}
    m = {"symbol": "0728.HK", "name": "China Telecom", "market_cap": 503e9,
         "sector": "Communication Services", "industry": "Telecommunications Services"}
    rows = rc.compute_members({"Telecommunications Services": [m]}, {"0728.HK": mm}, "industry")
    rows += rc.compute_members({"Communication Services": [m]}, {"0728.HK": mm}, "sector")
    assert len(rows) >= 2
    vals = {round(r["metrics"]["ev_ebitda"]["value"], 4) for r in rows}
    assert len(vals) == 1 and vals.pop() < 6.0
    assert all("_listing_restated" in r["metrics"] for r in rows)


def test_lease_principal_proxy_ifrs_only():
    bs = {"reportedCurrency": "SGD", "capitalLeaseObligations": 500.0}
    assert rc.lease_principal_proxy("C6L.SI", bs) == pytest.approx(100.0)
    assert rc.lease_principal_proxy("AAPL", {"reportedCurrency": "USD", "capitalLeaseObligations": 500.0}) == 0.0
    assert d._lease_principal_proxy({"lease_liabilities": 500.0}, "C6L.SI", "SGD") == pytest.approx(100.0)
    assert d._lease_principal_proxy({"lease_liabilities": 500.0}, "AAPL", "USD") == 0.0


def test_peer_fcf_yield_after_lease_principal_inside_a_valuation_only():
    m = {"fcf_yield": {"value": 0.08, "in_band": True}, "_fcf_lease_x": {"value": 0.02, "in_band": False}}
    assert rc.on_subject_ev_basis(m) is m                     # outside a valuation: the feed's yield
    s = rc.on_subject_ev_basis(m, "sti")
    assert s["fcf_yield"]["value"] == pytest.approx(0.06) and s["fcf_yield"]["feed_value"] == 0.08


def test_lease_share_follows_the_listing_restate():
    vals = {"_listing_restated": {"value": 0.73, "in_band": False}}
    rc._store_investment_shares(vals, "0728.HK", "Communication Services", "Telecommunications Services",
                                {"fcf_lease_x": 0.0073})
    assert vals["_fcf_lease_x"]["value"] == pytest.approx(0.01)
