"""
src/memory/freshness.py
=======================
M2 Track A1 — one shared freshness-search primitive.

One bounded web search per ticker + a fast-tier classification of whether
anything MATERIAL changed since a given date (usually the prior report).
Two consumers share this function:

  * pipeline phase 2_9 (`pipeline._delta_for_ticker` → full runs)
  * the Pulse endpoint (Track C — instant "recent developments" on search)

Design rules:
  * Qwen native web search is the PRIMARY provider (2026-08-09 directive —
    all research defaults to Qwen web search). Tavily is only a secondary
    fallback when a key exists AND Qwen returned nothing (Tavily was over
    quota in prod as of 2026-08, which silently killed the M1 delta).
  * Every failure mode is soft — callers always get a well-formed dict:
    {material: bool|None, events: [...], verdict: str, based_on_run,
     prior_run_at}. material=None means "no classification obtained".
  * Kill switch FRESHNESS_DELTA_SEARCH=false disables the search for both
    consumers (verdict "check disabled").
"""
from __future__ import annotations

import os

_SEARCH_TIMEOUT_S = 45.0
_SEARCH_MAX_CHARS = 6000


def _freshness_enabled() -> bool:
    return os.environ.get("FRESHNESS_DELTA_SEARCH", "true").strip().lower() not in (
        "0", "false", "no", "off", "")


def base_delta(prior: dict | None) -> dict:
    """Well-formed soft-fail shape every consumer can rely on."""
    prior = prior or {}
    return {
        "material": None,
        "events": [],
        "verdict": "check unavailable",
        "based_on_run": prior.get("run_id"),
        "prior_run_at": prior.get("run_at"),
    }


def classify_delta(ticker: str, prior: dict, snippets: str) -> dict | None:
    """Fast-tier LLM pass over one search result set. Returns
    {material, events, verdict} or None on any failure (soft-fail)."""
    try:
        from pydantic import BaseModel, Field

        class DeltaEvent(BaseModel):
            headline: str = ""
            date: str = ""
            relevance: str = ""

        class DeltaClassification(BaseModel):
            material: bool = Field(
                default=False,
                description="True if any event meaningfully changes the prior thesis")
            events: list[DeltaEvent] = Field(default_factory=list)
            verdict: str = Field(
                default="",
                description="One sentence: is the prior report still current and why")

        from src.llm.models import ModelProvider, get_model
        from src.memory.report_recap import RECAP_MODEL_NAME
        provider = ModelProvider.ALIBABA
        if RECAP_MODEL_NAME.lower().startswith(("gpt", "o1", "o3", "o4")):
            provider = ModelProvider.OPENAI
        llm = get_model(RECAP_MODEL_NAME, provider, None)
        if llm is None:
            return None

        recap_json = prior.get("recap_json") or {}
        assumptions = "; ".join((recap_json.get("assumptions") or [])[:5]) or "(none recorded)"
        catalysts = "; ".join((recap_json.get("catalysts") or [])[:5]) or "(none recorded)"

        system = (
            "You check whether a prior equity research report is still current. "
            "Compare fresh news against the prior thesis. Be strict: only "
            "earnings, guidance, M&A, regulatory, macro-sector or thesis-level "
            "developments count as material — routine price moves do not. "
            "Respond in JSON format."
        )
        human = (
            f"Ticker: {ticker}\n"
            f"Prior report ({str(prior.get('run_at') or '')[:10]}): "
            f"{prior.get('final_action') or 'N/A'}, "
            f"price target {recap_json.get('price_target')} — "
            f"{(prior.get('recap_text') or '')[:400]}\n"
            f"Key assumptions: {assumptions}\n"
            f"Watched catalysts: {catalysts}\n\n"
            f"Fresh search results since then:\n{snippets[:4000]}\n\n"
            "Classify: material (bool), events (max 5, only genuinely material "
            "ones, each with headline/date/relevance to the prior thesis), "
            "verdict (one sentence on whether the prior report is still current).\n"
            "Return a JSON object: {\"material\": true|false, \"events\": "
            "[{\"headline\": \"...\", \"date\": \"...\", \"relevance\": \"...\"}], "
            "\"verdict\": \"...\"}"
        )
        messages = [("system", system), ("human", human)]
        try:
            from src.research_ideas.complacency import qwen_throttle
            qwen_throttle.acquire(weight=1.0)
        except Exception:
            pass  # throttle is a courtesy; never block the delta check on it

        structured_llm = llm.with_structured_output(DeltaClassification, method="json_mode")
        try:
            out = structured_llm.invoke(messages)
        except Exception:
            import json as _json
            raw = llm.invoke(messages)
            text = raw.content if hasattr(raw, "content") else str(raw)
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            out = DeltaClassification(**_json.loads(text[start:end + 1]))

        return {
            "material": bool(out.material),
            "events": [
                {"headline": (e.headline or "")[:200],
                 "date": (e.date or "")[:16],
                 "relevance": (e.relevance or "")[:300]}
                for e in (out.events or [])[:5]
            ],
            "verdict": (out.verdict or "")[:500],
        }
    except Exception as exc:
        print(f"  [freshness] {ticker}: classification failed: {exc}")
        return None


def _search_fresh_snippets(
    ticker: str,
    since: str,
    tavily_key: str | None,
    request_timeout_s: float,
) -> str:
    """Qwen primary, Tavily secondary. Returns '' when nothing usable."""
    query = (
        f"{ticker} stock material news earnings guidance M&A regulatory since {since}"
    )
    try:
        from src.research_ideas.complacency.web_research import qwen_web_search
        text = qwen_web_search(
            query + ". List dated developments; state explicitly if nothing "
                    "material happened.",
            system_prompt=(
                "You check whether fresh news materially changes a prior "
                "equity research report. Report only dated, factual "
                "developments."
            ),
            max_chars=_SEARCH_MAX_CHARS,
            request_timeout_s=request_timeout_s,
        )
        if text:
            return text
    except Exception as exc:
        print(f"  [freshness] {ticker}: qwen search failed: {exc}")
    if tavily_key:
        try:
            from src.agents.industry.deep_research import _search_web
            snippets = _search_web(query, tavily_key)
            if snippets and not snippets.startswith(("Search error", "No results")):
                return snippets
        except Exception as exc:
            print(f"  [freshness] {ticker}: tavily fallback failed: {exc}")
    return ""


def run_freshness_search(
    ticker: str,
    prior: dict | None,
    since_date: str | None = None,
    tavily_key: str | None = None,
    request_timeout_s: float = _SEARCH_TIMEOUT_S,
) -> dict:
    """One bounded search + classification for a single ticker since
    `since_date` (defaults to the prior report date). Soft-fail: always
    returns a well-formed delta dict — never raises."""
    base = base_delta(prior)
    if not _freshness_enabled():
        base["verdict"] = "check disabled"
        return base
    since = (
        since_date
        or str((prior or {}).get("run_at") or "")[:10]
        or "the last report"
    )
    if tavily_key is None:
        tavily_key = os.environ.get("TAVILY_API_KEY") or None
    try:
        snippets = _search_fresh_snippets(ticker, since, tavily_key, request_timeout_s)
        if not snippets:
            base["verdict"] = "no fresh results"
            return base
        classified = classify_delta(ticker, prior or {}, snippets)
        if classified:
            base.update(classified)
        return base
    except Exception as exc:
        print(f"  [freshness] {ticker}: freshness check failed: {exc}")
        return base
