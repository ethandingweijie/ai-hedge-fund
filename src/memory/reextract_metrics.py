"""
src/memory/reextract_metrics.py
================================
Re-run the LLM extractor chain against EXISTING stored deep research
(from web_runs.full_result_json) without triggering a fresh pipeline run.

Use case
--------
When the extractor JSON parser is hardened (e.g. the v2.0.1 _parse_llm_json
migration that recovers Qwen preamble-wrapped responses the old parser
silently dropped), historic runs still carry the old empty extractor
output. This module re-runs only the extractor passes against the stored
deep_research text + sections dict, applies FMP fallback, and patches
the result back into web_runs.full_result_json so the frontend sees the
recovered fields on next page load.

No web searches, no report synthesis. Just the extractor LLM passes
(~5-20s per run) + DB patch.

Usage from Python
-----------------
    from src.memory.reextract_metrics import reextract_for_run

    result = reextract_for_run(run_id="abc123", dry_run=True)
    # → {"ok": True, "ticker": "DDOG", "sector": "Tech",
    #    "profile_name": "Growth SaaS",
    #    "extractors_run": ["dcf_calibration", "segment_scenarios", "saas_metrics"],
    #    "saas_metrics_before": [],
    #    "saas_metrics_after":  ["cac_payback_months", "gross_retention_pct",
    #                             "ltv_cac_ratio", "magic_number", "nrr_pct",
    #                             "rule_of_40_score"],
    #    "would_update": True}

Protection
----------
- dry_run=True by default; must pass dry_run=False to write.
- Only operates on web_runs.full_result_json (NOT ticker_signals).
- Preserves every other key in the stored JSON — only overwrites the
  specific extractor output fields.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

import anthropic

from src.agents.industry.deep_research import (
    _extract_bank_metrics,
    _extract_dcf_calibration,
    _extract_pipeline_assets,
    _extract_reit_metrics,
    _extract_saas_metrics,
    _extract_segment_scenarios,
    _compute_saas_metrics_fallback,
)
from src.agents.industry.sector_prompts import (
    is_bank_sector,
    is_biopharma_sector,
    is_reit_sector,
    is_tech_sector,
)


# ── Env var resolution (mirrors deep_research.py _task lines 3578-3645) ─────

def _resolve_extractor_client() -> tuple[anthropic.Anthropic, str]:
    """Build the sdk_client + pick the extractor model from env vars.

    Matches the production setup: Qwen via DashScope is preferred (what DDOG /
    SNOW used on their original runs), with fallback to Anthropic Claude.
    Returns (client, model_name) or raises ValueError when no key is set.
    """
    dashscope_key      = os.environ.get("DEEP_RESEARCH_API_KEY")
    dashscope_base_url = os.environ.get("DEEP_RESEARCH_BASE_URL")
    dashscope_model    = os.environ.get("DEEP_RESEARCH_SYNTHESIS_MODEL") or "qwen3-max"
    anthropic_key      = os.environ.get("ANTHROPIC_API_KEY")

    if dashscope_key and dashscope_base_url:
        client = anthropic.Anthropic(
            api_key=dashscope_key,
            base_url=dashscope_base_url,
            timeout=60.0,
            max_retries=2,
        )
        return client, dashscope_model

    if anthropic_key:
        client = anthropic.Anthropic(
            api_key=anthropic_key,
            timeout=60.0,
            max_retries=2,
        )
        return client, "claude-sonnet-4-6"

    raise ValueError(
        "No LLM credentials available. Set DEEP_RESEARCH_API_KEY + "
        "DEEP_RESEARCH_BASE_URL (Qwen via DashScope) or ANTHROPIC_API_KEY."
    )


# ── DB access ────────────────────────────────────────────────────────────────

def _get_web_runs_db_path() -> str:
    """Return the web_runs SQLite path (same logic as analysis_service.py)."""
    from app.backend.services.analysis_service import _get_db_path
    return _get_db_path()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ── Extractor dispatch ──────────────────────────────────────────────────────

def _decide_sector_extractor(
    sector: str, profile_name: str, ticker: str = ""
) -> tuple[Optional[str], str]:
    """Return (extractor_name, effective_profile_name).

    Matches the needs_extractor() logic in sector_prompts.py including the
    TICKER_SECTOR_LOOKUP fallback for runs with empty profile_name. The
    fallback is critical for retro-extracting historic DDOG/SNOW runs
    archived before the strategic_router profile pre-classification was
    added in commit fe6f1ec — those runs have profile_name="" in stored
    JSON even though the ticker is canonically Growth SaaS.

    Returns the effective profile_name too so callers can pass it into
    the saas_metrics extractor (which uses it for prompt signature
    selection in get_kpi_prompt).
    """
    effective_profile = profile_name or ""

    if is_tech_sector(sector):
        _is_saas = effective_profile not in {"", "Levered Subscription"}
        if not _is_saas and ticker:
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                _entry = TICKER_SECTOR_LOOKUP.get(ticker.upper())
                if _entry and _entry[1] in {
                    "Hyperscaler / Tech Conglomerate", "Mature SaaS", "Growth SaaS",
                    "Cybersecurity / Mission-Critical SaaS",
                }:
                    _is_saas = True
                    effective_profile = _entry[1]
            except Exception:
                pass
        if _is_saas:
            return ("saas_metrics", effective_profile)

    if is_bank_sector(sector):
        _is_bank = (
            "Bank" in (effective_profile or "")
            or effective_profile in {"Mortgage/GSE", "Brokerage"}
        )
        if not _is_bank and ticker:
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                _entry = TICKER_SECTOR_LOOKUP.get(ticker.upper())
                if _entry and is_bank_sector(_entry[0]):
                    _is_bank = True
                    effective_profile = _entry[1] or effective_profile
            except Exception:
                pass
        if _is_bank:
            return ("bank_metrics", effective_profile)

    if is_reit_sector(sector) or "REIT" in (effective_profile or ""):
        return ("reit_metrics", effective_profile)

    if is_biopharma_sector(sector):
        return ("pipeline_assets", effective_profile)

    return (None, effective_profile)


def _run_extractors(
    sdk_client,
    model_name: str,
    ticker: str,
    sector: str,
    profile_name: str,
    sections: dict,
    deep_research: str,
    raw_financials: dict,
) -> dict[str, Any]:
    """Run the 2 universal extractors + 1 sector-specific extractor.

    Returns a dict of {extractor_name: output}. Values mirror what
    state["data"][<name>] would hold after a live pipeline run:
      dcf_calibration, segment_scenarios: dict
      saas_metrics / bank_metrics / reit_metrics: dict
      pipeline_assets: list
    """
    out: dict[str, Any] = {}

    # Always run the two universal extractors
    out["dcf_calibration"] = _extract_dcf_calibration(
        sdk_client, model_name, sections, ticker
    )
    out["segment_scenarios"] = _extract_segment_scenarios(
        sdk_client, model_name, sections, deep_research, ticker
    )

    # Sector-specific — includes TICKER_SECTOR_LOOKUP fallback when the
    # stored profile_name is empty (historic runs archived before the
    # strategic_router pre-classification was added)
    sector_extractor, _effective_profile = _decide_sector_extractor(
        sector, profile_name, ticker=ticker
    )
    if sector_extractor == "saas_metrics":
        raw = _extract_saas_metrics(
            sdk_client, model_name, sections, deep_research, ticker
        )
        # Apply FMP fallback after LLM pass — mirrors deep_research.py line 3377
        out["saas_metrics"] = _compute_saas_metrics_fallback(raw_financials, raw)
    elif sector_extractor == "bank_metrics":
        out["bank_metrics"] = _extract_bank_metrics(
            sdk_client, model_name, sections, deep_research, ticker
        )
    elif sector_extractor == "reit_metrics":
        out["reit_metrics"] = _extract_reit_metrics(
            sdk_client, model_name, sections, deep_research, ticker
        )
    elif sector_extractor == "pipeline_assets":
        out["pipeline_assets"] = _extract_pipeline_assets(
            sdk_client, model_name, sections, deep_research, ticker
        )

    return out


# ── Public API ──────────────────────────────────────────────────────────────

def reextract_for_run(
    run_id: str,
    dry_run: bool = True,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Re-run extractors against one stored run and optionally patch the DB.

    Params:
      run_id  — web_runs.run_id UUID
      dry_run — True (default) reports what would change without writing
      db_path — optional override; defaults to the analysis_service DB path

    Returns a summary dict with before/after field counts and whether a
    DB update would be / was performed.
    """
    path = db_path or _get_web_runs_db_path()
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT run_id, ticker, full_result_json FROM web_runs "
            "WHERE run_id = ? AND (is_checkpoint = 0 OR is_checkpoint IS NULL)",
            (run_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"run_id {run_id} not found"}

        full = json.loads(row["full_result_json"])
        data = full.get("data", {})
        ticker        = row["ticker"]
        sector        = data.get("sector", "")
        profile_name  = data.get("profile_name") or (
            data.get("profile_names", {}) or {}
        ).get(ticker, "")
        sections      = data.get("deep_research_sections", {}) or {}
        deep_research = data.get("deep_research", "") or ""
        raw_fin       = data.get("raw_financials", {}) or {}

        if not sections and not deep_research:
            return {
                "ok": False,
                "error": "No deep_research content in stored run",
                "run_id": run_id, "ticker": ticker,
            }

        # Snapshot BEFORE extractor output (for diff)
        before = {
            "saas_metrics":      sorted((data.get("saas_metrics") or {}).get(ticker, {}).keys())
                                   if isinstance(data.get("saas_metrics"), dict) else [],
            "bank_metrics":      sorted((data.get("bank_metrics") or {}).get(ticker, {}).keys())
                                   if isinstance(data.get("bank_metrics"), dict) else [],
            "reit_metrics":      sorted((data.get("reit_metrics") or {}).get(ticker, {}).keys())
                                   if isinstance(data.get("reit_metrics"), dict) else [],
            "pipeline_assets":   len((data.get("pipeline_assets") or {}).get(ticker, []))
                                   if isinstance(data.get("pipeline_assets"), dict) else 0,
            "dcf_calibration":   sorted((data.get("dcf_calibration") or {}).keys()),
        }

        # Resolve the effective profile_name — falls back to TICKER_SECTOR_LOOKUP
        # when stored profile_name is empty (historic DDOG/SNOW runs archived
        # before strategic_router pre-classification landed). Surfaced in the
        # result so callers can see what the lookup recovered.
        _, effective_profile = _decide_sector_extractor(sector, profile_name, ticker=ticker)

        # Build client + run extractors
        sdk_client, model_name = _resolve_extractor_client()
        extracted = _run_extractors(
            sdk_client, model_name, ticker, sector, effective_profile,
            sections, deep_research, raw_fin,
        )

        # Snapshot AFTER
        def _keys(v):
            if isinstance(v, dict):
                # Filter out empty / None values to reflect real populated fields
                return sorted(k for k, val in v.items() if val not in (None, "", [], {}))
            if isinstance(v, list):
                return len(v)
            return []
        after = {
            "saas_metrics":      _keys(extracted.get("saas_metrics", {})),
            "bank_metrics":      _keys(extracted.get("bank_metrics", {})),
            "reit_metrics":      _keys(extracted.get("reit_metrics", {})),
            "pipeline_assets":   _keys(extracted.get("pipeline_assets", [])),
            "dcf_calibration":   _keys(extracted.get("dcf_calibration", {})),
        }

        # Decide whether an update is warranted — skip write when AFTER is
        # strictly worse or equal (protects against an extractor regression
        # that would erase existing data).
        def _gained(b, a):
            if isinstance(b, list) and isinstance(a, list):
                return len(a) > len(b)
            if isinstance(b, int) and isinstance(a, int):
                return a > b
            return False

        gained_any = any(_gained(before[k], after[k]) for k in before)

        result: dict[str, Any] = {
            "ok": True,
            "run_id": row["run_id"],
            "ticker": ticker,
            "sector": sector,
            "profile_name": profile_name,
            "extractors_run": sorted(extracted.keys()),
            "before": before,
            "after":  after,
            "gained_fields": gained_any,
            "dry_run": dry_run,
        }

        if dry_run:
            result["would_update"] = gained_any
            return result

        if not gained_any:
            result["updated"] = False
            result["note"] = "No new fields gained — DB not touched"
            return result

        # Patch stored JSON — merge extractor output into data dict.
        # Key convention: dcf_calibration + segment_scenarios are flat;
        # saas/bank/reit/pipeline are ticker-keyed dicts in state.
        data["dcf_calibration"]   = extracted.get("dcf_calibration", {})
        data["segment_scenarios"] = extracted.get("segment_scenarios", {})

        for k in ("saas_metrics", "bank_metrics", "reit_metrics", "pipeline_assets"):
            if k in extracted:
                existing = data.get(k) or {}
                if not isinstance(existing, dict):
                    existing = {}
                existing[ticker] = extracted[k]
                data[k] = existing

        full["data"] = data

        conn.execute(
            "UPDATE web_runs SET full_result_json = ? WHERE run_id = ?",
            (json.dumps(full, default=str), row["run_id"]),
        )
        conn.commit()
        result["updated"] = True
        return result
    finally:
        conn.close()


def reextract_by_ticker(
    ticker: str,
    dry_run: bool = True,
    limit: int = 1,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Re-run extractors for the last N non-checkpoint runs for one ticker.

    Params:
      ticker  — e.g. "DDOG"
      dry_run — True (default) shows diff without writing
      limit   — how many recent runs to process (1 = most recent only)

    Returns a list of per-run result dicts.
    """
    path = db_path or _get_web_runs_db_path()
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT run_id FROM web_runs "
            "WHERE ticker = ? AND (is_checkpoint = 0 OR is_checkpoint IS NULL) "
            "ORDER BY run_at DESC LIMIT ?",
            (ticker.upper(), limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return [{"ok": False, "error": f"No runs found for ticker {ticker}"}]

    return [
        reextract_for_run(r["run_id"], dry_run=dry_run, db_path=db_path)
        for r in rows
    ]
