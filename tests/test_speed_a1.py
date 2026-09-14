"""A1 speed work: every change here must leave the answer unchanged.

Each test checks the property that makes a reordering safe -- the same data
reaches the same consumer -- plus the saving itself (fewer fetches, fewer
LLM calls, nothing blocking).
"""
import asyncio
import threading
import time

import pytest


# ── statement cache (search_line_items) ────────────────────────────────────

@pytest.fixture
def fmp(monkeypatch):
    from src.tools import api as api_mod
    calls: list[str] = []

    def fake_get(path, params, api_key, uncap=False):
        calls.append(path.rsplit("/", 1)[-1])
        if path.endswith("income-statement"):
            return [{"date": "2025-12-31", "revenue": 1000.0, "netIncome": 90.0,
                     "period": "FY", "reportedCurrency": "USD"}]
        if path.endswith("balance-sheet-statement"):
            return [{"date": "2025-12-31", "totalDebt": 300.0,
                     "cashAndCashEquivalents": 50.0}]
        return []

    monkeypatch.setattr(api_mod, "_fmp_get", fake_get)
    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)
    monkeypatch.delenv("FMP_STATEMENT_CACHE_TTL_S", raising=False)
    return api_mod, calls


class TestStatementCache:
    def test_callers_asking_for_different_fields_share_one_fetch(self, fmp):
        api_mod, calls = fmp
        a = api_mod.search_line_items("ZZCO", ["revenue"], "2026-09-01",
                                      period="annual", api_key="k")
        b = api_mod.search_line_items("ZZCO", ["net_income", "total_debt"],
                                      "2026-09-01", period="annual", api_key="k")
        assert len(calls) == 4                      # one set of four, not two
        assert a[0].revenue == 1000.0
        assert b[0].net_income == 90.0 and b[0].total_debt == 300.0

    def test_cached_rows_give_the_same_answer_as_a_fresh_fetch(self, fmp, monkeypatch):
        api_mod, calls = fmp
        fields = ["revenue", "net_income", "total_debt", "net_debt"]
        first = api_mod.search_line_items("ZZCO", fields, "2026-09-01",
                                          period="annual", api_key="k")
        second = api_mod.search_line_items("ZZCO", fields, "2026-09-01",
                                           period="annual", api_key="k")
        monkeypatch.setenv("FMP_STATEMENT_CACHE_TTL_S", "0")
        fresh = api_mod.search_line_items("ZZCO", fields, "2026-09-01",
                                          period="annual", api_key="k")
        dump = lambda items: [i.model_dump() for i in items]
        assert dump(first) == dump(second) == dump(fresh)

    def test_an_empty_fetch_is_not_remembered(self, fmp, monkeypatch):
        api_mod, calls = fmp
        monkeypatch.setattr(api_mod, "_fmp_get",
                            lambda *a, **k: calls.append("x") or [])
        api_mod.search_line_items("ZZCO", ["revenue"], "2026-09-01",
                                  period="annual", api_key="k")
        api_mod.search_line_items("ZZCO", ["revenue"], "2026-09-01",
                                  period="annual", api_key="k")
        assert len(calls) == 8                      # retried, not cached empty

    def test_a_different_period_is_a_different_fetch(self, fmp):
        api_mod, calls = fmp
        api_mod.search_line_items("ZZCO", ["revenue"], "2026-09-01",
                                  period="annual", api_key="k")
        api_mod.search_line_items("ZZCO", ["revenue"], "2026-09-01",
                                  period="quarter", api_key="k")
        assert len(calls) == 8

    def test_ttl_zero_disables_the_cache(self, fmp, monkeypatch):
        api_mod, calls = fmp
        monkeypatch.setenv("FMP_STATEMENT_CACHE_TTL_S", "0")
        for _ in range(2):
            api_mod.search_line_items("ZZCO", ["revenue"], "2026-09-01",
                                      period="annual", api_key="k")
        assert len(calls) == 8


# ── macro regime ───────────────────────────────────────────────────────────

@pytest.fixture
def macro(monkeypatch, tmp_path):
    from src.agents.routing import macro_regime as mr
    fetched: list[str] = []
    llm_prompts: list = []

    def indicator(name, start, end, api_key=None):
        fetched.append(name)
        return [{"value": {"federalFunds": 4.5, "inflationRate": 3.1}.get(name, 1.0)}]

    monkeypatch.setattr(mr, "DATA_DIR", str(tmp_path))      # never touch src/data
    monkeypatch.setattr(mr, "get_api_key_from_state", lambda s, k: None)
    monkeypatch.setattr(mr, "get_prices", lambda *a, **k: fetched.append("SPY") or [])
    monkeypatch.setattr(mr, "get_company_news", lambda *a, **k: fetched.append("news") or [])
    monkeypatch.setattr(mr, "get_economic_indicator", indicator)
    monkeypatch.setattr(mr, "get_treasury_rates", lambda *a, **k: fetched.append("tsy") or [])
    monkeypatch.delenv("MACRO_REGIME_CACHE_TTL_S", raising=False)

    def fake_llm(prompt, pydantic_model, agent_name, state, default_factory):
        llm_prompts.append(str(prompt))
        return pydantic_model(
            regime={"risk_appetite": "risk-off", "rate_direction": "tightening",
                    "dollar_trend": "neutral", "volatility_regime": "high",
                    "recession_risk": "elevated", "regime_notes": "test"},
            agent_weights={}, position_size_cap=0.8, regime_notes="test")

    monkeypatch.setattr(mr, "call_llm", fake_llm)
    return mr, fetched, llm_prompts


def _state(end_date="2026-09-12", model="m"):
    return {"data": {"end_date": end_date, "tickers": ["ZZCO"]},
            "metadata": {"model_name": model, "model_provider": "p"}}


class TestMacroRegime:
    def test_every_series_still_reaches_the_prompt(self, macro):
        mr, fetched, prompts = macro
        mr.run_macro_regime_classifier(_state())
        assert len(fetched) == 10
        assert "4.50%" in prompts[0]                # federalFunds
        assert "3.1%" in prompts[0]                 # inflationRate

    def test_a_second_run_that_day_reuses_the_answer(self, macro):
        mr, fetched, prompts = macro
        first = mr.run_macro_regime_classifier(_state())["data"]
        second = mr.run_macro_regime_classifier(_state())["data"]
        assert len(prompts) == 1 and len(fetched) == 10
        assert second["macro_regime"] == first["macro_regime"]
        assert second["position_size_cap"] == first["position_size_cap"]

    def test_a_cached_regime_cannot_be_mutated_by_a_consumer(self, macro):
        mr, _, _ = macro
        mr.run_macro_regime_classifier(_state())            # fills the cache
        # the SECOND run is the one served from the cache -- mutate that
        mr.run_macro_regime_classifier(_state())["data"]["macro_regime"]["risk_appetite"] = "X"
        again = mr.run_macro_regime_classifier(_state())["data"]["macro_regime"]
        assert again["risk_appetite"] == "risk-off"

    def test_a_new_day_or_model_asks_again(self, macro):
        mr, _, prompts = macro
        mr.run_macro_regime_classifier(_state("2026-09-12"))
        mr.run_macro_regime_classifier(_state("2026-09-13"))
        mr.run_macro_regime_classifier(_state("2026-09-13", model="other"))
        assert len(prompts) == 3

    def test_a_fallback_regime_is_not_cached(self, macro, monkeypatch):
        mr, _, prompts = macro

        def failing_llm(prompt, pydantic_model, agent_name, state, default_factory):
            prompts.append("fallback")
            return default_factory()

        monkeypatch.setattr(mr, "call_llm", failing_llm)
        mr.run_macro_regime_classifier(_state())
        mr.run_macro_regime_classifier(_state())
        assert prompts == ["fallback", "fallback"]


# ── risk KPI prewarm ───────────────────────────────────────────────────────

class TestRiskPrewarm:
    def test_prewarm_fills_the_cache_the_augment_reads(self, monkeypatch):
        from src import pipeline
        from src.data import sector_kpi_framework as skf
        fetched = []

        def fake_kpis(t):
            fetched.append(t)
            skf._FMP_RISK_CACHE[t] = {"net_debt_to_ebitda": 1.5}
            return skf._FMP_RISK_CACHE[t]

        monkeypatch.setattr(skf, "_FMP_RISK_CACHE", {})
        monkeypatch.setattr(skf, "_fmp_risk_kpis", fake_kpis)
        monkeypatch.setattr(skf, "is_legacy_profile", lambda p: p == "Legacy")
        state = {"data": {"tickers": ["AAA", "BBB", "CCC"],
                          "profile_names": {"AAA": "Hyperscaler", "BBB": "Legacy"}}}
        pipeline._join_prewarm(pipeline._start_fmp_risk_prewarm(state, ["AAA"]))
        assert fetched == ["AAA"]                  # legacy + profile-less skipped
        # the augment now reads the prewarmed value (original fn reads the cache)
        monkeypatch.setattr(skf, "_fmp_risk_kpis",
                            lambda t: skf._FMP_RISK_CACHE.get(t) or pytest.fail("refetched"))
        assert skf._augment_metrics_with_fmp_risk("AAA", {})["net_debt_to_ebitda"] == 1.5

    def test_nothing_to_warm_returns_none(self):
        from src import pipeline
        assert pipeline._start_fmp_risk_prewarm({"data": {"tickers": ["A"]}}, ["A"]) is None
        pipeline._join_prewarm(None)

    def test_join_gives_up_at_the_deadline(self):
        from concurrent.futures import ThreadPoolExecutor
        from src import pipeline
        release = threading.Event()
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(release.wait)
        t0 = time.perf_counter()
        pipeline._join_prewarm([fut], timeout_s=0.2)
        assert time.perf_counter() - t0 < 2.0
        release.set()
        ex.shutdown(wait=True)


# ── background recap ───────────────────────────────────────────────────────

class TestBackgroundTask:
    def test_spawned_work_is_held_until_done_and_does_not_block(self):
        from app.backend.services import analysis_service as svc

        async def scenario():
            gate = asyncio.Event()
            done = []

            async def slow():
                await gate.wait()
                done.append(True)

            task = svc._spawn_background(slow())
            assert task in svc._BACKGROUND_TASKS    # strong ref while running
            assert not done                          # caller was not blocked
            gate.set()
            await task
            await asyncio.sleep(0)
            return done, task

        done, task = asyncio.run(scenario())
        assert done == [True]
        assert task not in svc._BACKGROUND_TASKS    # released once finished
