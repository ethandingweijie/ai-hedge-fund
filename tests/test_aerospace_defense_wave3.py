"""Wave 3: aerospace & defence, on the owner's analyst framework (2026-09-22).

One FMP label, `Aerospace & Defense`, covers three US businesses whose
clusters trade at 15x, 27x and unpriceable EV/EBITDA against a 23x basket
median that describes none of them (docs/wave3_stage3_methods_proposal.md).
The parent profile is retired; the label's row defaults to Defense Primes,
the rest are pinned, and each profile prices on a curated basket where one
exists. Labels were measured live on 2026-09-21 (docs/waves_2_5_stage2_probe.md).
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent
from src.data import industry_profile_map as ipm
from src.data import regional_comps as rc
from src.data import valuation_constants as vc
from src.data.sector_profiles import (INDUSTRY_VALUATION_PROFILES as P, SECTOR_PEER_MULTIPLES as US,
                                      TICKER_SECTOR_LOOKUP as pins, _INDUSTRIALS_PROFILE_WACC,
                                      classify_valuation_profile, wacc_base_breakdown)

PRIMES, COMM, NICHE, DTS = ("Defense Primes", "Commercial Aerospace & Engines",
                            "Niche Aerospace Components", "Defense Tech & Space")
HOLDCO, GA, GAENG, SG = ("Aerospace Holdco (HK)", "General Aviation (HK)", "GA Engines & Aftermarket (HK)",
                         "Aerospace & Engineering (SG)")

#: (method, weight, anchor) per profile, as the owner's framework maps onto the engine.
TABLE = {
    PRIMES: [("Backlog-coverage DCF", 0.40, True), ("EV/EBITDA (norm)", 0.25, False), ("FCF Yield", 0.20, False),
             ("EV/EBITDA", 0.15, False)],
    COMM:   [("EV/EBIT (norm)", 0.30, True), ("SOTP (analyst)", 0.25, False), ("EV/EBITDA", 0.25, False),
             ("P/E", 0.20, False)],
    NICHE:  [("PEG", 0.30, True), ("FCF Yield", 0.30, False), ("ROIC vs WACC", 0.20, False),
             ("Forward P/E", 0.20, False)],
    DTS:    [("EV/Fwd Rev", 0.45, True), ("Rev DCF (Target Margin)", 0.35, False), ("EV/Revenue", 0.20, False)],
    HOLDCO: [("DCF", 0.35, True), ("SOTP / NAV (look-through)", 0.35, False), ("Forward P/E", 0.30, False)],
    GA:     [("Forward P/E", 0.35, True), ("EV/Revenue", 0.25, False), ("Backlog-coverage DCF", 0.25, False),
             ("EV/EBITDA", 0.15, False)],
    GAENG:  [("EV/EBITDA", 0.45, True), ("P/BV", 0.35, False), ("FCF Yield", 0.20, False)],
    SG:     [("SOTP (analyst)", 0.35, False), ("DCF", 0.25, True), ("DDM", 0.25, False), ("EV/EBITDA", 0.15, False)],
}

#: The one label (US and HK), measured live 2026-09-21 on LMT NOC GD RTX BA GE
#: HWM TDG LHX KTOS AVAV RKLB 00232.HK 02357.HK; S63.SI on the SG table.
LABEL = "Aerospace & Defense"


# ── the split ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile", sorted(TABLE))
def test_each_profile_is_the_confirmed_table_leg_by_leg(profile):
    got = [(m["name"], m["weight"], bool(m.get("anchor"))) for m in P["Industrials"][profile]["methods"]]
    assert got == TABLE[profile]
    assert sum(w for _, w, _ in got) == pytest.approx(1.0)


def test_every_row_reached_anchor_is_computable_in_general():
    """Industry routing declines a profile whose anchor resolves to a proxy."""
    for profile in TABLE:
        anchors = [m for m in P["Industrials"][profile]["methods"] if m.get("anchor")]
        assert len(anchors) == 1 and anchors[0]["implementable"] is True, profile


def test_the_parent_profile_is_retired_and_nothing_reaches_it():
    from itertools import product
    assert LABEL not in P["Industrials"]
    assert LABEL not in {v[1] for v in pins.values()}
    assert LABEL not in {p for _, p in ipm.industry_map().values()}
    assert LABEL not in {p for _, p in ipm.ticker_overrides().values()}
    for cagr, fcf, de in product((-0.1, 0.05, 0.5), (-0.2, 0.1), (0.0, 3.0)):
        assert classify_valuation_profile("Industrials", cagr, fcf, de) != LABEL


def test_the_label_routes_to_the_primes_default_and_is_in_scope():
    assert ipm.profile_for_industry(LABEL) == ("Industrials", PRIMES)
    assert LABEL in ipm.routing_scope()
    # An unpinned newcomer errs toward the most conservative method set.
    assert ipm.profile_for_ticker("HII", LABEL) == ("Industrials", PRIMES)


def test_the_pins_are_the_owners_placements():
    want = {"LMT": PRIMES, "RTX": PRIMES, "NOC": PRIMES, "GD": PRIMES, "LHX": PRIMES,
            "BA": COMM, "GE": COMM, "HWM": NICHE, "TDG": NICHE, "HEI": NICHE,
            "KTOS": DTS, "AVAV": DTS, "RKLB": DTS,
            "02357.HK": HOLDCO, "02507.HK": GA, "00232.HK": GAENG}
    assert {t: pins[t][1] for t in want} == want
    # The SG names are pinned in the SG market registry, not TICKER_SECTOR_LOOKUP.
    from src.data.sector_profiles import SGX_TICKER_SECTOR_LOOKUP as sg_pins
    assert sg_pins["S63.SI"][1] == SG and sg_pins["S58.SI"][1] == SG


def test_sia_engineering_is_routed_by_ticker_and_the_airlines_label_never_enters_scope():
    """`Airlines, Airports & Air Services` maps to Transportation/Airlines for US
    carriers; putting the label in scope would move Delta and United."""
    assert ipm.in_routing_scope("S59.SI", "Airlines, Airports & Air Services")
    assert ipm.profile_for_ticker("S59.SI", "Airlines, Airports & Air Services") == ("Industrials", "Aviation & Marine (SG)")
    assert "Airlines, Airports & Air Services" not in ipm.routing_scope()
    assert ipm.profile_for_industry("Airlines, Airports & Air Services") == ("Transportation", "Airlines")


# ── the curated baskets ──────────────────────────────────────────────────────

def test_the_curated_baskets_are_the_named_lists_and_clear_the_floor():
    assert rc.PROFILE_PEER_BASKETS[PRIMES]["US"] == ("LMT", "RTX", "NOC", "GD", "LHX", "ESLT")
    assert rc.PROFILE_PEER_BASKETS[NICHE]["US"] == ("TDG", "HEI", "HWM", "CW", "WWD")
    for prof in (PRIMES, NICHE):
        assert len(rc.PROFILE_PEER_BASKETS[prof]["US"]) >= rc.MIN_INDUSTRY_PEERS
    assert COMM not in rc.PROFILE_PEER_BASKETS         # two names is not a peer set


def test_a_profile_basket_outranks_the_industry_median_for_every_field_it_resolves(monkeypatch):
    monkeypatch.setattr(rc, "profile_basket_multiples",
                        lambda ex, prof, **k: ({"pe": {"value": 21.0, "basis": "profile", "cohort": "all",
                                                       "peer_count": 6, "key": prof, "exchange": ex}}
                                               if prof == PRIMES else {}))
    from src.data import sector_profiles as sp
    monkeypatch.setattr(sp, "_regional_peer_multiples",
                        lambda *a, **k: {"pe": {"value": 36.3, "basis": "industry", "cohort": "all",
                                                "peer_count": 18, "key": LABEL, "exchange": "US"}})
    peer = sp.get_sector_peer_multiples("Industrials", profile_name=PRIMES, ticker="LMT", exchange="US")
    assert peer["pe"] == 21.0 and peer["_comp_basis"]["pe"]["basis"] == "profile"
    assert dcf_agent._basket_rank(peer, "pe") == 4 > 3


def test_a_field_under_the_floor_falls_to_the_industry_rung(monkeypatch):
    rows = [{"symbol": s, "metrics_json": '{"pe": {"value": 20, "in_band": true}, "pb": {"value": 4, "in_band": false}}',
             "computed_at": "2099-01-01T00:00:00+00:00"} for s in rc.PROFILE_PEER_BASKETS[PRIMES]["US"]]
    monkeypatch.setattr(rc, "_ensure_table", lambda: None)
    monkeypatch.setattr(rc._db, "query", lambda *a, **k: rows)
    out = rc.profile_basket_multiples("US", PRIMES)
    assert out["pe"]["peer_count"] == 6 and "pb" not in out


def test_ev_ebit_is_derived_from_the_two_ttm_calls_and_needs_a_positive_ebit_margin():
    assert rc.ev_ebit_from_ttm({"evToEBITDATTM": 14.3}, {"ebitdaMarginTTM": 0.155, "ebitMarginTTM": 0.117}) \
        == pytest.approx(14.3 * 0.155 / 0.117)
    assert rc.ev_ebit_from_ttm({"evToEBITDATTM": 14.3}, {"ebitdaMarginTTM": 0.05, "ebitMarginTTM": -0.02}) is None
    assert "ev_ebit" in rc.FIELDS and rc._BANDS["ev_ebit"] == (0.5, 120.0)


# ── the legs ─────────────────────────────────────────────────────────────────

KW = dict(revenue_base=7e10, shares=2.4e8, net_debt=1.8e10, market_cap=1.2e11, wacc=0.072, growth_base=0.05,
          fcf_margin_base=0.08, tgr=0.0275, fcf_floor=0.0, sector="Industrials", scenario="base")


def _leg(name, row, peer, monkeypatch, profile, **extra):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: dict(peer))
    return dcf_agent._traced_method_value(method_name=name, most_recent=row, profile_name=profile, **{**KW, **extra})


def test_ev_ebit_norm_prices_normalised_ebit_at_an_ev_ebit_median_and_declines_without_one(monkeypatch):
    row = {"normalized_ebit": 8e9}
    v, tr = _leg("EV/EBIT (norm)", row, {"ev_ebit": 19.0, "ev_ebitda": 15.0}, monkeypatch, COMM)
    assert v == pytest.approx((8e9 * 19.0 - 1.8e10) / 2.4e8)
    assert tr["multiple_parts"]["peer_source"] == "peer median ev_ebit"
    # Never EBIT at EV/EBITDA: without an EV/EBIT median the leg declines.
    assert _leg("EV/EBIT (norm)", row, {"ev_ebitda": 15.0}, monkeypatch, COMM)[0] is None
    assert _leg("EV/EBIT (norm)", {"normalized_ebit": -1e9}, {"ev_ebit": 19.0}, monkeypatch, COMM)[0] is None


def test_the_trailing_ev_ebit_leg_now_takes_the_ev_ebit_median_when_the_basket_has_one(monkeypatch):
    row = {"ebit": 5e9, "ebitda": 6e9}
    with_it, tr = _leg("EV/EBIT", row, {"ev_ebit": 19.0, "ev_ebitda": 15.0}, monkeypatch, PRIMES)
    without, tr2 = _leg("EV/EBIT", row, {"ev_ebitda": 15.0}, monkeypatch, PRIMES)
    assert tr["multiple_parts"]["peer_multiple"] == 19.0 and tr2["multiple_parts"]["peer_multiple"] == 15.0
    assert with_it > without


def test_peg_is_the_owner_set_ratio_times_consensus_eps_growth_on_ntm_eps(monkeypatch):
    monkeypatch.setattr(vc, "peg_ratio", lambda profile, doc=None: 2.2 if profile == NICHE else None)
    row = {"_eps_growth_fy2": 0.174, "net_income": 2e9}
    fc = {"eps": {"bear": 7.0, "base": 8.0, "bull": 9.0}}
    v, tr = _leg("PEG", row, {"pe": 39.0}, monkeypatch, NICHE, forward_consensus=fc)
    assert tr["multiple_parts"]["peer_multiple"] == pytest.approx(2.2 * 17.4)
    assert v == pytest.approx(8.0 * 2.2 * 17.4)
    assert "consensus EPS growth FY2/FY1" in tr["multiple_parts"]["peer_source"]
    assert "growth_premium" not in tr["multiple_parts"]                 # growth IS the multiplier
    # Scenarios come from the consensus EPS band, so they stay ordered.
    bear = _leg("PEG", row, {"pe": 39.0}, monkeypatch, NICHE, forward_consensus=fc, scenario="bear")[0]
    bull = _leg("PEG", row, {"pe": 39.0}, monkeypatch, NICHE, forward_consensus=fc, scenario="bull")[0]
    assert bear < v < bull


def test_peg_declines_without_a_constant_or_without_growth(monkeypatch):
    monkeypatch.setattr(vc, "peg_ratio", lambda profile, doc=None: None)
    assert _leg("PEG", {"_eps_growth_fy2": 0.2}, {}, monkeypatch, NICHE, forward_consensus={"eps": {"base": 8.0}})[0] is None
    monkeypatch.setattr(vc, "peg_ratio", lambda profile, doc=None: 2.2)
    assert _leg("PEG", {"_eps_growth_fy2": -0.05}, {}, monkeypatch, NICHE, forward_consensus={"eps": {"base": 8.0}})[0] is None


def test_the_peg_constant_is_recorded_with_its_market_derivation_for_the_owner_to_confirm():
    e = vc.entry(NICHE)
    assert vc.peg_ratio(NICHE) == 2.2 and e["peg_band"] == [1.5, 3.2]
    assert "TDG 1.47" in e["derivation"] and e["status"] == "OWNER_OVERRIDE_PENDING"
    assert vc.peg_detail(NICHE) == {"ratio": 2.2, "status": "OWNER_OVERRIDE_PENDING", "interval": [1.9, 2.5]}
    assert vc.peg_ratio(PRIMES) is None


def test_defense_primes_take_the_owners_terminal_growth_after_the_profile_is_final():
    assert dcf_agent._PROFILE_TGR[PRIMES] == {"bear": 0.020, "base": 0.0275, "bull": 0.030}
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    gate = src.index("Backlog-Gated Long Cycle: eligibility is a GATE")
    tgr = src.index("if profile_name in _PROFILE_TGR:")
    assert gate < tgr < src.index("# B4: an ACTIVE calibration")


def test_the_sotp_legs_read_owner_accepted_gemini_inputs_and_nothing_else():
    """Owner, 2026-09-22: 'tap on Gemini to get the business segments and also
    ask for the multiple range to adopt.' Review-gated like every input."""
    from src.data import industry_inputs as ii
    from src.agents.industry import gemini_params as gp
    assert "sotp" in ii.KINDS and "sotp" in ii.NO_OVERLAY and gp.INDUSTRY_INPUT_SCHEMAS["sotp"] is gp.SotpInputs
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    at = src.index('_ii_s.accepted_entry(ticker, "sotp")')
    assert "if not _ticker_sotp:" in src[at - 900: at]      # the pipeline's own assumptions still win
    assert "_to_engine(_sotp_e[\"data\"])" in src[at: at + 400]


def test_accepted_entry_is_the_same_gate_as_every_other_kind(tmp_path, monkeypatch):
    from src.data import db as _db
    from src.data import industry_inputs as ii
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(ii, "_reviews_ready_key", None)
    doc = {"version": 1, "tickers": {"BA": {"sotp": {"data": {"fiscal_year": "FY2026E", "segments": [
        {"name": "BCA", "revenue_fwd": {"value": 40, "currency": "USD", "scale": "bn", "period": "FY2026E",
                                        "source_url": "https://x", "quote": "q"},
         "multiple_metric": "ev_rev", "multiple_low": 1.5, "multiple_high": 2.0, "multiple_basis": "b",
         "multiple_source_url": "https://x"}], "holdco_discount_pct": 0.0, "holdco_basis": ""},
        "checks": [], "ok": True}}}}
    try:
        assert ii.accepted_entry("BA", "sotp", doc) is None
        ii.set_review("BA", "sotp", "accepted", "owner", doc=doc)
        assert ii.accepted_entry("BA", "sotp", doc)["data"]["segments"][0]["name"] == "BCA"
    finally:
        _db.close_all_connections()


# ── rates, statics, bands, sets ──────────────────────────────────────────────

def test_the_wacc_rows_are_damodarans_aerospace_rate_and_the_registry_serves_them():
    assert _INDUSTRIALS_PROFILE_WACC == {PRIMES: 0.072, COMM: 0.072, NICHE: 0.072, DTS: 0.090}
    b = wacc_base_breakdown("Industrials", profile=PRIMES)
    assert b["table"] == "Industrials profile WACC (Damodaran)" and b["table_rate"] == pytest.approx(0.072)


def test_the_statics_are_the_curated_baskets_medians_and_defense_tech_has_none():
    assert (US[PRIMES]["ev_ebitda"], US[PRIMES]["pe"], US[PRIMES]["ev_ebit"]) == (15.3, 23.1, 19.0)
    assert (US[NICHE]["ev_ebitda"], US[NICHE]["pe"]) == (26.4, 39.1)
    assert (US[COMM]["ev_ebitda"], US[COMM]["pe"]) == (27.2, 40.2)
    assert DTS not in US and LABEL not in US


def test_the_owners_bands_moved_with_the_split():
    assert vc.multiple_band("US", PRIMES, "ev_ebitda") == (14.0, 18.0, f"US/{PRIMES}")
    assert vc.multiple_band("US", NICHE, "pe") == (30.0, 45.0, f"US/{NICHE}")
    for prof in (HOLDCO, GA, GAENG):
        assert vc.multiple_band("HKSE", prof, "pe") == (12.0, 18.0, f"HKSE/{prof}")
    assert vc.multiple_band("US", LABEL, "pe") is None


def test_commercial_aerospace_is_cyclical_with_the_fade_and_the_backlog_profiles_take_the_bear_floor():
    assert COMM in dcf_agent._CYCLICAL_PROFILES and COMM in dcf_agent._CONVERGENCE_ALPHA_PROFILES
    assert {PRIMES, DTS, GA} <= dcf_agent._BACKLOG_VISIBILITY_PROFILES


# ── the three engine rules (owner, 2026-09-23) ───────────────────────────────

def test_rule_1_the_target_margin_dcf_survives_the_oe_gate_and_ramps_to_the_owners_margin(monkeypatch):
    """A loss-maker keeps the projection family's 0.35: the leg ramps from
    today's margin to an owner-set terminal EBIT margin and is exempt from the
    OE<=0 gate. No EV/Backlog: one constant cannot serve KTOS and RKLB."""
    assert "Rev DCF (Target Margin)" in dcf_agent._OE_GATE_EXEMPT <= dcf_agent._DCF_PROJECTION_FAMILY
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    assert "and method_name not in _OE_GATE_EXEMPT" in src
    assert vc.target_margin("RKLB", DTS)["ebit_margin"] == 0.165 and vc.target_margin("KTOS", DTS)["ebit_margin"] == 0.13
    assert vc.target_margin("KTOS", DTS)["band"] == [0.12, 0.14] and vc.target_margin("XYZ", "Tech") is None
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {})
    row = {"ebit": -1.0e8}                                       # loss-making today
    v, tr = dcf_agent._traced_method_value(
        method_name="Rev DCF (Target Margin)", most_recent=row, revenue_base=1.2e9, shares=1.5e8, net_debt=-2e8,
        market_cap=1.0e10, wacc=0.09, growth_base=0.15, fcf_margin_base=-0.05, tgr=0.025, fcf_floor=0.02,
        sector="Industrials", scenario="base", profile_name=DTS, ticker="KTOS",
        projection={"growth_schedule": [0.15] * 10})
    assert v is not None and v > 0
    path = tr["target_margin"]["ebit_margin_path"]
    assert path[0] == pytest.approx(-1e8 / 1.2e9 + (0.13 + 1e8 / 1.2e9) / 5, abs=1e-6)   # the trace rounds to 6 dp
    assert path[4] == pytest.approx(0.13)
    assert path[9] == pytest.approx(0.13) and tr["fcf_floor"] == -1.0    # the floor is lifted for the ramp
    assert tr["projection_rows"][0]["fcf_margin"] < 0 < tr["projection_rows"][-1]["fcf_margin"]
    assert "EV/Backlog" not in {m["name"] for m in P["Industrials"][DTS]["methods"]}


def test_rule_2_the_peg_runs_as_the_active_baseline_and_carries_its_sensitivity(monkeypatch):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"pe": 39.0})
    row = {"_eps_growth_fy2": 0.174, "net_income": 2e9}
    v, tr = dcf_agent._traced_method_value(
        method_name="PEG", most_recent=row, profile_name=NICHE, forward_consensus={"eps": {"base": 8.0}}, **KW)
    assert v == pytest.approx(8.0 * 2.2 * 17.4)                 # never halted: 2.2 is the baseline
    o = tr["owner_override"]
    assert o["status"] == "OWNER_OVERRIDE_PENDING" and o["interval"] == [1.9, 2.5]
    assert o["leg_at_low"] == pytest.approx(8.0 * 1.9 * 17.4) and o["leg_at_high"] == pytest.approx(8.0 * 2.5 * 17.4)
    assert "OWNER_OVERRIDE_PENDING" in tr["multiple_parts"]["peer_source"]
    # ...and the Summary sheet states it (workbook), the run flags it (engine).
    from src.utils import valuation_workbook as wb
    assert "Owner overrides pending" in inspect.getsource(wb)
    assert "[OWNER_OVERRIDE_PENDING]: active baseline" in inspect.getsource(dcf_agent.run_dcf_agent)


def test_rule_3_the_analyst_sotp_takes_precedence_and_the_lookthrough_goes_shadow():
    profile = {"methods": [{"name": "DCF", "weight": 0.35, "anchor": True, "implementable": True},
                           {"name": "SOTP / NAV (look-through)", "weight": 0.35, "anchor": False,
                            "implementable": False, "proxy": "P/BV"},
                           {"name": "Forward P/E", "weight": 0.30, "anchor": False, "implementable": True}]}
    out = dcf_agent._promote_sotp_analyst_profile(profile, True, shadow_lookthrough=True)
    names = [m["name"] for m in out["methods"]]
    assert "SOTP (analyst)" in names and "SOTP / NAV (look-through)" not in names
    assert out["shadow_methods"] == ["SOTP / NAV (look-through)"]
    # Scoped: a look-through that COMPLETES keeps the earlier decision that the
    # SOTP family shares the promoted weight (test_valuation_fixes_0916.py).
    kept = dcf_agent._promote_sotp_analyst_profile(profile, True, shadow_lookthrough=False)
    assert "SOTP / NAV (look-through)" in [m["name"] for m in kept["methods"]] and "shadow_methods" not in kept
    assert "shadow_lookthrough=not _lt_completes" in inspect.getsource(dcf_agent.run_dcf_agent)
    assert profile["methods"][1]["name"] == "SOTP / NAV (look-through)"     # copy-on-write: the table is untouched
    assert dcf_agent._promote_sotp_analyst_profile(profile, False) is profile  # no assumptions, no change
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    assert 'for _shadow in (profile_data.get("shadow_methods") or []):' in src
    assert '"gate_id": "GATE_SOTP_PRECEDENCE"' in src and "holding-company spread), unweighted" in src
