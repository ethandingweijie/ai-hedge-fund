"""
watchlist_service.py
====================
Manages a personal watchlist stored in SQLite (run_archive.db).

Storage strategy
----------------
The watchlist table is the single source of truth for VGPM and price:

  ticker        — primary key (uppercase)
  company_name  — from FMP profile on add
  added_at      — ISO timestamp
  price         — last known price (persisted, never null after first fetch)
  vgpm_json     — last known VGPM dict as JSON (persisted)
  vgpm_updated_at — ISO timestamp of last VGPM/price refresh

API call cadence
----------------
- Page load → read directly from watchlist table  (no API call)
- If vgpm_updated_at is older than STALE_HOURS  → refresh from FMP in-request,
  write back to watchlist table, return fresh data
- First add → always fetch profile + VGPM and store immediately

VGPM priority
-------------
  1. Pipeline VGPM — from completed full analysis in web_runs (authoritative)
  2. Fast VGPM    — screener_service.lookup_ticker() (FMP metrics, 24h cache)
"""
import json
import os
import sqlite3
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_STABLE = "https://financialmodelingprep.com/stable"
STALE_HOURS = 24   # refresh VGPM/price if older than this


def _get_db_path() -> str:
    import os
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    this_file = Path(__file__)
    project_root = this_file.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect(path: str | None = None, **kwargs) -> sqlite3.Connection:
    """Open run_archive.db with WAL mode, NORMAL sync, and a 5-second busy timeout."""
    conn = sqlite3.connect(path or _get_db_path(), **kwargs)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _get_fmp_key() -> Optional[str]:
    return os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY")


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    company_name    TEXT,
    added_at        TEXT NOT NULL,
    price           REAL,
    vgpm_json       TEXT,
    vgpm_updated_at TEXT,
    vgpm_source     TEXT,
    user_id         INTEGER
)
"""

# Columns added after initial table creation — safe to run on existing DBs
_MIGRATIONS = [
    "ALTER TABLE watchlist ADD COLUMN price           REAL",
    "ALTER TABLE watchlist ADD COLUMN vgpm_json       TEXT",
    "ALTER TABLE watchlist ADD COLUMN vgpm_updated_at TEXT",
    "ALTER TABLE watchlist ADD COLUMN vgpm_source     TEXT",
    "ALTER TABLE watchlist ADD COLUMN user_id         INTEGER",
]

# Replace old UNIQUE(ticker) with UNIQUE(ticker, user_id) for per-user watchlists
_POST_MIGRATIONS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_user_ticker ON watchlist(user_id, ticker)",
]


def _ensure_table():
    conn = _connect()
    try:
        conn.execute(_DDL)
        conn.commit()
        # Apply migrations idempotently (SQLite raises if column already exists)
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass  # column already exists
        for sql in _POST_MIGRATIONS:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass
    finally:
        conn.close()


# ── FMP helpers ───────────────────────────────────────────────────────────────

def _batch_fetch_prices(tickers: list[str]) -> dict[str, float]:
    """Batch-fetch current prices for multiple tickers in one FMP /stable/quote call."""
    if not tickers:
        return {}
    key = _get_fmp_key()
    if not key:
        return {}
    try:
        r = requests.get(
            f"{_STABLE}/quote/{','.join(tickers)}",
            params={"apikey": key},
            timeout=10,
        )
        if not r.ok:
            return {}
        data = r.json()
        if not isinstance(data, list):
            return {}
        return {
            item["symbol"]: item["price"]
            for item in data
            if item.get("symbol") and item.get("price") is not None
        }
    except Exception:
        return {}


def _fetch_profile(ticker: str) -> dict:
    key = _get_fmp_key()
    if not key:
        return {}
    try:
        r = requests.get(f"{_STABLE}/profile", params={"symbol": ticker, "apikey": key}, timeout=8)
        data = r.json() if r.ok else []
        return data[0] if isinstance(data, list) and data else {}
    except Exception:
        return {}


# ── VGPM fetch (pipeline → fast) ─────────────────────────────────────────────

def _get_pipeline_vgpm(tickers: list[str]) -> dict[str, dict]:
    """Return VGPM from the latest full pipeline run in web_runs."""
    if not tickers:
        return {}
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, full_result_json, MAX(run_at) AS latest "
            f"FROM web_runs WHERE ticker IN ({placeholders}) AND full_result_json IS NOT NULL "
            f"GROUP BY ticker",
            tickers,
        ).fetchall()
    finally:
        conn.close()

    result = {}
    for row in rows:
        ticker = row["ticker"]
        try:
            data = json.loads(row["full_result_json"])
            vgpm_raw = data.get("vgpm", {}).get(ticker, {})
            if vgpm_raw and isinstance(vgpm_raw, dict):
                result[ticker] = {
                    dim: {"score": v.get("score", 0), "grade": v.get("grade", "—")}
                    for dim, v in vgpm_raw.items()
                    if isinstance(v, dict)
                }
        except Exception:
            pass
    return result


def _fetch_vgpm_and_price(ticker: str) -> dict:
    """
    Fetch fresh VGPM + price for one ticker.
    Returns {"vgpm": ..., "price": ..., "company_name": ...}.
    Uses pipeline VGPM if available, otherwise fast VGPM from screener_service.
    API calls are backed by screener_service's SQLite caches (raw_metrics_cache,
    fast_vgpm_cache, screener_lookup_cache) so FMP is only hit when truly stale.
    """
    # 1. Pipeline VGPM (authoritative, from web_runs — no API call)
    pipeline = _get_pipeline_vgpm([ticker])
    vgpm = pipeline.get(ticker)
    source = "pipeline" if vgpm else "fast"
    price = None
    company_name = None

    # 2. Fast VGPM + price via lookup_ticker (hits screener caches first)
    try:
        from app.backend.services import screener_service
        item = screener_service.lookup_ticker(ticker)
        if item:
            if not vgpm and item.get("vgpm"):
                vgpm = item["vgpm"]
                # source stays 'fast' — pipeline already set to None above
            price = item.get("price")
            company_name = item.get("companyName")
    except Exception:
        pass

    # 3. If price still missing, hit FMP /quote directly (bypasses lookup cache)
    if price is None:
        try:
            from app.backend.services import screener_service as _ss
            import requests as _req
            api_key = _ss._get_fmp_key()
            base = {"apikey": api_key} if api_key else {}
            resp = _req.get(
                f"{_ss._STABLE}/quote/{ticker}",
                params=base,
                timeout=8,
            )
            if resp.ok:
                data = resp.json()
                if data and isinstance(data, list):
                    price = data[0].get("price")
        except Exception:
            pass

    return {"vgpm": vgpm, "price": price, "company_name": company_name, "source": source}


def _is_stale(updated_at: Optional[str]) -> bool:
    """True if updated_at is missing or older than STALE_HOURS."""
    if not updated_at:
        return True
    try:
        dt = datetime.fromisoformat(updated_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt > timedelta(hours=STALE_HOURS)
    except Exception:
        return True


def _write_vgpm_to_watchlist(
    ticker: str,
    vgpm: Optional[dict],
    price: Optional[float],
    source: str = "fast",
    user_id: Optional[int] = None,
):
    """Persist the latest VGPM and price back into the watchlist row.

    source: 'pipeline' (from full analysis run) or 'fast' (screener estimate).
    Pipeline VGPM is authoritative and will not be downgraded to 'fast' by
    the stale-refresh path — see get_watchlist() for the guard logic.
    Price is only written when non-None — never overwrites a stored price with NULL.
    """
    conn = _connect()
    now_iso = datetime.now(timezone.utc).isoformat()
    # Build WHERE clause — scope to user when provided
    where = "WHERE ticker = ?"
    params_suffix = [ticker]
    if user_id is not None:
        where += " AND user_id = ?"
        params_suffix.append(user_id)
    try:
        if price is not None:
            conn.execute(
                f"UPDATE watchlist SET vgpm_json = ?, price = ?, vgpm_updated_at = ?, vgpm_source = ? {where}",
                [json.dumps(vgpm) if vgpm else None, price, now_iso, source] + params_suffix,
            )
        else:
            conn.execute(
                f"UPDATE watchlist SET vgpm_json = ?, vgpm_updated_at = ?, vgpm_source = ? {where}",
                [json.dumps(vgpm) if vgpm else None, now_iso, source] + params_suffix,
            )
        conn.commit()
    finally:
        conn.close()


# ── Composite score helper ────────────────────────────────────────────────────

def _composite(vgpm: Optional[dict]) -> Optional[int]:
    if not vgpm:
        return None
    scores = [v["score"] for v in vgpm.values() if isinstance(v.get("score"), (int, float))]
    return round(sum(scores) / len(scores)) if scores else None


# ── Public API ────────────────────────────────────────────────────────────────

def get_watchlist(user_id: Optional[int] = None) -> list[dict]:
    """
    Load watchlist from SQLite, scoped to user_id.
    - Fresh rows (updated within STALE_HOURS): returned instantly, no API call.
    - Stale rows: refreshed from FMP (backed by screener caches), persisted back.
    """
    _ensure_table()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        if user_id is not None:
            rows = conn.execute(
                "SELECT ticker, company_name, added_at, price, vgpm_json, vgpm_updated_at, vgpm_source "
                "FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ticker, company_name, added_at, price, vgpm_json, vgpm_updated_at, vgpm_source "
                "FROM watchlist WHERE user_id IS NULL ORDER BY added_at DESC"
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    # Always batch-fetch live prices in one API call (cheap, fast, single request)
    all_tickers = [row["ticker"] for row in rows]
    live_prices = _batch_fetch_prices(all_tickers)

    items = []
    for row in rows:
        t    = row["ticker"]
        vgpm = None
        # Use live price if available, fall back to last stored value
        price = live_prices.get(t) or row["price"]
        current_source = row["vgpm_source"] or "fast"

        if row["vgpm_json"]:
            try:
                vgpm = json.loads(row["vgpm_json"])
            except Exception:
                pass

        # Persist live price back to DB (non-None guard inside _write_vgpm_to_watchlist)
        if t in live_prices:
            _write_vgpm_to_watchlist(t, vgpm, live_prices[t], source=current_source, user_id=user_id)

        # Refresh stale VGPM (prices already handled above)
        if _is_stale(row["vgpm_updated_at"]) or price is None:
            fresh = _fetch_vgpm_and_price(t)

            # Pipeline VGPM is authoritative: only overwrite it with a pipeline
            # result, never with a fast estimate.  Fast-sourced rows accept
            # either (pipeline wins if the ticker was later analysed).
            fresh_vgpm   = fresh.get("vgpm")
            fresh_source = fresh.get("source", "fast")

            if current_source == "pipeline":
                if fresh_source == "pipeline" and fresh_vgpm:
                    vgpm = fresh_vgpm
                if fresh.get("price") is not None:
                    price = fresh["price"]
                _write_vgpm_to_watchlist(t, vgpm, price, source="pipeline", user_id=user_id)
            else:
                if fresh_vgpm:
                    vgpm = fresh_vgpm
                if fresh.get("price") is not None:
                    price = fresh["price"]
                _write_vgpm_to_watchlist(t, vgpm, price, source=fresh_source, user_id=user_id)

        items.append({
            "ticker":          t,
            "companyName":     row["company_name"] or t,
            "addedAt":         row["added_at"],
            "price":           price,
            "vgpm":            vgpm,
            "composite_score": _composite(vgpm),
        })
    return items


def add_ticker(ticker: str, user_id: Optional[int] = None) -> dict:
    _ensure_table()
    ticker = ticker.strip().upper()

    # Fetch profile for company name (FMP call, not cached separately)
    profile = _fetch_profile(ticker)
    company_name = profile.get("companyName") or ticker
    now = datetime.now(timezone.utc).isoformat()

    # Fetch VGPM + price immediately on add
    fresh = _fetch_vgpm_and_price(ticker)
    vgpm  = fresh.get("vgpm")
    price = fresh.get("price")
    if fresh.get("company_name") and not profile.get("companyName"):
        company_name = fresh["company_name"]

    source = fresh.get("source", "fast")

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO watchlist
                (ticker, company_name, added_at, price, vgpm_json, vgpm_updated_at, vgpm_source, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                company_name,
                now,
                price,
                json.dumps(vgpm) if vgpm else None,
                now,
                source,
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ticker":          ticker,
        "companyName":     company_name,
        "addedAt":         now,
        "price":           price,
        "vgpm":            vgpm,
        "composite_score": _composite(vgpm),
    }


def remove_ticker(ticker: str, user_id: Optional[int] = None) -> bool:
    _ensure_table()
    ticker = ticker.strip().upper()
    conn = _connect()
    try:
        if user_id is not None:
            cur = conn.execute("DELETE FROM watchlist WHERE ticker = ? AND user_id = ?", (ticker, user_id))
        else:
            cur = conn.execute("DELETE FROM watchlist WHERE ticker = ? AND user_id IS NULL", (ticker,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def is_in_watchlist(ticker: str, user_id: Optional[int] = None) -> bool:
    _ensure_table()
    ticker = ticker.strip().upper()
    conn = _connect()
    try:
        if user_id is not None:
            row = conn.execute(
                "SELECT 1 FROM watchlist WHERE ticker = ? AND user_id = ?", (ticker, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM watchlist WHERE ticker = ? AND user_id IS NULL", (ticker,)
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def refresh_ticker_vgpm(ticker: str):
    """
    Force-refresh VGPM and price for a ticker after a pipeline analysis completes.
    Always tagged as source='pipeline' because this is only called post-analysis.
    Writes result directly to the watchlist table.
    Called by analysis_service after a run finishes.
    """
    ticker = ticker.strip().upper()
    _ensure_table()
    fresh = _fetch_vgpm_and_price(ticker)
    _write_vgpm_to_watchlist(ticker, fresh.get("vgpm"), fresh.get("price"), source="pipeline")
