"""Durable store for ticker news, and the ranking that decides what is shown.

Why a DB table and not Redis: the repo caches in the database deliberately, so
a web replica and the worker read the same rows. Redis here is the fan-out bus
(news_bus.py), not the record.

Why a ranking and not a filter: the previous news endpoint kept only an
allow-list of authoritative domains. That works for a US ticker on FMP and
erases Asia entirely -- HK news arrives from AKShare and SG from yfinance, and
none of those publishers is Bloomberg. Provenance is recorded per item as a
TIER, the caller sorts by it, and nothing is silently dropped.

Dedupe is by a stable content id rather than by position in a feed, following
src/triggers/detectors.py::new_edgar_filing, which keys on the accession number
for the same reason: a feed that reorders must not re-announce what it already
said.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from src.data import db as _db

logger = logging.getLogger(__name__)

# ── Source tiers ───────────────────────────────────────────────────────────
#: Highest first. A tier is provenance, not quality-of-writing: `regulatory`
#: outranks everything because an HKEXnews or EDGAR filing is the company
#: speaking, not a report of it.
TIER_REGULATORY = "regulatory"
TIER_AUTHORITATIVE = "authoritative"
TIER_AGGREGATOR = "aggregator"

_TIER_RANK = {TIER_REGULATORY: 0, TIER_AUTHORITATIVE: 1, TIER_AGGREGATOR: 2}

#: Moved here from app/backend/routes/analysis.py, where it was an allow-list.
_AUTHORITATIVE_DOMAINS = frozenset({
    "bloomberg.com", "ft.com", "reuters.com", "wsj.com", "barrons.com",
    "cnbc.com", "marketwatch.com", "investopedia.com", "morningstar.com",
    "businessinsider.com", "forbes.com", "nytimes.com", "economist.com",
    "financialtimes.com", "ap.org", "apnews.com", "nasdaq.com", "nyse.com",
    "prnewswire.com", "businesswire.com", "globenewswire.com",
})

#: Publishers that are company or regulator primary sources.
_REGULATORY_MARKERS = frozenset({
    "sec.gov", "hkexnews.hk", "hkex.com.hk", "sgx.com", "ir.", "investor.",
})


def classify_tier(site: str | None) -> str:
    """Provenance tier for a publisher domain.

    Unknown publishers are `aggregator`, not rejected. That is the whole point
    of the change: an unrecognised Chinese or Singaporean outlet is the only
    coverage those tickers have.
    """
    s = (site or "").lower()
    if any(m in s for m in _REGULATORY_MARKERS):
        return TIER_REGULATORY
    if any(d in s for d in _AUTHORITATIVE_DOMAINS):
        return TIER_AUTHORITATIVE
    return TIER_AGGREGATOR


def make_item_id(url: str | None, title: str, published_at: str) -> str:
    """Stable identity for a news item.

    The URL when there is one -- two feeds carrying the same story agree on it
    far more often than they agree on a headline. Falling back to title+time
    means a publisher who rewrites a headline in place produces a second row;
    that is the lesser fault against dropping a genuine follow-up story.
    """
    basis = (url or "").strip() or f"{title.strip()}|{published_at.strip()}"
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()


# ── Schema ─────────────────────────────────────────────────────────────────
_DDL_ITEMS = """
CREATE TABLE IF NOT EXISTS news_items (
    item_id      TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    published_at TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT,
    site         TEXT,
    image        TEXT,
    summary      TEXT,
    source_tier  TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
)
"""
_DDL_ITEMS_IDX = """
CREATE INDEX IF NOT EXISTS idx_news_items_ticker_published
    ON news_items (ticker, published_at DESC)
"""
#: When each ticker was last polled. Kept separate from the items so a ticker
#: that genuinely has no news is still recorded as having been checked --
#: otherwise every empty ticker looks like a cold cache forever and is
#: re-fetched on every page open, which is the cost this store exists to avoid.
_DDL_POLL = """
CREATE TABLE IF NOT EXISTS news_poll_state (
    ticker      TEXT PRIMARY KEY,
    polled_at   TEXT NOT NULL,
    item_count  INTEGER NOT NULL,
    last_error  TEXT
)
"""

_tables_ready_key: Optional[tuple] = None


def _ensure_tables() -> None:
    """Create the news tables if missing. Memoised per database, matching
    screener_service._ensure_tables()."""
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.ensure_table(_DDL_ITEMS)
        _db.ensure_table(_DDL_POLL)
        _db.execute(_DDL_ITEMS_IDX)
        _tables_ready_key = key
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store._ensure_tables: %s", exc)


def _upsert_sql(table: str, conflict_col: str, columns: list[str]) -> str:
    ph = ", ".join("?" * len(columns))
    if _db.is_postgres():
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns
                            if c != conflict_col)
        return (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({ph}) "
                f"ON CONFLICT ({conflict_col}) DO UPDATE SET {updates}")
    return (f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
            f"VALUES ({ph})")


_ITEM_COLUMNS = ["item_id", "ticker", "published_at", "title", "url", "site",
                 "image", "summary", "source_tier", "fetched_at"]


def _row_to_dict(row) -> dict:
    """Rows are name-addressable in both modes (sqlite3.Row / dict) but NOT
    positional -- see the contract in src/data/db.py."""
    return {c: row[c] for c in _ITEM_COLUMNS}


def upsert_items(items: Iterable[dict]) -> int:
    """Write items, returning how many were NEW.

    The count is the number of ids not already present, which is what the bus
    should announce -- re-writing an item whose headline was corrected is not
    news, and publishing it again would surface it twice on an open page.
    """
    _ensure_tables()
    items = [i for i in items if i.get("item_id") and i.get("ticker")]
    if not items:
        return 0
    ids = [i["item_id"] for i in items]
    existing: set[str] = set()
    try:
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start:chunk_start + 500]
            ph = ",".join("?" * len(chunk))
            rows = _db.query(
                f"SELECT item_id FROM news_items WHERE item_id IN ({ph})", chunk)
            existing.update(r["item_id"] for r in (rows or []))
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.upsert_items lookup: %s", exc)

    now = datetime.now(timezone.utc).isoformat()
    payload = []
    for i in items:
        payload.append([
            i["item_id"], i["ticker"].upper(), i.get("published_at") or now,
            i.get("title") or "", i.get("url"), i.get("site"), i.get("image"),
            i.get("summary"),
            i.get("source_tier") or classify_tier(i.get("site")), now,
        ])
    try:
        _db.executemany(
            _upsert_sql("news_items", "item_id", _ITEM_COLUMNS), payload)
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.upsert_items write: %s", exc)
        return 0
    return sum(1 for i in items if i["item_id"] not in existing)


def get_items(ticker: str, limit: int = 20) -> list[dict]:
    """Newest first, then by provenance tier. Never raises."""
    _ensure_tables()
    try:
        rows = _db.query(
            "SELECT " + ", ".join(_ITEM_COLUMNS) + " FROM news_items "
            "WHERE ticker = ? ORDER BY published_at DESC LIMIT ?",
            [(ticker or "").upper(), int(limit) * 3],
        ) or []
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.get_items: %s", exc)
        return []
    out = [_row_to_dict(r) for r in rows]
    # Newest first, to the MINUTE. Tier breaks exact ties only.
    #
    # This used to sort on the DATE and let tier decide the rest, which was a
    # reasonable reading of "a filing matters more than a recap" while every
    # provider truncated its timestamps to midnight. Now that the minute
    # survives (CompanyNews.published_at), that rule actively misorders: a
    # 17:30 filing ranked above a 23:24 story published six hours later. A
    # feed whose top item is not the newest one is not a feed.
    out.sort(key=lambda i: (i["published_at"],
                            -_TIER_RANK.get(i["source_tier"], 9)), reverse=True)
    return out[:limit]


def get_items_multi(tickers: list[str], limit: int = 50) -> list[dict]:
    """Merged feed across tickers, newest first. For the watchlist page."""
    _ensure_tables()
    syms = [t.upper() for t in (tickers or []) if t]
    if not syms:
        return []
    try:
        ph = ",".join("?" * len(syms))
        rows = _db.query(
            "SELECT " + ", ".join(_ITEM_COLUMNS) + " FROM news_items "
            f"WHERE ticker IN ({ph}) ORDER BY published_at DESC LIMIT ?",
            [*syms, int(limit)],
        ) or []
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.get_items_multi: %s", exc)
        return []
    return [_row_to_dict(r) for r in rows]


def set_summary(item_id: str, summary: str) -> bool:
    """Persist an on-demand summary so it is paid for once."""
    _ensure_tables()
    try:
        return _db.execute(
            "UPDATE news_items SET summary = ? WHERE item_id = ?",
            [summary, item_id]) > 0
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.set_summary: %s", exc)
        return False


def get_item(item_id: str) -> Optional[dict]:
    _ensure_tables()
    try:
        row = _db.query_one(
            "SELECT " + ", ".join(_ITEM_COLUMNS) +
            " FROM news_items WHERE item_id = ?", [item_id])
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.get_item: %s", exc)
        return None
    return _row_to_dict(row) if row else None


# ── Poll bookkeeping ───────────────────────────────────────────────────────

def mark_polled(ticker: str, item_count: int, error: str | None = None) -> None:
    _ensure_tables()
    try:
        _db.execute(
            _upsert_sql("news_poll_state", "ticker",
                        ["ticker", "polled_at", "item_count", "last_error"]),
            [(ticker or "").upper(),
             datetime.now(timezone.utc).isoformat(), int(item_count),
             (error or "")[:300] or None],
        )
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.mark_polled: %s", exc)


def is_fresh(ticker: str, max_age_minutes: int) -> bool:
    """True when this ticker was polled recently, regardless of whether the
    poll FOUND anything. A ticker with no coverage is still a ticker that has
    been checked."""
    _ensure_tables()
    try:
        row = _db.query_one(
            "SELECT polled_at FROM news_poll_state WHERE ticker = ?",
            [(ticker or "").upper()])
    except Exception:                                      # noqa: BLE001
        return False
    if not row or not row["polled_at"]:
        return False
    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=max_age_minutes)).isoformat()
    return str(row["polled_at"]) > cutoff


def prune(older_than_days: int = 45) -> int:
    """Drop aged items. News has no archival value here and the table is the
    hot path for every page open."""
    _ensure_tables()
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=older_than_days)).isoformat()
    try:
        return _db.execute(
            "DELETE FROM news_items WHERE published_at < ?", [cutoff])
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_store.prune: %s", exc)
        return 0
