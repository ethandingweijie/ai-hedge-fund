"""
Phase 7b — Power Law Agent

What it does:
- Scores the company on five category-leadership dimensions (0-10 each, integer):
    scale_economies | network_effects | winner_take_most | switching_costs | data_ip_moat
- Backend then computes total_score as a weighted mean of the five dimensions
  (NOT an independent LLM output). Weights tilt toward scale economies and
  concentration dynamics per Helmer's 7 Powers framework:
    total_score = round(
        0.25*scale_economies + 0.20*network_effects + 0.20*winner_take_most
      + 0.20*switching_costs + 0.15*data_ip_moat
    )
- Interpretation:
    8-10 → category king, premium multiple appropriate
    6-7.9 → solid compounder, market-rate multiple
    4-5.9 → average, in-line multiple with caveats
    <4   → commodity / eroding, discount or avoid
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

# Helmer-inspired weighting. Scale economies and market concentration are the
# strongest persistence predictors; data/IP is real but more fragile over time.
POWER_LAW_WEIGHTS = {
    "scale_economies":  0.25,
    "network_effects":  0.20,
    "winner_take_most": 0.20,
    "switching_costs":  0.20,
    "data_ip_moat":     0.15,
}


def _compute_total_score(dims: dict[str, int]) -> int:
    """Weighted mean of the five dimensions, rounded to integer in [0, 10]."""
    total = sum(POWER_LAW_WEIGHTS[k] * float(dims.get(k, 0)) for k in POWER_LAW_WEIGHTS)
    return max(0, min(10, round(total)))


def _interpretation_for(score: int) -> str:
    """Map the computed total score back to the UI interpretation label."""
    if score >= 8:
        return "category king"
    if score >= 6:
        return "solid compounder"
    if score >= 4:
        return "average"
    return "commodity risk"


SYSTEM_PROMPT = """
You are the Power Law Agent. Score the company under analysis on category
leadership potential based exclusively on the evidence in the Industry Brief,
Moat Analysis (2C), and Competitive Landscape (2B) provided below.

Score EACH of the five dimensions 0-10 (integer) using these anchors:
  0-1   Absent     — no structural advantage; commodity dynamic
  2-3   Weak       — faint evidence, easily reversed
  4-5   Emerging   — partial advantage, growing but not yet durable
  6-7   Material   — clear, defensible moat with real economics
  8-9   Dominant   — industry-shaping, multi-year lead
  10    Category king — essentially insurmountable; textbook case

Dimensions:
- scale_economies   — does unit cost fall as volume grows?
- network_effects   — does value increase with more users?
- winner_take_most  — is this a concentrated market?
- switching_costs   — how painful is it to leave?
- data_ip_moat      — proprietary assets that compound over time?

Do NOT output a total_score — the backend computes it as a weighted mean of
the five dimension scores. Your job is only to score each dimension honestly
against the anchors above.

For EACH dimension write TWO fields:
  "_note"    — one sentence (≤20 words) citing a specific stat or fact that
               supports whatever strength exists. Even a low-score dimension
               may have a partial positive — cite it. If truly none exists,
               write "No positive evidence found in provided research."
  "_concern" — one sentence (≤20 words) citing the specific risk, gap, or
               caveat for this dimension. Even a 10-score dimension has a risk
               (e.g. "advantage could erode if X"). Always provide a concern.

Examples of the specificity required:
  note:    "Holds 46% China e-commerce GMV; top-3 control 85% of market."
  concern: "Rival platforms gained 4pp share YoY; concentration is reversing."

  note:    "900M buyer base drives cross-side network; merchant churn <5%."
  concern: "Network effects are single-geography; limited international leverage."

Do NOT invent figures. Do NOT reference other companies by name.
If the research lacks evidence, write "Insufficient evidence in provided research."

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
                "Output format (note: total_score is NOT in the output — the backend computes it):\n"
                '{{\n'
                '  "scale_economies": int (0-10),\n'
                '  "scale_economies_note": "≤20-word positive evidence with specific stat",\n'
                '  "scale_economies_concern": "≤20-word specific risk or caveat",\n'
                '  "network_effects": int (0-10),\n'
                '  "network_effects_note": "≤20-word positive evidence with specific stat",\n'
                '  "network_effects_concern": "≤20-word specific risk or caveat",\n'
                '  "winner_take_most": int (0-10),\n'
                '  "winner_take_most_note": "≤20-word positive evidence with specific stat",\n'
                '  "winner_take_most_concern": "≤20-word specific risk or caveat",\n'
                '  "switching_costs": int (0-10),\n'
                '  "switching_costs_note": "≤20-word positive evidence with specific stat",\n'
                '  "switching_costs_concern": "≤20-word specific risk or caveat",\n'
                '  "data_ip_moat": int (0-10),\n'
                '  "data_ip_moat_note": "≤20-word positive evidence with specific stat",\n'
                '  "data_ip_moat_concern": "≤20-word specific risk or caveat",\n'
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
                scale_economies=5,
                network_effects=5,
                winner_take_most=5,
                switching_costs=5,
                data_ip_moat=5,
                interpretation="average",
                multiple_implication="Market-rate multiple appropriate.",
            ),
        )

        # Backend computes the composite instead of trusting the LLM — guarantees
        # total_score is internally consistent with the five dimension scores.
        dims = {
            "scale_economies":  result.scale_economies,
            "network_effects":  result.network_effects,
            "winner_take_most": result.winner_take_most,
            "switching_costs":  result.switching_costs,
            "data_ip_moat":     result.data_ip_moat,
        }
        computed_total = _compute_total_score(dims)
        computed_label = _interpretation_for(computed_total)

        # Override whatever the LLM returned — these are derived fields now.
        result.total_score    = computed_total
        result.interpretation = computed_label

        power_law_results[ticker] = result.model_dump()
        progress.update_status(agent_id, ticker, f"Score: {computed_total}/10 — {computed_label}")

    state["data"]["power_law_analysis"] = power_law_results
    return state
