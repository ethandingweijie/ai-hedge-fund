"""
src/research_ideas/sw46/universe.py
====================================
Load and cache the 46-ticker SW46 list plus per-ticker overrides.
JSON sources live in src/research_ideas/data/.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def load_universe() -> list[dict[str, Any]]:
    path = _DATA_DIR / "sw46_universe.json"
    with path.open("r", encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows


@lru_cache(maxsize=1)
def load_aict_tiers() -> dict[str, str]:
    path = _DATA_DIR / "sw46_aict_tiers.json"
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def load_overrides() -> dict[str, dict]:
    path = _DATA_DIR / "sw46_overrides.json"
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def load_purchase_obligations() -> dict[str, float]:
    path = _DATA_DIR / "sw46_purchase_obligations.json"
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: float(v) for k, v in data.items() if not k.startswith("_")}


def get_ticker_metadata(ticker: str) -> dict[str, Any]:
    for row in load_universe():
        if row["ticker"] == ticker:
            md = dict(row)
            override = load_overrides().get(ticker, {})
            md.update(override)
            return md
    return {"ticker": ticker, "name": ticker, "tax_rate_normalized": 0.21, "notes": ""}


def list_tickers() -> list[str]:
    return [row["ticker"] for row in load_universe()]
