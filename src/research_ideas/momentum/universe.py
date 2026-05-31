"""
src/research_ideas/momentum/universe.py
========================================
Load the momentum screen's ticker universe and sector-ETF map.

JSON sources:
  src/research_ideas/data/momentum_universe.json  — {ticker,name,sector,sector_etf}
  src/research_ideas/data/momentum_sectors.json   — {macro:[...], thematic:[...]}
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def load_universe() -> list[dict[str, Any]]:
    path = _DATA_DIR / "momentum_universe.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _load_sectors_raw() -> dict[str, Any]:
    path = _DATA_DIR / "momentum_sectors.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def list_tickers() -> list[str]:
    return [row["ticker"] for row in load_universe()]


def get_ticker_metadata(ticker: str) -> dict[str, Any]:
    for row in load_universe():
        if row["ticker"] == ticker:
            return row
    return {"ticker": ticker, "name": ticker, "sector": None, "sector_etf": None}


def list_sectors() -> list[dict[str, Any]]:
    """All sector proxies (macro SPDR ETFs + thematic), each {etf,sector,label,group}."""
    raw = _load_sectors_raw()
    out: list[dict[str, Any]] = []
    for group in ("macro", "thematic"):
        for row in raw.get(group, []):
            out.append({**row, "group": group})
    return out


def list_sector_etfs() -> list[str]:
    return [row["etf"] for row in list_sectors()]
