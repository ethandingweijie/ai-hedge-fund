"""
app/backend/services/etf_metadata_service.py
==============================================
Live FMP metadata for the Robo Strategy ETF universe (app/backend/data/etf_universe.py).

For every curated ticker, fetches:
  - price                                            (batch /stable/quote, same call watchlist_service uses)
  - expenseRatio, assetsUnderManagement, name         (/stable/etf/info)
  - sectorWeights: {sector: pct}                      (/stable/etf/sector-weightings)
  - regionWeights: {region: pct}                      (/stable/etf/country-weightings, aggregated)
  - holdings: [{asset, name, weightPercentage}]        (/stable/etf/holdings, top 50)
  - topHoldings: the first 5 of the above, for display
  - holdingsCount / holdingsCoveredPct: how many constituents the fund has
    and how much of its weight the kept rows explain

All four ETF-specific endpoints are single-symbol only (no confirmed batch
support) — fetched concurrently via a small ThreadPoolExecutor. The whole
universe is cached 24h under one fixed cache key, so a cache hit costs zero
FMP calls; a cold refresh costs ~43 tickers x up to 4 calls, run concurrently
to keep cold-start latency low.

If any single endpoint fails for a ticker, that ticker's field degrades to
None/empty rather than the ticker being dropped or the whole batch failing.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from app.backend.data.etf_universe import ETF_UNIVERSE, all_tickers, bucket_for

logger = logging.getLogger(__name__)

_STABLE = "https://financialmodelingprep.com/stable"
_CACHE_KEY = "etf_universe_v2"   # v2: full look-through holdings + coverage
_TTL_HOURS = 24

# FMP country name -> our 4-way macro-region taxonomy: US / Europe /
# Asia-Pacific / Emerging Markets. This is a deliberate PRODUCT taxonomy, not
# a strict MSCI developed/emerging classification: Hong Kong is grouped under
# Emerging Markets (with China) rather than its technical MSCI-Developed
# status, because in this app HK exposure is used as a Greater-China proxy
# (HK-listed China tech, KWEB/MCHI holdings, HK-listed dual-class stocks) —
# putting it in "Asia-Pacific" alongside Japan/Australia would dilute the
# one geography-preference lever a user has for dialing up China/HK exposure.
# South Korea/Taiwan placement (APAC vs EM) is a judgment call some index
# providers disagree on — kept as one clearly-named constant so it's a
# one-line fix if it ever looks wrong once real data is flowing through.
_COUNTRY_TO_REGION: dict[str, str] = {
    "United States": "US",

    "United Kingdom": "Europe",
    "Germany": "Europe",
    "France": "Europe",
    "Switzerland": "Europe",
    "Netherlands": "Europe",
    "Sweden": "Europe",
    "Spain": "Europe",
    "Italy": "Europe",
    "Denmark": "Europe",
    "Belgium": "Europe",
    "Norway": "Europe",
    "Finland": "Europe",
    "Israel": "Europe",
    "Ireland": "Europe",
    "Austria": "Europe",
    "Portugal": "Europe",

    "Japan": "Asia-Pacific",
    "Australia": "Asia-Pacific",
    "New Zealand": "Asia-Pacific",
    "Singapore": "Asia-Pacific",
    "South Korea": "Asia-Pacific",
    "Korea": "Asia-Pacific",
    "Canada": "Asia-Pacific",  # developed, non-US, non-Europe — closest-fit bucket

    "China": "Emerging Markets",
    "Hong Kong": "Emerging Markets",
    "India": "Emerging Markets",
    "Taiwan": "Emerging Markets",
    "Brazil": "Emerging Markets",
    "South Africa": "Emerging Markets",
    "Mexico": "Emerging Markets",
    "Indonesia": "Emerging Markets",
    "Thailand": "Emerging Markets",
    "Malaysia": "Emerging Markets",
    "Philippines": "Emerging Markets",
    "Vietnam": "Emerging Markets",
    "Saudi Arabia": "Emerging Markets",
    "United Arab Emirates": "Emerging Markets",
    "Turkey": "Emerging Markets",
    "Poland": "Emerging Markets",
    "Chile": "Emerging Markets",
    "Colombia": "Emerging Markets",
    "Peru": "Emerging Markets",
    "Qatar": "Emerging Markets",
    "Kuwait": "Emerging Markets",
    "Egypt": "Emerging Markets",
    "Greece": "Emerging Markets",
    "Czech Republic": "Emerging Markets",
    "Hungary": "Emerging Markets",
}


def _region_for_country(country: str) -> Optional[str]:
    return _COUNTRY_TO_REGION.get(country.strip())


def _get_db_path() -> str:
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _get_fmp_key() -> Optional[str]:
    return os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY")


_DDL = """
CREATE TABLE IF NOT EXISTS etf_metadata_cache (
    cache_key    TEXT PRIMARY KEY,
    fetched_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    results_json TEXT NOT NULL
)
"""


def _ensure_table() -> None:
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect()
    try:
        conn.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def _get_cached() -> Optional[list[dict]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT results_json, expires_at FROM etf_metadata_cache WHERE cache_key = ?",
            (_CACHE_KEY,),
        ).fetchone()
        if not row:
            return None
        if datetime.now(timezone.utc).isoformat() > row["expires_at"]:
            return None
        return json.loads(row["results_json"])
    finally:
        conn.close()


def _set_cached(results: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=_TTL_HOURS)).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO etf_metadata_cache "
            "(cache_key, fetched_at, expires_at, results_json) VALUES (?, ?, ?, ?)",
            (_CACHE_KEY, now.isoformat(), expires, json.dumps(results)),
        )
        conn.commit()
    finally:
        conn.close()


def invalidate_etf_cache() -> None:
    _ensure_table()
    conn = _connect()
    try:
        conn.execute("DELETE FROM etf_metadata_cache WHERE cache_key = ?", (_CACHE_KEY,))
        conn.commit()
    finally:
        conn.close()


# ── FMP calls ────────────────────────────────────────────────────────────────
#
# NOTE: watchlist_service._batch_fetch_prices's path-style batch call
# (GET /stable/quote/{comma-list}) was the original template here, but a
# live smoke test against this project's actual FMP_API_KEY found it 404s,
# and even the query-style multi-symbol form (?symbol=A,B) returns an empty
# list on this account/plan — only single-symbol query-style
# (?symbol=X) reliably returns data. Price is therefore fetched per-ticker,
# concurrently alongside the other three per-ticker endpoints below, rather
# than as a separate batch pre-fetch step.

def _fetch_price(ticker: str, key: str) -> Optional[float]:
    try:
        r = requests.get(f"{_STABLE}/quote", params={"symbol": ticker, "apikey": key}, timeout=10)
        if not r.ok:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
        price = data[0].get("price")
        return float(price) if price is not None else None
    except Exception as exc:
        logger.warning("quote failed for %s: %s", ticker, exc)
        return None


def _fetch_etf_info(ticker: str, key: str) -> dict:
    try:
        r = requests.get(f"{_STABLE}/etf/info", params={"symbol": ticker, "apikey": key}, timeout=10)
        if not r.ok:
            return {}
        data = r.json()
        if not isinstance(data, list) or not data:
            return {}
        row = data[0]
        return {
            "name": row.get("name"),
            "expenseRatio": row.get("expenseRatio"),
            "assetsUnderManagement": row.get("assetsUnderManagement"),
        }
    except Exception as exc:
        logger.warning("etf/info failed for %s: %s", ticker, exc)
        return {}


def _fetch_sector_weightings(ticker: str, key: str) -> dict[str, float]:
    try:
        r = requests.get(f"{_STABLE}/etf/sector-weightings", params={"symbol": ticker, "apikey": key}, timeout=10)
        if not r.ok:
            return {}
        data = r.json()
        if not isinstance(data, list):
            return {}
        weights: dict[str, float] = {}
        for row in data:
            sector = row.get("sector")
            pct = row.get("weightPercentage")
            if sector and isinstance(pct, (int, float)):
                weights[sector] = float(pct)
        return weights
    except Exception as exc:
        logger.warning("etf/sector-weightings failed for %s: %s", ticker, exc)
        return {}


def _parse_pct(value) -> Optional[float]:
    """country-weightings returns weightPercentage as a string like '97.82%'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _fetch_region_weightings(ticker: str, key: str) -> dict[str, float]:
    try:
        r = requests.get(f"{_STABLE}/etf/country-weightings", params={"symbol": ticker, "apikey": key}, timeout=10)
        if not r.ok:
            return {}
        data = r.json()
        if not isinstance(data, list):
            return {}
        region_weights: dict[str, float] = {}
        for row in data:
            country = row.get("country")
            pct = _parse_pct(row.get("weightPercentage"))
            if not country or pct is None:
                continue
            region = _region_for_country(country)
            if region is None:
                continue  # unmapped country — dropped rather than mis-bucketed
            region_weights[region] = region_weights.get(region, 0.0) + pct
        return region_weights
    except Exception as exc:
        logger.warning("etf/country-weightings failed for %s: %s", ticker, exc)
        return {}


# How many holdings to keep per fund for the look-through. Full lists run to
# thousands (VTI returns 3,524), which would bloat the cached universe for no
# real gain — weights tail off fast. Truncating is only honest if the caller
# is told how much of the fund is actually covered, hence holdingsCoveredPct.
_LOOKTHROUGH_DEPTH = 50


def _fetch_holdings(ticker: str, key: str, depth: int = _LOOKTHROUGH_DEPTH) -> dict:
    """Fund constituents by weight, plus honest coverage metadata.

    /etf/holdings is an Ultimate-tier endpoint. It now returns complete
    constituent lists with weights summing to ~100 (verified 2026-08-27:
    SPY 505 rows, QQQ 107, VTI 3,524, KWEB 45 — the last including 0700.HK
    and 9988.HK, so an HK name reached through an ETF resolves to the same
    ticker the rest of this app uses).

    Returns:
        {holdings, topHoldings, holdingsCount, holdingsCoveredPct}
      holdings          — up to `depth` constituents, heaviest first
      topHoldings       — the first 5, kept for the existing display
      holdingsCount     — how many the fund actually reports
      holdingsCoveredPct— total weight of the kept rows, so a look-through
                          can state what share of the fund it explains
    """
    empty = {"holdings": [], "topHoldings": [], "holdingsCount": 0,
             "holdingsCoveredPct": 0.0}
    try:
        r = requests.get(f"{_STABLE}/etf/holdings",
                         params={"symbol": ticker, "apikey": key}, timeout=20)
        if not r.ok:
            return empty
        data = r.json()
        if not isinstance(data, list):
            return empty
        rows = sorted(
            (row for row in data
             if isinstance(row.get("weightPercentage"), (int, float))
             and row.get("weightPercentage") > 0
             and row.get("asset")),
            key=lambda row: row["weightPercentage"],
            reverse=True,
        )
        kept = [
            {
                "asset": row.get("asset"),
                "name": row.get("name"),
                "weightPercentage": round(float(row["weightPercentage"]), 4),
            }
            for row in rows[:depth]
        ]
        return {
            "holdings": kept,
            "topHoldings": kept[:5],
            "holdingsCount": len(rows),
            "holdingsCoveredPct": round(sum(h["weightPercentage"] for h in kept), 2),
        }
    except Exception as exc:
        logger.warning("etf/holdings failed for %s: %s", ticker, exc)
        return empty


def _fetch_one_ticker_metadata(ticker: str, key: str) -> dict:
    info = _fetch_etf_info(ticker, key)
    return {
        "ticker": ticker,
        "bucket": bucket_for(ticker),
        "price": _fetch_price(ticker, key),
        "name": info.get("name"),
        "expenseRatio": info.get("expenseRatio"),
        "assetsUnderManagement": info.get("assetsUnderManagement"),
        "sectorWeights": _fetch_sector_weightings(ticker, key),
        "regionWeights": _fetch_region_weightings(ticker, key),
        **_fetch_holdings(ticker, key),
    }


def get_etf_universe(force_refresh: bool = False) -> list[dict]:
    """Return the full curated ETF universe with live-fetched metadata (24h cached)."""
    _ensure_table()
    if not force_refresh:
        cached = _get_cached()
        if cached is not None:
            return cached

    tickers = all_tickers()
    key = _get_fmp_key()
    if not key:
        logger.error("get_etf_universe: no FMP API key configured — returning bucket-only entries")
        return [
            {"ticker": e["ticker"], "bucket": e["bucket"], "price": None,
             "name": None, "expenseRatio": None, "assetsUnderManagement": None,
             "sectorWeights": {}, "regionWeights": {}, "topHoldings": [],
             "holdings": [], "holdingsCount": 0, "holdingsCoveredPct": 0.0}
            for e in ETF_UNIVERSE
        ]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_fetch_one_ticker_metadata, ticker, key): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning("ETF metadata fetch failed entirely for %s: %s", ticker, exc)
                results.append({
                    "ticker": ticker, "bucket": bucket_for(ticker), "price": None,
                    "name": None, "expenseRatio": None, "assetsUnderManagement": None,
                    "sectorWeights": {}, "regionWeights": {}, "topHoldings": [],
                    "holdings": [], "holdingsCount": 0, "holdingsCoveredPct": 0.0,
                })

    # Keep deterministic ordering matching the static universe list.
    order = {t: i for i, t in enumerate(tickers)}
    results.sort(key=lambda r: order.get(r["ticker"], 0))

    _set_cached(results)
    return results
