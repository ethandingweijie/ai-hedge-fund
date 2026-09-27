"""Wave 9, industrials, materials, metals and transport (owner decisions of 2026-09-27).

Gate 1 (scope and pin freeze) and Gate 2 (profile compilation) are pinned here: the 32 labels brought into
routing scope and the row each takes, the new and re-specified profiles at the owner's weights, the
per-category pins, the cohort carve-out and sigma trim, the sub-cohort baskets, the margin-peak and
backlog gates, the EBITDA operating bridge, the maintenance-capex ceiling, the Asian holdco discount index,
and the rule that only a property profile reads P/B as P/NAV.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.data import industry_profile_map as ipm
from src.data import regional_comps as rc
from src.data import report_families as rf
from src.data import sector_profiles as sp

P = sp.INDUSTRY_VALUATION_PROFILES


def _weights(sector: str, profile: str) -> dict:
    return {m["name"]: m["weight"] for m in P[sector][profile]["methods"]}


def _anchor(sector: str, profile: str) -> str:
    return next(m["name"] for m in P[sector][profile]["methods"] if m.get("anchor"))


# ── Gate 1: scope and rows ────────────────────────────────────────────────────

_WAVE9_ROWS = {
    "Agricultural - Machinery": ("Industrials", "Capital Goods"),
    "Agricultural Inputs": ("Materials", "Commodity Chemicals & Ag Inputs"),
    "Airlines, Airports & Air Services": ("Transportation", "Airlines"),
    "Aluminum": ("Resources", "Base Metals"),
    "Chemicals": ("Materials", "Commodity Chemicals & Ag Inputs"),
    "Chemicals - Specialty": ("Materials", "Specialty Chemicals"),
    "Conglomerates": ("Industrials", "Blended Industrial OpCo"),
    "Construction Materials": ("Materials", "Aggregates & Cement"),
    "Copper": ("Resources", "Base Metals"),
    "Electrical Equipment & Parts": ("Industrials", "Capital Goods"),
    "Engineering & Construction": ("Industrials", "Long-Cycle E&C"),
    "Gold": ("Resources", "Precious Metals"),
    "Industrial - Distribution": ("Industrials", "Industrial Distribution"),
    "Industrial - Infrastructure Operations": ("Industrials", "Toll Road / Infrastructure (HK)"),
    "Industrial - Machinery": ("Industrials", "Capital Goods"),
    "Industrial - Pollution & Treatment Controls": ("Industrials", "Waste & Environmental Services"),
    "Industrial Materials": ("Resources", "Diversified Miners"),
    "Integrated Freight & Logistics": ("Transportation", "Trucking & Parcel Logistics"),
    "Manufacturing - Metal Fabrication": ("Industrials", "Capital Goods"),
    "Manufacturing - Tools & Accessories": ("Industrials", "Capital Goods"),
    "Marine Shipping": ("Transportation", "Container & Bulk Shipping"),
    "Other Precious Metals": ("Resources", "Precious Metals"),
    "Packaging & Containers": ("Materials", "Packaging & Paper"),
    "Paper, Lumber & Forest Products": ("Materials", "Packaging & Paper"),
    "Railroads": ("Transportation", "Rail / Logistics"),
    "Rental & Leasing Services": ("Industrials", "Equipment Rental"),
    "Security & Protection Services": ("Industrials", "Capital Goods"),
    "Silver": ("Resources", "Precious Metals"),
    "Specialty Business Services": ("Industrials", "Industrial Route & Uniform Services"),
    "Steel": ("Materials", "Steel / Metals"),
    "Trucking": ("Transportation", "Trucking & Parcel Logistics"),
    "Waste Management": ("Industrials", "Waste & Environmental Services"),
}


def test_the_thirty_two_labels_are_in_scope_on_their_rows():
    m = ipm.industry_map()
    assert len(_WAVE9_ROWS) == 32
    for label, row in _WAVE9_ROWS.items():
        assert ipm.profile_for_industry(label) == row, label
        assert row[1] in P[row[0]], row
    import json
    from pathlib import Path
    raw = json.loads((Path(ipm.__file__).parent / "industry_profile_map.json").read_text(encoding="utf-8"))
    assert raw["version"] == 23
    assert set(_WAVE9_ROWS) <= set(raw["routing_scope"])
    assert m is not None


def test_the_market_rows_split_the_conglomerates_and_the_sg_transport_labels():
    # the US conglomerate is a blended OpCo; an Asian conglomerate is a look-through holding company
    assert ipm.profile_for_ticker("MMM", "Conglomerates") == ("Industrials", "Blended Industrial OpCo")
    assert ipm.profile_for_ticker("00001.HK", "Conglomerates") == ("Financials", "Asian Holding Company (Look-Through)")
    assert ipm.profile_for_ticker("BN4.SI", "Conglomerates") == ("Financials", "Asian Holding Company (Look-Through)")
    assert ipm.profile_for_ticker("C6L.SI", "Airlines, Airports & Air Services") == ("Transportation", "Airlines")
    # SIA Engineering keeps its Wave 3 profile by an explicit override, not by the SG airline row
    assert ipm.ticker_overrides()["S59.SI"] == ("Industrials", "Aviation & Marine (SG)")


# ── Gate 1: pins by category ─────────────────────────────────────────────────

_PINS = {
    "Capital Goods": ["CAT", "DE", "PH", "TT", "SNA", "ETN", "VRT", "01766.HK"],
    "Long-Cycle E&C": ["PWR", "00390.HK", "01186.HK"],
    "Industrial Distribution": ["GWW", "FAST"],
    "Equipment Rental": ["URI", "AER"],
    "Waste & Environmental Services": ["WM", "RSG", "VLTO"],
    "Industrial Route & Uniform Services": ["CTAS", "UNF", "ROL"],
    "Information Services": ["RELX", "TRI"],
    "Marketplace / Salvage Platform": ["CPRT", "RBA"],
    "Blended Industrial OpCo": ["MMM", "HON"],
    "Airlines": ["DAL", "UAL", "00293.HK"],
    "Rail / Logistics": ["UNP", "CSX", "00066.HK"],
    "Trucking & Parcel Logistics": ["ODFL", "XPO", "UPS", "FDX"],
    "Commodity Chemicals & Ag Inputs": ["DOW", "CTVA", "01772.HK"],
    "Aggregates & Cement": ["CRH", "VMC", "MLM", "00914.HK"],
    "Packaging & Paper": ["IP", "PKG"],
    "Steel / Metals": ["NUE", "STLD", "MT"],
    "Base Metals": ["FCX", "SCCO", "AA", "01378.HK", "02600.HK", "00358.HK"],
    "Precious Metals": ["NEM", "AEM", "02899.HK"],
    "Diversified Miners": ["BHP", "RIO", "VALE", "03993.HK"],
    "Battery & Energy Storage": ["03750.HK"],
    "Asian Holding Company (Look-Through)": ["00001.HK", "00267.HK"],
    "Toll Road / Infrastructure (HK)": ["00177.HK", "00576.HK"],
    "Container & Bulk Shipping": ["01919.HK", "00316.HK"],
}


@pytest.mark.parametrize("profile,tickers", sorted(_PINS.items()))
def test_every_pin_resolves_its_category(profile, tickers):
    for t in tickers:
        assert sp.get_wacc_profile_for_ticker(t)[1] == profile, t


def test_the_sgx_rows_carry_the_holdcos_and_the_flag_carrier():
    for t in ("J36.SI", "BN4.SI"):
        assert sp.SGX_TICKER_SECTOR_LOOKUP[t][:2] == ("Financials", "Asian Holding Company (Look-Through)"), t
    assert sp.SGX_TICKER_SECTOR_LOOKUP["C6L.SI"][:2] == ("Transportation", "Airlines")


# ── Gate 2: profiles at the owner's weights ──────────────────────────────────

_SPEC = {
    ("Industrials", "Long-Cycle E&C"): ({"Backlog-Gated EV/EBITDA": .50, "Forward P/E": .30, "P/BV": .20}, "Backlog-Gated EV/EBITDA"),
    ("Industrials", "Industrial Distribution"): ({"Forward EV/EBITDA": .45, "Forward P/E": .35, "FCF Yield": .20}, "Forward EV/EBITDA"),
    ("Industrials", "Equipment Rental"): ({"EV/EBITDA": .60, "P/BV": .20, "Forward P/E": .20}, "EV/EBITDA"),
    ("Industrials", "Waste & Environmental Services"): ({"Forward EV/EBITDA": .50, "FCF Yield": .30, "Forward P/E": .20}, "Forward EV/EBITDA"),
    ("Industrials", "Toll Road / Infrastructure (HK)"): ({"DDM": .50, "EV/EBITDA": .30, "P/BV": .20}, "DDM"),
    ("Industrials", "Industrial Route & Uniform Services"): ({"Forward EV/EBITDA": .50, "EV/EBIT": .30, "P/E": .20}, "Forward EV/EBITDA"),
    ("Industrials", "Information Services"): ({"Forward P/E": .40, "EV/EBITDA": .40, "FCF Yield": .20}, "Forward P/E"),
    ("Industrials", "Marketplace / Salvage Platform"): ({"Forward P/E": .40, "EV/EBITDA": .40, "FCF Yield": .20}, "Forward P/E"),
    ("Industrials", "Blended Industrial OpCo"): ({"Forward EV/EBIT": .50, "FCF Yield": .50}, "Forward EV/EBIT"),
    ("Industrials", "Battery & Energy Storage"): ({"Forward EV/EBITDA": .60, "EV/Revenue": .40}, "Forward EV/EBITDA"),
    ("Industrials", "Capital Goods"): ({"Forward EV/EBITDA": .40, "P/E": .40, "EV/EBIT": .20}, "Forward EV/EBITDA"),
    ("Transportation", "Trucking & Parcel Logistics"): ({"Forward EV/EBITDA": .45, "Forward P/E": .35, "EV/EBIT": .20}, "Forward EV/EBITDA"),
    ("Transportation", "Container & Bulk Shipping"): ({"EV/EBITDA (norm)": .50, "RNAV (published)": .50}, "EV/EBITDA (norm)"),
    ("Transportation", "Airlines"): ({"Forward EV/EBITDA": .50, "P/BV": .30, "P/E (norm)": .20}, "Forward EV/EBITDA"),
    ("Transportation", "Rail / Logistics"): ({"EV/EBITDA": .50, "Forward P/E": .35, "FCF Yield": .15}, "EV/EBITDA"),
    ("Materials", "Commodity Chemicals & Ag Inputs"): ({"EV/EBITDA (norm)": .50, "P/BV": .30, "P/E (norm)": .20}, "EV/EBITDA (norm)"),
    ("Materials", "Aggregates & Cement"): ({"Forward EV/EBITDA": .55, "RNAV (published)": .25, "Forward P/E": .20}, "Forward EV/EBITDA"),
    ("Materials", "Packaging & Paper"): ({"EV/EBITDA (norm)": .50, "EV/EBIT": .30, "Forward P/E": .20}, "EV/EBITDA (norm)"),
    ("Materials", "Steel / Metals"): ({"EV/EBITDA (Norm)": .50, "P/BV": .30, "P/E (norm)": .20}, "EV/EBITDA (Norm)"),
    ("Resources", "Base Metals"): ({"EV/EBITDA (norm)": .50, "RNAV (published)": .30, "EV/OCF": .20}, "EV/EBITDA (norm)"),
    ("Resources", "Precious Metals"): ({"RNAV (published)": .60, "EV/EBITDA": .25, "EV/OCF": .15}, "RNAV (published)"),
    ("Resources", "Diversified Miners"): ({"EV/EBITDA (norm)": .50, "RNAV (published)": .50}, "EV/EBITDA (norm)"),
    ("Financials", "Asian Holding Company (Look-Through)"): ({"SOTP (analyst)": .60, "P/BV": .25, "DDM": .15}, "SOTP (analyst)"),
}


@pytest.mark.parametrize("key", sorted(_SPEC))
def test_each_profile_is_the_owner_spec(key):
    weights, anchor = _SPEC[key]
    assert _weights(*key) == pytest.approx(weights)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert _anchor(*key) == anchor


def test_a_reviewed_input_leg_falls_back_to_a_market_leg_until_it_is_accepted():
    fb = {k: P[k[0]][k[1]].get("leg_fallback") for k in _SPEC}
    assert fb[("Resources", "Base Metals")] == {"RNAV (published)": ["EV/EBITDA (norm)"]}
    assert fb[("Resources", "Precious Metals")] == {"RNAV (published)": ["EV/EBITDA"]}
    assert fb[("Resources", "Diversified Miners")] == {"RNAV (published)": ["EV/EBITDA (norm)"]}
    assert fb[("Materials", "Aggregates & Cement")] == {"RNAV (published)": ["Forward EV/EBITDA"]}
    assert fb[("Transportation", "Container & Bulk Shipping")] == {"RNAV (published)": ["EV/EBITDA (norm)"]}
    assert fb[("Financials", "Asian Holding Company (Look-Through)")] == {"SOTP (analyst)": ["P/BV"]}
    assert P["Industrials"]["Capital Goods"].get("margin_peak_gate") is True


def test_the_cyclical_profiles_are_on_the_normalised_path_and_the_report_families():
    for prof in ("Base Metals", "Diversified Miners", "Container & Bulk Shipping", "Commodity Chemicals & Ag Inputs",
                 "Packaging & Paper"):
        assert prof in d._CYCLICAL_PROFILES, prof
    fam = {p: rf.report_family_for(p) for p in ("Capital Goods", "Trucking & Parcel Logistics", "Aggregates & Cement",
                                                "Base Metals", "Precious Metals", "Asian Holding Company (Look-Through)")}
    assert fam["Capital Goods"] == fam["Trucking & Parcel Logistics"] == fam["Aggregates & Cement"] == "Industrials, materials and transport"
    assert fam["Base Metals"] == fam["Precious Metals"] == "Energy and resources"
    assert fam["Asian Holding Company (Look-Through)"] == "Property, REITs and holdcos"


# ── Gate 2: cohorts ──────────────────────────────────────────────────────────

def test_catl_is_carved_out_and_the_us_electrical_cohort_is_sigma_trimmed():
    assert sp.COHORT_RULES[("HKSE", "Electrical Equipment & Parts")] == {"exclude": ["03750.HK"]}
    assert sp.COHORT_RULES[("US", "Electrical Equipment & Parts")] == {"trim_field": "ev_ebitda_ntm", "trim_sigma": 2.0}
    src = inspect.getsource(sp.get_sector_peer_multiples)
    assert "COHORT_RULES.get((_market or \"US\", _lab9))" in src and "SUBCOHORT_OF.get(" in src
    # a rule that removes nobody returns nothing, so the plain label median stands
    assert rc.label_multiples_ruled("US", "No Such Label", {"exclude": ["ZZZZ"]}) == {}


def test_the_sub_cohorts_split_ltl_truckload_parcel_minimills_integrated_and_aggregates():
    assert set(sp.SUBCOHORT_BASKETS) == {"LTL", "TRUCKLOAD", "PARCEL & LOGISTICS", "STEEL - MINIMILL (EAF)",
                                         "STEEL - INTEGRATED", "AGGREGATES"}
    assert sp.SUBCOHORT_OF["ODFL"] == "LTL" and sp.SUBCOHORT_OF["UPS"] == "PARCEL & LOGISTICS"
    assert sp.SUBCOHORT_OF["NUE"] == "STEEL - MINIMILL (EAF)" and sp.SUBCOHORT_OF["MT"] == "STEEL - INTEGRATED"
    assert sp.SUBCOHORT_OF["VMC"] == "AGGREGATES"
    for k, members in sp.SUBCOHORT_BASKETS.items():
        assert len(members) >= 5, k                                   # the floor a median needs


# ── Gate 2: mid-cycle and backlog gates ──────────────────────────────────────

def _series(margins):
    return [{"revenue": 100.0, "ebit": m * 100.0} for m in margins]


def test_the_margin_peak_fires_above_the_seven_year_median_plus_one_and_a_half_sigma():
    assert (d._MARGIN_PEAK_WINDOW, d._MARGIN_PEAK_SIGMA) == (7, 1.5)
    hot = d._margin_peak(_series([.10, .11, .10, .12, .11, .10, .11, .20]))
    assert hot["fired"] is True and hot["years"] == 7 and hot["latest"] == pytest.approx(.20)
    assert hot["threshold"] == pytest.approx(hot["median"] + 1.5 * hot["sigma"], abs=2e-4)
    calm = d._margin_peak(_series([.10, .11, .10, .12, .11, .10, .11, .12]))
    assert calm["fired"] is False
    thin = d._margin_peak(_series([.10, .11, .20]))
    assert thin["fired"] is False and "four needed" in thin["reason"]
    assert d._MARGIN_PEAK_SWAPS == {"Forward EV/EBITDA": "EV/EBITDA (norm)", "EV/EBITDA": "EV/EBITDA (norm)", "P/E": "P/E (norm)"}


def test_backlog_scales_the_multiple_linearly_under_one_point_two_times_revenue(monkeypatch):
    from src.data import industry_inputs as ii
    assert d._BACKLOG_MULTIPLE_COVER == 1.2
    monkeypatch.setattr(ii, "accepted_detail", lambda t, kind, ccy: None)
    scale, rec = d._backlog_multiple_scale("PWR", 100.0, "USD")
    assert scale == 1.0 and rec["cover"] is None                    # no accepted backlog: unscaled, recorded
    monkeypatch.setattr(ii, "accepted_detail", lambda t, kind, ccy: {"value": 90.0})
    scale, rec = d._backlog_multiple_scale("PWR", 100.0, "USD")
    assert scale == pytest.approx(0.75) and rec["cover"] == 0.9
    monkeypatch.setattr(ii, "accepted_detail", lambda t, kind, ccy: {"value": 300.0})
    assert d._backlog_multiple_scale("PWR", 100.0, "USD")[0] == 1.0  # capped at one: backlog never lifts a multiple


def test_the_gates_are_recorded_as_applied():
    src = inspect.getsource(d)
    assert '"gate_id": "GATE_MARGIN_PEAK"' in src and '"gate_id": "GATE_BACKLOG_MULTIPLE"' in src
    assert "_backlog_multiple_scale(" in inspect.getsource(d._compute_method_value)


# ── data guardrails ──────────────────────────────────────────────────────────

def test_ebitda_is_rebuilt_through_the_operating_bridge_and_a_ten_percent_gap_is_flagged():
    assert d._EBITDA_BRIDGE_FLAG == 0.10
    for prof in ("Capital Goods", "Long-Cycle E&C", "Equipment Rental", "Airlines", "Rail / Logistics",
                 "Trucking & Parcel Logistics"):
        assert prof in d._EBITDA_BRIDGE_PROFILES, prof
    src = inspect.getsource(d.run_dcf_agent)
    assert "if profile_name in _EBITDA_BRIDGE_PROFILES:" in src
    assert 'most_recent["_ebitda_bridge"] = {"feed_ebitda": _fe9, "bridge_ebitda": _br9, "gap": _gap9}' in src


def test_maintenance_capex_is_capped_at_d_and_a_on_the_capital_heavy_profiles():
    assert d._MAINT_CAPEX_CEILING_K == 1.0                            # PROPOSED: the owner named the index, not the multiple
    assert d._MAINT_CAPEX_CEILING_PROFILES == frozenset({"Aggregates & Cement", "Diversified Miners",
                                                          "Packaging & Paper", "Waste & Environmental Services"})
    assert "min(abs(float(_cx9)), _MAINT_CAPEX_CEILING_K * abs(float(_da9)))" in inspect.getsource(d._compute_method_value)


# ── Asian holdco discount and the P/NAV boundary ─────────────────────────────

def test_the_asian_holdco_discount_is_indexed_to_conglomerate_book_and_held_in_band(monkeypatch):
    assert d._ASIAN_HOLDCO_DISCOUNT_BAND == (0.25, 0.40)
    monkeypatch.setattr(rc, "label_member_symbols", lambda ex, label: ["A", "B", "C"])
    monkeypatch.setattr(rc, "basket_field_values", lambda ex, syms, field, *a, **k: [0.35, 0.41, 0.60])
    out, note = d._index_asian_holdco_discount("00001.HK", {"holdco_discount_pct": 0.2})
    assert out["holdco_discount_pct"] == 0.40 and "HKSE" in note        # implied 59%, clamped to the band's top
    monkeypatch.setattr(rc, "basket_field_values", lambda ex, syms, field, *a, **k: [0.65, 0.70, 0.80])
    out, _ = d._index_asian_holdco_discount("BN4.SI", {})
    assert out["holdco_discount_pct"] == pytest.approx(0.30)            # inside the band: the index stands
    same, none = d._index_asian_holdco_discount("CAT", {"holdco_discount_pct": 0.2})
    assert same == {"holdco_discount_pct": 0.2} and none is None        # not the profile: untouched


def test_only_a_property_profile_reads_book_as_p_nav():
    hk = {"pb": 0.295, "_comp_basis": {"pb": {"basis": "industry", "key": "Real Estate - Diversified", "exchange": "HKSE", "cohort": "all"}}}
    mr = {}
    assert d._calibrated_published_nav(mr, hk, 100.0, "RNAV (published)", "Base Metals") == 100.0
    assert mr["_nav_calibration"]["cohort_p_nav_median_4q"] is None
    for prof in ("REIT", "S-REIT", "Landlord / Investment Property (HK)"):
        assert prof in d._P_NAV_PROFILES
    for prof in ("Base Metals", "Precious Metals", "Diversified Miners", "Container & Bulk Shipping", "Aggregates & Cement"):
        assert prof not in d._P_NAV_PROFILES
