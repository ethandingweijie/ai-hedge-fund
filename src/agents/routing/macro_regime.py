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
- Uses a hard-coded RULE TABLE (not the LLM) to derive position_size_cap from the regime
- Persists the regime to regime_state.json for Phase 10 post-trade review

M2 Track E: the investor committee is decommissioned — this agent no longer
emits agent_weight_multipliers / conviction_weights. Only the regime dict and
position_size_cap are consumed downstream (risk manager, rotation engine, PM).
"""

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
# Regime → sizing rules (CLAUDE.md §1)
# Key: (risk_appetite, rate_direction) or (volatility_regime,)
# Values: position_size_cap (0.5–1.0) for defensive regimes.
# (The upweight/downweight lists are historical — the investor committee that
# consumed them is decommissioned as of M2 Track E; only position_size_cap
# is read today.)
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


def _apply_regime_rules(regime: dict) -> float:
    """
    Walk the rule table and return the position_size_cap implied by the
    regime (1.0 when no cap rule matches). The agent-weight multipliers in
    the table are historical (committee decommissioned, M2 Track E).
    """
    position_size_cap = 1.0

    for condition, adjustments in REGIME_WEIGHT_RULES:
        # Check if ALL keys in condition match the current regime
        if all(regime.get(k) == v for k, v in condition.items()):
            if "position_size_cap" in adjustments:
                position_size_cap = min(position_size_cap, adjustments["position_size_cap"])

    return position_size_cap


# ── A1: same-day regime reuse ────────────────────────────────────────────────
# Every input below is a function of end_date alone, so every run on the same
# day asks the LLM the same question of the same data. One answer per
# (end_date, model) per process, for MACRO_REGIME_CACHE_TTL_S (0 disables).
# A default-fallback regime is never cached -- it would pin a failed call.
import threading as _threading
import time as _time

_REGIME_CACHE: dict[str, tuple[float, dict]] = {}
_REGIME_CACHE_LOCK = _threading.Lock()
_REGIME_CACHE_MAX = 16


def _regime_cache_ttl_s() -> float:
    try:
        return max(0.0, float(os.environ.get("MACRO_REGIME_CACHE_TTL_S", "21600")))
    except ValueError:
        return 21600.0


def _regime_cache_key(state: AgentState, end_date: str) -> str:
    md = state.get("metadata") or {}
    return f"{end_date}|{md.get('model_provider') or ''}|{md.get('model_name') or ''}"


def _regime_cache_get(key: str) -> dict | None:
    ttl = _regime_cache_ttl_s()
    if ttl <= 0:
        return None
    with _REGIME_CACHE_LOCK:
        hit = _REGIME_CACHE.get(key)
    if not hit or _time.monotonic() - hit[0] > ttl:
        return None
    return hit[1]


def _regime_cache_put(key: str, value: dict) -> None:
    if _regime_cache_ttl_s() <= 0:
        return
    with _REGIME_CACHE_LOCK:
        _REGIME_CACHE[key] = (_time.monotonic(), value)
        while len(_REGIME_CACHE) > _REGIME_CACHE_MAX:
            oldest = min(_REGIME_CACHE, key=lambda k: _REGIME_CACHE[k][0])
            _REGIME_CACHE.pop(oldest, None)


def run_macro_regime_classifier(state: AgentState) -> AgentState:
    """Phase 1: classify macro regime using economic indicators + treasury rates + SPY prices."""
    agent_id = "macro_regime_classifier"
    progress.update_status(agent_id, None, "Fetching macro data (economic indicators + treasury rates)")

    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    end_date = state["data"]["end_date"]
    start_date = (
        datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=90)
    ).strftime("%Y-%m-%d")

    _cache_key = _regime_cache_key(state, end_date)
    _hit = _regime_cache_get(_cache_key)
    if _hit is not None:
        state["data"]["macro_regime"] = dict(_hit["regime"])
        state["data"]["position_size_cap"] = _hit["position_size_cap"]
        progress.update_status(
            agent_id, None,
            f"Regime (reused for {end_date}): {_hit['regime'].get('risk_appetite')} / "
            f"{_hit['regime'].get('volatility_regime')} vol")
        return state

    # ── Fetch all data ────────────────────────────────────────────────────────
    # Ten independent calls; the FMP token bucket still bounds the rate.
    from concurrent.futures import ThreadPoolExecutor
    from src.utils.run_config import submit as _ctx_submit

    def _indicator(name: str):
        return get_economic_indicator(name, start_date, end_date, api_key=api_key)

    with ThreadPoolExecutor(max_workers=10) as _ex:
        _f_spy_prices = _ctx_submit(_ex, get_prices, "SPY", start_date, end_date, api_key=api_key)
        _f_spy_news   = _ctx_submit(_ex, get_company_news, "SPY", end_date, start_date, limit=20, api_key=api_key)
        _f_fed        = _ctx_submit(_ex, _indicator, "federalFunds")
        _f_infl       = _ctx_submit(_ex, _indicator, "inflationRate")
        _f_rec        = _ctx_submit(_ex, _indicator, "smoothedUSRecessionProbabilities")
        _f_claims     = _ctx_submit(_ex, _indicator, "initialClaims")
        _f_sent       = _ctx_submit(_ex, _indicator, "consumerSentiment")
        _f_rmf        = _ctx_submit(_ex, _indicator, "retailMoneyFunds")
        _f_trade      = _ctx_submit(_ex, _indicator, "tradeBalanceGoodsAndServices")
        _f_tsy        = _ctx_submit(_ex, get_treasury_rates, start_date, end_date, api_key=api_key)
        spy_prices  = _f_spy_prices.result()
        spy_news    = _f_spy_news.result()
        fed_data    = _f_fed.result()
        infl_data   = _f_infl.result()
        rec_data    = _f_rec.result()
        claims_data = _f_claims.result()
        sent_data   = _f_sent.result()
        rmf_data    = _f_rmf.result()
        trade_data  = _f_trade.result()
        tsy_data    = _f_tsy.result()

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

    # Use the deterministic rule table to derive position_size_cap.
    # Take the minimum (more conservative) of the rule-table cap and the
    # LLM's suggestion.  The LLM cap is clamped to [0.5, 1.0] to guard
    # against hallucination extremes while still letting it reduce the cap
    # in risk-off / cautious regimes that the rule table doesn't explicitly cover.
    regime_dict  = llm_out.regime.model_dump()
    rule_cap = _apply_regime_rules(regime_dict)
    llm_cap = max(0.5, min(1.0, float(llm_out.position_size_cap)))
    position_size_cap = min(rule_cap, llm_cap)

    # Persist regime state to disk
    regime_path = os.path.join(DATA_DIR, "regime_state.json")
    try:
        from src.utils.json_state import save_json_locked
        save_json_locked(
            regime_path,
            {
                "last_run":          datetime.now().strftime("%Y-%m-%d"),
                "tickers":           state["data"]["tickers"],
                "regime":            regime_dict,
                "position_size_cap": position_size_cap,
            },
        )
    except Exception:
        pass  # Non-fatal; pipeline continues

    rec_label = regime_dict.get("recession_risk", "low")
    progress.update_status(
        agent_id, None,
        f"Regime: {regime_dict['risk_appetite']} / {regime_dict['volatility_regime']} vol / recession {rec_label}"
    )

    state["data"]["macro_regime"]            = regime_dict
    state["data"]["position_size_cap"]        = position_size_cap

    if "LLM fallback" not in str(regime_dict.get("regime_notes") or ""):
        _regime_cache_put(_cache_key, {"regime": dict(regime_dict),
                                       "position_size_cap": position_size_cap})

    return state
