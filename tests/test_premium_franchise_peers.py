"""Premium-franchise peers, owner 2026-11-25.

1. COST prices on the membership / scale grocers (WMT, PSMT, BJ), a thin basket with its own floor of 3;
2. AAPL's hardware + services SOTP takes live forward P/E medians on NTM earnings, the owner's fixed
   16.5x / 29x only as the fallback, and keeps the owner's bear / bull band around the base;
3. the report says what the price requires (reverse DCF against the model and the company's own record).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agents.analysis import dcf_agent as d
from src.data import regional_comps as rc
from src.utils.report_disclosures import priced_in

MEM = "Membership / Subscription Retail"
HW, SVC = "Consumer Technology Ecosystem: Hardware", "Consumer Technology Ecosystem: Services"


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


def _members(exchange, level, key, values: dict, field="pe", more=()):
    """One exchange's rows in one write (save_members replaces the exchange). more: extra (key, values)."""
    rows = [{"level": level, "key": k, "cohort": "all", "symbol": s, "name": s, "market_cap": 1e11,
             "metrics": {field: {"value": v, "in_band": True}}}
            for k, vals in ((key, values), *more) for s, v in vals.items()]
    rc.save_members(exchange, rows, datetime.now(timezone.utc).isoformat())


# ── 1. COST ──────────────────────────────────────────────────────────────────

def test_cost_basket_is_the_membership_grocers_with_cost_excluded(store):
    assert rc.PROFILE_PEER_BASKETS[MEM]["US"] == ("COST", "WMT", "PSMT", "BJ")
    assert rc.BASKET_FLOORS[MEM] == 3
    _members("US", "industry", "Discount Stores", {"COST": 52.5, "WMT": 50.5, "PSMT": 32.3, "BJ": 20.1})
    got = rc.profile_basket_multiples("US", MEM, exclude="COST")
    assert got["pe"]["value"] == pytest.approx(32.3)
    assert got["pe"]["members"] == ["WMT", "PSMT", "BJ"] or sorted(got["pe"]["members"]) == ["BJ", "PSMT", "WMT"]
    assert got["pe"]["subject_excluded"] is True


def test_a_thin_basket_below_its_own_floor_resolves_nothing(store):
    _members("US", "industry", "Discount Stores", {"COST": 52.5, "WMT": 50.5, "BJ": 20.1})
    assert "pe" not in rc.profile_basket_multiples("US", MEM, exclude="COST")


def test_other_baskets_keep_the_standard_floor(store):
    _members("US", "industry", "Aerospace & Defense", {"TDG": 40.0, "HEI": 60.0, "HWM": 45.0})
    assert "pe" not in rc.basket_multiples("US", ("TDG", "HEI", "HWM"), "Niche Aerospace Components")


# ── 2. AAPL SOTP ─────────────────────────────────────────────────────────────

_ROW = {"segment_breakdown": {"iPhone": 254e9, "Mac": 38e9, "iPad": 28e9, "Wearables": 36e9, "Service": 123e9},
        "net_income": 132e9, "interest_income": 0.0}
_KW = dict(ticker="AAPL", net_debt=-56.8e9, shares=14.59e9, sector="Tech", revenue_base=479e9, market_cap=5e12,
           growth_base=0.07, fcf_margin_base=0.24, tgr=0.0275, fcf_floor=0.0,
           profile_name="Consumer Technology Ecosystem", wacc=0.10, scenario="base")


def _sotp(monkeypatch, fc, scenario="base"):
    monkeypatch.setattr(d, "get_sector_peer_multiples", lambda *a, **k: {})
    return d._traced_method_value(method_name="SOTP (Hardware + Services)", most_recent=dict(_ROW),
                                  forward_consensus=fc, **{**_KW, "scenario": scenario})


def test_sotp_prices_segments_on_live_forward_pe_and_ntm_earnings(store, monkeypatch):
    _members(rc.CROSS_MARKET_EXCHANGE, "profile", HW, {"SONY": 20.4, "RMS.PA": 26.2, "MC.PA": 16.8, "RACE": 33.8}, "pe_ntm",
             more=[(SVC, {"MSFT": 22.9, "GOOGL": 17.9, "META": 17.5, "V": 30.1, "MA": 28.6, "SPGI": 23.2})])
    eps_ttm = 132e9 / 14.59e9
    fc = {"eps": {"base": eps_ttm * 1.06}}
    v, tr = _sotp(monkeypatch, fc)
    hw_gp, svc_gp = 356e9 * 0.36, 123e9 * 0.725
    ni = 132e9 * 1.06
    ni_hw = ni * hw_gp / (hw_gp + svc_gp)
    pe_hw, pe_svc = (20.4 + 26.2) / 2, (22.9 + 23.2) / 2
    assert v == pytest.approx((ni_hw * pe_hw + (ni - ni_hw) * pe_svc + 56.8e9) / 14.59e9, rel=1e-6)
    mp = tr["multiple_parts"]
    assert "live forward P/E" in mp["peer_source"] and mp["ntm_earnings_factor"] == pytest.approx(1.06)
    assert [m["symbol"] for m in mp["hardware_members_used"]] and "NTM" in tr["metric"]
    # bear keeps the owner's band: hardware x 15/16.5, services x 26/29
    _, trb = _sotp(monkeypatch, {"eps": {"base": eps_ttm * 1.06, "bear": eps_ttm}}, "bear")
    segs = {s["segment"]: s["multiple"] for s in trb["segments"]}
    assert segs["Hardware (Products)"] == pytest.approx(pe_hw * 15 / 16.5)
    assert segs["Services"] == pytest.approx(pe_svc * 26 / 29)


def test_sotp_falls_back_to_the_owner_constants_without_a_basket_or_consensus(store, monkeypatch):
    _members(rc.CROSS_MARKET_EXCHANGE, "profile", HW, {"SONY": 20.4, "RMS.PA": 26.2}, "pe_ntm")   # short of 3
    v, tr = _sotp(monkeypatch, {"eps": {"base": 9.6}})
    assert {s["multiple"] for s in tr["segments"]} == {16.5, 29.0}
    assert "fallback" in tr["multiple_parts"]["peer_source"]


# ── 3. what the price requires ───────────────────────────────────────────────

def test_priced_in_states_implied_model_and_own_growth():
    dcf_t = {"reverse_dcf": {"spot": 1088.88, "solved": True, "implied_cagr10": 0.19871, "model_cagr10": 0.03685},
             "cycle": {"trend_revenue_growth": 0.1029, "n_years": 15}}
    out = priced_in(dcf_t, "US$")
    assert out["applies"] and out["own_years"] == 10
    assert "19.9%" in out["text"] and "3.7%" in out["text"] and "10.3%" in out["text"]


def test_priced_in_is_silent_when_the_reverse_dcf_did_not_solve():
    assert priced_in({"reverse_dcf": {"solved": False}}, "US$")["applies"] is False
    assert priced_in({}, "US$")["text"] is None
