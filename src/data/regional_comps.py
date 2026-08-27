"""
src/data/regional_comps.py
==========================
Live industry- and sector-level valuation comps for the HK and SG markets.

Why this exists
---------------
get_sector_peer_multiples() early-returned a hardcoded 16-entry table for
`is_hk` and never reached the dynamic peer-median path, and SECTOR_PEER_BASKETS
had no HK or SG entries at all. So an HKEX stock was valued against static
sector numbers calibrated once in 2026-04, and an SGX stock got the US table.
Neither market had any industry-level granularity.

FMP global coverage makes real comps possible (verified 2026-08-27):

    company-screener?exchange=HKSE   1,817 names >$300M across 139 industries,
                                     97 of them with >=5 peers
    company-screener?exchange=SES      155 names across  69 industries, only
                                     7 of them with >=5 peers

That asymmetry drives the design: HK genuinely supports industry-level
medians, SGX mostly does not, so both markets resolve through the same
ladder and SG simply degrades to sector sooner. Every value carries the
basis it was computed at and its peer count, so a caller can never mistake a
2-peer reading for a real comp set.

Resolution ladder
-----------------
    1. industry median   requires >= MIN_INDUSTRY_PEERS (5)
    2. sector   median   requires >= MIN_SECTOR_PEERS   (8)
    3. caller's static table

Applied field by field — a field with enough industry peers uses the
industry median even when a neighbouring field falls back to sector. This
matches the existing merge contract in sector_profiles.get_sector_peer_multiples:
never blend a stale-static and a live value for the SAME field.

Universe hygiene
----------------
The raw screener output is not clean, and the contamination is material:

  * HKEX lists 22 RMB dual-counters (80700.HK is the same security as
    0700.HK, same ISIN) which would double-weight their companies.
  * HKEX carries dormant foreign mirrors — 4338.HK is "Microsoft
    Corporation" with a US ISIN. isActivelyTrading=false already excludes
    these, and the ISIN check is a second line of defence.
  * SGX lists HK depositary receipts. HBND.SI (Bank of China) is the single
    largest "SGX" name by market cap and HPAD.SI (Ping An) the third; left
    in, they drag Singapore bank comps toward mainland Chinese multiples,
    which is precisely the cross-contamination this module must avoid.

Filters below remove what is safely identifiable. Beyond that the defence is
statistical rather than rule-based: medians (not means) over a peer floor,
with implausible multiples dropped first, so one surviving receipt cannot
move a sector reading.

Refresh cadence: weekly. Cost ~3 calls per basket name, ~1,100 names for
HKSE (~5 min at the 11.5 rps token bucket in api.py) and ~150 for SES.
"""
from __future__ import annotations

import logging
import math
import os
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from src.data import db as _db
from src.tools.api import _STABLE, _fmp_get, _safe_float

logger = logging.getLogger(__name__)

# Storage key -> the FMP exchange codes that feed it.
#
# US pools NASDAQ, NYSE and AMEX into one universe: where a company chose to
# list is not an economic distinction, and pooling gives industry baskets
# deep enough to clear the peer floor. HK and SG each stand alone because
# their risk pricing genuinely differs — the whole point of keeping SGX
# comps free of HK depositary receipts.
MARKETS: dict[str, tuple[str, ...]] = {
    "HKSE": ("HKSE",),
    "SES": ("SES",),
    "US": ("NASDAQ", "NYSE", "AMEX"),
}

EXCHANGES = tuple(MARKETS)

# FMP exchangeShortName -> storage key.
_EXCHANGE_TO_MARKET: dict[str, str] = {
    code: market for market, codes in MARKETS.items() for code in codes
}


def market_for_exchange(exchange: Optional[str]) -> Optional[str]:
    """Storage key for an FMP exchangeShortName ("NASDAQ" -> "US")."""
    return _EXCHANGE_TO_MARKET.get((exchange or "").strip().upper())

# Market-cap floor for the universe: below this, listed multiples are too
# illiquid to be meaningful comps.
MIN_MARKET_CAP = 300_000_000

# Peer floors for each rung of the ladder. Industry is the tighter grouping
# so it needs fewer names; sector is broader and noisier, so it needs more.
MIN_INDUSTRY_PEERS = 5
MIN_SECTOR_PEERS = 8

# Basket sizes — the largest names by market cap in each grouping. Capping
# keeps the weekly refresh affordable and keeps micro-caps from dominating a
# median through sheer count.
INDUSTRY_BASKET_SIZE = 20
SECTOR_BASKET_SIZE = 40

# Size cohorts.
#
# An equal-weighted median over a whole industry is a bad comp for a large
# company, and on HKEX it is badly wrong rather than merely imprecise. The
# "Internet Content & Information" basket runs from Tencent (HK$4.1tn) down
# to Inkeverse (HK$1.7bn) — a 2,400x span — so its median lands on de-rated
# micro-caps at ~8x earnings. Valuing Tencent off that number would roughly
# halve its intrinsic value against a peer set it has nothing in common with.
#
# So every grouping is stored twice: "all" (the full basket) and "large"
# (the upper half by market cap). A caller that knows the target's own
# market cap gets the cohort it actually belongs to. This is ordinary comp
# practice — large caps and small caps trade at different multiples — and it
# keeps the peer floors intact rather than lowering them.
COHORTS = ("large", "all")

# Staleness window for reads. Refresh is weekly; 14 days leaves room for one
# missed run before callers fall back to static.
MAX_AGE_DAYS = 14

# Field -> (min, max) plausibility band. Values outside are dropped before
# the median, not clipped into it: a negative P/E is not a cheap stock, it
# is a loss-maker that does not belong in a P/E comp set at all.
_BANDS: dict[str, tuple[float, float]] = {
    "ev_ebitda":  (0.5, 100.0),
    "pe":         (1.0, 200.0),
    "ev_revenue": (0.05, 60.0),
    "pb":         (0.05, 30.0),
    "fcf_yield":  (-0.50, 0.50),
    "growth_avg": (-0.60, 2.00),
}

FIELDS = tuple(_BANDS.keys())


# ── Universe ────────────────────────────────────────────────────────────────

# Depositary receipts name themselves. HPAD.SI is
#   "Ping An Insurance (Group) Company of China Ltd. Shs UnSp Singapore
#    Depositary Receipt Repr 1/2 Sh"
# — the #2 name by market cap on SGX and a wrapper over an HK-primary
# company. Cross-referencing company names against the other exchange
# catches HBND.SI (whose name is plain "Bank of China Limited") but misses
# this one, because the receipt suffix makes the names differ. The wrapper
# says what it is, so read that directly.
_RECEIPT_RE = re.compile(
    r"depositary receipt|depository receipt"
    r"|(?:un)?sponsored\s+adr"
    r"|(?<![A-Za-z])(?:ADR|GDR|SDR)(?![A-Za-z])",
    re.IGNORECASE,
)


def is_depositary_receipt(name: Optional[str]) -> bool:
    """True when a listing is a receipt over a security listed elsewhere."""
    return bool(_RECEIPT_RE.search(name or ""))


_NAME_NOISE = re.compile(
    r"\b(limited|ltd|holdings|holding|group|company|co|inc|corporation|corp|"
    r"plc|the|public|berhad|sa|nv|se|ag)\b"
)


def normalize_name(name: Optional[str]) -> str:
    """Company name reduced to a comparable core, for de-duplication."""
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = _NAME_NOISE.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def fetch_universe(market: str) -> list[dict]:
    """Actively-traded operating companies in `market`.

    `market` is a MARKETS key, so the US pass unions its three exchanges.
    Returns [{symbol, name, sector, industry, market_cap, price, beta}, ...]
    sorted by market cap descending.
    """
    rows: list = []
    for code in MARKETS.get(market, (market,)):
        page = _fmp_get(
            f"{_STABLE}/company-screener",
            {
                "exchange": code,
                "limit": 5000,
                "marketCapMoreThan": MIN_MARKET_CAP,
                "isActivelyTrading": "true",
            },
            api_key=None,
            uncap=True,
        )
        if isinstance(page, list):
            rows.extend(page)
    if not rows:
        return []

    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("symbol"):
            continue
        if r.get("isEtf") or r.get("isFund"):
            continue
        if is_depositary_receipt(r.get("companyName")):
            continue
        mcap = _safe_float(r.get("marketCap")) or 0.0
        if mcap < MIN_MARKET_CAP:
            continue
        out.append({
            "symbol": r["symbol"],
            "name": (r.get("companyName") or "").strip(),
            "sector": (r.get("sector") or "").strip(),
            "industry": (r.get("industry") or "").strip(),
            "market_cap": mcap,
            # The screener response already carries these; the comps path
            # ignores them, but the screener path needs price (a candidate
            # with no price is dropped downstream) and beta (risk tier).
            # Free — no extra call.
            "price": _safe_float(r.get("price")),
            "beta": _safe_float(r.get("beta")),
        })
    out.sort(key=lambda r: -r["market_cap"])
    return out


def dedupe_universe(rows: list[dict], exclude_names: Optional[set] = None) -> list[dict]:
    """Drop duplicate securities and known cross-listings.

    Keeps the largest listing of each company (rows arrive sorted by market
    cap), which for HK's dual-counter pairs is the main HKD line rather than
    the thinner RMB counter.

    `exclude_names` carries normalised names from the other exchange, so an
    SGX depositary receipt over a company that is primary-listed in Hong Kong
    is dropped from the Singapore comp set.
    """
    seen_names: set[str] = set()
    excluded = exclude_names or set()
    out: list[dict] = []
    for r in rows:
        key = normalize_name(r["name"])
        if not key:
            continue
        if key in seen_names or key in excluded:
            continue
        seen_names.add(key)
        out.append(r)
    return out


def build_baskets(rows: list[dict]) -> tuple[dict, dict, list[str]]:
    """Group the universe into industry and sector baskets.

    Returns (industry_baskets, sector_baskets, symbols_to_fetch) where the
    symbol list is the union of both, so each name is fetched once.
    """
    by_industry: dict[str, list[dict]] = {}
    by_sector: dict[str, list[dict]] = {}
    for r in rows:
        if r["industry"]:
            by_industry.setdefault(r["industry"], []).append(r)
        if r["sector"]:
            by_sector.setdefault(r["sector"], []).append(r)

    ind = {k: v[:INDUSTRY_BASKET_SIZE] for k, v in by_industry.items()}
    sec = {k: v[:SECTOR_BASKET_SIZE] for k, v in by_sector.items()}

    symbols = {r["symbol"] for group in ind.values() for r in group}
    symbols |= {r["symbol"] for group in sec.values() for r in group}
    return ind, sec, sorted(symbols)


# ── Per-name metrics ────────────────────────────────────────────────────────

def fetch_name_multiples(symbol: str) -> Optional[dict]:
    """TTM multiples plus mean revenue growth for one name.

    Three calls: key-metrics-ttm (EV multiples, FCF yield), ratios-ttm (P/E,
    P/B) and financial-growth (revenue growth, averaged over the available
    years). Returns None only when every field came back empty.
    """
    out: dict = {"symbol": symbol}

    km = _fmp_get(f"{_STABLE}/key-metrics-ttm", {"symbol": symbol}, api_key=None)
    if isinstance(km, list) and km:
        row = km[0]
        out["ev_ebitda"] = _safe_float(row.get("evToEBITDATTM"))
        out["ev_revenue"] = _safe_float(row.get("evToSalesTTM"))
        out["fcf_yield"] = _safe_float(row.get("freeCashFlowYieldTTM"))

    rt = _fmp_get(f"{_STABLE}/ratios-ttm", {"symbol": symbol}, api_key=None)
    if isinstance(rt, list) and rt:
        row = rt[0]
        out["pe"] = _safe_float(row.get("priceToEarningsRatioTTM"))
        out["pb"] = _safe_float(row.get("priceToBookRatioTTM"))

    gr = _fmp_get(f"{_STABLE}/financial-growth",
                  {"symbol": symbol, "period": "annual", "limit": 3}, api_key=None)
    if isinstance(gr, list) and gr:
        vals = [v for v in (_safe_float(r.get("revenueGrowth")) for r in gr)
                if v is not None]
        if vals:
            out["growth_avg"] = sum(vals) / len(vals)

    if not any(out.get(f) is not None for f in FIELDS):
        return None
    return out


def _fetch_all(symbols: list[str], max_workers: int = 8) -> dict[str, dict]:
    got: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_name_multiples, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:
                logger.warning("regional_comps fetch failed for %s: %s", sym, exc)
                continue
            if row:
                got[sym] = row
    return got


# ── Medians ─────────────────────────────────────────────────────────────────

def _clean(field: str, values: list[Optional[float]]) -> list[float]:
    """Drop None, NaN/inf and out-of-band readings before taking a median."""
    lo, hi = _BANDS[field]
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        if lo <= f <= hi:
            out.append(f)
    return out


def split_cohorts(members: list[dict]) -> dict[str, list[dict]]:
    """{"all": every member, "large": the upper half by market cap}.

    Members arrive sorted by market cap descending. "large" is omitted when
    halving it would leave too few names to clear a peer floor anyway.
    """
    out = {"all": members}
    half = len(members) // 2
    if half >= MIN_INDUSTRY_PEERS:
        out["large"] = members[:half]
    return out


def compute_medians(baskets: dict[str, list[dict]], metrics: dict[str, dict],
                    level: str, min_peers: int) -> list[dict]:
    """Median per (grouping, cohort, field), keeping only combinations that
    clear the peer floor for that field."""
    rows: list[dict] = []
    for key, members in baskets.items():
        for cohort, group in split_cohorts(members).items():
            # Smallest member of the cohort — the caller compares the
            # target's own market cap against this to pick its cohort.
            floor_mcap = min((m.get("market_cap") or 0.0) for m in group) if group else 0.0
            for field in FIELDS:
                vals = _clean(field, [metrics.get(m["symbol"], {}).get(field)
                                      for m in group])
                if len(vals) < min_peers:
                    continue
                rows.append({
                    "level": level,
                    "key": key,
                    "cohort": cohort,
                    "field": field,
                    "value": round(statistics.median(vals), 6),
                    "peer_count": len(vals),
                    "min_market_cap": float(floor_mcap),
                })
    return rows


# ── Storage ─────────────────────────────────────────────────────────────────
#
# A brand-new table, so CREATE TABLE IF NOT EXISTS is sufficient — the
# schema-guard requirement applies to new COLUMNS on existing tables, which
# neither create_all nor Railway's predeploy Alembic reliably applies.

_DDL = """
CREATE TABLE IF NOT EXISTS regional_comps (
    exchange       TEXT NOT NULL,
    level          TEXT NOT NULL,
    key            TEXT NOT NULL,
    cohort         TEXT NOT NULL,
    field          TEXT NOT NULL,
    value          REAL NOT NULL,
    peer_count     INTEGER NOT NULL,
    min_market_cap REAL,
    computed_at    TEXT NOT NULL,
    PRIMARY KEY (exchange, level, key, cohort, field)
)
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_regional_comps_lookup "
    "ON regional_comps(exchange, level, key, cohort)",
]

_tables_ready_key: Optional[tuple] = None


def _ensure_table() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.execute_script(";".join([_DDL] + _INDEXES))
        _tables_ready_key = key
    except Exception as exc:
        logger.warning("regional_comps _ensure_table: %s", exc)


_SAVE_SQL = """
INSERT INTO regional_comps
    (exchange, level, key, cohort, field, value, peer_count,
     min_market_cap, computed_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(exchange, level, key, cohort, field) DO UPDATE SET
    value = excluded.value,
    peer_count = excluded.peer_count,
    min_market_cap = excluded.min_market_cap,
    computed_at = excluded.computed_at
"""


def save_comps(exchange: str, rows: list[dict], computed_at: str) -> int:
    if not rows:
        return 0
    _ensure_table()
    _db.executemany(_SAVE_SQL, [
        [exchange, r["level"], r["key"], r.get("cohort", "all"), r["field"],
         float(r["value"]), int(r["peer_count"]),
         float(r.get("min_market_cap") or 0.0), computed_at]
        for r in rows
    ])
    return len(rows)


def _age_days(computed_at: str) -> Optional[float]:
    try:
        ts = datetime.fromisoformat(str(computed_at))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except Exception:
        return None


def load_comps(exchange: str, level: str, key: str, cohort: str = "all",
               max_age_days: float = MAX_AGE_DAYS) -> dict[str, dict]:
    """{field: {value, peer_count, min_market_cap, ...}} for one grouping+cohort.

    Empty when nothing is stored or every row is staler than max_age_days.
    """
    if not key:
        return {}
    _ensure_table()
    try:
        rows = _db.query(
            "SELECT field, value, peer_count, min_market_cap, computed_at "
            "FROM regional_comps "
            "WHERE exchange = ? AND level = ? AND key = ? AND cohort = ?",
            [exchange, level, key, cohort],
        )
    except Exception as exc:
        logger.warning("regional_comps load failed: %s", exc)
        return {}

    out: dict[str, dict] = {}
    for row in rows or []:
        # PG returns dicts, SQLite returns Row — index positionally for both.
        field, value, peers, min_mcap, computed = (
            row[0], row[1], row[2], row[3], row[4])
        age = _age_days(computed)
        if age is not None and age > max_age_days:
            continue
        out[field] = {
            "value": float(value),
            "peer_count": int(peers),
            "min_market_cap": float(min_mcap or 0.0),
            "computed_at": computed,
            "age_days": None if age is None else round(age, 2),
        }
    return out


# ── Public read: the resolution ladder ──────────────────────────────────────

def get_regional_multiples(
    exchange: str,
    industry: Optional[str] = None,
    sector: Optional[str] = None,
    market_cap: Optional[float] = None,
    max_age_days: float = MAX_AGE_DAYS,
) -> dict[str, dict]:
    """Live comps for one stock, resolved field by field.

    Returns {field: {value, basis, cohort, peer_count}}. Fields with no
    qualifying comp set are simply absent — the caller keeps its static
    value for those, and can tell the difference.

    Rungs, tried in order and applied per field:

        industry / large   (only when the target is itself a large name)
        industry / all
        sector   / large   (likewise)
        sector   / all

    Passing `market_cap` is what unlocks the "large" rungs. Without it the
    caller gets whole-grouping medians, which for a mega-cap on HKEX can be
    a comp set two orders of magnitude smaller than the target.
    """
    resolved: dict[str, dict] = {}

    rungs: list[tuple[str, str, str, int]] = []
    if market_cap:
        rungs.append(("industry", industry or "", "large", MIN_INDUSTRY_PEERS))
    rungs.append(("industry", industry or "", "all", MIN_INDUSTRY_PEERS))
    if market_cap:
        rungs.append(("sector", sector or "", "large", MIN_SECTOR_PEERS))
    rungs.append(("sector", sector or "", "all", MIN_SECTOR_PEERS))

    for level, key, cohort, floor in rungs:
        if not key:
            continue
        for field, row in load_comps(exchange, level, key, cohort,
                                     max_age_days).items():
            if field in resolved or row["peer_count"] < floor:
                continue
            # A "large" cohort only applies to a target that belongs in it.
            if cohort == "large" and market_cap:
                if float(market_cap) < row.get("min_market_cap", 0.0):
                    continue
            resolved[field] = {
                "value": row["value"],
                "basis": level,
                "cohort": cohort,
                "peer_count": row["peer_count"],
            }
    return resolved


# ── Public refresh ──────────────────────────────────────────────────────────

def refresh_regional_comps(
    exchange: str,
    max_workers: int = 8,
    persist: bool = True,
) -> dict:
    """Rebuild every industry and sector median for one exchange.

    HKSE is refreshed before SES so the Singapore pass can exclude companies
    that are primary-listed in Hong Kong.
    """
    if exchange not in EXCHANGES:
        return {"error": f"unsupported market {exchange!r}"}

    t0 = time.time()
    universe = fetch_universe(exchange)
    if not universe:
        return {"error": "screener_fetch_failed", "exchange": exchange}
    raw_count = len(universe)

    exclude: set[str] = set()
    if exchange == "SES":
        # Drop SGX depositary receipts over HK-primary companies so mainland
        # China risk pricing cannot leak into Singapore comps.
        hk = fetch_universe("HKSE")
        exclude = {normalize_name(r["name"]) for r in hk}

    universe = dedupe_universe(universe, exclude_names=exclude)
    industry_baskets, sector_baskets, symbols = build_baskets(universe)

    metrics = _fetch_all(symbols, max_workers=max_workers)

    rows = compute_medians(industry_baskets, metrics, "industry", MIN_INDUSTRY_PEERS)
    rows += compute_medians(sector_baskets, metrics, "sector", MIN_SECTOR_PEERS)

    computed_at = datetime.now(timezone.utc).isoformat()
    written = 0
    if persist:
        try:
            written = save_comps(exchange, rows, computed_at)
        except Exception as exc:
            logger.exception("regional_comps persist failed: %s", exc)

    return {
        "exchange": exchange,
        "computed_at": computed_at,
        "universe_raw": raw_count,
        "universe_deduped": len(universe),
        "excluded_cross_listings": raw_count - len(universe),
        "industries": len(industry_baskets),
        "sectors": len(sector_baskets),
        "symbols_fetched": len(symbols),
        "symbols_with_data": len(metrics),
        "industry_rows": sum(1 for r in rows if r["level"] == "industry"),
        "sector_rows": sum(1 for r in rows if r["level"] == "sector"),
        "persisted": written,
        "elapsed_sec": round(time.time() - t0, 1),
    }


def latest_refresh_age_days(exchange: str) -> Optional[float]:
    """Age of the freshest stored row, or None when nothing is stored."""
    _ensure_table()
    try:
        row = _db.query_one(
            "SELECT MAX(computed_at) FROM regional_comps WHERE exchange = ?",
            [exchange],
        )
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    return _age_days(row[0])


# ── Per-ticker classification ───────────────────────────────────────────────

# FMP /profile is the only place the exchange + FMP industry string live for
# an arbitrary ticker. One call per ticker per process is negligible next to
# the rest of a pipeline run, and the result never changes within a run.
_CLASSIFICATION_CACHE: dict[str, dict] = {}


def get_fmp_classification(ticker: str) -> dict:
    """{exchange, sector, industry, name, currency} for a ticker.

    Exchange is FMP's short code — "HKSE", "SES", "NASDAQ", ... — which is
    what keys the regional_comps table. Returns {} on any failure so callers
    fall straight through to their static tables.
    """
    if not ticker:
        return {}
    key = ticker.strip().upper()
    if key in _CLASSIFICATION_CACHE:
        return _CLASSIFICATION_CACHE[key]

    result: dict = {}
    try:
        from src.tools.intl_provider import detect_market, fmp_symbol
        market = detect_market(key)
        symbol = fmp_symbol(key, market) if market else key
        rows = _fmp_get(f"{_STABLE}/profile", {"symbol": symbol}, api_key=None)
        if isinstance(rows, list) and rows:
            row = rows[0]
            result = {
                "exchange": (row.get("exchangeShortName")
                             or row.get("exchange") or "").strip(),
                "sector": (row.get("sector") or "").strip(),
                "industry": (row.get("industry") or "").strip(),
                "name": (row.get("companyName") or "").strip(),
                "currency": (row.get("currency") or "").strip(),
            }
    except Exception as exc:
        logger.debug("get_fmp_classification(%s) failed: %s", ticker, exc)
        result = {}

    _CLASSIFICATION_CACHE[key] = result
    return result


# ── Weekly refresh schedule ─────────────────────────────────────────────────
#
# Default fire: Saturday 01:00 UTC = Saturday 09:00 Singapore Time. The
# weekend is the right slot — no market is open, the refresh spends ~15k FMP
# calls across the three markets, and nothing else competes for the token
# bucket in api.py.
#
# Env overrides follow the same convention as the other schedulers:
#   REGIONAL_COMPS_SCHEDULER_DISABLED=1   — skip entirely
#   REGIONAL_COMPS_HOUR_UTC=1             — UTC fire hour (default 1 = SGT 09:00)
#   REGIONAL_COMPS_WEEKDAY=5              — 0=Monday .. 5=Saturday

_FIRE_HOUR_UTC = int(os.environ.get("REGIONAL_COMPS_HOUR_UTC", "1"))
_FIRE_WEEKDAY = int(os.environ.get("REGIONAL_COMPS_WEEKDAY", "5"))   # Saturday

#: A refresh younger than this counts as "already ran this week".
_IDEMPOTENCY_HOURS = 6 * 24


def seconds_until_next_fire() -> float:
    """Seconds until the next Saturday 01:00 UTC boundary."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    target = now.replace(hour=_FIRE_HOUR_UTC, minute=0, second=0, microsecond=0)
    target += timedelta(days=(_FIRE_WEEKDAY - target.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


def already_ran_this_week() -> bool:
    """True when every market has a refresh inside the idempotency window.

    Partial completion counts as NOT done, so a run that died halfway
    through (say after HKSE but before US) is retried rather than skipped.
    """
    for market in EXCHANGES:
        age = latest_refresh_age_days(market)
        if age is None or age * 24 >= _IDEMPOTENCY_HOURS:
            return False
    return True


def run_weekly_refresh(force: bool = False) -> Optional[dict]:
    """Refresh every market. Returns None when the idempotency gate skips it.

    Markets run in order so the SES pass can exclude companies that are
    primary-listed in Hong Kong.
    """
    if not force and already_ran_this_week():
        logger.info("[regional_comps] already refreshed this week — skipping")
        return None
    results = {}
    for market in EXCHANGES:
        try:
            results[market] = refresh_regional_comps(market)
        except Exception as exc:
            logger.exception("[regional_comps] %s refresh failed: %s", market, exc)
            results[market] = {"error": str(exc)[:200]}
    return results


if __name__ == "__main__":
    # Manual seed:
    #   .\.venv\Scripts\python.exe -m src.data.regional_comps
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    for _ex in EXCHANGES:
        print(f"\n=== {_ex} ===")
        for k, v in refresh_regional_comps(_ex).items():
            print(f"  {k:26s} {v}")
