"""Tests for src/agents/dd/batch_quote.py — FMP /stable/quote bulk wrapper."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.dd.batch_quote import (
    BatchQuote,
    detect_breaches,
    fetch_batch_quotes,
)


# ── fetch_batch_quotes ──────────────────────────────────────────────────────


def test_fetch_returns_empty_for_empty_input():
    assert fetch_batch_quotes([]) == {}
    assert fetch_batch_quotes(["", "  "]) == {}


def test_fetch_normalizes_input_to_uppercase():
    """Mixed-case input → all uppercase keys in output."""
    fake = [{"symbol": "AAPL", "price": 150.0, "changesPercentage": 2.5}]
    with patch("src.tools.api._fmp_get", return_value=fake):
        out = fetch_batch_quotes(["aapl"])
    assert "AAPL" in out
    assert "aapl" not in out


def test_fetch_normalizes_pct_to_decimal():
    """FMP returns pct as percent (-11.5); we normalize to decimal (-0.115)."""
    fake = [{"symbol": "PEGA", "price": 89.0, "changesPercentage": -11.5}]
    with patch("src.tools.api._fmp_get", return_value=fake):
        out = fetch_batch_quotes(["PEGA"])
    assert out["PEGA"].changes_percentage == pytest.approx(-0.115)


def test_fetch_skips_rows_missing_required_fields():
    """Rows without price OR changesPercentage are skipped silently."""
    fake = [
        {"symbol": "AAPL", "price": 150.0, "changesPercentage": 2.5},
        {"symbol": "BAD",  "price": 50.0},                             # no pct
        {"symbol": "ALSO", "changesPercentage": 5.0},                  # no price
        {"price": 100.0,   "changesPercentage": 1.0},                  # no symbol
    ]
    with patch("src.tools.api._fmp_get", return_value=fake):
        out = fetch_batch_quotes(["AAPL", "BAD", "ALSO"])
    assert set(out.keys()) == {"AAPL"}


def test_fetch_returns_empty_dict_on_fmp_error():
    """FMP throws → graceful empty dict (dispatcher logs + skips)."""
    with patch("src.tools.api._fmp_get", side_effect=Exception("network")):
        out = fetch_batch_quotes(["AAPL"])
    assert out == {}


def test_fetch_returns_empty_dict_on_fmp_none():
    """FMP returns None (auth/plan failure) → empty dict."""
    with patch("src.tools.api._fmp_get", return_value=None):
        out = fetch_batch_quotes(["AAPL"])
    assert out == {}


def test_fetch_batches_in_chunks_of_100():
    """600 input symbols → 6 bulk FMP calls (one per 100-ticker chunk).

    Uses a non-empty mock response so the fallback path doesn't fire — we're
    checking BATCH-LEVEL chunking here, not the fallback. See
    test_fetch_falls_back_to_per_ticker_when_batch_returns_empty for the
    fallback path.
    """
    syms = [f"T{i:03d}" for i in range(600)]
    # Mock returns at least one row so the bulk path is treated as successful
    # and no fallback fires.
    fake = [{"symbol": "T000", "price": 1.0, "changesPercentage": 0.0}]
    with patch("src.tools.api._fmp_get", return_value=fake) as mock_get:
        fetch_batch_quotes(syms)
    # Exactly 6 bulk calls — one per 100-ticker chunk. The mock returning
    # a non-empty list keeps us on the happy-path bulk endpoint.
    assert mock_get.call_count == 6
    # And every call hit the bulk endpoint (not the per-ticker fallback)
    for call in mock_get.call_args_list:
        url = call.args[0]
        assert "/batch-quote" in url


def test_fetch_dedupes_input():
    """Duplicate input tickers → single FMP entry per unique symbol."""
    fake = [{"symbol": "AAPL", "price": 150.0, "changesPercentage": 1.0}]
    with patch("src.tools.api._fmp_get", return_value=fake) as mock_get:
        out = fetch_batch_quotes(["AAPL", "AAPL", "aapl"])
    # Single chunk request even with duplicates
    assert mock_get.call_count == 1
    assert len(out) == 1


def test_fetch_uses_batch_quote_endpoint_with_symbols_param():
    """Regression guard: FMP's multi-ticker bulk endpoint is
    `/stable/batch-quote?symbols=AAPL,MSFT` (NOTE: param is `symbols`
    PLURAL, not `symbol`). DO NOT change this to:

      * `/stable/quote?symbol=AAPL,MSFT` — silently returns 200 OK, [].
        (Was the bug before commit 59b1d09.)
      * `/stable/quote/AAPL,MSFT,GOOGL` (path-style) — returns 404 because
        path-style /stable/quote/{TICKER} only accepts a single ticker on
        most FMP plans. (Was the regression in commit 59b1d09.)
      * Singular `?symbol=` on /stable/batch-quote — FMP returns 4xx.

    Failure history:
      2026-05-10..12: query-param ?symbol form returned empty arrays
                      silently → zero alerts despite real market moves
      2026-05-20:     path-style /stable/quote/MULTI returned 404 → still
                      zero alerts; user pinged with dispatcher logs
      2026-05-20+:    switched to /stable/batch-quote?symbols=...
                      (the correct dedicated bulk endpoint per FMP docs:
                       https://site.financialmodelingprep.com/developer/docs/stable/batch-quote)
    """
    fake = [{"symbol": "AAPL", "price": 150.0, "changesPercentage": 2.5}]
    with patch("src.tools.api._fmp_get", return_value=fake) as mock_get:
        fetch_batch_quotes(["AAPL", "MSFT", "GOOGL"])

    url_arg = mock_get.call_args.args[0]
    params_arg = mock_get.call_args.kwargs.get("params", {})

    # 1. URL targets the BULK endpoint (not /stable/quote)
    assert "/batch-quote" in url_arg and "/quote/" not in url_arg, (
        f"Expected /stable/batch-quote endpoint, got: {url_arg}"
    )
    # 2. Param name is PLURAL `symbols`, comma-separated, sorted
    assert "symbols" in params_arg, f"params must use 'symbols' (plural); got params={params_arg}"
    assert params_arg["symbols"] == "AAPL,GOOGL,MSFT", (
        f"symbols value should be sorted+joined ticker list; got {params_arg['symbols']!r}"
    )
    # 3. Defensive: must NOT use the singular 'symbol' form (that was the v1 bug)
    assert "symbol" not in params_arg, (
        f"params must NOT contain singular 'symbol' key (v1 bug pattern); got {params_arg}"
    )


def test_fetch_falls_back_to_per_ticker_when_batch_returns_empty():
    """If /stable/batch-quote returns empty (plan doesn't include bulk or
    FMP silently degrades), we fall back to per-ticker /stable/quote calls.

    This is what protects against the v3 fix failing silently the same way
    v1 did. Bulk + fallback = belt + suspenders.
    """
    call_log = []

    def _fake_fmp_get(url, params=None, api_key=None, uncap=False):
        call_log.append((url, dict(params or {})))
        if "/batch-quote" in url:
            return []   # simulate plan-restricted empty response
        if "/quote" in url:
            sym = (params or {}).get("symbol")
            return [{"symbol": sym, "price": 100.0, "changesPercentage": 1.0}]
        return None

    with patch("src.tools.api._fmp_get", side_effect=_fake_fmp_get):
        out = fetch_batch_quotes(["AAPL", "MSFT"])

    # Bulk attempt + 2 per-ticker fallback attempts
    assert any("/batch-quote" in u for u, _ in call_log)
    per_ticker_calls = [(u, p) for u, p in call_log if "/quote" in u and "/batch-quote" not in u]
    assert len(per_ticker_calls) == 2
    # Both tickers should have data despite bulk-empty
    assert set(out.keys()) == {"AAPL", "MSFT"}


# ── detect_breaches ─────────────────────────────────────────────────────────


def _q(ticker: str, pct: float, price: float = 100.0) -> BatchQuote:
    return BatchQuote(ticker=ticker, price=price, changes_percentage=pct, raw={})


def test_detect_breaches_at_threshold():
    """exactly ±10% qualifies."""
    quotes = {
        "AAPL": _q("AAPL", -0.10),
        "MSFT": _q("MSFT", 0.10),
        "BORD": _q("BORD", -0.099),     # 9.9% — under
    }
    breaches = detect_breaches(quotes, threshold_pct=0.10)
    tickers = [b.ticker for b in breaches]
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert "BORD" not in tickers


def test_detect_breaches_bidirectional():
    """Both DROPS and PUMPS surface."""
    quotes = {
        "DROP1": _q("DROP1", -0.15),
        "PUMP1": _q("PUMP1",  0.12),
        "FLAT1": _q("FLAT1",  0.05),
    }
    breaches = detect_breaches(quotes, threshold_pct=0.10)
    assert len(breaches) == 2


def test_detect_breaches_sorted_by_abs_magnitude():
    """Largest moves first, regardless of sign."""
    quotes = {
        "MED": _q("MED", -0.12),
        "BIG": _q("BIG",  0.25),
        "SML": _q("SML", -0.11),
    }
    breaches = detect_breaches(quotes, threshold_pct=0.10)
    assert [b.ticker for b in breaches] == ["BIG", "MED", "SML"]


def test_detect_breaches_empty_when_nothing_qualifies():
    quotes = {"X": _q("X", 0.05), "Y": _q("Y", -0.04)}
    assert detect_breaches(quotes, threshold_pct=0.10) == []


def test_detect_breaches_custom_threshold():
    """Caller can pass a different threshold (e.g. 5% for tier1_held)."""
    quotes = {"A": _q("A", -0.07), "B": _q("B", 0.04)}
    breaches = detect_breaches(quotes, threshold_pct=0.05)
    assert {b.ticker for b in breaches} == {"A"}
