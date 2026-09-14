"""The scheduled news sweep: tiering, slot arithmetic, and the gate.

The gate is the part worth guarding. regional_comps used an AGE gate -- "was
anything refreshed in the last six days" -- and a run that finished late
satisfied the following slot and skipped it, silently halving a weekly cadence
(fixed in e668e9c). News runs every fifteen minutes, so the same mistake would
be far less visible and far more costly.
"""
import datetime as dt

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Own SQLite file per test; never touch the real archive."""
    import os
    import tempfile
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH",
                       os.path.join(tempfile.mkdtemp(), "sched_test.db"))
    from app.backend.services import news_store
    news_store._tables_ready_key = None
    yield
    news_store._tables_ready_key = None

from app.backend import scheduler_service as sched
from app.backend.services import news_ingest as ni


class TestTiering:
    @pytest.mark.parametrize("ticker,tier", [
        ("0700.HK", ni.TIER_FAST), ("D05.SI", ni.TIER_FAST),
        ("AAPL", ni.TIER_SLOW), ("BRK.B", ni.TIER_SLOW), ("", ni.TIER_SLOW),
    ])
    def test_only_asian_venues_are_fast(self, ticker, tier):
        """FMP is the only US source and publishes ~4.4 hours late, so no
        cadence makes a US ticker fast. HKEXnews and yfinance are near-real
        time, which is what the short interval is for."""
        assert ni.tier_of(ticker) == tier

    def test_both_tiers_are_registered(self):
        names = [s.name for s in sched.build_schedules()]
        assert "news_fast" in names and "news_slow" in names

    def test_the_fast_tier_runs_more_often_than_the_slow_one(self):
        assert sched.NEWS_FAST_MINUTES < sched.NEWS_SLOW_MINUTES


class TestSlotArithmetic:
    def test_a_slot_identifies_the_bucket_not_the_moment(self):
        base = dt.datetime(2026, 9, 14, 10, 2, tzinfo=dt.timezone.utc)
        same = dt.datetime(2026, 9, 14, 10, 14, tzinfo=dt.timezone.utc)
        later = dt.datetime(2026, 9, 14, 10, 16, tzinfo=dt.timezone.utc)
        assert sched._bucket_slot(15, base) == sched._bucket_slot(15, same)
        assert sched._bucket_slot(15, base) != sched._bucket_slot(15, later)

    def test_slots_do_not_collide_across_days(self):
        a = dt.datetime(2026, 9, 14, 10, 2, tzinfo=dt.timezone.utc)
        b = dt.datetime(2026, 9, 15, 10, 2, tzinfo=dt.timezone.utc)
        assert sched._bucket_slot(15, a) != sched._bucket_slot(15, b)

    def test_each_tier_advances_at_its_own_rate(self):
        """The point of two tiers: over an hour the fast one opens four slots
        and the slow one opens a single slot."""
        day = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
        marks = [day + dt.timedelta(minutes=m) for m in range(0, 60, 5)]
        assert len({sched._bucket_slot(15, t) for t in marks}) == 4
        assert len({sched._bucket_slot(60, t) for t in marks}) == 1

    def test_the_two_tiers_cannot_claim_each_other_slot(self):
        """_bucket_slot alone does not guarantee this -- at 10:02 the 15m and
        60m buckets differ (40 vs 10) but at 01:00 vs 04:00 both are 4. What
        actually separates them is the job id, "sched:{name}:{slot}", so the
        invariant to hold is that the two schedules are distinctly named."""
        specs = {s.name: s for s in sched.build_schedules()}
        assert specs["news_fast"].task != specs["news_slow"].task
        collide = dt.datetime(2026, 9, 14, 1, 0, tzinfo=dt.timezone.utc)
        far = dt.datetime(2026, 9, 14, 4, 0, tzinfo=dt.timezone.utc)
        assert sched._bucket_slot(15, collide)[-2:] ==                sched._bucket_slot(60, far)[-2:]     # same bucket index...
        assert f"sched:news_fast:{sched._bucket_slot(15, collide)}" !=                f"sched:news_slow:{sched._bucket_slot(60, far)}"   # ...still distinct

    def test_the_next_fire_lands_inside_the_interval(self):
        for minutes in (15, 60):
            wait = sched._seconds_until_bucket(minutes)
            assert 0 < wait <= minutes * 60


class TestGate:
    def test_nothing_to_poll_counts_as_done(self, monkeypatch):
        """Otherwise an empty watchlist would re-enqueue for ever."""
        monkeypatch.setattr(ni, "watched_tickers", lambda **k: [])
        assert sched._news_slot_done("fast", 15) is True

    def test_done_only_when_every_ticker_was_polled_this_slot(self, monkeypatch):
        from app.backend.services import news_store
        monkeypatch.setattr(ni, "watched_tickers",
                            lambda **k: ["0700.HK", "D05.SI"])
        monkeypatch.setattr(news_store, "is_fresh",
                            lambda t, m: t == "0700.HK")
        assert sched._news_slot_done("fast", 15) is False
        monkeypatch.setattr(news_store, "is_fresh", lambda t, m: True)
        assert sched._news_slot_done("fast", 15) is True

    def test_the_gate_only_considers_its_own_tier(self, monkeypatch):
        from app.backend.services import news_store
        monkeypatch.setattr(ni, "watched_tickers",
                            lambda **k: ["0700.HK", "AAPL"])
        # the US ticker is stale; the FAST gate must not care
        monkeypatch.setattr(news_store, "is_fresh",
                            lambda t, m: t.endswith(".HK"))
        assert sched._news_slot_done("fast", 15) is True
        assert sched._news_slot_done("slow", 60) is False

    def test_an_error_reads_as_not_done(self, monkeypatch):
        def boom(**k):
            raise RuntimeError("db down")
        monkeypatch.setattr(ni, "watched_tickers", boom)
        assert sched._news_slot_done("fast", 15) is False


class TestSweepCoversEveryUser:
    """Behavioural, not source-inspecting.

    An earlier draft of these asserted on the function's source text and failed
    on its own docstring -- which names get_watchlist() in order to say it is
    deliberately NOT used. Prose that explains a rule reads identically to code
    that breaks it.
    """

    @pytest.fixture(autouse=True)
    def _offline_watchlist(self, monkeypatch):
        """add_ticker() calls FMP for a profile and a VGPM on every add. Stub
        those, but keep the real INSERT -- the row shape is the thing under
        test, and hand-rolling one already hid a NOT NULL column."""
        from app.backend.services import watchlist_service as ws
        monkeypatch.setattr(ws, "_fetch_profile", lambda t: {})
        monkeypatch.setattr(ws, "_fetch_vgpm_and_price", lambda t: {})

    def _add(self, ticker, user_id):
        from app.backend.services import watchlist_service as ws
        ws.add_ticker(ticker, user_id=user_id)

    def test_it_polls_every_user_not_just_the_anonymous_rows(self):
        from app.backend.services import news_ingest as ni
        self._add("0700.HK", None)
        self._add("D05.SI", 42)
        self._add("AAPL", 99)
        got = ni.all_watchlist_tickers()
        assert set(got) == {"0700.HK", "D05.SI", "AAPL"}

    def test_the_same_ticker_watched_twice_is_polled_once(self):
        from app.backend.services import news_ingest as ni
        self._add("0700.HK", 1)
        self._add("0700.HK", 2)
        assert ni.all_watchlist_tickers() == ["0700.HK"]

    def test_the_sweep_does_not_enrich_rows_it_does_not_need(self, monkeypatch):
        """get_watchlist() fetches a live price and VGPM per row -- an upstream
        call per sweep for symbols the sweep already has. Making it raise is a
        sharper check than grepping for the name."""
        from app.backend.services import news_ingest as ni
        from app.backend.services import watchlist_service

        def boom(*a, **k):
            raise AssertionError("sweep must not call get_watchlist()")
        monkeypatch.setattr(watchlist_service, "get_watchlist", boom)
        self._add("0700.HK", None)
        assert ni.all_watchlist_tickers() == ["0700.HK"]


class TestPrune:
    def test_the_slow_tier_prunes(self):
        """prune() shipped with the store and nothing called it, so the table
        had no ceiling."""
        import inspect
        from app.backend import worker
        assert "news_store.prune" in inspect.getsource(worker.run_news_slow_task)
