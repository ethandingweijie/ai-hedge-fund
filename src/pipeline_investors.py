"""
Phase 5 — Advanced Investor Agent Runner

What it does:
- Defines the 12 investor personas (verbatim from CLAUDE.md)
- Defines sector calibrations that get injected into each persona's prompt
  based on the sector identified in Phase 2
- run_advanced_investor(agent_key, state) → dict[ticker, AdvancedInvestorSignal]
  calls the LLM once per ticker with the full enriched prompt:
    [1] persona + philosophy
    [2] industry intelligence brief (Phase 3)
    [3] agent-specific pre-fetched data (Phase 4)
    [4] macro regime context (Phase 1)
- The richer output schema (conviction 1-10, price_target, thesis_summary, key_risks, cot_log)
  is what powers the Debate Round and Scenario Agent downstream

Why separate from the existing agent files:
- Existing agents use {signal, confidence, reasoning} — we need {signal, conviction, cot_log, ...}
- We don't want to break the existing simple pipeline
- Investor logic here is LLM-only (persona + data); the existing agents do local computation
  first then LLM. Both approaches are valid; here the LLM does more of the reasoning work
  because it has the full industry context it previously lacked.
"""

import json
from langchain_core.prompts import ChatPromptTemplate

from src.data.models import AdvancedInvestorSignal
from src.graph.state import AgentState
from src.utils.llm import call_llm
from src.utils.progress import progress

# ---------------------------------------------------------------------------
# Investor personas (from CLAUDE.md §4)
# ---------------------------------------------------------------------------
INVESTOR_PERSONAS: dict[str, str] = {
    "damodaran": """
Aswath Damodaran — Dean of Valuation (NYU Stern).
Always start with the narrative, then stress-test with numbers.
Build DCF bottoms-up: narrative → revenue growth → margins → reinvestment → cost of capital → intrinsic value.
Attach probability distributions to outcomes. Separate price from value.
Output bull/base/bear intrinsic value with narrative variants for each.
""",
    "graham": """
Benjamin Graham — Father of Value Investing.
Trust only audited numbers. Require minimum 33% margin of safety.
Compute: Net Current Asset Value (NCAV), Earnings Power Value (EPV).
Screen: current ratio >2, debt/equity <0.5, 10-year earnings consistency, dividend record.
Only output BUY if margin of safety ≥ 33%. Otherwise output HOLD or SELL.
""",
    "ackman": """
Bill Ackman — Activist Investor (Pershing Square).
Seek fundamentally sound businesses that are mismanaged or misunderstood.
Identify the activist catalyst: board change, buyback, spinoff, strategic review.
Quantify upside/downside asymmetry explicitly.
Also consider SHORT if fraud or structural overvaluation detected.
Output LONG or SHORT thesis with specific catalyst milestones.
""",
    "cathie_wood": """
Cathie Wood — Queen of Disruptive Growth (ARK Invest).
Minimum 5-year horizon. Focus on five platforms: AI, robotics, energy storage, genomics, blockchain.
Apply Wright's Law to model cost curves and adoption S-curves.
Model 5-year TAM expansion. Build expected value across bull/base/bear.
High near-term multiples acceptable if 5-year TAM expansion justifies.
Output 5-year price target with TAM model summary.
""",
    "munger": """
Charlie Munger — Worldly Wise Investor (Berkshire Hathaway).
Think in mental models: psychology, physics, biology, economics, history.
Seek wonderful businesses at fair prices. Hold forever.
Always invert: list every way this investment fails before listing upsides.
Assess moat durability: pricing power, switching costs, network effects, cost advantages.
Output: WONDERFUL / GOOD / AVOID framing with inversion analysis.
""",
    "burry": """
Michael Burry — Forensic Contrarian (Scion Asset Management).
Read every footnote. Flag accounting red flags: revenue recognition, off-balance-sheet items,
receivables growth > revenue growth, FCF vs net income divergence.
Identify what the market is wrong about (variant perception).
Also consider macro dislocations and systemic risks.
Output DEEP VALUE BUY / PASS / SHORT with forensic evidence.
""",
    "pabrai": """
Mohnish Pabrai — Dhandho Investor.
Mantra: heads I win, tails I don't lose much.
Identify hard asset floor or cash flow floor that caps downside.
Model path to a double in 2-3 years.
Business must be simple enough to explain in one paragraph.
Check if a superinvestor you respect already owns it (cloning check).
Output DHANDHO BUY / PASS with explicit upside/downside table.
""",
    "lynch": """
Peter Lynch — Practical Tenbagger Hunter (Fidelity Magellan).
Categorise: slow grower / stalwart / fast grower / cyclical / asset play / turnaround.
Compute PEG ratio (target <1). Check earnings consistency.
Write the "Peter Lynch Story": one paragraph a non-investor can understand.
Flag institutional neglect or under-ownership as a positive signal.
Output TENBAGGER / STALWART / AVOID with plain-English thesis.
""",
    "fisher": """
Phil Fisher — Scuttlebutt Investigator.
Answer the top 8 of Fisher's 15 questions:
1. Products/services with sufficient market potential?
2. Management determined to develop new products?
3. R&D efforts effective relative to size?
4. Above-average sales organisation?
5. Worthwhile profit margin?
6. Improving profit margins?
7. Outstanding labour/personnel relations?
8. Outstanding executive relations?
Project 5-year sales and earnings trajectory.
Output LONG-TERM BUY / HOLD / SELL with 5-year earnings model.
""",
    "jhunjhunwala": """
Rakesh Jhunjhunwala — The Big Bull of India.
Focus on India-listed or India-exposed names. Assess alignment with India's decade-long
consumption, financialisation, and infrastructure growth story.
Evaluate management execution track record (Indian promoter culture lens).
Identify sector tailwind and market share opportunity in Indian context.
Particularly bullish on: retail banking, consumer discretionary, healthcare, infrastructure.
Output MULTIBAGGER / HOLD / EXIT with India macro thesis.
""",
    "druckenmiller": """
Stanley Druckenmiller — Macro Legend (Duquesne Capital).
Always start with the macro regime. Is the wind at your back?
Identify whether the catalyst is macro (liquidity, rates, flows) or micro (earnings, restructuring).
When right, size very large. Position sizing is the edge.
Define explicit stop-loss trigger that proves the thesis wrong.
Output ASYMMETRIC LONG / SHORT / NEUTRAL with macro regime map and position sizing rationale.
""",
    "buffett": """
Warren Buffett — Oracle of Omaha (Berkshire Hathaway).
Seek wonderful businesses: durable moat, high ROCE without leverage, honest management, simple model.
Identify moat type: brand / switching costs / network effects / cost advantage / toll bridge.
Calculate owner earnings = Net Income + D&A - Maintenance Capex.
Assess management capital allocation: buybacks, dividends, acquisitions (value-creative?).
Value as private business owner. Never overpay.
Output WONDERFUL AT FAIR PRICE / GOOD AT CHEAP / PASS with one-page investment memo style.
""",
}

# ---------------------------------------------------------------------------
# Sector calibrations — appended to each investor's prompt
# ---------------------------------------------------------------------------
SECTOR_CALIBRATIONS: dict[str, str] = {
    "Tech": (
        "Focus on: revenue growth rate, gross margin trend, NRR if SaaS, R&D spend efficiency, "
        "competitive moat (API lock-in, ecosystem breadth). Beware of high CAC payback periods."
    ),
    "Consumer": (
        "Focus on: brand pricing power, same-store sales momentum, input cost pass-through ability, "
        "debt load vs. cash generation. Beware of volume declines masked by pricing."
    ),
    "Biopharma": (
        "Focus on: pipeline rNPV vs. market cap, upcoming binary events (FDA/EMA), "
        "patent cliff exposure, cash runway vs. burn rate."
    ),
    "Telco": (
        "Focus on: FCF yield, tenancy ratio, maintenance vs. growth capex split, "
        "spectrum asset value, subscriber churn trends."
    ),
    "Crypto": (
        "Focus on: production cost per coin vs. current price, hash rate trajectory, "
        "MW pipeline, balance sheet BTC/ETH holdings, jurisdiction risk."
    ),
    "Energy": (
        "Focus on: SOTP vs. market cap, PPA contract quality and tenor, "
        "LCOE vs. power prices, regulatory pipeline, capacity factor."
    ),
    "Financials": (
        "Focus on: NIM trend, NPL ratio vs. sector, CET1 buffer, RoE vs. CoE spread, "
        "loan growth quality, credit cycle position."
    ),
    "Industrials": (
        "Focus on: backlog coverage ratio, book-to-bill trend, fixed-price contract risk, "
        "customer concentration, margin on backlog vs. current margins."
    ),
}

BASE_INVESTOR_SYSTEM = """
You are {persona_name}.

You have received:
[1] Your investment philosophy (above)
[2] An Industry Intelligence Brief from the Industry Specialist
[3] Agent-specific financial data pre-fetched for your analysis
[4] Current macro regime context

Your task — Phase 2 Chain-of-Thought Computation:
- Show your step-by-step reasoning in cot_log. Do not skip steps.
- Apply your investment philosophy through the lens of the industry brief.
- Your sector calibration for this analysis: {sector_calibration}

Output JSON only. Do not include any text outside the JSON.
""".strip()


def _default_signal(agent_key: str, ticker: str) -> dict:
    return {
        "signal": "HOLD",
        "conviction": 5,
        "time_horizon": "medium",
        "price_target": 0.0,
        "thesis_summary": f"Default hold — {agent_key} analysis unavailable.",
        "key_risks": ["Data unavailable"],
        "cot_log": "LLM call failed; defaulting to HOLD.",
    }


def run_advanced_investor(agent_key: str, state: AgentState) -> dict[str, dict]:
    """
    Run one investor agent across all tickers in state.
    Returns {ticker: signal_dict} — does NOT mutate state (called from parallel threads).
    """
    tickers = state["data"]["tickers"]
    # Per-ticker sector map built by strategic_router; fall back to shared sector for
    # single-ticker runs or runs predating this change.
    sectors_map = state["data"].get("sectors", {})
    _primary_sector = state["data"].get("sector", "Tech")
    industry_brief = state["data"].get("industry_brief", "")
    # Per-ticker deep research map (parallel research); falls back to shared sections
    deep_research_map = state["data"].get("deep_research_map", {})
    macro_regime = state["data"].get("macro_regime", {})
    routed_data = state["data"].get("routed_data", {})
    agent_data = routed_data.get(agent_key, {})

    persona = INVESTOR_PERSONAS.get(agent_key, f"Generic investor agent: {agent_key}")
    persona_name = agent_key.replace("_", " ").title()

    # Structured sector KPIs extracted by specialist.py — compact, typed, pre-validated
    # (these are keyed to the primary ticker; used as fallback for non-primary tickers)
    industry_kpis = state["data"].get("industry_kpis", {})
    sector_kpis = state["data"].get("sector_kpis", {})
    # Merge: sector_kpis (typed) takes precedence over raw industry_kpis (freeform)
    combined_kpis = {**industry_kpis, **sector_kpis}
    kpis_str = json.dumps(combined_kpis, default=str)[:600] if combined_kpis else ""

    results: dict[str, dict] = {}

    for ticker in tickers:
        # Resolve per-ticker sector so calibration and prompt reflect the correct industry
        sector = sectors_map.get(ticker, _primary_sector)
        sector_cal = SECTOR_CALIBRATIONS.get(sector, "")
        # Use this ticker's own deep research sections when available (parallel research);
        # fall back to the shared primary-ticker sections for backward-compat
        _ticker_dr = deep_research_map.get(ticker, {})
        _dr_sections = _ticker_dr.get("deep_research_sections") or state["data"].get("deep_research_sections", {})
        kpi_framework = _dr_sections.get("2f", "")
        progress.update_status(f"investor_{agent_key}", ticker, "Analysing")

        # DCF Engine anchors (Phase 4.5) — multi-method blended IV, macro-adjusted
        dcf_ticker = state["data"].get("dcf_range", {}).get(ticker, {})
        dcf_section = ""
        if dcf_ticker and dcf_ticker.get("base"):
            cal_warn = (" ⚠ CALIBRATION ERROR — model struggled to reproduce T-1 price: "
                        f"{dcf_ticker.get('calibration_note', '')}"
                        if dcf_ticker.get("calibration_error") else "")
            fwd_flags_base = dcf_ticker["base"].get("forward_flags", [])
            fwd_note = (" | Forward flags: " + "; ".join(fwd_flags_base)) if fwd_flags_base else ""
            methods_used = dcf_ticker["base"].get("methods_used", [])
            profile = dcf_ticker.get("profile", "—")
            c_macro = dcf_ticker.get("c_macro", 0.0)
            dcf_section = (
                f"DCF Engine anchors (multi-method blended IV, Phase 4.5):\n"
                f"  Profile: {profile}  |  C_macro: {c_macro:+.2f}  |  Methods: {', '.join(methods_used)}\n"
                f"  Bear IV: ${dcf_ticker['bear']['intrinsic_value']:.2f}  "
                f"Base IV: ${dcf_ticker['base']['intrinsic_value']:.2f}  "
                f"Bull IV: ${dcf_ticker['bull']['intrinsic_value']:.2f}\n"
                f"  WACC: {dcf_ticker['wacc']:.1%}  "
                f"Growth source: {dcf_ticker['data_source']}  "
                f"FCF margin base: {dcf_ticker['fcf_margin_base']:.1%}"
                f"{fwd_note}{cal_warn}\n\n"
            )

        # ── Phase 2.5 Intelligence signals (pre-computed, deterministic) ──
        ia_data  = state["data"].get("insider_activity", {}).get(ticker, {})
        ar_data  = state["data"].get("analyst_revisions", {}).get(ticker, {})
        ns_data  = state["data"].get("news_sentiment", {}).get(ticker, {})
        eq_data  = state["data"].get("earnings_quality", {}).get(ticker, {})
        si_data  = state["data"].get("short_interest", {}).get(ticker, {})
        intel_section = ""
        if ia_data or ar_data or ns_data or eq_data or si_data:
            parts = ["Phase 2.5 Intelligence Signals (deterministic, trust these):\n"]
            if ia_data:
                key_txns = ia_data.get("key_transactions", [])
                txn_str = "; ".join(
                    f"{t.get('transaction_type')} ${t.get('value_usd') or 0:,.0f} "
                    f"by {t.get('title','?')} on {t.get('date','?')}"
                    for t in key_txns[:3]
                ) or "none"
                parts.append(
                    f"  Insider Activity [{ia_data.get('data_source','?')}]: "
                    f"signal={ia_data.get('signal','?')} | "
                    f"cluster_buy={ia_data.get('cluster_buy',False)} | "
                    f"conviction_sell={ia_data.get('conviction_sell_flag',False)} | "
                    f"net_12m=${ia_data.get('net_buying_12m_usd',0):+,.0f} | "
                    f"buy/sell_ratio={ia_data.get('buy_sell_ratio_12m',0):.1f}x\n"
                    f"  Key transactions: {txn_str}\n"
                )
            if ar_data:
                surprises = ar_data.get("recent_surprises", [])
                surp_str = ", ".join(
                    f"{s.get('surprise_pct',0):+.1f}%({'B' if s.get('beat') else 'M'})"
                    for s in surprises[:4]
                ) or "none"
                parts.append(
                    f"  Analyst Revision: direction={ar_data.get('revision_direction','?')} | "
                    f"streak={ar_data.get('surprise_streak',0):+d} | "
                    f"dispersion={ar_data.get('estimate_dispersion','?')} "
                    f"(EPS: {ar_data.get('eps_dispersion_pct') or '?'}%) | "
                    f"analysts={ar_data.get('analyst_count',0)}\n"
                    f"  Recent EPS surprises: {surp_str}\n"
                )
            if ns_data:
                headlines = ns_data.get("top_headlines", [])
                top_str = " | ".join(headlines[:3]) or "none"
                spike_note = " ⚠ VOLUME SPIKE" if ns_data.get("volume_spike") else ""
                parts.append(
                    f"  News Sentiment: signal={ns_data.get('signal','?')} | "
                    f"score={ns_data.get('composite_score', 0.0):+.3f}{spike_note} | "
                    f"articles={ns_data.get('article_count',0)} "
                    f"(B:{ns_data.get('bullish_count',0)} "
                    f"N:{ns_data.get('neutral_count',0)} "
                    f"Be:{ns_data.get('bearish_count',0)}) | "
                    f"press_releases={ns_data.get('press_release_count',0)} "
                    f"({ns_data.get('press_release_signal','NONE')})\n"
                    f"  Top headlines: {top_str}\n"
                )
            if eq_data and eq_data.get("data_quality") != "INSUFFICIENT":
                eq_flags = eq_data.get("flags", [])
                eq_flag_str = " | ".join(eq_flags[:2]) if eq_flags else "none"
                parts.append(
                    f"  Earnings Quality [data={eq_data.get('data_quality','?')}]: "
                    f"verdict={eq_data.get('quality_verdict','?')} | "
                    f"score={eq_data.get('overall_quality_score', 0.0):.1f}/10 | "
                    f"pre_earnings_risk={eq_data.get('pre_earnings_risk','?')}\n"
                    f"  accrual_flag={eq_data.get('accrual_flag','?')} "
                    f"(avg={eq_data.get('accrual_ratio_avg') or '?'}) | "
                    f"cash_conv_ratio={eq_data.get('cash_conversion_ratio') or '?'} "
                    f"[{eq_data.get('cash_conversion_flag','?')}] | "
                    f"fcf_ni={eq_data.get('fcf_ni_divergence','?')} | "
                    f"ar_rev_div={eq_data.get('ar_revenue_divergence','?')} | "
                    f"dso_trend={eq_data.get('dso_trend','?')} | "
                    f"sbc_drag={eq_data.get('sbc_drag_pct') or '?'}% "
                    f"[{eq_data.get('sbc_drag_flag','?')}]\n"
                    f"  Key flags: {eq_flag_str}\n"
                )
            if si_data and si_data.get("signal") != "UNKNOWN":
                # All investors get the core positioning signal
                si_base = (
                    f"  Short Interest [{si_data.get('data_source','?')} "
                    f"date={si_data.get('report_date','?')}]: "
                    f"signal={si_data.get('signal','?')} | "
                    f"short_float={si_data.get('short_float_pct','?')}% "
                    f"[{si_data.get('short_float_flag','?')}] | "
                    f"dtc={si_data.get('days_to_cover','?')}d "
                    f"[{si_data.get('days_to_cover_flag','?')}] | "
                    f"borrow={si_data.get('borrow_rate_pct','?')}% "
                    f"[{si_data.get('borrow_rate_flag','?')}] | "
                    f"trend={si_data.get('short_interest_trend','?')} | "
                    f"squeeze_risk={si_data.get('squeeze_risk',False)} | "
                    f"crowded={si_data.get('crowded_trade',False)}\n"
                )
                # Persona-specific notes for Burry and Druckenmiller
                burry_note = si_data.get("burry_note", "")
                druck_note = si_data.get("druckenmiller_note", "")
                persona_si_note = ""
                if agent_key == "burry" and burry_note:
                    persona_si_note = f"  [Burry lens] {burry_note}\n"
                elif agent_key == "druckenmiller" and druck_note:
                    persona_si_note = f"  [Druckenmiller lens] {druck_note}\n"
                parts.append(si_base + persona_si_note)
            intel_section = "".join(parts) + "\n"

        # Compact agent data for the prompt (cap to avoid token overflow).
        # Sector overlay fields land at the front of the bundle dict so they
        # survive the 3000-char truncation even for data-heavy agents.
        agent_data_str = json.dumps(agent_data, default=str)[:3000]
        macro_str = json.dumps(macro_regime)

        template = ChatPromptTemplate.from_messages([
            ("system", BASE_INVESTOR_SYSTEM.format(
                persona_name=persona_name,
                sector_calibration=sector_cal,
            )),
            ("human", (
                "Persona:\n{persona}\n\n"
                "Ticker: {ticker}\n\n"
                "Industry Intelligence Brief:\n{industry_brief}\n\n"
                "{kpi_framework_section}"
                "{kpis_section}"
                "{dcf_section}"
                "{intel_section}"
                "Your pre-fetched financial data:\n{agent_data}\n\n"
                "Macro regime: {macro}\n\n"
                "Output format:\n"
                '{{\n'
                '  "signal": "BUY"|"SELL"|"SHORT"|"HOLD",\n'
                '  "conviction": 1-10,\n'
                '  "time_horizon": "short"|"medium"|"long",\n'
                '  "price_target": float,\n'
                '  "thesis_summary": "2-3 sentences",\n'
                '  "key_risks": ["risk1", "risk2", "risk3"],\n'
                '  "cot_log": "full chain-of-thought reasoning"\n'
                "}}"
            )),
        ])

        kpis_section = (
            f"Structured sector KPIs (pre-computed, trust these numbers):\n{kpis_str}\n\n"
            if kpis_str else ""
        )
        kpi_framework_section = (
            f"2F — Industry KPI Framework (anchor KPI, leading indicator, risk threshold):\n"
            f"{kpi_framework[:1200]}\n\n"
            if kpi_framework else ""
        )

        prompt = template.invoke({
            "persona": persona,
            "ticker": ticker,
            "industry_brief": industry_brief[:30000],
            "kpi_framework_section": kpi_framework_section,
            "kpis_section": kpis_section,
            "dcf_section": dcf_section,
            "intel_section": intel_section,
            "agent_data": agent_data_str,
            "macro": macro_str,
        })

        signal: AdvancedInvestorSignal = call_llm(
            prompt=prompt,
            pydantic_model=AdvancedInvestorSignal,
            agent_name=f"investor_{agent_key}",
            state=state,
            default_factory=lambda: AdvancedInvestorSignal(
                signal="HOLD",
                conviction=5,
                time_horizon="medium",
                price_target=0.0,
                thesis_summary=f"Default hold — {agent_key} analysis unavailable.",
                key_risks=["Data unavailable"],
                cot_log="LLM call failed; defaulting to HOLD.",
            ),
        )

        results[ticker] = signal.model_dump()
        progress.update_status(
            f"investor_{agent_key}", ticker,
            f"{signal.signal} | conviction {signal.conviction}/10"
        )

    return results
