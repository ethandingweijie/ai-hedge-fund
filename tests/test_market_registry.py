"""Market is a dimension, not a boolean — and no market inherits another's.

`is_hk: bool` could express exactly two markets, and gave Singapore whichever
one it was not. The static peer-multiple lookup was
`HK_SECTOR_PEER_MULTIPLES if is_hk else SECTOR_PEER_MULTIPLES`, so every SGX
name that missed the live comps was valued on US multiples — which run 20-60%
above comparable HK levels across 13 of 15 sectors, and SGX sits far closer to
HK than to the US. SK Hynix had the same problem via `Memory / DRAM-NAND`:
a Korean memory maker valued against US semiconductor multiples.

The registry makes a new market one entry — exchange codes, a country risk
premium, and whatever static fallbacks exist. The load-bearing rule is that
a market with NO authored table returns {} and relies on live comps, rather
than silently borrowing a different economy's multiples.
"""

from __future__ import annotations

import pytest

from src.data import regional_comps as rc
from src.data.sector_profiles import (
    MARKET_REGISTRY,
    market_crp,
    market_peer_multiples,
    market_sector_wacc,
    resolve_market,
)

EXPECTED = {"US", "HKSE", "SES", "JPX", "KSC", "SHH", "SHZ"}


# ── The registry itself ──────────────────────────────────────────────────

def test_seven_markets_are_registered():
    assert set(MARKET_REGISTRY) == EXPECTED


def test_the_two_registries_agree():
    """`regional_comps.MARKETS` keys the live comps; `MARKET_REGISTRY` keys the
    static fallbacks. A key present in one and not the other means a market
    whose live comps can never be found by its static lookup, or vice versa."""
    assert set(rc.MARKETS) == set(MARKET_REGISTRY)


@pytest.mark.parametrize("ticker, expected", [
    ("AAPL",       "US"),
    ("00700.HK",   "HKSE"),
    ("D05.SI",     "SES"),
    ("000660.KS",  "KSC"),
    ("7203.T",     "JPX"),
    ("600519.SS",  "SHH"),
    ("000001.SZ",  "SHZ"),
])
def test_market_resolves_from_the_ticker(ticker, expected):
    assert resolve_market(ticker=ticker) == expected


@pytest.mark.parametrize("exchange, expected", [
    ("NASDAQ", "US"), ("NYSE", "US"), ("AMEX", "US"),
    ("HKSE", "HKSE"), ("SES", "SES"), ("JPX", "JPX"), ("KSC", "KSC"),
])
def test_market_resolves_from_the_exchange(exchange, expected):
    assert resolve_market(exchange=exchange) == expected


def test_an_unknown_symbol_falls_back_to_us():
    assert resolve_market(ticker="SOMETHING") == "US"


def test_the_legacy_flag_still_resolves():
    """64 call sites still pass is_hk; they must keep working while they
    migrate. It is the weakest signal — anything more specific wins."""
    assert resolve_market(is_hk=True) == "HKSE"
    assert resolve_market(ticker="D05.SI", is_hk=True) == "SES"


# ── No market inherits another's economics ───────────────────────────────

def test_singapore_has_no_static_table_and_does_not_borrow_one():
    """The bug, stated as a rule. {} means 'rely on live comps', and is a
    better answer than a US multiple for an SGX name."""
    assert market_peer_multiples("SES") == {}


@pytest.mark.parametrize("market", ["JPX", "KSC", "SHH", "SHZ"])
def test_new_markets_start_with_no_borrowed_multiples(market):
    assert market_peer_multiples(market) == {}


def test_the_markets_that_do_have_tables_still_get_them():
    us, hk = market_peer_multiples("US"), market_peer_multiples("HKSE")
    assert us and hk
    assert us is not hk
    # The divergence the shared table was hiding: HK Tech trades well below US.
    assert hk["Tech"]["ev_ebitda"] < us["Tech"]["ev_ebitda"]


# ── Country risk premia ──────────────────────────────────────────────────

def test_the_us_base_carries_no_premium():
    assert market_crp("US") == 0.0


@pytest.mark.parametrize("market", sorted(EXPECTED - {"US"}))
def test_every_other_market_carries_a_positive_premium(market):
    assert market_crp(market) > 0


def test_premia_are_ordered_by_sovereign_standing():
    """SG (AAA) and KR (AA) below JP (A+), all well below mainland China —
    the same ordering the existing SECTOR_WACC tables already express."""
    assert market_crp("SES") <= market_crp("JPX") < market_crp("SHH")
    assert market_crp("KSC") <= market_crp("JPX")
    assert market_crp("SHH") == market_crp("SHZ") == market_crp("HKSE")


def test_an_unknown_market_is_premium_free_rather_than_a_crash():
    assert market_crp("NOPE") == 0.0
    assert market_peer_multiples("NOPE") == {}
    assert market_sector_wacc("NOPE") == {}


def test_authored_wacc_tables_resolve():
    assert market_sector_wacc("HKSE"), "HK_SECTOR_WACC should resolve"
    assert market_sector_wacc("SES"), "SG_SECTOR_WACC should resolve"
    # US is the base itself, so it has no separate table.
    assert market_sector_wacc("US") == {}
