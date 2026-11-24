"""Relative-valuation (peer multiples) mechanics, owner 2026-11-25.

1. the valued company is never inside its own median (label path and curated path alike);
2. every basket median is screened (MAD trim, market-cap sanity) and the floor applies after the screen;
3. no growth premium on through-cycle legs or cyclical profiles; forward growth compared with forward growth;
4. the through-cycle multiple is re-based onto the screened basket, and a sector-level one is refused;
5. the depleting-asset DCF without accepted reserves declines, it never grows at growth_base;
6. one peer set per subject (anchor basket first) and no sector rung for cyclical / industrial profiles;
7. the trailing P/E leg does not capitalise non-recurring income.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import _compute_method_value
from src.data import regional_comps as rc
from src.data import sector_profiles as sp


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


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed_basket(exchange, level, key, field, members, cohort="all", stored=None, mcaps=None, extra=None):
    """Store a median row and its named members. members: {symbol: value}."""
    import statistics
    vals = list(members.values())
    rc.save_comps(exchange, [{"level": level, "key": key, "cohort": cohort, "field": field,
                              "value": stored if stored is not None else statistics.median(vals),
                              "peer_count": len(vals), "min_market_cap": 0.0}], _now())
    rows = []
    for i, (sym, v) in enumerate(members.items()):
        metrics = {field: {"value": v, "in_band": True}}
        metrics.update((extra or {}).get(sym) or {})
        rows.append({"level": level, "key": key, "cohort": cohort, "symbol": sym, "name": sym,
                     "market_cap": (mcaps or {}).get(sym, 1e10 * (i + 1)), "metrics": metrics})
    return rows


def _save_members(exchange, rows):
    """One members-store row per (basket, cohort, symbol), its fields merged -- the store's shape."""
    merged: dict = {}
    for r in rows:
        k = (r["level"], r["key"], r["cohort"], r["symbol"])
        if k in merged:
            merged[k]["metrics"].update(r["metrics"])
        else:
            merged[k] = {**r, "metrics": dict(r["metrics"])}
    rc.save_members(exchange, list(merged.values()), _now())


# ── 1. never the subject ─────────────────────────────────────────────────────

def test_the_subject_is_never_kept_in_its_own_median(store):
    """GM's P/E: five members with GM, four without -> the stored 17.49 (GM's own) must not price GM."""
    rows = _seed_basket("US", "industry", "Auto - Manufacturers", "pe",
                        {"TM": 9.6, "RACE": 38.06, "GM": 17.49, "STLA": 156.52, "BLBD": 6.69})
    _save_members("US", rows)
    diag = {}
    got = rc.get_regional_multiples("US", "Auto - Manufacturers", "Consumer Cyclical",
                                    exclude_symbol="GM", diagnostics=diag)
    assert "pe" not in got
    assert "pe" in diag["dropped"] and "the subject" in diag["dropped"]["pe"][0]


def test_with_the_subject_out_the_next_rung_prices_the_field(store):
    rows = _seed_basket("US", "industry", "Auto - Manufacturers", "pe",
                        {"TM": 9.6, "RACE": 38.0, "GM": 17.5, "HMC": 9.0, "BLBD": 6.7})
    rows += _seed_basket("US", "sector", "Consumer Cyclical", "pe",
                         {f"S{i}": 20.0 + i for i in range(10)})
    _save_members("US", rows)
    got = rc.get_regional_multiples("US", "Auto - Manufacturers", "Consumer Cyclical", exclude_symbol="GM")
    assert got["pe"]["basis"] == "sector"
    assert "GM" not in [m["symbol"] for m in got["pe"]["members_used"]]


def test_the_subject_is_removed_and_the_median_retaken_when_enough_remain(store):
    rows = _seed_basket("US", "industry", "Oil & Gas Refining & Marketing", "pe",
                        {"MPC": 7.17, "PSX": 7.61, "VLO": 6.23, "DINO": 4.42, "PBF": 2.7, "DK": 3.1, "UGP": 6.2})
    _save_members("US", rows)
    got = rc.get_regional_multiples("US", "Oil & Gas Refining & Marketing", "Energy", exclude_symbol="VLO")
    assert got["pe"]["subject_excluded"] is True
    assert got["pe"]["peer_count"] == 6
    assert got["pe"]["value"] == pytest.approx((4.42 + 6.2) / 2)
    assert got["pe"]["label_value"] == pytest.approx(6.2)


# ── 2. outlier screen ────────────────────────────────────────────────────────

def test_robust_trim_drops_the_order_of_magnitude_outlier_only():
    pairs = [("CNQ", 5.2), ("EOG", 5.03), ("OXY", 4.19), ("FANG", 7.66), ("DVN", 5.76),
             ("WDS", 5.06), ("EQT", 5.35), ("EXE", 3.56), ("TPL", 21.67)]
    kept, trimmed = rc.robust_trim("ev_ebitda", pairs)
    assert [s for s, _ in trimmed] == ["TPL"]
    assert len(kept) == 8


def test_robust_trim_leaves_small_and_flat_baskets_alone():
    assert rc.robust_trim("pe", [("A", 1.0), ("B", 50.0), ("C", 5.0), ("D", 6.0)])[1] == []
    assert rc.robust_trim("pe", [(str(i), 10.0) for i in range(6)])[1] == []


def test_a_label_median_is_trimmed_and_names_the_trimmed_member(store):
    rows = _seed_basket("US", "industry", "Oil & Gas Exploration & Production", "ev_ebitda",
                        {"CNQ": 5.2, "EOG": 5.03, "OXY": 4.19, "FANG": 7.66, "DVN": 5.76, "WDS": 5.06,
                         "EQT": 5.35, "EXE": 3.56, "TPL": 21.67, "COP": 5.3})
    _save_members("US", rows)
    got = rc.get_regional_multiples("US", "Oil & Gas Exploration & Production", "Energy", exclude_symbol="COP")
    f = got["ev_ebitda"]
    assert [m["symbol"] for m in f["members_trimmed"]] == ["TPL"]
    assert f["peer_count"] == 8
    assert "TPL" not in [m["symbol"] for m in f["members_used"]]


def test_a_label_left_under_the_floor_by_the_trim_falls_to_the_next_rung(store):
    rows = _seed_basket("US", "industry", "X", "pe", {"A": 10.0, "B": 10.5, "C": 11.0, "D": 9.5, "E": 95.0})
    rows += _seed_basket("US", "sector", "Y", "pe", {f"S{i}": 15.0 + 0.1 * i for i in range(9)})
    _save_members("US", rows)
    got = rc.get_regional_multiples("US", "X", "Y")
    assert got["pe"]["basis"] == "sector"


def test_a_member_whose_multiples_sit_on_a_wrong_market_cap_is_dropped(store):
    extra = {"STLA": {"_mcap_multiples": {"value": 4e9, "in_band": False}}}
    rows = _seed_basket("US", "industry", "Autos", "pb",
                        {"TM": 1.03, "HMC": 0.65, "F": 1.21, "STLA": 0.15, "LI": 1.13, "RIVN": 1.6},
                        mcaps={"STLA": 4e10}, extra=extra)
    _save_members("US", rows)
    got = rc.get_regional_multiples("US", "Autos", "Consumer Cyclical")
    assert got["pb"]["excluded_mcap_mismatch"] == ["STLA"]
    assert got["pb"]["peer_count"] == 5


def test_the_curated_basket_is_screened_too(store):
    rows = []
    for i, (s_, v) in enumerate({"A": 10.0, "B": 11.0, "C": 12.0, "D": 10.5, "E": 11.5, "F": 90.0}.items()):
        rows.append({"level": "industry", "key": "K", "cohort": "all", "symbol": s_, "name": s_,
                     "market_cap": 1e10, "metrics": {"pe": {"value": v, "in_band": True}}})
    _save_members("US", rows)
    got = rc.basket_multiples("US", ("A", "B", "C", "D", "E", "F"), "Curated")
    assert got["pe"]["members_trimmed"] == [{"symbol": "F", "value": 90.0}]
    assert got["pe"]["peer_count"] == 5


def test_stored_medians_are_screened_at_refresh():
    basket = {"K": [{"symbol": f"S{i}", "market_cap": 1e10} for i in range(6)]}
    metrics = {f"S{i}": {"pe": v} for i, v in enumerate([12.0, 13.0, 14.0, 15.0, 16.0, 120.0])}
    rows = rc.compute_medians(basket, metrics, "industry", rc.MIN_INDUSTRY_PEERS)
    pe = [r for r in rows if r["field"] == "pe" and r["cohort"] == "all"][0]
    assert pe["peer_count"] == 5 and pe["value"] == pytest.approx(14.0)


# ── 6. one peer set; no sector rung for cyclicals ────────────────────────────

def test_every_field_tries_the_anchor_basket_first(store):
    """EV/EBITDA (anchor) has no large-cohort reading; P/E has both -> P/E comes from the anchor's cohort."""
    rows = _seed_basket("US", "industry", "K", "ev_ebitda", {f"A{i}": 8.0 + 0.1 * i for i in range(6)})
    rows += _seed_basket("US", "industry", "K", "pe", {f"A{i}": 12.0 + 0.1 * i for i in range(6)})
    rows += _seed_basket("US", "industry", "K", "pe", {f"A{i}": 20.0 + 0.1 * i for i in range(5)}, cohort="large")
    _save_members("US", rows)
    diag = {}
    got = rc.get_regional_multiples("US", "K", "Sec", market_cap=1e12, anchor_field="ev_ebitda", diagnostics=diag)
    assert got["ev_ebitda"]["cohort"] == "all"
    assert got["pe"]["cohort"] == "all"
    assert diag["anchor"]["cohort"] == "all"
    # without an anchor the ladder takes each field's first rung, as before
    assert rc.get_regional_multiples("US", "K", "Sec", market_cap=1e12)["pe"]["cohort"] == "large"


def test_the_sector_rung_is_refused_when_blocked(store):
    rows = _seed_basket("US", "sector", "Consumer Cyclical", "pe", {f"S{i}": 30.0 + i for i in range(10)})
    _save_members("US", rows)
    diag = {}
    assert rc.get_regional_multiples("US", "Auto - Manufacturers", "Consumer Cyclical")["pe"]["basis"] == "sector"
    got = rc.get_regional_multiples("US", "Auto - Manufacturers", "Consumer Cyclical", block_sector=True,
                                    diagnostics=diag)
    assert "pe" not in got
    assert "sector rung refused" in diag["dropped"]["pe"][0]


def test_cyclical_and_industrial_profiles_block_the_sector_rung():
    assert sp.sector_rung_blocked("Automotive (OEM)")
    assert sp.sector_rung_blocked("Refining & Marketing")
    assert sp.sector_rung_blocked("Capital Goods")
    assert not sp.sector_rung_blocked("Mature SaaS")
    assert sp.profile_anchor_field("Memory / DRAM-NAND") == "pe"
    assert sp.profile_anchor_field("Automotive (OEM)") == "ev_ebitda"


def test_a_sector_reading_never_reaches_a_cyclical_valuation_and_says_why(monkeypatch):
    monkeypatch.setattr(sp, "_regional_peer_multiples", lambda *a, **k: {
        "pe": {"value": 30.69, "basis": "sector", "cohort": "all", "peer_count": 40, "key": "Consumer Cyclical",
               "exchange": "US"},
        "ev_ebitda": {"value": 9.0, "basis": "industry", "cohort": "all", "peer_count": 6,
                      "key": "Auto - Manufacturers", "exchange": "US"}})
    monkeypatch.setattr(sp, "get_dynamic_peer_multiples", lambda *a, **k: {})
    out = sp.get_sector_peer_multiples("Industrials", profile_name="Automotive (OEM)", ticker="", exchange="NYSE")
    assert out["pe"] != 30.69
    assert out["_comp_basis"]["pe"]["basis"] == "static"
    assert "sector rung refused" in out["_comp_basis"]["pe"]["live_dropped"][0]
    assert out["ev_ebitda"] == 9.0


# ── 3. growth premium ────────────────────────────────────────────────────────

def test_no_growth_premium_on_through_cycle_legs_or_cyclical_profiles():
    w = dcf_agent._growth_premium_withheld
    assert w("EV/EBITDA (norm)", "Mature SaaS")
    assert w("P/E (norm)", "Mature SaaS")
    assert w("P/B-ROE (mid-cycle)", "Mature SaaS")
    assert w("EV/EBITDA (Norm)", "Mature SaaS")
    assert w("P/BV", "Refining & Marketing")
    assert w("Forward P/E", "Upstream Oil & Gas")
    assert w("P/E", "Mature SaaS") is None
    assert w("Forward EV/EBITDA", "Mature SaaS") is None


_ROW = {"revenue": 20e9, "ebitda": 4e9, "net_income": 2e9, "operating_cash_flow": 3e9,
        "depreciation_and_amortization": 1e9, "net_debt": 1e9, "shares_outstanding": 200e6,
        "total_equity": 10e9, "normalized_ebitda": 4e9, "normalized_net_income": 2e9}
_PEER = {"ev_ebitda": 8.0, "pe": 14.0, "pb": 1.2, "fcf_yield": 0.06, "ev_ocf": 6.0}


def _call(method, monkeypatch, *, row=None, peer=None, profile="Mature SaaS", growth_premium=1.3,
          forward_consensus=None, fcf_margin_base=0.10, growth_base=0.03, sector="Tech"):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: dict(peer or _PEER))
    r = dict(row or _ROW)
    return _compute_method_value(
        method_name=method, most_recent=r, revenue_base=r["revenue"], shares=200e6,
        net_debt=r["net_debt"], market_cap=10e9, wacc=0.08, growth_base=growth_base,
        fcf_margin_base=fcf_margin_base, tgr=0.02, fcf_floor=0.0, sector=sector, scenario="base",
        reported_currency="USD", is_hk=False, growth_premium=growth_premium, sbc_pe_discount=1.0,
        profile_name=profile, ticker="", end_date="2026-11-25", forward_consensus=forward_consensus)


def test_a_normalised_leg_ignores_the_growth_premium(monkeypatch):
    monkeypatch.setenv("DYNAMIC_MULTIPLES_ENABLED", "false")
    kw = dict(profile="Capital Goods", sector="Industrials", growth_premium=1.3)
    v = _call("EV/EBITDA (norm)", monkeypatch, **kw)
    assert v == pytest.approx((4e9 * 8.0 - 1e9) / 200e6)
    v_plain = _call("EV/EBITDA", monkeypatch, **kw)
    assert v_plain == pytest.approx((4e9 * 8.0 * 1.3 - 1e9) / 200e6)


def test_a_cyclical_profile_carries_no_premium_on_any_leg(monkeypatch):
    v = _call("P/BV", monkeypatch, profile="Refining & Marketing", growth_premium=1.3, sector="Energy")
    assert v == pytest.approx(10e9 / 200e6 * 1.2)


def test_peer_forward_growth_is_read_per_member_off_ttm_and_ntm_ev_revenue():
    mem = lambda vals: [{"symbol": s_, "value": v} for s_, v in vals.items()]     # noqa: E731
    peer = {"ev_revenue": 2.2, "ev_revenue_ntm": 2.0,
            "_comp_basis": {"ev_revenue": {"basis": "industry", "key": "K", "cohort": "all",
                                           "members_used": mem({"A": 2.2, "B": 3.3, "C": 1.1})},
                            "ev_revenue_ntm": {"basis": "industry", "key": "K", "cohort": "all",
                                               "members_used": mem({"A": 2.0, "B": 3.0, "C": 1.0})}}}
    g, note = dcf_agent._peer_forward_growth(peer)
    assert g == pytest.approx(0.10)
    assert "3 members" in note


def test_forward_adjustment_compares_forward_with_forward(monkeypatch):
    peer = {"growth_avg": 0.20, "ev_revenue": 2.2, "ev_revenue_ntm": 2.0,
            "_comp_basis": {"ev_revenue": {"basis": "industry", "key": "K", "cohort": "all"},
                            "ev_revenue_ntm": {"basis": "industry", "key": "K", "cohort": "all"}}}
    fc = {"_fy1_fy2": {"revenue": {"base": (100.0, 110.0)}}}
    f, note = dcf_agent._forward_growth_adjustment_raw(fc, peer)
    assert f == pytest.approx(1.0)          # +10% vs the peers' NTM +10%, not their trailing +20%
    assert "NTM" in note
    f2, note2 = dcf_agent._forward_growth_adjustment(fc, {**peer, "_growth_premium_withheld": "cyclical"})
    assert f2 == 1.0 and "no growth premium" in note2


# ── 4. through-cycle multiple ────────────────────────────────────────────────

def test_a_sector_level_through_cycle_multiple_is_refused(monkeypatch):
    monkeypatch.setenv("DYNAMIC_MULTIPLES_ENABLED", "true")
    peer = {"pe": 9.5, "_comp_basis": {"pe": {"basis": "sector", "key": "Consumer Cyclical", "exchange": "US"}}}
    m, src = dcf_agent._dynamic_norm_multiple(peer, "pe", None, "P/E (norm)")
    assert m == 9.5
    assert "sector-level through-cycle multiple refused" in src


def test_the_through_cycle_multiple_is_rebased_onto_the_screened_basket(monkeypatch):
    from src.data import dynamic_multiples as dm
    monkeypatch.setenv("DYNAMIC_MULTIPLES_ENABLED", "true")
    monkeypatch.setattr(dm, "current_multiple", lambda *a, **k: {"multiple": 8.28, "effective_at": "2026-09-21"})
    monkeypatch.setattr(rc, "basket_field_values", lambda *a, **k: [])
    peer = {"pe": 5.33, "_comp_basis": {"pe": {
        "basis": "industry", "key": "Oil & Gas Refining & Marketing", "exchange": "US", "label_value": 6.23,
        "members_used": [{"symbol": s_} for s_ in ("MPC", "PSX", "DINO", "PBF", "UGP", "DK")]}}}
    m, src = dcf_agent._dynamic_norm_multiple(peer, "pe", None, "P/E (norm)")
    assert m == pytest.approx(8.28 * 5.33 / 6.23)
    assert "re-based" in src
    # the screen moved nothing -> the label's multiple stands
    peer["_comp_basis"]["pe"]["label_value"] = 5.33
    m2, _ = dcf_agent._dynamic_norm_multiple(peer, "pe", None, "P/E (norm)")
    assert m2 == pytest.approx(8.28)


def test_member_through_cycle_readings_win_when_the_store_has_them(monkeypatch):
    from src.data import dynamic_multiples as dm
    monkeypatch.setattr(dm, "current_multiple",
                        lambda *a, **k: {"multiple": 42.0, "market_now": 40.0, "effective_at": "2026-09-21"})
    monkeypatch.setattr(rc, "basket_field_values", lambda *a, **k: [20.0, 22.0, 24.0, 18.0, 21.0])
    res = dm.basket_through_cycle("US", "industry", "Semiconductors", "pe_norm",
                                  {"members_used": [{"symbol": s_} for s_ in "ABCDE"], "label_value": 30.0}, 28.0)
    assert res["multiple"] == pytest.approx(42.0 * 21.0 / 40.0)
    assert res["rebase"]["method"] == "member through-cycle readings"
    assert res["members"] == list("ABCDE")


# ── 5. depleting asset DCF ───────────────────────────────────────────────────

def test_unanchored_depleting_dcf_declines_and_ignores_growth_base(monkeypatch):
    m = dcf_agent._DEPLETING_DCF
    kw = dict(row=dict(_ROW), profile="Upstream Oil & Gas", sector="Energy", growth_premium=1.0)
    v1 = _call(m, monkeypatch, growth_base=0.115, **kw)
    v2 = _call(m, monkeypatch, growth_base=-0.05, **kw)
    assert v1 == pytest.approx(v2)
    pv = sum(20e9 * 0.95 ** t * 0.10 / 1.08 ** t for t in range(1, 16))
    assert v1 == pytest.approx((pv - 1e9) / 200e6)
    miner = _call(m, monkeypatch, growth_base=0.115, row=dict(_ROW), profile="Base Metals", sector="Materials")
    assert miner == pytest.approx((sum(20e9 * 0.97 ** t * 0.10 / 1.08 ** t for t in range(1, 16)) - 1e9) / 200e6)


# ── 7. trailing P/E on non-recurring income ──────────────────────────────────

def test_trailing_pe_does_not_capitalise_a_disposal_gain(monkeypatch):
    """BA: TTM NI $2.235bn (Jeppesen gain), NTM EPS $0.84 -> the trailing leg prices normalised core earnings."""
    row = dict(_ROW, net_income=2.235e9, normalized_net_income=-1.0e9)
    fc = {"eps": {"base": 0.84}}
    assert _call("P/E", monkeypatch, row=row, forward_consensus=fc, growth_premium=1.0) is None
    row2 = dict(row, normalized_net_income=0.5e9)
    v = _call("P/E", monkeypatch, row=row2, forward_consensus=fc, growth_premium=1.0)
    assert v == pytest.approx(0.5e9 / 200e6 * 14.0)


def test_trailing_pe_stands_when_trailing_is_in_line_with_consensus(monkeypatch):
    fc = {"eps": {"base": 9.0}}                 # TTM EPS 10.0 <= 2 x 9.0
    v = _call("P/E", monkeypatch, forward_consensus=fc, growth_premium=1.0)
    assert v == pytest.approx(2e9 / 200e6 * 14.0)


def test_trailing_pe_flag_names_the_basis():
    ni, label, flag = dcf_agent._trailing_pe_earnings({"net_income": 2.235e9, "normalized_net_income": 0.4e9},
                                                      2.235e9, 790e6, {"eps": {"base": 0.84}})
    assert ni == 0.4e9 and flag["basis"] == "normalised" and "non-recurring" in label
    ni2, label2, flag2 = dcf_agent._trailing_pe_earnings({"net_income": 2.2e9, "net_income_core": 1.0e9},
                                                         1.0e9, 790e6, None)
    assert ni2 == 1.0e9 and flag2["basis"] == "core" and "core" in label2
