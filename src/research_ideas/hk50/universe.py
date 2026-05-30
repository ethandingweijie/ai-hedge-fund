"""
src/research_ideas/hk50/universe.py
====================================
The "Long China / HK" 50-stock universe. Single source of truth is the HK50
list + ADR routing already curated in scripts/hk50_adr_report.py — we re-export
it here so the backend package never re-types the 50 tickers (no drift).

  list_tickers()              -> [canonical HK ticker, ...]   (len 50)
  get_ticker_metadata(hk)     -> {hk_ticker, name, report_ticker, route_label}
"""
from __future__ import annotations

from scripts.hk50_adr_report import HK50, route


def list_tickers() -> list[str]:
    """Canonical HK tickers (4 native ADRs included as their own symbol)."""
    return [hk for hk, _name in HK50]


def get_ticker_metadata(hk_ticker: str) -> dict:
    name = next((n for hk, n in HK50 if hk == hk_ticker), hk_ticker)
    rep, label = route(hk_ticker)
    return {
        "hk_ticker": hk_ticker,
        "name": name,
        "report_ticker": rep,
        "route_label": label,
    }


def universe_pairs() -> list[tuple[str, str]]:
    """[(hk_ticker, display_name), ...] — the raw HK50 seed."""
    return list(HK50)
