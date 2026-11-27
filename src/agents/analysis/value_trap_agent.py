"""
Phase 7c — Value Trap Audit Agent

What it does:
- Runs five specific RED/AMBER/GREEN checks (from CLAUDE.md §7c):
    1. Dividend sustainability (FCF payout ratio, debt-funded dividends)
    2. Structural decline (negative 5-year revenue CAGR, shrinking market share)
    3. Earnings vs. cash flow mismatch (net income up but FCF flat, receivables > revenue growth)
    4. Insider behaviour (net selling by CEO/CFO >$5M last 12 months)
    5. Balance sheet deterioration (net debt/EBITDA increasing 3+ consecutive years). Owner,
       2026-11-27: the model's call is replaced after Phase 7 by the deterministic check in
       src/agents/analysis/risk_metrics.py (level thresholds, banks on capital, NOT ASSESSED
       on thin or stale data); the verdict moves by exactly that check's change.
- Produces TRAP RISK HIGH / MEDIUM / LOW verdict
- TRAP RISK HIGH halves the position size in the Portfolio Manager formula

Why a separate kill-switch agent:
- Value traps are the most common way fundamental analysts lose money
- These specific red flags are well-evidenced in empirical finance literature
- Making them explicit and required forces the pipeline to face them rather than let
  an optimistic investor agent bury them in caveats
"""

from langchain_core.prompts import ChatPromptTemplate

from src.data.models import ValueTrapCheck, ValueTrapOutput
from src.graph.state import AgentState
from src.utils.llm import call_llm
from src.utils.progress import progress

SYSTEM_PROMPT = """
You are the Value Trap Audit Agent. Your job is to find what could go wrong.

Run the following checks and flag RED / AMBER / GREEN for each:

1. Dividend sustainability: FCF payout ratio >80%? Debt-funded dividends? → RED if yes
2. Structural decline: Revenue CAGR negative over 5 years? Market share shrinking? → RED if yes
3. Earnings vs cash flow mismatch: Net Income growing but FCF flat/declining?
   Receivables growing faster than revenue? → RED if yes
4. Insider behaviour: Net selling by CEO/CFO >$5M in last 12 months? → AMBER
5. Balance sheet deterioration: Net Debt/EBITDA increasing for 3+ consecutive years? → RED
6. Material asset impairment: Has a major facility (plant, battery storage site,
   mine, data centre) been destroyed, condemned, or written down in the last 18 months?
   Is the company still including that asset's EBITDA contribution in forward guidance
   without disclosing an adjusted estimate?
   → RED if impairment is material (>5% of EBITDA) and not yet reflected in consensus estimates.
   → AMBER if impairment is known but recovery/insurance timeline is uncertain.
   → GREEN if impairment is fully reflected in guidance and insurance recovery is contractually secured.
   Note: cite the specific event (e.g. "Moss Landing battery fire Jan 2025, ~$300M asset") if present.

Overall verdict:
- 2+ RED flags → TRAP RISK HIGH
- 1 RED or 2+ AMBER → TRAP RISK MEDIUM
- All GREEN/AMBER with 0 RED → TRAP RISK LOW

Be specific. Cite the data point that triggers each flag.
Output JSON only.
""".strip()


#: Owner, 2026-11-25: an unrun check is NOT ASSESSED, never a silent GREEN.
NOT_ASSESSED = "NOT ASSESSED"
NOT_ASSESSED_VERDICT = "TRAP RISK NOT ASSESSED"
_NOT_ASSESSED_EVIDENCE = "Not assessed: the value-trap check could not run, so this item carries no verdict."

#: Line items the checklist needs, in the order they are printed.
_TRAP_FIELDS = (
    ("revenue", "revenue"), ("net_income", "net income"), ("ebitda", "EBITDA"),
    ("operating_cash_flow", "operating cash flow"), ("capital_expenditure", "capex"),
    ("free_cash_flow", "FCF"), ("dividends_and_distributions", "dividends paid"),
    ("share_buyback", "buybacks"), ("accounts_receivable", "receivables"),
    ("net_debt", "net debt"), ("total_debt", "total debt"), ("shareholders_equity", "equity"),
)


def _fmt_amount(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    a = abs(f)
    if a >= 1e9:
        return f"{f / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{f / 1e6:,.1f}M"
    return f"{f:,.0f}"


def financials_for_trap(raw_financials: dict, years: int = 5) -> str:
    """The newest `years` fiscal years, NEWEST FIRST, one compact line each, with the ratios
    the checks ask for computed (FCF payout, net debt / EBITDA). Replaces str(raw)[:2000],
    which sent the oldest year first and was cut off before the latest one (owner,
    2026-11-25: prod GM's dividend check cited FY2021)."""
    if not isinstance(raw_financials, dict):
        return "Not available."
    fys = sorted((k for k, v in raw_financials.items() if isinstance(v, dict)), reverse=True)[:years]
    if not fys:
        return "Not available."
    ccy = raw_financials.get("currency") or raw_financials.get("reported_currency")
    lines = [f"Fiscal years {fys[0]} back to {fys[-1]}, newest first"
             + (f" (amounts in {ccy})" if ccy else "") + ":"]
    for fy in fys:
        r = raw_financials[fy]
        ebitda = r.get("ebitda")
        if ebitda is None and r.get("operating_income") is not None and r.get("depreciation_and_amortization") is not None:
            try:
                ebitda = float(r["operating_income"]) + float(r["depreciation_and_amortization"])
            except (TypeError, ValueError):
                ebitda = None
        vals = dict(r, ebitda=ebitda)
        bits = [f"{lbl} {_fmt_amount(vals.get(k))}" for k, lbl in _TRAP_FIELDS if vals.get(k) is not None]
        try:
            fcf, div = float(vals.get("free_cash_flow")), abs(float(vals.get("dividends_and_distributions")))
            if div > 0:
                bits.append("FCF payout n/m (FCF negative)" if fcf <= 0 else f"FCF payout {div / fcf:.0%}")
        except (TypeError, ValueError):
            pass
        try:
            nd, eb = float(vals.get("net_debt")), float(ebitda)
            if eb > 0:
                bits.append(f"net debt/EBITDA {nd / eb:.2f}x")
        except (TypeError, ValueError):
            pass
        pe = r.get("period_end") or r.get("date")
        lines.append(f"{fy}" + (f" (ended {pe})" if pe else "") + ": " + "; ".join(bits))
    return "\n".join(lines)


def not_assessed_output() -> ValueTrapOutput:
    chk = lambda: ValueTrapCheck(status=NOT_ASSESSED, evidence=_NOT_ASSESSED_EVIDENCE)   # noqa: E731
    return ValueTrapOutput(
        dividend_sustainability=chk(), structural_decline=chk(), earnings_cashflow_mismatch=chk(),
        insider_behaviour=chk(), balance_sheet_deterioration=chk(), overall_verdict=NOT_ASSESSED_VERDICT)


def run_value_trap_agent(state: AgentState) -> AgentState:
    """Phase 7c: value trap checklist — RED/AMBER/GREEN per criterion."""
    agent_id = "value_trap_agent"
    tickers = state["data"]["tickers"]
    raw_financials = state["data"].get("raw_financials", {})
    insider_summary = state["data"].get("insider_summary", "")
    dr_sections = state["data"].get("deep_research_sections", {})
    # 2D (cycle positioning) informs the structural decline check:
    # a company declining in a growing industry is a value trap;
    # one declining in a structurally declining industry may be priced correctly
    cycle_section = dr_sections.get("2d", "")

    value_trap_results: dict[str, object] = {}
    # Phase 2.5 structured data (prefer over raw text summaries)
    ia_all = state["data"].get("insider_activity", {})
    eq_all = state["data"].get("earnings_quality", {})

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Running value trap audit")

        # Build insider context: prefer Phase 2.5 structured signal over raw text summary
        ia = ia_all.get(ticker, {})
        if ia:
            key_txns = ia.get("key_transactions", [])
            txn_lines = "; ".join(
                f"{t.get('transaction_type')} ${t.get('value_usd') or 0:,.0f} "
                f"by {t.get('title','?')} on {t.get('date','?')}"
                for t in key_txns[:3]
            ) or "none"
            insider_context = (
                f"Phase 2.5 InsiderActivityAgent [{ia.get('data_source','?')}]: "
                f"signal={ia.get('signal','?')} | "
                f"net_12m=${ia.get('net_buying_12m_usd', 0):+,.0f} | "
                f"buy_sell_ratio={ia.get('buy_sell_ratio_12m', 0):.1f}x | "
                f"cluster_buy={ia.get('cluster_buy', False)} | "
                f"conviction_sell={ia.get('conviction_sell_flag', False)}\n"
                f"Key transactions: {txn_lines}"
            )
        else:
            insider_context = insider_summary[:500]

        # ── Pathway 2: Earnings Quality → Check 3 pre-answer ─────────────────
        # Deterministic metrics replace LLM inference for Check 3 when
        # FULL or PARTIAL data is available; LLM adds qualitative context only.
        eq = eq_all.get(ticker, {})
        eq_context = ""
        if eq and eq.get("data_quality") in ("FULL", "PARTIAL"):
            eq_flags = eq.get("flags", [])
            flag_lines = "\n".join(f"    - {f}" for f in eq_flags) if eq_flags else "    - None"
            eq_context = (
                "\nPhase 2.5 EarningsQualityAgent (deterministic — use as Check 3 evidence):\n"
                f"  verdict={eq.get('quality_verdict','?')} | "
                f"score={eq.get('overall_quality_score', 0.0):.1f}/10 | "
                f"data_quality={eq.get('data_quality','?')}\n"
                f"  accrual_flag={eq.get('accrual_flag','?')} "
                f"(3yr avg={eq.get('accrual_ratio_avg') or 'N/A'}) | "
                f"accrual_trend={eq.get('accrual_trend','?')}\n"
                f"  cash_conversion_ratio={eq.get('cash_conversion_ratio') or 'N/A'} "
                f"[{eq.get('cash_conversion_flag','?')}]\n"
                f"  fcf_ni_divergence={eq.get('fcf_ni_divergence','?')} "
                f"(ratios newest→oldest: {eq.get('fcf_ni_ratios',[])})\n"
                f"  ar_revenue_divergence={eq.get('ar_revenue_divergence','?')} "
                f"(AR CAGR={eq.get('ar_cagr_3y') or 'N/A'} "
                f"vs Rev CAGR={eq.get('revenue_cagr_3y') or 'N/A'})\n"
                f"  dso_trend={eq.get('dso_trend','?')} "
                f"(values newest→oldest: {eq.get('dso_values',[])})\n"
                f"  sbc_drag={eq.get('sbc_drag_pct') or 'N/A'}% [{eq.get('sbc_drag_flag','?')}]\n"
                f"  pre_earnings_risk={eq.get('pre_earnings_risk','?')}\n"
                f"  Computed flags:\n{flag_lines}\n"
                "INSTRUCTION: Use the metrics above as your primary evidence for "
                "Check 3 (earnings vs cash flow mismatch). Do not contradict "
                "deterministic computed values — only add qualitative nuance.\n"
            )

        template = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", (
                "Ticker: {ticker}\n\n"
                "Financials (newest fiscal year first -- judge each check on the latest "
                "year and the trend into it, and cite the latest year):\n{raw_financials}\n\n"
                "Insider activity:\n{insider}\n"
                "{eq_context}\n"
                "2D — Industry Cycle Positioning (context for structural decline check —\n"
                "company declining in a growing industry = trap; declining in a structurally\n"
                "declining industry = correctly priced):\n{cycle}\n\n"
                "Output format:\n"
                '{{\n'
                '  "dividend_sustainability": {{"status": "RED"|"AMBER"|"GREEN", "evidence": "..."}},\n'
                '  "structural_decline": {{"status": "RED"|"AMBER"|"GREEN", "evidence": "..."}},\n'
                '  "earnings_cashflow_mismatch": {{"status": "RED"|"AMBER"|"GREEN", "evidence": "..."}},\n'
                '  "insider_behaviour": {{"status": "RED"|"AMBER"|"GREEN", "evidence": "..."}},\n'
                '  "balance_sheet_deterioration": {{"status": "RED"|"AMBER"|"GREEN", "evidence": "..."}},\n'
                '  "overall_verdict": "TRAP RISK HIGH"|"TRAP RISK MEDIUM"|"TRAP RISK LOW"\n'
                "}}"
            )),
        ])

        prompt = template.invoke({
            "ticker": ticker,
            "raw_financials": financials_for_trap(raw_financials),
            "insider": insider_context,
            "eq_context": eq_context,
            "cycle": cycle_section[:1200] if cycle_section else "Not available.",
        })

        # Owner, 2026-11-25: a failed call publishes NOT ASSESSED, never a default GREEN.
        result: ValueTrapOutput = call_llm(
            prompt=prompt,
            pydantic_model=ValueTrapOutput,
            agent_name=agent_id,
            state=state,
            default_factory=not_assessed_output,
        )

        value_trap_results[ticker] = result.model_dump()
        progress.update_status(agent_id, ticker, result.overall_verdict)

    state["data"]["value_trap_analysis"] = value_trap_results
    return state
