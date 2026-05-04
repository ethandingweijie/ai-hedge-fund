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
    _call_llm_with_rate_retry,
    _extract_bank_metrics,
    _extract_dcf_calibration,
    _extract_pipeline_assets,
    _extract_reit_metrics,
    _extract_saas_metrics,
    _extract_sections,
    _extract_segment_scenarios,
    _compute_saas_metrics_fallback,
)


# ── OpenAI SDK → Anthropic-style adapter ────────────────────────────────────
# DashScope's Qwen endpoint is natively OpenAI-compatible. The existing
# production code path goes through the Anthropic SDK which works but leaks
# HTTP semantics through an Anthropic abstraction layer — 403 Rate Limit
# surfaces as PermissionDeniedError, Retry-After headers aren't exposed,
# and response-body error messages have to be string-matched. The openai
# SDK handles DashScope natively: maps 429 / 403 correctly, exposes response
# headers cleanly, supports Retry-After.
#
# Problem: all 6 extractor functions expect anthropic-style client with
# client.messages.create(model=, max_tokens=, system=, messages=) and response
# with .content[0].text. Swapping every extractor to openai would be a 13-site
# refactor touching the live pipeline.
#
# Solution: thin adapter that exposes the Anthropic surface but delegates
# to openai.chat.completions under the hood. Isolated to re-extract path —
# live pipeline continues using the real anthropic SDK untouched.

class _TextBlockShim:
    """Mimic anthropic's text block — has a .text attribute."""
    def __init__(self, text: str):
        self.text = text


class _MessageShim:
    """Mimic anthropic's Message — has .content list of text blocks."""
    def __init__(self, text: str):
        self.content = [_TextBlockShim(text)]


class _MessagesShim:
    """Exposes .create(model=, max_tokens=, system=, messages=) — the
    anthropic client.messages surface that all 6 extractors call."""
    def __init__(self, openai_client):
        self._client = openai_client

    def create(self, *, model, max_tokens, system=None, messages=None, **kwargs):
        # Translate anthropic-shaped call → openai-shaped call
        oai_messages: list[dict] = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for m in (messages or []):
            oai_messages.append({"role": m["role"], "content": m["content"]})

        # temperature=0.1 — extractors want deterministic JSON output. The
        # original anthropic-side extractors didn't set temperature (default
        # ~0.7 for Claude), which is fine for prose but sub-optimal for
        # structured KPI extraction. 0.1 mirrors what sector_prompts
        # documentation suggests for JSON-returning prompts.
        #
        # Forward to openai — raises openai.RateLimitError on 429,
        # openai.PermissionDeniedError on 403, openai.APITimeoutError on timeout.
        # _call_llm_with_rate_retry catches all of these by message-string match.
        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.1,
            messages=oai_messages,
        )
        # Extract content and wrap in anthropic-shaped response
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        return _MessageShim(text)


class _OpenAIAsAnthropicAdapter:
    """Drop-in replacement for anthropic.Anthropic that routes through
    openai SDK. Exposes only .messages.create — enough for all 6 extractors.
    Errors are NOT translated (same openai exception types bubble up) so
    _call_llm_with_rate_retry can match them by message content as before.
    """
    def __init__(self, openai_client):
        self.messages = _MessagesShim(openai_client)
from src.agents.industry.sector_prompts import (
    is_bank_sector,
    is_biopharma_sector,
    is_reit_sector,
    is_tech_sector,
)


# ── Env var resolution (mirrors deep_research.py _task lines 3578-3645) ─────

def _resolve_extractor_client(
    provider: str = "auto",
) -> tuple[anthropic.Anthropic, str, str]:
    """Build the sdk_client + pick the extractor model from env vars.

    Args:
      provider: "auto" | "qwen" | "anthropic"
        - "auto"      — prefer Qwen if available, fall back to Anthropic
        - "qwen"      — force DashScope/Qwen; fails if credentials missing
        - "anthropic" — force Anthropic Claude; fails if ANTHROPIC_API_KEY missing

    Returns (client, model_name, actual_provider). The third element is
    surfaced in re-extract responses so the user can see which provider
    ran against their data (important when quota-switching between them).
    """
    dashscope_key      = os.environ.get("DEEP_RESEARCH_API_KEY")
    dashscope_base_url = os.environ.get("DEEP_RESEARCH_BASE_URL")
    # Hard-coded to qwen3.6-plus — user's confirmed provisioned model on the
    # DashScope International deployment (30K RPM / 5M TPM). Intentionally
    # ignores DEEP_RESEARCH_SYNTHESIS_MODEL env var to prevent accidental
    # fallthrough to qwen3-max (documented globally but NotFoundError 404
    # on user's regional endpoint). If the model needs to change later,
    # edit this constant — this is admin tooling, not a configurable
    # production path.
    dashscope_model    = "qwen3.6-plus"
    anthropic_key      = os.environ.get("ANTHROPIC_API_KEY")

    want_anthropic = provider == "anthropic"
    want_qwen      = provider == "qwen"
    # "auto" means try Qwen first, fall back to Anthropic

    if not want_anthropic and dashscope_key and dashscope_base_url:
        # Use anthropic SDK — DashScope exposes TWO compatibility endpoints:
        #   /compatible-mode/v1  → OpenAI-compat (needs openai SDK)
        #   /apps/anthropic      → Anthropic-compat (needs anthropic SDK)
        # User's DEEP_RESEARCH_BASE_URL is the Anthropic-compat endpoint
        # (/apps/anthropic), so anthropic SDK is the correct client.
        #
        # timeout=180.0 (3 min) — DDOG re-extract hit APITimeoutError at
        # 60s on 2026-04-24. Qwen can be slow to start generation under
        # rate-limit pressure or when the model is warming up; 60s was
        # too tight. 3 min matches the long-tail latency observed in
        # production without masking genuine hangs.
        # Rate-limit handling is delegated to _call_llm_with_rate_retry.
        client = anthropic.Anthropic(
            api_key=dashscope_key,
            base_url=dashscope_base_url,
            timeout=180.0,
            max_retries=0,  # handled by _call_llm_with_rate_retry
        )
        return client, dashscope_model, "qwen"

    if not want_qwen and anthropic_key:
        client = anthropic.Anthropic(
            api_key=anthropic_key,
            timeout=60.0,
            max_retries=2,
        )
        return client, "claude-sonnet-4-6", "anthropic"

    # Explicit provider request failed — give the user a clear error
    if want_qwen:
        raise ValueError(
            "provider='qwen' requested but DEEP_RESEARCH_API_KEY + "
            "DEEP_RESEARCH_BASE_URL are not set. Use provider='anthropic' "
            "or provider='auto' instead."
        )
    if want_anthropic:
        raise ValueError(
            "provider='anthropic' requested but ANTHROPIC_API_KEY is not "
            "set. Use provider='qwen' or provider='auto' instead."
        )
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
    """Run the 2 universal extractors + 1 sector-specific extractor IN PARALLEL.

    Returns a dict of {extractor_name: output}. Values mirror what
    state["data"][<name>] would hold after a live pipeline run:
      dcf_calibration, segment_scenarios: dict
      saas_metrics / bank_metrics / reit_metrics: dict
      pipeline_assets: list

    Parallel rationale (restored 2026-04-25 after fixing persistence bug):
    LLM API calls are I/O-bound (network wait, not CPU). ThreadPoolExecutor
    fires the 3 Qwen calls concurrently — wall time ≈ slowest extractor
    (~10s) instead of sum of all extractors (~30s). Matches the live
    pipeline's extractor fan-out at deep_research.py.

    Earlier sequential revert (305a120) was a misdiagnosis — we thought
    parallel caused unreliable saas_metrics persistence. Real cause was
    the pipeline return dict dropping extractor outputs (fixed in 1ac5490).
    With that fixed, parallel is safe and 3× faster for the same reliability.

    FMP fallback runs AFTER the thread pool completes because it's pure-
    Python / non-I/O and depends on the LLM's raw result. Running it
    in-thread would add no speedup.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Decide which sector extractor applies (TICKER_SECTOR_LOOKUP fallback
    # for runs with empty stored profile_name)
    sector_extractor, _effective_profile = _decide_sector_extractor(
        sector, profile_name, ticker=ticker
    )

    # Build task map — callables to submit to the thread pool. Universal
    # extractors always fire; sector-specific is added based on dispatch.
    tasks: dict[str, Any] = {
        "dcf_calibration": lambda: _extract_dcf_calibration(
            sdk_client, model_name, sections, ticker
        ),
        "segment_scenarios": lambda: _extract_segment_scenarios(
            sdk_client, model_name, sections, deep_research, ticker
        ),
    }
    if sector_extractor == "saas_metrics":
        tasks["saas_metrics"] = lambda: _extract_saas_metrics(
            sdk_client, model_name, sections, deep_research, ticker
        )
    elif sector_extractor == "bank_metrics":
        tasks["bank_metrics"] = lambda: _extract_bank_metrics(
            sdk_client, model_name, sections, deep_research, ticker
        )
    elif sector_extractor == "reit_metrics":
        tasks["reit_metrics"] = lambda: _extract_reit_metrics(
            sdk_client, model_name, sections, deep_research, ticker
        )
    elif sector_extractor == "pipeline_assets":
        tasks["pipeline_assets"] = lambda: _extract_pipeline_assets(
            sdk_client, model_name, sections, deep_research, ticker
        )

    # v3.15 — always fire framework_metrics dispatch alongside the legacy
    # extractor (matches the live pipeline post-v3.14). Without this the
    # backfill path leaves framework_metrics_all empty, so the new
    # SectorValuationCard renders mostly blank even when the legacy panel
    # has full values.
    #
    # v3.21 — resolve framework profile_name via TICKER_SECTOR_LOOKUP when the
    # stored profile_name is empty. Older runs (pre-strategic-router) and
    # tickers absent from the lookup at original-save time (GTM, LIF, TRI
    # before v3.21 added them) have empty stored profile_name. Without this
    # fallback the framework dispatch task isn't registered → backfill leaves
    # the card unchanged, which defeats the purpose of the v3.21 lookup-table
    # additions.
    _framework_profile_for_dispatch = (profile_name or "").strip()
    if not _framework_profile_for_dispatch and ticker:
        try:
            from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
            _entry = TICKER_SECTOR_LOOKUP.get(ticker.upper())
            if _entry and len(_entry) >= 2 and _entry[1]:
                _framework_profile_for_dispatch = _entry[1]
                print(f"  [reextract {ticker}] framework profile resolved from "
                      f"TICKER_SECTOR_LOOKUP: {_framework_profile_for_dispatch}")
        except Exception:
            pass

    if _framework_profile_for_dispatch:
        try:
            from src.data.sector_kpi_framework import (
                SECTOR_KPI_FRAMEWORK,
                extract_via_framework,
            )
            if _framework_profile_for_dispatch in SECTOR_KPI_FRAMEWORK:
                _resolved = _framework_profile_for_dispatch  # capture for lambda
                tasks["framework_metrics"] = lambda: extract_via_framework(
                    sdk_client, model_name, sections, deep_research, ticker,
                    profile_name=_resolved,
                )
        except Exception:
            pass

    # Fan out all LLM calls concurrently. Each extractor has its own
    # _call_llm_with_rate_retry wrapper for rate-limit + timeout handling.
    out: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_to_name = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                out[name] = future.result()
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {str(exc)[:200]}"
                print(f"  [run_extractors {ticker}] {name} failed: {err_msg}")
                # Typed empty defaults so output shape stays stable
                if name == "pipeline_assets":
                    out[name] = []
                elif name == "dcf_calibration":
                    out[name] = {
                        "growth_rate_adj": None, "margin_direction": "stable",
                        "risk_flag": "MEDIUM", "notes": f"Extraction failed: {err_msg}",
                    }
                else:
                    out[name] = {}

    # FMP fallback for saas_metrics — runs AFTER the thread pool completes.
    # Applies to both successful LLM results (augments with FMP-computable
    # fields like billings_growth_yoy) and failed LLM calls (provides
    # rule_of_40_score / magic_number / cac_payback from raw_financials
    # when LLM extraction raised).
    if "saas_metrics" in out:
        _llm_result = out["saas_metrics"] if isinstance(out["saas_metrics"], dict) else {}
        out["saas_metrics"] = _compute_saas_metrics_fallback(raw_financials, _llm_result)

    return out


# ── Public API ──────────────────────────────────────────────────────────────

def _diagnose_saas_extractor(
    sdk_client,
    model_name: str,
    sections: dict,
    deep_research: str,
    raw_financials: dict,
    ticker: str,
) -> dict:
    """Run the saas_metrics extractor with full diagnostic visibility.

    Unlike _extract_saas_metrics which returns only the validated dict,
    this returns the RAW Qwen response + parsed output + clamp decisions
    so the caller can see exactly why fields did/didn't populate.

    Structure:
      {
        "input_chars": 5366,
        "combined_preview": "2A. ... 2F. ...",  # first 500 chars
        "raw_response": "<full LLM output>",
        "raw_len": 350,
        "parsed_type": "dict",
        "parsed_keys": ["nrr_pct", ...],
        "parsed_sample": {...},             # safe-truncated parsed dict
        "validated_fields": ["rule_of_40_score"],
        "clamp_rejections": ["nrr_pct=120(range 0.8-1.5)", ...],
        "error": None | "<ErrorType: msg>",
      }

    Runs the same API call as _extract_saas_metrics but replicates the
    validation logic inline so we can surface every intermediate state.
    """
    import re as _re_diag

    # Mirror the input construction from _extract_saas_metrics.
    # 2F ONLY — all SaaS KPIs (NRR, CAC, Rule of 40, Magic Number, Gross
    # Retention, LTV/CAC) live in 2F. 2A (profit pool) and 2D (cycle) are
    # context that dilutes LLM attention without contributing KPIs.
    s2a = sections.get("2a") or sections.get("2A") or ""
    s2d = sections.get("2d") or sections.get("2D") or ""
    s2f = sections.get("2f") or sections.get("2F") or ""
    combined = s2f.strip()
    if len(combined) < 500:
        combined = (s2a + "\n\n" + s2d + "\n\n" + s2f).strip()
    if not combined or len(combined) < 500:
        combined = (deep_research or "")[:20000]

    result: dict[str, Any] = {
        "input_chars": len(combined),
        "combined_preview": combined[:500],
        "raw_response": "",
        "raw_len": 0,
        "parsed_type": None,
        "parsed_keys": [],
        "parsed_sample": {},
        "validated_fields": [],
        "clamp_rejections": [],
        "error": None,
    }

    if not combined:
        result["error"] = "Empty input (no sections + no deep_research)"
        return result

    # Replicate the extractor system prompt (from _extract_saas_metrics)
    _system = (
        "You are a SaaS / tech-company analyst. Extract structured KPIs "
        "from the research and return ONLY valid JSON (no markdown fences, "
        "no commentary).\n\n"
        "Schema (all fields OPTIONAL — omit if not substantiated):\n"
        "  nrr_pct: float (0.80-1.50)\n"
        "  gross_retention_pct: float (0.80-1.00)\n"
        "  cac_payback_months: float (3-60)\n"
        "  ltv_cac_ratio: float (1-15) — COHORT-MATCHED: CAC = period S&M / NEW customers added (NOT total customers); LTV = (cohort ACV × gross margin) / annual churn. Elite SaaS 7-15x. If your derivation gives >15, recompute with the new-customer cohort and cohort-specific ACV before reporting.\n"
        "  rule_of_40_score: float (-30 to 120)\n"
        "  magic_number: float (0.1-3.0)\n"
        "  rpo_growth_yoy: float (-0.20 to 0.80)\n"
        "  billings_growth_yoy: float (-0.20 to 0.80)\n"
        "  evidence: string ≤300 chars\n\n"
        "Rules: Return {} if not SaaS. Convert percentages to decimals "
        "(120% NRR → 1.20; 40 score of rule of 40 → 40)."
    )

    try:
        resp = _call_llm_with_rate_retry(
            sdk_client,
            extractor_name="saas_metrics_diagnostic",
            ticker=ticker,
            model=model_name,
            max_tokens=500,
            system=_system,
            messages=[{
                "role": "user",
                "content": f"Ticker: {ticker}\n\nResearch excerpts:\n{combined[:20000]}",
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        result["raw_response"] = raw
        result["raw_len"] = len(raw)

        parsed = _parse_llm_json_local(raw)
        if parsed is None:
            result["parsed_type"] = "None (parse failed)"
            return result
        result["parsed_type"] = type(parsed).__name__
        if isinstance(parsed, dict):
            result["parsed_keys"] = sorted(parsed.keys())
            # Truncate any long string values for sample
            result["parsed_sample"] = {
                k: (str(v)[:150] if isinstance(v, str) else v)
                for k, v in parsed.items()
            }

            clamps = {
                "nrr_pct":             (0.80, 1.50),
                "gross_retention_pct": (0.80, 1.00),
                "cac_payback_months":  (3, 60),
                "ltv_cac_ratio":       (1, 15),
                "rule_of_40_score":    (-30, 120),
                "magic_number":        (0.1, 3.0),
                "rpo_growth_yoy":      (-0.20, 0.80),
                "billings_growth_yoy": (-0.20, 0.80),
            }
            for k, (lo, hi) in clamps.items():
                v = parsed.get(k)
                if v is None:
                    continue
                if isinstance(v, (int, float)) and lo <= v <= hi:
                    result["validated_fields"].append(k)
                else:
                    result["clamp_rejections"].append(f"{k}={v!r}(range {lo}-{hi})")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

    return result


def _parse_llm_json_local(raw: str):
    """Local copy of _parse_llm_json for diagnostic use (avoids import cycle)."""
    from src.agents.industry.deep_research import _parse_llm_json as _p
    return _p(raw, extractor_name="saas_metrics_diagnostic")


def reextract_for_run(
    run_id: str,
    dry_run: bool = True,
    db_path: Optional[str] = None,
    verbose: bool = False,
    provider: str = "auto",
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
        stored_sections = data.get("deep_research_sections", {}) or {}
        deep_research   = data.get("deep_research", "") or ""
        raw_fin         = data.get("raw_financials", {}) or {}

        if not stored_sections and not deep_research:
            return {
                "ok": False,
                "error": "No deep_research content in stored run",
                "run_id": run_id, "ticker": ticker,
            }

        # Re-parse sections from stored deep_research using the CURRENT
        # widened regex (commit d8706df). Historic runs archived before
        # that fix have a partial sections dict where 2F is often missing
        # (old parser dropped headings with list markers / prose "Section
        # 2F:" / divider bars / etc.). Re-parsing recovers 2F so the
        # saas_metrics extractor gets the KPI text it needs. Falls back
        # to stored_sections when deep_research is empty (edge case).
        if deep_research:
            reparsed_sections = _extract_sections(deep_research)
            # Prefer reparsed when it yielded more section keys OR recovered
            # "2f" that the stored dict was missing. Keeps stored dict as
            # fallback when re-parse degrades (shouldn't happen but defensive).
            stored_has_2f  = bool(stored_sections.get("2f") or stored_sections.get("2F"))
            reparsed_has_2f = bool(reparsed_sections.get("2f") or reparsed_sections.get("2F"))
            if reparsed_has_2f or len(reparsed_sections) >= len(stored_sections):
                sections = reparsed_sections
                sections_source = f"reparsed (stored_had_2f={stored_has_2f}, now={reparsed_has_2f})"
            else:
                sections = stored_sections
                sections_source = "stored (reparse was worse)"
        else:
            sections = stored_sections
            sections_source = "stored (no deep_research text)"

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

        # v3.21 — Generic profile resolution for the framework path. _decide_
        # sector_extractor only resolves to one of {Hyperscaler, Mature SaaS,
        # Growth SaaS, Cybersecurity / Mission-Critical SaaS} — leaves other
        # profiles (Levered Subscription, Hyper-Growth Platform, IT Services,
        # Ad/Consulting, etc.) unresolved. Add a generic TICKER_SECTOR_LOOKUP
        # fallback so the framework dispatch + sector_card re-render see a
        # real profile and back-fill these tickers' cards correctly.
        _framework_profile = (profile_name or "").strip()
        if not _framework_profile:
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                _entry = TICKER_SECTOR_LOOKUP.get((ticker or "").upper())
                if _entry and len(_entry) >= 2 and _entry[1]:
                    _framework_profile = _entry[1]
                    print(f"  [reextract {ticker}] framework profile resolved from "
                          f"TICKER_SECTOR_LOOKUP: {_framework_profile}")
                    # Patch the in-memory data dict so downstream consumers
                    # (sector_card render, DB column patch) see it as well.
                    _pn_dict = data.get("profile_names") or {}
                    if isinstance(_pn_dict, dict):
                        _pn_dict[ticker] = _framework_profile
                        data["profile_names"] = _pn_dict
                    if not data.get("profile_name"):
                        data["profile_name"] = _framework_profile
                    profile_name = _framework_profile  # reuse downstream
            except Exception as _e:
                print(f"  [reextract {ticker}] TICKER_SECTOR_LOOKUP fallback failed: {_e!r}")

        # Build client + run extractors. provider="auto" tries Qwen first
        # and falls back to Anthropic if DashScope creds missing; explicit
        # "qwen" / "anthropic" pin to one provider (useful when DashScope
        # quota is exhausted and user wants to force Claude).
        sdk_client, model_name, actual_provider = _resolve_extractor_client(provider=provider)
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
            "framework_metrics": _keys(extracted.get("framework_metrics", {})),
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
            "provider": actual_provider,
            "model_name": model_name,
            "sections_source": sections_source,
            "sections_keys": sorted(sections.keys()) if isinstance(sections, dict) else [],
            "section_2f_len": len(sections.get("2f", "") or sections.get("2F", "") or ""),
            "extractors_run": sorted(extracted.keys()),
            "before": before,
            "after":  after,
            "gained_fields": gained_any,
            "dry_run": dry_run,
        }

        # Verbose mode — surface the raw Qwen response + parse/clamp state
        # for the saas_metrics extractor specifically. Used for diagnosing
        # "extractor ran but fields empty" failures without dashboard access.
        if verbose and _decide_sector_extractor(sector, effective_profile, ticker=ticker)[0] == "saas_metrics":
            result["diagnostic_saas"] = _diagnose_saas_extractor(
                sdk_client, model_name, sections, deep_research, raw_fin, ticker
            )

        if dry_run:
            result["would_update"] = gained_any
            return result

        if not gained_any:
            result["updated"] = False
            result["note"] = "No new fields gained — DB not touched"
            return result

        # Patch stored JSON — merge extractor output into data dict.
        # Key convention: dcf_calibration + segment_scenarios are flat;
        # saas/bank/reit/pipeline/framework are ticker-keyed dicts in state.
        data["dcf_calibration"]   = extracted.get("dcf_calibration", {})
        data["segment_scenarios"] = extracted.get("segment_scenarios", {})

        # v3.15 — write to BOTH legacy (no `_all`) AND canonical `_all` keys
        # so legacy panels (TechValuationPanel etc.) AND the new
        # SectorValuationCard both pick up the backfilled values. Mirrors the
        # v3.13 aggregator bridge in deep_research.py.
        for k in ("saas_metrics", "bank_metrics", "reit_metrics",
                  "pipeline_assets", "framework_metrics"):
            if k in extracted:
                # Legacy bucket (no _all suffix)
                existing = data.get(k) or {}
                if not isinstance(existing, dict):
                    existing = {}
                existing[ticker] = extracted[k]
                data[k] = existing
                # Canonical *_all bucket — what _collect_kpi_values reads
                all_key = f"{k}_all"
                existing_all = data.get(all_key) or {}
                if not isinstance(existing_all, dict):
                    existing_all = {}
                existing_all[ticker] = extracted[k]
                data[all_key] = existing_all

        # v3.15 — re-render sector_card from the freshly-extracted metrics so
        # the new SectorValuationCard reflects the backfill on next page load
        # (frontend reads sector_card[ticker], not the raw *_all buckets).
        try:
            from src.data.sector_kpi_framework import (
                render_card_payload, is_legacy_profile, _augment_metrics_with_fmp_risk,
            )
            if profile_name and not is_legacy_profile(profile_name):
                # Mirror pipeline.py L671-687: FMP-augment the framework_metrics_all
                # bucket for this ticker BEFORE rendering, so the card's R-multiplier
                # picks up FMP-derived KPIs (net_debt_to_ebitda etc.) that
                # extract_via_framework alone wouldn't surface.
                fwm = data.get("framework_metrics_all") or {}
                if not isinstance(fwm, dict):
                    fwm = {}
                ticker_metrics = fwm.get(ticker) or {}
                try:
                    ticker_metrics = _augment_metrics_with_fmp_risk(ticker, ticker_metrics)
                    fwm[ticker] = ticker_metrics
                    data["framework_metrics_all"] = fwm
                except Exception:
                    pass  # FMP augment is best-effort

                synthetic_state = {"data": data}
                rendered = render_card_payload(profile_name, synthetic_state, ticker)
                if rendered:
                    sector_card = data.get("sector_card") or {}
                    if not isinstance(sector_card, dict):
                        sector_card = {}
                    sector_card[ticker] = rendered
                    data["sector_card"] = sector_card
        except Exception as _re:
            print(f"  [reextract {ticker}] sector_card re-render failed: {_re!r}")

        full["data"] = data

        # v3.20 — Also patch the indexed profile_name column. Pre-v3.20 the
        # column stayed NULL even when JSON had the value, which broke the
        # z-score peer cohort SQL (`WHERE profile_name = ?` returned 0 rows).
        # Resolves the column from the resolved profile_name, falling back to
        # data.profile_names[ticker] then TICKER_SECTOR_LOOKUP. Sector column
        # is updated in lockstep when known to keep historical filters working.
        _resolved_profile = (profile_name or "").strip()
        if not _resolved_profile:
            _pn_dict = data.get("profile_names") or {}
            if isinstance(_pn_dict, dict):
                _resolved_profile = (_pn_dict.get(ticker) or "").strip()
        if not _resolved_profile:
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                _entry = TICKER_SECTOR_LOOKUP.get((ticker or "").upper())
                if _entry and len(_entry) >= 2:
                    _resolved_profile = (_entry[1] or "").strip()
            except Exception:
                pass
        _resolved_sector = (data.get("sector") or "").strip() or sector

        conn.execute(
            "UPDATE web_runs SET full_result_json = ?, "
            "profile_name = COALESCE(NULLIF(?, ''), profile_name), "
            "sector       = COALESCE(NULLIF(?, ''), sector) "
            "WHERE run_id = ?",
            (json.dumps(full, default=str), _resolved_profile, _resolved_sector, row["run_id"]),
        )
        conn.commit()
        result["updated"] = True
        result["profile_name_column_patched"] = bool(_resolved_profile)
        return result
    finally:
        conn.close()


def reextract_by_ticker(
    ticker: str,
    dry_run: bool = True,
    limit: int = 1,
    db_path: Optional[str] = None,
    verbose: bool = False,
    provider: str = "auto",
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
        reextract_for_run(r["run_id"], dry_run=dry_run, db_path=db_path,
                           verbose=verbose, provider=provider)
        for r in rows
    ]
