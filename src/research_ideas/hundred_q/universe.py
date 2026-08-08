"""
src/research_ideas/hundred_q/universe.py
==========================================
Load the 100-Question screener universe (pilot: ~30 curated US tickers).

JSON source: src/research_ideas/data/hundred_q_universe.json

This is the sole seam for a future HK sibling loader — a later
`market="hk"` parameter (or a separate hundred_q_hk/universe.py mirroring
hk50/universe.py) is a drop-in here, not a rewrite of any other module.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def load_universe() -> list[dict[str, Any]]:
    path = _DATA_DIR / "hundred_q_universe.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def list_tickers() -> list[str]:
    return [row["ticker"] for row in load_universe()]


def get_ticker_metadata(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper()
    for row in load_universe():
        if row["ticker"] == ticker:
            return row
    return {"ticker": ticker, "name": ticker, "sector": None, "industry": None}
