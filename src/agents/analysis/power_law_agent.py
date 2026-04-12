"""
Phase 7b — Power Law Agent

What it does:
- Scores the company 1-10 on five category-leadership dimensions (0-2 pts each):
    scale_economies | network_effects | winner_take_most | switching_costs | data_ip_moat
- Interprets the score:
    8-10 → category king, premium multiple appropriate
    5-7  → solid compounder, market-rate multiple
    <5   → commodity risk, discount or avoid
- The score feeds into the Portfolio Manager's position sizing formula:
    position_size = approved_size × (ev_upside/100) × (power_law_score/10)
  So a score of 10 keeps full position size; score of 5 halves it; score of 2 cuts it by 80%

Why this matters:
- Standard DCF and ratio analysis doesn't capture winner-take-most dynamics
- A company with score 9 deserves a larger bet than one with score 4,
  even if their near-term P/E looks identical
"""

from langchain_core.prompts import ChatPromptTemplate

from src.data.models import PowerLawOutput
from src.graph.state import AgentState
from src.utils.llm import call_llm
from src.utils.progress import progress

SYSTEM_PROMPT = """
You are the Power Law Agent. Score the company under analysis 1-10 on category
leadership potential based exclusively on the evidence in the Industry Brief,
Moat Analysis (2C), and Competitive Landscape (2B) provided below.

Score each dimension 0-2 points:
- Scale economies: does unit cost fall as volume grows?
- Network effects: does value increase with more users?
- Winner-take-most dynamics: is this a concentrated market?
- Switching costs: how painful is it to leave?
- Data/IP moat: proprietary assets that compound over time?

For EACH dimension write TWO fields:
  "_note"    — one sentence (≤20 words) citing a specific stat or fact that
               supports whatever strength exists. Even a score-0 dimension may
               have a partial positive — cite it. If truly none exists, write
               "No positive evidence found in provided research."
  "_concern" — one sentence (≤20 words) citing the specific risk, gap, or
               caveat for this dimension. Even a score-2 dimension has a risk
               (e.g. "advantage could erode if X"). Always provide a concern.

Examples of the specificity required:
  note:    "Holds 46% China e-commerce GMV; top-3 control 85% of market."
  concern: "Rival platforms gained 4pp share YoY; concentration is reversing."

  note:    "900M buyer base drives cross-side network; merchant churn <5%."
  concern: "Network effects are single-geography; limited international leverage."

Do NOT invent figures. Do NOT reference other companies by name.
If the research lacks evidence, write "Insufficient evidence in provided research."

Total score /10. Interpretation label:
8-10: Potential category king. Apply premium multiple.
5-7: Solid compounder. Market-rate multiple appropriate.
<5: Commodity risk. Apply discount or avoid.

CRITICAL — COMPANY SPECIFICITY:
- Every sentence must refer specifically to the ticker being analysed.
- Evidence must come solely from the provided sections, not training knowledge.

Output JSON only.
""".strip()


def run_power_law_agent(state: AgentState) -> AgentState:
    """Phase 7b: category leadership scoring."""
    agent_id = "power_law_agent"
    tickers = state["data"]["tickers"]
    industry_brief = state["data"].get("industry_brief", "")
    sector = state["data"].get("sector", "Tech")
    dr_sections = state["data"].get("deep_research_sections", {})
    # 2C (moat analysis) is the primary input for category leadership scoring
    # 2B (competitive landscape) informs winner-take-most and switching cost scores
    moat_section        = dr_sections.get("2c", "")
    competitive_section = dr_sections.get("2b", "")

    power_law_results: dict[str, object] = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Scoring category leadership")

        routing_decision = state["data"].get("routing_decision", {})

        template = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", (
                "Company under analysis: {ticker}  |  Sector: {sector}\n"
                "REMINDER: All scoring and justification must be specific to {ticker}. "
                "Do not mention or compare to any other company.\n\n"
                "Industry Intelligence Brief:\n{brief}\n\n"
                "2C — Moat Analysis (primary input — moat type, evidence, direction, stress test):\n{moat}\n\n"
                "2B — Competitive Landscape (informs winner-take-most and switching cost scores):\n{competitive}\n\n"
                "Routing context:\n{routing}\n\n"
                "Output format:\n"
                '{{\n'
                '  "total_score": int (1-10),\n'
                '  "scale_economies": int (0-2),\n'
                '  "scale_economies_note": "≤20-word positive evidence with specific stat",\n'
                '  "scale_economies_concern": "≤20-word specific risk or caveat",\n'
                '  "network_effects": int (0-2),\n'
                '  "network_effects_note": "≤20-word positive evidence with specific stat",\n'
                '  "network_effects_concern": "≤20-word specific risk or caveat",\n'
                '  "winner_take_most": int (0-2),\n'
                '  "winner_take_most_note": "≤20-word positive evidence with specific stat",\n'
                '  "winner_take_most_concern": "≤20-word specific risk or caveat",\n'
                '  "switching_costs": int (0-2),\n'
                '  "switching_costs_note": "≤20-word positive evidence with specific stat",\n'
                '  "switching_costs_concern": "≤20-word specific risk or caveat",\n'
                '  "data_ip_moat": int (0-2),\n'
                '  "data_ip_moat_note": "≤20-word positive evidence with specific stat",\n'
                '  "data_ip_moat_concern": "≤20-word specific risk or caveat",\n'
                '  "interpretation": "category king | solid compounder | commodity risk",\n'
                '  "multiple_implication": "one sentence on multiple premium/discount implied for {ticker}"\n'
                "}}"
            )),
        ])

        prompt = template.invoke({
            "ticker": ticker,
            "sector": sector,
            "brief": industry_brief[:25000],
            "moat": moat_section[:2000] if moat_section else "Not available.",
            "competitive": competitive_section[:1000] if competitive_section else "Not available.",
            "routing": str(routing_decision)[:500],
        })

        result: PowerLawOutput = call_llm(
            prompt=prompt,
            pydantic_model=PowerLawOutput,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: PowerLawOutput(
                total_score=5,
                scale_economies=1,
                network_effects=1,
                winner_take_most=1,
                switching_costs=1,
                data_ip_moat=1,
                interpretation="solid compounder",
                multiple_implication="Market-rate multiple appropriate.",
            ),
        )

        power_law_results[ticker] = result.model_dump()
        progress.update_status(agent_id, ticker, f"Score: {result.total_score}/10 — {result.interpretation}")

    state["data"]["power_law_analysis"] = power_law_results
    return state
