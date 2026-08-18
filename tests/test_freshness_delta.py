"""
tests/test_freshness_delta.py
=============================
M1 — inline freshness delta (src/pipeline.py phase 2_9): one bounded web
search per ticker with a prior report recap + a fast-tier classification of
whether anything MATERIAL changed since that report.

Every failure mode must be soft (the run always continues), the phase must
respect its kill switch, and tickers without a prior report are skipped
entirely (no search spent on them).
"""
import inspect
from types import SimpleNamespace

import pytest


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


# ── Phase-level: kill switch + skip semantics ────────────────────────────────

def test_kill_switch_returns_empty(monkeypatch):
    from src import pipeline
    monkeypatch.setenv("FRESHNESS_DELTA_SEARCH", "false")
    out = pipeline._run_freshness_delta(["CRWD"], {"CRWD": _prior()})
    assert out == {}


def test_tickers_without_prior_skipped(monkeypatch):
    from src import pipeline
    called = []
    monkeypatch.setattr(pipeline, "_delta_for_ticker",
                        lambda t, p, k: called.append(t) or {"material": None})
    out = pipeline._run_freshness_delta(["PANW"], {})
    assert out == {}
    assert called == []  # no prior for PANW → no search spent
    out = pipeline._run_freshness_delta(["PANW", "CRWD"], {"CRWD": _prior()})
    assert set(out) == {"CRWD"}
    assert called == ["CRWD"]


# ── Per-ticker soft-fail shapes ───────────────────────────────────────────────

def test_no_tavily_key_check_unavailable(monkeypatch):
    from src import pipeline
    d = pipeline._delta_for_ticker("CRWD", _prior(), None)
    assert d["material"] is None
    assert d["events"] == []
    assert d["verdict"] == "check unavailable"
    assert d["based_on_run"] == "run-prev"
    assert d["prior_run_at"] == "2026-08-05T00:00:00+00:00"


def test_search_error_softfail(monkeypatch):
    from src import pipeline
    import src.agents.industry.deep_research as dr
    monkeypatch.setattr(dr, "_search_web",
                        lambda q, k, citation_sink=None: "Search error: 401 Unauthorized")
    d = pipeline._delta_for_ticker("CRWD", _prior(), "tvly-test-key")
    assert d["material"] is None
    assert d["verdict"] == "no fresh results"


def test_no_results_softfail(monkeypatch):
    from src import pipeline
    import src.agents.industry.deep_research as dr
    monkeypatch.setattr(dr, "_search_web",
                        lambda q, k, citation_sink=None: "No results found.")
    d = pipeline._delta_for_ticker("CRWD", _prior(), "tvly-test-key")
    assert d["material"] is None
    assert d["verdict"] == "no fresh results"


def test_material_classification_merged(monkeypatch):
    from src import pipeline
    import src.agents.industry.deep_research as dr
    queries = []

    def fake_search(q, k, citation_sink=None):
        queries.append(q)
        return "1. CRWD guidance raised..."

    monkeypatch.setattr(dr, "_search_web", fake_search)
    monkeypatch.setattr(pipeline, "_classify_delta", lambda t, p, s: {
        "material": True,
        "events": [{"headline": "Guidance raised", "date": "2026-08-14",
                    "relevance": "beats prior ARR assumption"}],
        "verdict": "Prior thesis strengthened",
    })
    d = pipeline._delta_for_ticker("CRWD", _prior(), "tvly-test-key")
    assert d["material"] is True
    assert d["events"][0]["headline"] == "Guidance raised"
    assert d["verdict"] == "Prior thesis strengthened"
    # provenance fields survive the merge
    assert d["based_on_run"] == "run-prev"
    assert d["prior_run_at"] == "2026-08-05T00:00:00+00:00"
    # the search query is bounded and anchored to the prior report date
    assert "CRWD" in queries[0] and "2026-08-05" in queries[0]


def test_classifier_failure_keeps_base(monkeypatch):
    from src import pipeline
    import src.agents.industry.deep_research as dr
    monkeypatch.setattr(dr, "_search_web", lambda q, k, citation_sink=None: "snippets")
    monkeypatch.setattr(pipeline, "_classify_delta", lambda t, p, s: None)
    d = pipeline._delta_for_ticker("CRWD", _prior(), "tvly-test-key")
    assert d["material"] is None
    assert d["verdict"] == "check unavailable"


def test_search_exception_softfail(monkeypatch):
    """An unexpected raise inside the search/classify path never escapes."""
    from src import pipeline
    import src.agents.industry.deep_research as dr

    def boom(q, k, citation_sink=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(dr, "_search_web", boom)
    d = pipeline._delta_for_ticker("CRWD", _prior(), "tvly-test-key")
    assert d["material"] is None
    assert d["verdict"] == "check unavailable"


# ── _classify_delta LLM shapes (mocked model) ────────────────────────────────

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
    from src import pipeline
    import src.llm.models as models
    out = SimpleNamespace(
        material=True,
        events=[SimpleNamespace(headline="H", date="2026-08-14", relevance="R")],
        verdict="Changed",
    )
    monkeypatch.setattr(models, "get_model",
                        lambda name, provider, cfg: _FakeLLM(out))
    got = pipeline._classify_delta("CRWD", _prior(), "snippets")
    assert got["material"] is True
    assert got["events"] == [{"headline": "H", "date": "2026-08-14",
                              "relevance": "R"}]
    assert got["verdict"] == "Changed"


def test_classify_delta_raw_json_fallback(monkeypatch):
    """Structured-output failure falls back to raw JSON extraction."""
    from src import pipeline
    import src.llm.models as models
    raw = ('noise {"material": false, "events": [], '
           '"verdict": "still current"} trailing')
    monkeypatch.setattr(
        models, "get_model",
        lambda name, provider, cfg: _FakeLLM(RuntimeError("no json mode"), raw))
    got = pipeline._classify_delta("CRWD", _prior(), "snippets")
    assert got is not None
    assert got["material"] is False
    assert got["verdict"] == "still current"


def test_classify_delta_no_model(monkeypatch):
    from src import pipeline
    import src.llm.models as models
    monkeypatch.setattr(models, "get_model", lambda name, provider, cfg: None)
    assert pipeline._classify_delta("CRWD", _prior(), "snippets") is None


def test_classify_delta_garbage_raw_returns_none(monkeypatch):
    from src import pipeline
    import src.llm.models as models
    monkeypatch.setattr(
        models, "get_model",
        lambda name, provider, cfg: _FakeLLM(RuntimeError("x"), "no json here"))
    assert pipeline._classify_delta("CRWD", _prior(), "snippets") is None


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
