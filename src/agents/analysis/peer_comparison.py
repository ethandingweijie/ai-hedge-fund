"""
Phase 4.6 — Peer Comparison Engine  (deterministic, no LLM)

Fetches live TTM financial metrics for the subject ticker(s) and a
sector-defined peer group, then stores the result in state so downstream
consumers (PDF, portfolio manager) can access it without re-fetching.

Return structure in state["data"]["peer_comparison"]:
{
  "NVDA": {                       # keyed by subject ticker
    "NVDA": { "ticker": "NVDA", "is_subject": True,  "pe_ratio": ..., ... },
    "AMD":  { "ticker": "AMD",  "is_subject": False, "pe_ratio": ..., ... },
    ...
  },
  ...
}

Each row dict contains:
  ticker, is_subject, pe_ratio, ev_ebitda, ev_revenue, pb_ratio,
  fcf_yield, roic, revenue_growth, gross_margin, net_margin, market_cap
"""

from __future__ import annotations

import os
from datetime import datetime

from src.graph.state import AgentState
from src.tools.api import get_financial_metrics

# ── Sector peer groups ──────────────────────────────────────────────────────────
# 5-6 liquid, representative peers per sector.
# Subject ticker is excluded at runtime if it appears in the list.
SECTOR_PEERS: dict[str, list[str]] = {
    # Tech: semiconductor + hyperscaler mix; AMD/AVGO/INTC closest GPU/chip comps
    "Tech":                ["AMD",  "AVGO", "MSFT", "GOOGL", "META", "INTC"],
    # Consumer: split into apparel and broad retail; router should select the right block
    # Broad consumer/retail (default)
    "Consumer":            ["AMZN", "WMT",  "COST", "TGT",  "HD",   "NKE"],
    # Apparel/athletic: used when subject is apparel-focused (LULU, NKE, UA, ONON, etc.)
    "ConsumerApparel":     ["NKE",  "ONON", "UA",   "VFC",  "PVH",  "RL"],
    "Biopharma":           ["JNJ",  "PFE",  "MRK",  "ABBV", "AMGN", "GILD"],
    "Telco":               ["VZ",   "T",    "TMUS", "AMT",  "CCI"],
    "Crypto":              ["MSTR", "COIN", "MARA", "RIOT", "HUT"],
    "Energy":              ["XOM",  "CVX",  "NEE",  "DUK",  "SO",   "SLB"],
    "Financials":          ["JPM",  "BAC",  "GS",   "MS",   "WFC",  "C"],
    "Industrials":         ["GE",   "HON",  "LMT",  "RTX",  "BA",   "CAT"],
    "RealEstate":          ["PLD",  "AMT",  "EQIX", "SPG",  "O"],
    "Transportation":      ["UPS",  "FDX",  "DAL",  "UAL",  "CSX"],
    "Materials":           ["LIN",  "APD",  "NEM",  "FCX",  "BHP"],
    "Resources":           ["XOM",  "CVX",  "COP",  "EOG",  "MPC"],
    "ProfessionalServices":["ACN",  "IBM",  "INFY", "WIT",  "EPAM"],
    "HealthcareServices":  ["UNH",  "CVS",  "HCA",  "CI",   "HUM"],
}

# Tickers that should use a sub-sector peer group instead of the broad sector default.
# Maps ticker → sector key in SECTOR_PEERS.
_TICKER_PEER_OVERRIDE: dict[str, str] = {
    # Athletic/premium apparel — use ConsumerApparel not generic Consumer
    "LULU": "ConsumerApparel",
    "NKE":  "ConsumerApparel",
    "UA":   "ConsumerApparel",
    "UAA":  "ConsumerApparel",
    "ONON": "ConsumerApparel",
    "VFC":  "ConsumerApparel",
    "PVH":  "ConsumerApparel",
    "RL":   "ConsumerApparel",
    "SKX":  "ConsumerApparel",
    "CROX": "ConsumerApparel",
}

_MAX_PEERS = 4   # peers shown alongside the subject (so table = 5 cols max)


# ── Metric extraction ───────────────────────────────────────────────────────────

def _extract_metrics(fm_list: list, ticker: str, is_subject: bool) -> dict:
    """Pull the most-recent TTM metrics from a FinancialMetrics list."""
    base: dict = {"ticker": ticker, "is_subject": is_subject}
    if not fm_list:
        return base
    m = fm_list[0]
    base.update({
        "pe_ratio":       getattr(m, "price_to_earnings_ratio",          None),
        # Correct attribute names from FinancialMetrics model:
        "ev_ebitda":      getattr(m, "enterprise_value_to_ebitda_ratio", None),
        "ev_revenue":     getattr(m, "enterprise_value_to_revenue_ratio",None),
        "pb_ratio":       getattr(m, "price_to_book_ratio",              None),
        "fcf_yield":      getattr(m, "free_cash_flow_yield",             None),
        "roic":           getattr(m, "return_on_invested_capital",       None),
        "revenue_growth": getattr(m, "revenue_growth",                   None),
        "gross_margin":   getattr(m, "gross_margin",                     None),
        "net_margin":     getattr(m, "net_margin",                       None),  # was net_profit_margin
        "market_cap":     getattr(m, "market_cap",                       None),
    })
    return base


# ── Main entry point ────────────────────────────────────────────────────────────

def run_peer_comparison(state: AgentState) -> AgentState:
    """
    Phase 4.6 — Peer Comparison Engine.

    Fetches live TTM financial metrics for each subject ticker and its
    sector peers. Writes results to state["data"]["peer_comparison"].
    Degrades gracefully if the API key is absent or calls fail.
    """
    tickers  = state["data"].get("tickers", [])
    sector   = state["data"].get("sector", "")
    end_date = state["data"].get("end_date", datetime.now().strftime("%Y-%m-%d"))
    api_key  = (
        state["data"].get("api_key")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    )

    peer_comparison: dict[str, dict] = {}

    for subject in tickers:
        # Ticker-level override takes priority (e.g. LULU → ConsumerApparel)
        # then sector default, then empty list
        peer_sector = _TICKER_PEER_OVERRIDE.get(subject.upper(), sector)
        raw_peers   = SECTOR_PEERS.get(peer_sector, SECTOR_PEERS.get(sector, []))
        peers       = [p for p in raw_peers if p.upper() != subject.upper()][:_MAX_PEERS]
        all_tickers = [subject] + peers

        comparison: dict[str, dict] = {}
        for t in all_tickers:
            is_subj = (t.upper() == subject.upper())
            try:
                fm   = get_financial_metrics(t, end_date, period="ttm", limit=1, api_key=api_key)
                row  = _extract_metrics(fm, t, is_subj)
            except Exception:
                row  = {"ticker": t, "is_subject": is_subj}
            comparison[t] = row

        peer_comparison[subject] = comparison

    state["data"]["peer_comparison"] = peer_comparison
    return state
