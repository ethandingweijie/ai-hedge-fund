"""News store, bus and ingest.

No network and no Redis: the adapters are patched and the bus is exercised
through its in-process fallback, which is the path local dev and a
Redis outage both take.
"""
import asyncio
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Each test gets its own SQLite file; never touch the real archive."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH",
                       os.path.join(tempfile.mkdtemp(), "news_test.db"))
    from app.backend.services import news_store
    news_store._tables_ready_key = None
    yield
    news_store._tables_ready_key = None


# ── Store ──────────────────────────────────────────────────────────────────

class TestTiering:
    def test_regulatory_outranks_a_wire(self):
        from app.backend.services import news_store as ns
        assert ns.classify_tier("hkexnews.hk") == ns.TIER_REGULATORY
        assert ns.classify_tier("sec.gov") == ns.TIER_REGULATORY
        assert ns.classify_tier("reuters.com") == ns.TIER_AUTHORITATIVE

    def test_an_unknown_publisher_is_kept_not_dropped(self):
        """The old endpoint kept an allow-list, which erased Asia entirely --
        HK news comes from AKShare and SG from yfinance, and neither is
        Bloomberg."""
        from app.backend.services import news_store as ns
        assert ns.classify_tier("gurufocus.com") == ns.TIER_AGGREGATOR
        assert ns.classify_tier(None) == ns.TIER_AGGREGATOR


class TestItemIdentity:
    def test_the_url_is_the_identity_when_there_is_one(self):
        from app.backend.services import news_store as ns
        a = ns.make_item_id("http://x/1", "One headline", "2026-09-13 01:00:00")
        b = ns.make_item_id("http://x/1", "Rewritten headline", "2026-09-13 09:00:00")
        assert a == b, "two feeds carrying one story agree on the URL"

    def test_without_a_url_title_and_time_identify_it(self):
        from app.backend.services import news_store as ns
        a = ns.make_item_id(None, "Same", "2026-09-13 01:00:00")
        b = ns.make_item_id("", "Same", "2026-09-13 01:00:00")
        c = ns.make_item_id(None, "Same", "2026-09-13 02:00:00")
        assert a == b and a != c


class TestStore:
    def _item(self, **kw):
        from app.backend.services import news_store as ns
        base = {"ticker": "0700.HK", "published_at": "2026-09-11 17:30:00",
                "title": "T", "url": "http://x/1", "site": "hkexnews.hk"}
        base.update(kw)
        base["item_id"] = ns.make_item_id(base["url"], base["title"],
                                          base["published_at"])
        return base

    def test_only_genuinely_new_items_are_counted(self):
        """The count drives what the bus announces: re-writing a corrected
        headline must not surface it twice on an open page."""
        from app.backend.services import news_store as ns
        items = [self._item(), self._item(url="http://x/2", title="U")]
        assert ns.upsert_items(items) == 2
        assert ns.upsert_items(items) == 0

    def test_a_filing_leads_the_day_it_shares_with_an_aggregator(self):
        from app.backend.services import news_store as ns
        ns.upsert_items([
            self._item(url="http://x/a", title="aggregated",
                       site="gurufocus.com", published_at="2026-09-11 00:00:00"),
            self._item(url="http://x/b", title="filing",
                       site="hkexnews.hk", published_at="2026-09-11 17:30:00"),
        ])
        got = ns.get_items("0700.HK", 5)
        assert [g["title"] for g in got] == ["filing", "aggregated"]

    def test_recency_still_beats_tier_across_days(self):
        """A week-old Reuters piece must not outrank this morning's filing."""
        from app.backend.services import news_store as ns
        ns.upsert_items([
            self._item(url="http://x/old", title="old wire",
                       site="reuters.com", published_at="2026-09-04 10:00:00"),
            self._item(url="http://x/new", title="new aggregate",
                       site="gurufocus.com", published_at="2026-09-11 10:00:00"),
        ])
        assert ns.get_items("0700.HK", 5)[0]["title"] == "new aggregate"

    def test_a_ticker_with_no_news_still_counts_as_polled(self):
        """Otherwise an uncovered ticker looks like a cold cache forever and
        is re-fetched on every page open -- the cost this store exists to
        avoid."""
        from app.backend.services import news_store as ns
        assert ns.is_fresh("D05.SI", 60) is False
        ns.mark_polled("D05.SI", 0)
        assert ns.is_fresh("D05.SI", 60) is True
        assert ns.is_fresh("D05.SI", 0) is False

    def test_summary_is_persisted_so_it_is_paid_for_once(self):
        from app.backend.services import news_store as ns
        item = self._item()
        ns.upsert_items([item])
        assert ns.set_summary(item["item_id"], "one line") is True
        assert ns.get_item(item["item_id"])["summary"] == "one line"


# ── Timestamps ─────────────────────────────────────────────────────────────

class TestTimestampNormalisation:
    @pytest.mark.parametrize("raw,expect", [
        ("2026-09-13 05:36:52", "2026-09-13 05:36:52"),      # FMP
        ("2026-09-13T05:36:52Z", "2026-09-13 05:36:52"),     # yfinance
        ("11/09/2026 17:30", "2026-09-11 17:30:00"),         # HKEXnews
        ("11-09-2026", "2026-09-11 00:00:00"),               # get_press_releases
        ("2026-09-13", "2026-09-13 00:00:00"),
    ])
    def test_every_adapter_dialect_normalises(self, raw, expect):
        from app.backend.services import news_ingest as ni
        assert ni._iso(raw) == expect

    def test_day_first_is_not_read_as_month_first(self):
        """"11-09-2026" is 11 September. Read as month-first it becomes
        9 November -- a date two months in the future, which would pin the item
        to the top of the feed forever."""
        from app.backend.services import news_ingest as ni
        assert ni._iso("11-09-2026").startswith("2026-09-11")

    def test_an_unreadable_time_drops_the_item(self):
        """Storing the raw string sorted "11-09-2026" below every ISO date and
        buried the day's filings; fabricating `now` would have floated stale
        ones to the top. Neither is acceptable in a feed ordered by time."""
        from app.backend.services import news_ingest as ni
        assert ni._iso("last Tuesday") is None
        assert ni._item("0700.HK", "T", "http://x", "last Tuesday", "s") is None


class TestDuplicateMerge:
    def test_the_precise_timestamp_wins(self):
        """HK press releases and HKEXnews announcements are the same
        documents, but get_press_releases truncates the time."""
        from app.backend.services import news_ingest as ni
        coarse = {"published_at": "2026-09-11 00:00:00", "source_tier":
                  "regulatory", "url": "http://x/1", "title": "T"}
        precise = {"published_at": "2026-09-11 17:30:00", "source_tier":
                   "regulatory", "url": "http://x/1", "title": "T"}
        assert ni._better(coarse, precise)["published_at"].endswith("17:30:00")
        assert ni._better(precise, coarse)["published_at"].endswith("17:30:00")

    def test_fields_the_winner_lacks_are_carried_over(self):
        from app.backend.services import news_ingest as ni
        a = {"published_at": "2026-09-11 17:30:00", "source_tier": "regulatory",
             "url": "http://x/1", "image": None, "title": "T"}
        b = {"published_at": "2026-09-11 00:00:00", "source_tier": "regulatory",
             "url": "http://x/1", "image": "http://img", "title": "T"}
        assert ni._better(a, b)["image"] == "http://img"


# ── Bus ────────────────────────────────────────────────────────────────────

class TestBusWithoutRedis:
    def test_fans_in_across_tickers_and_excludes_the_rest(self, monkeypatch):
        from app.backend.services import news_bus
        monkeypatch.setattr(news_bus, "redis_ready",
                            lambda: asyncio.sleep(0, result=False))
        news_bus.reset_local_state()

        async def scenario():
            got, ready = [], asyncio.Event()

            async def reader():
                agen = news_bus.iter_items(["0700.HK", "D05.SI"])
                first = asyncio.ensure_future(agen.__anext__())
                await asyncio.sleep(0.05)
                ready.set()
                got.append(await asyncio.wait_for(first, timeout=3))
                got.append(await asyncio.wait_for(agen.__anext__(), timeout=3))

            task = asyncio.create_task(reader())
            await ready.wait()
            await asyncio.sleep(0.05)
            await news_bus.publish("0700.HK", {"ticker": "0700.HK", "title": "hk"})
            await news_bus.publish("AAPL", {"ticker": "AAPL", "title": "nope"})
            await news_bus.publish("D05.SI", {"ticker": "D05.SI", "title": "sg"})
            await asyncio.wait_for(task, timeout=5)
            return [g["title"] for g in got]

        titles = asyncio.run(scenario())
        assert titles == ["hk", "sg"]
        assert "nope" not in titles

    def test_subscribers_are_cleaned_up_on_exit(self, monkeypatch):
        from app.backend.services import news_bus
        monkeypatch.setattr(news_bus, "redis_ready",
                            lambda: asyncio.sleep(0, result=False))
        news_bus.reset_local_state()

        async def scenario():
            agen = news_bus.iter_items(["X"])
            fut = asyncio.ensure_future(agen.__anext__())
            await asyncio.sleep(0.05)
            await news_bus.publish("X", {"ticker": "X", "title": "t"})
            await asyncio.wait_for(fut, timeout=3)
            await agen.aclose()

        asyncio.run(scenario())
        assert news_bus._local_hubs.get("X") in (None, set())

    def test_publish_survives_an_unreachable_redis(self, monkeypatch):
        """Fails to local-only rather than raising into the ingest sweep."""
        from app.backend.services import news_bus
        news_bus.reset_local_state()
        monkeypatch.setattr(news_bus, "redis_ready",
                            lambda: asyncio.sleep(0, result=True))

        async def boom():
            raise RuntimeError("redis gone")

        monkeypatch.setattr(news_bus, "get_redis", boom)
        asyncio.run(news_bus.publish("X", {"ticker": "X", "title": "t"}))
        assert len(news_bus._local_buf["X"]) == 1


# ── Ingest ─────────────────────────────────────────────────────────────────

class TestIngest:
    def test_a_dead_adapter_does_not_stop_the_others(self, monkeypatch):
        from app.backend.services import news_ingest as ni

        def dead(_t):
            raise RuntimeError("upstream down")

        def alive(t):
            return [ni._item(t, "survivor", "http://x/1",
                             "2026-09-11 10:00:00", "reuters.com")]

        monkeypatch.setattr(ni, "_ADAPTERS", [("dead", dead), ("alive", alive)])
        report = asyncio.run(ni.ingest_ticker("AAPL", force=True))
        assert report["new"] == 1
        assert report["errors"] == ["dead: RuntimeError"]

    def test_a_fresh_ticker_is_skipped(self, monkeypatch):
        from app.backend.services import news_ingest as ni, news_store as ns
        monkeypatch.setattr(ni, "_ADAPTERS", [])
        ns.mark_polled("AAPL", 0)
        assert asyncio.run(ni.ingest_ticker("AAPL"))["skipped"] == "fresh"
        assert asyncio.run(ni.ingest_ticker("AAPL", force=True)).get("skipped") is None

    def test_only_new_items_reach_the_bus(self, monkeypatch):
        from app.backend.services import news_bus, news_ingest as ni
        sent = []

        async def capture(items):
            items = list(items)
            sent.extend(items)
            return len(items)

        monkeypatch.setattr(ni.news_bus, "publish_many", capture)
        monkeypatch.setattr(ni, "_ADAPTERS", [("one", lambda t: [
            ni._item(t, "headline", "http://x/1", "2026-09-11 10:00:00", "s")])])
        asyncio.run(ni.ingest_ticker("AAPL", force=True))
        asyncio.run(ni.ingest_ticker("AAPL", force=True))
        assert len(sent) == 1, "the second sweep re-saw the item, not re-announced it"
        news_bus.reset_local_state()

    def test_watched_set_is_bounded(self, monkeypatch):
        """Polling the whole universe would be thousands of upstream calls an
        hour for pages nobody has open."""
        from app.backend.services import news_ingest as ni
        assert ni.watched_tickers(limit=5) == ni.watched_tickers(limit=5)[:5]
