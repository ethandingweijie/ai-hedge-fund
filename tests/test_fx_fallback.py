"""FX fallback coverage for get_fx_rate (src/tools/api.py).

Pins the behaviour added for task #25's HK blend-currency fix: when the FMP
forex batch is unavailable (402 throttle), non-USD crosses must still
resolve instead of silently returning 1.0.

  * direct table pairs are unchanged (HKDUSD, CNYUSD),
  * inverse pairs resolve through the table (USDHKD = 1/HKDUSD),
  * USD crosses resolve through two table entries (CNYHKD = CNYUSD/HKDUSD),
  * unknown pairs still return 1.0,
  * a live FMP batch quote always wins over the fallback table.

Regression context: during an FMP 402-throttled run, 3690.HK logged
"[FX] CNYHKD — unknown pair, returning 1.0" and the HK blend ran on
unscaled CNY values; the SOTP (analyst) leg likewise had no USD→HKD path.
"""
from __future__ import annotations

import time

import pytest

from src.tools import api


@pytest.fixture
def fmp_down(monkeypatch):
    """Non-empty fresh batch cache → _get_fx_batch never hits FMP."""
    monkeypatch.setattr(api, "_FX_BATCH_CACHE", {"sentinel": {}})
    monkeypatch.setattr(api, "_FX_BATCH_TS", time.time())
    yield


def test_direct_table_pair_unchanged(fmp_down):
    assert api.get_fx_rate("HKD", "USD") == pytest.approx(0.1282)
    assert api.get_fx_rate("CNY", "USD") == pytest.approx(0.1376)


def test_inverse_pair_resolves_through_table(fmp_down):
    rate = api.get_fx_rate("USD", "HKD")
    assert rate == pytest.approx(1.0 / 0.1282)
    assert 7.5 < rate < 8.1  # sanity: sane USDHKD band, not 1.0


def test_usd_cross_resolves_through_two_table_entries(fmp_down):
    rate = api.get_fx_rate("CNY", "HKD")
    assert rate == pytest.approx(0.1376 / 0.1282)
    assert 1.02 < rate < 1.15  # sanity: not 1.0 / not absurd


def test_same_currency_short_circuits(fmp_down):
    assert api.get_fx_rate("USD", "USD") == 1.0
    assert api.get_fx_rate("HKD", "HKD") == 1.0


def test_unknown_pair_still_returns_one(fmp_down):
    assert api.get_fx_rate("XYZ", "ABC") == 1.0


def test_live_batch_quote_beats_fallback(monkeypatch):
    monkeypatch.setattr(
        api, "_FX_BATCH_CACHE",
        {"USDHKD": {"symbol": "USDHKD", "price": 7.75}})
    monkeypatch.setattr(api, "_FX_BATCH_TS", time.time())
    assert api.get_fx_rate("USD", "HKD") == pytest.approx(7.75)
