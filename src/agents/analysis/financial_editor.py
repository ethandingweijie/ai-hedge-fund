"""
Phase 7e — Senior Financial Editor: Clarity, Logic & Formatting

Role: "Act as a Lead Editor for a Global Hedge Fund. Review the report
for professional tone, logical integrity, and structural clarity."

Three editorial tasks:
  1. Correction & Polish — fix typos, remove AI disclaimers, consistent nomenclature
  2. Formatting & Visualization — consolidate fragmented data, unified tables
  3. Logic Audit — flag internal contradictions between bear/base/bull assumptions
     and final position-size recommendation

Output:
  - Polished executive summary
  - Logic audit flags
  - Report quality score (1-10)
  - state["data"]["editor_review"][ticker]
"""

from langchain_core.prompts import ChatPromptTemplate
from src.graph.state import AgentState
from src.data.models import FinancialEditorOutput
from src.utils.llm import call_llm
from src.utils.progress import progress


SYSTEM_PROMPT = """
You are the Lead Editor for a Global Hedge Fund research team. Review the investment report.

1. CORRECTION & POLISH: Remove AI disclaimers. Fix metric nomenclature (EBITDA vs EBIT).
   Write a polished executive summary (3-4 sentences, investment-grade tone).

2. FORMATTING: Flag data that should be consolidated into a unified Valuation Matrix.
   Verify currency and decimal consistency (2dp prices, 1dp percentages).

3. LOGIC AUDIT (most important): Flag every contradiction found. Check:
   - Action (BUY/SELL/HOLD) follows logically from bear/base/bull assumptions
   - Bull-case revenue growth consistent with sector historical range
   - WACC consistent with macro regime (tightening → higher WACC); flag if WACC < 12% for
     pre-profitability companies (negative operating income) — minimum floor is 12%
   - FCF margin trajectory must be consistent with the revenue growth narrative: high-growth
     companies burning cash should not show simultaneously rising FCF margins unless explicitly
     explained by operating leverage; flag any contradiction
   - For foreign issuers (non-USD reporting currency): flag any ratio (P/E, EV/EBITDA,
     FCF yield) where market cap (USD) is divided by an income figure that may still be in
     the home currency (CNY, HKD, EUR, etc.) without FX conversion — label as CURRENCY MISMATCH
   - Any growth rate cited above 30% in the DCF narrative must distinguish stage-1 CAGR
     from the terminal growth rate; flag as GROWTH RATE AMBIGUITY if not explicitly separated
   - Position size reflects conviction score and EV upside

4. QUALITY SCORE: 9-10 publication-ready | 7-8 minor edits | 5-6 moderate issues |
   3-4 major inconsistencies | 1-2 report does not support recommendation

Output JSON only.
""".strip()


def run_financial_editor(state: AgentState) -> AgentState:
    """Phase 7e: Senior Financial Editor — logic audit, polish, format review."""
    agent_id = "financial_editor"
    tickers = state["data"]["tickers"]
    editor_results: dict = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Auditing report logic and quality")

        # Gather all key report data for this ticker
        decision = (state["data"].get("decisions") or {}).get(ticker, {})
        # Also check analyst_signals for the advanced PM output
        if not decision:
            adv_pm = state["data"].get("analyst_signals", {}).get("advanced_portfolio_manager", {})
            decision = adv_pm.get(ticker, {}) if adv_pm else {}

        scenario = (state["data"].get("scenario_analysis") or {}).get(ticker, {})
        dcf_ticker = (state["data"].get("dcf_range") or {}).get(ticker, {})
        power_law = (state["data"].get("power_law_analysis") or {}).get(ticker, {})
        trap = (state["data"].get("value_trap_analysis") or {}).get(ticker, {})
        macro_regime = state["data"].get("macro_regime", {})
        consistency_flag = (state["data"].get("consistency_flags") or {}).get(ticker, "")

        # Build a compact report summary for the editor
        action      = str(decision.get("action", "HOLD")).upper()
        size_pct    = decision.get("position_size_pct", 0)
        price_tgt   = decision.get("price_target", 0)
        rationale   = str(decision.get("rationale", decision.get("reasoning", "")))[:500]

        bull_fv     = scenario.get("bull", {}).get("fair_value", 0) if isinstance(scenario.get("bull"), dict) else 0
        base_fv     = scenario.get("base", {}).get("fair_value", 0) if isinstance(scenario.get("base"), dict) else 0
        bear_fv     = scenario.get("bear", {}).get("fair_value", 0) if isinstance(scenario.get("bear"), dict) else 0
        bull_p      = scenario.get("bull", {}).get("probability", 0) if isinstance(scenario.get("bull"), dict) else 0
        base_p      = scenario.get("base", {}).get("probability", 0) if isinstance(scenario.get("base"), dict) else 0
        bear_p      = scenario.get("bear", {}).get("probability", 0) if isinstance(scenario.get("bear"), dict) else 0
        ev          = scenario.get("expected_value", 0)
        curr_price  = scenario.get("current_price", 0)
        pt_12m      = scenario.get("12m_price_target", 0)
        upside_pct  = scenario.get("upside_pct", 0)

        base_iv     = (dcf_ticker.get("base") or {}).get("intrinsic_value", 0)
        bear_iv     = (dcf_ticker.get("bear") or {}).get("intrinsic_value", 0)
        bull_iv     = (dcf_ticker.get("bull") or {}).get("intrinsic_value", 0)
        wacc        = dcf_ticker.get("wacc", 0)
        profile     = dcf_ticker.get("profile", "unknown")
        data_source = dcf_ticker.get("data_source", "unknown")
        cal_error   = dcf_ticker.get("calibration_error", False)
        methods_count = (dcf_ticker.get("base") or {}).get("methods_count", 1)

        pl_score    = power_law.get("total_score", 5)
        trap_verdict = trap.get("overall_verdict", "TRAP RISK LOW")
        rate_dir    = macro_regime.get("rate_direction", "neutral")
        risk_app    = macro_regime.get("risk_appetite", "neutral")

        template = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", (
                "TICKER: {ticker}\n\n"
                "=== DECISION ===\n"
                "Action: {action} | Position Size: {size_pct:.1%} | Price Target: ${price_tgt:.2f}\n"
                "Rationale: {rationale}\n"
                "Directional flag: {flag}\n\n"
                "=== VALUATION ===\n"
                "Current price: ${curr:.2f} | EV: ${ev:.2f} | 12m PT: ${pt12:.2f} | Upside: {upside:.1f}%\n"
                "Blended IV — Bear: ${bear_iv:.0f} | Base: ${base_iv:.0f} | Bull: ${bull_iv:.0f}\n"
                "Methods used: {methods_count} | Profile: {profile} | Data source: {data_source}\n"
                "WACC: {wacc:.1%} | Calibration error: {cal_error}\n\n"
                "=== SCENARIOS ===\n"
                "Bear ({bear_p:.0%}): IV ${bear_fv:.0f} | Base ({base_p:.0%}): IV ${base_fv:.0f} | Bull ({bull_p:.0%}): IV ${bull_fv:.0f}\n\n"
                "=== QUALITATIVE SCORES ===\n"
                "Power Law: {pl}/10 | Value Trap: {trap}\n"
                "Macro: {risk_app} risk appetite | Rates: {rate_dir}\n\n"
                "Output format:\n"
                "{{\n"
                '  "polished_summary": "3-4 sentence investment-grade summary",\n'
                '  "logic_audit_flags": ["flag1", "flag2"],\n'
                '  "formatting_notes": ["note1"],\n'
                '  "report_quality_score": int (1-10),\n'
                '  "key_corrections": ["correction1"]\n'
                "}}"
            )),
        ])

        prompt = template.invoke({
            "ticker": ticker,
            "action": action,
            "size_pct": size_pct or 0,
            "price_tgt": price_tgt or 0,
            "rationale": rationale,
            "flag": consistency_flag or "None",
            "curr": curr_price or 0,
            "ev": ev or 0,
            "pt12": pt_12m or 0,
            "upside": upside_pct or 0,
            "bear_iv": bear_iv or 0,
            "base_iv": base_iv or 0,
            "bull_iv": bull_iv or 0,
            "methods_count": methods_count,
            "profile": profile,
            "data_source": data_source,
            "wacc": wacc or 0,
            "cal_error": "YES — model unreliable" if cal_error else "PASS",
            "bear_p": bear_p, "bear_fv": bear_fv or 0,
            "base_p": base_p, "base_fv": base_fv or 0,
            "bull_p": bull_p, "bull_fv": bull_fv or 0,
            "pl": pl_score,
            "trap": trap_verdict,
            "risk_app": risk_app,
            "rate_dir": rate_dir,
        })

        result: FinancialEditorOutput = call_llm(
            prompt=prompt,
            pydantic_model=FinancialEditorOutput,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: FinancialEditorOutput(
                polished_summary="Editor review unavailable.",
                logic_audit_flags=["Editor LLM call failed."],
                formatting_notes=[],
                report_quality_score=5,
                key_corrections=[],
            ),
        )

        editor_results[ticker] = result.model_dump()
        score = result.report_quality_score
        flags = len(result.logic_audit_flags)
        progress.update_status(agent_id, ticker, f"Quality score: {score}/10 | {flags} logic flag(s)")

    state["data"]["editor_review"] = editor_results
    return state
