"""
tests/test_screener_intl.py
===========================
Smoke cover for the HK and SGX screeners after they moved onto FMP.

Why this exists: the SGX rewrite dropped a `raw_metrics = {}` initialisation
and shipped. Every existing test passed, because none of them execute the
body of get_sg_screener_stocks — it needs network — so a plain NameError
reached production and 500'd the endpoint for 88 seconds per request.

These tests stub the two network boundaries (_intl_universe and
_fetch_ticker_metrics) and run the real function bodies end to end, so any
unbound name, bad key or shape error surfaces offline.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app.backend.services import screener_service as ss


# ── Fixtures ────────────────────────────────────────────────────────────────

def _universe(prefix: str, n: int = 12):
    """(rows, sector_map, industry_map, name_map) in _intl_universe's shape."""
    rows, sector_map, industry_map, name_map = [], {}, {}, {}
    for i in range(n):
        t = f"{prefix}{i:04d}"
        # Two industries so the industry tier has a chance to engage.
        industry = "Banks" if i % 2 == 0 else "Software - Application"
        sector = "Financial Services" if i % 2 == 0 else "Technology"
        rows.append({"canonical": t, "name": f"Co {i}", "sector": sector,
                     "industry": industry, "market_cap": 1e11 - i * 1e9})
        sector_map[t], industry_map[t], name_map[t] = sector, industry, f"Co {i}"
    return rows, sector_map, industry_map, name_map


def _metrics(ticker: str, api_key=None, **kw):
    """Plausible _fetch_ticker_metrics output.

    Seeded from a stable digest, NOT hash() — Python randomises string
    hashing per process, which made metric values and therefore VGPM row
    order differ between runs. A test that passes alone and fails in the
    suite is worse than one that just fails.
    """
    seed = int(hashlib.md5(ticker.encode()).hexdigest()[:4], 16) % 17
    return {
        "ticker": ticker,
        "pe": 10.0 + seed, "pb": 1.0 + seed / 10, "ev_ebitda": 8.0 + seed,
        "ev_sales": 2.0 + seed / 5, "fcf_yield": 0.03 + seed / 1000,
        "roe": 0.10 + seed / 200, "net_margin": 0.15,
        "rev_growth": 0.05, "eps_growth": 0.07,
        "price": 100.0 + seed, "beta": 1.0,
        "market_cap": 5e10,
    }


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_get_fmp_key", lambda: "test-key")
    monkeypatch.setattr(ss, "_fetch_ticker_metrics", _metrics)
    monkeypatch.setattr(ss, "_get_cached", lambda key: None)      # always cold
    monkeypatch.setattr(ss, "_get_cached_stale", lambda key: None)
    monkeypatch.setattr(ss, "_set_cached", lambda *a, **k: None)
    # Fresh build locks per test so one test's lock can't leak into another.
    monkeypatch.setattr(ss, "_BUILD_LOCKS", {})
    monkeypatch.setattr(ss, "_ensure_tables", lambda: None)
    monkeypatch.setattr(ss, "get_live_quotes", lambda *a, **k: {})
    monkeypatch.setattr(ss, "_set_fast_vgpm_cached", lambda *a, **k: None)
    import app.backend.services.knowledge_graph as kg
    monkeypatch.setattr(kg, "set_ttm_metrics", lambda *a, **k: None)
    yield


# ── The regression ──────────────────────────────────────────────────────────

class TestSgScreenerRuns:
    def test_returns_items(self, stubbed, monkeypatch):
        """The exact failure that reached prod: NameError on raw_metrics."""
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0"))
        out = ss.get_sg_screener_stocks()
        assert out["total"] == 12
        assert len(out["items"]) == 12
        assert out["cached"] is False

    def test_items_carry_required_fields(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0"))
        item = ss.get_sg_screener_stocks()["items"][0]
        for field in ("symbol", "companyName", "sector", "industry"):
            assert field in item, f"{field} missing from SGX screener item"

    def test_empty_universe_degrades(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: ([], {}, {}, {}))
        out = ss.get_sg_screener_stocks()
        assert out == {"items": [], "total": 0, "cached": False}

    def test_universe_failure_degrades(self, stubbed, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("screener down")
        monkeypatch.setattr(ss, "_intl_universe", boom)
        out = ss.get_sg_screener_stocks()
        assert out == {"items": [], "total": 0, "cached": False}


class TestHkScreenerRuns:
    def test_returns_items(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        out = ss.get_hk_screener_stocks()
        assert out["total"] == 12
        assert len(out["items"]) == 12

    def test_industry_is_real_not_the_hkex_placeholder(self, stubbed, monkeypatch):
        """Every HK stock used to be labelled the literal "HKEX", which put
        them all in one bucket and made the industry percentile tier
        meaningless."""
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        industries = {i["industry"] for i in ss.get_hk_screener_stocks()["items"]}
        assert "HKEX" not in industries
        assert industries == {"Banks", "Software - Application"}

    def test_exchange_and_country_tagged(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        item = ss.get_hk_screener_stocks()["items"][0]
        assert item["exchange"] == "HKEX"
        assert item["country"] == "HK"

    def test_empty_universe_degrades(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: ([], {}, {}, {}))
        assert ss.get_hk_screener_stocks()["total"] == 0


class TestVgpmScoring:
    def test_scores_are_computed(self, stubbed, monkeypatch):
        """A screener row without vgpm is the visible symptom of the metric
        pipeline failing, so assert the scores actually land."""
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0", n=20))
        items = ss.get_hk_screener_stocks()["items"]
        scored = [i for i in items if i.get("vgpm")]
        assert scored, "no HK row received a VGPM score"
        assert any(i.get("composite_score") is not None for i in scored)


class TestSharedMetricPipeline:
    def test_hk_and_sg_use_fetch_ticker_metrics(self, stubbed, monkeypatch):
        """Both markets must go through the same fetcher as US, or their
        scores are not comparable to anything."""
        seen: list[str] = []

        def spy(ticker, api_key=None, **kw):
            seen.append(ticker)
            return _metrics(ticker)

        monkeypatch.setattr(ss, "_fetch_ticker_metrics", spy)
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0", n=6))
        ss.get_hk_screener_stocks()
        assert len(seen) == 6
        seen.clear()
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0", n=6))
        ss.get_sg_screener_stocks()
        assert len(seen) == 6


class TestIntlUniverseContract:
    """_intl_universe is the network boundary the tests above stub, so its
    own shape is asserted separately."""

    def test_per_sector_cap_matches_the_us_screener(self):
        import inspect
        sig = inspect.signature(ss._intl_universe)
        assert sig.parameters["per_sector_limit"].default == ss._PER_SECTOR_LIMIT


# ── Cold-build protection ───────────────────────────────────────────────────

class TestColdBuildProtection:
    """A cold HK build is ~2,600 FMP calls (330 names x 8 endpoints) and
    cannot finish inside an HTTP request. It ran 300s and the browser gave
    up — "loading failed". Two guards: expired rows beat a timeout, and only
    one build runs at a time so concurrent requests don't multiply load on
    the same rate-limit bucket."""

    def test_stale_beats_a_timeout_when_the_universe_fails(self, stubbed, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("FMP screener down")
        monkeypatch.setattr(ss, "_intl_universe", boom)
        monkeypatch.setattr(ss, "_get_cached_stale",
                            lambda key: [{"symbol": "00700.HK"}])
        out = ss.get_hk_screener_stocks()
        assert out["total"] == 1
        assert out["stale"] is True

    def test_concurrent_request_serves_stale_instead_of_queueing(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        monkeypatch.setattr(ss, "_get_cached_stale",
                            lambda key: [{"symbol": "00700.HK"}])
        # Simulate a build already in flight by taking the lock first.
        lock = ss._build_lock("hk_fmp_v7")
        assert lock.acquire(blocking=False)
        try:
            out = ss.get_hk_screener_stocks()
        finally:
            lock.release()
        assert out["stale"] is True
        assert out["total"] == 1

    def test_lock_is_released_after_a_build(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        ss.get_hk_screener_stocks()
        lock = ss._build_lock("hk_fmp_v7")
        assert lock.acquire(blocking=False), "build lock leaked"
        lock.release()

    def test_lock_released_even_when_the_build_raises(self, stubbed, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(ss, "_intl_universe", boom)
        ss.get_hk_screener_stocks()
        lock = ss._build_lock("hk_fmp_v7")
        assert lock.acquire(blocking=False), "build lock leaked on failure"
        lock.release()

    def test_sg_has_the_same_protection(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0"))
        monkeypatch.setattr(ss, "_get_cached_stale",
                            lambda key: [{"symbol": "D05.SI"}])
        lock = ss._build_lock("sg_fmp_v3")
        assert lock.acquire(blocking=False)
        try:
            out = ss.get_sg_screener_stocks()
        finally:
            lock.release()
        assert out["stale"] is True

    def test_locks_are_per_cache_key(self, stubbed):
        assert ss._build_lock("hk_fmp_v7") is not ss._build_lock("sg_fmp_v3")
        assert ss._build_lock("hk_fmp_v7") is ss._build_lock("hk_fmp_v7")


class TestKgMetricReuse:
    """Every rebuild used to re-fetch all ~330 names at 8 FMP calls each even
    when the metrics were minutes old. The US path already reads this cache."""

    def test_cached_metrics_skip_the_fetch(self, stubbed, monkeypatch):
        rows, *_ = _universe("0", n=6)
        cached = {r["canonical"]: _metrics(r["canonical"]) for r in rows}
        import app.backend.services.knowledge_graph as kg
        monkeypatch.setattr(kg, "get_ttm_metrics_cached", lambda ts: cached)
        fetched: list[str] = []
        monkeypatch.setattr(ss, "_fetch_ticker_metrics",
                            lambda t, k=None, **kw: fetched.append(t) or _metrics(t))
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0", n=6))
        out = ss.get_hk_screener_stocks()
        assert out["total"] == 6
        assert fetched == [], "cached metrics must not be re-fetched"

    def test_missing_metrics_still_fetched(self, stubbed, monkeypatch):
        import app.backend.services.knowledge_graph as kg
        monkeypatch.setattr(kg, "get_ttm_metrics_cached", lambda ts: {})
        fetched: list[str] = []
        monkeypatch.setattr(ss, "_fetch_ticker_metrics",
                            lambda t, k=None, **kw: fetched.append(t) or _metrics(t))
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0", n=6))
        ss.get_hk_screener_stocks()
        assert len(fetched) == 6


class TestPriceAndBetaCarry:
    """robo_strategy_service._to_candidate DROPS any stock without a price,
    so a screener row with price=None silently excluded every HK and SG name
    from Individual Stocks mode — the stock plan came back 100% US even when
    Emerging Markets and Asia-Pacific were the highest-weighted preferences.

    _fetch_ticker_metrics returns neither price nor beta; both are already on
    the FMP company-screener row, so carrying them costs no extra call."""

    def _universe_with_price(self, prefix, n=6):
        rows, sm, im, nm = _universe(prefix, n)
        for i, r in enumerate(rows):
            r["price"] = 100.0 + i
            r["beta"] = 1.1
        return rows, sm, im, nm

    def test_hk_items_carry_price(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe",
                            lambda m, **k: self._universe_with_price("0"))
        items = ss.get_hk_screener_stocks()["items"]
        assert all(i.get("price") for i in items), "HK rows must carry a price"

    def test_sg_items_carry_price(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe",
                            lambda m, **k: self._universe_with_price("D0"))
        items = ss.get_sg_screener_stocks()["items"]
        assert all(i.get("price") for i in items), "SG rows must carry a price"

    def test_beta_carried_for_risk_tiering(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe",
                            lambda m, **k: self._universe_with_price("0"))
        items = ss.get_hk_screener_stocks()["items"]
        assert any(i.get("beta") for i in items)

    def test_fetched_metrics_do_not_overwrite_screener_price(self, stubbed, monkeypatch):
        """setdefault, not assignment — a metrics price would win if present,
        but a None from the metrics fetcher must not blank the row."""
        monkeypatch.setattr(ss, "_fetch_ticker_metrics",
                            lambda t, k=None, **kw: {**_metrics(t), "price": None})
        monkeypatch.setattr(ss, "_intl_universe",
                            lambda m, **k: self._universe_with_price("0"))
        items = ss.get_hk_screener_stocks()["items"]
        assert all(i.get("price") for i in items)


class TestRoboCandidateEligibility:
    """The end-to-end consequence: an HK screener row must survive
    _to_candidate, or Individual Stocks mode can never show a non-US name."""

    def test_hk_row_becomes_a_candidate(self, stubbed, monkeypatch):
        from app.backend.services import robo_strategy_service as rs
        rows, sm, im, nm = _universe("0", 4)
        for i, r in enumerate(rows):
            r["price"], r["beta"] = 100.0 + i, 1.1
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: (rows, sm, im, nm))
        items = ss.get_hk_screener_stocks()["items"]
        # Every row must survive — row order depends on VGPM scores, so
        # asserting on items[0] alone would be order-dependent.
        cands = [rs._to_candidate(i, region="Emerging Markets",
                                  sector_map=rs._HK_SECTOR_TO_CANONICAL)
                 for i in items]
        assert all(c is not None for c in cands),             "HK screener rows were rejected as candidates"
        assert all(c["price"] for c in cands)


class TestSectorCanonicalisation:
    """_to_candidate drops any stock whose sector doesn't canonicalise. The
    maps used to hold ONLY legacy labels ("Property", "Tech", "Telco"), so
    once the screeners emitted FMP sectors just 4 of 11 matched and seven
    sectors' worth of HK/SG stocks were silently discarded."""

    def test_every_fmp_sector_maps(self):
        from app.backend.services import robo_strategy_service as rs
        for sector in rs._FMP_SECTORS:
            assert sector in rs._HK_SECTOR_TO_CANONICAL, f"{sector} unmapped"
            assert sector in rs._SG_SECTOR_TO_CANONICAL, f"{sector} unmapped"

    def test_fmp_sectors_map_to_themselves(self):
        from app.backend.services import robo_strategy_service as rs
        for sector in rs._FMP_SECTORS:
            assert rs._SECTOR_TO_CANONICAL[sector] == sector

    @pytest.mark.parametrize("legacy,expected", [
        ("Property", "Real Estate"),
        ("REIT", "Real Estate"),
        ("Tech", "Technology"),
        ("Telco", "Communication Services"),
        ("Financials", "Financial Services"),
    ])
    def test_legacy_labels_still_resolve(self, legacy, expected):
        """Older cached rows may still carry these."""
        from app.backend.services import robo_strategy_service as rs
        assert rs._SECTOR_TO_CANONICAL[legacy] == expected

    def test_a_financial_services_stock_is_not_dropped(self):
        """The single biggest casualty: "Financial Services" is FMP's label
        and the map only had "Financials"."""
        from app.backend.services import robo_strategy_service as rs
        cand = rs._to_candidate(
            {"symbol": "01398.HK", "companyName": "ICBC", "price": 5.2,
             "sector": "Financial Services", "marketCap": 3.5e12, "beta": 0.8},
            region="Emerging Markets", sector_map=rs._HK_SECTOR_TO_CANONICAL)
        assert cand is not None
        assert cand["sector"] == "Financial Services"


class TestCacheKeyVersionFallback:
    """Cache keys carry a version suffix and get bumped when the row shape or
    a field fix changes (sg_universe_v1 -> sg_fmp_v2 -> sg_fmp_v3). A bump
    leaves ZERO rows under the new key, so the exact-key stale lookup finds
    nothing, the stale guard cannot fire, and the first reader after the
    deploy eats the full cold build — 93s for SGX. That is how SGX failed a
    second time, after its marketCap fix."""

    def test_family_prefix(self):
        assert ss._cache_key_family("sg_fmp_v3") == "sg_"
        assert ss._cache_key_family("hk_fmp_v7") == "hk_"
        assert ss._cache_key_family("d910850ee9375ba2") == "", "US keys are hashes"

    def test_previous_version_serves_when_the_new_key_is_empty(self, monkeypatch):
        rows = [{"symbol": "D05.SI"}]
        monkeypatch.setattr(ss._db, "query_one", lambda *a, **k: None)
        monkeypatch.setattr(ss._db, "query", lambda *a, **k: [
            {"cache_key": "sg_fmp_v2", "results_json": json.dumps(rows),
             "fetched_at": "2026-08-20T00:00:00"},
        ])
        assert ss._get_cached_stale("sg_fmp_v3") == rows

    def test_other_markets_are_not_borrowed(self, monkeypatch):
        """An HK payload must never stand in for SGX."""
        monkeypatch.setattr(ss._db, "query_one", lambda *a, **k: None)
        monkeypatch.setattr(ss._db, "query", lambda *a, **k: [
            {"cache_key": "hk_fmp_v7", "results_json": json.dumps([{"symbol": "0700.HK"}]),
             "fetched_at": "2026-08-26T00:00:00"},
        ])
        assert ss._get_cached_stale("sg_fmp_v3") is None

    def test_exact_key_wins_over_older_versions(self, monkeypatch):
        monkeypatch.setattr(ss._db, "query_one",
                            lambda *a, **k: {"results_json": json.dumps([{"symbol": "NEW"}])})
        monkeypatch.setattr(ss._db, "query", lambda *a, **k: [
            {"cache_key": "sg_fmp_v2", "results_json": json.dumps([{"symbol": "OLD"}]),
             "fetched_at": "2026-08-20T00:00:00"},
        ])
        assert ss._get_cached_stale("sg_fmp_v3") == [{"symbol": "NEW"}]

    def test_no_percent_in_the_sql(self):
        """A literal percent sign in the SQL text breaks psycopg ("only
        '%s'... allowed as placeholders"), which rules out a prefix-wildcard
        query — hence the Python-side filter."""
        import inspect
        sql_lines = [l for l in inspect.getsource(ss._get_cached_stale).splitlines()
                     if "SELECT" in l.upper() or "FROM screener_cache" in l]
        assert sql_lines, "no SQL found to check"
        for line in sql_lines:
            assert "%" not in line, f"percent in SQL breaks psycopg: {line.strip()}"


class TestServeStaleAndRefresh:
    def test_cold_cache_returns_stale_immediately(self, stubbed, monkeypatch):
        built: list[int] = []

        def slow_build(*a, **k):
            built.append(1)
            return {"items": [], "total": 0, "cached": False}

        monkeypatch.setattr(ss, "_build_sg_screener", slow_build)
        monkeypatch.setattr(ss, "_get_cached_stale",
                            lambda key: [{"symbol": "D05.SI"}])
        out = ss.get_sg_screener_stocks()
        assert out["stale"] is True
        assert out["total"] == 1
        # The refresh runs on a daemon thread; give it a moment to land.
        import time
        for _ in range(50):
            if built:
                break
            time.sleep(0.01)
        assert built, "background refresh never ran"

    def test_first_ever_build_still_runs_inline(self, stubbed, monkeypatch):
        """Nothing to serve — blocking once per market is unavoidable."""
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0", 4))
        monkeypatch.setattr(ss, "_get_cached_stale", lambda key: None)
        out = ss.get_sg_screener_stocks()
        assert out["total"] == 4
        assert not out.get("stale")

    def test_lock_not_left_held_after_serving_stale(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_build_sg_screener",
                            lambda *a, **k: {"items": [], "total": 0, "cached": False})
        monkeypatch.setattr(ss, "_get_cached_stale",
                            lambda key: [{"symbol": "D05.SI"}])
        ss.get_sg_screener_stocks()
        import time
        lock = ss._build_lock("sg_fmp_v3")
        for _ in range(100):
            if lock.acquire(blocking=False):
                lock.release()
                return
            time.sleep(0.01)
        raise AssertionError("build lock still held after serving stale")

    def test_ancient_versions_are_not_borrowed(self, monkeypatch):
        """A key is bumped BECAUSE the old rows were wrong (v3 fixed null
        marketCap in v2), so the fallback is bounded — it exists to bridge
        the minutes after a deploy, not to serve last month's screener."""
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc)
               - timedelta(days=ss._STALE_VERSION_MAX_AGE_DAYS + 1)).isoformat()
        monkeypatch.setattr(ss._db, "query_one", lambda *a, **k: None)
        monkeypatch.setattr(ss._db, "query", lambda *a, **k: [
            {"cache_key": "sg_universe_v1",
             "results_json": json.dumps([{"symbol": "OLD"}]), "fetched_at": old},
        ])
        assert ss._get_cached_stale("sg_fmp_v3") is None

    def test_recent_previous_version_is_borrowed(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        monkeypatch.setattr(ss._db, "query_one", lambda *a, **k: None)
        monkeypatch.setattr(ss._db, "query", lambda *a, **k: [
            {"cache_key": "sg_fmp_v2",
             "results_json": json.dumps([{"symbol": "D05.SI"}]), "fetched_at": recent},
        ])
        assert ss._get_cached_stale("sg_fmp_v3") == [{"symbol": "D05.SI"}]

    def test_newest_version_preferred(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        monkeypatch.setattr(ss._db, "query_one", lambda *a, **k: None)
        monkeypatch.setattr(ss._db, "query", lambda *a, **k: [
            {"cache_key": "sg_fmp_v2", "results_json": json.dumps([{"symbol": "NEWER"}]),
             "fetched_at": (now - timedelta(days=1)).isoformat()},
            {"cache_key": "sg_universe_v1", "results_json": json.dumps([{"symbol": "OLDER"}]),
             "fetched_at": (now - timedelta(days=20)).isoformat()},
        ])
        assert ss._get_cached_stale("sg_fmp_v3") == [{"symbol": "NEWER"}]
