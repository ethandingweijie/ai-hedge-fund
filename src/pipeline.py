"""
Advanced Pipeline Orchestrator (committee-free as of M2 Track D/E)

Sequence:
  [1] Macro Regime Classifier      — front block, sets regime + position cap
  [2] Strategic Routing Agent      — front block, sector + raw financials scratchpad
  [2.5] Intelligence Agents        — front block, deterministic intel signals
  [2.7] EDGAR/HKEX Resolver        — front block, filing refs
  [2.9] Freshness delta            — one bounded search per ticker with a prior recap
  [3] Deep Research + Industry Brief — Qwen/Anthropic web research, extractor fan-out
  [4] DCF / SOTP / Data Router     — valuation engines + per-agent data pre-fetch
  [4.6/4.7] Peer comparison + price history (background since the front block)
  [7] Scenario + PowerLaw + Trap   — 3 parallel threads, all read same state
  [8] Advanced Risk Manager        — dual-layer quality filter + position caps
  [9] Advanced Portfolio Manager   — valuation-band cascade + research digest + LLM rationale
  [10] Post-Trade Review (optional)— scores prior calls against outcomes

The former phases 5 (investor agents) and 6 (debate round) were
decommissioned in M2: the PM decides from qualitative research +
quantitative valuation directly ("Decision path: no committee").

Entry point called from main.py when --pipeline advanced is passed.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime

from langchain_core.messages import HumanMessage

# Context-preserving submit(). A bare executor.submit() starts the worker in a
# fresh context, dropping the run's API-key overlay and its progress run_id tag —
# which made concurrent web runs leak progress events into each other's SSE
# streams. Every submit() in this module goes through this wrapper.
from src.utils.run_config import submit as _ctx_submit

from src.graph.state import AgentState
from src.utils.progress import progress
from src.agents.routing.macro_regime import run_macro_regime_classifier
from src.agents.routing.strategic_router import run_strategic_router
from src.agents.industry.edgar_hkex_resolver import run_edgar_hkex_resolver
from src.agents.intelligence.insider_activity_agent import run_insider_activity_agent
from src.agents.intelligence.analyst_revision_agent import run_analyst_revision_agent
from src.agents.intelligence.news_sentiment_agent import run_news_sentiment_agent
from src.agents.intelligence.earnings_quality_agent import run_earnings_quality_agent
from src.agents.intelligence.short_interest_agent import run_short_interest_agent
from src.agents.industry.specialist import assemble_industry_brief_merged, run_industry_specialist
from src.agents.industry.data_router import run_data_router
from src.agents.analysis.dcf_agent import run_dcf_agent
# SOTP extractor is IDE-only until reproducibility is proven; some deploys
# legitimately lack the module. Guard the import so a partial deploy keeps
# the prior pipeline behavior instead of crashing at startup.
try:
    from src.agents.analysis.sotp_extractor import run_sotp_extractor
except Exception:  # ImportError or any missing transitive dependency
    run_sotp_extractor = None
from src.agents.analysis.peer_comparison import run_peer_comparison
from src.agents.analysis.scenario_agent import run_scenario_agent
from src.agents.analysis.power_law_agent import run_power_law_agent
from src.agents.analysis.value_trap_agent import run_value_trap_agent
# Citation auditor removed from pipeline — see commit notes.
# citation_audit dict is still seeded empty for backward compat with PDF/frontend.
from src.agents.risk_manager import run_advanced_risk_manager, risk_management_agent
from src.agents.portfolio_manager import run_advanced_portfolio_manager
from src.agents.portfolio.post_trade_review import run_post_trade_review
from src.memory.run_archive import save_run, archive_summary, get_phase_cache
from src.utils.pdf_report import _compute_vgpm

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TRADE_LOG_PATH = os.path.join(DATA_DIR, "trade_log.json")


def _env_seconds(name: str, default: float) -> float:
    """Env-tunable timeout in seconds; invalid values fall back to default."""
    try:
        _v = os.environ.get(name, "").strip()
        return float(_v) if _v else default
    except ValueError:
        return default


# Hard wall-clock caps on the parallel-block joins (R1 reliability batch).
# Before this, a hung dependency (FMP/yfinance/Tavily never returning) hung
# the run forever — ThreadPoolExecutor joins blocked without a timeout and
# the in-process SSE stream heartbeated indefinitely. A bounded join converts
# the hang into a fail-fast RuntimeError: the dedup slot is released, the
# user sees a clear error and can retry immediately.
# Front block: Claude client timeout 600s x retry headroom. Bumped +100s for
# the L2 fast path: the one freshness web search now runs as the block's 5th
# leg (self-bounds at ~45s/ticker, soft-fail), so the shared deadline gets a
# little extra headroom on top of the existing four legs.
_FRONT_BLOCK_TIMEOUT_S = _env_seconds("PIPELINE_FRONT_BLOCK_TIMEOUT_S", 1600.0)
# Phase-7 trio: fast-tier Qwen ~120s calls x retry headroom.
_PHASE7_TIMEOUT_S = _env_seconds("PIPELINE_PHASE7_TIMEOUT_S", 900.0)

# Engine-cache version gate for the Phase 4.5 dcf_range archive cache.
# Archived dcf_range payloads are only reusable when stamped with this
# version at serialization time; anything older (or unstamped) is treated
# as stale and recomputed. Bump whenever a DCF-engine change alters the
# IVs a cached entry would otherwise keep serving:
#   v1 (implicit, unstamped): pre-task-#26 legacy engine — wrong legacy IVs
#     for CN names (FX artifact in _analyst_growth_bands) and no SOTP blend.
#   v2: task #26 FX fix + task #27 SOTP(analyst) snapshot attachment.
_DCF_CACHE_VERSION = 2


def _bounded_join(executor, futures: list, timeout_s: float, label: str) -> list:
    """Join `futures` against ONE shared wall-clock deadline.

    Returns the results in submission order. On deadline expiry the queued
    futures are cancelled and RuntimeError('<label> timed out ...') is
    raised — a hung dependency becomes a bounded failure instead of an
    infinite block. Exceptions raised INSIDE a future propagate unchanged
    (fail semantics identical to the sequential pipeline).

    The executor is shut down here (callers must NOT use a `with` block:
    its shutdown(wait=True) on exit would block until every future
    completes, defeating the deadline).
    """
    deadline = time.monotonic() + timeout_s
    try:
        results = []
        for _f in futures:
            _remaining = deadline - time.monotonic()
            if _remaining <= 0:
                raise TimeoutError
            results.append(_f.result(timeout=_remaining))
    except TimeoutError:
        executor.shutdown(wait=False, cancel_futures=True)
        raise RuntimeError(
            f"{label} timed out after {timeout_s:.0f}s — a dependency hung; "
            f"run aborted")
    except BaseException:
        # A phase raised: keep the old `with`-block semantics — join the
        # sibling phases before propagating so no threads are orphaned.
        executor.shutdown(wait=True)
        raise
    executor.shutdown(wait=True)
    return results

# Speed round 2 (R5): card-QA delta check — how far back to look for a prior
# run whose card+research inputs match this one. Reuse only happens when the
# stored hash, QA version and clean-audit conditions all hold (see
# src.agents.audit.card_qa_agent.should_reuse_card_qa). Env gate:
# CARD_QA_DELTA=true (default) | false.
_CARD_QA_REUSE_DAYS = 7

# L1 fast path: the pipeline's recap read is effectively uncapped — the most
# recent recap is the right prior-report anchor (and PM flip-flag context) at
# ANY age. get_recent_recap treats max_age_days=None as "use the env default"
# (RECAP_MAX_AGE_DAYS, 30d), so pass a horizon large enough to never bind
# instead of changing that function's contract (the Pulse endpoint relies on
# the 30d default).
_RECAP_UNCAPPED_DAYS = 36500


def _merge_resume_into_phase_cache(
    phase_cache: dict,
    resume_bundle: dict | None,
    tickers: list[str],
) -> bool:
    """R2 checkpoint resume — fill GAPS in the archive phase cache with a
    crashed predecessor run's checkpoint outputs.

    Archive entries always win: they come from COMPLETED runs. Checkpoint
    values only fill keys the archive left missing/empty, so the existing
    `_all_cached` skip gates (power_law / value_trap / industry_brief /
    dcf_range) fire unchanged. Returns True if anything was seeded.
    """
    if not resume_bundle:
        return False
    data = resume_bundle.get("data") or {}
    if not data:
        return False

    # checkpoint payload field -> phase-cache key, per-ticker or global
    _PER_TICKER = {
        "dcf_range":           "dcf_range",
        "power_law_analysis":  "power_law",
        "value_trap_analysis": "value_trap",
    }
    _GLOBAL = {
        "industry_brief": "industry_brief",
        "deep_research":  "deep_research",
    }

    seeded = False
    for t in tickers:
        entry = phase_cache.get(t)
        if not entry:
            entry = {
                "run_id":           resume_bundle.get("run_id") or "",
                "run_at":           resume_bundle.get("run_at") or "",
                "age_days":         resume_bundle.get("age_days") or 0.0,
                "industry_brief":   None,
                "deep_research":    None,
                "power_law":        None,
                "dcf_range":        None,
                "citation_audit":   None,
                "scenario":         None,
                "value_trap":       None,
                "sector_card_hash": None,
                "card_qa_audit":    None,
            }
            phase_cache[t] = entry
        for src_key, cache_key in _GLOBAL.items():
            if not entry.get(cache_key) and data.get(src_key):
                entry[cache_key] = data[src_key]
                seeded = True
        for src_key, cache_key in _PER_TICKER.items():
            val = (data.get(src_key) or {}).get(t)
            if not entry.get(cache_key) and val:
                entry[cache_key] = val
                seeded = True
    return seeded


# ── M1 recency loop: freshness delta ─────────────────────────────────────────
# One cheap web search per ticker that has a prior report recap, classifying
# whether anything MATERIAL changed since that report. User-triggered by
# design (runs inside the requested pipeline — nothing runs unattended).
# Every failure mode is soft: the run always continues.
#
# M2 Track A1: the search + classify pair moved to src/memory/freshness.py,
# shared with the Pulse endpoint (Track C). Thin callers stay here.

from src.memory.freshness import classify_delta as _classify_delta  # back-compat alias
from src.memory.freshness import run_freshness_search


def _delta_for_ticker(ticker: str, prior: dict, tavily_key: str | None) -> dict:
    """One bounded search + classification for a single ticker. Soft-fail:
    always returns a well-formed delta dict. Thin caller over
    src.memory.freshness (M2 Track A1) — Qwen primary, Tavily secondary."""
    return run_freshness_search(ticker, prior, tavily_key=tavily_key)


def _pulse_cache_delta(ticker: str) -> dict | None:
    """L3: same-day Pulse-endpoint cache (kind='pulse'). The Pulse flow
    already paid for the exact `run_freshness_search` result today — reuse
    it instead of searching again. Same gate as the Pulse endpoint's own
    same-day read (routes/analysis.py::_pulse_cache_get_delta): cache row
    <=1d old AND pulse_date == today (UTC). `discovery`-shaped deltas (no
    prior coverage) are NOT reused — pipeline 2_9 only runs for tickers
    WITH a prior recap, so the anchored shape is the only valid one here.
    Soft-fail: any read problem -> None -> caller falls through to a search."""
    try:
        from datetime import timezone as _tz
        from src.research_ideas.complacency import web_research as _wr
        payload = _wr._cache_get(ticker, "pulse", "", max_age_days=1)
        if not payload:
            return None
        _today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
        if payload.get("pulse_date") != _today:
            return None
        delta = payload.get("delta")
        if not isinstance(delta, dict) or delta.get("discovery"):
            return None
        return delta
    except Exception as exc:
        print(f"  [freshness] {ticker}: pulse cache read failed: {exc}")
        return None


def _run_freshness_delta(
    tickers: list[str],
    prior_reports: dict[str, dict],
) -> dict[str, dict]:
    """M1 phase 2.9 — freshness delta for every ticker with a prior recap.
    Kill switch: FRESHNESS_DELTA_SEARCH=false.
    L3: a same-day Pulse-cache hit is reused as-is (0 searches) — the Pulse
    flow already paid for that search today."""
    if os.environ.get("FRESHNESS_DELTA_SEARCH", "true").strip().lower() in (
            "0", "false", "no", "off", ""):
        return {}
    tavily_key = os.environ.get("TAVILY_API_KEY")
    deltas: dict[str, dict] = {}
    for t in tickers:
        prior = prior_reports.get(t)
        if not prior:
            continue
        _cached = _pulse_cache_delta(t)
        if _cached is not None:
            print(f"  [freshness] {t}: reusing today's Pulse-cache delta "
                  f"(0 searches)")
            deltas[t] = _cached
            continue
        deltas[t] = _delta_for_ticker(t, prior, tavily_key)
        # Write-through (mirror of L3): this run's delta becomes today's
        # Pulse beat 2 — later pulses for this ticker serve it from cache
        # instead of searching again. Soft-fail inside the helper.
        try:
            from src.memory import freshness as _freshness
            _freshness.publish_pulse_delta(t, deltas[t])
        except Exception as exc:
            print(f"  [freshness] {t}: pulse write-through failed: {exc}")
    return deltas


def run_advanced_pipeline(
    tickers: list[str],
    start_date: str,
    end_date: str,
    portfolio: dict,
    model_name: str = "claude-sonnet-4-6",
    model_provider: str = "Anthropic",
    show_reasoning: bool = False,
    enable_post_trade_review: bool = False,
    management_guidance: dict[str, dict] | None = None,
    on_checkpoint: "callable | None" = None,
    resume_bundle: dict | None = None,
) -> dict:
    """
    Run the full advanced pipeline (committee-free as of M2 Track D/E).

    on_checkpoint, if provided, is called after the deep_research,
    industry_brief and final_calculation checkpoints so the caller can
    persist partial results early.
    Signature: on_checkpoint(state: AgentState, checkpoint_name: str) -> None

    resume_bundle, if provided (see analysis_service._build_resume_bundle),
    is a crashed predecessor run's checkpoint payload. Its research/valuation
    outputs seed the phase cache so the expensive skip gates fire; later
    phases always rerun fresh.
    Returns a result dict compatible with print_trading_output().
    """
    progress.start()

    # B2 background executor (peer comparison + price history shadow run).
    # Initialised here so the finally-block cleanup is safe even if the
    # pipeline raises before the shadow launch.
    _bg_peer_exec: ThreadPoolExecutor | None = None

    try:
        # ----------------------------------------------------------------
        # Initialise state
        # ----------------------------------------------------------------
        state: AgentState = {
            "messages": [HumanMessage(content="Advanced 10-phase pipeline analysis")],
            "data": {
                "tickers": tickers,
                "portfolio": portfolio,
                "start_date": start_date,
                "end_date": end_date,
                "analyst_signals": {},
                "management_guidance": management_guidance or {},
            },
            "metadata": {
                "show_reasoning": show_reasoning,
                "model_name": model_name,
                "model_provider": model_provider,
            },
        }

        # ── Decision path (M2 Track D) ───────────────────────────────────
        # The investor committee and debate round are decommissioned: the
        # portfolio manager decides from qualitative research + quantitative
        # valuation directly. No investor phase, no debate phase.
        primary_ticker = tickers[0] if tickers else ""
        print("  Decision path: qualitative + valuation (no committee)")

        # ----------------------------------------------------------------
        # Phase timing instrumentation (Workstream A)
        # Every phase's wall-clock duration is appended to one shared list,
        # mounted at state["data"]["phase_durations"] and serialised via the
        # return dict into web_runs.full_result_json + the dedicated
        # web_runs.phase_durations column. The list OBJECT is shared (never
        # copied) so checkpoint partial saves see in-progress timings.
        # ----------------------------------------------------------------
        _phase_durations: list[dict] = []
        state["data"]["phase_durations"] = _phase_durations

        @contextmanager
        def _timed(phase_name: str):
            _t0 = time.perf_counter()
            _started_at = datetime.now().isoformat(timespec="seconds")
            try:
                yield
            finally:
                _dur = time.perf_counter() - _t0
                _phase_durations.append({
                    "phase":       phase_name,
                    "started_at":  _started_at,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "duration_s":  round(_dur, 2),
                })
                # Re-mount in case the timed phase replaced the state dict
                state["data"]["phase_durations"] = _phase_durations
                # One-line, Railway-greppable duration log
                print(f"  [timing] {phase_name}: {_dur:.1f}s")

        # ----------------------------------------------------------------
        # ARCHIVE CACHE — load recent phase outputs so expensive phases
        # (Industry Brief, DCF, Power Law, Citation) can be skipped when
        # fresh-enough data already exists in the archive.
        # Deep Research has its own internal caching (data_router/deep_research.py)
        # and is intentionally NOT bypassed here.
        #
        # L2 fast path: moved BEFORE the front block so the one freshness web
        # search can run as the block's 5th leg — hiding its ~45s behind the
        # parallel macro/router/intel/edgar work. These are pure DB reads (<1s).
        # ----------------------------------------------------------------
        _phase_cache: dict[str, dict | None] = {}
        _prior_reports: dict[str, dict] = {}
        with _timed("2_8_archive_cache_load"):
            for _t in tickers:
                _phase_cache[_t] = get_phase_cache(_t, max_age_days=60)
                if _phase_cache[_t]:
                    _c = _phase_cache[_t]
                    print(f"  [cache] {_t}: found recent run from "
                          f"{_c['run_at'][:10]} (age {_c['age_days']:.1f}d) — "
                          f"brief={'✓' if _c.get('industry_brief') else '✗'} "
                          f"dcf={'✓' if _c.get('dcf_range') else '✗'} "
                          f"power_law={'✓' if _c.get('power_law') else '✗'} "
                          f"citation={'✓' if _c.get('citation_audit') else '✗'}")

            # R2 checkpoint resume — seed gaps from a crashed predecessor's
            # checkpoint so the expensive Phase 3/4 skip gates fire.
            if resume_bundle:
                _rb_data = resume_bundle.get("data") or {}
                if _merge_resume_into_phase_cache(_phase_cache, resume_bundle, tickers):
                    print(f"  [resume] phase cache seeded from checkpoint "
                          f"'{resume_bundle.get('checkpoint')}' (run "
                          f"{str(resume_bundle.get('run_id') or '')[:8]}, "
                          f"{(resume_bundle.get('age_days') or 0) * 24:.1f}h old)")
                    progress.update_status(
                        "archive_cache", primary_ticker,
                        f"[resume] recovered checkpoint '{resume_bundle.get('checkpoint')}' "
                        f"— reusing Phase 3/4 outputs")
                if _rb_data.get("deep_research"):
                    # Shaped exactly like run_archive.get_recent_research's
                    # return so deep_research's pure-cache path consumes it
                    # unchanged (popped inside run_deep_research_agent).
                    state["data"]["_resume_research"] = {
                        "run_id":                 resume_bundle.get("run_id"),
                        "run_at":                 resume_bundle.get("run_at"),
                        "analysis_date":          end_date,
                        "age_days":               resume_bundle.get("age_days") or 0.0,
                        "research_tier":          _rb_data.get("research_tier"),
                        "deep_research_text":     _rb_data.get("deep_research"),
                        "deep_research_sections": _rb_data.get("deep_research_sections") or {},
                    }

            # ── M1 recency loop: load each ticker's last report recap ──────
            # Feeds phase 2.9 (freshness delta), the deep-research/PM prompt
            # injections and the saved payload's data.prior_recap. A ticker
            # with no archive/resume cache entry gets a minimal dict: the
            # oversized age_days keeps every _all_cached skip gate inert,
            # while resume-seeded entries keep their own (usable) age.
            # L1 fast path: the recap read is uncapped (_RECAP_UNCAPPED_DAYS)
            # — the most recent recap is the right prior-report anchor at ANY
            # age; the 30d default cap starved the freshness anchor for older
            # memory.
            try:
                from src.memory import report_recap as _report_recap
                if _report_recap.recaps_enabled():
                    for _t in tickers:
                        _pr = _report_recap.get_recent_recap(
                            _t, max_age_days=_RECAP_UNCAPPED_DAYS)
                        if not _pr:
                            continue
                        if _phase_cache[_t] is None:
                            _phase_cache[_t] = {"age_days": 999.0}
                        _phase_cache[_t]["prior_report"] = _pr
                        print(f"  [recap] {_t}: prior report recap loaded "
                              f"({_pr.get('final_action') or 'N/A'}, "
                              f"{_pr.get('age_days', 0):.1f}d old)")
            except Exception as _recap_exc:
                print(f"  [recap] prior recap load failed: {_recap_exc}")

            # Build the prior-report map for the freshness leg (2_9) and the
            # saved payload's data.prior_recap.
            for _t in tickers:
                _pr = (_phase_cache.get(_t) or {}).get("prior_report")
                if _pr:
                    _prior_reports[_t] = _pr
                    _pr_px = _pr.get("price_at_run")
                    progress.update_status(
                        "archive_cache", _t,
                        f"[recap] prior: {_pr.get('final_action') or 'N/A'}"
                        + (f" @ ${_pr_px}" if _pr_px else "")
                        + f" ({_pr.get('age_days') or 0:.1f}d old)")
            if _prior_reports:
                # Progress visibility: the freshness search below runs as the
                # front block's 5th leg (bounded ~45s/ticker, soft-fail).
                progress.update_status(
                    "archive_cache", primary_ticker,
                    "Searching for material developments since the prior report…")

        # ----------------------------------------------------------------
        # FRONT BLOCK — Phases 1, 2, 2.5, 2.7 (parallel)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[1-2.7/10] Front block (Macro ∥ Router ∥ Intelligence ∥ EDGAR ∥ Freshness, parallel)")
        print('='*60)
        # B1 — phases 1 + 2 + 2.5 + 2.7 run CONCURRENTLY below, plus (L2) the
        # one 2_9 freshness web search as a 5th leg. The four research phases
        # need only tickers + dates and write DISJOINT state keys (verified
        # by grep):
        #   macro  → macro_regime, position_size_cap
        #   router → company_name, sector(s), profile_name(s),
        #             raw_financials, routing_decision, insider_summary, …
        #   intel  → insider_activity, analyst_revisions, news_sentiment,
        #             earnings_quality, short_interest, analyst_signals
        #   edgar  → edgar_filing_refs
        # The router does NOT read macro_regime (verified), so no ordering
        # dependency exists. Each research phase runs on a deepcopy; results
        # merge after the join. Wall time becomes max(the five legs) instead of
        # the sum (~35-70 s saved on fresh runs; more when FMP is slow). Per-phase
        # timings keep their original phase names (overlapping started_at
        # values reveal the concurrency); SSE progress events are emitted
        # post-merge in the same order as the sequential pipeline, so
        # frontend map-based tracking is unaffected.

        def _timed_call(phase_name: str, fn, st):
            """_timed() equivalent safe to run inside a worker thread."""
            _t0 = time.perf_counter()
            _started_at = datetime.now().isoformat(timespec="seconds")
            try:
                return fn(st)
            finally:
                _dur = time.perf_counter() - _t0
                _phase_durations.append({
                    "phase":       phase_name,
                    "started_at":  _started_at,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "duration_s":  round(_dur, 2),
                })
                print(f"  [timing] {phase_name}: {_dur:.1f}s")

        import copy as _copy
        _st_macro  = _copy.deepcopy(state)
        _st_router = _copy.deepcopy(state)
        _st_intel  = _copy.deepcopy(state)
        _st_edgar  = _copy.deepcopy(state)

        # NOTE: manual executor lifecycle — a `with` context manager's
        # shutdown(wait=True) would block until every future completes and
        # defeat the deadline. _bounded_join owns shutdown and raises
        # RuntimeError("<block> timed out ...") on a hang instead of
        # blocking forever. Exceptions from phases re-raise at the join
        # exactly as the sequential pipeline would have raised mid-phase.
        def _freshness_leg(_st_unused):
            """L2: the one per-ticker freshness search + classify (phase 2_9)
            as a front-block leg. Reads only the pre-built _prior_reports and
            writes nothing to state — the result is mounted after the join.
            Soft-fail: _run_freshness_delta always returns a well-formed dict
            and self-bounds (~45s search timeout per ticker)."""
            return _run_freshness_delta(list(tickers), _prior_reports)

        _front_ex = ThreadPoolExecutor(max_workers=5)
        _f_macro  = _ctx_submit(_front_ex, _timed_call, "1_macro_regime",
                                run_macro_regime_classifier, _st_macro)
        _f_router = _ctx_submit(_front_ex, _timed_call, "2_strategic_router",
                                run_strategic_router, _st_router)
        _f_intel  = _ctx_submit(_front_ex, _timed_call, "2_5_intelligence",
                                _run_intelligence_agents_parallel, _st_intel)
        _f_edgar  = _ctx_submit(_front_ex, _timed_call, "2_7_edgar_hkex",
                                run_edgar_hkex_resolver, _st_edgar)
        # L2: 5th leg — the one freshness web search runs CONCURRENTLY with
        # the four research legs instead of sequentially after them. It needs
        # only _prior_reports (built in the pre-block 2_8) and writes no
        # state, so it needs no deepcopy of its own.
        _f_fresh  = _ctx_submit(_front_ex, _timed_call, "2_9_freshness_delta",
                                _freshness_leg, None)

        _st_macro, _st_router, _st_intel, _st_edgar, _fresh_deltas = _bounded_join(
            _front_ex, [_f_macro, _f_router, _f_intel, _f_edgar, _f_fresh],
            _FRONT_BLOCK_TIMEOUT_S,
            "Front block (macro/router/intel/edgar/freshness)")

        # Merge: router's copy is the base (carries the most downstream
        # keys); graft the other three phases' disjoint key sets on top.
        state = _merge_front_block(_st_router, _st_macro, _st_intel, _st_edgar,
                                   _phase_durations)
        # L2: mount the prior-report map + freshness verdicts collected by the
        # front block's 5th leg — phase 3 (deep research) consumes both.
        state["data"]["prior_recap"] = dict(_prior_reports)
        state["data"]["freshness_delta"] = _fresh_deltas or {}

        regime = state["data"].get("macro_regime", {})
        print(f"  Regime: {regime.get('risk_appetite')} | "
              f"{regime.get('rate_direction')} rates | "
              f"{regime.get('volatility_regime')} vol")
        progress.update_status("macro_regime_classifier", primary_ticker, "✓ Regime identified",
                               partial_data={"macro_regime": state["data"].get("macro_regime")})

        # (Phase 2 strategic router ran in the front block above.)
        print(f"  Sector: {state['data'].get('sector')}")
        progress.update_status("strategic_router", primary_ticker, "✓ Routing complete",
                               partial_data={"routing_decision": state["data"].get("routing_decision"),
                                             "raw_financials": state["data"].get("raw_financials")})

        # (Phase 2.5 intelligence agents ran in the front block above.)
        progress.update_status("intelligence_agents", primary_ticker, "✓ Intelligence complete",
                               partial_data={"news_sentiment":   state["data"].get("news_sentiment", {}),
                                             "short_interest":   state["data"].get("short_interest", {}),
                                             "insider_activity": state["data"].get("insider_activity", {}),
                                             "analyst_revisions":state["data"].get("analyst_revisions", {}),
                                             "earnings_quality": state["data"].get("earnings_quality", {}),
                                             "analyst_signals":  state["data"].get("analyst_signals", {})})
        for ticker in tickers:
            ia = state["data"].get("insider_activity", {}).get(ticker, {})
            ar = state["data"].get("analyst_revisions", {}).get(ticker, {})
            ns = state["data"].get("news_sentiment", {}).get(ticker, {})
            eq = state["data"].get("earnings_quality", {}).get(ticker, {})
            si = state["data"].get("short_interest", {}).get(ticker, {})
            print(
                f"  {ticker}: insider={ia.get('signal','?')} "
                f"(src={ia.get('data_source','?')}) | "
                f"revision={ar.get('revision_direction','?')} "
                f"streak={ar.get('surprise_streak', 0):+d} | "
                f"news={ns.get('signal','?')} "
                f"score={ns.get('composite_score', 0.0):+.3f} | "
                f"eq_quality={eq.get('quality_verdict','?')} "
                f"({eq.get('overall_quality_score', 0.0):.1f}/10) "
                f"pre_earn_risk={eq.get('pre_earnings_risk','?')} | "
                f"short={si.get('signal','?')} "
                f"float={si.get('short_float_pct','?')}% "
                f"squeeze={si.get('squeeze_risk','?')}"
            )

        # (Phase 2.7 EDGAR/HKEX resolver ran in the front block above.
        #  US tickers: SEC EDGAR accession + filing URL; HK tickers:
        #  HKEXnews Annual Report PDF URL — lets deep research cite
        #  financial data to the primary source.)
        for ticker in tickers:
            ref = state["data"].get("edgar_filing_refs", {}).get(ticker, {})
            if ref:
                print(
                    f"  {ticker}: {ref.get('filing_type')} — "
                    f"acc={ref.get('accession_number')} | "
                    f"period={ref.get('period_of_report')} | "
                    f"foreign={ref.get('is_foreign')}"
                )
            else:
                print(f"  {ticker}: EDGAR filing not resolved — FMP attribution used")

        # ----------------------------------------------------------------
        # 2_9 FRESHNESS DELTA — reporting + lessons only (M1 recency loop).
        # L2: the actual work (uncapped recap load in the pre-block 2_8, the
        # one bounded web search per ticker + fast-tier classify as the front
        # block's 5th leg) already ran — the verdicts are mounted on
        # state["data"] above. This block reports them, runs the M1 agent-
        # lessons post-mortem, and emits the terminal archive_cache ✓ event.
        # Every failure mode is soft; the run always continues.
        # ----------------------------------------------------------------
        _fresh_deltas_rep = state["data"].get("freshness_delta") or {}
        if _prior_reports:
            for _t, _d in _fresh_deltas_rep.items():
                _n_ev = len(_d.get("events") or [])
                _mat = _d.get("material")
                if _mat is None:
                    _label = "check unavailable"
                elif _mat:
                    _label = f"MATERIAL change ({_n_ev} event(s))"
                else:
                    _label = "no material change"
                print(f"  [delta] {_t}: {_label} since "
                      f"{str(_d.get('prior_run_at') or '')[:10]} — "
                      f"{(_d.get('verdict') or '')[:160]}")
                progress.update_status("archive_cache", _t, f"[delta] {_label}")
        else:
            print("  [delta] no prior report recaps — freshness check skipped")

        # ── M1 agent lessons: post-mortem any detected gap ─────────────
        # Detection reads each ticker's last SCORED archive run
        # (INCORRECT outcome or DCF calibration error); no gap → no LLM
        # call at all, and a gap that already produced lessons is
        # skipped too. Lazy/user-triggered exactly like the freshness
        # check above; failures are logged and never break the run.
        try:
            from src.memory import agent_lessons as _agent_lessons
            if _agent_lessons.lessons_enabled():
                for _t in tickers:
                    _agent_lessons.maybe_generate_lessons(
                        _t, _prior_reports.get(_t))
        except Exception as _lessons_exc:
            print(f"  [lessons] lesson generation failed: {_lessons_exc}")

        # Terminal ✓ event: archive_cache never got a completion marker,
        # so the progress bar froze at the front-block percentage for the
        # entire 2_8+2_9 window ("archive cache 28% forever" on cache
        # runs). Counting it as a done phase keeps the bar moving through
        # the reuse work and marks the phase complete in /analysis/status.
        _n_mat = sum(
            1 for _d in _fresh_deltas_rep.values()
            if _d.get("material"))
        progress.update_status(
            "archive_cache", primary_ticker,
            "✓ Research memory checked — prior report + freshness delta loaded"
            + (f" ({_n_mat} material)" if _n_mat else ""))

        def _all_cached(key: str, age_days: float = 7.0) -> bool:
            """True if every ticker has a fresh-enough, non-empty cache entry for key.

            Truthy check (not `is not None`) is deliberate: a prior run that
            crashed or skipped a ticker still archives that phase as `{}`
            (e.g. dcf_range on a DCF-engine failure — see pipeline.py's
            except block around run_dcf_agent). `{} is not None` is True, so
            the old `is not None` check treated that empty placeholder as a
            valid cache hit and silently propagated the emptiness forward for
            up to `age_days`, with no error surfaced anywhere in the new run.
            """
            return all(
                _phase_cache.get(t) is not None
                and _phase_cache[t].get(key)  # type: ignore[union-attr]
                and _phase_cache[t]["age_days"] <= age_days  # type: ignore[index]
                for t in tickers
            )

        # ── Stream cached structural phases to frontend immediately ──────────────
        # These phases (Power Law, Value Trap) have no dependency on live data —
        # emit them now so the frontend can render them while deep research runs.
        if _all_cached("power_law", age_days=60.0):
            _early_pl: dict = {_t: _phase_cache[_t]["power_law"] for _t in tickers}  # type: ignore[index]
            state["data"]["power_law_analysis"] = _early_pl
            for _t in tickers:
                progress.update_status("power_law_agent", _t,
                                       f"[cache] Score {_early_pl[_t].get('total_score','?')}/10 "
                                       f"({_phase_cache[_t]['age_days']:.1f}d old)",  # type: ignore[index]
                                       partial_data={"power_law_analysis": _early_pl})
            print(f"  [cache] Power Law streamed to frontend early — skipping Phase 7 LLM call")

        if _all_cached("value_trap", age_days=30.0):
            _early_vt: dict = {_t: _phase_cache[_t]["value_trap"] for _t in tickers}  # type: ignore[index]
            state["data"]["value_trap_analysis"] = _early_vt
            for _t in tickers:
                progress.update_status("value_trap_agent", _t,
                                       f"[cache] Loaded from archive ({_phase_cache[_t]['age_days']:.1f}d old)",  # type: ignore[index]
                                       partial_data={"value_trap_analysis": _early_vt})
            print(f"  [cache] Value Trap streamed to frontend early — skipping Phase 7 LLM call")

        # ----------------------------------------------------------------
        # B2 — Peer Comparison (4.6) + Price History (4.7) shadow launch.
        # Both need only tickers + sector + dates (all present after the
        # front-block merge), and their sole consumers are the pipeline
        # return dict and the PDF report — nothing between here and their
        # nominal phase slots reads peer_comparison or price_history.
        # Launched now, they run CONCURRENTLY with deep research / industry
        # brief / DCF and are joined at their nominal 4.6/4.7 slots, hiding
        # ~1 min of wall time entirely. run_peer_comparison writes exactly
        # one state key (peer_comparison — verified in source), so joining
        # extracts just that key into the live state.
        # ----------------------------------------------------------------
        _bg_peer_exec = ThreadPoolExecutor(max_workers=2)
        _st_peer = _copy.deepcopy(state)
        _f_peer_bg = _ctx_submit(_bg_peer_exec, run_peer_comparison, _st_peer)
        _ph_api_key_bg = (
            state["data"].get("api_key")
            or os.environ.get("FINANCIAL_DATASETS_API_KEY")
        )
        _f_ph_bg = _ctx_submit(_bg_peer_exec, _fetch_price_history,
                               list(tickers), end_date, _ph_api_key_bg)
        print("  [B2] Peer comparison + price history launched in background")

        # ----------------------------------------------------------------
        # PHASE 3 — Deep Research (Claude+Tavily) & Data Router
        # Deep research runs first so the Industry Specialist can use the report.
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[3/10] Deep Research (Claude+Tavily) & Data Router")
        print('='*60)
        with _timed("3_deep_research_router"):
            state = run_data_router(state)
        # Emit comprehensive partial_data so the frontend populates Valuation
        # Key Metrics + Commentary cards mid-run (2026-04-25).
        # Previous emit only surfaced the raw deep_research text + citations,
        # leaving saas_metrics / bank_metrics / reit_metrics / pipeline_assets /
        # deep_research_sections / dcf_calibration / segment_scenarios stuck
        # on "Computing..." skeletons until pipeline fully completed AND
        # liveResult loaded. User feedback: "want content populated when the
        # corresponding phase completes". Extractors all finish inside Phase 3,
        # so emit them together here.
        progress.update_status("deep_research_agent", primary_ticker, "✓ Research complete",
                               partial_data={
                                   # Core research output
                                   "deep_research":           state["data"].get("deep_research"),
                                   "deep_research_annotated": state["data"].get("deep_research_annotated"),
                                   "deep_research_sections":  state["data"].get("deep_research_sections", {}),
                                   "citation_registry":       state["data"].get("citation_registry", []),
                                   "research_tier":           state["data"].get("research_tier"),
                                   # Sector extractor outputs — populate Key Metrics / Traffic Light /
                                   # Commentary cards progressively instead of post-completion only
                                   "saas_metrics":            state["data"].get("saas_metrics", {}),
                                   "bank_metrics":            state["data"].get("bank_metrics", {}),
                                   "reit_metrics":            state["data"].get("reit_metrics", {}),
                                   "pipeline_assets":         state["data"].get("pipeline_assets", {}),
                                   # DCF signal inputs (used internally by Phase 4.5 DCF but also
                                   # useful for progressive UI reveal of research confidence)
                                   "dcf_calibration":         state["data"].get("dcf_calibration", {}),
                                   "segment_scenarios":       state["data"].get("segment_scenarios", {}),
                                   # Classification — frontend routes Tech Valuation Panel variants
                                   # (Growth SaaS / Mature SaaS / Hyperscaler) via profile_name;
                                   # also surfaces in admin DB viewer sub-sector column.
                                   "profile_name":            state["data"].get("profile_name", ""),
                                   "profile_names":           state["data"].get("profile_names", {}),
                                   "sectors":                 state["data"].get("sectors", {}),
                               })

        # ── Checkpoint 1 — deep research complete ─────────────────────────────
        if on_checkpoint:
            try:
                on_checkpoint(state, "deep_research")
            except Exception as _ck_err:
                print(f"  [checkpoint] deep_research save failed (non-fatal): {_ck_err}")

        deep_research = state["data"].get("deep_research", "")
        if deep_research:
            dr_lines = deep_research.splitlines()
            searches_note = f"({len([l for l in dr_lines if l.strip()])} non-empty lines)"
            print(f"  Deep research complete {searches_note}")
            for line in dr_lines:
                print(f"    {line}")
        else:
            web_intel = state["data"].get("web_intelligence", {})
            if web_intel:
                print(f"  Web intelligence pre-fetched ({len(web_intel)} sections)")
            else:
                print("  No real-time intelligence (TAVILY_API_KEY not set)")
        print(f"  Pre-fetched data for {len(state['data'].get('routed_data', {}))} agents")

        # ----------------------------------------------------------------
        # PHASE 4 — Industry Specialist Agent (consumes deep research)
        # Cache: reuse industry_brief if all tickers have a <7-day cached run.
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[4/10] Industry Specialist Agent")
        print('='*60)
        with _timed("4_industry_brief"):
            if _all_cached("industry_brief", age_days=14.0):
                # Inject cached brief — skip LLM call entirely
                for _t in tickers:
                    _cached_brief = _phase_cache[_t]["industry_brief"]  # type: ignore[index]
                # The brief is global (same for all tickers in the run), use the first one
                state["data"]["industry_brief"] = _phase_cache[tickers[0]]["industry_brief"]  # type: ignore[index]
                progress.update_status("industry_specialist", tickers[0],
                                       f"[cache] Loaded from archive ({_phase_cache[tickers[0]]['age_days']:.1f}d old)")  # type: ignore[index]
                print(f"  [cache] Industry brief loaded from archive — skipping LLM call")
            else:
                # Speed round 2 (R1): merged mode assembles the brief
                # deterministically from deep-research SECTION 7 + FMP
                # indicators — zero LLM calls (was a ~3.5 min specialist
                # call). Falls back to the specialist when the research has
                # no SECTION 7 (archived runs pre-dating the merge) or the
                # INDUSTRY_BRIEF_MODE=legacy rollback knob is set.
                _brief_mode = os.environ.get("INDUSTRY_BRIEF_MODE", "merged").strip().lower()
                _merged_ok = False
                if _brief_mode == "merged":
                    try:
                        _merged_ok = assemble_industry_brief_merged(state)
                    except Exception as _bm_err:
                        print(f"  [brief-merged] deterministic assembly failed "
                              f"({type(_bm_err).__name__}: {_bm_err}) — falling back to specialist")
                if not _merged_ok:
                    state = run_industry_specialist(state)
        brief_lines = state["data"].get("industry_brief", "").splitlines()
        for line in brief_lines[:80]:
            print(f"  {line}")
        progress.update_status("industry_specialist", primary_ticker, "✓ Brief complete",
                               partial_data={"industry_brief": state["data"].get("industry_brief")})

        # ── Checkpoint 2 — industry brief complete ─────────────────────────────
        if on_checkpoint:
            try:
                on_checkpoint(state, "industry_brief")
            except Exception as _ck_err:
                print(f"  [checkpoint] industry_brief save failed (non-fatal): {_ck_err}")

        # ----------------------------------------------------------------
        # PHASE 4.4 — SOTP Assumption Extractor (GS-style SOTP inputs)
        # Sourced by assumption nature: reported facts from FMP line items,
        # segment revenues anchored to FMP product segmentation where
        # available, economics + multiples via two targeted LLM passes,
        # policy constants for China internet. Gated: runs only when
        # sotp_enabled is set on the request or licensed PDF evidence is
        # attached for a ticker (zero-cost otherwise). Output feeds the
        # "SOTP (analyst)" shadow method in the DCF engine.
        # ----------------------------------------------------------------
        _sotp_docs = bool(state["data"].get("sotp_enabled"))
        if not _sotp_docs:
            try:
                from src.utils.research_pdf import load_research_manifest
                _manifest = load_research_manifest(
                    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
                _sotp_docs = any(
                    d["ticker"].upper() in {t.upper() for t in tickers}
                    and d["ai_input_allowed"] for d in _manifest)
            except Exception:
                _sotp_docs = False
        if _sotp_docs and run_sotp_extractor is None:
            print("[4.4/10] SOTP extractor requested but module not deployed "
                  "— skipping (legacy pipeline behavior).")
            _sotp_docs = False
        if _sotp_docs:
            print(f"\n{'='*60}")
            print("[4.4/10] SOTP Assumption Extractor (GS-style)")
            print('='*60)
            with _timed("4_4_sotp_extractor"):
                try:
                    state = run_sotp_extractor(state)
                except Exception as _sotp_err:
                    import traceback as _tb
                    print(f"[ERROR] SOTP extractor failed (non-fatal): "
                          f"{type(_sotp_err).__name__}: {_sotp_err}")
                    print(_tb.format_exc()[:1200])
                    state["data"]["sotp_assumptions"] = {}
            _sotp_res = state["data"].get("sotp_assumptions", {})
            for ticker in tickers:
                _sa = _sotp_res.get(ticker)
                if _sa:
                    print(f"  {ticker}: {len(_sa.get('segments', []))} segments, "
                          f"holdco {_sa.get('holdco_discount_pct', 0):.0%}, "
                          f"sources={_sa.get('_sources', {})}")
                else:
                    print(f"  {ticker}: no SOTP assumptions assembled")
        else:
            state["data"]["sotp_assumptions"] = state["data"].get("sotp_assumptions", {})

        # ── Static SOTP snapshot (task #27) ─────────────────────────────────
        # Tickers validated in the IDE SOTP trial carry their trialed
        # assumptions (src/data/sotp_assumptions_v1.json, generated by
        # .stage7_snapshot_assumptions.py in NOTE mode) into production runs
        # whenever the live extractor produced nothing for them — live
        # extractor output always wins. Deterministic by construction: no
        # production LLM variance, no licensed PDFs on Railway, zero
        # run-time cost. Kill-switch: SOTP_SNAPSHOT_DISABLED=1; allowlist:
        # SOTP_SNAPSHOT_TICKERS="BABA,JD". Every failure mode degrades to
        # exact pre-task-#27 behavior (legacy IV, no "SOTP (analyst)" row).
        try:
            from src.agents.analysis.sotp_snapshot import (
                attach_snapshot, load_sotp_snapshot,
            )
            _sotp_snapshot = load_sotp_snapshot()
        except Exception as _snap_imp_err:
            print(f"  [sotp-snapshot] loader unavailable (non-fatal): "
                  f"{type(_snap_imp_err).__name__}: {_snap_imp_err}")
            _sotp_snapshot = {}
        if _sotp_snapshot:
            _sotp_merged, _sotp_attached = attach_snapshot(
                state["data"].get("sotp_assumptions") or {},
                _sotp_snapshot, list(tickers))
            for ticker in _sotp_attached:
                # Invalidate this ticker's cached dcf_range: any cache entry
                # predating this attachment was computed WITHOUT the
                # "SOTP (analyst)" method, and the Phase 4.5 cache gate
                # (_all_cached("dcf_range", 60d)) would otherwise propagate
                # the SOTP-less IV forward for up to 60 days. Popping for one
                # ticker fails the all-tickers gate → engine recomputes.
                if (_phase_cache.get(ticker) or {}).get("dcf_range"):
                    _phase_cache[ticker].pop("dcf_range", None)  # type: ignore[union-attr]
                    print(f"  [sotp-snapshot] {ticker}: dropped cached "
                          f"pre-SOTP dcf_range — engine will recompute")
            if _sotp_attached:
                state["data"]["sotp_assumptions"] = _sotp_merged
                for ticker in _sotp_attached:
                    _sa = _sotp_merged[ticker]
                    print(f"  [sotp-snapshot] {ticker}: attached "
                          f"{len(_sa.get('segments', []))} segment(s) from "
                          f"sotp_assumptions_v1.json (holdco "
                          f"{_sa.get('holdco_discount_pct', 0):.0%})")
                    progress.update_status(
                        "sotp_extractor", ticker,
                        "[snapshot] validated trialed assumptions attached")

        # ----------------------------------------------------------------
        # PHASE 4.5 — DCF Engine (deterministic, no LLM)
        # Cache: reuse dcf_range if all tickers have a <3-day cached run.
        # (Shorter window — financials can move fast.)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[4.5/10] DCF Engine (multi-method, macro-aware)")
        print('='*60)
        with _timed("4_5_dcf_engine"):
            _dcf_cache_ok = _all_cached("dcf_range", age_days=60.0)
            if _dcf_cache_ok:
                # Engine version gate: DCF-engine code changes (task #26 FX
                # fix, task #27 SOTP snapshot attach) silently invalidate
                # archived dcf_range payloads — reuse would keep serving the
                # old IVs for up to the 60-day cache window. Only entries
                # stamped with the current _DCF_CACHE_VERSION at serialization
                # time are reusable; unstamped entries predate the gate.
                for _t in tickers:
                    _stamp = (_phase_cache[_t]["dcf_range"] or {}).get(  # type: ignore[index]
                        "_engine_cache_version")
                    if _stamp != _DCF_CACHE_VERSION:
                        print(f"  [cache] {_t}: cached dcf_range stamped "
                              f"engine v{_stamp} (current v{_DCF_CACHE_VERSION})"
                              f" — stale, recomputing")
                        _dcf_cache_ok = False
                        break
            if _dcf_cache_ok:
                cached_dcf: dict = {}
                for _t in tickers:
                    _cd = _phase_cache[_t]["dcf_range"]  # type: ignore[index]
                    cached_dcf[_t] = _cd
                    progress.update_status("dcf_engine", _t,
                                           f"[cache] Loaded from archive ({_phase_cache[_t]['age_days']:.1f}d old)")  # type: ignore[index]
                state["data"]["dcf_range"] = cached_dcf
                print(f"  [cache] DCF range loaded from archive — skipping recalculation")
            else:
                # Defensive exception handling — surface any silent DCF crash via
                # progress.update_status so the error is visible in /analysis/status
                # and the frontend SSE stream. Previously an exception in run_dcf_agent
                # would propagate up through run_advanced_pipeline, be caught by the
                # analysis_service wrapper, and become an invisible RuntimeError —
                # user would see "pipeline_complete" with no valuation and no trace.
                try:
                    progress.update_status("dcf_engine", primary_ticker, "Starting DCF engine")
                    state = run_dcf_agent(state)
                except Exception as _dcf_exc:
                    import traceback as _tb
                    _err_head = f"{type(_dcf_exc).__name__}: {str(_dcf_exc)[:200]}"
                    _err_trace = _tb.format_exc()[:1500]
                    progress.update_status(
                        "dcf_engine", primary_ticker,
                        f"DCF CRASHED — {_err_head}"
                    )
                    print(f"\n[ERROR] DCF engine crashed:\n{_err_trace}\n")
                    # Persist the exception to state for post-hoc forensics —
                    # progress.update_status is transient, print() goes to
                    # Railway logs but isn't accessible from /analysis/runs.
                    # state["data"]["dcf_engine_error"] is the only surface
                    # that survives into the run JSON for browsing later.
                    state["data"]["dcf_engine_error"] = {
                        "exception_type": type(_dcf_exc).__name__,
                        "message":        str(_dcf_exc)[:500],
                        "traceback":      _err_trace,
                        "primary_ticker": primary_ticker,
                        "tickers":        list(tickers),
                    }
                    # Ensure dcf_range is set to empty dict for each ticker so
                    # downstream code doesn't re-throw on missing key
                    state["data"]["dcf_range"] = {t: {} for t in tickers}
        dcf_range = state["data"].get("dcf_range", {})
        for ticker in tickers:
            dcf = dcf_range.get(ticker, {})
            if dcf and dcf.get("base"):
                base_iv = dcf["base"]["intrinsic_value"]
                wacc = dcf.get("wacc", 0)
                src = dcf.get("data_source", "?")
                profile = dcf.get("profile", "—")
                c_macro = dcf.get("c_macro", 0)
                cal_tag = " ⚠ CALIBRATION ERROR" if dcf.get("calibration_error") else ""
                methods = dcf["base"].get("methods_used", [])
                fwd_flags = dcf["base"].get("forward_flags", [])
                print(f"  {ticker}: base IV ${base_iv:.2f} | WACC {wacc:.1%} | "
                      f"C_macro {c_macro:+.2f} | profile: {profile} | source: {src}{cal_tag}")
                if methods:
                    print(f"    methods: {', '.join(methods)}")
                for flag in fwd_flags:
                    print(f"    ↳ {flag}")
                if dcf.get("calibration_error"):
                    print(f"    ↳ {dcf.get('calibration_note', '')}")
            else:
                print(f"  {ticker}: DCF skipped (insufficient data)")

        progress.update_status("dcf_engine", primary_ticker, "✓ DCF complete",
                               partial_data={"dcf_range": state["data"].get("dcf_range")})

        # ── Sector valuation card — FIRST mid-run emit ────────────────────────
        # By this point profile_names (Phase 3), the metric extractors (Phase 3),
        # and the DCF multiples (just now) are all in state, so the card renders
        # substantially complete. Emit it as partial_data so the frontend's
        # SectorValuationCard populates DURING the run instead of only after the
        # whole pipeline finishes. The authoritative final render still happens
        # later (Phase 10) and flows to the archive. Wrapped in try/except so a
        # render failure never breaks this phase emit (mirrors the final render's
        # defensive guard below).
        try:
            from src.data.sector_kpi_framework import render_card_payloads_for_run as _render_sc
            _sc_partial = _render_sc(state) or {}
            if _sc_partial:
                progress.update_status("dcf_engine", primary_ticker, "✓ DCF complete",
                                       partial_data={"sector_card": _sc_partial})
        except Exception as _sc_e:
            print(f"  [sector_card] mid-run render failed (non-fatal): {_sc_e!r}")

        # ----------------------------------------------------------------
        # PHASE 4.6 — Peer Comparison Engine (deterministic, no LLM)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[4.6/10] Peer Comparison Engine")
        print('='*60)
        with _timed("4_6_peer_comparison"):
            # B2: ran in the background since the front block — just join.
            # run_peer_comparison writes exactly one data key; extract it.
            _st_peer_done = _f_peer_bg.result()
            state["data"]["peer_comparison"] = _st_peer_done["data"].get("peer_comparison", {})
        peer_comp = state["data"].get("peer_comparison", {})
        for ticker in tickers:
            peers_found = list(peer_comp.get(ticker, {}).keys())
            print(f"  {ticker}: {len(peers_found)} tickers fetched "
                  f"({', '.join(peers_found[:5])})")

        # ----------------------------------------------------------------
        # PHASE 4.7 — Price History (12-month, for sparkline)
        # ----------------------------------------------------------------
        with _timed("4_7_price_history"):
            # B2: fetched in the background since the front block — join.
            price_history_all: dict[str, list] = _f_ph_bg.result()
            state["data"]["price_history"] = price_history_all
        _bg_peer_exec.shutdown(wait=False)

        # ----------------------------------------------------------------
        # PHASES 5+6 — decommissioned (M2 Track D/E)
        # The investor committee and debate round no longer run: the PM
        # decides from qualitative research + quantitative valuation.
        # ----------------------------------------------------------------

        # ----------------------------------------------------------------
        # PHASE 7 — Scenario + Power Law + Value Trap (parallel)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[7/10] Phase 3 Analysis: Scenario + Power Law + Value Trap (parallel)")
        print('='*60)

        # Run Phase 8 (basic risk manager) first so scenario has current prices
        # risk_management_agent returns a partial state (LangGraph pattern) — merge, don't replace
        with _timed("7_0_basic_risk"):
            _partial = risk_management_agent(state)
            state["messages"] = _partial.get("messages", state["messages"])
            state["data"].update(_partial.get("data", {}))

        import copy

        with _timed("7_scenario_pl_trap"):
            # Power law: skip LLM if cached within 60 days (structural moat is sticky)
            _power_law_cached = _all_cached("power_law", age_days=60.0)
            # Value trap: skip LLM if cached within 30 days (financial quality signals are semi-stable)
            _value_trap_cached = _all_cached("value_trap", age_days=30.0)
            # (cached data was already emitted to frontend before Phase 3 — no re-emission needed here)

            state_copy_a = copy.deepcopy(state)
            state_copy_b = copy.deepcopy(state)
            state_copy_c = copy.deepcopy(state)

            workers = max(1, 3 - int(_power_law_cached) - int(_value_trap_cached))
            # Manual executor lifecycle — see front block note; the join
            # deadline is enforced by _bounded_join.
            executor = ThreadPoolExecutor(max_workers=workers)
            f_scenario = _ctx_submit(executor, run_scenario_agent, state_copy_a)
            f_power = None if _power_law_cached else _ctx_submit(executor, run_power_law_agent, state_copy_b)
            f_trap  = None if _value_trap_cached else _ctx_submit(executor, run_value_trap_agent, state_copy_c)

            _p7_futures = [f_scenario]
            if not _value_trap_cached:
                _p7_futures.append(f_trap)
            if not _power_law_cached:
                _p7_futures.append(f_power)
            _p7_joined = _bounded_join(
                executor, _p7_futures, _PHASE7_TIMEOUT_S,
                "Phase 7 (scenario/power-law/value-trap)")

            _p7_it = iter(_p7_joined)
            state["data"]["scenario_analysis"] = next(_p7_it)["data"]["scenario_analysis"]
            if not _value_trap_cached:
                state["data"]["value_trap_analysis"] = next(_p7_it)["data"]["value_trap_analysis"]
            if not _power_law_cached:
                state["data"]["power_law_analysis"] = next(_p7_it)["data"]["power_law_analysis"]

        for ticker in tickers:
            scen = state["data"]["scenario_analysis"].get(ticker, {})
            pl = state["data"]["power_law_analysis"].get(ticker, {})
            trap = state["data"]["value_trap_analysis"].get(ticker, {})
            print(f"  {ticker}: EV upside {scen.get('upside_pct', 0):.1f}% | "
                  f"Power Law {pl.get('total_score', '?')}/10 | "
                  f"{trap.get('overall_verdict', '?')}")
        # ── Compute VGPM immediately after Phase 7 — all dependencies now satisfied:
        #    raw_financials (Phase 2) + insider_activity (Phase 2.5) +
        #    dcf_range (Phase 4.5) + scenario_analysis (Phase 7, just set above).
        #    Emitting here means the scorecard appears ~3 phases earlier than
        #    waiting for the full pipeline to return to analysis_service.
        with _timed("7_vgpm"):
            _vgpm: dict = {}
            _analyst_signals = state["data"].get("analyst_signals", {})
            # Sector lookup for sector-aware VGPM sub-score thresholds (added
            # 2026-05-21 to fix the post-Apr-25 B-band grade compression).
            # Pulls sector from state.data.sectors[ticker]; falls back to
            # _SECTOR_DEFAULT (Technology) when absent.
            _sectors_map = state["data"].get("sectors", {})
            for _t in tickers:
                try:
                    _dcf_t   = state["data"].get("dcf_range", {}).get(_t, {})
                    _scen_t  = state["data"].get("scenario_analysis", {}).get(_t, {})
                    # ROIC-proxy input (net_income/revenue for the latest fiscal
                    # year) sourced from the shared knowledge graph — FMP-direct,
                    # ~24h fresh + earnings-date-aware — instead of
                    # state["data"]["raw_financials"], which is LLM-reformatted
                    # and (on a routing-cache hit) can be up to 30 days stale.
                    # Falls back to the state value if the KG fetch fails for
                    # any reason, so this never blocks a run.
                    try:
                        from app.backend.services.knowledge_graph import get_kg_annual_line_items
                        _raw_fin = get_kg_annual_line_items(
                            _t, end_date, sector=_sectors_map.get(_t) if isinstance(_sectors_map, dict) else None,
                        )
                    except Exception:
                        _raw_fin = {}
                    if not _raw_fin:
                        _raw_fin = state["data"].get("raw_financials", {})
                    _dcf_cal = {
                        "margin_direction": _dcf_t.get("base", {}).get("margin_direction", "stable"),
                        "risk_flag":        _dcf_t.get("base", {}).get("risk_flag", ""),
                    }
                    _insider_raw = _analyst_signals.get("insider_activity_agent", {}).get(_t, {})
                    _insider_sum = _insider_raw.get("summary", "") if isinstance(_insider_raw, dict) else ""
                    _sector = _sectors_map.get(_t) if isinstance(_sectors_map, dict) else None
                    _vgpm[_t] = _compute_vgpm(
                        dcf_ticker=_dcf_t,
                        scen_ticker=_scen_t,
                        raw_financials=_raw_fin,
                        dcf_cal=_dcf_cal,
                        insider_summary=_insider_sum,
                        sector=_sector,
                    )
                except Exception as _e:
                    print(f"  [vgpm] Warning: could not compute VGPM for {_t}: {_e}")
            state["data"]["vgpm"] = _vgpm

        progress.update_status("phase7_complete", primary_ticker, "✓ Phase 7 complete",
                               partial_data={"scenario_analysis":  state["data"].get("scenario_analysis"),
                                             "power_law_analysis": state["data"].get("power_law_analysis"),
                                             "value_trap_analysis":state["data"].get("value_trap_analysis"),
                                             "vgpm":               _vgpm})

        # ----------------------------------------------------------------
        # PHASE 7.5 — Citation Auditor  [REMOVED]
        # ----------------------------------------------------------------
        # Citation auditor phase was removed from the pipeline.
        # Seed empty citation_audit dict so downstream consumers (PDF generator,
        # frontend, run archive) don't KeyError on missing field.
        state["data"].setdefault("citation_audit", {ticker: {} for ticker in tickers})

        # ----------------------------------------------------------------
        # PHASE 8 — Advanced Risk Manager (dual-layer)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[8/10] Advanced Risk Manager (dual-layer)")
        print('='*60)
        with _timed("8_risk_manager"):
            state = run_advanced_risk_manager(state)
        for ticker in tickers:
            risk = state["data"]["analyst_signals"].get("advanced_risk_manager", {}).get(ticker, {})
            flags = risk.get("level1_flags", []) + risk.get("sector_flags", [])
            print(f"  {ticker}: approved size {risk.get('approved_size_pct', 0):.1%}"
                  + (f" | flags: {'; '.join(flags)}" if flags else ""))
        progress.update_status("advanced_risk_manager", primary_ticker, "✓ Risk assessed",
                               partial_data={"analyst_signals": state["data"].get("analyst_signals")})

        # ----------------------------------------------------------------
        # PHASE 9 — Advanced Portfolio Manager
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[9/10] Conviction-Weighted Portfolio Manager")
        print('='*60)
        with _timed("9_portfolio_manager"):
            pm_result = run_advanced_portfolio_manager(state)
            state["messages"] = pm_result["messages"]
            state["data"].update(pm_result.get("data", {}))
        decisions = pm_result.get("decisions", {})
        for ticker, d in decisions.items():
            print(f"  {ticker}: {d.get('action')} | "
                  f"size {d.get('position_size_pct', 0):.2%} | "
                  f"target ${d.get('price_target', 0):.2f} | "
                  f"stop ${d.get('stop_loss', 0):.2f}")
        progress.update_status("portfolio_manager", primary_ticker, "✓ Decision complete",
                               partial_data={"decisions": decisions})

        # ── Checkpoint 4 — final calculation complete ──────────────────────────
        state["data"]["decisions"] = decisions   # expose to checkpoint serialiser
        if on_checkpoint:
            try:
                on_checkpoint(state, "final_calculation")
            except Exception as _ck_err:
                print(f"  [checkpoint] final_calculation save failed (non-fatal): {_ck_err}")

        # ----------------------------------------------------------------
        # PHASE 10 — Post-Trade Review (optional)
        # ----------------------------------------------------------------
        with _timed("10_post_trade_review"):
            if enable_post_trade_review:
                print(f"\n{'='*60}")
                print("[10/10] Post-Trade Review")
                print('='*60)
                state = run_post_trade_review(state)
                review = state["data"].get("post_trade_review", {})
                print(f"  Reviewed {review.get('reviewed', 0)} past trade(s)")
                for upd in review.get("weight_updates", []):
                    print(f"  Weight update: {upd}")
            else:
                print(f"\n[10/10] Post-Trade Review — SKIPPED (use --post-trade-review to enable)")

        # ----------------------------------------------------------------
        # Append to trade log for future Phase 10 reviews
        # ----------------------------------------------------------------
        _append_to_trade_log(state, decisions)

        # ----------------------------------------------------------------
        # V3.2 — Augment per-ticker metrics dicts with FMP-derived risk KPIs
        # (net_debt_to_ebitda, cash_runway_years, debt_to_ebitda).
        #
        # WHY HERE: extractor often misses balance-sheet KPIs (rarely quoted
        # verbatim in research narrative). FMP is authoritative anyway. By
        # augmenting BEFORE save_run() and BEFORE render_card_payloads_for_run,
        # the FMP-derived values:
        #   1. Become part of framework_metrics_all[ticker] dict
        #   2. Get persisted to web_runs JSON + archive ticker_signals
        #   3. Survive the run replay path (get_run_result reconstruction)
        #   4. Show up in the V3 audit_bridge Risk multiplier (no longer 1.0x)
        # ----------------------------------------------------------------
        with _timed("10_fmp_risk_augment"):
            try:
                from src.data.sector_kpi_framework import (
                    _augment_metrics_with_fmp_risk,
                    _augment_metrics_with_fmp_commodity,  # V3.1
                    is_legacy_profile,
                )
                _profile_names = state["data"].get("profile_names", {})
                _tickers = state["data"].get("tickers", []) or list(_profile_names.keys())
                for _t in _tickers:
                    _profile = _profile_names.get(_t) or state["data"].get("profile_name") or ""
                    if not _profile or is_legacy_profile(_profile):
                        continue
                    # Pick the right metrics dict for this profile (framework vs
                    # legacy sector-specific). Augment in-place if present.
                    for _state_key in ("framework_metrics_all",
                                       "insurance_metrics_all", "bank_metrics_all"):
                        _bucket = state["data"].get(_state_key) or {}
                        if _t in _bucket and isinstance(_bucket[_t], dict):
                            _bucket[_t] = _augment_metrics_with_fmp_risk(_t, _bucket[_t])
                            # V3.1 — also augment commodity prices for Resources/Energy
                            _bucket[_t] = _augment_metrics_with_fmp_commodity(_profile, _bucket[_t])
                            state["data"][_state_key] = _bucket
                    # If no metrics dict exists yet for this ticker, create one
                    # in framework_metrics_all (so render_card_payload finds it)
                    _fwm = state["data"].setdefault("framework_metrics_all", {})
                    if _t not in _fwm:
                        _aug = _augment_metrics_with_fmp_risk(_t, {})
                        _aug = _augment_metrics_with_fmp_commodity(_profile, _aug)
                        _fwm[_t] = _aug
            except Exception as _e:
                print(f"  [fmp_risk_augment] failed: {_e!r} — Risk/Commodity multiplier will be 1.0x")

        # ----------------------------------------------------------------
        # v3.4 — SEC EDGAR fallback for cet1_ratio (Banks).
        # FMP's /stable/key-metrics-ttm doesn't expose risk-weighted-assets
        # so CET1 can't be derived from balance-sheet alone. Banks ALWAYS
        # report it explicitly in 10-Q "Capital Ratios" sections — fetch it
        # directly from SEC EDGAR as a fallback when LLM extraction missed it.
        #
        # Runs only for bank profiles where cet1_ratio is missing or None.
        # Caches by ticker so re-runs in same session don't re-hit SEC.
        # ----------------------------------------------------------------
        with _timed("10_sec_cet1_fallback"):
            try:
                from src.data.sec_edgar import cet1_for_ticker
                _BANK_PROFILES = {
                    "Money Center Bank", "Money Center Bank (EU)",
                    "Regional Bank", "Super-Regional Bank",
                    "EM Bank", "EM Bank (Premium)",
                    "Bank / Lending Institution",
                    "Investment Bank", "Mortgage/GSE",
                }
                _profile_names_sec = state["data"].get("profile_names", {})
                _tickers_sec = state["data"].get("tickers", []) or list(_profile_names_sec.keys())
                for _t in _tickers_sec:
                    _profile = _profile_names_sec.get(_t) or state["data"].get("profile_name") or ""
                    if _profile not in _BANK_PROFILES:
                        continue
                    for _state_key in ("framework_metrics_all", "bank_metrics_all"):
                        _bucket = state["data"].get(_state_key) or {}
                        if _t not in _bucket or not isinstance(_bucket[_t], dict):
                            continue
                        if _bucket[_t].get("cet1_ratio") is not None:
                            continue  # LLM/FMP already provided it
                        _sec = cet1_for_ticker(_t)
                        if _sec and _sec.get("cet1_ratio"):
                            _bucket[_t]["cet1_ratio"] = _sec["cet1_ratio"]
                            print(f"  [sec_edgar] {_t} CET1={_sec['cet1_ratio']*100:.2f}% "
                                  f"from {_sec.get('filing_date','?')} 10-Q")
            except Exception as _e:
                print(f"  [sec_edgar_fallback] failed: {_e!r} — banks without LLM CET1 will use band default 1.0x")

        # ----------------------------------------------------------------
        # V4-β — Z-Score Engine: augment per-ticker metrics dicts with
        # peer-cohort z-scores. Runs AFTER FMP augmentation (so z-scores
        # cover augmented KPIs too) and BEFORE render_card_payloads_for_run
        # (so the audit_bridge picks up z-driven tier kickers).
        #
        # Cohort source: web_runs WHERE profile_name=<this profile>
        # within last 60 days. Self-excludes the current ticker.
        #
        # Sparse-cohort safety: per-KPI skip when cohort < 3 peers; the
        # multiplier path silently falls back to band-based tiers. So a
        # fresh deploy with empty archive degrades to v3.0 behaviour
        # (band-only) and progressively migrates to z-driven as runs
        # accumulate.
        # ----------------------------------------------------------------
        with _timed("10_zscore_engine"):
            try:
                from src.data.zscore_engine import augment_metrics_with_z_scores as _z_augment
                from src.data.sector_kpi_framework import is_legacy_profile as _is_legacy
                _profile_names_z = state["data"].get("profile_names", {})
                _tickers_z = state["data"].get("tickers", []) or list(_profile_names_z.keys())
                _z_summary: list[str] = []
                for _t in _tickers_z:
                    _profile = _profile_names_z.get(_t) or state["data"].get("profile_name") or ""
                    if not _profile or _is_legacy(_profile):
                        continue
                    for _state_key in ("framework_metrics_all",
                                       "insurance_metrics_all", "bank_metrics_all"):
                        _bucket = state["data"].get(_state_key) or {}
                        if _t in _bucket and isinstance(_bucket[_t], dict):
                            _bucket[_t] = _z_augment(_profile, _t, _bucket[_t])
                            state["data"][_state_key] = _bucket
                            _zs = _bucket[_t].get("_z_scores") or {}
                            if _zs:
                                _z_summary.append(f"{_t}({_profile}):{len(_zs)}KPIs")
                if _z_summary:
                    print(f"  [zscore_engine] {' | '.join(_z_summary)}")
                else:
                    print(f"  [zscore_engine] no peer cohorts found (fresh archive or sparse profiles)")
            except Exception as _e:
                print(f"  [zscore_engine] failed: {_e!r} — composite will use band-based tiers")

        # ----------------------------------------------------------------
        # Sector valuation card payload — per-ticker dict consumed by the
        # frontend `SectorValuationCard` component. Built from the
        # SECTOR_KPI_FRAMEWORK + already-extracted metric state. Legacy
        # sub-profiles (SaaS / REIT / Biopharma) return None and keep
        # their existing bespoke cards.
        #
        # CRITICAL: must be written to state BEFORE save_run() so the
        # archive picks it up, AND added to the run_advanced_pipeline
        # return dict below so web_runs JSON gets it (per 1ac5490 fix).
        # ----------------------------------------------------------------
        with _timed("10_sector_card_render"):
            try:
                from src.data.sector_kpi_framework import render_card_payloads_for_run
                _sector_card = render_card_payloads_for_run(state) or {}
            except Exception as _e:
                print(f"  [sector_card] render failed: {_e!r} — frontend will hide card")
                _sector_card = {}
            state["data"]["sector_card"] = _sector_card

        # SECOND mid-run emit — the now-fully-correct card (includes any KPIs
        # that only landed after the DCF emit, e.g. z-score composite inputs).
        # Streams it BEFORE the Card-QA phase (which can run up to ~$0.50/ticker
        # of LLM time) + save_run, so the user sees the final card without
        # waiting for the run to fully complete.
        if _sector_card:
            try:
                progress.update_status("sector_card", primary_ticker, "✓ Sector card complete",
                                       partial_data={"sector_card": _sector_card})
            except Exception as _sc_emit_e:
                print(f"  [sector_card] final emit failed (non-fatal): {_sc_emit_e!r}")

        # ----------------------------------------------------------------
        # PHASE 10.5 — Card QA Agent (Layer A self-healing audit)
        #
        # Walks each ticker through Meta-Check (10.5a) + per-card audits
        # (10.5b). For missing mandatory fields, calls the LLM judge; for
        # EXTRACTOR_DROPPED verdicts, hint-driven re-extraction can mutate
        # state[field_path] in place. All work runs under a $0.50 per-
        # ticker budget cap with a heartbeat inside the LLM wrapper.
        #
        # Wrapped in try/except so any QA failure is logged + persisted
        # but never blocks downstream save_run + return — the pipeline
        # must always finish producing a usable run, even when self-
        # healing breaks.
        #
        # See plan: C:/Users/ethan/.claude/plans/mighty-gliding-graham.md
        # ----------------------------------------------------------------
        with _timed("10_5_card_qa"):
            try:
                from src.agents.audit.card_qa_agent import (  # type: ignore
                    compute_card_qa_hash,
                    run_card_qa_agent,
                    should_reuse_card_qa,
                )
                # R5 delta check: skip the QA LLM pass when this run's card +
                # deep-research inputs are byte-identical to a recent run that
                # produced a clean audit of the current QA_VERSION.
                _qa_delta_on = os.getenv("CARD_QA_DELTA", "true").strip().lower() != "false"
                _qa_audits: dict[str, dict] = {}
                _card_hashes: dict[str, str] = {}
                _dr_text = state["data"].get("deep_research") or ""
                for _qa_ticker in tickers:
                    if not _qa_ticker:
                        continue
                    _card_hashes[_qa_ticker] = compute_card_qa_hash(
                        (_sector_card or {}).get(_qa_ticker), _dr_text,
                    )
                    _reused_audit: dict | None = None
                    if _qa_delta_on:
                        try:
                            _prior = get_phase_cache(_qa_ticker, max_age_days=_CARD_QA_REUSE_DAYS)
                            if _prior is not None and should_reuse_card_qa(
                                _prior.get("card_qa_audit"),
                                _prior.get("sector_card_hash"),
                                _card_hashes[_qa_ticker],
                            ):
                                _reused_audit = dict(_prior["card_qa_audit"])
                        except Exception as _delta_e:
                            print(f"  [card_qa] delta check failed for {_qa_ticker}: "
                                  f"{_delta_e!r} — running full QA")
                    if _reused_audit is not None:
                        _reused_audit["qa_reused"] = True
                        _qa_audits[_qa_ticker] = _reused_audit
                        print(f"  [card_qa] {_qa_ticker}: inputs unchanged — reused "
                              f"prior clean audit (R5 delta)")
                        continue
                    _qa_audits[_qa_ticker] = run_card_qa_agent(state, _qa_ticker)
                state["data"]["card_qa_audit"] = _qa_audits
                # Persisted by save_run → ticker_signals.sector_card_hash /
                # card_qa_json so the next run can delta-check against it.
                state["data"]["sector_card_hash"] = _card_hashes
            except Exception as _qa_exc:
                import traceback as _qa_tb
                _qa_trace = "".join(
                    _qa_tb.format_exception(type(_qa_exc), _qa_exc, _qa_exc.__traceback__)
                )
                print(f"  [card_qa_agent] FAILED — pipeline continues: {type(_qa_exc).__name__}: {_qa_exc!r}")
                state["data"]["card_qa_engine_error"] = {
                    "exception_type": type(_qa_exc).__name__,
                    "message":        str(_qa_exc)[:500],
                    "traceback":      _qa_trace,
                    "primary_ticker": primary_ticker,
                    "tickers":        list(tickers),
                }
                state["data"]["card_qa_audit"] = {}

        # ----------------------------------------------------------------
        # Episodic run archive (dual-mode: SQLite local / Postgres when
        # DATABASE_URL is set — see src/data/db.py)
        # ----------------------------------------------------------------
        with _timed("11_save_archive"):
            _archive_run_id = save_run(state, decisions)
            summary = archive_summary()
        print(f"  [archive] {summary['total_runs']} run(s) stored | "
              f"{summary['scored']} scored | {summary['pending']} pending")

        # ── One-line timing summary (Railway-greppable) ─────────────────────
        if _phase_durations:
            _total_s = sum(e["duration_s"] for e in _phase_durations)
            _slowest = max(_phase_durations, key=lambda e: e["duration_s"])
            print(f"  [timing] pipeline total: {_total_s:.1f}s across "
                  f"{len(_phase_durations)} phases | slowest: "
                  f"{_slowest['phase']} ({_slowest['duration_s']:.1f}s)")

        print(f"\n{'='*60}")
        print("Advanced pipeline complete.")
        print('='*60)

        # Stamp each non-empty per-ticker dcf_range payload with the engine
        # cache version so the Phase 4.5 gate on future runs can distinguish
        # entries produced by this engine from stale ones. Empty {} placeholders
        # stay empty — the _all_cached truthy guard relies on them remaining
        # falsy so a crashed engine never masquerades as a valid cache hit.
        _dcf_range_out = {
            _t: ({**_v, "_engine_cache_version": _DCF_CACHE_VERSION}
                 if isinstance(_v, dict) and _v else _v)
            for _t, _v in (state["data"].get("dcf_range") or {}).items()
        }

        return {
            "decisions":          decisions,
            "analyst_signals":    state["data"]["analyst_signals"],
            "macro_regime":       state["data"].get("macro_regime"),
            "sector":             state["data"].get("sector"),
            "industry_brief":     state["data"].get("industry_brief"),
            "deep_research":           state["data"].get("deep_research"),
            "deep_research_annotated": state["data"].get("deep_research_annotated"),
            "scenario_analysis":  state["data"].get("scenario_analysis"),
            "power_law_analysis": state["data"].get("power_law_analysis"),
            "value_trap_analysis":state["data"].get("value_trap_analysis"),
            "post_trade_review":  state["data"].get("post_trade_review"),
            # Phase 2.5 intelligence — included so downstream consumers
            # (alerts, PDF) can access without re-fetching from state.
            "insider_activity":   state["data"].get("insider_activity", {}),
            "analyst_revisions":  state["data"].get("analyst_revisions", {}),
            "news_sentiment":     state["data"].get("news_sentiment", {}),
            "short_interest":     state["data"].get("short_interest", {}),
            "earnings_quality":   state["data"].get("earnings_quality", {}),
            # Raw financial history (strategic router Phase 2) + DCF outputs
            # (stamped with _DCF_CACHE_VERSION above for the Phase 4.5 gate).
            "raw_financials":     state["data"].get("raw_financials", {}),
            # Three-statement view (data_router) + the stylist's audit line.
            # Both existed in state and were absent from the archived run
            # until they were listed here — the same serialization-contract
            # bug the dcf_skip_reasons comment below describes.
            "financial_statements": state["data"].get("financial_statements", {}),
            "style_audit":        state["data"].get("style_audit", {}),
            "dcf_range":          _dcf_range_out,
            # Per-ticker reason a DCF entry came back {} (set by dcf_agent.py's
            # early-exit branches) + the exception when the whole engine
            # crashed (set by the try/except around run_dcf_agent below).
            # Without these two keys in this allowlist, an empty
            # Valuation Methodology panel is silent and undiagnosable from
            # the archived run — the reason existed in state but never
            # reached web_runs.full_result_json. Same serialization-contract
            # bug class as saas_metrics/framework_metrics_all above.
            "dcf_skip_reasons":   state["data"].get("dcf_skip_reasons", {}),
            "dcf_engine_error":   state["data"].get("dcf_engine_error"),
            # Phase 4.6 — peer comparison table data
            "peer_comparison":    state["data"].get("peer_comparison", {}),
            # Phase 4.7 — 12-month price history for sparkline
            "price_history":      state["data"].get("price_history", {}),
            # Phase 7.5 — BU Analyst, Financial Editor, Citation Auditor
            "bu_analysis":        {},
            "editor_review":      {},
            "citation_audit":     state["data"].get("citation_audit", {}),
            "consistency_flags":  state["data"].get("consistency_flags", {}),
            # C3 — extractor exceptions / still-empty outputs per ticker
            "extractor_failures": state["data"].get("extractor_failures", {}),
            # C6 — deterministic research↔books divergences per ticker
            # (separate from consistency_flags: PM overwrites that key with a
            # string in phase 9; these dicts must survive to persistence)
            "research_financial_divergences": state["data"].get("research_financial_divergences", {}),
            # C4 — primary ticker below the live-search floor
            "research_degraded":  state["data"].get("research_degraded", False),
            # Citation registry + footnotes (deep_research → specialist → PDF)
            "citation_registry":  state["data"].get("citation_registry", []),
            "industry_footnotes": state["data"].get("industry_footnotes", []),
            # VGPM scorecard — computed after Phase 7, before Phase 7.5+
            "vgpm":               state["data"].get("vgpm", {}),
            # ── Phase 3 extractor outputs ──────────────────────────────────────
            # CRITICAL: these must be included or they're lost between pipeline
            # return and web_runs persistence, which blanks the Valuation /
            # Commentary / KPI panels on the frontend. Bug discovered 2026-04-25
            # when NET's stored full_result_json had no saas_metrics despite
            # Railway logs clearly showing 8/8 fields extracted. Root cause:
            # this return dict is the serialization contract — state["data"]
            # values not listed here don't make it to the DB.
            "saas_metrics":           state["data"].get("saas_metrics", {}),
            "bank_metrics":           state["data"].get("bank_metrics", {}),
            "reit_metrics":           state["data"].get("reit_metrics", {}),
            "pipeline_assets":        state["data"].get("pipeline_assets", {}),
            "dcf_calibration":        state["data"].get("dcf_calibration", {}),
            "segment_scenarios":      state["data"].get("segment_scenarios", {}),
            # ── v3.5: Raw V3 framework metric DICTS — persistence fix ──────────
            # These are the per-ticker structured KPI dicts (e.g.
            # framework_metrics_all["AAPL"] = {"revenue_growth_pct": 0.064,
            # "operating_margin_pct": 0.324, ...}). The RENDERED `sector_card`
            # above already has the multipliers computed, BUT:
            #   - Z-engine `fetch_peer_cohort` reads framework_metrics_all from
            #     past web_runs to build peer cohorts. Without persistence the
            #     cohort is always empty → z-tier kickers never fire across runs.
            #   - Admin re-render after schema fix needs raw KPIs to recompute.
            #   - Replay path (analysis_service.reconstruct) needs these to
            #     rebuild the audit_bridge with current code.
            # Bug discovered 2026-04-26: these dicts existed in state but were
            # never persisted to web_runs.full_result_json.
            "framework_metrics_all":  state["data"].get("framework_metrics_all", {}),
            "insurance_metrics_all":  state["data"].get("insurance_metrics_all", {}),
            "bank_metrics_all":       state["data"].get("bank_metrics_all", {}),
            "saas_metrics_all":       state["data"].get("saas_metrics_all", {}),
            "reit_metrics_all":       state["data"].get("reit_metrics_all", {}),
            "pipeline_assets_all":    state["data"].get("pipeline_assets_all", {}),
            # ── Phase 2 routing + sector/profile classification ─────────────
            # Similarly needed: without these, admin panels can't filter/group
            # runs by profile, and the frontend's TechValuationPanel routing
            # falls back to the classifyTechSubtype ticker-table (works for
            # known tickers but breaks for uncovered ones).
            "profile_name":           state["data"].get("profile_name", ""),
            "profile_names":          state["data"].get("profile_names", {}),
            "sectors":                state["data"].get("sectors", {}),
            # ── Phase 10.5: Card QA Agent audit (Layer A self-healing) ──────
            # Per-ticker audit dicts with Meta-Check + card-level findings +
            # any auto-remediations + human_review_flags. Empty dict on QA
            # failure (card_qa_engine_error captured separately).
            "card_qa_audit":          state["data"].get("card_qa_audit", {}),
            "card_qa_engine_error":   state["data"].get("card_qa_engine_error"),
            # ── Phase 3 deep research artifacts ─────────────────────────────
            # deep_research_sections is the parsed Section 2A-2F dict that
            # feeds the frontend commentary cards (NRR Trajectory, Path to
            # Profitability, AI Capex ROI, etc.). Without it, commentary
            # cards silently hide even when Qwen produced rich 2F content.
            "deep_research_sections": state["data"].get("deep_research_sections", {}),
            "research_tier":          state["data"].get("research_tier"),
            # ── M1 recency loop: prior report recap + freshness delta ───────
            # Per-ticker dicts ({ticker: {...}}) like every other data.* key.
            # Must be listed here or they never reach web_runs.full_result_json
            # and the frontend "Since last report" card stays hidden.
            "prior_recap":            state["data"].get("prior_recap", {}),
            "freshness_delta":        state["data"].get("freshness_delta", {}),
            # Sector-specific valuation card payload (Option B render). Must
            # be in this return dict — see commit 1ac5490 / sector_kpi_framework
            # render_card_payload docstring for why state-only writes get lost.
            "sector_card":            state["data"].get("sector_card", {}),
            # ── Workstream A: per-phase wall-clock timings ─────────────────────
            # List of {phase, started_at, finished_at, duration_s}. Serialised
            # into web_runs.full_result_json AND the dedicated
            # web_runs.phase_durations column by analysis_service._save_web_run.
            # Additive key — old replay payloads simply lack it.
            "phase_durations":    _phase_durations,
            # Internal — lets analysis_service link web_runs to the archive row
            # without calling save_run() a second time (which would create a duplicate).
            "_archive_run_id":    _archive_run_id,
            # Pass tickers list through so analysis_service can reconstruct state
            "tickers":            state["data"].get("tickers", []),
        }

    finally:
        # B2: on any exit path, cancel still-queued shadow work (running
        # futures finish on their deepcopies — bounded and harmless).
        if _bg_peer_exec is not None:
            try:
                _bg_peer_exec.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        progress.stop()


def _run_intelligence_agents_parallel(state: AgentState) -> AgentState:
    """
    Run Insider Activity, Analyst Revision, News Sentiment, Earnings Quality,
    and Short Interest agents concurrently (Phase 2.5).  Each operates on a
    deepcopy so they cannot collide; results are merged back under their
    respective state["data"] keys.
    """
    import copy

    state_ia = copy.deepcopy(state)
    state_ar = copy.deepcopy(state)
    state_ns = copy.deepcopy(state)
    state_eq = copy.deepcopy(state)
    state_si = copy.deepcopy(state)

    with ThreadPoolExecutor(max_workers=5) as executor:
        f_ia = _ctx_submit(executor, run_insider_activity_agent, state_ia)
        f_ar = _ctx_submit(executor, run_analyst_revision_agent, state_ar)
        f_ns = _ctx_submit(executor, run_news_sentiment_agent, state_ns)
        f_eq = _ctx_submit(executor, run_earnings_quality_agent, state_eq)
        f_si = _ctx_submit(executor, run_short_interest_agent, state_si)

    try:
        state["data"]["insider_activity"] = f_ia.result()["data"]["insider_activity"]
    except Exception as e:
        print(f"  Warning: InsiderActivityAgent failed: {e}")
        state["data"]["insider_activity"] = {}

    try:
        state["data"]["analyst_revisions"] = f_ar.result()["data"]["analyst_revisions"]
    except Exception as e:
        print(f"  Warning: AnalystRevisionAgent failed: {e}")
        state["data"]["analyst_revisions"] = {}

    try:
        state["data"]["news_sentiment"] = f_ns.result()["data"]["news_sentiment"]
    except Exception as e:
        print(f"  Warning: NewsSentimentAgent failed: {e}")
        state["data"]["news_sentiment"] = {}

    try:
        state["data"]["earnings_quality"] = f_eq.result()["data"]["earnings_quality"]
    except Exception as e:
        print(f"  Warning: EarningsQualityAgent failed: {e}")
        state["data"]["earnings_quality"] = {}

    try:
        state["data"]["short_interest"] = f_si.result()["data"]["short_interest"]
    except Exception as e:
        print(f"  Warning: ShortInterestAgent failed: {e}")
        state["data"]["short_interest"] = {}

    return state


def _merge_front_block(router_state: AgentState, macro_state: AgentState,
                       intel_state: AgentState, edgar_state: AgentState,
                       phase_durations: list) -> AgentState:
    """B1 merge — combine the four front-block phase results into one state.

    The router's copy is the base (it carries the most downstream keys); the
    other three phases' DISJOINT key sets are grafted on top (disjointness
    verified by grep — see the FRONT BLOCK comment in run_advanced_pipeline).
    The shared phase_durations list is re-mounted on the surviving state so
    later phases keep appending to the one serialised list.
    """
    state = router_state
    state["data"]["phase_durations"] = phase_durations
    for _k in ("macro_regime", "position_size_cap"):
        if _k in macro_state["data"]:
            state["data"][_k] = macro_state["data"][_k]
    for _k in ("insider_activity", "analyst_revisions", "news_sentiment",
               "earnings_quality", "short_interest", "analyst_signals"):
        if _k in intel_state["data"]:
            state["data"][_k] = intel_state["data"][_k]
    state["data"]["edgar_filing_refs"] = edgar_state["data"].get("edgar_filing_refs", {})
    return state


def _fetch_price_history(tickers: list[str], end_date: str, api_key: str | None) -> dict[str, list]:
    """B2 — 12-month price history (sparkline) extracted for background execution.

    Was inline in phase 4.7; identical behaviour — per-ticker graceful
    degradation to [] on any fetch failure.
    """
    from datetime import datetime as _dt, timedelta as _td
    from src.tools.api import get_prices as _get_prices

    _ph_end   = end_date
    _ph_start = (_dt.strptime(end_date, "%Y-%m-%d") - _td(days=365)).strftime("%Y-%m-%d")
    price_history_all: dict[str, list] = {}
    for ticker in tickers:
        try:
            _prices = _get_prices(ticker, _ph_start, _ph_end, api_key=api_key)
            price_history_all[ticker] = [
                {"date": p.time, "close": p.close} for p in (_prices or [])
            ]
            print(f"  {ticker}: {len(price_history_all[ticker])} price points fetched")
        except Exception:
            price_history_all[ticker] = []
            print(f"  {ticker}: price history unavailable")
    return price_history_all


def _append_to_trade_log(state: AgentState, decisions: dict) -> None:
    """Append current run decisions to trade_log.json for future Phase 10 scoring."""
    try:
        # Store a lean version of analyst signals (skip risk manager data)
        skip_agents = {"risk_management_agent", "advanced_risk_manager"}
        lean_signals: dict = {}
        for k, v in state["data"]["analyst_signals"].items():
            if k in skip_agents or not isinstance(v, dict):
                continue
            lean_signals[k] = {}
            for ticker, sig in v.items():
                if isinstance(sig, dict):
                    lean_signals[k][ticker] = {
                        "signal": sig.get("signal"),
                        "conviction": sig.get("conviction"),
                    }

        entry = {
            "run_date": datetime.now().strftime("%Y-%m-%d"),
            "date": state["data"]["end_date"],
            "tickers": state["data"]["tickers"],
            "decisions": decisions,
            "analyst_signals": lean_signals,
        }

        def _append(existing):
            existing.append(entry)
            return existing

        from src.utils.json_state import update_json_locked
        update_json_locked(TRADE_LOG_PATH, _append, default=[])

    except Exception as e:
        print(f"  Warning: could not write trade log: {e}")
