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
from datetime import datetime, timedelta

_SEARCH_TIMEOUT_S = 45.0
_SEARCH_MAX_CHARS = 6000
# R2: how much of the freshness snippets travel with the verdict for the
# one-search delta synthesis (state blob stays small; synthesis prompts
# truncate further themselves).
_SNIPPETS_KEEP_CHARS = 8000


def _freshness_enabled() -> bool:
    return os.environ.get("FRESHNESS_DELTA_SEARCH", "true").strip().lower() not in (
        "0", "false", "no", "off", "")


def _window_days() -> float:
    """Fast-path search window (L5): how many days BEFORE the search's own
    date/time the one freshness search covers. Default 3 (covers the 2–3
    days the user cares about); env-tunable, never zero/negative."""
    try:
        v = float(os.environ.get("FRESHNESS_SEARCH_WINDOW_DAYS", "3"))
        return v if v > 0 else 3.0
    except (TypeError, ValueError):
        return 3.0


def base_delta(prior: dict | None) -> dict:
    """Well-formed soft-fail shape every consumer can rely on."""
    prior = prior or {}
    return {
        "material": None,
        "events": [],
        "verdict": "check unavailable",
        # R2: the raw search snippets the classification was based on —
        # the one-search delta route synthesizes amendments from these
        # instead of running fresh searches. Empty when nothing usable.
        "snippets": "",
        "based_on_run": prior.get("run_id"),
        "prior_run_at": prior.get("run_at"),
    }


def classify_delta(ticker: str, prior: dict, snippets: str, since: str = "") -> dict | None:
    """Fast-tier LLM pass over one search result set. Returns
    {material, events, verdict} or None on any failure (soft-fail).
    `since`, when given, is the search window start — echoed into the
    prompt so the verdict states the span it actually covers."""
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
            "earnings releases, product launches/releases, guidance, M&A, "
            "regulatory, macro-sector or thesis-level developments count as "
            "material — routine price moves do not. Earnings releases and "
            "product launches are the highest-priority signals. "
            "Respond in JSON format."
        )
        _span = f" since {since}" if since else " since then"
        human = (
            f"Ticker: {ticker}\n"
            f"Prior report ({str(prior.get('run_at') or '')[:10]}): "
            f"{prior.get('final_action') or 'N/A'}, "
            f"price target {recap_json.get('price_target')} — "
            f"{(prior.get('recap_text') or '')[:400]}\n"
            f"Key assumptions: {assumptions}\n"
            f"Watched catalysts: {catalysts}\n\n"
            f"Fresh search results{_span}:\n{snippets[:4000]}\n\n"
            "Classify: material (bool), events (max 5, only genuinely material "
            "ones, ordered with earnings releases and product launches first, "
            "each with headline/date/relevance to the prior thesis), "
            "verdict (one sentence on whether the prior report is still current, "
            "naming the covered news span).\n"
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
    """Qwen primary, Tavily secondary. Returns '' when nothing usable.
    L5: query leads with earnings releases and product launches — the two
    event classes that most often move a thesis inside a 2–3 day window."""
    query = (
        f"{ticker} earnings results or announcement, product launch or release "
        f"since {since}, then guidance M&A regulatory news"
    )
    try:
        from src.research_ideas.complacency.web_research import qwen_web_search
        text = qwen_web_search(
            query + ". Prioritise earnings releases and product launches. "
                    "List dated developments; state explicitly if nothing "
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
    `since_date`. Soft-fail: always returns a well-formed delta dict —
    never raises.

    L5 basis (when `since_date` is not given): the search covers the
    window max(prior_report_date, now − FRESHNESS_SEARCH_WINDOW_DAYS)
    (default 3 days before the search's own date/time). Older reports no
    longer open an ever-wider search window — the memory layer carries the
    older history, the one search patches the recent window, and the
    verdict/addendum state the span they cover.
    """
    base = base_delta(prior)
    if not _freshness_enabled():
        base["verdict"] = "check disabled"
        return base
    if since_date:
        since = since_date
    else:
        _prior_date = str((prior or {}).get("run_at") or "")[:10]
        _window_start = (
            datetime.now() - timedelta(days=_window_days())
        ).strftime("%Y-%m-%d")
        # ISO dates compare lexicographically; the later of the two anchors
        # the window. No prior at all → search the bare window.
        since = max(_prior_date, _window_start) if _prior_date else _window_start
    base["since"] = since
    if tavily_key is None:
        tavily_key = os.environ.get("TAVILY_API_KEY") or None
    try:
        snippets = _search_fresh_snippets(ticker, since, tavily_key, request_timeout_s)
        if not snippets:
            base["verdict"] = "no fresh results"
            return base
        # R2: keep what the classification saw so the delta route can
        # synthesize amendments from THE one search instead of re-searching.
        base["snippets"] = snippets[:_SNIPPETS_KEEP_CHARS]
        classified = classify_delta(ticker, prior or {}, snippets, since=since)
        if classified:
            base.update(classified)
        return base
    except Exception as exc:
        print(f"  [freshness] {ticker}: freshness check failed: {exc}")
        return base


def publish_pulse_delta(ticker: str, delta: dict) -> None:
    """Write one freshness delta into the shared Pulse cache
    (complacency_web_research, kind='pulse', same-day gate on read).

    Shared write side of the pulse cache: the Pulse endpoint writes here
    after its beat-2 search, and the pipeline writes here after 2_9 — so
    any completed run today makes the rest of today's pulses instant on
    beat 2 (mirror of the pipeline's same-day pulse-cache reuse).
    Soft-fail: the cache is a courtesy; a write failure must never break
    the caller."""
    try:
        from datetime import timezone as _tz
        from src.research_ideas.complacency import web_research as _wr
        _today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
        _wr._cache_put(ticker, "pulse", "",
                       {"pulse_date": _today, "delta": delta})
    except Exception as exc:
        print(f"  [freshness] {ticker}: pulse cache write failed: {exc}")
