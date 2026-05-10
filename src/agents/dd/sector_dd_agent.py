"""
sector_dd_agent.py — LLM agent for sector-cluster DD investigations.

Mirrors the architecture of dd_agent.py but framed for sector-level analysis:

  • Pre-fetches sector ETF context (e.g. XLK for Tech, XLF for Financials,
    SMH for Semis) — gives the LLM macro context "what's the sector ETF
    doing while these constituents move?"
  • Skips the per-ticker filings + insider pulls (cluster is too broad)
  • Calls Qwen via DashScope Anthropic-compat with web_search_20260209
  • Uses the sector-specific prompts (sector_prompts.py)

Returns a SectorDdReport with the same structural shape as DdReport so
the dashboard can render both as the same AlertCard component.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
from pydantic import BaseModel, Field, ValidationError

from src.agents.industry.deep_research import (
    _call_llm_with_rate_retry,
    _parse_llm_json,
    _WEB_SEARCH_TOOL,
)
from src.agents.dd.dd_agent import (
    DDAgentError,
    DD_CLIENT_TIMEOUT_SEC,
    DD_DEFAULT_MODEL,
    DD_MAX_OUTPUT_TOKENS,
    NewsDriver,
    Filing,
)
from src.agents.dd.sector_prompts import (
    select_sector_prompt,
    build_sector_user_message,
)


logger = logging.getLogger(__name__)


# ── Schema ──────────────────────────────────────────────────────────────────
# Identical shape to DdReport so the frontend AlertCard renders both
# uniformly. The "sector" field is added so the dashboard can label the
# cluster card as a sector alert.

class SectorDdReport(BaseModel):
    sector:             str  = Field(default="")
    direction:          str  = Field(default="")
    cluster_members:    list[str] = Field(default_factory=list)
    cause_summary:      str  = Field(default="")
    thesis_impact:      str  = Field(default="")
    recommended_action: str  = Field(default="")
    news_drivers:       list[NewsDriver] = Field(default_factory=list)
    filings:            list[Filing]     = Field(default_factory=list)
    insider_signal:     str  = Field(default="n/a (cluster too broad)")


# ── Sector ETF mapping (for quick macro context in the prompt) ──────────────


_SECTOR_ETF_MAP = {
    "Tech":          "XLK",
    "Semiconductor": "SMH",
    "Banks":         "KBE",
    "Financials":    "XLF",
    "Energy":        "XLE",
    "Healthcare":    "XLV",
    "Biotech":       "XBI",
    "Industrials":   "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Utilities":     "XLU",
    "RealEstate":    "XLRE",
    "Materials":     "XLB",
    "Communication Services": "XLC",
}


def _fetch_sector_etf_context(sector: str) -> str | None:
    """Try to fetch the matching sector ETF's daily change for context.

    The LLM uses this to answer "is the cluster moving with or against
    the sector ETF?" — useful for distinguishing sector-wide stories from
    name-specific noise that happens to overlap.

    Returns a one-line string or None on failure. Best-effort.
    """
    etf = _SECTOR_ETF_MAP.get(sector)
    if not etf:
        return None
    try:
        from src.agents.dd.batch_quote import fetch_batch_quotes
        quotes = fetch_batch_quotes([etf])
        q = quotes.get(etf)
        if q is None:
            return None
        sign = "+" if q.changes_percentage >= 0 else ""
        return (
            f"Sector ETF {etf} today: {sign}{q.changes_percentage * 100:.2f}% "
            f"@ ${q.price:.2f} — {'matches' if abs(q.changes_percentage) >= 0.02 else 'lighter than'} "
            f"the cluster magnitude."
        )
    except Exception as exc:
        logger.warning("sector_dd_agent: ETF context fetch failed for %s: %s", sector, exc)
        return None


# ── Top-level orchestrator ──────────────────────────────────────────────────


def run_sector_dd_agent(
    *,
    sector:    str,
    direction: str,
    members:   list[tuple[str, float, float]],   # [(ticker, pct, price), ...]
) -> SectorDdReport:
    """Produce a sector-level DD report for a fired cluster.

    Args:
      sector:    e.g. "Tech", "Semiconductor"
      direction: "DROP" or "PUMP"
      members:   list of (ticker, pct_change_decimal, price) for each
                 cluster constituent

    Returns:
      Validated SectorDdReport. Always returns a complete schema even if
      partial — defaults rather than throwing on missing fields.

    Raises:
      DDAgentError: pipeline can't produce ANY parseable report. Caller
                    (route's background thread) catches + falls back to a
                    synthetic placeholder so the cluster alert is preserved.
    """
    if not members:
        raise DDAgentError(f"Cannot run sector agent on empty {sector} cluster")

    logger.info(
        "sector_dd_agent: %s/%s starting (members=%d)",
        sector, direction, len(members),
    )

    # Median pct for the headline + prompt context
    pcts = sorted(m[1] for m in members)
    median_pct = pcts[len(pcts) // 2]

    # Pre-fetch sector ETF context (best-effort)
    etf_ctx = _fetch_sector_etf_context(sector)

    # Build prompts
    system_prompt, prompt_id = select_sector_prompt(direction)
    user_message = build_sector_user_message(
        sector=sector,
        direction=direction,
        members=members,
        median_pct=median_pct,
    )
    if etf_ctx:
        user_message += f"\n\n## Sector ETF context\n{etf_ctx}\n"

    # Build Qwen client (reuse env vars from dd_agent — same DashScope endpoint)
    api_key  = os.environ.get("DEEP_RESEARCH_API_KEY")
    base_url = os.environ.get("DEEP_RESEARCH_BASE_URL")
    if not api_key or not base_url:
        raise DDAgentError(
            "DEEP_RESEARCH_API_KEY and DEEP_RESEARCH_BASE_URL must be set"
        )
    client = anthropic.Anthropic(
        api_key=api_key, base_url=base_url,
        timeout=DD_CLIENT_TIMEOUT_SEC, max_retries=4,
    )
    model = os.environ.get("DD_AGENT_MODEL") or os.environ.get("DEEP_RESEARCH_MODEL") or DD_DEFAULT_MODEL

    # Invoke
    try:
        response = _call_llm_with_rate_retry(
            client,
            extractor_name=f"sector_dd_agent_{direction.lower()}",
            ticker=f"{sector}/{direction}",
            model=model,
            max_tokens=DD_MAX_OUTPUT_TOKENS,
            system=system_prompt,
            tools=[_WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise DDAgentError(
            f"Qwen API call failed for sector cluster {sector}/{direction}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

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
            f"Qwen returned empty text for sector cluster {sector}/{direction} "
            f"after {n_searches} searches"
        )

    logger.info(
        "sector_dd_agent: %s/%s — %d web searches, %d chars",
        sector, direction, n_searches, len(text),
    )

    # Parse
    parsed: Any = _parse_llm_json(text, extractor_name=f"sector_dd_agent_{direction.lower()}")
    if not isinstance(parsed, dict):
        raise DDAgentError(
            f"Sector agent output for {sector}/{direction} is not a JSON object "
            f"(got {type(parsed).__name__})"
        )

    # Augment with cluster metadata + validate
    parsed.setdefault("sector", sector)
    parsed.setdefault("direction", direction)
    parsed.setdefault("cluster_members", [m[0] for m in members])

    try:
        return SectorDdReport.model_validate(parsed)
    except ValidationError as exc:
        # Coerce common issues (string in place of list)
        coerced = dict(parsed)
        for k in ("news_drivers", "filings", "cluster_members"):
            v = coerced.get(k)
            if not isinstance(v, list):
                coerced[k] = [] if k != "cluster_members" else [m[0] for m in members]
        try:
            return SectorDdReport.model_validate(coerced)
        except ValidationError:
            raise DDAgentError(
                f"Sector agent output for {sector}/{direction} failed schema validation: {exc}"
            ) from exc
