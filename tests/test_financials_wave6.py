"""Wave 6, financials (owner decisions, 2026-09-27).

Stage 0 was the worst of the six waves (11 of 30 within consensus) for three
known reasons: bank calibration constants from 2023, life insurers and lenders
placed by the ladder, and non-implementable proxies on the alt managers. This
file pins the Stage 4 build: eleven labels in scope with the re-targeted rows,
the pins, the three new profiles and the two re-specified ones, the bank
calibration re-derivation, the implied-CoE flag, the leg fallback, and the two
review-gated input kinds the life and alt-manager legs read.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.industry import gemini_params as gp
from src.data import industry_inputs as ii
from src.data import industry_profile_map as ipm
from src.data import sector_profiles as sp

P = sp.INDUSTRY_VALUATION_PROFILES["Financials"]
LABELS = {
    "Banks - Diversified": ("Financials", "Money Center Bank"),
    "Banks - Regional": ("Financials", "Super-Regional Bank"),          # was EM Bank: PNC and TFC on an EM row
    "Banks": ("Financials", "Money Center Bank"),
    "Financial - Capital Markets": ("Financials", "Brokerage"),
    "Financial - Credit Services": ("Financials", "Card Issuer & Consumer Lender"),
    "Insurance - Diversified": ("Financials", "Insurance (P&C)"),        # was the life profile
    "Insurance - Property & Casualty": ("Financials", "Insurance (P&C)"),
    "Insurance - Life": ("Financials", "Insurance"),
    "Insurance - Brokers": ("Financials", "Insurance Broker"),
    "Asset Management": ("Financials", "Asset Manager"),
    "Financial - Data & Stock Exchanges": ("Financials", "Market Infrastructure"),
}


def test_every_financials_label_is_in_scope_and_routes():
    scope = ipm.routing_scope()
    for label, target in LABELS.items():
        assert label in scope, label
        assert ipm.profile_for_industry(label) == target, label
    assert "Conglomerates" in scope and ipm.profile_for_ticker("J36.SI", "Conglomerates") == ("Financials", "Asian Holding Company (Look-Through)")   # Wave 9 (2026-09-27) brought them in
    assert ipm.market_map("SG")["Banks"] == ("Financials", "Money Center Bank (SG)")
    assert ipm.market_map("HK")["Banks - Regional"] == ("Financials", "EM Bank")
    assert ipm.profile_for_ticker("D05.SI", "Banks") == ("Financials", "Money Center Bank (SG)")
    assert ipm.profile_for_ticker("PNC", "Banks - Regional") == ("Financials", "Super-Regional Bank")


OWNER_PINS = {
    "Super-Regional Bank": ["USB", "PNC", "TFC"],
    "Card Issuer & Consumer Lender": ["AXP", "COF", "SYF"],
    "Insurance (P&C)": ["PGR", "CB", "TRV", "AIG"],
    "Insurance": ["MET", "01299.HK", "02628.HK", "02318.HK", "G07.SI"],
    "Insurance Broker": ["MMC", "AON"],
    "Financial Data & Ratings": ["SPGI", "MSCI"],
    "Market Infrastructure": ["ICE", "CME", "00388.HK"],
    "Market Infrastructure (SG)": ["S68.SI"],
    "Holding Company": ["BRK-B"],
    "Alt Asset Manager": ["BX", "KKR", "APO"],
    "Payment Networks": ["V", "MA"],
    "Money Center Bank (SG)": ["D05.SI", "O39.SI", "U11.SI"],
}


@pytest.mark.parametrize("profile,tickers", list(OWNER_PINS.items()))
def test_the_owner_pins_resolve(profile, tickers):
    assert [sp.get_wacc_profile_for_ticker(t)[1] for t in tickers] == [profile] * len(tickers)


# ── profiles ─────────────────────────────────────────────────────────────────

def _w(name):
    return {m["name"]: m["weight"] for m in P[name]["methods"]}


def test_the_three_new_profiles_are_the_owner_spec():
    assert _w("Card Issuer & Consumer Lender") == {"P/TBV": 0.35, "P/E (norm)": 0.30, "GGM (P/B)": 0.25, "Excess Capital": 0.10}
    assert {"DCF", "FCF Yield"} <= set(P["Card Issuer & Consumer Lender"]["excluded"])     # a lender's FCF is loan growth, sign flipped
    assert _w("Insurance Broker") == {"Forward P/E": 0.35, "EV/EBITDA": 0.30, "DCF": 0.25, "FCF Yield": 0.10}
    assert {"P/BV", "GGM (P/B)"} <= set(P["Insurance Broker"]["excluded"])
    assert _w("Financial Data & Ratings") == {"Forward P/E": 0.35, "EV/EBITDA": 0.25, "DCF": 0.25, "FCF Yield": 0.15}
    for n in ("Card Issuer & Consumer Lender", "Insurance Broker", "Financial Data & Ratings", "Alt Asset Manager"):
        assert sum(_w(n).values()) == pytest.approx(1.0), n
        assert n in sp.SECTOR_PEER_MULTIPLES and n in sp.SECTOR_PEER_BASKETS, n
    # card issuers ride the bank block: they need a calibration row
    assert d._BANK_PROFILE_CALIBRATION["Card Issuer & Consumer Lender"]["p_tbv"] == 1.3


def test_alt_asset_manager_is_on_distributable_earnings_and_every_leg_is_implementable():
    assert _w("Alt Asset Manager") == {"P/DE (Forward)": 0.40, "SOTP (FRE + carry)": 0.30, "Forward P/E": 0.20, "DDM": 0.10}
    assert all(m["implementable"] for m in P["Alt Asset Manager"]["methods"])
    assert [m["name"] for m in P["Alt Asset Manager"]["methods"] if m.get("anchor")] == ["P/DE (Forward)"]


def test_life_insurance_dropped_the_combined_ratio_gate_and_holdco_declares_the_analyst_sotp():
    assert _w("Insurance") == {"Embedded Value": 0.35, "P/BV": 0.40, "P/E (ops)": 0.20, "DDM": 0.05}
    assert "Combined Ratio Gate" in P["Insurance"]["excluded"]
    assert "proxy" not in P["Insurance"]["methods"][0]                    # EV is fed by the accepted input only
    assert P["Insurance (P&C)"]["leg_fallback"] == {"Combined Ratio Gate": ["P/E (ops)"]}
    assert P["Holding Company"]["methods"][0] == {"name": "SOTP (analyst)", "weight": 0.70, "anchor": True, "implementable": True}
    # decision 5: SGX benchmarked against the global exchange basket
    assert sp.SECTOR_PEER_MULTIPLES["Market Infrastructure (SG)"]["pe"] == 25.0
    assert "CME" in sp.SECTOR_PEER_BASKETS["Market Infrastructure (SG)"]


def test_sgx_takes_the_global_exchange_basket_by_a_documented_exception():
    assert ipm.comps_exchange_for("S68.SI") == "US" and ipm.comps_exchange_for("D05.SI") is None
    src = inspect.getsource(sp)
    assert "market = comps_exchange_for(ticker) or market" in src


def test_the_pnc_calibration_row_is_the_owner_accepted_cohort_derivation():   # accepted 2026-09-27
    c = d._BANK_PROFILE_CALIBRATION["Insurance (P&C)"]
    assert (c["p_tbv"], c["pe"], c["coe"], c["target_roe"]) == (2.2, 11.4, 0.09, 0.15)


def test_the_holdco_sotp_carries_the_listed_portfolio_at_market():
    """Decision 6: Berkshire's first pre-fill carried only the equity-method associates
    ($19.9bn) and missed the $300bn listed portfolio; the field is separate and cited, and
    the bridge adds it to the associates figure the NAV reads."""
    assert "listed_investments_at_market" in gp.SotpInputs.model_fields
    src = inspect.getsource(gp.to_engine_assumptions)
    assert 'if field == "listed_investments_at_market":' in src
    assert "LISTED equity investments at" in gp.sotp_prompt("Berkshire", "BRK-B", {})


def test_the_bank_calibration_is_the_owner_accepted_table():
    c = d._BANK_PROFILE_CALIBRATION
    assert (c["Money Center Bank"]["p_tbv"], c["Money Center Bank"]["pe"], c["Money Center Bank"]["coe"]) == (2.2, 14.2, 0.093)
    assert (c["Super-Regional Bank"]["p_tbv"], c["Super-Regional Bank"]["pe"]) == (1.6, 11.6)
    assert (c["Investment Bank"]["p_tbv"], c["Investment Bank"]["pe"], c["Investment Bank"]["coe"]) == (2.4, 15.3, 0.095)
    assert (c["EM Bank"]["p_tbv"], c["EM Bank"]["coe"], c["EM Bank"]["target_roe"]) == (0.6, 0.0975, 0.105)   # zero China/HK premium
    assert "EM Bank (Premium)" not in c        # profile removed 2026-09-27 (no row or pin reached it)
    assert (c["Money Center Bank (EU)"]["p_tbv"], c["Money Center Bank (EU)"]["target_roe"]) == (1.3, 0.15)
    assert (c["Money Center Bank (SG)"]["p_tbv"], c["Money Center Bank (SG)"]["coe"]) == (1.9, 0.088)      # broker table CoE kept


# ── engine ───────────────────────────────────────────────────────────────────

def test_the_alt_manager_legs_read_the_accepted_input_and_price_nothing_without_it():
    src = inspect.getsource(d._compute_method_value)
    assert 'if method_name in {"P/DE (Forward)", "P/DE"}:' in src
    assert 'if method_name in {"SOTP (FRE + carry)", "SOTP (FRE+Carry)"}:' in src
    assert '_de = most_recent.get("alt_de_fwd_total")' in src and '_pfre = most_recent.get("alt_pfre_multiple")' in src
    run = inspect.getsource(d.run_dcf_agent)
    assert '_ev_e = _ii_w6.accepted_entry(ticker, "embedded_value")' in run
    assert 'most_recent["embedded_value_per_share"] = None' in run          # extractor value quarantined
    assert '_am_e = _ii_w6.accepted_entry(ticker, "alt_manager")' in run


def test_the_implied_coe_is_a_flag_and_never_a_rate():
    run = inspect.getsource(d.run_dcf_agent)
    at = run.index('"gate_id": "GATE_BANK_IMPLIED_COE"')
    block = run[at - 1600: at + 400]
    assert "_coe_impl = float(_ga[\"g\"]) + (float(_ga[\"roe\"]) - float(_ga[\"g\"])) / _ptbv_spot" in block
    assert "abs(_coe_impl - _coe_used) > _BANK_COE_PLAUSIBLE_BAND" in block
    assert '"applied": False,' in run[at: at + 700]
    assert "anomaly flag, not applied" in block


def test_the_leg_fallback_rolls_a_missing_leg_before_the_blend():
    run = inspect.getsource(d.run_dcf_agent)
    at = run.index('.get("leg_fallback")')
    assert run.index("blend_breakdown: dict = {}", at) - at < 1400          # sits right before the blend
    rows, rec = d._roll_leg_weight(P["Insurance (P&C)"]["methods"], "Combined Ratio Gate", ["P/E (ops)"])
    w = {m["name"]: m["weight"] for m in rows}
    assert "Combined Ratio Gate" not in w and w["P/E (ops)"] == pytest.approx(0.45) and w["GGM (P/B)"] == 0.40


# ── the two review-gated kinds ───────────────────────────────────────────────

def _cite(v, ccy="USD", scale="units", period="FY2025"):
    return {"value": v, "currency": ccy, "scale": scale, "period": period, "source_url": "https://example.com/r", "quote": "q"}


def test_the_embedded_value_kind_bridges_per_share_in_the_statement_currency():
    assert "embedded_value" in ii.KINDS and "embedded_value" in ii.NO_OVERLAY and ii.BOUNDS["embedded_value"][0] == "market_cap"
    assert gp.INDUSTRY_INPUT_SCHEMAS["embedded_value"] is gp.EmbeddedValueInputs
    data = {"fiscal_year": "FY2025", "basis": "EEV", "ev_total": _cite(72.0, "USD", "bn"),
            "ev_per_share": _cite(6.5, "USD"), "vnb_margin": {"value": 54.0, "period": "FY2025", "source_url": "https://e.com", "quote": "q"}}
    out = gp.embedded_value_to_engine(data, lambda c: 1.0)
    assert out["ev_per_share"] == 6.5 and out["vnb_margin"] == pytest.approx(0.54) and out["basis"] == "EEV"
    rows = ii.ui_summary(doc={"version": 1, "tickers": {"01299.HK": {"embedded_value": {"data": data, "checks": [], "ok": True, "company": "AIA", "basis": "actual"}}}},
                         reviews=lambda *a, **k: {"status": "pending", "reviewer": None, "reviewed_at": None, "stale": False})["rows"]
    assert rows[0]["kind"] == "embedded_value" and rows[0]["period"] == "FY2025"
    assert rows[0]["detail"]["ev_per_share"]["period_label"] == "FY2025" and rows[0]["detail"]["vnb_margin"] == 54.0


def test_the_alt_manager_kind_bridges_totals_and_cited_range_midpoints():
    assert "alt_manager" in ii.KINDS and ii.BOUNDS["alt_manager"] == ("market_cap", 0.02, 0.15)
    data = {"fiscal_year": "FY2026E", "fre": _cite(5.6, "USD", "bn", "FY2026E"), "de": _cite(7.1, "USD", "bn", "FY2026E"),
            "net_accrued_carry": _cite(6.0, "USD", "bn", "2Q26"),
            "pde_multiple": {"low": 22.0, "high": 26.0, "basis": "broker P/DE FY26E", "source_url": "https://e.com", "quote": "q"},
            "pfre_multiple": {"low": 28.0, "high": 32.0, "basis": "broker P/FRE", "source_url": "not a url", "quote": ""}}   # uncited: ignored
    out = gp.alt_manager_to_engine(data, lambda c: 1.0)
    assert out["alt_de_fwd_total"] == 7.1e9 and out["alt_fre_fwd_total"] == 5.6e9 and out["alt_net_accrued_carry_total"] == 6.0e9
    assert out["alt_pde_multiple"] == 24.0 and "alt_pfre_multiple" not in out
    # consensus published per share (Blackstone's first pass: $6.02/share DE) fills the per-share field
    out2 = gp.alt_manager_to_engine({"fiscal_year": "FY2026E", "de_per_share": _cite(6.02, "USD", "units", "FY2026E"),
                                     "pde_multiple": {"low": 21.0, "high": 30.0, "basis": "b", "source_url": "https://e.com"}}, lambda c: 1.0)
    assert out2["alt_de_ps_fwd"] == 6.02 and out2["alt_pde_multiple"] == 25.5 and "alt_de_fwd_total" not in out2
    src = inspect.getsource(d._compute_method_value)
    assert '_de_ps_in = most_recent.get("alt_de_ps_fwd")' in src
    p = gp.alt_manager_prompt("Blackstone", "BX", {"market_cap_usd_bn": 143.0})
    assert "fee-related" in p and "distributable earnings (DE)" in p and "P/DE and P/FRE multiple RANGES" in p
    assert "Do NOT value" in gp.embedded_value_prompt("AIA", "01299.HK", {})
