"""
batch_quote.py — FMP /stable/quote bulk wrapper for the DD cron dispatcher.

FMP's /stable/quote endpoint accepts comma-separated symbols. With proper
batching (100 tickers per request) we can quote a 600-ticker universe with
6 API calls — well under the 300/min FMP limit.

Public surface:
  fetch_batch_quotes(tickers) → dict[ticker, BatchQuote]

Each BatchQuote captures the fields the dispatcher needs to detect breaches:
  - price            (current/last)
  - changes_percentage  (signed pct as DECIMAL, e.g. -0.115 for -11.5%)
                     ── note: FMP returns this as a percent value (e.g. -11.5),
                              we normalize to decimal for downstream consistency
                              with alert_dedup which expects -0.115.

Failure modes:
  - FMP down / 401 / 402 → returns {} (dispatcher logs + skips this tick)
  - Partial response (some tickers missing from FMP's reply) → returned dict
    only contains tickers FMP knew about; missing ones treated as "no data"
    by dispatcher (no alert fires, no false positive)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable


logger = logging.getLogger(__name__)


# FMP's /stable/quote can technically take more, but 100 is the documented
# safe batch size and matches the plan's batching.
_MAX_BATCH_SIZE = 100


@dataclass(frozen=True)
class BatchQuote:
    """One ticker's quote, normalized for the DD dispatcher.

    `changes_percentage` is in DECIMAL form (e.g. -0.115 = -11.5%), NOT the
    raw FMP percent. This matches alert_dedup.check_alert_eligibility's
    `current_pct` argument and the EXTREME_PCT_THRESHOLD = 0.10 constant.
    """
    ticker: str
    price:  float
    changes_percentage: float          # decimal, sign-aware
    raw: dict                          # untouched FMP row for downstream logging


def fetch_batch_quotes(tickers: Iterable[str]) -> dict[str, BatchQuote]:
    """Return {ticker: BatchQuote} for every ticker FMP returned data for.

    Args:
      tickers: any iterable of ticker symbols (case-insensitive). Empty
               input → empty dict (no API call made).

    Behaviour:
      - Auto-batches in groups of 100.
      - Skips tickers FMP returns no row for (just absent from result dict).
      - Skips rows that lack price OR changesPercentage (can't make alert
        decisions without both).
      - Never raises. FMP errors → empty dict + warning log.
    """
    syms = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not syms:
        return {}

    out: dict[str, BatchQuote] = {}
    for batch in _chunks(syms, _MAX_BATCH_SIZE):
        rows = _fetch_one_batch(batch)
        for r in rows:
            sym   = (r.get("symbol") or "").strip().upper()
            price = r.get("price")
            pct   = r.get("changesPercentage")
            if not sym or price is None or pct is None:
                continue
            try:
                price_f = float(price)
                # FMP returns pct as percent (e.g. -11.5). Normalize to decimal.
                pct_dec = float(pct) / 100.0
            except (ValueError, TypeError):
                continue
            out[sym] = BatchQuote(
                ticker=sym,
                price=price_f,
                changes_percentage=pct_dec,
                raw=r,
            )

    logger.info(
        "batch_quote: requested=%d, returned=%d (missing=%d)",
        len(syms), len(out), len(syms) - len(out),
    )
    return out


def detect_breaches(
    quotes: dict[str, BatchQuote],
    *,
    threshold_pct: float = 0.10,
) -> list[BatchQuote]:
    """Filter quotes → list of breaches (|changes_percentage| >= threshold).

    Args:
      quotes:        result of fetch_batch_quotes()
      threshold_pct: decimal trigger threshold (e.g. 0.10 = ±10%)

    Returns:
      List of BatchQuotes ordered by absolute pct DESC (largest moves first).
      Bidirectional: includes both DROPS (negative) and PUMPS (positive).
    """
    breaches = [q for q in quotes.values() if abs(q.changes_percentage) >= threshold_pct]
    breaches.sort(key=lambda q: abs(q.changes_percentage), reverse=True)
    return breaches


# ── Internals ───────────────────────────────────────────────────────────────


def _chunks(seq: list[str], n: int):
    """Yield successive n-sized chunks from seq."""
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _fetch_one_batch(symbols: list[str]) -> list[dict]:
    """Single FMP /stable/quote call for up to 100 symbols.

    Returns the raw FMP rows or [] on any failure. Logs but never raises.
    """
    try:
        from src.tools.api import _fmp_get, _STABLE   # type: ignore

        symbol_param = ",".join(symbols)
        data = _fmp_get(
            f"{_STABLE}/quote",
            params={"symbol": symbol_param},
            api_key=None,
            uncap=True,
        )
        if isinstance(data, list):
            return data
        return []
    except Exception as exc:
        logger.warning("batch_quote: FMP /stable/quote failed for %d symbols: %s",
                       len(symbols), exc)
        return []
