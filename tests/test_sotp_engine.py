"""Stage 7 / Phase 7g — deterministic GS-style SOTP engine tests.

Guards the contract of ``_sotp_analyst_style`` (dcf_agent.py), the engine
behind the "SOTP (analyst)" shadow method:

  * Fixture accuracy — hand-transcribed GS Exhibit 17 (Meituan, 10 Aug 2026)
    reproduces the published HK$123 TP within ±1.
  * Determinism — identical input → bit-identical output.
  * NAV arithmetic — segments + associates + net cash − holdco discount.
  * Anchor selection — higher of P/E-on-NOPAT vs EV/Rev wins per segment.
  * EBIT derivation — unit economics (volume × profit/unit × fx) and
    ebit_margin × revenue fallbacks.
  * Keyword-multiple fallback when no explicit multiple is supplied.
  * Precondition guards — None on missing shares / segments.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.agents.analysis.dcf_agent import _classify_segment, _sotp_analyst_style

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sotp"


@pytest.fixture(scope="module")
def meituan_fixture() -> dict:
    with open(_FIXTURE_DIR / "gs_meituan_exhibit17.json", encoding="utf-8") as fh:
        return json.load(fh)


# ── Fixture accuracy ──────────────────────────────────────────────────────────

def test_meituan_reproduces_gs_tp(meituan_fixture):
    """Engine on Exhibit-17 assumptions lands at GS HK$123 TP ± 1."""
    a = meituan_fixture["assumptions"]
    meta = meituan_fixture["_meta"]
    table = _sotp_analyst_style(
        a,
        shares=meta["shares"],
        fx_to_reporting=meta["fx_usd_to_hkd"],
    )
    assert table is not None
    gs_tp = meta["gs_12m_tp_hkd"]
    assert abs(table["per_share_reporting"] - gs_tp) <= 1.0, (
        f"engine gave {table['per_share_reporting']:.2f}, GS published {gs_tp}"
    )


def test_meituan_nav_close_to_published(meituan_fixture):
    """NAV within 1% of GS's 116,044 (engine takes max(P/E, EV/Rev) per
    segment; GS's IHT row resolves ~2% differently on that rule)."""
    a = meituan_fixture["assumptions"]
    meta = meituan_fixture["_meta"]
    table = _sotp_analyst_style(
        a, shares=meta["shares"], fx_to_reporting=meta["fx_usd_to_hkd"])
    published_nav = meituan_fixture["gs_published"]["nav_usdmn"] * 1e6
    assert abs(table["nav"] / published_nav - 1.0) < 0.01


def test_meituan_value_split_present(meituan_fixture):
    a = meituan_fixture["assumptions"]
    meta = meituan_fixture["_meta"]
    table = _sotp_analyst_style(
        a, shares=meta["shares"], fx_to_reporting=meta["fx_usd_to_hkd"])
    for row in table["rows"]:
        assert row["value_split_pct"] is not None
        assert 0.0 < row["value_split_pct"] < 1.0


# ── Determinism ───────────────────────────────────────────────────────────────

def test_deterministic_bit_identical(meituan_fixture):
    a = meituan_fixture["assumptions"]
    meta = meituan_fixture["_meta"]
    t1 = _sotp_analyst_style(a, shares=meta["shares"],
                             fx_to_reporting=meta["fx_usd_to_hkd"])
    t2 = _sotp_analyst_style(copy.deepcopy(a), shares=meta["shares"],
                             fx_to_reporting=meta["fx_usd_to_hkd"])
    assert t1 == t2


# ── NAV arithmetic ────────────────────────────────────────────────────────────

def test_holdco_associates_netcash_arithmetic():
    assumptions = {
        "segments": [{"name": "Core", "revenue_fwd": 100.0,
                      "ev_rev_multiple": 2.0}],
        "associates_investments": 50.0,
        "net_cash": 30.0,
        "holdco_discount_pct": 0.20,
    }
    table = _sotp_analyst_style(assumptions, shares=10.0)
    assert table is not None
    assert table["segment_value"] == pytest.approx(200.0)
    assert table["nav"] == pytest.approx(280.0)          # 200 + 50 + 30
    assert table["holdco_discount"] == pytest.approx(56.0)  # 20% of 280
    assert table["final"] == pytest.approx(224.0)
    assert table["per_share"] == pytest.approx(22.4)


def test_net_debt_fallback_when_net_cash_missing():
    assumptions = {
        "segments": [{"name": "Core", "revenue_fwd": 100.0,
                      "ev_rev_multiple": 2.0}],
    }
    table = _sotp_analyst_style(assumptions, shares=10.0, net_debt=-40.0)
    assert table is not None
    assert table["net_cash"] == pytest.approx(40.0)  # −net_debt when negative
    assert table["nav"] == pytest.approx(240.0)


# ── Anchor selection ──────────────────────────────────────────────────────────

def test_pe_path_wins_when_higher():
    assumptions = {
        "segments": [{
            "name": "Profitable", "revenue_fwd": 1000.0,
            "ebit": 200.0, "pe_multiple": 12.0, "ev_rev_multiple": 1.0,
        }],
        "default_tax_rate": 0.15,
    }
    table = _sotp_analyst_style(assumptions, shares=10.0)
    row = table["rows"][0]
    # P/E: 200 × 0.85 × 12 = 2,040 > EV/Rev: 1,000 × 1.0
    assert row["method"] == "P/E"
    assert row["value"] == pytest.approx(2040.0)


def test_evrev_path_wins_when_higher():
    assumptions = {
        "segments": [{
            "name": "High multiple", "revenue_fwd": 1000.0,
            "ebit": 100.0, "pe_multiple": 10.0, "ev_rev_multiple": 2.2,
        }],
        "default_tax_rate": 0.15,
    }
    table = _sotp_analyst_style(assumptions, shares=10.0)
    row = table["rows"][0]
    # P/E: 100 × 0.85 × 10 = 850 < EV/Rev: 1,000 × 2.2 = 2,200
    assert row["method"] == "EV/Rev"
    assert row["value"] == pytest.approx(2200.0)


def test_loss_maker_uses_evrev_only():
    assumptions = {
        "segments": [{
            "name": "New initiatives", "revenue_fwd": 500.0,
            "ebit": -50.0, "pe_multiple": 12.0, "ev_rev_multiple": 1.3,
        }],
    }
    table = _sotp_analyst_style(assumptions, shares=10.0)
    row = table["rows"][0]
    assert row["method"] == "EV/Rev"
    assert row["value"] == pytest.approx(650.0)


# ── EBIT derivation paths ─────────────────────────────────────────────────────

def test_unit_economics_ebit():
    """Meituan food-delivery style: 67mn daily orders × 365 × Rmb1.1 × fx."""
    vol = 67e6 * 365
    ppu = 1.1
    fx = 1 / 7.2  # RMB → USD
    assumptions = {
        "segments": [{
            "name": "Food Delivery", "revenue_fwd": 23_251e6,
            "unit_economics": {"volume_annual": vol, "profit_per_unit": ppu,
                               "fx_to_usd": fx},
            "pe_multiple": 12.0,
        }],
        "default_tax_rate": 0.15,
    }
    table = _sotp_analyst_style(assumptions, shares=6.25e9)
    row = table["rows"][0]
    expected_ebit = vol * ppu * fx
    assert row["ebit"] == pytest.approx(expected_ebit, rel=1e-9)
    assert row["method"] == "P/E"
    assert row["value"] == pytest.approx(expected_ebit * 0.85 * 12.0, rel=1e-9)


def test_ebit_margin_path():
    assumptions = {
        "segments": [{
            "name": "IHT", "revenue_fwd": 1000.0, "ebit_margin": 0.25,
            "pe_multiple": 10.0,
        }],
        "default_tax_rate": 0.15,
    }
    table = _sotp_analyst_style(assumptions, shares=10.0)
    row = table["rows"][0]
    assert row["ebit"] == pytest.approx(250.0)
    assert row["value"] == pytest.approx(250.0 * 0.85 * 10.0)


def test_segment_tax_rate_overrides_default():
    assumptions = {
        "segments": [{
            "name": "Taxed", "revenue_fwd": 1000.0, "ebit": 100.0,
            "tax_rate": 0.30, "pe_multiple": 10.0,
        }],
        "default_tax_rate": 0.15,
    }
    table = _sotp_analyst_style(assumptions, shares=10.0)
    assert table["rows"][0]["value"] == pytest.approx(100.0 * 0.70 * 10.0)


# ── Fallback keyword multiples ────────────────────────────────────────────────

def test_fallback_multiple_path():
    """No explicit multiple → _classify_segment keyword multiple."""
    assumptions = {
        "segments": [{"name": "advertising platform", "revenue_fwd": 100.0}],
    }
    table = _sotp_analyst_style(assumptions, shares=10.0, tier="default")
    row = table["rows"][0]
    _, expected_mult = _classify_segment("advertising platform", tier="default")
    assert row["method"] == "EV/Rev (fallback)"
    assert row["multiple"] == expected_mult
    assert row["value"] == pytest.approx(100.0 * expected_mult)


def test_fallback_honors_tier():
    assumptions = {
        "segments": [{"name": "cloud infrastructure", "revenue_fwd": 100.0}],
    }
    default_t = _sotp_analyst_style(copy.deepcopy(assumptions), shares=10.0,
                                    tier="default")
    premium_t = _sotp_analyst_style(copy.deepcopy(assumptions), shares=10.0,
                                    tier="premium")
    assert premium_t["rows"][0]["value"] > default_t["rows"][0]["value"]


# ── Precondition guards ───────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, {}, {"segments": []}])
def test_none_on_missing_assumptions(bad):
    assert _sotp_analyst_style(bad, shares=10.0) is None


@pytest.mark.parametrize("bad_shares", [0, -1, None])
def test_none_on_bad_shares(bad_shares):
    assumptions = {"segments": [{"name": "x", "revenue_fwd": 10.0,
                                 "ev_rev_multiple": 1.0}]}
    assert _sotp_analyst_style(assumptions, shares=bad_shares) is None


def test_segments_without_revenue_skipped():
    assumptions = {
        "segments": [
            {"name": "no rev", "ev_rev_multiple": 2.0},
            {"name": "zero rev", "revenue_fwd": 0.0, "ev_rev_multiple": 2.0},
            {"name": "ok", "revenue_fwd": 100.0, "ev_rev_multiple": 2.0},
        ],
    }
    table = _sotp_analyst_style(assumptions, shares=10.0)
    assert len(table["rows"]) == 1
    assert table["rows"][0]["name"] == "ok"


# ── GS AMZN replication shape: EV/EBIT multiples + net debt ──────────────────

def test_amzn_ev_ebit_replication_zero_tax_honored():
    """GS AMZN 31 Jul 2026: EV/EBIT-style multiples apply to PRE-TAX GAAP
    EBIT, so the researched group tax rate is 0.0 — a falsy-zero fallback to
    0.15 would silently deflate the NA/AWS legs by 15%. Net debt enters as
    negative net cash."""
    assumptions = {
        "default_tax_rate": 0.0,
        "holdco_discount_pct": 0.0,
        "net_cash": -59.22e9,
        "segments": [
            {"name": "North America", "revenue_fwd": 518.601e9,
             "ebit_margin": 0.10075, "pe_multiple": 16.0},
            {"name": "International", "revenue_fwd": 209.723e9,
             "ev_rev_multiple": 1.25},
            {"name": "AWS", "revenue_fwd": 248.890e9,
             "ebit_margin": 0.42526, "pe_multiple": 29.0},
        ],
    }
    table = _sotp_analyst_style(assumptions, shares=10.974e9)
    # NA 52.25x16 + Intl 209.7x1.25 + AWS 105.84x29 = 4,167.6e9
    # less net debt 59.22e9 -> /10,974mn = ~374.4 (GS PT $375)
    assert abs(table["per_share"] - 374.4) < 0.5
    assert table["holdco_discount"] == 0.0
