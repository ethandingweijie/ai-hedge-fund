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
    """600 input symbols → 6 FMP calls."""
    syms = [f"T{i:03d}" for i in range(600)]
    fake = []  # empty responses are fine, we're checking call_count
    with patch("src.tools.api._fmp_get", return_value=fake) as mock_get:
        fetch_batch_quotes(syms)
    assert mock_get.call_count == 6


def test_fetch_dedupes_input():
    """Duplicate input tickers → single FMP entry per unique symbol."""
    fake = [{"symbol": "AAPL", "price": 150.0, "changesPercentage": 1.0}]
    with patch("src.tools.api._fmp_get", return_value=fake) as mock_get:
        out = fetch_batch_quotes(["AAPL", "AAPL", "aapl"])
    # Single chunk request even with duplicates
    assert mock_get.call_count == 1
    assert len(out) == 1


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
