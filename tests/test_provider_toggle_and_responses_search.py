"""Landing-page provider toggle + Qwen Responses-API web search (owner, 2026-11-26)."""
from types import SimpleNamespace as NS

from app.backend.services.analysis_service import _resolve_run_models
from src.agents.industry import deep_research as dr


def test_qwen_run_puts_the_pipeline_on_pipeline_model(monkeypatch):
    monkeypatch.delenv("PIPELINE_MODEL", raising=False)
    assert _resolve_run_models("qwen3.8-flash") == ("qwen3.8-flash", "Alibaba")
    monkeypatch.setenv("PIPELINE_MODEL", "not-a-model")
    assert _resolve_run_models("qwen3.6-plus") == ("qwen3.8-flash", "Alibaba")


def test_anthropic_run_stays_on_claude_even_with_pipeline_model_set(monkeypatch):
    monkeypatch.setenv("PIPELINE_MODEL", "qwen3.8-flash")
    assert _resolve_run_models("claude-sonnet-4-6") == ("claude-sonnet-4-6", "Anthropic")


def _call(query, urls):
    return NS(type="web_search_call", action=NS(model_dump=lambda: {
        "query": query, "queries": [query], "sources": [{"type": "url", "url": u} for u in urls]}))


def _msg(text):
    return NS(type="message", content=[NS(text=text)])


def test_responses_output_gives_search_count_queries_and_deduped_sources():
    out = [_msg("I'll search."), _call("q1", ["https://a", "https://b"]),
           _call("q2", ["https://b", "https://c"]), _msg("R" * 2500)]
    n, queries, cits = dr._responses_search_sources(out)
    assert n == 2 and queries == ["q1", "q2"]
    assert [c["url"] for c in cits] == ["https://a", "https://b", "https://c"]
    assert set(cits[0]) == {"url", "title", "cited_text"}          # Anthropic citation shape
    assert dr._responses_report_text(out) == "R" * 2500            # narration dropped


def test_short_last_message_keeps_every_message():
    out = [_msg("part one"), _call("q", []), _msg("part two")]
    assert dr._responses_report_text(out) == "part one\n\npart two"


def test_search_api_defaults_to_responses_with_chat_kill_switch(monkeypatch):
    monkeypatch.delenv("QWEN_SEARCH_API", raising=False)
    assert dr._qwen_search_api() == "responses"
    monkeypatch.setenv("QWEN_SEARCH_API", "chat")
    assert dr._qwen_search_api() == "chat"


def test_guidance_parser_survives_a_bare_dollar_dot():
    # S68.SI live run, 2026-11-26: "$." in the report crashed the whole run.
    txt = "Revenue outlook: . Capex guidance: $1.2B"
    assert isinstance(dr._extract_management_guidance(txt), dict)


def test_registry_seed_keeps_each_searchs_top_sources_only():
    out = [_call("q", [f"https://s/{i}" for i in range(20)])]
    assert len(dr._responses_search_sources(out)[2]) == 5


def test_freshness_query_leads_with_the_company_name(monkeypatch):
    import src.research_ideas.complacency.web_research as wr
    from src.memory import freshness
    seen = {}
    monkeypatch.setattr(dr, "_fetch_company_name", lambda t: "Singapore Telecommunications Limited")
    monkeypatch.setattr(wr, "qwen_web_search", lambda q, **k: seen.setdefault("q", q) and "ok")
    freshness._search_fresh_snippets("Z74.SI", "2026-11-25", None, 5.0)
    assert seen["q"].startswith("Singapore Telecommunications Limited (Z74.SI)")
