"""Screener scale + correctness fixes (QA power H1-H5, chaos #11-13).

Every network boundary is stubbed: FMP company-screener, batch-quote,
lookup endpoints and FX. Nothing here touches the network.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.services import screener_service as ss


# ── helpers ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.ok = status == 200

    def json(self):
        return self._data


def _client():
    from app.backend.routes import screener as S
    app = FastAPI()
    app.include_router(S.router, prefix="/screener")
    return TestClient(app, raise_server_exceptions=False)


SECTORS = ["Technology", "Healthcare", "Financial Services", "Energy"]


def _fmp_rows(exchange: str, n: int, start_cap: float):
    rows = []
    for i in range(n):
        rows.append({
            "symbol": f"{exchange[:2]}{i:03d}",
            "companyName": f"{exchange} Co {i}",
            "sector": SECTORS[i % len(SECTORS)],
            "industry": "Ind",
            "marketCap": start_cap - i * 1e9,
            "price": 10.0 + i,
            "exchangeShortName": exchange,
            "country": "US",
        })
    # A foreign listing FMP returns despite country=US — must be dropped.
    rows.append({"symbol": f"FOREIGN{exchange}.L", "companyName": "Foreign",
                 "sector": "Technology", "marketCap": 9e12, "price": 1.0,
                 "exchangeShortName": "LSE", "country": "GB"})
    return rows


@pytest.fixture
def us_stub(monkeypatch):
    """Cold US universe: 150 NASDAQ + 120 NYSE + 30 AMEX names >= $2B."""
    cache: dict = {}
    calls: list = []

    def _screener(**kw):
        calls.append(kw)
        ex = kw.get("exchange")
        n, top = {"NASDAQ": (150, 300e9), "NYSE": (120, 250e9), "AMEX": (30, 40e9)}[ex]
        rows = _fmp_rows(ex, n, top)
        floor = kw.get("market_cap_more_than") or 0
        return [r for r in rows if r["marketCap"] >= floor]

    monkeypatch.setattr(ss, "_ensure_tables", lambda: None)
    monkeypatch.setattr(ss, "_call_fmp_screener", _screener)
    monkeypatch.setattr(ss, "_get_master_universe", lambda: None)
    monkeypatch.setattr(ss, "_master_universe_fresh", lambda c: False)
    monkeypatch.setattr(ss, "_get_vgpm_map", lambda t: {})
    monkeypatch.setattr(ss, "_get_fast_vgpm_cached", lambda t: {})
    monkeypatch.setattr(ss, "_get_or_compute_fast_vgpm", lambda *a, **k: {})
    monkeypatch.setattr(ss, "_get_cached", lambda k: cache.get(k))
    monkeypatch.setattr(ss, "_get_cached_stale", lambda k: None)
    monkeypatch.setattr(ss, "_set_cached", lambda k, v, ttl_hours=24: cache.__setitem__(k, v))
    monkeypatch.setattr(ss, "_BUILD_LOCKS", {})
    monkeypatch.setattr(ss, "get_live_quotes_detail",
                        lambda syms: {"quotes": {s: {"price": 99.0, "change_pct": 1.5}
                                                 for s in syms},
                                      "missing": [], "stale": [], "as_of": "now"})
    return {"cache": cache, "calls": calls}


# ── H1: limit / filters / paging on the merged universe ─────────────────────

class TestUsUniverse:
    def test_limit_is_honoured(self, us_stub):
        out = ss.get_screener_stocks(market_cap_more_than=2_000_000_000, limit=500)
        assert out["total"] == 300           # every US name, foreign rows dropped
        assert len(out["items"]) == 300
        assert not any(i["symbol"].startswith("FOREIGN") for i in out["items"])
        out = ss.get_screener_stocks(market_cap_more_than=2_000_000_000, limit=50)
        assert len(out["items"]) == 50 and out["total"] == 300 and out["has_more"]

    def test_one_build_serves_every_limit_and_filter(self, us_stub):
        ss.get_screener_stocks(limit=10)
        ss.get_screener_stocks(limit=500)
        ss.get_screener_stocks(market_cap_more_than=100e9, limit=5)
        ss.get_screener_stocks(sector="Technology")
        # One company-screener call per US exchange, once.
        assert sorted(c["exchange"] for c in us_stub["calls"]) == ["AMEX", "NASDAQ", "NYSE"]
        assert len(us_stub["cache"]) == 1

    def test_market_cap_min_filters(self, us_stub):
        all_ = ss.get_screener_stocks(market_cap_more_than=2e9)["total"]
        big = ss.get_screener_stocks(market_cap_more_than=100e9)
        assert big["total"] < all_
        assert all(i["marketCap"] >= 100e9 for i in big["items"])
        band = ss.get_screener_stocks(market_cap_more_than=12e9, market_cap_lower_than=50e9,
                                      limit=None)
        assert band["items"] and all(12e9 <= i["marketCap"] < 50e9 for i in band["items"])

    def test_all_sectors_contains_every_sector_member(self, us_stub):
        tech = {i["symbol"] for i in ss.get_screener_stocks(sector="Technology")["items"]}
        everything = {i["symbol"] for i in ss.get_screener_stocks(limit=None)["items"]}
        assert tech and tech <= everything

    def test_pages_partition_the_universe(self, us_stub):
        seen = []
        offset = 0
        while True:
            out = ss.get_screener_stocks(limit=70, offset=offset, sort="marketCap")
            seen += [i["symbol"] for i in out["items"]]
            if not out["has_more"]:
                break
            offset += 70
        assert len(seen) == len(set(seen)) == 300

    def test_sort_by_market_cap_price_change(self, us_stub):
        caps = [i["marketCap"] for i in ss.get_screener_stocks(limit=None, sort="marketCap")["items"]]
        assert caps == sorted(caps, reverse=True)
        prices = [i["price"] for i in
                  ss.get_screener_stocks(limit=None, sort="price", order="asc")["items"]]
        assert prices == sorted(prices)

    def test_sort_by_change_uses_live_quotes(self, us_stub, monkeypatch):
        def _q(syms):
            return {"quotes": {s: {"price": 10.0, "change_pct": float(n % 7) - 3}
                               for n, s in enumerate(syms)},
                    "missing": [], "stale": [], "as_of": "now"}
        monkeypatch.setattr(ss, "get_live_quotes_detail", _q)
        out = ss.get_screener_stocks(limit=None, sort="change", live=True)
        ch = [i["change_pct"] for i in out["items"]]
        assert None not in ch and ch == sorted(ch, reverse=True)

    def test_us_change_pct_no_longer_null(self, us_stub):
        """`live_quotes = {}` for the all-sector path left every US row's
        change_pct null."""
        out = ss.get_screener_stocks(limit=20, live=True)
        assert all(i["change_pct"] == 1.5 for i in out["items"])
        assert all(i["currency"] == "USD" and i["marketCapUsd"] == i["marketCap"]
                   for i in out["items"])

    def test_nulls_sort_last_both_ways(self):
        items = [{"symbol": "A", "price": None}, {"symbol": "B", "price": 2.0},
                 {"symbol": "C", "price": 1.0}]
        assert [i["symbol"] for i in ss.sort_items(items, "price", "asc")] == ["C", "B", "A"]
        assert [i["symbol"] for i in ss.sort_items(items, "price", "desc")] == ["B", "C", "A"]

    def test_unknown_sort_rejected(self):
        with pytest.raises(ValueError):
            ss.normalize_sort("vibes")

    def test_route_pagination_and_backcompat(self, us_stub):
        c = _client()
        r = c.get("/screener/stocks", params={"limit": 500, "marketCapMin": 2_000_000_000})
        assert r.status_code == 200
        body = r.json()
        assert {"items", "total", "cached"} <= set(body)
        assert len(body["items"]) == 300
        p2 = c.get("/screener/stocks", params={"limit": 100, "page": 2, "sort": "marketCap"}).json()
        p1 = c.get("/screener/stocks", params={"limit": 100, "sort": "marketCap"}).json()
        assert p2["offset"] == 100
        assert not {i["symbol"] for i in p1["items"]} & {i["symbol"] for i in p2["items"]}
        assert c.get("/screener/stocks", params={"sort": "vibes"}).status_code == 422


# ── H4 / chaos #13: /screener/prices ────────────────────────────────────────

@pytest.fixture
def quote_stub(monkeypatch):
    """batch-quote / quote endpoints with a 0.3 s simulated round trip."""
    state = {"calls": [], "latency": 0.3, "fail": False,
             "rows": lambda syms: [{"symbol": s, "price": 100.0, "changePercentage": 1.0,
                                    "volume": 5, "timestamp": time.time()} for s in syms]}

    def _get(url, params=None, timeout=None):
        state["calls"].append((url.rsplit("/", 1)[-1], dict(params or {})))
        time.sleep(state["latency"])
        if state["fail"]:
            return _Resp({"Error": "Limit Reach"}, 429)
        if url.endswith("/batch-quote"):
            return _Resp(state["rows"](params["symbols"].split(",")))
        if url.endswith("/quote"):
            return _Resp(state["rows"]([params["symbol"]]))
        return _Resp([], 404)

    monkeypatch.setattr(ss.requests, "get", _get)
    monkeypatch.setattr(ss, "_get_fmp_key", lambda: "k")
    monkeypatch.setattr("src.tools.api.acquire_fmp_token", lambda: None)
    monkeypatch.setattr(ss, "_fetch_hk_yfinance", lambda t: {})
    ss._QUOTE_CACHE.clear()
    yield state
    ss._QUOTE_CACHE.clear()


US50 = [f"S{i:02d}" for i in range(50)]


class TestLiveQuotes:
    def test_fifty_symbols_one_batch_call(self, quote_stub):
        t0 = time.time()
        out = ss.get_live_quotes_detail(US50)
        elapsed = time.time() - t0
        assert len(out["quotes"]) == 50 and out["missing"] == [] and out["stale"] == []
        assert [c[0] for c in quote_stub["calls"]] == ["batch-quote"]
        # One simulated round trip, not 50 (the old path: 50 calls / 10 workers).
        assert elapsed < 1.5

    def test_chunks_above_batch_size(self, quote_stub):
        syms = [f"T{i:03d}" for i in range(ss.MAX_PRICE_SYMBOLS)]
        ss.get_live_quotes_detail(syms)
        assert len(quote_stub["calls"]) == -(-len(syms) // ss._QUOTE_BATCH)

    def test_short_cache_and_dedupe(self, quote_stub):
        ss.get_live_quotes_detail(["AAPL", "aapl", " AAPL "])
        assert quote_stub["calls"][0][1]["symbols"] == "AAPL"
        ss.get_live_quotes_detail(["AAPL"])
        assert len(quote_stub["calls"]) == 1          # served from the cache

    def test_single_flight(self, quote_stub):
        quote_stub["latency"] = 0.5
        results = []
        ts = [threading.Thread(target=lambda: results.append(ss.get_live_quotes_detail(US50)))
              for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(results) == 4 and all(len(r["quotes"]) == 50 for r in results)
        assert len(quote_stub["calls"]) == 1

    def test_nonpositive_price_rejected(self, quote_stub):
        quote_stub["rows"] = lambda syms: [
            {"symbol": "ZERO", "price": 0, "timestamp": time.time()},
            {"symbol": "NEG", "price": -1.0, "timestamp": time.time()},
            {"symbol": "OK", "price": 5.0, "timestamp": time.time()},
        ]
        out = ss.get_live_quotes_detail(["ZERO", "NEG", "OK"])
        assert set(out["quotes"]) == {"OK"}
        assert sorted(out["missing"]) == ["NEG", "ZERO"]

    def test_delisted_quote_is_flagged_stale(self, quote_stub):
        """FMP still quotes delisted TWTR at 53.70 from Oct 2022."""
        quote_stub["rows"] = lambda syms: [
            {"symbol": "TWTR", "price": 53.7, "changePercentage": 0, "timestamp": 1666911005}]
        out = ss.get_live_quotes_detail(["TWTR"])
        assert out["stale"] == ["TWTR"] and out["quotes"]["TWTR"]["stale"] is True

    def test_provider_failure_serves_recent_cache_as_stale(self, quote_stub, monkeypatch):
        ss.get_live_quotes_detail(["AAPL"])
        # Age the cache past the fresh TTL but inside the stale window.
        ts, q = ss._QUOTE_CACHE["AAPL"]
        ss._QUOTE_CACHE["AAPL"] = (ts - ss._QUOTE_TTL_S - 1, q)
        quote_stub["fail"] = True
        quote_stub["latency"] = 0
        monkeypatch.setattr(ss.time, "sleep", lambda s: None)
        out = ss.get_live_quotes_detail(["AAPL", "MSFT"])
        assert out["stale"] == ["AAPL"] and out["missing"] == ["MSFT"]
        assert out["quotes"]["AAPL"]["price"] == 100.0

    def test_hk_symbol_mapped_and_keyed_back(self, quote_stub):
        quote_stub["rows"] = lambda syms: [{"symbol": "9988.HK", "price": 86.65,
                                            "timestamp": time.time()}]
        out = ss.get_live_quotes_detail(["09988.HK"])
        assert quote_stub["calls"][0][1]["symbols"] == "9988.HK"
        assert out["quotes"]["09988.HK"]["price"] == 86.65

    def test_get_live_quotes_keeps_old_contract(self, quote_stub):
        q = ss.get_live_quotes(["AAPL", "MSFT"])
        assert set(q) == {"AAPL", "MSFT"} and q["AAPL"]["price"] == 100.0

    def test_route_shape_and_cap(self, quote_stub, monkeypatch):
        monkeypatch.setattr(ss, "update_cached_prices", lambda q: None)
        c = _client()
        body = c.get("/screener/prices", params={"symbols": "AAPL,MSFT,AAPL"}).json()
        assert set(body) == {"quotes", "missing", "stale", "as_of"}
        assert set(body["quotes"]) == {"AAPL", "MSFT"}
        too_many = ",".join(f"X{i}" for i in range(ss.MAX_PRICE_SYMBOLS + 1))
        assert c.get("/screener/prices", params={"symbols": too_many}).status_code == 400
        assert c.get("/screener/prices", params={"symbols": " , "}).status_code == 400


# ── H3: SGX depositary receipts ─────────────────────────────────────────────

class TestSgxReceipts:
    @pytest.mark.parametrize("sym,name,price,mcap", [
        ("UTSD.SI", "Taiwan Semiconductor Manufacturing Company Limited", 1.51, 2.396e12),
        ("UXSD.SI", "Space Exploration Technologies Corp.", 1.6, 2.31e12),
        ("UNXD.SI", "SK hynix Inc.", 1.47, 1.02e12),
        ("UGGD.SI", "Sea Limited", 1.75, 5.24e10),
        ("UGBD.SI", "Grab Holdings Limited", 1.85, 1.46e10),
        ("HPAD.SI", "Ping An Insurance Shs UnSp Singapore Depositary Receipt", 5.0, 1e11),
        ("ZZZ.SI", "Some Wrapper Co", 1.0, 5e11),          # price/cap inconsistency
    ])
    def test_receipts_detected(self, sym, name, price, mcap):
        assert ss.is_sgx_depositary_receipt(sym, name, price, mcap)

    @pytest.mark.parametrize("sym,name,price,mcap", [
        ("D05.SI", "DBS Group Holdings Ltd", 65.9, 1.87e11),
        ("Y92.SI", "Thai Beverage Public Company Limited", 0.42, 1.056e10),
        ("A50.SI", "Thomson Medical Group Limited", 0.044, 1.164e9),
        ("5DD.SI", "Micro-Mechanics (Holdings) Ltd.", 2.29, 3.1e8),
        ("F9D.SI", "Boustead Singapore Limited", 1.64, 8e8),
        ("AJBU.SI", "Keppel DC REIT", 2.0, 4e9),
        ("Z74.SI", "Singapore Telecommunications Limited", 4.29, 6.9e10),
    ])
    def test_ordinary_shares_kept(self, sym, name, price, mcap):
        assert not ss.is_sgx_depositary_receipt(sym, name, price, mcap)

    def test_cached_rows_filtered_on_read(self, monkeypatch):
        rows = [{"symbol": "UTSD.SI", "companyName": "TSMC", "price": 1.5, "marketCap": 2.4e12},
                {"symbol": "D05.SI", "companyName": "DBS", "price": 65.0, "marketCap": 1.8e11}]
        monkeypatch.setattr(ss, "_ensure_tables", lambda: None)
        monkeypatch.setattr(ss, "_get_cached", lambda k: json.loads(json.dumps(rows)))
        out = ss.get_sg_screener_stocks()
        assert [i["symbol"] for i in out["items"]] == ["D05.SI"] and out["total"] == 1

    def test_receipts_never_enter_the_peer_universe(self, monkeypatch):
        import src.data.regional_comps as rc
        uni = [
            {"symbol": "UTSD.SI", "name": "TSMC", "sector": "Technology",
             "industry": "Semis", "market_cap": 2.4e12, "price": 1.5},
            {"symbol": "D05.SI", "name": "DBS", "sector": "Financial Services",
             "industry": "Banks", "market_cap": 1.8e11, "price": 65.0},
        ]
        monkeypatch.setattr(rc, "fetch_universe", lambda m: [] if m == "HKSE" else list(uni))
        rows, *_ = ss._intl_universe("SES")
        assert [r["canonical"] for r in rows] == ["D05.SI"]


# ── H2: HK / SG currency ────────────────────────────────────────────────────

class TestCurrency:
    def test_market_cap_converted_for_bands(self, monkeypatch):
        rates = {"HKD": 0.128, "CNY": 0.14}
        monkeypatch.setattr("src.tools.api.get_fx_rate", lambda f, t="USD", api_key=None: rates.get(f))
        res = {"items": [{"symbol": "00700.HK", "marketCap": 4e12},
                         {"symbol": "87001.HK", "marketCap": 1e10},
                         {"symbol": "00001.HK", "marketCap": None}]}
        ss.annotate_currency(res, "HK")
        a, b, c = res["items"]
        assert a["currency"] == "HKD" and a["marketCapUsd"] == pytest.approx(5.12e11)
        assert b["currency"] == "CNY" and b["marketCapUsd"] == pytest.approx(1.4e9)
        assert c["marketCapUsd"] is None
        assert res["currency"] == "HKD"

    def test_missing_fx_gives_none_never_one(self, monkeypatch):
        monkeypatch.setattr("src.tools.api.get_fx_rate", lambda *a, **k: None)
        res = {"items": [{"symbol": "D05.SI", "marketCap": 1.8e11},
                         {"symbol": "J36.SI", "marketCap": 2e10}]}
        ss.annotate_currency(res, "SG")
        d05, j36 = res["items"]
        assert d05["currency"] == "SGD" and d05["marketCapUsd"] is None
        # USD-quoted SGX counter needs no FX at all.
        assert j36["currency"] == "USD" and j36["marketCapUsd"] == 2e10

    def test_sg_route_carries_currency(self, monkeypatch):
        monkeypatch.setattr(ss, "get_sg_screener_stocks", lambda force_refresh=False:
                            {"items": [{"symbol": "D05.SI", "marketCap": 1.3e11}],
                             "total": 1, "cached": True})
        monkeypatch.setattr(ss, "overlay_exchange_quotes", lambda r, e: r)
        monkeypatch.setattr("src.tools.api.get_fx_rate", lambda f, t="USD", api_key=None: 0.77)
        body = _client().get("/screener/sg-stocks").json()
        item = body["items"][0]
        assert item["currency"] == "SGD" and item["marketCapUsd"] == pytest.approx(1.001e11)


# ── chaos #11: /screener/lookup ─────────────────────────────────────────────

@pytest.fixture
def lookup_stub(monkeypatch):
    state = {"calls": [], "status": 200, "routes": {}}

    def _get(url, params=None, timeout=None):
        path = url.rsplit("/", 1)[-1]
        state["calls"].append(path)
        if state["status"] != 200:
            return _Resp({"Error": "Limit Reach"}, state["status"])
        fn = state["routes"].get(path)
        return _Resp(fn(params) if fn else [])

    monkeypatch.setattr(ss.requests, "get", _get)
    monkeypatch.setattr(ss, "_get_fmp_key", lambda: "k")
    monkeypatch.setattr("src.tools.api.acquire_fmp_token", lambda: None)
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    monkeypatch.setattr(ss, "_ensure_tables", lambda: None)
    monkeypatch.setattr(ss, "get_live_quotes", lambda t: {})
    monkeypatch.setattr(ss._db, "query_one", lambda *a, **k: None)
    return state


class TestLookup:
    def test_cached_symbol_answers_without_fmp(self, lookup_stub, monkeypatch):
        """A 429 used to come back as 404 even though the ticker was cached:
        FMP was called before the cache."""
        lookup_stub["status"] = 429
        item = {"symbol": "AAPL", "companyName": "Apple Inc."}
        monkeypatch.setattr(ss._db, "query_one", lambda q, p=None:
                            {"item_json": json.dumps(item)} if p and p[0] == "AAPL" else None)
        assert ss.lookup_ticker("aapl")["companyName"] == "Apple Inc."
        assert lookup_stub["calls"] == []

    def test_rate_limit_is_503_not_404(self, lookup_stub):
        lookup_stub["status"] = 429
        r = _client().get("/screener/lookup", params={"symbol": "MSFT"})
        assert r.status_code == 503

    def test_company_name_resolves_confidently(self, lookup_stub, monkeypatch):
        lookup_stub["routes"] = {
            "profile": lambda p: ([{"symbol": "AAPL", "companyName": "Apple Inc.",
                                    "sector": "Technology", "industry": "Hardware",
                                    "marketCap": 5e12, "price": 355.0}]
                                  if p.get("symbol") == "AAPL" else []),
            "search-symbol": lambda p: [],
            "search-name": lambda p: [
                {"symbol": "APC.DE", "name": "Apple Inc.", "currency": "EUR", "exchange": "XETRA"},
                {"symbol": "APLE", "name": "Apple Hospitality REIT, Inc.", "currency": "USD",
                 "exchange": "NYSE"},
                {"symbol": "AAPL", "name": "Apple Inc.", "currency": "USD", "exchange": "NASDAQ"},
            ],
        }
        monkeypatch.setattr(ss, "_get_vgpm_map", lambda t: {})
        monkeypatch.setattr(ss, "_get_or_compute_fast_vgpm", lambda *a, **k: {})
        monkeypatch.setattr(ss, "_upsert", lambda *a, **k: None)
        out = ss.lookup_ticker("apple")
        assert out["symbol"] == "AAPL" and out["marketCap"] == 5e12

    def test_fuzzy_result_is_not_a_match(self, lookup_stub):
        """results[0] used to be accepted for any query."""
        lookup_stub["routes"] = {
            "search-name": lambda p: [{"symbol": "XYZ", "name": "Xylem Inc.",
                                       "currency": "USD", "exchange": "NYSE"}],
        }
        assert ss.lookup_ticker("xyzzzq") is None
        r = _client().get("/screener/lookup", params={"symbol": "xyzzzq"})
        assert r.status_code == 404

    @pytest.mark.parametrize("q,name,ok", [
        ("apple", "Apple Inc.", True),
        ("micro", "Microsoft Corporation", False),
        ("bank of america", "Bank of America Corporation", True),
        ("america", "Bank of America Corporation", False),
    ])
    def test_name_match_rule(self, q, name, ok):
        assert ss._confident_name_match(q, name) is ok
