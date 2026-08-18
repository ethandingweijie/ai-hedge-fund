"""
Regression tests for GET /analysis/popular-tickers directional output.

Root cause covered here: the 2026-08-15 dependency rebuild brought
yfinance 1.2.0 to prod, which passes Yahoo's trailing INCOMPLETE bar
(current/pre-open session, NaN close) straight through `history()`.
The old code read `iloc[-1]` blindly, hit the NaN guard, and returned
price/change/change_pct = None for EVERY ticker — the ticker tape lost
its directional arrows. The fix drops incomplete rows and computes the
change from the last two FINALIZED closes.

The route handler is called directly (no HTTP layer), with the DB query
and yfinance.Ticker mocked, so the tests never touch the network.
"""
import asyncio
import math

import pandas as pd
import pytest

from app.backend.routes.analysis import get_popular_tickers


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-08-11", periods=len(closes), freq="B",
                        tz="America/New_York")
    return pd.DataFrame({"Close": closes}, index=idx)


class _FakeTicker:
    """Stands in for yfinance.Ticker; history() returns a canned frame."""
    frames: dict[str, pd.DataFrame] = {}

    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, period: str = "5d") -> pd.DataFrame:
        return self.frames[self.symbol]


@pytest.fixture(autouse=True)
def _wire_mocks(monkeypatch):
    monkeypatch.setattr(
        "app.backend.services.analysis_service._ensure_web_runs_table",
        lambda: None,
    )
    monkeypatch.setattr(
        "src.data.db.query",
        lambda sql, params=None: [{"ticker": "CRWD"}],
    )
    monkeypatch.setattr("yfinance.Ticker", _FakeTicker)
    _FakeTicker.frames = {}
    yield
    _FakeTicker.frames = {}


def _run() -> list[dict]:
    return asyncio.run(get_popular_tickers(limit=5))


def test_trailing_nan_row_still_yields_direction():
    """The prod failure mode: Yahoo's trailing bar for the current session
    has a NaN close. The tape must fall back to the last finalized session
    (Friday here) instead of blanking out."""
    _FakeTicker.frames["CRWD"] = _frame(
        [221.90, 221.78, 225.53, 216.95, float("nan")])

    (row,) = _run()
    assert row["ticker"] == "CRWD"
    assert row["price"] == 216.95
    assert row["change"] == pytest.approx(216.95 - 225.53, abs=0.005)
    expected_pct = (216.95 - 225.53) / 225.53 * 100
    assert row["change_pct"] == pytest.approx(expected_pct, abs=0.005)


def test_fully_valid_rows_compute_normally():
    _FakeTicker.frames["CRWD"] = _frame([100.0, 102.0, 101.0])

    (row,) = _run()
    assert row["price"] == 101.0
    assert row["change"] == -1.0
    assert row["change_pct"] == pytest.approx(-1.0 / 102.0 * 100, abs=0.005)


def test_single_valid_close_returns_nulls():
    """One finalized close has no previous close to compare against —
    degrade to nulls (chip shows ticker only) rather than crash."""
    _FakeTicker.frames["CRWD"] = _frame([float("nan"), float("nan"), 100.0])

    (row,) = _run()
    assert row["price"] is None
    assert row["change"] is None
    assert row["change_pct"] is None


def test_all_nan_returns_nulls():
    _FakeTicker.frames["CRWD"] = _frame([float("nan"), float("nan")])

    (row,) = _run()
    assert row["price"] is None
    assert row["change_pct"] is None


def test_inf_close_rejected():
    """Inf values (rare upstream glitch) must still be rejected, not
    serialized (would 500 the endpoint via FastAPI's JSON encoder)."""
    _FakeTicker.frames["CRWD"] = _frame([100.0, math.inf])

    (row,) = _run()
    assert row["price"] is None
    assert row["change_pct"] is None
