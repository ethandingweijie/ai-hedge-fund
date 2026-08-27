"""
app/backend/services/screener_service.py
=========================================
Fetches candidate stocks from FMP /stable/company-screener, joins with
internal VGPM grades from web_runs, and computes a Fast VGPM for remaining
tickers.

Fast VGPM methodology
---------------------
- Peer universe: industry-relative first (≥5 peers), then sector (≥8), then
  full universe — 3-tier fallback.
- Factors per dimension: 8–9 FMP sub-factors (see _fetch_ticker_metrics).
  yfinance supplements (rec_score, short_ratio) only for single-ticker lookups.
- Weights: sector-specific per dimension (12 GICS profiles + default).
- Caching: raw TTM metrics cached in the shared knowledge graph
  (app/backend/services/knowledge_graph.py, kg_ticker_metrics — also read by
  the live analysis pipeline); computed VGPM in fast_vgpm_cache; screener
  results in screener_cache. All FMP calls (including this module's own)
  are throttled through the shared token bucket in src/tools/api.py.
"""
import hashlib
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import requests

# Dual-mode DB layer (SQLite local / Postgres production)
from src.data import db as _db

_STABLE = "https://financialmodelingprep.com/stable"

# FMP sector names — the same 11 names in every market (US/HK/SG verified
# 2026-08-27). The frontend derives its filter options from the loaded
# universe, so these strings are the de facto contract for both sides.
_SCREENER_SECTORS = [
    "Technology", "Healthcare", "Consumer Cyclical", "Financial Services",
    "Communication Services", "Consumer Defensive", "Energy", "Industrials",
    "Basic Materials", "Real Estate", "Utilities",
]
# How many stocks per sector when fetching the "All sectors" universe.
_PER_SECTOR_LIMIT = 30

# FMP /stable/company-screener ignores the `country` parameter (verified
# 2026-08-27: a country=US call returned LSE/XETRA/TSX/NEO rows), so the US
# universe is filtered to US exchanges after the fetch. Same pooling
# convention as regional_comps.MARKETS["US"].
_US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}

# Bumped whenever cached entries written under the same parameter dict
# become stale — a bump retires them without a manual cache wipe. v2: the
# US universe cache predates the exchange filter above and is contaminated
# with non-US listings.
_CACHE_V = 2


def _us_listing(s: dict) -> bool:
    """True when an FMP screener row is a US-exchange listing."""
    ex = (s.get("exchangeShortName") or s.get("exchange") or "").strip().upper()
    if ex:
        return ex in _US_EXCHANGES
    return (s.get("country") or "").strip().upper() == "US"


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


def _overlay_live(item: dict, quote: dict) -> None:
    """Overwrite price/marketCap/volume/beta/change_pct on a screener item dict with live quote values."""
    for field in ("price", "marketCap", "volume", "beta", "change_pct"):
        v = quote.get(field)
        if v is not None:
            item[field] = v


def update_cached_prices(quotes: dict[str, dict]) -> None:
    """Write live prices back into every screener_cache entry so the 24-h cache stays fresh."""
    if not quotes:
        return
    rows = _db.query("SELECT cache_key, results_json FROM screener_cache")
    for row in rows:
        items = json.loads(row["results_json"])
        changed = False
        for item in items:
            q = quotes.get(item.get("symbol", ""))
            if q:
                _overlay_live(item, q)
                changed = True
        if changed:
            _db.execute(
                "UPDATE screener_cache SET results_json = ? WHERE cache_key = ?",
                [json.dumps(items), row["cache_key"]],
            )


import logging as _logging
_sqlog = _logging.getLogger(__name__)

def get_live_quotes(tickers: list[str], exchanges: Optional[list[str]] = None) -> dict[str, dict]:
    """Fetch live price + volume + day % change for a set of tickers.

    US tickers: FMP batch-exchange-quote (NASDAQ/NYSE/AMEX in parallel).
    HK tickers: yfinance fast_info (previous_close → change_pct computed).
    Returns {symbol: {price, volume, change_pct}}, {} on failure.
    """
    if not tickers:
        return {}

    # ── Split HK vs US ───────────────────────────────────────────────────────
    try:
        from src.tools.hk.ticker import is_hk_ticker, to_yfinance_code as _hk_yf
        hk_tickers = [t for t in tickers if is_hk_ticker(t)]
        us_tickers  = [t for t in tickers if not is_hk_ticker(t)]
    except Exception:
        hk_tickers, us_tickers = [], list(tickers)

    result: dict[str, dict] = {}

    # ── US: FMP /stable/quote — one call per symbol in parallel ─────────────────
    # /stable/quote only accepts a single symbol per request (batch returns []).
    # v3 is deprecated (403) for current API keys. Use per-symbol parallel calls.
    if us_tickers:
        api_key = _get_fmp_key()
        if not api_key:
            _sqlog.warning("get_live_quotes: no FMP API key found")
        else:
            _FMP_STABLE = "https://financialmodelingprep.com/stable/quote"

            def _fetch_one(sym: str) -> tuple[str, dict] | None:
                try:
                    from src.tools.api import acquire_fmp_token
                    acquire_fmp_token()
                    r = requests.get(
                        _FMP_STABLE,
                        params={"symbol": sym, "apikey": api_key},
                        timeout=10,
                    )
                    if not r.ok:
                        _sqlog.debug("FMP quote %s HTTP %s", sym, r.status_code)
                        return None
                    data = r.json()
                    if not isinstance(data, list) or not data:
                        return None
                    item = data[0]
                    if item.get("price") is None:
                        return None
                    q: dict = {
                        "price":  item.get("price"),
                        "volume": item.get("volume"),
                    }
                    pct = item.get("changePercentage")
                    if pct is not None:
                        q["change_pct"] = pct
                    return (sym, q)
                except Exception as exc:
                    _sqlog.debug("FMP quote %s exception: %s", sym, exc)
                    return None

            # Cap at 10 workers to stay within FMP free-tier rate limits (~300 req/min)
            with ThreadPoolExecutor(max_workers=10) as pool:
                for pair in pool.map(_fetch_one, us_tickers):
                    if pair:
                        result[pair[0]] = pair[1]

            _sqlog.info("FMP /stable/quote: fetched %d/%d symbols", len(result), len(us_tickers))
            if result:
                sample = next(iter(result.values()))
                _sqlog.info("Sample: price=%s change_pct=%s", sample.get("price"), sample.get("change_pct", "MISSING"))

    # ── HK: yfinance fast_info (change_pct from previous_close) ─────────────
    if hk_tickers:
        def _fetch_hk(ticker: str) -> tuple[str, dict] | None:
            try:
                import yfinance as yf
                yf_sym = _hk_yf(ticker)
                fi = yf.Ticker(yf_sym).fast_info
                price = fi.get("last_price") or fi.get("regular_market_price")
                prev  = fi.get("previous_close")
                if price is None:
                    return None
                change_pct = ((price - prev) / prev * 100) if prev else None
                volume = fi.get("three_month_average_volume")
                q: dict = {"price": price, "volume": volume}
                if change_pct is not None:   # omit key if unavailable — don't wipe AKShare value
                    q["change_pct"] = change_pct
                return ticker, q
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=min(4, len(hk_tickers))) as pool:
            for res in pool.map(_fetch_hk, hk_tickers):
                if res:
                    result[res[0]] = res[1]

    return result


# ── DB helpers ─────────────────────────────────────────────────────────────────

_DDL_SCREENER = """
CREATE TABLE IF NOT EXISTS screener_cache (
    cache_key    TEXT PRIMARY KEY,
    fetched_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    results_json TEXT NOT NULL
)
"""

_DDL_FAST_VGPM = """
CREATE TABLE IF NOT EXISTS fast_vgpm_cache (
    ticker      TEXT PRIMARY KEY,
    cached_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    data_json   TEXT NOT NULL
)
"""

_DDL_RAW_METRICS = """
CREATE TABLE IF NOT EXISTS raw_metrics_cache (
    ticker      TEXT PRIMARY KEY,
    cached_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    data_json   TEXT NOT NULL
)
"""

_DDL_LOOKUP_CACHE = """
CREATE TABLE IF NOT EXISTS screener_lookup_cache (
    symbol       TEXT PRIMARY KEY,
    fetched_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    item_json    TEXT NOT NULL
)
"""

_DDL_COMPANY_NAME_CACHE = """
CREATE TABLE IF NOT EXISTS company_name_cache (
    ticker      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sector      TEXT,
    industry    TEXT,
    expires_at  TEXT NOT NULL
)
"""

_DDL_MASTER_UNIVERSE = """
CREATE TABLE IF NOT EXISTS master_universe (
    symbol       TEXT PRIMARY KEY,
    data_json    TEXT NOT NULL,
    cached_at    TEXT NOT NULL,
    expires_at   TEXT NOT NULL
)
"""


# DDL target memo so CREATE TABLE IF NOT EXISTS runs once per database
# (per-process in PG mode, per-file in SQLite mode).
_tables_ready_key: Optional[tuple] = None


def _ensure_tables():
    """Create the screener cache tables if missing.

    Dual-mode (SQLite local / Postgres production) via src.data.db — the old
    raw-sqlite3 version 500'd on 2026-08-16 when the /data volume was
    detached from the (now multi-replica) web service.
    """
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.execute_script(";".join([
            _DDL_SCREENER, _DDL_FAST_VGPM, _DDL_RAW_METRICS,
            _DDL_LOOKUP_CACHE, _DDL_COMPANY_NAME_CACHE, _DDL_MASTER_UNIVERSE,
        ]))
        _tables_ready_key = key
    except Exception as exc:
        # A concurrent CREATE TABLE IF NOT EXISTS race at boot is harmless;
        # anything persistent surfaces loudly on the first real query.
        _sqlog.warning("screener _ensure_tables: %s", exc)


def _upsert_sql(table: str, conflict_col: str, columns: list[str]) -> str:
    """INSERT-with-replace SQL for the active DB mode."""
    ph = ", ".join("?" * len(columns))
    if _db.is_postgres():
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != conflict_col)
        return (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({ph}) "
            f"ON CONFLICT ({conflict_col}) DO UPDATE SET {updates}"
        )
    return f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({ph})"


def _upsert(table: str, conflict_col: str, columns: list[str], values: list) -> None:
    _db.execute(_upsert_sql(table, conflict_col, columns), list(values))


def _upsert_many(table: str, conflict_col: str, columns: list[str], rows: list) -> None:
    if rows:
        _db.executemany(
            _upsert_sql(table, conflict_col, columns),
            [list(r) for r in rows],
        )


def _make_cache_key(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _get_cached(cache_key: str) -> Optional[list]:
    row = _db.query_one(
        "SELECT results_json, expires_at FROM screener_cache WHERE cache_key = ?",
        [cache_key],
    )
    if not row:
        return None
    if datetime.now(timezone.utc).isoformat() > row["expires_at"]:
        return None
    return json.loads(row["results_json"])


#: One build per cache key at a time. Without this, every request that
#: arrives during a cold ~2,600-call HK build starts its OWN build; they then
#: compete for the same FMP rate-limit bucket and each one gets slower, which
#: is how a slow first load became a failing one. Waiters serve stale instead.
#: How far back a PREVIOUS cache-key version may be borrowed from. Bounded
#: so a long-dead payload can never resurface — the point is to bridge the
#: minutes after a deploy, not to serve last month's screener.
_STALE_VERSION_MAX_AGE_DAYS = 30

_BUILD_LOCKS: dict[str, threading.Lock] = {}
_BUILD_LOCKS_GUARD = threading.Lock()


def _build_lock(cache_key: str) -> threading.Lock:
    with _BUILD_LOCKS_GUARD:
        return _BUILD_LOCKS.setdefault(cache_key, threading.Lock())


def _get_cached_stale(cache_key: str) -> Optional[list]:
    """Cached rows IGNORING the TTL, falling back across cache-key versions.

    A cold full-universe build cannot finish inside an HTTP request — HK ran
    300s and the browser gave up, which is what "loading failed" looked like.
    Expired rows are a far better answer than a timeout: the weekly job
    rewrites them, so the worst case is data a few days old.

    The version fallback matters as much as the TTL one. Cache keys carry a
    version suffix and get bumped whenever the row shape or a field fix
    changes (sg_universe_v1 -> sg_fmp_v2 -> sg_fmp_v3). A bump leaves ZERO
    rows under the new key, so an exact-key lookup finds nothing, the stale
    guard cannot fire, and the first reader after the deploy eats the full
    cold build anyway — which is exactly how SGX failed again after its
    marketCap fix. Serving the previous version's rows is stale twice over
    and marked as such, but it keeps a screener on screen.

    The prefix match is done in Python, not in SQL: a literal percent sign
    in the SQL text breaks psycopg ("only '%s'... allowed as placeholders"),
    which rules out a prefix-wildcard query. The table holds a handful of
    rows, so filtering in Python is both safe and cheap.
    """
    row = _db.query_one(
        "SELECT results_json FROM screener_cache WHERE cache_key = ?", [cache_key])
    if row and row["results_json"]:
        try:
            return json.loads(row["results_json"])
        except (TypeError, ValueError):
            pass

    prefix = _cache_key_family(cache_key)
    if not prefix:
        return None
    try:
        rows = _db.query(
            "SELECT cache_key, results_json, fetched_at FROM screener_cache "
            "ORDER BY fetched_at DESC")
    except Exception:
        return None
    floor = (datetime.now(timezone.utc)
             - timedelta(days=_STALE_VERSION_MAX_AGE_DAYS)).isoformat()
    for r in rows or []:
        key = r["cache_key"] or ""
        if key == cache_key or not key.startswith(prefix) or not r["results_json"]:
            continue
        if (r["fetched_at"] or "") < floor:
            continue
        try:
            parsed = json.loads(r["results_json"])
        except (TypeError, ValueError):
            continue
        if parsed:
            _sqlog.info("screener %s: no rows, serving previous version %s",
                        cache_key, key)
            return parsed
    return None


def _serve_stale_and_refresh(cache_key: str, build: "Callable[[], dict]",
                             label: str) -> Optional[dict]:
    """Return stale rows now and rebuild in the background, or None.

    The cold path is the one that actually reached users: after a deploy
    that bumps a cache key the table has no rows under it, no build is yet
    in flight so the lock is free, and the first reader runs the whole
    build synchronously — 93s for SGX, 300s+ for HK. Both exceed what a
    browser will wait for.

    Returning the previous version immediately and refreshing behind the
    request turns that into a fast, visibly-stale answer. Only when there is
    nothing at all to serve (a genuinely first-ever build) does a caller
    still block, which happens once per market rather than once per deploy.
    """
    stale = _get_cached_stale(cache_key)
    if not stale:
        return None

    def _refresh() -> None:
        # Blocking acquire, NOT try-acquire: the request thread is still
        # holding the lock when this thread starts and releases it a moment
        # later. A non-blocking attempt loses that race every time and the
        # refresh silently never happens.
        lock = _build_lock(cache_key)
        lock.acquire()
        try:
            # Another waiter may have rebuilt while this thread queued.
            if _get_cached(cache_key) is not None:
                return
            build()
        except Exception as exc:
            _sqlog.warning("%s background refresh failed: %s", label, exc)
        finally:
            lock.release()

    threading.Thread(target=_refresh, name=f"{label}-refresh", daemon=True).start()
    _sqlog.info("%s: cache cold — serving stale rows, refreshing in background", label)
    return {"items": stale, "total": len(stale), "cached": True, "stale": True}


def _cache_key_family(cache_key: str) -> str:
    """The version-independent prefix of a screener cache key.

    "sg_fmp_v3" -> "sg_", so any earlier sg_* payload can stand in. Returns
    "" for keys with no market prefix (the US screener hashes its params, so
    it has no stable family and is left alone).
    """
    for family in ("sg_", "hk_"):
        if cache_key.startswith(family):
            return family
    return ""


#: Structure caches (universe / sector / VGPM) are rewritten by the weekly
#: scheduler job; the 8-day TTL keeps rows live between Saturday runs so no
#: user ever hits a cold full-universe fetch. Prices ride on top via the
#: /screener/prices write-back (update_cached_prices).
_WEEKLY_TTL_HOURS = 24 * 8


def _set_cached(cache_key: str, results: list, ttl_hours: int = 24):
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    _upsert(
        "screener_cache", "cache_key",
        ["cache_key", "fetched_at", "expires_at", "results_json"],
        [cache_key, now.isoformat(), expires, json.dumps(results)],
    )


# ── Fast VGPM per-ticker cache ─────────────────────────────────────────────────

def _get_fast_vgpm_cached(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    now_iso = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(tickers))
    rows = _db.query(
        f"SELECT ticker, data_json FROM fast_vgpm_cache "
        f"WHERE ticker IN ({placeholders}) AND expires_at > ?",
        [*tickers, now_iso],
    )
    return {row["ticker"]: json.loads(row["data_json"]) for row in rows}


def _set_fast_vgpm_cached(data: dict[str, dict], ttl_hours: int = 24):
    if not data:
        return
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    now_iso = now.isoformat()
    _upsert_many(
        "fast_vgpm_cache", "ticker",
        ["ticker", "cached_at", "expires_at", "data_json"],
        [(t, now_iso, expires, json.dumps(v)) for t, v in data.items()],
    )


# ── Raw metrics cache ──────────────────────────────────────────────────────
# Superseded by app/backend/services/knowledge_graph.py's kg_ticker_metrics
# table (shared with the live pipeline). raw_metrics_cache's DDL (_ensure_tables
# above) is kept as a harmless no-op for one release in case of rollback —
# nothing writes to it anymore.


# ── FMP call ───────────────────────────────────────────────────────────────────

def _call_fmp_screener(
    sector: Optional[str] = None,
    exchange: Optional[str] = None,
    country: str = "US",
    market_cap_more_than: Optional[int] = None,
    market_cap_lower_than: Optional[int] = None,
    limit: int = 100,
) -> list[dict]:
    api_key = _get_fmp_key()
    params: dict = {
        "isActivelyTrading": "true",
        "isEtf": "false",
        "isFund": "false",
        # NOTE: FMP ignores `country` on this endpoint (verified 2026-08-27);
        # callers filter the response by exchange instead (see _us_listing).
        "country": country,
        "limit": limit,
        "sortBy": "marketCap",
        "sort": "desc",
    }
    if api_key:
        params["apikey"] = api_key
    if sector:
        # FMP /stable/company-screener uses its own sector naming (not GICS).
        # Frontend SECTORS labels match FMP names exactly:
        #   Technology, Healthcare, Consumer Cyclical, Financial Services,
        #   Communication Services, Consumer Defensive, Energy, Industrials,
        #   Basic Materials, Real Estate, Utilities
        # Pass through directly — no remapping needed.
        params["sector"] = sector
    if exchange:
        params["exchange"] = exchange
    if market_cap_more_than is not None:
        params["marketCapMoreThan"] = market_cap_more_than
    if market_cap_lower_than is not None:
        params["marketCapLowerThan"] = market_cap_lower_than

    try:
        resp = requests.get(f"{_STABLE}/company-screener", params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _resolve_to_ticker(query: str, api_key: Optional[str]) -> Optional[str]:
    base = {"apikey": api_key} if api_key else {}
    q = query.strip().upper()
    try:
        pr = requests.get(f"{_STABLE}/profile", params={"symbol": q, **base}, timeout=8)
        if pr.ok:
            data = pr.json()
            if data and isinstance(data, list) and data[0].get("symbol"):
                return data[0]["symbol"]
    except Exception:
        pass
    try:
        sr = requests.get(
            f"{_STABLE}/search",
            params={"query": query, "limit": 5, **base},
            timeout=8,
        )
        if sr.ok:
            results = sr.json()
            if isinstance(results, list):
                for r in results:
                    if r.get("symbol", "").upper() == q:
                        return r["symbol"]
                if results:
                    return results[0].get("symbol")
    except Exception:
        pass
    return None


def lookup_ticker(symbol: str, force_refresh: bool = False) -> Optional[dict]:
    """
    Fetch a single stock's profile + VGPM from FMP and return a screener-shaped item.
    Accepts either a ticker symbol OR a company name.
    HK tickers (numeric, e.g. "00700.HK" or "700") are routed through AKShare/yfinance
    instead of FMP — they do not appear in FMP's global search reliably.
    yfinance supplements (rec_score, short_ratio) are included here since this is
    a single-ticker path and latency is tolerable.
    Result cached 24h in screener_lookup_cache.
    """
    query = symbol.strip()
    _ensure_tables()
    api_key = _get_fmp_key()
    base = {"apikey": api_key} if api_key else {}

    # ── SG ticker fast-path (bypass FMP resolve + profile) ───────────────────
    try:
        from src.tools.sg.ticker import is_sg_ticker, to_canonical as _sg_canonical
        if is_sg_ticker(query):
            canonical = _sg_canonical(query)
            from src.tools.sg.vgpm_metrics import fetch_sg_vgpm_metrics
            from src.tools.sg.universe import get_sg_stock_info
            stock_info = get_sg_stock_info(query)
            metrics = fetch_sg_vgpm_metrics(query)
            vgpm = _compute_fast_vgpm_universe({canonical: metrics}).get(canonical)
            composite = None
            if vgpm:
                scores = [v["score"] for v in vgpm.values() if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
                composite = round(sum(scores) / len(scores)) if scores else None
            return {
                "symbol": canonical,
                "companyName": (stock_info or {}).get("name", canonical),
                "sector": (stock_info or {}).get("sector", "Unknown"),
                "industry": (stock_info or {}).get("industry", "Unknown"),
                "marketCap": metrics.get("market_cap_sgd"),
                "price": metrics.get("price"),
                "exchange": "SGX",
                "country": "SG",
                "vgpm": vgpm,
                "vgpm_estimated": True,
                "composite_score": composite,
            }
    except Exception:
        pass

    # ── HK ticker fast-path (bypass FMP resolve + profile) ───────────────────
    try:
        from src.tools.hk.ticker import is_hk_ticker, to_canonical, to_yfinance_code
        if is_hk_ticker(query):
            canonical = to_canonical(query)  # e.g. "00700.HK"
            ticker    = canonical

            # Check cache first
            if not force_refresh:
                now_iso = datetime.now(timezone.utc).isoformat()
                row = _db.query_one(
                    "SELECT item_json FROM screener_lookup_cache WHERE symbol = ? AND expires_at > ?",
                    [ticker, now_iso],
                )
                if row:
                    return json.loads(row["item_json"])

            # Resolve sector: TICKER_SECTOR_LOOKUP first, yfinance info fallback
            sector = "Unknown"
            industry = "Unknown"
            company_name = ""
            price = None
            market_cap = None
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                entry = TICKER_SECTOR_LOOKUP.get(canonical)
                if entry:
                    sector = entry[0]   # e.g. "Tech"
                    industry = entry[2] # e.g. "Software (Internet)"
                    company_name = entry[3]
            except Exception:
                pass

            # yfinance for live price + company name supplement
            try:
                import yfinance as yf
                yf_sym = to_yfinance_code(canonical)
                info   = yf.Ticker(yf_sym).info or {}
                if not company_name:
                    company_name = info.get("longName") or info.get("shortName") or ""
                if sector == "Unknown":
                    sector = info.get("sector") or "Unknown"
                if industry == "Unknown":
                    industry = info.get("industry") or "Unknown"
                price      = info.get("currentPrice") or info.get("regularMarketPrice")
                market_cap = info.get("marketCap")
            except Exception:
                pass

            # Fetch VGPM metrics (HK multi-source)
            from src.tools.hk.vgpm_metrics import fetch_hk_vgpm_metrics
            metrics = fetch_hk_vgpm_metrics(canonical)

            vgpm = None
            if metrics:
                fast = _get_or_compute_fast_vgpm(
                    [canonical],
                    sector_map={canonical: sector},
                    industry_map={canonical: industry},
                )
                vgpm = fast.get(canonical)

            composite = None
            if vgpm:
                scores = [v["score"] for v in vgpm.values() if isinstance(v.get("score"), (int, float))]
                composite = round(sum(scores) / len(scores)) if scores else None

            item = {
                "symbol":          ticker,
                "companyName":     company_name,
                "sector":          sector,
                "industry":        industry,
                "marketCap":       market_cap,
                "price":           price,
                "volume":          None,
                "beta":            None,
                "exchange":        "HKEX",
                "country":         "HK",
                "vgpm":            vgpm,
                "vgpm_estimated":  vgpm is not None,
                "composite_score": composite,
            }

            now     = datetime.now(timezone.utc)
            expires = (now + timedelta(hours=24)).isoformat()
            _upsert(
                "screener_lookup_cache", "symbol",
                ["symbol", "fetched_at", "expires_at", "item_json"],
                [ticker, now.isoformat(), expires, json.dumps(item)],
            )

            return item
    except Exception as _hk_err:
        _sqlog.warning("lookup_ticker HK path failed for %s: %s", query, _hk_err)
    # ─────────────────────────────────────────────────────────────────────────

    ticker = _resolve_to_ticker(query, api_key)
    if not ticker:
        return None

    if not force_refresh:
        now_iso = datetime.now(timezone.utc).isoformat()
        row = _db.query_one(
            "SELECT item_json FROM screener_lookup_cache WHERE symbol = ? AND expires_at > ?",
            [ticker, now_iso],
        )
        if row:
            item = json.loads(row["item_json"])
            # Always overlay live quote data — not cached so it stays fresh
            live = get_live_quotes([ticker])
            if ticker in live:
                _overlay_live(item, live[ticker])
            return item

    try:
        pr = requests.get(f"{_STABLE}/profile", params={"symbol": ticker, **base}, timeout=10)
        profile = (pr.json()[0] if pr.ok and pr.json() else None) or {}
        if not profile:
            return None

        sector   = profile.get("sector")   or "Unknown"
        industry = profile.get("industry") or "Unknown"

        # Pipeline VGPM first
        pipeline_vgpm = _get_vgpm_map([ticker])
        vgpm = pipeline_vgpm.get(ticker)
        vgpm_estimated = False

        if not vgpm:
            # Fast VGPM with yfinance supplements enabled
            fast = _get_or_compute_fast_vgpm(
                [ticker],
                sector_map={ticker: sector},
                industry_map={ticker: industry},
                use_yfinance=True,
            )
            vgpm = fast.get(ticker)
            vgpm_estimated = vgpm is not None

        composite = None
        if vgpm:
            scores = [v["score"] for v in vgpm.values() if isinstance(v.get("score"), (int, float))]
            composite = round(sum(scores) / len(scores)) if scores else None

        item = {
            "symbol":          ticker,
            "companyName":     profile.get("companyName", ""),
            "sector":          sector,
            "industry":        industry,
            "marketCap":       profile.get("mktCap"),
            "price":           profile.get("price"),
            "volume":          profile.get("volAvg"),
            "beta":            profile.get("beta"),
            "exchange":        profile.get("exchangeShortName", ""),
            "country":         profile.get("country", ""),
            "vgpm":            vgpm,
            "vgpm_estimated":  vgpm_estimated,
            "composite_score": composite,
        }

        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=24)).isoformat()
        _upsert(
            "screener_lookup_cache", "symbol",
            ["symbol", "fetched_at", "expires_at", "item_json"],
            [ticker, now.isoformat(), expires, json.dumps(item)],
        )

        # Overlay live quote data after caching base item — always fresh, never cached
        live = get_live_quotes([ticker])
        if ticker in live:
            _overlay_live(item, live[ticker])

        return item
    except Exception:
        return None


# ── Pipeline VGPM lookup ───────────────────────────────────────────────────────

def _get_vgpm_map(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {valuation, growth, profitability, momentum}} from latest web run.

    Dual-mode: reads whichever store the run records were written to
    (SQLite locally, Postgres production).
    """
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    if _db.is_postgres():
        # PG rejects bare non-aggregated columns under GROUP BY;
        # DISTINCT ON picks the latest row per ticker instead.
        rows = _db.query(
            f"SELECT DISTINCT ON (ticker) ticker, full_result_json "
            f"FROM web_runs "
            f"WHERE ticker IN ({placeholders}) AND full_result_json IS NOT NULL "
            f"ORDER BY ticker, run_at DESC",
            list(tickers),
        )
    else:
        rows = _db.query(
            f"SELECT ticker, full_result_json, MAX(run_at) AS latest "
            f"FROM web_runs "
            f"WHERE ticker IN ({placeholders}) AND full_result_json IS NOT NULL "
            f"GROUP BY ticker",
            tickers,
        )

    result: dict[str, dict] = {}
    for row in rows:
        try:
            data = json.loads(row["full_result_json"])
            vgpm_raw = data.get("vgpm", {}).get(row["ticker"], {})
            if vgpm_raw:
                result[row["ticker"]] = {
                    dim: {"score": v.get("score", 0), "grade": v.get("grade", "—")}
                    for dim, v in vgpm_raw.items()
                }
        except Exception:
            pass
    return result


# ── Sector-specific VGPM weight profiles ──────────────────────────────────────
#
# Each sector maps V/G/P/M to a list of (factor_key, weight) tuples.
# _avg_scores normalises weights internally, so they don't need to sum to 1.
# Factors not listed are simply excluded from that dimension for that sector.

_SECTOR_VGPM_CONFIG: dict[str, dict[str, list[tuple[str, float]]]] = {
    # fwd_pe and fwd_rev_growth use analyst-estimates (NTM consensus).
    # If unavailable (free API tier), both return None and are skipped by _avg_scores,
    # which re-normalises remaining TTM-based weights automatically — no special fallback needed.
    "Technology": {
        "V": [("ev_sales", 0.20), ("peg", 0.20), ("fcf_yield", 0.30), ("fwd_pe", 0.30)],
        "G": [("fwd_rev_growth", 0.30), ("rev_cagr_3y", 0.25), ("fwd_eps_growth", 0.25), ("earnings_surprise", 0.20)],
        "P": [("gross_margin", 0.30), ("roic", 0.30), ("fcf_conversion", 0.25), ("piotroski", 0.15)],
        "M": [("earnings_revision", 0.35), ("price_1y", 0.25), ("analyst_upgrade", 0.20), ("rec_score", 0.20)],
    },
    "Financial Services": {
        "V": [("fwd_pe", 0.25), ("pe", 0.25), ("pb", 0.35), ("div_yield", 0.15)],
        "G": [("fwd_rev_growth", 0.30), ("eps_growth", 0.25), ("net_inc_growth", 0.25), ("rev_growth", 0.20)],
        "P": [("roe", 0.50), ("roa", 0.35), ("piotroski", 0.15)],
        "M": [("price_1y", 0.35), ("earnings_revision", 0.30), ("analyst_upgrade", 0.20), ("rec_score", 0.15)],
    },
    "Utilities": {
        "V": [("ev_ebitda", 0.35), ("div_yield", 0.35), ("pb", 0.20), ("fwd_pe", 0.10)],
        "G": [("fwd_rev_growth", 0.25), ("rev_growth", 0.30), ("net_inc_growth", 0.25), ("fcf_growth", 0.20)],
        "P": [("net_margin", 0.35), ("roa", 0.35), ("piotroski", 0.30)],
        "M": [("price_1y", 0.45), ("price_6m", 0.30), ("analyst_upgrade", 0.25)],
    },
    "Energy": {
        "V": [("ev_ebitda", 0.30), ("fcf_yield", 0.35), ("pb", 0.25), ("fwd_pe", 0.10)],
        "G": [("fwd_rev_growth", 0.25), ("fcf_growth", 0.30), ("net_inc_growth", 0.25), ("rev_growth", 0.20)],
        "P": [("roic", 0.40), ("net_margin", 0.35), ("fcf_conversion", 0.25)],
        "M": [("price_1y", 0.40), ("price_6m", 0.30), ("analyst_upgrade", 0.20), ("earnings_revision", 0.10)],
    },
    "Real Estate": {
        "V": [("ev_ebitda", 0.35), ("div_yield", 0.40), ("pb", 0.25)],
        "G": [("fwd_rev_growth", 0.30), ("rev_growth", 0.35), ("net_inc_growth", 0.20), ("fcf_growth", 0.15)],
        "P": [("net_margin", 0.35), ("roa", 0.35), ("asset_turnover", 0.30)],
        "M": [("price_1y", 0.45), ("price_6m", 0.30), ("analyst_upgrade", 0.25)],
    },
    "Healthcare": {
        "V": [("ev_sales", 0.25), ("fwd_pe", 0.30), ("peg", 0.25), ("fcf_yield", 0.20)],
        "G": [("fwd_rev_growth", 0.30), ("rev_cagr_3y", 0.25), ("fwd_eps_growth", 0.25), ("earnings_surprise", 0.20)],
        "P": [("gross_margin", 0.35), ("roic", 0.30), ("net_margin", 0.20), ("piotroski", 0.15)],
        "M": [("earnings_revision", 0.35), ("price_1y", 0.25), ("analyst_upgrade", 0.25), ("rec_score", 0.15)],
    },
    "Consumer Defensive": {
        "V": [("ev_ebitda", 0.25), ("fwd_pe", 0.25), ("div_yield", 0.25), ("fcf_yield", 0.25)],
        "G": [("fwd_rev_growth", 0.30), ("rev_growth", 0.25), ("eps_growth", 0.25), ("net_inc_growth", 0.20)],
        "P": [("gross_margin", 0.30), ("net_margin", 0.25), ("roic", 0.25), ("piotroski", 0.20)],
        "M": [("price_1y", 0.40), ("earnings_revision", 0.25), ("div_yield", 0.20), ("analyst_upgrade", 0.15)],
    },
    "Consumer Cyclical": {
        "V": [("fwd_pe", 0.30), ("ev_ebitda", 0.25), ("fcf_yield", 0.25), ("peg", 0.20)],
        "G": [("fwd_rev_growth", 0.30), ("rev_growth", 0.20), ("fwd_eps_growth", 0.25), ("earnings_surprise", 0.25)],
        "P": [("gross_margin", 0.25), ("roic", 0.30), ("net_margin", 0.25), ("asset_turnover", 0.20)],
        "M": [("price_1y", 0.35), ("price_6m", 0.25), ("earnings_revision", 0.25), ("analyst_upgrade", 0.15)],
    },
    "Industrials": {
        "V": [("ev_ebitda", 0.25), ("fwd_pe", 0.30), ("fcf_yield", 0.25), ("pb", 0.20)],
        "G": [("fwd_rev_growth", 0.30), ("rev_growth", 0.20), ("eps_growth", 0.20), ("fwd_eps_growth", 0.30)],
        "P": [("roic", 0.35), ("net_margin", 0.25), ("asset_turnover", 0.25), ("piotroski", 0.15)],
        "M": [("price_1y", 0.35), ("earnings_revision", 0.30), ("analyst_upgrade", 0.20), ("price_6m", 0.15)],
    },
    "Communication Services": {
        "V": [("ev_ebitda", 0.25), ("ev_sales", 0.20), ("fcf_yield", 0.25), ("fwd_pe", 0.30)],
        "G": [("fwd_rev_growth", 0.30), ("rev_cagr_3y", 0.25), ("fwd_eps_growth", 0.25), ("earnings_surprise", 0.20)],
        "P": [("gross_margin", 0.30), ("roic", 0.25), ("net_margin", 0.25), ("fcf_conversion", 0.20)],
        "M": [("earnings_revision", 0.35), ("price_1y", 0.30), ("analyst_upgrade", 0.20), ("rec_score", 0.15)],
    },
    "Basic Materials": {
        "V": [("ev_ebitda", 0.35), ("fcf_yield", 0.30), ("pb", 0.25), ("fwd_pe", 0.10)],
        "G": [("fwd_rev_growth", 0.25), ("rev_growth", 0.25), ("fcf_growth", 0.25), ("net_inc_growth", 0.25)],
        "P": [("roic", 0.40), ("net_margin", 0.30), ("asset_turnover", 0.30)],
        "M": [("price_1y", 0.40), ("price_6m", 0.30), ("analyst_upgrade", 0.30)],
    },
}

# FMP sector name aliases → normalised key in _SECTOR_VGPM_CONFIG
# Also includes internal pipeline sector names (sector_profiles.py "Tech", "Consumer", etc.)
# so that HK tickers resolved via TICKER_SECTOR_LOOKUP get sector-specific VGPM weights.
_SECTOR_ALIASES: dict[str, str] = {
    # FMP / standard naming variations
    "Financials":                "Financial Services",
    "Finance":                   "Financial Services",
    "Consumer Staples":          "Consumer Defensive",
    "Consumer Discretionary":    "Consumer Cyclical",
    "Materials":                 "Basic Materials",
    "Information Technology":    "Technology",
    "Telecom":                   "Communication Services",
    "Telecommunications":        "Communication Services",
    # Internal pipeline sector names (from sector_profiles.py TICKER_SECTOR_LOOKUP)
    "Tech":                      "Technology",
    "Consumer":                  "Consumer Cyclical",
    "Biopharma":                 "Healthcare",
    "Telco":                     "Communication Services",
    "RealEstate":                "Real Estate",
    "Energy":                    "Energy",       # already matches, explicit for clarity
    "Industrials":               "Industrials",  # already matches, explicit for clarity
    "Crypto":                    "Technology",   # closest proxy
}

_DEFAULT_VGPM_CONFIG: dict[str, list[tuple[str, float]]] = {
    "V": [("fwd_pe", 0.25), ("pe", 0.15), ("ev_ebitda", 0.20), ("fcf_yield", 0.25), ("peg", 0.15)],
    "G": [("fwd_rev_growth", 0.25), ("rev_cagr_3y", 0.20), ("fwd_eps_growth", 0.20), ("earnings_surprise", 0.15), ("eps_growth", 0.20)],
    "P": [("roic", 0.25), ("roe", 0.15), ("net_margin", 0.20), ("gross_margin", 0.15), ("fcf_conversion", 0.15), ("piotroski", 0.10)],
    "M": [("price_1y", 0.30), ("earnings_revision", 0.30), ("analyst_upgrade", 0.20), ("rec_score", 0.20)],
}


# ── Fast VGPM: FMP metric fetching ─────────────────────────────────────────────

def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _fmp_get(url: str, params: dict, timeout: int = 10) -> list | dict | None:
    """Single FMP GET — returns parsed JSON or None on any failure.

    Routed through the shared FMP token bucket (src/tools/api.py) so this
    module's traffic counts against the same combined rate budget as the
    live pipeline's FMP calls — previously this hit FMP with zero throttling.
    Retries on 429 (rate-limit) and 402 (burst-throttle), matching
    src/tools/api.py's own _fmp_get retry policy.
    """
    from src.tools.api import acquire_fmp_token

    for attempt in range(3):
        acquire_fmp_token()
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except Exception:
            import time as _time
            _time.sleep(1)
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None

        if r.status_code == 429:
            import time as _time
            _time.sleep(20 * (attempt + 1))
            continue

        if r.status_code == 402:
            import time as _time
            if attempt < 2:
                _time.sleep(1.5 + attempt * 1.5)
                continue
            return None

        return None

    return None


def _legacy_intl_metrics(ticker: str, market: str) -> Optional[dict]:
    """Pre-FMP HK/SG metric fetchers, kept as the fallback arm.

    Returns None when the legacy source has nothing, so the caller can carry
    on down the FMP path rather than treating a miss as a hard failure.
    """
    try:
        if market == "hk":
            from src.tools.hk.vgpm_metrics import fetch_hk_vgpm_metrics
            return fetch_hk_vgpm_metrics(ticker)
        if market == "sg":
            from src.tools.sg.vgpm_metrics import fetch_sg_vgpm_metrics
            return fetch_sg_vgpm_metrics(ticker)
    except Exception as exc:
        _sqlog.warning("legacy %s metrics failed for %s: %s",
                       market.upper(), ticker, exc)
    return None


def _fetch_ticker_metrics(
    ticker: str,
    api_key: Optional[str],
    use_yfinance: bool = False,
) -> Optional[dict]:
    """
    Fetch all fast-VGPM sub-factors for one ticker.

    HK tickers (numeric, e.g. '00700.HK') are routed to the dedicated
    multi-source HK fetcher (AKShare + yfinance + Alpha Spread +
    Stock Analysis + FinanceToolkit) instead of the FMP path.

    US tickers use 8 FMP endpoints (parallelised internally) plus optional
    yfinance supplements.

    FMP endpoints used (US only)
    ----------------------------
    key-metrics-ttm   — valuation multiples, ROIC, FCF/share, dividend yield
    ratios-ttm        — margin ratios, asset turnover, P/FCF
    financial-growth  — historical growth rates (limit=5 for 3Y CAGR)
    stock-price-change — 1Y/6M/3M price momentum
    analyst-estimates  — forward EPS/revenue consensus (revision trend)
    earnings-surprises — last 4 quarterly EPS beat/miss percentages
    financial-score   — Piotroski F-score (0-9), Altman Z-score
    upgrades-downgrades — last 20 analyst actions (buy/sell/upgrade/downgrade)

    yfinance (single-ticker lookups only)
    -------------------------------------
    info.recommendationMean — analyst consensus rating 1-5
    info.shortRatio         — short interest days-to-cover
    """
    # ── HK ticker routing ─────────────────────────────────────────────────────
    # ── HK / SG routing ──────────────────────────────────────────────────
    # These used to bypass FMP entirely for the dedicated AKShare/yfinance
    # fetchers, "which has no meaningful HKEX coverage" — true before the
    # global-coverage upgrade, not any more. Running them through the SAME
    # pipeline as US is what finally makes their VGPM scores industry-
    # relative: the legacy path had no industry at all (every HK stock was
    # labelled "HKEX"), so all 118 landed in one bucket and the industry
    # ranking tier never actually differentiated anything.
    #
    # FMP wants the 4-digit HK form (0700.HK, not 00700.HK), so the request
    # symbol and the output label differ. The legacy fetchers remain behind
    # the provider kill switch and as an empty-response fallback.
    fmp_ticker = ticker
    _market = None
    try:
        from src.tools.intl_provider import detect_market, fmp_symbol, use_fmp
        _market = detect_market(ticker)
        if _market:
            if not use_fmp(_market):
                legacy = _legacy_intl_metrics(ticker, _market)
                if legacy is not None:
                    return legacy
            else:
                fmp_ticker = fmp_symbol(ticker, _market) or ticker
    except Exception as _intl_exc:
        _sqlog.warning("intl screener routing failed for %s: %s", ticker, _intl_exc)

    base = {"apikey": api_key} if api_key else {}

    # ── Parallel FMP fetch ────────────────────────────────────────────────────
    # Four of these were wrong and had been 404/400-ing for every market,
    # US included (verified against live /stable 2026-08-27):
    #   financial-score     -> financial-scores      (404)
    #   earnings-surprises  -> earnings              (404)
    #   upgrades-downgrades -> grades-historical     (404)
    #   analyst-estimates   needs period=annual      (400)
    endpoints = {
        "km":  (f"{_STABLE}/key-metrics-ttm",    {"symbol": fmp_ticker}),
        "rt":  (f"{_STABLE}/ratios-ttm",          {"symbol": fmp_ticker}),
        "fg":  (f"{_STABLE}/financial-growth",    {"symbol": fmp_ticker, "limit": 5}),
        "spc": (f"{_STABLE}/stock-price-change",  {"symbol": fmp_ticker}),
        "ae":  (f"{_STABLE}/analyst-estimates",   {"symbol": fmp_ticker,
                                                   "period": "annual", "limit": 2}),
        "es":  (f"{_STABLE}/earnings",            {"symbol": fmp_ticker, "limit": 4}),
        "fs":  (f"{_STABLE}/financial-scores",    {"symbol": fmp_ticker}),
        "ud":  (f"{_STABLE}/grades-historical",   {"symbol": fmp_ticker, "limit": 20}),
    }

    raw: dict[str, list | dict | None] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_fmp_get, url, {**base, **params}): key
            for key, (url, params) in endpoints.items()
        }
        for fut in as_completed(futures):
            raw[futures[fut]] = fut.result()

    def first(key) -> dict:
        data = raw.get(key)
        if isinstance(data, list) and data:
            return data[0] or {}
        return {}

    if _market and not (raw.get("km") or raw.get("rt")):
        legacy = _legacy_intl_metrics(ticker, _market)
        if legacy is not None:
            _sqlog.info("screener %s: FMP empty, served by legacy provider", ticker)
            return legacy

    km  = first("km")
    rt  = first("rt")
    fg  = raw.get("fg") or []      # list of annual records
    spc = first("spc")
    ae  = raw.get("ae") or []      # analyst estimate periods
    es  = raw.get("es") or []      # earnings surprise records
    fs  = first("fs")
    ud  = raw.get("ud") or []      # upgrades/downgrades

    # ── yfinance supplements (single-ticker only) ─────────────────────────────
    yf_rec_score    = None
    yf_short_ratio  = None
    if use_yfinance:
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).fast_info  # fast_info avoids heavy download
            rec_mean   = _safe_float(getattr(info, "recommendationMean", None))
            short_pct  = _safe_float(getattr(info, "shortRatio", None))
            if rec_mean is not None:
                # 1=strong buy, 5=strong sell → invert to 0-1 (higher=better)
                yf_rec_score = (5.0 - rec_mean) / 4.0
            if short_pct is not None:
                # Lower days-to-cover = less short headwind → higher score
                yf_short_ratio = max(0.0, (20.0 - min(short_pct, 20.0)) / 20.0)
        except Exception:
            pass

    # ── Valuation ─────────────────────────────────────────────────────────────
    pe        = _safe_float(km.get("peRatioTTM")     or rt.get("priceToEarningsRatioTTM"))
    pb        = _safe_float(km.get("pbRatioTTM")     or rt.get("priceToBookRatioTTM"))
    ev_ebitda = _safe_float(km.get("evToEBITDATTM"))
    ev_sales  = _safe_float(km.get("evToSalesTTM")   or rt.get("priceToSalesRatioTTM"))
    # peg and dividend yield live on ratios-ttm, not key-metrics-ttm; with no
    # fallback both were unconditionally None (verified 2026-08-27).
    peg       = _safe_float(km.get("pegRatioTTM")
                            or rt.get("priceToEarningsGrowthRatioTTM"))
    div_yield = _safe_float(km.get("dividendYieldTTM")
                            or rt.get("dividendYieldTTM"))

    # FCF yield: prefer direct field, fall back to 1/P_FCF
    fcf_yield = _safe_float(km.get("freeCashFlowYieldTTM"))
    if fcf_yield is None:
        # Field is priceToFreeCashFlowRatioTTM — no "s" after Flow.
        p_fcf = _safe_float(
            rt.get("priceToFreeCashFlowRatioTTM")
            or rt.get("priceToFreeCashFlowsRatioTTM")
            or km.get("priceToFreeCashFlowsRatioTTM")
        )
        if p_fcf and p_fcf > 0:
            fcf_yield = (1.0 / p_fcf) * 100

    # ── Growth ────────────────────────────────────────────────────────────────
    rev_growth    = _safe_float(fg[0].get("revenueGrowth"))      if fg else None
    eps_growth    = _safe_float(fg[0].get("epsgrowth"))          if fg else None
    fcf_growth    = _safe_float(fg[0].get("freeCashFlowGrowth")) if fg else None

    # Forward P/E — two-tier:
    #   Tier 1 (paid): analyst-estimates NTM EPS consensus → pe * (ttm_eps / fwd_eps)
    #   Tier 2 (free): extrapolate TTM P/E by most-recent EPS growth → pe / (1 + eps_growth)
    #   Falls back to None if neither is computable; _avg_scores skips None gracefully.
    fwd_pe = None
    if pe:
        # Tier 1: analyst-estimates (requires paid FMP plan)
        if ae:
            _fwd_eps = _safe_float(ae[0].get("estimatedEpsAvg"))
            _curr_eps = _safe_float(km.get("netIncomePerShareTTM"))
            if _fwd_eps and _curr_eps and _curr_eps != 0:
                fwd_pe = pe * (_curr_eps / _fwd_eps)
        # Tier 2: free fallback — pe / (1 + historical eps growth)
        if fwd_pe is None and eps_growth is not None and eps_growth > -1.0:
            fwd_pe = pe / (1.0 + eps_growth)
    net_inc_growth= _safe_float(fg[0].get("netIncomeGrowth"))    if fg else None

    # 3-year revenue CAGR (geometric mean of up to 3 annual growth rates)
    rev_cagr_3y = None
    if len(fg) >= 3:
        rates = [_safe_float(fg[i].get("revenueGrowth")) for i in range(3)]
        rates = [r for r in rates if r is not None and r > -1.0]
        if len(rates) >= 2:
            product = 1.0
            for r in rates:
                product *= (1.0 + r)
            rev_cagr_3y = product ** (1.0 / len(rates)) - 1.0

    # Earnings surprise trend (average beat % over last 4 quarters)
    earnings_surprise = None
    if es:
        surp = []
        for e in es[:4]:
            actual    = _safe_float(e.get("actualEarningResult"))
            estimated = _safe_float(e.get("estimatedEarning"))
            if actual is not None and estimated is not None and estimated != 0:
                surp.append((actual - estimated) / abs(estimated))
        if surp:
            earnings_surprise = sum(surp) / len(surp)

    # Forward EPS growth (FY+1 consensus vs FY0 consensus)
    fwd_eps_growth = None
    if len(ae) >= 2:
        fwd_eps  = _safe_float(ae[0].get("estimatedEpsAvg"))
        curr_eps_est = _safe_float(ae[1].get("estimatedEpsAvg"))
        if fwd_eps and curr_eps_est and curr_eps_est > 0:
            fwd_eps_growth = (fwd_eps - curr_eps_est) / abs(curr_eps_est)

    # Forward revenue growth (FY+1 consensus vs FY0 consensus) — more time-sensitive than TTM rev_growth.
    # Falls back to None when analyst-estimates are unavailable (free tier); _avg_scores skips None gracefully.
    fwd_rev_growth = None
    if len(ae) >= 2:
        fwd_rev  = _safe_float(ae[0].get("estimatedRevenueAvg"))
        curr_rev_est = _safe_float(ae[1].get("estimatedRevenueAvg"))
        if fwd_rev and curr_rev_est and curr_rev_est > 0:
            fwd_rev_growth = (fwd_rev - curr_rev_est) / abs(curr_rev_est)

    # ── Profitability ─────────────────────────────────────────────────────────
    roe          = _safe_float(km.get("returnOnEquityTTM"))
    roa          = _safe_float(km.get("returnOnAssetsTTM"))
    roic         = _safe_float(km.get("roicTTM") or km.get("returnOnInvestedCapitalTTM"))
    net_margin   = _safe_float(rt.get("netProfitMarginTTM"))
    gross_margin = _safe_float(rt.get("grossProfitMarginTTM"))
    asset_turnover = _safe_float(rt.get("assetTurnoverTTM"))

    # Cash conversion: FCF per share / EPS (quality of reported earnings)
    fcf_conversion = None
    fcf_ps = _safe_float(km.get("freeCashFlowPerShareTTM"))
    eps_ps = _safe_float(km.get("netIncomePerShareTTM"))
    if fcf_ps is not None and eps_ps is not None and eps_ps != 0:
        fcf_conversion = fcf_ps / abs(eps_ps)

    # Piotroski F-score (0-9) normalised to 0-1
    piotroski = None
    raw_p = _safe_float(fs.get("piotroskiScore"))
    if raw_p is not None:
        piotroski = raw_p / 9.0

    # ── Momentum ──────────────────────────────────────────────────────────────
    price_1y = _safe_float(spc.get("1Y"))
    price_6m = _safe_float(spc.get("6M"))
    price_3m = _safe_float(spc.get("3M"))

    # Earnings revision = fwd EPS growth (proxy: analysts raising estimates = positive)
    earnings_revision = fwd_eps_growth

    # Analyst upgrade trend: fraction of last 20 actions that are positive
    analyst_upgrade = None
    if ud:
        actions = [str(d.get("action", "")).lower() for d in ud[:20]]
        pos = sum(1 for a in actions if any(k in a for k in ("upgrade", "initiated", "buy", "outperform", "overweight", "positive")))
        neg = sum(1 for a in actions if any(k in a for k in ("downgrade", "sell", "underperform", "underweight", "negative")))
        total = pos + neg
        if total > 0:
            analyst_upgrade = pos / total

    # Last reported earnings date — drives earnings-aware cache invalidation
    # for the knowledge graph's annual line-items (see knowledge_graph.py).
    last_earnings_date = es[0].get("date") if es else None

    return {
        "ticker": ticker,
        "last_earnings_date": last_earnings_date,
        # Valuation
        "pe": pe, "pb": pb, "ev_ebitda": ev_ebitda, "ev_sales": ev_sales,
        "peg": peg, "fcf_yield": fcf_yield, "div_yield": div_yield, "fwd_pe": fwd_pe,
        # Growth
        "rev_growth": rev_growth, "rev_cagr_3y": rev_cagr_3y,
        "eps_growth": eps_growth, "fcf_growth": fcf_growth,
        "net_inc_growth": net_inc_growth,
        "earnings_surprise": earnings_surprise, "fwd_eps_growth": fwd_eps_growth,
        "fwd_rev_growth": fwd_rev_growth,
        # Profitability
        "roe": roe, "roa": roa, "roic": roic,
        "net_margin": net_margin, "gross_margin": gross_margin,
        "fcf_conversion": fcf_conversion, "piotroski": piotroski,
        "asset_turnover": asset_turnover,
        # Momentum
        "price_1y": price_1y, "price_6m": price_6m, "price_3m": price_3m,
        "earnings_revision": earnings_revision,
        "analyst_upgrade": analyst_upgrade,
        "rec_score": yf_rec_score,
        "short_ratio": yf_short_ratio,
    }


def _score_to_grade(score: int) -> str:
    if score >= 93: return "A+"
    if score >= 85: return "A"
    if score >= 77: return "A-"
    if score >= 70: return "B+"
    if score >= 62: return "B"
    if score >= 54: return "B-"
    if score >= 46: return "C+"
    if score >= 38: return "C"
    if score >= 30: return "C-"
    if score >= 22: return "D+"
    if score >= 14: return "D"
    return "D-"


def _percentile_ranks(
    ticker_values: dict[str, Optional[float]],
    lower_is_better: bool = False,
    cap_ratio: float = 50.0,
) -> dict[str, int]:
    pairs = [
        (t, v) for t, v in ticker_values.items()
        if v is not None and v == v
    ]
    if lower_is_better:
        pairs = [(t, min(v, cap_ratio)) if v > 0 else (t, cap_ratio + 1) for t, v in pairs]

    if not pairs:
        return {}

    sorted_pairs = sorted(pairs, key=lambda x: x[1])
    n = len(sorted_pairs)
    result: dict[str, int] = {}
    for rank, (t, _) in enumerate(sorted_pairs):
        pct = rank / (n - 1) if n > 1 else 0.5
        score = (1.0 - pct) * 100 if lower_is_better else pct * 100
        result[t] = max(1, min(100, round(score)))
    return result


def _avg_scores(
    ticker: str,
    rank_maps: list[dict],
    weights: list[float],
) -> Optional[int]:
    vals, ws = [], []
    for rm, w in zip(rank_maps, weights):
        s = rm.get(ticker)
        if s is not None:
            vals.append(s)
            ws.append(w)
    if not vals:
        return None
    return round(sum(v * w for v, w in zip(vals, ws)) / sum(ws))


def _ranks_for_group(
    metrics: list[dict],
    ticker_subset: Optional[set[str]] = None,
) -> dict[str, dict]:
    """
    Compute percentile rank maps for every sub-factor across the given universe
    (optionally restricted to ticker_subset for sector/industry-relative ranking).
    """
    universe = [d for d in metrics if d and (ticker_subset is None or d["ticker"] in ticker_subset)]

    def col(key):
        return {d["ticker"]: d.get(key) for d in universe}

    return {
        # ── Valuation (lower multiple = better, except yield fields) ──
        "pe":        _percentile_ranks(col("pe"),        lower_is_better=True,  cap_ratio=60),
        "pb":        _percentile_ranks(col("pb"),        lower_is_better=True,  cap_ratio=20),
        "ev_ebitda": _percentile_ranks(col("ev_ebitda"), lower_is_better=True,  cap_ratio=40),
        "ev_sales":  _percentile_ranks(col("ev_sales"),  lower_is_better=True,  cap_ratio=20),
        "peg":       _percentile_ranks(col("peg"),       lower_is_better=True,  cap_ratio=5),
        "fwd_pe":    _percentile_ranks(col("fwd_pe"),    lower_is_better=True,  cap_ratio=50),
        "fcf_yield": _percentile_ranks(col("fcf_yield"), lower_is_better=False),  # higher = cheaper
        "div_yield": _percentile_ranks(col("div_yield"), lower_is_better=False),
        # ── Growth (higher = better) ──
        "rev_growth":      _percentile_ranks(col("rev_growth"),      lower_is_better=False),
        "rev_cagr_3y":     _percentile_ranks(col("rev_cagr_3y"),     lower_is_better=False),
        "eps_growth":      _percentile_ranks(col("eps_growth"),      lower_is_better=False),
        "fcf_growth":      _percentile_ranks(col("fcf_growth"),      lower_is_better=False),
        "net_inc_growth":  _percentile_ranks(col("net_inc_growth"),  lower_is_better=False),
        "earnings_surprise": _percentile_ranks(col("earnings_surprise"), lower_is_better=False),
        "fwd_eps_growth":  _percentile_ranks(col("fwd_eps_growth"),  lower_is_better=False),
        "fwd_rev_growth":  _percentile_ranks(col("fwd_rev_growth"),  lower_is_better=False),
        # ── Profitability (higher = better) ──
        "roe":           _percentile_ranks(col("roe"),           lower_is_better=False),
        "roa":           _percentile_ranks(col("roa"),           lower_is_better=False),
        "roic":          _percentile_ranks(col("roic"),          lower_is_better=False),
        "net_margin":    _percentile_ranks(col("net_margin"),    lower_is_better=False),
        "gross_margin":  _percentile_ranks(col("gross_margin"),  lower_is_better=False),
        "fcf_conversion":_percentile_ranks(col("fcf_conversion"),lower_is_better=False),
        "piotroski":     _percentile_ranks(col("piotroski"),     lower_is_better=False),
        "asset_turnover":_percentile_ranks(col("asset_turnover"),lower_is_better=False),
        # ── Momentum (higher = better) ──
        "price_1y":          _percentile_ranks(col("price_1y"),          lower_is_better=False),
        "price_6m":          _percentile_ranks(col("price_6m"),          lower_is_better=False),
        "price_3m":          _percentile_ranks(col("price_3m"),          lower_is_better=False),
        "earnings_revision": _percentile_ranks(col("earnings_revision"), lower_is_better=False),
        "analyst_upgrade":   _percentile_ranks(col("analyst_upgrade"),   lower_is_better=False),
        "rec_score":         _percentile_ranks(col("rec_score"),         lower_is_better=False),
        "short_ratio":       _percentile_ranks(col("short_ratio"),       lower_is_better=False),
    }


def _vgpm_from_ranks(ticker: str, r: dict, sector: str = "Unknown") -> dict:
    """Build {valuation, growth, profitability, momentum} using sector-specific weights."""
    sector_norm = _SECTOR_ALIASES.get(sector, sector)
    config = _SECTOR_VGPM_CONFIG.get(sector_norm, _DEFAULT_VGPM_CONFIG)

    def score(dim: str) -> Optional[int]:
        factors = config[dim]
        return _avg_scores(ticker, [r.get(f, {}) for f, _ in factors], [w for _, w in factors])

    vgpm = {}
    v = score("V")
    g = score("G")
    p = score("P")
    m = score("M")
    if v is not None: vgpm["valuation"]     = {"score": v, "grade": _score_to_grade(v)}
    if g is not None: vgpm["growth"]        = {"score": g, "grade": _score_to_grade(g)}
    if p is not None: vgpm["profitability"] = {"score": p, "grade": _score_to_grade(p)}
    if m is not None: vgpm["momentum"]      = {"score": m, "grade": _score_to_grade(m)}
    return vgpm


def _compute_fast_vgpm_universe(raw_metrics: list[dict]) -> dict[str, dict]:
    """
    Compute VGPM using 3-tier peer-relative percentile ranking:
      1. Industry (sub-sector) peers  — min 5 tickers
      2. Sector peers                 — min 8 tickers
      3. Full universe                — fallback

    raw_metrics items must have 'sector' and 'industry' keys (injected by caller).
    """
    from collections import defaultdict

    MIN_INDUSTRY_SIZE = 5
    MIN_SECTOR_SIZE   = 8

    industry_to_tickers: dict[str, list[str]] = defaultdict(list)
    sector_to_tickers:   dict[str, list[str]] = defaultdict(list)
    ticker_to_sector:    dict[str, str] = {}
    ticker_to_industry:  dict[str, str] = {}

    for d in raw_metrics:
        if not d:
            continue
        t        = d["ticker"]
        sector   = d.get("sector")   or "Unknown"
        industry = d.get("industry") or "Unknown"
        industry_to_tickers[industry].append(t)
        sector_to_tickers[sector].append(t)
        ticker_to_sector[t]   = sector
        ticker_to_industry[t] = industry

    # Pre-compute rank maps at each tier (only for groups large enough)
    full_ranks: dict = _ranks_for_group(raw_metrics)

    sector_ranks:   dict[str, dict] = {}
    industry_ranks: dict[str, dict] = {}

    for sector, tickers in sector_to_tickers.items():
        if len(tickers) >= MIN_SECTOR_SIZE:
            sector_ranks[sector] = _ranks_for_group(raw_metrics, ticker_subset=set(tickers))

    for industry, tickers in industry_to_tickers.items():
        if len(tickers) >= MIN_INDUSTRY_SIZE:
            industry_ranks[industry] = _ranks_for_group(raw_metrics, ticker_subset=set(tickers))

    result: dict[str, dict] = {}
    for d in raw_metrics:
        if not d:
            continue
        t        = d["ticker"]
        sector   = ticker_to_sector.get(t, "Unknown")
        industry = ticker_to_industry.get(t, "Unknown")

        # Most granular group with enough peers wins
        if industry in industry_ranks:
            ranks = industry_ranks[industry]
        elif sector in sector_ranks:
            ranks = sector_ranks[sector]
        else:
            ranks = full_ranks

        vgpm = _vgpm_from_ranks(t, ranks, sector)
        if vgpm:
            result[t] = vgpm

    return result


def _get_or_compute_fast_vgpm(
    tickers: list[str],
    sector_map: Optional[dict[str, str]] = None,
    industry_map: Optional[dict[str, str]] = None,
    use_yfinance: bool = False,
) -> dict[str, dict]:
    """
    Return fast VGPM for the given tickers, using cache where available.
    Fetches missing tickers from FMP (in parallel) and caches raw metrics + VGPM.
    """
    if not tickers:
        return {}

    _ensure_tables()
    cached_vgpm = _get_fast_vgpm_cached(tickers)
    missing = [t for t in tickers if t not in cached_vgpm]

    if missing:
        # Check the shared knowledge-graph cache first to avoid re-fetching.
        # (Supersedes the old per-screener raw_metrics_cache table — see
        # app/backend/services/knowledge_graph.py.)
        from app.backend.services import knowledge_graph as _kg
        cached_raw = _kg.get_ttm_metrics_cached(missing)
        to_fetch   = [t for t in missing if t not in cached_raw]

        api_key = _get_fmp_key()
        good_fetched: dict[str, dict] = {}

        if to_fetch:
            # Each ticker makes 8 internal FMP calls with 4 workers. Outer
            # concurrency is capped to bound in-flight requests; the actual
            # rate ceiling is enforced by the shared token bucket in
            # src/tools/api.py (acquire_fmp_token, ~700/min combined with
            # the live pipeline's traffic), not by this worker count.
            workers = min(5, len(to_fetch))
            newly_fetched: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_fetch_ticker_metrics, t, api_key, use_yfinance): t
                    for t in to_fetch
                }
                for fut in as_completed(futures):
                    m = fut.result()
                    if m:
                        newly_fetched[m["ticker"]] = m

            # Only cache tickers that returned at least one real metric value.
            # Rate-limited responses produce all-None dicts — caching those would
            # prevent retries and poison the VGPM scoring universe.
            _metric_keys = {"pe", "pb", "roe", "rev_growth", "price_1y"}
            good_fetched = {
                t: m for t, m in newly_fetched.items()
                if any(m.get(k) is not None for k in _metric_keys)
            }
            _kg.set_ttm_metrics(good_fetched, sector_map=sector_map, industry_map=industry_map)

        all_raw = {**cached_raw, **good_fetched}

        # Inject sector and industry metadata
        raw_list: list[dict] = []
        for t, m in all_raw.items():
            enriched = dict(m)
            enriched["sector"]   = (sector_map   or {}).get(t, "Unknown")
            enriched["industry"] = (industry_map or {}).get(t, "Unknown")
            raw_list.append(enriched)

        if raw_list:
            computed = _compute_fast_vgpm_universe(raw_list)
            # Only cache tickers that produced actual VGPM scores.
            scored = {t: v for t, v in computed.items() if v}
            _set_fast_vgpm_cached(scored)
            cached_vgpm.update(scored)

    return cached_vgpm


# ── Cache invalidation ─────────────────────────────────────────────────────────

def invalidate_for_ticker(ticker: str):
    """
    Called after a pipeline analysis completes for *ticker*.
    Clears that ticker's lookup and fast-VGPM rows, and EXPIRES the screener
    universe caches so the next request rebuilds with the new pipeline VGPM.

    Expire, not delete. This used to run `DELETE FROM screener_cache`, which
    dropped every market's universe — analysing one US ticker wiped the HK
    and SGX screeners too. The next visitor then paid a full cold build
    (~90s for SGX, 300s+ for HK, past what a browser waits for), and because
    the delete also removed the previous cache-key versions there was
    nothing for the stale fallback to serve. That is what made the SGX
    screener fail intermittently and unpredictably: it had nothing to do
    with SGX, only with someone having run an analysis.

    Expiring keeps the rows readable by _get_cached_stale, so the next
    request returns the old universe immediately and refreshes behind it.
    The freshly-analysed ticker's VGPM lands a few seconds later instead of
    instantly — a fair trade for not taking the screener down.
    """
    stale_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    try:
        _db.execute("DELETE FROM screener_lookup_cache WHERE symbol = ?", [ticker])
        _db.execute("DELETE FROM fast_vgpm_cache WHERE ticker = ?", [ticker])
        _db.execute("UPDATE screener_cache SET expires_at = ?", [stale_at])
    except Exception:
        pass

    try:
        from app.backend.services import knowledge_graph as _kg
        _kg.delete_ticker(ticker)
    except Exception:
        pass


# ── Master universe ────────────────────────────────────────────────────────────

def _get_master_universe() -> Optional[list[dict]]:
    """Return all rows from master_universe if not expired, else None."""
    try:
        row = _db.query_one(
            "SELECT data_json, expires_at FROM master_universe LIMIT 1"
        )
        if not row:
            return None
        if datetime.now(timezone.utc).isoformat() > row["expires_at"]:
            return None
        all_rows = _db.query("SELECT data_json FROM master_universe")
        return [json.loads(r["data_json"]) for r in all_rows]
    except Exception:
        return None


def _set_master_universe(stocks: list[dict], ttl_hours: int = 24):
    """Write all stocks to master_universe, replacing any existing data."""
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    now_iso = now.isoformat()
    _db.execute("DELETE FROM master_universe")
    _db.executemany(
        "INSERT INTO master_universe (symbol, data_json, cached_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        [[s.get("symbol", ""), json.dumps(s), now_iso, expires]
         for s in stocks if s.get("symbol")],
    )


def _get_master_universe_cached_at() -> Optional[str]:
    """cached_at of the current master_universe rows (a backfill stamps every
    row with the same timestamp), or None when the table is empty/absent."""
    try:
        row = _db.query_one("SELECT cached_at FROM master_universe LIMIT 1")
        return row["cached_at"] if row else None
    except Exception:
        return None


def _build_screener_items(
    fmp_stocks: list[dict],
    pipeline_vgpm: dict,
    fast_vgpm: dict,
    live_quotes: dict | None = None,
) -> list[dict]:
    """Assemble screener item dicts from FMP data + VGPM scores.

    Shared by get_screener_stocks() and backfill_master_universe() to avoid
    duplicating the item-building logic.
    """
    lq = live_quotes or {}
    items = []
    for s in fmp_stocks:
        ticker = s.get("symbol", "")
        if not ticker:
            continue
        is_pipeline = ticker in pipeline_vgpm
        vgpm = pipeline_vgpm.get(ticker) or fast_vgpm.get(ticker)
        vgpm_estimated = (not is_pipeline) and (ticker in fast_vgpm)

        composite = None
        if vgpm:
            scores = [v["score"] for v in vgpm.values() if isinstance(v.get("score"), (int, float))]
            composite = round(sum(scores) / len(scores)) if scores else None

        q = lq.get(ticker, {})
        items.append({
            "symbol":          ticker,
            "companyName":     s.get("companyName", ""),
            "sector":          s.get("sector", ""),
            "industry":        s.get("industry", ""),
            "marketCap":       q.get("marketCap") or s.get("marketCap"),
            "price":           q.get("price")     or s.get("price"),
            "volume":          q.get("volume")    or s.get("volume"),
            "beta":            q.get("beta")      or s.get("beta"),
            "change_pct":      q.get("change_pct"),
            "exchange":        s.get("exchangeShortName") or s.get("exchange", ""),
            "country":         s.get("country", ""),
            "vgpm":            vgpm,
            "vgpm_estimated":  vgpm_estimated,
            "composite_score": composite,
        })
    items.sort(key=lambda x: (
        x["vgpm_estimated"] is True,
        x["composite_score"] is None,
        -(x["composite_score"] or 0),
    ))
    return items


# Market-cap ranges matching the frontend — used for pre-computing cache subsets.
_FRONTEND_CAP_RANGES: list[dict] = [
    {"label": "All",         "min": 2_000_000_000, "max": None},
    {"label": "$2B-$12B",    "min": 2_000_000_000, "max": 12_000_000_000},
    {"label": "$12B-$50B",   "min": 12_000_000_000, "max": 50_000_000_000},
    {"label": "$50B-$100B",  "min": 50_000_000_000, "max": 100_000_000_000},
    {"label": "$100B-$500B", "min": 100_000_000_000, "max": 500_000_000_000},
    {"label": "$500B-$1T",   "min": 500_000_000_000, "max": 1_000_000_000_000},
    {"label": ">$1T",        "min": 1_000_000_000_000, "max": None},
]


def backfill_master_universe(
    batch_size: int = 50,
    passes: int = 5,
    delay: int = 30,
    on_progress: Optional[object] = None,
) -> dict:
    """Fetch all US stocks with market cap ≥ $2B, score them with VGPM, and
    pre-compute cache entries for every frontend cap-range filter.

    Returns {total, scored, passes: [{pass, scored, missing}], ranges: [...]}.
    """
    import time

    _ensure_tables()

    # ── Step 1: Fetch full universe from FMP ──────────────────────────────────
    _sqlog.info("backfill: fetching full universe from FMP...")
    fmp_stocks = _call_fmp_screener(
        market_cap_more_than=2_000_000_000,
        limit=5000,
    )
    # If FMP caps results (e.g. 1000), supplement with per-sector calls
    if len(fmp_stocks) < 500:
        _sqlog.info("backfill: FMP returned only %d, supplementing with per-sector fetch", len(fmp_stocks))
        sector_stocks = _fetch_all_sectors_parallel(
            exchange=None, country="US",
            market_cap_more_than=2_000_000_000,
            market_cap_lower_than=None,
        )
        # Merge, dedup by symbol
        seen = {s.get("symbol") for s in fmp_stocks}
        for s in sector_stocks:
            sym = s.get("symbol", "")
            if sym and sym not in seen:
                fmp_stocks.append(s)
                seen.add(sym)
        fmp_stocks.sort(key=lambda s: s.get("marketCap") or 0, reverse=True)

    # FMP ignores the country filter — the raw fetch includes foreign
    # listings (verified 2026-08-27: ~1/3 were LSE/XETRA/TSX/…). Drop them
    # before storing and scoring.
    fmp_stocks = [s for s in fmp_stocks if _us_listing(s)]

    _set_master_universe(fmp_stocks)
    tickers = [s.get("symbol", "") for s in fmp_stocks if s.get("symbol")]
    _sqlog.info("backfill: %d tickers in master universe", len(tickers))

    # ── Step 2: Score VGPM in rate-limit-safe batches ─────────────────────────
    sector_map   = {s.get("symbol", ""): s.get("sector", "Unknown") for s in fmp_stocks}
    industry_map = {s.get("symbol", ""): s.get("industry", "Unknown") for s in fmp_stocks}

    pass_log = []
    for p in range(passes):
        # Check pipeline VGPM first
        pipeline_vgpm = _get_vgpm_map(tickers)
        # Only score tickers that still need fast VGPM
        need_scoring = [
            t for t in tickers
            if t not in pipeline_vgpm
            and not _get_fast_vgpm_cached([t]).get(t)
        ]
        _sqlog.info("backfill pass %d: %d tickers need scoring", p + 1, len(need_scoring))

        if not need_scoring:
            pass_log.append({"pass": p + 1, "scored": len(tickers), "missing": 0})
            break

        # Process in batches with delay between each
        for i in range(0, len(need_scoring), batch_size):
            batch = need_scoring[i:i + batch_size]
            _get_or_compute_fast_vgpm(
                batch,
                sector_map=sector_map,
                industry_map=industry_map,
                use_yfinance=False,
            )
            if i + batch_size < len(need_scoring):
                time.sleep(delay)

        # Count scored after this pass
        all_vgpm = _get_fast_vgpm_cached(tickers)
        scored = sum(1 for t in tickers if t in pipeline_vgpm or (t in all_vgpm and all_vgpm[t]))
        pass_log.append({"pass": p + 1, "scored": scored, "missing": len(tickers) - scored})
        _sqlog.info("backfill pass %d complete: %d/%d scored", p + 1, scored, len(tickers))

        if scored >= len(tickers):
            break
        if p < passes - 1:
            time.sleep(delay)

    # ── Step 3: Pre-compute cache entries for all cap ranges ──────────────────
    pipeline_vgpm = _get_vgpm_map(tickers)
    all_fast_vgpm = _get_fast_vgpm_cached(tickers)
    merged_vgpm = {**all_fast_vgpm}
    merged_vgpm.update(pipeline_vgpm)  # pipeline overrides fast

    range_results = []
    for cap in _FRONTEND_CAP_RANGES:
        # Filter stocks by cap range
        subset = []
        for s in fmp_stocks:
            mc = s.get("marketCap") or 0
            if mc < cap["min"]:
                continue
            if cap["max"] is not None and mc >= cap["max"]:
                continue
            subset.append(s)

        items = _build_screener_items(subset, pipeline_vgpm, all_fast_vgpm)

        # Build the cache key matching what get_screener_stocks() would produce
        cache_params = dict(
            sector=None, exchange=None, country="US",
            market_cap_more_than=cap["min"],
            market_cap_lower_than=cap["max"],
            limit=_PER_SECTOR_LIMIT,
            cache_v=_CACHE_V,
        )
        ck = _make_cache_key(cache_params)
        if items:
            _set_cached(ck, items)

        scored_count = sum(1 for i in items if i.get("vgpm"))
        range_results.append({
            "range": cap["label"],
            "total": len(items),
            "scored": scored_count,
        })
        _sqlog.info("backfill cached %s: %d items (%d scored)", cap["label"], len(items), scored_count)

    final_scored = pass_log[-1]["scored"] if pass_log else 0
    return {
        "total": len(tickers),
        "scored": final_scored,
        "passes": pass_log,
        "ranges": range_results,
    }


# ── Per-sector parallel fetch ──────────────────────────────────────────────────

def _fetch_all_sectors_parallel(
    exchange: Optional[str],
    country: str,
    market_cap_more_than: Optional[int],
    market_cap_lower_than: Optional[int],
) -> list[dict]:
    """Fetch _PER_SECTOR_LIMIT stocks from each GICS sector in parallel.

    Returns a de-duped list sorted by market cap descending.  Running 11
    FMP calls in parallel keeps wall-clock time ≈ 1-2 s (one round-trip).
    """
    cap_floor = market_cap_more_than if market_cap_more_than is not None else 2_000_000_000

    def _fetch_one(sec: str) -> list[dict]:
        return _call_fmp_screener(
            sector=sec,
            exchange=exchange,
            country=country,
            market_cap_more_than=cap_floor,
            market_cap_lower_than=market_cap_lower_than,
            limit=_PER_SECTOR_LIMIT,
        )

    merged: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=11) as pool:
        futs = {pool.submit(_fetch_one, sec): sec for sec in _SCREENER_SECTORS}
        for fut in as_completed(futs):
            for stock in fut.result():
                sym = stock.get("symbol", "")
                if sym and sym not in merged:
                    merged[sym] = stock

    # Sort by market cap descending (consistent with single-call behaviour)
    return sorted(merged.values(), key=lambda s: s.get("marketCap") or 0, reverse=True)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_screener_stocks(
    sector: Optional[str] = None,
    exchange: Optional[str] = None,
    country: str = "US",
    market_cap_more_than: Optional[int] = None,
    market_cap_lower_than: Optional[int] = None,
    limit: int = 100,
    force_refresh: bool = False,
) -> dict:
    _ensure_tables()
    # When sector is None we use per-sector parallel fetch (ignoring limit),
    # so normalise the cache key so different limit values don't create duplicates.
    effective_limit = _PER_SECTOR_LIMIT if sector is None else limit
    filter_params = dict(
        sector=sector, exchange=exchange, country=country,
        market_cap_more_than=market_cap_more_than,
        market_cap_lower_than=market_cap_lower_than,
        limit=effective_limit,
        cache_v=_CACHE_V,
    )
    cache_key = _make_cache_key(filter_params)

    if not force_refresh:
        cached = _get_cached(cache_key)
        if cached is not None:
            # Overlay any VGPM scores that became available since the cache was written
            # (e.g. from a background backfill pass).  Only reads from cache — never
            # triggers new FMP fetches, so this is always fast.
            missing_vgpm = [i for i in cached if not i.get("vgpm")]
            if missing_vgpm:
                backfill_tickers = [i["symbol"] for i in missing_vgpm]
                fresh = _get_fast_vgpm_cached(backfill_tickers)
                pipeline = _get_vgpm_map(backfill_tickers)
                merged = {**fresh, **pipeline}
                updated = False
                for item in cached:
                    sym = item["symbol"]
                    vgpm = merged.get(sym)
                    if not item.get("vgpm") and vgpm:
                        item["vgpm"] = vgpm
                        item["vgpm_estimated"] = sym not in pipeline
                        scores = [v["score"] for v in vgpm.values()
                                  if isinstance(v.get("score"), (int, float))]
                        item["composite_score"] = round(sum(scores) / len(scores)) if scores else None
                        updated = True
                if updated:
                    _set_cached(cache_key, cached)
            return {"items": cached, "total": len(cached), "cached": True}

    # ── Try master universe first (populated by backfill) ────────────────────
    # If the master universe is fresh, filter it in-memory instead of calling
    # FMP again.  This gives instant responses for any filter combination.
    master = _get_master_universe()
    if master is not None:
        # Filter master universe by the requested params
        filtered = master
        cap_floor = market_cap_more_than or 2_000_000_000
        if cap_floor:
            filtered = [s for s in filtered if (s.get("marketCap") or 0) >= cap_floor]
        if market_cap_lower_than is not None:
            filtered = [s for s in filtered if (s.get("marketCap") or 0) < market_cap_lower_than]
        if sector:
            filtered = [s for s in filtered if s.get("sector") == sector]
        if exchange:
            filtered = [s for s in filtered if
                        (s.get("exchangeShortName") or s.get("exchange", "")) == exchange]
        if country == "US" and not exchange:
            # FMP ignores the country param — master_universe carries foreign
            # listings (LSE/XETRA/TSX/…) that don't belong in the US tab.
            filtered = [s for s in filtered if _us_listing(s)]

        tickers = [s.get("symbol", "") for s in filtered if s.get("symbol")]
        pipeline_vgpm = _get_vgpm_map(tickers)
        fast_vgpm = _get_fast_vgpm_cached(tickers)
        items = _build_screener_items(filtered, pipeline_vgpm, fast_vgpm)
        if items:
            _set_cached(cache_key, items)
        return {"items": items, "total": len(items), "cached": False}

    # ── Fallback: no master universe — fetch from FMP directly ────────────────
    if sector is None:
        fmp_stocks = _fetch_all_sectors_parallel(
            exchange=exchange,
            country=country,
            market_cap_more_than=market_cap_more_than,
            market_cap_lower_than=market_cap_lower_than,
        )
    else:
        fetch_params = {k: v for k, v in filter_params.items() if k != "cache_v"}
        fmp_stocks = _call_fmp_screener(**fetch_params)
    if country == "US" and not exchange:
        # FMP ignores the country param — keep US-exchange listings only.
        fmp_stocks = [s for s in fmp_stocks if _us_listing(s)]
    tickers    = [s.get("symbol", "") for s in fmp_stocks if s.get("symbol")]

    live_quotes = {} if sector is None else get_live_quotes(tickers)

    pipeline_vgpm = _get_vgpm_map(tickers)
    sector_map   = {s.get("symbol", ""): s.get("sector")   or "Unknown" for s in fmp_stocks}
    industry_map = {s.get("symbol", ""): s.get("industry") or "Unknown" for s in fmp_stocks}
    tickers_needing_fast = [
        t for t in tickers
        if t not in pipeline_vgpm and (
            {s.get("symbol"): s.get("marketCap") or 0 for s in fmp_stocks}.get(t, 0) >= 2_000_000_000
        )
    ]
    fast_vgpm = _get_or_compute_fast_vgpm(
        tickers_needing_fast,
        sector_map=sector_map,
        industry_map=industry_map,
        use_yfinance=False,
    )

    items = _build_screener_items(fmp_stocks, pipeline_vgpm, fast_vgpm, live_quotes)
    if items:
        _set_cached(cache_key, items)
    return {"items": items, "total": len(items), "cached": False}


def get_company_names(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {name, sector, industry}} for a list of tickers.

    Checks company_name_cache first (7-day TTL), then screener_lookup_cache, then
    screener_cache items, and finally falls back to a yfinance call for any remaining misses.
    Results are written back to company_name_cache so subsequent calls are instant.
    """
    _ensure_tables()
    if not tickers:
        return {}

    now_iso = datetime.now(timezone.utc).isoformat()
    result: dict[str, dict] = {}
    misses: list[str] = []

    for ticker in tickers:
        row = _db.query_one(
            "SELECT name, sector, industry FROM company_name_cache "
            "WHERE ticker = ? AND expires_at > ?",
            [ticker, now_iso],
        )
        if row:
            result[ticker] = {
                "name": row["name"], "sector": row["sector"], "industry": row["industry"],
            }
        else:
            misses.append(ticker)

    # Try screener_lookup_cache for misses
    still_missing: list[str] = []
    for ticker in misses:
        row = _db.query_one(
            "SELECT item_json FROM screener_lookup_cache WHERE symbol = ? AND expires_at > ?",
            [ticker, now_iso],
        )
        if row:
            item = json.loads(row["item_json"])
            result[ticker] = {
                "name":     item.get("companyName") or ticker,
                "sector":   item.get("sector"),
                "industry": item.get("industry"),
            }
        else:
            still_missing.append(ticker)

    # Try screener_cache items for still-missing tickers
    if still_missing:
        missing_set = set(still_missing)
        for row in _db.query(
            "SELECT results_json FROM screener_cache WHERE expires_at > ?", [now_iso]
        ):
            if not missing_set:
                break
            try:
                items_list = json.loads(row["results_json"])
                for item in items_list:
                    sym = item.get("symbol", "")
                    if sym in missing_set:
                        result[sym] = {
                            "name":     item.get("companyName") or sym,
                            "sector":   item.get("sector"),
                            "industry": item.get("industry"),
                        }
                        missing_set.discard(sym)
            except Exception:
                pass
        still_missing = list(missing_set)

    # yfinance fallback for true cache misses (parallel, max 10 workers)
    def _fetch_one(ticker: str) -> tuple[str, dict]:
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            name = info.get("longName") or info.get("shortName") or ticker
            return ticker, {
                "name":     name,
                "sector":   info.get("sector"),
                "industry": info.get("industry"),
            }
        except Exception:
            return ticker, {"name": ticker, "sector": None, "industry": None}

    if still_missing:
        with ThreadPoolExecutor(max_workers=10) as pool:
            for ticker, data in pool.map(_fetch_one, still_missing):
                result[ticker] = data

    # Persist all newly fetched entries to company_name_cache (7-day TTL)
    newly_fetched = [t for t in tickers if t in result and t in (misses)]
    if newly_fetched:
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        for ticker in newly_fetched:
            d = result[ticker]
            _upsert(
                "company_name_cache", "ticker",
                ["ticker", "name", "sector", "industry", "expires_at"],
                [ticker, d.get("name") or ticker, d.get("sector"), d.get("industry"), expires],
            )

    return result




def _intl_universe(market: str,
                   per_sector_limit: int = _PER_SECTOR_LIMIT
                   ) -> tuple[list[dict], dict, dict, dict]:
    """Exchange universe for HK or SG, from the same FMP screener the US
    path uses.

    Replaces AKShare's stock_hk_famous_spot_em (a curated ~118 "well-known"
    list with NO industry data) and SG's hand-maintained universe.py. Two
    things change as a result:

      * Coverage goes from ~118 hand-picked HK names to the whole exchange
        above the market-cap floor.
      * Every stock carries a real FMP `industry`, so the screener's
        industry-relative percentile tier finally engages. Previously every
        HK stock was labelled "HKEX", which put all of them in one bucket —
        the tier ran, but ranked each stock against the entire market while
        reporting it as industry-relative.

    Reuses regional_comps.fetch_universe/dedupe_universe so the screener and
    the valuation comps see exactly the same universe, including the RMB
    dual-counter and SGX depositary-receipt filtering.

    Returns (rows, sector_map, industry_map, name_map) keyed by canonical
    ticker.
    """
    from src.data.regional_comps import (
        dedupe_universe, fetch_universe, normalize_name,
    )
    from src.tools.intl_provider import canonical_symbol

    exclude: set = set()
    if market == "SES":
        # Keep HK-primary depositary receipts out of the SGX screener for
        # the same reason they are kept out of SGX comps.
        try:
            exclude = {normalize_name(r["name"]) for r in fetch_universe("HKSE")}
        except Exception as exc:
            _sqlog.warning("SGX cross-listing filter unavailable: %s", exc)

    raw = dedupe_universe(fetch_universe(market), exclude_names=exclude)

    # Bound the universe the same way the US screener does: the largest
    # `per_sector_limit` names in each GICS sector. HKEX has ~1,750 names
    # above the cap floor and each one costs 8 FMP calls, so taking the lot
    # would be a ~14k-call refresh — an order of magnitude more than the US
    # path it is supposed to mirror.
    #
    # The trade-off is the same one US lives with: a bounded universe means
    # only concentrated industries (HK property developers, banks) keep >=5
    # peers and resolve at the industry tier; thinner ones fall to sector.
    # That is reported per stock rather than assumed.
    by_sector: dict[str, list] = {}
    for r in raw:                       # already sorted by market cap desc
        by_sector.setdefault(r["sector"] or "Unknown", []).append(r)
    raw = [r for group in by_sector.values() for r in group[:per_sector_limit]]
    raw.sort(key=lambda r: -(r["market_cap"] or 0))

    rows: list[dict] = []
    sector_map: dict[str, str] = {}
    industry_map: dict[str, str] = {}
    name_map: dict[str, str] = {}
    mkt = "hk" if market == "HKSE" else "sg"
    for r in raw:
        canonical = canonical_symbol(r["symbol"], mkt) or r["symbol"]
        rows.append({
            "canonical": canonical,
            "name": r["name"],
            "sector": r["sector"] or "Unknown",
            "industry": r["industry"] or "Unknown",
            "market_cap": r["market_cap"],
            # _fetch_ticker_metrics returns no price or beta, and
            # robo_strategy_service._to_candidate DROPS any stock without a
            # price — which silently excluded every HK and SG name from
            # Individual Stocks mode. Both come free on the screener row.
            "price": r.get("price"),
            "beta": r.get("beta"),
        })
        sector_map[canonical] = r["sector"] or "Unknown"
        industry_map[canonical] = r["industry"] or "Unknown"
        name_map[canonical] = r["name"]
    _sqlog.info("%s screener universe: %d names, %d industries, %d sectors",
                market, len(rows), len(set(industry_map.values())),
                len(set(sector_map.values())))
    return rows, sector_map, industry_map, name_map


def get_hk_screener_stocks(force_refresh: bool = False) -> dict:
    """
    Return the HKEX universe with VGPM scores.

    Universe source: FMP company-screener, exchange=HKSE — the same call the
                     US screener uses. Replaces AKShare's
                     stock_hk_famous_spot_em, a curated ~118 "well-known"
                     list that carried no industry data.
    Metrics source:  the shared _fetch_ticker_metrics FMP pipeline.
    Sector/industry: FMP classification (was TICKER_SECTOR_LOOKUP with every
                     stock's industry set to the literal "HKEX").
    Caching:         8d in screener_cache under "hk_fmp_v7" (the weekly
                     scheduler job rewrites it; prices via write-back).

    The industry field is the substantive change. VGPM ranks peer-relative,
    industry first (>=5 peers) then sector (>=8); with every stock labelled
    "HKEX" the industry tier ran against the entire market while reporting
    itself as industry-relative. HKEX has ~139 real industries, 97 of which
    clear the peer floor.
    """
    _ensure_tables()
    cache_key = "hk_fmp_v7"   # v7: FMP exchange universe + real industries (was AKShare famous-spot)

    if not force_refresh:
        cached = _get_cached(cache_key)
        if cached is not None:
            # No blocking full-universe re-quote here — that cost seconds on
            # every page load (SGX: 128 individual FMP /stable/quote calls,
            # 5-11 s). Prices stay live via the frontend's 15 s
            # /screener/prices tick, whose write-back (update_cached_prices)
            # also keeps these rows fresh between weekly refreshes.
            return {"items": cached, "total": len(cached), "cached": True}

    # A cold HK build is ~2,600 FMP calls (330 names x 8 endpoints) and
    # cannot finish inside an HTTP request — it ran 300s and the browser gave
    # up, which is what "loading failed" looked like. Only one build runs at
    # a time; concurrent requests serve the last known rows instead of
    # queueing behind it and multiplying load on the same rate-limit bucket.
    # The weekly refresh job is what keeps those rows current.
    _lock = _build_lock(cache_key)
    if not _lock.acquire(blocking=False):
        _stale = _get_cached_stale(cache_key)
        if _stale:
            _sqlog.info("HK screener: build in progress — serving stale rows")
            return {"items": _stale, "total": len(_stale),
                    "cached": True, "stale": True}
        _lock.acquire()          # nothing to serve — wait for the build
        try:
            _done = _get_cached(cache_key) or _get_cached_stale(cache_key) or []
        finally:
            _lock.release()
        return {"items": _done, "total": len(_done), "cached": bool(_done)}

    # Ownership of the lock is handed to the background refresh, so track
    # the release explicitly — Lock.locked() cannot tell WHICH thread holds
    # it, and releasing another thread's lock would let two builds run.
    _released = False
    try:
        served = _serve_stale_and_refresh(
            cache_key, lambda: _build_hk_screener(cache_key), "HK screener")
        if served is not None:
            _lock.release()
            _released = True
            return served
        return _build_hk_screener(cache_key)
    finally:
        if not _released:
            _lock.release()


def _build_hk_screener(cache_key: str) -> dict:
    """Cold-path build for the HK screener. Callers hold the build lock."""
    # ── Steps 1-2: universe + classification, both from FMP ──────────────
    try:
        universe_rows, sector_map, industry_map, name_map = _intl_universe("HKSE")
    except Exception as exc:
        _sqlog.error("HK screener universe fetch failed: %s", exc)
        stale = _get_cached_stale(cache_key)
        if stale:
            return {"items": stale, "total": len(stale), "cached": True, "stale": True}
        return {"items": [], "total": 0, "cached": False}
    if not universe_rows:
        stale = _get_cached_stale(cache_key)
        if stale:
            return {"items": stale, "total": len(stale), "cached": True, "stale": True}
        return {"items": [], "total": 0, "cached": False}

    # ── Step 3: metric fetch via the shared FMP pipeline (parallel) ──────
    # Same _fetch_ticker_metrics the US screener uses, so HK stocks are
    # scored on the same sub-factors from the same source. The per-stock
    # AKShare + yfinance .info combination this replaces cost ~1.5s and a
    # separate HTTP call each, and supplied no industry.
    raw_metrics_list: list[dict] = []
    api_key = _get_fmp_key()

    # Reuse anything already in the knowledge-graph TTM cache, the same way
    # _get_or_compute_fast_vgpm does for US. Without this every rebuild
    # re-fetched all ~330 names at 8 FMP calls each even when the metrics
    # were minutes old.
    from app.backend.services import knowledge_graph as _kg_read
    try:
        _cached_metrics = _kg_read.get_ttm_metrics_cached(
            [r["canonical"] for r in universe_rows]) or {}
    except Exception:
        _cached_metrics = {}
    if _cached_metrics:
        _sqlog.info("HK screener: %d/%d metrics served from KG cache",
                    len(_cached_metrics), len(universe_rows))

    def _fetch_fast_metrics(row: dict) -> Optional[dict]:
        canonical = row["canonical"]
        try:
            metrics = _cached_metrics.get(canonical) or _fetch_ticker_metrics(
                canonical, api_key)
            if not metrics:
                return None
            metrics["ticker"] = canonical
            metrics["sector"] = sector_map.get(canonical, "Unknown")
            metrics["industry"] = industry_map.get(canonical, "Unknown")
            if row.get("market_cap"):
                metrics.setdefault("market_cap", row["market_cap"])
            if row.get("price") and not metrics.get("price"):
                metrics["price"] = row["price"]
            if row.get("beta") is not None and metrics.get("beta") is None:
                metrics["beta"] = row["beta"]
            return metrics
        except Exception as exc:
            _sqlog.warning("HK screener metrics failed for %s: %s", canonical, exc)
            return None

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for m in pool.map(_fetch_fast_metrics, universe_rows):
                if m:
                    raw_metrics_list.append(m)
    except Exception as exc:
        _sqlog.error("HK screener bulk metric fetch failed: %s", exc)
        raw_metrics_list = []

    # ── Step 4: compute VGPM within HK universe (peer-relative) ──────────────
    hk_vgpm: dict[str, dict] = {}
    if raw_metrics_list:
        # Store metrics in the knowledge graph so subsequent lookup_ticker calls benefit
        from app.backend.services import knowledge_graph as _kg
        _kg.set_ttm_metrics({m["ticker"]: m for m in raw_metrics_list})
        try:
            hk_vgpm = _compute_fast_vgpm_universe(raw_metrics_list)
            _set_fast_vgpm_cached(hk_vgpm)
        except Exception as exc:
            _sqlog.error("HK VGPM universe computation failed: %s", exc)

    # ── Step 5: assemble output items ─────────────────────────────────────────
    # The screener response carries no live quote; get_live_quotes overlays
    # price/change on the cached-read path, same as the US screener.
    metrics_map: dict[str, dict] = {m["ticker"]: m for m in raw_metrics_list}

    items = []
    for row in universe_rows:
        t      = row["canonical"]
        vgpm   = hk_vgpm.get(t)
        m      = metrics_map.get(t, {})
        composite = None
        if vgpm:
            scores = [v["score"] for v in vgpm.values() if isinstance(v.get("score"), (int, float))]
            composite = round(sum(scores) / len(scores)) if scores else None

        items.append({
            "symbol":          t,
            "companyName":     name_map.get(t, ""),
            "sector":          sector_map.get(t, "Unknown"),
            "industry":        industry_map.get(t, "Unknown"),
            # Market cap comes from the FMP screener row (HKD), so it no
            # longer depends on AKShare's 亿-unit scaling.
            "marketCap":       row.get("market_cap") or m.get("market_cap"),
            "price":           m.get("price"),
            "change_pct":      m.get("change_pct"),
            "volume":          m.get("volume"),
            "beta":            m.get("beta") or m.get("beta_yf"),
            "exchange":        "HKEX",
            "country":         "HK",
            "vgpm":            vgpm,
            "vgpm_estimated":  vgpm is not None,
            "composite_score": composite,
        })

    # Sort: highest composite score first (HK peers ranked within HK universe)
    items.sort(key=lambda x: (x["composite_score"] is None, -(x["composite_score"] or 0)))

    _set_cached(cache_key, items, ttl_hours=_WEEKLY_TTL_HOURS)
    return {"items": items, "total": len(items), "cached": False}


def get_sg_screener_stocks(force_refresh: bool = False) -> dict:
    """
    Return the SGX universe with VGPM scores.

    Universe source: FMP company-screener, exchange=SES — the same call the
                     US screener uses (was a hand-curated ~80-name list in
                     src/tools/sg/universe.py, now the fallback).
    Metrics source:  the shared _fetch_ticker_metrics FMP pipeline.
    VGPM:            peer-relative percentile ranks, industry-first.
    Caching:         8d in screener_cache under "sg_fmp_v3" (the weekly
                     scheduler job rewrites it; prices via write-back).

    SGX is small (~150 names above the market-cap floor), so most stocks
    resolve at the sector tier rather than industry — reported as such
    rather than presented as industry-relative.
    """
    _ensure_tables()
    cache_key = "sg_fmp_v3"   # v3: marketCap fix (v2 rows were all null — the FMP metrics path never sets market_cap_sgd)

    if not force_refresh:
        cached = _get_cached(cache_key)
        if cached is not None:
            # No blocking full-universe re-quote here — that cost seconds on
            # every page load (SGX: 128 individual FMP /stable/quote calls,
            # 5-11 s). Prices stay live via the frontend's 15 s
            # /screener/prices tick, whose write-back (update_cached_prices)
            # also keeps these rows fresh between weekly refreshes.
            return {"items": cached, "total": len(cached), "cached": True}

    # Same cold-build protection as HK. SGX is smaller (~130 names) so its
    # build fits inside a request today, but the failure mode is identical
    # if the universe grows or FMP slows, and the herd behaviour is worse
    # than the latency.
    _lock = _build_lock(cache_key)
    if not _lock.acquire(blocking=False):
        _stale = _get_cached_stale(cache_key)
        if _stale:
            _sqlog.info("SGX screener: build in progress — serving stale rows")
            return {"items": _stale, "total": len(_stale),
                    "cached": True, "stale": True}
        _lock.acquire()
        try:
            _done = _get_cached(cache_key) or _get_cached_stale(cache_key) or []
        finally:
            _lock.release()
        return {"items": _done, "total": len(_done), "cached": bool(_done)}

    # Ownership of the lock is handed to the background refresh, so track
    # the release explicitly — Lock.locked() cannot tell WHICH thread holds
    # it, and releasing another thread's lock would let two builds run.
    _released = False
    try:
        served = _serve_stale_and_refresh(
            cache_key, lambda: _build_sg_screener(cache_key), "SGX screener")
        if served is not None:
            _lock.release()
            _released = True
            return served
        return _build_sg_screener(cache_key)
    finally:
        if not _released:
            _lock.release()


def _build_sg_screener(cache_key: str) -> dict:
    """Cold-path build for the SGX screener. Callers hold the build lock."""
    try:
        universe_rows, sector_map, industry_map, name_map = _intl_universe("SES")
    except Exception as exc:
        _sqlog.error("SGX screener universe fetch failed: %s", exc)
        return {"items": [], "total": 0, "cached": False}
    if not universe_rows:
        return {"items": [], "total": 0, "cached": False}
    universe = universe_rows
    raw_metrics: dict[str, dict] = {}

    _sg_api_key = _get_fmp_key()

    from app.backend.services import knowledge_graph as _kg_read
    try:
        _sg_cached = _kg_read.get_ttm_metrics_cached(
            [r["canonical"] for r in universe]) or {}
    except Exception:
        _sg_cached = {}
    if _sg_cached:
        _sqlog.info("SGX screener: %d/%d metrics served from KG cache",
                    len(_sg_cached), len(universe))

    def _fetch_one(stock: dict) -> tuple[str, dict]:
        canonical = stock["canonical"]
        try:
            # Same shared FMP pipeline the US and HK screeners use, so all
            # three are scored on identical sub-factors from one source.
            metrics = dict(_sg_cached.get(canonical) or {}) or (
                _fetch_ticker_metrics(canonical, _sg_api_key) or {})
            metrics["_sector"] = stock.get("sector", "Unknown")
            metrics["_industry"] = stock.get("industry", "Unknown")
            metrics["_name"] = stock.get("name", canonical)
            if stock.get("market_cap"):
                metrics.setdefault("market_cap", stock["market_cap"])
            if stock.get("price") and not metrics.get("price"):
                metrics["price"] = stock["price"]
            if stock.get("beta") is not None and metrics.get("beta") is None:
                metrics["beta"] = stock["beta"]
            return canonical, metrics
        except Exception as e:
            _sqlog.warning("SGX metric fetch failed for %s: %s", canonical, e)
            return canonical, {"_sector": stock.get("sector"),
                               "_industry": stock.get("industry"),
                               "_name": stock.get("name")}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_one, s): s["canonical"] for s in universe}
        for future in as_completed(futures):
            canonical, metrics = future.result()
            raw_metrics[canonical] = metrics

    _sqlog.info("SGX screener: fetched metrics for %d tickers", len(raw_metrics))

    # Convert dict[str, dict] → list[dict] with ticker/sector/industry keys
    # as expected by _compute_fast_vgpm_universe
    metrics_list: list[dict] = []
    for canonical, m in raw_metrics.items():
        entry = dict(m)
        entry["ticker"] = canonical
        entry["sector"] = m.get("_sector", "Unknown")
        entry["industry"] = m.get("_industry", "Unknown")
        metrics_list.append(entry)

    scored = _compute_fast_vgpm_universe(metrics_list)

    # The universe row carries the authoritative market cap. The item used to
    # read metrics["market_cap_sgd"] — a key only the legacy yfinance fetcher
    # sets — so every FMP-path row shipped marketCap=None.
    mcap_by_canonical = {s["canonical"]: s.get("market_cap") for s in universe}

    items: list[dict] = []
    for canonical, metrics in raw_metrics.items():
        vgpm = scored.get(canonical)
        composite = None
        if vgpm:
            scores = [v["score"] for v in vgpm.values() if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
            composite = round(sum(scores) / len(scores)) if scores else None

        items.append({
            "symbol":          canonical,
            "companyName":     metrics.get("_name", canonical),
            "sector":          metrics.get("_sector", "Unknown"),
            "industry":        metrics.get("_industry", "Unknown"),
            "marketCap":       (mcap_by_canonical.get(canonical)
                                or metrics.get("market_cap_sgd")
                                or metrics.get("market_cap")),
            "price":           metrics.get("price"),
            "change_pct":      metrics.get("change_pct"),
            "volume":          metrics.get("volume"),
            "beta":            metrics.get("beta"),
            "exchange":        "SGX",
            "country":         "SG",
            "vgpm":            vgpm,
            "vgpm_estimated":  True,
            "composite_score": composite,
        })

    items.sort(key=lambda x: (x["composite_score"] is None, -(x["composite_score"] or 0)))

    _set_cached(cache_key, items, ttl_hours=_WEEKLY_TTL_HOURS)
    return {"items": items, "total": len(items), "cached": False}


# ── Weekly refresh (scheduler job + manual prod seed) ────────────────────────
#: Primary structure caches the weekly job keeps warm. US needs no entry:
#: its read path filters the daily-backfilled master_universe in-memory.
_PRIMARY_KEYS = ("hk_fmp_v7", "sg_fmp_v3")

#: A refresh younger than this counts as "already ran this week" — the
#: worker-side idempotency gate (mirrors regional_comps' ~6-day window).
_REFRESH_GATE_DAYS = 6.0


def screener_refresh_due() -> bool:
    """True when any primary screener cache row is missing or older than
    ~6 days — i.e. the current weekly slot's work is not done."""
    _ensure_tables()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_REFRESH_GATE_DAYS)).isoformat()
    for key in _PRIMARY_KEYS:
        row = _db.query_one(
            "SELECT fetched_at FROM screener_cache WHERE cache_key = ?", [key])
        if not row or (row["fetched_at"] or "") < cutoff:
            return True
    return False


def run_weekly_screener_refresh(force: bool = False) -> Optional[dict]:
    """Re-fetch the US, HK and SG screener universes from FMP and rewrite
    their cache rows (8-day TTL). Mirrors regional_comps.run_weekly_refresh:
    self-gates on the ~6-day window unless force=True (manual seed).

    Everything below reads FMP directly — company-screener for the universes,
    the shared _fetch_ticker_metrics pipeline for per-stock metrics — nothing
    is recycled from stale rows.
    """
    if not force and not screener_refresh_due():
        _sqlog.info("[screener] refreshed within %.0f days — skipping", _REFRESH_GATE_DAYS)
        return None
    results: dict = {}
    for market, fn in (
        ("US", lambda: get_screener_stocks(market_cap_more_than=2_000_000_000,
                                           force_refresh=True)),
        ("HK", lambda: get_hk_screener_stocks(force_refresh=True)),
        ("SG", lambda: get_sg_screener_stocks(force_refresh=True)),
    ):
        try:
            out = fn()
            results[market] = {"total": out.get("total", 0)}
            _sqlog.info("[screener] weekly refresh %s: %s items", market, out.get("total", 0))
        except Exception as exc:
            _sqlog.exception("[screener] weekly refresh %s failed: %s", market, exc)
            results[market] = {"error": str(exc)[:200]}
    return results

