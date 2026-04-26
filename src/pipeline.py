"""
Advanced 10-Phase Pipeline Orchestrator

Sequence:
  [1] Macro Regime Classifier      — sequential, sets agent weights + position cap
  [2] Strategic Routing Agent      — sequential, sector + raw financials scratchpad
  [3] Industry Specialist Agent    — sequential, shared intelligence brief
  [4] Data Router                  — sequential, no LLM, pre-fetches per-agent data
  [5] Investor Agents (parallel)   — 12 threads, CoT signals with conviction + cot_log
  [6] Debate Round (conditional)   — sequential, only if ≥3 BUY and ≥3 SELL on same ticker
  [7] Scenario + PowerLaw + Trap   — 3 parallel threads, all read same state
  [8] Advanced Risk Manager        — sequential, dual-layer quality filter + position caps
  [9] Advanced Portfolio Manager   — sequential, conviction-weighted formula + LLM rationale
  [10] Post-Trade Review (optional)— sequential, scores prior calls, updates weights on disk

Entry point called from main.py when --pipeline advanced is passed.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from langchain_core.messages import HumanMessage

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
from src.agents.industry.specialist import run_industry_specialist
from src.agents.industry.data_router import run_data_router
from src.agents.analysis.dcf_agent import run_dcf_agent
from src.agents.analysis.peer_comparison import run_peer_comparison
from src.agents.analysis.debate_round import run_debate_round, should_trigger_debate
from src.agents.analysis.scenario_agent import run_scenario_agent
from src.agents.analysis.power_law_agent import run_power_law_agent
from src.agents.analysis.value_trap_agent import run_value_trap_agent
# Citation auditor removed from pipeline — see commit notes.
# citation_audit dict is still seeded empty for backward compat with PDF/frontend.
from src.agents.risk_manager import run_advanced_risk_manager, risk_management_agent
from src.agents.portfolio_manager import run_advanced_portfolio_manager
from src.agents.portfolio.post_trade_review import run_post_trade_review
from src.memory.run_archive import save_run, archive_summary, get_phase_cache
from src.pipeline_investors import run_advanced_investor, INVESTOR_PERSONAS
from src.utils.pdf_report import _compute_vgpm

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TRADE_LOG_PATH = os.path.join(DATA_DIR, "trade_log.json")


def run_advanced_pipeline(
    tickers: list[str],
    start_date: str,
    end_date: str,
    portfolio: dict,
    selected_agents: list[str] | None = None,
    model_name: str = "claude-sonnet-4-6",
    model_provider: str = "Anthropic",
    show_reasoning: bool = False,
    enable_post_trade_review: bool = False,
    management_guidance: dict[str, dict] | None = None,
    on_checkpoint: "callable | None" = None,
) -> dict:
    """
    Run the full 10-phase advanced pipeline.

    on_checkpoint, if provided, is called after Phase 3 (deep research) and
    Phase 4 (industry brief) so the caller can persist partial results early.
    Signature: on_checkpoint(state: AgentState, checkpoint_name: str) -> None
    Returns a result dict compatible with print_trading_output().
    """
    progress.start()

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

        active_agents = selected_agents if selected_agents else list(INVESTOR_PERSONAS.keys())
        primary_ticker = tickers[0] if tickers else ""
        print(f"  Active investor agents ({len(active_agents)}): {', '.join(active_agents)}")

        # ----------------------------------------------------------------
        # PHASE 1 — Macro Regime Classifier
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[1/10] Macro Regime Classifier")
        print('='*60)
        state = run_macro_regime_classifier(state)
        regime = state["data"].get("macro_regime", {})
        print(f"  Regime: {regime.get('risk_appetite')} | "
              f"{regime.get('rate_direction')} rates | "
              f"{regime.get('volatility_regime')} vol")
        progress.update_status("macro_regime_classifier", primary_ticker, "✓ Regime identified",
                               partial_data={"macro_regime": state["data"].get("macro_regime")})

        # ----------------------------------------------------------------
        # PHASE 2 — Strategic Routing Agent
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[2/10] Strategic Routing Agent")
        print('='*60)
        state = run_strategic_router(state)
        print(f"  Sector: {state['data'].get('sector')}")
        progress.update_status("strategic_router", primary_ticker, "✓ Routing complete",
                               partial_data={"routing_decision": state["data"].get("routing_decision"),
                                             "raw_financials": state["data"].get("raw_financials")})

        # ----------------------------------------------------------------
        # PHASE 2.5 — Intelligence Agents (deterministic, parallel)
        # Insider Activity + Analyst Revision + News Sentiment +
        # Earnings Quality run concurrently on deepcopies, then results
        # are merged back.  No LLM required — pure data signals.
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[2.5/10] Intelligence Agents (Insider · Revision · Sentiment · EarningsQuality · ShortInterest, parallel)")
        print('='*60)
        state = _run_intelligence_agents_parallel(state)
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

        # ----------------------------------------------------------------
        # PHASE 2.7 — EDGAR_HKEX Resolver (no LLM, ~0.5 s per ticker)
        # US tickers : resolves SEC EDGAR accession number + filing URL
        # HK tickers : resolves HKEXnews Annual Report PDF URL
        # Enables deep research to cite financial data to the primary source
        # instead of the vague "Financial Data API" attribution.
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[2.7/10] EDGAR_HKEX Resolver")
        print('='*60)
        state = run_edgar_hkex_resolver(state)
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
        # ARCHIVE CACHE — load recent phase outputs so expensive phases
        # (Industry Brief, DCF, Power Law, Citation) can be skipped when
        # fresh-enough data already exists in the archive.
        # Deep Research has its own internal caching (data_router/deep_research.py)
        # and is intentionally NOT bypassed here.
        # ----------------------------------------------------------------
        _phase_cache: dict[str, dict | None] = {}
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

        def _all_cached(key: str, age_days: float = 7.0) -> bool:
            """True if every ticker has a fresh-enough non-None cache entry for key."""
            return all(
                _phase_cache.get(t) is not None
                and _phase_cache[t].get(key) is not None  # type: ignore[union-attr]
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
        # PHASE 3 — Deep Research (Claude+Tavily) & Data Router
        # Deep research runs first so the Industry Specialist can use the report.
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[3/10] Deep Research (Claude+Tavily) & Data Router")
        print('='*60)
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
        # PHASE 4.5 — DCF Engine (deterministic, no LLM)
        # Cache: reuse dcf_range if all tickers have a <3-day cached run.
        # (Shorter window — financials can move fast.)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[4.5/10] DCF Engine (multi-method, macro-aware)")
        print('='*60)
        if _all_cached("dcf_range", age_days=60.0):
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

        # ----------------------------------------------------------------
        # PHASE 4.6 — Peer Comparison Engine (deterministic, no LLM)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[4.6/10] Peer Comparison Engine")
        print('='*60)
        state = run_peer_comparison(state)
        peer_comp = state["data"].get("peer_comparison", {})
        for ticker in tickers:
            peers_found = list(peer_comp.get(ticker, {}).keys())
            print(f"  {ticker}: {len(peers_found)} tickers fetched "
                  f"({', '.join(peers_found[:5])})")

        # ----------------------------------------------------------------
        # PHASE 4.7 — Price History (12-month, for sparkline)
        # ----------------------------------------------------------------
        from datetime import datetime as _dt, timedelta as _td
        from src.tools.api import get_prices as _get_prices
        import os as _os
        _ph_api_key = (
            state["data"].get("api_key")
            or _os.environ.get("FINANCIAL_DATASETS_API_KEY")
        )
        _ph_end   = end_date
        _ph_start = (_dt.strptime(end_date, "%Y-%m-%d") - _td(days=365)).strftime("%Y-%m-%d")
        price_history_all: dict[str, list] = {}
        for ticker in tickers:
            try:
                _prices = _get_prices(ticker, _ph_start, _ph_end, api_key=_ph_api_key)
                price_history_all[ticker] = [
                    {"date": p.time, "close": p.close} for p in (_prices or [])
                ]
                print(f"  {ticker}: {len(price_history_all[ticker])} price points fetched")
            except Exception:
                price_history_all[ticker] = []
                print(f"  {ticker}: price history unavailable")
        state["data"]["price_history"] = price_history_all

        # ----------------------------------------------------------------
        # PHASE 5 — Investor Agents (parallel)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print(f"[5/10] Investor Agents ({len(active_agents)} agents, parallel)")
        print('='*60)
        state = _run_investor_agents_parallel(state, active_agents)

        # Signal summary
        for ticker in tickers:
            buy_c = sum(
                1 for k, v in state["data"]["analyst_signals"].items()
                if isinstance(v, dict) and ticker in v and v[ticker].get("signal") == "BUY"
            )
            sell_c = sum(
                1 for k, v in state["data"]["analyst_signals"].items()
                if isinstance(v, dict) and ticker in v and v[ticker].get("signal") in ("SELL", "SHORT")
            )
            hold_c = sum(
                1 for k, v in state["data"]["analyst_signals"].items()
                if isinstance(v, dict) and ticker in v and v[ticker].get("signal") == "HOLD"
            )
            print(f"  {ticker}: {buy_c} BUY | {sell_c} SELL/SHORT | {hold_c} HOLD")
        progress.update_status("investor_agents", primary_ticker, "✓ Signals complete",
                               partial_data={"analyst_signals": state["data"].get("analyst_signals")})

        # ── Checkpoint 3 — investor signals complete ───────────────────────────
        if on_checkpoint:
            try:
                on_checkpoint(state, "investor_signals")
            except Exception as _ck_err:
                print(f"  [checkpoint] investor_signals save failed (non-fatal): {_ck_err}")

        # ----------------------------------------------------------------
        # PHASE 6 — Debate Round (conditional)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[6/10] Debate Round")
        print('='*60)
        if should_trigger_debate(state["data"]["analyst_signals"], tickers):
            print("  TRIGGERED — genuine conflict detected")
            state = run_debate_round(state)
            for ticker in tickers:
                dr = state["data"].get("debate_result", {}).get(ticker)
                if dr:
                    print(f"  {ticker} adjudicated: {dr.get('adjudicated_signal')} "
                          f"conviction {dr.get('adjudicated_conviction')}/10")
        else:
            print("  SKIPPED — no strong conflict (< 3 BUY and 3 SELL on same ticker)")
            state["data"]["debate_result"] = {}
        progress.update_status("debate_round", primary_ticker, "✓ Debate complete",
                               partial_data={"debate_result": state["data"].get("debate_result")})

        # ----------------------------------------------------------------
        # PHASE 7 — Scenario + Power Law + Value Trap (parallel)
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("[7/10] Phase 3 Analysis: Scenario + Power Law + Value Trap (parallel)")
        print('='*60)

        # Run Phase 8 (basic risk manager) first so scenario has current prices
        # risk_management_agent returns a partial state (LangGraph pattern) — merge, don't replace
        _partial = risk_management_agent(state)
        state["messages"] = _partial.get("messages", state["messages"])
        state["data"].update(_partial.get("data", {}))

        import copy

        # Power law: skip LLM if cached within 60 days (structural moat is sticky)
        _power_law_cached = _all_cached("power_law", age_days=60.0)
        # Value trap: skip LLM if cached within 30 days (financial quality signals are semi-stable)
        _value_trap_cached = _all_cached("value_trap", age_days=30.0)
        # (cached data was already emitted to frontend before Phase 3 — no re-emission needed here)

        state_copy_a = copy.deepcopy(state)
        state_copy_b = copy.deepcopy(state)
        state_copy_c = copy.deepcopy(state)

        workers = max(1, 3 - int(_power_law_cached) - int(_value_trap_cached))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            f_scenario = executor.submit(run_scenario_agent, state_copy_a)
            f_power = None if _power_law_cached else executor.submit(run_power_law_agent, state_copy_b)
            f_trap  = None if _value_trap_cached else executor.submit(run_value_trap_agent, state_copy_c)

        state["data"]["scenario_analysis"] = f_scenario.result()["data"]["scenario_analysis"]

        if not _value_trap_cached:
            state["data"]["value_trap_analysis"] = f_trap.result()["data"]["value_trap_analysis"]  # type: ignore[union-attr]

        if not _power_law_cached:
            state["data"]["power_law_analysis"] = f_power.result()["data"]["power_law_analysis"]  # type: ignore[union-attr]

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
        _vgpm: dict = {}
        _analyst_signals = state["data"].get("analyst_signals", {})
        for _t in tickers:
            try:
                _dcf_t   = state["data"].get("dcf_range", {}).get(_t, {})
                _scen_t  = state["data"].get("scenario_analysis", {}).get(_t, {})
                _raw_fin = state["data"].get("raw_financials", {})
                _dcf_cal = {
                    "margin_direction": _dcf_t.get("base", {}).get("margin_direction", "stable"),
                    "risk_flag":        _dcf_t.get("base", {}).get("risk_flag", ""),
                }
                _insider_raw = _analyst_signals.get("insider_activity_agent", {}).get(_t, {})
                _insider_sum = _insider_raw.get("summary", "") if isinstance(_insider_raw, dict) else ""
                _vgpm[_t] = _compute_vgpm(
                    dcf_ticker=_dcf_t,
                    scen_ticker=_scen_t,
                    raw_financials=_raw_fin,
                    dcf_cal=_dcf_cal,
                    insider_summary=_insider_sum,
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
        try:
            from src.data.sector_kpi_framework import render_card_payloads_for_run
            _sector_card = render_card_payloads_for_run(state) or {}
        except Exception as _e:
            print(f"  [sector_card] render failed: {_e!r} — frontend will hide card")
            _sector_card = {}
        state["data"]["sector_card"] = _sector_card

        # ----------------------------------------------------------------
        # Episodic run archive (SQLite — src/data/run_archive.db)
        # ----------------------------------------------------------------
        _archive_run_id = save_run(state, decisions)
        summary = archive_summary()
        print(f"  [archive] {summary['total_runs']} run(s) stored | "
              f"{summary['scored']} scored | {summary['pending']} pending")

        print(f"\n{'='*60}")
        print("Advanced pipeline complete.")
        print('='*60)

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
            "debate_result":      state["data"].get("debate_result"),
            "post_trade_review":  state["data"].get("post_trade_review"),
            # Phase 2.5 intelligence — included so downstream consumers
            # (alerts, PDF) can access without re-fetching from state.
            "insider_activity":   state["data"].get("insider_activity", {}),
            "analyst_revisions":  state["data"].get("analyst_revisions", {}),
            "news_sentiment":     state["data"].get("news_sentiment", {}),
            "short_interest":     state["data"].get("short_interest", {}),
            "earnings_quality":   state["data"].get("earnings_quality", {}),
            # Raw financial history (strategic router Phase 2) + DCF outputs
            "raw_financials":     state["data"].get("raw_financials", {}),
            "dcf_range":          state["data"].get("dcf_range", {}),
            # Phase 4.6 — peer comparison table data
            "peer_comparison":    state["data"].get("peer_comparison", {}),
            # Phase 4.7 — 12-month price history for sparkline
            "price_history":      state["data"].get("price_history", {}),
            # Phase 7.5 — BU Analyst, Financial Editor, Citation Auditor
            "bu_analysis":        {},
            "editor_review":      {},
            "citation_audit":     state["data"].get("citation_audit", {}),
            "consistency_flags":  state["data"].get("consistency_flags", {}),
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
            # ── Phase 2 routing + sector/profile classification ─────────────
            # Similarly needed: without these, admin panels can't filter/group
            # runs by profile, and the frontend's TechValuationPanel routing
            # falls back to the classifyTechSubtype ticker-table (works for
            # known tickers but breaks for uncovered ones).
            "profile_name":           state["data"].get("profile_name", ""),
            "profile_names":          state["data"].get("profile_names", {}),
            "sectors":                state["data"].get("sectors", {}),
            # ── Phase 3 deep research artifacts ─────────────────────────────
            # deep_research_sections is the parsed Section 2A-2F dict that
            # feeds the frontend commentary cards (NRR Trajectory, Path to
            # Profitability, AI Capex ROI, etc.). Without it, commentary
            # cards silently hide even when Qwen produced rich 2F content.
            "deep_research_sections": state["data"].get("deep_research_sections", {}),
            "research_tier":          state["data"].get("research_tier"),
            # Sector-specific valuation card payload (Option B render). Must
            # be in this return dict — see commit 1ac5490 / sector_kpi_framework
            # render_card_payload docstring for why state-only writes get lost.
            "sector_card":            state["data"].get("sector_card", {}),
            # Internal — lets analysis_service link web_runs to the archive row
            # without calling save_run() a second time (which would create a duplicate).
            "_archive_run_id":    _archive_run_id,
            # Pass tickers list through so analysis_service can reconstruct state
            "tickers":            state["data"].get("tickers", []),
        }

    finally:
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
        f_ia = executor.submit(run_insider_activity_agent, state_ia)
        f_ar = executor.submit(run_analyst_revision_agent, state_ar)
        f_ns = executor.submit(run_news_sentiment_agent, state_ns)
        f_eq = executor.submit(run_earnings_quality_agent, state_eq)
        f_si = executor.submit(run_short_interest_agent, state_si)

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


def _run_investor_agents_parallel(state: AgentState, active_agents: list[str]) -> AgentState:
    """Run all investor agents concurrently. Max 6 threads to avoid API rate limits."""
    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=min(len(active_agents), 6)) as executor:
        futures = {
            executor.submit(run_advanced_investor, agent_key, state): agent_key
            for agent_key in active_agents
        }
        for future in as_completed(futures):
            agent_key = futures[future]
            try:
                results[agent_key] = future.result()
            except Exception as e:
                print(f"  Warning: {agent_key} agent failed: {e}")
                results[agent_key] = {}

    for agent_key, agent_result in results.items():
        state["data"]["analyst_signals"][agent_key] = agent_result

    return state


def _append_to_trade_log(state: AgentState, decisions: dict) -> None:
    """Append current run decisions to trade_log.json for future Phase 10 scoring."""
    try:
        existing: list[dict] = []
        if os.path.exists(TRADE_LOG_PATH):
            with open(TRADE_LOG_PATH) as f:
                existing = json.load(f)

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
        existing.append(entry)

        with open(TRADE_LOG_PATH, "w") as f:
            json.dump(existing, f, indent=2)

    except Exception as e:
        print(f"  Warning: could not write trade log: {e}")
