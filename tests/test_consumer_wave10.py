"""Wave 10 (owner, 2026-09-27): the 18 labels whose routing row sat outside routing scope.

Decisions 1, 2, 3, 5, 6 and 7 of docs/wave10_proposal.md, and decision 4 with the under-floor
baskets populated to five names or more from the comps store ("suggest and populate").
"""
import json
from pathlib import Path

import pytest

from src.data import industry_profile_map as ipm
from src.data import regional_comps as rc
from src.data import sector_profiles as sp
from src.agents.analysis import dcf_agent as d

P = sp.INDUSTRY_VALUATION_PROFILES
SCOPE = ["Specialty Retail", "Apparel - Retail", "Apparel - Manufacturers", "Apparel - Footwear & Accessories",
         "Auto - Manufacturers", "Auto - Parts", "Restaurants", "Gambling, Resorts & Casinos", "Leisure", "Luxury Goods",
         "Travel Lodging", "Travel Services", "Furnishings, Fixtures & Appliances",
         "Drug Manufacturers - Specialty & Generic", "Medical - Pharmaceuticals", "Regulated Gas", "Renewable Utilities"]


def _w(sector, profile):
    return {m["name"]: m["weight"] for m in P[sector][profile]["methods"]}


def test_the_seventeen_labels_are_in_scope_and_the_dead_row_is_gone():
    raw = json.loads((Path(ipm.__file__).parent / "industry_profile_map.json").read_text(encoding="utf-8"))
    assert raw["version"] == 24
    assert set(SCOPE) <= set(raw["routing_scope"])
    assert "Other Industrial Metals & Mining" not in raw["map"]                     # decision 7


@pytest.mark.parametrize("ticker,label,want", [
    ("CMG", "Restaurants", ("Consumer", "Restaurants")),
    ("LVS", "Gambling, Resorts & Casinos", ("Consumer", "Casinos & Integrated Resorts")),
    ("MAR", "Travel Lodging", ("Consumer", "Lodging (Asset-Light)")),
    ("00045.HK", "Travel Lodging", ("Consumer", "Hotel Owner-Operator (HK)")),
    ("OU8.SI", "Travel Lodging", ("Property", "Specialised Accommodation (SG)")),    # SGX owners keep their SG profile
    ("EXPE", "Travel Services", ("Consumer", "Online Travel")),
    ("HMC", "Auto - Manufacturers", ("Industrials", "Automotive (OEM)")),
    ("MGA", "Auto - Parts", ("Consumer", "Auto Parts & Suppliers")),
    ("HAS", "Leisure", ("Consumer", "Leisure Products & Brands")),
    ("TEVA", "Drug Manufacturers - Specialty & Generic", ("Biopharma", "Specialty & Generic Pharma")),
    ("00874.HK", "Medical - Pharmaceuticals", ("Biopharma", "Specialty & Generic Pharma")),
    ("00003.HK", "Regulated Gas", ("Energy", "City Gas Distribution (HK / China)")),
    ("ATO", "Regulated Gas", ("Energy", "Regulated Utility")),
    ("CWEN", "Renewable Utilities", ("Energy", "IPP")),
    ("TJX", "Apparel - Retail", ("Consumer", "Traditional Retail")),
])
def test_rows_route_where_the_owner_decided(ticker, label, want):
    assert ipm.in_routing_scope(ticker, label)
    assert ipm.profile_for_ticker(ticker, label) == want


@pytest.mark.parametrize("ticker,profile", [
    ("MCD", "Restaurants"), ("SBUX", "Restaurants"), ("09987.HK", "Restaurants"), ("06862.HK", "Restaurants"),
    ("00027.HK", "Casinos & Integrated Resorts"), ("01928.HK", "Casinos & Integrated Resorts"),
    ("01179.HK", "Lodging (Asset-Light)"), ("BKNG", "Online Travel"), ("ABNB", "Online Travel"), ("09961.HK", "Online Travel"),
    ("RCL", "Cruise Lines"), ("CCL", "Cruise Lines"), ("NCLH", "Cruise Lines"), ("VIK", "Cruise Lines"),
    ("RACE", "Luxury Goods"), ("ZTS", "Specialty & Generic Pharma"), ("01276.HK", "Large Cap Pharma"),
    ("MELI", "Hyper-Growth Platform"), ("SE", "Hyper-Growth Platform"), ("GPC", "Industrial Distribution"),
    ("HLN", "Household / Personal"), ("CORT", "Commercial Biotech"), ("KNSA", "Commercial Biotech"),
    ("LQDA", "Commercial Biotech"), ("02331.HK", "Apparel / Athletic Wear"), ("06618.HK", "Pharma Distribution"),
])
def test_the_pins_ahead_of_scope(ticker, profile):
    assert sp.get_wacc_profile_for_ticker(ticker)[1] == profile


@pytest.mark.parametrize("key,weights,anchor", [
    (("Consumer", "Restaurants"), {"Forward EV/EBITDA": .45, "Forward P/E": .35, "FCF Yield": .20}, "Forward EV/EBITDA"),
    (("Consumer", "Casinos & Integrated Resorts"), {"Forward EV/EBITDA": .50, "EV/EBITDA (norm)": .30, "FCF Yield": .20}, "Forward EV/EBITDA"),
    (("Consumer", "Lodging (Asset-Light)"), {"Forward EV/EBITDA": .50, "Forward P/E": .30, "FCF Yield": .20}, "Forward EV/EBITDA"),
    (("Consumer", "Hotel Owner-Operator (HK)"), {"RNAV (published)": .50, "EV/EBITDA": .30, "DDM": .20}, "EV/EBITDA"),   # anchor flag on a computable leg
    (("Consumer", "Online Travel"), {"Forward P/E": .40, "Forward EV/EBITDA": .40, "FCF Yield": .20}, "Forward P/E"),
    (("Consumer", "Cruise Lines"), {"Forward EV/EBITDA": .50, "EV/EBITDA (norm)": .30, "Forward P/E": .20}, "Forward EV/EBITDA"),
    (("Consumer", "Auto Parts & Suppliers"), {"EV/EBITDA (norm)": .45, "Forward P/E": .35, "FCF Yield": .20}, "EV/EBITDA (norm)"),
    (("Consumer", "Leisure Products & Brands"), {"Forward P/E": .40, "Forward EV/EBITDA": .40, "FCF Yield": .20}, "Forward P/E"),
    (("Consumer", "Traditional Retail"), {"Forward EV/EBITDA": .40, "Forward P/E": .35, "FCF Yield": .25}, "Forward EV/EBITDA"),
    (("Energy", "City Gas Distribution (HK / China)"), {"Forward P/E": .40, "EV/EBITDA": .30, "DDM": .30}, "Forward P/E"),
    (("Biopharma", "Specialty & Generic Pharma"), {"Forward P/E": .40, "EV/EBITDA": .35, "FCF Yield": .25}, "Forward P/E"),
])
def test_the_profiles_are_the_owner_spec(key, weights, anchor):
    assert _w(*key) == pytest.approx(weights)
    assert next(m["name"] for m in P[key[0]][key[1]]["methods"] if m.get("anchor")) == anchor


def test_the_hotel_owners_price_on_book_until_a_published_nav_is_accepted():
    row = P["Consumer"]["Hotel Owner-Operator (HK)"]["methods"][0]
    assert row["name"] == "RNAV (published)" and row["implementable"] is False and row["proxy"] == "P/BV"
    assert "RNAV (published)" in d._PER_TICKER_METHODS          # the real leg is computed beside its proxy and wins


def test_the_baskets_hold_five_names_or_more():
    for name in ("QSR FRANCHISORS", "OFF-PRICE RETAIL", "AUTO AFTERMARKET", "US RENEWABLE OWNERS"):
        assert len(sp.SUBCOHORT_BASKETS[name]) >= rc.MIN_INDUSTRY_PEERS, name
    for prof in ("Lodging (Asset-Light)", "Online Travel", "Cruise Lines", "City Gas Distribution (HK / China)",
                 "Specialty & Generic Pharma"):
        for ex, syms in rc.PROFILE_PEER_BASKETS[prof].items():
            assert len(syms) >= rc.MIN_INDUSTRY_PEERS, (prof, ex)
    assert sp.SUBCOHORT_OF["TJX"] == "OFF-PRICE RETAIL" and sp.SUBCOHORT_OF["ORLY"] == "AUTO AFTERMARKET"


def test_the_cyclicals_carry_the_fade():
    for prof in ("Auto Parts & Suppliers", "Casinos & Integrated Resorts", "Cruise Lines"):
        assert prof in d._CYCLICAL_PROFILES and prof in d._CONVERGENCE_ALPHA_PROFILES


def test_alibaba_prices_on_china_internet_peers_not_specialty_retail():
    """Owner, 2026-09-28: Alibaba is not specialty retail. China Internet Platform reads a curated China
    internet basket on both exchanges, ahead of FMP's Specialty Retail label cohort."""
    b = rc.PROFILE_PEER_BASKETS["China Internet Platform"]
    assert "9988.HK" in b["HKSE"] and "0700.HK" in b["HKSE"] and "BABA" in b["US"]
    assert all(len(v) >= rc.MIN_INDUSTRY_PEERS for v in b.values())
    assert sp.get_wacc_profile_for_ticker("BABA")[1] == sp.get_wacc_profile_for_ticker("09988.HK")[1] == "China Internet Platform"
