import json
import os
import re
import time
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from src.graph.state import AgentState, show_agent_reasoning
from pydantic import BaseModel, Field
from typing_extensions import Literal
from src.utils.progress import progress
from src.utils.llm import call_llm


# Tier 2.7 — thesis-density rule for the Summary-tab rationale. Sell-side
# theses open with a handful of numbered themes, each carrying cited figures
# and an implication — not three generic bullets. The constant is module-level
# so the contract test can pin the wording without running the agent.
_PM_RATIONALE_SYSTEM_PROMPT = (
    "You are a senior portfolio advisor. Write the rationale as a numbered "
    "thesis: at most FIVE themes, each prefixed \"1. \", \"2. \", ... and "
    "1-2 lines of plain English, no jargon.\n"
    "Thesis-density rule — every theme MUST:\n"
    "- open with the theme itself (no heading labels),\n"
    "- cite at least TWO specific figures with units (e.g. \"rev +18% y/y\", "
    "\"24x fwd P/E\", \"$15.5B net cash\", \"PT $186\"),\n"
    "- end with the implication for the stock (what it means for the "
    "position).\n"
    "Theme 1 states the single dominant theme driving the stock right now. "
    "Among the themes, cover whether the dominant theme is priced in, "
    "sustainable, or at risk, and cover price target, structural moat "
    "quality, and the primary risk. Use FEWER themes rather than padding — "
    "three dense themes beat five thin ones. Do NOT use bullet glyphs; "
    "number only.\n"
    "Continuity rule — if prior-report context is supplied and this decision "
    "DIFFERS from the prior action, open theme 1 by naming the change and "
    "why (e.g. \"Upgraded from HOLD — ...\"). If the action is unchanged, do "
    "not mention the prior report.\n"
    "Anchor-citation rule — the rationale MUST cite at least ONE quantitative "
    "anchor (blended IV, price target, upside %, WACC, or a multiple) AND at "
    "least ONE qualitative input from the research digest (a dated news "
    "event, a research theme, or a trap/regulatory flag). When a macro-"
    "regime conviction cap is noted in the inputs, cite the regime in one "
    "line.\n"
    "Currency rule — every monetary figure MUST carry its reporting "
    "currency (\"S$61.00\", \"US$305\", \"HK$74\"), never a bare "
    "\"$\" on a non-USD name. Do not describe a currency label as a "
    "risk; if a figure's currency is ambiguous, omit the figure.\n"
    "Valuation-basis rule — exactly one theme MUST state the method and "
    "the inputs behind the price target in the house form: name the "
    "method, the multiple, and the driver (e.g. \"our GGM assumes a "
    "2.51x FY26e P/BV on a 16.6% ROE and an 8.6% cost of equity\"). "
    "Never quote a price target the supplied anchors do not support.\n"
    "Balance rule — carry at least one genuine negative even on a BUY, "
    "and at least one genuine positive even on a SELL.\n"
    "Analyst-thesis rule — when a deposited sell-side thesis is supplied, "
    "engage with it: say where we agree and where we differ, and why. It "
    "is another analyst's argument on a stated date, not a conclusion to "
    "adopt — never restate it as our own, and never treat its price "
    "target as ours. If our call is the opposite of theirs, say so "
    "explicitly and give the reason.\n"
    "Output JSON only."
)

# Profile addendum for banks. A bank thesis is written off different
# primitives than an industrial or a software name: the P&L line that
# matters is total income (net interest income + non-interest income),
# the multiple is P/B against ROE rather than EV/EBITDA against growth,
# and the capital-return story (CET1 headroom, payout, buyback) is a
# first-class part of the thesis rather than a footnote. Mirrors the
# structure of published Singapore bank coverage.
_PM_BANK_RATIONALE_ADDENDUM = """
BANK-SPECIFIC RULES (this name is a bank):
- Use TOTAL INCOME (net interest income + non-interest income) whenever
  you refer to the bank's revenue. Never use gross interest income.
- Value the bank on P/B against ROE - target P/B = (ROE - g) / (CoE - g)
  - and on P/E. NEVER cite EV/EBITDA, EV/Revenue or free cash flow for a
  bank: deposits are not debt, and free cash flow is not a meaningful
  unit of output for a balance-sheet business.
- Cover, where the supplied inputs support it: NIM and its direction,
  cost-income ratio, credit cost in bps against the guided range, NPL
  ratio and coverage, CET1 against target, and the capital-return plan
  (dividend per share, yield, buyback).
- Separate the two halves of the earnings engine: rate-driven net
  interest income versus fee-driven non-interest income (wealth,
  insurance, treasury). State which is carrying growth, and whether
  that is sustainable.
- Where management guidance is supplied, cite it alongside our own
  estimate so the reader can see the gap.
"""


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
# Phase 9 — Advanced Portfolio Manager (committee-free since M2 Track D/E)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# M2 D2 — Committee-free decision primitives.
#
# The 12-persona investor committee is decommissioned: the PM decides from
# qualitative research + quantitative valuation directly. The action comes
# from a deterministic cascade —
#   1. Quantitative band on blended-IV upside (primary mover);
#   2. Qualitative gates (freshness-delta direction, value-trap block,
#      degraded-research cap) that move at most ONE step from the band;
#   3. The existing directional guards (BUY guard, B1 SHORT guard,
#      stop/target override) and the B3 flip backstop.
# Macro regime modulates conviction/sizing only — it never moves the band,
# so valuation keeps a single mover.
# ---------------------------------------------------------------------------

# Bearish → bullish ladder; every gate operates as an index shift on it.
_PM_LADDER = ("SELL", "SHORT", "HOLD", "BUY")

# Tiers that carried a live web search vs accumulated-reuse tiers (mirrors
# deep_research._LIVE_RESEARCH_TIERS + the A2 reuse tiers).
_PM_FRESH_TIERS = ("anthropic_web", "tavily", "qwen_web")
_PM_RECENT_TIERS = ("anthropic_web_cached", "archive_news_delta")


def _pm_band_thresholds() -> tuple[float, float, float]:
    """(buy_above_pct, hold_floor_pct, short_floor_pct), env-overridable.

    Band on reconciliation.upside_to_iv_pct (blended IV vs spot):
      ≥ buy_above        → BUY        (default +15%)
      hold_floor…buy     → HOLD       (default −10%…+15%)
      short_floor…hold   → SHORT      (default −20%…−10%)
      ≤ short_floor      → SELL       (deepest overvaluation, mirrors the
                                        old weighted-signal severity order)
    Read at call time so PM_BUY_BAND_PCT / PM_HOLD_FLOOR_PCT /
    PM_SHORT_FLOOR_PCT can be tuned without code changes."""
    def _env(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default
    return (
        _env("PM_BUY_BAND_PCT", 15.0),
        _env("PM_HOLD_FLOOR_PCT", -10.0),
        _env("PM_SHORT_FLOOR_PCT", -20.0),
    )


def _band_action(upside_pct) -> str | None:
    """Quantitative band action, or None when upside is not computable."""
    if not isinstance(upside_pct, (int, float)):
        return None
    buy_t, hold_lo, short_lo = _pm_band_thresholds()
    if upside_pct >= buy_t:
        return "BUY"
    if upside_pct >= hold_lo:
        return "HOLD"
    if upside_pct >= short_lo:
        return "SHORT"
    return "SELL"


# Deterministic adverse/positive read of freshness-delta text. The delta
# carries {material, events[{headline,date,relevance}], verdict} with no
# sentiment field — the gate needs a direction, so keyword-pass it is
# (no LLM in the gate path). Mixed signals → None (no shift).
_PM_ADVERSE_WORDS = (
    "guidance cut", "cut guidance", "cuts guidance", "profit warning",
    "warns", "warning", "downgrade", "downgraded", "misses", "missed",
    "probe", "investigation", "lawsuit", "sued", "sanction", "tariff",
    "recall", "fraud", "restat", "resign", "antitrust", "fine",
    "penalty", "breach", "outage", "bankruptcy", "default", "impair",
    "writedown", "write-down", "layoff", "restructur", "slump", "plunge",
    "crackdown", "suspend", "halt",
)
_PM_POSITIVE_WORDS = (
    "beats", "beat estimates", "raise guidance", "raised guidance",
    "guidance raise", "hikes guidance", "upgrade", "upgraded", "approval",
    "approved", "wins", "award", "partnership", "buyback", "dividend hike",
    "record", "milestone", "strong earnings", "upside surprise", "surge",
    "expand", "breakthrough",
)


def _delta_direction(delta: dict) -> str | None:
    """'adverse' | 'positive' | None (no material change / unclear)."""
    if not delta or not delta.get("material"):
        return None
    _bits = [str(delta.get("verdict") or "")]
    for e in (delta.get("events") or []):
        if isinstance(e, dict):
            _bits.append(f"{e.get('headline') or ''} {e.get('relevance') or ''}")
    text = " ".join(_bits).lower()
    adverse = any(w in text for w in _PM_ADVERSE_WORDS)
    positive = any(w in text for w in _PM_POSITIVE_WORDS)
    if adverse and not positive:
        return "adverse"
    if positive and not adverse:
        return "positive"
    return None


# Regulatory watch — deterministic keyword pass (no LLM). \b-anchored where
# the bare token is short ("SEC" must not match "second"/"sector").
_PM_REG_PATTERNS = (
    r"regulat", r"antitrust", r"\bsec\b", r"\bcsrc\b", r"tariff",
    r"sanction", r"export[- ]control", r"\bdoj\b", r"\bftc\b", r"probe",
    r"investigation", r"fine[d]?\b", r"penalt",
)


def _regulatory_watch_hits(recent_news: str, delta: dict) -> list[str]:
    """Unique regulatory keywords found in the recent-news section and the
    freshness-delta events — surfaced in the digest and consistency_flags."""
    _bits = [str(recent_news or "")]
    if delta:
        _bits.append(str(delta.get("verdict") or ""))
        for e in (delta.get("events") or []):
            if isinstance(e, dict):
                _bits.append(f"{e.get('headline') or ''} {e.get('relevance') or ''}")
    text = " ".join(_bits).lower()
    hits: list[str] = []
    for pat in _PM_REG_PATTERNS:
        m = re.search(pat, text)
        if m and m.group(0) not in hits:
            hits.append(m.group(0))
    return hits


def _research_is_stale(state) -> bool:
    """Degraded / knowledge-only research: no new BUY/SHORT may be opened.
    Missing tier (legacy/CLI states) is given the benefit of the doubt."""
    data = state["data"]
    if data.get("research_degraded"):
        return True
    tier = str(data.get("research_tier") or "").lower()
    return tier in ("none", "knowledge_only")


def _vgpm_breadth(vgpm_t: dict) -> float:
    """Fraction of V/G/P/M dimensions graded A/B (0.0–1.0); 0.5 when the
    scorecard is absent (neutral)."""
    grades = []
    for dim in ("valuation", "growth", "profitability", "momentum"):
        g = str(((vgpm_t or {}).get(dim) or {}).get("grade") or "").upper()
        if g:
            grades.append(g)
    if not grades:
        return 0.5
    return sum(1 for g in grades if g[:1] in ("A", "B")) / len(grades)


def _qualitative_conviction(state, ticker: str, final_action: str,
                            reg_hits: list[str]) -> tuple[float, list[str]]:
    """(conviction, cap-notes): 0.5 / 0.75 / 1.0 from evidence tier +
    delta availability + VGPM breadth; capped at 0.75 by regime
    misalignment (risk-off/high-recession vs new BUY, risk-on vs new
    SHORT) or an unresolved regulatory watch item."""
    data = state["data"]
    notes: list[str] = []
    tier = str(data.get("research_tier") or "").lower()
    if tier in _PM_FRESH_TIERS and not data.get("research_degraded"):
        fresh_pts = 1.0
    elif tier in _PM_RECENT_TIERS:
        fresh_pts = 0.75
    else:
        fresh_pts = 0.25
    _delta = (data.get("freshness_delta") or {}).get(ticker) or {}
    delta_pts = 1.0 if _delta.get("material") is not None else 0.0
    breadth = _vgpm_breadth((data.get("vgpm") or {}).get(ticker))
    raw = (fresh_pts + delta_pts + breadth) / 3.0
    conv = 1.0 if raw >= 0.75 else (0.75 if raw >= 0.5 else 0.5)

    regime = data.get("macro_regime") or {}
    appetite = str(regime.get("risk_appetite") or "").lower()
    recession = str(regime.get("recession_risk") or "").lower()
    if final_action == "BUY" and (appetite == "risk-off" or recession == "high"):
        if conv > 0.75:
            conv = 0.75
            notes.append(
                "⚠ conviction capped at 0.75: risk-off/high-recession "
                "regime vs new BUY — macro decides size, not direction")
    elif final_action == "SHORT" and appetite == "risk-on":
        if conv > 0.75:
            conv = 0.75
            notes.append(
                "⚠ conviction capped at 0.75: risk-on regime vs new SHORT "
                "— macro decides size, not direction")
    if reg_hits and conv > 0.75:
        conv = 0.75
        notes.append(
            "⚠ conviction capped at 0.75: unresolved regulatory watch "
            f"({', '.join(reg_hits[:3])})")
    return conv, notes


def _build_research_digest(state, ticker: str) -> str:
    """Deterministic per-ticker research digest — no LLM call. Budget
    ~1.5–2k tokens (≤8000 chars): recent_news (carrying Track A's LATEST
    DEVELOPMENTS addendum) + thesis/executive section excerpts + compressed
    industry brief. Fallback = head-truncate the full research text."""
    data = state["data"]
    sections = data.get("deep_research_sections") or {}
    if not isinstance(sections, dict) or not sections:
        _full = str(data.get("deep_research") or "")
        if _full:
            return _full[:4000] + "\n[…research head-truncated]"
        return "(no research available)"
    parts: list[str] = []
    _rn = str(sections.get("recent_news") or "")
    if _rn:
        parts.append("RECENT NEWS / LATEST DEVELOPMENTS:\n" + _rn[:2500])
    # 2F is the research narrative (thesis); 2A/2B the structural analysis.
    for _k in ("2f", "2a", "2b"):
        _s = str(sections.get(_k) or "")
        if _s:
            parts.append(f"RESEARCH SECTION {_k.upper()} (excerpt):\n" + _s[:1800])
    for _k in ("2c", "2d", "2e"):
        _s = str(sections.get(_k) or "")
        if _s:
            parts.append(f"SECTION {_k.upper()} (excerpt):\n" + _s[:500])
    _brief = str(data.get("industry_brief") or "")
    if _brief:
        parts.append("INDUSTRY BRIEF (compressed):\n" + _brief[:1500])
    if not parts and sections.get("full"):
        parts.append(str(sections["full"])[:4000])
    digest = "\n\n".join(parts)
    return (digest[:8000] if digest else "(no research sections parsed)")


def _analyst_thesis_block(ticker: str) -> str:
    """The deposited sell-side thesis, as a view to weigh.

    Applies to US, HKEX and SGX alike — the extraction shape is the same
    for all three, so a Phillip note on ST Engineering and a Goldman note
    on Alibaba arrive in the same form.

    Capped deliberately: four thesis points, three catalysts, three risks.
    The whole note is already available to deep research; what the decision
    needs is the argument, not the document.
    """
    try:
        from src.memory.analyst_basis import get_analyst_thesis
        thesis = get_analyst_thesis(ticker)
    except Exception:
        return "  (no deposited analyst report)"
    if not thesis:
        return "  (no deposited analyst report)"

    header = " ".join(x for x in (
        thesis.get("house"), thesis.get("analyst"), thesis.get("as_of")) if x)
    stance = " ".join(x for x in (
        thesis.get("rating"),
        (f"TP {thesis['price_target']}" if thesis.get("price_target") else ""),
    ) if x)

    lines = [f"  Source: {header or 'sell-side'}"
             + (f" — {stance}" if stance else "")]
    for label, key, cap in (("Thesis", "points", 4),
                            ("Catalysts", "catalysts", 3),
                            ("Risks", "risks", 3)):
        items = thesis.get(key) or []
        for item in items[:cap]:
            lines.append(f"  [{label}] {item[:260]}")
    return "\n".join(lines)



def _quant_block_text(ticker: str, state, scenario: dict) -> str:
    """Quantitative anchors for the rationale LLM: DCF range + WACC, blended
    IV, scenario EV + 12m PT + upsides, VGPM grades, power-law score, peer
    comparison one-liner."""
    data = state["data"]
    recon = scenario.get("reconciliation") or {}
    dcf = (data.get("dcf_range") or {}).get(ticker) or {}
    lines: list[str] = []

    # Reporting-currency symbol. A hardcoded "$" against SGD/HKD anchors is
    # what leaked a bare dollar sign into the D05.SI narrative and got
    # raised as a currency-mislabelling risk by the value-trap agent.
    _ccy = ((data.get("raw_financials") or {}).get("currency")
            or (dcf.get("base") or {}).get("reported_currency") or "USD")
    _sym = {"USD": "$", "SGD": "S$", "HKD": "HK$", "CNY": "RMB", "EUR": "€",
            "GBP": "£", "JPY": "¥", "AUD": "A$",
            "INR": "₹"}.get(str(_ccy).upper(), str(_ccy).upper() + " ")

    # ── Analyst valuation basis ──────────────────────────────────────────
    # The sell-side method and its published parameters, so the thesis can
    # state what the street assumed and where we differ. Benchmark only —
    # the engine's own profile and method still govern the numbers above.
    try:
        from src.memory.analyst_basis import get_analyst_basis as _gab
        _ab = _gab(ticker) or {}
    except Exception:
        _ab = {}
    if _ab.get("method"):
        _ap = [f"method {_ab['method']}"]
        for _k, _l in (("wacc", "WACC"), ("cost_of_equity", "CoE"),
                       ("terminal_growth", "terminal g"),
                       ("holdco_discount", "holdco discount")):
            if _ab.get(_k) is not None:
                _ap.append(f"{_l} {_ab[_k] * 100:.2f}%")
        if _ab.get("target_multiple"):
            _ap.append(f"{_ab['target_multiple']:g}x "
                       f"{_ab.get('multiple_basis') or ''}".strip())
        if _ab.get("price_target"):
            _ap.append(f"street TP {_ab['price_target']}")
        if _ab.get("rating"):
            _ap.append(str(_ab["rating"]))
        lines.append(
            f"Analyst basis ({_ab.get('house') or 'sell-side'} "
            f"{_ab.get('as_of') or ''}): " + ", ".join(_ap))
    _wacc = dcf.get("wacc")
    if isinstance(_wacc, (int, float)) and _wacc > 0:
        lines.append(f"WACC {_wacc * 100:.1f}%" if _wacc <= 1 else f"WACC {_wacc:.1f}%")
    for _case in ("bear", "base", "bull"):
        _iv = (dcf.get(_case) or {}).get("intrinsic_value")
        if isinstance(_iv, (int, float)) and _iv > 0:
            lines.append(f"DCF {_case} IV {_sym}{_iv:,.2f}")
    if recon.get("blended_iv"):
        lines.append(f"Blended IV {_sym}{recon['blended_iv']:,.2f}")
    if scenario.get("expected_value"):
        lines.append(
            f"Scenario EV {_sym}{scenario['expected_value']:,.2f} "
            f"({scenario.get('upside_pct') or 0:+.1f}%)")
    if scenario.get("12m_price_target"):
        lines.append(f"12m PT {_sym}{scenario['12m_price_target']:,.2f}")
    _uiv = recon.get("upside_to_iv_pct")
    if isinstance(_uiv, (int, float)):
        lines.append(f"Upside to blended IV {_uiv:+.1f}%")
    _vg = (data.get("vgpm") or {}).get(ticker) or {}
    _grades = "/".join(
        str((_vg.get(d) or {}).get("grade") or "–")
        for d in ("valuation", "growth", "profitability", "momentum"))
    if any(g != "–" for g in _grades.split("/")):
        lines.append(f"VGPM grades V/G/P/M: {_grades}")
    _pl = (data.get("power_law_analysis") or {}).get(ticker) or {}
    if _pl.get("total_score") is not None:
        lines.append(f"Power Law {_pl['total_score']}/10")
    _pc = (data.get("peer_comparison") or {}).get(ticker) or {}
    if _pc:
        lines.append("Peer comparison: " + str(_pc)[:280].replace("\n", " "))
    return "\n".join(f"  {l}" for l in lines) or "  (valuation data unavailable)"


def _macro_one_liner(state) -> str:
    """risk_appetite / volatility / recession risk + regime_notes — the PM
    cites this in one line when a regime conviction cap applies."""
    regime = state["data"].get("macro_regime") or {}
    if not regime:
        return "Macro regime: unavailable."
    _bits = [b for b in (
        regime.get("risk_appetite"),
        (f"{regime.get('volatility_regime')} vol"
         if regime.get("volatility_regime") else None),
        (f"recession risk {regime.get('recession_risk')}"
         if regime.get("recession_risk") else None),
    ) if b]
    _line = "Macro regime: " + (" / ".join(_bits) if _bits else "unclassified")
    _notes = str(regime.get("regime_notes") or "")[:200]
    return _line + (f" — {_notes}" if _notes else "")


def run_advanced_portfolio_manager(state) -> dict:
    """
    Phase 9: committee-free final decision (M2 D2).

    Action — deterministic cascade: quantitative band on blended-IV upside
    (≥ +15% BUY, −10…+15% HOLD, −20…−10% SHORT, ≤ −20% SELL) adjusted by
    qualitative gates that move at most ONE step (material freshness-delta
    direction, TRAP RISK HIGH blocks BUY, degraded research caps at
    HOLD/SELL), then the directional guards. Missing valuation → HOLD,
    size 0, flagged.

    Position Size = approved_size × ev_factor × power_factor ×
    qualitative_conviction (0.5/0.75/1.0 from evidence tier + delta +
    VGPM breadth; capped at 0.75 by regime misalignment or an unresolved
    regulatory watch item). Halved if Value Trap verdict is HIGH.

    The LLM writes the numbered-thesis rationale over a quantitative anchor
    block + a deterministic research digest; Python stays the authority for
    action / size / stop / target.
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
        progress.update_status(agent_id, ticker, "Computing valuation-anchored decision")

        # ── Inputs ─────────────────────────────────────────────────────────
        risk_data = state["data"].get("analyst_signals", {}).get(
            "advanced_risk_manager", {}
        ).get(ticker, {})
        approved_size_pct = risk_data.get("approved_size_pct", 0.05)

        scenario = state["data"].get("scenario_analysis", {}).get(ticker, {})
        ev_upside = scenario.get("upside_pct", 0.0)
        current_price = scenario.get("current_price", 0.0)
        expected_value = scenario.get("expected_value", 0.0)
        recon = scenario.get("reconciliation", {})

        power_law = state["data"].get("power_law_analysis", {}).get(ticker, {})
        power_score = power_law.get("total_score", 5)

        trap = state["data"].get("value_trap_analysis", {}).get(ticker, {})
        trap_verdict = trap.get("overall_verdict", "TRAP RISK LOW")

        _prior_recap = (state["data"].get("prior_recap") or {}).get(ticker) or {}
        _delta = (state["data"].get("freshness_delta") or {}).get(ticker) or {}
        _sections = state["data"].get("deep_research_sections") or {}
        _recent_news_txt = str(_sections.get("recent_news") or "") if isinstance(_sections, dict) else ""

        # ── M2 D2 step 1: quantitative band on blended-IV upside ──────────
        # Blended IV is the primary anchor; fall back to scenario EV upside
        # when the reconciliation block is absent (no DCF blend).
        _upside_iv = recon.get("upside_to_iv_pct")
        if _upside_iv is None and (expected_value or 0) > 0 and current_price > 0:
            _upside_iv = (expected_value - current_price) / current_price * 100.0

        _band_flag = ""
        _gate_notes: list[str] = []
        _band = None
        _size_zero = False
        if _upside_iv is None:
            # No blended IV nor EV — nothing to anchor a direction on.
            action = "HOLD"
            _size_zero = True
            _band_flag = (
                "⚠ no blended IV / EV available — valuation missing; "
                "defaulting to HOLD (size 0)")
        else:
            _band = _band_action(_upside_iv)
            _band_idx = _PM_LADDER.index(_band)
            _idx = _band_idx
            # Gate 1 — material freshness delta: ONE-step adjustment with a
            # deterministic direction read (no LLM in the gate path).
            _dir = _delta_direction(_delta)
            if _dir == "adverse" and _idx > 0:
                _idx -= 1
                _gate_notes.append(
                    f"material-adverse fresh news shifted {_band} → {_PM_LADDER[_idx]} (one step)")
            elif _dir == "positive" and _idx < len(_PM_LADDER) - 1:
                _idx += 1
                _gate_notes.append(
                    f"material-positive fresh news shifted {_band} → {_PM_LADDER[_idx]} (one step)")
            # Gate 2 — TRAP RISK HIGH blocks BUY outright.
            if trap_verdict == "TRAP RISK HIGH" and _PM_LADDER[_idx] == "BUY":
                _idx = _PM_LADDER.index("HOLD")
                _gate_notes.append("TRAP RISK HIGH blocked BUY (value-trap gate)")
            # Delta + trap never stack beyond ONE step from the band.
            _idx = max(_band_idx - 1, min(_band_idx + 1, _idx))
            # Gate 3 — degraded / knowledge-only research: no new BUY/SHORT
            # opened on stale research (absolute eligibility rule).
            if _research_is_stale(state) and _PM_LADDER[_idx] in ("BUY", "SHORT"):
                _from = _PM_LADDER[_idx]
                _idx = _PM_LADDER.index("HOLD" if _from == "BUY" else "SELL")
                _gate_notes.append(
                    f"degraded research capped {_from} → {_PM_LADDER[_idx]} "
                    "(no new positions on stale research)")
            action = _PM_LADDER[_idx]

        # ── M2 D2 step 2: qualitative conviction (sizing multiplier) ──────
        _reg_hits = _regulatory_watch_hits(_recent_news_txt, _delta)
        conviction, _conv_notes = _qualitative_conviction(
            state, ticker, action, _reg_hits)

        # Core formula — three normalised factors, each in [0, 1]:
        # 1. EV upside: cap at ±50% so LLM-optimistic upsides don't saturate formula
        ev_factor = min(abs(ev_upside), 50.0) / 50.0
        # 2. Power Law score: already 1-10, normalise to [0.1, 1.0]
        power_factor = power_score / 10.0
        # 3. Qualitative conviction replaces the committee's signal_factor
        #    (evidence tier + delta availability + VGPM breadth, capped by
        #    regime misalignment / regulatory watch).

        if ev_upside > 0 and action == "BUY":
            size_pct = approved_size_pct * ev_factor * power_factor * conviction
        elif action in ("SELL", "SHORT"):
            # Fix 1b: size driven by signal + power law even when scenario EV is flat.
            # Previously: ev_upside=0 → ev_factor=0 → size_pct=0 (silent zero).
            # Now: if no measurable downside use 0.5 as a neutral ev proxy so
            # conviction and power law score still produce a non-zero size.
            _sell_ev = min(abs(ev_upside), 50.0) / 50.0 if ev_upside < 0 else 0.5
            size_pct = approved_size_pct * _sell_ev * power_factor * conviction
            # Minimum floor: actionable SELL/SHORT must always show ≥20% of approved
            size_pct = max(size_pct, approved_size_pct * 0.20)
        else:
            # HOLD or misaligned signal — scale down but keep conviction/power influence
            # Bug 2 fix: when EV upside is large (>50%) even a HOLD warrants more than
            # the flat 0.5 haircut — use ev_factor at 75% cap rather than discarding it
            if ev_upside > 50.0:
                hold_factor = min(ev_factor * 0.75, 0.75)
                size_pct = approved_size_pct * hold_factor * power_factor * conviction
            else:
                size_pct = approved_size_pct * 0.5 * power_factor * conviction

        if _size_zero:
            size_pct = 0.0

        if trap_verdict == "TRAP RISK HIGH":
            size_pct *= 0.5

        # Cap at approved_size_pct (already incorporates sector caps and macro regime cap
        # from Phase 8 risk manager — never exceed what risk approved)
        size_pct = min(size_pct, approved_size_pct)
        size_pct = max(size_pct, 0.0)

        # HOLD with 0% size means "no new position" — keep as HOLD (no position opened).
        # PASS is not a valid AdvancedPortfolioDecision action; HOLD covers this case.

        # ── M2 D2 step 3: cascade flags → consistency_flags ───────────────
        _pm_flag_bits: list[str] = []
        if _band_flag:
            _pm_flag_bits.append(_band_flag)
        _pm_flag_bits.extend(_gate_notes)
        if _reg_hits:
            _pm_flag_bits.append(
                "⚠ regulatory watch: "
                + ", ".join(_reg_hits[:4])
                + " mentioned in fresh research — unresolved")
        _pm_flag_bits.extend(_conv_notes)
        if _pm_flag_bits:
            _existing_flag = state["data"].setdefault(
                "consistency_flags", {}).get(ticker, "")
            state["data"]["consistency_flags"][ticker] = (
                (_existing_flag + " | " if _existing_flag else "")
                + " | ".join(_pm_flag_bits)
            )

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
        # Problem: the action can be BUY even when the forward PT < current price
        # (the band keys off blended IV, a long-horizon anchor, while the PT is
        # 12m forward-multiple based).
        # Resolution:
        #   (a) If 12m PT AND blended IV are both below current price → BUY is inconsistent → HOLD
        #   (b) If only EV < current but bull case exceeds it → keep BUY, use bull target
        #   (c) Add a flag so the PDF/editor agents can explain the gap (§8 Reconciliation)
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
                        "Valuation band is bullish but near-term targets do not support entry."
                    )
                    # Use bear case as stop-loss reference, keep target at bull (downside-risk frame)
                    price_target = expected_value

        # ── B1: SHORT-side directional consistency (mirror of the BUY guard) ──
        # Problem: a bearish action can carry a price target ABOVE current
        # price — the 12m forward-multiple PT can exceed spot, or the fallback
        # chain can land the anchor above entry. A short whose target is above
        # entry can never profit (prod BABA 08-19: SHORT with PT $134.76 vs
        # spot $127.48).
        # Resolution:
        #   (a) Neither the 12m PT nor the bear-case fair value is below
        #       current price → the bearish action is inconsistent → HOLD
        #   (b) PT at/above current but bear case below it → keep the action,
        #       clamp PT to the bear anchor
        if action in ("SELL", "SHORT") and current_price > 0:
            if (price_target or 0) >= current_price:
                # Same anchor the PT fallback chain uses (Fix 1a shape):
                # bear fair value when positive, else 0.80 × current.
                _bear_anchor_g = (
                    bear_fv if (isinstance(bear_fv, (int, float)) and bear_fv > 0)
                    else current_price * 0.80
                )
                if 0 < _bear_anchor_g < current_price:
                    # Downside anchor still below current — clamp target, keep action
                    _orig_pt = price_target or 0.0
                    price_target = round(_bear_anchor_g, 2)
                    _directional_flag = (
                        (_directional_flag + " | " if _directional_flag else "")
                        + f"⚠ {action} target clamped to downside anchor "
                        f"(${_bear_anchor_g:.2f}): initial PT (${_orig_pt:.2f}) was "
                        f"at/above current price (${current_price:.2f}). "
                        "Downside thesis anchored to the bear scenario."
                    )
                else:
                    # Neither 12m PT nor bear case below current price → downgrade
                    action = "HOLD"
                    # HOLD uses the long-side stop (was computed short-side at 1.10)
                    stop_loss = current_price * 0.90
                    _directional_flag = (
                        (_directional_flag + " | " if _directional_flag else "")
                        + f"⚠ SELL/SHORT downgraded to HOLD: 12m PT (${(_12m_pt or 0):.2f}) "
                        f"and bear-case IV (${(bear_fv or 0):.2f}) both at/above current "
                        f"price (${current_price:.2f}). Bearish view exists but near-term "
                        "valuation does not support a short entry."
                    )
                    # Neutral reference target (mirrors the BUY-downgrade path)
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
            _existing_flag = state["data"].setdefault(
                "consistency_flags", {}).get(ticker, "")
            state["data"]["consistency_flags"][ticker] = (
                (_existing_flag + " | " if _existing_flag else "")
                + _directional_flag
            )

        progress.update_status(agent_id, ticker, "Generating rationale via LLM")

        # ── Build narrative context for the rationale LLM ─────────────────
        # M2 D2: with the committee gone the PM internalizes the research
        # directly — a deterministic digest (no new LLM call) of the thesis
        # sections + recent news + industry brief, plus the quantitative
        # anchor block and the macro-regime one-liner.
        _base_assumptions = str(
            scenario.get("base", {}).get("assumptions", "")
        )[:300]
        _bull_assumptions = str(
            scenario.get("bull", {}).get("assumptions", "")
        )[:200]

        _digest = _build_research_digest(state, ticker)
        _quant_block = _quant_block_text(ticker, state, scenario)
        _macro_line = _macro_one_liner(state)

        # Catalyst continuity: this run's bull-case catalyst alongside the
        # prior report's watched catalysts (recap_json["catalysts"], ≤8).
        _prior_catalysts = ((_prior_recap.get("recap_json") or {})
                            .get("catalysts") or [])
        _catalyst_bits: list[str] = []
        if _bull_assumptions:
            _catalyst_bits.append(
                f"This run's bull-case catalyst: {_bull_assumptions}")
        if _prior_catalysts:
            _catalyst_bits.append(
                "Prior report's watched catalysts: "
                + "; ".join(str(c)[:120] for c in _prior_catalysts[:8]))
        _catalyst_block = "\n".join(_catalyst_bits) or "No catalyst context."

        # Research↔books divergence flags (C6 checks incl. B2 currency
        # mislabel) — structured signals the rationale must not contradict.
        _divs = (state["data"].get("research_financial_divergences") or {}).get(ticker) or {}
        _div_lines: list[str] = []
        if isinstance(_divs, dict):
            for _k, _v in list(_divs.items())[:6]:
                if isinstance(_v, dict):
                    _div_lines.append(f"{_k}: {_v.get('note') or _v}")
                else:
                    _div_lines.append(f"{_k}: {_v}")
        _div_block = ("\n".join(_div_lines)[:800]
                      if _div_lines else "No research↔books divergences flagged.")

        # ── M1 recency: prior report recap + freshness delta ────────────────
        # Continuity with the last report on this ticker (if any) so the PM
        # can explicitly upgrade/downgrade vs the prior call instead of
        # deciding from amnesia. Absent on first-ever runs.
        if _prior_recap:
            _pr_bits = [
                f"Prior report ({str(_prior_recap.get('run_at') or '')[:10]}, "
                f"{_prior_recap.get('age_days', 0):.1f}d old): "
                f"{_prior_recap.get('final_action') or 'N/A'}"
            ]
            _pr_json = _prior_recap.get("recap_json") or {}
            if _pr_json.get("price_target"):
                _pr_bits.append(f"| prior PT {_pr_json['price_target']}")
            if _prior_recap.get("price_at_run"):
                _pr_bits.append(f"@ ${_prior_recap['price_at_run']}")
            _prior_block = " ".join(_pr_bits)
            _thesis = (_prior_recap.get("recap_text") or "").strip()
            if _thesis:
                _prior_block += f"\nPrior thesis: {_thesis[:400]}"
            if _delta:
                _mat = _delta.get("material")
                if _mat is None:
                    _prior_block += "\nSince then: freshness check unavailable."
                elif _mat:
                    _evs = "; ".join(
                        (e.get("headline") or "")[:80]
                        for e in (_delta.get("events") or [])[:3]
                    )
                    _prior_block += (
                        f"\nSince then: MATERIAL change — {_evs}. "
                        f"{(_delta.get('verdict') or '')[:200]}"
                    )
                else:
                    _prior_block += (
                        "\nSince then: no material change — prior report "
                        "still current."
                    )
        else:
            _prior_block = "No prior report on this ticker (first run)."

        # ── B3: flip-justification backstop ──────────────────────────────────
        # Reversing the prior report's direction must cite material new
        # evidence. The LLM is told to justify any flip; the deterministic
        # backstop appends a visible flag when a flip happens WITHOUT a
        # material freshness delta (material None = check unavailable and
        # material False = no material change both flag). No sizing change —
        # the flag is the disclosure.
        _prior_action = str(_prior_recap.get("final_action") or "").upper()
        _is_flip = (
            (_prior_action == "BUY" and action in ("SELL", "SHORT"))
            or (_prior_action in ("SELL", "SHORT") and action == "BUY")
        )
        _flip_flag = ""
        if _is_flip:
            _prior_block += (
                "\nNOTE: This action REVERSES the prior report's call. Your "
                "rationale MUST cite the material new evidence that justifies "
                "the flip."
            )
            if (_delta.get("material") if _delta else None) is not True:
                _flip_flag = (
                    f"⚠ flipped from {_prior_action} without material fresh news"
                )
                _existing_flag = state["data"].setdefault(
                    "consistency_flags", {}).get(ticker, "")
                state["data"]["consistency_flags"][ticker] = (
                    (_existing_flag + " | " if _existing_flag else "")
                    + _flip_flag
                )

        # LLM writes the rationale as numbered thesis themes (Tier 2.7
        # thesis-density rule — see _PM_RATIONALE_SYSTEM_PROMPT).
        _upside_str = (f"{_upside_iv:+.1f}%"
                       if isinstance(_upside_iv, (int, float)) else "n/a")
        # Bank names get the profile addendum appended to the house
        # rules, so the thesis is written in bank primitives rather
        # than the generic growth/margin frame.
        _profile_for_prompt = (
            (state["data"].get("profile_names") or {}).get(ticker)
            or state["data"].get("profile_name")
            or ""
        )
        _system_prompt = _PM_RATIONALE_SYSTEM_PROMPT
        if ("Bank" in _profile_for_prompt
                or _profile_for_prompt in {"Bank / Lending Institution",
                                           "Mortgage/GSE"}):
            _system_prompt = _system_prompt + _PM_BANK_RATIONALE_ADDENDUM

        template = ChatPromptTemplate.from_messages([
            ("system", _system_prompt),
            ("human", (
                "Ticker: {ticker} | Action: {action} | Size: {size_pct:.1%}\n"
                "IV-band upside: {upside_iv} | Qualitative conviction: {conviction:.2f} | Trap: {trap}\n"
                "{macro_line}\n"
                "Quantitative anchors:\n{quant_block}\n"
                "Catalyst continuity:\n{catalyst_block}\n"
                "Research vs books checks:\n{div_block}\n"
                "Base scenario: {base_assumptions}\n"
                "Deposited analyst thesis (a view to weigh, not to adopt):\n"
                "{analyst_thesis}\n"
                "Research digest (qualitative):\n{digest}\n"
                "Prior report context:\n{prior_block}\n\n"
                "Output:\n"
                '{{\n'
                '  "action": "{action}",\n'
                '  "position_size_pct": {size_pct},\n'
                '  "entry_range": [float, float],\n'
                '  "stop_loss": {stop_loss},\n'
                '  "price_target": {price_target},\n'
                '  "time_horizon": "short"|"medium"|"long",\n'
                '  "rationale": "1. ...\\n2. ...\\n3. ..."\n'
                "}}"
            )),
        ])

        prompt = template.invoke({
            "ticker": ticker,
            "action": action,
            "size_pct": size_pct,
            "upside_iv": _upside_str,
            "conviction": conviction,
            "trap": trap_verdict,
            "macro_line": _macro_line,
            "quant_block": _quant_block,
            "analyst_thesis": _analyst_thesis_block(ticker),
            "catalyst_block": _catalyst_block,
            "div_block": _div_block,
            "base_assumptions": _base_assumptions,
            "digest": _digest,
            "prior_block": _prior_block,
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

        # ── M2 D3 payload: what the decision was made from ─────────────────
        # Rendered by the frontend "Decision inputs" card (both render
        # paths) and persisted through web_runs.full_result_json.
        _vgpm_t = (state["data"].get("vgpm") or {}).get(ticker) or {}
        d["decision_inputs"] = {
            "quantitative": {
                "band_action": _band,
                "upside_to_iv_pct": _upside_iv,
                "blended_iv": recon.get("blended_iv"),
                "expected_value": expected_value or None,
                "price_target_12m": scenario.get("12m_price_target"),
                "ev_upside_pct": ev_upside,
                "vgpm_grades": {
                    dim: (_vgpm_t.get(dim) or {}).get("grade")
                    for dim in ("valuation", "growth", "profitability", "momentum")
                },
                "power_law_score": power_score,
                "trap_verdict": trap_verdict,
            },
            "qualitative": {
                "research_tier": state["data"].get("research_tier"),
                "digest_chars": len(_digest),
                "delta_material": _delta.get("material") if _delta else None,
                "regulatory_watch": _reg_hits,
                "prior_catalysts": [str(c)[:120] for c in _prior_catalysts[:8]],
            },
            "gates": (_gate_notes + ([_band_flag] if _band_flag else [])),
            "conviction": {"value": conviction, "notes": _conv_notes},
        }

        # B3: make the flip backstop visible in the report itself — append the
        # flag to the rationale so it renders wherever the rationale renders.
        if _flip_flag:
            d["rationale"] = ((d.get("rationale") or "").rstrip()
                              + f"\n{_flip_flag}")
        # Anchor-citation enforcement (lightweight): a rationale with no
        # numbers at all cites no quantitative anchor — flag it.
        if not any(ch.isdigit() for ch in (d.get("rationale") or "")):
            _existing_flag = state["data"].setdefault(
                "consistency_flags", {}).get(ticker, "")
            state["data"]["consistency_flags"][ticker] = (
                (_existing_flag + " | " if _existing_flag else "")
                + "⚠ rationale cites no quantitative anchor")
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
