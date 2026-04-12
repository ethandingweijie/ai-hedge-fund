"""
Phase 1 — Macro Regime Classifier

What it does:
- Fetches 7 Tier-1 economic indicators via FMP /stable/economic-indicators:
    federalFunds, inflationRate, smoothedUSRecessionProbabilities,
    initialClaims, consumerSentiment, retailMoneyFunds, tradeBalanceGoodsAndServices
- Fetches Treasury yield curve via FMP /stable/treasury-rates (2Y vs 10Y spread)
- Fetches SPY 90-day prices + news headlines as equity market cross-check
- Pre-computes trend signals in Python (direction, thresholds, labels)
  so the LLM receives a structured narrative block — not raw numbers
- Asks the LLM to classify the regime across FIVE dimensions:
    risk_appetite | rate_direction | dollar_trend | volatility_regime | recession_risk
- Uses a hard-coded RULE TABLE (not the LLM) to translate regime → agent weight multipliers
- Reads conviction_weights.json (track record from prior post-trade reviews)
- Multiplies track-record weights × regime multipliers → agent_weight_multipliers for Phase 9
- Persists the regime to regime_state.json for Phase 10 post-trade review
"""

import json
import os
from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate

from src.data.models import MacroRegimeOutput
from src.graph.state import AgentState
from src.tools.api import (
    get_prices,
    get_company_news,
    get_economic_indicator,
    get_treasury_rates,
)
from src.utils.llm import call_llm
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state

# ---------------------------------------------------------------------------
# Regime → weight adjustment rules (CLAUDE.md §1)
# Key: (risk_appetite, rate_direction) or (volatility_regime,)
# Values: agents to upweight (×1.3) and downweight (×0.7)
# ---------------------------------------------------------------------------
REGIME_WEIGHT_RULES: list[tuple[dict, dict]] = [
    (
        {"risk_appetite": "risk-off", "rate_direction": "tightening"},
        {
            "upweight": ["graham", "munger", "burry"],
            "downweight": ["cathie_wood", "jhunjhunwala"],
        },
    ),
    (
        {"risk_appetite": "risk-on", "rate_direction": "easing"},
        {
            "upweight": ["cathie_wood", "druckenmiller", "lynch"],
            "downweight": ["graham"],
        },
    ),
    (
        {"volatility_regime": "high"},
        {
            "upweight": ["druckenmiller", "burry"],
            "downweight": [],
            "position_size_cap": 0.5,
        },
    ),
    (
        {"dollar_trend": "strengthening"},
        {
            "upweight": [],
            "downweight": ["jhunjhunwala"],
        },
    ),
    (
        {"recession_risk": "elevated"},
        {
            "upweight": ["burry", "munger"],
            "downweight": [],
        },
    ),
    (
        {"recession_risk": "high"},
        {
            "upweight": ["graham", "burry", "munger"],
            "downweight": ["cathie_wood", "jhunjhunwala"],
            "position_size_cap": 0.6,
        },
    ),
]

ALL_AGENTS = [
    "damodaran", "graham", "ackman", "cathie_wood", "munger",
    "burry", "pabrai", "lynch", "fisher", "jhunjhunwala",
    "druckenmiller", "buffett",
]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


def _load_conviction_weights() -> dict[str, float]:
    path = os.path.join(DATA_DIR, "conviction_weights.json")
    try:
        with open(path) as f:
            raw = json.load(f)
        return {k: float(v) for k, v in raw.items() if k in ALL_AGENTS}
    except Exception:
        return {agent: 1.0 for agent in ALL_AGENTS}


def _apply_regime_rules(regime: dict, base_weights: dict[str, float]) -> tuple[dict[str, float], float]:
    """
    Walk the rule table, apply multipliers where the regime matches,
    and return (adjusted_weights, position_size_cap).
    """
    weights = dict(base_weights)
    position_size_cap = 1.0

    for condition, adjustments in REGIME_WEIGHT_RULES:
        # Check if ALL keys in condition match the current regime
        if all(regime.get(k) == v for k, v in condition.items()):
            for agent in adjustments.get("upweight", []):
                if agent in weights:
                    weights[agent] = min(2.0, weights[agent] * 1.3)
            for agent in adjustments.get("downweight", []):
                if agent in weights:
                    weights[agent] = max(0.5, weights[agent] * 0.7)
            if "position_size_cap" in adjustments:
                position_size_cap = min(position_size_cap, adjustments["position_size_cap"])

    return weights, position_size_cap


def run_macro_regime_classifier(state: AgentState) -> AgentState:
    """Phase 1: classify macro regime using economic indicators + treasury rates + SPY prices."""
    agent_id = "macro_regime_classifier"
    progress.update_status(agent_id, None, "Fetching macro data (economic indicators + treasury rates)")

    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    end_date = state["data"]["end_date"]
    start_date = (
        datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=90)
    ).strftime("%Y-%m-%d")

    # ── Fetch all data ────────────────────────────────────────────────────────
    spy_prices  = get_prices("SPY", start_date, end_date, api_key=api_key)
    spy_news    = get_company_news("SPY", end_date, start_date, limit=20, api_key=api_key)
    fed_data    = get_economic_indicator("federalFunds",                       start_date, end_date, api_key=api_key)
    infl_data   = get_economic_indicator("inflationRate",                      start_date, end_date, api_key=api_key)
    rec_data    = get_economic_indicator("smoothedUSRecessionProbabilities",   start_date, end_date, api_key=api_key)
    claims_data = get_economic_indicator("initialClaims",                      start_date, end_date, api_key=api_key)
    sent_data   = get_economic_indicator("consumerSentiment",                  start_date, end_date, api_key=api_key)
    rmf_data    = get_economic_indicator("retailMoneyFunds",                   start_date, end_date, api_key=api_key)
    trade_data  = get_economic_indicator("tradeBalanceGoodsAndServices",       start_date, end_date, api_key=api_key)
    tsy_data    = get_treasury_rates(start_date, end_date, api_key=api_key)

    # ── Helper: extract up to N most-recent values from sorted-desc series ────
    def vals(series: list[dict], n: int = 2) -> list[float]:
        return [r["value"] for r in series[:n]] if series else []

    # ── SPY equity summary (used as volatility_regime proxy) ─────────────────
    spy_summary = "(SPY data unavailable)"
    if spy_prices:
        first, last = spy_prices[0], spy_prices[-1]
        chg = ((last.close - first.close) / first.close) * 100
        spy_summary = (
            f"SPY 90-day: {chg:+.1f}%  (from {first.close:.0f} to {last.close:.0f}). "
            f"Latest: {last.close:.0f}."
        )

    # ── RATE ENVIRONMENT ──────────────────────────────────────────────────────
    # Federal Funds Rate
    fv = vals(fed_data, 2)
    if len(fv) >= 2:
        if fv[0] > fv[-1] + 0.1:
            fed_signal = f"Fed Funds RISING: {fv[-1]:.2f}% -> {fv[0]:.2f}% (tightening)"
        elif fv[0] < fv[-1] - 0.1:
            fed_signal = f"Fed Funds FALLING: {fv[-1]:.2f}% -> {fv[0]:.2f}% (easing)"
        else:
            fed_signal = f"Fed Funds FLAT: {fv[0]:.2f}% (neutral)"
    elif fv:
        fed_signal = f"Fed Funds: {fv[0]:.2f}% (trend N/A — only 1 data point)"
    else:
        fed_signal = "Fed Funds: unavailable"

    # Inflation Rate
    iv = vals(infl_data, 1)
    if iv:
        if iv[0] > 4.0:
            infl_signal = f"Inflation YoY: {iv[0]:.1f}% (elevated — tightening bias)"
        elif iv[0] > 2.5:
            infl_signal = f"Inflation YoY: {iv[0]:.1f}% (above 2% target — mild tightening pressure)"
        else:
            infl_signal = f"Inflation YoY: {iv[0]:.1f}% (near/below target — neutral/easing bias)"
    else:
        infl_signal = "Inflation: unavailable"

    # Treasury yield curve (2Y vs 10Y inversion is the premier recession/rate signal)
    curve_signal = "Treasury rates: unavailable"
    if tsy_data:
        t = tsy_data[0]
        y2, y10, m3 = t.get("year2"), t.get("year10"), t.get("month3")
        if y2 and y10:
            spread = y10 - y2
            if spread < -0.25:
                curve_signal = (
                    f"Yield Curve INVERTED: 2Y={y2:.2f}% > 10Y={y10:.2f}%"
                    f" (spread {spread:+.2f}%) — historical recession precursor"
                )
            elif spread < 0.25:
                curve_signal = f"Yield Curve FLAT: 2Y={y2:.2f}%, 10Y={y10:.2f}% (spread {spread:+.2f}%)"
            else:
                curve_signal = f"Yield Curve NORMAL: 2Y={y2:.2f}%, 10Y={y10:.2f}% (spread {spread:+.2f}%)"
        if m3:
            curve_signal += f" | 3M T-bill: {m3:.2f}%"

    # ── GROWTH / LABOUR ───────────────────────────────────────────────────────
    # Recession probability
    rv = vals(rec_data, 1)
    if rv:
        label = "HIGH" if rv[0] > 30 else ("elevated" if rv[0] > 10 else "low")
        rec_signal = f"Recession Probability: {rv[0]:.1f}% ({label})"
    else:
        rec_signal = "Recession Probability: unavailable"

    # Initial Claims (4-week average — weekly data so ~13 points in 90 days)
    cv = [r["value"] for r in claims_data[:4]] if claims_data else []
    if cv:
        avg_c = sum(cv) / len(cv)
        if avg_c > 350_000:
            claims_signal = f"Initial Claims 4wk avg: {avg_c/1000:.0f}k (ABOVE stress threshold — risk-off)"
        elif avg_c > 270_000:
            claims_signal = f"Initial Claims 4wk avg: {avg_c/1000:.0f}k (elevated — watch closely)"
        else:
            claims_signal = f"Initial Claims 4wk avg: {avg_c/1000:.0f}k (healthy labour market)"
    else:
        claims_signal = "Initial Claims: unavailable"

    # Consumer Sentiment
    sv = vals(sent_data, 1)
    if sv:
        if sv[0] < 70:
            sent_signal = f"Consumer Sentiment: {sv[0]:.1f} (distressed <70 — risk-off bias)"
        elif sv[0] > 90:
            sent_signal = f"Consumer Sentiment: {sv[0]:.1f} (confident >90 — risk-on bias)"
        else:
            sent_signal = f"Consumer Sentiment: {sv[0]:.1f} (moderate 70–90)"
    else:
        sent_signal = "Consumer Sentiment: unavailable"

    # ── RISK APPETITE SIGNALS ─────────────────────────────────────────────────
    # Retail Money Funds (rising = flight-to-safety = risk-off)
    rmv = vals(rmf_data, 2)
    if len(rmv) >= 2 and rmv[-1]:
        rmf_chg = ((rmv[0] - rmv[-1]) / rmv[-1]) * 100
        if rmf_chg > 2:
            rmf_signal = f"Retail Money Funds: RISING +{rmf_chg:.1f}% (flight-to-safety — risk-off)"
        elif rmf_chg < -2:
            rmf_signal = f"Retail Money Funds: FALLING {rmf_chg:.1f}% (leaving safety — risk-on)"
        else:
            rmf_signal = f"Retail Money Funds: STABLE ({rmf_chg:+.1f}%)"
    else:
        rmf_signal = "Retail Money Funds: unavailable"

    # ── DOLLAR ────────────────────────────────────────────────────────────────
    tv = vals(trade_data, 2)
    if len(tv) >= 2:
        # Balance is negative (deficit); less negative = narrowing = mild USD support
        if tv[0] > tv[-1] + 2:
            tb_signal = f"Trade Balance: NARROWING (${tv[0]:.1f}B) — mild USD support"
        elif tv[0] < tv[-1] - 2:
            tb_signal = f"Trade Balance: WIDENING (${tv[0]:.1f}B) — USD headwind"
        else:
            tb_signal = f"Trade Balance: STABLE (${tv[0]:.1f}B)"
    elif tv:
        tb_signal = f"Trade Balance: ${tv[0]:.1f}B (trend N/A)"
    else:
        tb_signal = "Trade Balance: unavailable"

    # ── News headlines (supplementary) ───────────────────────────────────────
    news_lines = "\n".join(
        f"  - {n.title} ({n.date})" for n in (spy_news or [])[:10]
    ) or "  (no headlines available)"

    # ── Build structured prompt block ─────────────────────────────────────────
    macro_data_block = (
        f"RATE ENVIRONMENT:\n"
        f"  {fed_signal}\n"
        f"  {infl_signal}\n"
        f"  {curve_signal}\n"
        f"\nGROWTH / LABOUR:\n"
        f"  {rec_signal}\n"
        f"  {claims_signal}\n"
        f"  {sent_signal}\n"
        f"\nRISK APPETITE SIGNALS:\n"
        f"  {rmf_signal}\n"
        f"  Equity: {spy_summary}\n"
        f"\nDOLLAR:\n"
        f"  {tb_signal}\n"
        f"\nRECENT MARKET HEADLINES:\n"
        f"{news_lines}"
    )

    progress.update_status(agent_id, None, "Classifying macro regime via LLM")

    system_prompt = (
        "You are a macro regime classifier. Given structured economic indicator data, "
        "classify the current market regime across FIVE dimensions and output JSON only.\n\n"
        "CRITICAL — use EXACTLY these string values:\n"
        "- risk_appetite:     'risk-on' | 'risk-off'  "
        "(no neutral — lean risk-on for calm/up markets, risk-off for stressed/declining)\n"
        "- rate_direction:    'tightening' | 'easing' | 'neutral'\n"
        "- dollar_trend:      'strengthening' | 'weakening' | 'neutral'\n"
        "- volatility_regime: 'low' | 'medium' | 'high'  "
        "(use SPY 90-day trend + recession probability as proxy for VIX)\n"
        "- recession_risk:    'low' (prob<10%) | 'elevated' (prob 10–30%) | 'high' (prob>30%)\n\n"
        "Also output agent_weights (dict agent->float 0.5–2.0), "
        "position_size_cap (float 0.5–1.0), and regime_notes (one sentence max)."
    )

    template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Economic & market data:\n{macro_data_block}"),
    ])
    prompt = template.invoke({"macro_data_block": macro_data_block})

    llm_out: MacroRegimeOutput = call_llm(
        prompt=prompt,
        pydantic_model=MacroRegimeOutput,
        agent_name=agent_id,
        state=state,
        default_factory=lambda: MacroRegimeOutput(
            regime={
                "risk_appetite":     "risk-on",
                "rate_direction":    "neutral",
                "dollar_trend":      "neutral",
                "volatility_regime": "medium",
                "recession_risk":    "low",
                "regime_notes":      "Default regime (LLM fallback — economic data unavailable)",
            },
            agent_weights={a: 1.0 for a in ALL_AGENTS},
            position_size_cap=1.0,
            regime_notes="Default regime (LLM fallback — economic data unavailable)",
        ),
    )

    # Use deterministic rule table to override LLM weight suggestions.
    # For position_size_cap: take the minimum (more conservative) of the rule-table
    # floor and the LLM's suggestion.  The LLM cap is clamped to [0.5, 1.0] to
    # guard against hallucination extremes while still letting it reduce the cap
    # in risk-off / cautious regimes that the rule table doesn't explicitly cover.
    base_weights = _load_conviction_weights()
    regime_dict  = llm_out.regime.model_dump()
    adjusted_weights, rule_cap = _apply_regime_rules(regime_dict, base_weights)
    llm_cap = max(0.5, min(1.0, float(llm_out.position_size_cap)))
    position_size_cap = min(rule_cap, llm_cap)

    # Persist regime state to disk
    regime_path = os.path.join(DATA_DIR, "regime_state.json")
    try:
        with open(regime_path, "w") as f:
            json.dump(
                {
                    "last_run":          datetime.now().strftime("%Y-%m-%d"),
                    "tickers":           state["data"]["tickers"],
                    "regime":            regime_dict,
                    "agent_weights":     adjusted_weights,
                    "position_size_cap": position_size_cap,
                },
                f, indent=2,
            )
    except Exception:
        pass  # Non-fatal; pipeline continues

    rec_label = regime_dict.get("recession_risk", "low")
    progress.update_status(
        agent_id, None,
        f"Regime: {regime_dict['risk_appetite']} / {regime_dict['volatility_regime']} vol / recession {rec_label}"
    )

    # ── Blend data-driven regime weights when available ───────────────────────
    # regime_weights.json is written by:
    #   python -m src.memory.reweight --regime-stratified
    # It maps regime → {agent: weight} using the formula:
    #   weight = base_weight × (1 + α × regime_hit_rate)
    # When a data-driven weight exists for the current regime it replaces
    # base_weights as the track_w input to the portfolio manager, giving
    # the formula:  signal × conviction × track_w(regime-aware) × regime_w
    _rw_path     = os.path.join(DATA_DIR, "regime_weights.json")
    regime_key   = regime_dict.get("risk_appetite", "")
    track_weights = base_weights   # default: flat conviction weights
    try:
        with open(_rw_path, encoding="utf-8") as _f:
            _rw = json.load(_f)
        _data_driven = _rw.get(regime_key, {})
        if _data_driven:
            # Merge: data-driven wins where it has data; base fills the rest
            track_weights = {
                a: _data_driven.get(a, base_weights.get(a, 1.0))
                for a in ALL_AGENTS
            }
            progress.update_status(
                agent_id, None,
                f"Regime weights loaded for '{regime_key}' "
                f"({len(_data_driven)} agents data-driven)"
            )
    except FileNotFoundError:
        pass   # regime_weights.json not yet generated — use base_weights
    except Exception:
        pass   # Never fatal

    state["data"]["macro_regime"]            = regime_dict
    state["data"]["agent_weight_multipliers"] = adjusted_weights
    state["data"]["position_size_cap"]        = position_size_cap
    state["data"]["conviction_weights"]       = track_weights

    return state
