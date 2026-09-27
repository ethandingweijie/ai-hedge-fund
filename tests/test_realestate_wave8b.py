"""Wave 8b, the real-estate remediation in the owner's order B -> C -> A -> D (decisions of 2026-09-27).

Each step is pinned as it lands. Step 1: clean cash NOI (revenue less cost of revenue) on every cap-rate
NAV, statutory EBITDA only as the fallback, the basis on the trace.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d


# ── step 1 (B): clean cash NOI ────────────────────────────────────────────────

def test_clean_noi_is_revenue_less_cost_of_revenue_with_ebitda_as_the_fallback():
    # Swire-shaped: a revaluation loss drives EBITDA and net income negative; the property NOI is positive
    m = d._compute_reit_metrics({"revenue": 16.0e9, "cost_of_revenue": 6.0e9, "ebitda": 1.6e9, "net_income": -1.5e9,
                                 "depreciation_and_amortization": 0.6e9}, subtype="default")
    assert m["noi"] == pytest.approx(10.0e9) and m["noi_basis"].startswith("clean NOI")
    # a negative cost of revenue sign convention is taken at absolute value
    assert d._compute_reit_metrics({"revenue": 100.0, "cost_of_revenue": -30.0, "ebitda": 5.0}, subtype="retail")["noi"] == pytest.approx(70.0)
    # no cost of revenue: EBITDA, and the basis says so
    m2 = d._compute_reit_metrics({"revenue": 100.0, "cost_of_revenue": None, "ebitda": 60.0}, subtype="retail")
    assert m2["noi"] == 60.0 and "fallback: cost of revenue missing" in m2["noi_basis"]
    # cost of revenue above revenue and no positive EBITDA: nothing, stated
    m3 = d._compute_reit_metrics({"revenue": 100.0, "cost_of_revenue": 120.0, "ebitda": None}, subtype="retail")
    assert m3["noi"] is None and m3["noi_basis"].startswith("none")
    # FFO is untouched by the step (decision B covered NOI): net income plus D&A
    assert m["ffo"] == pytest.approx(-0.9e9)


def test_the_nav_leg_trace_names_the_noi_basis_and_the_bridge():
    src = inspect.getsource(d._compute_method_value)
    for lit in ('noi_basis=_reit_pre.get("noi_basis")', "gross_asset_value=", 'total_debt=most_recent.get("total_debt")'):
        assert lit in src, lit
    assert "[{_reit_m.get('noi_basis')}]" in inspect.getsource(d.run_dcf_agent)


# ── step 2 (C): a published NAV read at the cohort's trailing four-quarter P/NAV ──────────────

def test_a_published_nav_is_read_at_the_cohort_p_nav_on_fair_value_exchanges_and_at_par_on_us_gaap():
    hk = {"pb": 0.295, "_comp_basis": {"pb": {"basis": "industry", "key": "Real Estate - Diversified", "exchange": "HKSE", "cohort": "all"}}}
    us = {"pb": 1.82, "_comp_basis": {"pb": {"basis": "industry", "key": "REIT - Healthcare Facilities", "exchange": "US"}}}
    sector_rung = {"pb": 0.5, "_comp_basis": {"pb": {"basis": "sector", "key": "Real Estate", "exchange": "HKSE"}}}
    cal = d._cohort_p_nav_4q(hk)
    assert cal and cal["exchange"] == "HKSE" and 0.2 < cal["median"] < 0.5 and cal["points"] >= 0
    assert d._cohort_p_nav_4q(us) is None                       # US GAAP book is historic cost: P/B is not P/NAV
    assert d._cohort_p_nav_4q(sector_rung) is None              # a whole-sector rung is not a cohort P/NAV
    assert d._P_NAV_WINDOW_DAYS == 365 and d._P_NAV_CALIBRATED_EXCHANGES == ("HKSE", "SES")
    mr = {}
    v = d._calibrated_published_nav(mr, hk, 100.0, "NAV (Cap Rates)")
    assert v == pytest.approx(100.0 * cal["median"])
    rec = mr["_nav_calibration"]
    assert rec["raw_published_nav"] == 100.0 and rec["cohort_p_nav_median_4q"] == cal["median"] and rec["calibrated_nav"] == v
    mr2 = {}
    assert d._calibrated_published_nav(mr2, us, 87.0, "NAV (Cap Rates)") == 87.0 and mr2["_nav_calibration"]["cohort_p_nav_median_4q"] is None
    src = inspect.getsource(d._compute_method_value)
    assert 'return _calibrated_published_nav(most_recent, peer, float(_pub), "NAV (Cap Rates)")' in src
    assert 'return _calibrated_published_nav(most_recent, peer, float(_rn), "RNAV (published)")' in src


# ── step 3 (A): the FFO field, the live P/FFO leg, the REIT (Specialty / OpCo) profile ────────

def test_the_ffo_field_and_the_opco_profile_are_the_owner_spec():
    from src.data import regional_comps as rc
    from src.data import report_families as rf
    from src.data import sector_profiles as sp
    assert "p_ffo" in rc.FIELDS and rc._BANDS["p_ffo"] == (3.0, 120.0)
    assert rc._FFO_LABEL_PREFIXES == ("REIT",)
    assert rc.fetch_p_ffo("X", None) is None                          # no market cap, no multiple, no call
    assert "if symbol in _FFO_SYMBOLS and _km_row:" in inspect.getsource(rc.fetch_name_multiples)
    P = sp.INDUSTRY_VALUATION_PROFILES["RealEstate"]
    assert {m["name"]: m["weight"] for m in P["REIT"]["methods"]} == {"NAV (Cap Rates)": 0.6, "P/FFO": 0.4}
    assert {m["name"]: m["weight"] for m in P["REIT (Specialty / OpCo)"]["methods"]} == {"P/FFO": 0.4, "Forward EV/EBITDA": 0.3, "NAV (Cap Rates)": 0.3}
    assert rc.PROFILE_PEER_BASKETS["REIT (Specialty / OpCo)"]["US"] == ("WELL", "VTR", "IRM", "EQIX", "DLR", "AMT", "CCI", "SBAC")
    assert [sp.get_wacc_profile_for_ticker(t)[1] for t in ("WELL", "VTR", "IRM", "EQIX", "DLR", "AMT", "CCI", "SBAC")] == ["REIT (Specialty / OpCo)"] * 8
    assert sp.get_wacc_profile_for_ticker("PLD")[1] == "REIT" and sp.get_wacc_profile_for_ticker("PSA")[1] == "REIT"
    assert rf.report_family_for("REIT (Specialty / OpCo)") == "Property, REITs and holdcos"
    src = inspect.getsource(d._compute_method_value)
    assert '_live_pffo = peer.get("p_ffo") if _basket_rank(peer, "p_ffo") >= 1 else None' in src
    assert "mult = _pffo_base * sm * growth_premium" in src
    # the sub-type cap rate still comes from the pins for the OpCo names
    assert d._classify_reit_subtype("AMT", "", industry="REIT - Specialty") == "tower"


# ── step 4 (D): developer sub-cohorts with the contagion rule ────────────────────────────────

def test_the_developer_sub_cohorts_price_on_their_own_median_with_loss_makers_excluded():
    from src.data import regional_comps as rc
    from src.data import sector_profiles as sp
    assert set(sp.DEVELOPER_SUBCOHORTS) == {"DEV_MAINLAND_SOE", "DEV_MAINLAND_PRIVATE", "DEV_HK_DIVERSIFIED"}
    assert sp.DEVELOPER_SUBCOHORT_OF["00688.HK"] == "DEV_MAINLAND_SOE" and sp.DEVELOPER_SUBCOHORT_OF["02202.HK"] == "DEV_MAINLAND_PRIVATE"
    assert sp.DEVELOPER_SUBCOHORT_OF["00016.HK"] == "DEV_HK_DIVERSIFIED" and "01810.HK" not in sp.DEVELOPER_SUBCOHORT_OF
    # the store keeps four-digit HK codes; the basket is written canonically and reported canonically
    r = rc.basket_multiples("HKSE", ("00688.HK", "01109.HK", "00123.HK", "01908.HK", "03900.HK"), "DEV_MAINLAND_SOE", exclude_loss_makers=True)
    if r:                                                             # the local store may be empty on a fresh clone
        pb = r["pb"]
        assert pb["basis"] == "profile" and pb["key"] == "DEV_MAINLAND_SOE" and pb["cohort"] == "all"
        assert all(m.startswith("0") and m.endswith(".HK") and len(m) == 8 for m in pb["members"])
        assert "excluded_loss_makers" in pb and not (set(pb["members"]) & set(pb["excluded_loss_makers"]))
    assert rc.basket_multiples("HKSE", (), "x") == {}
    # the A&D and OpCo baskets still resolve through the same function
    assert "return basket_multiples(exchange, tuple(syms), profile or \"\", max_age_days=max_age_days)" in inspect.getsource(rc.profile_basket_multiples)
    src = inspect.getsource(sp.get_sector_peer_multiples)
    assert "DEVELOPER_SUBCOHORT_OF.get((ticker or \"\").upper())" in src and "exclude_loss_makers=True" in src


def test_the_p_nav_read_follows_a_sub_cohort_basket_too():
    """Step 4's sub-cohorts carry a 'profile' basis with a key of their own; the step 2 read must take the
    basket's P/B (live, no history yet) rather than fall back to par."""
    cal = d._cohort_p_nav_4q({"pb": 0.402, "_comp_basis": {"pb": {"basis": "profile", "key": "DEV_HK_DIVERSIFIED", "exchange": "HKSE", "cohort": "all"}}})
    assert cal and cal["median"] == pytest.approx(0.402) and cal["points"] == 0 and "live P/B" in cal["window"]
    assert d._cohort_p_nav_4q({"pb": 7.2, "_comp_basis": {"pb": {"basis": "profile", "key": "REIT (Specialty / OpCo)", "exchange": "US"}}}) is None
