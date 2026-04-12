"""
Phase 6 — Debate Round

What it does:
- Checks if there is genuine disagreement: ≥3 agents BUY AND ≥3 agents SELL/SHORT
  on the same ticker. If not, skips.
- When triggered:
  1. Picks the highest-conviction BUY agent and highest-conviction SELL/SHORT agent
  2. Presents agent A's full cot_log to agent B and asks for a 2-3 sentence rebuttal
  3. Presents agent B's full cot_log to agent A and asks for a rebuttal
  4. Asks the moderator LLM to adjudicate: which argument is more logically consistent?
  5. Outputs an adjudicated signal + conviction that the Portfolio Manager uses
     alongside (not instead of) the individual signals

Why this matters:
- Without debate, a 7-BUY vs 5-SELL split just becomes a weighted average
- With debate, the moderator identifies the CORE disagreement (valuation? macro? quality?)
  and forces each side to address the strongest counter-argument
- The adjudication output feeds into the Portfolio Manager as an additional signal
  with a boosted weight (reflecting that it synthesises two prior signals)
"""

from langchain_core.prompts import ChatPromptTemplate

from src.data.models import DebateResult
from src.graph.state import AgentState
from src.utils.llm import call_llm
from src.utils.progress import progress

DEBATE_SYSTEM_PROMPT = """
You are the Debate Moderator for an investment committee.

Two investor agents have reached opposite conclusions on the same stock.
Your job: force each agent to rebut the other's strongest argument.

Process:
1. Identify the core disagreement (valuation? growth rate? macro? accounting quality? moat?)
2. Present each agent's thesis to the opposing agent (done for you in the human turn)
3. Each agent responds with their counter-argument (2-3 sentences max per rebuttal)
4. You adjudicate: which argument is more logically consistent with the evidence?
5. Output an adjudicated signal and conviction score for the Portfolio Manager

Be rigorous. Do not simply split the difference. Pick the stronger argument.
Output JSON only.
""".strip()


def should_trigger_debate(analyst_signals: dict, tickers: list[str]) -> bool:
    """Return True if any ticker has ≥3 BUY AND ≥3 SELL/SHORT signals."""
    for ticker in tickers:
        buy_count = 0
        sell_count = 0
        for agent_key, signals in analyst_signals.items():
            if not isinstance(signals, dict):
                continue
            if ticker not in signals:
                continue
            sig = signals[ticker].get("signal", "")
            if sig in ("BUY",):
                buy_count += 1
            elif sig in ("SELL", "SHORT"):
                sell_count += 1
        if buy_count >= 3 and sell_count >= 3:
            return True
    return False


def _pick_top_agent(signals: dict, tickers: list[str], side: list[str]) -> tuple[str, str, dict] | None:
    """Pick the agent with the highest conviction on the given signal side."""
    best_agent = None
    best_ticker = None
    best_signal = None
    best_conviction = -1

    for agent_key, agent_signals in signals.items():
        if not isinstance(agent_signals, dict):
            continue
        for ticker in tickers:
            if ticker not in agent_signals:
                continue
            sig_data = agent_signals[ticker]
            if sig_data.get("signal") in side:
                conv = sig_data.get("conviction", 0)
                if conv > best_conviction:
                    best_conviction = conv
                    best_agent = agent_key
                    best_ticker = ticker
                    best_signal = sig_data

    if best_agent:
        return best_agent, best_ticker, best_signal
    return None


def run_debate_round(state: AgentState) -> AgentState:
    """Phase 6: adversarial debate between the strongest bull and bear."""
    agent_id = "debate_round"
    tickers = state["data"]["tickers"]
    analyst_signals = state["data"]["analyst_signals"]

    debate_results: dict[str, object] = {}

    for ticker in tickers:
        # Find highest-conviction bull and bear for this ticker
        ticker_signals = {
            k: {ticker: v[ticker]}
            for k, v in analyst_signals.items()
            if isinstance(v, dict) and ticker in v
        }

        bull = _pick_top_agent(ticker_signals, [ticker], ["BUY"])
        bear = _pick_top_agent(ticker_signals, [ticker], ["SELL", "SHORT"])

        if not bull or not bear:
            debate_results[ticker] = None
            continue

        bull_agent, _, bull_signal = bull
        bear_agent, _, bear_signal = bear

        progress.update_status(
            agent_id, ticker,
            f"Debating: {bull_agent} (BUY {bull_signal.get('conviction')}) "
            f"vs {bear_agent} ({bear_signal.get('signal')} {bear_signal.get('conviction')})"
        )

        bull_cot = bull_signal.get("cot_log", bull_signal.get("thesis_summary", ""))
        bear_cot = bear_signal.get("cot_log", bear_signal.get("thesis_summary", ""))
        bull_summary = bull_signal.get("thesis_summary", "")
        bear_summary = bear_signal.get("thesis_summary", "")

        template = ChatPromptTemplate.from_messages([
            ("system", DEBATE_SYSTEM_PROMPT),
            ("human", (
                "Ticker: {ticker}\n\n"
                "=== AGENT A: {bull_agent} (signal: BUY, conviction: {bull_conv}/10) ===\n"
                "Thesis: {bull_summary}\n"
                "Full reasoning: {bull_cot}\n\n"
                "=== AGENT B: {bear_agent} (signal: {bear_sig}, conviction: {bear_conv}/10) ===\n"
                "Thesis: {bear_summary}\n"
                "Full reasoning: {bear_cot}\n\n"
                "Output format:\n"
                '{{\n'
                '  "disagreement_core": "What is the fundamental disagreement?",\n'
                '  "agent_a": "{bull_agent}",\n'
                '  "agent_b": "{bear_agent}",\n'
                '  "agent_a_rebuttal": "Agent A rebuttal to B (2-3 sentences)",\n'
                '  "agent_b_rebuttal": "Agent B rebuttal to A (2-3 sentences)",\n'
                '  "adjudication": "Which argument wins and why (3-4 sentences)",\n'
                '  "adjudicated_signal": "BUY"|"SELL"|"HOLD",\n'
                '  "adjudicated_conviction": 1-10\n'
                "}}"
            )),
        ])

        prompt = template.invoke({
            "ticker": ticker,
            "bull_agent": bull_agent,
            "bull_conv": bull_signal.get("conviction", 5),
            "bull_summary": bull_summary[:500],
            "bull_cot": bull_cot[:1500],
            "bear_agent": bear_agent,
            "bear_sig": bear_signal.get("signal"),
            "bear_conv": bear_signal.get("conviction", 5),
            "bear_summary": bear_summary[:500],
            "bear_cot": bear_cot[:1500],
        })

        result: DebateResult = call_llm(
            prompt=prompt,
            pydantic_model=DebateResult,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: DebateResult(
                disagreement_core="Valuation vs. growth",
                agent_a=bull_agent,
                agent_b=bear_agent,
                agent_a_rebuttal="Unable to generate rebuttal.",
                agent_b_rebuttal="Unable to generate rebuttal.",
                adjudication="Defaulting to HOLD on debate failure.",
                adjudicated_signal="HOLD",
                adjudicated_conviction=5,
            ),
        )

        debate_results[ticker] = result.model_dump()
        progress.update_status(
            agent_id, ticker,
            f"Adjudicated: {result.adjudicated_signal} conviction {result.adjudicated_conviction}"
        )

    state["data"]["debate_result"] = debate_results
    return state
