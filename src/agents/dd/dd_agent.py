"""
dd_agent.py — Real LLM-driven Due Diligence agent.

Replaces the synthetic placeholder report from app/backend/routes/dd_alerts.py
with a structured DD brief grounded in:

  * Recent price action (last 30d via FMP get_prices)
  * Insider transactions (last 30d via SEC EDGAR Form 4 — free)
  * Recent SEC filings (last 30d via recent_filings.py)
  * Live web search (Anthropic web_search_20260209 tool via Qwen on DashScope)

Architecture:

    run_dd_agent(...)
      ├─ _fetch_price_context(ticker)       — formatted 30-day OHLC text block
      ├─ _fetch_insider_summary(ticker)     — buys/sells aggregated for prompt
      ├─ get_recent_filings(ticker)         — last 30d filings list
      ├─ select_prompt(direction, prior, reason)  — picks 1 of 8 system prompts
      ├─ build_user_message(...)            — assembles case file
      ├─ _call_qwen_with_search(...)        — Anthropic SDK → DashScope, web_search tool
      └─ _parse_dd_report(text)             — robust JSON → DdReport Pydantic model

LLM choice:
  Qwen 3.6-plus via DashScope International (Anthropic-compatible endpoint).
  Same provider deep_research.py uses. Web search tool is the v20260209 GA
  surface — no anthropic-beta header required.

Failure modes:
  Every public call returns either a valid DdReport OR raises DDAgentError.
  Caller (the route's background thread) catches DDAgentError and falls back
  to a synthetic placeholder so the alert is never lost.

Model invocation pattern is borrowed from src/agents/industry/deep_research.py
including the _call_llm_with_rate_retry wrapper (DashScope returns HTTP 403
not 429 for rate limits — the SDK's built-in retry doesn't catch it).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
from pydantic import BaseModel, Field, ValidationError

# Reuse battle-tested helpers from deep_research.py:
#   _call_llm_with_rate_retry — DashScope 403 retry + Retry-After
#   _parse_llm_json           — robust JSON extractor (handles preamble/postamble)
#   _WEB_SEARCH_TOOL          — Anthropic web_search_20260209 tool spec
from src.agents.industry.deep_research import (
    _call_llm_with_rate_retry,
    _parse_llm_json,
    _WEB_SEARCH_TOOL,
)
from src.agents.dd.prompts import select_prompt, build_user_message
from src.agents.dd.recent_filings import (
    get_recent_filings,
    format_filings_for_prompt,
)


logger = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────────────
# DD reports are SHORT structured JSON — not 50-page synthesis. 4k output
# tokens is enough headroom for a 6-key JSON object with up to 5 news + 5
# filing entries. Lower than deep_research's 64k → faster + cheaper.
DD_MAX_OUTPUT_TOKENS = 4096

# Web search + synthesis. deep_research uses 720s for 10 searches + full
# report. DD does 3-6 searches + small JSON, so 240s is comfortable.
DD_CLIENT_TIMEOUT_SEC = 240.0

# Default model. Override via DD_AGENT_MODEL env var if needed (e.g. for A/B
# against qwen3-max or claude-sonnet-4-6 on the same DashScope account).
DD_DEFAULT_MODEL = "qwen3.6-plus"


# ── Public schema ───────────────────────────────────────────────────────────
# Mirrors app/frontend/src/lib/reportTypes.ts::DdReport. The web dashboard
# unmarshals this exact shape from web_runs.full_result_json.

class NewsDriver(BaseModel):
    title:         str
    url:           str | None = None
    publishedDate: str | None = None


class Filing(BaseModel):
    form:        str | None = None
    filing_date: str | None = None
    url:         str | None = None
    summary:     str | None = None


class DdReport(BaseModel):
    cause_summary:      str  = Field(default="")
    thesis_impact:      str  = Field(default="")
    recommended_action: str  = Field(default="")
    news_drivers:       list[NewsDriver] = Field(default_factory=list)
    filings:            list[Filing]     = Field(default_factory=list)
    insider_signal:     str  = Field(default="n/a")


class DDAgentError(RuntimeError):
    """Raised when the DD agent cannot produce a valid DdReport.

    Caller (the route background thread) should catch this and fall back to
    the synthetic placeholder so the alert pipeline never silently drops a
    fired alert.
    """


# ── Qwen client construction ────────────────────────────────────────────────


def _build_qwen_client() -> tuple[anthropic.Anthropic, str]:
    """Return (Anthropic SDK client pointing at DashScope, model name).

    Reads DEEP_RESEARCH_API_KEY and DEEP_RESEARCH_BASE_URL from env. Mirrors
    deep_research.py's pattern. Raises DDAgentError if either env var missing
    (fail-fast — caller falls back to synthetic).
    """
    api_key  = os.environ.get("DEEP_RESEARCH_API_KEY")
    base_url = os.environ.get("DEEP_RESEARCH_BASE_URL")
    if not api_key or not base_url:
        raise DDAgentError(
            "DEEP_RESEARCH_API_KEY and DEEP_RESEARCH_BASE_URL must be set "
            "(DashScope International Anthropic-compat endpoint)"
        )

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url,
        timeout=DD_CLIENT_TIMEOUT_SEC,
        max_retries=4,
    )
    model = os.environ.get("DD_AGENT_MODEL") or os.environ.get("DEEP_RESEARCH_MODEL") or DD_DEFAULT_MODEL
    return client, model


# ── Data fetchers (graceful degradation — return None on failure) ──────────


def _fetch_price_context(ticker: str, lookback_days: int = 30) -> str | None:
    """Pull last 30 days of EOD prices and format as a brief text block.

    Returns None if FMP is unavailable or returns empty data. The agent can
    still produce a report without this — it just lacks chart context.
    """
    try:
        from src.tools.api import get_prices  # local import — keeps top-level light

        end   = datetime.now(timezone.utc).date()
        start = end - timedelta(days=lookback_days)
        prices = get_prices(ticker, start.isoformat(), end.isoformat())
        if not prices:
            return None

        # Most recent first; cap to ~10 rows so the prompt stays compact.
        prices = sorted(prices, key=lambda p: p.time, reverse=True)[:10]
        first = prices[-1]   # oldest in our window
        last  = prices[0]    # newest

        try:
            change_pct = (last.close - first.close) / first.close
        except (AttributeError, ZeroDivisionError, TypeError):
            change_pct = 0.0

        sign  = "+" if change_pct >= 0 else ""
        lines = [f"30-day move: {sign}{change_pct * 100:.1f}% (${first.close:.2f} → ${last.close:.2f})"]
        lines.append("Last 10 sessions (most recent first):")
        for p in prices:
            lines.append(f"  {p.time}: O={p.open:.2f} H={p.high:.2f} L={p.low:.2f} C={p.close:.2f} V={p.volume:,}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("dd_agent: price context fetch failed for %s: %s", ticker, exc)
        return None


def _fetch_insider_summary(ticker: str, lookback_days: int = 30) -> str | None:
    """Aggregate Form 4 insider transactions into a one-paragraph summary.

    Free SEC EDGAR fallback (no FMP Ultimate tier required). Returns None on
    failure. Format mirrors what an analyst would write at the top of a brief.
    """
    try:
        from src.tools.api import get_insider_trades_edgar  # local import

        end   = datetime.now(timezone.utc).date()
        start = end - timedelta(days=lookback_days)
        trades = get_insider_trades_edgar(ticker, start.isoformat(), end.isoformat(), max_filings=20)
        if not trades:
            return "No Form 4 filings in last 30 days (insider activity quiet)."

        n_buys  = 0
        n_sells = 0
        buy_value  = 0.0
        sell_value = 0.0
        for t in trades:
            shares = t.transaction_shares or 0.0
            value  = t.transaction_value  or 0.0
            if shares > 0:
                n_buys += 1
                buy_value += value
            elif shares < 0:
                n_sells += 1
                sell_value += abs(value)

        if n_buys == 0 and n_sells == 0:
            return f"{len(trades)} Form 4 filings (no net buys or sells parseable)."

        return (
            f"Last 30 days: {n_buys} insider buy(s) totaling ~${buy_value:,.0f} · "
            f"{n_sells} insider sell(s) totaling ~${sell_value:,.0f}."
        )
    except Exception as exc:
        logger.warning("dd_agent: insider summary fetch failed for %s: %s", ticker, exc)
        return None


# ── LLM call ────────────────────────────────────────────────────────────────


def _call_qwen_with_search(
    client: anthropic.Anthropic,
    model: str,
    *,
    system_prompt: str,
    user_message: str,
    ticker: str,
) -> str:
    """Invoke Qwen with the Anthropic web_search tool, return full text.

    Borrowed pattern from deep_research.py: web_search_20260209 is server-side
    so tool_choice is intentionally omitted. Text + tool_use blocks are
    interleaved; we concatenate text blocks for the JSON extractor.

    Raises DDAgentError if the API returns nothing parseable (empty text,
    auth failure, model error). The retry wrapper handles transient
    rate-limit / timeout errors internally.
    """
    try:
        response = _call_llm_with_rate_retry(
            client,
            extractor_name="dd_agent",
            ticker=ticker,
            model=model,
            max_tokens=DD_MAX_OUTPUT_TOKENS,
            system=system_prompt,
            tools=[_WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise DDAgentError(f"Qwen API call failed for {ticker}: {type(exc).__name__}: {exc}") from exc

    text = ""
    n_searches = 0
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype in ("server_tool_use", "tool_use") and getattr(block, "name", None) == "web_search":
            n_searches += 1
        if hasattr(block, "text"):
            text += block.text

    if not text.strip():
        raise DDAgentError(
            f"Qwen returned empty text for {ticker} after {n_searches} searches "
            f"(stop_reason={getattr(response, 'stop_reason', 'unknown')})"
        )

    logger.info("dd_agent: %s — %d web searches, %d chars of text", ticker, n_searches, len(text))
    return text


def _parse_dd_report(raw_text: str, ticker: str) -> DdReport:
    """Robust-parse the LLM output into a validated DdReport.

    Two-stage:
      1. _parse_llm_json (from deep_research.py) — extracts JSON object from
         any preamble/postamble noise the model added.
      2. Pydantic DdReport.model_validate — schema enforcement + coercion.

    Returns a DdReport. Raises DDAgentError if both stages fail.
    """
    parsed: Any = _parse_llm_json(raw_text, extractor_name="dd_agent")
    if not isinstance(parsed, dict):
        raise DDAgentError(
            f"DD agent output for {ticker} is not a JSON object "
            f"(parsed type: {type(parsed).__name__})"
        )

    # Pydantic with default-on-missing means partial responses still yield a
    # usable report. ValidationError only fires on hard type mismatches
    # (e.g. news_drivers is "n/a" instead of a list).
    try:
        return DdReport.model_validate(parsed)
    except ValidationError as exc:
        # Most common failure: model returned strings instead of empty arrays.
        # Try to coerce the obvious cases before giving up.
        coerced = dict(parsed)
        for k in ("news_drivers", "filings"):
            v = coerced.get(k)
            if not isinstance(v, list):
                coerced[k] = []
        try:
            return DdReport.model_validate(coerced)
        except ValidationError:
            raise DDAgentError(
                f"DD agent output for {ticker} failed schema validation: {exc}"
            ) from exc


# ── Top-level orchestrator ──────────────────────────────────────────────────


def run_dd_agent(
    *,
    ticker: str,
    direction: str,
    pct_change: float,
    current_price: float,
    prior_direction: str | None,
    reason: str,
) -> DdReport:
    """Produce a real DD report for a fired alert.

    Args:
      ticker:         Symbol that triggered the alert.
      direction:      "DROP" or "PUMP" (current direction).
      pct_change:     Decimal (e.g. -0.11 for -11%). Sign matches direction.
      current_price:  Latest price observed when alert fired.
      prior_direction: Previous alert's direction, or None if first-ever alert.
      reason:         alert_dedup eligibility reason (drives prompt routing).

    Returns:
      A validated DdReport. Always returns a complete schema — partial fields
      use defaults rather than throwing.

    Raises:
      DDAgentError: If the pipeline can't produce ANY parseable report. The
                    route's background thread catches this and falls back to
                    a synthetic placeholder so the alert is preserved.
    """
    logger.info(
        "dd_agent: starting %s direction=%s pct=%.3f reason=%s prior=%s",
        ticker, direction, pct_change, reason, prior_direction,
    )

    # 1. Pre-fetch grounding data (best-effort, all optional)
    price_ctx        = _fetch_price_context(ticker)
    insider_summary  = _fetch_insider_summary(ticker)
    filings          = get_recent_filings(ticker, lookback_days=30, max_filings=10)
    filings_summary  = format_filings_for_prompt(filings)

    # 2. Select prompt + assemble user message
    system_prompt, prompt_id = select_prompt(direction, prior_direction, reason)
    user_message = build_user_message(
        ticker=ticker,
        direction=direction,
        pct_change=pct_change,
        current_price=current_price,
        prior_direction=prior_direction,
        reason=reason,
        price_context_30d=price_ctx,
        insider_summary=insider_summary,
        recent_filings_summary=filings_summary,
    )
    logger.info("dd_agent: %s using prompt=%s", ticker, prompt_id)

    # 3. Build client + invoke Qwen with web_search
    client, model = _build_qwen_client()
    raw_text = _call_qwen_with_search(
        client, model,
        system_prompt=system_prompt,
        user_message=user_message,
        ticker=ticker,
    )

    # 4. Parse + validate
    report = _parse_dd_report(raw_text, ticker)

    # 5. If the model produced empty filings but we DID find SEC filings,
    #    backfill with our pre-fetched list so the dashboard isn't blank.
    #    The model's filing list often misses the basic recent 8-K data we
    #    handed it because it focused on news search.
    if not report.filings and filings:
        report.filings = [
            Filing(
                form=f.form,
                filing_date=f.filing_date,
                url=f.url,
                summary=None,
            )
            for f in filings[:5]
        ]

    logger.info(
        "dd_agent: %s SUCCESS — cause=%r drivers=%d filings=%d",
        ticker, report.cause_summary[:60], len(report.news_drivers), len(report.filings),
    )
    return report
