"""Data problems are never presented as clean data (audit, 2026-11-27).

1. Qwen Responses research counts only COMPLETED web_search calls and is
   degraded below the live floor or with zero citations; the chat-search
   fallback (uncountable) keeps its old treatment.
2. Tavily / Anthropic searches that errored or returned nothing do not count
   toward the live-search floor.
3. A partial FMP statement fetch is not cached and is recorded as a data gap.
4. The gap reaches report_disclosures data_limitations (rendered on the web by
   DataLimitationsBanner and in the PDF).
No network: every provider is faked.
"""
import sys
from types import SimpleNamespace as NS

import pytest

from src.agents.industry import deep_research as dr
from src.tools import api
from src.utils import report_disclosures as rd


# ── 1. Qwen Responses search: completed calls only ───────────────────────────

def _call(query, urls, status="completed"):
    return NS(type="web_search_call", status=status, action=NS(model_dump=lambda: {
        "query": query, "queries": [query], "sources": [{"type": "url", "url": u} for u in urls]}))


def test_only_completed_web_search_calls_count():
    out = [_call("ok", ["https://a"]), _call("bad", ["https://b"], status="failed"),
           _call("busy", [], status="in_progress"), _call("searching", [], status="searching")]
    n, queries, cits = dr._responses_search_sources(out)
    assert n == 1 and queries == ["ok"]
    assert [c["url"] for c in cits] == ["https://a"]       # a failed call's sources are not evidence


def test_web_search_call_without_status_is_not_counted():
    item = NS(type="web_search_call", action=NS(model_dump=lambda: {"query": "q", "sources": []}))
    assert dr._responses_search_sources([item])[0] == 0


def test_countable_qwen_with_zero_searches_is_degraded():
    is_live, degraded, label = dr._research_evidence_state("qwen_web", 0, countable=True, citation_count=0)
    assert degraded and not is_live
    assert "DEGRADED" in label


def test_countable_qwen_below_floor_is_degraded():
    is_live, degraded, _ = dr._research_evidence_state("qwen_web", dr._MIN_LIVE_SEARCHES - 1,
                                                       countable=True, citation_count=12)
    assert degraded and not is_live


def test_countable_qwen_with_searches_but_no_citations_is_degraded():
    is_live, degraded, label = dr._research_evidence_state("qwen_web", 6, countable=True, citation_count=0)
    assert degraded and not is_live
    assert "no cited sources" in label


def test_countable_qwen_above_floor_with_citations_is_live():
    is_live, degraded, label = dr._research_evidence_state("qwen_web", 6, countable=True, citation_count=9)
    assert is_live and not degraded
    assert "LIVE WEB DATA" in label


def test_qwen_chat_fallback_stays_uncountable():
    # The chat enable_search path cannot count: unchanged behaviour.
    is_live, degraded, _ = dr._research_evidence_state("qwen_web", 1)
    assert is_live and not degraded


# ── 2. Tavily / Anthropic: failed searches are attempts, not evidence ────────

def _fake_tavily(monkeypatch, search):
    monkeypatch.setitem(sys.modules, "tavily", NS(TavilyClient=lambda api_key: NS(search=search)))


def test_tavily_error_string_is_not_a_success(monkeypatch):
    def _boom(**k):
        raise RuntimeError("401 unauthorized")
    _fake_tavily(monkeypatch, _boom)
    res = dr._search_web("q", "key")
    assert res.startswith("Search error:")
    assert not dr._tavily_result_ok(res)


def test_tavily_no_results_is_not_a_success(monkeypatch):
    _fake_tavily(monkeypatch, lambda **k: {"results": []})
    res = dr._search_web("q", "key")
    assert not dr._tavily_result_ok(res)


def test_tavily_real_results_are_a_success(monkeypatch):
    _fake_tavily(monkeypatch, lambda **k: {"results": [
        {"title": "T", "content": "body", "url": "https://x", "published_date": "2026-11-01"}]})
    sink = []
    res = dr._search_web("q", "key", citation_sink=sink)
    assert dr._tavily_result_ok(res) and sink[0]["url"] == "https://x"


def _result(content):
    return NS(type="web_search_tool_result", content=content)


def test_anthropic_errored_search_result_is_not_counted():
    err = NS(type="web_search_tool_result_error", error_code="max_uses_exceeded")
    blocks = [NS(type="server_tool_use", name="web_search"), _result(err),
              NS(type="server_tool_use", name="web_search"), _result([]),
              NS(type="server_tool_use", name="web_search"), _result([NS(url="https://ok")]),
              NS(type="server_tool_use", name="web_search"),
              _result({"type": "web_search_tool_result_error", "error_code": "unavailable"})]
    assert dr._count_successful_web_searches(blocks) == 1


def test_coverage_nudge_does_not_count_errored_searches():
    err = NS(type="web_search_tool_result_error", error_code="unavailable")
    resp = NS(content=[NS(type="server_tool_use", name="web_search"), _result(err),
                       NS(type="server_tool_use", name="web_search"), _result(err),
                       NS(type="text", text="re-issued report", citations=[])])
    client = NS(messages=NS(create=lambda **k: resp))
    text, n = dr._coverage_nudge(client, "m", "sys", "human", [object()], "thin", 1, set(), [], "TEST")
    assert text == "re-issued report"
    assert n == 1                                    # the two errored searches added nothing


# ── 3. FMP partial statement fetch: not cached, recorded ─────────────────────

@pytest.fixture
def _fmp(monkeypatch):
    api._STATEMENT_CACHE.clear()
    api._STATEMENT_GAPS.clear()
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    calls = []
    payload = {}

    def _get(path, params, api_key, uncap=False):
        calls.append(path.rsplit("/", 1)[-1])
        return payload.get(path.rsplit("/", 1)[-1])
    monkeypatch.setattr(api, "_fmp_get", _get)
    yield calls, payload
    api._STATEMENT_CACHE.clear()
    api._STATEMENT_GAPS.clear()


_INC = [{"date": "2025-12-31", "revenue": 100.0, "netIncome": 10.0}]
_BAL = [{"date": "2025-12-31", "totalDebt": 50.0, "cashAndCashEquivalents": 20.0}]
_CF = [{"date": "2025-12-31", "freeCashFlow": 8.0}]


def test_partial_statements_are_not_cached_and_are_recorded(_fmp):
    calls, payload = _fmp
    payload.update({"income-statement": _INC, "balance-sheet-statement": [], "cash-flow-statement": _CF})
    t0 = api.time.time()
    api.search_line_items("ZZTEST", ["revenue", "total_debt"], "2026-11-27", period="annual", api_key="k")
    api.search_line_items("ZZTEST", ["revenue", "total_debt"], "2026-11-27", period="annual", api_key="k")
    assert calls.count("income-statement") == 2       # second call refetched: nothing cached
    gaps = api.statement_gaps("ZZTEST", since=t0)
    assert gaps == [{"period": "annual", "missing": ["balance"]}]
    assert api.statement_gaps("ZZTEST", since=api.time.time() + 60) == []   # scoped to the run window
    assert "balance sheet" in api.statement_gap_text(gaps)[0]


def test_complete_statements_are_cached_and_record_no_gap(_fmp):
    calls, payload = _fmp
    payload.update({"income-statement": _INC, "balance-sheet-statement": _BAL, "cash-flow-statement": _CF})
    out = api.search_line_items("ZZFULL", ["revenue", "total_debt"], "2026-11-27", period="annual", api_key="k")
    api.search_line_items("ZZFULL", ["revenue", "total_debt"], "2026-11-27", period="annual", api_key="k")
    assert calls.count("income-statement") == 1
    assert api.statement_gaps("ZZFULL") == []
    assert getattr(out[0], "total_debt", None) == 50.0


# ── 4. The gap reaches data_limitations ──────────────────────────────────────

def test_fmp_gap_reaches_data_limitations():
    data = {"fmp_data_gaps": {"ZZTEST": [{"period": "annual", "missing": ["balance", "cashflow"]}]}}
    lims = rd.data_limitations(data, "ZZTEST")
    assert any("balance sheet, cash-flow statement" in x for x in lims)
    assert rd.data_limitations(data, "OTHER") == []      # per ticker, not run-wide


def test_search_floor_reason_text_is_carried_into_limitations():
    data = {"research_degraded": True,
            "research_degraded_reasons": [{"source": "search_floor",
                                           "text": "Live research returned no cited sources, so its "
                                                   "narrative is unverified."}]}
    assert "Live research returned no cited sources, so its narrative is unverified." in \
        rd.data_limitations(data, "T")
