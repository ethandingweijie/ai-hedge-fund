"""Wave 1 oil, gas & coal (owner-approved 2026-09-20).

Baseline that prompted it (current engine, production flags, 35 names): with
industry routing off, every uncurated energy name defaulted to the Tech sector
-- Valero, Marathon and Phillips 66 valued as Hyperscaler / Tech Conglomerate,
Williams and Energy Transfer as Levered Subscription, Seatrium as Growth SaaS,
China Coal as a hyperscaler at 4.8x consensus. 4 of 35 passed the T-1
methodology backtest; 12 of 29 with a consensus target sat within 30% of it.

Owner decisions pinned here: scoped industry routing for these industries only;
a same-market oil, gas & coal family basket for thin HK/SG industries; new Coal
and oil & gas profiles with the method table as tabled; Damodaran January 2026
discount rates for the Energy sub-types.
"""
import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import _CYCLICAL_PROFILES, _compute_method_value
from src.data import industry_profile_map as ipm
from src.data import regional_comps as rc
from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES as P, _ENERGY_PROFILE_WACC, get_wacc_profile_for_ticker,
)

WAVE1_ROWS = {
    "Oil & Gas Integrated": ("Resources", "Integrated Oil & Gas"),
    "Oil & Gas Exploration & Production": ("Resources", "Upstream Oil & Gas"),
    "Oil & Gas Midstream": ("Energy", "Midstream / Pipelines"),
    "Oil & Gas Refining & Marketing": ("Energy", "Refining & Marketing"),
    "Oil & Gas Equipment & Services": ("Energy", "Oilfield Services & Drilling"),
    "Oil & Gas Drilling": ("Energy", "Oilfield Services & Drilling"),
    "Coal": ("Resources", "Coal"),
}

TABLE = {   # owner-approved method table: (name, weight, anchor)
    ("Resources", "Integrated Oil & Gas"): [("EV/EBITDA (norm)", .45, True), ("EV/OCF", .25, False),
                                            ("FCF Yield", .15, False), ("DDM", .15, False)],
    ("Resources", "Upstream Oil & Gas"): [("EV/OCF", .40, True), ("EV/EBITDA (norm)", .25, False),
                                          ("Depleting Asset DCF (Finite Life, No TV)", .25, False),
                                          ("P/BV", .10, False)],
    ("Energy", "Midstream / Pipelines"): [("EV/EBITDA", .45, True), ("Distributable CF Yield", .25, False),
                                          ("DDM", .20, False), ("DCF", .10, False)],
    ("Energy", "Refining & Marketing"): [("EV/EBITDA (norm)", .40, True), ("Forward EV/EBITDA", .20, False),
                                         ("FCF Yield", .20, False), ("P/BV", .10, False),
                                         ("P/E (norm)", .10, False)],
    ("Energy", "Oilfield Services & Drilling"): [("EV/EBITDA (norm)", .50, True), ("P/BV", .20, False),
                                                 ("FCF Yield", .20, False), ("P/E (norm)", .10, False)],
    ("Resources", "Coal"): [("EV/EBITDA (norm)", .40, True),
                            ("Depleting Asset DCF (Finite Life, No TV)", .25, False),
                            ("DDM", .20, False), ("P/BV", .15, False)],
}


# ── routing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("industry,pair", sorted(WAVE1_ROWS.items()))
def test_each_industry_row_routes_to_its_profile(industry, pair):
    assert ipm.profile_for_industry(industry) == pair
    assert P[pair[0]][pair[1]]


def test_the_scope_is_exactly_the_wave_one_industries():
    assert ipm.routing_scope() == frozenset(WAVE1_ROWS)


def test_scope_does_not_reach_other_industries():
    for other in ("Software - Application", "Regulated Electric", "Solar", "Banks - Diversified"):
        assert not ipm.in_routing_scope("XYZ", other)


def test_pacific_radiance_is_routed_by_ticker():
    assert ipm.in_routing_scope("RXS.SI", "Marine Shipping")
    assert ipm.profile_for_ticker("RXS.SI", "Marine Shipping") == (
        "Industrials", "Offshore Marine & Resources (SG)")


def test_singapore_offshore_keeps_its_calibrated_profile():
    assert ipm.profile_for_ticker("5E2.SI", "Oil & Gas Equipment & Services") == (
        "Industrials", "Offshore Marine & Resources (SG)")


@pytest.mark.parametrize("ticker,profile", [
    ("XOM", "Integrated Oil & Gas"), ("CVX", "Integrated Oil & Gas"),
    ("COP", "Upstream Oil & Gas"), ("RE4.SI", "Coal")])
def test_curated_pins_agree_with_the_rows(ticker, profile):
    """A pin with a profile beats the industry row, so a stale pin would pull a
    name straight back off the profile its row routes it to."""
    assert get_wacc_profile_for_ticker(ticker)[1] == profile


# ── profiles ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(TABLE))
def test_profile_matches_the_approved_table(key):
    sector, name = key
    got = [(m["name"], m["weight"], bool(m.get("anchor"))) for m in P[sector][name]["methods"]]
    assert got == [(n, pytest.approx(w), a) for n, w, a in TABLE[key]]
    assert all(m.get("implementable") for m in P[sector][name]["methods"])
    assert round(sum(m["weight"] for m in P[sector][name]["methods"]), 6) == 1.0


def test_commodity_cycle_profiles_are_cyclical_and_midstream_is_not():
    for name in ("Integrated Oil & Gas", "Upstream Oil & Gas", "Refining & Marketing",
                 "Oilfield Services & Drilling", "Coal"):
        assert name in _CYCLICAL_PROFILES
    assert "Midstream / Pipelines" not in _CYCLICAL_PROFILES


def test_energy_subtype_rates_are_damodaran_january_2026():
    assert _ENERGY_PROFILE_WACC["Midstream / Pipelines"] == pytest.approx(0.058)       # Oil/Gas Distribution 5.78%
    assert _ENERGY_PROFILE_WACC["Oilfield Services & Drilling"] == pytest.approx(0.070)  # Oilfield Svcs/Equip. 7.04%
    assert _ENERGY_PROFILE_WACC["Refining & Marketing"] == pytest.approx(0.070)         # no row: Resources rate


# ── the two new methods ─────────────────────────────────────────────────────

_ROW = {"revenue": 20e9, "ebitda": 4e9, "net_income": 2e9, "operating_cash_flow": 3e9,
        "depreciation_and_amortization": 1e9, "net_debt": 1e9, "cash": 1e9, "total_debt": 2e9,
        "shares_outstanding": 200e6}
_PEER = {"ev_ebitda": 8.0, "pe": 14.0, "pb": 1.2, "fcf_yield": 0.06, "ev_ocf": 6.0}


def _call(method, row=None, peer=None, monkeypatch=None):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: dict(peer or _PEER))
    r = dict(row or _ROW)
    return _compute_method_value(
        method_name=method, most_recent=r, revenue_base=r["revenue"], shares=200e6,
        net_debt=r["net_debt"], market_cap=10e9, wacc=0.08, growth_base=0.03,
        fcf_margin_base=0.10, tgr=0.02, fcf_floor=0.0, sector="Energy", scenario="base",
        reported_currency="USD", is_hk=False, growth_premium=1.0, sbc_pe_discount=1.0,
        profile_name="Midstream / Pipelines", ticker="", end_date="2026-09-20")


def test_ev_ocf_is_ocf_times_the_peer_multiple_to_equity(monkeypatch):
    v = _call("EV/OCF", monkeypatch=monkeypatch)
    assert v == pytest.approx((3e9 * 6.0 - 1e9) / 200e6)          # 85.0


def test_ev_ocf_refuses_without_a_peer_reading(monkeypatch):
    peer = {k: v for k, v in _PEER.items() if k != "ev_ocf"}
    assert _call("EV/OCF", peer=peer, monkeypatch=monkeypatch) is None


def test_distributable_cf_uses_da_until_maintenance_capex_is_accepted(monkeypatch):
    v = _call("Distributable CF Yield", monkeypatch=monkeypatch)
    assert v == pytest.approx(((3e9 - 1e9) / 200e6) / 0.06)       # 166.67
    row = dict(_ROW, maintenance_capex_accepted=1.5e9)
    v2 = _call("Distributable CF Yield", row=row, monkeypatch=monkeypatch)
    assert v2 == pytest.approx(((3e9 - 1.5e9) / 200e6) / 0.06)


def test_distributable_cf_refuses_a_non_positive_amount(monkeypatch):
    row = dict(_ROW, depreciation_and_amortization=4e9)
    assert _call("Distributable CF Yield", row=row, monkeypatch=monkeypatch) is None


# ── comps: EV/OCF field and the same-market family basket ──────────────────

def test_ev_ocf_is_a_comps_field_with_a_band():
    assert "ev_ocf" in rc.FIELDS
    lo, hi = rc._BANDS["ev_ocf"]
    assert 0 < lo < hi


def test_family_membership():
    fam = "Oil, Gas & Coal (family)"
    for ind in WAVE1_ROWS:
        assert rc.family_of(ind) == fam
    assert rc.family_of("Regulated Electric") is None


def test_family_basket_pools_the_industries_in_market_cap_order():
    def row(sym, ind, cap):
        return {"symbol": sym, "name": sym, "sector": "Energy", "industry": ind, "market_cap": cap}
    rows = [row("A", "Oil & Gas Integrated", 9e11), row("B", "Solar", 8e11),
            row("C", "Oil & Gas Midstream", 7e11), row("D", "Coal", 6e11)]
    ind, _sec, symbols = rc.build_baskets(rows)
    assert [r["symbol"] for r in ind["Oil, Gas & Coal (family)"]] == ["A", "C", "D"]
    assert "B" in symbols


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "t.db"))
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(rc, "_tables_ready_key", None, raising=False)
    yield
    _db.close_all_connections()


def _seed(key, level, field, value, peers):
    from datetime import datetime, timezone
    rc.save_comps("HKSE", [{"level": level, "key": key, "cohort": "all", "field": field,
                            "value": value, "peer_count": peers, "min_market_cap": 0.0}],
                  datetime.now(timezone.utc).isoformat())


def test_family_rung_sits_between_industry_and_sector(store):
    """HK integrated oil has 4 names (under the floor of 5): the family pool,
    not the whole Energy sector with its coal, solar and utilities, prices it."""
    _seed("Oil & Gas Integrated", "industry", "ev_ebitda", 3.0, 4)
    _seed("Oil, Gas & Coal (family)", "industry", "ev_ebitda", 4.5, 18)
    _seed("Energy", "sector", "ev_ebitda", 9.0, 39)
    got = rc.get_regional_multiples("HKSE", "Oil & Gas Integrated", "Energy")
    assert got["ev_ebitda"]["value"] == 4.5
    assert got["ev_ebitda"]["key"] == "Oil, Gas & Coal (family)"


def test_a_thick_industry_basket_still_wins(store):
    _seed("Coal", "industry", "ev_ebitda", 5.0, 13)
    _seed("Oil, Gas & Coal (family)", "industry", "ev_ebitda", 4.5, 18)
    got = rc.get_regional_multiples("HKSE", "Coal", "Energy")
    assert got["ev_ebitda"]["value"] == 5.0


def test_singapore_never_takes_the_us_curated_basket(monkeypatch):
    """Seatrium, 2026-09-20: SGX baskets under the floor fell through to the US
    'dynamic' Industrials basket (18.9x book). Non-US markets use their own
    static table when their comps are thin."""
    from src.data import sector_profiles as sp
    monkeypatch.setattr(sp, "get_dynamic_peer_multiples", lambda *a, **k: {"pb": 18.86, "pe": 35.77})
    monkeypatch.setattr(sp, "_regional_peer_multiples", lambda *a, **k: {})
    sg = sp.get_sector_peer_multiples("Industrials", ticker="5E2.SI", exchange="SES",
                                      profile_name="Offshore Marine & Resources (SG)")
    assert sg.get("pb") != 18.86 and sg.get("pe") != 35.77
    us = sp.get_sector_peer_multiples("Industrials", ticker="CAT", exchange="NYSE")
    assert us.get("pb") == 18.86


def test_a_loss_making_cyclical_does_not_crash_the_peak_diagnostic():
    """Transocean, 2026-09-20: the day Oilfield Services & Drilling became a
    cyclical profile, RIG reached the peak-consensus diagnostic, whose text
    formatted `max_line` -- None when no year in the history had positive EPS --
    and the whole run raised TypeError."""
    import inspect
    from src.agents.analysis import dcf_agent as d
    src = inspect.getsource(d.run_dcf_agent)
    assert 'if _peak["max_line"] is not None else' in src
    peak = d._peak_consensus_trigger(
        [{"period": f"202{i}-12-31", "net_income": -1e8, "shares_outstanding": 1e8} for i in range(5)],
        {"eps": {"base": 0.5}})
    assert peak is None or peak["max_line"] is None


def test_an_empty_peer_set_says_so_rather_than_reading_as_measured():
    """Seatrium's run recorded `all_static: false` over zero fields, which reads
    as "these are live multiples" and means the opposite: SGX lists three energy
    names above the universe floor against a five-peer minimum, so there is no
    Singapore energy basket and every multiple came from profile defaults."""
    from src.agents.analysis.dcf_agent import _multiples_trace
    empty = _multiples_trace({"_comp_market": "SES", "_comp_age_days": 0.11})
    assert empty["fields"] == {}
    assert empty["no_peer_multiples"] is True
    assert empty["all_static"] is False

    live = _multiples_trace({"ev_ebitda": 8.0, "_comp_market": "SES",
                             "_comp_basis": {"ev_ebitda": {"basis": "live", "peer_count": 7}}})
    assert live["no_peer_multiples"] is False
