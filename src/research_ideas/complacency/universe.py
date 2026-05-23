"""
src/research_ideas/complacency/universe.py
===========================================
Load the Complacency screener universe (curated growth + meme + mega-cap
list where complacency would actually manifest).

JSON source: src/research_ideas/data/complacency_universe.json
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def load_universe() -> list[dict[str, Any]]:
    path = _DATA_DIR / "complacency_universe.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def list_tickers() -> list[str]:
    return [row["ticker"] for row in load_universe()]


def get_ticker_metadata(ticker: str) -> dict[str, Any]:
    for row in load_universe():
        if row["ticker"] == ticker:
            return row
    return {"ticker": ticker, "name": ticker, "sector": None, "industry": None}
