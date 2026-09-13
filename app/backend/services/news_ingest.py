"""Pull news for watched tickers, store what is new, announce it once.

Every adapter here wraps something that already exists. `get_company_news` and
`get_press_releases` in src/tools/api.py already route by market -- US to FMP,
HK to AKShare, SG to yfinance -- and `search_hkex_announcements` already
scrapes HKEXnews. None of that needed rebuilding; what was missing was
somewhere to put the result and something to tell a page about it.

Adding a commercial wire later means adding one `_Adapter` and putting it
first in `_ADAPTERS`. Nothing else in the pipeline changes, which is the point
of the shape: the sources available today are cheap and slow, and the design
should not have to be revisited when that stops being true.

Cost control lives in `watched_tickers()`. Polling the whole universe would be
thousands of upstream calls an hour for pages nobody has open.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from app.backend.services import news_bus, news_store

logger = logging.getLogger(__name__)

#: Skip a ticker polled more recently than this. The floor on upstream calls.
DEFAULT_MAX_AGE_MINUTES = 15

#: How many items to ask each adapter for.
_PER_SOURCE_LIMIT = 25


def _iso(value) -> Optional[str]:
    """Normalise a publisher timestamp to a sortable "YYYY-MM-DD HH:MM:SS".

    Adapters disagree, and not in small ways. FMP gives "2026-09-13 05:36:52",
    yfinance "2026-09-13T05:36:52Z", HKEXnews "11/09/2026 17:30", and
    get_press_releases hands back HK dates as "11-09-2026" -- DAY first.

    Returns None when the value cannot be read, and the caller DROPS the item.
    That is deliberate. The first version stored the unparsed string, so
    "11-09-2026" sorted below every ISO date and the day's filings landed at
    the bottom of the feed; fabricating a timestamp instead would have put
    stale ones at the top. An item whose time cannot be read cannot be placed
    in a feed ordered by time, and the next poll will offer it again anyway.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M",
                "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
                "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    try:                       # ISO with an offset, e.g. "...+08:00"
        return (datetime.fromisoformat(s.replace("Z", "+00:00"))
                .strftime("%Y-%m-%d %H:%M:%S"))
    except ValueError:
        logger.debug("news_ingest: unparseable timestamp %r", s[:40])
        return None


def _item(ticker: str, title: str, url: str | None, published, site: str | None,
          image: str | None = None, tier: str | None = None) -> Optional[dict]:
    title = (title or "").strip()
    if not title:
        return None
    published_at = _iso(published)
    if not published_at:
        return None            # unreadable time -> unplaceable in the feed
    return {
        "item_id": news_store.make_item_id(url, title, published_at),
        "ticker": ticker.upper(),
        "published_at": published_at,
        "title": title,
        "url": (url or "").strip() or None,
        "site": (site or "").strip() or None,
        "image": image,
        "summary": None,
        "source_tier": tier or news_store.classify_tier(site),
    }


# ── Adapters ───────────────────────────────────────────────────────────────

def _adapter_company_news(ticker: str) -> list[dict]:
    """src/tools/api.py::get_company_news — already market-routed."""
    from src.tools.api import get_company_news
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = get_company_news(ticker, end, start, limit=_PER_SOURCE_LIMIT) or []
    out = []
    for r in rows:
        out.append(_item(
            ticker, getattr(r, "title", ""), getattr(r, "url", None),
            # published_at carries the minute; date is date-only by contract.
            getattr(r, "published_at", None) or getattr(r, "date", None),
            getattr(r, "source", None)))
    return [o for o in out if o]


def _adapter_press_releases(ticker: str) -> list[dict]:
    """Company primary source. Tiered `regulatory` regardless of publisher --
    a press release IS the company speaking, whoever carries the wire."""
    from src.tools.api import get_press_releases
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = get_press_releases(ticker, end, start, limit=10) or []
    out = []
    for r in rows:
        out.append(_item(
            ticker, getattr(r, "title", ""), getattr(r, "url", None),
            getattr(r, "published_at", None) or getattr(r, "date", None),
            getattr(r, "source", None), tier=news_store.TIER_REGULATORY))
    return [o for o in out if o]


def _adapter_hkex(ticker: str) -> list[dict]:
    """HKEXnews announcements — the fastest and most authoritative HK source,
    and the only one that is the issuer rather than a report of it."""
    from src.tools.ticker_canonical import canonical_ticker
    t = canonical_ticker(ticker)
    if not t.upper().endswith(".HK"):
        return []
    from src.tools.hkex_news_api import search_hkex_announcements
    code = t.split(".")[0]
    rows = search_hkex_announcements(code, category="-2", max_results=15) or []
    out = []
    for r in rows:
        out.append(_item(ticker, r.get("title", ""), r.get("url"),
                         r.get("date"), "hkexnews.hk",
                         tier=news_store.TIER_REGULATORY))
    return [o for o in out if o]


_Adapter = Callable[[str], list]

#: Order is presentation-neutral -- items are ranked at read time by date and
#: tier. It matters only for which adapter's copy of a duplicate wins the
#: upsert, so primary sources go first.
_ADAPTERS: list[tuple[str, _Adapter]] = [
    ("press_releases", _adapter_press_releases),
    ("hkex", _adapter_hkex),
    ("company_news", _adapter_company_news),
]


# ── Ingest ─────────────────────────────────────────────────────────────────

def _better(a: dict, b: dict) -> dict:
    """Pick the richer of two records of the SAME item.

    Adapters overlap on purpose -- HK press releases and HKEXnews
    announcements are literally the same documents -- and the copies are not
    equally good. `get_press_releases` truncates its timestamp to the date
    (src/tools/api.py keeps only raw_date[:10]), while the HKEXnews adapter
    carries the real publication minute. First-wins merging discarded 17:30
    in favour of 00:00 and sank the day's filings below a week of aggregator
    posts that share their date.

    Preference order: a real time over midnight, then the stronger provenance
    tier, then whichever carries a URL.
    """
    def precise(i: dict) -> int:
        return 0 if (i.get("published_at") or "").endswith("00:00:00") else 1

    def tier(i: dict) -> int:
        return -news_store._TIER_RANK.get(i.get("source_tier"), 9)

    def linked(i: dict) -> int:
        return 1 if i.get("url") else 0

    score = lambda i: (precise(i), tier(i), linked(i))      # noqa: E731
    best = b if score(b) > score(a) else a
    other = a if best is b else b
    # Keep any field the winner lacks -- an image or summary is worth having
    # whichever copy carried it.
    for field in ("url", "image", "site", "summary"):
        if not best.get(field) and other.get(field):
            best[field] = other[field]
    return best


async def ingest_ticker(ticker: str, *, force: bool = False,
                        max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES) -> dict:
    """Fetch, store and announce for one ticker.

    Returns a per-ticker report. Never raises: one dead upstream must not stop
    the sweep, and a ticker whose adapters all fail is still recorded as
    polled so it does not become a permanent cold-cache miss.
    """
    tk = (ticker or "").strip().upper()
    if not tk:
        return {"ticker": ticker, "skipped": "empty"}
    if not force and news_store.is_fresh(tk, max_age_minutes):
        return {"ticker": tk, "skipped": "fresh"}

    collected: dict[str, dict] = {}
    errors: list[str] = []
    for name, adapter in _ADAPTERS:
        try:
            for item in adapter(tk):
                prev = collected.get(item["item_id"])
                collected[item["item_id"]] = _better(prev, item) if prev else item
        except Exception as exc:                           # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}")
            logger.warning("news_ingest %s/%s: %s", tk, name, exc)

    items = list(collected.values())
    new_count = news_store.upsert_items(items)
    news_store.mark_polled(tk, len(items), "; ".join(errors) or None)

    # Announce only the genuinely new. upsert_items returns the count rather
    # than the rows, so the newest `new_count` by publish time are taken --
    # an item that is new to the store is, by construction, recent.
    if new_count:
        items.sort(key=lambda i: i["published_at"], reverse=True)
        await news_bus.publish_many(items[:new_count])

    return {"ticker": tk, "fetched": len(items), "new": new_count,
            "errors": errors}


def watchlist_tickers(user_id: Optional[int] = None) -> list[str]:
    """What this user asked to monitor.

    The watchlist is the explicit signal -- a ticker is there because someone
    put it there -- so it leads the poll order and drives the news feed. Lives
    in its own SQLite table rather than the run archive, hence the direct
    service call rather than a db.query here.
    """
    try:
        from app.backend.services import watchlist_service
        rows = watchlist_service.get_watchlist(user_id=user_id) or []
    except Exception as exc:                               # noqa: BLE001
        logger.warning("news_ingest.watchlist_tickers: %s", exc)
        return []
    out: list[str] = []
    for row in rows:
        t = str((row or {}).get("ticker") or "").strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def watched_tickers(recent_days: int = 7, limit: int = 200,
                    user_id: Optional[int] = None) -> list[str]:
    """Tickers worth polling, most-wanted first.

    Watchlist, then holdings, then anything analysed recently. Explicitly NOT
    the whole universe -- that would be thousands of upstream calls an hour for
    pages nobody has open, and it is the main cost control in this service.
    """
    from src.data import db as _db
    out: list[str] = []
    seen: set[str] = set()

    def _add(value) -> None:
        t = (str(value or "")).strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    for t in watchlist_tickers(user_id=user_id):
        _add(t)

    for sql, params in (
        ("SELECT DISTINCT ticker FROM user_holdings", []),
        ("SELECT DISTINCT ticker FROM web_runs WHERE run_at > ?",
         [(datetime.now(timezone.utc)
           - timedelta(days=recent_days)).isoformat()]),
    ):
        if len(out) >= limit:
            break
        try:
            for row in (_db.query(sql, params) or []):
                _add(row["ticker"])
        except Exception:                                  # noqa: BLE001
            continue          # a missing table is not an error here
    return out[:limit]


async def ingest_watched(*, force: bool = False,
                         max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES,
                         limit: int = 200) -> dict:
    """One sweep over the watched set."""
    tickers = watched_tickers(limit=limit)
    started = datetime.now(timezone.utc)
    fetched = new = skipped = 0
    failures: list[str] = []
    for tk in tickers:
        report = await ingest_ticker(tk, force=force,
                                     max_age_minutes=max_age_minutes)
        if report.get("skipped"):
            skipped += 1
            continue
        fetched += report.get("fetched", 0)
        new += report.get("new", 0)
        if report.get("errors"):
            failures.append(tk)
    return {
        "tickers": len(tickers), "skipped_fresh": skipped,
        "fetched": fetched, "new": new,
        "tickers_with_errors": failures[:20],
        "elapsed_s": round(
            (datetime.now(timezone.utc) - started).total_seconds(), 1),
    }
