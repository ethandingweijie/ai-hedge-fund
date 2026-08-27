"""
tests/test_fast_path.py
=======================
Fast path — every covered run becomes memory + one web search (≤5 min),
purely backend. No user-visible mode: no toggle, no badge, no API param.

Levers under test:
  L1 — archived research reused at ANY content age (get_recent_research
       max_age_days=None; kill switch RESEARCH_FORCE_REUSE=false restores
       the M2 age tiers via _is_pure_cache_hit).
  L2 — the freshness search is the front block's 5th leg; 2_8 (DB reads)
       runs before the front block; join results mounted on state.
  L3 — same-day Pulse-cache reuse in _run_freshness_delta (0 searches when
       the ticker was already pulsed today; discovery shapes excluded).
  L4 — fast-tier model selection for display-guarded LLM calls
       (_fast_tier_model: FAST_TIER_MODEL env, default = recap fast tier).
  L5 — search basis max(prior_report_date, now − FRESHNESS_SEARCH_WINDOW_DAYS)
       default 3 days; earnings releases + product launches lead the query
       and the classification prompt.
"""
import inspect
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.memory.freshness as freshness
import src.memory.run_archive as run_archive
import src.agents.industry.deep_research as dr

from src.data import db as _db

# Shared fixtures/helpers from the content-age suite (same tmp-archive pattern).
from tests.test_content_age_reuse import tmp_archive, _seed, _days_ago  # noqa: F401


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("RESEARCH_FORCE_REUSE", raising=False)
    monkeypatch.delenv("FAST_TIER_MODEL", raising=False)
    monkeypatch.delenv("FRESHNESS_SEARCH_WINDOW_DAYS", raising=False)
    monkeypatch.delenv("FRESHNESS_DELTA_SEARCH", raising=False)
    monkeypatch.delenv("RECAP_MAX_AGE_DAYS", raising=False)
    yield


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer (report_recaps + complacency cache) at a
    fresh tmp SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "fast_path.db"))
    _db.close_all_connections()
    from src.memory import report_recap
    from src.research_ideas.complacency import web_research
    report_recap._tables_ready_key = None
    web_research._tables_ready_key = None
    yield
    _db.close_all_connections()


def _prior(run_at=None):
    return {
        "run_id": "run-prev",
        "run_at": run_at or "2026-08-05T00:00:00+00:00",
        "age_days": 12.0,
        "final_action": "BUY",
        "recap_text": "BUY on recurring-revenue compounding.",
        "recap_json": {"price_target": 445.0, "assumptions": [], "catalysts": []},
    }


# ── L1: get_recent_research None-cap ─────────────────────────────────────────

def test_none_cap_returns_old_run(tmp_archive):  # noqa: F811 (fixture import)
    """L1: max_age_days=None lifts the age filter — a >20d-old run is reuse
    seed; the default (7d) and the M2 reuse window (14d) still exclude it."""
    _seed(as_of=_days_ago(22), run_at=_days_ago(22))
    assert run_archive.get_recent_research("CRWD") is None
    assert run_archive.get_recent_research("CRWD", max_age_days=14) is None
    got = run_archive.get_recent_research("CRWD", max_age_days=None)
    assert got is not None
    assert got["age_days"] > 20.0


def test_none_cap_keeps_tier_qualification(tmp_archive):  # noqa: F811
    """Uncapped reuse must NOT widen WHICH tiers qualify — knowledge_only
    stays excluded at any age."""
    _seed(ticker="KONLY", tier="knowledge_only", as_of=_days_ago(2))
    assert run_archive.get_recent_research("KONLY", max_age_days=None) is None


def test_resolve_research_cache_passes_none_cap(monkeypatch):
    seen = {}

    def fake_get_recent(ticker, max_age_days=7, qualifying_tiers=None):
        seen["cap"] = max_age_days
        return None

    monkeypatch.setattr(run_archive, "get_recent_research", fake_get_recent)
    assert dr._resolve_research_cache("CRWD", max_age_days=None) is None
    assert seen["cap"] is None


def test_resolve_research_cache_discards_knowledge_only_even_uncapped(monkeypatch):
    monkeypatch.setattr(
        run_archive, "get_recent_research",
        lambda ticker, max_age_days=7, qualifying_tiers=None: {
            "research_tier": "knowledge_only", "age_days": 40.0,
            "deep_research_text": "text", "run_id": "r"},
    )
    assert dr._resolve_research_cache("CRWD", max_age_days=None) is None


# ── L1: kill switch + branch decision ────────────────────────────────────────

def test_force_reuse_default_on(monkeypatch):
    monkeypatch.delenv("RESEARCH_FORCE_REUSE", raising=False)
    assert dr._force_reuse_enabled() is True


@pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "off", ""])
def test_force_reuse_kill_switch_values(monkeypatch, val):
    monkeypatch.setenv("RESEARCH_FORCE_REUSE", val)
    assert dr._force_reuse_enabled() is False


def test_is_pure_cache_hit_truth_table(monkeypatch):
    # Explicit force_reuse=True → reuse at ANY cached age
    assert dr._is_pure_cache_hit(0.1, force_reuse=True) is True
    assert dr._is_pure_cache_hit(500.0, force_reuse=True) is True
    # Explicit False → restored M2 gate: pure cache only under 3 days
    assert dr._is_pure_cache_hit(2.9, force_reuse=False) is True
    assert dr._is_pure_cache_hit(3.0, force_reuse=False) is False
    assert dr._is_pure_cache_hit(20.0, force_reuse=False) is False
    # None → reads the env kill switch
    monkeypatch.delenv("RESEARCH_FORCE_REUSE", raising=False)
    assert dr._is_pure_cache_hit(20.0) is True
    monkeypatch.setenv("RESEARCH_FORCE_REUSE", "false")
    assert dr._is_pure_cache_hit(20.0) is False
    assert dr._is_pure_cache_hit(2.0) is True


def test_research_one_ticker_call_site_is_force_reuse_gated():
    """The archive lookup + branch gate stay wired to the kill switch."""
    src = inspect.getsource(dr._research_one_ticker)
    assert "max_age_days=None if _force_reuse_enabled() else _FRESH_DAYS" in src
    assert "_is_pure_cache_hit(_age)" in src


# ── L1: uncapped recap load (pipeline constant) ──────────────────────────────

def _seed_recap_backdated(ticker: str, days_ago_n: float, run_id="run-old"):
    from src.memory import report_recap
    report_recap._ensure_table()
    run_at = (datetime.now(timezone.utc) - timedelta(days=days_ago_n)).isoformat()
    _db.execute(
        "INSERT INTO report_recaps (ticker, run_id, run_at, price_at_run, "
        "final_action, signal_score, recap_json, recap_text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [ticker.upper(), run_id, run_at, 100.0, "BUY", 5.0,
         json.dumps({"price_target": 120.0}), "old recap", run_at],
    )


def test_uncapped_recap_returns_gt30d_recap(tmp_db):
    """A 45d-old recap is invisible under the 30d default cap (which the
    Pulse endpoint relies on — unchanged) but visible under the pipeline's
    uncapped horizon constant."""
    from src import pipeline
    from src.memory import report_recap
    _seed_recap_backdated("CRWD", 45.0)
    assert report_recap.get_recent_recap("CRWD") is None
    assert report_recap.get_recent_recap("CRWD", max_age_days=30) is None
    got = report_recap.get_recent_recap(
        "CRWD", max_age_days=pipeline._RECAP_UNCAPPED_DAYS)
    assert got is not None
    assert got["run_id"] == "run-old"
    assert got["final_action"] == "BUY"


# ── L4: fast-tier model selection ────────────────────────────────────────────

def test_fast_tier_model_not_qwen_routed(monkeypatch):
    """Pure Anthropic endpoints keep the standard model — the fast tier is a
    DashScope model and would 404 there."""
    monkeypatch.delenv("FAST_TIER_MODEL", raising=False)
    assert dr._fast_tier_model("claude-sonnet-4-6", qwen_routed=False) == \
        "claude-sonnet-4-6"


def test_fast_tier_model_defaults_to_recap_tier(monkeypatch):
    monkeypatch.delenv("FAST_TIER_MODEL", raising=False)
    from src.memory.report_recap import RECAP_MODEL_NAME
    assert dr._fast_tier_model("qwen3-max", qwen_routed=True) == RECAP_MODEL_NAME


@pytest.mark.parametrize("val", ["false", "0", "no", "off"])
def test_fast_tier_model_kill_switch(monkeypatch, val):
    monkeypatch.setenv("FAST_TIER_MODEL", val)
    assert dr._fast_tier_model("qwen3-max", qwen_routed=True) == "qwen3-max"


def test_fast_tier_model_explicit_name(monkeypatch):
    monkeypatch.setenv("FAST_TIER_MODEL", "qwen3.6-flash")
    assert dr._fast_tier_model("qwen3-max", qwen_routed=True) == "qwen3.6-flash"


# ── L2: front block structure (freshness as 5th leg) ─────────────────────────

def test_bounded_join_returns_five_results_in_order():
    """The join the front block uses must hand back all five leg results in
    submission order (the 5-value unpack after the join)."""
    from concurrent.futures import ThreadPoolExecutor
    from src import pipeline
    ex = ThreadPoolExecutor(max_workers=5)
    fs = [ex.submit(lambda i=i: i * 10) for i in range(5)]
    got = pipeline._bounded_join(ex, fs, 30.0, "test block")
    assert got == [0, 10, 20, 30, 40]


def test_front_block_structure():
    from src import pipeline
    src = inspect.getsource(pipeline.run_advanced_pipeline)
    # 2_8 runs exactly once — BEFORE the front block
    assert src.count('_timed("2_8_archive_cache_load")') == 1
    assert src.index("2_8_archive_cache_load") < src.index("FRONT BLOCK")
    # the freshness search exists ONLY as the front-block leg — the old
    # sequential 2_9 block (with _timed wrapper + search call) is gone
    assert src.count('"2_9_freshness_delta"') == 1
    assert "_deltas = _run_freshness_delta" not in src
    # five concurrent legs, results mounted before phase 3 consumes them
    assert "ThreadPoolExecutor(max_workers=5)" in src
    assert 'state["data"]["prior_recap"] = dict(_prior_reports)' in src
    assert 'state["data"]["freshness_delta"] = _fresh_deltas or {}' in src
    # recap load is uncapped (L1)
    assert "max_age_days=_RECAP_UNCAPPED_DAYS" in src


# ── L3: pulse-cache reuse ────────────────────────────────────────────────────

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_pulse_cache_reader_same_day_hit(tmp_db):
    from src import pipeline
    from src.research_ideas.complacency import web_research
    delta = {"material": False, "events": [], "verdict": "no material change",
             "since": _today_utc(), "based_on_run": "run-p",
             "prior_run_at": "2026-08-20T00:00:00+00:00"}
    web_research._cache_put("CRWD", "pulse", "",
                            {"pulse_date": _today_utc(), "delta": delta})
    got = pipeline._pulse_cache_delta("CRWD")
    assert got is not None
    assert got["verdict"] == "no material change"
    assert got["based_on_run"] == "run-p"


def test_pulse_cache_reader_rejects_discovery_shape(tmp_db):
    """Discovery deltas (no prior coverage) belong to the Pulse endpoint's
    uncovered path — pipeline 2_9 only runs WITH a prior recap."""
    from src import pipeline
    from src.research_ideas.complacency import web_research
    web_research._cache_put(
        "CRWD", "pulse", "",
        {"pulse_date": _today_utc(),
         "delta": {"discovery": True, "brief": "tariff probe opened",
                   "material": None, "verdict": "discovery"}})
    assert pipeline._pulse_cache_delta("CRWD") is None


def test_pulse_cache_reader_rejects_stale_day(tmp_db):
    from src import pipeline
    from src.research_ideas.complacency import web_research
    web_research._cache_put("CRWD", "pulse", "",
                            {"pulse_date": "2020-01-01",
                             "delta": {"material": False, "verdict": "old"}})
    assert pipeline._pulse_cache_delta("CRWD") is None


def test_pulse_cache_hit_skips_search(monkeypatch):
    """With a same-day pulse delta, _run_freshness_delta performs 0 searches."""
    from src import pipeline
    cached = {"material": True, "events": [{"headline": "Q2 beat"}],
              "verdict": "thesis strengthened", "since": _today_utc()}
    monkeypatch.setattr(pipeline, "_pulse_cache_delta", lambda t: dict(cached))

    def boom(ticker, prior, **kwargs):
        raise AssertionError("search must not run on a pulse-cache hit")

    monkeypatch.setattr(pipeline, "run_freshness_search", boom)
    out = pipeline._run_freshness_delta(["CRWD"], {"CRWD": _prior()})
    assert out["CRWD"]["verdict"] == "thesis strengthened"
    assert out["CRWD"]["material"] is True


def test_pulse_cache_miss_falls_through_to_search(tmp_db, monkeypatch):
    """No cache row → soft-fail read → the normal search path runs."""
    from src import pipeline
    monkeypatch.setattr(
        pipeline, "_delta_for_ticker",
        lambda t, p, k: {"material": None, "verdict": "searched"})
    out = pipeline._run_freshness_delta(["CRWD"], {"CRWD": _prior()})
    assert out["CRWD"]["verdict"] == "searched"


def test_pulse_cache_read_error_falls_through(tmp_db, monkeypatch):
    """Any reader explosion is swallowed inside _pulse_cache_delta (soft-fail)
    and the normal search path still runs."""
    from src import pipeline
    import src.research_ideas.complacency.web_research as wr

    def boom(ticker, kind, scope, max_age_days):
        raise RuntimeError("cache offline")

    monkeypatch.setattr(wr, "_cache_get", boom)
    assert pipeline._pulse_cache_delta("CRWD") is None
    monkeypatch.setattr(
        pipeline, "_delta_for_ticker",
        lambda t, p, k: {"material": None, "verdict": "searched"})
    out = pipeline._run_freshness_delta(["CRWD"], {"CRWD": _prior()})
    assert out["CRWD"]["verdict"] == "searched"


# ── L5: search basis window ──────────────────────────────────────────────────

def _capture_since(monkeypatch):
    """Replace the search provider + classifier; capture the `since` used."""
    captured = {}

    def fake_search(ticker, since, tavily_key, request_timeout_s):
        captured["since"] = since
        captured["ticker"] = ticker
        return "snippets"

    monkeypatch.setattr(freshness, "_search_fresh_snippets", fake_search)
    monkeypatch.setattr(freshness, "classify_delta",
                        lambda t, p, s, since="": None)
    return captured


def test_old_prior_clamped_to_3_day_window(monkeypatch):
    """A 20d-old report must NOT open a 20d search window — since is
    clamped to now − FRESHNESS_SEARCH_WINDOW_DAYS (default 3)."""
    captured = _capture_since(monkeypatch)
    prior = _prior(run_at=(datetime.now() - timedelta(days=20)).isoformat())
    out = freshness.run_freshness_search("CRWD", prior)
    want = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    assert captured["since"] == want
    assert out["since"] == want


def test_fresh_prior_keeps_report_date_anchor(monkeypatch):
    """A 1d-old report is fresher than the window — its exact report date
    stays the anchor."""
    captured = _capture_since(monkeypatch)
    run_at = (datetime.now() - timedelta(days=1)).isoformat()
    freshness.run_freshness_search("CRWD", _prior(run_at=run_at))
    assert captured["since"] == run_at[:10]


def test_window_env_override(monkeypatch):
    monkeypatch.setenv("FRESHNESS_SEARCH_WINDOW_DAYS", "5")
    captured = _capture_since(monkeypatch)
    prior = _prior(run_at=(datetime.now() - timedelta(days=20)).isoformat())
    freshness.run_freshness_search("CRWD", prior)
    want = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    assert captured["since"] == want


def test_no_prior_searches_bare_window(monkeypatch):
    captured = _capture_since(monkeypatch)
    freshness.run_freshness_search("CRWD", None)
    want = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    assert captured["since"] == want


@pytest.mark.parametrize("val, expected", [
    ("0", 3.0), ("-2", 3.0), ("garbage", 3.0), ("7", 7.0), ("2.5", 2.5),
])
def test_window_days_clamping(monkeypatch, val, expected):
    monkeypatch.setenv("FRESHNESS_SEARCH_WINDOW_DAYS", val)
    assert freshness._window_days() == expected


def test_window_days_default(monkeypatch):
    monkeypatch.delenv("FRESHNESS_SEARCH_WINDOW_DAYS", raising=False)
    assert freshness._window_days() == 3.0


def test_query_prioritises_earnings_and_product_launches(monkeypatch):
    """The one search leads with earnings releases + product launches."""
    import src.research_ideas.complacency.web_research as wr
    prompts = []
    monkeypatch.setattr(wr, "qwen_web_search",
                        lambda p, **k: prompts.append(p) or "text")
    monkeypatch.setattr(freshness, "classify_delta",
                        lambda t, p, s, since="": None)
    since = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    freshness.run_freshness_search(
        "CRWD", _prior(run_at=(datetime.now() - timedelta(days=20)).isoformat()))
    q = prompts[0].lower()
    assert "earnings" in q
    assert "product launch" in q
    # earnings + product come BEFORE the other categories, and the window
    # start is part of the query
    assert q.index("earnings") < q.index("guidance")
    assert q.index("product launch") < q.index("m&a")
    assert since in prompts[0]


def test_classify_prompt_priorities_and_span(monkeypatch):
    """The classifier prompt names earnings + product launches as the top
    materiality signals, orders events accordingly, and states the covered
    span."""
    import src.llm.models as models
    msgs = []

    class _FakeStructured:
        def invoke(self, messages):
            msgs.extend(messages)
            return SimpleNamespace(material=False, events=[], verdict="ok")

    class _FakeLLM:
        def with_structured_output(self, schema, method=None):
            return _FakeStructured()

        def invoke(self, messages):
            raise RuntimeError("structured only")

    monkeypatch.setattr(models, "get_model", lambda n, p, c: _FakeLLM())
    got = freshness.classify_delta("CRWD", _prior(), "snips",
                                   since="2026-08-21")
    assert got is not None and got["material"] is False
    text = "\n".join(m[1] for m in msgs)
    low = text.lower()
    assert "earnings releases" in low
    assert "product launches" in low
    assert "highest-priority" in low
    assert "earnings releases and product launches first" in low
    assert "since 2026-08-21" in text


# ── L5 honesty guard: addendum states the covered span ───────────────────────

def test_addendum_states_covered_span():
    txt = dr._build_latest_developments_addendum(
        {"material": False, "events": [], "verdict": "current",
         "since": "2026-08-21"}, as_of="2026-08-24")
    assert "since 2026-08-21" in txt
    assert "no material developments" in txt.lower()

    txt = dr._build_latest_developments_addendum(
        {"material": True, "since": "2026-08-21", "verdict": "changed",
         "events": [{"headline": "Q2 beat", "date": "2026-08-22",
                     "relevance": "beats ARR assumption"}]},
        as_of="2026-08-24")
    assert "material developments since 2026-08-21" in txt.lower()
    assert "q2 beat" in txt.lower()


def test_addendum_without_since_falls_back_to_original_research():
    txt = dr._build_latest_developments_addendum(
        {"material": False, "events": [], "verdict": "current"},
        as_of="2026-08-24")
    assert "since the original research" in txt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
