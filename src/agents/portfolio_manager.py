import json
import time
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from src.graph.state import AgentState, show_agent_reasoning
from pydantic import BaseModel, Field
from typing_extensions import Literal
from src.utils.progress import progress
from src.utils.llm import call_llm


class PortfolioDecision(BaseModel):
    action: Literal["buy", "sell", "short", "cover", "hold"]
    quantity: int = Field(description="Number of shares to trade")
    confidence: int = Field(description="Confidence 0-100")
    reasoning: str = Field(description="Reasoning for the decision")


class PortfolioManagerOutput(BaseModel):
    decisions: dict[str, PortfolioDecision] = Field(description="Dictionary of ticker to trading decisions")


##### Portfolio Management Agent #####
def portfolio_management_agent(state: AgentState, agent_id: str = "portfolio_manager"):
    """Makes final trading decisions and generates orders for multiple tickers"""

    portfolio = state["data"]["portfolio"]
    analyst_signals = state["data"]["analyst_signals"]
    tickers = state["data"]["tickers"]

    position_limits = {}
    current_prices = {}
    max_shares = {}
    signals_by_ticker = {}
    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Processing analyst signals")

        # Find the corresponding risk manager for this portfolio manager
        if agent_id.startswith("portfolio_manager_"):
            suffix = agent_id.split('_')[-1]
            risk_manager_id = f"risk_management_agent_{suffix}"
        else:
            risk_manager_id = "risk_management_agent"  # Fallback for CLI

        risk_data = analyst_signals.get(risk_manager_id, {}).get(ticker, {})
        position_limits[ticker] = risk_data.get("remaining_position_limit", 0.0)
        current_prices[ticker] = float(risk_data.get("current_price", 0.0))

        # Calculate maximum shares allowed based on position limit and price
        if current_prices[ticker] > 0:
            max_shares[ticker] = int(position_limits[ticker] // current_prices[ticker])
        else:
            max_shares[ticker] = 0

        # Compress analyst signals to {sig, conf}
        ticker_signals = {}
        for agent, signals in analyst_signals.items():
            if not agent.startswith("risk_management_agent") and ticker in signals:
                sig = signals[ticker].get("signal")
                conf = signals[ticker].get("confidence")
                if sig is not None and conf is not None:
                    ticker_signals[agent] = {"sig": sig, "conf": conf}
        signals_by_ticker[ticker] = ticker_signals

    state["data"]["current_prices"] = current_prices

    progress.update_status(agent_id, None, "Generating trading decisions")

    result = generate_trading_decision(
        tickers=tickers,
        signals_by_ticker=signals_by_ticker,
        current_prices=current_prices,
        max_shares=max_shares,
        portfolio=portfolio,
        agent_id=agent_id,
        state=state,
    )
    message = HumanMessage(
        content=json.dumps({ticker: decision.model_dump() for ticker, decision in result.decisions.items()}),
        name=agent_id,
    )

    if state["metadata"]["show_reasoning"]:
        show_agent_reasoning({ticker: decision.model_dump() for ticker, decision in result.decisions.items()},
                             "Portfolio Manager")

    progress.update_status(agent_id, None, "Done")

    return {
        "messages": state["messages"] + [message],
        "data": state["data"],
    }


def compute_allowed_actions(
        tickers: list[str],
        current_prices: dict[str, float],
        max_shares: dict[str, int],
        portfolio: dict[str, float],
) -> dict[str, dict[str, int]]:
    """Compute allowed actions and max quantities for each ticker deterministically."""
    allowed = {}
    cash = float(portfolio.get("cash", 0.0))
    positions = portfolio.get("positions", {}) or {}
    margin_requirement = float(portfolio.get("margin_requirement", 0.5))
    margin_used = float(portfolio.get("margin_used", 0.0))
    equity = float(portfolio.get("equity", cash))

    for ticker in tickers:
        price = float(current_prices.get(ticker, 0.0))
        pos = positions.get(
            ticker,
            {"long": 0, "long_cost_basis": 0.0, "short": 0, "short_cost_basis": 0.0},
        )
        long_shares = int(pos.get("long", 0) or 0)
        short_shares = int(pos.get("short", 0) or 0)
        max_qty = int(max_shares.get(ticker, 0) or 0)

        # Start with zeros
        actions = {"buy": 0, "sell": 0, "short": 0, "cover": 0, "hold": 0}

        # Long side
        if long_shares > 0:
            actions["sell"] = long_shares
        if cash > 0 and price > 0:
            max_buy_cash = int(cash // price)
            max_buy = max(0, min(max_qty, max_buy_cash))
            if max_buy > 0:
                actions["buy"] = max_buy

        # Short side
        if short_shares > 0:
            actions["cover"] = short_shares
        if price > 0 and max_qty > 0:
            if margin_requirement <= 0.0:
                # If margin requirement is zero or unset, only cap by max_qty
                max_short = max_qty
            else:
                available_margin = max(0.0, (equity / margin_requirement) - margin_used)
                max_short_margin = int(available_margin // price)
                max_short = max(0, min(max_qty, max_short_margin))
            if max_short > 0:
                actions["short"] = max_short

        # Hold always valid
        actions["hold"] = 0

        # Prune zero-capacity actions to reduce tokens, keep hold
        pruned = {"hold": 0}
        for k, v in actions.items():
            if k != "hold" and v > 0:
                pruned[k] = v

        allowed[ticker] = pruned

    return allowed


def _compact_signals(signals_by_ticker: dict[str, dict]) -> dict[str, dict]:
    """Keep only {agent: {sig, conf}} and drop empty agents."""
    out = {}
    for t, agents in signals_by_ticker.items():
        if not agents:
            out[t] = {}
            continue
        compact = {}
        for agent, payload in agents.items():
            sig = payload.get("sig") or payload.get("signal")
            conf = payload.get("conf") if "conf" in payload else payload.get("confidence")
            if sig is not None and conf is not None:
                compact[agent] = {"sig": sig, "conf": conf}
        out[t] = compact
    return out


def generate_trading_decision(
        tickers: list[str],
        signals_by_ticker: dict[str, dict],
        current_prices: dict[str, float],
        max_shares: dict[str, int],
        portfolio: dict[str, float],
        agent_id: str,
        state: AgentState,
) -> PortfolioManagerOutput:
    """Get decisions from the LLM with deterministic constraints and a minimal prompt."""

    # Deterministic constraints
    allowed_actions_full = compute_allowed_actions(tickers, current_prices, max_shares, portfolio)

    # Pre-fill pure holds to avoid sending them to the LLM at all
    prefilled_decisions: dict[str, PortfolioDecision] = {}
    tickers_for_llm: list[str] = []
    for t in tickers:
        aa = allowed_actions_full.get(t, {"hold": 0})
        # If only 'hold' key exists, there is no trade possible
        if set(aa.keys()) == {"hold"}:
            prefilled_decisions[t] = PortfolioDecision(
                action="hold", quantity=0, confidence=100.0, reasoning="No valid trade available"
            )
        else:
            tickers_for_llm.append(t)

    if not tickers_for_llm:
        return PortfolioManagerOutput(decisions=prefilled_decisions)

    # Build compact payloads only for tickers sent to LLM
    compact_signals = _compact_signals({t: signals_by_ticker.get(t, {}) for t in tickers_for_llm})
    compact_allowed = {t: allowed_actions_full[t] for t in tickers_for_llm}

    # Minimal prompt template
    template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a portfolio manager.\n"
                "Inputs per ticker: analyst signals and allowed actions with max qty (already validated).\n"
                "Pick one allowed action per ticker and a quantity ≤ the max. "
                "Keep reasoning very concise (max 100 chars). No cash or margin math. Return JSON only."
            ),
            (
                "human",
                "Signals:\n{signals}\n\n"
                "Allowed:\n{allowed}\n\n"
                "Format:\n"
                "{{\n"
                '  "decisions": {{\n'
                '    "TICKER": {{"action":"...","quantity":int,"confidence":int,"reasoning":"..."}}\n'
                "  }}\n"
                "}}"
            ),
        ]
    )

    prompt_data = {
        "signals": json.dumps(compact_signals, separators=(",", ":"), ensure_ascii=False),
        "allowed": json.dumps(compact_allowed, separators=(",", ":"), ensure_ascii=False),
    }
    prompt = template.invoke(prompt_data)

    # Default factory fills remaining tickers as hold if the LLM fails
    def create_default_portfolio_output():
        # start from prefilled
        decisions = dict(prefilled_decisions)
        for t in tickers_for_llm:
            decisions[t] = PortfolioDecision(
                action="hold", quantity=0, confidence=0.0, reasoning="Default decision: hold"
            )
        return PortfolioManagerOutput(decisions=decisions)

    llm_out = call_llm(
        prompt=prompt,
        pydantic_model=PortfolioManagerOutput,
        agent_name=agent_id,
        state=state,
        default_factory=create_default_portfolio_output,
    )

    # Merge prefilled holds with LLM results
    merged = dict(prefilled_decisions)
    merged.update(llm_out.decisions)
    return PortfolioManagerOutput(decisions=merged)


# ---------------------------------------------------------------------------
# Phase 9 — Advanced Conviction-Weighted Portfolio Manager
# ---------------------------------------------------------------------------

def _compute_weighted_signal(ticker: str, state) -> float:
    """
    Weighted Signal = Σ(signal × conviction × agent_weight × regime_weight) / Σ weights
    Returns a float in [-10, +10]: positive = bullish, negative = bearish.
    """
    signals = state["data"].get("analyst_signals", {})
    regime_weights = state["data"].get("agent_weight_multipliers", {})
    conviction_weights = state["data"].get("conviction_weights", {})

    SIGNAL_MAP = {"BUY": 1, "HOLD": 0, "SELL": -1, "SHORT": -1, "COVER": 0}

    numerator = 0.0
    denominator = 0.0

    skip_agents = {"risk_management_agent", "advanced_risk_manager"}

    for agent_key, agent_signals in signals.items():
        if agent_key in skip_agents:
            continue
        if not isinstance(agent_signals, dict) or ticker not in agent_signals:
            continue
        sig = agent_signals[ticker]
        if not isinstance(sig, dict):
            continue

        signal_str = sig.get("signal", "HOLD")
        signal_val = SIGNAL_MAP.get(signal_str, 0)
        conviction = sig.get("conviction", sig.get("confidence", 50))
        if conviction > 10:
            conviction = conviction / 10  # normalise 0-100 to 0-10

        # Derive short agent key for weight lookup (e.g. "warren_buffett_agent" → "buffett")
        short_key = agent_key.replace("_agent", "").replace("warren_", "").replace("ben_", "").replace("charlie_", "").replace("stanley_", "").replace("michael_", "").replace("mohnish_", "").replace("peter_", "").replace("phil_", "").replace("rakesh_", "").replace("bill_", "").replace("aswath_", "").replace("cathie_", "")

        regime_w = regime_weights.get(short_key, regime_weights.get(agent_key, 1.0))
        track_w = conviction_weights.get(short_key, conviction_weights.get(agent_key, 1.0))
        w = regime_w * track_w

        numerator += signal_val * conviction * w
        denominator += w

    # Also factor in debate adjudication if present (with boosted weight of 2.0)
    debate_result = state["data"].get("debate_result", {}).get(ticker)
    if debate_result and isinstance(debate_result, dict):
        adj_sig = debate_result.get("adjudicated_signal", "HOLD")
        adj_conv = debate_result.get("adjudicated_conviction", 5)
        adj_val = SIGNAL_MAP.get(adj_sig, 0)
        debate_weight = 2.0
        numerator += adj_val * adj_conv * debate_weight
        denominator += debate_weight

    return numerator / denominator if denominator > 0 else 0.0


def run_advanced_portfolio_manager(state) -> dict:
    """
    Phase 9: conviction-weighted final decision.

    Position Size = approved_size × (ev_upside/100) × (power_law_score/10)
    Halved if Value Trap verdict is HIGH.
    Action derived from weighted signal score threshold.
    LLM writes the 3-4 sentence rationale.
    """
    import json
    from langchain_core.messages import HumanMessage
    from src.data.models import AdvancedPortfolioDecision
    from src.utils.llm import call_llm
    from langchain_core.prompts import ChatPromptTemplate
    from src.utils.progress import progress

    agent_id = "advanced_portfolio_manager"
    tickers = state["data"]["tickers"]

    decisions: dict[str, dict] = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Computing conviction-weighted signal")

        weighted_signal = _compute_weighted_signal(ticker, state)

        # Map signal score to action
        if weighted_signal >= 4:
            action = "BUY"
        elif weighted_signal <= -4:
            action = "SELL"
        elif weighted_signal <= -2:
            action = "SHORT"
        else:
            action = "HOLD"

        # Position sizing formula
        risk_data = state["data"].get("analyst_signals", {}).get(
            "advanced_risk_manager", {}
        ).get(ticker, {})
        approved_size_pct = risk_data.get("approved_size_pct", 0.05)

        scenario = state["data"].get("scenario_analysis", {}).get(ticker, {})
        ev_upside = scenario.get("upside_pct", 0.0)
        current_price = scenario.get("current_price", 0.0)
        expected_value = scenario.get("expected_value", 0.0)

        power_law = state["data"].get("power_law_analysis", {}).get(ticker, {})
        power_score = power_law.get("total_score", 5)

        trap = state["data"].get("value_trap_analysis", {}).get(ticker, {})
        trap_verdict = trap.get("overall_verdict", "TRAP RISK LOW")

        # Core formula — three normalised factors, each in [0, 1]:
        # 1. EV upside: cap at ±50% so LLM-optimistic upsides don't saturate formula
        ev_factor = min(abs(ev_upside), 50.0) / 50.0
        # 2. Power Law score: already 1-10, normalise to [0.1, 1.0]
        power_factor = power_score / 10.0
        # 3. Weighted signal strength: computed from all investor agents, range [-10, +10]
        signal_factor = min(abs(weighted_signal), 10.0) / 10.0

        if ev_upside > 0 and action == "BUY":
            size_pct = approved_size_pct * ev_factor * power_factor * signal_factor
        elif action in ("SELL", "SHORT"):
            # Fix 1b: size driven by signal + power law even when scenario EV is flat.
            # Previously: ev_upside=0 → ev_factor=0 → size_pct=0 (silent zero).
            # Now: if no measurable downside use 0.5 as a neutral ev proxy so
            # signal strength and power law score still produce a non-zero size.
            _sell_ev = min(abs(ev_upside), 50.0) / 50.0 if ev_upside < 0 else 0.5
            size_pct = approved_size_pct * _sell_ev * power_factor * signal_factor
            # Minimum floor: actionable SELL/SHORT must always show ≥20% of approved
            size_pct = max(size_pct, approved_size_pct * 0.20)
        else:
            # HOLD or misaligned signal — scale down but keep signal/power influence
            # Bug 2 fix: when EV upside is large (>50%) even a HOLD warrants more than
            # the flat 0.5 haircut — use ev_factor at 75% cap rather than discarding it
            if ev_upside > 50.0:
                hold_factor = min(ev_factor * 0.75, 0.75)
                size_pct = approved_size_pct * hold_factor * power_factor * signal_factor
            else:
                size_pct = approved_size_pct * 0.5 * power_factor * signal_factor

        if trap_verdict == "TRAP RISK HIGH":
            size_pct *= 0.5

        # Cap at approved_size_pct (already incorporates sector caps and macro regime cap
        # from Phase 8 risk manager — never exceed what risk approved)
        size_pct = min(size_pct, approved_size_pct)
        size_pct = max(size_pct, 0.0)

        # HOLD with 0% size means "no new position" — keep as HOLD (no position opened).
        # PASS is not a valid AdvancedPortfolioDecision action; HOLD covers this case.

        # Stop loss: 10% below current for longs/holds, 10% above for shorts/sells
        stop_loss = current_price * 0.90 if action in ("BUY", "HOLD") else current_price * 1.10

        # ── §7/§11 Framework: use forward-multiple 12m price target when available ──
        # This separates market pricing (§7 forward multiples) from intrinsic value (§6 DCF/blend).
        # Fallback chain: 12m_price_target → scenario EV → bull/bear fair value
        _12m_pt = scenario.get("12m_price_target")
        bear_fv = scenario.get("bear", {}).get("fair_value", expected_value)
        bull_fv = scenario.get("bull", {}).get("fair_value", expected_value)
        if action in ("SELL", "SHORT"):
            # Fix 1a: guard against 0.0 bear_fv masking as falsy in `or` chain.
            # Use explicit truthiness check so a genuine non-zero bear target is kept.
            _bear_anchor = (
                bear_fv if (isinstance(bear_fv, (int, float)) and bear_fv > 0)
                else (current_price * 0.80 if current_price > 0 else None)
            )
            price_target = (
                _12m_pt if (isinstance(_12m_pt, (int, float)) and _12m_pt > 0)
                else _bear_anchor
            )
        else:
            price_target = _12m_pt or expected_value

        # ── §11 Directional consistency check (CHECK #1 logic gap) ───────────────
        # Problem: investor consensus can be BUY even when EV < current price, because
        # some investors use long time horizons while the scenario agent uses near-term data.
        # Resolution:
        #   (a) If 12m PT AND blended IV are both below current price → BUY is inconsistent → HOLD
        #   (b) If only EV < current but bull case exceeds it → keep BUY, use bull target
        #   (c) Add a flag so the PDF/editor agents can explain the gap (§8 Reconciliation)
        recon = scenario.get("reconciliation", {})
        _blended_iv = recon.get("blended_iv") or expected_value
        _directional_flag: str = ""
        if action == "BUY" and current_price > 0:
            if (price_target or 0) < current_price * 0.95:
                if bull_fv and bull_fv > current_price:
                    # Bull case still above current — use bull target, keep BUY
                    price_target = bull_fv
                    _directional_flag = (
                        f"⚠ 12m PT (${(_12m_pt or expected_value):.2f}) below current price "
                        f"(${current_price:.2f}); using bull-case IV (${bull_fv:.2f}) as target. "
                        "Upside is conditional on bull-scenario realisation."
                    )
                else:
                    # Neither 12m PT nor bull case exceeds current price → downgrade to HOLD
                    action = "HOLD"
                    _directional_flag = (
                        f"⚠ BUY downgraded to HOLD: 12m PT (${(price_target or 0):.2f}) and "
                        f"bull-case IV (${bull_fv:.2f}) both below current price (${current_price:.2f}). "
                        "Investor consensus is bullish but near-term valuation does not support entry."
                    )
                    # Use bear case as stop-loss reference, keep target at bull (downside-risk frame)
                    price_target = expected_value

        # ── Bug 1 fix: Stop/Target directional guard ─────────────────────────
        # For long positions (BUY/HOLD), price_target must always exceed stop_loss.
        # The 12m forward-multiple PT can legitimately be below current price (multiple
        # compression), but using it as the price_target while the stop is 10% below
        # entry creates an inverted trade that can never reach target.
        # Resolution: if 12m PT < stop_loss, override target with long-term EV (DCF).
        if action in ("BUY", "HOLD") and current_price > 0 and stop_loss > 0:
            if (price_target or 0) <= stop_loss:
                _pt_override = expected_value if (expected_value or 0) > current_price else (bull_fv or expected_value or current_price * 1.10)
                _pt_override = _pt_override or current_price * 1.10
                _directional_flag = (
                    (_directional_flag + " | " if _directional_flag else "")
                    + f"⚠ 12m fwd-multiple PT (${(_12m_pt or price_target or 0):.2f}) ≤ stop-loss "
                    f"(${stop_loss:.2f}); target overridden to DCF intrinsic EV (${_pt_override:.2f}). "
                    "Near-term multiples compressing but long-term DCF supports upside."
                )
                price_target = round(_pt_override, 2)

        # Store flag in scenario dict for downstream agents (editor, auditor, PDF)
        if _directional_flag:
            scenario["directional_consistency_flag"] = _directional_flag
            state["data"].setdefault("consistency_flags", {})[ticker] = _directional_flag

        progress.update_status(agent_id, ticker, "Generating rationale via LLM")

        # ── Build narrative context for the rationale LLM ─────────────────
        # Extract the key catalyst/theme from scenario assumptions and top
        # investor theses so the rationale reads like a human portfolio note,
        # not a formula dump.
        _base_assumptions = str(
            scenario.get("base", {}).get("assumptions", "")
        )[:300]
        _bull_assumptions = str(
            scenario.get("bull", {}).get("assumptions", "")
        )[:200]
        # Top 2 investor signals by conviction (strongest thesis voices)
        _all_signals = state["data"].get("analyst_signals", {})
        _investor_theses = []
        for _agent_key, _agent_data in _all_signals.items():
            _td = _agent_data.get(ticker, {})
            if isinstance(_td, dict) and _td.get("signal") and _td.get("confidence"):
                _investor_theses.append((
                    _agent_key, _td.get("signal"), _td.get("confidence", 0),
                    str(_td.get("reasoning", ""))[:150]
                ))
        _investor_theses.sort(key=lambda x: x[2], reverse=True)
        _top_theses = "\n".join(
            f"  {t[0]}: {t[1]} (conv {t[2]}/10) — {t[3]}"
            for t in _investor_theses[:2]
        ) or "No investor theses available"

        debate_summary = str(
            state["data"].get("debate_result", {}).get(ticker, "No debate")
        )[:300]

        # LLM writes the rationale as 3 structured bullet points
        template = ChatPromptTemplate.from_messages([
            ("system",
                "You are a senior portfolio advisor. Write the rationale as EXACTLY 3 bullet "
                "points (• prefix). Each bullet is 1-2 lines max, proper English, no jargon.\n"
                "Bullet 1: State the single dominant theme driving the stock right now.\n"
                "Bullet 2: Your view — is this priced in, sustainable, or at risk?\n"
                "Bullet 3: Price target, structural moat quality, and primary risk.\n"
                "Do NOT label the bullets with headings. Just write naturally.\n"
                "Output JSON only."
            ),
            ("human", (
                "Ticker: {ticker} | Action: {action} | Size: {size_pct:.1%}\n"
                "Signal: {ws:.1f}/10 | EV upside: {ev:.1f}% | Power Law: {pl}/10 | Trap: {trap}\n"
                "Base scenario: {base_assumptions}\n"
                "Bull catalyst: {bull_assumptions}\n"
                "Top theses:\n{top_theses}\n\n"
                "Output:\n"
                '{{\n'
                '  "action": "{action}",\n'
                '  "position_size_pct": {size_pct},\n'
                '  "entry_range": [float, float],\n'
                '  "stop_loss": {stop_loss},\n'
                '  "price_target": {price_target},\n'
                '  "time_horizon": "short"|"medium"|"long",\n'
                '  "rationale": "• ...\\n• ...\\n• ..."\n'
                "}}"
            )),
        ])

        prompt = template.invoke({
            "ticker": ticker,
            "action": action,
            "size_pct": size_pct,
            "ws": weighted_signal,
            "ev": ev_upside,
            "pl": power_score,
            "trap": trap_verdict,
            "base_assumptions": _base_assumptions,
            "bull_assumptions": _bull_assumptions,
            "top_theses": _top_theses,
            "stop_loss": stop_loss,
            "price_target": price_target,
        })

        decision: AdvancedPortfolioDecision = call_llm(
            prompt=prompt,
            pydantic_model=AdvancedPortfolioDecision,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: AdvancedPortfolioDecision(
                action=action,
                position_size_pct=size_pct,
                entry_range=[current_price * 0.98, current_price * 1.02],
                stop_loss=stop_loss,
                price_target=price_target,
                time_horizon="medium",
                rationale="Default decision due to LLM failure.",
            ),
        )

        d = decision.model_dump()
        # Pin deterministic values — the LLM sometimes misinterprets the
        # position_size_pct format (e.g. returns 7.5 instead of 0.075).
        # Python-computed values always win over LLM interpretation.
        d["action"] = action
        d["position_size_pct"] = size_pct
        d["stop_loss"] = stop_loss
        d["price_target"] = price_target
        # Compatibility shims so print_trading_output() works with either pipeline
        d.setdefault("confidence", round(size_pct * 100, 1))
        d.setdefault("reasoning", d.get("rationale", ""))
        d.setdefault("quantity", 0)
        decisions[ticker] = d

    message = HumanMessage(
        content=json.dumps(decisions),
        name=agent_id,
    )

    return {
        "messages": state["messages"] + [message],
        "data": state["data"],
        "decisions": decisions,
    }
