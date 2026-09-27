"""Wave 8, real estate (owner decisions 2026-09-27: all seven; decision 1 amended to keep every name,
decision 3 = the live cap rate and the nav kind; the owner's equity directory as pins).

Stage 0 was 7 of 12 within consensus with four causes: nothing routed, the REIT legs read a static
April table through a keyword classifier, developers were capitalised as rent, and CapitaLand Investment
had no fund-manager input. This file pins the build against each.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.data import industry_inputs as ii
from src.data import industry_profile_map as ipm
from src.data import report_families as rf
from src.data import sector_profiles as sp
from src.agents.industry import gemini_params as gp

P = sp.INDUSTRY_VALUATION_PROFILES
REIT_LABELS = ["REIT - Retail", "REIT - Industrial", "REIT - Office", "REIT - Residential", "REIT - Diversified",
               "REIT - Healthcare Facilities", "REIT - Specialty", "REIT - Hotel & Motel"]


# ── routing (decisions 1, 5, 6) ───────────────────────────────────────────────

def test_the_twelve_labels_route_and_mortgage_reits_stay_out():
    scope = ipm.routing_scope()
    for lab in REIT_LABELS:
        assert lab in scope and ipm.profile_for_industry(lab) == ("RealEstate", "REIT"), lab
    assert ipm.profile_for_industry("Real Estate - Development") == ("Property", "Homebuilder / Land Developer")
    assert ipm.profile_for_industry("Real Estate - Diversified") == ("RealEstate", "REIT")
    assert ipm.profile_for_industry("Real Estate - Services") == ("Property", "Real Estate Services")
    assert ipm.profile_for_industry("Residential Construction") == ("Property", "Homebuilder / Land Developer")
    assert "REIT - Mortgage" not in scope and ipm.profile_for_industry("REIT - Mortgage") is None
    hk = ipm.market_map("HK")
    assert hk["Real Estate - Development"] == ("Property", "Property Developer (HK / China)")
    assert hk["Real Estate - Diversified"] == ("Property", "Landlord / Investment Property (HK)")
    assert hk["Real Estate - Services"] == ("Property", "Real Estate Services")
    assert hk["REIT - Retail"] == ("RealEstate", "REIT")
    assert ipm.profile_for_ticker("U06.SI", "Real Estate - Diversified") == ("Property", "Property Developer (SG)")   # the SG row applies now
    assert ipm.profile_for_ticker("01997.HK", "Real Estate - Services") == ("Property", "Landlord / Investment Property (HK)")
    assert ipm.comps_exchange_for("H78.SI") == "HKSE"                   # Hongkong Land prices on the HK landlord cohort (store key HKSE, as S68.SI -> US)


DIRECTORY = {   # the owner's categories, 2026-09-27
    ("RealEstate", "REIT"): ["PLD", "REXR", "FR", "STAG", "O", "SPG", "KIM", "FRT", "ADC", "NNN", "AVB", "EQR", "ESS", "CPT", "MAA",
                             "INVH", "AMH", "WELL", "VTR", "HR", "OHI", "EQIX", "DLR", "PSA", "EXR", "CUBE", "ARE", "BXP", "KRC", "HIW",
                             "VICI", "GLPI", "AMT", "CCI", "SBAC", "IRM",
                             "00823.HK", "02778.HK", "00435.HK", "00808.HK", "00778.HK", "00405.HK", "87001.HK", "01426.HK", "01503.HK"],
    ("Property", "Landlord / Investment Property (HK)"): ["00016.HK", "01113.HK", "01997.HK", "01972.HK", "00012.HK", "00101.HK", "00014.HK", "00017.HK", "00004.HK", "00083.HK"],
    ("Property", "Property Developer (HK / China)"): ["01109.HK", "00688.HK", "00123.HK", "00960.HK", "01908.HK", "03900.HK", "02202.HK", "01030.HK"],
    ("Property", "Homebuilder / Land Developer"): ["DHI", "LEN", "NVR", "PHM", "TOL", "MTH", "KBH", "TMHC"],
    ("Property", "Real Estate Services"): ["CBRE", "JLL", "CWK", "CIGI", "BEKE", "HOUS", "COMP", "Z", "ZG", "CSGP", "01209.HK", "02669.HK", "06049.HK", "02423.HK"],
}


@pytest.mark.parametrize("target,tickers", list(DIRECTORY.items()))
def test_the_directory_pins_resolve(target, tickers):
    assert [tuple(sp.get_wacc_profile_for_ticker(t)[:2]) for t in tickers] == [target] * len(tickers)
    assert target[1] in P[target[0]]


def test_the_singapore_rows_from_the_directory():
    L = sp.SGX_TICKER_SECTOR_LOOKUP
    for t in ("F17.SI", "B61.SI", "H13.SI", "TQ5.SI", "U06.SI", "C09.SI", "U14.SI"):
        assert L[t][:2] == ("Property", "Property Developer (SG)"), t
    assert L["H78.SI"][:2] == ("Property", "Landlord / Investment Property (HK)")
    assert L["9CI.SI"][:2] == ("Financials", "Real Estate Asset Manager (SG)")           # decision 7: the alt_manager input, not a new profile
    for t, sub_ in (("O5RU.SI", "Industrial"), ("DCRU.SI", "DataCentre"), ("C2PU.SI", "Healthcare"), ("AW9U.SI", "Healthcare"),
                    ("J85.SI", "Hospitality"), ("MXNU.SI", "European"), ("CMOU.SI", "US Office"), ("CWBU.SI", "European")):
        assert L[t][0] == "REIT" and L[t][1] == sub_, t


# ── the four profiles (decision 5) ────────────────────────────────────────────

def _w(sector, name):
    return {m["name"]: m["weight"] for m in P[sector][name]["methods"]}


def test_the_new_profiles_are_the_owner_spec():
    assert _w("Property", "Property Developer (HK / China)") == {"P/BV": 0.40, "RNAV (published)": 0.25, "Forward P/E": 0.20, "DDM": 0.15}
    assert P["Property"]["Property Developer (HK / China)"]["leg_fallback"] == {"RNAV (published)": ["P/BV"]}
    assert _w("Property", "Landlord / Investment Property (HK)") == {"NAV (Cap Rates)": 0.40, "P/BV": 0.30, "DDM": 0.30}
    assert _w("Property", "Homebuilder / Land Developer") == {"P/BV": 0.35, "Forward P/E": 0.35, "EV/EBITDA": 0.20, "FCF Yield": 0.10}
    assert _w("Property", "Real Estate Services") == {"Forward P/E": 0.40, "EV/EBITDA": 0.30, "DCF": 0.20, "FCF Yield": 0.10}
    for name in ("Property Developer (HK / China)", "Landlord / Investment Property (HK)", "Homebuilder / Land Developer", "Real Estate Services"):
        assert sum(_w("Property", name).values()) == pytest.approx(1.0)
        assert name in sp.SECTOR_PEER_MULTIPLES and name in sp.SECTOR_PEER_BASKETS
        assert rf.report_family_for(name) == "Property, REITs and holdcos"
    # statics read off the store: the HK developer cohort's 0.40x book is the anchor's discount
    assert sp.SECTOR_PEER_MULTIPLES["Property Developer (HK / China)"]["pb"] == pytest.approx(0.40, abs=0.02)
    assert sp.SECTOR_PEER_MULTIPLES["Homebuilder / Land Developer"]["pb"] == pytest.approx(1.18, abs=0.05)


# ── the REIT sub-type by label and pin (decision 2) and the table (decision 4) ─

def test_sub_type_comes_from_the_pin_then_the_label_then_the_keywords():
    assert d._classify_reit_subtype("AMT", "American Tower REIT — telecoms towers", industry="REIT - Specialty") == "tower"
    assert d._classify_reit_subtype("CCI", "", industry="REIT - Specialty") == "tower"
    assert d._classify_reit_subtype("EQIX", "equinix interconnection", industry="REIT - Specialty") == "data_center"   # the premium tier is gone
    assert d._classify_reit_subtype("IRM", "", industry="REIT - Specialty") == "specialty"
    assert d._classify_reit_subtype("WELL", "", industry="REIT - Healthcare Facilities") == "healthcare"
    assert d._classify_reit_subtype("PSA", "", industry="REIT - Industrial") == "self_storage"
    assert d._classify_reit_subtype("O", "", industry="REIT - Retail") == "net_lease"
    assert d._classify_reit_subtype("VICI", "", industry="REIT - Diversified") == "net_lease"
    assert d._classify_reit_subtype("XYZ", "", industry="REIT - Office") == "office"
    assert d._classify_reit_subtype("XYZ", "some tower office", industry=None) == "office"      # keyword fallback unchanged
    t = d._REIT_SUBTYPE_MULTIPLES
    assert (t["healthcare"]["cap_rate"], t["retail"]["cap_rate"], t["office"]["cap_rate"], t["residential"]["cap_rate"],
            t["hospitality"]["cap_rate"], t["tower"]["cap_rate"], t["specialty"]["cap_rate"], t["default"]["cap_rate"]) == \
           (0.051, 0.069, 0.065, 0.064, 0.091, 0.051, 0.057, 0.066)
    assert t["data_center_premium"] == t["data_center"]
    assert "tower" in d._REIT_MAINT_CAPEX_PCT and "specialty" in d._REIT_MAINT_CAPEX_PCT


# ── the live cap rate and the accepted NAV (decision 3, both) ─────────────────

def test_the_live_cap_rate_reads_only_a_real_estate_cohort():
    peer = {"ev_ebitda": 19.78, "_comp_basis": {"ev_ebitda": {"basis": "industry", "key": "REIT - Healthcare Facilities", "cohort": "large", "peer_count": 8}}}
    cap, basis = d._live_reit_cap_rate(peer)
    assert cap == pytest.approx(1 / 19.78) and "live REIT - Healthcare Facilities" in basis
    assert d._live_reit_cap_rate({"ev_ebitda": 14.3, "_comp_basis": {"ev_ebitda": {"basis": "sector", "key": "Real Estate"}}}) is None
    assert d._live_reit_cap_rate({"ev_ebitda": 16.4, "_comp_basis": {"ev_ebitda": {"basis": "static", "cohort": "US"}}}) is None
    assert d._live_reit_cap_rate({"ev_ebitda": 30.3, "_comp_basis": {"ev_ebitda": {"basis": "industry", "key": "Software - Infrastructure"}}}) is None
    assert d._live_reit_cap_rate({}) is None
    src = inspect.getsource(d._compute_method_value)
    for lit in ('"research cap_rate_market"', '"owner-accepted nav input"', "_live_reit_cap_rate(peer)", 'f"sub-type table ({reit_subtype})"',
                'most_recent.get("nav_per_share_published")', 'if method_name in {"RNAV (published)"}:'):
        assert lit in src, lit


def test_the_nav_kind_is_registered_like_the_wave_six_kinds():
    assert "nav" in ii.KINDS and "nav" in ii.NO_OVERLAY and ii.BOUNDS["nav"] == ("market_cap", 0.3, 5.0)
    assert gp.INDUSTRY_INPUT_SCHEMAS["nav"] is gp.NavInputs
    out = gp.nav_to_engine({"fiscal_year": "FY2025", "basis": "RNAV",
                            "nav_per_share": {"value": 120.0, "currency": "HKD", "scale": "units", "source_url": "https://example.com/ar2025.pdf", "quote": "NAV per share HK$120"},
                            "cap_rate": {"value": 5.2}}, lambda ccy: 1.0)
    assert out["nav_per_share"] == 120.0 and out["cap_rate"] == pytest.approx(0.052) and out["basis"] == "RNAV"
    assert "nav_to_engine(" in inspect.getsource(d.run_dcf_agent)
    checks = ii.reconcile("nav", 5.0e9, {"market_cap": 10.0e9, "period": "2025-12-31"}, period="FY2025")
    assert all(c["ok"] is not False for c in checks), checks


# ── the distress gate (decision 5, PROPOSED rule accepted) ────────────────────

def test_the_distress_rule_fires_on_vanke_and_longfor_and_not_on_a_going_concern():
    vanke = d._distressed_developer([{"net_income": 12.2e9}, {"net_income": -49.5e9}, {"net_income": -88.6e9}],
                                    {"total_equity": 116.9e9, "total_debt": 209.2e9, "cash_and_equivalents": 67.2e9})
    longfor = d._distressed_developer([{"net_income": 12.9e9}, {"net_income": 10.4e9}, {"net_income": 1.0e9}],
                                      {"total_equity": 162.9e9, "total_debt": 166.0e9, "cash_and_equivalents": 29.2e9})
    healthy = d._distressed_developer([{"net_income": 12e9}, {"net_income": 11e9}, {"net_income": 10e9}],
                                      {"total_equity": 100e9, "total_debt": 50e9, "cash_and_equivalents": 20e9})
    assert vanke["fired"] and "two consecutive loss years" in vanke["reason"]
    assert longfor["fired"] and "92%" in longfor["reason"] and longfor["net_debt_to_equity"] == pytest.approx(0.84, abs=0.01)
    assert not healthy["fired"]
    # the profit-collapse arm needs the leverage too: the same fall with net cash does not fire
    assert not d._distressed_developer([{"net_income": 12.9e9}, {"net_income": 10.4e9}, {"net_income": 1.0e9}],
                                       {"total_equity": 162.9e9, "total_debt": 20e9, "cash_and_equivalents": 29.2e9})["fired"]
    src = inspect.getsource(d.run_dcf_agent)
    assert '"gate_id": "GATE_DISTRESSED_DEVELOPER"' in src and '_regime_flag = "Distressed_Developer"' in src
    assert 'if method_name == "P/BV" and most_recent.get("_distressed_developer"):' in inspect.getsource(d._compute_method_value)
