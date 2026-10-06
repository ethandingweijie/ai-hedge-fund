"""Biopharma valuation-method selection (owner, 2026-10-06): the owner's table for popular tickers and
the four gates for the rest."""
from src.data import biopharma_methods as bm
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES


def test_every_archetype_maps_to_a_real_profile():
    for a in bm.ARCHETYPES.values():
        assert a["profile"] in INDUSTRY_VALUATION_PROFILES["Biopharma"], a["profile"]


def test_the_owner_table_decides_popular_tickers():
    assert bm.select("PFE")["archetype"] == "consolidated_dcf"
    assert bm.select("BEAM")["profile"] == "Pre-approval Biotech"
    assert bm.select("MRNA")["profile"] == "Commercial Biotech"          # not pre-approval any more
    assert bm.select("09688.HK")["archetype"] == "inlicensing_sotp"
    assert bm.select("01276.HK")["profile"] == "Large Cap Pharma"
    assert bm.select("SDGR")["profile"] == "Biotech Platform (SOTP)"
    assert bm.select("pfe")["source"].startswith("owner table")


def test_gate1_a_concentrated_big_pharma_takes_the_consolidated_dcf():
    r = bm.select_by_gates({"total_revenue": 60e9, "product_sales": 58e9, "ev": 200e9, "n_clinical": 40,
                            "late_stage_value": 20e9})
    assert r["archetype"] == "consolidated_dcf"


def test_gate1_a_breakthrough_that_could_double_the_enterprise_keeps_it_hybrid():
    r = bm.select_by_gates({"total_revenue": 3e9, "product_sales": 2.9e9, "ev": 10e9, "n_clinical": 6,
                            "late_stage_value": 12e9})
    assert r["archetype"] == "hybrid_sotp" and "breakthrough" in r["trace"][0]


def test_gate2_a_focused_pre_commercial_name_takes_standalone_rnpv():
    r = bm.select_by_gates({"total_revenue": 50e6, "product_sales": 0.0, "ev": 1.5e9, "n_clinical": 2})
    assert r["archetype"] == "pipeline_rnpv"


def test_gate3_platform_revenue_takes_the_two_pillar_sotp():
    r = bm.select_by_gates({"total_revenue": 200e6, "product_sales": 0.0, "platform_revenue": 180e6,
                            "ev": 2e9, "n_clinical": 8})
    assert r["archetype"] == "platform_sotp"


def test_gate4_a_commercial_base_with_a_pipeline_is_hybrid_and_without_one_is_pipeline():
    assert bm.select_by_gates({"total_revenue": 2e9, "product_sales": 1.2e9, "ev": 15e9,
                               "n_clinical": 12})["archetype"] == "hybrid_sotp"
    assert bm.select_by_gates({"total_revenue": 5e6, "product_sales": 0.0, "ev": 3e9,
                               "n_clinical": 7})["archetype"] == "pipeline_rnpv"


def test_segments_split_into_product_platform_and_other():
    c = bm.classify_segments({"Software": 180.0, "Drug Discovery": 20.0, "Collaboration Revenue": 50.0,
                              "Product Revenue, Net": 900.0})
    assert c["platform"] == 200.0 and c["other"] == 50.0 and c["product"] == 900.0


def test_the_pre_approval_profile_no_longer_values_cash_at_a_book_multiple():
    rows = INDUSTRY_VALUATION_PROFILES["Biopharma"]["Pre-approval Biotech"]["methods"]
    w = {m["name"]: m["weight"] for m in rows}
    assert w["rNPV"] == 1.0 and "Pipeline NAV" not in w and w.get("Cash Runway") == 0.0


def test_gate1_needs_material_product_sales():
    # FIXBT: pre-revenue, no segmentation -- a few million of revenue is not a 100%-commercial big pharma.
    r = bm.select_by_gates({"total_revenue": 3e6, "product_sales": None, "ev": 1e9, "n_clinical": 2})
    assert r["archetype"] == "pipeline_rnpv"


def test_the_selection_is_biopharma_only():
    import inspect
    from src.agents.analysis import dcf_agent as d
    src = inspect.getsource(d)
    assert 'if sector == "Biopharma" and (profile_name in _bpm.DRUG_PROFILES or ticker.upper() in _bpm.POPULAR):' in src
    assert "Specialty & Generic Pharma" not in bm.DRUG_PROFILES and "MedTech / Devices" not in bm.DRUG_PROFILES


def test_gate1_is_a_maturity_filter():
    # A loss-making company with concentrated product revenue is not valued like big pharma.
    r = bm.select_by_gates({"total_revenue": 900e6, "product_sales": 880e6, "ev": 5e9, "n_clinical": 6,
                            "operating_profitable": False})
    assert r["archetype"] == "hybrid_sotp"
