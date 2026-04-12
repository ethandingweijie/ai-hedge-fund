"""
src/agents/intelligence/insider_activity_agent.py
==================================================
Phase 2.5 — Insider Activity Agent (deterministic, no LLM)

Runs in parallel with the Analyst Revision Agent immediately after the
Strategic Router (Phase 2) and before the Industry Specialist (Phase 3).

Data source priority:
  Tier 1 — FMP /stable/insider-trading/search  (Ultimate plan, $149/mo)
  Tier 2 — SEC EDGAR Form 4 XML parsing         (free, no API key required)

Output written to state["data"]["insider_activity"][ticker] as an
InsiderActivityOutput dict, consumed by:
  - Michael Burry    (forensic accounting cross-check)
  - Mohnish Pabrai   (superinvestor / cloning check)
  - Charlie Munger   (management quality signal)
  - Portfolio Manager (conviction multiplier on cluster buy)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from src.graph.state import AgentState
from src.data.models import InsiderActivityOutput, InsiderTransaction
from src.tools.api import get_insider_trades, get_insider_trades_edgar

# Role-weight table — higher weight = stronger signal per dollar transacted
_ROLE_WEIGHTS: dict[str, float] = {
    "ceo":       3.0,
    "cfo":       3.0,
    "president": 2.5,
    "coo":       2.0,
    "cto":       2.0,
    "chairman":  2.5,
    "director":  1.5,
    "officer":   1.0,
}

# Conviction-sell threshold: single sell > this value by CEO/CFO triggers flag
_CONVICTION_SELL_USD = 5_000_000


def _role_weight(title: str | None) -> float:
    """Map a job title string to a role weight multiplier."""
    if not title:
        return 1.0
    t = title.lower()
    for kw, w in _ROLE_WEIGHTS.items():
        if kw in t:
            return w
    return 1.0


def _is_senior_executive(title: str | None) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in ("ceo", "cfo", "president", "chairman"))


def run_insider_activity_agent(state: AgentState) -> AgentState:
    """
    Compute insider activity metrics for each ticker in state.

    Reads:   state["data"]["tickers"], state["data"]["end_date"]
    Writes:  state["data"]["insider_activity"][ticker]
    """
    tickers  = state["data"]["tickers"]
    end_date = state["data"]["end_date"]
    api_key  = (
        os.environ.get("FMP_API_KEY")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    )

    start_12m = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    start_90d = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    start_30d = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")

    results: dict[str, dict] = {}

    for ticker in tickers:
        print(f"  [InsiderActivityAgent] {ticker} — fetching trades")

        # ── Tier 1: FMP (requires Ultimate plan) ──────────────────────────
        trades = get_insider_trades(
            ticker,
            end_date=end_date,
            start_date=start_12m,
            limit=200,
            api_key=api_key,
        )
        data_source = "FMP" if trades else None

        # ── Tier 2: SEC EDGAR (free fallback) ─────────────────────────────
        if not trades:
            trades = get_insider_trades_edgar(ticker, start_12m, end_date)
            data_source = "EDGAR" if trades else "NONE"

        if not trades:
            results[ticker] = InsiderActivityOutput(
                ticker=ticker,
                signal="NEUTRAL",
                data_source="NONE",
                analysis_note="No insider trade data available from FMP or EDGAR.",
            ).model_dump()
            continue

        # ── Compute metrics ───────────────────────────────────────────────
        net_12m = net_90d = net_30d = 0.0
        buy_value_12m = sell_value_12m = 0.0
        conviction_sell_flag = False
        key_txns: list[InsiderTransaction] = []

        # Track cluster-buy: insiders who bought within 30d window
        buyers_30d: set[str] = set()

        for t in trades:
            txn_date = t.transaction_date or ""
            if not txn_date or txn_date > end_date:
                continue

            shares = t.transaction_shares or 0.0
            value  = t.transaction_value  or 0.0
            title  = t.title
            name   = t.name or "Unknown"
            rw     = _role_weight(title)
            is_buy = shares > 0
            signed = value if is_buy else -value   # transaction_value is always positive

            # 12-month window
            if txn_date >= start_12m:
                net_12m += signed
                if is_buy:
                    buy_value_12m  += value
                else:
                    sell_value_12m += value

            # 90-day window
            if txn_date >= start_90d:
                net_90d += signed

            # 30-day window
            if txn_date >= start_30d:
                net_30d += signed
                if is_buy:
                    buyers_30d.add(name)

            # Conviction-sell flag: senior executive sells > $5M in one transaction
            if not is_buy and abs(value) >= _CONVICTION_SELL_USD and _is_senior_executive(title):
                conviction_sell_flag = True

            # Keep top 5 key transactions by absolute value
            if len(key_txns) < 5 or (abs(value) > min(abs(k.value_usd or 0) for k in key_txns)):
                key_txns.append(InsiderTransaction(
                    name=name,
                    title=title,
                    transaction_type="BUY" if is_buy else "SELL",
                    shares=abs(shares),
                    price_per_share=t.transaction_price_per_share,
                    value_usd=abs(value) if value else None,
                    date=txn_date,
                    role_weight=rw,
                ))
                # Keep sorted by abs value, trim to 5
                key_txns.sort(key=lambda k: abs(k.value_usd or 0), reverse=True)
                key_txns = key_txns[:5]

        # ── Classify signal ───────────────────────────────────────────────
        cluster_buy = len(buyers_30d) >= 2
        buy_sell_ratio = (
            buy_value_12m / sell_value_12m if sell_value_12m > 0 else float("inf")
        )

        if cluster_buy or (net_12m > 0 and buy_sell_ratio >= 2.0):
            signal = "BULLISH"
        elif conviction_sell_flag or (net_12m < 0 and buy_sell_ratio <= 0.5):
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        # ── Build note ────────────────────────────────────────────────────
        note_parts = [f"Source: {data_source}. "]
        if cluster_buy:
            note_parts.append(f"Cluster buy: {len(buyers_30d)} insiders bought in last 30d. ")
        if conviction_sell_flag:
            note_parts.append("Conviction sell: senior executive sold >$5M in single transaction. ")
        note_parts.append(
            f"Net 12m ${net_12m:+,.0f} | "
            f"Net 90d ${net_90d:+,.0f} | "
            f"Buy/Sell ratio 12m {buy_sell_ratio:.1f}x."
        )

        results[ticker] = InsiderActivityOutput(
            ticker=ticker,
            signal=signal,
            cluster_buy=cluster_buy,
            net_buying_30d_usd=round(net_30d, 2),
            net_buying_90d_usd=round(net_90d, 2),
            net_buying_12m_usd=round(net_12m, 2),
            gross_buy_value_12m=round(buy_value_12m, 2),
            gross_sell_value_12m=round(sell_value_12m, 2),
            buy_sell_ratio_12m=round(buy_sell_ratio, 2) if buy_sell_ratio != float("inf") else 99.0,
            conviction_sell_flag=conviction_sell_flag,
            key_transactions=key_txns,
            data_source=data_source,
            analysis_note="".join(note_parts),
        ).model_dump()

        print(
            f"  [InsiderActivityAgent] {ticker} — {signal} | "
            f"net 12m ${net_12m:+,.0f} | cluster_buy={cluster_buy} | source={data_source}"
        )

    state["data"]["insider_activity"] = results
    return state
