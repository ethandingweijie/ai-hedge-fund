"""
tests/test_freshness_delta.py
=============================
M1/M2 — freshness delta: one bounded web search per ticker with a prior
report recap + a fast-tier classification of whether anything MATERIAL
changed since that report.

M2 Track A1 moved the search + classify pair into src/memory/freshness.py
(shared with the Pulse endpoint): Qwen native web search is the PRIMARY
provider; Tavily is only a secondary fallback when Qwen returns nothing.
Every failure mode must be soft (the run always continues), the phase must
respect its kill switch, and tickers without a prior report are skipped
entirely (no search spent on them).
"""
import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import src.memory.freshness as freshness


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("FRESHNESS_DELTA_SEARCH", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    yield


def _prior(run_id="run-prev", run_at="2026-08-05T00:00:00+00:00") -> dict:
    return {
        "run_id": run_id,
        "run_at": run_at,
        "age_days": 12.0,
        "final_action": "BUY",
        "recap_text": "BUY on recurring-revenue compounding.",
        "recap_json": {
            "price_target": 445.0,
            "assumptions": ["ARR growth >= 25%"],
            "catalysts": ["Q2 earnings beat"],
        },
    }


def _no_qwen(monkeypatch):
    """Disable the Qwen primary path (missing API key shape)."""
    import src.research_ideas.complacency.web_research as wr
    monkeypatch.setattr(wr, "qwen_web_search", lambda *a, **k: None)


# ── Phase-level: kill switch + skip semantics ────────────────────────────────

def test_kill_switch_returns_empty(monkeypatch):
    from src import pipeline
    monkeypatch.setenv("FRESHNESS_DELTA_SEARCH", "false")
    out = pipeline._run_freshness_delta(["CRWD"], {"CRWD": _prior()})
    assert out == {}


def test_tickers_without_prior_skipped(monkeypatch):
    from src import pipeline
    monkeypatch.setattr(pipeline, "_pulse_cache_delta", lambda t: None)
    called = []
    monkeypatch.setattr(pipeline, "_delta_for_ticker",
                        lambda t, p, k: called.append(t) or {"material": None})
    out = pipeline._run_freshness_delta(["PANW"], {})
    assert out == {}
    assert called == []  # no prior for PANW → no search spent
    out = pipeline._run_freshness_delta(["PANW", "CRWD"], {"CRWD": _prior()})
    assert set(out) == {"CRWD"}
    assert called == ["CRWD"]


def test_pipeline_classify_alias_back_compat():
    """`pipeline._classify_delta` must keep resolving (moved to freshness)."""
    from src import pipeline
    assert pipeline._classify_delta is freshness.classify_delta


# ── run_freshness_search: provider selection + soft-fail shapes ──────────────

def test_kill_switch_inside_primitive(monkeypatch):
    monkeypatch.setenv("FRESHNESS_DELTA_SEARCH", "false")
    d = freshness.run_freshness_search("CRWD", _prior())
    assert d["material"] is None
    assert d["verdict"] == "check disabled"
    assert d["based_on_run"] == "run-prev"


def test_no_provider_returns_no_fresh_results(monkeypatch):
    """Qwen unavailable AND no usable Tavily → well-formed soft-fail."""
    _no_qwen(monkeypatch)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    d = freshness.run_freshness_search("CRWD", _prior(), tavily_key=None)
    assert d["material"] is None
    assert d["events"] == []
    assert d["verdict"] == "no fresh results"
    assert d["based_on_run"] == "run-prev"
    assert d["prior_run_at"] == "2026-08-05T00:00:00+00:00"


def test_qwen_primary_path_anchored_query(monkeypatch):
    """Qwen is searched FIRST, with a query bounded and anchored to the
    prior report date (a report fresher than the search window keeps its
    exact date as the anchor — L5)."""
    import src.research_ideas.complacency.web_research as wr
    prompts = []

    def fake_qwen(prompt, **kwargs):
        prompts.append(prompt)
        return "2026-08-14: CRWD raised FY guidance."

    monkeypatch.setattr(wr, "qwen_web_search", fake_qwen)
    monkeypatch.setattr(freshness, "classify_delta", lambda t, p, s, since="": {
        "material": True,
        "events": [{"headline": "Guidance raised", "date": "2026-08-14",
                    "relevance": "beats prior ARR assumption"}],
        "verdict": "Prior thesis strengthened",
    })
    fresh_prior = _prior(run_at=(datetime.now() - timedelta(days=1)).isoformat())
    d = freshness.run_freshness_search("CRWD", fresh_prior)
    assert d["material"] is True
    assert d["events"][0]["headline"] == "Guidance raised"
    assert d["verdict"] == "Prior thesis strengthened"
    # provenance fields survive the merge
    assert d["based_on_run"] == "run-prev"
    assert d["prior_run_at"] == fresh_prior["run_at"]
    assert "CRWD" in prompts[0]
    assert fresh_prior["run_at"][:10] in prompts[0]


def test_tavily_fallback_when_qwen_empty(monkeypatch):
    """Qwen returns nothing → Tavily is consulted as secondary fallback."""
    _no_qwen(monkeypatch)
    import src.agents.industry.deep_research as dr
    queries = []

    def fake_tavily(q, k, citation_sink=None):
        queries.append((q, k))
        return "1. CRWD guidance raised..."

    monkeypatch.setattr(dr, "_search_web", fake_tavily)
    monkeypatch.setattr(freshness, "classify_delta", lambda t, p, s, since="": {
        "material": True, "events": [], "verdict": "changed",
    })
    fresh_prior = _prior(run_at=(datetime.now() - timedelta(days=1)).isoformat())
    d = freshness.run_freshness_search("CRWD", fresh_prior, tavily_key="tvly-x")
    assert d["material"] is True
    assert queries and queries[0][1] == "tvly-x"
    assert fresh_prior["run_at"][:10] in queries[0][0]


def test_tavily_error_prefix_treated_as_empty(monkeypatch):
    _no_qwen(monkeypatch)
    import src.agents.industry.deep_research as dr
    monkeypatch.setattr(dr, "_search_web",
                        lambda q, k, citation_sink=None: "Search error: 401 Unauthorized")
    d = freshness.run_freshness_search("CRWD", _prior(), tavily_key="tvly-x")
    assert d["material"] is None
    assert d["verdict"] == "no fresh results"


def test_classifier_failure_keeps_base(monkeypatch):
    import src.research_ideas.complacency.web_research as wr
    monkeypatch.setattr(wr, "qwen_web_search", lambda *a, **k: "snippets")
    monkeypatch.setattr(freshness, "classify_delta", lambda t, p, s, since="": None)
    d = freshness.run_freshness_search("CRWD", _prior())
    assert d["material"] is None
    assert d["verdict"] == "check unavailable"


def test_search_exception_softfail(monkeypatch):
    """An unexpected raise inside the search/classify path never escapes."""
    import src.research_ideas.complacency.web_research as wr

    def boom(prompt, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(wr, "qwen_web_search", boom)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    d = freshness.run_freshness_search("CRWD", _prior(), tavily_key=None)
    assert d["material"] is None
    assert d["verdict"] == "no fresh results"


def test_explicit_since_date_overrides_prior(monkeypatch):
    import src.research_ideas.complacency.web_research as wr
    prompts = []
    monkeypatch.setattr(wr, "qwen_web_search",
                        lambda p, **k: prompts.append(p) or "text")
    monkeypatch.setattr(freshness, "classify_delta",
                        lambda t, p, s, since="": None)
    freshness.run_freshness_search("CRWD", _prior(), since_date="2026-08-18")
    assert "2026-08-18" in prompts[0]


# ── classify_delta LLM shapes (mocked model) ─────────────────────────────────

class _FakeStructured:
    def __init__(self, out):
        self._out = out

    def invoke(self, messages):
        if isinstance(self._out, Exception):
            raise self._out
        return self._out


class _FakeLLM:
    def __init__(self, structured_out, raw_content=None):
        self._structured = structured_out
        self._raw = raw_content

    def with_structured_output(self, schema, method=None):
        return _FakeStructured(self._structured)

    def invoke(self, messages):
        return SimpleNamespace(content=self._raw)


def test_classify_delta_structured(monkeypatch):
    import src.llm.models as models
    out = SimpleNamespace(
        material=True,
        events=[SimpleNamespace(headline="H", date="2026-08-14", relevance="R")],
        verdict="Changed",
    )
    monkeypatch.setattr(models, "get_model",
                        lambda name, provider, cfg: _FakeLLM(out))
    got = freshness.classify_delta("CRWD", _prior(), "snippets")
    assert got["material"] is True
    assert got["events"] == [{"headline": "H", "date": "2026-08-14",
                              "relevance": "R"}]
    assert got["verdict"] == "Changed"


def test_classify_delta_raw_json_fallback(monkeypatch):
    """Structured-output failure falls back to raw JSON extraction."""
    import src.llm.models as models
    raw = ('noise {"material": false, "events": [], '
           '"verdict": "still current"} trailing')
    monkeypatch.setattr(
        models, "get_model",
        lambda name, provider, cfg: _FakeLLM(RuntimeError("no json mode"), raw))
    got = freshness.classify_delta("CRWD", _prior(), "snippets")
    assert got is not None
    assert got["material"] is False
    assert got["verdict"] == "still current"


def test_classify_delta_no_model(monkeypatch):
    import src.llm.models as models
    monkeypatch.setattr(models, "get_model", lambda name, provider, cfg: None)
    assert freshness.classify_delta("CRWD", _prior(), "snippets") is None


def test_classify_delta_garbage_raw_returns_none(monkeypatch):
    import src.llm.models as models
    monkeypatch.setattr(
        models, "get_model",
        lambda name, provider, cfg: _FakeLLM(RuntimeError("x"), "no json here"))
    assert freshness.classify_delta("CRWD", _prior(), "snippets") is None


# ── Pipeline wiring guards ────────────────────────────────────────────────────

def test_pipeline_allowlist_carries_recency_keys():
    """The run_advanced_pipeline return dict is the allowlist that decides
    what reaches web_runs.full_result_json — the recency keys must be on it
    or the UI card never sees them."""
    from src import pipeline
    src = inspect.getsource(pipeline.run_advanced_pipeline)
    assert '"prior_recap"' in src
    assert '"freshness_delta"' in src
    assert "2_9_freshness_delta" in src
