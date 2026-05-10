"""
digest_agent.py — Phase 2E: LLM-narrated EOD digest agent.

Pulls today's dd_alerts aggregates, asks Qwen (with web search) to write
a senior-analyst end-of-day note answering "what was the dominant story
today, was it macro or micro, what to watch tomorrow."

Storage: result lives in dd_reports as a single row keyed by
  run_id = f"digest_{utc_date}" e.g. "digest_2026-05-11"
  model_name = "dd_digest_qwen"
  full_result_json = {"narrative", "key_themes", "macro_or_micro",
                      "tomorrow_watch", "drops", "pumps", "clusters",
                      "generated_at"}

Web-only delivery — Slack stays pure real-time push, no EOD spam.

Idempotent: INSERT-OR-REPLACE means re-running the digest on the same UTC
date overwrites the previous row (useful if late alerts came in after the
first run, or if the LLM had a transient failure).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
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
)
from src.agents.dd.digest_prompts import (
    select_digest_prompt,
    build_digest_user_message,
)


logger = logging.getLogger(__name__)


# ── Schema ──────────────────────────────────────────────────────────────────


class DigestNarrative(BaseModel):
    """Pydantic model for the LLM's structured digest output."""
    narrative:        str  = Field(default="")
    key_themes:       list[str] = Field(default_factory=list)
    macro_or_micro:   str  = Field(default="mixed")   # macro | micro | mixed
    tomorrow_watch:   str  = Field(default="n/a")


# ── Aggregator (read today's alerts from the DB) ────────────────────────────


def gather_today_aggregates(utc_date: str | None = None) -> dict[str, Any]:
    """Read today's dd_alerts rows + group into drops / pumps / clusters
    in the same shape the existing /api/dd-alerts/digest/today endpoint
    returns. Pure read — never mutates DB state.

    Args:
      utc_date: ISO date string (YYYY-MM-DD). Defaults to today UTC.

    Returns:
      {
        "utc_date":   "2026-05-11",
        "drops":      [{"ticker":..., "pct":..., "price":...}, ...],
        "pumps":      [...],
        "clusters":   [{"sector":..., "direction":..., "n":..., "median_pct":...}, ...],
        "n_drops":    int,
        "n_pumps":    int,
        "n_clusters": int,
      }
    """
    if utc_date is None:
        utc_date = datetime.now(timezone.utc).date().isoformat()

    from app.backend.services.analysis_service import _connect
    from src.agents.dd import alert_dedup
    # Ensure table exists in fresh DBs
    with alert_dedup._conn():
        pass

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        # Top 10 drops + pumps by magnitude
        # NOTE: cluster_member rows are EXCLUDED from individual drops/pumps —
        # they're already aggregated under the cluster row.
        drops = conn.execute(
            "SELECT ticker, trigger_pct AS pct, trigger_price AS price "
            "FROM dd_alerts "
            "WHERE last_triggered_at >= ? AND last_direction = 'DROP' "
            "AND (sent_status IS NULL OR sent_status NOT LIKE '%cluster_member%') "
            "ORDER BY trigger_pct ASC LIMIT 10",
            (utc_date,),
        ).fetchall()
        pumps = conn.execute(
            "SELECT ticker, trigger_pct AS pct, trigger_price AS price "
            "FROM dd_alerts "
            "WHERE last_triggered_at >= ? AND last_direction = 'PUMP' "
            "AND (sent_status IS NULL OR sent_status NOT LIKE '%cluster_member%') "
            "ORDER BY trigger_pct DESC LIMIT 10",
            (utc_date,),
        ).fetchall()
        cluster_rows = conn.execute(
            "SELECT cluster_id, last_direction AS direction, "
            "       COUNT(*) AS n, AVG(trigger_pct) AS median_pct "
            "FROM dd_alerts "
            "WHERE last_triggered_at >= ? AND cluster_id IS NOT NULL "
            "GROUP BY cluster_id, last_direction",
            (utc_date,),
        ).fetchall()

    # Decode cluster_id ('tech_drop_2026-05-11' → sector='Tech')
    clusters = []
    for row in cluster_rows:
        cid_parts = (row["cluster_id"] or "").split("_")
        # Best-effort sector extraction; cluster_id format is
        # '<sector_slug>_<direction>_<utc_date>'
        sector = cid_parts[0].title() if cid_parts else "Unknown"
        clusters.append({
            "cluster_id": row["cluster_id"],
            "sector":     sector,
            "direction":  row["direction"],
            "n":          int(row["n"]),
            "median_pct": float(row["median_pct"] or 0.0),
        })

    return {
        "utc_date":   utc_date,
        "drops":      [dict(r) for r in drops],
        "pumps":      [dict(r) for r in pumps],
        "clusters":   clusters,
        "n_drops":    len(drops),
        "n_pumps":    len(pumps),
        "n_clusters": len(clusters),
    }


def get_watchlist_size() -> int:
    """Read the global watchlist size from the DB. Best-effort; returns 0
    if the watchlist table is missing or empty."""
    try:
        from app.backend.services.analysis_service import _connect
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ticker) FROM watchlist "
                "WHERE ticker IS NOT NULL AND ticker != ''"
            ).fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


# ── LLM orchestrator ───────────────────────────────────────────────────────


def run_digest_agent(*, utc_date: str | None = None) -> DigestNarrative:
    """Produce the EOD digest narrative for `utc_date` (default: today UTC).

    Returns:
      Validated DigestNarrative. Always returns a complete schema even
      if partial — defaults rather than throwing on missing fields.

    Raises:
      DDAgentError: pipeline can't produce ANY parseable narrative. Caller
                    falls back to a synthetic minimal digest so the
                    dashboard isn't blank.
    """
    if utc_date is None:
        utc_date = datetime.now(timezone.utc).date().isoformat()

    aggregates = gather_today_aggregates(utc_date)
    watchlist_size = get_watchlist_size()

    logger.info(
        "digest_agent: starting %s (drops=%d pumps=%d clusters=%d)",
        utc_date, aggregates["n_drops"], aggregates["n_pumps"], aggregates["n_clusters"],
    )

    system_prompt, prompt_id = select_digest_prompt()
    user_message = build_digest_user_message(
        utc_date=utc_date,
        n_drops=aggregates["n_drops"],
        n_pumps=aggregates["n_pumps"],
        n_clusters=aggregates["n_clusters"],
        drops=aggregates["drops"],
        pumps=aggregates["pumps"],
        clusters=aggregates["clusters"],
        watchlist_size=watchlist_size,
    )

    # Build Qwen client (same DashScope endpoint as dd_agent + sector_dd_agent)
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

    try:
        response = _call_llm_with_rate_retry(
            client,
            extractor_name="digest_agent",
            ticker=f"digest_{utc_date}",
            model=model,
            max_tokens=DD_MAX_OUTPUT_TOKENS,
            system=system_prompt,
            tools=[_WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise DDAgentError(
            f"Qwen API call failed for digest {utc_date}: {type(exc).__name__}: {exc}"
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
            f"Qwen returned empty text for digest {utc_date} after {n_searches} searches"
        )

    logger.info(
        "digest_agent: %s — %d web searches, %d chars",
        utc_date, n_searches, len(text),
    )

    parsed: Any = _parse_llm_json(text, extractor_name="digest_agent")
    if not isinstance(parsed, dict):
        raise DDAgentError(
            f"Digest output for {utc_date} is not a JSON object (got {type(parsed).__name__})"
        )

    try:
        return DigestNarrative.model_validate(parsed)
    except ValidationError as exc:
        # Coerce common LLM mistakes (string in place of list)
        coerced = dict(parsed)
        if not isinstance(coerced.get("key_themes"), list):
            coerced["key_themes"] = []
        try:
            return DigestNarrative.model_validate(coerced)
        except ValidationError:
            raise DDAgentError(
                f"Digest output for {utc_date} failed schema validation: {exc}"
            ) from exc


# ── Persistence ────────────────────────────────────────────────────────────


def upsert_digest_row(*, utc_date: str, narrative: DigestNarrative,
                      aggregates: dict) -> str:
    """Write the digest to dd_reports keyed by `digest_<utc_date>`.

    Idempotent — INSERT-OR-REPLACE so re-running on the same date
    overwrites the prior row.

    Returns the run_id that was written (e.g. "digest_2026-05-11").
    """
    from src.agents.dd import alert_dedup
    run_id = f"digest_{utc_date}"
    payload = {
        "narrative":      narrative.narrative,
        "key_themes":     narrative.key_themes,
        "macro_or_micro": narrative.macro_or_micro,
        "tomorrow_watch": narrative.tomorrow_watch,
        "drops":          aggregates.get("drops", []),
        "pumps":          aggregates.get("pumps", []),
        "clusters":       aggregates.get("clusters", []),
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }
    alert_dedup.upsert_dd_report(
        run_id=run_id,
        ticker="DIGEST",   # placeholder — digest is sector-spanning
        model_name="dd_digest_qwen",
        full_result_json=json.dumps(payload, default=str),
    )
    return run_id


def upsert_synthetic_digest(*, utc_date: str, aggregates: dict) -> str:
    """Fallback when the LLM agent fails — preserve a minimal narrative
    so the dashboard isn't blank."""
    from src.agents.dd import alert_dedup

    n = aggregates.get("n_drops", 0) + aggregates.get("n_pumps", 0) + aggregates.get("n_clusters", 0)
    if n == 0:
        narrative = (
            "[SYNTHETIC] Quiet day on the watchlist — no ±10% breaches "
            "recorded. The LLM digest agent did not run successfully."
        )
    else:
        narrative = (
            f"[SYNTHETIC] {aggregates.get('n_drops', 0)} drops, "
            f"{aggregates.get('n_pumps', 0)} pumps, "
            f"{aggregates.get('n_clusters', 0)} sector clusters today. "
            f"The LLM digest agent did not produce a parseable narrative — "
            f"raw aggregates remain available below."
        )
    run_id = f"digest_{utc_date}"
    payload = {
        "narrative":      narrative,
        "key_themes":     [],
        "macro_or_micro": "mixed",
        "tomorrow_watch": "n/a (synthetic fallback)",
        "drops":          aggregates.get("drops", []),
        "pumps":          aggregates.get("pumps", []),
        "clusters":       aggregates.get("clusters", []),
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }
    alert_dedup.upsert_dd_report(
        run_id=run_id,
        ticker="DIGEST",
        model_name="dd_digest_qwen_FALLBACK",
        full_result_json=json.dumps(payload, default=str),
    )
    return run_id
