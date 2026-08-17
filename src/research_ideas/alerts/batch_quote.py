"""
batch_quote.py — FMP /stable/quote bulk wrapper.

FMP's /stable/quote endpoint accepts comma-separated symbols. With proper
batching (100 tickers per request) we can quote a 600-ticker universe with
6 API calls — well under the 300/min FMP limit.

Public surface:
  fetch_batch_quotes(tickers) → dict[ticker, BatchQuote]

Each BatchQuote captures:
  - price            (current/last)
  - changes_percentage  (signed pct as DECIMAL, e.g. -0.115 for -11.5%)
                     ── note: FMP returns this as a percent value (e.g. -11.5),
                              we normalize to decimal for downstream consistency.

Failure modes:
  - FMP down / 401 / 402 → returns {} (caller logs + skips this tick)
  - Partial response (some tickers missing from FMP's reply) → returned dict
    only contains tickers FMP knew about; missing ones treated as "no data"
    by the caller (no alert fires, no false positive)
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
    """One ticker's quote, normalized.

    `changes_percentage` is in DECIMAL form (e.g. -0.115 = -11.5%), NOT the
    raw FMP percent.
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
            # FMP changed the field name between endpoints:
            #   /stable/quote?symbol=X        → "changesPercentage" (PLURAL)
            #   /stable/batch-quote?symbols=X → "changePercentage"  (SINGULAR)
            #   /stable/batch-quote-short?... → "change"            (raw delta, not pct)
            # Verified live on 2026-05-20 with upgraded plan key. Accept both
            # field names so the parser works regardless of which path
            # _fetch_one_batch took (bulk vs per-ticker fallback).
            pct = r.get("changePercentage")
            if pct is None:
                pct = r.get("changesPercentage")
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


# ── Internals ───────────────────────────────────────────────────────────────


def _chunks(seq: list[str], n: int):
    """Yield successive n-sized chunks from seq."""
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _fetch_one_batch(symbols: list[str]) -> list[dict]:
    """Single FMP call for up to 100 symbols. Tries bulk endpoint first;
    falls back to per-ticker quotes when bulk isn't on the plan.

    Returns the raw FMP rows or [] on any failure. Logs but never raises.

    URL FORMAT HISTORY (2026-05):
      v1 (broken): `/stable/quote?symbol=AAPL,MSFT,GOOGL` (query-param)
                   → 200 OK but empty array. FMP silently dropped the comma
                     list. Discovered when every Tier 1 cron tick reported
                     `quotes_returned: 0`.
      v2 (wrong endpoint, commit 59b1d09):
                   `/stable/quote/AAPL,MSFT,GOOGL` (path-style)
                   → 404 not-found. `/stable/quote/{TICKER}` is a SINGLE-
                     ticker path; multi-ticker comma in the PATH isn't
                     supported on this account's plan. Discovered 2026-05-20
                     from dispatcher logs.
      v3 (current): `/stable/batch-quote?symbols=AAPL,MSFT,GOOGL`
                   → correct bulk endpoint per FMP /stable/batch-quote docs.
                     Note the param name is `symbols` (plural), NOT `symbol`.

    Fallback: if `/stable/batch-quote` returns None (404 / 402 / plan limit),
    we fan out into per-ticker `/stable/quote?symbol=TICKER` calls. That
    endpoint is on every FMP plan and is the same one watchlist_service.py
    uses for single-ticker fetches. The fanout costs N API calls instead
    of 1, but Tier 1 universes are small (~10 tickers) so the cost is
    negligible. The fallback ALSO trips when bulk returns an empty list
    (FMP sometimes degrades to silently empty rather than 4xx for plan
    issues — defense in depth catches both signatures).
    """
    if not symbols:
        return []

    try:
        from src.tools.api import _fmp_get, _STABLE   # type: ignore
    except Exception as exc:
        logger.warning("batch_quote: cannot import _fmp_get/_STABLE: %s", exc)
        return []

    symbols_csv = ",".join(symbols)

    # ── v3: primary — bulk endpoint with `symbols` query param ───────────
    try:
        data = _fmp_get(
            f"{_STABLE}/batch-quote",
            params={"symbols": symbols_csv},
            api_key=None,
            uncap=True,
        )
        if isinstance(data, list) and data:
            return data
        # Empty list = plan likely doesn't include batch-quote OR FMP
        # degraded to silent-empty. Fall through to per-ticker fallback.
        logger.info(
            "batch_quote: /stable/batch-quote returned empty for %d symbols — "
            "falling back to per-ticker /stable/quote", len(symbols),
        )
    except Exception as exc:
        logger.warning("batch_quote: /stable/batch-quote raised: %s — falling back", exc)

    # ── Fallback: per-ticker /stable/quote?symbol=TICKER ─────────────────
    rows: list[dict] = []
    for sym in symbols:
        try:
            data = _fmp_get(
                f"{_STABLE}/quote",
                params={"symbol": sym},
                api_key=None,
                uncap=True,
            )
            if isinstance(data, list) and data:
                rows.extend(data)
        except Exception as exc:
            logger.warning("batch_quote: per-ticker /stable/quote failed for %s: %s", sym, exc)
            continue
    return rows
