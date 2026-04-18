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
- Caching: raw metrics cached 24h in raw_metrics_cache; computed VGPM in
  fast_vgpm_cache; screener results in screener_cache.
"""
import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

_STABLE = "https://financialmodelingprep.com/stable"

# FMP sector names — must match the frontend SECTORS list exactly.
_SCREENER_SECTORS = [
    "Technology", "Healthcare", "Consumer Cyclical", "Financial Services",
    "Communication Services", "Consumer Defensive", "Energy", "Industrials",
    "Basic Materials", "Real Estate", "Utilities",
]
# How many stocks per sector when fetching the "All sectors" universe.
_PER_SECTOR_LIMIT = 30


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
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT cache_key, results_json FROM screener_cache").fetchall()
        for row in rows:
            items = json.loads(row["results_json"])
            changed = False
            for item in items:
                q = quotes.get(item.get("symbol", ""))
                if q:
                    _overlay_live(item, q)
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE screener_cache SET results_json = ? WHERE cache_key = ?",
                    (json.dumps(items), row["cache_key"]),
                )
        conn.commit()
    finally:
        conn.close()


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


def _ensure_tables():
    conn = _connect()
    try:
        conn.execute(_DDL_SCREENER)
        conn.execute(_DDL_FAST_VGPM)
        conn.execute(_DDL_RAW_METRICS)
        conn.execute(_DDL_LOOKUP_CACHE)
        conn.execute(_DDL_COMPANY_NAME_CACHE)
        conn.execute(_DDL_MASTER_UNIVERSE)
        conn.commit()
    finally:
        conn.close()


def _make_cache_key(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _get_cached(cache_key: str) -> Optional[list]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT results_json, expires_at FROM screener_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        if datetime.now(timezone.utc).isoformat() > row["expires_at"]:
            return None
        return json.loads(row["results_json"])
    finally:
        conn.close()


def _set_cached(cache_key: str, results: list, ttl_hours: int = 24):
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO screener_cache "
            "(cache_key, fetched_at, expires_at, results_json) VALUES (?, ?, ?, ?)",
            (cache_key, now.isoformat(), expires, json.dumps(results)),
        )
        conn.commit()
    finally:
        conn.close()


# ── Fast VGPM per-ticker cache ─────────────────────────────────────────────────

def _get_fast_vgpm_cached(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    conn = _connect()
    conn.row_factory = sqlite3.Row
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, data_json FROM fast_vgpm_cache "
            f"WHERE ticker IN ({placeholders}) AND expires_at > ?",
            [*tickers, now_iso],
        ).fetchall()
    finally:
        conn.close()
    return {row["ticker"]: json.loads(row["data_json"]) for row in rows}


def _set_fast_vgpm_cached(data: dict[str, dict], ttl_hours: int = 24):
    if not data:
        return
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    now_iso = now.isoformat()
    conn = _connect()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO fast_vgpm_cache (ticker, cached_at, expires_at, data_json) "
            "VALUES (?, ?, ?, ?)",
            [(t, now_iso, expires, json.dumps(v)) for t, v in data.items()],
        )
        conn.commit()
    finally:
        conn.close()


# ── Raw metrics cache (stores fetched FMP values, survives methodology updates) ─

def _get_raw_metrics_cached(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    conn = _connect()
    conn.row_factory = sqlite3.Row
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, data_json FROM raw_metrics_cache "
            f"WHERE ticker IN ({placeholders}) AND expires_at > ?",
            [*tickers, now_iso],
        ).fetchall()
    finally:
        conn.close()
    return {row["ticker"]: json.loads(row["data_json"]) for row in rows}


def _set_raw_metrics_cached(data: dict[str, dict], ttl_hours: int = 24):
    if not data:
        return
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    now_iso = now.isoformat()
    conn = _connect()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO raw_metrics_cache (ticker, cached_at, expires_at, data_json) "
            "VALUES (?, ?, ?, ?)",
            [(t, now_iso, expires, json.dumps(v)) for t, v in data.items()],
        )
        conn.commit()
    finally:
        conn.close()


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
                conn = _connect()
                conn.row_factory = sqlite3.Row
                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    row = conn.execute(
                        "SELECT item_json FROM screener_lookup_cache WHERE symbol = ? AND expires_at > ?",
                        (ticker, now_iso),
                    ).fetchone()
                    if row:
                        return json.loads(row["item_json"])
                finally:
                    conn.close()

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
            conn = _connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO screener_lookup_cache VALUES (?,?,?,?)",
                    (ticker, now.isoformat(), expires, json.dumps(item)),
                )
                conn.commit()
            finally:
                conn.close()

            return item
    except Exception as _hk_err:
        _sqlog.warning("lookup_ticker HK path failed for %s: %s", query, _hk_err)
    # ─────────────────────────────────────────────────────────────────────────

    ticker = _resolve_to_ticker(query, api_key)
    if not ticker:
        return None

    if not force_refresh:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            row = conn.execute(
                "SELECT item_json FROM screener_lookup_cache WHERE symbol = ? AND expires_at > ?",
                (ticker, now_iso),
            ).fetchone()
            if row:
                item = json.loads(row["item_json"])
                # Always overlay live quote data — not cached so it stays fresh
                live = get_live_quotes([ticker])
                if ticker in live:
                    _overlay_live(item, live[ticker])
                return item
        finally:
            conn.close()

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
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO screener_lookup_cache (symbol, fetched_at, expires_at, item_json) "
                "VALUES (?, ?, ?, ?)",
                (ticker, now.isoformat(), expires, json.dumps(item)),
            )
            conn.commit()
        finally:
            conn.close()

        # Overlay live quote data after caching base item — always fresh, never cached
        live = get_live_quotes([ticker])
        if ticker in live:
            _overlay_live(item, live[ticker])

        return item
    except Exception:
        return None


# ── Pipeline VGPM lookup ───────────────────────────────────────────────────────

def _get_vgpm_map(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {valuation, growth, profitability, momentum}} from latest web run."""
    if not tickers:
        return {}
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, full_result_json, MAX(run_at) AS latest "
            f"FROM web_runs "
            f"WHERE ticker IN ({placeholders}) AND full_result_json IS NOT NULL "
            f"GROUP BY ticker",
            tickers,
        ).fetchall()
    finally:
        conn.close()

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
    """Single FMP GET — returns parsed JSON or None on any failure."""
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json() if r.ok else None
    except Exception:
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
    # Numeric HKEX tickers (e.g. '00700.HK') are routed to the dedicated
    # multi-source HK fetcher instead of the FMP pipeline, which has no
    # meaningful HKEX coverage.
    try:
        from src.tools.hk.ticker import is_hk_ticker
        if is_hk_ticker(ticker):
            from src.tools.hk.vgpm_metrics import fetch_hk_vgpm_metrics
            result = fetch_hk_vgpm_metrics(ticker)
            if result is not None:
                return result
            # If HK fetch returns None (unexpected), fall through to FMP
    except Exception as _hk_exc:
        _sqlog.warning("HK VGPM routing failed for %s: %s", ticker, _hk_exc)
    # ── (HK routing end — US tickers continue below) ─────────────────────────

    base = {"apikey": api_key} if api_key else {}

    # ── Parallel FMP fetch ────────────────────────────────────────────────────
    endpoints = {
        "km":  (f"{_STABLE}/key-metrics-ttm",    {"symbol": ticker}),
        "rt":  (f"{_STABLE}/ratios-ttm",          {"symbol": ticker}),
        "fg":  (f"{_STABLE}/financial-growth",    {"symbol": ticker, "limit": 5}),
        "spc": (f"{_STABLE}/stock-price-change",  {"symbol": ticker}),
        "ae":  (f"{_STABLE}/analyst-estimates",   {"symbol": ticker, "limit": 2}),
        "es":  (f"{_STABLE}/earnings-surprises",  {"symbol": ticker, "limit": 4}),
        "fs":  (f"{_STABLE}/financial-score",     {"symbol": ticker}),
        "ud":  (f"{_STABLE}/upgrades-downgrades", {"symbol": ticker, "limit": 20, "page": 0}),
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
    peg       = _safe_float(km.get("pegRatioTTM"))
    div_yield = _safe_float(km.get("dividendYieldTTM"))

    # FCF yield: prefer direct field, fall back to 1/P_FCF
    fcf_yield = _safe_float(km.get("freeCashFlowYieldTTM"))
    if fcf_yield is None:
        p_fcf = _safe_float(
            rt.get("priceToFreeCashFlowsRatioTTM")
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

    return {
        "ticker": ticker,
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
        # Check raw metrics cache first to avoid re-fetching
        cached_raw = _get_raw_metrics_cached(missing)
        to_fetch   = [t for t in missing if t not in cached_raw]

        api_key = _get_fmp_key()
        good_fetched: dict[str, dict] = {}

        if to_fetch:
            # Each ticker makes 8 internal FMP calls with 4 workers.
            # Cap outer concurrency so total in-flight requests stay under
            # FMP's ~300 req/min free-tier limit.
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
            _set_raw_metrics_cached(good_fetched)

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
    Clears lookup cache, fast VGPM cache, raw metrics cache, and all screener
    cache entries so the next request reflects the new pipeline VGPM.
    """
    conn = _connect()
    try:
        conn.execute("DELETE FROM screener_lookup_cache WHERE symbol = ?", (ticker,))
        conn.execute("DELETE FROM fast_vgpm_cache WHERE ticker = ?", (ticker,))
        conn.execute("DELETE FROM raw_metrics_cache WHERE ticker = ?", (ticker,))
        conn.execute("DELETE FROM screener_cache")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ── Master universe ────────────────────────────────────────────────────────────

def _get_master_universe() -> Optional[list[dict]]:
    """Return all rows from master_universe if not expired, else None."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT data_json, expires_at FROM master_universe LIMIT 1"
        ).fetchone()
        if not rows:
            return None
        if datetime.now(timezone.utc).isoformat() > rows["expires_at"]:
            return None
        all_rows = conn.execute("SELECT data_json FROM master_universe").fetchall()
        return [json.loads(r["data_json"]) for r in all_rows]
    except Exception:
        return None
    finally:
        conn.close()


def _set_master_universe(stocks: list[dict], ttl_hours: int = 24):
    """Write all stocks to master_universe, replacing any existing data."""
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    now_iso = now.isoformat()
    conn = _connect()
    try:
        conn.execute("DELETE FROM master_universe")
        conn.executemany(
            "INSERT INTO master_universe (symbol, data_json, cached_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            [(s.get("symbol", ""), json.dumps(s), now_iso, expires)
             for s in stocks if s.get("symbol")],
        )
        conn.commit()
    finally:
        conn.close()


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
        fmp_stocks = _call_fmp_screener(**filter_params)
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

    conn = _connect()
    try:
        for ticker in tickers:
            row = conn.execute(
                "SELECT name, sector, industry FROM company_name_cache "
                "WHERE ticker = ? AND expires_at > ?",
                (ticker, now_iso),
            ).fetchone()
            if row:
                result[ticker] = {"name": row[0], "sector": row[1], "industry": row[2]}
            else:
                misses.append(ticker)

        # Try screener_lookup_cache for misses
        still_missing: list[str] = []
        for ticker in misses:
            row = conn.execute(
                "SELECT item_json FROM screener_lookup_cache WHERE symbol = ? AND expires_at > ?",
                (ticker, now_iso),
            ).fetchone()
            if row:
                item = json.loads(row[0])
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
            for (data_json,) in conn.execute(
                "SELECT results_json FROM screener_cache WHERE expires_at > ?", (now_iso,)
            ).fetchall():
                if not missing_set:
                    break
                try:
                    items_list = json.loads(data_json)
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
    finally:
        conn.close()

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
        conn2 = _connect()
        try:
            for ticker in newly_fetched:
                d = result[ticker]
                conn2.execute(
                    "INSERT OR REPLACE INTO company_name_cache (ticker, name, sector, industry, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ticker, d.get("name") or ticker, d.get("sector"), d.get("industry"), expires),
                )
            conn2.commit()
        finally:
            conn2.close()

    return result




def get_hk_screener_stocks(force_refresh: bool = False) -> dict:
    """
    Return the well-known HKEX universe (~118 stocks) with VGPM scores.

    Universe source: AKShare stock_hk_famous_spot_em() — Eastmoney Well-known HK Stocks.
    Metrics source:  AKShare per-stock (fast) + yfinance supplement for deeper fields.
    Sector/industry: TICKER_SECTOR_LOOKUP in sector_profiles.py, fallback yfinance info.
    Caching:         24h in screener_cache under cache key "hk_famous".

    Strategy for speed:
      - AKShare indicator + valuation endpoints (~1.5s/stock) run in ThreadPoolExecutor
      - yfinance is skipped for the bulk run (too slow at 118×10s = 20min)
      - Full multi-source fetch (fetch_hk_vgpm_metrics) is reserved for lookup_ticker
    """
    _ensure_tables()
    cache_key = "hk_famous_v6"   # v6: AKShare indicator shares cross-check (yfinance inflation guard)

    if not force_refresh:
        cached = _get_cached(cache_key)
        if cached is not None:
            cached_tickers = [i["symbol"] for i in cached if i.get("symbol")]
            live_quotes = get_live_quotes(cached_tickers)
            if live_quotes:
                for item in cached:
                    q = live_quotes.get(item["symbol"])
                    if q:
                        _overlay_live(item, q)
            return {"items": cached, "total": len(cached), "cached": True}

    # ── Step 1: get universe from AKShare famous spots ────────────────────────
    try:
        import akshare as ak
        df_famous = ak.stock_hk_famous_spot_em()
    except Exception as exc:
        _sqlog.error("stock_hk_famous_spot_em failed: %s", exc)
        return {"items": [], "total": 0, "cached": False}

    if df_famous is None or df_famous.empty:
        return {"items": [], "total": 0, "cached": False}

    # Normalise columns (AKShare returns Chinese headers)
    col_map = {
        "代码":   "code",       # ticker code e.g. "00700"
        "名称":   "name",       # company name
        "最新价": "price",      # latest price (HKD)
        "涨跌幅": "change_pct", # day % change
        "成交量": "volume",     # shares traded
    }
    df_famous = df_famous.rename(columns={k: v for k, v in col_map.items() if k in df_famous.columns})

    # Build canonical ticker list
    try:
        from src.tools.hk.ticker import to_canonical
    except Exception:
        return {"items": [], "total": 0, "cached": False}

    universe_rows: list[dict] = []
    for _, row in df_famous.iterrows():
        raw_code = str(row.get("code", "")).strip()
        if not raw_code:
            continue
        canonical = to_canonical(raw_code)   # e.g. "00700.HK"
        price      = _safe_float(row.get("price"))
        change_pct = _safe_float(row.get("change_pct"))
        name       = str(row.get("name", ""))
        universe_rows.append({
            "canonical": canonical,
            "name":       name,
            "price":      price,
            "change_pct": change_pct,
            "volume":     _safe_float(row.get("volume")),
        })

    # ── Step 2: sector/industry lookup ────────────────────────────────────────
    try:
        from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
    except Exception:
        TICKER_SECTOR_LOOKUP = {}

    sector_map:   dict[str, str] = {}
    industry_map: dict[str, str] = {}
    name_map:     dict[str, str] = {}
    for row in universe_rows:
        t    = row["canonical"]
        name_map[t] = row["name"]
        entry = TICKER_SECTOR_LOOKUP.get(t)
        if entry:
            sector_map[t]   = entry[0]            # e.g. "Tech"
            industry_map[t] = entry[2] or "HKEX"  # e.g. "Software (Internet)"
        else:
            sector_map[t]   = "Unknown"
            industry_map[t] = "HKEX"

    # ── Step 3: fast AKShare metric fetch for all 118 (parallel) ─────────────
    try:
        from src.tools.hk.vgpm_metrics import _fetch_akshare
        from src.tools.hk.ticker import to_akshare_code

        def _fetch_fast_metrics(row: dict) -> Optional[dict]:
            try:
                canonical = row["canonical"]
                ak_code   = to_akshare_code(canonical)
                metrics   = _fetch_akshare(ak_code)
                if not metrics:
                    metrics = {}
                metrics["ticker"]   = canonical
                metrics["sector"]   = sector_map.get(canonical, "Unknown")
                metrics["industry"] = industry_map.get(canonical, "HKEX")
                # Inject live price
                metrics.setdefault("price", row.get("price"))

                # ── Market cap via stock_hk_scale_comparison_em (HKD, no unit conversion) ──
                try:
                    import akshare as ak_lib
                    sc_df = ak_lib.stock_hk_scale_comparison_em(symbol=ak_code)
                    if sc_df is not None and not sc_df.empty:
                        mc_col = next(
                            (c for c in sc_df.columns
                             if "总市值" in str(c) and "排名" not in str(c)),
                            None,
                        )
                        if mc_col:
                            mc_val = sc_df.iloc[0][mc_col]
                            mc_f   = float(mc_val) if mc_val is not None else None
                            if mc_f and mc_f == mc_f:  # not NaN
                                metrics["market_cap_hkd"] = mc_f
                except Exception:
                    pass

                # ── yfinance .info — V/G/P/M extras + beta (single HTTP call) ──────────
                try:
                    import yfinance as yf
                    from src.tools.hk.ticker import to_yfinance_code

                    def _sf2(v):
                        if v is None: return None
                        try:
                            f = float(v)
                            return None if f != f else f  # guard NaN
                        except Exception:
                            return None

                    info = yf.Ticker(to_yfinance_code(canonical)).info or {}

                    # Beta
                    if (b := _sf2(info.get("beta"))) is not None:
                        metrics["beta_yf"] = b

                    # Valuation extras
                    for src_k, dst_k in [("forwardPE",         "fwd_pe"),
                                          ("pegRatio",          "peg"),
                                          ("enterpriseToEbitda","ev_ebitda")]:
                        if (v := _sf2(info.get(src_k))) is not None:
                            metrics.setdefault(dst_k, v)

                    # Growth extras
                    fwd_eps = _sf2(info.get("forwardEps"))
                    ttm_eps = _sf2(info.get("trailingEps"))
                    if (fwd_eps is not None and ttm_eps is not None
                            and abs(ttm_eps) >= 0.10):
                        feg = (fwd_eps - ttm_eps) / abs(ttm_eps)
                        metrics.setdefault("fwd_eps_growth", max(-2.0, min(feg, 2.0)))

                    # Profitability extras
                    for src_k, dst_k in [("grossMargins",  "gross_margin"),
                                          ("returnOnEquity","roe"),
                                          ("returnOnAssets","roa"),
                                          ("profitMargins", "net_margin")]:
                        if (v := _sf2(info.get(src_k))) is not None:
                            metrics.setdefault(dst_k, v)

                    # Momentum extras
                    rec = _sf2(info.get("recommendationMean"))
                    if rec is not None:
                        metrics.setdefault("rec_score", (5.0 - rec) / 4.0)
                    sr = _sf2(info.get("shortRatio"))
                    if sr is not None:
                        metrics.setdefault("short_ratio", sr)
                    wc52 = _sf2(info.get("52WeekChange"))
                    if wc52 is not None:
                        metrics.setdefault("price_1y", wc52)

                except Exception:
                    pass

                return metrics if metrics.get("ticker") else None
            except Exception:
                return None

        raw_metrics_list: list[dict] = []
        with ThreadPoolExecutor(max_workers=20) as pool:
            for m in pool.map(_fetch_fast_metrics, universe_rows):
                if m:
                    raw_metrics_list.append(m)
    except Exception as exc:
        _sqlog.error("HK screener bulk metric fetch failed: %s", exc)
        raw_metrics_list = []

    # ── Step 4: compute VGPM within HK universe (peer-relative) ──────────────
    hk_vgpm: dict[str, dict] = {}
    if raw_metrics_list:
        # Store metrics in raw_metrics_cache so subsequent lookup_ticker calls benefit
        _set_raw_metrics_cached({m["ticker"]: m for m in raw_metrics_list})
        try:
            hk_vgpm = _compute_fast_vgpm_universe(raw_metrics_list)
            _set_fast_vgpm_cached(hk_vgpm)
        except Exception as exc:
            _sqlog.error("HK VGPM universe computation failed: %s", exc)

    # ── Step 5: assemble output items ─────────────────────────────────────────
    # Live quotes already in universe_rows (from stock_hk_famous_spot_em)
    # Build fast lookup: canonical → metrics dict (for market_cap_hkd, beta_yf)
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
            "industry":        industry_map.get(t, "HKEX"),
            "marketCap":       m.get("market_cap_hkd"),   # 亿HKD × 1e8, from AKShare indicator
            "price":           row.get("price"),
            "change_pct":      row.get("change_pct"),
            "volume":          row.get("volume"),
            "beta":            m.get("beta_yf"),           # from yfinance .info
            "exchange":        "HKEX",
            "country":         "HK",
            "vgpm":            vgpm,
            "vgpm_estimated":  vgpm is not None,
            "composite_score": composite,
        })

    # Sort: highest composite score first (HK peers ranked within HK universe)
    items.sort(key=lambda x: (x["composite_score"] is None, -(x["composite_score"] or 0)))

    _set_cached(cache_key, items)
    return {"items": items, "total": len(items), "cached": False}


def get_sg_screener_stocks(force_refresh: bool = False) -> dict:
    """
    Return the curated SGX universe (~80 stocks) with VGPM scores.

    Universe source: src/tools/sg/universe.py (curated STI 30 + Mid Caps + REITs).
    Metrics source:  yfinance .info + .financials (primary).
    VGPM:            Computed within SG peer universe using same percentile-rank engine.
    Caching:         24h in screener_cache under cache key "sg_universe_v1".
    """
    _ensure_tables()
    cache_key = "sg_universe_v1"

    if not force_refresh:
        cached = _get_cached(cache_key)
        if cached is not None:
            return {"items": cached, "total": len(cached), "cached": True}

    try:
        from src.tools.sg.universe import get_sg_universe
        from src.tools.sg.ticker import to_yfinance_code, to_canonical as _sg_canonical
        from src.tools.sg.vgpm_metrics import fetch_sg_vgpm_metrics
    except ImportError as exc:
        _sqlog.error("SGX module import failed: %s", exc)
        return {"items": [], "total": 0, "cached": False}

    universe = get_sg_universe()
    _sqlog.info("SGX screener: %d stocks in universe", len(universe))

    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, as_completed

    raw_metrics: dict[str, dict] = {}

    def _fetch_one(stock: dict) -> tuple[str, dict]:
        code = stock["code"]
        canonical = _sg_canonical(code)
        try:
            metrics = fetch_sg_vgpm_metrics(code)
            metrics["_sector"] = stock.get("sector", "Unknown")
            metrics["_industry"] = stock.get("industry", "Unknown")
            metrics["_name"] = stock.get("name", code)
            return canonical, metrics
        except Exception as e:
            _sqlog.warning("SGX metric fetch failed for %s: %s", code, e)
            return canonical, {"_sector": stock.get("sector"), "_industry": stock.get("industry"), "_name": stock.get("name")}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, s): s["code"] for s in universe}
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
            "marketCap":       metrics.get("market_cap_sgd"),
            "price":           metrics.get("price"),
            "change_pct":      None,
            "volume":          None,
            "beta":            metrics.get("beta"),
            "exchange":        "SGX",
            "country":         "SG",
            "vgpm":            vgpm,
            "vgpm_estimated":  True,
            "composite_score": composite,
        })

    items.sort(key=lambda x: (x["composite_score"] is None, -(x["composite_score"] or 0)))

    _set_cached(cache_key, items)
    return {"items": items, "total": len(items), "cached": False}

