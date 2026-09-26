"""Wave 4, consumer staples (owner universe and decisions, 2026-09-26).

Stage 0 missed because industry routing was switched off for every staples
label: 20 of 23 names fell to the ratio ladder (Coca-Cola as Luxury Goods,
Mondelez as Mature SaaS). This file pins the Stage 4 fix: the labels in
scope, the rows, the pooled families, the three new profiles, the revised
anchors, the COST pin that ships with the Discount Stores row, and the WACC
rows the Consumer table did not have.
"""
import pytest

from src.agents.analysis import dcf_agent as d
from src.data import industry_profile_map as ipm
from src.data import regional_comps as rc
from src.data import sector_profiles as sp

P = sp.INDUSTRY_VALUATION_PROFILES["Consumer"]
LABELS = {
    "Agricultural Farm Products": "Agribusiness & Food Processing",
    "Beverages - Non-Alcoholic": "Food & Beverage",
    "Beverages - Alcoholic": "Food & Beverage",
    "Beverages - Wineries & Distilleries": "Food & Beverage",
    "Food Confectioners": "Food & Beverage",
    "Packaged Foods": "Food & Beverage",
    "Discount Stores": "Grocery & Discount Retail",
    "Grocery Stores": "Grocery & Discount Retail",
    "Food Distribution": "Grocery & Discount Retail",
    "Household & Personal Products": "Household / Personal",
    "Tobacco": "Tobacco",
}


def test_every_staples_label_is_in_scope_and_routes_to_a_consumer_profile():
    scope = ipm.routing_scope()
    for label, profile in LABELS.items():
        assert label in scope, label
        assert ipm.profile_for_industry(label) == ("Consumer", profile), label
    assert "Department Stores" not in scope                       # Sun Art cut; no row


def test_sg_keeps_its_own_agribusiness_row_by_market():
    assert ipm.market_map("SG")["Agricultural Farm Products"] == ("Consumer", "Agribusiness & Food (SG)")
    assert sp.get_wacc_profile_for_ticker("F34.SI") == ("Consumer", "Agribusiness & Food (SG)")


def test_the_thin_labels_pool_into_families():
    assert rc.family_of("Beverages - Wineries & Distilleries") == "Food & Beverage (family)"
    assert rc.family_of("Food Confectioners") == "Food & Beverage (family)"
    assert rc.family_of("Discount Stores") == rc.family_of("Grocery Stores") == "Grocery & Distribution (family)"
    assert rc.family_of("Household & Personal Products") is None


@pytest.mark.parametrize("name,expected", [
    ("Food & Beverage", {"Forward P/E": 0.35, "EV/EBITDA": 0.25, "DCF": 0.25, "FCF Yield": 0.15}),
    ("Agribusiness & Food Processing", {"EV/EBITDA (norm)": 0.35, "P/E (norm)": 0.25, "DCF": 0.25, "P/BV": 0.15}),
    ("Grocery & Discount Retail", {"EV/EBITDAR": 0.35, "Forward P/E": 0.25, "FCF Yield": 0.20, "DCF": 0.20}),
    ("Tobacco", {"FCF Yield": 0.35, "Forward P/E": 0.25, "DDM": 0.20, "EV/EBITDA": 0.20}),
    ("Household / Personal", {"Forward P/E": 0.40, "EV/EBITDA": 0.30, "DCF": 0.20, "ROIC": 0.10}),
])
def test_the_method_tables_are_the_proposal(name, expected):
    ms = P[name]["methods"]
    assert {m["name"]: m["weight"] for m in ms} == expected
    assert sum(m["weight"] for m in ms) == pytest.approx(1.0)
    anchors = [m["name"] for m in ms if m.get("anchor")]
    assert len(anchors) == 1 and anchors[0] == next(iter(expected)), anchors
    assert all(m.get("implementable") is True for m in ms), name


def test_agribusiness_is_cyclical_and_convergent_and_no_other_staples_profile_is():
    assert "Agribusiness & Food Processing" in d._CYCLICAL_PROFILES
    assert "Agribusiness & Food Processing" in d._CONVERGENCE_ALPHA_PROFILES
    for n in ("Food & Beverage", "Grocery & Discount Retail", "Tobacco", "Household / Personal"):
        assert n not in d._CYCLICAL_PROFILES, n


def test_the_pins_the_owner_decided():
    assert sp.get_wacc_profile_for_ticker("COST") == ("Consumer", "Membership / Subscription Retail")
    assert sp.get_wacc_profile_for_ticker("WMT") == ("Consumer", "Grocery & Discount Retail")
    assert sp.get_wacc_profile_for_ticker("EL") == ("Consumer", "Luxury Goods")     # owner: brand economics
    # the empty-profile pins leave the rows in charge
    for t in ("KO", "PEP", "PG"):
        assert sp.TICKER_SECTOR_LOOKUP[t][1] == ""


def test_the_consumer_wacc_rows_exist_and_bracket_the_sector_default():
    rows = sp._PROFILE_WACC["Consumer"]
    assert rows["Tobacco"] == rows["Agribusiness & Food Processing"] == 0.0825
    assert rows["Food & Beverage"] == rows["Household / Personal"] == 0.070
    assert rows["Grocery & Discount Retail"] == 0.0725
    assert set(rows) == {"Food & Beverage", "Household / Personal", "Grocery & Discount Retail",
                         "Agribusiness & Food Processing", "Tobacco"}


def test_the_statics_carry_the_ntm_reference_read_that_day():
    s = sp.SECTOR_PEER_MULTIPLES
    assert s["Tobacco"]["pe_ntm"] == 11.9 and s["Tobacco"]["ev_ebitda"] == 11.7
    assert s["Agribusiness & Food Processing"]["pe_ntm"] == 11.8 and s["Agribusiness & Food Processing"]["ev_ebitda"] == 9.5
    assert s["Grocery & Discount Retail"]["ev_ebitda_ntm"] == 9.8
