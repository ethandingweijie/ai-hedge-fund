"""Stage 4 (Workstream B — Speed) unit tests.

Covers the testable extractions of the B-items:

* B1  ``_merge_front_block`` — the front-parallel-block merge helper:
      disjoint key sets from the four concurrent phases union correctly.
* B2  ``_fetch_price_history`` — background price-history fetch: shape of
      the returned dict and graceful per-ticker degradation.
* B3  ``run_data_router`` parallel pre-fetch — all four FMP fetches are
      wired and their results land in routed_data / raw_financials.
* B4  ``get_prices`` superset-window cache slicing — a narrow window is
      served from a cached wider window without a network fetch, and a
      non-covered window still falls through to fetch.
* B5  ``_investor_max_workers`` — env-tunable worker cap parsing.

B6 (ChatAnthropic timeout/retries), B7 (Tavily timeout+retry) and B8
(citation-registry concurrency) are thin wiring over third-party SDKs and
are covered by the E2E timing gates + full-suite compile rather than unit
tests here.
"""

from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# B1 — front-block merge
# ---------------------------------------------------------------------------

def _blank_state() -> dict:
    return {"messages": [], "data": {"tickers": ["CRWD"]}}


def test_b1_merge_unions_disjoint_phase_keys():
    from src.pipeline import _merge_front_block

    durations: list = []
    router = _blank_state()
    router["data"].update({
        "sector": "Tech", "profile_name": "Growth SaaS",
        "raw_financials": {"FY2025": {}}, "routing_decision": "proceed",
    })
    macro = _blank_state()
    macro["data"].update({
        "macro_regime": {"risk_appetite": "risk_on"},
        "position_size_cap": 0.25,
    })
    intel = _blank_state()
    intel["data"].update({
        "insider_activity": {"CRWD": {"signal": "neutral"}},
        "analyst_signals": {"buffett": {"CRWD": {"signal": "BUY"}}},
    })
    edgar = _blank_state()
    edgar["data"]["edgar_filing_refs"] = {"CRWD": {"accession_number": "X-1"}}

    merged = _merge_front_block(router, macro, intel, edgar, durations)

    # router base keys survive
    assert merged["data"]["sector"] == "Tech"
    assert merged["data"]["routing_decision"] == "proceed"
    # macro keys grafted
    assert merged["data"]["macro_regime"]["risk_appetite"] == "risk_on"
    assert merged["data"]["position_size_cap"] == 0.25
    # intel keys grafted
    assert merged["data"]["insider_activity"]["CRWD"]["signal"] == "neutral"
    assert merged["data"]["analyst_signals"]["buffett"]["CRWD"]["signal"] == "BUY"
    # edgar keys grafted
    assert merged["data"]["edgar_filing_refs"]["CRWD"]["accession_number"] == "X-1"
    # the SHARED durations list is re-mounted (identity, not copy)
    assert merged["data"]["phase_durations"] is durations


def test_b1_merge_absent_keys_do_not_leak():
    from src.pipeline import _merge_front_block

    router = _blank_state()
    macro = _blank_state()          # macro produced nothing
    intel = _blank_state()
    edgar = _blank_state()

    merged = _merge_front_block(router, macro, intel, edgar, [])

    assert "macro_regime" not in merged["data"]
    assert "position_size_cap" not in merged["data"]
    assert "insider_activity" not in merged["data"]
    # edgar_filing_refs is always present (possibly empty) — downstream
    # consumers index it without a None-guard.
    assert merged["data"]["edgar_filing_refs"] == {}


# ---------------------------------------------------------------------------
# B2 — background price-history fetch
# ---------------------------------------------------------------------------

def test_b2_fetch_price_history_shape_and_degradation(monkeypatch):
    from src.pipeline import _fetch_price_history
    from src.tools import api as api_mod

    seen: dict = {}

    class _P:
        def __init__(self, t, c):
            self.time, self.close = t, c

    def fake_get_prices(ticker, start_date, end_date, api_key=None):
        seen[ticker] = (start_date, end_date, api_key)
        if ticker == "BAD":
            raise RuntimeError("boom")
        return [_P("2025-08-08", 100.0), _P("2025-08-09", 101.5)]

    monkeypatch.setattr(api_mod, "get_prices", fake_get_prices)

    out = _fetch_price_history(["CRWD", "BAD"], "2025-08-09", "the-key")

    # 365-day window anchored on end_date
    assert seen["CRWD"][0] == "2024-08-09"
    assert seen["CRWD"][1] == "2025-08-09"
    assert seen["CRWD"][2] == "the-key"
    assert out["CRWD"] == [
        {"date": "2025-08-08", "close": 100.0},
        {"date": "2025-08-09", "close": 101.5},
    ]
    # failing ticker degrades to [] instead of sinking the run
    assert out["BAD"] == []


# ---------------------------------------------------------------------------
# B3 — data_router parallel pre-fetch
# ---------------------------------------------------------------------------

def test_b3_data_router_prefetch_all_four_and_overlay(monkeypatch):
    from src.agents.industry import data_router as dr_mod

    calls: dict = {}

    def fake_metrics(ticker, end_date, period=None, limit=None, api_key=None):
        calls["metrics"] = (ticker, period, limit)
        return [SimpleNamespace(model_dump=lambda: {"period": "ttm"})]

    def fake_mcap(ticker, end_date, api_key=None):
        calls["mcap"] = ticker
        return 1_000_000_000

    def fake_prices(ticker, start_date, end_date, api_key=None):
        calls["prices"] = (ticker, start_date, end_date)
        return [SimpleNamespace(time="2025-01-02", close=350.0)]

    def fake_items(ticker=None, line_items=None, end_date=None,
                   period=None, limit=None, api_key=None):
        calls["items"] = (ticker, len(line_items or []), period)
        item = SimpleNamespace(report_period="2024-01-31")
        item.free_cash_flow = 123.0
        item.revenue = 999.0
        item.stock_based_compensation = 55.0   # Tech overlay field
        return [item]

    monkeypatch.setattr(dr_mod, "get_financial_metrics", fake_metrics)
    monkeypatch.setattr(dr_mod, "get_market_cap", fake_mcap)
    monkeypatch.setattr(dr_mod, "get_prices", fake_prices)
    monkeypatch.setattr(dr_mod, "search_line_items", fake_items)
    monkeypatch.setattr(dr_mod, "run_deep_research_agent", lambda st: st)

    state = {"messages": [], "data": {
        "tickers": ["CRWD"],
        "start_date": "2021-01-01",
        "end_date": "2025-08-09",
        "sector": "Tech",
        "raw_financials": {"FY2024": {"revenue": 999.0}},
    }}

    out = dr_mod.run_data_router(state)

    # all four fetches happened (parallel wiring intact)
    assert set(calls) == {"metrics", "mcap", "prices", "items"}
    assert calls["metrics"] == ("CRWD", "ttm", 5)
    assert calls["items"][2] == "annual"

    rd = out["data"]["routed_data"]
    assert set(rd) == set(dr_mod.AGENT_LINE_ITEMS)
    assert rd["buffett"]["ticker"] == "CRWD"
    assert rd["buffett"]["market_cap"] == 1_000_000_000
    assert rd["buffett"]["metrics"] == [{"period": "ttm"}]
    assert rd["buffett"]["free_cash_flow"] == [
        {"period": "2024-01-31", "value": 123.0}
    ]
    # druckenmiller gets the price series
    assert rd["druckenmiller"]["prices"] == [
        {"date": "2025-01-02", "close": 350.0}
    ]
    # Tech overlay field merged into raw_financials FY row
    assert out["data"]["raw_financials"]["FY2024"]["stock_based_compensation"] == 55.0
    # deep-research keys always seeded even when research was skipped
    assert out["data"]["deep_research"] == ""
    assert out["data"]["deep_research_sections"] == {}
    assert out["data"]["citation_registry"] == []


# ---------------------------------------------------------------------------
# B4 — superset-window price cache slicing
# ---------------------------------------------------------------------------

def _fresh_cache(monkeypatch):
    from src.data.cache import Cache
    from src.tools import api as api_mod
    fresh = Cache()
    monkeypatch.setattr(api_mod, "_cache", fresh)
    return api_mod, fresh


def test_b4_narrow_window_served_from_wider_cache(monkeypatch):
    api_mod, fresh = _fresh_cache(monkeypatch)

    def no_fetch(*a, **k):
        raise AssertionError("network fetch attempted despite superset cache")

    monkeypatch.setattr(api_mod, "_fmp_get", no_fetch)

    fresh.set_prices("fmp_CRWD_2024-01-01_2025-12-31", [
        {"time": "2025-06-01", "open": 99.0, "close": 100.0,
         "high": 101.0, "low": 98.0, "volume": 10},
        {"time": "2025-06-02", "open": 100.0, "close": 102.0,
         "high": 103.0, "low": 99.0, "volume": 20},
        {"time": "2025-07-15", "open": 110.0, "close": 111.0,
         "high": 112.0, "low": 109.0, "volume": 30},
    ])

    out = api_mod.get_prices("CRWD", "2025-06-01", "2025-06-30", api_key="k")

    # sliced to the window — the July row is excluded
    assert [p.time for p in out] == ["2025-06-01", "2025-06-02"]
    assert [p.close for p in out] == [100.0, 102.0]
    # exact key pinned so a repeat of the same narrow request is O(1)
    assert fresh.get_prices("fmp_CRWD_2025-06-01_2025-06-30") is not None


def test_b4_non_covered_window_falls_through_to_fetch(monkeypatch):
    api_mod, fresh = _fresh_cache(monkeypatch)

    # cached window does NOT cover June
    fresh.set_prices("fmp_CRWD_2025-01-01_2025-03-31", [
        {"time": "2025-02-01", "open": 90.0, "close": 91.0,
         "high": 92.0, "low": 89.0, "volume": 5},
    ])

    hits = []

    def fake_fmp(url, params, api_key):
        hits.append(params)
        return [{"date": "2025-06-01", "price": 100.0, "volume": 5}]

    monkeypatch.setattr(api_mod, "_fmp_get", fake_fmp)

    out = api_mod.get_prices("CRWD", "2025-06-01", "2025-06-30", api_key="k")

    assert len(hits) == 1
    assert hits[0]["symbol"] == "CRWD"
    assert len(out) == 1 and out[0].close == 100.0


def test_b4_different_ticker_not_cross_served(monkeypatch):
    api_mod, fresh = _fresh_cache(monkeypatch)

    fresh.set_prices("fmp_PANW_2024-01-01_2025-12-31", [
        {"time": "2025-06-01", "open": 1.0, "close": 2.0,
         "high": 3.0, "low": 0.5, "volume": 1},
    ])

    hits = []

    def fake_fmp(url, params, api_key):
        hits.append(params["symbol"])
        return [{"date": "2025-06-01", "price": 100.0, "volume": 5}]

    monkeypatch.setattr(api_mod, "_fmp_get", fake_fmp)

    api_mod.get_prices("CRWD", "2025-06-01", "2025-06-30", api_key="k")

    # PANW's superset must not serve CRWD's window
    assert hits == ["CRWD"]


# ---------------------------------------------------------------------------
# B5 — retired: PIPELINE_INVESTOR_MAX_WORKERS and _investor_max_workers were
# decommissioned together with the investor wave they throttled (M2 Track E).
# ---------------------------------------------------------------------------

