"""
app/backend/services/knowledge_graph.py
========================================
Shared, persistent per-ticker financial-data cache (the "knowledge graph")
that both the screener's VGPM scoring and the live analysis pipeline read
from, replacing two previously-independent live-fetch paths:

  - The screener's `_fetch_ticker_metrics` (8 FMP endpoints/ticker, TTM
    ratios + growth + momentum sub-factors) now writes/reads
    `ttm_metrics_json` here instead of the old `raw_metrics_cache` table.

  - The live pipeline's VGPM ROIC-proxy submetric (`_compute_vgpm` in
    src/utils/pdf_report.py) needs last-fiscal-year revenue/net_income.
    Previously this came from `state["data"]["raw_financials"]`, an
    LLM-reformatted value cached for up to 30 days in `ticker_routing_cache`.
    `get_kg_annual_line_items()` sources the same fields directly from FMP
    (bypassing the LLM reformatting step entirely) and caches them here.

One row per ticker. TTM metrics use a blind 24h TTL (same as before this
change). Annual line items additionally track the earnings date they were
captured against (`annual_as_of_earnings_date`) so a row is treated stale
the moment a newer earnings date is observed — even inside the 24h TTL
window — since that's the only real-world event that changes reported
annual financials.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_TTL_HOURS = 24

_ANNUAL_LINE_ITEM_FIELDS = [
    "revenue", "net_income", "operating_cash_flow", "net_debt",
    "capital_expenditure", "free_cash_flow", "total_assets", "total_liabilities",
]


def _get_db_path() -> str:
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or _get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


_DDL = """
CREATE TABLE IF NOT EXISTS kg_ticker_metrics (
    ticker                     TEXT PRIMARY KEY,
    sector                     TEXT,
    industry                   TEXT,
    ttm_metrics_json           TEXT,
    ttm_fetched_at             TEXT,
    ttm_expires_at             TEXT,
    annual_line_items_json     TEXT,
    annual_fetched_at          TEXT,
    annual_expires_at          TEXT,
    annual_as_of_earnings_date TEXT,
    last_earnings_date         TEXT,
    source                     TEXT
)
"""

_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_kg_ticker_sector "
    "ON kg_ticker_metrics(sector, industry)"
)


def _ensure_table() -> None:
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_DDL)
        conn.execute(_INDEX)
        conn.commit()
    finally:
        conn.close()


# ── TTM metrics (screener's 28 VGPM sub-factors) ────────────────────────────
# Same signature/behavior as the old screener_service._get_raw_metrics_cached
# / _set_raw_metrics_cached it supersedes — blind 24h TTL, no change there.

def get_ttm_metrics_cached(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    _ensure_table()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, ttm_metrics_json, ttm_expires_at "
            f"FROM kg_ticker_metrics WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, dict] = {}
    for row in rows:
        if not row["ttm_metrics_json"] or not row["ttm_expires_at"]:
            continue
        if row["ttm_expires_at"] <= now_iso:
            continue
        try:
            result[row["ticker"]] = json.loads(row["ttm_metrics_json"])
        except Exception:
            continue
    return result


def set_ttm_metrics(
    data: dict[str, dict],
    sector_map: Optional[dict[str, str]] = None,
    industry_map: Optional[dict[str, str]] = None,
    source: str = "screener_backfill",
    ttl_hours: int = _TTL_HOURS,
) -> None:
    if not data:
        return
    _ensure_table()
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    now_iso = now.isoformat()
    sector_map = sector_map or {}
    industry_map = industry_map or {}
    conn = _connect()
    try:
        for t, metrics in data.items():
            last_earnings = metrics.get("last_earnings_date")
            conn.execute(
                """
                INSERT INTO kg_ticker_metrics
                    (ticker, sector, industry, ttm_metrics_json, ttm_fetched_at, ttm_expires_at,
                     last_earnings_date, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    sector             = excluded.sector,
                    industry           = excluded.industry,
                    ttm_metrics_json   = excluded.ttm_metrics_json,
                    ttm_fetched_at     = excluded.ttm_fetched_at,
                    ttm_expires_at     = excluded.ttm_expires_at,
                    last_earnings_date = COALESCE(excluded.last_earnings_date, kg_ticker_metrics.last_earnings_date),
                    source             = excluded.source
                """,
                (t, sector_map.get(t), industry_map.get(t), json.dumps(metrics),
                 now_iso, expires, last_earnings, source),
            )
        conn.commit()
    finally:
        conn.close()


# ── Annual line items (live-pipeline VGPM ROIC-proxy input) ────────────────

def get_annual_line_items_cached(ticker: str) -> Optional[dict]:
    """Fresh {"<report_period>": {field: value, ...}} for one ticker, or None
    if missing, TTL-expired, or a newer earnings date has since been seen."""
    _ensure_table()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        row = conn.execute(
            "SELECT annual_line_items_json, annual_expires_at, "
            "       last_earnings_date, annual_as_of_earnings_date "
            "FROM kg_ticker_metrics WHERE ticker = ?",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["annual_line_items_json"] or not row["annual_expires_at"]:
        return None
    if row["annual_expires_at"] <= now_iso:
        return None
    if row["last_earnings_date"] and row["annual_as_of_earnings_date"]:
        if row["last_earnings_date"] > row["annual_as_of_earnings_date"]:
            return None
    try:
        return json.loads(row["annual_line_items_json"])
    except Exception:
        return None


def set_annual_line_items(
    ticker: str,
    annual: dict,
    sector: Optional[str] = None,
    source: str = "live_pipeline_fetch",
    ttl_hours: int = _TTL_HOURS,
) -> None:
    if not annual:
        return
    _ensure_table()
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    now_iso = now.isoformat()
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT last_earnings_date FROM kg_ticker_metrics WHERE ticker = ?", (ticker,)
        ).fetchone()
        as_of_earnings = existing[0] if existing else None
        conn.execute(
            """
            INSERT INTO kg_ticker_metrics
                (ticker, sector, annual_line_items_json, annual_fetched_at, annual_expires_at,
                 annual_as_of_earnings_date, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                sector                     = COALESCE(kg_ticker_metrics.sector, excluded.sector),
                annual_line_items_json     = excluded.annual_line_items_json,
                annual_fetched_at          = excluded.annual_fetched_at,
                annual_expires_at          = excluded.annual_expires_at,
                annual_as_of_earnings_date = excluded.annual_as_of_earnings_date,
                source                     = excluded.source
            """,
            (ticker, sector, json.dumps(annual), now_iso, expires, as_of_earnings, source),
        )
        conn.commit()
    finally:
        conn.close()


def get_kg_annual_line_items(ticker: str, end_date: str, sector: Optional[str] = None) -> dict:
    """
    Return {"<report_period>": {field: value, ...}} for the ticker's most
    recent annual fiscal years — same fields/shape as strategic_router.py's
    search_line_items call, but consumed here without the LLM reformatting
    step in between. KG-fresh (~24h + earnings-date-aware) or live-fetch
    (throttled via src/tools/api.py) and cache the result.

    Used by _compute_vgpm's ROIC-proxy submetric so the live pipeline's VGPM
    doesn't need a full live fetch or the 30-day-stale routing cache just to
    get last-fiscal-year net_income/revenue.
    """
    cached = get_annual_line_items_cached(ticker)
    if cached:
        return cached

    try:
        from src.tools.api import search_line_items
        items = search_line_items(
            ticker=ticker,
            line_items=_ANNUAL_LINE_ITEM_FIELDS,
            end_date=end_date,
            period="annual",
            limit=5,
        )
    except Exception:
        return {}

    annual: dict[str, dict] = {}
    for item in (items or []):
        period = getattr(item, "report_period", None)
        if not period:
            continue
        row = {}
        for field in _ANNUAL_LINE_ITEM_FIELDS:
            val = getattr(item, field, None)
            if val is not None:
                row[field] = val
        annual[period] = row

    if annual:
        set_annual_line_items(ticker, annual, sector=sector)
    return annual


def delete_ticker(ticker: str) -> None:
    """Drop the cached row for one ticker (e.g. after a live pipeline run
    produces fresher data that should force a re-fetch next time)."""
    _ensure_table()
    conn = _connect()
    try:
        conn.execute("DELETE FROM kg_ticker_metrics WHERE ticker = ?", (ticker,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
