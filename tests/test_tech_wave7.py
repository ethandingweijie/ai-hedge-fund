"""Wave 7, technology and communications (owner decisions, 2026-09-27: 1 yes, 2 alternative, 3-7 yes).

Stage 0 was 10 of 30 within consensus for five architectural reasons; this file pins the Stage 4
build against each of them: the fourteen labels in scope with the re-targeted rows and HK market
map, the four new profiles and the forward anchors, the static tech table demoted to a fallback,
NTM forward multiples on, the cyclical peak flag, the growth-premium bound, and the ladder comparing
revenue in dollars.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.data import industry_profile_map as ipm
from src.data import report_families as rf
from src.data import sector_profiles as sp

P = sp.INDUSTRY_VALUATION_PROFILES
LABELS = {
    "Semiconductors": ("Semiconductor", "IDM / Foundry"),
    "Semiconductor Equipment & Materials": ("Semiconductor", "Equipment / EDA"),
    "Software - Infrastructure": ("Tech", "Mature SaaS"),
    "Software - Application": ("Tech", "Mature SaaS"),
    "Internet Content & Information": ("Tech", "Mature Platform"),
    "Electronic Gaming & Multimedia": ("Tech", "Media & Streaming"),
    "Entertainment": ("Tech", "Media & Streaming"),
    "Consumer Electronics": ("Tech", "Consumer Electronics / Hardware Ecosystem"),
    "Computer Hardware": ("Tech", "Mature Platform"),
    "Hardware, Equipment & Parts": ("Tech", "Mature Platform"),
    "Communication Equipment": ("Tech", "Networking & Communication Equipment"),
    "Telecommunications Services": ("Telco", "Telecom Carrier"),
    "Information Technology Services": ("ProfessionalServices", "IT Services"),
    "Advertising Agencies": ("ProfessionalServices", "Ad / Consulting"),
}


def test_every_wave7_label_is_in_scope_and_routes():
    scope = ipm.routing_scope()
    for label, target in LABELS.items():
        assert label in scope, label
        assert ipm.profile_for_industry(label) == target, label
    hk = ipm.market_map("HK")
    assert hk["Internet Content & Information"] == ("Tech", "China Internet Platform")      # decision 6
    assert hk["Electronic Gaming & Multimedia"] == ("Tech", "China Internet Platform")
    assert hk["Computer Hardware"] == ("Tech", "Consumer Electronics / Hardware Ecosystem")
    assert ipm.profile_for_ticker("09888.HK", "Internet Content & Information") == ("Tech", "China Internet Platform")
    assert ipm.profile_for_ticker("Z74.SI", "Telecommunications Services") == ("Telco", "Telco / Infrastructure (SG)")   # SG keeps its own


OWNER_PINS = {
    "Analog / Mixed-signal IDM": ["TXN", "ADI", "NXPI", "MCHP", "ON"],
    "Equipment / EDA": ["ASML", "AMAT", "LRCX", "KLAC", "TER"],
    "Fabless": ["NVDA", "AVGO", "AMD", "QCOM", "MRVL", "ARM"],
    "Memory / DRAM-NAND": ["MU", "WDC", "STX", "SNDK"],
    "IDM / Foundry": ["INTC", "00981.HK", "01347.HK"],
    "Telecom Carrier": ["T", "VZ", "TMUS", "CMCSA", "CHTR", "00941.HK", "00728.HK", "00762.HK"],
    "Media & Streaming": ["NFLX", "DIS", "WBD", "EA", "TTWO"],
    "Networking & Communication Equipment": ["CSCO", "ANET", "CIEN", "MSI", "JNPR"],
    "IT Services": ["ACN", "IBM", "CTSH", "INFY"],
    "China Internet Platform": ["00700.HK", "09888.HK", "01024.HK", "09999.HK"],
    "Consumer Electronics / Hardware Ecosystem": ["01810.HK", "00992.HK", "02382.HK", "00285.HK"],
    "Hyperscaler / Tech Conglomerate": ["AAPL", "MSFT", "GOOG", "META", "ORCL"],
    "Growth SaaS": ["PLTR", "SNOW", "DDOG", "NET"],
    "Cybersecurity / Mission-Critical SaaS": ["CRWD", "PANW", "ZS"],
}


@pytest.mark.parametrize("profile,tickers", list(OWNER_PINS.items()))
def test_the_owner_pins_resolve(profile, tickers):
    assert [sp.get_wacc_profile_for_ticker(t)[1] for t in tickers] == [profile] * len(tickers)


def _w(sector, name):
    return {m["name"]: m["weight"] for m in P[sector][name]["methods"]}


def test_the_new_profiles_and_forward_anchors_are_the_owner_spec():
    assert _w("Semiconductor", "Analog / Mixed-signal IDM") == {"Forward P/E": 0.35, "EV/EBITDA": 0.25, "DCF": 0.25, "FCF Yield": 0.15}
    assert _w("Tech", "Media & Streaming") == {"Forward P/E": 0.35, "EV/EBITDA": 0.30, "DCF": 0.25, "FCF Yield": 0.10}
    assert _w("Tech", "Networking & Communication Equipment") == {"Forward P/E": 0.35, "EV/EBITDA": 0.25, "DCF": 0.25, "FCF Yield": 0.15}
    assert _w("Telco", "Telecom Carrier") == {"EV/EBITDA": 0.35, "FCF Yield": 0.25, "DDM": 0.20, "DCF (2-stage)": 0.20}
    assert _w("Semiconductor", "Fabless") == {"Forward P/E": 0.35, "DCF": 0.25, "EV/EBITDA": 0.20, "EV/NTM Revenue": 0.20}
    assert _w("Semiconductor", "Equipment / EDA")["Forward P/E"] == 0.35 and "P/E" not in _w("Semiconductor", "Equipment / EDA")
    assert _w("Tech", "Mature SaaS") == {"Forward P/E": 0.35, "DCF (2-stage)": 0.30, "EV/EBITDA": 0.20, "FCF Yield": 0.15}
    assert P["Tech"]["Mature SaaS"]["shadow_methods"] == ["EPV"]
    for sec, name in (("Semiconductor", "Analog / Mixed-signal IDM"), ("Tech", "Media & Streaming"),
                      ("Tech", "Networking & Communication Equipment"), ("Telco", "Telecom Carrier")):
        assert sum(_w(sec, name).values()) == pytest.approx(1.0)
        assert name in sp.SECTOR_PEER_MULTIPLES and name in sp.SECTOR_PEER_BASKETS
        assert rf.report_family_for(name) == "Technology, telecom and media"
    assert P["Telco"]["Stable Growth"]                               # the old row's profile stays for names that still use it


# ── decision 2, alternative: the static table is the fallback ────────────────

def test_the_static_tech_table_is_a_fallback_not_an_override():
    src = inspect.getsource(d._compute_method_value)
    assert src.count("_basket_rank(peer, _peer_field) >= 1") == 1                 # EV/EBITDA and EV/EBIT branch
    assert '_basket_rank(peer, "ev_revenue") >= 1' in src                      # P/S branch
    assert '_basket_rank(peer, "ev_ebitda") >= 1' in src                       # forward EV/EBIT branch
    t = d._TECH_SUBTYPE_MULTIPLES
    assert (t["Hyperscaler / Tech Conglomerate"]["ev_ebitda"], t["Mature SaaS"]["ev_revenue"], t["Mature Platform"]["pe"]) == (16.4, 5.1, 19.9)
    assert d._CONVERGENCE_LIVE_LABEL["Mature SaaS"] == ("Software - Application", "Technology")
    # the live cohort of a profile with a curated basket is that basket (the one the table was
    # re-derived from), so one profile is priced on one cohort, not on each name's FMP industry
    from src.data.regional_comps import PROFILE_PEER_BASKETS as PB
    assert PB["Hyperscaler / Tech Conglomerate"]["US"] == ("AAPL", "MSFT", "GOOG", "META", "AMZN", "ORCL")
    assert PB["Mature SaaS"]["US"] == ("CRM", "ADBE", "NOW", "INTU", "WDAY", "ADSK")
    assert PB["Mature Platform"]["US"] == ("GOOG", "META", "BKNG", "UBER", "EBAY", "SPOT")
    assert "profile_basket_multiples(ex, convergence)" in inspect.getsource(d._live_mature_ev_revenue)
    src2 = inspect.getsource(d._terminal_multiple_ev_revenue)
    assert '_live_mature_ev_revenue(convergence, peer) or mature_mults["ev_revenue"]' in src2
    # with no market basis to look up from, the fallback is the table (test doubles never hit FMP)
    assert d._live_mature_ev_revenue("Levered Subscription", {}) is None


def test_ntm_forward_multiples_are_on_unless_switched_off(monkeypatch):
    monkeypatch.delenv(d.NTM_FORWARD_FLAG, raising=False)
    assert d._ntm_forward_enabled() is True
    monkeypatch.setenv(d.NTM_FORWARD_FLAG, "off")
    assert d._ntm_forward_enabled() is False
    peer = {"pe": 40.2, "pe_ntm": 27.7, "_comp_basis": {"pe": {"basis": "industry"}, "pe_ntm": {"basis": "industry"}}}
    monkeypatch.delenv(d.NTM_FORWARD_FLAG, raising=False)
    m, src = d._forward_peer_multiple(peer, "pe", 18.0)
    assert m == 27.7 and "forward basis" in src


# ── decision 4: the growth premium bound ─────────────────────────────────────

def test_the_growth_premium_is_an_absolute_spread_capped_at_the_tech_band():
    src = inspect.getsource(d.run_dcf_agent)
    assert "_GROWTH_SPREAD_K = 2.5" in src
    assert "1.0 + _GROWTH_SPREAD_K * (g - _sector_g_avg)" in src
    assert "growth_premium = max(0.85, min(1.30, _gp_raw))   # Wave 7" in src
    assert "(g - _sector_g_avg) / _sector_g_avg" not in src
    # ten points above the cohort adds 25%; ACN's 1.50 becomes ~1.13 and NetEase's 1.80 ~1.16
    assert 1.0 + 2.5 * 0.10 == pytest.approx(1.25)


# ── decision 3: the cyclical peak flag; decision 7: the ladder in dollars ─────

def test_the_peak_trigger_becomes_a_regime_flag_and_the_ladder_compares_dollars():
    src = inspect.getsource(d.run_dcf_agent)
    assert 'most_recent["_cyclical_peak_flag"] = True' in src
    assert '_regime_flag = "Cyclical_Peak_Consensus"' in src
    assert src.count("revenue_base=_ladder_revenue_usd(revenue_base, _target_ccy, api_key)") == 2
    assert d._ladder_revenue_usd(648.85e9, "USD") == 648.85e9
    assert d._ladder_revenue_usd(None, "HKD") is None


def test_the_ladder_helper_converts_and_never_blocks(monkeypatch):
    monkeypatch.setattr(d, "get_fx_rate", lambda a, b, k=None: 0.128)
    assert d._ladder_revenue_usd(649e9, "HKD") == pytest.approx(649e9 * 0.128)
    monkeypatch.setattr(d, "get_fx_rate", lambda a, b, k=None: (_ for _ in ()).throw(RuntimeError("fx down")))
    assert d._ladder_revenue_usd(649e9, "HKD") == 649e9
